package com.quantedge.trading.service;

import lombok.Builder;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Set;

@Slf4j
@Service
@RequiredArgsConstructor
public class OrderValidationGateway {

    public enum RejectionReasonCode {
        ACCOUNT_DISABLED,
        ALGO_DISABLED,
        KILL_SWITCH_ACTIVE,
        EXCHANGE_DISCONNECTED,
        INVALID_CREDENTIALS,
        UNSUPPORTED_SYMBOL,
        INVALID_DIRECTION,
        UNSUPPORTED_ORDER_TYPE,
        INVALID_QUANTITY_NON_POSITIVE,
        QUANTITY_BELOW_MINIMUM,
        INVALID_QUANTITY_STEP,
        INVALID_PRICE_NON_POSITIVE,
        INVALID_TICK_SIZE,
        INSUFFICIENT_BALANCE,
        EXCESSIVE_LEVERAGE,
        EXCESSIVE_RISK,
        MISSING_STOP_LOSS,
        MISSING_TAKE_PROFIT,
        INVALID_TP_SL_GEOMETRY,
        ZERO_OR_NEGATIVE_RISK_DISTANCE,
        INVALID_RISK_REWARD,
        DUPLICATE_CLIENT_ORDER_ID,
        DUPLICATE_SETUP_ID,
        CONCURRENT_TRADE_LIMIT_EXCEEDED
    }

    @Getter
    @Builder
    public static class ValidationRequest {
        private String accountId;
        private String symbol;
        private String direction; // BUY / LONG or SELL / SHORT
        private String orderType; // LIMIT_ORDER, MARKET_ORDER
        private BigDecimal quantity;
        private BigDecimal entryPrice;
        private BigDecimal stopLoss;
        private BigDecimal takeProfit;
        private Integer leverage;
        private String clientOrderId;
        private String setupId;
        private boolean reduceOnly;
    }

    @Getter
    @Builder
    public static class ValidationResult {
        private boolean valid;
        private RejectionReasonCode rejectionCode;
        private String rejectionReason;
        private String failedCheck;
        private Instant validatedAt;
        private BigDecimal calculatedRiskAmount;
        private BigDecimal calculatedRiskReward;
    }

    @Getter
    @Builder
    public static class ValidationContext {
        private boolean accountActive;
        private boolean algoEnabled;
        private boolean killSwitchActive;
        private String connectionStatus; // CONNECTED, ERROR, DISCONNECTED
        private boolean credentialsValid;
        private BigDecimal totalEquity;
        private BigDecimal availableBalance;
        private int activePositionsCount;
        private int maxConcurrentTrades;
        private int maxLeverage;
        private BigDecimal riskPerTradePct;
        private BigDecimal minRiskReward;
        private Set<String> activeClientOrderIds;
        private Set<String> activeSetupIds;
        private List<String> supportedSymbols;
    }

    public ValidationResult validate(ValidationRequest request, ValidationContext context) {
        Instant now = Instant.now();

        // 1. Account active check
        if (!context.isAccountActive()) {
            return reject(RejectionReasonCode.ACCOUNT_DISABLED, "Trading account is disabled", "CHECK_ACCOUNT_ACTIVE", now);
        }

        // 2. algo_enabled check
        if (!context.isAlgoEnabled()) {
            return reject(RejectionReasonCode.ALGO_DISABLED, "Algorithmic execution is disabled for account", "CHECK_ALGO_ENABLED", now);
        }

        // 3. Kill switch check
        if (context.isKillSwitchActive()) {
            return reject(RejectionReasonCode.KILL_SWITCH_ACTIVE, "Emergency kill switch is active", "CHECK_KILL_SWITCH", now);
        }

        // 4. Exchange connection health
        if (!"CONNECTED".equalsIgnoreCase(context.getConnectionStatus())) {
            return reject(RejectionReasonCode.EXCHANGE_DISCONNECTED, "Delta Exchange is not connected", "CHECK_EXCHANGE_CONNECTION", now);
        }

        // 5. API credentials valid
        if (!context.isCredentialsValid()) {
            return reject(RejectionReasonCode.INVALID_CREDENTIALS, "Delta Exchange API credentials missing or invalid", "CHECK_CREDENTIALS", now);
        }

        // 6. Supported symbol
        if (context.getSupportedSymbols() != null && !context.getSupportedSymbols().contains(request.getSymbol().toUpperCase())) {
            return reject(RejectionReasonCode.UNSUPPORTED_SYMBOL, "Unsupported instrument symbol: " + request.getSymbol(), "CHECK_SYMBOL", now);
        }

        // 7. Valid direction
        String dir = request.getDirection() != null ? request.getDirection().toUpperCase() : "";
        boolean isLong = "BUY".equals(dir) || "LONG".equals(dir);
        boolean isShort = "SELL".equals(dir) || "SHORT".equals(dir);
        if (!isLong && !isShort) {
            return reject(RejectionReasonCode.INVALID_DIRECTION, "Invalid order direction: " + request.getDirection(), "CHECK_DIRECTION", now);
        }

        // 8. Supported order type
        String orderType = request.getOrderType() != null ? request.getOrderType().toUpperCase() : "";
        if (!"LIMIT_ORDER".equals(orderType) && !"MARKET_ORDER".equals(orderType) && !"LIMIT".equals(orderType) && !"MARKET".equals(orderType)) {
            return reject(RejectionReasonCode.UNSUPPORTED_ORDER_TYPE, "Unsupported order type: " + request.getOrderType(), "CHECK_ORDER_TYPE", now);
        }

        // 9. Quantity positive
        if (request.getQuantity() == null || request.getQuantity().compareTo(BigDecimal.ZERO) <= 0) {
            return reject(RejectionReasonCode.INVALID_QUANTITY_NON_POSITIVE, "Quantity must be positive", "CHECK_QUANTITY_POSITIVE", now);
        }

        // 10. Entry price positive for limit orders
        if (orderType.contains("LIMIT")) {
            if (request.getEntryPrice() == null || request.getEntryPrice().compareTo(BigDecimal.ZERO) <= 0) {
                return reject(RejectionReasonCode.INVALID_PRICE_NON_POSITIVE, "Limit order requires a positive entry price", "CHECK_PRICE_POSITIVE", now);
            }
        }

        // 11. Concurrent trades check
        if (!request.isReduceOnly() && context.getActivePositionsCount() >= context.getMaxConcurrentTrades()) {
            return reject(RejectionReasonCode.CONCURRENT_TRADE_LIMIT_EXCEEDED, "Max concurrent trade limit reached", "CHECK_CONCURRENT_TRADES", now);
        }

        // 12. Leverage cap
        int leverage = request.getLeverage() != null ? request.getLeverage() : 1;
        if (leverage > context.getMaxLeverage() || leverage < 1) {
            return reject(RejectionReasonCode.EXCESSIVE_LEVERAGE, "Requested leverage exceeds max allowed " + context.getMaxLeverage() + "x", "CHECK_LEVERAGE", now);
        }

        // 13. TP/SL geometry
        if (!request.isReduceOnly()) {
            if (request.getStopLoss() == null) {
                return reject(RejectionReasonCode.MISSING_STOP_LOSS, "Stop Loss is required for live order", "CHECK_MISSING_SL", now);
            }
            if (request.getTakeProfit() == null) {
                return reject(RejectionReasonCode.MISSING_TAKE_PROFIT, "Take Profit is required for live order", "CHECK_MISSING_TP", now);
            }

            BigDecimal entry = request.getEntryPrice();
            BigDecimal sl = request.getStopLoss();
            BigDecimal tp = request.getTakeProfit();

            if (entry == null || entry.compareTo(BigDecimal.ZERO) <= 0) {
                return reject(RejectionReasonCode.INVALID_PRICE_NON_POSITIVE, "Entry price required for TP/SL geometry", "CHECK_ENTRY_PRICE", now);
            }

            BigDecimal riskDist;
            BigDecimal rewardDist;

            if (isLong) {
                if (entry.compareTo(sl) <= 0 || tp.compareTo(entry) <= 0) {
                    return reject(RejectionReasonCode.INVALID_TP_SL_GEOMETRY, "Invalid LONG geometry: require TP > Entry > SL", "CHECK_TP_SL_GEOMETRY", now);
                }
                riskDist = entry.subtract(sl);
                rewardDist = tp.subtract(entry);
            } else {
                if (sl.compareTo(entry) <= 0 || entry.compareTo(tp) <= 0) {
                    return reject(RejectionReasonCode.INVALID_TP_SL_GEOMETRY, "Invalid SHORT geometry: require SL > Entry > TP", "CHECK_TP_SL_GEOMETRY", now);
                }
                riskDist = sl.subtract(entry);
                rewardDist = entry.subtract(tp);
            }

            if (riskDist.compareTo(BigDecimal.ZERO) <= 0 || rewardDist.compareTo(BigDecimal.ZERO) <= 0) {
                return reject(RejectionReasonCode.ZERO_OR_NEGATIVE_RISK_DISTANCE, "Risk and reward distance must be > 0", "CHECK_RISK_DISTANCE", now);
            }

            BigDecimal rr = rewardDist.divide(riskDist, 4, BigDecimal.ROUND_HALF_UP);
            if (context.getMinRiskReward() != null && rr.compareTo(context.getMinRiskReward()) < 0) {
                return reject(RejectionReasonCode.INVALID_RISK_REWARD, "Risk/Reward " + rr + " below required " + context.getMinRiskReward(), "CHECK_MIN_RR", now);
            }

            // 14. Balance & margin check
            BigDecimal requiredMargin = request.getQuantity().multiply(entry).divide(BigDecimal.valueOf(leverage), 4, BigDecimal.ROUND_HALF_UP);
            if (context.getAvailableBalance() != null && requiredMargin.compareTo(context.getAvailableBalance()) > 0) {
                return reject(RejectionReasonCode.INSUFFICIENT_BALANCE, "Insufficient available balance for margin", "CHECK_MARGIN", now);
            }
        }

        // 15. Duplicate client_order_id
        if (request.getClientOrderId() != null && context.getActiveClientOrderIds() != null && context.getActiveClientOrderIds().contains(request.getClientOrderId())) {
            return reject(RejectionReasonCode.DUPLICATE_CLIENT_ORDER_ID, "Duplicate client_order_id: " + request.getClientOrderId(), "CHECK_DUPLICATE_CLIENT_ORDER_ID", now);
        }

        // 16. Duplicate setup_id
        if (request.getSetupId() != null && context.getActiveSetupIds() != null && context.getActiveSetupIds().contains(request.getSetupId())) {
            return reject(RejectionReasonCode.DUPLICATE_SETUP_ID, "Duplicate strategy setup_id: " + request.getSetupId(), "CHECK_DUPLICATE_SETUP_ID", now);
        }

        return ValidationResult.builder()
                .valid(true)
                .validatedAt(now)
                .build();
    }

    private ValidationResult reject(RejectionReasonCode code, String reason, String check, Instant now) {
        log.warn("Order validation rejected: [{}] {} (Check: {})", code, reason, check);
        return ValidationResult.builder()
                .valid(false)
                .rejectionCode(code)
                .rejectionReason(reason)
                .failedCheck(check)
                .validatedAt(now)
                .build();
    }
}
