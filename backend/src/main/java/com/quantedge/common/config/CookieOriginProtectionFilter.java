package com.quantedge.common.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Rejects cross-origin unsafe browser requests that carry an authentication cookie.
 * Engine-to-backend requests do not carry browser cookies and are authenticated
 * independently with X-Engine-Api-Key.
 *
 * Uses proper URI parsing for Origin/Referer validation to prevent prefix-matching
 * bypasses (e.g., https://localhost:3100.attacker.com must NOT match https://localhost:3100).
 */
@Component
public class CookieOriginProtectionFilter extends OncePerRequestFilter {

    private final Set<String> allowedOrigins;

    public CookieOriginProtectionFilter(@Value("${quantedge.cors.allowed-origins}") String origins) {
        this.allowedOrigins = Arrays.stream(origins.split(","))
                .map(String::trim)
                .filter(value -> !value.isEmpty())
                .collect(Collectors.toUnmodifiableSet());
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        if (isUnsafe(request.getMethod()) && hasAccessCookie(request)) {
            String origin = request.getHeader("Origin");
            String referer = request.getHeader("Referer");
            boolean validOrigin = origin != null && allowedOrigins.contains(origin);
            boolean validReferer = referer != null && isValidReferer(referer);
            if (!validOrigin && !validReferer) {
                response.sendError(HttpServletResponse.SC_FORBIDDEN, "Cross-origin cookie request rejected");
                return;
            }
        }
        filterChain.doFilter(request, response);
    }

    /**
     * Validates that the referer URL's origin exactly matches an allowed origin.
     * Uses URI parsing to extract the origin (scheme + host + port) and compares
     * against the configured allowed origins set. This prevents prefix-matching
     * bypasses like https://localhost:3100.attacker.com matching https://localhost:3100.
     */
    private boolean isValidReferer(String referer) {
        try {
            URI uri = new URI(referer);
            String refererOrigin = uri.getScheme() + "://" + uri.getHost()
                    + (uri.getPort() != -1 ? ":" + uri.getPort() : "");
            return allowedOrigins.contains(refererOrigin);
        } catch (URISyntaxException e) {
            return false;
        }
    }

    private boolean isUnsafe(String method) {
        return !HttpMethod.GET.matches(method)
                && !HttpMethod.HEAD.matches(method)
                && !HttpMethod.OPTIONS.matches(method);
    }

    private boolean hasAccessCookie(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies == null) {
            return false;
        }
        return Arrays.stream(cookies).anyMatch(cookie -> "access_token".equals(cookie.getName()));
    }
}
