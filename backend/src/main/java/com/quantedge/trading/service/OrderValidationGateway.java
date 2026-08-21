package com.quantedge.trading.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.*;

@Service
public class OrderValidationGateway {

    private static final Logger log = LoggerFactory.getLogger(OrderValidationGateway.class);

    public enum RejectionReasonCode {
        ACCOUNT_NOT_FOUND,
        ACCOUNT_DISABLED,
        ALGO_DISABLED,
        KILL_SWITCH_ACTIVE,
        EXCHANGE_DISCONNECTED,
        INVALID_CREDENTIALS,
        DELTA_CREDENTIALS_MISSING,
        UNAUTHORIZED_ACCOUNT,
        ACCOUNT_STATE_STALE,
        UNSUPPORTED_SYMBOL,
        INVALID_DIRECTION,
        UNSUPPORTED_ORDER_TYPE,
        INVALID_QUANTITY_NON_POSITIVE,
        QUANTITY_BELOW_MINIMUM,
        INVALID_QUANTITY_STEP,
        INVALID_PRICE_NON_POSITIVE,
        INVALID_TICK_SIZE,
        INSUFFICIENT_BALANCE,
        INSUFFICIENT_AVAILABLE_MARGIN,
        EXCESSIVE_LEVERAGE,
        EXCESSIVE_RISK,
        MISSING_STOP_LOSS,
        MISSING_TAKE_PROFIT,
        INVALID_TP_SL_GEOMETRY,
        ZERO_OR_NEGATIVE_RISK_DISTANCE,
        INVALID_RISK_REWARD,
        DUPLICATE_CLIENT_ORDER_ID,
        DUPLICATE_SETUP_ID,
        CONCURRENT_TRADE_LIMIT_EXCEEDED,
        DECISION_NOT_READY,
        SETUP_NOT_FOUND,
        SETUP_EXPIRED
    }

    public record ProductSpecification(
            String symbol,
            int productId,
            BigDecimal minSize,
            BigDecimal sizeStep,
            BigDecimal tickSize,
            int maxLeverage
    ) {}

    public static final Map<String, ProductSpecification> DEFAULT_PRODUCTS;

    static {
        Map<String, ProductSpecification> map = new HashMap<>();
        map.put("BTCUSD", new ProductSpecification("BTCUSD", 27, new BigDecimal("1"), new BigDecimal("1"), new BigDecimal("0.5"), 100));
        map.put("BTCUSD.P", new ProductSpecification("BTCUSD.P", 27, new BigDecimal("1"), new BigDecimal("1"), new BigDecimal("0.5"), 100));
        map.put("ETHUSD", new ProductSpecification("ETHUSD", 28, new BigDecimal("1"), new BigDecimal("1"), new BigDecimal("0.05"), 100));
        map.put("ETHUSD.P", new ProductSpecification("ETHUSD.P", 28, new BigDecimal("1"), new BigDecimal("1"), new BigDecimal("0.05"), 100));
        map.put("SOLUSD", new ProductSpecification("SOLUSD", 29, new BigDecimal("1"), new BigDecimal("1"), new BigDecimal("0.01"), 50));
        map.put("SOLUSD.P", new ProductSpecification("SOLUSD.P", 29, new BigDecimal("1"), new BigDecimal("1"), new BigDecimal("0.01"), 50));
        map.put("XRPUSD", new ProductSpecification("XRPUSD", 30, new BigDecimal("1"), new BigDecimal("1"), new BigDecimal("0.0001"), 50));
        map.put("XRPUSD.P", new ProductSpecification("XRPUSD.P", 30, new BigDecimal("1"), new BigDecimal("1"), new BigDecimal("0.0001"), 50));
        DEFAULT_PRODUCTS = Collections.unmodifiableMap(map);
    }

    public static class ValidationRequest {
        private final String accountId;
        private final String symbol;
        private final String direction;
        private final String orderType;
        private final BigDecimal quantity;
        private final BigDecimal entryPrice;
        private final BigDecimal stopLoss;
        private final BigDecimal takeProfit;
        private final Integer leverage;
        private final String clientOrderId;
        private final String setupId;
        private final boolean reduceOnly;

        public ValidationRequest(
                String accountId,
                String symbol,
                String direction,
                String orderType,
                BigDecimal quantity,
                BigDecimal entryPrice,
                BigDecimal stopLoss,
                BigDecimal takeProfit,
                Integer leverage,
                String clientOrderId,
                String setupId,
                boolean reduceOnly
        ) {
            this.accountId = accountId;
            this.symbol = symbol;
            this.direction = direction;
            this.orderType = orderType;
            this.quantity = quantity;
            this.entryPrice = entryPrice;
            this.stopLoss = stopLoss;
            this.takeProfit = takeProfit;
            this.leverage = leverage;
            this.clientOrderId = clientOrderId;
            this.setupId = setupId;
            this.reduceOnly = reduceOnly;
        }

        public String getAccountId() { return accountId; }
        public String getSymbol() { return symbol; }
        public String getDirection() { return direction; }
        public String getOrderType() { return orderType; }
        public BigDecimal getQuantity() { return quantity; }
        public BigDecimal getEntryPrice() { return entryPrice; }
        public BigDecimal getStopLoss() { return stopLoss; }
        public BigDecimal getTakeProfit() { return takeProfit; }
        public Integer getLeverage() { return leverage; }
        public String getClientOrderId() { return clientOrderId; }
        public String getSetupId() { return setupId; }
        public boolean isReduceOnly() { return reduceOnly; }
    }

    public static class ValidationContext {
        private final boolean accountActive;
        private final boolean algoEnabled;
        private final boolean killSwitchActive;
        private final String connectionStatus;
        private final boolean credentialsValid;
        private final BigDecimal totalEquity;
        private final BigDecimal availableBalance;
        private final int activePositionsCount;
        private final int maxConcurrentTrades;
        private final int maxLeverage;
        private final BigDecimal riskPerTradePct;
        private final BigDecimal minRiskReward;
        private final Set<String> activeClientOrderIds;
        private final Set<String> activeSetupIds;
        private final Map<String, ProductSpecification> productSpecs;

        public ValidationContext(
                boolean accountActive,
                boolean algoEnabled,
                boolean killSwitchActive,
                String connectionStatus,
                boolean credentialsValid,
                BigDecimal totalEquity,
                BigDecimal availableBalance,
                int activePositionsCount,
                int maxConcurrentTrades,
                int maxLeverage,
                BigDecimal riskPerTradePct,
                BigDecimal minRiskReward,
                Set<String> activeClientOrderIds,
                Set<String> activeSetupIds,
                Map<String, ProductSpecification> productSpecs
        ) {
            this.accountActive = accountActive;
            this.algoEnabled = algoEnabled;
            this.killSwitchActive = killSwitchActive;
            this.connectionStatus = connectionStatus;
            this.credentialsValid = credentialsValid;
            this.totalEquity = totalEquity != null ? totalEquity : BigDecimal.ZERO;
            this.availableBalance = availableBalance != null ? availableBalance : BigDecimal.ZERO;
            this.activePositionsCount = activePositionsCount;
            this.maxConcurrentTrades = maxConcurrentTrades > 0 ? maxConcurrentTrades : 1;
            this.maxLeverage = maxLeverage > 0 ? maxLeverage : 100;
            this.riskPerTradePct = riskPerTradePct != null ? riskPerTradePct : new BigDecimal("35.0");
            this.minRiskReward = minRiskReward != null ? minRiskReward : new BigDecimal("1.5");
            this.activeClientOrderIds = activeClientOrderIds != null ? activeClientOrderIds : Collections.emptySet();
            this.activeSetupIds = activeSetupIds != null ? activeSetupIds : Collections.emptySet();
            this.productSpecs = productSpecs != null ? productSpecs : DEFAULT_PRODUCTS;
        }

        public boolean isAccountActive() { return accountActive; }
        public boolean isAlgoEnabled() { return algoEnabled; }
        public boolean isKillSwitchActive() { return killSwitchActive; }
        public String getConnectionStatus() { return connectionStatus; }
        public boolean isCredentialsValid() { return credentialsValid; }
        public BigDecimal getTotalEquity() { return totalEquity; }
        public BigDecimal getAvailableBalance() { return availableBalance; }
        public int getActivePositionsCount() { return activePositionsCount; }
        public int getMaxConcurrentTrades() { return maxConcurrentTrades; }
        public int getMaxLeverage() { return maxLeverage; }
        public BigDecimal getRiskPerTradePct() { return riskPerTradePct; }
        public BigDecimal getMinRiskReward() { return minRiskReward; }
        public Set<String> getActiveClientOrderIds() { return activeClientOrderIds; }
        public Set<String> getActiveSetupIds() { return activeSetupIds; }
        public Map<String, ProductSpecification> getProductSpecs() { return productSpecs; }
    }

    public record ValidationResult(
            boolean valid,
            RejectionReasonCode rejectionCode,
            String rejectionReason,
            String failedCheck,
            Instant validatedAt,
            BigDecimal calculatedRiskAmount,
            BigDecimal calculatedRiskReward
    ) {
        public static ValidationResult approved(Instant validatedAt, BigDecimal riskAmount, BigDecimal riskReward) {
            return new ValidationResult(true, null, null, null, validatedAt, riskAmount, riskReward);
        }

        public static ValidationResult rejected(RejectionReasonCode code, String reason, String check, Instant validatedAt) {
            return new ValidationResult(false, code, reason, check, validatedAt, null, null);
        }
    }

    public ValidationResult validate(ValidationRequest request, ValidationContext context) {
        Instant now = Instant.now();

        // 1. Account Active Check
        if (!context.isAccountActive()) {
            return reject(RejectionReasonCode.ACCOUNT_DISABLED, "Trading account is disabled or does not exist", "CHECK_ACCOUNT_ACTIVE", now);
        }

        // 2. algo_enabled Check
        if (!context.isAlgoEnabled()) {
            return reject(RejectionReasonCode.ALGO_DISABLED, "Algorithmic execution is disabled for account (algo_enabled=false)", "CHECK_ALGO_ENABLED", now);
        }

        // 3. Emergency Kill Switch Check
        if (context.isKillSwitchActive()) {
            return reject(RejectionReasonCode.KILL_SWITCH_ACTIVE, "Emergency kill switch is active. All order submissions are blocked.", "CHECK_KILL_SWITCH", now);
        }

        // 4. Exchange Connection Health Check
        if (!"CONNECTED".equalsIgnoreCase(context.getConnectionStatus())) {
            return reject(RejectionReasonCode.EXCHANGE_DISCONNECTED, "Delta Exchange connection status is not CONNECTED", "CHECK_EXCHANGE_CONNECTION", now);
        }

        // 5. API Credentials Check
        if (!context.isCredentialsValid()) {
            return reject(RejectionReasonCode.INVALID_CREDENTIALS, "Authenticated Delta Exchange API credentials missing or invalid", "CHECK_CREDENTIALS", now);
        }

        // 6. Supported Symbol Check
        String symbolUpper = request.getSymbol() != null ? request.getSymbol().trim().toUpperCase() : "";
        ProductSpecification spec = context.getProductSpecs().get(symbolUpper);
        if (spec == null) {
            return reject(RejectionReasonCode.UNSUPPORTED_SYMBOL, "Unsupported instrument symbol: " + request.getSymbol(), "CHECK_SUPPORTED_SYMBOL", now);
        }

        // 7. Valid Direction Check
        String dir = request.getDirection() != null ? request.getDirection().trim().toUpperCase() : "";
        boolean isLong = "BUY".equals(dir) || "LONG".equals(dir);
        boolean isShort = "SELL".equals(dir) || "SHORT".equals(dir);
        if (!isLong && !isShort) {
            return reject(RejectionReasonCode.INVALID_DIRECTION, "Invalid order direction: " + request.getDirection() + ". Must be BUY/LONG or SELL/SHORT", "CHECK_DIRECTION", now);
        }

        // 8. Supported Order Type Check
        String orderType = request.getOrderType() != null ? request.getOrderType().trim().toUpperCase() : "";
        if (!"LIMIT_ORDER".equals(orderType) && !"MARKET_ORDER".equals(orderType) &&
            !"STOP_LIMIT_ORDER".equals(orderType) && !"STOP_MARKET_ORDER".equals(orderType) &&
            !"LIMIT".equals(orderType) && !"MARKET".equals(orderType)) {
            return reject(RejectionReasonCode.UNSUPPORTED_ORDER_TYPE, "Unsupported order type: " + request.getOrderType(), "CHECK_ORDER_TYPE", now);
        }

        // 9. Positive Quantity Check
        if (request.getQuantity() == null || request.getQuantity().compareTo(BigDecimal.ZERO) <= 0) {
            return reject(RejectionReasonCode.INVALID_QUANTITY_NON_POSITIVE, "Order quantity must be positive", "CHECK_QUANTITY_POSITIVE", now);
        }

        // 10. Quantity Minimum and Step Size Check
        if (request.getQuantity().compareTo(spec.minSize()) < 0) {
            return reject(RejectionReasonCode.QUANTITY_BELOW_MINIMUM, "Quantity " + request.getQuantity() + " is below minimum " + spec.minSize(), "CHECK_QUANTITY_MINIMUM", now);
        }
        BigDecimal remStep = request.getQuantity().subtract(spec.minSize()).remainder(spec.sizeStep());
        if (remStep.compareTo(BigDecimal.ZERO) != 0) {
            return reject(RejectionReasonCode.INVALID_QUANTITY_STEP, "Quantity " + request.getQuantity() + " does not align with step size " + spec.sizeStep(), "CHECK_QUANTITY_STEP", now);
        }

        // 11. Entry Price Check for Limit Orders
        if (orderType.contains("LIMIT")) {
            if (request.getEntryPrice() == null || request.getEntryPrice().compareTo(BigDecimal.ZERO) <= 0) {
                return reject(RejectionReasonCode.INVALID_PRICE_NON_POSITIVE, "Limit order requires a positive entry price", "CHECK_PRICE_POSITIVE", now);
            }

            // 12. Price Tick Size Check
            BigDecimal remTick = request.getEntryPrice().remainder(spec.tickSize());
            if (remTick.compareTo(BigDecimal.ZERO) != 0) {
                return reject(RejectionReasonCode.INVALID_TICK_SIZE, "Price " + request.getEntryPrice() + " does not align with tick size " + spec.tickSize(), "CHECK_TICK_SIZE", now);
            }
        }

        // 13. Max Concurrent Trades Limit Check
        if (!request.isReduceOnly() && context.getActivePositionsCount() >= context.getMaxConcurrentTrades()) {
            return reject(RejectionReasonCode.CONCURRENT_TRADE_LIMIT_EXCEEDED, "Account already has " + context.getActivePositionsCount() + " open positions (max allowed: " + context.getMaxConcurrentTrades() + ")", "CHECK_CONCURRENT_TRADES", now);
        }

        // 14. Leverage Cap Check
        int leverage = request.getLeverage() != null ? request.getLeverage() : 1;
        int maxAllowedLeverage = Math.min(spec.maxLeverage(), context.getMaxLeverage());
        if (leverage > maxAllowedLeverage || leverage < 1) {
            return reject(RejectionReasonCode.EXCESSIVE_LEVERAGE, "Requested leverage " + leverage + "x exceeds maximum allowed " + maxAllowedLeverage + "x", "CHECK_LEVERAGE_CAP", now);
        }

        BigDecimal riskAmount = null;
        BigDecimal riskReward = null;

        // 15. TP / SL Geometry & Risk Checks
        if (!request.isReduceOnly()) {
            if (request.getStopLoss() == null) {
                return reject(RejectionReasonCode.MISSING_STOP_LOSS, "Stop Loss price is required for live orders", "CHECK_MISSING_SL", now);
            }
            if (request.getTakeProfit() == null) {
                return reject(RejectionReasonCode.MISSING_TAKE_PROFIT, "Take Profit price is required for live orders", "CHECK_MISSING_TP", now);
            }

            BigDecimal entry = request.getEntryPrice();
            if (entry == null || entry.compareTo(BigDecimal.ZERO) <= 0) {
                return reject(RejectionReasonCode.INVALID_PRICE_NON_POSITIVE, "Valid entry price is required for TP/SL risk calculations", "CHECK_ENTRY_PRICE", now);
            }

            BigDecimal sl = request.getStopLoss();
            BigDecimal tp = request.getTakeProfit();

            BigDecimal riskDist;
            BigDecimal rewardDist;

            if (isLong) {
                if (entry.compareTo(sl) <= 0 || tp.compareTo(entry) <= 0) {
                    return reject(RejectionReasonCode.INVALID_TP_SL_GEOMETRY, "Invalid LONG geometry: require TP (" + tp + ") > Entry (" + entry + ") > SL (" + sl + ")", "CHECK_TP_SL_GEOMETRY", now);
                }
                riskDist = entry.subtract(sl);
                rewardDist = tp.subtract(entry);
            } else {
                if (sl.compareTo(entry) <= 0 || entry.compareTo(tp) <= 0) {
                    return reject(RejectionReasonCode.INVALID_TP_SL_GEOMETRY, "Invalid SHORT geometry: require SL (" + sl + ") > Entry (" + entry + ") > TP (" + tp + ")", "CHECK_TP_SL_GEOMETRY", now);
                }
                riskDist = sl.subtract(entry);
                rewardDist = entry.subtract(tp);
            }

            if (riskDist.compareTo(BigDecimal.ZERO) <= 0 || rewardDist.compareTo(BigDecimal.ZERO) <= 0) {
                return reject(RejectionReasonCode.ZERO_OR_NEGATIVE_RISK_DISTANCE, "Risk and reward distances must be strictly positive", "CHECK_RISK_DISTANCE", now);
            }

            riskReward = rewardDist.divide(riskDist, 4, RoundingMode.HALF_UP);
            if (riskReward.compareTo(context.getMinRiskReward()) < 0) {
                return reject(RejectionReasonCode.INVALID_RISK_REWARD, "Risk/Reward ratio " + riskReward + " is below minimum required " + context.getMinRiskReward(), "CHECK_MIN_RR", now);
            }

            // 16. Excessive Account Risk & Available Margin Check
            riskAmount = request.getQuantity().multiply(riskDist);
            BigDecimal maxRiskAllowed = context.getTotalEquity().multiply(context.getRiskPerTradePct()).divide(new BigDecimal("100"), 4, RoundingMode.HALF_UP);
            BigDecimal tolerance = maxRiskAllowed.multiply(new BigDecimal("1.01")); // 1% tolerance for fractional rounding
            if (maxRiskAllowed.compareTo(BigDecimal.ZERO) > 0 && riskAmount.compareTo(tolerance) > 0) {
                return reject(RejectionReasonCode.EXCESSIVE_RISK, "Trade risk " + riskAmount + " USDT exceeds maximum allowed risk " + maxRiskAllowed + " USDT", "CHECK_RISK_AMOUNT", now);
            }

            BigDecimal notionalValue = request.getQuantity().multiply(entry);
            BigDecimal requiredMargin = notionalValue.divide(BigDecimal.valueOf(leverage), 4, RoundingMode.HALF_UP);
            if (requiredMargin.compareTo(context.getAvailableBalance()) > 0) {
                return reject(RejectionReasonCode.INSUFFICIENT_BALANCE, "Required margin " + requiredMargin + " USDT exceeds available balance " + context.getAvailableBalance() + " USDT", "CHECK_AVAILABLE_MARGIN", now);
            }
        }

        // 17. Idempotency: Duplicate client_order_id
        if (request.getClientOrderId() != null && context.getActiveClientOrderIds().contains(request.getClientOrderId())) {
            return reject(RejectionReasonCode.DUPLICATE_CLIENT_ORDER_ID, "Duplicate client_order_id: " + request.getClientOrderId(), "CHECK_DUPLICATE_CLIENT_ORDER_ID", now);
        }

        // 18. Idempotency: Duplicate setup_id
        if (request.getSetupId() != null && context.getActiveSetupIds().contains(request.getSetupId())) {
            return reject(RejectionReasonCode.DUPLICATE_SETUP_ID, "Duplicate strategy setup_id: " + request.getSetupId(), "CHECK_DUPLICATE_SETUP_ID", now);
        }

        log.info("Order validation APPROVED for account {} on {} {} (Quantity: {}, Entry: {}, Risk: {})",
                request.getAccountId(), request.getSymbol(), dir, request.getQuantity(), request.getEntryPrice(), riskAmount);

        return ValidationResult.approved(now, riskAmount, riskReward);
    }

    private ValidationResult reject(RejectionReasonCode code, String reason, String check, Instant now) {
        log.warn("Order validation REJECTED: [{}] {} (Check: {})", code, reason, check);
        return ValidationResult.rejected(code, reason, check, now);
    }
}
