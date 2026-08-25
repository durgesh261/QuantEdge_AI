"""
QuantEdge AI — Phase I Runner: Real OB Historical Trade Replay & AI Filter Validation.

Orchestrates the complete Phase I experiment:

    REAL SMC/OB setups (authoritative engine)
        -> causal 24-feature extraction at decision bar (data <= T only)
        -> frozen ONNX inference (quantedge-ai-v2.onnx, threshold +0.50R)
        -> AI decision BEFORE outcome
        -> forward-only candle replay (SL-first same-candle policy, 72h horizon)
        -> GROUP A (SMC only) / GROUP B (SMC+AI) / GROUP C (AI rejected)
        -> metrics per asset + pooled, gross & net of costs
        -> MBB bootstrap CIs
        -> leverage & liquidation analysis
        -> Phase I promotion gate (research-only; never authorises live trading)

Usage:
    python -m quantedge.ai.evaluation.run_phase_i

Outputs:
    docs/ai/phase_i_results.json
    docs/ai/PHASE_I_OB_TRADE_REPLAY_REPORT.md
    docs/ai/PHASE_I_AI_FILTER_ANALYSIS.md
    docs/ai/PHASE_I_LEVERAGE_ANALYSIS.md
    docs/ai/PHASE_I_STATISTICAL_REPORT.md

This runner is strictly research/shadow: it places ZERO live orders.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import onnxruntime as ort

from quantedge.ai.evaluation.phase_i_ob_replay import (
    BOOTSTRAP_N,
    BOOTSTRAP_SEED,
    MAINTENANCE_MARGIN_RATE,
    MAX_LOSS_PCT_OF_BALANCE,
    PHASE_I_CONFIG_NAME,
    PHASE_I_TP_RR_CONFIG,
    PRODUCTION_MAX_LEVERAGE,
    REPLAY_HORIZON_BARS,
    SLIPPAGE_RATE_PER_SIDE,
    TAKER_FEE_RATE_PER_SIDE,
    FUNDING_RATE_PER_HOUR,
    WARMUP_BARS,
    ExtendedMetrics,
    PhaseISetup,
    PhaseITradeRecord,
    build_smc_context,
    compute_extended_metrics,
    compute_score_buckets,
    equity_curve,
    evaluate_phase_i_gate,
    extract_phase_i_setups,
    load_canonical_candles,
    mbb_block_size,
    moving_block_bootstrap_groups,
    replay_phase_i_trades,
)
from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.model_config import (
    AUTHORITATIVE_MODEL_CONFIG,
    compute_dataset_fingerprint,
    compute_onnx_sha256,
)

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

#: Frozen Phase H out-of-sample window (model provenance; MUST stay untouched).
OOS_START_UTC = "2026-07-06T00:00:00+00:00"
OOS_END_UTC = "2026-08-21T14:00:00+00:00"

AI_THRESHOLD = AUTHORITATIVE_MODEL_CONFIG.threshold  # +0.50R frozen


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


# ═════════════════════════════════════════════════════════════════════════════
# Per-asset processing
# ═════════════════════════════════════════════════════════════════════════════


def _predict_batch(session: ort.InferenceSession, features: List[Tuple[float, ...]]) -> np.ndarray:
    inp = np.array(np.asarray(features, dtype=np.float32), dtype=np.float32)
    if inp.ndim == 1:
        inp = inp.reshape(1, -1)
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    return session.run([output_name], {input_name: inp})[0]


def process_asset(
    session: ort.InferenceSession,
    canonical_base: Path,
    symbol: str,
) -> Dict[str, Any]:
    candles = load_canonical_candles(canonical_base, symbol)
    ctx = build_smc_context(candles)
    setups, audit = extract_phase_i_setups(candles, symbol, ctx=ctx)

    preds = (
        _predict_batch(session, [s.features_24 for s in setups])
        if setups
        else np.zeros((0, 3), dtype=np.float32)
    )
    pred_map = {
        s.setup_id: (float(preds[k, 0]), float(preds[k, 1]), float(preds[k, 2]))
        for k, s in enumerate(setups)
    }
    records = replay_phase_i_trades(candles, setups, pred_map, AI_THRESHOLD)
    audit["trades_replayed"] = len(records)
    return {"candles": candles, "setups": setups, "records": records, "audit": audit}


# ═════════════════════════════════════════════════════════════════════════════
# Group analysis helpers
# ═════════════════════════════════════════════════════════════════════════════


def _in_oos(ts_iso: str) -> bool:
    return OOS_START_UTC <= ts_iso <= OOS_END_UTC


def _decision_in_oos(t: PhaseITradeRecord) -> bool:
    """OOS membership is decided by the DECISION timestamp only (causal boundary)."""
    return _in_oos(t.setup.decision_time)


def split_groups(records: List[PhaseITradeRecord]) -> Tuple[List[PhaseITradeRecord], List[PhaseITradeRecord], List[PhaseITradeRecord]]:
    """GROUP A = all SMC setups; B = AI accepted; C = AI rejected."""
    a = list(records)
    b = [t for t in records if t.ai_decision == "ACCEPT"]
    c = [t for t in records if t.ai_decision == "REJECT"]
    return a, b, c


def analyze_window(
    records: List[PhaseITradeRecord],
    oos_only: bool,
    net: bool = False,
) -> Dict[str, Any]:
    sel = [t for t in records if (not oos_only or _decision_in_oos(t))]
    groups = {}
    a, b, c = split_groups(sel)
    rkey = "net_r" if net else "gross_r"
    groups["A_smc_only"] = compute_extended_metrics(a, r_key=rkey).to_dict()
    groups["B_smc_plus_ai"] = compute_extended_metrics(b, r_key=rkey).to_dict()
    groups["C_ai_rejected"] = compute_extended_metrics(c, r_key=rkey).to_dict()

    extra: Dict[str, Any] = {}
    if len(a) > 0 and len(b) > 0:
        smc_exp = groups["A_smc_only"]["expectancy_r"]
        ai_exp = groups["B_smc_plus_ai"]["expectancy_r"]
        rej_exp = groups["C_ai_rejected"]["expectancy_r"]
        extra = {
            "incremental_expectancy_r": round(ai_exp - smc_exp, 4),
            "ai_filter_lift_r": round(ai_exp - smc_exp, 4),
            "ai_rejection_value_r": round(smc_exp - rej_exp, 4),
            "accepted_expectancy_r": ai_exp,
            "rejected_expectancy_r": rej_exp,
            "pf_improvement": round(groups["B_smc_plus_ai"]["profit_factor"] - groups["A_smc_only"]["profit_factor"], 4),
            "mdd_improvement_r": round(groups["A_smc_only"]["max_drawdown_r"] - groups["B_smc_plus_ai"]["max_drawdown_r"], 2),
        }
    else:
        extra = {
            "incremental_expectancy_r": None,
            "ai_filter_lift_r": None,
            "ai_rejection_value_r": None,
            "accepted_expectancy_r": groups["B_smc_plus_ai"]["expectancy_r"],
            "rejected_expectancy_r": groups["C_ai_rejected"]["expectancy_r"],
            "pf_improvement": None,
            "mdd_improvement_r": None,
        }

    return {"n_selection": len(sel), "groups": groups, **extra}


def per_asset_table(records_by_asset: Dict[str, List[PhaseITradeRecord]], oos_only: bool) -> List[Dict[str, Any]]:
    rows = []
    for sym in SYMBOLS:
        recs = records_by_asset.get(sym, [])
        sel = [t for t in recs if (not oos_only or _decision_in_oos(t))]
        n_all = len(sel)
        acc = [t for t in sel if t.ai_decision == "ACCEPT"]
        rej = [t for t in sel if t.ai_decision == "REJECT"]
        m_all = compute_extended_metrics(sel)
        m_acc = compute_extended_metrics(acc)
        m_rej = compute_extended_metrics(rej)
        inc = (m_acc.base.expectancy_r - m_all.base.expectancy_r) if n_all > 0 and acc else None
        rows.append(
            {
                "asset": sym,
                "smc_setups": n_all,
                "ai_accepted": len(acc),
                "ai_rejected": len(rej),
                "ai_coverage_pct": round(len(acc) / n_all * 100.0, 2) if n_all else 0.0,
                "smc_expectancy_r": m_all.base.expectancy_r,
                "ai_expectancy_r": m_acc.base.expectancy_r if acc else None,
                "incremental_expectancy_r": round(inc, 4) if inc is not None else None,
                "smc_profit_factor": m_all.base.profit_factor,
                "ai_profit_factor": m_acc.base.profit_factor if acc else None,
                "smc_win_rate_pct": m_all.base.win_rate_pct,
                "ai_win_rate_pct": m_acc.base.win_rate_pct if acc else None,
                "smc_max_drawdown_r": m_all.base.max_drawdown_r,
                "ai_max_drawdown_r": m_acc.base.max_drawdown_r if acc else None,
                "smc_total_r": m_all.base.total_r,
                "ai_total_r": m_acc.base.total_r if acc else None,
                "rejected_expectancy_r": m_rej.base.expectancy_r if rej else None,
                "avg_leverage": m_all.avg_leverage,
                "liquidation_violations": sum(1 for t in sel if t.liquidation_before_sl),
            }
        )
    return rows


# ═════════════════════════════════════════════════════════════════════════════
# Main experiment
# ═════════════════════════════════════════════════════════════════════════════


def run_phase_i(verbose: bool = True) -> Dict[str, Any]:
    t_start = time.time()
    repo_root = _get_repo_root()
    canonical_base = repo_root / "data" / "canonical" / "delta_exchange_india"
    onnx_path = repo_root / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"

    if verbose:
        print("=" * 78)
        print("PHASE I — REAL OB HISTORICAL TRADE REPLAY (research/shadow only)")
        print("=" * 78)

    session = ort.InferenceSession(str(onnx_path))
    model_sha = compute_onnx_sha256(onnx_path)
    dataset_fp = compute_dataset_fingerprint(canonical_base)

    all_records: List[PhaseITradeRecord] = []
    records_by_asset: Dict[str, List[PhaseITradeRecord]] = {}
    audits: Dict[str, Any] = {}

    for sym in SYMBOLS:
        res = process_asset(session, canonical_base, sym)
        records_by_asset[sym] = res["records"]
        audits[sym] = res["audit"]
        all_records.extend(res["records"])
        if verbose:
            print(
                f"  {sym}: {res['audit']['unique_setups']} unique OB setups | "
                f"{res['audit']['duplicate_decisions_skipped']} duplicates skipped | "
                f"{len(res['records'])} trades replayed"
            )

    # ── Windows: full history (descriptive) + frozen Phase H OOS (confirmatory) ─
    full_gross = analyze_window(all_records, oos_only=False, net=False)
    full_net = analyze_window(all_records, oos_only=False, net=True)
    oos_gross = analyze_window(all_records, oos_only=True, net=False)
    oos_net = analyze_window(all_records, oos_only=True, net=True)

    # ── Bootstrap CIs (paired MBB on the OOS selection) ─────────────────────────
    oos_sel = [t for t in all_records if _decision_in_oos(t)]
    r_all = np.array([t.gross_r for t in oos_sel], dtype=float)
    mask = np.array([t.ai_decision == "ACCEPT" for t in oos_sel], dtype=bool)
    bootstrap = (
        moving_block_bootstrap_groups(r_all, mask, n_boot=BOOTSTRAP_N, seed=BOOTSTRAP_SEED)
        if len(r_all) > 0
        else {"error": "no OOS trades"}
    )

    # ── Score buckets over ALL setups (calibration diagnostics) ────────────────
    buckets = compute_score_buckets(all_records)

    # ── Leverage & liquidation analysis (all trades, capped production formula) ─
    liq_violations = [t for t in all_records if t.liquidation_before_sl]
    liq_stop_ratios = [
        t.liq_distance_fraction / (t.setup.stop_distance_percent / 100.0)
        for t in all_records
        if t.setup.stop_distance_percent > 0
    ]
    lev_summary = {
        "formula": "leverage = min(100, max(1, floor(35.0 / stop_distance_pct)))",
        "cap": PRODUCTION_MAX_LEVERAGE,
        "risk_at_sl_pct_of_balance": float(MAX_LOSS_PCT_OF_BALANCE),
        "maintenance_margin_rate_assumption": MAINTENANCE_MARGIN_RATE,
        "avg_leverage": round(float(np.mean([t.setup.leverage for t in all_records])), 2) if all_records else 0.0,
        "min_leverage": min((t.setup.leverage for t in all_records), default=0),
        "max_leverage": max((t.setup.leverage for t in all_records), default=0),
        "liquidation_before_sl_count": len(liq_violations),
        "liquidation_risk_flagged_assets": sorted({t.setup.asset for t in liq_violations}),
        "min_liq_distance_to_stop_ratio": round(min(liq_stop_ratios), 4) if liq_stop_ratios else None,
        "near_boundary_trades_ratio_lt_2x": (
            sum(1 for r in liq_stop_ratios if r < 2.0) if liq_stop_ratios else 0
        ),
    }

    # ── Promotion gate (OOS, gross R primary) ───────────────────────────────────
    oos_smc = compute_extended_metrics([t for t in oos_sel])
    oos_ai = compute_extended_metrics([t for t in oos_sel if t.ai_decision == "ACCEPT"])
    per_asset_inc = {
        row["asset"]: (row["incremental_expectancy_r"] if row["incremental_expectancy_r"] is not None else -99.0)
        for row in per_asset_table(records_by_asset, oos_only=True)
    }
    rejected_exp = oos_gross["rejected_expectancy_r"]
    accepted_exp = oos_gross["accepted_expectancy_r"]
    oos_accepted_n = sum(1 for t in oos_sel if t.ai_decision == "ACCEPT")
    oos_ai_coverage = (oos_accepted_n / len(oos_sel) * 100.0) if oos_sel else 0.0
    gate = evaluate_phase_i_gate(
        oos_smc=oos_smc,
        oos_ai=oos_ai,
        incremental_ci_low=(bootstrap.get("incremental_mean_r_95ci", (-99, 0))[0] if "incremental_mean_r_95ci" in bootstrap else -99.0),
        per_asset_incremental=per_asset_inc,
        rejected_expectancy=rejected_exp if rejected_exp is not None else 0.0,
        accepted_expectancy=accepted_exp if accepted_exp is not None else 0.0,
        liquidation_violations=len(liq_violations),
        ai_coverage_pct=oos_ai_coverage,
    )

    results: Dict[str, Any] = {
        "phase": "I",
        "experiment_name": "Real OB Historical Trade Replay & AI Filter Validation",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_name": PHASE_I_CONFIG_NAME,
        "reproducibility": {
            "dataset_fingerprint": dataset_fp,
            "manifest_sha256": hashlib.sha256((canonical_base / "manifest.json").read_bytes()).hexdigest(),
            "onnx_sha256": model_sha,
            "model_name": AUTHORITATIVE_MODEL_CONFIG.model_name,
            "model_type": AUTHORITATIVE_MODEL_CONFIG.model_type,
            "feature_contract_version": AUTHORITATIVE_MODEL_CONFIG.feature_contract_version,
            "feature_names": list(FEATURE_NAMES),
            "threshold_predicted_r": AI_THRESHOLD,
            "tp_sl_config": {
                "name": PHASE_I_CONFIG_NAME,
                "reward_multiple": str(PHASE_I_TP_RR_CONFIG.reward_multiple),
                "interpretation": "TP distance = 60/35 x SL distance (account-level 60% reward vs 35% risk)",
                "production_default_unchanged": "reward_multiple=2.0 (untouched)",
            },
            "sl_config": "second edge of the Order Block (OrderBlock.calculate_stop_loss)",
            "entry_rule": "StrategyEngine.evaluate_state -> OrderBlock.calculate_entry_price",
            "intrabar_policy": "same-candle TP+SL => SL first (repository convention)",
            "leverage_formula": lev_summary["formula"],
            "fee_assumptions": {"taker_per_side": TAKER_FEE_RATE_PER_SIDE},
            "slippage_assumptions": {"per_side": SLIPPAGE_RATE_PER_SIDE},
            "funding_assumption_per_hour": FUNDING_RATE_PER_HOUR,
            "bootstrap_n": BOOTSTRAP_N,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_block_size_rule": "max(3, ceil(N^(1/3)))",
            "random_seed": BOOTSTRAP_SEED,
        },
        "oos_window_frozen": {"start_utc": OOS_START_UTC, "end_utc": OOS_END_UTC},
        "extraction_audit": audits,
        "full_period_gross": full_gross,
        "full_period_net": full_net,
        "oos_gross": oos_gross,
        "oos_net": oos_net,
        "per_asset_full_period": per_asset_table(records_by_asset, oos_only=False),
        "per_asset_oos": per_asset_table(records_by_asset, oos_only=True),
        "score_buckets": buckets,
        "bootstrap": bootstrap,
        "leverage_analysis": lev_summary,
        "promotion_gate": gate,
        "runtime_seconds": round(time.time() - t_start, 2),
        "trade_count": len(all_records),
        "governance": {
            "promotion_status": gate["status"],
            "live_execution_authorized": False,
            "deterministic_smc_is_production_authority": True,
        },
    }

    if verbose:
        print(f"\n  Total trades: {results['trade_count']}")
        print(f"  OOS selections: {oos_gross['n_selection']}")
        print(f"  Gate status: {gate['status']}")
        print(f"  Runtime: {results['runtime_seconds']}s")

    return results


def write_results(results: Dict[str, Any], repo_root: Optional[Path] = None) -> None:
    from quantedge.ai.evaluation.phase_i_reports import write_all_phase_i_reports

    root = repo_root or _get_repo_root()
    docs_dir = root / "docs" / "ai"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "phase_i_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    write_all_phase_i_reports(results, docs_dir)


def main() -> None:
    results = run_phase_i(verbose=True)
    write_results(results)
    print("\nReports written to docs/ai/")


if __name__ == "__main__":
    main()
