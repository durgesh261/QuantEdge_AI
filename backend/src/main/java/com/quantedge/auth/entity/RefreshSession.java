package com.quantedge.auth.entity;

import jakarta.persistence.*;

import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;

/**
 * Persistent server-side record for a refresh-token session.
 *
 * The raw refresh token is NEVER stored. Only a SHA-256 hex digest of the
 * issued refresh JWT is persisted. This enables:
 *  - server-side revocation on logout / password reset / account disable
 *  - rotation with replay detection (old token becomes unusable once rotated)
 *  - multi-session management per user
 */
@Entity
@Table(name = "refresh_sessions", indexes = {
        @Index(name = "idx_rs_token_hash", columnList = "token_hash"),
        @Index(name = "idx_rs_user_id", columnList = "user_id")
})
public class RefreshSession {

    @Id
    @org.hibernate.annotations.JavaType(com.quantedge.common.entity.StringAsUuidJavaType.class)
    @Column(name = "id", updatable = false, nullable = false)
    private String id;

    @Column(name = "token_hash", nullable = false, unique = true, length = 64)
    private String tokenHash;

    @org.hibernate.annotations.JavaType(com.quantedge.common.entity.StringAsUuidJavaType.class)
    @Column(name = "user_id", nullable = false)
    private String userId;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt = Instant.now();

    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;

    @Column(name = "revoked_at")
    private Instant revokedAt;

    /** SHA-256 hash of the replacement refresh token after rotation. */
    @Column(name = "replaced_by_hash", length = 64)
    private String replacedByHash;

    public RefreshSession() {}

    public RefreshSession(String tokenHash, String userId, Instant expiresAt) {
        this.tokenHash = tokenHash;
        this.userId = userId;
        this.expiresAt = expiresAt;
    }

    public String getId() { return id; }
    public String getTokenHash() { return tokenHash; }
    public String getUserId() { return userId; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getExpiresAt() { return expiresAt; }
    public Instant getRevokedAt() { return revokedAt; }
    public String getReplacedByHash() { return replacedByHash; }

    public boolean isRevoked() {
        return revokedAt != null;
    }

    public boolean isExpired() {
        return Instant.now().isAfter(expiresAt);
    }

    public boolean isActive() {
        return !isRevoked() && !isExpired();
    }

    public void revoke() {
        if (this.revokedAt == null) {
            this.revokedAt = Instant.now();
        }
    }

    public void revokeAndRotateTo(String replacementTokenHash) {
        revoke();
        this.replacedByHash = replacementTokenHash;
    }
}
