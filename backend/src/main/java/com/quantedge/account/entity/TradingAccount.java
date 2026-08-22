package com.quantedge.account.entity;

import com.quantedge.auth.entity.User;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "trading_accounts")
public class TradingAccount extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Column(name = "name", nullable = false, length = 100)
    private String name;

    @Column(name = "account_type", nullable = false, length = 20)
    private String accountType = "LIVE";

    @Column(name = "is_active", nullable = false)
    private Boolean isActive = true;

    @Column(name = "is_default", nullable = false)
    private Boolean isDefault = false;

    @Column(name = "base_currency", nullable = false, length = 10)
    private String baseCurrency = "USDT";

    @Column(name = "starting_balance", precision = 20, scale = 8, nullable = false)
    private BigDecimal startingBalance = BigDecimal.ZERO;

    @Column(name = "current_balance", precision = 20, scale = 8, nullable = false)
    private BigDecimal currentBalance = BigDecimal.ZERO;

    @Column(name = "total_equity", precision = 20, scale = 8)
    private BigDecimal totalEquity = BigDecimal.ZERO;

    @Column(name = "available_balance", precision = 20, scale = 8)
    private BigDecimal availableBalance = BigDecimal.ZERO;

    @Column(name = "margin_used", precision = 20, scale = 8)
    private BigDecimal marginUsed = BigDecimal.ZERO;

    /**
     * FAIL-SAFE DEFAULT: algo_enabled is always false on account creation.
     * Must be explicitly enabled by an authorized administrator action.
     */
    @Column(name = "algo_enabled", nullable = false)
    private Boolean algoEnabled = false;

    /**
     * FAIL-SAFE DEFAULT: kill_switch_active is always true on account creation.
     * Must be explicitly disabled by an authorized administrator action.
     */
    @Column(name = "kill_switch_active", nullable = false)
    private Boolean killSwitchActive = true;

    @Column(name = "last_synced_at")
    private Instant lastSyncedAt;

    public TradingAccount() {
        // Fail-safe defaults enforced by field initializers:
        // algoEnabled = false, killSwitchActive = true
    }

    /**
     * Primary constructor. Always enforces fail-safe defaults:
     * algoEnabled = false, killSwitchActive = true.
     * baseCurrency is expected as the 4th argument for type safety.
     */
    public TradingAccount(User user, String name, String accountType, String baseCurrency) {
        this.user = user;
        this.name = name;
        this.accountType = accountType;
        this.baseCurrency = baseCurrency;
        // CRITICAL: fail-safe defaults — never change these in a constructor
        this.algoEnabled = false;
        this.killSwitchActive = true;
        this.isActive = true;
    }

    public User getUser() { return user; }
    public void setUser(User user) { this.user = user; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getAccountType() { return accountType; }
    public void setAccountType(String accountType) { this.accountType = accountType; }

    public Boolean getIsActive() { return isActive; }
    public void setIsActive(Boolean active) { isActive = active; }

    public Boolean getIsDefault() { return isDefault; }
    public void setIsDefault(Boolean aDefault) { isDefault = aDefault; }

    public String getBaseCurrency() { return baseCurrency; }
    public void setBaseCurrency(String baseCurrency) { this.baseCurrency = baseCurrency; }

    public BigDecimal getStartingBalance() { return startingBalance; }
    public void setStartingBalance(BigDecimal startingBalance) { this.startingBalance = startingBalance; }

    public BigDecimal getCurrentBalance() { return currentBalance; }
    public void setCurrentBalance(BigDecimal currentBalance) { this.currentBalance = currentBalance; }

    public BigDecimal getTotalEquity() { return totalEquity; }
    public void setTotalEquity(BigDecimal totalEquity) { this.totalEquity = totalEquity; }

    public BigDecimal getAvailableBalance() { return availableBalance; }
    public void setAvailableBalance(BigDecimal availableBalance) { this.availableBalance = availableBalance; }

    public BigDecimal getMarginUsed() { return marginUsed; }
    public void setMarginUsed(BigDecimal marginUsed) { this.marginUsed = marginUsed; }

    public Boolean getAlgoEnabled() { return algoEnabled; }
    public void setAlgoEnabled(Boolean algoEnabled) { this.algoEnabled = algoEnabled; }

    public Boolean getKillSwitchActive() { return killSwitchActive; }
    public void setKillSwitchActive(Boolean killSwitchActive) { this.killSwitchActive = killSwitchActive; }

    public Instant getLastSyncedAt() { return lastSyncedAt; }
    public void setLastSyncedAt(Instant lastSyncedAt) { this.lastSyncedAt = lastSyncedAt; }
}
