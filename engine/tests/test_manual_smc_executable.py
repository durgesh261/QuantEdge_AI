"""
Manual SMC — Phase 2 (quantization half): the exchange-executable baseline.
==========================================================================

Step 8 section 11 required that "the ideal strategy baseline and the
exchange-executable baseline must remain distinguishable". Step 8 delivered the
separation; this file covers the MEASUREMENT of the gap between them,
`quantedge.strategy.manual_smc.executable`.

What this file asserts:

  §A  Governance: the measurement never applies quantization to strategy
      geometry, never re-simulates timing, and computes no order quantity.
      Each flag is asserted False AND proven load-bearing by flipping it.
  §B  It duplicates NO logic. Proven by AST: it defines no quantizer, no
      lifecycle method, no sizing function and no aggregator, it re-quantizes
      nothing, and it restates no Manual SMC constant. It CALLS the shared
      `sizing` and `backtest.aggregate` implementations instead.
  §C  The measurement is correct on the SHORT fixture and on its LONG mirror,
      leg by leg, against named numbers.
  §D  The two baselines are reported as two things, over the same measured
      subset, by the same single aggregation function.
  §E  It fails closed: a missing bracket is missing COVERAGE, never a guessed
      tick size; a bracket that disagrees with its ledger row is refused.
  §F  It mutates nothing — the Step 8 result is unchanged and remeasuring is
      idempotent.
  §G  Package inventory and the import boundary, including the whole-package
      module list that Step 8 used to pin.
"""

from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quantedge.strategy.manual_smc import executable as ex
from quantedge.strategy.manual_smc.backtest import (
    Aggregate,
    ManualSMCBacktest,
    aggregate,
    build_timeline,
    candles_from_ohlc,
)
from quantedge.strategy.manual_smc.lifecycle import ACTIVATION_MODE_ORACLE_C
from quantedge.strategy.manual_smc.executable import (
    APPLIES_TO_STRATEGY_GEOMETRY,
    ORDER_QUANTITY_IS_COMPUTED,
    TIMING_IS_RESIMULATED,
    ExecutableBaseline,
    ExecutableDataError,
    ExecutableGovernanceError,
    ExecutableTrade,
    LegDivergence,
    format_baseline_comparison,
    measure_executable_divergence,
    measure_trade,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.quantization import (
    PriceRole,
    TickRounding,
    conservative_rounding,
)

MODULE_PATH = Path(ex.__file__)
MODULE_SRC = MODULE_PATH.read_text(encoding="utf-8")
MODULE_AST = ast.parse(MODULE_SRC)
PACKAGE = MODULE_PATH.parent

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
BTC_TICK = Decimal("0.5")


def _ts(bar_idx: int) -> datetime:
    return BASE + timedelta(hours=bar_idx)


class FakeSpec:
    """The minimal structural `TickSizeSpec`, as Step 4 and Step 8 use it."""

    def __init__(self, tick_size: Decimal) -> None:
        self.tick_size = tick_size


# The Step 8 fixtures, verbatim, so this file measures the SAME trades the
# driver's own suite proves the semantics of rather than inventing new ones.
SHORT_ROWS = [
    (0, 100.0, 106.0, 99.0, 105.0),
    (1, 104.0, 104.5, 97.0, 98.0),
    (2, 98.0, 101.0, 97.5, 100.0),
    (3, 100.0, 100.2, 98.0, 98.5),
    (4, 99.6, 101.0, 99.5, 100.0),
    (5, 100.0, 100.5, 99.5, 99.8),
]
LONG_ROWS = [
    (0, 105.0, 106.0, 99.0, 100.0),
    (1, 101.0, 107.5, 100.5, 107.0),
    (2, 107.0, 107.2, 104.0, 105.0),
    (3, 105.0, 107.0, 104.8, 106.5),
    (4, 105.5, 106.0, 104.0, 105.0),
    (5, 105.0, 105.2, 104.9, 105.1),
]


# The Step 8 fixtures are Mode-C scripts carrying the oracle's 0.60% take
# profit, and every named number below (99.897, 105.127, the R deltas) is that
# TP quantized. `ManualSMCBacktest` now defaults to the production policy, so the
# two oracle keywords are named here to keep the measured trades identical to the
# ones Step 8 proved the semantics of — this file measures a quantization gap,
# not an activation rule.
ORACLE_KW = {
    "activation_mode": ACTIVATION_MODE_ORACLE_C,
    "config": ManualSpecConfig(),
}


def _run(rows, tick=BTC_TICK, symbol="BTCUSD"):
    """Drive the Step 8 backtest over one fixture and return its result."""
    data = {symbol: candles_from_ohlc(rows, _ts)}
    specs = None if tick is None else {symbol: FakeSpec(tick)}
    driver = ManualSMCBacktest(symbols=(symbol,), tick_specs=specs,
                               **ORACLE_KW)
    timeline = build_timeline(data, (symbol,))
    list(driver.iter_run(timeline, data))
    return driver.result()


def _measured(rows, **kwargs):
    result = _run(rows, **kwargs)
    baseline = measure_executable_divergence(result)
    return result, baseline


# ===========================================================================
# §A — governance
# ===========================================================================
class TestGovernance:

    def test_the_three_flags_are_all_false(self):
        assert APPLIES_TO_STRATEGY_GEOMETRY is False
        assert TIMING_IS_RESIMULATED is False
        assert ORDER_QUANTITY_IS_COMPUTED is False

    @pytest.mark.parametrize("flag", ["APPLIES_TO_STRATEGY_GEOMETRY",
                                      "TIMING_IS_RESIMULATED",
                                      "ORDER_QUANTITY_IS_COMPUTED"])
    def test_flipping_any_flag_stops_the_measurement(self, flag, monkeypatch):
        """
        Each flag is load-bearing, not decoration. A future edit that turns one
        on gets an exception at every entry point rather than a quietly
        different report.
        """
        result, _ = _measured(SHORT_ROWS)
        monkeypatch.setattr(ex, flag, True)
        with pytest.raises(ExecutableGovernanceError, match=flag):
            measure_executable_divergence(result)
        with pytest.raises(ExecutableGovernanceError, match=flag):
            measure_trade(result.trades[0], result.config)

    def test_the_formatter_is_gated_too(self, monkeypatch):
        _, baseline = _measured(SHORT_ROWS)
        monkeypatch.setattr(ex, "APPLIES_TO_STRATEGY_GEOMETRY", True)
        with pytest.raises(ExecutableGovernanceError):
            format_baseline_comparison(baseline)

    def test_no_reported_field_is_an_order_quantity(self):
        """
        Safety rules #8/#15/#16: Delta's contract value is unverified, so there
        is nowhere in this report for a quantity to appear.

        `tick_size` is the ONE permitted "size" — it is a price grid, not an
        amount of anything — and it is allowed by exact name so that a future
        `position_size` or `order_size` still fails here.
        """
        banned = ("quantity", "qty", "contracts", "lots", "contract_value",
                  "product_id", "_size", "size_")
        for cls in (LegDivergence, ExecutableTrade, ExecutableBaseline):
            names = {f.name for f in dataclasses.fields(cls)}
            names |= {n for n in vars(cls)
                      if isinstance(vars(cls)[n], property)}
            names -= {"tick_size", "max_ticks_moved"}
            for bad in banned:
                assert not [n for n in names if bad in n], (cls.__name__, bad)
            assert "size" not in names

    def test_no_capital_field_is_reported(self):
        """
        An executable equity curve would need a sequential re-run at a
        different leverage, which is the driver's job and not a measurement.
        R, prices, distances, risk % and leverage only.
        """
        banned = ("notional", "fee", "pnl", "balance", "capital", "margin",
                  "return_pct", "drawdown")
        for cls in (LegDivergence, ExecutableTrade, ExecutableBaseline):
            names = {f.name for f in dataclasses.fields(cls)}
            for bad in banned:
                assert not [n for n in names if bad in n], (cls.__name__, bad)

    def test_every_reported_dataclass_is_frozen(self):
        for cls in (LegDivergence, ExecutableTrade, ExecutableBaseline):
            assert cls.__dataclass_params__.frozen, cls.__name__

    def test_the_module_does_not_authorise_or_touch_execution(self):
        """
        No name in this module reaches an exchange, a transport or a CLI.

        Checked over IDENTIFIERS (names, attributes, imports, parameters), not
        raw text, because the module's own docstring lists what it deliberately
        does not do — "no cancellation, no amendment" is a promise, and a text
        search cannot tell a promise from a call.
        """
        idents = set()
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Name):
                idents.add(node.id)
            elif isinstance(node, ast.Attribute):
                idents.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                idents.add(node.name)
            elif isinstance(node, ast.arg):
                idents.add(node.arg)
            elif isinstance(node, ast.alias):
                idents.add(node.name)
                idents.add(node.asname or "")
            elif isinstance(node, ast.ImportFrom):
                idents.add(node.module or "")
        idents -= {"ORDER_QUANTITY_IS_COMPUTED"}
        for banned in ("place_order", "order_", "_order", "submit", "cancel",
                       "amend", "httpx", "requests", "urllib", "psycopg",
                       "sqlalchemy", "socket", "subprocess", "argparse",
                       "boto3", "delta_", "client"):
            hits = sorted(n for n in idents if banned in n.lower())
            assert not hits, (banned, hits)
        assert "if __name__" not in MODULE_SRC


# ===========================================================================
# §B — it duplicates nothing
# ===========================================================================
class TestNoDuplicatedLogic:
    """
    The whole point of the module is that it OWNS no rule. These read the AST,
    not the docstring, for the same reason Step 8's own contract tests do.
    """

    #: Any of these as a DEFINITION here would be a second implementation.
    FORBIDDEN_DEFS = (
        "quantize_price", "quantize_bracket", "quantize_ob_bracket",
        "conservative_rounding", "compute_leverage", "compute_sl_dist_pct",
        "size_position", "settle_trade", "realized_r_for_outcome",
        "return_pct_for_outcome", "aggregate", "process_candle",
        "evaluate_closed_candle", "_make_manual_ob", "make_manual_ob",
        "_entry_blocked", "try_acquire", "release", "scan", "candidate_obs",
        "_step1_resolve_active_trade", "_step2_update_obs",
        "_step3_scan_and_admit",
    )

    def _defs(self):
        return {n.name for n in ast.walk(MODULE_AST)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def _calls(self):
        names = set()
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
        return names

    def test_it_defines_no_shared_rule(self):
        clash = self._defs() & set(self.FORBIDDEN_DEFS)
        assert not clash, f"executable.py redefines {sorted(clash)}"

    def test_it_never_quantizes_a_price_itself(self):
        """
        The on-grid bracket is the one `strategy.py` computed at fill. If this
        module called a quantizer it could disagree with the run it is
        measuring, and the divergence would be its own artefact.
        """
        calls = self._calls()
        for banned in ("quantize_price", "quantize_bracket",
                       "quantize_ob_bracket", "price_from_strategy_float",
                       "conservative_rounding", "tick_size_of"):
            assert banned not in calls, banned

    def test_it_calls_the_shared_sizing_and_aggregation_implementations(self):
        calls = self._calls()
        for required in ("compute_sl_dist_pct", "compute_leverage",
                         "realized_r_for_outcome", "aggregate", "replace"):
            assert required in calls, required

    def test_it_restates_no_manual_smc_constant(self):
        """
        No entry depth, TP percentage, risk budget, leverage cap, fee rate,
        timeout horizon or minimum OB width may appear as a literal here. Every
        one of them arrives inside the `ManualSpecConfig` it is handed.
        """
        for banned in ("0.25", "0.60", "0.6", "35.0", "35 ", "0.0008",
                       "72", "1e-6", "99.0", "0.08"):
            assert banned not in MODULE_SRC, banned

    def test_the_risk_budget_comes_from_the_config_object(self):
        _, baseline = _measured(SHORT_ROWS)
        cfg = ManualSpecConfig(max_sl_account_risk_pct=20.0)
        other = measure_executable_divergence(
            dataclasses.replace(_run(SHORT_ROWS), config=cfg))
        assert baseline.trades[0].budget_limit_pct == 35.0
        assert other.trades[0].budget_limit_pct == 20.0

    def test_it_imports_nothing_outside_the_manual_smc_package(self):
        imported = set()
        for node in ast.walk(MODULE_AST):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        foreign = {m for m in imported if m.startswith("quantedge.")
                   and not m.startswith("quantedge.strategy.manual_smc")}
        assert foreign == set(), foreign
        assert "pandas" not in imported
        assert not any(m.startswith("quantedge.execution")
                       or m.startswith("quantedge.runtime")
                       or m.startswith("quantedge.strategy.smc")
                       for m in imported)

    def test_it_sits_above_the_driver_and_the_driver_never_reads_it(self):
        """
        The dependency direction is executable -> backtest, one way. If the
        driver imported this module, a reporting layer would become a runtime
        dependency of the thing it reports on.
        """
        driver_src = (PACKAGE / "backtest.py").read_text(encoding="utf-8")
        driver_imports = set()
        for node in ast.walk(ast.parse(driver_src)):
            if isinstance(node, ast.ImportFrom):
                driver_imports.add(node.module or "")
        assert ("quantedge.strategy.manual_smc.executable"
                not in driver_imports)
        assert "executable" not in {m.rsplit(".", 1)[-1]
                                    for m in driver_imports}


# ===========================================================================
# §C — the measurement itself, against named numbers
# ===========================================================================
class TestShortFixtureDivergence:
    """
    Step 8's SHORT trade: entry 100.5, SL 105.0, TP 99.897, BTC tick 0.5.

    Entry and SL are already exact multiples of 0.5. The TP is not, and a SHORT
    take profit rounds UP (toward the entry, i.e. the conservative direction),
    so 99.897 -> 100.0. Risk is untouched at 4.5 and reward shrinks from 0.603
    to 0.5, which is the whole executable cost of this trade.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def measured():
        return _measured(SHORT_ROWS)

    def test_the_fixture_produced_exactly_one_measurable_trade(self, measured):
        result, baseline = measured
        assert result.overall.trades == 1
        assert baseline.trades_total == 1
        assert baseline.trades_measured == 1
        assert baseline.trades_unmeasurable == 0
        assert baseline.unmeasurable_reasons == ()
        assert baseline.coverage_pct == 100.0

    def test_the_entry_and_stop_were_already_on_the_grid(self, measured):
        _, baseline = measured
        trade = baseline.trades[0]
        assert trade.tick_size == BTC_TICK
        assert trade.entry.ideal == Decimal("100.5")
        assert trade.entry.executable == Decimal("100.5")
        assert trade.entry.already_on_grid
        assert trade.entry.ticks_moved == 0
        assert trade.sl.ideal == Decimal("105.0")
        assert trade.sl.executable == Decimal("105.0")
        assert trade.sl.already_on_grid

    def test_the_take_profit_moved_one_partial_tick_toward_the_entry(
            self, measured):
        _, baseline = measured
        tp = baseline.trades[0].tp
        assert tp.role is PriceRole.TAKE_PROFIT
        assert tp.ideal == Decimal("99.897")
        assert tp.executable == Decimal("100.0")
        assert tp.moved
        assert tp.delta == Decimal("0.103")
        assert tp.ticks_moved == Decimal("0.206")
        assert tp.ticks_moved < 1

    def test_the_rounding_direction_is_the_quantizers_own_choice(self, measured):
        """
        Copied from the recorded bracket, never re-derived. Cross-checked
        against `quantization.conservative_rounding` so a divergence between
        the two would fail here rather than skew a baseline.
        """
        _, baseline = measured
        trade = baseline.trades[0]
        for leg in trade.legs:
            assert leg.rounding is conservative_rounding(leg.role,
                                                         trade.direction)
        assert trade.entry.rounding is TickRounding.UP
        assert trade.sl.rounding is TickRounding.DOWN
        assert trade.tp.rounding is TickRounding.UP

    def test_risk_is_untouched_and_reward_shrinks(self, measured):
        _, baseline = measured
        trade = baseline.trades[0]
        assert trade.ideal_risk_dist == Decimal("4.5")
        assert trade.executable_risk_dist == Decimal("4.5")
        assert trade.ideal_reward_dist == Decimal("0.603")
        assert trade.executable_reward_dist == Decimal("0.5")
        assert trade.risk_shrank

    def test_the_executable_r_is_lower_and_both_are_reported(self, measured):
        _, baseline = measured
        trade = baseline.trades[0]
        assert trade.ideal_realized_r == pytest.approx(0.603 / 4.5)
        assert trade.realized_r == pytest.approx(0.5 / 4.5)
        assert trade.realized_r_delta == pytest.approx(-0.0228888, abs=1e-6)
        assert trade.realized_r < trade.ideal_realized_r

    def test_leverage_is_unchanged_because_the_risk_leg_did_not_move(
            self, measured):
        _, baseline = measured
        trade = baseline.trades[0]
        assert trade.executable_sl_dist_pct == trade.ideal_sl_dist_pct
        assert (trade.executable_applied_leverage
                == trade.ideal_applied_leverage)
        assert trade.budget_used_pct == pytest.approx(35.0)
        assert trade.budget_respected

    def test_the_divergence_counters_agree_with_the_legs(self, measured):
        _, baseline = measured
        assert baseline.legs_moved == 1
        assert baseline.legs_already_on_grid == 2
        assert baseline.trades_with_any_leg_moved == 1
        assert baseline.max_ticks_moved == Decimal("0.206")
        assert baseline.risk_grew_count == 0
        assert baseline.budget_breach_count == 0
        assert baseline.guarantees_hold
        assert baseline.trades[0].legs_moved == 1
        assert baseline.trades[0].any_leg_moved


class TestLongMirrorDivergence:
    """
    The LONG mirror on the SAME code: entry 104.5, SL 100.0, TP 105.127.

    A LONG take profit rounds DOWN toward the entry, so 105.127 -> 105.0. The
    mirror is not a special case anywhere in the measurement; it falls out of
    the recorded bracket's own rounding directions.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def measured():
        return _measured(LONG_ROWS)

    def test_the_mirror_moves_the_take_profit_the_other_way(self, measured):
        _, baseline = measured
        trade = baseline.trades[0]
        assert trade.entry.already_on_grid and trade.sl.already_on_grid
        assert trade.tp.ideal == Decimal("105.127")
        assert trade.tp.executable == Decimal("105.0")
        assert trade.tp.delta == Decimal("-0.127")
        assert trade.tp.ticks_moved == Decimal("0.254")
        assert trade.tp.rounding is TickRounding.DOWN
        assert trade.entry.rounding is TickRounding.DOWN
        assert trade.sl.rounding is TickRounding.UP

    def test_the_mirror_costs_r_in_the_same_direction(self, measured):
        _, baseline = measured
        trade = baseline.trades[0]
        assert trade.ideal_risk_dist == trade.executable_risk_dist
        assert trade.ideal_reward_dist == Decimal("0.627")
        assert trade.executable_reward_dist == Decimal("0.5")
        assert trade.realized_r == pytest.approx(0.5 / 4.5)
        assert trade.realized_r_delta == pytest.approx(-0.0282222, abs=1e-6)
        assert trade.risk_shrank and trade.budget_respected

    def test_a_coarser_grid_costs_more(self, measured):
        """
        A monotonicity check the measurement must reproduce: the same trade on a
        wider tick grid can only lose at least as much reward. It uses the same
        recorded-bracket path, so this is a property of the report, not of a
        second quantizer.
        """
        _, fine = measured
        _, coarse = _measured(LONG_ROWS, tick=Decimal("0.25"))
        assert coarse.trades[0].executable_reward_dist \
            >= fine.trades[0].executable_reward_dist
        assert coarse.trades[0].realized_r >= fine.trades[0].realized_r


# ===========================================================================
# §D — two baselines, reported as two things, by ONE aggregator
# ===========================================================================
class TestTwoBaselinesAreTwoThings:
    """
    Step 8 section 11: "the ideal strategy baseline and the exchange-executable
    baseline must remain distinguishable". Distinguishable means BOTH are
    reported, over the SAME measured subset, and the ideal one is still the
    driver's own untouched number.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def measured():
        return _measured(SHORT_ROWS)

    def test_the_ideal_column_is_literally_the_drivers_own_aggregate(
            self, measured):
        result, baseline = measured
        assert baseline.ideal_overall is result.overall
        assert baseline.strategy_name == MANUAL_SMC_STRATEGY_NAME
        assert baseline.strategy_version == MANUAL_SMC_STRATEGY_VERSION
        assert baseline.config is result.config
        assert baseline.symbols == result.symbols

    def test_both_columns_are_the_same_type_from_the_same_function(
            self, measured):
        _, baseline = measured
        assert isinstance(baseline.ideal_measured, Aggregate)
        assert isinstance(baseline.executable_measured, Aggregate)
        assert baseline.executable_measured == aggregate(baseline.trades)
        assert baseline.ideal_measured is not baseline.executable_measured
        assert baseline.baselines_are_distinguishable

    def test_the_two_columns_cover_the_same_trades_and_outcomes(self, measured):
        """
        Only PRICES differ. Trade count, wins, losses, timeouts and ambiguity
        are the ideal run's, because timing was not re-simulated.
        """
        _, baseline = measured
        ideal, ex_ = baseline.ideal_measured, baseline.executable_measured
        for field in ("trades", "wins", "losses", "timeouts", "ambiguous",
                      "win_rate_pct", "classified"):
            assert getattr(ideal, field) == getattr(ex_, field), field
        assert ex_.total_r != ideal.total_r
        assert ex_.expectancy_r < ideal.expectancy_r

    def test_the_measured_subset_is_the_whole_ledger_when_coverage_is_full(
            self, measured):
        result, baseline = measured
        assert baseline.coverage_pct == 100.0
        assert baseline.ideal_measured == result.overall

    def test_the_per_asset_breakdowns_are_keyed_by_the_run_symbols(
            self, measured):
        result, baseline = measured
        assert set(baseline.ideal_by_asset) == set(result.symbols)
        assert set(baseline.executable_by_asset) == set(result.symbols)
        assert baseline.ideal_by_asset["BTCUSD"] == result.asset_breakdown[
            "BTCUSD"]
        assert (baseline.executable_by_asset["BTCUSD"].trades
                == baseline.ideal_by_asset["BTCUSD"].trades)

    def test_the_deltas_are_reported_and_signed(self, measured):
        """
        `expectancy_r_delta` is a difference of two `aggregate` expectancies,
        and `aggregate` rounds those to 4dp exactly as the oracle's `_agg`
        does — so it is -0.0229 here, not the exact per-trade -0.0228888. The
        per-trade `realized_r_delta` is the unrounded figure.
        """
        _, baseline = measured
        assert baseline.total_r_delta < 0
        assert baseline.expectancy_r_delta == pytest.approx(-0.0229, abs=1e-9)
        assert baseline.trades[0].realized_r_delta == pytest.approx(
            -0.0228888, abs=1e-6)
        assert baseline.worst_budget_used_pct == pytest.approx(35.0)

    def test_the_report_labels_both_columns_and_omits_quantity(self, measured):
        _, baseline = measured
        text = format_baseline_comparison(baseline)
        assert "IDEAL (unquantized strategy geometry)" in text
        assert "EXCHANGE-EXECUTABLE (bracket legs on the tick grid)" in text
        assert "timing is NOT re-simulated" in text
        assert "DIVERGENCE" in text
        assert "ORDER QUANTITY IS ABSENT" in text
        assert f"{MANUAL_SMC_STRATEGY_NAME} / {MANUAL_SMC_STRATEGY_VERSION}" \
            in text
        assert text.isascii(), "the report must survive a cp1252 console"
        for banned in ("quantity:", "qty", "contracts", "notional"):
            assert banned not in text.lower().replace(
                "order quantity is absent", ""), banned


# ===========================================================================
# §E — it fails closed
# ===========================================================================
class TestFailsClosed:
    """
    Rule #15/#16 at the reporting layer: an absent tick size is absent
    COVERAGE. Nothing here may substitute a default grid, and a bracket that
    does not describe the ledger row it is attached to is refused outright.
    """

    def test_no_product_specification_is_missing_coverage_not_a_default_tick(
            self):
        result, baseline = _measured(SHORT_ROWS, tick=None)
        assert result.overall.trades == 1
        assert result.fills_with_quantized_bracket == 0
        assert baseline.trades_total == 1
        assert baseline.trades_measured == 0
        assert baseline.trades_unmeasurable == 1
        assert baseline.coverage_pct == 0.0
        assert baseline.trades == ()
        assert baseline.max_ticks_moved is None
        assert baseline.legs_moved == 0
        assert baseline.legs_already_on_grid == 0
        assert baseline.worst_budget_used_pct == 0.0
        assert len(baseline.unmeasurable_reasons) == 1
        assert "no product specification" in baseline.unmeasurable_reasons[0]
        assert "BTCUSD" in baseline.unmeasurable_reasons[0]

    def test_an_uncovered_ledger_still_reports_the_ideal_baseline(self):
        """
        Zero coverage must not silence the ideal column: the run happened, and
        the report says so while reporting no executable numbers.
        """
        result, baseline = _measured(SHORT_ROWS, tick=None)
        assert baseline.ideal_overall is result.overall
        assert baseline.ideal_measured.trades == 0
        assert baseline.executable_measured.trades == 0
        text = format_baseline_comparison(baseline)
        assert "0.0%" in text or "0.00%" in text

    def test_a_trade_without_a_bracket_measures_to_none(self):
        result, _ = _measured(SHORT_ROWS, tick=None)
        assert measure_trade(result.trades[0], result.config) is None

    def test_partial_coverage_is_reported_as_partial(self):
        result = _run(SHORT_ROWS)
        stripped = dataclasses.replace(result.trades[0],
                                       quantized_bracket=None,
                                       quantized_at_fill=False)
        two = dataclasses.replace(result,
                                  trades=(result.trades[0], stripped))
        baseline = measure_executable_divergence(two)
        assert baseline.trades_total == 2
        assert baseline.trades_measured == 1
        assert baseline.coverage_pct == 50.0

    def test_a_refusal_reason_is_carried_through_verbatim(self):
        result = _run(SHORT_ROWS)
        refused = dataclasses.replace(
            result.trades[0], quantized_bracket=None, quantized_at_fill=False,
            quantization_refusal="tick size is not a positive Decimal")
        baseline = measure_executable_divergence(
            dataclasses.replace(result, trades=(refused,)))
        assert baseline.unmeasurable_reasons == (
            f"{refused.asset} {refused.ob_id}: "
            f"tick size is not a positive Decimal",)

    def _corrupt(self, result, **bracket_fields):
        trade = result.trades[0]
        bracket = dataclasses.replace(trade.quantized_bracket,
                                      **bracket_fields)
        return dataclasses.replace(trade, quantized_bracket=bracket)

    def test_a_raw_price_that_disagrees_with_the_ledger_is_refused(self):
        result = _run(SHORT_ROWS)
        bad = self._corrupt(result, raw_tp_price=Decimal("99.5"))
        with pytest.raises(ExecutableDataError, match="disagrees with the "
                                                     "ledger"):
            measure_trade(bad, result.config)

    def test_a_bracket_for_another_asset_is_refused(self):
        result = _run(SHORT_ROWS)
        bad = self._corrupt(result, asset="ETHUSD")
        with pytest.raises(ExecutableDataError, match="ETHUSD"):
            measure_trade(bad, result.config)

    def test_a_bracket_for_the_other_direction_is_refused(self):
        result = _run(SHORT_ROWS)
        bad = self._corrupt(result, direction="LONG")
        with pytest.raises(ExecutableDataError, match="direction"):
            measure_trade(bad, result.config)

    def test_a_non_decimal_price_is_refused_rather_than_coerced(self):
        """
        The Decimal/float boundary is one-way and explicit. A float sneaking
        into a bracket must stop the measurement, not be silently accepted.
        """
        result = _run(SHORT_ROWS)
        bad = self._corrupt(result, raw_tp_price=99.897)
        with pytest.raises(ExecutableDataError, match="expected a Decimal"):
            measure_trade(bad, result.config)

    def test_a_ledger_r_that_cannot_be_reconstructed_is_refused(self):
        """
        The ideal R is recomputed from the recorded sizing and cross-checked
        against the ledger. If they disagree the two baselines would not be
        measuring the same trade, so no baseline is produced at all.
        """
        result = _run(SHORT_ROWS)
        bad = dataclasses.replace(result.trades[0], realized_r=9.99)
        with pytest.raises(ExecutableDataError, match="IDEAL R"):
            measure_trade(bad, result.config)


# ===========================================================================
# §F — it mutates nothing
# ===========================================================================
class TestPurity:

    def test_the_step_8_result_is_untouched_by_being_measured(self):
        result = _run(SHORT_ROWS)
        before = repr(result)
        bracket = result.trades[0].quantized_bracket
        sizing = result.trades[0].sizing_at_fill
        measure_executable_divergence(result)
        assert repr(result) == before
        assert result.trades[0].quantized_bracket is bracket
        assert result.trades[0].sizing_at_fill is sizing

    def test_remeasuring_is_idempotent(self):
        result = _run(SHORT_ROWS)
        first = measure_executable_divergence(result)
        second = measure_executable_divergence(result)
        assert first == second
        assert repr(format_baseline_comparison(first)) == repr(
            format_baseline_comparison(second))

    def test_the_report_cannot_be_edited_after_the_fact(self):
        _, baseline = _measured(SHORT_ROWS)
        for obj, field in ((baseline, "trades_measured"),
                           (baseline.trades[0], "realized_r"),
                           (baseline.trades[0].tp, "executable")):
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(obj, field, 0)

    def test_measuring_one_trade_does_not_depend_on_the_others(self):
        """
        `measure_trade` is a pure function of (trade, config): the per-trade
        rows inside a full baseline are equal to measuring each trade alone.
        """
        result, baseline = _measured(SHORT_ROWS)
        alone = [measure_trade(t, result.config) for t in result.trades]
        assert list(baseline.trades) == alone


# ===========================================================================
# §G — package inventory and the import boundary
# ===========================================================================
#: The whole package, pinned ONCE here. `test_manual_smc_backtest.py` and
#: `test_manual_smc_adapter.py` were converted to delegate this inventory to
#: this class rather than each carrying a copy that has to be edited by every
#: future step.
PACKAGE_MODULES = (
    "__init__.py", "adapter.py", "backtest.py", "executable.py", "geometry.py",
    "lifecycle.py", "models.py", "portfolio.py", "quantization.py",
    "scanner.py", "sizing.py", "state.py", "strategy.py",
)


class TestPackageInventory:

    def test_the_package_holds_exactly_these_modules(self):
        present = tuple(sorted(p.name for p in PACKAGE.glob("*.py")))
        assert present == PACKAGE_MODULES

    def test_the_package_docstring_declares_the_phase_2_scope(self):
        doc = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
        assert "Phase 2 scope (quantization half)" in doc
        assert "test_manual_smc_executable.py" in doc
        assert "TIMING_IS_RESIMULATED" in doc

    def test_the_module_is_not_re_exported(self):
        """
        Like `adapter.py` and `backtest.py`, the reporting layer is opt-in: a
        caller must name it. `__init__.py` must not pull it in, so importing the
        strategy package can never drag a reporting layer into a runtime.
        """
        from quantedge.strategy import manual_smc as pkg
        assert "executable" not in pkg.__all__
        assert "ExecutableBaseline" not in pkg.__all__
        assert "measure_executable_divergence" not in pkg.__all__
        for name in pkg.__all__:
            assert hasattr(pkg, name), name

    def test_importing_the_package_does_not_import_the_reporting_layer(self):
        """
        Proven in a SUBPROCESS: importing a submodule binds it as an attribute
        of its parent package, so an in-process check would be polluted by this
        test file's own imports.
        """
        code = ("import sys, quantedge.strategy.manual_smc as p;"
                "print([m for m in ('executable', 'backtest', 'adapter')"
                " if f'quantedge.strategy.manual_smc.{m}' in sys.modules])")
        out = subprocess.run([sys.executable, "-c", code],
                             cwd=str(PACKAGE.parents[2]),
                             capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "[]", out.stdout

    def test_the_reporting_layer_is_importable_on_its_own(self):
        code = ("from quantedge.strategy.manual_smc.executable import "
                "measure_executable_divergence as m; print(m.__name__)")
        out = subprocess.run([sys.executable, "-c", code],
                             cwd=str(PACKAGE.parents[2]),
                             capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "measure_executable_divergence"
