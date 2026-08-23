package com.quantedge.auth;

import com.quantedge.auth.entity.RefreshSession;
import com.quantedge.auth.entity.User;
import com.quantedge.auth.repository.RefreshSessionRepository;
import com.quantedge.auth.repository.UserRepository;
import com.quantedge.auth.service.EmailService;
import com.quantedge.auth.service.UserService;
import com.quantedge.common.config.JwtTokenProvider;
import com.quantedge.common.exception.BusinessRuleViolationException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class UserServiceRefreshSessionTest {

    private UserRepository userRepository;
    private RefreshSessionRepository refreshSessionRepository;
    private JwtTokenProvider jwtTokenProvider;
    private UserService userService;

    private User alice;
    private String aliceId = "usr-alice-0001";
    private String aliceEmail = "alice@example.com";
    private String validPassword = "Password123!";

    // Token constants
    private static final String REFRESH_JWT = "refresh.jwt.token";
    private static final String ROTATED_REFRESH_JWT = "rotated.refresh.jwt.token";
    private static final String MISMATCH_TOKEN = "mismatch.token";
    private static final String LOGOUT_TOKEN = "logout.token";
    private static final String UNKNOWN_TOKEN = "unknown.token";
    private static final String EXPIRED_TOKEN = "expired.token";
    private static final String BAD_TOKEN = "bad.token";
    private static final String ACCESS_JWT = "access.jwt.token";

    @BeforeEach
    void setUp() {
        userRepository = mock(UserRepository.class);
        refreshSessionRepository = mock(RefreshSessionRepository.class);
        jwtTokenProvider = mock(JwtTokenProvider.class);
        var passwordEncoder = mock(org.springframework.security.crypto.password.PasswordEncoder.class);
        var passwordResetTokenRepository = mock(com.quantedge.auth.repository.PasswordResetTokenRepository.class);
        var emailService = mock(EmailService.class);

        // Common user
        alice = new User(aliceEmail, "$2a$12$fakehash", "Alice", "USER", true, true, null);
        setIdViaReflection(alice, aliceId);

        when(userRepository.findById(aliceId)).thenReturn(Optional.of(alice));
        when(userRepository.findByEmail(aliceEmail)).thenReturn(Optional.of(alice));
        when(userRepository.existsByEmail(aliceEmail)).thenReturn(false);
        when(passwordEncoder.matches(validPassword, "$2a$12$fakehash")).thenReturn(true);
        when(passwordEncoder.encode(anyString())).thenReturn("$2a$12$newhash");

        // JWT defaults - use anyString() for generateRefreshToken so it works for any userId
        when(jwtTokenProvider.generateAccessToken(anyString(), anyString())).thenReturn(ACCESS_JWT);
        when(jwtTokenProvider.generateRefreshToken(anyString())).thenReturn(ROTATED_REFRESH_JWT);
        when(jwtTokenProvider.validateToken(REFRESH_JWT)).thenReturn(true);
        when(jwtTokenProvider.isRefreshToken(REFRESH_JWT)).thenReturn(true);
        when(jwtTokenProvider.isTokenExpired(REFRESH_JWT)).thenReturn(false);
        when(jwtTokenProvider.getUserIdFromToken(REFRESH_JWT)).thenReturn(aliceId);
        when(jwtTokenProvider.getEmailFromToken(ACCESS_JWT)).thenReturn(aliceEmail);

        when(jwtTokenProvider.validateToken(ROTATED_REFRESH_JWT)).thenReturn(true);
        when(jwtTokenProvider.isRefreshToken(ROTATED_REFRESH_JWT)).thenReturn(true);
        when(jwtTokenProvider.isTokenExpired(ROTATED_REFRESH_JWT)).thenReturn(false);
        when(jwtTokenProvider.getUserIdFromToken(ROTATED_REFRESH_JWT)).thenReturn(aliceId);

        // Other tokens
        when(jwtTokenProvider.validateToken(MISMATCH_TOKEN)).thenReturn(true);
        when(jwtTokenProvider.isRefreshToken(MISMATCH_TOKEN)).thenReturn(true);
        when(jwtTokenProvider.isTokenExpired(MISMATCH_TOKEN)).thenReturn(false);
        when(jwtTokenProvider.getUserIdFromToken(MISMATCH_TOKEN)).thenReturn(aliceId);

        when(jwtTokenProvider.validateToken(LOGOUT_TOKEN)).thenReturn(true);
        when(jwtTokenProvider.isRefreshToken(LOGOUT_TOKEN)).thenReturn(true);
        when(jwtTokenProvider.isTokenExpired(LOGOUT_TOKEN)).thenReturn(false);
        when(jwtTokenProvider.getUserIdFromToken(LOGOUT_TOKEN)).thenReturn(aliceId);

        when(jwtTokenProvider.validateToken(UNKNOWN_TOKEN)).thenReturn(false);

        when(jwtTokenProvider.validateToken(EXPIRED_TOKEN)).thenReturn(true);
        when(jwtTokenProvider.isRefreshToken(EXPIRED_TOKEN)).thenReturn(true);
        when(jwtTokenProvider.isTokenExpired(EXPIRED_TOKEN)).thenReturn(true);

        when(jwtTokenProvider.validateToken(BAD_TOKEN)).thenReturn(false);

        // Password reset token
        var validResetToken = mock(com.quantedge.auth.entity.PasswordResetToken.class);
        when(validResetToken.getUser()).thenReturn(alice);
        when(validResetToken.isValid()).thenReturn(true);
        when(passwordResetTokenRepository.findByTokenHash(anyString())).thenReturn(Optional.of(validResetToken));

        userService = new UserService(
                userRepository,
                passwordEncoder,
                jwtTokenProvider,
                passwordResetTokenRepository,
                refreshSessionRepository,
                emailService
        );

        // Set refreshExpirationMs via reflection (7 days = 604800000 ms)
        try {
            var field = UserService.class.getDeclaredField("refreshExpirationMs");
            field.setAccessible(true);
            field.set(userService, 604800000L);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private void setIdViaReflection(User user, String id) {
        try {
            var idField = user.getClass().getSuperclass().getDeclaredField("id");
            idField.setAccessible(true);
            idField.set(user, id);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private String sha256Hex(String input) {
        try {
            var digest = java.security.MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(input.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            return java.util.HexFormat.of().formatHex(hash);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @Nested
    @DisplayName("Login & Signup — session creation")
    class LoginSignupTests {

        @Test
        @DisplayName("Signup creates a persisted refresh session")
        void signupCreatesSession() {
            when(userRepository.save(any(User.class))).thenAnswer(inv -> {
                User u = inv.getArgument(0);
                setIdViaReflection(u, "usr-new-0001");
                return u;
            });

            userService.signup("Alice", aliceEmail, validPassword);

            var captor = org.mockito.ArgumentCaptor.forClass(RefreshSession.class);
            verify(refreshSessionRepository).save(captor.capture());
            RefreshSession session = captor.getValue();
            assertNotNull(session.getTokenHash());
            assertEquals(64, session.getTokenHash().length());
            assertEquals("usr-new-0001", session.getUserId());
            assertTrue(session.getExpiresAt().isAfter(Instant.now()));
            assertNull(session.getRevokedAt());
        }

        @Test
        @DisplayName("Login creates a persisted refresh session")
        void loginCreatesSession() {
            when(userRepository.findByEmail(aliceEmail)).thenReturn(Optional.of(alice));

            userService.login(aliceEmail, validPassword);

            var captor = org.mockito.ArgumentCaptor.forClass(RefreshSession.class);
            verify(refreshSessionRepository).save(captor.capture());
            RefreshSession session = captor.getValue();
            assertEquals(aliceId, session.getUserId());
        }

        @Test
        @DisplayName("Login with wrong password fails and creates no session")
        void loginWrongPasswordNoSession() {
            when(userRepository.findByEmail(aliceEmail)).thenReturn(Optional.of(alice));
            var badEncoder = mock(org.springframework.security.crypto.password.PasswordEncoder.class);
            when(badEncoder.matches(anyString(), anyString())).thenReturn(false);
            var badService = new UserService(
                    userRepository, badEncoder, jwtTokenProvider,
                    mock(com.quantedge.auth.repository.PasswordResetTokenRepository.class),
                    refreshSessionRepository, mock(EmailService.class)
            );
            assertThrows(BusinessRuleViolationException.class, () -> badService.login(aliceEmail, "wrong"));
            verify(refreshSessionRepository, never()).save(any());
        }
    }

    @Nested
    @DisplayName("Refresh token rotation")
    class RotationTests {

        @Test
        @DisplayName("Valid refresh rotates: old session revoked, new session created, new tokens returned")
        void validRefreshRotates() {
            String originalHash = sha256Hex(REFRESH_JWT);

            RefreshSession originalSession = new RefreshSession(originalHash, aliceId, Instant.now().plusSeconds(3600));
            when(refreshSessionRepository.findByTokenHash(originalHash)).thenReturn(Optional.of(originalSession));

            UserService.AuthResult result = userService.refreshToken(REFRESH_JWT);

            assertNotNull(result);
            assertEquals(aliceId, result.user().getId());

            // Original session revoked and linked to replacement
            assertNotNull(originalSession.getRevokedAt());
            assertNotNull(originalSession.getReplacedByHash());
            assertFalse(originalSession.getReplacedByHash().equals(originalHash));
            verify(refreshSessionRepository).save(originalSession);

            // New session persisted FIRST, then revoked original
            var newCaptor = org.mockito.ArgumentCaptor.forClass(RefreshSession.class);
            verify(refreshSessionRepository, times(2)).save(newCaptor.capture());
            RefreshSession newSession = newCaptor.getAllValues().get(0); // First save = new session
            assertNotEquals(originalHash, newSession.getTokenHash());
            assertEquals(aliceId, newSession.getUserId());
        }

        @Test
        @DisplayName("Replay of rotated (old) token fails — session already revoked")
        void replayOfRotatedTokenFails() {
            String originalHash = sha256Hex(REFRESH_JWT);
            RefreshSession revokedSession = new RefreshSession(originalHash, aliceId, Instant.now().plusSeconds(3600));
            revokedSession.revoke();
            when(refreshSessionRepository.findByTokenHash(originalHash)).thenReturn(Optional.of(revokedSession));

            assertThrows(BusinessRuleViolationException.class, () -> userService.refreshToken(REFRESH_JWT));
            verify(refreshSessionRepository, never()).save(any(RefreshSession.class));
        }

        @Test
        @DisplayName("Unknown refresh token hash fails")
        void unknownTokenFails() {
            String unknownHash = sha256Hex(UNKNOWN_TOKEN);
            when(refreshSessionRepository.findByTokenHash(unknownHash)).thenReturn(Optional.empty());

            assertThrows(BusinessRuleViolationException.class, () -> userService.refreshToken(UNKNOWN_TOKEN));
        }

        @Test
        @DisplayName("Expired refresh session fails")
        void expiredSessionFails() {
            String expiredHash = sha256Hex(EXPIRED_TOKEN);
            RefreshSession expired = new RefreshSession(expiredHash, aliceId, Instant.now().minusSeconds(60));
            when(refreshSessionRepository.findByTokenHash(expiredHash)).thenReturn(Optional.of(expired));

            assertThrows(BusinessRuleViolationException.class, () -> userService.refreshToken(EXPIRED_TOKEN));
        }

        @Test
        @DisplayName("Invalid JWT signature fails before session lookup")
        void invalidJwtSignatureFails() {
            when(jwtTokenProvider.validateToken(BAD_TOKEN)).thenReturn(false);

            assertThrows(BusinessRuleViolationException.class, () -> userService.refreshToken(BAD_TOKEN));
            verify(refreshSessionRepository, never()).findByTokenHash(anyString());
        }

        @Test
        @DisplayName("Refresh token with wrong userId in JWT subject vs session fails")
        void userIdMismatchFails() {
            String hash = sha256Hex(MISMATCH_TOKEN);
            RefreshSession session = new RefreshSession(hash, "usr-different-user", Instant.now().plusSeconds(3600));
            when(refreshSessionRepository.findByTokenHash(hash)).thenReturn(Optional.of(session));
            when(jwtTokenProvider.getUserIdFromToken(MISMATCH_TOKEN)).thenReturn(aliceId);

            assertThrows(BusinessRuleViolationException.class, () -> userService.refreshToken(MISMATCH_TOKEN));

            // Session should be revoked defensively
            verify(refreshSessionRepository).save(argThat(s -> s.isRevoked()));
        }

        @Test
        @DisplayName("Deactivated user cannot refresh")
        void deactivatedUserFails() {
            String hash = sha256Hex(REFRESH_JWT);
            RefreshSession session = new RefreshSession(hash, aliceId, Instant.now().plusSeconds(3600));
            when(refreshSessionRepository.findByTokenHash(hash)).thenReturn(Optional.of(session));
            User inactiveUser = new User(aliceEmail, "$2a$12$x", "Alice", "USER", false, true, null);
            setIdViaReflection(inactiveUser, aliceId);
            when(userRepository.findById(aliceId)).thenReturn(Optional.of(inactiveUser));

            assertThrows(BusinessRuleViolationException.class, () -> userService.refreshToken(REFRESH_JWT));
        }
    }

    @Nested
    @DisplayName("Logout — server-side revocation")
    class LogoutTests {

        @Test
        @DisplayName("Logout revokes the presented refresh session")
        void logoutRevokesSession() {
            String hash = sha256Hex(LOGOUT_TOKEN);
            RefreshSession session = new RefreshSession(hash, aliceId, Instant.now().plusSeconds(3600));
            when(refreshSessionRepository.findByTokenHash(hash)).thenReturn(Optional.of(session));

            userService.logout(LOGOUT_TOKEN);

            assertNotNull(session.getRevokedAt());
            verify(refreshSessionRepository).save(session);
        }

        @Test
        @DisplayName("Logout with unknown token is idempotent (no exception)")
        void logoutUnknownTokenIdempotent() {
            when(refreshSessionRepository.findByTokenHash(anyString())).thenReturn(Optional.empty());

            assertDoesNotThrow(() -> userService.logout(UNKNOWN_TOKEN));
            verify(refreshSessionRepository, never()).save(any());
        }

        @Test
        @DisplayName("Logout then refresh on same token fails")
        void logoutThenRefreshFails() {
            String hash = sha256Hex(LOGOUT_TOKEN);
            RefreshSession session = new RefreshSession(hash, aliceId, Instant.now().plusSeconds(3600));
            when(refreshSessionRepository.findByTokenHash(hash)).thenReturn(Optional.of(session));

            userService.logout(LOGOUT_TOKEN);

            assertThrows(BusinessRuleViolationException.class, () -> userService.refreshToken(LOGOUT_TOKEN));
        }
    }

    @Nested
    @DisplayName("Password reset revokes all sessions")
    class PasswordResetRevocationTests {

        @Test
        @DisplayName("resetPassword calls revokeAllSessionsForUser")
        void resetPasswordRevokesAllSessions() {
            when(refreshSessionRepository.revokeAllActiveForUser(eq(aliceId), any(Instant.class))).thenReturn(1);

            userService.resetPassword("raw.token", "NewPassword123!");

            verify(refreshSessionRepository).revokeAllActiveForUser(eq(aliceId), any(Instant.class));
        }
    }

    @Nested
    @DisplayName("Cross-user isolation")
    class IsolationTests {

        @Test
        @DisplayName("User B cannot use User A's refresh token")
        void crossUserTokenFails() {
            String bobId = "usr-bob-0002";
            String bobHash = sha256Hex(MISMATCH_TOKEN);

            RefreshSession bobsSession = new RefreshSession(bobHash, bobId, Instant.now().plusSeconds(3600));
            when(refreshSessionRepository.findByTokenHash(bobHash)).thenReturn(Optional.of(bobsSession));
            when(jwtTokenProvider.getUserIdFromToken(MISMATCH_TOKEN)).thenReturn(aliceId);

            assertThrows(BusinessRuleViolationException.class, () -> userService.refreshToken(MISMATCH_TOKEN));

            // Bob's session should be revoked defensively
            verify(refreshSessionRepository).save(argThat(s -> s.isRevoked()));
        }
    }
}