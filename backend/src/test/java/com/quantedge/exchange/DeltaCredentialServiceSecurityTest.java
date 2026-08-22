package com.quantedge.exchange;

import com.quantedge.exchange.service.DeltaCredentialService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Base64;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Security test suite for DeltaCredentialService.
 * Validates dedicated ENCRYPTION_KEY usage, AES-256-GCM integrity, fail-closed mechanics,
 * and complete decoupling from JWT_SECRET.
 */
class DeltaCredentialServiceSecurityTest {

    private static final String VALID_TEST_KEY = "test-encryption-key-minimum-256-bits-entropy-fixed-fixture";
    private static final String SYNTHETIC_API_KEY = "TEST_SYNTHETIC_API_KEY_00000000000000000001";
    private static final String SYNTHETIC_API_SECRET = "TEST_SYNTHETIC_API_SECRET_0000000000000000000000000000000001";

    @Test
    @DisplayName("A. Round-trip: Encrypt and decrypt correctly restores synthetic credentials")
    void testRoundTripEncryptionDecryption() {
        DeltaCredentialService service = new DeltaCredentialService(VALID_TEST_KEY);

        String encryptedKey = service.encrypt(SYNTHETIC_API_KEY);
        String encryptedSecret = service.encrypt(SYNTHETIC_API_SECRET);

        assertNotNull(encryptedKey);
        assertNotNull(encryptedSecret);

        String decryptedKey = service.decrypt(encryptedKey);
        String decryptedSecret = service.decrypt(encryptedSecret);

        assertEquals(SYNTHETIC_API_KEY, decryptedKey);
        assertEquals(SYNTHETIC_API_SECRET, decryptedSecret);
    }

    @Test
    @DisplayName("B. Ciphertext protection: Ciphertext differs from plaintext and uses random IVs")
    void testCiphertextProtectionAndRandomIV() {
        DeltaCredentialService service = new DeltaCredentialService(VALID_TEST_KEY);

        String cipher1 = service.encrypt(SYNTHETIC_API_KEY);
        String cipher2 = service.encrypt(SYNTHETIC_API_KEY);

        // Ciphertext must never equal plaintext
        assertNotEquals(SYNTHETIC_API_KEY, cipher1);
        assertNotEquals(SYNTHETIC_API_KEY, cipher2);

        // AES-256-GCM with secure random 12-byte IV must produce distinct ciphertexts for identical plaintext
        assertNotEquals(cipher1, cipher2);

        // Both distinct ciphertexts must decrypt to the exact same plaintext
        assertEquals(SYNTHETIC_API_KEY, service.decrypt(cipher1));
        assertEquals(SYNTHETIC_API_KEY, service.decrypt(cipher2));
    }

    @Test
    @DisplayName("C. Integrity protection: Tampered ciphertext or corrupted tag fails decryption safely")
    void testIntegrityProtectionAndTamperResistance() {
        DeltaCredentialService service = new DeltaCredentialService(VALID_TEST_KEY);
        String validCipherBase64 = service.encrypt(SYNTHETIC_API_KEY);

        byte[] rawBytes = Base64.getDecoder().decode(validCipherBase64);
        // Tamper with the last byte (authentication tag)
        rawBytes[rawBytes.length - 1] ^= 0xFF;
        String tamperedCipherBase64 = Base64.getEncoder().encodeToString(rawBytes);

        // Decryption must fail with SecurityException and must never return corrupted plaintext
        SecurityException ex = assertThrows(SecurityException.class, () -> service.decrypt(tamperedCipherBase64));
        assertFalse(ex.getMessage().contains(SYNTHETIC_API_KEY), "Exception message must never expose raw credentials");
    }

    @Test
    @DisplayName("C2. Integrity protection: Truncated ciphertext fails with IllegalArgumentException")
    void testTruncatedCiphertextFails() {
        DeltaCredentialService service = new DeltaCredentialService(VALID_TEST_KEY);
        byte[] tooShort = new byte[10]; // Less than IV (12) + Tag (16) = 28 bytes
        String shortCipher = Base64.getEncoder().encodeToString(tooShort);

        assertThrows(IllegalArgumentException.class, () -> service.decrypt(shortCipher));
    }

    @Test
    @DisplayName("D. Missing key: DeltaCredentialService initialization fails closed when ENCRYPTION_KEY is missing or blank")
    void testMissingEncryptionKeyFailsClosed() {
        // Null key
        assertThrows(IllegalStateException.class, () -> new DeltaCredentialService(null));

        // Empty string
        assertThrows(IllegalStateException.class, () -> new DeltaCredentialService(""));

        // Whitespace only
        assertThrows(IllegalStateException.class, () -> new DeltaCredentialService("   "));
    }

    @Test
    @DisplayName("E. Invalid key: Key with insufficient entropy (<16 chars) is rejected")
    void testShortEncryptionKeyFails() {
        assertThrows(IllegalArgumentException.class, () -> new DeltaCredentialService("short-key"));
    }

    @Test
    @DisplayName("F. Decoupling: Key independence and isolation from unrelated keys")
    void testKeyIndependence() {
        String keyA = "encryption-key-environment-alpha-32bytes-entropy!";
        String keyB = "encryption-key-environment-bravo-32bytes-entropy!";

        DeltaCredentialService serviceA = new DeltaCredentialService(keyA);
        DeltaCredentialService serviceB = new DeltaCredentialService(keyB);

        String cipherA = serviceA.encrypt(SYNTHETIC_API_KEY);

        // Service A decrypts its own ciphertext
        assertEquals(SYNTHETIC_API_KEY, serviceA.decrypt(cipherA));

        // Service B (different key) fails to decrypt Service A's ciphertext
        assertThrows(SecurityException.class, () -> serviceB.decrypt(cipherA));
    }

    @Test
    @DisplayName("G. Masking helper: maskSecret masks middle characters safely")
    void testMaskSecret() {
        assertEquals("TEST***0001", DeltaCredentialService.maskSecret(SYNTHETIC_API_KEY));
        assertEquals("***", DeltaCredentialService.maskSecret("short"));
        assertEquals("", DeltaCredentialService.maskSecret(""));
        assertEquals("", DeltaCredentialService.maskSecret(null));
    }
}
