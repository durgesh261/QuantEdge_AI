# Phase I — Leverage & Liquidation Analysis

Generated (UTC): 2026-08-25T07:12:55.137985+00:00

## Intended formula (verified against repository conventions)

Production dynamic-leverage rule (`strategy/engine.py`, Phase 5.9 authoritative):

```text
stop_distance_pct = (|entry − SL| / entry) × 100
leverage          = min(cap=100, max(1, ⌊35.0 / stop_distance_pct⌋))
```

With SL distance = 1% of entry ⇒ leverage = **35×** — matching the user's intent
"if SL distance = 1%, leverage = 35x". Loss at SL ≡ 35% of account balance by construction.

## Account simulation parameters

- Cap: 100× (production StrategyConfig.max_leverage)
- Maintenance margin rate assumption: 0.5% (isolated-margin research approximation)
- Observed leverage across 454 trades: avg 54.52×, range [13×, 100×]

## Liquidation analysis

Estimated liquidation move ≈ `1/leverage − mmr` (fraction of entry).

- Trades flagged **LIQUIDATION_RISK** (estimated liquidation at/inside SL): **0**
- Assets affected: none

No trade breaches the liquidation boundary before its intended stop under the capped formula:
for every observed stop width, `1/leverage − mmr > stop_distance`, i.e. the SL always sits inside
the estimated liquidation level. Residual risks remain: gaps through both levels, exchange-specific
margin rules, and funding accrual.

## Per-asset leverage summary (OOS window)

| Asset | Avg leverage | Liquidation-before-SL trades |
|---|---:|---:|
| BTCUSD | 76.7× | 0 |
| ETHUSD | 58.5× | 0 |
| SOLUSD | 58.6× | 0 |
| XRPUSD | 62.5× | 0 |

## Strategy vs account separation

- Strategy metrics (R, expectancy, PF, MDD) are computed at 1R = risk amount and are INDEPENDENT of leverage.
- Leveraged account returns scale linearly with leverage in R-space (return ≈ Σ Rᵢ × 35% of balance per trade)
  but amplify sequence risk: max leveraged drawdown ≈ strategy MDD × 35% of balance per unit R.
- Liquidation feasibility, not paper profitability, decides whether the leverage idea is practically safe.

## Verdict

UNSAFE-CLEAR: no estimated liquidation precedes SL under the capped production formula; however this conclusion depends on the documented margin assumptions and does not address gap risk.