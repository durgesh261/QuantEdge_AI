"""
Manual SMC — Step 8: the backtest driver.
=========================================

Proves that `quantedge.strategy.manual_smc.backtest` is a THIN driver over the
already-extracted production modules, and that driving it reproduces the
authoritative Manual SMC behaviour end to end.

What this file asserts, by Step 8 section:

  §2  `backtest.py` re-implements NO strategy rule. Proven by AST over the
      module source, not by reading the docstring: no lifecycle method, no BOS
      scan, no geometry, no lock rule, no direction branch, and exactly ONE
      call site for `evaluate_closed_candle`.
  §3  The global single-trade lock is `active_trade is not None`, portfolio
      wide, cross asset, and NOT a timestamp cooldown.
  §4  Manual SMC semantics survive the round trip: origin/OB geometry,
      probe -> pullback displacement, 25% entry, absolute ±0.60% TP, distal SL.
  §5  The golden BTC trade goes through `backtest.py -> strategy.py -> shared
      lifecycle/scanner/geometry`, against the real canonical CSV.
  §7  The LONG mirror runs end to end on the SAME shared code.
  §8  Multi-asset lock regression, including same-timestamp head-to-head and
      deterministic ordering.
  §9  Break+1, Displacement+1, and no entry/exit on one bar.
  §10 The seven forensic edge cases.
  §11 Quantization is REPORTING ONLY: identical trades with and without tick
      specs, so the behavioural baseline and the executable baseline stay
      distinguishable.
  §13 Importing the driver pulls in no adapter, no execution, no runtime.

Nothing here authorises execution: `LIVE_EXECUTION_AUTHORIZED` is asserted
False, and the driver refuses to construct if that is ever flipped.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quantedge.strategy.manual_smc import backtest as bt
from quantedge.strategy.manual_smc.backtest import (
    DEFAULT_SYMBOLS,
    LIVE_EXECUTION_AUTHORIZED,
    Aggregate,
    BacktestDataError,
    BacktestGovernanceError,
    BacktestResult,
    BacktestTrade,
    Candle,
    EntryBlock,
    ManualSMCBacktest,
    TimelineRow,
    aggregate,
    build_timeline,
    candles_from_ohlc,
    canonical_csv_path,
    default_canonical_base,
    find_repo_root,
    load_canonical_candles,
    load_canonical_dataset,
    run_manual_smc_backtest,
    run_manual_smc_backtest_from_candles,
)
from quantedge.strategy.manual_smc.lifecycle import (
    ACTIVATION_MODE_ORACLE_C,
    ManualLifecycleEventType as ET,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBState,
    ManualSpecConfig,
)

MODULE_PATH = Path(bt.__file__)
MODULE_SRC = MODULE_PATH.read_text(encoding="utf-8")
MODULE_AST = ast.parse(MODULE_SRC)
PACKAGE = MODULE_PATH.parent

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ts(bar_idx: int) -> datetime:
    """The 1h clock every synthetic fixture in this file shares."""
    return BASE + timedelta(hours=bar_idx)


def _c(rows):
    """`(bar_idx, o, h, l, c)` rows -> candles on the shared 1h clock."""
    return candles_from_ohlc(rows, _ts)


class FakeSpec:
    """The minimal structural `TickSizeSpec`: a `tick_size` and nothing else."""

    def __init__(self, tick_size: Decimal) -> None:
        self.tick_size = tick_size


REAL_TICKS = {
    "BTCUSD": Decimal("0.5"),
    "ETHUSD": Decimal("0.05"),
    "SOLUSD": Decimal("0.01"),
    "XRPUSD": Decimal("0.0001"),
}


def _specs(*assets):
    return {a: FakeSpec(REAL_TICKS[a]) for a in assets}


# ---------------------------------------------------------------------------
# Synthetic fixtures. SHORT geometry:  top = origin CLOSE, bottom = origin LOW,
# distal = origin CLOSE.  LONG geometry: top = origin HIGH, bottom = origin
# CLOSE, distal = origin CLOSE. Both derived by `geometry._make_manual_ob`;
# the constants below are the arithmetic consequence, restated so a silent
# change to the geometry breaks a named number rather than a whole run.
# ---------------------------------------------------------------------------
SHORT_ROWS = [
    (0, 100.0, 106.0, 99.0, 105.0),    # bullish origin: close 105.0, low 99.0
    (1, 104.0, 104.5, 97.0, 98.0),     # BOS: close 98.0 < ob_bottom 99.0
    (2, 98.0, 101.0, 97.5, 100.0),     # probe close 100.0 > proximal + touch
    (3, 100.0, 100.2, 98.0, 98.5),     # pullback close 98.5 -> displacement
    (4, 99.6, 101.0, 99.5, 100.0),     # high 101.0 >= entry 100.5 -> FILL
    (5, 100.0, 100.5, 99.5, 99.8),     # low 99.5 <= tp 99.897 -> TP close
]
SHORT_OB_ID = "MANUAL_BTCUSD_SHORT_0_1"
SHORT_TOP, SHORT_BOTTOM, SHORT_WIDTH = 105.0, 99.0, 6.0
SHORT_ENTRY, SHORT_SL, SHORT_TP = 100.5, 105.0, 99.897

LONG_ROWS = [
    (0, 105.0, 106.0, 99.0, 100.0),    # bearish origin: high 106.0, close 100.0
    (1, 101.0, 107.5, 100.5, 107.0),   # BOS: close 107.0 > ob_top 106.0
    (2, 107.0, 107.2, 104.0, 105.0),   # probe close 105.0 < proximal + touch
    (3, 105.0, 107.0, 104.8, 106.5),   # pullback close 106.5 -> displacement
    (4, 105.5, 106.0, 104.0, 105.0),   # low 104.0 <= entry 104.5 -> FILL
    (5, 105.0, 105.2, 104.9, 105.1),   # high 105.2 >= tp 105.127 -> TP close
]
LONG_OB_ID = "MANUAL_BTCUSD_LONG_0_1"
LONG_TOP, LONG_BOTTOM, LONG_WIDTH = 106.0, 100.0, 6.0
LONG_ENTRY, LONG_SL, LONG_TP = 104.5, 100.0, 105.127


# ---------------------------------------------------------------------------
# THIS FILE PINS THE ORACLE (RESEARCH) ACTIVATION MODE — deliberately.
# ---------------------------------------------------------------------------
# Every fixture here, including the published golden BTC window, is a Mode-C
# script: probe candle, pullback candle, then the fill on `displacement_bar + 1`
# or later, with the oracle's absolute 0.60% take profit. Those bar indices and
# prices ARE the published research baseline, which is the whole point of the
# golden test — so the fixtures keep their oracle meaning and the two oracle
# keywords are named explicitly.
#
# `ManualSMCBacktest` itself now DEFAULTS to the production policy (first touch,
# three-candle window, an authorized 0.60% TP — the same take profit the oracle
# uses, so ORACLE_KW below changes the ACTIVATION MODE and not the TP). The
# production backtest is covered by
# `test_manual_smc_first_touch_window.py` and by the 2024-2026 preload run.
ORACLE_KW = {
    "activation_mode": ACTIVATION_MODE_ORACLE_C,
    "config": ManualSpecConfig(),
}


def _drive(data, symbols=None, **kwargs):
    """One driver, one strategy, one lock. Returns (driver, [evaluations])."""
    syms = tuple(symbols) if symbols is not None else tuple(data)
    driver = ManualSMCBacktest(symbols=syms, **{**ORACLE_KW, **kwargs})
    timeline = build_timeline(data, syms)
    return driver, list(driver.iter_run(timeline, data))


def _run_oracle(*args, **kwargs) -> BacktestResult:
    """
    `run_manual_smc_backtest_from_candles` under the ORACLE activation policy.

    A wrapper rather than a per-call-site edit so that every assertion below
    stays exactly as it was written, and so there is one place that says why.
    """
    return run_manual_smc_backtest_from_candles(
        *args, **{**ORACLE_KW, **kwargs})


def _events(evaluations, ob_id=None):
    """Flatten to `(asset, bar_idx, event_type)`, optionally for one OB."""
    return [(ev.asset, e.bar_idx, e.event_type)
            for ev in evaluations for e in ev.events
            if ob_id is None or e.ob_id == ob_id]


def _bars_of(evaluations, event_type, ob_id=None):
    return [b for (_a, b, t) in _events(evaluations, ob_id) if t is event_type]


def _one(items, what):
    assert len(items) == 1, f"expected exactly one {what}, got {items}"
    return items[0]


# ===========================================================================
# §2 — the driver re-implements nothing
# ===========================================================================
class TestThinDriverContract:
    """
    The old golden suite reimplemented lifecycle behaviour inline and let
    orchestration bugs survive. These are the assertions that stop that from
    happening again, and they read the AST rather than the docstring.
    """

    #: Every method/function name that would betray a second state machine.
    FORBIDDEN_DEFS = (
        "process_candle", "_step1_resolve_active_trade", "_step2_update_obs",
        "_step3_scan_and_admit", "_update_awaiting", "_update_resting",
        "_close_trade", "_entry_blocked", "_emit", "candidate_obs",
        "scan", "_make_manual_ob", "make_manual_ob",
        "_manual_entry_touched", "_manual_distal_breached",
        "_manual_sl_hit", "_manual_tp_hit",
        "compute_leverage", "size_position", "settle_trade",
        "quantize_price", "quantize_bracket", "try_acquire", "acquire",
        "release", "evaluate_closed_candle",
    )

    def _defs(self):
        return {n.name for n in ast.walk(MODULE_AST)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def test_the_driver_defines_no_lifecycle_or_geometry_function(self):
        clash = self._defs() & set(self.FORBIDDEN_DEFS)
        assert not clash, (
            f"backtest.py defines {sorted(clash)}; a second implementation of "
            f"a shared rule is exactly what Step 8 section 2 forbids")

    def test_the_driver_calls_the_strategy_exactly_once_per_candle(self):
        calls = [n for n in ast.walk(MODULE_AST)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "evaluate_closed_candle"]
        assert len(calls) == 1, (
            "there must be exactly ONE call site into the strategy, so the "
            "lifecycle's load-bearing per-candle order cannot be run twice")

    def test_the_driver_never_touches_ob_state_or_the_active_trade(self):
        writes = []
        for node in ast.walk(MODULE_AST):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr in (
                        "state", "active_trade", "probe_confirmed",
                        "displacement_confirmed_bar", "limit_active_from_bar",
                        "entry_price", "sl_price", "tp_price", "retest_number",
                        "pre_displacement_touches", "live_obs", "_consumed"):
                    writes.append(t.attr)
        assert not writes, (
            f"backtest.py mutates strategy-owned state {sorted(set(writes))}; "
            f"the ledger must observe the lifecycle, never steer it")

    def test_the_driver_has_no_direction_specific_branch(self):
        assert "LONG" not in MODULE_SRC and "SHORT" not in MODULE_SRC, (
            "a direction literal in the driver would mean LONG is not simply "
            "the mirror of the same shared logic (Step 8 section 7)")

    def test_the_driver_imports_no_application_or_execution_module(self):
        imported = set()
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert "pandas" not in imported
        for banned in ("quantedge.strategy.models",
                       "quantedge.strategy.manual_smc.adapter",
                       "quantedge.execution", "quantedge.runtime",
                       "quantedge.strategy.smc"):
            assert banned not in imported, f"backtest.py imports {banned}"
        assert not any(m.startswith("quantedge.execution")
                       or ".delta" in m or "websocket" in m or "psycopg" in m
                       or m.startswith("sqlalchemy") for m in imported)

    def test_every_strategy_module_it_uses_is_the_shared_one(self):
        froms = {n.module for n in ast.walk(MODULE_AST)
                 if isinstance(n, ast.ImportFrom) and n.module
                 and n.module.startswith("quantedge")}
        assert froms == {
            "quantedge.strategy.manual_smc.lifecycle",
            "quantedge.strategy.manual_smc.models",
            "quantedge.strategy.manual_smc.quantization",
            "quantedge.strategy.manual_smc.sizing",
            "quantedge.strategy.manual_smc.strategy",
        }, sorted(froms)

    def test_one_strategy_instance_means_one_portfolio_lock(self):
        driver = ManualSMCBacktest(symbols=("BTCUSD", "ETHUSD"))
        assert driver.strategy.lock is driver.strategy.lock
        assert driver.strategy.lifecycle is driver.strategy.lifecycle
        # A second driver must not share the first one's slot.
        other = ManualSMCBacktest(symbols=("BTCUSD",))
        assert other.strategy.lock is not driver.strategy.lock

    def test_a_caller_supplied_strategy_is_reused_not_wrapped(self):
        from quantedge.strategy.manual_smc.strategy import ManualSMCStrategy
        strategy = ManualSMCStrategy(assets=["BTCUSD"])
        driver = ManualSMCBacktest(symbols=("BTCUSD",), strategy=strategy)
        assert driver.strategy is strategy
        assert driver.cfg is strategy.cfg


# ===========================================================================
# Governance: this file cannot be the thing that turns on execution
# ===========================================================================
class TestGovernance:

    def test_live_execution_is_not_authorized(self):
        assert LIVE_EXECUTION_AUTHORIZED is False
        assert bt.LIVE_EXECUTION_AUTHORIZED is False

    def test_the_driver_refuses_to_construct_if_the_flag_is_flipped(self,
                                                                   monkeypatch):
        monkeypatch.setattr(bt, "LIVE_EXECUTION_AUTHORIZED", True)
        with pytest.raises(BacktestGovernanceError):
            ManualSMCBacktest(symbols=("BTCUSD",))
        with pytest.raises(BacktestGovernanceError):
            _run_oracle({"BTCUSD": _c(SHORT_ROWS)})

    def test_the_driver_reports_the_manual_smc_identity(self):
        result = _run_oracle({"BTCUSD": _c(SHORT_ROWS)})
        assert result.strategy_name == MANUAL_SMC_STRATEGY_NAME == "MANUAL_SMC"
        assert result.strategy_version == MANUAL_SMC_STRATEGY_VERSION == "1.0.0"
        assert all(t.strategy_name == "MANUAL_SMC" for t in result.trades)


# ===========================================================================
# Historical data preparation — the one responsibility the driver may own
# ===========================================================================
def _write_csv(base: Path, symbol: str, lines, name="full_history.csv"):
    d = base / symbol / "1h"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d / name


HEADER = "timestamp,open,high,low,close,volume"


class TestDataContract:
    """
    Reproduces the oracle's loading contract as I/O, with two deliberate
    fail-closed divergences: no guessed repository root, and no duplicate
    timestamps.
    """

    def test_it_reads_the_canonical_columns_and_indexes_by_position(self, tmp_path):
        _write_csv(tmp_path, "BTCUSD", [
            HEADER,
            "2026-01-01T00:00:00+00:00,1,2,0.5,1.5,10",
            "2026-01-01T01:00:00+00:00,1.5,3,1,2.5,11",
        ])
        candles = load_canonical_candles(tmp_path, "BTCUSD")
        assert [c.bar_idx for c in candles] == [0, 1]
        assert candles[0] == Candle(0, BASE, 1.0, 2.0, 0.5, 1.5, 10.0)
        assert candles[1].close == 2.5

    def test_a_naive_timestamp_is_read_as_utc(self, tmp_path):
        _write_csv(tmp_path, "BTCUSD", [HEADER, "2026-01-01 00:00:00,1,2,0.5,1.5,0"])
        assert load_canonical_candles(tmp_path, "BTCUSD")[0].ts == BASE

    def test_rows_are_sorted_by_timestamp_before_indexing(self, tmp_path):
        _write_csv(tmp_path, "BTCUSD", [
            HEADER,
            "2026-01-01T02:00:00+00:00,3,3,3,3,0",
            "2026-01-01T00:00:00+00:00,1,1,1,1,0",
        ])
        candles = load_canonical_candles(tmp_path, "BTCUSD")
        assert [c.bar_idx for c in candles] == [0, 1]
        assert [c.close for c in candles] == [1.0, 3.0]

    def test_a_missing_symbol_yields_an_empty_history_like_the_oracle(self, tmp_path):
        assert load_canonical_candles(tmp_path, "NOPEUSD") == []
        assert canonical_csv_path(tmp_path, "NOPEUSD") is None

    def test_the_2026_csv_is_the_documented_fallback(self, tmp_path):
        _write_csv(tmp_path, "ETHUSD",
                   [HEADER, "2026-01-01T00:00:00+00:00,1,2,0.5,1.5,0"],
                   name="2026.csv")
        assert canonical_csv_path(tmp_path, "ETHUSD").name == "2026.csv"
        assert len(load_canonical_candles(tmp_path, "ETHUSD")) == 1

    def test_full_history_wins_over_the_fallback(self, tmp_path):
        _write_csv(tmp_path, "ETHUSD", [HEADER, "2026-01-01T00:00:00+00:00,1,2,0.5,1.5,0"])
        _write_csv(tmp_path, "ETHUSD", [HEADER, "2026-01-01T00:00:00+00:00,9,9,9,9,0"],
                   name="2026.csv")
        assert canonical_csv_path(tmp_path, "ETHUSD").name == "full_history.csv"

    def test_a_missing_column_is_refused_not_defaulted(self, tmp_path):
        _write_csv(tmp_path, "BTCUSD",
                   ["timestamp,open,high,close", "2026-01-01T00:00:00+00:00,1,2,1.5"])
        with pytest.raises(BacktestDataError, match="missing required column"):
            load_canonical_candles(tmp_path, "BTCUSD")

    def test_an_unreadable_row_is_refused_with_its_line_number(self, tmp_path):
        _write_csv(tmp_path, "BTCUSD", [
            HEADER,
            "2026-01-01T00:00:00+00:00,1,2,0.5,1.5,0",
            "2026-01-01T01:00:00+00:00,1,2,0.5,NOT_A_PRICE,0",
        ])
        with pytest.raises(BacktestDataError, match=r":3: unreadable row"):
            load_canonical_candles(tmp_path, "BTCUSD")

    def test_a_duplicate_timestamp_is_refused_rather_than_double_indexed(self, tmp_path):
        _write_csv(tmp_path, "BTCUSD", [
            HEADER,
            "2026-01-01T00:00:00+00:00,1,2,0.5,1.5,0",
            "2026-01-01T00:00:00+00:00,1,2,0.5,1.6,0",
        ])
        with pytest.raises(BacktestDataError, match="duplicate timestamp"):
            load_canonical_candles(tmp_path, "BTCUSD")

    def test_a_volume_column_is_optional(self, tmp_path):
        _write_csv(tmp_path, "BTCUSD",
                   ["timestamp,open,high,low,close", "2026-01-01T00:00:00+00:00,1,2,0.5,1.5"])
        assert load_canonical_candles(tmp_path, "BTCUSD")[0].volume == 0.0

    def test_the_dataset_keeps_every_requested_symbol_even_when_empty(self, tmp_path):
        _write_csv(tmp_path, "BTCUSD", [HEADER, "2026-01-01T00:00:00+00:00,1,2,0.5,1.5,0"])
        dataset = load_canonical_dataset(tmp_path, ("BTCUSD", "ETHUSD"))
        assert sorted(dataset) == ["BTCUSD", "ETHUSD"]
        assert dataset["ETHUSD"] == []

    def test_no_repository_root_is_refused_rather_than_guessed(self, tmp_path):
        with pytest.raises(BacktestDataError, match="no repository root"):
            find_repo_root(tmp_path / "a" / "b" / "c")

    def test_the_real_repository_root_is_found_by_marker(self):
        root = find_repo_root()
        assert (root / "engine").is_dir()
        assert default_canonical_base().parts[-3:] == (
            "data", "canonical", "delta_exchange_india")

    def test_the_default_symbol_set_is_the_four_documented_assets(self):
        assert DEFAULT_SYMBOLS == ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")


# ===========================================================================
# The single global clock
# ===========================================================================
class TestTimelineContract:

    def test_it_is_sorted_by_timestamp_then_symbol_name(self):
        data = {"ETHUSD": _c([(0, 1, 1, 1, 1), (1, 1, 1, 1, 1)]),
                "BTCUSD": _c([(0, 1, 1, 1, 1), (1, 1, 1, 1, 1)])}
        timeline = build_timeline(data, ("ETHUSD", "BTCUSD"))
        assert timeline == [
            TimelineRow(_ts(0), "BTCUSD", 0), TimelineRow(_ts(0), "ETHUSD", 0),
            TimelineRow(_ts(1), "BTCUSD", 1), TimelineRow(_ts(1), "ETHUSD", 1),
        ]

    def test_the_tie_break_does_not_depend_on_dict_insertion_order(self):
        rows = _c([(0, 1, 1, 1, 1)])
        a = build_timeline({"ETHUSD": rows, "BTCUSD": rows}, ("BTCUSD", "ETHUSD"))
        b = build_timeline({"BTCUSD": rows, "ETHUSD": rows}, ("ETHUSD", "BTCUSD"))
        assert a == b == [TimelineRow(_ts(0), "BTCUSD", 0),
                          TimelineRow(_ts(0), "ETHUSD", 0)]

    def test_a_date_filter_drops_rows_but_never_renumbers_bar_idx(self):
        data = {"BTCUSD": _c([(i, 1, 1, 1, 1) for i in range(5)])}
        timeline = build_timeline(data, ("BTCUSD",), start_date=_ts(2),
                                  end_date=_ts(3))
        assert [r.bar_idx for r in timeline] == [2, 3]

    def test_a_symbol_absent_from_the_dataset_contributes_no_rows(self):
        data = {"BTCUSD": _c([(0, 1, 1, 1, 1)])}
        assert build_timeline(data, ("BTCUSD", "ETHUSD")) == [
            TimelineRow(_ts(0), "BTCUSD", 0)]

    def test_a_timeline_row_with_no_candle_is_refused(self):
        data = {"BTCUSD": _c([(0, 1, 1, 1, 1)])}
        driver = ManualSMCBacktest(symbols=("BTCUSD",))
        bogus = [TimelineRow(_ts(9), "BTCUSD", 9)]
        with pytest.raises(BacktestDataError, match="not in the loaded history"):
            driver.run(bogus, data)

    def test_a_timeline_timestamp_that_contradicts_the_candle_is_refused(self):
        data = {"BTCUSD": _c([(0, 1, 1, 1, 1)])}
        driver = ManualSMCBacktest(symbols=("BTCUSD",))
        with pytest.raises(BacktestDataError, match="disagrees with the candle"):
            driver.run([TimelineRow(_ts(5), "BTCUSD", 0)], data)


# ===========================================================================
# §5 — the golden BTC trade, through the real backtest path
# ===========================================================================
GOLDEN_OB_ID = "MANUAL_BTCUSD_SHORT_19577_19580"
GOLDEN_ORIGIN_BAR, GOLDEN_BOS_BAR = 19577, 19580
GOLDEN_PROBE_BAR, GOLDEN_DISPLACEMENT_BAR = 19582, 19583
GOLDEN_LIMIT_ACTIVE_BAR, GOLDEN_FILL_BAR, GOLDEN_EXIT_BAR = 19584, 19585, 19593
GOLDEN_TOP, GOLDEN_BOTTOM, GOLDEN_WIDTH = 79210.5, 78725.5, 485.0
GOLDEN_ENTRY, GOLDEN_SL, GOLDEN_TP = 78846.75, 79210.5, 78373.6695
#: A window that starts well before the origin but keeps the test fast. The
#: scanner needs only `lookback + 1` bars of history, so bar 19500 is ample.
GOLDEN_WINDOW_START_BAR = 19500


def _golden_dataset():
    base = default_canonical_base()
    if canonical_csv_path(base, "BTCUSD") is None:
        pytest.skip(f"no canonical BTCUSD 1h history under {base}")
    dataset = load_canonical_dataset(base, ("BTCUSD",))
    if len(dataset["BTCUSD"]) <= GOLDEN_EXIT_BAR:
        pytest.skip("canonical BTCUSD history is shorter than the golden window")
    return dataset


class TestGoldenBTCTradeThroughTheRealBacktestPath:
    """
    `backtest.py -> strategy.py -> shared lifecycle/scanner/geometry`, against
    the real canonical CSV. Nothing here is transcribed candle data: the trade
    is whatever the production path actually produces.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def run():
        dataset = _golden_dataset()
        start = dataset["BTCUSD"][GOLDEN_WINDOW_START_BAR].ts
        driver = ManualSMCBacktest(symbols=("BTCUSD",),
                                   tick_specs=_specs("BTCUSD"), **ORACLE_KW)
        timeline = build_timeline(dataset, ("BTCUSD",), start_date=start)
        evaluations = list(driver.iter_run(timeline, dataset))
        return driver, evaluations

    def test_the_ob_is_created_on_the_bos_candle_from_the_expected_origin(self, run):
        _driver, evaluations = run
        assert _bars_of(evaluations, ET.OB_CREATED, GOLDEN_OB_ID) == [
            GOLDEN_BOS_BAR]

    def test_the_probe_and_the_displacement_are_two_distinct_candles(self, run):
        _driver, evaluations = run
        assert _bars_of(evaluations, ET.PROBE_CONFIRMED, GOLDEN_OB_ID) == [
            GOLDEN_PROBE_BAR]
        assert _bars_of(evaluations, ET.DISPLACEMENT_CONFIRMED,
                        GOLDEN_OB_ID) == [GOLDEN_DISPLACEMENT_BAR]
        assert GOLDEN_PROBE_BAR < GOLDEN_DISPLACEMENT_BAR

    def test_the_limit_goes_live_the_bar_after_displacement_and_does_not_fill_there(
            self, run):
        _driver, evaluations = run
        detail = _one([e.detail for ev in evaluations for e in ev.events
                       if e.ob_id == GOLDEN_OB_ID
                       and e.event_type is ET.DISPLACEMENT_CONFIRMED],
                      "displacement event")
        assert f"limit active from bar {GOLDEN_LIMIT_ACTIVE_BAR}" in detail
        fills = _bars_of(evaluations, ET.ENTRY_FILLED, GOLDEN_OB_ID)
        assert GOLDEN_DISPLACEMENT_BAR not in fills
        assert GOLDEN_LIMIT_ACTIVE_BAR not in fills

    def test_the_fill_and_the_close_land_on_the_published_bars(self, run):
        _driver, evaluations = run
        assert _bars_of(evaluations, ET.ENTRY_FILLED, GOLDEN_OB_ID) == [
            GOLDEN_FILL_BAR]
        assert _bars_of(evaluations, ET.TRADE_CLOSED, GOLDEN_OB_ID) == [
            GOLDEN_EXIT_BAR]

    @pytest.fixture(scope="class")
    @staticmethod
    def trade(run):
        driver, _evaluations = run
        return _one([t for t in driver.trades if t.ob_id == GOLDEN_OB_ID],
                    "golden trade in the ledger")

    def test_the_ob_geometry_is_the_published_geometry(self, trade):
        assert trade.origin_bar_idx == GOLDEN_ORIGIN_BAR
        assert trade.bos_bar_idx == GOLDEN_BOS_BAR
        assert trade.ob_top == GOLDEN_TOP
        assert trade.ob_bottom == GOLDEN_BOTTOM
        assert trade.ob_width == GOLDEN_WIDTH
        # SHORT: price returns from below, so the proximal is the bottom and
        # the distal (= origin close = top) is what a wick invalidates through.
        assert trade.proximal == GOLDEN_BOTTOM
        assert trade.distal == GOLDEN_TOP

    def test_the_bracket_is_the_published_bracket(self, trade):
        assert trade.entry_price == GOLDEN_ENTRY
        assert trade.sl_price == GOLDEN_SL
        assert trade.tp_price == GOLDEN_TP
        # 25% depth from the proximal, and an ABSOLUTE 0.60% market move.
        assert trade.entry_price == pytest.approx(
            GOLDEN_BOTTOM + 0.25 * GOLDEN_WIDTH)
        assert trade.tp_price == pytest.approx(GOLDEN_ENTRY * (1 - 0.006))
        assert trade.sl_price == trade.distal

    def test_the_outcome_is_the_published_take_profit(self, trade):
        assert (trade.outcome, trade.reason_for_exit) == ("FILLED_TP", "TP_HIT")
        assert trade.exit_price == GOLDEN_TP
        assert trade.is_ambiguous is False
        assert trade.is_win and not trade.is_loss and not trade.is_timeout
        assert trade.fill_bar_idx == GOLDEN_FILL_BAR
        assert trade.exit_bar_idx == GOLDEN_EXIT_BAR
        assert trade.holding_bars == GOLDEN_EXIT_BAR - GOLDEN_FILL_BAR == 8

    def test_the_sizing_is_the_shared_sizing_module_arithmetic(self, trade):
        assert trade.realized_r == pytest.approx(1.3005649484535984, rel=1e-12)
        assert trade.sl_dist_pct == pytest.approx(
            abs(GOLDEN_ENTRY - GOLDEN_SL) / GOLDEN_ENTRY * 100.0)
        assert trade.theoretical_leverage == pytest.approx(
            35.0 / trade.sl_dist_pct)
        assert trade.applied_leverage == trade.theoretical_leverage < 100.0
        assert trade.leverage_clamped is False

    def test_the_provenance_fields_survive_into_the_ledger(self, trade):
        assert trade.entry_bar_from_bos == GOLDEN_FILL_BAR - GOLDEN_BOS_BAR == 5
        assert trade.pre_displacement_touches == 2
        assert trade.retest_number == 1
        assert trade.data_timeframe == "1h"
        assert trade.displacement_mode
        assert trade.quantized_at_fill is True
        assert trade.quantization_refusal is None


# ===========================================================================
# §4 / §7 — SHORT and its LONG mirror, both through the same shared code
# ===========================================================================
class TestShortSemanticsThroughTheDriver:

    @pytest.fixture(scope="class")
    @staticmethod
    def run():
        return _drive({"BTCUSD": _c(SHORT_ROWS)})

    def test_the_origin_is_the_most_recent_bullish_candle_before_the_bos(self, run):
        _d, evaluations = run
        assert _bars_of(evaluations, ET.OB_CREATED, SHORT_OB_ID) == [1]

    def test_short_geometry_uses_the_origin_close_as_top(self, run):
        driver, _e = run
        trade = _one(list(driver.trades), "trade")
        assert (trade.ob_top, trade.ob_bottom) == (SHORT_TOP, SHORT_BOTTOM)
        assert (trade.proximal, trade.distal) == (SHORT_BOTTOM, SHORT_TOP)
        assert (trade.entry_price, trade.sl_price, trade.tp_price) == (
            SHORT_ENTRY, SHORT_SL, SHORT_TP)
        assert trade.tp_price == pytest.approx(SHORT_ENTRY * (1 - 0.006))

    def test_the_short_sequence_is_probe_then_displacement_then_fill_then_tp(self, run):
        _d, evaluations = run
        assert _bars_of(evaluations, ET.PROBE_CONFIRMED, SHORT_OB_ID) == [2]
        assert _bars_of(evaluations, ET.DISPLACEMENT_CONFIRMED, SHORT_OB_ID) == [3]
        assert _bars_of(evaluations, ET.ENTRY_FILLED, SHORT_OB_ID) == [4]
        assert _bars_of(evaluations, ET.TRADE_CLOSED, SHORT_OB_ID) == [5]

    def test_the_trade_exits_at_take_profit(self, run):
        driver, _e = run
        trade = _one(list(driver.trades), "trade")
        assert (trade.direction, trade.outcome) == ("SHORT", "FILLED_TP")
        assert trade.exit_price == SHORT_TP
        assert trade.realized_r > 0


class TestLongMirrorEndToEnd:
    """
    Step 8 section 7. The LONG side must be the mirror of the SAME shared
    logic — which `TestThinDriverContract` already proves structurally by the
    absence of any direction literal in the driver. This walks it behaviourally.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def run():
        return _drive({"BTCUSD": _c(LONG_ROWS)})

    @pytest.fixture(scope="class")
    @staticmethod
    def trade(run):
        driver, _e = run
        return _one(list(driver.trades), "LONG trade")

    def test_a_long_bos_is_a_close_above_the_origin_high(self, run):
        _d, evaluations = run
        assert _bars_of(evaluations, ET.OB_CREATED, LONG_OB_ID) == [1]
        assert LONG_ROWS[1][4] > LONG_TOP          # close 107.0 > ob_top 106.0

    def test_the_long_origin_is_the_most_recent_bearish_candle(self, trade):
        assert trade.direction == "LONG"
        assert trade.origin_bar_idx == 0           # close 100.0 < open 105.0
        assert trade.bos_bar_idx == 1

    def test_long_geometry_uses_the_origin_high_and_the_origin_close(self, trade):
        assert trade.ob_top == LONG_TOP == LONG_ROWS[0][2]      # origin HIGH
        assert trade.ob_bottom == LONG_BOTTOM == LONG_ROWS[0][4]  # origin CLOSE
        assert trade.ob_width == LONG_WIDTH

    def test_the_long_proximal_and_distal_are_mirrored(self, trade):
        # LONG: price returns from above, so the proximal is the TOP and the
        # distal is the origin close at the BOTTOM — the exact inverse of SHORT.
        assert trade.proximal == LONG_TOP
        assert trade.distal == LONG_BOTTOM
        assert trade.sl_price == trade.distal == LONG_SL

    def test_the_long_entry_is_25_percent_deep_from_the_proximal(self, trade):
        assert trade.entry_price == LONG_ENTRY
        assert trade.entry_price == pytest.approx(LONG_TOP - 0.25 * LONG_WIDTH)

    def test_the_long_take_profit_is_a_plus_0_60_percent_market_move(self, trade):
        assert trade.tp_price == LONG_TP
        assert trade.tp_price == pytest.approx(LONG_ENTRY * (1 + 0.006))
        assert trade.tp_price > trade.entry_price > trade.sl_price

    def test_the_long_probe_is_a_close_back_below_the_proximal(self, run):
        _d, evaluations = run
        assert _bars_of(evaluations, ET.PROBE_CONFIRMED, LONG_OB_ID) == [2]
        assert LONG_ROWS[2][4] < LONG_TOP

    def test_the_long_displacement_is_a_later_close_back_above_the_proximal(self, run):
        _d, evaluations = run
        assert _bars_of(evaluations, ET.DISPLACEMENT_CONFIRMED, LONG_OB_ID) == [3]
        assert LONG_ROWS[3][4] > LONG_TOP

    def test_the_long_fill_is_a_low_touching_the_entry_after_displacement(self, run, trade):
        _d, evaluations = run
        assert _bars_of(evaluations, ET.ENTRY_FILLED, LONG_OB_ID) == [4]
        assert LONG_ROWS[4][3] <= LONG_ENTRY
        assert trade.fill_bar_idx == 4
        assert trade.entry_bar_from_bos == 3

    def test_the_long_trade_closes_at_take_profit(self, run, trade):
        _d, evaluations = run
        assert _bars_of(evaluations, ET.TRADE_CLOSED, LONG_OB_ID) == [5]
        assert (trade.outcome, trade.reason_for_exit) == ("FILLED_TP", "TP_HIT")
        assert trade.exit_price == LONG_TP
        assert trade.realized_r == pytest.approx(
            (LONG_TP - LONG_ENTRY) / (LONG_ENTRY - LONG_SL), rel=1e-9)

    def test_a_long_wick_below_the_distal_invalidates_and_no_later_fill_happens(self):
        rows = LONG_ROWS[:4] + [
            (4, 105.5, 106.0, 99.5, 101.0),    # low 99.5 <= distal 100.0
            (5, 101.0, 106.0, 100.5, 105.0),   # would have touched entry 104.5
        ]
        driver, evaluations = _drive({"BTCUSD": _c(rows)})
        assert _bars_of(evaluations, ET.INVALIDATED, LONG_OB_ID) == [4]
        assert _bars_of(evaluations, ET.ENTRY_FILLED, LONG_OB_ID) == []
        assert not [t for t in driver.trades if t.ob_id == LONG_OB_ID]

    def test_a_long_stop_loss_is_the_distal(self):
        rows = LONG_ROWS[:5] + [(5, 105.0, 105.1, 99.0, 100.0)]
        driver, _e = _drive({"BTCUSD": _c(rows)})
        trade = _one([t for t in driver.trades if t.ob_id == LONG_OB_ID], "trade")
        assert (trade.outcome, trade.reason_for_exit) == ("FILLED_SL", "SL_HIT")
        assert trade.exit_price == LONG_SL == trade.distal
        assert trade.realized_r == pytest.approx(-1.0)
        assert trade.is_loss and not trade.is_win

    def test_a_long_dual_touch_resolves_conservatively_to_the_stop(self):
        rows = LONG_ROWS[:5] + [(5, 105.0, 105.5, 99.5, 104.0)]
        driver, _e = _drive({"BTCUSD": _c(rows)})
        trade = _one([t for t in driver.trades if t.ob_id == LONG_OB_ID], "trade")
        assert rows[5][2] >= LONG_TP and rows[5][3] <= LONG_SL
        assert trade.reason_for_exit == "DUAL_TOUCH_CONSERVATIVE_SL"
        assert (trade.outcome, trade.exit_price) == ("FILLED_SL", LONG_SL)
        assert trade.is_ambiguous is True

    def test_a_long_trade_times_out_at_market_after_72_bars(self):
        rows = LONG_ROWS[:5] + [
            (b, 104.8, 105.0, 104.6, 104.8) for b in range(5, 78)]
        driver, _e = _drive({"BTCUSD": _c(rows)})
        trade = _one([t for t in driver.trades if t.ob_id == LONG_OB_ID], "trade")
        assert (trade.outcome, trade.reason_for_exit) == ("FILLED_TIMEOUT",
                                                         "TIMEOUT")
        assert trade.holding_bars == 72
        assert trade.exit_bar_idx == 4 + 72 == 76
        assert trade.exit_price == 104.8      # the timeout bar's CLOSE
        assert trade.is_timeout and not trade.is_win and not trade.is_loss


# ===========================================================================
# §3 / §8 — the global single-trade lock is portfolio wide
# ===========================================================================
#: BTC fills at bar 4, holds through bars 5-8, takes profit at bar 9.
_HOLD = (100.2, 100.4, 100.0, 100.2)     # neither tp 99.897 nor sl 105.0
BTC_LOCK_ROWS = (
    SHORT_ROWS[:5]
    + [(b,) + _HOLD for b in (5, 6, 7, 8)]
    + [(9, 100.2, 100.4, 99.5, 99.8),    # low 99.5 <= tp 99.897 -> TP close
       (10,) + _HOLD, (11,) + _HOLD]
)
#: The same SHORT setup one bar later on ETH, with an entry touch on every bar
#: from 5 to 11 — so every bar BTC holds the slot is a bar ETH wanted it.
_ETH_TOUCH = (99.6, 101.0, 99.5, 100.0)  # high 101.0 >= entry 100.5, < distal
ETH_LOCK_ROWS = (
    [(0, 100.0, 100.5, 99.5, 100.0)]
    + [(b + 1,) + tuple(SHORT_ROWS[b][1:]) for b in range(4)]
    + [(b,) + _ETH_TOUCH for b in (5, 6, 7, 8, 9, 10)]
    + [(11, 100.0, 100.5, 99.5, 99.8)]
)
BTC_LOCK_OB_ID = "MANUAL_BTCUSD_SHORT_0_1"
ETH_LOCK_OB_ID = "MANUAL_ETHUSD_SHORT_1_2"
LOCK_SYMBOLS = ("BTCUSD", "ETHUSD")


def _lock_data():
    return {"BTCUSD": _c(BTC_LOCK_ROWS), "ETHUSD": _c(ETH_LOCK_ROWS)}


class TestPortfolioWideGlobalLock:
    """
    Step 8 section 3 and section 8. The oracle's lock was a timestamp
    watermark: a setup at a STRICTLY LATER timestamp passed it and overwrote
    `active_trade`, stranding the previous trade. The corrected rule is
    `active_trade is not None`, and it is one slot for the whole portfolio.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def run():
        return _drive(_lock_data(), LOCK_SYMBOLS)

    def test_eth_has_a_live_limit_while_btc_holds_the_slot(self, run):
        _d, evaluations = run
        assert _bars_of(evaluations, ET.DISPLACEMENT_CONFIRMED,
                        ETH_LOCK_OB_ID) == [4]
        assert _bars_of(evaluations, ET.ENTRY_FILLED, BTC_LOCK_OB_ID) == [4]

    def test_eth_is_blocked_on_every_bar_btc_is_active(self, run):
        _d, evaluations = run
        assert _bars_of(evaluations, ET.ENTRY_BLOCKED_BY_ACTIVE_TRADE,
                        ETH_LOCK_OB_ID) == [5, 6, 7, 8, 9]

    def test_the_block_names_the_other_asset_as_the_holder(self, run):
        driver, _e = run
        blocks = [b for b in driver.entry_blocks if b.bar_idx in (5, 6, 7, 8)]
        assert len(blocks) == 4
        for block in blocks:
            assert block.asset == "ETHUSD"
            assert block.ob_id == ETH_LOCK_OB_ID
            assert block.rejection_code == "ACTIVE_TRADE_OPEN"
            assert block.holder_asset == "BTCUSD"
            assert block.holder_ob_id == BTC_LOCK_OB_ID
            assert block.holder_acquired_at == _ts(4)

    def test_the_lock_is_not_a_timestamp_cooldown(self, run):
        """
        Every one of those blocks is at a timestamp strictly LATER than the
        holder's fill, which is precisely the case the oracle's
        `c_ts <= global_lock_until_dt` watermark let through.
        """
        driver, _e = run
        later = [b for b in driver.entry_blocks
                 if b.oracle_would_have_overwritten]
        assert [b.bar_idx for b in later] == [5, 6, 7, 8]
        assert all(b.ts > b.holder_acquired_at for b in later)

    def test_eth_fills_only_after_btc_actually_closes(self, run):
        driver, evaluations = run
        assert _bars_of(evaluations, ET.TRADE_CLOSED, BTC_LOCK_OB_ID) == [9]
        assert _bars_of(evaluations, ET.ENTRY_FILLED, ETH_LOCK_OB_ID) == [10]
        btc = _one([t for t in driver.trades if t.asset == "BTCUSD"], "BTC trade")
        eth = _one([t for t in driver.trades if t.asset == "ETHUSD"], "ETH trade")
        assert eth.fill_dt > btc.exit_dt

    def test_a_release_and_a_new_entry_never_share_one_candle(self, run):
        """The retained intra-candle guard: bar 9 is the release bar."""
        driver, _e = run
        bar9 = _one([b for b in driver.entry_blocks if b.bar_idx == 9],
                    "bar-9 block")
        assert bar9.rejection_code == "INTRA_CANDLE_AMBIGUITY"
        assert bar9.holder_asset is None
        assert bar9.oracle_would_have_overwritten is False

    def test_never_more_than_one_trade_active_at_any_point(self):
        data = _lock_data()
        driver = ManualSMCBacktest(symbols=LOCK_SYMBOLS)
        for _ev in driver.iter_run(build_timeline(data, LOCK_SYMBOLS), data):
            active = [ob for ob in driver.strategy.lifecycle.live_obs.values()
                      if ob.state is ManualOBState.TRADE_ACTIVE]
            assert len(active) <= 1
            holder = driver.strategy.lock.active_trade
            assert (holder is None) == (
                driver.strategy.lifecycle.active_trade is None)

    def test_the_lock_is_not_per_symbol(self, run):
        driver, _e = run
        cross = [b for b in driver.entry_blocks
                 if b.holder_asset is not None and b.holder_asset != b.asset]
        assert cross, "no cross-asset block: the slot would be per symbol"

    def test_two_assets_ready_on_the_same_candle_share_one_slot(self):
        rows = SHORT_ROWS[:5] + [(5, 100.0, 100.4, 100.0, 100.2)]
        data = {"BTCUSD": _c(rows), "ETHUSD": _c(rows)}
        driver, evaluations = _drive(data, LOCK_SYMBOLS)
        fills = [(a, b) for (a, b, t) in _events(evaluations)
                 if t is ET.ENTRY_FILLED]
        assert fills == [("BTCUSD", 4)]
        block = _one(list(driver.entry_blocks), "same-candle block")
        assert (block.asset, block.bar_idx) == ("ETHUSD", 4)
        assert block.rejection_code == "ACTIVE_TRADE_OPEN"
        assert block.ts == block.holder_acquired_at == _ts(4)
        # Same timestamp, so even the oracle's watermark would have held here.
        assert block.oracle_would_have_overwritten is False

    def test_the_winner_of_a_tie_is_deterministic_and_alphabetical(self):
        """
        The tie is decided by `build_timeline`'s `(ts, symbol)` sort, so the
        winner is the alphabetically first asset REGARDLESS of dict insertion
        order. Neither fill closes inside this six-bar fixture, so the winner is
        read off the FILL and the block's holder — `result.trades` records only
        CLOSED trades and is empty here, which is itself the honest answer.
        """
        rows = SHORT_ROWS[:5] + [(5, 100.0, 100.4, 100.0, 100.2)]
        forward = {"BTCUSD": _c(rows), "ETHUSD": _c(rows)}
        reversed_ = {"ETHUSD": _c(rows), "BTCUSD": _c(rows)}

        def _outcome(data):
            driver, evaluations = _drive(data, LOCK_SYMBOLS)
            fills = [(a, b) for (a, b, t) in _events(evaluations)
                     if t is ET.ENTRY_FILLED]
            blocks = [(b.asset, b.bar_idx, b.holder_asset)
                      for b in driver.entry_blocks]
            return fills, blocks, driver.strategy.lock.active_trade.asset

        a = _outcome(forward)
        b = _outcome(reversed_)
        assert a == b
        assert a == ([("BTCUSD", 4)], [("ETHUSD", 4, "BTCUSD")], "BTCUSD")

        # And the closed-trade ledger agrees with itself across both orders.
        first = _run_oracle(forward,
                                                     symbols=LOCK_SYMBOLS)
        second = _run_oracle(reversed_,
                                                      symbols=LOCK_SYMBOLS)
        assert [t.ob_id for t in first.trades] == [t.ob_id for t in second.trades]
        assert first.entry_blocks == second.entry_blocks

    def test_the_whole_multi_asset_run_is_reproducible(self):
        first = _run_oracle(_lock_data(),
                                                     symbols=LOCK_SYMBOLS)
        second = _run_oracle(_lock_data(),
                                                      symbols=LOCK_SYMBOLS)
        assert first.trades == second.trades
        assert first.entry_blocks == second.entry_blocks
        assert first.overall == second.overall


# ===========================================================================
# §9 — exact candle sequencing
# ===========================================================================
class TestCandleSequencing:

    def test_break_plus_one_an_ob_cannot_displace_on_its_own_bos_candle(self):
        _d, evaluations = _drive({"BTCUSD": _c(SHORT_ROWS)})
        created = _one(_bars_of(evaluations, ET.OB_CREATED, SHORT_OB_ID), "OB")
        for event_type in (ET.PROBE_CONFIRMED, ET.DISPLACEMENT_CONFIRMED,
                           ET.ENTRY_FILLED, ET.PRE_DISPLACEMENT_TOUCH):
            assert created not in _bars_of(evaluations, event_type, SHORT_OB_ID)

    def test_displacement_plus_one_the_displacement_candle_cannot_fill(self):
        # Bar 3 confirms displacement AND its low 98.0 is through entry 100.5
        # from a SHORT's perspective... but its HIGH 100.2 is not, so build a
        # displacement bar that unambiguously touches the entry level.
        rows = SHORT_ROWS[:3] + [
            (3, 100.0, 101.5, 98.0, 98.5),     # displacement + high >= 100.5
            (4, 99.6, 99.9, 99.5, 99.7),       # no touch
            (5, 99.7, 101.0, 99.5, 100.0),     # touch -> fill here instead
        ]
        _d, evaluations = _drive({"BTCUSD": _c(rows)})
        assert _bars_of(evaluations, ET.DISPLACEMENT_CONFIRMED, SHORT_OB_ID) == [3]
        assert _bars_of(evaluations, ET.ENTRY_FILLED, SHORT_OB_ID) == [5]

    def test_a_trade_cannot_open_and_close_on_the_same_bar(self):
        """
        Bar 4 both fills the limit (high 101.0 >= entry 100.5) AND trades
        through the take profit (low 99.5 <= tp 99.897). The exit resolves on
        bar 5 because the fill happens in step 2, after step 1 has already run.
        """
        driver, evaluations = _drive({"BTCUSD": _c(SHORT_ROWS)})
        assert SHORT_ROWS[4][3] <= SHORT_TP        # the TP was inside bar 4
        trade = _one(list(driver.trades), "trade")
        assert trade.fill_bar_idx == 4 and trade.exit_bar_idx == 5
        assert trade.holding_bars >= 1

    def test_the_exit_is_resolved_before_the_obs_advance(self):
        """
        Step 1 closes the active trade, step 2 then sweeps the OBs. The ledger
        must therefore record the close before any fill on the same candle —
        and the freed slot is still not reusable on that candle.
        """
        driver, evaluations = _drive(_lock_data(), LOCK_SYMBOLS)
        release_bar = _one(_bars_of(evaluations, ET.TRADE_CLOSED,
                                    BTC_LOCK_OB_ID), "release")
        same_bar_fills = [(a, b) for (a, b, t) in _events(evaluations)
                          if t is ET.ENTRY_FILLED and b == release_bar]
        assert same_bar_fills == []

    def test_a_replayed_candle_is_refused_rather_than_silently_reprocessed(self):
        from quantedge.strategy.manual_smc.strategy import DuplicateCandleError
        data = {"BTCUSD": _c(SHORT_ROWS)}
        driver = ManualSMCBacktest(symbols=("BTCUSD",))
        timeline = build_timeline(data, ("BTCUSD",))
        driver.run(timeline[:3], data)
        with pytest.raises(DuplicateCandleError):
            driver.run(timeline[2:3], data)

    def test_an_out_of_order_candle_is_refused(self):
        from quantedge.strategy.manual_smc.strategy import OutOfOrderCandleError
        data = {"BTCUSD": _c(SHORT_ROWS)}
        driver = ManualSMCBacktest(symbols=("BTCUSD",))
        timeline = build_timeline(data, ("BTCUSD",))
        driver.run(timeline[:4], data)
        with pytest.raises(OutOfOrderCandleError):
            driver.run(timeline[1:2], data)

    def test_equal_timestamps_across_assets_are_allowed(self):
        """Four 1h assets on one clock require this; it must not raise."""
        rows = _c([(0, 1, 1, 1, 1), (1, 1, 1, 1, 1)])
        result = _run_oracle(
            {"BTCUSD": rows, "ETHUSD": rows}, symbols=LOCK_SYMBOLS)
        assert result.candles_processed == 4


# ===========================================================================
# §10 — the forensic-audit edge cases
# ===========================================================================
class TestForensicEdgeCases:

    def test_1_a_dual_touch_resolves_to_the_stop_and_keeps_the_ambiguity(self):
        rows = SHORT_ROWS[:5] + [(5, 100.0, 105.5, 99.0, 100.0)]
        driver, _e = _drive({"BTCUSD": _c(rows)})
        trade = _one([t for t in driver.trades if t.ob_id == SHORT_OB_ID], "trade")
        assert rows[5][2] >= SHORT_SL and rows[5][3] <= SHORT_TP
        assert (trade.outcome, trade.reason_for_exit) == (
            "FILLED_SL", "DUAL_TOUCH_CONSERVATIVE_SL")
        assert trade.exit_price == SHORT_SL
        assert trade.is_ambiguous is True
        assert trade.realized_r == pytest.approx(-1.0)
        result = driver.result()
        assert result.overall.ambiguous == 1
        assert result.overall.losses == 1

    def test_2_a_trade_that_never_resolves_times_out_at_market(self):
        rows = SHORT_ROWS[:5] + [
            (b, 100.2, 100.4, 100.0, 100.2) for b in range(5, 78)]
        driver, _e = _drive({"BTCUSD": _c(rows)})
        trade = _one([t for t in driver.trades if t.ob_id == SHORT_OB_ID], "trade")
        assert (trade.outcome, trade.reason_for_exit) == ("FILLED_TIMEOUT",
                                                         "TIMEOUT")
        assert trade.holding_bars == 72 == ManualSpecConfig().max_holding_bars
        assert trade.exit_bar_idx == 76
        assert trade.exit_price == 100.2          # the timeout bar's close
        # The oracle counts a timeout as neither a win nor a loss.
        agg = driver.result().overall
        assert (agg.trades, agg.wins, agg.losses, agg.timeouts) == (1, 0, 0, 1)

    def test_3_a_wick_through_the_distal_while_resting_forbids_any_later_fill(self):
        rows = SHORT_ROWS[:4] + [
            (4, 100.0, 105.5, 99.5, 100.0),    # high 105.5 >= distal 105.0
            (5, 100.0, 101.0, 99.5, 100.0),    # would have touched entry 100.5
            (6, 100.0, 101.0, 99.5, 100.0),
        ]
        driver, evaluations = _drive({"BTCUSD": _c(rows)})
        assert _bars_of(evaluations, ET.INVALIDATED, SHORT_OB_ID) == [4]
        assert _bars_of(evaluations, ET.ENTRY_FILLED, SHORT_OB_ID) == []
        assert not [t for t in driver.trades if t.ob_id == SHORT_OB_ID]
        assert driver.result().invalidations >= 1

    def test_4_a_pre_displacement_entry_touch_is_diagnostic_only(self):
        driver, evaluations = _drive({"BTCUSD": _c(SHORT_ROWS)})
        touches = _bars_of(evaluations, ET.PRE_DISPLACEMENT_TOUCH, SHORT_OB_ID)
        assert touches == [2]                    # bar 2 high 101.0 >= 100.5
        fills = _bars_of(evaluations, ET.ENTRY_FILLED, SHORT_OB_ID)
        assert all(f > max(touches) for f in fills)
        trade = _one(list(driver.trades), "trade")
        assert trade.pre_displacement_touches == 1
        assert trade.retest_number == 1          # the touch is not a retest

    def test_5_one_origin_produces_one_setup_forever(self):
        rows = [
            (0, 100.0, 106.0, 99.0, 105.0),      # bullish origin
            (1, 104.0, 104.5, 97.0, 98.0),       # BOS: close < 99.0
            (2, 98.0, 98.5, 96.0, 96.5),         # closes lower still
            (3, 96.5, 97.0, 95.0, 95.5),         # and lower again
        ]
        _d, evaluations = _drive({"BTCUSD": _c(rows)})
        created = [(a, b) for (a, b, t) in _events(evaluations)
                   if t is ET.OB_CREATED]
        assert created == [("BTCUSD", 1)]

    def test_6_the_lookback_window_is_exactly_ten_bars(self):
        origin = (0, 100.0, 106.0, 99.0, 105.0)
        doji = lambda b: (b, 102.0, 103.0, 101.0, 102.0)   # noqa: E731
        bos = lambda b: (b, 100.0, 100.5, 96.0, 98.0)      # noqa: E731
        assert ManualSpecConfig().lookback == 10

        inside = [origin] + [doji(b) for b in range(1, 10)] + [bos(10)]
        _d, evaluations = _drive({"BTCUSD": _c(inside)})
        assert [(a, b) for (a, b, t) in _events(evaluations)
                if t is ET.OB_CREATED] == [("BTCUSD", 10)]

        outside = [origin] + [doji(b) for b in range(1, 11)] + [bos(11)]
        _d, evaluations = _drive({"BTCUSD": _c(outside)})
        assert [t for (_a, _b, t) in _events(evaluations)
                if t is ET.OB_CREATED] == []

    def test_7_the_golden_trade_runs_through_this_same_driver(self):
        """
        Section 10's seventh case is section 5's trade, and it is asserted in
        full by `TestGoldenBTCTradeThroughTheRealBacktestPath`. This is the
        cross-reference: the same driver class, the same single call site.
        """
        assert hasattr(ManualSMCBacktest, "iter_run")
        assert "evaluate_closed_candle" in MODULE_SRC


# ===========================================================================
# §11 — quantization is reporting only
# ===========================================================================
#: Everything a Manual SMC rule decides. If any of these moved because a tick
#: grid was supplied, the behavioural baseline and the executable baseline
#: would have been silently mixed together.
BEHAVIOURAL_FIELDS = (
    "asset", "direction", "ob_id", "origin_bar_idx", "bos_bar_idx",
    "fill_bar_idx", "exit_bar_idx", "ob_top", "ob_bottom", "ob_width",
    "proximal", "distal", "entry_price", "sl_price", "tp_price", "exit_price",
    "risk_dist", "reward_dist", "sl_dist_pct", "theoretical_leverage",
    "applied_leverage", "leverage_clamped", "position_notional", "fees_usd",
    "gross_pnl_usd", "net_pnl_usd", "ending_capital", "return_pct",
    "realized_r", "outcome", "reason_for_exit", "is_ambiguous",
    "holding_bars", "entry_bar_from_bos", "retest_number",
    "pre_displacement_touches",
)


def _behaviour(result: BacktestResult):
    return [tuple(getattr(t, f) for f in BEHAVIOURAL_FIELDS)
            for t in result.trades]


class TestQuantizationIsReportingOnly:

    @pytest.fixture(scope="class")
    @staticmethod
    def pair():
        data = _lock_data()
        plain = _run_oracle(data, symbols=LOCK_SYMBOLS)
        ticked = _run_oracle(
            _lock_data(), symbols=LOCK_SYMBOLS,
            tick_specs=_specs("BTCUSD", "ETHUSD"))
        return plain, ticked

    def test_a_tick_grid_changes_no_trade_and_no_statistic(self, pair):
        plain, ticked = pair
        assert _behaviour(plain) == _behaviour(ticked)
        assert plain.overall == ticked.overall
        assert plain.ending_capital == ticked.ending_capital
        assert plain.asset_breakdown == ticked.asset_breakdown

    def test_the_only_difference_is_the_reported_quantization_state(self, pair):
        plain, ticked = pair
        assert [t.quantized_at_fill for t in plain.trades] == [False, False]
        assert [t.quantized_at_fill for t in ticked.trades] == [True, True]
        assert plain.fills_with_quantized_bracket == 0
        assert ticked.fills_with_quantized_bracket == len(ticked.trades)

    def test_a_missing_product_specification_is_reported_not_raised(self, pair):
        plain, _ticked = pair
        assert plain.quantization_refusals
        assert all("no product specification" in r
                   for r in plain.quantization_refusals)
        assert all(t.quantization_refusal for t in plain.trades)

    def test_a_partially_specified_portfolio_still_runs(self):
        result = _run_oracle(
            _lock_data(), symbols=LOCK_SYMBOLS, tick_specs=_specs("BTCUSD"))
        by_asset = {t.asset: t for t in result.trades}
        assert by_asset["BTCUSD"].quantized_at_fill is True
        assert by_asset["ETHUSD"].quantized_at_fill is False
        assert _behaviour(result) == _behaviour(
            _run_oracle(_lock_data(),
                                                 symbols=LOCK_SYMBOLS))

    def test_the_strategy_prices_stay_off_the_tick_grid_when_the_rule_says_so(self):
        """
        The golden BTC take profit 78373.6695 is NOT a multiple of the 0.5
        tick. Step 8 section 11 forbids moving it to make it executable, so the
        strategy price must remain exactly what the 0.60% rule produced.
        """
        assert (Decimal("78373.6695") % REAL_TICKS["BTCUSD"]) != 0
        dataset = _golden_dataset()
        start = dataset["BTCUSD"][GOLDEN_WINDOW_START_BAR].ts
        driver = ManualSMCBacktest(symbols=("BTCUSD",),
                                   tick_specs=_specs("BTCUSD"), **ORACLE_KW)
        driver.run(build_timeline(dataset, ("BTCUSD",), start_date=start),
                   dataset)
        trade = _one([t for t in driver.trades if t.ob_id == GOLDEN_OB_ID],
                     "golden trade")
        assert trade.tp_price == GOLDEN_TP
        assert trade.quantized_at_fill is True

    def test_no_order_quantity_is_ever_invented(self):
        assert "quantity" not in {f for f in BacktestTrade.__dataclass_fields__}
        assert not any("quantity" in f
                       for f in BacktestTrade.__dataclass_fields__)


# ===========================================================================
# §6 — the aggregation the baseline is reported with
# ===========================================================================
class TestAggregateSemanticsMatchTheOracle:
    """
    The oracle's `_agg`, expression for expression, so the corrected baseline
    is comparable with the published 2,022-trade / +461.99 R one.
    """

    def test_an_empty_ledger_aggregates_to_zeroes(self):
        assert aggregate([]) == Aggregate(
            trades=0, wins=0, losses=0, timeouts=0, ambiguous=0,
            win_rate_pct=0.0, total_r=0.0, expectancy_r=0.0,
            profit_factor=0.0, classified=0)

    def test_only_filled_tp_counts_as_a_win_and_filled_sl_as_a_loss(self):
        rows = SHORT_ROWS[:5] + [
            (b, 100.2, 100.4, 100.0, 100.2) for b in range(5, 78)]
        agg = _run_oracle(
            {"BTCUSD": _c(rows)}, symbols=("BTCUSD",)).overall
        assert (agg.wins, agg.losses, agg.timeouts) == (0, 0, 1)
        assert agg.trades == 1
        assert agg.win_rate_pct == 0.0
        # wins == 0 -> gain_r 0.0; losses == 0 -> loss_r defaults to 1.0.
        assert agg.profit_factor == 0.0
        assert agg.classified == 1

    def test_win_rate_total_r_and_expectancy_use_the_oracle_rounding(self):
        result = _run_oracle(_lock_data(),
                                                      symbols=LOCK_SYMBOLS)
        agg = result.overall
        raw = sum(t.realized_r for t in result.trades)
        assert agg.total_r == round(raw, 2)
        assert agg.expectancy_r == round(raw / agg.trades, 4)
        assert agg.win_rate_pct == round(agg.wins / agg.trades * 100, 2)
        assert agg.average_r == agg.expectancy_r

    def test_the_asset_breakdown_partitions_the_trades(self):
        result = _run_oracle(_lock_data(),
                                                      symbols=LOCK_SYMBOLS)
        assert sorted(result.asset_breakdown) == list(sorted(LOCK_SYMBOLS))
        assert sum(a.trades for a in result.asset_breakdown.values()) == \
            result.overall.trades

    def test_the_oracle_shaped_dict_carries_the_documented_keys(self):
        result = _run_oracle(_lock_data(),
                                                      symbols=LOCK_SYMBOLS)
        as_dict = result.as_oracle_dict()
        for key in ("total_executed_trades", "wins", "losses", "win_rate_pct",
                    "total_realized_r", "expectancy_r", "profit_factor",
                    "starting_capital", "ending_capital", "total_return_pct",
                    "max_drawdown_pct", "asset_breakdown", "config"):
            assert key in as_dict
        assert "trades_df" not in as_dict          # no pandas in this package
        assert as_dict["total_executed_trades"] == result.overall.trades

    def test_the_cumulative_r_column_is_a_running_total(self):
        result = _run_oracle(_lock_data(),
                                                      symbols=LOCK_SYMBOLS)
        running = 0.0
        for trade in result.trades:
            running += trade.realized_r
            assert trade.cumulative_realized_r == pytest.approx(running)
        assert [t.trade_id for t in result.trades] == list(
            range(1, len(result.trades) + 1))


# ===========================================================================
# §13 — the import boundary
# ===========================================================================
ENGINE_DIR = Path(__file__).parent.parent


def _probe(source: str):
    proc = subprocess.run([sys.executable, "-c", source],
                          capture_output=True, text=True, cwd=str(ENGINE_DIR))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestImportBoundary:
    """
    Subprocess proofs. Importing a submodule binds it on the parent package, so
    an in-process check would report whatever an earlier test already imported.
    """

    def test_importing_the_driver_does_not_pull_in_the_adapter(self):
        added = _probe(
            "import sys, json\n"
            "import quantedge\n"
            "base = set(sys.modules)\n"
            "import quantedge.strategy.manual_smc.backtest as b\n"
            "print(json.dumps({\n"
            "  'adapter': 'quantedge.strategy.manual_smc.adapter' in sys.modules,\n"
            "  'has_attr': hasattr(quantedge.strategy.manual_smc, 'adapter'),\n"
            "  'live': b.LIVE_EXECUTION_AUTHORIZED,\n"
            "  'added': sorted(set(sys.modules) - base),\n"
            "}))\n")
        assert added["adapter"] is False
        assert added["has_attr"] is False
        assert added["live"] is False

    def test_importing_the_driver_pulls_in_no_transport_database_or_runtime(self):
        added = _probe(
            "import sys, json\n"
            "import quantedge\n"
            "base = set(sys.modules)\n"
            "import quantedge.strategy.manual_smc.backtest\n"
            "print(json.dumps(sorted(set(sys.modules) - base)))\n")
        for banned in ("httpx", "cryptography", "psycopg", "psycopg2",
                       "sqlalchemy", "requests", "boto3", "socket", "pandas",
                       "numpy", "websockets", "asyncio"):
            assert not [m for m in added if m.split(".")[0] == banned], banned
        assert [m for m in added if m.startswith("quantedge.execution")] == []
        assert [m for m in added if m.startswith("quantedge.backend")] == []

    def test_importing_the_package_still_does_not_import_the_adapter(self):
        state = _probe(
            "import sys, json\n"
            "import quantedge.strategy.manual_smc as pkg\n"
            "print(json.dumps({\n"
            "  'adapter': 'quantedge.strategy.manual_smc.adapter' in sys.modules,\n"
            "  'has_attr': hasattr(pkg, 'adapter'),\n"
            "  'exports': [n for n in pkg.__all__ if 'Adapter' in n\n"
            "              or n.startswith('adapt')],\n"
            "  'backtest': 'quantedge.strategy.manual_smc.backtest' in sys.modules,\n"
            "}))\n")
        assert state == {"adapter": False, "has_attr": False, "exports": [],
                         "backtest": False}

    def test_the_driver_does_not_run_anything_on_import(self):
        """No CLI, no `__main__` side effect, no filesystem read at import."""
        assert not [n for n in MODULE_AST.body
                    if isinstance(n, ast.If)
                    and ast.dump(n.test).find("__main__") != -1]
        assert "argparse" not in MODULE_SRC
        assert "if __name__" not in MODULE_SRC


# ===========================================================================
# §12 / §14 — the Step 8 scope marker
# ===========================================================================
STEP_8_MODULES = (
    "__init__.py", "adapter.py", "backtest.py", "geometry.py", "lifecycle.py",
    "models.py", "portfolio.py", "quantization.py", "scanner.py", "sizing.py",
    "state.py", "strategy.py",
)
#: Phase 2's quantization half added the executable-baseline reporting layer.
#: The whole-package inventory is pinned once, in
#: `test_manual_smc_executable.py::TestPackageInventory`.
PHASE_2_MODULES = ("executable.py",)


class TestStep8ScopeMarker:

    def test_the_package_now_holds_exactly_the_step_8_modules(self):
        """
        Every Step 8 module is present, and everything added since is a
        NON-STRATEGY layer above the driver rather than a new rule owner.

        The Step 8 form asserted equality with `STEP_8_MODULES`. Phase 2 added
        `executable.py`, so the assertion is converted to the requirement it
        encoded — no module may appear here that Step 8 or a later approved step
        did not put there — instead of being deleted.
        """
        present = tuple(sorted(p.name for p in PACKAGE.glob("*.py")))
        assert set(STEP_8_MODULES) <= set(present)
        assert set(present) - set(STEP_8_MODULES) == set(PHASE_2_MODULES)

    def test_the_package_docstring_documents_the_backtest_module(self):
        import quantedge.strategy.manual_smc as pkg
        assert "Phase 1 Step 8 scope: `backtest.py`" in pkg.__doc__
        assert "NOT YET IMPLEMENTED" not in pkg.__doc__

    def test_the_driver_is_strategy_layer_code_not_runtime_code(self):
        assert MODULE_PATH.parent.name == "manual_smc"
        assert MODULE_PATH.parts[-3:] == ("strategy", "manual_smc",
                                          "backtest.py")

    def test_the_public_surface_is_declared(self):
        for name in ("ManualSMCBacktest", "BacktestResult", "BacktestTrade",
                     "EntryBlock", "Aggregate", "Candle", "TimelineRow",
                     "run_manual_smc_backtest",
                     "run_manual_smc_backtest_from_candles",
                     "LIVE_EXECUTION_AUTHORIZED"):
            assert name in bt.__all__, name
            assert hasattr(bt, name), name

    def test_the_csv_entry_point_exists_without_being_called_here(self):
        """
        `run_manual_smc_backtest` reads the real dataset and is exercised by
        `TestGoldenBTCTradeThroughTheRealBacktestPath` through the driver it
        wraps. This only pins its signature so a caller cannot be surprised.
        """
        import inspect
        params = list(inspect.signature(run_manual_smc_backtest).parameters)
        assert params == ["data_base_dir", "config", "symbols", "start_date",
                          "end_date", "tick_specs", "registry", "account_id",
                          "activation_mode", "entry_window_candles"]
        assert all(p.default is not inspect.Parameter.empty
                   for p in inspect.signature(
                       run_manual_smc_backtest).parameters.values())
