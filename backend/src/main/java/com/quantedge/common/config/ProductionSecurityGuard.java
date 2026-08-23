package com.quantedge.common.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

/** Fails closed when a deployment explicitly enables the production profile. */
@Component
public class ProductionSecurityGuard implements ApplicationRunner {

    private final Environment environment;

    @Value("${quantedge.jwt.secret:}")
    private String jwtSecret;

    @Value("${quantedge.encryption.key:}")
    private String encryptionKey;

    @Value("${quantedge.python-engine.api-key:}")
    private String engineApiKey;

    @Value("${quantedge.auth.cookie-secure:false}")
    private boolean cookieSecure;

    public ProductionSecurityGuard(Environment environment) {
        this.environment = environment;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (!environment.matchesProfiles("prod", "production")) {
            return;
        }
        require("JWT_SECRET", jwtSecret, "dev-secret-key-change-in-production");
        require("ENCRYPTION_KEY", encryptionKey, null);
        require("PYTHON_ENGINE_API_KEY", engineApiKey, null);
        if (!cookieSecure) {
            throw new IllegalStateException("COOKIE_SECURE=true is required in production");
        }
    }

    private void require(String name, String value, String forbiddenValue) {
        if (value == null || value.isBlank() || (forbiddenValue != null && value.contains(forbiddenValue))) {
            throw new IllegalStateException(name + " must be securely configured in production");
        }
    }
}
