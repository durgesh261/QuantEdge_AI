package com.quantedge.auth.controller;

import com.quantedge.auth.entity.User;
import com.quantedge.auth.service.UserService;
import com.quantedge.common.config.JwtTokenProvider;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

/**
 * Authentication REST controller.
 *
 * With server.servlet.context-path=/api, the controller mapping "/v1/auth" produces
 * the effective public URL: http://host:8080/api/v1/auth/**
 *
 * Frontend Vite proxy rewrites /api/* → http://localhost:8080/api/*, so frontend
 * calls POST /api/v1/auth/signup → backend receives POST /v1/auth/signup (relative
 * to context-path /api). This is consistent.
 */
@RestController
@RequestMapping("/v1/auth")
public class AuthController {

    private final UserService userService;
    private final JwtTokenProvider jwtTokenProvider;

    @Value("${quantedge.auth.cookie-secure:false}")
    private boolean cookieSecure;

    @Value("${quantedge.auth.cookie-same-site:Lax}")
    private String cookieSameSite;

    public AuthController(UserService userService, JwtTokenProvider jwtTokenProvider) {
        this.userService = userService;
        this.jwtTokenProvider = jwtTokenProvider;
    }

    @PostMapping("/signup")
    public ResponseEntity<AuthResponse> signup(@Valid @RequestBody SignupRequest request, HttpServletResponse response) {
        UserService.AuthResult result = userService.signup(request.name(), request.email(), request.password());
        setAuthCookies(response, result.accessToken(), result.refreshToken());
        return ResponseEntity.ok(new AuthResponse(result.user(), null));
    }

    @PostMapping("/login")
    public ResponseEntity<AuthResponse> login(@Valid @RequestBody LoginRequest request, HttpServletResponse response) {
        UserService.AuthResult result = userService.login(request.email(), request.password());
        setAuthCookies(response, result.accessToken(), result.refreshToken());
        return ResponseEntity.ok(new AuthResponse(result.user(), null));
    }

    @PostMapping("/logout")
    public ResponseEntity<Void> logout(HttpServletResponse response) {
        clearAuthCookies(response);
        return ResponseEntity.ok().build();
    }

    @PostMapping("/refresh")
    public ResponseEntity<AuthResponse> refresh(
            @CookieValue(name = "refresh_token", required = false) String refreshToken,
            HttpServletResponse response) {
        if (refreshToken == null || refreshToken.isBlank()) {
            return ResponseEntity.status(401).build();
        }
        UserService.AuthResult result = userService.refreshToken(refreshToken);
        setAuthCookies(response, result.accessToken(), result.refreshToken());
        return ResponseEntity.ok(new AuthResponse(result.user(), null));
    }

    /**
     * Returns the currently authenticated user.
     * Relies entirely on Spring Security context populated by JwtAuthenticationFilter.
     * No @RequestAttribute — that pattern requires a separate filter that was missing.
     */
    @GetMapping("/me")
    public ResponseEntity<AuthResponse> me(Authentication authentication) {
        if (authentication == null || !authentication.isAuthenticated()) {
            return ResponseEntity.status(401).build();
        }
        User user = (User) authentication.getPrincipal();
        return ResponseEntity.ok(new AuthResponse(user, null));
    }

    @PostMapping("/forgot-password")
    public ResponseEntity<MessageResponse> forgotPassword(@Valid @RequestBody ForgotPasswordRequest request) {
        // Always return the same generic message regardless of whether the email exists
        userService.initiatePasswordReset(request.email());
        return ResponseEntity.ok(new MessageResponse(
                "If an account exists for this email, a password reset link has been sent."));
    }

    @PostMapping("/reset-password")
    public ResponseEntity<MessageResponse> resetPassword(@Valid @RequestBody ResetPasswordRequest request) {
        userService.resetPassword(request.token(), request.newPassword());
        return ResponseEntity.ok(new MessageResponse("Password has been reset. You may now sign in with your new password."));
    }

    // ─── Cookie helpers ────────────────────────────────────────────────────────

    private void setAuthCookies(HttpServletResponse response, String accessToken, String refreshToken) {
        response.addCookie(buildCookie("access_token", accessToken, 24 * 60 * 60));
        response.addCookie(buildCookie("refresh_token", refreshToken, 7 * 24 * 60 * 60));
    }

    private void clearAuthCookies(HttpServletResponse response) {
        response.addCookie(buildCookie("access_token", "", 0));
        response.addCookie(buildCookie("refresh_token", "", 0));
    }

    private Cookie buildCookie(String name, String value, int maxAge) {
        Cookie cookie = new Cookie(name, value);
        cookie.setHttpOnly(true);
        cookie.setSecure(cookieSecure);
        cookie.setPath("/");
        cookie.setMaxAge(maxAge);
        cookie.setAttribute("SameSite", cookieSameSite);
        return cookie;
    }

    // ─── Request / Response Records ───────────────────────────────────────────

    public record SignupRequest(
            @NotBlank @Size(min = 2, max = 100) String name,
            @NotBlank @Email String email,
            @NotBlank @Size(min = 8, max = 128) String password
    ) {}

    public record LoginRequest(
            @NotBlank @Email String email,
            @NotBlank String password
    ) {}

    public record ForgotPasswordRequest(
            @NotBlank @Email String email
    ) {}

    public record ResetPasswordRequest(
            @NotBlank String token,
            @NotBlank @Size(min = 8, max = 128) String newPassword
    ) {}

    public record AuthResponse(UserDto user, String accessToken) {
        public AuthResponse(User user, String accessToken) {
            this(new UserDto(user.getId(), user.getName(), user.getEmail(), user.getRole(), user.getIsActive()), accessToken);
        }
    }

    public record UserDto(String id, String name, String email, String role, Boolean isActive) {}

    public record MessageResponse(String message) {}
}
