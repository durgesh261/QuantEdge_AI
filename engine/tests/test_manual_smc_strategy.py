"""
Manual SMC — strategy orchestration acceptance tests (Phase 1 Step 6).
======================================================================

MANDATED COVERAGE -> CLASS
    LONG and SHORT ............................. TestShortPath / TestLongPath
    BOS -> displacement -> entry ............... TestShortPath / TestLongPath
    active-trade portfolio lock ................ TestPortfolioLockIntegration
    correct TP from the absolute price .......... TestAbsoluteTakeProfit
    quantization boundary ....................... TestQuantizationBoundary
    duplicate / out-of-order candles ............ TestCandleAdmission
    deterministic repeated evaluation ........... TestDeterminism
    state restore -> continued evaluation ....... TestStateRestore
    no application / exchange imports ........... TestModuleIndependence
    identity MANUAL_SMC / 1.0.0 ................. TestIdentity

Plus TestNonAtomicPersistence (requirement 11 — the strategy must not pretend
the lifecycle mutation and the watermark advance are one operation) and
TestOrchestrationOnly (the module adds no strategy rules of its own).

The candle sequences are hand-built because the published BTC reference window
does not contain every transition Step 6 must prove (a foreign lock, a torn
watermark, a second resting OB alive at the moment of a fill). Every sequence
is OHLC-consistent — `validate_candle` is stricter than the lifecycle, which
never inspects `open` except through the scanner, so the older lifecycle
fixtures (`open` below `low`) are deliberately NOT reused here.
"""

from __future__ import annotations

import ast
import dataclasses
import io
import json
import math
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quantedge.strategy.manual_smc.lifecycle import (
    ACTIVATION_MODE_ORACLE_C,
    OUTCOME_SL,
    OUTCOME_TP,
    REASON_DUAL_TOUCH,
    ManualLifecycleEventType as ET,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBState as S,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.portfolio import (
    LockRejectionCode,
    PortfolioLock,
)
from quantedge.strategy.manual_smc.quantization import (
    QuantizedBracket,
    TickSizeSpec,
)
from quantedge.strategy.manual_smc.sizing import ContractSpecRegistry
from quantedge.strategy.manual_smc.state import CandleWatermark, StateError
from quantedge.strategy.manual_smc.strategy import (
    ATOMICITY_NOTE,
    PERSISTENCE_IS_ATOMIC,
    TP_SOURCE,
    CandleOrderError,
    DuplicateCandleError,
    GlobalOrderError,
    InvalidCandleError,
    ManualSMCBlocked,
    ManualSMCClose,
    ManualSMCEvaluation,
    ManualSMCFill,
    ManualSMCSetup,
    ManualSMCStrategy,
    OutOfOrderCandleError,
    PortfolioLockDesyncError,
    StrategyStateError,
    TornStateError,
    validate_candle,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
MODULE_PATH = (Path(__file__).parent.parent / "src" / "quantedge" / "strategy"
               / "manual_smc" / "strategy.py")
MODULE_SRC = MODULE_PATH.read_text(encoding="utf-8")

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ts(bar_idx: int) -> datetime:
    """Synthetic 1h clock. 1h is the Manual SMC timeframe."""
    return BASE + timedelta(hours=bar_idx)


@dataclass(frozen=True)
class FakeSpec:
    """
    Minimal structural `TickSizeSpec`.

    The real `ProductSpecification` is exercised separately in
    `TestQuantizationBoundary`; this stub keeps the bulk of the suite free of
    `quantedge.execution` (and therefore of httpx and the credential crypto).
    """
    tick_size: Decimal


REAL_TICKS = {
    "BTCUSD": Decimal("0.5"),
    "ETHUSD": Decimal("0.05"),
    # Authoritative Delta India tick; the pre-registry product table said 0.01.
    "SOLUSD": Decimal("0.0001"),
    "XRPUSD": Decimal("0.0001"),
}


def _specs(*assets: str):
    return {a: FakeSpec(REAL_TICKS[a]) for a in assets}


# ---------------------------------------------------------------------------
# THIS FILE PINS THE ORACLE (RESEARCH) ACTIVATION MODE — deliberately.
# ---------------------------------------------------------------------------
# Every candle sequence below is a Mode-C script: a probe candle, then a
# pullback candle, then the fill. It was authored before the manual
# specification's first-touch rule existed, and under that rule the SAME rows
# mean something different (bar 2 both touches the zone and reaches the 25%
# entry, so it fills two bars earlier and the probe/pullback candles are no
# longer gates at all).
#
# Rather than delete or rewrite these acceptance tests, they keep testing what
# they were written to test — the orchestration layer over the ORACLE-faithful
# activation mode, with the oracle's own 0.60% take profit — by naming that mode
# explicitly. That preserves every assertion in this file verbatim AND keeps the
# research path provably reproducible.
#
# The PRODUCTION policy (first touch -> 3-candle window -> permanent
# invalidation, an authorized 0.60% TP — the same take profit as the oracle, so
# ORACLE_KW above pins the ACTIVATION MODE and not the TP) is what
# `ManualSMCStrategy()` does by default, and it
# is covered by `test_manual_smc_first_touch_window.py`.
ORACLE_KW = {
    "activation_mode": ACTIVATION_MODE_ORACLE_C,
    "config": ManualSpecConfig(),
}


# A SHORT setup. Origin bar 0 is bullish, so ob_top = origin.close = 105.0 and
# ob_bottom = origin.low = 99.0 (never origin.high).
#   width 6.0 -> proximal 99.0, distal 105.0
#   entry = 99.0 + 0.25 * 6.0 = 100.5, sl = 105.0, tp = 100.5 * 0.994 = 99.897
SHORT_ROWS = [
    (0, 100.0, 106.0, 99.0, 105.0),    # bullish origin
    (1, 104.0, 104.5, 97.0, 98.0),     # BOS: close 98.0 < ob_bottom 99.0
    (2, 98.0, 101.0, 97.5, 100.0),     # probe close 100.0 > proximal + touch
    (3, 100.0, 100.2, 98.0, 98.5),     # pullback close 98.5 -> displacement
    (4, 99.6, 101.0, 99.5, 100.0),     # high 101.0 >= entry 100.5 -> FILL
    (5, 100.0, 100.5, 99.5, 99.8),     # low 99.5 <= tp 99.897 -> TP close
]
SHORT_OB_ID = "MANUAL_BTCUSD_SHORT_0_1"
SHORT_ENTRY, SHORT_SL, SHORT_TP = 100.5, 105.0, 99.897

# The LONG mirror. Origin bar 0 is bearish, so ob_top = origin.high = 106.0 and
# ob_bottom = origin.close = 100.0 (never origin.low).
#   width 6.0 -> proximal 106.0, distal 100.0
#   entry = 106.0 - 0.25 * 6.0 = 104.5, sl = 100.0, tp = 104.5 * 1.006
LONG_ROWS = [
    (0, 105.0, 106.0, 99.0, 100.0),    # bearish origin
    (1, 101.0, 107.5, 100.5, 107.0),   # BOS: close 107.0 > ob_top 106.0
    (2, 107.0, 107.2, 104.0, 105.0),   # probe close 105.0 < proximal + touch
    (3, 105.0, 107.0, 104.8, 106.5),   # pullback close 106.5 -> displacement
    (4, 105.5, 106.0, 104.0, 105.0),   # low 104.0 <= entry 104.5 -> FILL
    (5, 105.0, 105.2, 104.9, 105.1),   # high 105.2 >= tp 105.127 -> TP close
]
LONG_OB_ID = "MANUAL_BTCUSD_LONG_0_1"
LONG_ENTRY, LONG_SL, LONG_TP = 104.5, 100.0, 105.127


def _new(assets=("BTCUSD",), **kwargs) -> ManualSMCStrategy:
    """A strategy with tick specs for `assets` unless overridden."""
    kwargs.setdefault("tick_specs", _specs(*assets))
    return ManualSMCStrategy(assets=list(assets), **{**ORACLE_KW, **kwargs})


def _drive(strategy: ManualSMCStrategy, asset: str, rows):
    """Feed (bar_idx, o, h, l, c) rows for ONE asset; return the evaluations."""
    return [strategy.evaluate_closed_candle(asset, b, _ts(b), o, h, l, c)
            for (b, o, h, l, c) in rows]


def _events(evaluations):
    return [(ev.bar_idx, e.event_type) for ev in evaluations for e in ev.events]


def _kinds(evaluations, event_type):
    return [b for (b, k) in _events(evaluations) if k is event_type]


def _only_fill(evaluations) -> ManualSMCFill:
    fills = [ev.filled for ev in evaluations if ev.filled is not None]
    assert len(fills) == 1, f"expected exactly one fill, got {len(fills)}"
    return fills[0]


def _only_close(evaluations) -> ManualSMCClose:
    closes = [ev.closed for ev in evaluations if ev.closed is not None]
    assert len(closes) == 1, f"expected exactly one close, got {len(closes)}"
    return closes[0]


def _code_without_strings() -> str:
    """Source with every comment and string literal removed."""
    skip = {tokenize.COMMENT, tokenize.STRING}
    for name in ("FSTRING_MIDDLE",):
        tok_type = getattr(tokenize, name, None)
        if tok_type is not None:
            skip.add(tok_type)
    pieces = []
    for tok in tokenize.generate_tokens(io.StringIO(MODULE_SRC).readline):
        if tok.type in skip:
            continue
        pieces.append(tok.string)
    return " ".join(pieces)


def _imported_modules():
    """Every module named by an import statement, via the AST (not a regex)."""
    out = set()
    for node in ast.walk(ast.parse(MODULE_SRC)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return sorted(out)


def _identifiers():
    """
    Every NAME token in the module.

    Substring checks against source text are unreliable here — `_code_without_
    strings` joins tokens with spaces, so `"os."` could never match even if
    `os.environ` were present. A token set cannot be fooled that way.
    """
    return {tok.string
            for tok in tokenize.generate_tokens(io.StringIO(MODULE_SRC).readline)
            if tok.type == tokenize.NAME}


RESULT_TYPES = (ManualSMCSetup, ManualSMCFill, ManualSMCClose,
                ManualSMCBlocked, ManualSMCEvaluation)


# ---------------------------------------------------------------------------
# BOS -> displacement -> entry, both directions
# ---------------------------------------------------------------------------
class TestShortPath:
    """A SHORT setup from BOS to settled close, through the orchestrator."""

    def test_event_sequence_is_the_lifecycles_own_order(self):
        evals = _drive(_new(), "BTCUSD", SHORT_ROWS)
        assert _events(evals) == [
            (1, ET.OB_CREATED),
            (2, ET.PRE_DISPLACEMENT_TOUCH),
            (2, ET.PROBE_CONFIRMED),
            (3, ET.DISPLACEMENT_CONFIRMED),
            (4, ET.ENTRY_FILLED),
            (5, ET.TRADE_CLOSED),
        ]

    def test_ob_geometry_is_the_raw_oracle_geometry(self):
        evals = _drive(_new(), "BTCUSD", SHORT_ROWS[:4])
        setup = evals[-1].setups[0]
        assert (setup.ob_id, setup.direction) == (SHORT_OB_ID, "SHORT")
        assert setup.ob_top == 105.0        # origin.close, never origin.high
        assert setup.ob_bottom == 99.0      # origin.low
        assert setup.ob_width == 6.0
        assert setup.proximal == 99.0 and setup.distal == 105.0
        assert setup.entry_price == SHORT_ENTRY
        assert setup.sl_price == SHORT_SL == setup.distal
        assert setup.tp_price == SHORT_TP
        assert setup.origin_bar_idx == 0 and setup.bos_bar_idx == 1

    def test_no_limit_is_live_before_displacement(self):
        evals = _drive(_new(), "BTCUSD", SHORT_ROWS[:3])
        setup = evals[-1].setups[0]
        assert setup.state is S.AWAITING_DISPLACEMENT
        assert setup.limit_is_live is False
        assert setup.is_executable is False
        assert setup.limit_active_from_bar is None
        assert setup.probe_confirmed is True          # bar 2 probed
        assert setup.pre_displacement_touches == 1    # counted, never filled
        assert all(ev.filled is None for ev in evals)

    def test_displacement_arms_the_limit_for_the_next_bar_only(self):
        evals = _drive(_new(), "BTCUSD", SHORT_ROWS[:4])
        setup = evals[-1].setups[0]
        assert setup.state is S.LIMIT_RESTING
        assert setup.displacement_confirmed_bar == 3
        assert setup.limit_active_from_bar == 4       # break+1, not this bar
        assert setup.limit_is_live is True
        assert setup.is_executable is True            # resting + on-grid bracket
        assert evals[-1].has_active_trade is False

    def test_entry_fills_on_a_wick_touch_of_the_25pct_level(self):
        evals = _drive(_new(), "BTCUSD", SHORT_ROWS[:5])
        fill = _only_fill(evals)
        assert fill.ob_id == SHORT_OB_ID and fill.direction == "SHORT"
        assert fill.bar_idx == 4 and fill.ts == _ts(4)
        assert fill.entry_price == SHORT_ENTRY       # the limit price, not close
        assert fill.sl_price == SHORT_SL
        assert fill.tp_price == SHORT_TP
        assert evals[-1].active_trade is not None
        assert evals[-1].setups == ()                # filled OB is no candidate

    def test_close_settles_against_the_balance_and_frees_the_slot(self):
        strategy = _new()
        evals = _drive(strategy, "BTCUSD", SHORT_ROWS)
        closed = _only_close(evals)
        assert closed.outcome == OUTCOME_TP
        assert closed.is_ambiguous is False
        assert closed.balance_before == 10.0         # the $10 starting capital
        assert closed.balance_after == closed.settlement.ending_balance
        assert closed.balance_after > closed.balance_before
        assert strategy.account_balance == closed.balance_after
        assert strategy.open_sizing is None
        assert strategy.has_active_trade() is False
        assert evals[-1].lock_holder is None

    def test_distal_wick_breach_while_resting_invalidates_and_reports_it(self):
        # high 105.0 touches the distal exactly; close 100.0 admits no new BOS.
        rows = SHORT_ROWS[:4] + [(4, 100.0, 105.0, 99.0, 100.0)]
        evals = _drive(_new(), "BTCUSD", rows)
        assert evals[-1].invalidated == (SHORT_OB_ID,)
        assert evals[-1].filled is None
        assert SHORT_OB_ID not in [s.ob_id for s in evals[-1].setups]
        assert 4 in _kinds(evals, ET.INVALIDATED)


class TestLongPath:
    """The LONG mirror. Same orchestration, opposite geometry."""

    def test_event_sequence_matches_the_short_path(self):
        evals = _drive(_new(), "BTCUSD", LONG_ROWS)
        assert _events(evals) == [
            (1, ET.OB_CREATED),
            (2, ET.PRE_DISPLACEMENT_TOUCH),
            (2, ET.PROBE_CONFIRMED),
            (3, ET.DISPLACEMENT_CONFIRMED),
            (4, ET.ENTRY_FILLED),
            (5, ET.TRADE_CLOSED),
        ]

    def test_ob_geometry_uses_high_and_close_not_low(self):
        evals = _drive(_new(), "BTCUSD", LONG_ROWS[:4])
        setup = evals[-1].setups[0]
        assert (setup.ob_id, setup.direction) == (LONG_OB_ID, "LONG")
        assert setup.ob_top == 106.0        # origin.high
        assert setup.ob_bottom == 100.0     # origin.close, never origin.low
        assert setup.proximal == 106.0 and setup.distal == 100.0
        assert setup.entry_price == LONG_ENTRY
        assert setup.sl_price == LONG_SL == setup.distal
        assert setup.tp_price == LONG_TP

    def test_fill_and_close(self):
        strategy = _new()
        evals = _drive(strategy, "BTCUSD", LONG_ROWS)
        fill = _only_fill(evals)
        assert (fill.bar_idx, fill.entry_price) == (4, LONG_ENTRY)
        closed = _only_close(evals)
        assert closed.outcome == OUTCOME_TP
        assert strategy.account_balance == closed.balance_after
        assert strategy.has_active_trade() is False

    def test_distal_invalidation_reads_the_low_not_the_high(self):
        # low 100.0 touches the LONG distal; close 106.0 admits no new BOS.
        rows = LONG_ROWS[:4] + [(4, 105.0, 106.0, 100.0, 106.0)]
        evals = _drive(_new(), "BTCUSD", rows)
        assert evals[-1].invalidated == (LONG_OB_ID,)
        assert evals[-1].filled is None

    def test_stop_loss_close_debits_the_balance(self):
        # After the fill, low 99.0 breaches the LONG SL at 100.0.
        rows = LONG_ROWS[:5] + [(5, 104.0, 104.5, 99.0, 100.0)]
        strategy = _new()
        evals = _drive(strategy, "BTCUSD", rows)
        closed = _only_close(evals)
        assert closed.outcome == OUTCOME_SL
        assert closed.balance_after < closed.balance_before
        assert strategy.account_balance == closed.balance_after

    def test_dual_touch_resolves_conservatively_to_the_stop(self):
        # One candle spanning both the LONG TP (105.127) and the SL (100.0).
        rows = LONG_ROWS[:5] + [(5, 104.0, 106.0, 99.0, 100.0)]
        evals = _drive(_new(), "BTCUSD", rows)
        closed = _only_close(evals)
        assert closed.outcome == OUTCOME_SL
        assert closed.is_ambiguous is True
        assert closed.exit.reason_for_exit == REASON_DUAL_TOUCH


class TestIdentity:
    """`MANUAL_SMC` / `1.0.0` — never `SMC`, never `2.1`."""

    def test_the_constants_are_not_the_application_strategys(self):
        assert MANUAL_SMC_STRATEGY_NAME == "MANUAL_SMC" != "SMC"
        assert MANUAL_SMC_STRATEGY_VERSION == "1.0.0" != "2.1"

    def test_the_strategy_reports_its_own_identity(self):
        strategy = _new()
        assert strategy.strategy_name == "MANUAL_SMC"
        assert strategy.strategy_version == "1.0.0"

    def test_every_result_object_carries_the_identity(self):
        evals = _drive(_new(), "BTCUSD", SHORT_ROWS)
        carriers = list(evals)
        carriers += [s for ev in evals for s in ev.setups]
        carriers += [ev.filled for ev in evals if ev.filled]
        carriers += [ev.closed for ev in evals if ev.closed]
        carriers += [e for ev in evals for e in ev.events]
        assert len(carriers) > 10
        for obj in carriers:
            assert obj.strategy_name == "MANUAL_SMC"
            assert obj.strategy_version == "1.0.0"

    def test_identity_fields_are_not_constructor_dependent(self):
        """Identity is a class default, not something a caller can set."""
        for cls in (ManualSMCSetup, ManualSMCFill, ManualSMCClose,
                    ManualSMCEvaluation):
            by_name = {f.name: f for f in dataclasses.fields(cls)}
            assert by_name["strategy_name"].default == "MANUAL_SMC"
            assert by_name["strategy_version"].default == "1.0.0"


# ---------------------------------------------------------------------------
# The single trade slot (safety rules #13 and #14)
# ---------------------------------------------------------------------------
# BTCUSD fills at bar 4 and closes (TP) at bar 8. ETHUSD runs the same SHORT
# setup shifted +2 bars, so its limit is armed from bar 6 and it touches its
# entry on bars 6..10 — candles STRICTLY LATER than the BTC fill. Those touches
# must be refused while the slot is taken, and the OB must stay alive.
_BTC = {0: (100.0, 106.0, 99.0, 105.0), 1: (104.0, 104.5, 97.0, 98.0),
        2: (98.0, 101.0, 97.5, 100.0), 3: (100.0, 100.2, 98.0, 98.5),
        4: (99.6, 101.0, 99.5, 100.0), 8: (100.0, 100.5, 99.5, 99.8)}
for _b in (5, 6, 7):
    _BTC[_b] = (100.0, 100.0, 100.0, 100.0)     # inert: no origin, no exit
for _b in (9, 10):
    _BTC[_b] = (99.8, 99.8, 99.8, 99.8)

_ETH = {2: (100.0, 106.0, 99.0, 105.0), 3: (104.0, 104.5, 97.0, 98.0),
        4: (98.0, 101.0, 97.5, 100.0), 5: (100.0, 100.2, 98.0, 98.5)}
for _b in (6, 7, 8, 9, 10):
    _ETH[_b] = (99.6, 101.0, 99.5, 100.0)       # touches the entry every bar

ETH_OB_ID = "MANUAL_ETHUSD_SHORT_2_3"


def _run_two_assets(last_bar: int = 10, strategy=None):
    """Interleave the assets in global chronological order, BTC then ETH."""
    strategy = strategy or _new(assets=("BTCUSD", "ETHUSD"))
    out = []
    for bar_idx in range(0, last_bar + 1):
        for asset, rows in (("BTCUSD", _BTC), ("ETHUSD", _ETH)):
            if bar_idx not in rows:
                continue
            o, h, l, c = rows[bar_idx]
            out.append(strategy.evaluate_closed_candle(
                asset, bar_idx, _ts(bar_idx), o, h, l, c))
    return strategy, out


class TestPortfolioLockIntegration:
    """The lifecycle's gate and `PortfolioLock` must agree, always."""

    def test_fill_acquires_the_lock_for_the_filling_ob(self):
        strategy = _new()
        evals = _drive(strategy, "BTCUSD", SHORT_ROWS[:5])
        fill = _only_fill(evals)
        holder = strategy.lock.active_trade
        assert holder is not None
        assert holder.ob_id == fill.ob_id == SHORT_OB_ID
        assert holder.asset == "BTCUSD" and holder.direction == "SHORT"
        assert holder.acquired_bar_idx == 4 and holder.acquired_at == _ts(4)
        assert fill.lock_holder.token == holder.token
        assert evals[-1].lock_holder.token == holder.token

    def test_a_second_asset_is_refused_while_a_trade_is_open(self):
        _strategy, evals = _run_two_assets(last_bar=7)
        blocked = [b for ev in evals for b in ev.blocked]
        assert [b.bar_idx for b in blocked] == [6, 7]
        assert {b.ob_id for b in blocked} == {ETH_OB_ID}
        for b in blocked:
            assert b.lock_rejection is not None
            assert b.lock_rejection.code is LockRejectionCode.ACTIVE_TRADE_OPEN
            assert b.lock_rejection.held_by.ob_id == SHORT_OB_ID

    def test_a_refused_ob_stays_resting_and_still_fills_later(self):
        _strategy, evals = _run_two_assets(last_bar=7)
        eth = [s for s in evals[-1].setups if s.ob_id == ETH_OB_ID]
        assert len(eth) == 1 and eth[0].state is S.LIMIT_RESTING
        _strategy2, evals2 = _run_two_assets(last_bar=10)
        fills = [ev.filled for ev in evals2 if ev.filled]
        assert [(f.bar_idx, f.ob_id) for f in fills] == [
            (4, SHORT_OB_ID), (9, ETH_OB_ID)]

    def test_never_two_active_trades(self):
        strategy, evals = _run_two_assets(last_bar=10)
        for ev in evals:
            holders = [h for h in (ev.lock_holder,) if h is not None]
            assert len(holders) <= 1
            assert (ev.active_trade is not None) == (ev.lock_holder is not None)
        assert strategy.lock.is_held() is False

    def test_the_close_candle_itself_still_refuses_reentry(self):
        """The BTC close and the ETH touch share bar 8's timestamp."""
        _strategy, evals = _run_two_assets(last_bar=8)
        blocked = [b for ev in evals for b in ev.blocked]
        bar8 = [b for b in blocked if b.bar_idx == 8]
        assert len(bar8) == 1
        assert bar8[0].lock_rejection.code is (
            LockRejectionCode.INTRA_CANDLE_AMBIGUITY)

    def test_close_releases_with_the_holders_token_and_outcome(self):
        strategy = _new()
        evals = _drive(strategy, "BTCUSD", SHORT_ROWS)
        fill = _only_fill(evals)
        closed = _only_close(evals)
        assert closed.lock_released_token == fill.lock_holder.token
        released = [e for e in strategy.lock.events if e.event == "RELEASED"]
        assert len(released) == 1 and released[0].ob_id == SHORT_OB_ID
        assert strategy.lock.last_closed_dt == _ts(5)


# A second scenario: ETHUSD reaches LIMIT_RESTING first and never touches its
# entry, while BTCUSD (shifted +2) fills at bar 6. At that moment a foreign
# resting limit is alive, which is what `cancel_ob_ids` must report.
_ETH_IDLE = {0: (100.0, 106.0, 99.0, 105.0), 1: (104.0, 104.5, 97.0, 98.0),
             2: (98.0, 101.0, 97.5, 100.0), 3: (100.0, 100.2, 98.0, 98.5)}
for _b in (4, 5, 6):
    _ETH_IDLE[_b] = (100.0, 100.2, 98.5, 99.0)  # never reaches entry 100.5

_BTC_LATE = {2: (100.0, 106.0, 99.0, 105.0), 3: (104.0, 104.5, 97.0, 98.0),
             4: (98.0, 101.0, 97.5, 100.0), 5: (100.0, 100.2, 98.0, 98.5),
             6: (99.6, 101.0, 99.5, 100.0)}
ETH_IDLE_OB_ID = "MANUAL_ETHUSD_SHORT_0_1"
BTC_LATE_OB_ID = "MANUAL_BTCUSD_SHORT_2_3"


def _run_resting_then_fill(strategy=None):
    strategy = strategy or _new(assets=("BTCUSD", "ETHUSD"))
    out = []
    for bar_idx in range(0, 7):
        for asset, rows in (("BTCUSD", _BTC_LATE), ("ETHUSD", _ETH_IDLE)):
            if bar_idx not in rows:
                continue
            o, h, l, c = rows[bar_idx]
            out.append(strategy.evaluate_closed_candle(
                asset, bar_idx, _ts(bar_idx), o, h, l, c))
    return strategy, out


class TestRestingOrdersAreReportedNotCancelled:
    """Safety rule #9 — reported here, acted on by the adapter (Step 7)."""

    def test_a_fill_names_every_other_resting_ob(self):
        _strategy, evals = _run_resting_then_fill()
        fill = _only_fill(evals)
        assert fill.ob_id == BTC_LATE_OB_ID and fill.bar_idx == 6
        assert fill.cancel_ob_ids == (ETH_IDLE_OB_ID,)

    def test_the_named_ob_is_still_live_and_untouched(self):
        """This module cancels nothing; it only says what must be cancelled."""
        strategy, evals = _run_resting_then_fill()
        assert ETH_IDLE_OB_ID in strategy.lifecycle.live_obs
        assert strategy.lifecycle.live_obs[ETH_IDLE_OB_ID].state is (
            S.LIMIT_RESTING)
        assert evals[-1].invalidated == ()

    def test_an_invalidated_ob_is_reported_for_withdrawal(self):
        rows = SHORT_ROWS[:4] + [(4, 100.0, 105.0, 99.0, 100.0)]
        strategy = _new()
        evals = _drive(strategy, "BTCUSD", rows)
        assert evals[-1].invalidated == (SHORT_OB_ID,)
        # Reported once, on the candle that killed it, and then gone from the
        # pool — the adapter has exactly one chance to withdraw the order.
        assert SHORT_OB_ID not in strategy.lifecycle.live_obs
        assert evals[-2].invalidated == ()


class TestLockDesyncFailsClosed:
    """
    If the lifecycle and the lock disagree, refuse — never pick a winner.

    Each case simulates something outside this strategy having touched the lock,
    which is the only way the two can diverge (`PortfolioLock.evaluate` mirrors
    `ManualSMCLifecycle._entry_blocked` expression for expression).
    """

    def test_a_foreign_holder_blocks_the_fill_instead_of_doubling_up(self):
        foreign = PortfolioLock()
        foreign.acquire(asset="ZZZUSD", ob_id="FOREIGN", direction="LONG",
                        ts=_ts(0), bar_idx=0)
        strategy = _new(lock=foreign)
        _drive(strategy, "BTCUSD", SHORT_ROWS[:4])
        with pytest.raises(PortfolioLockDesyncError, match="refused it"):
            _drive(strategy, "BTCUSD", SHORT_ROWS[4:5])
        assert foreign.active_trade.ob_id == "FOREIGN"   # not overwritten

    def test_a_freed_lock_makes_a_blocked_entry_a_refusal(self):
        strategy, _evals = _run_two_assets(last_bar=5)
        _drive(strategy, "BTCUSD", [(6, 100.0, 100.0, 100.0, 100.0)])
        strategy.lock = PortfolioLock()                  # somebody freed it
        with pytest.raises(PortfolioLockDesyncError, match="available"):
            strategy.evaluate_closed_candle("ETHUSD", 6, _ts(6),
                                            *_ETH[6])

    def test_a_freed_lock_makes_a_close_a_refusal(self):
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:5])
        strategy.lock = PortfolioLock()
        with pytest.raises(PortfolioLockDesyncError, match="held by None"):
            _drive(strategy, "BTCUSD", SHORT_ROWS[5:6])

    def test_a_refused_candle_does_not_advance_the_watermark(self):
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:5])
        strategy.lock = PortfolioLock()
        with pytest.raises(PortfolioLockDesyncError):
            _drive(strategy, "BTCUSD", SHORT_ROWS[5:6])
        assert strategy.watermark.last("BTCUSD").bar_idx == 4
        assert strategy.unpersisted_strategy_state()["last_global_ts"] == _ts(4)

    def test_a_close_without_captured_sizing_refuses_and_says_why(self):
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:5])
        strategy._open_sizing = None                     # as after a bad restore
        with pytest.raises(StrategyStateError,
                           match="restored_balance_at_fill"):
            _drive(strategy, "BTCUSD", SHORT_ROWS[5:6])


# ---------------------------------------------------------------------------
# Take profit: absolute price only (requirement 5)
# ---------------------------------------------------------------------------
class TestAbsoluteTakeProfit:
    """
    TP is `entry * (1 -+ 0.006)`, an absolute price from the OB.

    The application's `StrategyDecision.take_profit_target_pct` is 60.0 and
    means return ON MARGIN. The collision is easy to miss because
    `ManualSpecConfig.fixed_tp_market_pct` is 0.60 as well, and
    `PositionSizing.gross_tp_return_pct` is `0.60 * applied_leverage` — the two
    numbers agree only at exactly 100x leverage. These tests pin the price.
    """

    def test_short_tp_is_the_market_move_not_a_return_target(self):
        cfg = ManualSpecConfig()
        assert cfg.fixed_tp_market_pct == 0.60
        fill = _only_fill(_drive(_new(), "BTCUSD", SHORT_ROWS[:5]))
        assert fill.tp_price == SHORT_ENTRY * (1.0 - 0.60 / 100.0)
        assert fill.tp_price == SHORT_TP
        # A 60%-of-price reading would be a different universe.
        assert fill.tp_price != pytest.approx(SHORT_ENTRY * (1.0 - 0.60), rel=1e-6)

    def test_long_tp_is_the_market_move_not_a_return_target(self):
        fill = _only_fill(_drive(_new(), "BTCUSD", LONG_ROWS[:5]))
        assert fill.tp_price == LONG_ENTRY * (1.0 + 0.60 / 100.0)
        assert fill.tp_price == LONG_TP

    def test_the_roe_percentage_is_not_the_tp_and_the_two_differ_here(self):
        fill = _only_fill(_drive(_new(), "BTCUSD", SHORT_ROWS[:5]))
        sizing = fill.sizing
        assert sizing.applied_leverage < 100.0            # not the 100x corner
        assert sizing.gross_tp_return_pct == 0.60 * sizing.applied_leverage
        assert sizing.gross_tp_return_pct != 60.0
        assert sizing.tp_price == fill.tp_price           # the price, unchanged

    def test_the_tp_price_comes_from_the_ob_record_itself(self):
        strategy = _new()
        evals = _drive(strategy, "BTCUSD", SHORT_ROWS[:4])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        assert evals[-1].setups[0].tp_price == ob.tp_price
        fill = _only_fill(_drive(strategy, "BTCUSD", SHORT_ROWS[4:5]))
        assert fill.tp_price == ob.tp_price

    def test_provenance_is_recorded_on_setups_and_fills(self):
        assert TP_SOURCE == "ABSOLUTE_OB_TP_PRICE"
        evals = _drive(_new(), "BTCUSD", SHORT_ROWS[:5])
        setup = evals[3].setups[0]
        assert setup.tp_source == TP_SOURCE
        assert _only_fill(evals).tp_source == TP_SOURCE

    def test_no_result_type_carries_a_percentage_take_profit(self):
        allowed = {"tp_price", "tp_source"}
        for cls in RESULT_TYPES:
            names = {f.name for f in dataclasses.fields(cls)}
            tp_named = {n for n in names
                        if "tp" in n or "take_profit" in n or "target" in n}
            assert tp_named <= allowed, f"{cls.__name__} exposes {tp_named}"
            assert not any("roe" in n for n in names)

    def test_the_module_never_mentions_the_application_percentage_field(self):
        code = _code_without_strings()
        for banned in ("take_profit_target_pct", "target_roe_pct",
                       "max_loss_pct", "take_profit_price",
                       "StrategyDecision", "SetupState"):
            assert banned not in code, f"{banned!r} appears in strategy.py"

    def test_the_exit_price_on_a_tp_close_is_the_absolute_tp(self):
        closed = _only_close(_drive(_new(), "BTCUSD", SHORT_ROWS))
        assert closed.exit.exit_price == SHORT_TP
        assert closed.exit.tp_price == SHORT_TP


# ---------------------------------------------------------------------------
# Quantization: output boundary only (requirement 4)
# ---------------------------------------------------------------------------
class TestQuantizationBoundary:
    """Quantize at the edge; never touch the oracle's floats."""

    def test_a_resting_setup_carries_an_on_grid_bracket(self):
        setup = _drive(_new(), "BTCUSD", SHORT_ROWS[:4])[-1].setups[0]
        bracket = setup.quantized
        assert isinstance(bracket, QuantizedBracket)
        assert setup.quantization_refusal is None
        assert bracket.tick_size == Decimal("0.5")
        for price in (bracket.entry_price, bracket.sl_price, bracket.tp_price):
            assert price % Decimal("0.5") == 0

    def test_the_bracket_records_the_raw_float_it_came_from(self):
        setup = _drive(_new(), "BTCUSD", SHORT_ROWS[:4])[-1].setups[0]
        bracket = setup.quantized
        assert bracket.raw_entry_price == Decimal(str(SHORT_ENTRY))
        assert bracket.raw_sl_price == Decimal(str(SHORT_SL))
        assert bracket.raw_tp_price == Decimal(str(SHORT_TP))

    def test_the_ob_record_is_never_mutated_by_reporting(self):
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:4])
        ob = strategy.lifecycle.live_obs[SHORT_OB_ID]
        before = dataclasses.asdict(ob)
        for _ in range(3):
            strategy._setup_from_ob(ob)
        assert dataclasses.asdict(ob) == before
        assert isinstance(ob.entry_price, float)
        assert ob.entry_price == SHORT_ENTRY

    def test_raw_floats_survive_on_the_setup_alongside_the_bracket(self):
        setup = _drive(_new(), "BTCUSD", SHORT_ROWS[:4])[-1].setups[0]
        assert isinstance(setup.entry_price, float)
        assert setup.entry_price == SHORT_ENTRY
        assert setup.quantized.entry_price != Decimal(str(SHORT_TP))

    def test_a_missing_tick_spec_yields_a_non_executable_setup(self):
        """Safety rule #15: no default tick, and no guess."""
        strategy = ManualSMCStrategy(assets=["BTCUSD"], **ORACLE_KW)  # no tick_specs
        setup = _drive(strategy, "BTCUSD", SHORT_ROWS[:4])[-1].setups[0]
        assert setup.state is S.LIMIT_RESTING
        assert setup.limit_is_live is True
        assert setup.quantized is None
        assert setup.is_executable is False
        assert "no product specification for BTCUSD" in setup.quantization_refusal
        assert "safety rule #15" in setup.quantization_refusal

    def test_a_missing_tick_spec_does_not_stop_the_lifecycle(self):
        """A refusal is reported, not raised — the candle still processes."""
        strategy = ManualSMCStrategy(assets=["BTCUSD"], **ORACLE_KW)
        evals = _drive(strategy, "BTCUSD", SHORT_ROWS)
        fill = _only_fill(evals)
        assert fill.quantized is None
        assert fill.is_executable is False
        assert fill.quantization_refusal is not None
        assert _only_close(evals).outcome == OUTCOME_TP      # still settled

    def test_a_bad_tick_spec_is_reported_not_raised(self):
        strategy = ManualSMCStrategy(
            assets=["BTCUSD"], tick_specs={"BTCUSD": FakeSpec(Decimal("0"))},
            **ORACLE_KW)
        setup = _drive(strategy, "BTCUSD", SHORT_ROWS[:4])[-1].setups[0]
        assert setup.quantized is None
        assert setup.is_executable is False
        assert setup.quantization_refusal.startswith("InvalidTickSizeError")

    def test_executable_setups_filters_on_both_conditions(self):
        armed = _drive(_new(), "BTCUSD", SHORT_ROWS[:4])[-1]
        assert [s.ob_id for s in armed.executable_setups] == [SHORT_OB_ID]
        awaiting = _drive(_new(), "BTCUSD", SHORT_ROWS[:3])[-1]
        assert awaiting.setups and awaiting.executable_setups == ()

    def test_the_four_real_delta_ticks_are_read_from_the_product_spec(self):
        """The real `ProductSpecification` satisfies the protocol structurally."""
        from quantedge.execution.validation import (
            ProductSpecification, get_product_specification)
        for symbol, tick in REAL_TICKS.items():
            spec = get_product_specification(symbol)
            assert isinstance(spec, ProductSpecification)
            assert isinstance(spec, TickSizeSpec)
            assert spec.tick_size == tick
            strategy = ManualSMCStrategy(assets=[symbol],
                                         tick_specs={symbol: spec},
                                         **ORACLE_KW)
            setup = _drive(strategy, symbol, SHORT_ROWS[:4])[-1].setups[0]
            assert setup.quantized.tick_size == tick
            assert setup.is_executable is True

# ---------------------------------------------------------------------------
# Candle admission: refuse, never silently re-process (requirement 12)
# ---------------------------------------------------------------------------
class TestCandleAdmission:
    """
    A replayed candle is a correctness bug, not a no-op.

    The OB update sweep is not idempotent — re-feeding a candle re-runs the
    entry-touch test and could fill the same limit twice — so every one of
    these paths raises before `lifecycle.process_candle` is reached.
    """

    def test_a_replayed_candle_is_refused(self):
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:3])
        with pytest.raises(DuplicateCandleError) as exc:
            strategy.evaluate_closed_candle("BTCUSD", 2, _ts(2), *SHORT_ROWS[2][1:])
        assert "already" in str(exc.value)
        assert "fill an entry twice" in str(exc.value)

    def test_a_backwards_bar_index_is_refused(self):
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:3])
        with pytest.raises(OutOfOrderCandleError):
            strategy.evaluate_closed_candle("BTCUSD", 1, _ts(1), *SHORT_ROWS[1][1:])

    def test_the_same_bar_index_with_a_new_timestamp_is_refused(self):
        """Bar indices are the identity; a fresh timestamp does not excuse one."""
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:3])
        with pytest.raises(OutOfOrderCandleError):
            strategy.evaluate_closed_candle("BTCUSD", 2, _ts(9), *SHORT_ROWS[2][1:])

    def test_a_regressed_timestamp_is_refused_even_with_a_new_bar_index(self):
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:3])
        with pytest.raises(OutOfOrderCandleError) as exc:
            strategy.evaluate_closed_candle("BTCUSD", 3, _ts(1), *SHORT_ROWS[3][1:])
        assert "is not after the watermark" in str(exc.value)

    def test_gaps_in_the_feed_are_allowed(self):
        """A missing candle is a data property, not state corruption."""
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:2])
        result = strategy.evaluate_closed_candle(
            "BTCUSD", 7, _ts(7), *SHORT_ROWS[2][1:])
        assert result.watermark_advanced is True
        assert strategy.watermark.last("BTCUSD").bar_idx == 7

    def test_a_globally_stale_candle_is_refused_across_assets(self):
        """
        The single trade slot couples the assets.

        ETH has never been seen, so its own watermark says nothing — but a
        candle older than the last globally processed one would let ETH claim a
        slot that BTC's later candles have already contested.
        """
        strategy = _new(("BTCUSD", "ETHUSD"))
        _drive(strategy, "BTCUSD", SHORT_ROWS)
        with pytest.raises(GlobalOrderError) as exc:
            strategy.evaluate_closed_candle(
                "ETHUSD", 0, _ts(3), *SHORT_ROWS[0][1:])
        assert "single trade slot couples the assets" in str(exc.value)

    def test_an_equal_timestamp_on_a_different_asset_is_allowed(self):
        """Assets on the same 1h close share a timestamp. That is normal."""
        strategy = _new(("BTCUSD", "ETHUSD"))
        strategy.evaluate_closed_candle("BTCUSD", 0, _ts(0), *SHORT_ROWS[0][1:])
        result = strategy.evaluate_closed_candle(
            "ETHUSD", 0, _ts(0), *SHORT_ROWS[0][1:])
        assert result.watermark_advanced is True
        assert sorted(strategy.watermark.assets()) == ["BTCUSD", "ETHUSD"]

    def test_a_naive_timestamp_after_an_aware_one_is_refused(self):
        strategy = _new()
        strategy.evaluate_closed_candle("BTCUSD", 0, _ts(0), *SHORT_ROWS[0][1:])
        naive = datetime(2026, 1, 1, 1)
        with pytest.raises(InvalidCandleError) as exc:
            strategy.evaluate_closed_candle("BTCUSD", 1, naive, *SHORT_ROWS[1][1:])
        assert "cannot compare candle timestamp" in str(exc.value)

    @pytest.mark.parametrize("bad", [
        ("", 0, "asset"),                       # empty asset
        (None, 0, "asset"),
        (b"BTCUSD", 0, "asset"),
        ("BTCUSD", 1.0, "bar_idx"),             # float bar index
        ("BTCUSD", True, "bar_idx"),            # bool is not an int here
        ("BTCUSD", "0", "bar_idx"),
    ])
    def test_the_asset_and_bar_index_are_type_checked(self, bad):
        asset, bar_idx, where = bad
        with pytest.raises(InvalidCandleError) as exc:
            _new().evaluate_closed_candle(
                asset, bar_idx, _ts(0), *SHORT_ROWS[0][1:])
        assert str(exc.value).startswith(where)

    @pytest.mark.parametrize("leg", ["o", "h", "l", "c"])
    @pytest.mark.parametrize("value", [
        float("nan"), float("inf"), float("-inf"), 0.0, -1.0, None, "100.0",
    ])
    def test_every_price_leg_must_be_a_finite_positive_number(self, leg, value):
        names = {"o": "open", "h": "high", "l": "low", "c": "close"}
        row = dict(zip(("o", "h", "l", "c"), SHORT_ROWS[0][1:]))
        row[leg] = value
        with pytest.raises(InvalidCandleError) as exc:
            _new().evaluate_closed_candle("BTCUSD", 0, _ts(0), **row)
        assert str(exc.value).startswith(names[leg])

    @pytest.mark.parametrize("row", [
        (100.0, 99.0, 98.0, 99.5),      # high below open
        (100.0, 101.0, 100.5, 100.2),   # low above open
        (100.0, 100.5, 99.0, 101.0),    # close above high
        (100.0, 101.0, 99.5, 99.0),     # close below low
        (100.0, 98.0, 99.0, 98.5),      # high below low
    ])
    def test_an_inconsistent_candle_is_refused(self, row):
        """
        `low <= open, close <= high` is the definition of a candle.

        It is deliberately stricter than the lifecycle, which never inspects
        `open` outside the scanner: an inverted bar can both miss a TP and hit
        an SL on the same wick predicates.
        """
        with pytest.raises(InvalidCandleError) as exc:
            _new().evaluate_closed_candle("BTCUSD", 0, _ts(0), *row)
        assert "bar 0" in str(exc.value)

    def test_validate_candle_normalises_without_rounding(self):
        out = validate_candle("BTCUSD", 3, _ts(3), 100, 101, 99, 100.5)
        assert out == ("BTCUSD", 3, _ts(3), 100.0, 101.0, 99.0, 100.5)
        assert all(isinstance(x, float) for x in out[3:])

    def test_a_doji_is_a_valid_candle(self):
        """Flat OHLC is degenerate but real; it must not be refused."""
        result = _new().evaluate_closed_candle(
            "BTCUSD", 0, _ts(0), 100.0, 100.0, 100.0, 100.0)
        assert result.events == () and result.setups == ()

    @pytest.mark.parametrize("call", [
        ("BTCUSD", 3, 3),        # duplicate
        ("BTCUSD", 2, 2),        # backwards bar index
        ("BTCUSD", 4, 1),        # regressed timestamp
    ])
    def test_a_refused_candle_mutates_nothing(self, call):
        """
        The pre-check runs BEFORE `process_candle`, so nothing has moved.

        This is why the check is duplicated from `CandleWatermark.advance`
        rather than delegated to it: `advance()` mutates, so it cannot be the
        gate that runs first.
        """
        asset, bar_idx, when = call
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:4])
        before = json.dumps(strategy.capture_state(), sort_keys=True)
        with pytest.raises(CandleOrderError):
            strategy.evaluate_closed_candle(
                asset, bar_idx, _ts(when), *SHORT_ROWS[4][1:])
        assert json.dumps(strategy.capture_state(), sort_keys=True) == before
        assert strategy.watermark.last("BTCUSD").bar_idx == 3
        assert strategy._last_global_ts == _ts(3)
        assert strategy.account_balance == 10.0
        assert strategy.lock.active_trade is None

    def test_a_refused_candle_cannot_double_fill_an_armed_limit(self):
        """The concrete harm the refusal prevents."""
        strategy = _new()
        evals = _drive(strategy, "BTCUSD", SHORT_ROWS[:5])
        fill = _only_fill(evals)
        assert fill.ob_id == SHORT_OB_ID
        for bad_bar, bad_ts in ((4, 4), (4, 8), (3, 3)):
            with pytest.raises(CandleOrderError):
                strategy.evaluate_closed_candle(
                    "BTCUSD", bad_bar, _ts(bad_ts), *SHORT_ROWS[4][1:])
        assert len(strategy.lifecycle.exits) == 0
        assert strategy.lifecycle.active_trade.ob.ob_id == SHORT_OB_ID
        assert strategy.lock.active_trade.ob_id == SHORT_OB_ID
        assert strategy.account_balance == 10.0

    def test_every_admitted_candle_advances_the_watermark(self):
        for ev in _drive(_new(), "BTCUSD", SHORT_ROWS):
            assert ev.watermark_advanced is True

# ---------------------------------------------------------------------------
# Determinism (requirement 1)
# ---------------------------------------------------------------------------
class TestDeterminism:
    """
    Same construction arguments plus same candles -> identical everything.

    Nothing in the module reads a clock, a random source, a uuid or the
    environment, so equality of the whole projection is a fair test. Lock
    tokens are a per-account counter (`ACCOUNT#1`), which is why they compare
    equal across runs.
    """

    def test_two_identical_runs_produce_identical_evaluations(self):
        first = _drive(_new(), "BTCUSD", SHORT_ROWS)
        second = _drive(_new(), "BTCUSD", SHORT_ROWS)
        assert first == second

    def test_two_identical_runs_produce_identical_balances(self):
        a, b = _new(), _new()
        _drive(a, "BTCUSD", SHORT_ROWS)
        _drive(b, "BTCUSD", SHORT_ROWS)
        assert a.account_balance == b.account_balance
        assert repr(a.account_balance) == repr(b.account_balance)   # bit-exact

    def test_two_identical_runs_produce_identical_snapshots(self):
        a, b = _new(), _new()
        _drive(a, "BTCUSD", SHORT_ROWS)
        _drive(b, "BTCUSD", SHORT_ROWS)
        assert (json.dumps(a.capture_state(), sort_keys=True)
                == json.dumps(b.capture_state(), sort_keys=True))

    def test_the_multi_asset_run_is_deterministic_too(self):
        """The interleaved run is where a hash-order dependency would show."""
        first_strategy, first = _run_two_assets()
        second_strategy, second = _run_two_assets()
        assert first == second
        assert (first_strategy.account_balance
                == second_strategy.account_balance)
        assert list(first_strategy.lock.events) == list(
            second_strategy.lock.events)

    def test_asset_registration_order_does_not_change_the_outcome(self):
        _fwd, forward = _run_two_assets(strategy=_new(("BTCUSD", "ETHUSD")))
        _rev, reverse = _run_two_assets(strategy=_new(("ETHUSD", "BTCUSD")))
        assert forward == reverse

# ---------------------------------------------------------------------------
# State restore -> continued evaluation
# ---------------------------------------------------------------------------
class TestStateRestore:
    """
    A resumed strategy must continue, not restart.

    `capture_state` / `from_state` delegate reconstruction to Step 5's
    `state.py`; what is tested here is the strategy-level state the snapshot
    schema does NOT carry — the compounded balance, the sizing captured at
    fill, and the lock — and the refusal to guess any of it.
    """

    def test_resume_between_candles_matches_an_uninterrupted_run(self):
        whole = _drive(_new(), "BTCUSD", SHORT_ROWS)
        first = _new()
        _drive(first, "BTCUSD", SHORT_ROWS[:4])
        resumed = ManualSMCStrategy.from_state(
            first.capture_state(),
            account_balance=first.account_balance,
            expected_config=ManualSpecConfig(),
            tick_specs=_specs("BTCUSD"),
        )
        tail = _drive(resumed, "BTCUSD", SHORT_ROWS[4:])
        assert tail == whole[4:]
        assert resumed.account_balance == 10.406466666666667

    def test_resume_survives_a_json_round_trip(self):
        first = _new()
        _drive(first, "BTCUSD", SHORT_ROWS[:4])
        payload = json.loads(json.dumps(first.capture_state()))
        resumed = ManualSMCStrategy.from_state(
            payload, account_balance=10.0, tick_specs=_specs("BTCUSD"))
        fill = _only_fill(_drive(resumed, "BTCUSD", SHORT_ROWS[4:]))
        assert (fill.ob_id, fill.entry_price) == (SHORT_OB_ID, SHORT_ENTRY)

    def test_the_watermark_survives_and_still_refuses_a_replay(self):
        first = _new()
        _drive(first, "BTCUSD", SHORT_ROWS[:4])
        resumed = ManualSMCStrategy.from_state(
            first.capture_state(), account_balance=10.0)
        assert resumed.watermark.last("BTCUSD").bar_idx == 3
        with pytest.raises(DuplicateCandleError):
            resumed.evaluate_closed_candle(
                "BTCUSD", 3, _ts(3), *SHORT_ROWS[3][1:])

    def test_the_last_global_timestamp_is_rebuilt_from_the_watermark(self):
        """Otherwise a resumed strategy would accept a globally stale candle."""
        first = _new(("BTCUSD", "ETHUSD"))
        _drive(first, "BTCUSD", SHORT_ROWS[:4])
        resumed = ManualSMCStrategy.from_state(
            first.capture_state(), account_balance=10.0,
            tick_specs=_specs("BTCUSD", "ETHUSD"))
        assert resumed._last_global_ts == _ts(3)
        with pytest.raises(GlobalOrderError):
            resumed.evaluate_closed_candle(
                "ETHUSD", 0, _ts(1), *SHORT_ROWS[0][1:])

    def test_an_empty_watermark_leaves_the_global_timestamp_unset(self):
        fresh = ManualSMCStrategy.from_state(
            _new().capture_state(), account_balance=10.0)
        assert fresh._last_global_ts is None

    def test_resume_with_an_open_trade_reproduces_its_sizing_and_settlement(self):
        whole = _drive(_new(), "BTCUSD", SHORT_ROWS)
        first = _new()
        _drive(first, "BTCUSD", SHORT_ROWS[:5])
        resumed = ManualSMCStrategy.from_state(
            first.capture_state(),
            account_balance=first.account_balance,
            tick_specs=_specs("BTCUSD"),
            restored_balance_at_fill=10.0,
        )
        assert resumed.open_sizing == first.open_sizing
        tail = _drive(resumed, "BTCUSD", SHORT_ROWS[5:])
        assert _only_close(tail).settlement == _only_close(whole).settlement
        assert resumed.account_balance == 10.406466666666667

    def test_resume_with_an_open_trade_re_occupies_the_trade_slot(self):
        """Safety rule #13: the slot must be taken from the first candle back."""
        first = _new()
        _drive(first, "BTCUSD", SHORT_ROWS[:5])
        resumed = ManualSMCStrategy.from_state(
            first.capture_state(), account_balance=10.0,
            restored_balance_at_fill=10.0)
        holder = resumed.lock.active_trade
        assert holder is not None
        assert (holder.ob_id, holder.asset) == (SHORT_OB_ID, "BTCUSD")
        assert holder.acquired_at == _ts(4) and holder.acquired_bar_idx == 4
        assert resumed.lock.evaluate(_ts(5)) is not None    # still blocking
        assert resumed.unpersisted_strategy_state()["lock_token"] == holder.token

    def test_resume_with_an_open_trade_refuses_to_guess_the_fill_balance(self):
        """
        Sizing is a pure function of (OB, balance-at-fill, config).

        Without the balance as of the fill the trade's PnL would be wrong, so
        `from_state` refuses rather than defaulting to `starting_capital`.
        """
        first = _new()
        _drive(first, "BTCUSD", SHORT_ROWS[:5])
        with pytest.raises(StrategyStateError) as exc:
            ManualSMCStrategy.from_state(
                first.capture_state(), account_balance=10.0)
        assert "balance AS OF THAT FILL is required" in str(exc.value)
        assert SHORT_OB_ID in str(exc.value)

    def test_a_fill_balance_without_an_open_trade_is_refused(self):
        first = _new()
        _drive(first, "BTCUSD", SHORT_ROWS[:4])
        with pytest.raises(StrategyStateError) as exc:
            ManualSMCStrategy.from_state(
                first.capture_state(), account_balance=10.0,
                restored_balance_at_fill=10.0)
        assert "holds no active trade" in str(exc.value)

    def test_the_compounded_balance_is_never_silently_reset(self):
        """A resumed run must compound from where it stopped, not from $10."""
        first = _new()
        _drive(first, "BTCUSD", SHORT_ROWS)
        grown = first.account_balance
        assert grown > 10.0
        resumed = ManualSMCStrategy.from_state(
            first.capture_state(), account_balance=grown,
            tick_specs=_specs("BTCUSD"))
        assert resumed.account_balance == grown
        assert resumed.cfg.starting_capital == 10.0     # config unchanged
        assert resumed.open_sizing is None

    def test_a_snapshot_carries_the_manual_smc_identity(self):
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:4])
        snapshot = strategy.capture_state()
        assert snapshot["strategy_name"] == "MANUAL_SMC"
        assert snapshot["strategy_version"] == "1.0.0"
        assert "account_balance" not in snapshot

# ---------------------------------------------------------------------------
# No application / exchange imports (requirements 7, 8, 9)
# ---------------------------------------------------------------------------
class TestModuleIndependence:
    """`strategy.py` may import the eight approved modules and stdlib. Nothing else."""

    def test_the_import_list_is_exactly_the_approved_set(self):
        assert _imported_modules() == [
            "__future__",
            "dataclasses",
            "datetime",
            "quantedge.strategy.manual_smc.lifecycle",
            "quantedge.strategy.manual_smc.models",
            "quantedge.strategy.manual_smc.portfolio",
            "quantedge.strategy.manual_smc.quantization",
            "quantedge.strategy.manual_smc.sizing",
            "quantedge.strategy.manual_smc.state",
            "typing",
        ]

    def test_no_application_strategy_types_are_imported(self):
        """
        Requirement 7: translating to `StrategyDecision` is Step 7's adapter.

        `quantedge.strategy.models` (the application's `StrategyDecision` /
        `SetupState`) is deliberately absent from the import list above, and the
        names never appear.
        """
        assert "quantedge.strategy.models" not in _imported_modules()
        names = _identifiers()
        for banned in ("StrategyDecision", "SetupState", "SignalType",
                       "take_profit_target_pct", "roe", "target_roe_pct"):
            assert banned not in names, f"{banned!r} appears in strategy.py"

    def test_no_exchange_transport_or_database_names_appear(self):
        names = _identifiers()
        for banned in ("httpx", "requests", "aiohttp", "urllib", "socket",
                       "asyncio", "sqlalchemy", "psycopg", "sqlite3",
                       "delta_client", "DeltaClient", "execution", "runtime"):
            assert banned not in names, f"{banned!r} appears in strategy.py"
        code = _code_without_strings()
        for banned in ("quantedge . execution", "quantedge . runtime",
                       "quantedge . ai", "quantedge . database"):
            assert banned not in code, f"{banned!r} appears in strategy.py"

    def test_no_order_placement_or_credential_names_appear(self):
        """Requirement 9, and safety rules #1/#2: no execution lives here."""
        names = _identifiers()
        for banned in ("place_order", "cancel_order", "submit_order",
                       "place_bracket", "OrderExecutionService", "kill_switch",
                       "api_key", "api_secret", "secret", "expiresAt",
                       "expires_at", "live_loop", "_allow_direct_execution"):
            assert banned not in names, f"{banned!r} appears in strategy.py"

    def test_no_file_process_or_environment_io_appears(self):
        names = _identifiers()
        for banned in ("open", "Path", "pathlib", "shutil", "tempfile", "os",
                       "sys", "environ", "getenv", "subprocess", "pickle",
                       "logging", "print", "input"):
            assert banned not in names, f"{banned!r} appears in strategy.py"

    def test_the_modules_own_dependency_closure_has_no_exchange_transport(self):
        """
        Measure `strategy.py`'s OWN closure, end to end.

        Run in a subprocess so an httpx already imported by another test module
        cannot mask the result, with STUB parent packages pre-seeded so the
        pre-existing `quantedge/__init__.py` (which does `from . import
        execution`) never runs. A full SHORT sequence is driven inside the
        probe, so late imports inside a code path would be caught too.
        """
        code = (
            "import sys, types, pathlib, datetime as dt;"
            "root=pathlib.Path('src').resolve();"
            "sys.path.insert(0,str(root));"
            "[sys.modules.setdefault(n, _m) for n, _m in "
            "[(n, types.ModuleType(n)) for n in ("
            "'quantedge','quantedge.strategy','quantedge.strategy.manual_smc')]];"
            "sys.modules['quantedge'].__path__=[str(root/'quantedge')];"
            "sys.modules['quantedge.strategy'].__path__="
            "[str(root/'quantedge'/'strategy')];"
            "sys.modules['quantedge.strategy.manual_smc'].__path__="
            "[str(root/'quantedge'/'strategy'/'manual_smc')];"
            "import quantedge.strategy.manual_smc.strategy as m;"
            "s=m.ManualSMCStrategy(assets=['BTCUSD']);"
            "rows=" + repr(SHORT_ROWS) + ";"
            "base=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc);"
            "[s.evaluate_closed_candle('BTCUSD',b,base+dt.timedelta(hours=b),"
            "o,h,l,c) for (b,o,h,l,c) in rows];"
            "m.ManualSMCStrategy.from_state(s.capture_state(),10.0);"
            "third=sorted(n for n in sys.modules "
            "if getattr(sys.modules[n],'__file__',None) "
            "and 'site-packages' in str(sys.modules[n].__file__));"
            "bad=[x for x in ('httpx','cryptography','quantedge.execution',"
            "'quantedge.execution.delta_client',"
            "'quantedge.execution.validation','quantedge.runtime',"
            "'sqlalchemy','psycopg') if x in sys.modules];"
            "print('LOADED:'+','.join(bad+third))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=180)
        assert out.returncode == 0, out.stderr
        assert "LOADED:" in out.stdout
        assert out.stdout.strip().split("LOADED:")[1] == ""

    def test_it_adds_no_transport_beyond_the_pre_existing_package_import(self):
        """
        PRE-EXISTING FINDING, pinned rather than hidden.

        `quantedge/__init__.py` ends with `from . import execution`, so merely
        importing the top-level `quantedge` package already loads httpx, the
        signed Delta REST client and the AESGCM credential crypto. That baseline
        is not this module's doing; what this proves is that `strategy.py` adds
        nothing to it.
        """
        probe = (
            "import sys;"
            "{stmt};"
            "print('SET:'+','.join(sorted(m for m in sys.modules "
            "if m.startswith(('httpx','cryptography','quantedge.execution')))))"
        )

        def _snapshot(stmt: str) -> set:
            out = subprocess.run(
                [sys.executable, "-c", probe.format(stmt=stmt)],
                cwd=str(Path(__file__).parent.parent),
                capture_output=True, text=True, timeout=180)
            assert out.returncode == 0, out.stderr
            return set(filter(None, out.stdout.strip().split("SET:")[1].split(",")))

        baseline = _snapshot("import quantedge")
        with_module = _snapshot("import quantedge.strategy.manual_smc.strategy")
        assert with_module == baseline
        assert "httpx" in baseline

    def test_strategy_py_depends_on_neither_the_adapter_nor_the_backtest(self):
        """
        `adapter.py` (Step 7) and `backtest.py` (Step 8) now exist, so the
        Step 6 emptiness check has become a DEPENDENCY-DIRECTION check, which is
        what it was really protecting: the orchestration layer must sit BELOW
        both of them. `strategy.py` may not import either one, or the
        application types would re-enter through the adapter and the driver
        would become a runtime dependency of production code.

        The package inventory is pinned once, in
        `test_manual_smc_backtest.py::TestStep8ScopeMarker`.
        """
        package = MODULE_PATH.parent
        assert (package / "adapter.py").exists(), "Step 7 deliverable"
        assert (package / "backtest.py").exists(), "Step 8 deliverable"

        imported: set[str] = set()
        for node in ast.walk(ast.parse(MODULE_SRC)):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        for banned in ("quantedge.strategy.manual_smc.adapter",
                       "quantedge.strategy.manual_smc.backtest"):
            assert not [m for m in imported if m == banned
                        or m.startswith(banned + ".")], banned
        assert "adapter" not in {m.rsplit(".", 1)[-1] for m in imported}
        assert "backtest" not in {m.rsplit(".", 1)[-1] for m in imported}

# ---------------------------------------------------------------------------
# Requirement 11: the non-atomic window is stated, not papered over
# ---------------------------------------------------------------------------
class _TornWatermark(CandleWatermark):
    """A watermark that fails to advance on one chosen bar — a crash stand-in."""

    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self.fail_at = fail_at

    def advance(self, asset, bar_idx, ts):
        if bar_idx == self.fail_at:
            raise StateError("simulated crash between the two writes")
        return super().advance(asset, bar_idx, ts)


class TestNonAtomicPersistence:
    """
    The lifecycle mutation and the watermark advance are TWO operations.

    This module provides no transaction, no write-ahead log and no storage, and
    it must not pretend otherwise.
    """

    def test_the_module_declares_that_persistence_is_not_atomic(self):
        assert PERSISTENCE_IS_ATOMIC is False
        assert "two separate operations" in ATOMICITY_NOTE
        assert "no transaction" in ATOMICITY_NOTE

    def test_the_unpersisted_values_are_named_not_hidden(self):
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:5])
        unpersisted = strategy.unpersisted_strategy_state()
        assert set(unpersisted) == {
            "account_balance", "open_sizing_present", "lock_token",
            "last_global_ts", "persistence_is_atomic", "note"}
        assert unpersisted["account_balance"] == 10.0
        assert unpersisted["open_sizing_present"] is True
        assert unpersisted["lock_token"] == strategy.lock.active_trade.token
        assert unpersisted["last_global_ts"] == _ts(4)
        assert unpersisted["persistence_is_atomic"] is False
        assert unpersisted["note"] == ATOMICITY_NOTE

    def test_the_step_five_snapshot_really_does_omit_them(self):
        """The claim above is checked against the snapshot, not just asserted."""
        strategy = _new()
        _drive(strategy, "BTCUSD", SHORT_ROWS[:5])
        text = json.dumps(strategy.capture_state())
        assert "account_balance" not in text
        assert "lock_token" not in text
        assert "position_size" not in text
        assert str(strategy.lock.active_trade.token) not in text

    def test_a_watermark_that_cannot_advance_raises_a_torn_state_error(self):
        strategy = ManualSMCStrategy(
            assets=["BTCUSD"], tick_specs=_specs("BTCUSD"),
            watermark=_TornWatermark(fail_at=1), **ORACLE_KW)
        strategy.evaluate_closed_candle("BTCUSD", 0, _ts(0), *SHORT_ROWS[0][1:])
        with pytest.raises(TornStateError) as exc:
            strategy.evaluate_closed_candle(
                "BTCUSD", 1, _ts(1), *SHORT_ROWS[1][1:])
        message = str(exc.value)
        assert "the lifecycle processed this candle" in message
        assert "state.py will refuse it" in message
        assert ATOMICITY_NOTE in message

    def test_the_tear_it_reports_is_real(self):
        """
        The error is not defensive decoration.

        After it fires the lifecycle HAS advanced (the OB from bar 1 exists)
        while the watermark has not — which is exactly the window the note
        describes, and exactly what `state.py` then refuses.
        """
        strategy = ManualSMCStrategy(
            assets=["BTCUSD"], tick_specs=_specs("BTCUSD"),
            watermark=_TornWatermark(fail_at=1), **ORACLE_KW)
        strategy.evaluate_closed_candle("BTCUSD", 0, _ts(0), *SHORT_ROWS[0][1:])
        with pytest.raises(TornStateError):
            strategy.evaluate_closed_candle(
                "BTCUSD", 1, _ts(1), *SHORT_ROWS[1][1:])
        assert SHORT_OB_ID in strategy.lifecycle.live_obs      # ahead
        assert strategy.watermark.last("BTCUSD").bar_idx == 0  # behind
        with pytest.raises(StateError) as exc:
            ManualSMCStrategy.from_state(strategy.capture_state(), 10.0)
        assert "torn" in str(exc.value)

    def test_the_docstring_states_the_non_atomicity_for_a_human_reader(self):
        head = MODULE_SRC[:MODULE_SRC.index('"""', 3)]
        assert "PERSISTENCE IS NOT ATOMIC" in head
        assert "does not add a transaction" in head
        assert "hiding it would be worse than the gap" in head

# ---------------------------------------------------------------------------
# The module adds no strategy rules of its own
# ---------------------------------------------------------------------------
class TestOrchestrationOnly:
    """
    Every Manual SMC rule stays where it was approved.

    `strategy.py` sequences the modules, refuses bad input, cross-checks the
    lock against the lifecycle and projects results. It re-implements no rule,
    which is what makes the frozen oracle equivalence still meaningful.
    """

    def test_no_strategy_constant_is_re_stated_as_a_literal(self):
        """
        The only numbers in the module are 0.0, 1 and 1e-9.

        Not 0.25 (entry depth), not 0.994/1.006 (the TP), not 72 (the horizon),
        not 35 (the risk budget), not 100 (the leverage cap), not 0.08 (fees).
        Every one of those lives in `ManualSpecConfig` and is read from it.
        """
        numbers = {tok.string for tok in tokenize.generate_tokens(
            io.StringIO(MODULE_SRC).readline) if tok.type == tokenize.NUMBER}
        assert numbers == {"0.0", "1", "1e-9"}

    def test_no_rule_predicate_is_redefined_here(self):
        defined = {node.name for node in ast.walk(ast.parse(MODULE_SRC))
                   if isinstance(node, ast.FunctionDef)}
        for word in ("bos", "displacement", "probe", "touch", "invalidat",
                     "leverage", "size", "settle", "horizon", "dual",
                     "acquire", "release"):
            offenders = [n for n in defined if word in n.lower()]
            assert not offenders, f"{word!r} logic redefined: {offenders}"

    def test_the_lifecycle_is_driven_only_through_its_public_surface(self):
        """No poking at the lifecycle's internals to re-order its sweep."""
        called = {node.func.attr for node in ast.walk(ast.parse(MODULE_SRC))
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        assert "process_candle" in called
        assert called.isdisjoint({"_scan", "_update_obs", "_resolve_active",
                                  "_admit", "_scanners", "_expire"})

    def test_process_candle_is_called_exactly_once_per_candle(self):
        """The load-bearing resolve -> update -> scan order belongs to it."""
        strategy = _new()
        calls = []
        real = strategy.lifecycle.process_candle

        def spy(*args, **kwargs):
            calls.append(args[:2])
            return real(*args, **kwargs)

        strategy.lifecycle.process_candle = spy
        _drive(strategy, "BTCUSD", SHORT_ROWS)
        assert calls == [("BTCUSD", b) for (b, *_rest) in SHORT_ROWS]

    def test_nothing_reads_a_clock_a_random_source_or_the_environment(self):
        """Requirement 1: deterministic. Every timestamp comes from the candle."""
        names = _identifiers()
        for banned in ("random", "uuid", "time", "now", "utcnow", "today",
                       "monotonic", "perf_counter", "sleep", "getenv"):
            assert banned not in names, f"{banned!r} appears in strategy.py"

    def test_it_reports_invalidations_but_cancels_nothing(self):
        """
        Safety rule #9 is REPORTED here, acted on later.

        The module has no cancel path at all, so an invalidated OB can only
        surface as data on the evaluation — which is what a later step consumes.
        """
        names = _identifiers()
        assert names.isdisjoint({"cancel", "cancel_all", "withdraw", "amend"})
        assert "cancel_ob_ids" in {f.name for f in dataclasses.fields(ManualSMCFill)}
        assert "invalidated" in {
            f.name for f in dataclasses.fields(ManualSMCEvaluation)}

    def test_math_is_not_needed_for_the_nan_check(self):
        """`_require_price` uses `x != x`, so no float helper import is needed."""
        assert "math" not in _imported_modules()
        with pytest.raises(InvalidCandleError):
            _new().evaluate_closed_candle(
                "BTCUSD", 0, _ts(0), 100.0, math.nan, 99.0, 100.0)


class TestPackageSurface:
    """The package re-exports Step 6 without shadowing anything."""

    def test_the_step_six_surface_is_re_exported(self):
        import quantedge.strategy.manual_smc as pkg
        from quantedge.strategy.manual_smc import strategy as mod
        for name in mod.__all__:
            assert name in pkg.__all__, f"{name} is not re-exported"
            assert getattr(pkg, name) is getattr(mod, name)

    def test_the_package_exports_are_unique_and_resolvable(self):
        import quantedge.strategy.manual_smc as pkg
        assert len(pkg.__all__) == len(set(pkg.__all__))
        missing = [n for n in pkg.__all__ if not hasattr(pkg, n)]
        assert missing == []

    def test_the_package_still_advertises_the_manual_smc_identity(self):
        """
        The Step 6 form of this test read the docstring's "NOT YET IMPLEMENTED
        (deliberately absent): adapter.py  backtest.py" list. Both modules now
        exist, so the same requirement — the docstring must not misdescribe the
        package's own contents — is checked the other way round: every shipped
        step is claimed, nothing is still advertised as absent, and the
        no-execution-wiring guarantee is still stated.
        """
        import quantedge.strategy.manual_smc as pkg
        assert pkg.MANUAL_SMC_STRATEGY_NAME == "MANUAL_SMC"
        assert pkg.MANUAL_SMC_STRATEGY_VERSION == "1.0.0"
        assert "Phase 1 Step 7 scope: `adapter.py`" in pkg.__doc__
        assert "Phase 1 Step 8 scope: `backtest.py`" in pkg.__doc__
        assert "NOT YET IMPLEMENTED" not in pkg.__doc__
        assert "deliberately absent" not in pkg.__doc__
        assert ("This package has NO production wiring and NO execution "
                "wiring." in pkg.__doc__)
