package com.quantedge.auth.controller;

import com.quantedge.auth.service.UserService;
import com.quantedge.common.config.JwtTokenProvider;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    private final UserService userService;
    private final JwtTokenProvider jwtTokenProvider;

    public AuthController(UserService userService, JwtTokenProvider jwtTokenProvider) {
        this.userService = userService;
        this.jwtTokenProvider = jwtTokenProvider;
    }

    @PostMapping("/signup")
    public ResponseEntity<AuthResponse> signup(@Valid @RequestBody SignupRequest request, HttpServletResponse response) {
        UserService.AuthResult result = userService.signup(request.name(), request.email(), request.password());
        setAuthCookies(response, result.accessToken(), result.refreshToken());
        return ResponseEntity.ok(new AuthResponse(result.user(), result.accessToken()));
    }

    @PostMapping("/login")
    public ResponseEntity<AuthResponse> login(@Valid @RequestBody LoginRequest request, HttpServletResponse response) {
        UserService.AuthResult result = userService.login(request.email(), request.password());
        setAuthCookies(response, result.accessToken(), result.refreshToken());
        return ResponseEntity.ok(new AuthResponse(result.user(), result.accessToken()));
    }

    @PostMapping("/logout")
    public ResponseEntity<Void> logout(HttpServletResponse response) {
        clearAuthCookies(response);
        return ResponseEntity.ok().build();
    }

    @PostMapping("/refresh")
    public ResponseEntity<AuthResponse> refresh(@CookieValue(name = "refresh_token", required = false) String refreshToken,
                                                HttpServletResponse response) {
        if (refreshToken == null) {
            return ResponseEntity.status(401).build();
        }

        UserService.AuthResult result = userService.refreshToken(refreshToken);
        setAuthCookies(response, result.accessToken(), result.refreshToken());
        return ResponseEntity.ok(new AuthResponse(result.user(), result.accessToken()));
    }

    @GetMapping("/me")
    public ResponseEntity<AuthResponse> me(@RequestAttribute("currentUser") com.quantedge.auth.entity.User user) {
        return ResponseEntity.ok(new AuthResponse(user, null));
    }

    private void setAuthCookies(HttpServletResponse response, String accessToken, String refreshToken) {
        Cookie accessCookie = new Cookie("access_token", accessToken);
        accessCookie.setHttpOnly(true);
        accessCookie.setSecure(false);
        accessCookie.setPath("/");
        accessCookie.setMaxAge(24 * 60 * 60);
        accessCookie.setAttribute("SameSite", "Lax");
        response.addCookie(accessCookie);

        Cookie refreshCookie = new Cookie("refresh_token", refreshToken);
        refreshCookie.setHttpOnly(true);
        refreshCookie.setSecure(false);
        refreshCookie.setPath("/");
        refreshCookie.setMaxAge(7 * 24 * 60 * 60);
        refreshCookie.setAttribute("SameSite", "Lax");
        response.addCookie(refreshCookie);
    }

    private void clearAuthCookies(HttpServletResponse response) {
        Cookie accessCookie = new Cookie("access_token", "");
        accessCookie.setHttpOnly(true);
        accessCookie.setSecure(false);
        accessCookie.setPath("/");
        accessCookie.setMaxAge(0);
        response.addCookie(accessCookie);

        Cookie refreshCookie = new Cookie("refresh_token", "");
        refreshCookie.setHttpOnly(true);
        refreshCookie.setSecure(false);
        refreshCookie.setPath("/");
        refreshCookie.setMaxAge(0);
        response.addCookie(refreshCookie);
    }

    public record SignupRequest(
            @NotBlank @Size(min = 2, max = 100) String name,
            @NotBlank @Email String email,
            @NotBlank @Size(min = 8, max = 128) String password
    ) {}

    public record LoginRequest(
            @NotBlank @Email String email,
            @NotBlank String password
    ) {}

    public record AuthResponse(
            UserDto user,
            String accessToken
    ) {
        public AuthResponse(com.quantedge.auth.entity.User user, String accessToken) {
            this(new UserDto(user.getId(), user.getName(), user.getEmail(), user.getRole(), user.getIsActive()), accessToken);
        }
    }

    public record UserDto(
            String id,
            String name,
            String email,
            String role,
            Boolean isActive
    ) {}
}