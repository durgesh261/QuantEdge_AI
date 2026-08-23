package com.quantedge.auth.service;

import com.quantedge.auth.entity.User;
import com.quantedge.auth.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Creates the DEVELOPER account on application startup if it does not exist.
 *
 * Credentials are sourced exclusively from environment variables:
 *   DEVELOPER_EMAIL           — developer login email
 *   DEVELOPER_INITIAL_PASSWORD — developer login password (used only to create the account; ignored if account already exists)
 *
 * If either variable is absent, initialization is skipped with a warning.
 * Do NOT hard-code credentials in this file or in application.yml.
 */
@Component
public class DeveloperAccountInitializer implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(DeveloperAccountInitializer.class);

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    @Value("${quantedge.developer.email:}")
    private String developerEmail;

    @Value("${quantedge.developer.initial-password:}")
    private String developerInitialPassword;

    public DeveloperAccountInitializer(UserRepository userRepository, PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        if (developerEmail == null || developerEmail.isBlank()) {
            log.warn("[DevInit] DEVELOPER_EMAIL not set — skipping developer account initialization.");
            return;
        }
        if (developerInitialPassword == null || developerInitialPassword.isBlank()) {
            log.warn("[DevInit] DEVELOPER_INITIAL_PASSWORD not set — skipping developer account initialization.");
            return;
        }

        String normalizedEmail = developerEmail.trim().toLowerCase();

        if (userRepository.findByEmail(normalizedEmail).isPresent()) {
            log.info("[DevInit] Developer account already exists for {}. Skipping.", normalizedEmail);
            return;
        }

        User developer = User.builder()
                .name("QuantEdge System Developer")
                .email(normalizedEmail)
                .passwordHash(passwordEncoder.encode(developerInitialPassword))
                .role("DEVELOPER")
                .isActive(true)
                .emailVerified(true)
                .build();

        userRepository.save(developer);
        log.info("[DevInit] Developer account created for {}", normalizedEmail);
    }
}
