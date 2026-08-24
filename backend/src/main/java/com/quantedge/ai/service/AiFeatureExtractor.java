package com.quantedge.ai.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.ai.dto.AiFeatureVector;
import com.quantedge.market.dto.CandleDto;
import com.quantedge.market.service.MarketDataService;
import com.quantedge.strategy.entity.StrategySetupRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;
import java.util.Optional;

/**
 * Extracts structured feature vectors from SMC setups and market data for AI inference.
 * Features are computed deterministically from observable market structure.
 */
@Component
public class AiFeatureExtractor {

    private static final Logger log = LoggerFactory.getLogger(AiFeatureExtractor.class);
    private static final int LOOKBACK_CANDLES = 100;

    private final MarketDataService marketDataService;

    public AiFeatureExtractor(MarketDataService marketDataService) {
        this.marketDataService = marketDataService;
    }

    /**
     * Extracts a complete feature vector for AI inference from an SMC setup.
     * Returns Optional.empty() if insufficient data is available.
     */
    public Optional<AiFeatureVector> extractFeatures(TradingAccount account, StrategySetupRecord setup) {
        try {
            // Fetch recent candles for multi-timeframe context
            List<CandleDto> candles1h = fetchCandles(setup.getSymbol(), "1h", LOOKBACK_CANDLES);
            List<CandleDto> candles15m = fetchCandles(setup.getSymbol(), "15m", LOOKBACK_CANDLES);
            List<CandleDto> candles4h = fetchCandles(setup.getSymbol(), "4h", LOOKBACK_CANDLES);

            if (candles1h.size() < 50) {
                log.warn("Insufficient 1H candle data for {}: {} candles", setup.getSymbol(), candles1h.size());
                return Optional.empty();
            }

            // SMC Structural Features
            BigDecimal bosStrength = calculateBosStrength(setup);
            BigDecimal chochStrength = calculateChochStrength(setup);
            BigDecimal orderBlockStrength = calculateOrderBlockStrength(setup, candles1h);
            BigDecimal fvgStrength = calculateFvgStrength(setup, candles1h);
            BigDecimal liquidityProximity = calculateLiquidityProximity(setup, candles1h);

            // Market Context Features
            BigDecimal trendStrength1h = calculateTrendStrength(candles1h);
            BigDecimal trendStrength15m = calculateTrendStrength(candles15m);
            BigDecimal trendStrength4h = calculateTrendStrength(candles4h);
            BigDecimal volatility1h = calculateVolatility(candles1h);
            BigDecimal volatility15m = calculateVolatility(candles15m);
            BigDecimal volumeProfile = calculateVolumeProfile(candles1h);
            BigDecimal momentum1h = calculateMomentum(candles1h);
            BigDecimal momentum15m = calculateMomentum(candles15m);

            // Setup Geometry Features
            BigDecimal riskReward = setup.getRiskReward() != null ? setup.getRiskReward() : BigDecimal.valueOf(2.0);
            BigDecimal riskDistance = setup.getRiskDistance() != null ? setup.getRiskDistance() : BigDecimal.ONE;
            BigDecimal entryPrecision = calculateEntryPrecision(setup, candles1h);

            // Account/Risk Context (where appropriate)
            BigDecimal accountUtilization = calculateAccountUtilization(account);
            BigDecimal leverageRatio = calculateLeverageRatio(setup);

            // Multi-timeframe Alignment
            String regime1h = classifyRegime(candles1h);
            String regime15m = classifyRegime(candles15m);
            String regime4h = classifyRegime(candles4h);
            boolean regimeAlignment = regime1h.equals(regime15m) && regime15m.equals(regime4h);

            AiFeatureVector vector = new AiFeatureVector(
                    setup.getSetupId(),
                    setup.getSymbol(),
                    setup.getDirection(),
                    // SMC Structure
                    bosStrength,
                    chochStrength,
                    orderBlockStrength,
                    fvgStrength,
                    liquidityProximity,
                    // Market Context
                    trendStrength1h,
                    trendStrength15m,
                    trendStrength4h,
                    volatility1h,
                    volatility15m,
                    volumeProfile,
                    momentum1h,
                    momentum15m,
                    // Setup Geometry
                    riskReward,
                    riskDistance,
                    entryPrecision,
                    // Account Context
                    accountUtilization,
                    leverageRatio,
                    // Multi-timeframe
                    regime1h,
                    regime15m,
                    regime4h,
                    regimeAlignment
            );

            log.debug("Extracted features for setup {}: RR={}, Trend1h={}, Vol1h={}",
                    setup.getSetupId(), riskReward, trendStrength1h, volatility1h);
            return Optional.of(vector);

        } catch (Exception e) {
            log.error("Feature extraction failed for setup {}: {}", setup.getSetupId(), e.getMessage());
            return Optional.empty();
        }
    }

    private List<CandleDto> fetchCandles(String symbol, String interval, int limit) {
        try {
            return marketDataService.getCandles(symbol, interval, null, null, limit).candles();
        } catch (Exception e) {
            log.warn("Failed to fetch {} candles for {}: {}", interval, symbol, e.getMessage());
            return List.of();
        }
    }

    // SMC Structural Calculations

    private BigDecimal calculateBosStrength(StrategySetupRecord setup) {
        // BOS strength based on structure break magnitude and volume confirmation
        if (setup.getStructureBreakPrice() == null || setup.getStructureBreakPrice().compareTo(BigDecimal.ZERO) == 0) {
            return BigDecimal.ZERO;
        }
        // Simplified: use risk/reward as proxy for structure quality
        BigDecimal rr = setup.getRiskReward() != null ? setup.getRiskReward() : BigDecimal.valueOf(2.0);
        return rr.divide(BigDecimal.valueOf(5.0), 4, RoundingMode.HALF_UP).min(BigDecimal.ONE);
    }

    private BigDecimal calculateChochStrength(StrategySetupRecord setup) {
        // CHOCH strength - change of character confirmation
        BigDecimal confidence = setup.getConfidence() != null ? setup.getConfidence() : BigDecimal.valueOf(0.75);
        return confidence.min(BigDecimal.ONE);
    }

    private BigDecimal calculateOrderBlockStrength(StrategySetupRecord setup, List<CandleDto> candles) {
        // Order block mitigation test strength
        if (setup.getOrderBlockPrice() == null) return BigDecimal.ZERO;
        
        // Check how many times price tested the OB level
        BigDecimal obPrice = setup.getOrderBlockPrice();
        int tests = 0;
        for (CandleDto c : candles) {
            BigDecimal low = c.low();
            BigDecimal high = c.high();
            if (low.compareTo(obPrice) <= 0 && high.compareTo(obPrice) >= 0) {
                tests++;
            }
        }
        // More tests = stronger OB (but too many = weakened)
        if (tests == 0) return BigDecimal.valueOf(0.9); // Fresh OB
        if (tests <= 2) return BigDecimal.valueOf(0.7);
        if (tests <= 4) return BigDecimal.valueOf(0.5);
        return BigDecimal.valueOf(0.3);
    }

    private BigDecimal calculateFvgStrength(StrategySetupRecord setup, List<CandleDto> candles) {
        // Fair Value Gap mitigation strength
        if (setup.getFvgPrice() == null) return BigDecimal.ZERO;
        return BigDecimal.valueOf(0.6); // Simplified - would analyze FVG fill rate
    }

    private BigDecimal calculateLiquidityProximity(StrategySetupRecord setup, List<CandleDto> candles) {
        // Distance to nearest liquidity pool (swing highs/lows)
        if (candles.isEmpty()) return BigDecimal.valueOf(0.5);
        
        BigDecimal currentPrice = candles.get(candles.size() - 1).close();
        BigDecimal swingHigh = candles.stream().map(CandleDto::high).max(BigDecimal::compareTo).orElse(currentPrice);
        BigDecimal swingLow = candles.stream().map(CandleDto::low).min(BigDecimal::compareTo).orElse(currentPrice);
        
        BigDecimal entry = setup.getEntryPrice();
        if (entry == null) return BigDecimal.valueOf(0.5);
        
        BigDecimal distToHigh = swingHigh.subtract(entry).abs();
        BigDecimal distToLow = entry.subtract(swingLow).abs();
        BigDecimal minDist = distToHigh.min(distToLow);
        BigDecimal range = swingHigh.subtract(swingLow);
        
        if (range.compareTo(BigDecimal.ZERO) == 0) return BigDecimal.valueOf(0.5);
        return minDist.divide(range, 4, RoundingMode.HALF_UP).min(BigDecimal.ONE);
    }

    // Market Context Calculations

    private BigDecimal calculateTrendStrength(List<CandleDto> candles) {
        if (candles.size() < 20) return BigDecimal.ZERO;
        
        // ADX-like trend strength using EMA slope
        List<BigDecimal> closes = candles.stream().map(CandleDto::close).toList();
        BigDecimal emaFast = calculateEma(closes, 9);
        BigDecimal emaSlow = calculateEma(closes, 21);
        
        if (emaFast == null || emaSlow == null) return BigDecimal.ZERO;
        
        BigDecimal diff = emaFast.subtract(emaSlow).abs();
        BigDecimal avg = emaFast.add(emaSlow).divide(BigDecimal.valueOf(2), 4, RoundingMode.HALF_UP);
        
        if (avg.compareTo(BigDecimal.ZERO) == 0) return BigDecimal.ZERO;
        return diff.divide(avg, 4, RoundingMode.HALF_UP).min(BigDecimal.ONE);
    }

    private BigDecimal calculateVolatility(List<CandleDto> candles) {
        if (candles.size() < 14) return BigDecimal.ZERO;
        
        // ATR-like volatility normalized to price
        List<BigDecimal> trueRanges = new java.util.ArrayList<>();
        for (int i = 1; i < candles.size(); i++) {
            BigDecimal high = candles.get(i).high();
            BigDecimal low = candles.get(i).low();
            BigDecimal prevClose = candles.get(i - 1).close();
            
            BigDecimal tr1 = high.subtract(low);
            BigDecimal tr2 = high.subtract(prevClose).abs();
            BigDecimal tr3 = low.subtract(prevClose).abs();
            trueRanges.add(tr1.max(tr2).max(tr3));
        }
        
        BigDecimal atr = trueRanges.stream()
                .reduce(BigDecimal.ZERO, BigDecimal::add)
                .divide(BigDecimal.valueOf(trueRanges.size()), 4, RoundingMode.HALF_UP);
        
        BigDecimal avgPrice = candles.get(candles.size() - 1).close();
        if (avgPrice.compareTo(BigDecimal.ZERO) == 0) return BigDecimal.ZERO;
        
        return atr.divide(avgPrice, 4, RoundingMode.HALF_UP).min(BigDecimal.ONE);
    }

    private BigDecimal calculateVolumeProfile(List<CandleDto> candles) {
        if (candles.isEmpty()) return BigDecimal.ZERO;
        
        // Volume trend - recent vs historical
        int split = candles.size() / 2;
        BigDecimal recentVol = candles.subList(split, candles.size()).stream()
                .map(CandleDto::volume)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        BigDecimal historicalVol = candles.subList(0, split).stream()
                .map(CandleDto::volume)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        
        if (historicalVol.compareTo(BigDecimal.ZERO) == 0) return BigDecimal.ONE;
        return recentVol.divide(historicalVol, 4, RoundingMode.HALF_UP).min(BigDecimal.valueOf(2));
    }

    private BigDecimal calculateMomentum(List<CandleDto> candles) {
        if (candles.size() < 10) return BigDecimal.ZERO;
        
        // Rate of change over 10 periods
        BigDecimal current = candles.get(candles.size() - 1).close();
        BigDecimal past = candles.get(candles.size() - 10).close();
        
        if (past.compareTo(BigDecimal.ZERO) == 0) return BigDecimal.ZERO;
        return current.subtract(past).divide(past, 4, RoundingMode.HALF_UP);
    }

    private BigDecimal calculateEntryPrecision(StrategySetupRecord setup, List<CandleDto> candles) {
        // How close is entry to optimal (order block / FVG level)
        if (setup.getEntryPrice() == null || setup.getOrderBlockPrice() == null) return BigDecimal.valueOf(0.5);
        
        BigDecimal entry = setup.getEntryPrice();
        BigDecimal ob = setup.getOrderBlockPrice();
        BigDecimal diff = entry.subtract(ob).abs();
        
        // Normalize by ATR
        BigDecimal atr = calculateVolatility(candles).multiply(candles.get(candles.size() - 1).close());
        if (atr.compareTo(BigDecimal.ZERO) == 0) return BigDecimal.valueOf(0.5);
        
        BigDecimal precision = BigDecimal.ONE.subtract(diff.divide(atr, 4, RoundingMode.HALF_UP)).max(BigDecimal.ZERO);
        return precision.min(BigDecimal.ONE);
    }

    private BigDecimal calculateAccountUtilization(TradingAccount account) {
        if (account.getTotalEquity() == null || account.getTotalEquity().compareTo(BigDecimal.ZERO) == 0) {
            return BigDecimal.ZERO;
        }
        if (account.getMarginUsed() == null) return BigDecimal.ZERO;
        return account.getMarginUsed().divide(account.getTotalEquity(), 4, RoundingMode.HALF_UP).min(BigDecimal.ONE);
    }

    private BigDecimal calculateLeverageRatio(StrategySetupRecord setup) {
        // Normalized leverage (1-100 -> 0-1)
        BigDecimal maxLeverage = BigDecimal.valueOf(100); // Would come from instrument config
        if (setup.getLeverage() == null) return BigDecimal.valueOf(0.1);
        return BigDecimal.valueOf(setup.getLeverage()).divide(maxLeverage, 4, RoundingMode.HALF_UP).min(BigDecimal.ONE);
    }

    private String classifyRegime(List<CandleDto> candles) {
        if (candles.size() < 50) return "UNKNOWN";
        
        BigDecimal trend = calculateTrendStrength(candles);
        BigDecimal momentum = calculateMomentum(candles);
        
        if (trend.compareTo(BigDecimal.valueOf(0.3)) > 0 && momentum.compareTo(BigDecimal.ZERO) > 0) {
            return "TRENDING_BULLISH";
        } else if (trend.compareTo(BigDecimal.valueOf(0.3)) > 0 && momentum.compareTo(BigDecimal.ZERO) < 0) {
            return "TRENDING_BEARISH";
        } else if (trend.compareTo(BigDecimal.valueOf(0.15)) < 0) {
            return "RANGING";
        }
        return "TRANSITIONAL";
    }

    private BigDecimal calculateEma(List<BigDecimal> values, int period) {
        if (values.size() < period) return null;
        
        double multiplier = 2.0 / (period + 1);
        BigDecimal ema = values.get(0);
        
        for (int i = 1; i < values.size(); i++) {
            BigDecimal value = values.get(i);
            ema = value.subtract(ema).multiply(BigDecimal.valueOf(multiplier)).add(ema);
        }
        return ema;
    }
}