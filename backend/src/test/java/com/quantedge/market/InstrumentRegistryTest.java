package com.quantedge.market;

import com.quantedge.market.dto.ProductDto;
import com.quantedge.market.service.InstrumentRegistry;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class InstrumentRegistryTest {

    @Test
    @DisplayName("Should normalize various symbol formats to canonical symbols")
    void shouldNormalizeSymbols() {
        assertThat(InstrumentRegistry.normalizeSymbol("BTCUSD")).isEqualTo("BTCUSD");
        assertThat(InstrumentRegistry.normalizeSymbol("BTC/USD")).isEqualTo("BTCUSD");
        assertThat(InstrumentRegistry.normalizeSymbol("btc-usd")).isEqualTo("BTCUSD");
        assertThat(InstrumentRegistry.normalizeSymbol(" ETH/USD ")).isEqualTo("ETHUSD");
        assertThat(InstrumentRegistry.normalizeSymbol("SOL/USD")).isEqualTo("SOLUSD");
        assertThat(InstrumentRegistry.normalizeSymbol("xrp-usd")).isEqualTo("XRPUSD");
        assertThat(InstrumentRegistry.normalizeSymbol(null)).isNull();
    }

    @Test
    @DisplayName("Should correctly identify supported vs unsupported symbols")
    void shouldCheckSupportedSymbols() {
        assertThat(InstrumentRegistry.isSupported("BTCUSD")).isTrue();
        assertThat(InstrumentRegistry.isSupported("ETHUSD")).isTrue();
        assertThat(InstrumentRegistry.isSupported("SOLUSD")).isTrue();
        assertThat(InstrumentRegistry.isSupported("XRPUSD")).isTrue();
        assertThat(InstrumentRegistry.isSupported("DOGEUSD")).isFalse();
        assertThat(InstrumentRegistry.isSupported(null)).isFalse();
    }

    @Test
    @DisplayName("Should contain canonical specs with verified tick, lot, and leverage caps")
    void shouldVerifyCanonicalSpecs() {
        var btc = InstrumentRegistry.getSpec("BTCUSD");
        assertThat(btc).isPresent();
        assertThat(btc.get().internalSymbol()).isEqualTo("BTCUSD");
        assertThat(btc.get().tickSize()).isEqualByComparingTo("0.5");
        assertThat(btc.get().maxLeverage()).isEqualTo(100);

        var eth = InstrumentRegistry.getSpec("ETHUSD");
        assertThat(eth).isPresent();
        assertThat(eth.get().tickSize()).isEqualByComparingTo("0.05");
        assertThat(eth.get().maxLeverage()).isEqualTo(100);

        var sol = InstrumentRegistry.getSpec("SOLUSD");
        assertThat(sol).isPresent();
        assertThat(sol.get().tickSize()).isEqualByComparingTo("0.01");
        assertThat(sol.get().maxLeverage()).isEqualTo(100);

        var xrp = InstrumentRegistry.getSpec("XRPUSD");
        assertThat(xrp).isPresent();
        assertThat(xrp.get().tickSize()).isEqualByComparingTo("0.0001");
        assertThat(xrp.get().maxLeverage()).isEqualTo(100);
    }

    @Test
    @DisplayName("Every instrument carries the same authoritative leverage band")
    void shouldApplyOneLeverageBandToEveryInstrument() {
        // SOLUSD and XRPUSD used to carry 50 here while BTCUSD and ETHUSD
        // carried 100, which is how a requested 100x came to be rejected on
        // two of the four symbols. Pinned uniform so a per-symbol cap cannot
        // reappear silently.
        assertThat(InstrumentRegistry.MAX_LEVERAGE).isEqualTo(100);
        for (var spec : InstrumentRegistry.getAllSupported()) {
            assertThat(spec.maxLeverage())
                    .as("maxLeverage for %s", spec.internalSymbol())
                    .isEqualTo(InstrumentRegistry.MAX_LEVERAGE);
        }
    }

    @Test
    @DisplayName("Should create fallback product DTOs for cold start")
    void shouldCreateDefaultProducts() {
        List<ProductDto> products = InstrumentRegistry.createDefaultProducts();
        assertThat(products).hasSize(4);
        assertThat(products).extracting(ProductDto::symbol)
                .containsExactlyInAnyOrder("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD");
    }
}
