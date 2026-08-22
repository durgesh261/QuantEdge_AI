package com.quantedge.engine;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.trading.service.TradePersistenceService;
import com.quantedge.trading.service.TradePersistenceService.AccountStateSnapshot;
import com.quantedge.trading.service.TradePersistenceService.TradeOpenRequest;
import com.quantedge.trading.service.TradePersistenceService.TradeOpenResult;
import com.quantedge.trading.service.TradePersistenceService.TradeCloseRequest;
import com.quantedge.trading.service.TradePersistenceService.TradeCloseResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Internal REST API consumed exclusively by the Python trading engine.
 *
 * <h3>Architecture Contract:</h3>
 * <ul>
 *   <li>Python engine → this API → PostgreSQL (authoritative state)</li>
 *   <li>Frontend NEVER calls these endpoints directly</li>
 *   <li>Trading logic lives in Python; persistence lives in Java/PostgreSQL</li>
 *   <li>Credentials are held only by the Java backend (never in Python)</li>
 * </ul>
 *
 * <h3>Security:</h3>
 * Requests are authenticated via an internal API key header
 * ({@code X-Engine-Api-Key}) validated against {@code PYTHON_ENGINE_API_KEY} env var.
 * This endpoint family is NOT exposed to the public internet.
 */
@RestController
@RequestMapping("/api/engine")
public class EngineStateController {

    private static final Logger log = LoggerFactory.getLogger(EngineStateController.class);

    private final TradePersistenceService persistenceService;
    private final TradingAccountRepository accountRepository;

    public EngineStateController(
            TradePersistenceService persistenceService,
            TradingAccountRepository accountRepository
    ) {
        this.persistenceService = persistenceService;
        this.accountRepository = accountRepository;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // DTOs
    // ─────────────────────────────────────────────────────────────────────────

    public record StateResponse(
            String accountId,
            boolean hasActiveTrade,
            String activeSetupId,
            String activeSymbol,
            String activeLockState,
            String lockAcquiredAt,
            BigDecimal currentBalance,
            BigDecimal nextTradeCapital,
            BigDecimal latestPostTradeBalance,
            long totalClosedTrades,
            BigDecimal totalNetPnl,
            BigDecimal totalFeesPaid,
            boolean algoEnabled,
            boolean killSwitchActive
    ) {}

    public record TradeOpenApiRequest(
            String setupId,
            String symbol,
            String direction,
            BigDecimal entryPrice,
            BigDecimal quantity,
            Integer leverage,
            BigDecimal preTradeBalance,
            BigDecimal orderBlockUpper,
            BigDecimal orderBlockLower,
            BigDecimal stopLossPrice,
            BigDecimal takeProfitPrice,
            Integer configurationVersion,
            BigDecimal maxLossPct,
            BigDecimal targetRoePct
    ) {}

    public record TradeCloseApiRequest(
            String setupId,
            BigDecimal grossPnl,
            BigDecimal tradingFees,
            BigDecimal fundingCosts,
            BigDecimal otherCosts,
            BigDecimal exitPrice,
            String closeReason,
            BigDecimal authoritativeExchangeBalance,
            String exitOrderId
    ) {}

    public record ApiResult(boolean success, String error, Object data) {}

    // ─────────────────────────────────────────────────────────────────────────
    // Endpoints
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * GET /api/engine/state/{accountId}
     *
     * Called by Python engine on startup and after each scan cycle to get
     * the authoritative state snapshot from PostgreSQL.
     * Replaces the in-memory export_state/load_state pattern.
     */
    @GetMapping("/state/{accountId}")
    public ResponseEntity<StateResponse> getAccountState(@PathVariable String accountId) {
        try {
            AccountStateSnapshot snap = persistenceService.getAccountStateSnapshot(accountId);
            BigDecimal nextCapital = persistenceService.getNextTradeCapital(accountId);
            StateResponse resp = new StateResponse(
                    snap.accountId(),
                    snap.hasActiveTrade(),
                    snap.activeSetupId(),
                    snap.activeSymbol(),
                    snap.activeLockState(),
                    snap.lockAcquiredAt() != null ? snap.lockAcquiredAt().toString() : null,
                    snap.currentBalance(),
                    nextCapital,
                    snap.latestPostTradeBalance(),
                    snap.totalClosedTrades(),
                    snap.totalNetPnl(),
                    snap.totalFeesPaid(),
                    snap.algoEnabled(),
                    snap.killSwitchActive()
            );
            return ResponseEntity.ok(resp);
        } catch (IllegalArgumentException e) {
            log.warn("getAccountState: {}", e.getMessage());
            return ResponseEntity.notFound().build();
        } catch (Exception e) {
            log.error("getAccountState error for account {}", accountId, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        }
    }

    /**
     * POST /api/engine/trade/open/{accountId}
     *
     * Atomically acquires the DB trade lock and persists the trade open record.
     * Returns 409 CONFLICT if an active trade already exists for this account.
     * Idempotent: repeated calls with the same setupId return success.
     */
    @PostMapping("/trade/open/{accountId}")
    public ResponseEntity<ApiResult> openTrade(
            @PathVariable String accountId,
            @RequestBody TradeOpenApiRequest req
    ) {
        try {
            TradeOpenRequest openReq = new TradeOpenRequest(
                    accountId, req.setupId(), req.symbol(), req.direction(),
                    req.entryPrice(), req.quantity(), req.leverage(), req.preTradeBalance(),
                    req.orderBlockUpper(), req.orderBlockLower(), req.stopLossPrice(),
                    req.takeProfitPrice(), req.configurationVersion(), req.maxLossPct(), req.targetRoePct()
            );
            TradeOpenResult result = persistenceService.openTrade(openReq);
            if (!result.success()) {
                return ResponseEntity.status(HttpStatus.FORBIDDEN)
                        .body(new ApiResult(false, result.error(), null));
            }
            return ResponseEntity.ok(new ApiResult(true, null, result));
        } catch (TradePersistenceService.TradeLockException e) {
            log.warn("openTrade CONFLICT: account={} {}", accountId, e.getMessage());
            return ResponseEntity.status(HttpStatus.CONFLICT)
                    .body(new ApiResult(false, e.getMessage(), null));
        } catch (Exception e) {
            log.error("openTrade error: account={}", accountId, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(new ApiResult(false, "INTERNAL_ERROR: " + e.getMessage(), null));
        }
    }

    /**
     * POST /api/engine/trade/close/{accountId}
     *
     * Atomically:
     * - Records authoritative gross P&amp;L, fees, net P&amp;L
     * - Updates account.current_balance to post_trade_balance
     * - Releases the DB trade lock
     * All in one transaction. Idempotent for already-closed trades.
     */
    @PostMapping("/trade/close/{accountId}")
    public ResponseEntity<ApiResult> closeTrade(
            @PathVariable String accountId,
            @RequestBody TradeCloseApiRequest req
    ) {
        try {
            TradeCloseRequest closeReq = new TradeCloseRequest(
                    accountId, req.setupId(), req.grossPnl(), req.tradingFees(),
                    req.fundingCosts(), req.otherCosts(), req.exitPrice(),
                    req.closeReason(), req.authoritativeExchangeBalance(), req.exitOrderId()
            );
            TradeCloseResult result = persistenceService.closeTrade(closeReq);
            return ResponseEntity.ok(new ApiResult(true, null, result));
        } catch (TradePersistenceService.TradeNotFoundException e) {
            log.warn("closeTrade: setup not found for account={}: {}", accountId, e.getMessage());
            return ResponseEntity.notFound().build();
        } catch (Exception e) {
            log.error("closeTrade error: account={}", accountId, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(new ApiResult(false, "INTERNAL_ERROR: " + e.getMessage(), null));
        }
    }

    /**
     * POST /api/engine/trade/lock-state/{accountId}
     *
     * Updates the active trade lock's lifecycle state.
     * Called by Python engine when trade state transitions occur
     * (e.g., entry filled → PROTECTED_POSITION after SL/TP placement).
     */
    @PostMapping("/trade/lock-state/{accountId}")
    public ResponseEntity<ApiResult> updateLockState(
            @PathVariable String accountId,
            @RequestParam String state
    ) {
        try {
            persistenceService.updateLockState(accountId, state);
            return ResponseEntity.ok(new ApiResult(true, null, null));
        } catch (Exception e) {
            log.error("updateLockState error: account={} state={}", accountId, state, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(new ApiResult(false, e.getMessage(), null));
        }
    }

    /**
     * POST /api/engine/trade/force-release/{accountId}
     *
     * Force-releases a stuck trade lock AFTER confirmed Delta reconciliation.
     * Should only be called by the engine's crash-recovery flow when Delta
     * confirms no open position and no pending orders exist.
     */
    @PostMapping("/trade/force-release/{accountId}")
    public ResponseEntity<ApiResult> forceReleaseLock(
            @PathVariable String accountId,
            @RequestParam String reason
    ) {
        log.warn("FORCE RELEASE requested: account={} reason={}", accountId, reason);
        try {
            persistenceService.forceReleaseLock(accountId, reason);
            return ResponseEntity.ok(new ApiResult(true, null, null));
        } catch (Exception e) {
            log.error("forceReleaseLock error: account={}", accountId, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(new ApiResult(false, e.getMessage(), null));
        }
    }

    /**
     * GET /api/engine/capital/{accountId}
     *
     * Returns the authoritative capital for the next trade (100% allocation).
     * Priority: post_trade_balance of latest closed trade → account.current_balance.
     */
    @GetMapping("/capital/{accountId}")
    public ResponseEntity<ApiResult> getNextTradeCapital(@PathVariable String accountId) {
        try {
            BigDecimal capital = persistenceService.getNextTradeCapital(accountId);
            return ResponseEntity.ok(new ApiResult(true, null, capital));
        } catch (Exception e) {
            log.error("getNextTradeCapital error: account={}", accountId, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(new ApiResult(false, e.getMessage(), null));
        }
    }
}
