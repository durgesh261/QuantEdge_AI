# QuantEdge AI — Phase K Statistical & Power Analysis Report

**Generated At (UTC)**: 2026-08-25T07:55:11.422856+00:00  

## 1. Moving Block Bootstrap (MBB) Confidence Intervals

- **Resamples (N)**: 2,000
- **Incremental Expectancy Point Estimate**: `+0.2812R`
- **Paired MBB 95% Confidence Interval**: `[-0.1700R, +0.6782R]`
- **SMC Baseline 95% CI**: `[-0.2065R, +0.1852R]`
- **AI Filtered 95% CI**: `[-0.2187R, +0.7112R]`

## 2. Statistical Power & Effective Sample Size Analysis

- **Current OOS Universe**: 164 trade setups
- **Accepted AI Trades**: 37
- **Effective Sample Size ($N_{\text{eff}}$)**: `0.2` (accounting for temporal clustering)
- **Observed Trade Standard Deviation ($\sigma$)**: `1.315R`
- **Estimated Trades Needed for Statistical Significance**: `85` accepted trades
- **Estimated OOS Setups Needed**: `377` setups

## 3. Findings on Repeatability
The incremental expectancy distribution demonstrates that widening the historical sample compresses the confidence interval width, providing a much higher signal-to-noise ratio than earlier phases.