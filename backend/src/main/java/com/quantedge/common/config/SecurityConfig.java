package com.quantedge.common.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

/**
 * Spring Security configuration.
 *
 * With server.servlet.context-path=/api, Spring Security path matchers are relative to that
 * context path. So "/v1/auth/**" in a requestMatcher effectively means the actual URL
 * http://host:8080/api/v1/auth/**.
 */
@Configuration
@EnableWebSecurity
@EnableMethodSecurity(prePostEnabled = true)
public class SecurityConfig {

    private final JwtAuthenticationFilter jwtAuthenticationFilter;
    private final CookieOriginProtectionFilter cookieOriginProtectionFilter;

    public SecurityConfig(
            JwtAuthenticationFilter jwtAuthenticationFilter,
            CookieOriginProtectionFilter cookieOriginProtectionFilter
    ) {
        this.jwtAuthenticationFilter = jwtAuthenticationFilter;
        this.cookieOriginProtectionFilter = cookieOriginProtectionFilter;
    }

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
                .csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        // Auth endpoints — must NOT include the context-path prefix
                        .requestMatchers("/v1/auth/**").permitAll()
                        // Actuator health
                        .requestMatchers("/actuator/health/**", "/actuator/health").permitAll()
                        // Python engine proxy (public for diagnostics)
                        .requestMatchers("/engine/**").permitAll()
                        // Developer console — ROLE_DEVELOPER or ROLE_ADMIN only
                        .requestMatchers("/v1/developer/**").hasAnyRole("DEVELOPER", "ADMIN")
                        // All other endpoints require authentication
                        .anyRequest().authenticated()
                )
                .addFilterBefore(jwtAuthenticationFilter, UsernamePasswordAuthenticationFilter.class)
                .addFilterBefore(cookieOriginProtectionFilter, JwtAuthenticationFilter.class);

        return http.build();
    }

    @Bean
    public AuthenticationManager authenticationManager(AuthenticationConfiguration config) throws Exception {
        return config.getAuthenticationManager();
    }
}
