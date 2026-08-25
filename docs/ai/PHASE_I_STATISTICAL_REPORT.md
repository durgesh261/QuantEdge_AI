# Phase I — Statistical Report

Generated (UTC): 2026-08-25T07:12:55.137985+00:00

## Method

- Paired Moving Block Bootstrap (MBB) over the frozen-OOS trade sequence (consistent with Phases C–H).
- N = 2000 replicates (≥ 1000 required), block size = 5 = max(3, ⌈N^(1/3)⌉), seed = 42.
- Each replicate resamples blocks of indices from the FULL Group-A sequence; SMC mean, AI mean and their
  difference are computed on identical indices (paired), preserving cross-group dependence.

## Confidence intervals (OOS, gross R)

| Quantity | 95% CI low | 95% CI high | Crosses zero? |
|---|---:|---:|---|
| SMC-only expectancy | -0.2149 | +0.3120 | YES |
| SMC+AI expectancy | -1.0000 | +1.7142 | YES |
| Incremental expectancy (AI − SMC) | -1.1139 | +1.7285 | YES |
| AI-rejected expectancy | -0.2318 | +0.3053 | YES |

**The incremental expectancy 95% CI INCLUDES zero** — the AI improvement is NOT statistically significant.

## Sample size & power limitations

- OOS selections: 99 total; AI-accepted subset: 4.
- ⚠️ The AI-accepted sample (4) is small (< 30). All point estimates carry high variance;
  bootstrap percentiles understate tail uncertainty at this size. Treat any positive result as provisional.
- Overlapping 72h holding windows induce serial correlation; MBB blocks mitigate but cannot eliminate it.
- One historical path (single 2026 sample). No multiple-scenario robustness is possible.

## Decision rule applied

- Statistical significance requires incremental 95% CI lower bound > 0 (pre-declared criterion C5).
- If the CI includes zero the experiment is reported as inconclusive REGARDLESS of point estimates.

## Reproducibility

- Seed 42; identical inputs reproduce byte-identical intervals.
- Bootstrap N=2000, block size 5 chosen BEFORE OOS evaluation.