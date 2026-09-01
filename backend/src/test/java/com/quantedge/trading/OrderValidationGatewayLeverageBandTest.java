package com.quantedge.trading;

import com.quantedge.market.service.InstrumentRegistry;
import com.quantedge.trading.service.OrderValidationGateway;
import com.quantedge.trading.service.OrderValidationGateway.ProductSpecification;
import com.quantedge.trading.service.OrderValidationGateway.RejectionReasonCode;
import com.quantedge.trading.service.OrderValidationGateway.ValidationContext;
import com.quantedge.trading.service.OrderValidationGateway.ValidationRequest;
import com.quantedge.trading.service.OrderValidationGateway.ValidationResult;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.assertThatCode;

/**
 * The Java half of the 1x..100x leverage band.
 *
 * <p>Every other Java test mocks {@link OrderValidationGateway}, so before this
 * file the real {@code validate()} body had no coverage at all and
 * {@code ValidationContext} was never constructed anywhere in the repository --
 * which is how {@code DEFAULT_PRODUCTS} kept a 50x cap on SOLUSD and XRPUSD
 * long after the Python table was corrected, and how a context ceiling of 0
 * kept being silently substituted with the loosest ceiling in the application.
 *
 * <p>These tests assert behaviour through the public API rather than reading the
 * constants back: a table assertion alone would still pass if {@code validate()}
 * consulted something else.
 */
class OrderValidationGatewayLeverageBandTest {

    private static final String[] SYMBOLS = {
            "BTCUSD", "BTCUSD.P", "ETHUSD", "ETHUSD.P",
            "SOLUSD", "SOLUSD.P", "XRPUSD", "XRPUSD.P"
    };

    /** A context that clears checks 1-5 and 13 so a request reaches check 14. */
    private static ValidationContext context(int maxLeverage) {
        return new ValidationContext(
                true,                       // accountActive
                true,                       // algoEnabled
                false,                      // killSwitchActive
                "CONNECTED",                // connectionStatus
                true,                       // credentialsValid
                new BigDecimal("1000000"),  // totalEquity
                new BigDecimal("1000000"),  // availableBalance
                0,                          // activePositionsCount
                1,                          // maxConcurrentTrades
                maxLeverage,
                new BigDecimal("35.0"),     // riskPerTradePct
                new BigDecimal("1.5"),      // minRiskReward
                null,                       // activeClientOrderIds
                null,                       // activeSetupIds
                null                        // productSpecs -> DEFAULT_PRODUCTS
        );
    }

    /**
     * Entry price 100 aligns with every tick size in the table (0.5, 0.05,
     * 0.01, 0.0001) and quantity 1 equals every minSize, so checks 9-12 pass
     * for all eight symbols without per-symbol fixtures.
     */
    private static ValidationRequest request(String symbol, Integer leverage) {
        return new ValidationRequest(
                "acct-band-1",
                symbol,
                "BUY",
                "LIMIT_ORDER",
                BigDecimal.ONE,
                new BigDecimal("100"),
                new BigDecimal("99"),
                new BigDecimal("103"),
                leverage,
                "band-client-order-id",
                "band-setup-id",
                false
        );
    }

    private static ValidationResult validate(String symbol, Integer leverage, int ceiling) {
        return new OrderValidationGateway().validate(request(symbol, leverage), context(ceiling));
    }

    @Test
    @DisplayName("No symbol carries a leverage cap of its own; all eight rows declare the band maximum")
    void defaultProductsDeclareTheUniformBand() {
        for (Map.Entry<String, ProductSpecification> entry : OrderValidationGateway.DEFAULT_PRODUCTS.entrySet()) {
            assertThat(entry.getValue().maxLeverage())
                    .as("%s maxLeverage in OrderValidationGateway.DEFAULT_PRODUCTS", entry.getKey())
                    .isEqualTo(InstrumentRegistry.MAX_LEVERAGE)
                    .isEqualTo(100);
        }
        // Named explicitly: these four are the rows that held 50.
        assertThat(OrderValidationGateway.DEFAULT_PRODUCTS.get("SOLUSD").maxLeverage()).isEqualTo(100);
        assertThat(OrderValidationGateway.DEFAULT_PRODUCTS.get("SOLUSD.P").maxLeverage()).isEqualTo(100);
        assertThat(OrderValidationGateway.DEFAULT_PRODUCTS.get("XRPUSD").maxLeverage()).isEqualTo(100);
        assertThat(OrderValidationGateway.DEFAULT_PRODUCTS.get("XRPUSD.P").maxLeverage()).isEqualTo(100);
    }

    @Test
    @DisplayName("The Java band constants match the Python engine's authoritative pair")
    void bandConstantsMatchPython() {
        assertThat(InstrumentRegistry.MIN_LEVERAGE).isEqualTo(1);
        assertThat(InstrumentRegistry.MAX_LEVERAGE).isEqualTo(100);
    }

    @Test
    @DisplayName("100x is admissible on every symbol, including SOLUSD and XRPUSD")
    void hundredXIsAdmissibleOnEverySymbol() {
        for (String symbol : SYMBOLS) {
            ValidationResult result = validate(symbol, 100, 100);
            assertThat(result.rejectionCode())
                    .as("%s at 100x must not be refused on the leverage dimension (reason: %s)",
                            symbol, result.rejectionReason())
                    .isNotEqualTo(RejectionReasonCode.EXCESSIVE_LEVERAGE);
        }
    }

    @Test
    @DisplayName("Every leverage from 1x to 100x is admissible; none is refused on the leverage dimension")
    void theWholeBandIsAdmissible() {
        for (int leverage = InstrumentRegistry.MIN_LEVERAGE; leverage <= InstrumentRegistry.MAX_LEVERAGE; leverage++) {
            ValidationResult result = validate("XRPUSD", leverage, 100);
            assertThat(result.rejectionCode())
                    .as("XRPUSD at %sx", leverage)
                    .isNotEqualTo(RejectionReasonCode.EXCESSIVE_LEVERAGE);
        }
    }

    @Test
    @DisplayName("101x and above is refused on every symbol, and is never clamped to 100x")
    void aboveTheBandIsRefusedNotClamped() {
        for (String symbol : SYMBOLS) {
            for (int leverage : new int[]{101, 150, 1000, Integer.MAX_VALUE}) {
                ValidationResult result = validate(symbol, leverage, 100);
                assertThat(result.valid()).as("%s at %sx", symbol, leverage).isFalse();
                assertThat(result.rejectionCode()).isEqualTo(RejectionReasonCode.EXCESSIVE_LEVERAGE);
                assertThat(result.failedCheck()).isEqualTo("CHECK_LEVERAGE_CAP");
                assertThat(result.rejectionReason())
                        .as("the message must report the value asked for, not a clamped one")
                        .contains(leverage + "x")
                        .contains("exceeds maximum allowed 100x");
            }
        }
    }

    @Test
    @DisplayName("0x and negative leverage are refused as below the minimum, never coerced to 1x")
    void belowTheBandIsRefusedNotCoerced() {
        for (int leverage : new int[]{0, -1, -100, Integer.MIN_VALUE}) {
            ValidationResult result = validate("XRPUSD", leverage, 100);
            assertThat(result.valid()).as("XRPUSD at %sx", leverage).isFalse();
            assertThat(result.rejectionCode()).isEqualTo(RejectionReasonCode.EXCESSIVE_LEVERAGE);
            assertThat(result.failedCheck()).isEqualTo("CHECK_LEVERAGE_CAP");
            assertThat(result.rejectionReason())
                    .as("a sub-1x request has not exceeded anything")
                    .contains("below the minimum 1x");
        }
    }

    @Test
    @DisplayName("A stricter account ceiling still binds below the global maximum")
    void aStricterAccountCeilingBinds() {
        assertThat(validate("XRPUSD", 50, 50).rejectionCode())
                .isNotEqualTo(RejectionReasonCode.EXCESSIVE_LEVERAGE);

        ValidationResult over = validate("XRPUSD", 51, 50);
        assertThat(over.valid()).isFalse();
        assertThat(over.rejectionCode()).isEqualTo(RejectionReasonCode.EXCESSIVE_LEVERAGE);
        assertThat(over.rejectionReason()).contains("51x").contains("exceeds maximum allowed 50x");
    }

    @Test
    @DisplayName("An out-of-band context ceiling is refused, not defaulted to the loosest one")
    void anOutOfBandContextCeilingIsRefused() {
        for (int ceiling : new int[]{0, -1, -100, 101, 1000, Integer.MAX_VALUE, Integer.MIN_VALUE}) {
            assertThatThrownBy(() -> context(ceiling))
                    .as("ValidationContext ceiling %s", ceiling)
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("maxLeverage must be between 1 and 100 inclusive")
                    .hasMessageContaining(String.valueOf(ceiling));
        }
    }

    @Test
    @DisplayName("An in-band context ceiling is stored verbatim")
    void anInBandContextCeilingIsStoredVerbatim() {
        for (int ceiling : new int[]{1, 2, 25, 50, 99, 100}) {
            assertThatCode(() -> context(ceiling)).doesNotThrowAnyException();
            assertThat(context(ceiling).getMaxLeverage()).isEqualTo(ceiling);
        }
    }

    @Test
    @DisplayName("An unset request leverage falls back to the minimum, never the maximum")
    void unsetRequestLeverageFallsBackToTheMinimum() {
        ValidationResult result = validate("XRPUSD", null, 100);
        assertThat(result.rejectionCode()).isNotEqualTo(RejectionReasonCode.EXCESSIVE_LEVERAGE);
    }
}
