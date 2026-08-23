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
import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Rejects cross-origin unsafe browser requests that carry an authentication cookie.
 * Engine-to-backend requests do not carry browser cookies and are authenticated
 * independently with X-Engine-Api-Key.
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
            boolean validReferer = referer != null && allowedOrigins.stream().anyMatch(referer::startsWith);
            if (!validOrigin && !validReferer) {
                response.sendError(HttpServletResponse.SC_FORBIDDEN, "Cross-origin cookie request rejected");
                return;
            }
        }
        filterChain.doFilter(request, response);
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
