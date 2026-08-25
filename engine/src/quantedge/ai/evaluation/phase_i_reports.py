"""
QuantEdge AI — Phase I report generation.

Renders the four Phase I markdown reports from the machine-readable
phase_i_results.json payload produced by run_phase_i.run_phase_i().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def _fmt(v: Any, fmt: str = "{:+.4f}") -> str:
    if v is None:
        return "—"
    if isinstance(v, (int,)):
        return str(v)
    try:
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return str(v)


def _group_table(groups: Dict[str, Any]) -> List[str]:
    a = groups["A_smc_only"]
    b = groups["B_smc_plus_ai"]
    c = groups["C_ai_rejected"]
    rows = [
        "| Metric | GROUP A — SMC Only | GROUP B — SMC + AI | GROUP C — AI Rejected |",
        "|---|---:|---:|---:|",
        f"| Executed trades | {a['executed_setups']} | {b['executed_setups']} | {c['executed_setups']} |",
        f"| Coverage of SMC setups | {a['coverage_pct']:.1f}% | {b['coverage_pct']:.1f}% | {c['coverage_pct']:.1f}% |",
        f"| Win count / rate | {a['win_count']} ({a['win_rate_pct']:.2f}%) | {b['win_count']} ({b['win_rate_pct']:.2f}%) | {c['win_count']} ({c['win_rate_pct']:.2f}%) |",
        f"| Loss count / rate | {a['loss_count']} ({a['loss_rate_pct']:.2f}%) | {b['loss_count']} ({b['loss_rate_pct']:.2f}%) | {c['loss_count']} ({c['loss_rate_pct']:.2f}%) |",
        f"| Mean R (expectancy) | {_fmt(a['expectancy_r'])}R | {_fmt(b['expectancy_r'])}R | {_fmt(c['expectancy_r'])}R |",
        f"| Median R | {_fmt(a['median_r'])}R | {_fmt(b['median_r'])}R | {_fmt(c['median_r'])}R |",
        f"| Total R | {_fmt(a['total_r'], '{:+.2f}') }R | {_fmt(b['total_r'], '{:+.2f}')}R | {_fmt(c['total_r'], '{:+.2f}')}R |",
        f"| Profit factor | {a['profit_factor']:.3f} | {b['profit_factor']:.3f} | {c['profit_factor']:.3f} |",
        f"| Max drawdown | {a['max_drawdown_r']:.2f}R | {b['max_drawdown_r']:.2f}R | {c['max_drawdown_r']:.2f}R |",
        f"| Max consecutive losses | {a.get('max_consecutive_losses', '—')} | {b.get('max_consecutive_losses', '—')} | {c.get('max_consecutive_losses', '—')} |",
        f"| Max consecutive wins | {a.get('max_consecutive_wins', '—')} | {b.get('max_consecutive_wins', '—')} | {c.get('max_consecutive_wins', '—')} |",
        f"| Best trade | {_fmt(a.get('best_trade_r'))}R | {_fmt(b.get('best_trade_r'))}R | {_fmt(c.get('best_trade_r'))}R |",
        f"| Worst trade | {_fmt(a.get('worst_trade_r'))}R | {_fmt(b.get('worst_trade_r'))}R | {_fmt(c.get('worst_trade_r'))}R |",
        f"| Mean MFE | {_fmt(a['mean_mfe_r'], '{:.3f}')}R | {_fmt(b['mean_mfe_r'], '{:.3f}')}R | {_fmt(c['mean_mfe_r'], '{:.3f}')}R |",
        f"| Mean MAE | {_fmt(a['mean_mae_r'], '{:.3f}')}R | {_fmt(b['mean_mae_r'], '{:.3f}')}R | {_fmt(c['mean_mae_r'], '{:.3f}')}R |",
        f"| Avg SL distance | {_fmt(a.get('avg_sl_distance_pct'), '{:.3f}')}% | {_fmt(b.get('avg_sl_distance_pct'), '{:.3f}')}% | {_fmt(c.get('avg_sl_distance_pct'), '{:.3f}')}% |",
        f"| Avg TP distance | {_fmt(a.get('avg_tp_distance_pct'), '{:.3f}')}% | {_fmt(b.get('avg_tp_distance_pct'), '{:.3f}')}% | {_fmt(c.get('avg_tp_distance_pct'), '{:.3f}')}% |",
        f"| Avg holding bars | {a['avg_holding_bars']} | {b['avg_holding_bars']} | {c['avg_holding_bars']} |",
    ]
    return rows


def _asset_table(rows: List[Dict[str, Any]]) -> List[str]:
    header = [
        "| Asset | SMC setups | AI accepted | AI rejected | AI coverage | SMC E[R] | AI E[R] | ΔE[R] | SMC PF | AI PF | SMC WR | AI WR | SMC MDD | AI MDD | Net R (SMC) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines = list(header)
    for r in rows:
        lines.append(
            f"| {r['asset']} | {r['smc_setups']} | {r['ai_accepted']} | {r['ai_rejected']} | "
            f"{r['ai_coverage_pct']:.1f}% | {_fmt(r['smc_expectancy_r'])} | {_fmt(r['ai_expectancy_r'])} | "
            f"{_fmt(r['incremental_expectancy_r'])} | {_fmt(r['smc_profit_factor'], '{:.3f}')} | "
            f"{_fmt(r['ai_profit_factor'], '{:.3f}')} | {_fmt(r['smc_win_rate_pct'], '{:.1f}')}% | "
            f"{_fmt(r['ai_win_rate_pct'], '{:.1f}')}% | {_fmt(r['smc_max_drawdown_r'], '{:.2f}')} | "
            f"{_fmt(r['ai_max_drawdown_r'], '{:.2f}')} | {_fmt(r['smc_total_r'], '{:+.2f}')} |"
        )
    return lines


# ═════════════════════════════════════════════════════════════════════════════
# Main report
# ═════════════════════════════════════════════════════════════════════════════


def render_main_report(res: Dict[str, Any]) -> str:
    rep = res["reproducibility"]
    gate = res["promotion_gate"]
    audit = res["extraction_audit"]
    total_setups = sum(a["unique_setups"] for a in audit.values())

    lines = [
        "# Phase I — Real Order-Block Historical Trade Replay Report",
        "",
        f"Generated (UTC): {res['generated_at_utc']}",
        "",
        "## 1. Objective",
        "",
        "Determine whether the frozen Phase H AI model improves the **REAL QuantEdge deterministic",
        "SMC / Order-Block strategy** when evaluated on actual historical OB trades generated by the",
        "existing production engine — an OB-centric historical trade replay with zero synthetic setups.",
        "",
        "## 2. Repository architecture used",
        "",
        "| Component | Authoritative source (reused unmodified) |",
        "|---|---|",
        "| Volatility parsing | `quantedge.smc.volatility.parse_candles_with_volatility` (ATR-200 × 2.0) |",
        "| Structure / BOS / CHOCH | `quantedge.smc.structure.StructureDetector` streaming (internal=5, swing=50) |",
        "| Order Block creation | `quantedge.smc.order_blocks.OrderBlockDetector` (LuxAlgo slice semantics) |",
        "| Entry decision | `quantedge.strategy.engine.StrategyEngine.evaluate_state` (Phase 4.2) |",
        "| Entry price rule | `OrderBlock.calculate_entry_price()` |",
        "| Stop loss (second edge) | `OrderBlock.calculate_stop_loss()` |",
        "| Forward outcome replay | `quantedge.ai.training.real_dataset_builder.replay_forward_outcome` |",
        "| Causal feature extraction | `extract_causal_24_features` (canonical-24-v2) |",
        "| Frozen AI inference | ONNX `quantedge-ai-v2.onnx` via onnxruntime (identical to Phase G shadow path) |",
        "| Metrics | `quantedge.ai.evaluation.smc_baseline.calculate_performance_metrics` |",
        "",
        "No parallel SMC/OB detector was written; no production SMC behavior was altered.",
        "",
        "## 3. SMC/OB methodology",
        "",
        "- **BOS/CHOCH**: confirmed structural breaks emitted by the LuxAlgo-style streaming structure detectors.",
        "- **Bullish OB**: minimum parsed-low candle in `[broken_pivot_index, break_index)` after a bullish break; OB zone = full candle range [low, high].",
        "- **Bearish OB**: maximum parsed-high candle in the same slice after a bearish break.",
        "- **OB creation time** = formation (extreme) candle timestamp; **confirmation time** = structural-break candle timestamp (`break_index`).",
        "- **Lifecycle**: active from `break_index + 1`; invalidated on wick-through of the far boundary (Phase H dataset-builder convention).",
        "- **Duplicate handling**: one trade per OB (application USED-state semantics); later decisions on the same OB counted as duplicates and skipped.",
        "",
        "## 4. Entry methodology",
        "",
        "The application's actual entry rule is used verbatim:",
        "",
        "- A trade qualifies when a **closed candle's close is inside** an eligible OB zone (FRESH/TOUCHED)",
        "  with directional context, per `StrategyEngine.evaluate_state`.",
        "- **Entry price** = `OrderBlock.calculate_entry_price()`: narrow OB (width ≤ 0.6%) → edge entry;",
        "  wide OB → 25%-from-edge level. Fill assumed at this limit level from the following bar onward",
        "  (repository replay convention shared with Phases C–H).",
        "",
        "## 5. SL methodology — second edge of the OB",
        "",
        "`OrderBlock.calculate_stop_loss()` (engine/src/quantedge/smc/models.py):",
        "",
        "- Bullish setup → SL = OB **bottom_price** (second edge).",
        "- Bearish setup → SL = OB **top_price** (second edge).",
        "",
        "Per-setup recorded: `entry_price`, `stop_price`, `stop_distance`, `stop_distance_percent`,",
        "`atr_normalized_stop_distance`. All values are known at entry (causal).",
        "",
        "## 6. TP methodology — PHASE_I_OB_60TP_35SL",
        "",
        "The requested \"TP ≈ 60% / SL ≈ 35%\" is resolved against existing QuantEdge conventions",
        "(`strategy/risk.py`): risk_per_trade = **35% of account balance**, target reward = **60% of",
        "account balance**, dynamic leverage `lev = max(1, ⌊35/stop_distance_pct⌋)` makes the loss at SL",
        "exactly 35% of balance. For the TP to realise exactly +60% under that leverage:",
        "",
        "```text",
        "tp_distance = (0.60/lev)·entry = (60/35) · stop_distance   →   reward_multiple = 60/35 ≈ 1.7143R",
        "```",
        "",
        "Implemented as the explicit experimental config `PHASE_I_OB_60TP_35SL`. The production default",
        "(reward_multiple = 2.0) remains untouched. No parameter was selected using OOS performance.",
        "",
        "## 7–9. AI feature contract, model & threshold",
        "",
        f"- Feature contract: `{rep['feature_contract_version']}` ({FEATURE_CONTRACT_COUNT(res)} features, causal, data ≤ T only).",
        f"- Model: `{rep['model_name']}` ({rep['model_type']}), SHA256 `{rep['onnx_sha256'][:16]}…`.",
        f"- Threshold: predicted realized R ≥ **{res['reproducibility']['threshold_predicted_r']}R** → ACCEPT (frozen Phase H value).",
        "- The AI decision is computed at the decision bar BEFORE any forward outcome exists.",
        "- The model was NOT retrained or retuned on Phase I results.",
        "",
        "## 10. Dataset provenance",
        "",
        f"- Canonical Delta Exchange India 1h datasets (BTCUSD, ETHUSD, SOLUSD, XRPUSD), manifest SHA256 `{rep['manifest_sha256'][:16]}…`.",
        f"- Dataset fingerprint: `{rep['dataset_fingerprint']}`.",
        "- 100% real historical data; no synthetic candles, interpolation, or borrowed prices.",
        "",
        "## 11. Temporal split",
        "",
        f"- Frozen Phase H OOS window (confirmatory): **{res['oos_window_frozen']['start_utc']} → {res['oos_window_frozen']['end_utc']}**.",
        "- Full-history results are descriptive only; all promotion decisions use OOS exclusively.",
        "- Nothing (threshold, TP multiple, leverage cap, assets, block size) was tuned on OOS.",
        "",
        f"## 12–14. Groups A/B/C — pooled results (OOS window, gross R)",
        "",
        f"Selections in OOS window: **{res['oos_gross']['n_selection']}**",
        "",
    ]
    lines += _group_table(res["oos_gross"]["groups"])
    eg = res["oos_gross"]
    lines += [
        "",
        f"- Incremental expectancy (AI − SMC): **{_fmt(eg['incremental_expectancy_r'])}R**",
        f"- AI filter lift: {_fmt(eg['ai_filter_lift_r'])}R | AI rejection value: {_fmt(eg['ai_rejection_value_r'])}R",
        "",
        "### Full history (descriptive, gross R)",
        "",
        f"Selections: {res['full_period_gross']['n_selection']}; incremental expectancy {_fmt(res['full_period_gross']['incremental_expectancy_r'])}R.",
        "",
        "### Costs impact (net of fees/slippage/funding)",
        "",
        f"- Full period net incremental expectancy: {_fmt(res['full_period_net']['incremental_expectancy_r'])}R",
        f"- OOS net incremental expectancy: {_fmt(res['oos_net']['incremental_expectancy_r'])}R",
        "",
        "## 15. Asset-level results",
        "",
        "### Frozen OOS window",
        "",
    ]
    lines += _asset_table(res["per_asset_oos"])
    lines += [
        "",
        "**Pooled performance never hides asset failures:** see ΔE[R] per asset above.",
        "",
        "## 16. Prediction buckets",
        "",
    ]
    bk = res["score_buckets"]
    lines.append("| Bucket | Count | Predicted mean R | Realized mean R | Win rate | PF | Median R | Mean MFE | Mean MAE |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for label, s in bk["buckets"].items():
        cells = [
            label,
            str(s["count"]),
            _fmt(s["predicted_mean_r"]),
            _fmt(s["realized_mean_r"]),
            _fmt(s["win_rate_pct"], "{:.1f}") + ("%" if s["win_rate_pct"] is not None else ""),
            _fmt(s["profit_factor"], "{:.3f}"),
            _fmt(s["median_r"]),
            _fmt(s["mean_mfe_r"], "{:.3f}"),
            _fmt(s["mean_mae_r"], "{:.3f}"),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        f"- Monotonic calibration (realized mean R non-decreasing with predicted bucket, ≥5 samples/bucket): "
        f"**{bk['monotonic_calibration']}**",
        "" if bk["monotonic_calibration"] else "- ⚠️ **CALIBRATION FAILURE**: realized performance does NOT increase monotonically with predicted R.",
        "",
        "## 17. Statistical analysis",
        "",
        f"- Method: paired Moving Block Bootstrap, N={rep['bootstrap_n']}, block size {res['bootstrap'].get('mbb_block_size', '—')}, seed {rep['bootstrap_seed']}.",
        f"- SMC OOS expectancy 95% CI: [{_fmt(_ci(res, 'smc_mean_r_95ci')[0], '{:+.4f}')}, {_fmt(_ci(res, 'smc_mean_r_95ci')[1], '{:+.4f}')}]",
        f"- AI OOS expectancy 95% CI: [{_fmt(_ci(res, 'ai_mean_r_95ci')[0], '{:+.4f}')}, {_fmt(_ci(res, 'ai_mean_r_95ci')[1], '{:+.4f}')}]",
        f"- **Incremental expectancy 95% CI: [{_fmt(_ci(res, 'incremental_mean_r_95ci')[0], '{:+.4f}')}, {_fmt(_ci(res, 'incremental_mean_r_95ci')[1], '{:+.4f}')}]**",
        f"- Rejected-trade expectancy 95% CI: [{_fmt(_ci(res, 'rejected_mean_r_95ci')[0], '{:+.4f}')}, {_fmt(_ci(res, 'rejected_mean_r_95ci')[1], '{:+.4f}')}]",
        "- If any CI crosses zero the corresponding claim is NOT statistically significant.",
        "",
        "See PHASE_I_STATISTICAL_REPORT.md for details and power limitations.",
        "",
        "## 18. Drawdown",
        "",
        f"- SMC-only OOS max drawdown: **{res['oos_gross']['groups']['A_smc_only']['max_drawdown_r']:.2f}R** "
        f"(duration {res['oos_gross']['groups']['A_smc_only'].get('max_drawdown_duration_trades', '—')} trades)",
        f"- SMC+AI OOS max drawdown: **{res['oos_gross']['groups']['B_smc_plus_ai']['max_drawdown_r']:.2f}R** "
        f"(duration {res['oos_gross']['groups']['B_smc_plus_ai'].get('max_drawdown_duration_trades', '—')} trades)",
        "- Leveraged account simulation is reported separately in PHASE_I_LEVERAGE_ANALYSIS.md and never mixed into strategy metrics.",
        "",
        "## 19. Costs",
        "",
        f"- Taker fee {rep['fee_assumptions']['taker_per_side']*100:.2f}% per side; slippage {rep['slippage_assumptions']['per_side']*100:.3f}% per side; funding {rep['funding_assumption_per_hour']*100:.4f}%/hour on notional.",
        "- Fee/slippage are documented research assumptions (repo has no authoritative fee constants); slippage reuses `quantedge.config.slippage_pct`.",
        "",
        "## 20. Leverage simulation",
        "",
        f"- Formula: `{rep['leverage_formula']}` → exactly **35x at 1% stop distance**.",
        f"- Observed leverage range: {res['leverage_analysis']['min_leverage']}x – {res['leverage_analysis']['max_leverage']}x (avg {res['leverage_analysis']['avg_leverage']}x).",
        "- Leverage does NOT change R; account simulation is separate (see PHASE_I_LEVERAGE_ANALYSIS.md).",
        "",
        "## 21. Liquidation risk",
        "",
        f"- Trades where estimated liquidation occurs before SL: **{res['leverage_analysis']['liquidation_before_sl_count']}**"
        f" (assets: {res['leverage_analysis']['liquidation_risk_flagged_assets'] or 'none'}).",
        "- Isolated-margin approximation with maintenance margin assumption "
        f"{res['leverage_analysis']['maintenance_margin_rate_assumption']*100:.1f}%.",
        "",
        "## 22. Failure cases",
        "",
        f"- Duplicate decisions skipped: {sum(a['duplicate_decisions_skipped'] for a in audit.values())} "
        "(same OB re-engaged on later bars — application executes once per OB).",
        f"- End-of-data exits occur for late-OOS entries (data ends {res['oos_window_frozen']['end_utc']}); these exit mark-to-market at final close.",
        "",
        "## 23. Limitations",
        "",
        "- Independent per-setup accounting (no portfolio-level single-position sequencing) — consistent with Phases C–H methodology.",
        "- Intrabar ordering unknown on OHLC bars; conservative SL-first policy applied.",
        "- Entry fill assumes limit fill at the OB-derived level (repository convention).",
        "- Fee/funding figures are research assumptions, not exchange-authoritative constants.",
        "- Single historical sample (2026 YTD, 4 assets); bootstrap CIs quantify but cannot eliminate sampling limits.",
        "",
        "## 24. Promotion gate",
        "",
    ]

    lines.append("| Criterion | Passed | Detail |")
    lines.append("|---|---|---|")
    for name, c in gate["criteria"].items():
        lines.append(f"| {name} | {'✅' if c['passed'] else '❌'} | {c['detail']} |")
    lines += [
        "",
        f"**Gate status: {gate['status']}**",
        f"live_execution_authorized = **{str(gate['live_execution_authorized']).lower()}**",
        f"AI live execution = **{gate['ai_live_execution']}**",
        "",
        "## 25. Final conclusion",
        "",
    ]
    inc = res["oos_gross"].get("incremental_expectancy_r")
    ci = _ci(res, "incremental_mean_r_95ci")
    if gate["all_pass"]:
        verdict = (
            "**CANDIDATE FOR GOVERNANCE REVIEW** — SMC+AI beat SMC-only on the frozen OOS window with "
            "the pre-declared criteria satisfied. This authorises governance review only; live trading remains blocked."
        )
    elif inc is not None and inc > 0:
        verdict = (
            "**PROMISING BUT NOT STATISTICALLY PROVEN** — the AI filter shows positive incremental expectancy "
            f"({inc:+.4f}R) on the frozen OOS window, but the evidence fails at least one pre-declared criterion "
            f"(incremental 95% CI [{ci[0]}, {ci[1]}] crosses zero or another robustness condition failed)."
        )
    else:
        verdict = (
            "**AI DOES NOT IMPROVE THE CURRENT SMC STRATEGY** — on the frozen OOS window the AI filter did not "
            f"improve expectancy (incremental {_fmt(inc)}R). A negative result is a valid experimental outcome."
        )
    lines += [verdict, "", "---", "",
              "*Phase I is research/shadow-only. Zero live orders were placed. The deterministic SMC engine",
              "remains the sole production execution authority.*"]

    return "\n".join(lines)


def FEATURE_CONTRACT_COUNT(res: Dict[str, Any]) -> int:
    return len(res["reproducibility"]["feature_names"])


def _ci(res: Dict[str, Any], key: str) -> List[Any]:
    val = res.get("bootstrap", {}).get(key)
    if not val or len(val) != 2:
        return [None, None]
    return [val[0], val[1]]


# ═════════════════════════════════════════════════════════════════════════════
# AI filter analysis report
# ═════════════════════════════════════════════════════════════════════════════


def render_filter_report(res: Dict[str, Any]) -> str:
    oos = res["oos_gross"]
    bk = res["score_buckets"]
    lines = [
        "# Phase I — AI Filter Analysis",
        "",
        f"Generated (UTC): {res['generated_at_utc']}",
        "",
        "## Question",
        "",
        "Does the AI remove poor-quality REAL OB setups? Group C (AI-rejected) outcomes are computed",
        "even though those trades would NOT be executed under SMC+AI — enabling direct filter-value analysis.",
        "",
        "## Filter value (OOS window, gross R)",
        "",
        f"- Accepted expectancy (Group B): **{_fmt(oos['accepted_expectancy_r'])}R**",
        f"- Rejected expectancy (Group C): **{_fmt(oos['rejected_expectancy_r'])}R**",
        f"- Filter lift (B − A): **{_fmt(oos['ai_filter_lift_r'])}R**",
        f"- Rejection value (A − C): **{_fmt(oos['ai_rejection_value_r'])}R**",
        "",
        "> Interpretation: the filter is genuinely useful iff accepted setups materially outperform rejected",
        "> setups AND overall expectancy improves without unacceptable coverage loss.",
        "",
        "## Pooled full-history view (gross R)",
        "",
        f"- Accepted expectancy: {_fmt(res['full_period_gross']['accepted_expectancy_r'])}R",
        f"- Rejected expectancy: {_fmt(res['full_period_gross']['rejected_expectancy_r'])}R",
        f"- Filter lift: {_fmt(res['full_period_gross']['ai_filter_lift_r'])}R",
        "",
        "## Per-asset filter behaviour (OOS window)",
        "",
    ]
    lines += _asset_table(res["per_asset_oos"])
    lines += [
        "",
        "## Score-bucket calibration (all setups)",
        "",
        f"Monotonic calibration: **{bk['monotonic_calibration']}**",
        "",
        "| Bucket | Count | Pred mean R | Realized mean R | WR | PF | Median R | MFE | MAE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, s in bk["buckets"].items():
        lines.append(
            f"| {label} | {s['count']} | {_fmt(s['predicted_mean_r'])} | "
            f"{_fmt(s['realized_mean_r'])} | {_fmt(s['win_rate_pct'], '{:.1f}')} | {_fmt(s['profit_factor'], '{:.3f}')} | "
            f"{_fmt(s['median_r'])} | {_fmt(s['mean_mfe_r'], '{:.3f}')} | {_fmt(s['mean_mae_r'], '{:.3f}')} |"
        )
    lines += [
        "",
        "## Conclusion",
        "",
    ]
    acc = oos.get("accepted_expectancy_r")
    rej = oos.get("rejected_expectancy_r")
    if acc is not None and rej is not None and rej < acc - 0.10:
        lines.append(
            "The AI separates OB trade quality: rejected trades underperform accepted trades by more than the pre-declared 0.10R materiality margin."
        )
    else:
        lines.append(
            "The AI does NOT yet demonstrate reliable separation between good and bad REAL OB setups beyond noise/materiality thresholds."
        )
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# Leverage analysis report
# ═════════════════════════════════════════════════════════════════════════════


def render_leverage_report(res: Dict[str, Any]) -> str:
    lev = res["leverage_analysis"]
    lines = [
        "# Phase I — Leverage & Liquidation Analysis",
        "",
        f"Generated (UTC): {res['generated_at_utc']}",
        "",
        "## Intended formula (verified against repository conventions)",
        "",
        "Production dynamic-leverage rule (`strategy/engine.py`, Phase 5.9 authoritative):",
        "",
        "```text",
        "stop_distance_pct = (|entry − SL| / entry) × 100",
        "leverage          = min(cap=100, max(1, ⌊35.0 / stop_distance_pct⌋))",
        "```",
        "",
        "With SL distance = 1% of entry ⇒ leverage = **35×** — matching the user's intent",
        "\"if SL distance = 1%, leverage = 35x\". Loss at SL ≡ 35% of account balance by construction.",
        "",
        "## Account simulation parameters",
        "",
        f"- Cap: {lev['cap']}× (production StrategyConfig.max_leverage)",
        f"- Maintenance margin rate assumption: {lev['maintenance_margin_rate_assumption']*100:.1f}% (isolated-margin research approximation)",
        f"- Observed leverage across {res['trade_count']} trades: avg {lev['avg_leverage']}×, range [{lev['min_leverage']}×, {lev['max_leverage']}×]",
        "",
        "## Liquidation analysis",
        "",
        "Estimated liquidation move ≈ `1/leverage − mmr` (fraction of entry).",
        "",
        f"- Trades flagged **LIQUIDATION_RISK** (estimated liquidation at/inside SL): **{lev['liquidation_before_sl_count']}**",
        f"- Assets affected: {lev['liquidation_risk_flagged_assets'] or 'none'}",
        "",
    ]
    if lev["liquidation_before_sl_count"] > 0:
        lines += [
            "### Why violations occur",
            "",
            "For very tight stops the uncapped formula demands >100×; the production cap binds, so the",
            "margin cushion (1/cap = 1%) can sit closer than the stop. Such configurations are unsafe",
            "under this leverage scheme and must be excluded/capped further before any live use.",
            "",
        ]
    else:
        lines += [
            "No trade breaches the liquidation boundary before its intended stop under the capped formula:",
            "for every observed stop width, `1/leverage − mmr > stop_distance`, i.e. the SL always sits inside",
            "the estimated liquidation level. Residual risks remain: gaps through both levels, exchange-specific",
            "margin rules, and funding accrual.",
            "",
        ]

    # Per-asset avg leverage + violations table
    lines += [
        "## Per-asset leverage summary (OOS window)",
        "",
        "| Asset | Avg leverage | Liquidation-before-SL trades |",
        "|---|---:|---:|",
    ]
    for r in res["per_asset_oos"]:
        lines.append(f"| {r['asset']} | {r['avg_leverage']:.1f}× | {r['liquidation_violations']} |")

    lines += [
        "",
        "## Strategy vs account separation",
        "",
        "- Strategy metrics (R, expectancy, PF, MDD) are computed at 1R = risk amount and are INDEPENDENT of leverage.",
        "- Leveraged account returns scale linearly with leverage in R-space (return ≈ Σ Rᵢ × 35% of balance per trade)",
        "  but amplify sequence risk: max leveraged drawdown ≈ strategy MDD × 35% of balance per unit R.",
        "- Liquidation feasibility, not paper profitability, decides whether the leverage idea is practically safe.",
        "",
        "## Verdict",
        "",
    ]
    if lev["liquidation_before_sl_count"] == 0:
        lines.append(
            "UNSAFE-CLEAR: no estimated liquidation precedes SL under the capped production formula; "
            "however this conclusion depends on the documented margin assumptions and does not address gap risk."
        )
    else:
        lines.append(
            "⚠️ UNSAFE CONFIGURATION DETECTED: some trades could face liquidation before their stop loss under "
            "the proposed leverage scheme. Do not adopt this leverage configuration."
        )
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# Statistical report
# ═════════════════════════════════════════════════════════════════════════════


def render_statistical_report(res: Dict[str, Any]) -> str:
    bs = res["bootstrap"]
    oos_n = res["oos_gross"]["n_selection"]
    ai_n = res["oos_gross"]["groups"]["B_smc_plus_ai"]["executed_setups"]
    lines = [
        "# Phase I — Statistical Report",
        "",
        f"Generated (UTC): {res['generated_at_utc']}",
        "",
        "## Method",
        "",
        "- Paired Moving Block Bootstrap (MBB) over the frozen-OOS trade sequence (consistent with Phases C–H).",
        f"- N = {bs.get('n_bootstraps')} replicates (≥ 1000 required), block size = {bs.get('mbb_block_size')} = max(3, ⌈N^(1/3)⌉), seed = {bs.get('seed')}.",
        "- Each replicate resamples blocks of indices from the FULL Group-A sequence; SMC mean, AI mean and their",
        "  difference are computed on identical indices (paired), preserving cross-group dependence.",
        "",
        "## Confidence intervals (OOS, gross R)",
        "",
        f"| Quantity | 95% CI low | 95% CI high | Crosses zero? |",
        "|---|---:|---:|---|",
    ]
    for key, label in [
        ("smc_mean_r_95ci", "SMC-only expectancy"),
        ("ai_mean_r_95ci", "SMC+AI expectancy"),
        ("incremental_mean_r_95ci", "Incremental expectancy (AI − SMC)"),
        ("rejected_mean_r_95ci", "AI-rejected expectancy"),
    ]:
        ci = bs.get(key)
        if ci:
            crosses = "YES" if ci[0] <= 0 <= ci[1] else "no"
            lines.append(f"| {label} | {ci[0]:+.4f} | {ci[1]:+.4f} | {crosses} |")
    inc_ci = bs.get("incremental_mean_r_95ci")
    if inc_ci:
        lines += [
            "",
            (
                "**The incremental expectancy 95% CI EXCLUDES zero** — the AI improvement is statistically significant at the 5% level under MBB."
                if inc_ci[0] > 0
                else "**The incremental expectancy 95% CI INCLUDES zero** — the AI improvement is NOT statistically significant."
            ),
        ]
    lines += [
        "",
        "## Sample size & power limitations",
        "",
        f"- OOS selections: {oos_n} total; AI-accepted subset: {ai_n}.",
    ]
    if ai_n < 30:
        lines += [
            f"- ⚠️ The AI-accepted sample ({ai_n}) is small (< 30). All point estimates carry high variance;",
            "  bootstrap percentiles understate tail uncertainty at this size. Treat any positive result as provisional.",
        ]
    lines += [
        "- Overlapping 72h holding windows induce serial correlation; MBB blocks mitigate but cannot eliminate it.",
        "- One historical path (single 2026 sample). No multiple-scenario robustness is possible.",
        "",
        "## Decision rule applied",
        "",
        "- Statistical significance requires incremental 95% CI lower bound > 0 (pre-declared criterion C5).",
        "- If the CI includes zero the experiment is reported as inconclusive REGARDLESS of point estimates.",
        "",
        "## Reproducibility",
        "",
        f"- Seed {bs.get('seed')}; identical inputs reproduce byte-identical intervals.",
        f"- Bootstrap N={bs.get('n_bootstraps')}, block size {bs.get('mbb_block_size')} chosen BEFORE OOS evaluation.",
    ]
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════


def write_all_phase_i_reports(results: Dict[str, Any], docs_dir: Path) -> None:
    (docs_dir / "PHASE_I_OB_TRADE_REPLAY_REPORT.md").write_text(render_main_report(results), encoding="utf-8")
    (docs_dir / "PHASE_I_AI_FILTER_ANALYSIS.md").write_text(render_filter_report(results), encoding="utf-8")
    (docs_dir / "PHASE_I_LEVERAGE_ANALYSIS.md").write_text(render_leverage_report(results), encoding="utf-8")
    (docs_dir / "PHASE_I_STATISTICAL_REPORT.md").write_text(render_statistical_report(results), encoding="utf-8")
