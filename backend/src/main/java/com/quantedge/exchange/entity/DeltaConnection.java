package com.quantedge.exchange.entity;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.time.Instant;

@Entity
@Table(name = "delta_connections")
public class DeltaConnection extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false)
    private TradingAccount tradingAccount;

    @Column(name = "environment", nullable = false, length = 20)
    private String environment = "LIVE";

    @Column(name = "encrypted_api_key", nullable = false, columnDefinition = "TEXT")
    private String encryptedApiKey;

    @Column(name = "encrypted_api_secret", nullable = false, columnDefinition = "TEXT")
    private String encryptedApiSecret;

    @Column(name = "connection_status", nullable = false, length = 20)
    private String connectionStatus = "DISCONNECTED";

    @Column(name = "last_connected_at")
    private Instant lastConnectedAt;

    @Column(name = "last_error", columnDefinition = "TEXT")
    private String lastError;

    public DeltaConnection() {}

    public DeltaConnection(TradingAccount tradingAccount, String environment, String encryptedApiKey, String encryptedApiSecret) {
        this.tradingAccount = tradingAccount;
        this.environment = environment;
        this.encryptedApiKey = encryptedApiKey;
        this.encryptedApiSecret = encryptedApiSecret;
    }

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount tradingAccount) { this.tradingAccount = tradingAccount; }

    public String getEnvironment() { return environment; }
    public void setEnvironment(String environment) { this.environment = environment; }

    public String getEncryptedApiKey() { return encryptedApiKey; }
    public void setEncryptedApiKey(String encryptedApiKey) { this.encryptedApiKey = encryptedApiKey; }

    public String getEncryptedApiSecret() { return encryptedApiSecret; }
    public void setEncryptedApiSecret(String encryptedApiSecret) { this.encryptedApiSecret = encryptedApiSecret; }

    public String getConnectionStatus() { return connectionStatus; }
    public void setConnectionStatus(String connectionStatus) { this.connectionStatus = connectionStatus; }

    public Instant getLastConnectedAt() { return lastConnectedAt; }
    public void setLastConnectedAt(Instant lastConnectedAt) { this.lastConnectedAt = lastConnectedAt; }

    public String getLastError() { return lastError; }
    public void setLastError(String lastError) { this.lastError = lastError; }
}
