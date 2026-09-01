"""
Phase L chronological OOS validation — additive research guards (§35/§36).

These tests are ADDITIVE.  They neither modify nor weaken any existing test and
they assert nothing about production trading behaviour beyond the fail-closed
governance constants that already exist.

They pin four findings from the Phase L reproduction so that a future refactor
cannot silently invalidate the recorded REJECT decision:

1.  Pivot discovery lag.  `StructureDetector` exposes a `PivotPoint` exactly
    `length` bars after the bar the pivot's own `.index` refers to.  The frozen
    Phase J feature extractor filters pivots with `p.index <= decision_bar`,
    which is strictly weaker than `discovery_bar <= decision_bar`; feature
    `dist_nearest_pivot_atr` therefore consumes future information.
2.  The frozen feature contract still carries that non-causal feature, and the
    frozen filter is still the weaker one.  If either changes, the recorded
    result no longer describes the code and must be re-derived.
3.  Nothing under `quantedge.ai` reaches order placement, cancellation, sizing
    authority, leverage authority or an execution gate.
4.  The governance constants remain fail-closed:
    `AI_PROMOTION_STATUS == "REJECTED"`, `live_execution_authorized is False`,
    `execution_status == "BLOCKED_BY_SYSTEM"`, `LIVE_EXECUTION_AUTHORIZED is
    False`.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import quantedge
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.models import StructureType
from quantedge.smc.structure import StructureConfig, StructureDetector
from quantedge.smc.volatility import parse_candles_with_volatility

SRC_ROOT = pathlib.Path(quantedge.__file__).parent
AI_ROOT = SRC_ROOT / "ai"

INTERNAL_LENGTH = 5
SWING_LENGTH = 50


# ═════════════════════════════════════════════════════════════════════════════
# Synthetic zigzag — deterministic, no canonical data dependency
# ═════════════════════════════════════════════════════════════════════════════


def _zigzag_candles(n_bars: int = 900, period: int = 140, amp: float = 900.0):
    """Builds a deterministic triangular zigzag that forms both pivot classes.

    The period is comfortably larger than 2 * SWING_LENGTH so swing pivots can
    actually confirm; amplitude is large relative to the bar range so no bar
    trips the ATR volatility inversion.
    """
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles: list[Candle] = []
    half = period / 2.0
    for i in range(n_bars):
        phase = i % period
        ramp = phase / half if phase < half else (period - phase) / half
        mid = 30_000.0 + amp * ramp
        candles.append(
            Candle(
                symbol="TESTUSD",
                timeframe=Timeframe.H1,
                timestamp=base + timedelta(hours=i),
                open=Decimal(str(round(mid, 2))),
                high=Decimal(str(round(mid + 5.0, 2))),
                low=Decimal(str(round(mid - 5.0, 2))),
                close=Decimal(str(round(mid, 2))),
                volume=Decimal("100"),
            )
        )
    return candles


def _discovery_lags(length: int, stype: StructureType) -> list[int]:
    """Replays the detector and returns `discovery_bar - pivot.index` per pivot."""
    parsed = parse_candles_with_volatility(
        _zigzag_candles(), atr_period=200, atr_multiplier=2.0
    )
    detector = StructureDetector(StructureConfig(length, stype))
    lags: list[int] = []
    prev_high = prev_low = None
    for i, pc in enumerate(parsed):
        detector.process_candle(pc, i)
        ph = detector.state.pivot_high
        pl = detector.state.pivot_low
        if ph is not None and ph.index != prev_high:
            lags.append(i - ph.index)
            prev_high = ph.index
        if pl is not None and pl.index != prev_low:
            lags.append(i - pl.index)
            prev_low = pl.index
    return lags


# ═════════════════════════════════════════════════════════════════════════════
# 1. Pivot discovery lag is exactly `length` bars — never zero
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "length,stype",
    [
        (INTERNAL_LENGTH, StructureType.INTERNAL),
        (SWING_LENGTH, StructureType.SWING),
    ],
)
def test_pivot_discovery_lag_equals_detector_length(length, stype):
    lags = _discovery_lags(length, stype)
    assert lags, f"zigzag produced no {stype} pivots; fixture no longer exercises the detector"
    assert set(lags) == {length}, (
        f"{stype} pivot discovery lag must be exactly {length} bars, saw {sorted(set(lags))}"
    )
    assert 0 not in lags, "a pivot must never be knowable on its own bar"


def test_pivot_index_arithmetic_is_length_behind_current_bar():
    """Pins the `size_idx = candle_count - 1 - length` arithmetic itself."""
    source = inspect.getsource(StructureDetector.process_candle)
    assert "self._candle_count - 1 - self.length" in source, (
        "the pivot index offset arithmetic changed; re-derive the Phase L leakage audit"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. The frozen Phase J contract still carries the non-causal feature
# ═════════════════════════════════════════════════════════════════════════════

pytest.importorskip("sklearn", reason="Phase L research closure requires scikit-learn")


def test_frozen_phase_j_contract_still_contains_leaking_pivot_feature():
    from quantedge.ai.evaluation.phase_j_ob_dataset import (
        FEATURE_DIM,
        OB_FEATURE_NAMES,
        extract_ob_causal_features,
    )

    assert FEATURE_DIM == 29 == len(OB_FEATURE_NAMES)
    assert "dist_nearest_pivot_atr" in OB_FEATURE_NAMES, (
        "the leaking feature was removed; the recorded Phase L REJECT decision "
        "describes a contract that no longer exists and must be re-derived"
    )
    source = inspect.getsource(extract_ob_causal_features)
    assert "if p.index <= i" in source, (
        "the pivot filter changed; if it now filters on discovery bar the "
        "leakage finding must be re-measured before any promotion decision"
    )


def test_frozen_pre_registration_constants_are_untouched():
    from quantedge.ai.evaluation import phase_l_research as PL

    assert PL.FROZEN_ALPHA == 1.0
    assert PL.FROZEN_THRESHOLD == 0.20
    assert PL.RANDOM_SEED == 42
    assert PL.BOOTSTRAP_N_CONFIRMATORY == 10_000
    assert PL.COVERAGE_FLOOR_PCT == 15.0
    assert PL.EMBARGO_HOURS == 72.0
    assert PL.TRAIN_END_UTC == "2025-06-30T18:00:00+00:00"
    assert PL.SYMBOLS == ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")


# ═════════════════════════════════════════════════════════════════════════════
# 3. The research layer holds no execution authority
# ═════════════════════════════════════════════════════════════════════════════


def _ai_modules() -> list[pathlib.Path]:
    return [p for p in sorted(AI_ROOT.rglob("*.py")) if "__pycache__" not in p.parts]


def test_research_layer_has_no_execution_authority():
    forbidden = (
        "place" + "_order",
        "create" + "_order",
        "cancel" + "_order",
        "OrderValidation" + "Gateway",
        "Capital" + "Allocator",
        "TradeLifecycle" + "Manager",
        "SingleTradeLock" + "Manager",
        "kill" + "_switch",
        "algo" + "_enabled",
    )
    offenders: list[str] = []
    modules = _ai_modules()
    assert modules, "no modules found under quantedge.ai"
    for path in modules:
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{needle}")
    assert not offenders, f"research layer references execution authority: {offenders}"


def test_research_layer_does_not_import_execution_or_runtime():
    offenders: list[str] = []
    for path in _ai_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith(("quantedge.execution", "quantedge.runtime")):
                    offenders.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}:{name}")
    assert not offenders, f"research layer imports execution/runtime: {offenders}"


def test_production_does_not_import_the_research_layer():
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or "ai" in path.relative_to(SRC_ROOT).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith("quantedge.ai"):
                    offenders.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}:{name}")
    assert not offenders, f"production imports the research layer: {offenders}"


# ═════════════════════════════════════════════════════════════════════════════
# 4. Governance remains fail-closed
# ═════════════════════════════════════════════════════════════════════════════


def test_governance_constants_remain_fail_closed():
    from quantedge.ai.research import displacement_gated_retest_engine as gate
    from quantedge.strategy.manual_smc import backtest as bt

    assert gate.live_execution_authorized is False
    assert gate.AI_PROMOTION_STATUS == "REJECTED"
    assert gate.execution_status == "BLOCKED_BY_SYSTEM"
    assert bt.LIVE_EXECUTION_AUTHORIZED is False
