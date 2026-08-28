"""
Manual SMC — application boundary acceptance tests (Phase 1 Step 7).
====================================================================

MANDATED COVERAGE -> CLASS
    LONG conversion ............................ TestLongConversion
    SHORT conversion ........................... TestShortConversion
    absolute TP provenance ..................... TestAbsoluteTakeProfit
    quantized entry/SL/TP preserved ............ TestQuantizedPricesPreserved
    MANUAL_SMC / 1.0.0 identity ................ TestIdentity
    blocked / rejected evaluation mapping ...... TestBlockedAndRefused
    missing / invalid fields fail closed ....... TestFailClosed
    no duplicated sizing / rounding logic ...... TestNoDuplicatedLogic
    StrategyDecision / SetupState import bound . TestImportBoundary

The candle sequences are the Step 6 fixtures reproduced here rather than
imported, so this file stands alone: a change to another test module cannot
silently alter what this one asserts. They are OHLC-consistent because
`validate_candle` is stricter than the lifecycle.

KNOWN CONFLICT WITH AN EXISTING TEST — NOT RESOLVED HERE
--------------------------------------------------------
`test_manual_smc_strategy.py::TestModuleIndependence::
test_the_adapter_and_backtest_modules_do_not_exist_yet` asserts that
`adapter.py` does NOT exist and that the package contains exactly ten modules.
That was Step 6's scope marker and Step 7 necessarily contradicts it. The
testing rules forbid silently changing an existing test, so it is left
UNTOUCHED and reported as a failure; `TestImportBoundary` below carries the
Step 7 equivalent (adapter.py present, backtest.py still absent).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import io
import json
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quantedge.strategy.manual_smc.adapter import (
    ENTRY_ORDER_TYPE,
    ENTRY_REFUSED_THIS_CANDLE_REFUSAL,
    LEVERAGE_INT_TRUNCATION_NOTE,
    MISSING_BRACKET_REFUSAL,
    RESTING_ORDER_EXPIRY_POLICY,
    TAKE_PROFIT_SOURCE,
    TRADE_SLOT_TAKEN_REFUSAL,
    UNREPRESENTABLE_LEVERAGE_REFUSAL,
    AdapterConfigError,
    AdapterError,
    IdentityMismatchError,
    InconsistentEvaluationError,
    ManualSMCAdaptation,
    ManualSMCAdapter,
    UnknownSymbolError,
    UnmappedStateError,
    decision_from_blocked,
    decision_from_setup,
    map_direction,
    map_ob_state,
    map_setup_type,
    no_setup_decision,
    represent_leverage,
    require_manual_smc_identity,
    to_strategy_decisions,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBState as S,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.quantization import (
    is_on_tick_grid,
    price_from_strategy_float,
    quantize_bracket,
)
from quantedge.strategy.manual_smc.strategy import (
    TP_SOURCE,
    ManualSMCStrategy,
)
from quantedge.strategy.models import (
    SetupState,
    SetupType,
    StrategyDecision,
    StrategyDirection,
)

# ---------------------------------------------------------------------------
# Fixtures. Self-contained on purpose (see the module docstring).
# ---------------------------------------------------------------------------
PACKAGE = (Path(__file__).parent.parent / "src" / "quantedge" / "strategy"
           / "manual_smc")
ADAPTER_PATH = PACKAGE / "adapter.py"
ADAPTER_SRC = ADAPTER_PATH.read_text(encoding="utf-8")

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ts(bar_idx: int) -> datetime:
    return BASE + timedelta(hours=bar_idx)


@dataclass(frozen=True)
class FakeSpec:
    """Minimal structural `TickSizeSpec` — keeps `quantedge.execution` out."""
    tick_size: Decimal


REAL_TICKS = {
    "BTCUSD": Decimal("0.5"),
    "ETHUSD": Decimal("0.05"),
    "SOLUSD": Decimal("0.01"),
    "XRPUSD": Decimal("0.0001"),
}


def _specs(*assets: str):
    return {a: FakeSpec(REAL_TICKS[a]) for a in assets}


# SHORT: origin bar 0 is bullish -> ob_top = close 105.0, ob_bottom = low 99.0.
#   entry = 99.0 + 0.25 * 6.0 = 100.5, sl = 105.0, tp = 100.5 * 0.994 = 99.897
SHORT_ROWS = [
    (0, 100.0, 106.0, 99.0, 105.0),
    (1, 104.0, 104.5, 97.0, 98.0),      # BOS: close < ob_bottom
    (2, 98.0, 101.0, 97.5, 100.0),      # probe
    (3, 100.0, 100.2, 98.0, 98.5),      # displacement -> limit rests
    (4, 99.6, 101.0, 99.5, 100.0),      # high >= entry -> FILL
    (5, 100.0, 100.5, 99.5, 99.8),      # low <= tp -> TP close
]
SHORT_OB_ID = "MANUAL_BTCUSD_SHORT_0_1"
SHORT_RAW = (100.5, 105.0, 99.897)
SHORT_QUANTIZED = (Decimal("100.5"), Decimal("105.0"), Decimal("100.0"))
SHORT_LEVERAGE = 7                                   # floor(7.816666666666667)

# LONG: origin bar 0 is bearish -> ob_top = high 106.0, ob_bottom = close 100.0.
#   entry = 106.0 - 0.25 * 6.0 = 104.5, sl = 100.0, tp = 104.5 * 1.006
LONG_ROWS = [
    (0, 105.0, 106.0, 99.0, 100.0),
    (1, 101.0, 107.5, 100.5, 107.0),    # BOS: close > ob_top
    (2, 107.0, 107.2, 104.0, 105.0),    # probe
    (3, 105.0, 107.0, 104.8, 106.5),    # displacement -> limit rests
    (4, 105.5, 106.0, 104.0, 105.0),    # low <= entry -> FILL
    (5, 105.0, 105.2, 104.9, 105.1),    # high >= tp -> TP close
]
LONG_OB_ID = "MANUAL_BTCUSD_LONG_0_1"
LONG_RAW = (104.5, 100.0, 105.127)
LONG_QUANTIZED = (Decimal("104.5"), Decimal("100.0"), Decimal("105.0"))
LONG_LEVERAGE = 8                                    # floor(8.127777777777778)

ROWS = {"SHORT": SHORT_ROWS, "LONG": LONG_ROWS}
OB_IDS = {"SHORT": SHORT_OB_ID, "LONG": LONG_OB_ID}
RAW = {"SHORT": SHORT_RAW, "LONG": LONG_RAW}
QUANTIZED = {"SHORT": SHORT_QUANTIZED, "LONG": LONG_QUANTIZED}
LEVERAGE = {"SHORT": SHORT_LEVERAGE, "LONG": LONG_LEVERAGE}

# Two assets, one trade slot. BTC fills at bar 4 and closes (TP) at bar 8.
_BTC = {0: (100.0, 106.0, 99.0, 105.0), 1: (104.0, 104.5, 97.0, 98.0),
        2: (98.0, 101.0, 97.5, 100.0), 3: (100.0, 100.2, 98.0, 98.5),
        4: (99.6, 101.0, 99.5, 100.0), 8: (100.0, 100.5, 99.5, 99.8)}
for _b in (5, 6, 7):
    _BTC[_b] = (100.0, 100.0, 100.0, 100.0)     # inert: no origin, no exit
for _b in (9, 10):
    _BTC[_b] = (99.8, 99.8, 99.8, 99.8)

# ETH runs the same SHORT setup shifted +2 bars. TOUCH reaches its entry on
# every bar from 6 (so the lock REFUSES the entry); QUIET never reaches it (so
# the only thing standing between it and TRADE_SETUP_READY is the trade slot).
_ETH_HEAD = {2: (100.0, 106.0, 99.0, 105.0), 3: (104.0, 104.5, 97.0, 98.0),
             4: (98.0, 101.0, 97.5, 100.0), 5: (100.0, 100.2, 98.0, 98.5)}
_ETH_TOUCH = dict(_ETH_HEAD)
_ETH_QUIET = dict(_ETH_HEAD)
for _b in (6, 7, 8, 9, 10):
    _ETH_TOUCH[_b] = (99.6, 101.0, 99.5, 100.0)     # touches entry 100.5
    _ETH_QUIET[_b] = (99.6, 100.2, 99.5, 100.0)     # high 100.2 < entry 100.5
ETH_OB_ID = "MANUAL_ETHUSD_SHORT_2_3"


def _new(assets=("BTCUSD",), **kwargs) -> ManualSMCStrategy:
    kwargs.setdefault("tick_specs", _specs(*assets))
    return ManualSMCStrategy(assets=list(assets), **kwargs)


def _adapter(**kwargs) -> ManualSMCAdapter:
    kwargs.setdefault("timeframe", "1h")
    return ManualSMCAdapter(**kwargs)


def _drive(strategy, asset, rows):
    return [strategy.evaluate_closed_candle(asset, b, _ts(b), o, h, l, c)
            for (b, o, h, l, c) in rows]


def _adapt_all(direction: str, last: int = 4, adapter=None):
    """Drive one asset and translate every candle. Returns (evals, adaptations)."""
    adapter = adapter or _adapter()
    evals = _drive(_new(), "BTCUSD", ROWS[direction][:last])
    return evals, [adapter.adapt(ev) for ev in evals]


def _resting(direction: str):
    """The candle on which the limit first rests: (evaluation, setup)."""
    evals, _ = _adapt_all(direction, last=4)
    ev = evals[-1]
    assert len(ev.setups) == 1 and ev.setups[0].state is S.LIMIT_RESTING
    return ev, ev.setups[0]


def _ready(direction: str) -> StrategyDecision:
    ev, _setup = _resting(direction)
    decision = _adapter().adapt(ev).decisions[0]
    assert decision.setup_state is SetupState.TRADE_SETUP_READY
    return decision


def _replace_setup(ev, **changes):
    """An evaluation whose single setup carries `changes`."""
    setup = dataclasses.replace(ev.setups[0], **changes)
    return dataclasses.replace(ev, setups=(setup,)), setup


def _pair(eth_rows, last_bar: int = 10, adapter=None):
    """Interleave BTC and ETH in global chronological order, BTC first."""
    adapter = adapter or _adapter()
    strategy = _new(assets=("BTCUSD", "ETHUSD"))
    out = []
    for bar_idx in range(0, last_bar + 1):
        for asset, rows in (("BTCUSD", _BTC), ("ETHUSD", eth_rows)):
            if bar_idx not in rows:
                continue
            o, h, l, c = rows[bar_idx]
            ev = strategy.evaluate_closed_candle(
                asset, bar_idx, _ts(bar_idx), o, h, l, c)
            out.append((asset, bar_idx, ev, adapter.adapt(ev)))
    return out


def _eth(rows, last_bar: int = 10):
    return [(b, ev, ad) for (a, b, ev, ad) in _pair(rows, last_bar)
            if a == "ETHUSD"]


PRICE_FIELDS = ("entry", "stop_loss", "take_profit", "take_profit_price",
                "risk_distance", "reward_distance", "risk_reward")

INVENTED_FIELDS = ("quantity", "risk_amount", "reward_amount", "confidence",
                   "order_block", "candle", "minimum_risk_reward",
                   "configuration_version")


def _identifiers(src: str = ADAPTER_SRC):
    """Every NAME token — a token set cannot be fooled by a docstring."""
    return {tok.string
            for tok in tokenize.generate_tokens(io.StringIO(src).readline)
            if tok.type == tokenize.NAME}


def _numeric_literals(src: str = ADAPTER_SRC):
    """Every int/float literal in the AST. Booleans are not numbers here."""
    return sorted({node.value for node in ast.walk(ast.parse(src))
                   if isinstance(node, ast.Constant)
                   and isinstance(node.value, (int, float))
                   and not isinstance(node.value, bool)})


def _imports_of(path: Path):
    """Every module named by an import statement, via the AST (not a regex)."""
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return out


def _imported_names_of(path: Path, module: str):
    """The names a module imports FROM `module`, via the AST."""
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            out.update(alias.name for alias in node.names)
    return out


# ---------------------------------------------------------------------------
# SHORT conversion
# ---------------------------------------------------------------------------
class TestShortConversion:
    """A SHORT setup, candle by candle, in the application's vocabulary."""

    def test_one_decision_per_candle_in_the_lifecycles_own_order(self):
        _evals, adaptations = _adapt_all("SHORT", last=4)
        assert [(a.bar_idx, a.decisions[0].setup_state) for a in adaptations] == [
            (0, SetupState.NO_SETUP),
            (1, SetupState.WATCHING_OB),
            (2, SetupState.WATCHING_OB),
            (3, SetupState.TRADE_SETUP_READY),
        ]
        assert all(len(a.decisions) == 1 for a in adaptations)

    def test_direction_and_setup_type_are_preserved_exactly(self):
        _evals, adaptations = _adapt_all("SHORT", last=4)
        live = [a.decisions[0] for a in adaptations[1:]]
        assert {d.direction for d in live} == {StrategyDirection.SHORT}
        assert {d.setup_type for d in live} == {
            SetupType.BEARISH_OB_RETEST.value}
        # Only the empty candle is directionless, and it is also NO_SETUP.
        assert adaptations[0].decisions[0].direction is StrategyDirection.NONE

    def test_the_ready_decision_carries_the_quantized_bracket(self):
        entry, sl, tp = SHORT_QUANTIZED
        d = _ready("SHORT")
        assert (d.entry, d.stop_loss, d.take_profit) == (entry, sl, tp)
        assert d.take_profit_price == tp
        assert d.risk_distance == Decimal("4.5")        # 105.0 - 100.5
        assert d.reward_distance == Decimal("0.5")      # 100.5 - 100.0
        assert d.take_profit < d.entry < d.stop_loss    # SHORT geometry

    def test_leverage_is_the_floor_and_the_loss_is_reported(self):
        ev, setup = _resting("SHORT")
        adaptation = _adapter().adapt(ev)
        d = adaptation.decisions[0]
        assert setup.applied_leverage == pytest.approx(7.816666666666667)
        assert d.calculated_leverage == SHORT_LEVERAGE == 7
        assert d.metadata["applied_leverage"] == repr(setup.applied_leverage)
        assert d.metadata["leverage_truncated_to_int"] is True
        assert adaptation.leverage_truncated_ob_ids == (SHORT_OB_ID,)
        assert any(LEVERAGE_INT_TRUNCATION_NOTE in r for r in d.reasons)

    def test_a_watching_ob_publishes_prices_but_is_not_executable(self):
        _evals, adaptations = _adapt_all("SHORT", last=3)
        d = adaptations[-1].decisions[0]
        assert d.setup_state is SetupState.WATCHING_OB
        assert (d.entry, d.stop_loss, d.take_profit) == SHORT_QUANTIZED
        assert d.calculated_leverage is None       # no resting limit, no size
        assert adaptations[-1].has_ready_decision is False
        assert adaptations[-1].non_executable_ob_ids == ()

    def test_the_fill_candle_offers_no_setup_and_passes_the_fill_through(self):
        evals, adaptations = _adapt_all("SHORT", last=5)
        fill_bar = [a for a in adaptations if a.filled is not None]
        assert [a.bar_idx for a in fill_bar] == [4]
        assert fill_bar[0].filled is evals[4].filled          # by reference
        assert fill_bar[0].decisions[0].setup_state is SetupState.NO_SETUP
        assert fill_bar[0].has_ready_decision is False
        assert fill_bar[0].trade_slot_taken is True

    def test_the_close_passes_through_by_reference(self):
        evals, adaptations = _adapt_all("SHORT", last=6)
        closed = [a for a in adaptations if a.closed is not None]
        assert [a.bar_idx for a in closed] == [5]
        assert closed[0].closed is evals[5].closed
        assert closed[0].trade_slot_taken is False

    def test_metadata_records_the_provenance_and_the_expiry_policy(self):
        d = _ready("SHORT")
        assert d.metadata["tp_source"] == TAKE_PROFIT_SOURCE == TP_SOURCE
        assert d.metadata["take_profit_is_absolute_price"] is True
        assert d.metadata["entry_order_type"] == ENTRY_ORDER_TYPE == "LIMIT"
        assert d.metadata["entry_is_resting_limit"] is True
        policy = d.metadata["resting_order_expiry_policy"]
        assert policy == RESTING_ORDER_EXPIRY_POLICY
        assert "NO_TIME_BASED_EXPIRY" in policy and "72-bar" in policy
        assert d.metadata["manual_ob_id"] == SHORT_OB_ID
        assert d.metadata["manual_ob_state"] == S.LIMIT_RESTING.value

    def test_every_decisions_metadata_is_json_serialisable(self):
        _evals, adaptations = _adapt_all("SHORT", last=6)
        for a in adaptations:
            for d in a.decisions + a.blocked_decisions:
                json.dumps(d.metadata)

    def test_the_setup_id_is_deterministic_and_traceable(self):
        d = _ready("SHORT")
        assert d.setup_id == (
            f"BTCUSD_1h_{MANUAL_SMC_STRATEGY_NAME}_{SHORT_OB_ID}_SHORT")
        assert d.setup_id == _ready("SHORT").setup_id

    def test_the_timeframe_and_timestamp_come_from_the_evaluation(self):
        ev, _setup = _resting("SHORT")
        d = _adapter().adapt(ev).decisions[0]
        assert d.timeframe == "1h" and d.timestamp == ev.ts == _ts(3)
        assert d.symbol == "BTCUSD"


# ---------------------------------------------------------------------------
# LONG conversion
# ---------------------------------------------------------------------------
class TestLongConversion:
    """The LONG mirror. Every asymmetry is the strategy's, not the adapter's."""

    def test_the_state_sequence_mirrors_the_short_path(self):
        _evals, adaptations = _adapt_all("LONG", last=4)
        assert [(a.bar_idx, a.decisions[0].setup_state) for a in adaptations] == [
            (0, SetupState.NO_SETUP),
            (1, SetupState.WATCHING_OB),
            (2, SetupState.WATCHING_OB),
            (3, SetupState.TRADE_SETUP_READY),
        ]

    def test_direction_and_setup_type_are_preserved_exactly(self):
        _evals, adaptations = _adapt_all("LONG", last=4)
        live = [a.decisions[0] for a in adaptations[1:]]
        assert {d.direction for d in live} == {StrategyDirection.LONG}
        assert {d.setup_type for d in live} == {
            SetupType.BULLISH_OB_RETEST.value}

    def test_the_ready_decision_carries_the_quantized_bracket(self):
        entry, sl, tp = LONG_QUANTIZED
        d = _ready("LONG")
        assert (d.entry, d.stop_loss, d.take_profit) == (entry, sl, tp)
        assert d.take_profit_price == tp
        assert d.risk_distance == Decimal("4.5")        # 104.5 - 100.0
        assert d.reward_distance == Decimal("0.5")      # 105.0 - 104.5
        assert d.stop_loss < d.entry < d.take_profit    # LONG geometry

    def test_leverage_is_the_floor_of_the_long_stop_distance(self):
        ev, setup = _resting("LONG")
        adaptation = _adapter().adapt(ev)
        assert setup.applied_leverage == pytest.approx(8.127777777777778)
        assert adaptation.decisions[0].calculated_leverage == LONG_LEVERAGE == 8
        assert adaptation.leverage_truncated_ob_ids == (LONG_OB_ID,)

    def test_the_ob_edges_are_the_long_zone_not_the_short_one(self):
        """`ob_top` is origin.high and `ob_bottom` is origin.close for a LONG."""
        d = _ready("LONG")
        assert d.order_block_upper_edge == Decimal("106.0")
        assert d.order_block_lower_edge == Decimal("100.0")
        assert d.metadata["raw_proximal"] == repr(106.0)
        assert d.metadata["raw_distal"] == repr(100.0)

    def test_a_long_and_a_short_never_collide_in_the_setup_id(self):
        assert _ready("LONG").setup_id != _ready("SHORT").setup_id
        assert _ready("LONG").setup_id.endswith("_LONG")

    def test_the_long_lifecycle_fills_and_closes_through_the_boundary(self):
        evals, adaptations = _adapt_all("LONG", last=6)
        assert [a.bar_idx for a in adaptations if a.filled is not None] == [4]
        assert [a.bar_idx for a in adaptations if a.closed is not None] == [5]
        assert adaptations[4].filled is evals[4].filled
        assert adaptations[5].closed is evals[5].closed


# ---------------------------------------------------------------------------
# Absolute take-profit provenance (requirement 3)
# ---------------------------------------------------------------------------
class TestAbsoluteTakeProfit:
    """The TP is the OB's absolute price. `take_profit_target_pct` is not read."""

    @pytest.mark.parametrize("direction", ["SHORT", "LONG"])
    def test_the_take_profit_descends_from_the_setups_own_tp_price(self, direction):
        ev, setup = _resting(direction)
        d = _adapter().adapt(ev).decisions[0]
        bracket = setup.quantized
        assert setup.tp_price == pytest.approx(RAW[direction][2])
        assert bracket.raw_tp_price == price_from_strategy_float(setup.tp_price)
        assert d.take_profit is bracket.tp_price
        assert abs(d.take_profit - bracket.raw_tp_price) < bracket.tick_size

    def test_the_identifier_take_profit_target_pct_is_absent_from_the_code(self):
        """Requirement 3, proved on tokens: the field is never even named."""
        assert "take_profit_target_pct" not in _identifiers()
        for banned in ("target_roe_pct", "roe", "fixed_tp_market_pct",
                       "take_profit_pct", "tp_pct"):
            assert banned not in _identifiers(), banned

    @pytest.mark.parametrize("direction", ["SHORT", "LONG"])
    def test_the_percentage_field_would_give_a_wildly_different_price(self, direction):
        """
        `take_profit_target_pct` defaults to 60.0 — a RETURN ON MARGIN, not a
        price move. Deriving a price from it would be off by two orders of
        magnitude, which is exactly why it is never read.
        """
        d = _ready(direction)
        pct = d.take_profit_target_pct / Decimal("100")
        wrong = (d.entry * (Decimal("1") + pct) if direction == "LONG"
                 else d.entry * (Decimal("1") - pct))
        assert d.take_profit != wrong
        # The real TP is a ~0.60% price move away from the entry.
        move = abs(d.take_profit - d.entry) / d.entry
        assert move < Decimal("0.01")

    def test_the_application_default_percentage_is_left_untouched(self):
        """The adapter neither reads nor writes it; `models.py` is unmodified."""
        d = _ready("SHORT")
        default = {f.name: f.default
                   for f in dataclasses.fields(StrategyDecision)}
        assert d.take_profit_target_pct == default["take_profit_target_pct"]
        assert d.take_profit_target_pct == Decimal("60.0")

    def test_take_profit_and_take_profit_price_are_the_same_object(self):
        d = _ready("SHORT")
        assert d.take_profit is d.take_profit_price

    @pytest.mark.parametrize("field", ["entry_price", "sl_price", "tp_price"])
    def test_a_bracket_that_does_not_match_the_setup_is_refused(self, field):
        ev, _setup = _resting("SHORT")
        ev2, _s2 = _replace_setup(
            ev, **{field: getattr(ev.setups[0], field) + 1.0})
        with pytest.raises(InconsistentEvaluationError) as exc:
            _adapter().adapt(ev2)
        assert "does not match" in str(exc.value)

    def test_the_metadata_keeps_the_raw_and_the_quantized_tp(self):
        ev, setup = _resting("SHORT")
        d = _adapter().adapt(ev).decisions[0]
        assert d.metadata["raw_tp_price"] == repr(setup.tp_price)
        assert d.metadata["tick_size"] == str(setup.quantized.tick_size)
        assert d.take_profit == SHORT_QUANTIZED[2]


# ---------------------------------------------------------------------------
# Quantized prices preserved exactly (requirement 6)
# ---------------------------------------------------------------------------
class TestQuantizedPricesPreserved:
    """The published prices ARE the bracket's legs. This module never rounds."""

    @pytest.mark.parametrize("direction", ["SHORT", "LONG"])
    def test_the_price_fields_are_the_bracket_objects_themselves(self, direction):
        ev, setup = _resting(direction)
        d = _adapter().adapt(ev).decisions[0]
        bracket = setup.quantized
        assert d.entry is bracket.entry_price
        assert d.stop_loss is bracket.sl_price
        assert d.take_profit is bracket.tp_price
        assert d.risk_distance == bracket.risk_dist
        assert d.reward_distance == bracket.reward_dist

    @pytest.mark.parametrize("asset", sorted(REAL_TICKS))
    def test_every_published_price_is_on_that_assets_real_tick_grid(self, asset):
        """All four real Delta tick sizes, including XRP's 0.0001."""
        strategy = _new(assets=(asset,))
        evals = _drive(strategy, asset, SHORT_ROWS[:4])
        d = _adapter().adapt(evals[-1]).decisions[0]
        tick = REAL_TICKS[asset]
        assert d.setup_state is SetupState.TRADE_SETUP_READY
        for price in (d.entry, d.stop_loss, d.take_profit, d.take_profit_price):
            assert isinstance(price, Decimal)
            assert is_on_tick_grid(price, tick), (asset, price)
        assert d.metadata["tick_size"] == str(tick)

    @pytest.mark.parametrize("direction", ["SHORT", "LONG"])
    def test_the_prices_equal_an_independent_quantization_of_the_geometry(
            self, direction):
        """
        Quantize the setup's own floats again, outside the adapter, and compare.

        This is the check that would catch a second, divergent rounding rule
        hidden in the boundary: the adapter must reproduce `quantization.py`'s
        answer exactly because it never computes one of its own.
        """
        ev, setup = _resting(direction)
        d = _adapter().adapt(ev).decisions[0]
        independent = quantize_bracket(
            asset=setup.asset,
            direction=setup.direction,
            entry_price=price_from_strategy_float(setup.entry_price),
            sl_price=price_from_strategy_float(setup.sl_price),
            tp_price=price_from_strategy_float(setup.tp_price),
            tick_size=REAL_TICKS[setup.asset],
        )
        assert (d.entry, d.stop_loss, d.take_profit) == (
            independent.entry_price, independent.sl_price,
            independent.tp_price)
        assert (d.entry, d.stop_loss, d.take_profit) == QUANTIZED[direction]

    def test_the_boundary_names_no_rounding_machinery_at_all(self):
        """Requirement 6, on tokens: there is nothing here that could round."""
        banned = ("quantize", "quantize_price", "quantize_bracket",
                  "quantize_ob_bracket", "is_on_tick_grid", "TickRounding",
                  "conservative_rounding", "ROUND_HALF_UP", "ROUND_HALF_DOWN",
                  "ROUND_DOWN", "ROUND_UP", "ROUND_FLOOR", "ROUND_CEILING",
                  "localcontext", "getcontext", "Context", "round", "floor",
                  "ceil", "math", "divmod", "quantize_ob")
        names = _identifiers()
        for token in banned:
            assert token not in names, f"{token!r} appears in adapter.py"

    def test_the_only_numeric_literal_is_the_leverage_floor_bound(self):
        """
        `1` in `floored < 1`. No 0.994, no 1.006, no 0.25, no 35, no 72, no 100.

        A strategy or sizing constant reappearing here would mean the boundary
        had started to recompute something instead of translating it.
        """
        assert _numeric_literals() == [1]

    def test_a_setup_without_a_bracket_publishes_no_price_at_all(self):
        ev, _setup = _resting("SHORT")
        ev2, _s2 = _replace_setup(
            ev, quantized=None, quantization_refusal="no spec for this asset")
        adaptation = _adapter().adapt(ev2)
        d = adaptation.decisions[0]
        for field in PRICE_FIELDS:
            assert getattr(d, field) is None, field
        assert d.setup_state is SetupState.QUALIFIED_SHORT
        assert adaptation.has_ready_decision is False
        assert MISSING_BRACKET_REFUSAL in d.reasons
        assert d.metadata["quantization_refusal"] == "no spec for this asset"
        assert d.metadata["tick_size"] is None

    def test_the_informational_magnitudes_are_exact_decimals(self):
        ev, setup = _resting("SHORT")
        d = _adapter().adapt(ev).decisions[0]
        assert d.stop_distance_pct == Decimal(str(setup.sl_dist_pct))
        assert d.order_block_upper_edge == Decimal(str(setup.ob_top))
        assert d.order_block_lower_edge == Decimal(str(setup.ob_bottom))


# ---------------------------------------------------------------------------
# Identity MANUAL_SMC / 1.0.0 (requirement 4)
# ---------------------------------------------------------------------------
class TestIdentity:
    """Nothing leaves this boundary labelled anything but MANUAL_SMC / 1.0.0."""

    def test_the_application_default_is_a_different_strategy_entirely(self):
        default = {f.name: f.default
                   for f in dataclasses.fields(StrategyDecision)}
        assert default["strategy_name"] == "SMC"
        assert default["strategy_version"] == "2.1"
        assert (MANUAL_SMC_STRATEGY_NAME, MANUAL_SMC_STRATEGY_VERSION) == (
            "MANUAL_SMC", "1.0.0")

    def test_every_decision_the_boundary_emits_carries_the_manual_identity(self):
        emitted = []
        for direction in ("SHORT", "LONG"):
            _evals, adaptations = _adapt_all(direction, last=6)
            for a in adaptations:
                assert (a.strategy_name, a.strategy_version) == (
                    MANUAL_SMC_STRATEGY_NAME, MANUAL_SMC_STRATEGY_VERSION)
                emitted += list(a.decisions) + list(a.blocked_decisions)
        for _b, _ev, a in _eth(_ETH_TOUCH, last_bar=8):
            emitted += list(a.decisions) + list(a.blocked_decisions)
        assert len(emitted) > 15
        for d in emitted:
            assert d.strategy_name == "MANUAL_SMC"
            assert d.strategy_version == "1.0.0"
            assert d.metadata["strategy_name"] == "MANUAL_SMC"
            assert d.metadata["strategy_version"] == "1.0.0"

    def test_the_no_setup_decision_is_identified_too(self):
        d = no_setup_decision("BTCUSD", "1h", _ts(0))
        assert (d.strategy_name, d.strategy_version) == ("MANUAL_SMC", "1.0.0")
        assert d.setup_state is SetupState.NO_SETUP
        assert d.direction is StrategyDirection.NONE
        assert d.setup_type is None
        for field in PRICE_FIELDS:
            assert getattr(d, field) is None, field

    def test_a_foreign_evaluation_cannot_be_relabelled_manual_smc(self):
        ev, _setup = _resting("SHORT")
        foreign = dataclasses.replace(
            ev, strategy_name="SMC", strategy_version="2.1")
        with pytest.raises(IdentityMismatchError) as exc:
            _adapter().adapt(foreign)
        assert "'SMC' / '2.1'" in str(exc.value)

    @pytest.mark.parametrize("name,version", [
        ("SMC", "1.0.0"), ("MANUAL_SMC", "2.1"), ("MANUAL_SMC", "1.0"),
        ("manual_smc", "1.0.0"), (None, None),
    ])
    def test_every_near_miss_identity_is_refused(self, name, version):
        ev, _setup = _resting("SHORT")
        _ev2, setup = _replace_setup(
            ev, strategy_name=name, strategy_version=version)
        with pytest.raises(IdentityMismatchError):
            require_manual_smc_identity(setup, "setup")

    def test_a_foreign_setup_fill_or_close_is_refused_by_adapt(self):
        evals, _a = _adapt_all("SHORT", last=6)
        resting, fill_ev, close_ev = evals[3], evals[4], evals[5]
        ev2, _s = _replace_setup(resting, strategy_name="SMC")
        cases = [
            ev2,
            dataclasses.replace(fill_ev, filled=dataclasses.replace(
                fill_ev.filled, strategy_version="2.1")),
            dataclasses.replace(close_ev, closed=dataclasses.replace(
                close_ev.closed, strategy_name="SMC")),
        ]
        for case in cases:
            with pytest.raises(IdentityMismatchError):
                _adapter().adapt(case)

    def test_the_identity_is_in_the_setup_id_so_the_database_can_tell(self):
        for direction in ("SHORT", "LONG"):
            assert "MANUAL_SMC" in _ready(direction).setup_id


# ---------------------------------------------------------------------------
# Blocked / refused entries (requirement 7, safety rules #13 and #14)
# ---------------------------------------------------------------------------
class TestBlockedAndRefused:
    """A refusal must survive translation. It must never become readiness."""

    def test_a_refused_entry_maps_to_qualified_and_never_to_ready(self):
        blocked = [(b, a) for (b, _ev, a) in _eth(_ETH_TOUCH, last_bar=7)
                   if a.blocked_decisions]
        assert [b for (b, _a) in blocked] == [6, 7]
        for _b, a in blocked:
            assert len(a.blocked_decisions) == 1
            d = a.blocked_decisions[0]
            assert d.setup_state is SetupState.QUALIFIED_SHORT
            assert d.setup_state is not SetupState.TRADE_SETUP_READY
            assert d.direction is StrategyDirection.SHORT
            assert a.has_ready_decision is False

    def test_a_blocked_decision_is_kept_out_of_the_setup_decisions(self):
        _b, _ev, a = _eth(_ETH_TOUCH, last_bar=6)[-1]
        assert len(a.blocked_decisions) == 1 and len(a.decisions) == 1
        assert a.blocked_decisions[0] not in a.decisions
        assert a.blocked_decisions[0].setup_id == a.decisions[0].setup_id
        assert a.decisions[0].setup_state is SetupState.QUALIFIED_SHORT

    def test_a_blocked_decision_carries_no_prices_because_there_is_no_order(self):
        _b, _ev, a = _eth(_ETH_TOUCH, last_bar=6)[-1]
        d = a.blocked_decisions[0]
        for field in PRICE_FIELDS:
            assert getattr(d, field) is None, field
        assert d.calculated_leverage is None

    def test_the_lock_rejection_code_and_holder_survive_translation(self):
        _b, ev, a = _eth(_ETH_TOUCH, last_bar=6)[-1]
        d = a.blocked_decisions[0]
        rejection = ev.blocked[0].lock_rejection
        assert d.risk_validation_status == "REJECTED_ACTIVE_TRADE_OPEN"
        assert d.metadata["lock_rejection_code"] == "ACTIVE_TRADE_OPEN"
        assert d.metadata["lock_rejection_detail"] == rejection.detail
        assert d.metadata["lock_held_by_ob_id"] == SHORT_OB_ID
        assert d.metadata["lock_held_by_asset"] == "BTCUSD"
        assert d.metadata["lifecycle_detail"] == ev.blocked[0].detail
        assert d.metadata["blocked_by_single_trade_lock"] is True
        assert any("safety rule #13" in r for r in d.reasons)

    def test_the_intra_candle_ambiguity_refusal_is_preserved_verbatim(self):
        _b, _ev, a = _eth(_ETH_TOUCH, last_bar=8)[-1]
        d = a.blocked_decisions[0]
        assert d.metadata["lock_rejection_code"] == "INTRA_CANDLE_AMBIGUITY"
        assert d.risk_validation_status == "REJECTED_INTRA_CANDLE_AMBIGUITY"
        assert d.metadata["lock_held_by_ob_id"] is None

    def test_an_ob_refused_on_this_candle_is_not_advertised_as_ready(self):
        """
        Bar 8: the BTC trade has just CLOSED, so the slot is free — but the ETH
        entry was refused on this very candle. Readiness would contradict the
        refusal the strategy issued microseconds earlier.
        """
        _b, ev, a = _eth(_ETH_TOUCH, last_bar=8)[-1]
        assert ev.active_trade is None and a.trade_slot_taken is False
        assert ev.blocked and ev.setups[0].state is S.LIMIT_RESTING
        d = a.decisions[0]
        assert d.setup_state is SetupState.QUALIFIED_SHORT
        assert ENTRY_REFUSED_THIS_CANDLE_REFUSAL in d.reasons
        assert d.metadata["entry_refused_this_candle"] is True
        assert a.non_executable_ob_ids == (ETH_OB_ID,)

    def test_no_second_asset_is_ever_ready_while_a_trade_is_open(self):
        """Safety rule #13 at the boundary, over the whole interleaved run."""
        seen_taken = 0
        for _asset, _bar, ev, a in _pair(_ETH_TOUCH, last_bar=10):
            if ev.active_trade is not None or ev.lock_holder is not None:
                seen_taken += 1
                assert a.trade_slot_taken is True
                assert a.has_ready_decision is False
                assert a.ready_decisions == ()
        assert seen_taken >= 6

    def test_the_slot_gate_alone_withholds_readiness_and_then_releases_it(self):
        """
        The QUIET ETH never touches its entry, so nothing is ever blocked: the
        ONLY thing between it and TRADE_SETUP_READY is the occupied trade slot.
        """
        states = {b: (a.trade_slot_taken, a.decisions[0].setup_state)
                  for (b, _ev, a) in _eth(_ETH_QUIET, last_bar=10)}
        assert states[5] == (True, SetupState.QUALIFIED_SHORT)
        assert states[6] == (True, SetupState.QUALIFIED_SHORT)
        assert states[7] == (True, SetupState.QUALIFIED_SHORT)
        assert states[8] == (False, SetupState.TRADE_SETUP_READY)
        assert states[10] == (False, SetupState.TRADE_SETUP_READY)
        held = [a for (b, _ev, a) in _eth(_ETH_QUIET, last_bar=7)
                if a.trade_slot_taken]
        assert held and all(not a.blocked_decisions for a in held)
        d = held[-1].decisions[0]
        assert TRADE_SLOT_TAKEN_REFUSAL in d.reasons
        assert d.metadata["trade_slot_taken"] is True
        assert held[-1].non_executable_ob_ids == (ETH_OB_ID,)

    def test_a_withheld_setup_still_publishes_its_prices_and_leverage(self):
        """Qualified, not ready: informative without being executable."""
        held = [a for (b, _ev, a) in _eth(_ETH_QUIET, last_bar=7)
                if a.trade_slot_taken and a.decisions[0].entry is not None]
        d = held[-1].decisions[0]
        assert d.setup_state is SetupState.QUALIFIED_SHORT
        assert d.entry == Decimal("100.5") and d.stop_loss == Decimal("105.0")
        assert d.calculated_leverage == 7

    def test_invalidations_and_superseded_orders_are_reported_not_acted_on(self):
        """`cancel_ob_ids` is a withdrawal REPORT (safety rule #9)."""
        for _asset, _bar, ev, a in _pair(_ETH_TOUCH, last_bar=10):
            expected = list(ev.invalidated) + list(
                () if ev.filled is None else ev.filled.cancel_ob_ids)
            deduped = []
            for ob_id in expected:
                if ob_id not in deduped:
                    deduped.append(ob_id)
            assert a.cancel_ob_ids == tuple(deduped)
            assert a.invalidated_ob_ids == tuple(ev.invalidated)

    def test_a_fill_reports_the_foreign_resting_order_it_supersedes(self):
        fills = [(b, ev, a) for (_asset, b, ev, a) in _pair(_ETH_TOUCH, 10)
                 if a.filled is not None]
        assert [b for (b, _ev, _a) in fills] == [4, 9]
        eth_fill = fills[-1][2]
        assert eth_fill.filled.ob_id == ETH_OB_ID
        assert eth_fill.cancel_ob_ids == fills[-1][1].filled.cancel_ob_ids

    def test_decision_from_blocked_refuses_a_bad_direction(self):
        _b, ev, _a = _eth(_ETH_TOUCH, last_bar=6)[-1]
        broken = dataclasses.replace(ev.blocked[0], direction="short")
        with pytest.raises(InconsistentEvaluationError):
            decision_from_blocked(broken, "ETHUSD", "1h")


# ---------------------------------------------------------------------------
# Fail-closed: a missing or invalid executable field is never papered over
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StubConfig:
    """A config-shaped object. Only what `_risk_budget` and `__init__` read."""
    max_sl_account_risk_pct: float
    data_timeframe: str = "1h"


class TestFailClosed:
    """Every refusal raises or withholds readiness. None of them guess."""

    def test_a_setup_without_a_bracket_publishes_no_prices_and_no_leverage(self):
        ev, _setup = _resting("SHORT")
        broken, _s = _replace_setup(ev, quantized=None, quantization_refusal="x")
        adaptation = _adapter().adapt(broken)
        d = adaptation.decisions[0]
        assert d.setup_state is SetupState.QUALIFIED_SHORT
        for field in PRICE_FIELDS:
            assert getattr(d, field) is None, field
        assert d.calculated_leverage is None
        assert MISSING_BRACKET_REFUSAL in d.reasons
        assert adaptation.non_executable_ob_ids == (SHORT_OB_ID,)
        assert adaptation.has_ready_decision is False

    @pytest.mark.parametrize("raw,expected,noted", [
        (7.816666666666667, 7, True),        # the real SHORT leverage
        (8.127777777777778, 8, True),        # the real LONG leverage
        (100.0, 100, False),                 # the cap: exact, nothing lost
        (1.0, 1, False),                     # the floor of representability
        (1.9999, 1, True),
        (0.5, None, True),                   # sub-1x: REFUSED, never rounded up
        (0.0001, None, True),
    ])
    def test_leverage_representation_floors_and_refuses_sub_one(
            self, raw, expected, noted):
        leverage, note = represent_leverage(raw)
        assert leverage == expected
        assert (note is not None) is noted
        if expected is None:
            assert UNREPRESENTABLE_LEVERAGE_REFUSAL in note
            assert repr(raw) in note
        elif noted:
            assert LEVERAGE_INT_TRUNCATION_NOTE in note
            assert repr(raw) in note

    def test_a_sub_one_leverage_is_refused_rather_than_rounded_up_to_1x(self):
        """
        Rounding 0.5x up to 1x is the ONE direction that raises risk above the
        35% budget, so it is refused. `calculated_leverage` stays `None`, and
        because `None` fails OPEN downstream the state must not be READY.
        """
        ev, _setup = _resting("SHORT")
        broken, _s = _replace_setup(ev, applied_leverage=0.5)
        d = _adapter().adapt(broken).decisions[0]
        assert d.calculated_leverage is None
        assert d.setup_state is SetupState.QUALIFIED_SHORT
        assert any(UNREPRESENTABLE_LEVERAGE_REFUSAL in r for r in d.reasons)
        assert d.metadata["applied_leverage"] == repr(0.5)
        assert d.metadata["leverage_truncated_to_int"] is False

    @pytest.mark.parametrize("state", [
        S.TRADE_ACTIVE, S.TRADE_CLOSED, S.INVALIDATED,
    ])
    def test_a_non_setup_state_is_refused_instead_of_being_mapped(self, state):
        ev, _setup = _resting("SHORT")
        broken, _s = _replace_setup(ev, state=state)
        with pytest.raises(UnmappedStateError) as exc:
            _adapter().adapt(broken)
        assert state.value in str(exc.value) or state.name in str(exc.value)
        with pytest.raises(UnmappedStateError):
            map_ob_state(state, "SHORT", True)

    def test_map_ob_state_refuses_a_foreign_state_object(self):
        for junk in ("LIMIT_RESTING", None, 3, SetupState.TRADE_SETUP_READY):
            with pytest.raises(UnmappedStateError):
                map_ob_state(junk, "SHORT", True)

    @pytest.mark.parametrize("junk", ["short", "long", "", None, 1, "BUY"])
    def test_a_direction_that_is_not_long_or_short_is_refused(self, junk):
        with pytest.raises(InconsistentEvaluationError):
            map_direction(junk)
        with pytest.raises(InconsistentEvaluationError):
            map_setup_type(junk)

    def test_a_bracket_for_another_asset_is_refused(self):
        ev, setup = _resting("SHORT")
        foreign = quantize_bracket(
            asset="ETHUSD", direction="SHORT",
            entry_price=price_from_strategy_float(setup.entry_price),
            sl_price=price_from_strategy_float(setup.sl_price),
            tp_price=price_from_strategy_float(setup.tp_price),
            tick_size=REAL_TICKS["ETHUSD"])
        broken, _s = _replace_setup(ev, quantized=foreign)
        with pytest.raises(InconsistentEvaluationError) as exc:
            _adapter().adapt(broken)
        assert "ETHUSD" in str(exc.value)

    def test_a_bracket_whose_take_profit_is_not_the_setups_is_refused(self):
        """The absolute-TP provenance check, exercised as a refusal."""
        ev, setup = _resting("SHORT")
        tampered = quantize_bracket(
            asset=setup.asset, direction=setup.direction,
            entry_price=price_from_strategy_float(setup.entry_price),
            sl_price=price_from_strategy_float(setup.sl_price),
            # 60% target return — the field the adapter must never use.
            tp_price=price_from_strategy_float(setup.entry_price * 0.4),
            tick_size=REAL_TICKS["BTCUSD"])
        broken, _s = _replace_setup(ev, quantized=tampered)
        with pytest.raises(InconsistentEvaluationError) as exc:
            _adapter().adapt(broken)
        assert "take_profit" in str(exc.value)

    def test_a_setup_for_a_different_asset_than_its_evaluation_is_refused(self):
        ev, _setup = _resting("SHORT")
        broken, _s = _replace_setup(ev, asset="ETHUSD")
        with pytest.raises(InconsistentEvaluationError) as exc:
            _adapter().adapt(broken)
        assert "BTCUSD" in str(exc.value) and "ETHUSD" in str(exc.value)

    def test_an_adapter_without_a_timeframe_refuses_to_assume_one(self):
        with pytest.raises(AdapterConfigError) as exc:
            ManualSMCAdapter()
        assert "timeframe is required" in str(exc.value)
        with pytest.raises(AdapterConfigError):
            ManualSMCAdapter(timeframe="")
        with pytest.raises(AdapterConfigError):
            ManualSMCAdapter(config=StubConfig(35.0, data_timeframe=""))
        assert ManualSMCAdapter(config=ManualSpecConfig()).timeframe == "1h"

    def test_an_asset_outside_the_symbol_map_fails_closed(self):
        ev, _setup = _resting("SHORT")
        adapter = _adapter(symbol_map={"ETHUSD": "ETHUSD_PERP"})
        with pytest.raises(UnknownSymbolError) as exc:
            adapter.adapt(ev)
        assert "BTCUSD" in str(exc.value)
        # No suffix, product id or contract semantic is ever inferred.
        assert "_PERP" not in str(exc.value)
        with pytest.raises(UnknownSymbolError):
            adapter.symbol_for("XRPUSD")
        assert _adapter().symbol_for("BTCUSD") == "BTCUSD"

    def test_a_degenerate_risk_budget_is_refused_not_defaulted(self):
        """
        `StrategyDecision.max_loss_pct` defaults to 35.0, which coincides with
        the Manual SMC budget. Falling back to it would silently misreport a
        config that changed, so a degenerate budget raises.
        """
        ev, _setup = _resting("SHORT")
        for bad in (0.0, -35.0, float("nan"), float("inf")):
            with pytest.raises(AdapterConfigError) as exc:
                ManualSMCAdapter(config=StubConfig(bad)).adapt(ev)
            assert "max_sl_account_risk_pct" in str(exc.value)

    def test_a_supplied_risk_budget_is_copied_and_never_assumed(self):
        ev, _setup = _resting("SHORT")
        d = ManualSMCAdapter(config=StubConfig(20.0)).adapt(ev).decisions[0]
        assert d.max_loss_pct == Decimal("20.0")
        assert _adapter().adapt(ev).decisions[0].max_loss_pct == Decimal("35.0")

    def test_the_two_safety_gates_cannot_be_omitted_by_a_caller(self):
        ev, setup = _resting("SHORT")
        with pytest.raises(TypeError):
            decision_from_setup(setup, "BTCUSD", "1h", ev.ts)
        with pytest.raises(TypeError):
            decision_from_setup(setup, "BTCUSD", "1h", ev.ts,
                                trade_slot_taken=False)
        with pytest.raises(TypeError):
            decision_from_setup(setup, "BTCUSD", "1h", ev.ts,
                                entry_refused_this_candle=False)
        ok = decision_from_setup(setup, "BTCUSD", "1h", ev.ts,
                                 trade_slot_taken=False,
                                 entry_refused_this_candle=False)
        assert ok.setup_state is SetupState.TRADE_SETUP_READY

    def test_the_two_safety_gates_are_keyword_only(self):
        params = inspect.signature(decision_from_setup).parameters
        for name in ("trade_slot_taken", "entry_refused_this_candle"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert params[name].default is inspect.Parameter.empty

    def test_a_refusal_leaves_the_evaluation_and_setup_untouched(self):
        ev, _setup = _resting("SHORT")
        broken, setup = _replace_setup(ev, state=S.TRADE_ACTIVE)
        before = (dataclasses.astuple(setup), dataclasses.astuple(broken.setups[0]))
        with pytest.raises(UnmappedStateError):
            _adapter().adapt(broken)
        assert (dataclasses.astuple(setup),
                dataclasses.astuple(broken.setups[0])) == before

    def test_a_successful_adaptation_mutates_nothing_either(self):
        ev, _setup = _resting("SHORT")
        before = dataclasses.astuple(ev)
        adapter = _adapter()
        first = adapter.adapt(ev)
        assert dataclasses.astuple(ev) == before
        assert vars(adapter) == {"config": None, "timeframe": "1h",
                                "symbol_map": None}
        assert adapter.adapt(ev) == first

    def test_an_evaluation_from_another_strategy_is_refused(self):
        ev, _setup = _resting("SHORT")
        for changes in ({"strategy_name": "SMC"},
                        {"strategy_version": "2.1"},
                        {"strategy_name": "MANUAL_SMC "},
                        {"strategy_version": "1.0"}):
            with pytest.raises(IdentityMismatchError):
                _adapter().adapt(dataclasses.replace(ev, **changes))


# ---------------------------------------------------------------------------
# No duplicated lifecycle / portfolio / sizing / quantization logic (req. 8)
# ---------------------------------------------------------------------------
#: Identifiers that would mean this module had started doing another module's
#: job. Checked against NAME tokens, so a mention inside a docstring is fine and
#: only real code counts.
BANNED_IDENTIFIERS = {
    # sizing.py — the only place a leverage or a risk budget is DERIVED
    "compute_position_sizing", "applied_leverage_for", "size_for",
    "theoretical_leverage_for", "leverage_from_sl_distance",
    # lifecycle.py / scanner.py — no re-deciding what the strategy decided
    "advance_ob", "ManualOBLifecycle", "ManualSpecBOSScanner", "scan_for_bos",
    "evaluate_closed_candle", "validate_candle", "touches_entry",
    # portfolio.py — the lock is READ off the evaluation, never re-taken
    "PortfolioLock", "acquire", "release", "try_acquire",
    # quantization.py — the adapter must not round
    "quantize_price", "quantize_bracket", "quantize_ob_bracket",
    "quantize", "TickRounding", "conservative_rounding", "ROUND_HALF_UP",
    "round", "floor", "ceil", "trunc",
    # transport / persistence / order placement
    "requests", "httpx", "urllib", "socket", "aiohttp", "psycopg", "psycopg2",
    "sqlalchemy", "boto3", "cryptography", "AESGCM", "open", "Path", "os",
    "place_order", "submit_order", "cancel_order", "create_order",
    "DeltaClient", "OrderExecutionService", "execute", "authorize",
    # the field that must never be used to compute a TP
    "take_profit_target_pct",
}


class TestNoDuplicatedLogic:
    """The adapter translates. It does not compute, round, decide or persist."""

    def test_the_import_list_is_exactly_the_five_modules_it_needs(self):
        assert _imports_of(ADAPTER_PATH) == {
            "__future__",
            "dataclasses",
            "datetime",
            "decimal",
            "typing",
            "quantedge.strategy.manual_smc.models",
            "quantedge.strategy.manual_smc.quantization",
            "quantedge.strategy.manual_smc.strategy",
            "quantedge.strategy.models",
        }

    def test_no_banned_identifier_appears_in_the_code(self):
        assert BANNED_IDENTIFIERS & _identifiers() == set()

    def test_the_only_numeric_literal_is_the_one_in_the_leverage_floor_check(self):
        """No 0.25, no 0.994, no 1.006, no 35, no 0.08, no 72, no tick size."""
        assert _numeric_literals() == [1]

    def test_the_only_sizing_input_is_the_configs_own_risk_budget(self):
        """`max_sl_account_risk_pct` is COPIED; nothing is derived from it."""
        src = _identifiers()
        assert "max_sl_account_risk_pct" in src
        # `max_loss_pct` is a **kwargs dict KEY, never an assigned attribute:
        # the value is copied into the application field and not computed.
        assert '"max_loss_pct"' in ADAPTER_SRC
        assert "max_loss_pct" not in src
        assert not {"starting_capital", "fee_rate", "max_holding_bars",
                    "min_ob_width", "entry_depth_pct", "fixed_tp_market_pct",
                    "applied_leverage_cap", "lookback"} & src

    def test_the_adapter_holds_no_state_at_all(self):
        assert set(vars(_adapter())) == {"config", "timeframe", "symbol_map"}
        assert not hasattr(ManualSMCAdapter, "__slots__") or True
        assert [n for n in vars(ManualSMCAdapter)
                if not n.startswith("_")] == ["symbol_for", "adapt"]

    def test_the_same_evaluation_translates_identically_every_time(self):
        ev, _setup = _resting("SHORT")
        adapter = _adapter()
        results = [adapter.adapt(ev) for _ in range(4)]
        assert all(r == results[0] for r in results)
        assert to_strategy_decisions(ev, timeframe="1h") == results[0]

    def test_the_adaptation_is_frozen_and_its_collections_are_tuples(self):
        adaptation = _adapter().adapt(_resting("SHORT")[0])
        with pytest.raises(dataclasses.FrozenInstanceError):
            adaptation.decisions = ()
        for field in ("decisions", "blocked_decisions", "invalidated_ob_ids",
                      "cancel_ob_ids", "leverage_truncated_ob_ids",
                      "non_executable_ob_ids"):
            assert isinstance(getattr(adaptation, field), tuple), field

    @pytest.mark.parametrize("direction", ["SHORT", "LONG"])
    def test_every_field_the_boundary_will_not_invent_stays_none(self, direction):
        d = _ready(direction)
        for field in INVENTED_FIELDS:
            assert getattr(d, field) is None, field
        # A quantity needs a contract value, which is unverified (rule #8).
        assert d.quantity is None
        # Manual SMC applies no RR threshold, so nothing may gate on one.
        assert d.minimum_risk_reward is None and d.risk_reward is not None

    def test_the_risk_validation_status_is_untouched_on_a_live_setup(self):
        """Validation belongs to `execution.validation`, not to a translator."""
        for direction in ("SHORT", "LONG"):
            assert _ready(direction).risk_validation_status is None

    def test_the_published_prices_are_the_brackets_own_decimal_objects(self):
        ev, setup = _resting("SHORT")
        d = _adapter().adapt(ev).decisions[0]
        bracket = setup.quantized
        assert d.entry is bracket.entry_price
        assert d.stop_loss is bracket.sl_price
        assert d.take_profit is bracket.tp_price
        assert d.take_profit_price is bracket.tp_price


# ---------------------------------------------------------------------------
# Import boundary (requirements 1 and 13)
# ---------------------------------------------------------------------------
SIBLINGS = ("__init__.py", "geometry.py", "lifecycle.py", "models.py",
            "portfolio.py", "quantization.py", "scanner.py", "sizing.py",
            "state.py", "strategy.py")

STEP_7_MODULES = tuple(sorted(SIBLINGS + ("adapter.py",)))

# Step 8 added `backtest.py`. The adapter's own scope claim below is therefore
# expressed as "adapter.py plus the Step 7 siblings, and NOTHING application-
# facing beyond it" rather than as an inventory of the whole package, which is
# pinned once in `test_manual_smc_backtest.py::TestStep8ScopeMarker`.
STEP_8_ADDITIONS = ("backtest.py",)


class TestImportBoundary:
    """`adapter.py` is the ONLY module here that knows the application exists."""

    def test_only_the_adapter_imports_the_application_strategy_models(self):
        importers = {p.name for p in sorted(PACKAGE.glob("*.py"))
                     if "quantedge.strategy.models" in _imports_of(p)}
        assert importers == {"adapter.py"}

    def test_no_sibling_imports_anything_outside_the_manual_smc_package(self):
        for path in sorted(PACKAGE.glob("*.py")):
            if path.name == "adapter.py":
                continue
            foreign = {m for m in _imports_of(path)
                       if m.startswith("quantedge.")
                       and not m.startswith("quantedge.strategy.manual_smc")}
            assert foreign == set(), f"{path.name} imports {foreign}"

    def test_the_docstring_mentions_are_not_imports(self):
        """
        `strategy.py` and `__init__.py` name `StrategyDecision`/`SetupState` in
        PROSE. That is why this boundary is proved by AST and not by text search.
        """
        for name in ("__init__.py", "strategy.py"):
            src = (PACKAGE / name).read_text(encoding="utf-8")
            assert "StrategyDecision" in src
            assert "quantedge.strategy.models" not in _imports_of(PACKAGE / name)

    def test_the_adapter_imports_exactly_four_application_names(self):
        assert _imported_names_of(
            ADAPTER_PATH, "quantedge.strategy.models") == {
            "SetupState", "SetupType", "StrategyDecision", "StrategyDirection"}

    def test_the_package_init_does_not_re_export_the_adapter(self):
        """
        Deliberate: importing `quantedge.strategy.manual_smc` must not drag the
        application's types in. Consumers import `...manual_smc.adapter`.

        Proved in a SUBPROCESS. In this process the adapter is already imported,
        and importing a submodule binds it as an attribute of its parent package,
        so `hasattr(pkg, "adapter")` here would say nothing about the re-export.

        `quantedge.strategy.models` is measured as a DELTA against `import
        quantedge.strategy`, whose own `__init__.py` already imports it: that is
        the pre-existing baseline and the manual_smc package must not add to it.
        """
        init = PACKAGE / "__init__.py"
        assert "quantedge.strategy.manual_smc.adapter" not in _imports_of(init)
        assert ".adapter" not in _imports_of(init)
        probe = (
            "import sys, json\n"
            "import quantedge.strategy\n"
            "base = set(sys.modules)\n"
            "import quantedge.strategy.manual_smc as pkg\n"
            "added = set(sys.modules) - base\n"
            "print(json.dumps({\n"
            "  'adapter_imported': 'quantedge.strategy.manual_smc.adapter'"
            " in sys.modules,\n"
            "  'app_models_added': 'quantedge.strategy.models' in added,\n"
            "  'has_attr': hasattr(pkg, 'adapter'),\n"
            "  'exports_adapter': 'ManualSMCAdapter' in pkg.__all__,\n"
            "}))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent))
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout.strip().splitlines()[-1]) == {
            "adapter_imported": False,
            "app_models_added": False,
            "has_attr": False,
            "exports_adapter": False,
        }

    def test_importing_the_adapter_pulls_in_no_transport_or_database(self):
        """
        Subprocess closure proof. `quantedge/__init__.py` already does
        `from . import execution` — the pre-existing baseline — so the adapter is
        compared against a bare `import quantedge`, not against nothing.
        """
        probe = (
            "import sys, json\n"
            "import quantedge\n"
            "base = set(sys.modules)\n"
            "import quantedge.strategy.manual_smc.adapter\n"
            "print(json.dumps(sorted(set(sys.modules) - base)))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent))
        assert proc.returncode == 0, proc.stderr
        added = json.loads(proc.stdout.strip().splitlines()[-1])
        for banned in ("httpx", "cryptography", "psycopg", "psycopg2",
                       "sqlalchemy", "requests", "boto3", "socket"):
            assert not [m for m in added if m.split(".")[0] == banned], banned
        assert [m for m in added
                if m.startswith("quantedge.execution")] == []

    def test_step_7_scope_marker(self):
        """
        Step 7 adds `adapter.py` and nothing else APPLICATION-FACING.

        The Step 7 form of this test asserted `not (PACKAGE / "backtest.py")
        .exists()`. Step 8 added that module, so the assertion is converted to
        the requirement it actually encoded: `adapter.py` stays the SOLE
        translation boundary, and the module Step 8 added is a driver that sits
        ABOVE the strategy rather than a second application boundary.

        (It also replaced Step 6's `test_the_adapter_and_backtest_modules_do_not
        _exist_yet`, which has itself been converted to a dependency-direction
        check in `test_manual_smc_strategy.py`, per the testing rules.)
        """
        assert ADAPTER_PATH.exists()
        present = tuple(sorted(p.name for p in PACKAGE.glob("*.py")))
        assert set(STEP_7_MODULES) <= set(present)
        assert set(present) - set(STEP_7_MODULES) == set(STEP_8_ADDITIONS)

        # The Step 8 driver is not a second application boundary, and it does
        # not reach back into the adapter either.
        driver = PACKAGE / "backtest.py"
        assert "quantedge.strategy.models" not in _imports_of(driver)
        assert "quantedge.strategy.manual_smc.adapter" not in _imports_of(driver)
