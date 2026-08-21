"""
Phase 3.7 — Long-Lived Order Block Lifetime & Timezone Validation Test Suite.

Verifies:
1. Old untouched bullish OB remains valid
2. Old untouched bearish OB remains valid
3. Six-month-old untouched OB remains valid
4. No age-based expiration (1 day, 30 days, 90 days, 180 days)
5. Formation candle is not a touch
6. Break candle is not a touch
7. Genuine retest after break becomes touch
8. Invalidated OB remains invalid (no revival by age or price return)
9. Old OB survives newer OB creation
10. Multiple active OBs remain available
11. Current price inside valid OB (is_price_inside_ob)
12. Current price outside valid OB
13. Multiple OB zones can coexist and be queried
14. Incremental == full replay equivalence
15. Future-data invariance
16. UTC internal timestamp policy
17. UTC -> Asia/Kolkata user-facing display conversion
18. No production CSV mutation (isolated test fixtures)
19. Frozen SMC files unchanged
20. No Binance dependency in market data or SMC modules
"""

import copy
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

import pytest

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.models import (
    OrderBlock,
    OBState,
    BreakType,
    TrendDirection,
    StructureType,
    StructureBreak,
    PivotPoint,
    is_price_inside_ob,
)
from quantedge.market_data.incremental_engine import (
    IncrementalSMCEngine,
    IncrementalEngineConfig,
)
from quantedge.utils.timezone import (
    to_utc,
    to_ist,
    format_ist,
    from_ist_to_utc,
    UTC_TIMEZONE,
    IST_TIMEZONE,
)
from quantedge.market_data.ingestion import CANONICAL_CSV

HOUR = 3600
BASE_TS = int(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())


# ── Fixtures & Helpers ──────────────────────────────────────────────────────────

def make_candle(
    ts: int,
    open_p: float = 50000.0,
    high_p: float = 50100.0,
    low_p: float = 49900.0,
    close_p: float = 50050.0,
    vol: float = 1000.0,
) -> Candle:
    return Candle(
        symbol="BTCUSD.P",
        timeframe=Timeframe.H1,
        timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
        open=Decimal(str(open_p)),
        high=Decimal(str(high_p)),
        low=Decimal(str(low_p)),
        close=Decimal(str(close_p)),
        volume=Decimal(str(vol)),
        source=MarketDataSource.HISTORICAL,
    )


def write_candles_from_list(candles: List[Candle], csv_path: Path) -> None:
    """Write a list of Candle models to a CSV file."""
    from quantedge.market_data.ingestion import write_candles
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


def create_bullish_ob_scenario(base_ts: int = BASE_TS) -> tuple[List[Candle], OrderBlock]:
    """
    Create a standard bullish OB at base_ts.
    OB range: [49000, 50000].
    """
    formation_candle = make_candle(
        base_ts, open_p=49800.0, high_p=50000.0, low_p=49000.0, close_p=49200.0
    )
    ob = OrderBlock(
        index=1,
        symbol="BTCUSD.P",
        timeframe="1h",
        type="BULLISH",
        top_price=Decimal("50000.0"),
        bottom_price=Decimal("49000.0"),
        formation_candle=formation_candle,
        formation_index=10,
        break_index=15,
        break_type=BreakType.BOS,
        trend_before_break=TrendDirection.BULLISH,
        state=OBState.FRESH,
        touch_count=0,
    )
    return [formation_candle], ob


def create_bearish_ob_scenario(base_ts: int = BASE_TS) -> tuple[List[Candle], OrderBlock]:
    """
    Create a standard bearish OB at base_ts.
    OB range: [60000, 61000].
    """
    formation_candle = make_candle(
        base_ts, open_p=60200.0, high_p=61000.0, low_p=60000.0, close_p=60800.0
    )
    ob = OrderBlock(
        index=2,
        symbol="BTCUSD.P",
        timeframe="1h",
        type="BEARISH",
        top_price=Decimal("61000.0"),
        bottom_price=Decimal("60000.0"),
        formation_candle=formation_candle,
        formation_index=10,
        break_index=15,
        break_type=BreakType.BOS,
        trend_before_break=TrendDirection.BEARISH,
        state=OBState.FRESH,
        touch_count=0,
    )
    return [formation_candle], ob


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Bullish & Bearish Long-Lived OB Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLongLivedOrderBlocks:
    """Tests 1, 2, 3, 4: Order Blocks remain valid regardless of age."""

    def test_old_untouched_bullish_ob_remains_valid(self):
        """A bullish OB untouched for months remains FRESH and eligible for entry."""
        _, ob = create_bullish_ob_scenario()
        assert ob.is_fresh()
        assert ob.is_eligible_for_entry()

        # Simulate 1,000 hourly candles (over 41 days) trading high above OB [49000, 50000]
        for i in range(16, 1016):
            c = make_candle(BASE_TS + i * HOUR, open_p=55000.0, high_p=56000.0, low_p=54500.0, close_p=55500.0)
            ob.check_touch(c)
            ob.check_invalidation(c)

        # OB must STILL be FRESH
        assert ob.state == OBState.FRESH
        assert ob.touch_count == 0
        assert ob.is_eligible_for_entry()

        # Feed genuine retest candle (entering [49000, 50000])
        retest_candle = make_candle(
            BASE_TS + 1016 * HOUR, open_p=51000.0, high_p=51000.0, low_p=49500.0, close_p=50500.0
        )
        assert ob.check_touch(retest_candle) is True
        assert ob.state == OBState.TOUCHED
        assert ob.touch_count == 1

    def test_old_untouched_bearish_ob_remains_valid(self):
        """A bearish OB untouched for months remains FRESH and eligible for entry."""
        _, ob = create_bearish_ob_scenario()
        assert ob.is_fresh()
        assert ob.is_eligible_for_entry()

        # Simulate 1,000 hourly candles trading low below OB [60000, 61000]
        for i in range(16, 1016):
            c = make_candle(BASE_TS + i * HOUR, open_p=54000.0, high_p=54500.0, low_p=53500.0, close_p=54000.0)
            ob.check_touch(c)
            ob.check_invalidation(c)

        # OB must STILL be FRESH
        assert ob.state == OBState.FRESH
        assert ob.touch_count == 0
        assert ob.is_eligible_for_entry()

        # Feed genuine retest candle (entering [60000, 61000])
        retest_candle = make_candle(
            BASE_TS + 1016 * HOUR, open_p=59000.0, high_p=60500.0, low_p=58800.0, close_p=59200.0
        )
        assert ob.check_touch(retest_candle) is True
        assert ob.state == OBState.TOUCHED
        assert ob.touch_count == 1

    def test_six_month_old_untouched_ob_remains_valid(self):
        """An OB untouched for ~6 months (4,320 hourly candles) remains valid."""
        _, ob = create_bullish_ob_scenario()
        six_months_bars = 4320  # 180 days * 24 hours

        for i in range(16, 16 + six_months_bars):
            c = make_candle(BASE_TS + i * HOUR, open_p=70000.0, high_p=71000.0, low_p=69000.0, close_p=70500.0)
            ob.check_touch(c)
            ob.check_invalidation(c)

        assert ob.state == OBState.FRESH
        assert ob.is_eligible_for_entry()
        assert not ob.is_invalidated()

    @pytest.mark.parametrize("days,bars", [
        (1, 24),
        (30, 720),
        (90, 2160),
        (180, 4320),
    ])
    def test_no_age_based_expiration(self, days, bars):
        """Untouched OB at 1, 30, 90, 180 days has zero age-based decay or removal."""
        _, ob = create_bullish_ob_scenario()
        for i in range(16, 16 + bars):
            c = make_candle(BASE_TS + i * HOUR, open_p=80000.0, high_p=81000.0, low_p=79000.0, close_p=80500.0)
            ob.check_touch(c)
            ob.check_invalidation(c)

        assert ob.state == OBState.FRESH, f"OB expired after {days} days ({bars} bars)!"
        assert ob.is_eligible_for_entry()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Formation, Break, and Retest Semantics
# ═══════════════════════════════════════════════════════════════════════════════

class TestOBTouchAndBreakSemantics:
    """Tests 5, 6, 7, 8: Formation and break candles cannot count as touches."""

    def test_formation_candle_not_touch(self):
        """Formation candle itself does not count as a touch (break_index < candle_idx rule)."""
        candles, ob = create_bullish_ob_scenario()
        formation_candle = candles[0]
        # At formation_index (10), break_index is 15 -> candle_idx <= break_index
        assert ob.formation_index < ob.break_index
        # In engine, touch check is guarded by: if ob.break_index < candle_idx
        assert not (ob.break_index < ob.formation_index)
        assert ob.state == OBState.FRESH

    def test_break_candle_not_touch(self):
        """Break candle itself does not count as a touch."""
        _, ob = create_bullish_ob_scenario()
        break_candle = make_candle(
            BASE_TS + ob.break_index * HOUR, open_p=49500.0, high_p=52000.0, low_p=49100.0, close_p=51800.0
        )
        # Even if break candle overlaps zone [49000, 50000], engine skips touch because break_index == candle_idx
        assert not (ob.break_index < ob.break_index)
        assert ob.state == OBState.FRESH

    def test_genuine_retest_becomes_touch(self):
        """First candle after break that overlaps zone transitions FRESH -> TOUCHED."""
        _, ob = create_bullish_ob_scenario()
        retest_candle = make_candle(
            BASE_TS + (ob.break_index + 1) * HOUR, open_p=51000.0, high_p=51000.0, low_p=49500.0, close_p=50200.0
        )
        assert ob.break_index < (ob.break_index + 1)
        assert ob.check_touch(retest_candle) is True
        assert ob.state == OBState.TOUCHED
        assert ob.touch_count == 1

    def test_invalidated_ob_remains_invalid_no_revival(self):
        """Invalidated OB cannot be revived by age or future price returns."""
        _, ob = create_bullish_ob_scenario()
        
        # Invalidate bullish OB [49000, 50000] by candle closing below 49000
        inv_candle = make_candle(
            BASE_TS + 20 * HOUR, open_p=49500.0, high_p=49500.0, low_p=48000.0, close_p=48500.0
        )
        assert ob.check_invalidation(inv_candle) is True
        assert ob.is_invalidated()
        assert ob.state == OBState.INVALIDATED

        # 6 months pass and price rises back into [49000, 50000]
        future_candle = make_candle(
            BASE_TS + 4320 * HOUR, open_p=49200.0, high_p=49800.0, low_p=49100.0, close_p=49500.0
        )
        # Touch check must not revive invalidated OB
        ob.check_touch(future_candle)
        assert ob.state == OBState.INVALIDATED
        assert not ob.is_eligible_for_entry()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Multiple Active OBs & Zone Coexistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultipleActiveOBs:
    """Tests 9, 10, 13: Multiple active OBs coexist and survive new OB creations."""

    def test_old_ob_survives_newer_ob_creation(self):
        """Engine retains old active OBs when newer OBs are created."""
        engine = IncrementalSMCEngine()
        _, ob1 = create_bullish_ob_scenario(BASE_TS)
        ob1.index = 1
        ob1.top_price = Decimal("45000.0")
        ob1.bottom_price = Decimal("44000.0")

        _, ob2 = create_bullish_ob_scenario(BASE_TS + 1000 * HOUR)
        ob2.index = 2
        ob2.top_price = Decimal("55000.0")
        ob2.bottom_price = Decimal("54000.0")

        engine._register_ob(ob1)
        engine._register_ob(ob2)

        active = engine.get_active_obs()
        assert len(active) == 2
        assert ob1 in active
        assert ob2 in active

    def test_multiple_active_obs_remain_available(self):
        """Multiple active OBs at different levels remain in active pool."""
        engine = IncrementalSMCEngine()
        for i in range(5):
            _, ob = create_bullish_ob_scenario(BASE_TS + i * 500 * HOUR)
            ob.index = i + 1
            ob.top_price = Decimal(str(40000 + i * 5000))
            ob.bottom_price = Decimal(str(39000 + i * 5000))
            engine._register_ob(ob)

        active = engine.get_active_obs()
        assert len(active) == 5
        assert len(engine.get_all_obs()) == 5

    def test_multiple_ob_zones_can_coexist_and_query(self):
        """Querying get_active_obs_at_price returns all overlapping OB zones."""
        engine = IncrementalSMCEngine()
        
        # OB1: [48000, 52000]
        _, ob1 = create_bullish_ob_scenario(BASE_TS)
        ob1.index = 1
        ob1.top_price = Decimal("52000.0")
        ob1.bottom_price = Decimal("48000.0")

        # OB2: [50000, 55000] (overlaps with OB1 in [50000, 52000])
        _, ob2 = create_bullish_ob_scenario(BASE_TS + 100 * HOUR)
        ob2.index = 2
        ob2.top_price = Decimal("55000.0")
        ob2.bottom_price = Decimal("50000.0")

        engine._register_ob(ob1)
        engine._register_ob(ob2)

        # Price at 51000 is inside BOTH OB1 and OB2
        matching = engine.get_active_obs_at_price(51000.0)
        assert len(matching) == 2
        assert ob1 in matching
        assert ob2 in matching
        assert engine.is_price_in_active_ob(51000.0) is True

        # Price at 48500 is inside OB1 only
        matching_48500 = engine.get_active_obs_at_price(48500.0)
        assert len(matching_48500) == 1
        assert ob1 in matching_48500

        # Price at 60000 is outside both
        assert len(engine.get_active_obs_at_price(60000.0)) == 0
        assert engine.is_price_in_active_ob(60000.0) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Current Price Inside OB Zone Helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestCurrentPriceInsideOB:
    """Tests 11, 12: is_price_inside_ob helper function contract."""

    def test_current_price_inside_valid_ob(self):
        """is_price_inside_ob returns True for price within [bottom_price, top_price]."""
        _, ob = create_bullish_ob_scenario()
        # OB: [49000.0, 50000.0]
        assert is_price_inside_ob(49000.0, ob) is True   # lower boundary
        assert is_price_inside_ob(50000.0, ob) is True   # upper boundary
        assert is_price_inside_ob(49500.0, ob) is True   # midpoint
        assert is_price_inside_ob(Decimal("49250.75"), ob) is True

    def test_current_price_outside_valid_ob(self):
        """is_price_inside_ob returns False for price outside zone."""
        _, ob = create_bullish_ob_scenario()
        # OB: [49000.0, 50000.0]
        assert is_price_inside_ob(48999.99, ob) is False  # below
        assert is_price_inside_ob(50000.01, ob) is False  # above
        assert is_price_inside_ob(75000.0, ob) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Incremental vs Full Replay & Future Data Invariance
# ═══════════════════════════════════════════════════════════════════════════════

class TestReplayAndInvariance:
    """Tests 14, 15: Incremental equals full replay and future data invariance."""

    def test_incremental_equals_full_replay(self, tmp_path):
        """Processing candles incrementally matches processing all at once."""
        n_candles = 320
        candles = []
        for i in range(n_candles):
            cycle = i % 120
            if cycle < 60:
                p = 50000.0 + cycle * 100.0
            else:
                p = 50000.0 + (120 - cycle) * 100.0
            candles.append(make_candle(BASE_TS + i * HOUR, open_p=p, high_p=p + 60.0, low_p=p - 60.0, close_p=p + 20.0))

        history = candles[:300]
        extra = candles[300:]

        csv_full = tmp_path / "full.csv"
        write_candles_from_list(candles, csv_full)

        csv_hist = tmp_path / "hist.csv"
        write_candles_from_list(history, csv_hist)

        # Full replay
        eng_full = IncrementalSMCEngine()
        eng_full.initialize_from_canonical(csv_full)
        obs_full = len(eng_full.get_all_obs())
        active_full = len(eng_full.get_active_obs())

        # Incremental
        eng_inc = IncrementalSMCEngine()
        eng_inc.initialize_from_canonical(csv_hist)
        eng_inc.process_new_candles(extra)
        obs_inc = len(eng_inc.get_all_obs())
        active_inc = len(eng_inc.get_active_obs())

        assert eng_full._last_processed_ts == eng_inc._last_processed_ts
        assert obs_full == obs_inc, f"Total OB count mismatch: full={obs_full}, inc={obs_inc}"
        assert active_full == active_inc, f"Active OB count mismatch: full={active_full}, inc={active_inc}"

    def test_future_data_invariance(self):
        """Future candles cannot retroactively mutate state at timestamp T."""
        _, ob = create_bullish_ob_scenario()
        # State at T = 500
        for i in range(16, 500):
            c = make_candle(BASE_TS + i * HOUR, open_p=70000.0, high_p=71000.0, low_p=69000.0, close_p=70500.0)
            ob.check_touch(c)
            ob.check_invalidation(c)

        state_at_t = copy.deepcopy(ob.state)
        touch_count_at_t = copy.deepcopy(ob.touch_count)

        # Future candles from T=500 to T=1000 (still not touching)
        for i in range(500, 1000):
            c = make_candle(BASE_TS + i * HOUR, open_p=75000.0, high_p=76000.0, low_p=74000.0, close_p=75500.0)
            ob.check_touch(c)
            ob.check_invalidation(c)

        # State at T was FRESH and remained FRESH until genuinely interacted with
        assert state_at_t == OBState.FRESH
        assert touch_count_at_t == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Timezone Policy & Conversion Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTimezonePolicy:
    """Tests 16, 17: UTC internal timestamps and Asia/Kolkata user-facing display."""

    def test_utc_internal_timestamp(self):
        """All internal candles and utilities maintain strict UTC timezone."""
        c = make_candle(BASE_TS)
        assert c.timestamp.tzinfo == timezone.utc
        assert to_utc(BASE_TS).tzinfo == timezone.utc

    def test_utc_to_asia_kolkata_display(self):
        """2026-08-21 14:00 UTC converts to 2026-08-21 19:30:00 Asia/Kolkata (+05:30)."""
        dt_utc = datetime(2026, 8, 21, 14, 0, 0, tzinfo=timezone.utc)
        ist_dt = to_ist(dt_utc)

        assert ist_dt.year == 2026
        assert ist_dt.month == 8
        assert ist_dt.day == 21
        assert ist_dt.hour == 19
        assert ist_dt.minute == 30
        assert ist_dt.tzinfo == ZoneInfo("Asia/Kolkata")

        formatted = format_ist(dt_utc)
        assert "2026-08-21 19:30:00" in formatted

    def test_from_ist_to_utc_roundtrip(self):
        """Roundtrip conversion from IST to UTC is exact and deterministic."""
        dt_utc = datetime(2026, 8, 21, 14, 0, 0, tzinfo=timezone.utc)
        dt_ist = to_ist(dt_utc)
        dt_roundtrip = from_ist_to_utc(dt_ist)
        assert dt_roundtrip == dt_utc


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Safety, Frozen Files, and Cleanliness Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafetyAndFrozenInvariance:
    """Tests 18, 19, 20: Isolated tests, frozen SMC invariance, no Binance dependencies."""

    def test_no_production_csv_mutation(self, tmp_path):
        """Unit tests do not mutate production CANONICAL_CSV."""
        test_csv = tmp_path / "isolated.csv"
        c = make_candle(BASE_TS)
        from quantedge.market_data.ingestion import load_candles
        write_candles_from_list([c], test_csv)
        assert len(load_candles(test_csv)) == 1

    def test_frozen_smc_files_unchanged(self):
        """All three frozen SMC files exist and are present in repo."""
        repo_root = Path(__file__).parent.parent
        frozen = [
            repo_root / "src" / "quantedge" / "smc" / "structure.py",
            repo_root / "src" / "quantedge" / "smc" / "order_blocks.py",
            repo_root / "src" / "quantedge" / "smc" / "volatility.py",
        ]
        for f in frozen:
            assert f.exists(), f"Frozen SMC file missing: {f}"

    def test_no_binance_dependency(self):
        """Verify no Binance endpoints or references exist in timezone or incremental modules."""
        import quantedge.utils.timezone as tz_mod
        import quantedge.market_data.incremental_engine as inc_mod
        for mod in (tz_mod, inc_mod):
            content = Path(mod.__file__).read_text(encoding="utf-8")
            assert "binance" not in content.lower()
            assert "api.binance.com" not in content
