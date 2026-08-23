package com.quantedge.auth.service;

import com.quantedge.auth.entity.PasswordResetToken;
import com.quantedge.auth.entity.User;
import com.quantedge.auth.repository.PasswordResetTokenRepository;
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
    private final EmailService emailService;

    @Value("${quantedge.password-reset.base-url:http://localhost:3100}")
    private String passwordResetBaseUrl;

    public UserService(
            UserRepository userRepository,
            PasswordEncoder passwordEncoder,
            JwtTokenProvider jwtTokenProvider,
            PasswordResetTokenRepository passwordResetTokenRepository,
            EmailService emailService) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        this.jwtTokenProvider = jwtTokenProvider;
        this.passwordResetTokenRepository = passwordResetTokenRepository;
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
        String refreshToken = jwtTokenProvider.generateRefreshToken(user.getId());

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
        String refreshToken = jwtTokenProvider.generateRefreshToken(user.getId());

        return new AuthResult(user, accessToken, refreshToken);
    }

    @Transactional
    public AuthResult refreshToken(String refreshToken) {
        if (!jwtTokenProvider.validateToken(refreshToken) || !jwtTokenProvider.isRefreshToken(refreshToken)) {
            throw new BusinessRuleViolationException("Invalid or expired refresh token.");
        }

        String userId = jwtTokenProvider.getUserIdFromToken(refreshToken);
        User user = findById(userId);

        if (!user.getIsActive()) {
            throw new BusinessRuleViolationException("Account is deactivated.");
        }

        String newAccessToken = jwtTokenProvider.generateAccessToken(user.getId(), user.getEmail());
        String newRefreshToken = jwtTokenProvider.generateRefreshToken(user.getId());

        return new AuthResult(user, newAccessToken, newRefreshToken);
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

        log.info("[Auth] Password reset completed for user {}", user.getEmail());
    }

    // ─── Utilities ────────────────────────────────────────────────────────────

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