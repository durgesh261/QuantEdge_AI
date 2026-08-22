package com.quantedge.exchange.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;

@Service
public class DeltaCredentialService {

    private static final Logger log = LoggerFactory.getLogger(DeltaCredentialService.class);
    private static final String ALGORITHM = "AES/GCM/NoPadding";
    private static final int GCM_IV_LENGTH = 12; // 96 bits
    private static final int GCM_TAG_LENGTH = 128; // 128 bits authentication tag
    private static final int MIN_KEY_LENGTH = 16; // Minimum 128-bit input entropy for SHA-256 derivation

    private final SecretKey secretKey;
    private final SecureRandom secureRandom;

    public DeltaCredentialService(@Value("${quantedge.encryption.key:${ENCRYPTION_KEY:}}") String encryptionKey) {
        if (encryptionKey == null || encryptionKey.trim().isEmpty()) {
            throw new IllegalStateException(
                    "Delta credential encryption key (ENCRYPTION_KEY / quantedge.encryption.key) is not configured. " +
                    "Application must fail closed without an explicit encryption key."
            );
        }
        String trimmedKey = encryptionKey.trim();
        if (trimmedKey.length() < MIN_KEY_LENGTH) {
            throw new IllegalArgumentException(
                    "Delta credential encryption key (ENCRYPTION_KEY) must be at least " + MIN_KEY_LENGTH + " characters."
            );
        }
        this.secretKey = deriveKey(trimmedKey);
        this.secureRandom = new SecureRandom();
        log.info("DeltaCredentialService initialized with dedicated ENCRYPTION_KEY (AES-256-GCM).");
    }

    private SecretKey deriveKey(String rawKey) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] keyBytes = digest.digest(rawKey.getBytes(StandardCharsets.UTF_8));
            return new SecretKeySpec(keyBytes, "AES");
        } catch (Exception e) {
            throw new IllegalStateException("Failed to derive AES-256 encryption key from ENCRYPTION_KEY", e);
        }
    }

    public String encrypt(String plaintext) {
        if (plaintext == null || plaintext.trim().isEmpty()) {
            return "";
        }
        try {
            byte[] iv = new byte[GCM_IV_LENGTH];
            secureRandom.nextBytes(iv);

            Cipher cipher = Cipher.getInstance(ALGORITHM);
            GCMParameterSpec parameterSpec = new GCMParameterSpec(GCM_TAG_LENGTH, iv);
            cipher.init(Cipher.ENCRYPT_MODE, secretKey, parameterSpec);

            byte[] cipherText = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));

            ByteBuffer byteBuffer = ByteBuffer.allocate(iv.length + cipherText.length);
            byteBuffer.put(iv);
            byteBuffer.put(cipherText);

            return Base64.getEncoder().encodeToString(byteBuffer.array());
        } catch (Exception e) {
            log.error("Failed to encrypt credential");
            throw new RuntimeException("Failed to encrypt credential", e);
        }
    }

    public String decrypt(String encryptedBase64) {
        if (encryptedBase64 == null || encryptedBase64.trim().isEmpty()) {
            return "";
        }
        try {
            byte[] decoded = Base64.getDecoder().decode(encryptedBase64);
            if (decoded.length < GCM_IV_LENGTH + 16) {
                throw new IllegalArgumentException("Encrypted data too short for AES-GCM");
            }

            ByteBuffer byteBuffer = ByteBuffer.wrap(decoded);
            byte[] iv = new byte[GCM_IV_LENGTH];
            byteBuffer.get(iv);

            byte[] cipherText = new byte[byteBuffer.remaining()];
            byteBuffer.get(cipherText);

            Cipher cipher = Cipher.getInstance(ALGORITHM);
            GCMParameterSpec parameterSpec = new GCMParameterSpec(GCM_TAG_LENGTH, iv);
            cipher.init(Cipher.DECRYPT_MODE, secretKey, parameterSpec);

            byte[] plainText = cipher.doFinal(cipherText);
            return new String(plainText, StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            log.warn("Invalid encrypted credential format");
            throw e;
        } catch (Exception e) {
            log.warn("Failed to decrypt credential (authentication tag failure or key mismatch)");
            throw new SecurityException("Failed to decrypt credential. Ciphertext may be corrupted, tampered with, or encryption key is incorrect.", e);
        }
    }

    public static String maskSecret(String secret) {
        if (secret == null || secret.isEmpty()) {
            return "";
        }
        String trimmed = secret.trim();
        if (trimmed.length() <= 8) {
            return "***";
        }
        return trimmed.substring(0, 4) + "***" + trimmed.substring(trimmed.length() - 4);
    }
}
