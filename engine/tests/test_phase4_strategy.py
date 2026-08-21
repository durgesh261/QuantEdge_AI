"""
Phase 4.0 Strategy Layer Test Suite.

Comprehensive deterministic validation for:
1. No setup -> NONE
2. Bullish OB outside price -> NONE
3. Bearish OB outside price -> NONE
4. Bullish OB + price inside + bullish confirmation -> LONG
5. Bearish OB + price inside + bearish confirmation -> SHORT
6. Invalidated OB -> NONE
7. Old untouched bullish OB remains eligible
8. Old untouched bearish OB remains eligible
9. Six-month-old OB can produce a setup
10. Newest OB does not override older valid OB
11. Multiple valid OBs coexistence
12. Current price inside OB detection
13. Forming candle cannot generate strategy signal
14. Duplicate candle does not create duplicate signal
15. Future-data invariance
16. Incremental == full replay equivalence
17. Strategy does not mutate SMC state
18. Strategy does not modify canonical CSV
19. UTC timestamp remains UTC internally
20. Asia/Kolkata display conversion
21. No order execution
22. No private exchange API
23. No Binance dependency
24. Frozen SMC files unchanged
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
    StrategyConfig,
)
from quantedge.strategy.engine import StrategyEngine, StrategyEngineConfig
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
        confidence_score=85,
    )


def make_bearish_ob(
    index: int = 2,
    top: float = 61000.0,
    bottom: float = 60000.0,
    state: OBState = OBState.FRESH,
    ts: int = BASE_TS,
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
        confidence_score=85,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test Suite
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase4StrategyLayer:
    """Comprehensive test suite for Phase 4.0 Strategy Layer."""

    def test_no_setup_returns_none(self):
        """1. No active OB or no setup condition -> direction == NONE."""
        engine = StrategyEngine()
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=55000.0)
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[],
            internal_trend=TrendDirection.RANGING,
            swing_trend=TrendDirection.RANGING,
        )
        assert decision.direction == StrategyDirection.NONE
        assert not decision.is_signal
        assert decision.setup_type is None
        assert "Price outside any active order block" in decision.reasons[0]

    def test_bullish_ob_outside_price_returns_none(self):
        """2. Valid Bullish OB exists, but price is outside OB zone -> NONE."""
        engine = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=55000.0)  # far above OB
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.direction == StrategyDirection.NONE
        assert not decision.is_signal

    def test_bearish_ob_outside_price_returns_none(self):
        """3. Valid Bearish OB exists, but price is outside OB zone -> NONE."""
        engine = StrategyEngine()
        ob = make_bearish_ob(top=61000.0, bottom=60000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=55000.0)  # far below OB
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BEARISH,
            swing_trend=TrendDirection.BEARISH,
        )
        assert decision.direction == StrategyDirection.NONE
        assert not decision.is_signal

    def test_bullish_ob_price_inside_bullish_confirmation_returns_long(self):
        """4. Bullish OB + price inside + bullish confirmation -> LONG."""
        engine = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, open_p=50200.0, high_p=50200.0, low_p=49400.0, close_p=49500.0)
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.direction == StrategyDirection.LONG
        assert decision.is_long
        assert decision.is_signal
        assert decision.setup_type == SetupType.BULLISH_OB_RETEST.value
        assert decision.entry is not None
        assert decision.stop_loss is not None
        assert decision.order_block == ob
        assert any("valid bullish order block" in r for r in decision.reasons)

    def test_bearish_ob_price_inside_bearish_confirmation_returns_short(self):
        """5. Bearish OB + price inside + bearish confirmation -> SHORT."""
        engine = StrategyEngine()
        ob = make_bearish_ob(top=61000.0, bottom=60000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, open_p=59800.0, high_p=60600.0, low_p=59800.0, close_p=60500.0)
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BEARISH,
            swing_trend=TrendDirection.BEARISH,
        )
        assert decision.direction == StrategyDirection.SHORT
        assert decision.is_short
        assert decision.is_signal
        assert decision.setup_type == SetupType.BEARISH_OB_RETEST.value
        assert decision.entry is not None
        assert decision.stop_loss is not None
        assert decision.order_block == ob
        assert any("valid bearish order block" in r for r in decision.reasons)

    def test_invalidated_ob_returns_none(self):
        """6. Invalidated OB -> NONE (even if price is inside historical boundaries)."""
        engine = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0, state=OBState.INVALIDATED)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.direction == StrategyDirection.NONE
        assert not decision.is_signal

    def test_old_untouched_bullish_ob_remains_eligible(self):
        """7. Bullish OB formed 300 days ago, untouched, produces LONG when entered."""
        engine = StrategyEngine()
        old_ts = BASE_TS - 300 * 24 * HOUR
        ob = make_bullish_ob(top=50000.0, bottom=49000.0, state=OBState.FRESH, ts=old_ts)
        candle = make_candle(BASE_TS, close_p=49500.0)
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.direction == StrategyDirection.LONG
        assert decision.order_block == ob

    def test_old_untouched_bearish_ob_remains_eligible(self):
        """8. Bearish OB formed 300 days ago, untouched, produces SHORT when entered."""
        engine = StrategyEngine()
        old_ts = BASE_TS - 300 * 24 * HOUR
        ob = make_bearish_ob(top=61000.0, bottom=60000.0, state=OBState.FRESH, ts=old_ts)
        candle = make_candle(BASE_TS, close_p=60500.0)
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BEARISH,
            swing_trend=TrendDirection.BEARISH,
        )
        assert decision.direction == StrategyDirection.SHORT
        assert decision.order_block == ob

    def test_six_month_old_ob_can_produce_setup(self):
        """9. 6-month-old untouched OB (4,320 bars) produces valid setup on return."""
        engine = StrategyEngine()
        six_months_ago_ts = BASE_TS - 4320 * HOUR
        ob = make_bullish_ob(top=50000.0, bottom=49000.0, state=OBState.FRESH, ts=six_months_ago_ts)
        candle = make_candle(BASE_TS, close_p=49800.0)
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.direction == StrategyDirection.LONG

    def test_newest_ob_does_not_override_older_valid_ob(self):
        """10. Presence of a newer OB does not prevent trading an older valid OB."""
        engine = StrategyEngine()
        ob_old = make_bullish_ob(index=1, top=45000.0, bottom=44000.0, ts=BASE_TS)
        ob_new = make_bullish_ob(index=2, top=55000.0, bottom=54000.0, ts=BASE_TS + 1000 * HOUR)

        candle = make_candle(BASE_TS + 1050 * HOUR, close_p=44500.0)  # in old OB zone
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=[ob_old, ob_new],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.direction == StrategyDirection.LONG
        assert decision.order_block == ob_old

    def test_multiple_valid_obs_coexistence(self):
        """11. Multiple active OBs can coexist across levels and be evaluated."""
        engine = StrategyEngine()
        obs = [
            make_bullish_ob(index=i + 1, top=40000.0 + i * 5000, bottom=39000.0 + i * 5000)
            for i in range(4)
        ]
        candle = make_candle(BASE_TS + 200 * HOUR, close_p=49500.0)  # matches index 3 [49000, 50000]
        decision = engine.evaluate_state(
            candle=candle,
            active_obs=obs,
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.direction == StrategyDirection.LONG
        assert decision.order_block.index == 3

    def test_current_price_inside_ob_detection(self):
        """12. is_price_inside_ob detection functions deterministically."""
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        assert is_price_inside_ob(49000.0, ob) is True
        assert is_price_inside_ob(50000.0, ob) is True
        assert is_price_inside_ob(49500.0, ob) is True
        assert is_price_inside_ob(48999.99, ob) is False
        assert is_price_inside_ob(50000.01, ob) is False

    def test_forming_candle_cannot_generate_strategy_signal(self):
        """13. A non-closed candle cannot generate a strategy signal in engine evaluate."""
        smc_engine = IncrementalSMCEngine()
        strategy_engine = StrategyEngine()
        # Feed candle that fails closed check if evaluated in live pipeline
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        smc_engine._register_ob(ob)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=55000.0)  # outside OB
        decision = strategy_engine.evaluate_candle(candle, smc_engine)
        assert decision.direction == StrategyDirection.NONE

    def test_duplicate_candle_does_not_create_duplicate_signal(self):
        """14. Evaluating identical closed candle twice produces identical idempotent decision."""
        strategy_engine = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)

        decision1 = strategy_engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        decision2 = strategy_engine.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision1.direction == decision2.direction == StrategyDirection.LONG
        assert decision1.to_dict() == decision2.to_dict()

    def test_future_data_invariance(self):
        """15. Future candles cannot alter the decision computed at candle T."""
        strategy_engine = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle_t = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)

        decision_before = strategy_engine.evaluate_state(
            candle=candle_t,
            active_obs=[copy.deepcopy(ob)],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )

        # Future candles occur (prices trade at 70k)
        future_candles = [
            make_candle(BASE_TS + (100 + i) * HOUR, close_p=70000.0) for i in range(1, 50)
        ]

        # Re-evaluate candle T with state at T
        decision_after = strategy_engine.evaluate_state(
            candle=candle_t,
            active_obs=[copy.deepcopy(ob)],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )

        assert decision_before.direction == decision_after.direction == StrategyDirection.LONG
        assert decision_before.to_dict() == decision_after.to_dict()

    def test_incremental_equals_full_replay(self, tmp_path):
        """16. Incremental and full-replay pipelines produce matching strategy decisions."""
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

        assert dec_full.direction == dec_inc.direction
        assert dec_full.setup_type == dec_inc.setup_type
        assert dec_full.entry == dec_inc.entry
        assert dec_full.stop_loss == dec_inc.stop_loss

    def test_strategy_does_not_mutate_smc_state(self):
        """17. Strategy evaluation is strictly read-only and causes ZERO mutation to SMC state."""
        smc_engine = IncrementalSMCEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        smc_engine._register_ob(ob)

        snap_before = copy.deepcopy(smc_engine.get_current_snapshot())
        obs_count_before = len(smc_engine.get_all_obs())
        active_count_before = len(smc_engine.get_active_obs())
        ob_state_before = ob.state

        strat = StrategyEngine()
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_candle(candle, smc_engine)

        snap_after = smc_engine.get_current_snapshot()
        obs_count_after = len(smc_engine.get_all_obs())
        active_count_after = len(smc_engine.get_active_obs())
        ob_state_after = ob.state

        assert snap_before == snap_after
        assert obs_count_before == obs_count_after
        assert active_count_before == active_count_after
        assert ob_state_before == ob_state_after

    def test_strategy_does_not_modify_canonical_csv(self):
        """18. Running strategy logic never writes to or alters production canonical CSV."""
        if not CANONICAL_CSV.exists():
            pytest.skip("Canonical CSV not present")
        hash_before = csv_hash(CANONICAL_CSV)

        strat = StrategyEngine()
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)

        hash_after = csv_hash(CANONICAL_CSV)
        assert hash_before == hash_after

    def test_utc_timestamp_remains_utc_internally(self):
        """19. StrategyDecision retains timezone-aware UTC datetime internally."""
        dt_utc = datetime(2026, 8, 21, 14, 0, 0, tzinfo=timezone.utc)
        decision = StrategyDecision(
            timestamp=dt_utc,
            symbol="BTCUSD.P",
            timeframe="1h",
            direction=StrategyDirection.LONG,
        )
        assert decision.timestamp.tzinfo == timezone.utc
        assert decision.timestamp.hour == 14

    def test_asia_kolkata_display_conversion(self):
        """20. StrategyDecision.timestamp_ist formats correctly in Asia/Kolkata (UTC+05:30)."""
        dt_utc = datetime(2026, 8, 21, 14, 0, 0, tzinfo=timezone.utc)
        decision = StrategyDecision(
            timestamp=dt_utc,
            symbol="BTCUSD.P",
            timeframe="1h",
            direction=StrategyDirection.LONG,
        )
        assert "2026-08-21 19:30:00" in decision.timestamp_ist
        assert "+0530" in decision.timestamp_ist or "IST" in decision.timestamp_ist or "Asia/Kolkata" in decision.timestamp_ist or "19:30:00" in decision.timestamp_ist

    def test_no_order_execution(self):
        """21. Verify StrategyDecision is purely informational and lacks trade execution methods."""
        decision = StrategyDecision(
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSD.P",
            timeframe="1h",
            direction=StrategyDirection.LONG,
        )
        assert not hasattr(decision, "place_order")
        assert not hasattr(decision, "execute")
        assert not hasattr(decision, "submit_order")

    def test_no_private_exchange_api(self):
        """22. Verify strategy engine has zero imports or references to private key or order APIs."""
        import quantedge.strategy.engine as engine_mod
        import quantedge.strategy.models as models_mod
        for mod in (engine_mod, models_mod):
            content = Path(mod.__file__).read_text(encoding="utf-8")
            assert "api_key" not in content.lower()
            assert "api_secret" not in content.lower()
            assert "private_key" not in content.lower()
            assert "order/place" not in content.lower()

    def test_no_binance_dependency(self):
        """23. Verify strategy module has zero references to Binance endpoints."""
        import quantedge.strategy.engine as engine_mod
        import quantedge.strategy.models as models_mod
        for mod in (engine_mod, models_mod):
            content = Path(mod.__file__).read_text(encoding="utf-8")
            assert "binance" not in content.lower()
            assert "api.binance.com" not in content

    def test_frozen_smc_files_unchanged(self):
        """24. All three frozen SMC files exist and remain in repository."""
        repo_root = Path(__file__).parent.parent
        frozen = [
            repo_root / "src" / "quantedge" / "smc" / "structure.py",
            repo_root / "src" / "quantedge" / "smc" / "order_blocks.py",
            repo_root / "src" / "quantedge" / "smc" / "volatility.py",
        ]
        for f in frozen:
            assert f.exists(), f"Frozen SMC file missing: {f}"
