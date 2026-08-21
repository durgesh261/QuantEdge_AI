"""
Security utilities for credentials and logging in QuantEdge AI execution layer.

Provides:
- AES-256-GCM authenticated encryption and decryption for API keys/secrets.
- Secret masking / redaction helpers to prevent sensitive data leaks in logs and exceptions.
- Key derivation from master secrets.
"""

import base64
import hashlib
import os
from typing import Optional, Iterable
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def mask_secret(secret: Optional[str], visible_prefix: int = 4, visible_suffix: int = 4) -> str:
    """Mask a sensitive secret for safe display/logging.

    Examples:
        mask_secret("delta_api_key_123456789") -> "delt***6789"
        mask_secret("short") -> "***"
        mask_secret(None) -> ""
    """
    if not secret:
        return ""
    s = str(secret).strip()
    if len(s) <= visible_prefix + visible_suffix:
        return "***"
    return f"{s[:visible_prefix]}***{s[-visible_suffix:]}"


def derive_key(master_secret: str) -> bytes:
    """Derive a 256-bit (32-byte) key from master secret using SHA-256."""
    return hashlib.sha256(master_secret.encode("utf-8")).digest()


def encrypt_credential(plaintext: str, key_or_secret: str) -> str:
    """Encrypt plaintext string using AES-256-GCM.

    Returns base64-encoded string: base64(nonce [12 bytes] + ciphertext_with_tag).
    """
    if not plaintext:
        return ""
    key = derive_key(key_or_secret) if len(key_or_secret.encode("utf-8")) != 32 else key_or_secret.encode("utf-8")
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit standard GCM nonce
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("utf-8")


def decrypt_credential(encrypted_b64: str, key_or_secret: str) -> str:
    """Decrypt base64-encoded AES-256-GCM ciphertext."""
    if not encrypted_b64:
        return ""
    key = derive_key(key_or_secret) if len(key_or_secret.encode("utf-8")) != 32 else key_or_secret.encode("utf-8")
    data = base64.b64decode(encrypted_b64.encode("utf-8"))
    if len(data) < 28:  # 12-byte nonce + 16-byte tag minimum
        raise ValueError("Encrypted data is too short to be valid AES-256-GCM ciphertext")
    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(key)
    plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext_bytes.decode("utf-8")


def sanitize_text(text: str, secrets_to_redact: Optional[Iterable[Optional[str]]] = None) -> str:
    """Sanitize string by replacing occurrences of any sensitive secrets."""
    if not text:
        return ""
    sanitized = text
    if secrets_to_redact:
        for secret in secrets_to_redact:
            if secret and len(secret) > 3 and secret in sanitized:
                sanitized = sanitized.replace(secret, mask_secret(secret))
    return sanitized
