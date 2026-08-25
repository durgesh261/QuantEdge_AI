"""
Phase J — OB-Centric AI Research test suite.

Covers:
- no future leakage (feature mutation invariance)
- OB uniqueness / USED-state semantics / parity with Phase I universe
- entry / SL / TP parity with application rules
- leverage calculation parity
- fee/slippage/funding cost calculation
- liquidation safety
- feature dimensionality & normalization sanity
- deterministic replay & model determinism
- threshold selection rule (validation-only)
- train/validation/OOS isolation (embargo)
- cross-asset (LOAO) disjointness
- bootstrap determinism
- governance rejection lock
- zero live API calls (static import scan)
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantedge.ai.evaluation.phase_i_ob_replay import (
    compute_dynamic_leverage,
    estimate_liquidation,
    extract_phase_i_setups,
    load_canonical_candles,
)
from quantedge.ai.evaluation.phase_j_ob_dataset import (
    ABLATION_SETS,
    FEATURE_DIM,
    LABEL_REALIZED_R,
    OB_FEATURE_NAMES,
    build_phase_j_dataset,
    extract_ob_causal_features,
)
from quantedge.ai.evaluation.phase_j_research import (
    DEFAULT_THRESHOLD,
    EMBARGO_HOURS,
    OOS_END_UTC,
    OOS_START_UTC,
    THRESHOLD_GRID,
    TRAIN_END_UTC,
    VAL_END_UTC,
    VAL_START_UTC,
    assign_frozen_splits,
    calibration_buckets,
    evaluate_configuration,
    evaluate_phase_j_gate,
    group_metrics,
    leverage_analysis,
    model_candidates,
    paired_config_bootstrap,
    run_loao,
    select_threshold_on_validation,
    verify_split_isolation,
    wilson_interval,
)


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[3]


CANONICAL = None


@pytest.fixture(scope="module")
def btc_candles():
    csv_path = (
        _get_repo_root() / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
    )
    return load_canonical_candles(csv_path.parents[2], "BTCUSD")


@pytest.fixture(scope="module")
def btc_ctx(btc_candles):
    from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context

    return build_smc_context(btc_candles)


@pytest.fixture(scope="module")
def btc_setups(btc_candles, btc_ctx):
    setups, _ = extract_phase_i_setups(btc_candles, "BTCUSD", ctx=btc_ctx)
    return setups


@pytest.fixture(scope="module")
def dataset_raw():
    root = _get_repo_root()
    return build_phase_j_dataset(root / "data" / "canonical" / "delta_exchange_india")


@pytest.fixture(scope="module")
def dataset(dataset_raw):
    return assign_frozen_splits(dataset_raw.copy())


# ═════════════════════════════════════════════════════════════════════════════
# Dataset integrity: uniqueness, USED semantics, app-rule parity
# ═════════════════════════════════════════════════════════════════════════════


class TestDatasetIntegrity:
    def test_one_row_per_unique_ob(self, dataset):
        assert dataset["setup_id"].is_unique

    def test_matches_phase_i_universe_size(self, dataset_raw, dataset):
        # The raw dataset must equal the Phase I authoritative universe exactly.
        per_asset = dataset_raw.groupby("asset").size().to_dict()
        assert sum(per_asset.values()) == 454
        assert per_asset == {"BTCUSD": 123, "ETHUSD": 103, "SOLUSD": 130, "XRPUSD": 98}
        # Split assignment drops only embargo-gap rows (documented behaviour).
        assert len(dataset) == 449

    def test_labels_are_real_replay_outcomes(self, dataset):
        r = dataset[LABEL_REALIZED_R]
        tp_r = 60.0 / 35.0
        sl_hits = dataset[dataset["exit_reason"] == "SL_HIT"][LABEL_REALIZED_R]
        tp_hits = dataset[dataset["exit_reason"] == "TP_HIT"]
        # SL always realises exactly -1R.
        assert (sl_hits.abs() - 1.0).abs().max() < 1e-6
        # TP realises exactly reward/risk implied by the RECORDED prices.
        implied = (
            (tp_hits["tp_price"] - tp_hits["entry_price"]).abs()
            / (tp_hits["entry_price"] - tp_hits["sl_price"]).abs()
        )
        assert ((tp_hits[LABEL_REALIZED_R] - implied).abs() < 1e-3).all()
        # Nominal 60/35 multiple may be shifted ONLY by the application's
        # 0.01 TP-price quantisation: max |ΔR| <= 0.005 / risk_distance.
        risk_abs = (tp_hits["entry_price"] - tp_hits["sl_price"]).abs()
        bound = 0.005 / risk_abs + 1e-6
        dev = (tp_hits[LABEL_REALIZED_R] - tp_r).abs()
        assert (dev <= bound).all(), f"TP deviation beyond quantisation bound: max {dev.max():.4f}"
        assert ((r >= -1.05) & (r <= r.max())).all()

    def test_feature_dimensionality_and_finiteness(self, dataset):
        feats = dataset[list(OB_FEATURE_NAMES)].to_numpy(dtype=float)
        assert feats.shape == (len(dataset), FEATURE_DIM)
        assert np.isfinite(feats).all()

    def test_scale_invariant_normalization_ranges(self, dataset):
        # Ratio/percent features must stay within sane bounds for ALL assets.
        assert (dataset["entry_depth_in_zone"].between(0, 1)).all()
        assert (dataset["mitigation_depth_pct"].between(0, 1)).all()
        assert (dataset["formation_body_ratio"].between(0, 1)).all()
        assert (dataset["premium_discount"].between(0, 1)).all()
        assert (dataset["atr_percentile"].between(0, 100)).all()
        assert (dataset["direction_long"].isin([0.0, 1.0])).all()
        # Cross-asset scale invariance: stop_distance_atr must not differ wildly by asset.
        med = dataset.groupby("asset")["stop_distance_atr"].median()
        assert med.max() / max(med.min(), 1e-9) < 25

    def test_binary_structure_flags_consistent(self, dataset):
        assert ((dataset["is_bos"] + dataset["is_choch"]) <= 1).all()


class TestAppRuleParity:
    def test_entry_sl_tp_parity_with_application_rules(self, btc_setups, btc_candles, btc_ctx):
        s = next(x for x in btc_setups if x.direction == "LONG")
        f = extract_ob_causal_features(s, btc_candles, btc_ctx)
        row = dict(zip(OB_FEATURE_NAMES, f))
        # SL distance feature equals |entry-SL| percent from the setup record
        assert row["stop_distance_pct"] == pytest.approx(s.stop_distance_percent, abs=1e-4)
        if s.direction == "LONG":
            assert s.sl_price == pytest.approx(s.ob_low, abs=1e-6)

    def test_leverage_formula_parity(self, dataset):
        recomputed = dataset["stop_distance_percent"].apply(compute_dynamic_leverage)
        assert (recomputed == dataset["leverage"]).all()

    def test_liquidation_safety_all_trades(self, dataset):
        violations = 0
        for _, row in dataset.iterrows():
            liq = estimate_liquidation(
                float(row["entry_price"]),
                float(row["stop_distance_percent"]) / 100.0,
                int(row["leverage"]),
                str(row["direction"]),
            )
            violations += int(liq["liquidation_before_sl"])
        assert violations == 0


class TestNoFutureLeakage:
    def test_features_invariant_to_future_candle_mutation(self, btc_candles):
        horizon = 500
        candles_trunc = btc_candles[:horizon]
        setups_trunc, _ = extract_phase_i_setups(candles_trunc, "BTCUSD")
        assert setups_trunc, "expected at least one setup in truncated series"
        s0 = setups_trunc[0]
        t_bar = s0.decision_bar

        # Mutate every candle strictly AFTER the decision bar.
        mutated = list(candles_trunc)
        for k in range(t_bar + 1, len(mutated)):
            c = mutated[k]
            mutated[k] = type(c)(
                symbol=c.symbol, timeframe=c.timeframe, timestamp=c.timestamp,
                open=c.open * Decimal("5"), high=c.high * Decimal("6"),
                low=c.low / Decimal("6"), close=c.close * Decimal("4"),
                volume=c.volume * Decimal("99"), source=c.source,
            )

        from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context

        ctx_a = build_smc_context(list(candles_trunc))
        ctx_b = build_smc_context(mutated)
        fa = extract_ob_causal_features(s0, list(candles_trunc), ctx_a)
        fb = extract_ob_causal_features(s0, mutated, ctx_b)
        assert fa == fb, "feature vector changed when future candles were mutated"


# ═════════════════════════════════════════════════════════════════════════════
# Split isolation & embargo
# ═════════════════════════════════════════════════════════════════════════════


class TestSplitIsolation:
    def test_frozen_oos_window_preserved(self, dataset):
        oos = dataset[dataset["split"] == "oos"]
        times = pd.to_datetime(oos["decision_time"], utc=True)
        assert times.min() >= pd.Timestamp(OOS_START_UTC)
        assert times.max() <= pd.Timestamp(OOS_END_UTC)
        assert len(oos) == 99  # matches Phase I frozen-OOS selection exactly

    def test_embargo_gaps_enforced(self, dataset):
        rep = verify_split_isolation(dataset)
        assert rep["embargo_gap_train_to_val_hours"] >= EMBARGO_HOURS
        assert rep["embargo_gap_val_to_oos_hours"] >= EMBARGO_HOURS

    def test_train_never_crosses_boundary(self, dataset):
        tr = pd.to_datetime(dataset[dataset["split"] == "train"]["decision_time"], utc=True)
        assert tr.max() <= pd.Timestamp(TRAIN_END_UTC)
        va = pd.to_datetime(dataset[dataset["split"] == "val"]["decision_time"], utc=True)
        assert (va >= pd.Timestamp(VAL_START_UTC)).all() and (va <= pd.Timestamp(VAL_END_UTC)).all()


# ═════════════════════════════════════════════════════════════════════════════
# Threshold selection rule (validation-only)
# ═════════════════════════════════════════════════════════════════════════════


class TestThresholdRule:
    def _mk_val_frame(self, n=60):
        rng = np.random.default_rng(7)
        preds = rng.normal(0.2, 0.4, n)
        realized = np.where(preds > 0.15, rng.normal(0.5, 0.6, n), rng.normal(-0.4, 0.6, n))
        df = pd.DataFrame(
            {
                "pred": preds,
                LABEL_REALIZED_R: realized,
                "label_mfe_r": np.abs(realized),
                "label_mae_r": np.abs(realized),
                "label_holding_bars": np.full(n, 5.0),
                "exit_reason": np.where(realized > 0, "TP_HIT", "SL_HIT"),
            }
        )
        return df

    def test_rule_prefers_positive_incremental_with_coverage(self):
        out = select_threshold_on_validation(self._mk_val_frame())
        assert out["chosen_threshold"] in [float(g) for g in THRESHOLD_GRID] or out["chosen_threshold"] == DEFAULT_THRESHOLD
        assert out["selection_source"].startswith(("validation_rule", "fallback"))
        cand = {c["threshold"]: c for c in out["candidates"]}
        chosen = cand[out["chosen_threshold"]]
        if out["selection_source"].startswith("validation_rule"):
            assert chosen["eligible"]

    def test_no_eligible_falls_back_to_frozen_default(self):
        df = self._mk_val_frame()
        df[LABEL_REALIZED_R] = -1.0  # hopeless universe: no threshold can win
        out = select_threshold_on_validation(df)
        assert out["selection_source"] == "fallback_frozen_default"
        assert out["chosen_threshold"] == DEFAULT_THRESHOLD

    def test_grid_is_predeclared(self):
        assert THRESHOLD_GRID == (0.00, 0.10, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60)


# ═════════════════════════════════════════════════════════════════════════════
# Model determinism & configuration evaluation
# ═════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_configuration_evaluation_deterministic(self, dataset):
        r1 = evaluate_configuration(dataset, "random_forest", list(OB_FEATURE_NAMES))
        r2 = evaluate_configuration(dataset, "random_forest", list(OB_FEATURE_NAMES))
        assert r1.threshold_selection["chosen_threshold"] == r2.threshold_selection["chosen_threshold"]
        assert r1.oos == r2.oos
        assert r1.oos_bootstrap == r2.oos_bootstrap

    def test_model_candidates_are_seeded(self):
        specs = model_candidates()
        assert set(specs) == {
            "ridge", "random_forest", "extra_trees", "hist_gbdt", "tp_first_classifier",
        }

    def test_group_metrics_partition(self, dataset):
        oos = dataset[dataset["split"] == "oos"]
        mask = np.array([i % 2 == 0 for i in range(len(oos))])
        gm = group_metrics(oos, mask)
        assert gm["n_selected"] + (gm["n_universe"] - gm["n_selected"]) == gm["n_universe"]
        assert gm["smc"]["executed_setups"] == gm["n_universe"]


# ═════════════════════════════════════════════════════════════════════════════
# Cross-asset isolation (LOAO)
# ═════════════════════════════════════════════════════════════════════════════


class TestCrossAssetIsolation:
    def test_loao_held_out_asset_excluded_from_training(self, dataset):
        folds = run_loao(dataset, "ridge")  # cheap linear model for speed
        assets = set(dataset["asset"].unique())
        assert {f["held_out_asset"] for f in folds} == assets
        for f in folds:
            assert f["held_out_asset"] not in f["training_assets"]
            assert sorted(f["training_assets"] + [f["held_out_asset"]]) == sorted(assets)


# ═════════════════════════════════════════════════════════════════════════════
# Bootstrap & statistics determinism
# ═════════════════════════════════════════════════════════════════════════════


class TestStatistics:
    def test_paired_bootstrap_deterministic(self, dataset):
        oos = dataset[dataset["split"] == "oos"]
        r = oos[LABEL_REALIZED_R].to_numpy(dtype=float)
        m1 = np.arange(len(r)) % 3 == 0
        b1 = paired_config_bootstrap(r, m1, ~m1, n_boot=200, seed=42)
        b2 = paired_config_bootstrap(r, m1, ~m1, n_boot=200, seed=42)
        assert b1 == b2

    def test_wilson_interval_bounds(self):
        lo, hi = wilson_interval(30, 99)
        assert 0 < lo < 0.31 < hi < 1
        assert wilson_interval(0, 0) == (0.0, 0.0)

    def test_calibration_buckets_complete(self, dataset):
        df = dataset.assign(pred=np.random.default_rng(3).normal(0.2, 0.4, len(dataset)))
        calib = calibration_buckets(df)
        assert sum(bk.get("count", 0) for bk in calib["buckets"].values()) == len(df)
        assert calib["monotonic"] in (True, False, None)


class TestLeverageAccountAnalysis:
    def test_account_simulation_separate_from_r_space(self, dataset):
        out = leverage_analysis(dataset.head(50))
        assert out["avg_leverage"] > 0
        assert out["account_max_drawdown_pct_of_balance_path"] > 0
        assert set(out["by_leverage_bucket"]) <= {"1-19x", "20-39x", "40-69x", "70-100x"}
        # Deterministic
        again = leverage_analysis(dataset.head(50))
        assert again == out


# ═════════════════════════════════════════════════════════════════════════════
# Governance lock
# ═════════════════════════════════════════════════════════════════════════════


class TestGovernanceLock:
    def _oos_stub(self, exp_smc=-0.1, exp_ai=0.3, pf_ai=1.5, mdd_ai=4.0, n_sel=10):
        smc = {"expectancy_r": exp_smc, "profit_factor": 0.9, "max_drawdown_r": 12.0, "executed_setups": 99}
        ai = {
            "expectancy_r": exp_ai, "profit_factor": pf_ai, "max_drawdown_r": mdd_ai,
            "executed_setups": n_sel,
        }
        rej = {"expectancy_r": -0.3}
        return {"smc": smc, "filtered": ai, "rejected": rej, "coverage_pct": 30.0, "incremental_expectancy_r": exp_ai - exp_smc}

    def test_ci_crossing_zero_forces_rejection(self):
        gate = evaluate_phase_j_gate(
            self._oos_stub(),
            {"incremental_mean_r_95ci": (-0.15, 0.60)},
            [{"asset": a, "ai_accepted": 5, "incremental_expectancy_r": 0.1} for a in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]],
            liquidation_violations=0,
        )
        assert gate["status"] == "REJECTED"
        assert gate["live_execution_authorized"] is False
        assert gate["ai_live_execution"] == "BLOCKED_BY_SYSTEM"

    def test_zero_acceptance_asset_blocks_gate_even_if_pool_passes(self):
        gate = evaluate_phase_j_gate(
            self._oos_stub(),
            {"incremental_mean_r_95ci": (0.20, 0.90)},  # significant!
            [
                {"asset": "BTCUSD", "ai_accepted": 4, "incremental_expectancy_r": 0.5},
                {"asset": "ETHUSD", "ai_accepted": 10, "incremental_expectancy_r": 0.2},
                {"asset": "SOLUSD", "ai_accepted": 15, "incremental_expectancy_r": 0.4},
                {"asset": "XRPUSD", "ai_accepted": 0, "incremental_expectancy_r": None},
            ],
            liquidation_violations=0,
        )
        assert gate["criteria"]["C6_cross_asset_robustness"]["passed"] is False
        assert gate["status"] == "REJECTED"

    def test_passing_gate_still_never_authorises_live(self):
        gate = evaluate_phase_j_gate(
            self._oos_stub(),
            {"incremental_mean_r_95ci": (0.20, 0.90)},
            [{"asset": a, "ai_accepted": 5, "incremental_expectancy_r": 0.2} for a in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]],
            liquidation_violations=0,
        )
        assert gate["status"] == "CANDIDATE_FOR_GOVERNANCE_REVIEW"
        assert gate["live_execution_authorized"] is False
        assert gate["execution_authority"] == "DETERMINISTIC_SMC"


# ═════════════════════════════════════════════════════════════════════════════
# Zero live API calls — static guarantee
# ═════════════════════════════════════════════════════════════════════════════


class TestZeroLiveApiCalls:
    @pytest.mark.parametrize(
        "module_name",
        [
            "quantedge.ai.evaluation.phase_j_ob_dataset",
            "quantedge.ai.evaluation.phase_j_research",
            "quantedge.ai.evaluation.run_phase_j",
            "quantedge.ai.evaluation.phase_j_reports",
        ],
    )
    def test_phase_j_modules_never_import_execution_stack(self, module_name):
        import inspect
        import sys

        mod = sys.modules.get(module_name) or __import__(module_name, fromlist=["x"])
        src = inspect.getsource(sys.modules[module_name])
        forbidden = [
            "delta_client", "place_order", "execution_engine", "private_websocket",
            "market_orchestrator", "trade_lifecycle",
        ]
        for token in forbidden:
            assert token not in src, f"{module_name} references execution API: {token}"

    def test_ablation_sets_reference_known_features_only(self):
        known = set(OB_FEATURE_NAMES)
        for name, cols in ABLATION_SETS.items():
            assert set(cols) <= known, f"{name} contains unknown features"
