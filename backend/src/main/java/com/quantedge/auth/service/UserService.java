package com.quantedge.auth.service;

import com.quantedge.auth.entity.PasswordResetToken;
import com.quantedge.auth.entity.RefreshSession;
import com.quantedge.auth.entity.User;
import com.quantedge.auth.repository.PasswordResetTokenRepository;
import com.quantedge.auth.repository.RefreshSessionRepository;
import com.quantedge.auth.repository.UserRepository;
import com.quantedge.common.config.JwtTokenProvider;
import com.quantedge.common.exception.BusinessRuleViolationException;
import com.quantedge.common.exception.ResourceNotFoundException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.HexFormat;

@Service
public class UserService implements UserDetailsService {

    private static final Logger log = LoggerFactory.getLogger(UserService.class);
    private static final int RESET_TOKEN_EXPIRY_MINUTES = 30;

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokenProvider jwtTokenProvider;
    private final PasswordResetTokenRepository passwordResetTokenRepository;
    private final RefreshSessionRepository refreshSessionRepository;
    private final EmailService emailService;

    @Value("${quantedge.password-reset.base-url:http://localhost:3100}")
    private String passwordResetBaseUrl;

    @Value("${quantedge.jwt.refresh-expiration:604800000}")
    private long refreshExpirationMs;

    public UserService(
            UserRepository userRepository,
            PasswordEncoder passwordEncoder,
            JwtTokenProvider jwtTokenProvider,
            PasswordResetTokenRepository passwordResetTokenRepository,
            RefreshSessionRepository refreshSessionRepository,
            EmailService emailService) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        this.jwtTokenProvider = jwtTokenProvider;
        this.passwordResetTokenRepository = passwordResetTokenRepository;
        this.refreshSessionRepository = refreshSessionRepository;
        this.emailService = emailService;
    }

    /**
     * Spring Security UserDetailsService — loads user by email (used by JwtAuthenticationFilter).
     */
    @Override
    @Transactional(readOnly = true)
    public UserDetails loadUserByUsername(String email) throws UsernameNotFoundException {
        return userRepository.findByEmail(normalizeEmail(email))
                .orElseThrow(() -> new UsernameNotFoundException("User not found: " + email));
    }

    @Transactional(readOnly = true)
    public User findByEmail(String email) {
        return userRepository.findByEmail(normalizeEmail(email))
                .orElseThrow(() -> new ResourceNotFoundException("User", "email", email));
    }

    @Transactional(readOnly = true)
    public User findById(String id) {
        return userRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("User", "id", id));
    }

    @Transactional
    public AuthResult signup(String name, String rawEmail, String password) {
        String email = normalizeEmail(rawEmail);

        if (userRepository.existsByEmail(email)) {
            throw new BusinessRuleViolationException("An account with this email address already exists.");
        }

        User user = User.builder()
                .name(name.trim())
                .email(email)
                .passwordHash(passwordEncoder.encode(password))
                .isActive(true)
                .emailVerified(true) // email verification not yet implemented — default to verified
                .build();

        user = userRepository.save(user);

        String accessToken = jwtTokenProvider.generateAccessToken(user.getId(), user.getEmail());
        String refreshToken = issueRefreshSession(user.getId());

        return new AuthResult(user, accessToken, refreshToken);
    }

    @Transactional
    public AuthResult login(String rawEmail, String password) {
        String email = normalizeEmail(rawEmail);

        User user = userRepository.findByEmail(email)
                .orElseThrow(() -> new BusinessRuleViolationException("Invalid email or password."));

        if (!passwordEncoder.matches(password, user.getPasswordHash())) {
            throw new BusinessRuleViolationException("Invalid email or password.");
        }

        if (!user.getIsActive()) {
            throw new BusinessRuleViolationException("This account has been deactivated. Please contact support.");
        }

        user.setLastLoginAt(Instant.now());
        userRepository.save(user);

        String accessToken = jwtTokenProvider.generateAccessToken(user.getId(), user.getEmail());
        String refreshToken = issueRefreshSession(user.getId());

        return new AuthResult(user, accessToken, refreshToken);
    }

    /**
     * Rotates a refresh token against the persistent session store.
     *
     * A refresh succeeds only when ALL of the following hold:
     *  1. The JWT is well-formed, correctly signed, unexpired, and of type "refresh".
     *  2. A persisted session exists for the token's SHA-256 hash.
     *  3. The session is not revoked (covers logout, rotation replay, password reset).
     *  4. The session is not expired.
     *  5. The session's user is active.
     *
     * On success the old session is revoked and linked to the replacement token,
     * so any replay of the previous refresh token fails permanently.
     */
    @Transactional
    public AuthResult refreshToken(String refreshToken) {
        if (!jwtTokenProvider.validateToken(refreshToken)
                || !jwtTokenProvider.isRefreshToken(refreshToken)
                || jwtTokenProvider.isTokenExpired(refreshToken)) {
            throw new BusinessRuleViolationException("Invalid or expired refresh token.");
        }

        String presentedHash = sha256Hex(refreshToken);
        RefreshSession session = refreshSessionRepository.findByTokenHash(presentedHash)
                .orElseThrow(() -> new BusinessRuleViolationException("Invalid or expired refresh token."));

        if (!session.isActive()) {
            // Revoked (logout/rotation replay/password reset) or expired session.
            throw new BusinessRuleViolationException("Invalid or expired refresh token.");
        }

        String userId = jwtTokenProvider.getUserIdFromToken(refreshToken);
        if (!session.getUserId().equals(userId)) {
            // Defensive: token hash belongs to a different user than the JWT subject.
            session.revoke();
            refreshSessionRepository.save(session);
            throw new BusinessRuleViolationException("Invalid or expired refresh token.");
        }

        User user = findById(userId);

        if (!user.getIsActive()) {
            throw new BusinessRuleViolationException("Account is deactivated.");
        }

        // Rotate: revoke the presented session, create and persist the replacement.
        String newRefreshToken = issueRefreshSession(user.getId());
        session.revokeAndRotateTo(sha256Hex(newRefreshToken));
        refreshSessionRepository.save(session);

        String newAccessToken = jwtTokenProvider.generateAccessToken(user.getId(), user.getEmail());

        return new AuthResult(user, newAccessToken, newRefreshToken);
    }

    /**
     * Revokes the refresh session associated with the presented refresh token.
     * Idempotent: unknown or already-revoked tokens are ignored silently so
     * logout never leaks session-state information.
     */
    @Transactional
    public void logout(String refreshToken) {
        if (refreshToken == null || refreshToken.isBlank()) {
            return;
        }
        refreshSessionRepository.findByTokenHash(sha256Hex(refreshToken)).ifPresent(session -> {
            session.revoke();
            refreshSessionRepository.save(session);
        });
    }

    /**
     * Revokes every active refresh session for a user (password reset, account disable).
     */
    @Transactional
    public void revokeAllSessionsForUser(String userId) {
        refreshSessionRepository.revokeAllActiveForUser(userId, Instant.now());
    }

    /**
     * Initiates a password reset flow.
     * Always succeeds silently to prevent email enumeration.
     */
    @Transactional
    public void initiatePasswordReset(String rawEmail) {
        String email = normalizeEmail(rawEmail);

        userRepository.findByEmail(email).ifPresent(user -> {
            // Invalidate any pending reset tokens for this user
            passwordResetTokenRepository.invalidateAllForUser(user.getId());

            // Generate a cryptographically secure random token
            String rawToken = generateSecureToken();
            String tokenHash = sha256Hex(rawToken);

            Instant expiresAt = Instant.now().plus(RESET_TOKEN_EXPIRY_MINUTES, ChronoUnit.MINUTES);
            passwordResetTokenRepository.save(new PasswordResetToken(tokenHash, user, expiresAt));

            String resetUrl = passwordResetBaseUrl + "/reset-password?token=" + rawToken;
            emailService.sendPasswordResetEmail(user.getEmail(), user.getName(), resetUrl);
        });
        // If user not found, do nothing — caller returns the same generic response
    }

    /**
     * Validates the reset token and updates the user's password.
     */
    @Transactional
    public void resetPassword(String rawToken, String newPassword) {
        String tokenHash = sha256Hex(rawToken);

        PasswordResetToken resetToken = passwordResetTokenRepository.findByTokenHash(tokenHash)
                .orElseThrow(() -> new BusinessRuleViolationException("Invalid or expired reset link."));

        if (!resetToken.isValid()) {
            throw new BusinessRuleViolationException("This reset link has expired or has already been used.");
        }

        User user = resetToken.getUser();
        user.setPasswordHash(passwordEncoder.encode(newPassword));
        userRepository.save(user);

        resetToken.markUsed();
        passwordResetTokenRepository.save(resetToken);

        // A password reset must terminate all live sessions: any stolen refresh
        // token becomes unusable immediately.
        refreshSessionRepository.revokeAllActiveForUser(user.getId(), Instant.now());
        log.info("[Auth] Password reset completed for user {} — all refresh sessions revoked", user.getEmail());
    }

    // ─── Utilities ────────────────────────────────────────────────────────────

    /**
     * Creates a new persistent refresh session: generates the refresh JWT and
     * stores ONLY its SHA-256 hash server-side. The raw token is never persisted.
     */
    private String issueRefreshSession(String userId) {
        String refreshToken = jwtTokenProvider.generateRefreshToken(userId);
        Instant expiresAt = Instant.now().plusMillis(refreshExpirationMs);
        refreshSessionRepository.save(new RefreshSession(sha256Hex(refreshToken), userId, expiresAt));
        return refreshToken;
    }

    private String normalizeEmail(String email) {
        if (email == null) return null;
        return email.trim().toLowerCase();
    }

    private String generateSecureToken() {
        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private String sha256Hex(String input) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(input.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(hash);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 not available", e);
        }
    }

    public record AuthResult(User user, String accessToken, String refreshToken) {}
}