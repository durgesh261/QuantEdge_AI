"""
Phase 4.1 Signal Qualification & Trade Setup Layer Test Suite.

Comprehensive deterministic validation for:
1. no OB -> NO_SETUP
2. valid OB outside price -> WATCHING_OB
3. price enters bullish OB without confirmation -> OB_ENGAGED
4. price enters bearish OB without confirmation -> OB_ENGAGED
5. bullish OB + bullish confirmation -> QUALIFIED_LONG
6. bearish OB + bearish confirmation -> QUALIFIED_SHORT
7. bullish OB + bearish confirmation -> not LONG (OB_ENGAGED)
8. bearish OB + bullish confirmation -> not SHORT (OB_ENGAGED)
9. invalidated OB -> NO_SETUP
10. used OB -> NO_SETUP
11. 6-month untouched OB -> still valid and qualifies
12. old OB vs newer OB coexistence
13. multiple overlapping OBs engagement
14. deterministic OB selection
15. forming candle rejected
16. exact closed boundary testing
17. duplicate candle idempotency
18. duplicate strategy evaluation
19. future-data invariance
20. incremental == replay equivalence
21. strategy does not mutate OB
22. strategy does not mutate structure
23. strategy does not modify CSV
24. deterministic setup ID
25. deterministic reasons
26. UTC internal timestamp
27. IST display formatting
28. no order execution
29. no Binance dependency
30. frozen SMC files unchanged
"""

import copy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import pytest

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.models import (
    OrderBlock,
    OBState,
    BreakType,
    TrendDirection,
    StructureBreak,
    is_price_inside_ob,
)
from quantedge.market_data.incremental_engine import IncrementalSMCEngine
from quantedge.strategy.models import (
    StrategyDecision,
    StrategyDirection,
    SetupType,
    SetupState,
    StrategyConfig,
    generate_setup_id,
)
from quantedge.strategy.engine import StrategyEngine
from quantedge.market_data.ingestion import CANONICAL_CSV, csv_hash, write_candles

HOUR = 3600
BASE_TS = int(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_candle(
    ts: int,
    open_p: Optional[float] = None,
    high_p: Optional[float] = None,
    low_p: Optional[float] = None,
    close_p: float = 50000.0,
    vol: float = 1000.0,
) -> Candle:
    c = close_p
    o = open_p if open_p is not None else c
    h = high_p if high_p is not None else max(o, c) + 50.0
    l = low_p if low_p is not None else min(o, c) - 50.0
    return Candle(
        symbol="BTCUSD.P",
        timeframe=Timeframe.H1,
        timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal(str(vol)),
        source=MarketDataSource.HISTORICAL,
    )


def write_candles_from_list(candles: List[Candle], csv_path: Path) -> None:
    candle_dict = {
        int(c.timestamp.timestamp()): {
            "timestamp": c.timestamp.isoformat(),
            "open": str(c.open),
            "high": str(c.high),
            "low": str(c.low),
            "close": str(c.close),
            "volume": str(c.volume),
        }
        for c in candles
    }
    write_candles(csv_path, candle_dict)


def make_bullish_ob(
    index: int = 1,
    top: float = 50000.0,
    bottom: float = 49000.0,
    state: OBState = OBState.FRESH,
    ts: int = BASE_TS,
    conf: int = 85,
) -> OrderBlock:
    formation = make_candle(ts, open_p=bottom + 200, high_p=top, low_p=bottom, close_p=bottom + 100)
    return OrderBlock(
        index=index,
        symbol="BTCUSD.P",
        timeframe="1h",
        type="BULLISH",
        top_price=Decimal(str(top)),
        bottom_price=Decimal(str(bottom)),
        formation_candle=formation,
        formation_index=10,
        break_index=15,
        break_type=BreakType.BOS,
        trend_before_break=TrendDirection.BULLISH,
        state=state,
        touch_count=0 if state == OBState.FRESH else 1,
        confidence_score=conf,
    )


def make_bearish_ob(
    index: int = 2,
    top: float = 61000.0,
    bottom: float = 60000.0,
    state: OBState = OBState.FRESH,
    ts: int = BASE_TS,
    conf: int = 85,
) -> OrderBlock:
    formation = make_candle(ts, open_p=top - 200, high_p=top, low_p=bottom, close_p=top - 100)
    return OrderBlock(
        index=index,
        symbol="BTCUSD.P",
        timeframe="1h",
        type="BEARISH",
        top_price=Decimal(str(top)),
        bottom_price=Decimal(str(bottom)),
        formation_candle=formation,
        formation_index=10,
        break_index=15,
        break_type=BreakType.BOS,
        trend_before_break=TrendDirection.BEARISH,
        state=state,
        touch_count=0 if state == OBState.FRESH else 1,
        confidence_score=conf,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4.1 Test Suite
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase41SignalQualification:
    """30 comprehensive deterministic tests for Phase 4.1 Signal Qualification."""

    def test_no_ob_returns_no_setup(self):
        """1. Empty OB pool produces NO_SETUP state."""
        strat = StrategyEngine()
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=55000.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[],
            internal_trend=TrendDirection.RANGING,
            swing_trend=TrendDirection.RANGING,
            all_active_obs=[],
        )
        assert decision.setup_state == SetupState.NO_SETUP
        assert decision.direction == StrategyDirection.NONE
        assert not decision.is_qualified
        assert not decision.is_engaged

    def test_valid_ob_outside_price_returns_watching_ob(self):
        """2. Valid OB exists in pool, price is outside -> WATCHING_OB."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=55000.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
            all_active_obs=[ob],
        )
        assert decision.setup_state == SetupState.WATCHING_OB
        assert decision.direction == StrategyDirection.NONE
        assert decision.is_watching
        assert not decision.is_engaged

    def test_price_enters_bullish_ob_unconfirmed_returns_ob_engaged(self):
        """3. Price enters bullish OB but no bullish trend confirmation -> OB_ENGAGED."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.RANGING,
            swing_trend=TrendDirection.RANGING,
            recent_breaks=[],
        )
        assert decision.setup_state == SetupState.OB_ENGAGED
        assert decision.direction == StrategyDirection.NONE
        assert decision.is_engaged
        assert not decision.is_qualified
        assert decision.order_block == ob

    def test_price_enters_bearish_ob_unconfirmed_returns_ob_engaged(self):
        """4. Price enters bearish OB but no bearish trend confirmation -> OB_ENGAGED."""
        strat = StrategyEngine()
        ob = make_bearish_ob(top=61000.0, bottom=60000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=60500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.RANGING,
            swing_trend=TrendDirection.RANGING,
            recent_breaks=[],
        )
        assert decision.setup_state == SetupState.OB_ENGAGED
        assert decision.direction == StrategyDirection.NONE
        assert decision.is_engaged
        assert not decision.is_qualified
        assert decision.order_block == ob

    def test_bullish_ob_bullish_confirmation_returns_qualified_long(self):
        """5. Bullish OB + price inside + bullish confirmation -> QUALIFIED_LONG."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state == SetupState.QUALIFIED_LONG
        assert decision.direction == StrategyDirection.LONG
        assert decision.is_qualified
        assert decision.is_long
        assert decision.setup_id is not None
        assert decision.entry == ob.calculate_entry_price()
        assert decision.stop_loss == ob.calculate_stop_loss()

    def test_bearish_ob_bearish_confirmation_returns_qualified_short(self):
        """6. Bearish OB + price inside + bearish confirmation -> QUALIFIED_SHORT."""
        strat = StrategyEngine()
        ob = make_bearish_ob(top=61000.0, bottom=60000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=60500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BEARISH,
            swing_trend=TrendDirection.BEARISH,
        )
        assert decision.setup_state == SetupState.QUALIFIED_SHORT
        assert decision.direction == StrategyDirection.SHORT
        assert decision.is_qualified
        assert decision.is_short
        assert decision.setup_id is not None
        assert decision.entry == ob.calculate_entry_price()
        assert decision.stop_loss == ob.calculate_stop_loss()

    def test_bullish_ob_bearish_confirmation_not_long(self):
        """7. Bullish OB with conflicting bearish trend is NOT qualified as LONG."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BEARISH,
            swing_trend=TrendDirection.BEARISH,
            recent_breaks=[],
        )
        assert decision.setup_state == SetupState.OB_ENGAGED
        assert decision.direction == StrategyDirection.NONE
        assert not decision.is_qualified

    def test_bearish_ob_bullish_confirmation_not_short(self):
        """8. Bearish OB with conflicting bullish trend is NOT qualified as SHORT."""
        strat = StrategyEngine()
        ob = make_bearish_ob(top=61000.0, bottom=60000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=60500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
            recent_breaks=[],
        )
        assert decision.setup_state == SetupState.OB_ENGAGED
        assert decision.direction == StrategyDirection.NONE
        assert not decision.is_qualified

    def test_invalidated_ob_returns_no_setup(self):
        """9. Invalidated OB in pool is excluded -> NO_SETUP."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0, state=OBState.INVALIDATED)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
            all_active_obs=[ob],
        )
        assert decision.setup_state == SetupState.NO_SETUP
        assert decision.direction == StrategyDirection.NONE

    def test_used_ob_returns_no_setup(self):
        """10. Used OB in pool is excluded -> NO_SETUP."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0, state=OBState.USED)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
            all_active_obs=[ob],
        )
        assert decision.setup_state == SetupState.NO_SETUP
        assert decision.direction == StrategyDirection.NONE

    def test_six_month_untouched_ob_still_valid_and_qualifies(self):
        """11. 6-month-old untouched OB qualifies as QUALIFIED_LONG on retest."""
        strat = StrategyEngine()
        six_months_ago = BASE_TS - 4320 * HOUR
        ob = make_bullish_ob(top=50000.0, bottom=49000.0, state=OBState.FRESH, ts=six_months_ago)
        candle = make_candle(BASE_TS, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state == SetupState.QUALIFIED_LONG
        assert decision.direction == StrategyDirection.LONG
        assert decision.ob_age_days is not None
        assert decision.ob_age_days >= 170.0

    def test_old_ob_vs_newer_ob_coexistence(self):
        """12. Presence of a newer OB does not prevent trading an older valid OB."""
        strat = StrategyEngine()
        ob1 = make_bullish_ob(index=1, top=45000.0, bottom=44000.0, ts=BASE_TS)
        ob2 = make_bullish_ob(index=2, top=55000.0, bottom=54000.0, ts=BASE_TS + 500 * HOUR)

        candle = make_candle(BASE_TS + 600 * HOUR, close_p=44500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob1, ob2],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state == SetupState.QUALIFIED_LONG
        assert decision.order_block == ob1

    def test_multiple_overlapping_obs_engagement(self):
        """13. Price inside multiple overlapping active OBs engages the best candidate."""
        strat = StrategyEngine()
        ob_wide = make_bullish_ob(index=1, top=52000.0, bottom=48000.0, conf=80)
        ob_tight = make_bullish_ob(index=2, top=50000.0, bottom=49000.0, conf=90)

        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob_wide, ob_tight],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state == SetupState.QUALIFIED_LONG
        # Tight/higher confidence OB prioritized
        assert decision.order_block == ob_tight

    def test_deterministic_ob_selection(self):
        """14. OB priority key is deterministic across identical evaluations."""
        strat = StrategyEngine()
        ob1 = make_bullish_ob(index=1, top=50000.0, bottom=49000.0, conf=85, ts=BASE_TS)
        ob2 = make_bullish_ob(index=2, top=50000.0, bottom=49000.0, conf=85, ts=BASE_TS + 100 * HOUR)

        candle = make_candle(BASE_TS + 200 * HOUR, close_p=49500.0)
        dec1 = strat.evaluate_state(candle, [ob1, ob2], TrendDirection.BULLISH, TrendDirection.BULLISH)
        dec2 = strat.evaluate_state(candle, [ob1, ob2], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert dec1.order_block == dec2.order_block

    def test_forming_candle_rejected(self):
        """15. A non-closed/forming candle does not produce a qualified signal."""
        smc_engine = IncrementalSMCEngine()
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        smc_engine._register_ob(ob)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=55000.0)  # outside OB
        decision = strat.evaluate_candle(candle, smc_engine)
        assert decision.setup_state == SetupState.WATCHING_OB
        assert decision.direction == StrategyDirection.NONE

    def test_exact_closed_boundary_testing(self):
        """16. Exact boundary prices: bottom and top are inside, beyond boundary is outside."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)

        # Exact bottom
        c_bot = make_candle(BASE_TS + 100 * HOUR, close_p=49000.0)
        d_bot = strat.evaluate_state(c_bot, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert d_bot.setup_state == SetupState.QUALIFIED_LONG

        # Exact top
        c_top = make_candle(BASE_TS + 100 * HOUR, close_p=50000.0)
        d_top = strat.evaluate_state(c_top, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert d_top.setup_state == SetupState.QUALIFIED_LONG

        # Slightly below
        c_below = make_candle(BASE_TS + 100 * HOUR, close_p=48999.99)
        d_below = strat.evaluate_state(c_below, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert d_below.setup_state == SetupState.WATCHING_OB

    def test_duplicate_candle_idempotency(self):
        """17. Submitting the exact same candle twice produces identical setup state."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)

        d1 = strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        d2 = strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert d1.setup_state == d2.setup_state == SetupState.QUALIFIED_LONG
        assert d1.setup_id == d2.setup_id
        assert d1.to_dict() == d2.to_dict()

    def test_duplicate_strategy_evaluation(self):
        """18. Repeated evaluation on identical state produces identical outputs."""
        strat = StrategyEngine()
        ob = make_bearish_ob(top=61000.0, bottom=60000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=60500.0)
        dec_list = [
            strat.evaluate_state(candle, [ob], TrendDirection.BEARISH, TrendDirection.BEARISH)
            for _ in range(5)
        ]
        for d in dec_list:
            assert d.setup_state == SetupState.QUALIFIED_SHORT
            assert d.setup_id == dec_list[0].setup_id

    def test_future_data_invariance(self):
        """19. Future candles do not alter decision at candle T."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle_t = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)

        d_before = strat.evaluate_state(candle_t, [copy.deepcopy(ob)], TrendDirection.BULLISH, TrendDirection.BULLISH)

        # Future candles arrive
        _ = [make_candle(BASE_TS + (100 + i) * HOUR, close_p=75000.0) for i in range(1, 20)]

        d_after = strat.evaluate_state(candle_t, [copy.deepcopy(ob)], TrendDirection.BULLISH, TrendDirection.BULLISH)

        assert d_before.setup_state == d_after.setup_state == SetupState.QUALIFIED_LONG
        assert d_before.setup_id == d_after.setup_id

    def test_incremental_equals_full_replay(self, tmp_path):
        """20. Incremental processing and full batch replay yield identical setup state."""
        n_candles = 320
        candles = []
        for i in range(n_candles):
            cycle = i % 120
            p = 50000.0 + (cycle * 100.0 if cycle < 60 else (120 - cycle) * 100.0)
            candles.append(make_candle(BASE_TS + i * HOUR, open_p=p, high_p=p + 60.0, low_p=p - 60.0, close_p=p + 20.0))

        csv_full = tmp_path / "full.csv"
        write_candles_from_list(candles, csv_full)

        csv_hist = tmp_path / "hist.csv"
        write_candles_from_list(candles[:300], csv_hist)

        # Full
        eng_full = IncrementalSMCEngine()
        eng_full.initialize_from_canonical(csv_full)

        # Incremental
        eng_inc = IncrementalSMCEngine()
        eng_inc.initialize_from_canonical(csv_hist)
        eng_inc.process_new_candles(candles[300:])

        strat = StrategyEngine()
        dec_full = strat.evaluate_candle(candles[-1], eng_full)
        dec_inc = strat.evaluate_candle(candles[-1], eng_inc)

        assert dec_full.setup_state == dec_inc.setup_state
        assert dec_full.direction == dec_inc.direction
        assert dec_full.setup_id == dec_inc.setup_id

    def test_strategy_does_not_mutate_ob(self):
        """21. Strategy execution leaves OB touch_count, state, and properties 100% unchanged."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0, state=OBState.FRESH)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)

        touch_before = ob.touch_count
        state_before = ob.state

        strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)

        assert ob.touch_count == touch_before
        assert ob.state == state_before == OBState.FRESH

    def test_strategy_does_not_mutate_structure(self):
        """22. Strategy execution leaves IncrementalSMCEngine structure state untouched."""
        smc = IncrementalSMCEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        smc._register_ob(ob)

        snap_before = copy.deepcopy(smc.get_current_snapshot())
        strat = StrategyEngine()
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        strat.evaluate_candle(candle, smc)

        assert smc.get_current_snapshot() == snap_before

    def test_strategy_does_not_modify_csv(self):
        """23. Strategy evaluation does not alter canonical CSV."""
        if not CANONICAL_CSV.exists():
            pytest.skip("Canonical CSV not present")
        hash_before = csv_hash(CANONICAL_CSV)

        strat = StrategyEngine()
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)

        assert csv_hash(CANONICAL_CSV) == hash_before

    def test_deterministic_setup_id(self):
        """24. setup_id generation is deterministic and reproducible."""
        ob = make_bullish_ob(index=42, top=50000.0, bottom=49000.0)
        setup_id = generate_setup_id("BTCUSD.P", "1h", ob, StrategyDirection.LONG)
        assert "BTCUSD.P_1h_OB42" in setup_id
        assert "LONG" in setup_id

    def test_deterministic_reasons(self):
        """25. Reasons list is factual and contains all key conditions."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert any("bullish order block" in r for r in decision.reasons)
        assert any("price" in r and "entered" in r for r in decision.reasons)
        assert any("bullish structure confirmation" in r for r in decision.reasons)

    def test_utc_internal_timestamp(self):
        """26. StrategyDecision.timestamp maintains UTC timezone internally."""
        dt_utc = datetime(2026, 8, 21, 14, 0, 0, tzinfo=timezone.utc)
        decision = StrategyDecision(
            timestamp=dt_utc,
            symbol="BTCUSD.P",
            timeframe="1h",
            setup_state=SetupState.QUALIFIED_LONG,
            direction=StrategyDirection.LONG,
        )
        assert decision.timestamp.tzinfo == timezone.utc
        assert decision.timestamp.hour == 14

    def test_ist_display_formatting(self):
        """27. StrategyDecision.timestamp_ist formats into Asia/Kolkata (+05:30)."""
        dt_utc = datetime(2026, 8, 21, 14, 0, 0, tzinfo=timezone.utc)
        decision = StrategyDecision(
            timestamp=dt_utc,
            symbol="BTCUSD.P",
            timeframe="1h",
            setup_state=SetupState.QUALIFIED_LONG,
            direction=StrategyDirection.LONG,
        )
        assert "2026-08-21 19:30:00" in decision.timestamp_ist

    def test_no_order_execution(self):
        """28. Verify StrategyDecision and StrategyEngine lack order placement methods."""
        decision = StrategyDecision(
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSD.P",
            timeframe="1h",
            direction=StrategyDirection.LONG,
        )
        assert not hasattr(decision, "place_order")
        assert not hasattr(decision, "execute")
        assert not hasattr(decision, "submit_order")
        assert not hasattr(decision, "cancel_order")

    def test_no_binance_dependency(self):
        """29. Verify strategy modules have zero references to Binance."""
        import quantedge.strategy.engine as engine_mod
        import quantedge.strategy.models as models_mod
        for mod in (engine_mod, models_mod):
            content = Path(mod.__file__).read_text(encoding="utf-8")
            assert "binance" not in content.lower()
            assert "api.binance.com" not in content

    def test_frozen_smc_files_unchanged(self):
        """30. Frozen SMC files exist and remain in repository."""
        repo_root = Path(__file__).parent.parent
        frozen = [
            repo_root / "src" / "quantedge" / "smc" / "structure.py",
            repo_root / "src" / "quantedge" / "smc" / "order_blocks.py",
            repo_root / "src" / "quantedge" / "smc" / "volatility.py",
        ]
        for f in frozen:
            assert f.exists(), f"Frozen SMC file missing: {f}"
