package com.quantedge.trading.entity;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.common.entity.BaseEntity;
import jakarta.persistence.*;

import java.time.Instant;

/**
 * Database-enforced single-trade-at-a-time lock per trading account.
 *
 * <p>The partial unique index {@code idx_active_trade_locks_account_active}
 * (WHERE released_at IS NULL) prevents more than one active row per
 * {@code trading_account_id} at the database level. This constraint survives
 * every process restart, crash, WebSocket reconnect, or duplicate signal —
 * no in-memory ConcurrentHashMap can provide this guarantee.</p>
 *
 * <h3>Lifecycle:</h3>
 * <ol>
 *   <li>INSERT a row with {@code released_at = NULL} → lock acquired.</li>
 *   <li>UPDATE {@code released_at = NOW()} → lock released.</li>
 *   <li>Rows are kept permanently for audit and reconciliation history.</li>
 * </ol>
 */
@Entity
@Table(
    name = "active_trade_locks",
    indexes = {
        @Index(name = "idx_active_trade_locks_setup_id", columnList = "setup_id"),
        @Index(name = "idx_active_trade_locks_released_at", columnList = "released_at")
    }
)
public class ActiveTradeLock extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "trading_account_id", nullable = false)
    private TradingAccount tradingAccount;

    @Column(name = "setup_id", nullable = false, length = 100)
    private String setupId;

    @Column(name = "symbol", nullable = false, length = 20)
    private String symbol;

    /** Mirrors the Python TradeLifecycleState enum string. */
    @Column(name = "lifecycle_state", nullable = false, length = 30)
    private String lifecycleState = "ENTRY_SUBMITTED";

    @Column(name = "acquired_at", nullable = false)
    private Instant acquiredAt = Instant.now();

    /**
     * NULL → lock is active. Non-null → lock has been released.
     * The partial unique index uses (trading_account_id WHERE released_at IS NULL).
     */
    @Column(name = "released_at")
    private Instant releasedAt;

    @Column(name = "release_reason", length = 50)
    private String releaseReason;

    /** True if the lock was force-released (e.g. order not found on exchange after restart). */
    @Column(name = "force_released", nullable = false)
    private Boolean forceReleased = false;

    public ActiveTradeLock() {}

    public ActiveTradeLock(TradingAccount tradingAccount, String setupId, String symbol) {
        this.tradingAccount = tradingAccount;
        this.setupId = setupId;
        this.symbol = symbol;
        this.lifecycleState = "ENTRY_SUBMITTED";
        this.acquiredAt = Instant.now();
        this.releasedAt = null;
        this.forceReleased = false;
    }

    public boolean isActive() {
        return releasedAt == null;
    }

    public TradingAccount getTradingAccount() { return tradingAccount; }
    public void setTradingAccount(TradingAccount t) { this.tradingAccount = t; }

    public String getSetupId() { return setupId; }
    public void setSetupId(String s) { this.setupId = s; }

    public String getSymbol() { return symbol; }
    public void setSymbol(String s) { this.symbol = s; }

    public String getLifecycleState() { return lifecycleState; }
    public void setLifecycleState(String s) { this.lifecycleState = s; }

    public Instant getAcquiredAt() { return acquiredAt; }
    public void setAcquiredAt(Instant t) { this.acquiredAt = t; }

    public Instant getReleasedAt() { return releasedAt; }
    public void setReleasedAt(Instant t) { this.releasedAt = t; }

    public String getReleaseReason() { return releaseReason; }
    public void setReleaseReason(String s) { this.releaseReason = s; }

    public Boolean getForceReleased() { return forceReleased; }
    public void setForceReleased(Boolean b) { this.forceReleased = b; }
}
