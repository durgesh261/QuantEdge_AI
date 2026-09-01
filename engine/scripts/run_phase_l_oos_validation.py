"""
Phase L chronological out-of-sample validation driver — RESEARCH ONLY.

Reproduces the pre-registered Phase L experiment against the frozen
`Ridge(alpha=1.0)` model and the frozen `+0.20 R` confirmation threshold, and
emits every artifact the promotion gate requires.

This script places no orders, cancels no orders, touches no execution module,
and cannot change `AI_PROMOTION_STATUS` or enable live execution.  It reads the
canonical candle CSVs and writes research artifacts only.

Usage:
    python scripts/run_phase_l_oos_validation.py [--out DIR] [--rebuild]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine" / "src"))

from quantedge.ai.evaluation import phase_l_research as PL  # noqa: E402
from quantedge.ai.evaluation.phase_i_ob_replay import (  # noqa: E402
    WARMUP_BARS,
    build_smc_context,
    extract_phase_i_setups,
)
from quantedge.ai.evaluation.phase_j_ob_dataset import (  # noqa: E402
    FEATURE_DIM,
    LABEL_REALIZED_R,
    OB_FEATURE_NAMES,
)
from quantedge.smc.structure import (  # noqa: E402
    StructureConfig,
    StructureDetector,
    StructureType,
)
from quantedge.smc.volatility import parse_candles_with_volatility  # noqa: E402

CANONICAL = REPO_ROOT / "data" / "canonical" / "delta_exchange_india"
DEFAULT_OUT = REPO_ROOT / "engine" / "data" / "research" / "phase_l_oos"

# The single feature whose source (`SMCContext.int_pivots + sw_pivots`) is
# filtered on the pivot's own bar index rather than on the bar at which the
# frozen `StructureDetector` first reports it.  Quantified in `audit_leakage`.
PIVOT_FEATURE = "dist_nearest_pivot_atr"


# ---------------------------------------------------------------------------
# Reproducibility primitives
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def code_revision() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:  # pragma: no cover - git always present here
        return "UNKNOWN"


def dataset_provenance() -> Dict[str, Any]:
    """Hashes every input CSV plus every module in the reproduction closure."""
    inputs = {}
    for sym in PL.SYMBOLS:
        csv_path = CANONICAL / sym / "1h" / "full_history.csv"
        with open(csv_path, encoding="utf-8") as fh:
            rows = fh.read().splitlines()
        inputs[sym] = {
            "path": str(csv_path.relative_to(REPO_ROOT).as_posix()),
            "sha256": sha256_file(csv_path),
            "candles": len(rows) - 1,
            "first_timestamp": rows[1].split(",")[0],
            "last_timestamp": rows[-1].split(",")[0],
        }
    closure = {}
    src = REPO_ROOT / "engine" / "src" / "quantedge"
    for rel in (
        "ai/evaluation/phase_l_research.py",
        "ai/evaluation/phase_j_ob_dataset.py",
        "ai/evaluation/phase_i_ob_replay.py",
        "ai/evaluation/smc_baseline.py",
        "ai/training/real_dataset_builder.py",
        "smc/structure.py", "smc/order_blocks.py", "smc/volatility.py",
        "strategy/engine.py", "strategy/models.py",
    ):
        closure[rel] = sha256_file(src / rel)[:16]
    return {"candle_inputs": inputs, "code_closure_sha256_16": closure}


# ---------------------------------------------------------------------------
# §13 Leakage audit — pivot knowability
# ---------------------------------------------------------------------------

def pivot_discovery_bars(symbol: str) -> Tuple[List[Tuple[int, int, float]], int, int]:
    """Replays the frozen detectors and records (pivot_index, discovery_bar, price).

    `build_smc_context` appends a `PivotPoint` carrying the pivot's *own* bar
    index at the loop bar where the detector first exposes it.  The frozen
    feature extractor then admits any pivot with `index <= decision_bar`, which
    is a weaker condition than `discovery_bar <= decision_bar`.  This function
    recovers the discovery bars so the gap can be measured rather than assumed.
    """
    candles = PL.load_canonical_full_history(CANONICAL, symbol)
    parsed = parse_candles_with_volatility(candles, atr_period=200, atr_multiplier=2.0)
    records: List[Tuple[int, int, float]] = []
    int_lag = sw_lag = 0
    for length, stype in ((5, StructureType.INTERNAL), (50, StructureType.SWING)):
        det = StructureDetector(StructureConfig(length, stype))
        prev_h = prev_l = None
        for i, pc in enumerate(parsed):
            det.process_candle(pc, i)
            ph, pl = det.state.pivot_high, det.state.pivot_low
            if ph is not None and ph.index != prev_h:
                records.append((ph.index, i, float(ph.price)))
                prev_h = ph.index
                if length == 5:
                    int_lag = max(int_lag, i - ph.index)
                else:
                    sw_lag = max(sw_lag, i - ph.index)
            if pl is not None and pl.index != prev_l:
                records.append((pl.index, i, float(pl.price)))
                prev_l = pl.index
                if length == 5:
                    int_lag = max(int_lag, i - pl.index)
                else:
                    sw_lag = max(sw_lag, i - pl.index)
    return records, int_lag, sw_lag


def audit_leakage(df: pd.DataFrame) -> Dict[str, Any]:
    """Recomputes `dist_nearest_pivot_atr` under the strict causal filter.

    Frozen rule:  admit pivots with `pivot.index <= decision_bar`.
    Causal rule:  admit pivots with `discovery_bar <= decision_bar`.

    Any row where the two disagree consumed information that did not exist at
    the decision bar.  The causal column is written alongside the frozen one so
    the fail-closed variant can be evaluated as a diagnostic without editing
    the frozen pipeline.
    """
    per_symbol_lags: Dict[str, Dict[str, int]] = {}
    causal_vals: Dict[str, float] = {}
    contaminated: Dict[str, bool] = {}

    for sym in PL.SYMBOLS:
        records, int_lag, sw_lag = pivot_discovery_bars(sym)
        per_symbol_lags[sym] = {
            "internal_pivot_lag_bars": int_lag,
            "swing_pivot_lag_bars": sw_lag,
            "pivots_total": len(records),
        }
        candles = PL.load_canonical_full_history(CANONICAL, sym)
        ctx = build_smc_context(candles)
        sub = df[df["asset"] == sym]
        idx_sorted = sorted(records, key=lambda r: r[1])  # by discovery bar
        for _, row in sub.iterrows():
            i = int(row["decision_bar"])
            close = float(candles[i].close)
            atr = (float(ctx.parsed[i].atr_value)
                   if ctx.parsed[i].atr_value else close * 0.01)
            known = [r for r in idx_sorted if r[1] <= i]
            if known:
                nearest = min(known, key=lambda r: abs(r[2] - close))
                causal = abs(nearest[2] - close) / atr if atr > 1e-12 else 0.0
            else:
                causal = 1.0
            causal_vals[row["setup_id"]] = round(float(causal), 6)
            contaminated[row["setup_id"]] = bool(
                abs(causal - float(row[PIVOT_FEATURE])) > 1e-9)

    df[PIVOT_FEATURE + "_causal"] = df["setup_id"].map(causal_vals)
    df["pivot_feature_contaminated"] = df["setup_id"].map(contaminated)
    n_bad = int(df["pivot_feature_contaminated"].sum())
    return {
        "pivot_lag_by_symbol": per_symbol_lags,
        "rows_total": int(len(df)),
        "rows_where_frozen_feature_used_unknowable_pivot": n_bad,
        "contamination_pct": round(100.0 * n_bad / max(1, len(df)), 2),
    }


# ---------------------------------------------------------------------------
# §5/§6 Paired incremental R and the paired moving-block bootstrap
# ---------------------------------------------------------------------------

def paired_mbb(
    r_baseline: np.ndarray,
    accepted: np.ndarray,
    n_boot: int = PL.BOOTSTRAP_N_CONFIRMATORY,
    seed: int = PL.RANDOM_SEED,
) -> Dict[str, Any]:
    """The frozen `_calc_paired_mbb_ci` estimator, instrumented.

    Blocks of contiguous chronological setups are resampled; the acceptance
    mask is indexed by the *same* bootstrap indices as the baseline R vector,
    so every resample compares the AI arm against the baseline arm on the same
    opportunities.  Identical arithmetic to `PhaseLResearchPipeline`; this
    wrapper additionally returns the distribution and the empty-resample count.
    """
    n = len(r_baseline)
    block = max(3, int(np.ceil(n ** (1.0 / 3.0))))
    num_blocks = int(np.ceil(n / block))
    max_start = max(1, n - block + 1)
    rng = np.random.default_rng(seed)

    inc = np.empty(n_boot)
    base = np.empty(n_boot)
    ai = np.empty(n_boot)
    empty = 0
    for b in range(n_boot):
        starts = rng.integers(0, max_start, size=num_blocks)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:n]
        boot_r = r_baseline[idx]
        boot_mask = accepted[idx]
        m_base = float(np.mean(boot_r))
        if np.any(boot_mask):
            m_ai = float(np.mean(boot_r[boot_mask]))
        else:
            m_ai = 0.0
            empty += 1
        base[b], ai[b], inc[b] = m_base, m_ai, m_ai - m_base

    pcts = [0.5, 2.5, 5.0, 25.0, 50.0, 75.0, 95.0, 97.5, 99.5]
    return {
        "n_resamples": n_boot,
        "seed": seed,
        "block_size": block,
        "blocks_per_resample": num_blocks,
        "resamples_with_zero_accepted": empty,
        "incremental_95ci": [round(float(np.percentile(inc, 2.5)), 4),
                             round(float(np.percentile(inc, 97.5)), 4)],
        "incremental_ci_lower_bound": round(float(np.percentile(inc, 2.5)), 4),
        "incremental_mean": round(float(np.mean(inc)), 4),
        "incremental_distribution_percentiles": {
            f"p{p}": round(float(np.percentile(inc, p)), 4) for p in pcts},
        "share_of_resamples_above_zero": round(
            float(np.mean(inc > 0.0)), 4),
        "baseline_95ci": [round(float(np.percentile(base, 2.5)), 4),
                          round(float(np.percentile(base, 97.5)), 4)],
        "ai_95ci": [round(float(np.percentile(ai, 2.5)), 4),
                    round(float(np.percentile(ai, 97.5)), 4)],
    }


# ---------------------------------------------------------------------------
# §10 Power / minimum detectable effect
# ---------------------------------------------------------------------------

def power_analysis(
    r_oos: np.ndarray,
    n_accepted: int,
    observed_effect: float,
    ci: Dict[str, Any],
) -> Dict[str, Any]:
    """Analytical MDE for the paired difference-of-means at alpha=0.05.

    The CI half-width the bootstrap actually produced is the honest measure of
    resolution, so the MDE is reported both analytically (from the observed R
    dispersion and coverage) and empirically (from the bootstrap half-width).
    """
    z = 1.959963984540054
    n_oos = len(r_oos)
    std = float(np.std(r_oos, ddof=1))
    cov = n_accepted / max(1, n_oos)
    lo, hi = ci["incremental_95ci"]
    half_width = (hi - lo) / 2.0

    mde_analytic = z * std * np.sqrt(1.0 / max(1, n_accepted))
    return {
        "total_eligible_oos_setups": n_oos,
        "accepted_setups": n_accepted,
        "acceptance_rate_pct": round(100.0 * cov, 2),
        "observed_incremental_effect_r": round(observed_effect, 4),
        "oos_realized_r_std": round(std, 4),
        "ci_width_r": round(hi - lo, 4),
        "ci_half_width_r": round(half_width, 4),
        "minimum_detectable_effect_analytic_r": round(float(mde_analytic), 4),
        "minimum_detectable_effect_empirical_r": round(half_width, 4),
        "observed_effect_exceeds_mde": bool(observed_effect > half_width),
        "accepted_setups_needed_for_observed_effect": (
            int(np.ceil((z * std / observed_effect) ** 2))
            if observed_effect > 0 else None),
        "oos_setups_needed_for_observed_effect": (
            int(np.ceil(((z * std / observed_effect) ** 2) / max(cov, 1e-9)))
            if observed_effect > 0 else None),
        "sample_adequate_for_observed_effect": bool(lo > 0.0),
    }


# ---------------------------------------------------------------------------
# §4 Primary experiment
# ---------------------------------------------------------------------------

def run_primary(df: pd.DataFrame, feature_names: Tuple[str, ...],
                label: str) -> Dict[str, Any]:
    """Fits the frozen model on train only and evaluates the OOS split once."""
    from sklearn.linear_model import Ridge

    split = PL.assign_phase_l_splits(df)
    train = split[split["split"] == "train"].reset_index(drop=True)
    oos = split[split["split"] == "oos"].reset_index(drop=True)

    model = Ridge(alpha=PL.FROZEN_ALPHA, random_state=PL.RANDOM_SEED)
    model.fit(train[list(feature_names)].values, train[LABEL_REALIZED_R].values)
    preds = model.predict(oos[list(feature_names)].values)
    accepted = preds >= PL.FROZEN_THRESHOLD

    r_base = oos[LABEL_REALIZED_R].values.astype(float)
    base_m = PL.compute_phase_l_metrics(oos, len(oos))
    ai_m = PL.compute_phase_l_metrics(oos[accepted], len(oos))
    rej_m = PL.compute_phase_l_metrics(oos[~accepted], len(oos))
    inc = ai_m["expectancy_r"] - base_m["expectancy_r"]
    ci = paired_mbb(r_base, accepted)

    per_asset = {}
    for sym in PL.SYMBOLS:
        m = (oos["asset"] == sym).values
        s_m = PL.compute_phase_l_metrics(oos[m], int(m.sum()))
        a_m = PL.compute_phase_l_metrics(oos[m & accepted], int(m.sum()))
        a_ci = (paired_mbb(r_base[m], accepted[m], n_boot=2000)
                if int(m.sum()) >= 30 else None)
        per_asset[sym] = {
            "oos_setups": s_m["n"],
            "accepted": a_m["n"],
            "acceptance_rate_pct": a_m["coverage_pct"],
            "baseline_expectancy_r": s_m["expectancy_r"],
            "ai_expectancy_r": a_m["expectancy_r"],
            "incremental_r": round(a_m["expectancy_r"] - s_m["expectancy_r"], 4),
            "baseline_profit_factor": s_m["profit_factor"],
            "ai_profit_factor": a_m["profit_factor"],
            "ai_win_rate_pct": a_m["win_rate_pct"],
            "incremental_95ci": a_ci["incremental_95ci"] if a_ci else None,
        }

    return {
        "label": label,
        "feature_count": len(feature_names),
        "train_setups": int(len(train)),
        "oos_setups": int(len(oos)),
        "train_window": [str(train["decision_time"].min()),
                         str(train["decision_time"].max())],
        "oos_window": [str(oos["decision_time"].min()),
                       str(oos["decision_time"].max())],
        "baseline": base_m,
        "ai_accepted": ai_m,
        "ai_rejected": rej_m,
        "incremental_r": round(inc, 4),
        "paired_mbb": ci,
        "primary_gate_passed": bool(ci["incremental_ci_lower_bound"] > 0.0),
        "per_asset": per_asset,
        "power": power_analysis(r_base, ai_m["n"], inc, ci),
        "_oos": oos,
        "_preds": preds,
        "_accepted": accepted,
    }


def write_ledger(res: Dict[str, Any], out: Path) -> str:
    """§4 per-setup record.  One row per chronological OOS opportunity."""
    oos, preds, accepted = res["_oos"], res["_preds"], res["_accepted"]
    baseline_expectancy = res["baseline"]["expectancy_r"]
    rows = []
    for k in range(len(oos)):
        r = float(oos[LABEL_REALIZED_R].iloc[k])
        acc = bool(accepted[k])
        rows.append({
            "setup_id": oos["setup_id"].iloc[k],
            "asset": oos["asset"].iloc[k],
            "timestamp": oos["decision_time"].iloc[k],
            "direction": oos["direction"].iloc[k],
            "manual_smc_outcome": oos["exit_reason"].iloc[k],
            "manual_smc_realized_r": round(r, 6),
            "ai_prediction_r": round(float(preds[k]), 6),
            "ai_decision": "ACCEPT" if acc else "REJECT",
            "ai_confirmed_realized_r": round(r, 6) if acc else "",
            "incremental_r_vs_baseline_mean": round(r - baseline_expectancy, 6) if acc else "",
        })
    ledger = pd.DataFrame(rows)
    path = out / "phase_l_oos_setup_ledger.csv"
    ledger.to_csv(path, index=False, lineterminator="\n")
    return sha256_file(path)


def loao(df: pd.DataFrame, feature_names: Tuple[str, ...]) -> List[Dict[str, Any]]:
    """§8 leave-one-asset-out.  Diagnostic only — see the report's caveat."""
    from sklearn.linear_model import Ridge
    rows = []
    for held in PL.SYMBOLS:
        tr = df[df["asset"] != held].reset_index(drop=True)
        te = df[df["asset"] == held].reset_index(drop=True)
        m = Ridge(alpha=PL.FROZEN_ALPHA, random_state=PL.RANDOM_SEED)
        m.fit(tr[list(feature_names)].values, tr[LABEL_REALIZED_R].values)
        acc = m.predict(te[list(feature_names)].values) >= PL.FROZEN_THRESHOLD
        s_m = PL.compute_phase_l_metrics(te, len(te))
        a_m = PL.compute_phase_l_metrics(te[acc], len(te))
        ci = paired_mbb(te[LABEL_REALIZED_R].values.astype(float), acc, n_boot=2000)
        rows.append({
            "held_out_asset": held,
            "train_setups": int(len(tr)),
            "held_out_setups": int(len(te)),
            "accepted": a_m["n"],
            "acceptance_rate_pct": a_m["coverage_pct"],
            "baseline_expectancy_r": s_m["expectancy_r"],
            "ai_expectancy_r": a_m["expectancy_r"],
            "incremental_r": round(a_m["expectancy_r"] - s_m["expectancy_r"], 4),
            "incremental_95ci": ci["incremental_95ci"],
            "ci_lower_bound_positive": bool(ci["incremental_ci_lower_bound"] > 0),
        })
    return rows


def walk_forward(df: pd.DataFrame,
                 feature_names: Tuple[str, ...]) -> List[Dict[str, Any]]:
    """§9 chronological walk-forward with the frozen config, expanding window."""
    from sklearn.linear_model import Ridge
    ordered = df.sort_values("decision_time").reset_index(drop=True)
    n = len(ordered)
    fold = n // 5
    out: List[Dict[str, Any]] = []
    for f in range(4):
        train_end = (f + 2) * fold
        test_start = train_end + 5          # embargo gap, frozen
        test_end = min(n, test_start + fold)
        if test_end <= test_start or train_end >= n:
            break
        tr = ordered.iloc[:train_end]
        te = ordered.iloc[test_start:test_end]
        if len(tr) < 50 or len(te) < 20:
            continue
        m = Ridge(alpha=PL.FROZEN_ALPHA, random_state=PL.RANDOM_SEED)
        m.fit(tr[list(feature_names)].values, tr[LABEL_REALIZED_R].values)
        acc = m.predict(te[list(feature_names)].values) >= PL.FROZEN_THRESHOLD
        s_m = PL.compute_phase_l_metrics(te, len(te))
        a_m = PL.compute_phase_l_metrics(te[acc], len(te))
        out.append({
            "fold": f + 1,
            "training_period": [str(tr["decision_time"].min()),
                                str(tr["decision_time"].max())],
            "oos_period": [str(te["decision_time"].min()),
                           str(te["decision_time"].max())],
            "training_setups": int(len(tr)),
            "oos_setups": int(len(te)),
            "accepted": a_m["n"],
            "acceptance_rate_pct": a_m["coverage_pct"],
            "baseline_expectancy_r": s_m["expectancy_r"],
            "ai_expectancy_r": a_m["expectancy_r"],
            "incremental_r": round(a_m["expectancy_r"] - s_m["expectancy_r"], 4),
        })
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_or_load(out: Path, rebuild: bool) -> pd.DataFrame:
    cache = out / "phase_l_dataset.csv"
    if cache.exists() and not rebuild:
        return pd.read_csv(cache)
    df = PL.build_phase_l_dataset(CANONICAL)
    df.to_csv(cache, index=False, lineterminator="\n")
    return pd.read_csv(cache)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    df = build_or_load(out, args.rebuild)
    dataset_hash = sha256_file(out / "phase_l_dataset.csv")

    leak = audit_leakage(df)
    primary = run_primary(df, OB_FEATURE_NAMES, "frozen_29_feature_contract")
    ledger_hash = write_ledger(primary, out)

    # §13 fail-closed diagnostic: the same frozen model family, alpha, threshold
    # and split with the one non-causal feature removed.  Not a retune.
    causal_names = tuple(f for f in OB_FEATURE_NAMES if f != PIVOT_FEATURE)
    failclosed = run_primary(df, causal_names, "fail_closed_28_feature_contract")

    report: Dict[str, Any] = {
        "frozen_configuration": {
            "model": f"{PL.FROZEN_MODEL_NAME}(alpha={PL.FROZEN_ALPHA})",
            "confirmation_threshold_r": PL.FROZEN_THRESHOLD,
            "feature_contract": "phase-j-ob-causal-v1",
            "feature_count": FEATURE_DIM,
            "train_end_utc": PL.TRAIN_END_UTC,
            "embargo_hours": PL.EMBARGO_HOURS,
            "oos_start_utc": PL.OOS_START_UTC,
            "oos_end_utc": PL.OOS_END_UTC,
            "bootstrap_resamples": PL.BOOTSTRAP_N_CONFIRMATORY,
            "random_seed": PL.RANDOM_SEED,
            "warmup_bars": WARMUP_BARS,
        },
        "reproducibility": {
            "dataset_sha256": dataset_hash,
            "setup_ledger_sha256": ledger_hash,
            "code_revision": code_revision(),
            **dataset_provenance(),
        },
        "leakage_audit": leak,
        "primary": {k: v for k, v in primary.items() if not k.startswith("_")},
        "fail_closed_diagnostic": {
            k: v for k, v in failclosed.items() if not k.startswith("_")},
        "loao": loao(df, OB_FEATURE_NAMES),
        "walk_forward": walk_forward(df, OB_FEATURE_NAMES),
    }
    body = json.dumps(report, indent=2, sort_keys=True, default=str)
    report["result_sha256"] = sha256_text(body)
    (out / "phase_l_oos_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str),
        encoding="utf-8")

    p = report["primary"]
    print(f"dataset_sha256          {dataset_hash[:32]}")
    print(f"result_sha256           {report['result_sha256'][:32]}")
    print(f"total setups            {len(df)}")
    print(f"train / oos             {p['train_setups']} / {p['oos_setups']}")
    print(f"oos window              {p['oos_window'][0]} -> {p['oos_window'][1]}")
    print(f"accepted                {p['ai_accepted']['n']} "
          f"({p['ai_accepted']['coverage_pct']}%)")
    print(f"baseline E[R]           {p['baseline']['expectancy_r']:+.4f}")
    print(f"ai E[R]                 {p['ai_accepted']['expectancy_r']:+.4f}")
    print(f"incremental R           {p['incremental_r']:+.4f}")
    print(f"paired MBB 95% CI       [{p['paired_mbb']['incremental_95ci'][0]:+.4f}, "
          f"{p['paired_mbb']['incremental_95ci'][1]:+.4f}]")
    print(f"PRIMARY GATE            "
          f"{'PASS' if p['primary_gate_passed'] else 'FAIL'}")
    print(f"pivot contamination     "
          f"{leak['rows_where_frozen_feature_used_unknowable_pivot']}"
          f"/{leak['rows_total']} ({leak['contamination_pct']}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
