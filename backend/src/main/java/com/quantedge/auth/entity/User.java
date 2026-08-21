package com.quantedge.auth.entity;

import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.UserDetails;

import java.time.Instant;
import java.util.Collection;
import java.util.List;

@Entity
@Table(name = "users", uniqueConstraints = {
        @UniqueConstraint(name = "uk_users_email", columnNames = "email")
})
public class User extends BaseEntity implements UserDetails {

    @Column(name = "email", nullable = false, length = 255)
    private String email;

    @Column(name = "password_hash", nullable = false)
    private String passwordHash;

    @Column(name = "name", nullable = false, length = 100)
    private String name;

    @Column(name = "is_active", nullable = false)
    private Boolean isActive = true;

    @Column(name = "email_verified", nullable = false)
    private Boolean emailVerified = false;

    @Column(name = "last_login_at")
    private Instant lastLoginAt;

    public User() {}

    public User(String email, String passwordHash, String name, Boolean isActive, Boolean emailVerified, Instant lastLoginAt) {
        this.email = email;
        this.passwordHash = passwordHash;
        this.name = name;
        this.isActive = isActive != null ? isActive : true;
        this.emailVerified = emailVerified != null ? emailVerified : false;
        this.lastLoginAt = lastLoginAt;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder {
        private String email;
        private String passwordHash;
        private String name;
        private Boolean isActive = true;
        private Boolean emailVerified = false;
        private Instant lastLoginAt;

        public Builder email(String email) { this.email = email; return this; }
        public Builder passwordHash(String passwordHash) { this.passwordHash = passwordHash; return this; }
        public Builder name(String name) { this.name = name; return this; }
        public Builder isActive(Boolean isActive) { this.isActive = isActive; return this; }
        public Builder emailVerified(Boolean emailVerified) { this.emailVerified = emailVerified; return this; }
        public Builder lastLoginAt(Instant lastLoginAt) { this.lastLoginAt = lastLoginAt; return this; }

        public User build() {
            return new User(email, passwordHash, name, isActive, emailVerified, lastLoginAt);
        }
    }

    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }

    public String getPasswordHash() { return passwordHash; }
    public void setPasswordHash(String passwordHash) { this.passwordHash = passwordHash; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public Boolean getIsActive() { return isActive; }
    public void setIsActive(Boolean active) { isActive = active; }

    public Boolean getEmailVerified() { return emailVerified; }
    public void setEmailVerified(Boolean emailVerified) { this.emailVerified = emailVerified; }

    public Instant getLastLoginAt() { return lastLoginAt; }
    public void setLastLoginAt(Instant lastLoginAt) { this.lastLoginAt = lastLoginAt; }

    @Override
    public Collection<? extends GrantedAuthority> getAuthorities() {
        return List.of(new SimpleGrantedAuthority("ROLE_USER"));
    }

    @Override
    public String getPassword() {
        return passwordHash;
    }

    @Override
    public String getUsername() {
        return email;
    }

    @Override
    public boolean isAccountNonExpired() {
        return true;
    }

    @Override
    public boolean isAccountNonLocked() {
        return isActive != null && isActive;
    }

    @Override
    public boolean isCredentialsNonExpired() {
        return true;
    }

    @Override
    public boolean isEnabled() {
        return isActive != null && isActive && emailVerified != null && emailVerified;
    }
}