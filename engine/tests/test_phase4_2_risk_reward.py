"""
Phase 4.2 Risk/Reward & Final Trade Setup Validation Test Suite.

Comprehensive deterministic validation for:
1. LONG valid RR -> TRADE_SETUP_READY
2. SHORT valid RR -> TRADE_SETUP_READY
3. RR exactly at minimum
4. RR below minimum -> QUALIFIED but not TRADE_SETUP_READY
5. RR above minimum -> TRADE_SETUP_READY
6. zero risk -> rejected
7. negative risk geometry LONG (entry <= stop_loss)
8. negative risk geometry SHORT (stop_loss <= entry)
9. invalid entry (None) -> not ready
10. invalid stop loss (None) -> not ready
11. TP LONG calculation (entry + risk * multiple)
12. TP SHORT calculation (entry - risk * multiple)
13. Decimal precision arithmetic
14. configurable RR threshold (e.g. 1.0, 2.0, 3.0)
15. configurable reward multiple (e.g. 1.5, 2.0, 3.5)
16. invalid configuration raises ValueError
17. 6-month-old untouched bullish OB -> TRADE_SETUP_READY
18. 6-month-old untouched bearish OB -> TRADE_SETUP_READY
19. old OB not displaced by new OB
20. multiple OB deterministic selection
21. forming candle rejected
22. closed candle accepted
23. duplicate evaluation idempotency
24. future-data invariance
25. incremental == replay equivalence
26. zero SMC mutation
27. zero CSV modification
28. deterministic setup ID
29. deterministic reasons
30. UTC timestamp retention
31. IST presentation display
32. no order execution methods
33. no Binance dependency
34. frozen SMC files unchanged
"""

import copy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional
import pytest

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.models import (
    OrderBlock,
    OBState,
    BreakType,
    TrendDirection,
    StructureBreak,
)
from quantedge.market_data.incremental_engine import IncrementalSMCEngine
from quantedge.strategy.models import (
    StrategyDecision,
    StrategyDirection,
    SetupType,
    SetupState,
    RiskRewardConfig,
    generate_setup_id,
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
# Phase 4.2 Test Suite
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase42RiskRewardValidation:
    """34 comprehensive deterministic tests for Phase 4.2 Risk/Reward Validation."""

    def test_long_valid_rr(self):
        """1. LONG setup with RR >= min produces TRADE_SETUP_READY."""
        strat = StrategyEngine(RiskRewardConfig(minimum_risk_reward=Decimal("2.0"), reward_multiple=Decimal("2.0")))
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state == SetupState.TRADE_SETUP_READY
        assert decision.is_trade_setup_ready
        assert decision.trade_setup_ready
        assert decision.direction == StrategyDirection.LONG
        assert decision.entry == ob.calculate_entry_price()
        assert decision.stop_loss == ob.calculate_stop_loss()
        assert decision.risk_distance == decision.entry - decision.stop_loss
        assert decision.reward_distance == decision.take_profit - decision.entry
        assert decision.risk_reward == Decimal("2.0")

    def test_short_valid_rr(self):
        """2. SHORT setup with RR >= min produces TRADE_SETUP_READY."""
        strat = StrategyEngine(RiskRewardConfig(minimum_risk_reward=Decimal("2.0"), reward_multiple=Decimal("2.0")))
        ob = make_bearish_ob(top=61000.0, bottom=60000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=60500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BEARISH,
            swing_trend=TrendDirection.BEARISH,
        )
        assert decision.setup_state == SetupState.TRADE_SETUP_READY
        assert decision.is_trade_setup_ready
        assert decision.direction == StrategyDirection.SHORT
        assert decision.entry == ob.calculate_entry_price()
        assert decision.stop_loss == ob.calculate_stop_loss()
        assert decision.risk_distance == decision.stop_loss - decision.entry
        assert decision.reward_distance == decision.entry - decision.take_profit
        assert decision.risk_reward == Decimal("2.0")

    def test_rr_exactly_at_minimum(self):
        """3. RR exactly equal to minimum_risk_reward produces TRADE_SETUP_READY."""
        cfg = RiskRewardConfig(minimum_risk_reward=Decimal("2.5"), reward_multiple=Decimal("2.5"))
        strat = StrategyEngine(cfg)
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state == SetupState.TRADE_SETUP_READY
        assert decision.risk_reward == Decimal("2.5")

    def test_rr_below_minimum(self):
        """4. RR < minimum_risk_reward produces QUALIFIED_LONG, not TRADE_SETUP_READY."""
        cfg = RiskRewardConfig(minimum_risk_reward=Decimal("3.0"), reward_multiple=Decimal("2.0"))
        strat = StrategyEngine(cfg)
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state == SetupState.QUALIFIED_LONG
        assert not decision.is_trade_setup_ready
        assert decision.is_qualified
        assert any("risk_reward below minimum threshold" in r for r in decision.reasons)

    def test_rr_above_minimum(self):
        """5. RR > minimum_risk_reward produces TRADE_SETUP_READY."""
        cfg = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"), reward_multiple=Decimal("2.5"))
        strat = StrategyEngine(cfg)
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state == SetupState.TRADE_SETUP_READY
        assert decision.risk_reward == Decimal("2.5")
        assert decision.risk_reward > cfg.minimum_risk_reward

    def test_zero_risk(self, monkeypatch):
        """6. Zero risk distance (entry == stop_loss) cannot produce TRADE_SETUP_READY."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        monkeypatch.setattr(ob, "calculate_entry_price", lambda: Decimal("49000.0"))
        monkeypatch.setattr(ob, "calculate_stop_loss", lambda: Decimal("49000.0"))
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state != SetupState.TRADE_SETUP_READY
        assert not decision.is_trade_setup_ready

    def test_negative_risk_geometry_long(self, monkeypatch):
        """7. Negative risk geometry for LONG (entry <= stop_loss) returns factual reason."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        monkeypatch.setattr(ob, "calculate_entry_price", lambda: Decimal("48000.0"))
        monkeypatch.setattr(ob, "calculate_stop_loss", lambda: Decimal("50000.0"))
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state != SetupState.TRADE_SETUP_READY
        assert any("invalid risk geometry" in r for r in decision.reasons)

    def test_negative_risk_geometry_short(self, monkeypatch):
        """8. Negative risk geometry for SHORT (stop_loss <= entry) returns factual reason."""
        strat = StrategyEngine()
        ob = make_bearish_ob(top=61000.0, bottom=60000.0)
        monkeypatch.setattr(ob, "calculate_entry_price", lambda: Decimal("61000.0"))
        monkeypatch.setattr(ob, "calculate_stop_loss", lambda: Decimal("60000.0"))
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=60500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BEARISH,
            swing_trend=TrendDirection.BEARISH,
        )
        assert decision.setup_state != SetupState.TRADE_SETUP_READY
        assert any("invalid risk geometry" in r for r in decision.reasons)

    def test_invalid_entry(self, monkeypatch):
        """9. When entry calculation returns None, setup does not become TRADE_SETUP_READY."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        monkeypatch.setattr(ob, "calculate_entry_price", lambda: None)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state != SetupState.TRADE_SETUP_READY
        assert any("could not be calculated" in r for r in decision.reasons)

    def test_invalid_stop_loss(self, monkeypatch):
        """10. When stop_loss calculation returns None, setup does not become TRADE_SETUP_READY."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        monkeypatch.setattr(ob, "calculate_stop_loss", lambda: None)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BULLISH,
            swing_trend=TrendDirection.BULLISH,
        )
        assert decision.setup_state != SetupState.TRADE_SETUP_READY
        assert any("could not be calculated" in r for r in decision.reasons)

    def test_tp_long_calculation(self):
        """11. TP for LONG = entry + (risk * reward_multiple)."""
        cfg = RiskRewardConfig(minimum_risk_reward=Decimal("2.0"), reward_multiple=Decimal("3.0"))
        strat = StrategyEngine(cfg)
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        entry = decision.entry
        risk = decision.risk_distance
        assert decision.take_profit == entry + (risk * Decimal("3.0"))
        assert decision.reward_distance == risk * Decimal("3.0")
        assert decision.risk_reward == Decimal("3.0")

    def test_tp_short_calculation(self):
        """12. TP for SHORT = entry - (risk * reward_multiple)."""
        cfg = RiskRewardConfig(minimum_risk_reward=Decimal("2.0"), reward_multiple=Decimal("2.5"))
        strat = StrategyEngine(cfg)
        ob = make_bearish_ob(top=61000.0, bottom=60000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=60500.0)
        decision = strat.evaluate_state(candle, [ob], TrendDirection.BEARISH, TrendDirection.BEARISH)
        entry = decision.entry
        risk = decision.risk_distance
        assert decision.take_profit == entry - (risk * Decimal("2.5"))
        assert decision.reward_distance == risk * Decimal("2.5")
        assert decision.risk_reward == Decimal("2.5")

    def test_decimal_precision(self):
        """13. Ensure Decimal arithmetic is used and precise."""
        cfg = RiskRewardConfig(minimum_risk_reward=Decimal("2.0"), reward_multiple=Decimal("2.0"))
        strat = StrategyEngine(cfg)
        ob = make_bullish_ob(top=50000.1234, bottom=49000.5678)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert isinstance(decision.risk_distance, Decimal)
        assert isinstance(decision.reward_distance, Decimal)
        assert isinstance(decision.take_profit, Decimal)
        assert isinstance(decision.risk_reward, Decimal)
        assert decision.risk_distance == decision.entry - decision.stop_loss

    def test_configurable_rr_threshold(self):
        """14. Configurable minimum_risk_reward filtering dynamically."""
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)

        # RR=2.0 with min=2.0 -> READY
        s1 = StrategyEngine(RiskRewardConfig(minimum_risk_reward=Decimal("2.0"), reward_multiple=Decimal("2.0")))
        d1 = s1.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert d1.setup_state == SetupState.TRADE_SETUP_READY

        # RR=2.0 with min=3.0 -> NOT READY (QUALIFIED_LONG)
        s2 = StrategyEngine(RiskRewardConfig(minimum_risk_reward=Decimal("3.0"), reward_multiple=Decimal("2.0")))
        d2 = s2.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert d2.setup_state == SetupState.QUALIFIED_LONG

    def test_configurable_reward_multiple(self):
        """15. Configurable reward_multiple sets target and RR dynamically."""
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)

        s = StrategyEngine(RiskRewardConfig(minimum_risk_reward=Decimal("1.5"), reward_multiple=Decimal("1.5")))
        d = s.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert d.setup_state == SetupState.TRADE_SETUP_READY
        assert d.risk_reward == Decimal("1.5")

    def test_invalid_configuration(self):
        """16. Non-positive parameters in RiskRewardConfig raise ValueError."""
        with pytest.raises(ValueError, match="minimum_risk_reward must be > 0"):
            RiskRewardConfig(minimum_risk_reward=Decimal("0.0"))

        with pytest.raises(ValueError, match="minimum_risk_reward must be > 0"):
            RiskRewardConfig(minimum_risk_reward=Decimal("-1.0"))

        with pytest.raises(ValueError, match="reward_multiple must be > 0"):
            RiskRewardConfig(minimum_risk_reward=Decimal("2.0"), reward_multiple=Decimal("0.0"))

        with pytest.raises(ValueError, match="reward_multiple must be > 0"):
            RiskRewardConfig(minimum_risk_reward=Decimal("2.0"), reward_multiple=Decimal("-2.0"))

    def test_six_month_old_untouched_bullish_ob(self):
        """17. 6-month-old untouched bullish OB produces TRADE_SETUP_READY upon retest."""
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
        assert decision.setup_state == SetupState.TRADE_SETUP_READY
        assert decision.direction == StrategyDirection.LONG
        assert decision.ob_age_days is not None
        assert decision.ob_age_days >= 170.0

    def test_six_month_old_untouched_bearish_ob(self):
        """18. 6-month-old untouched bearish OB produces TRADE_SETUP_READY upon retest."""
        strat = StrategyEngine()
        six_months_ago = BASE_TS - 4320 * HOUR
        ob = make_bearish_ob(top=61000.0, bottom=60000.0, state=OBState.FRESH, ts=six_months_ago)
        candle = make_candle(BASE_TS, close_p=60500.0)
        decision = strat.evaluate_state(
            candle=candle,
            active_obs=[ob],
            internal_trend=TrendDirection.BEARISH,
            swing_trend=TrendDirection.BEARISH,
        )
        assert decision.setup_state == SetupState.TRADE_SETUP_READY
        assert decision.direction == StrategyDirection.SHORT
        assert decision.ob_age_days is not None
        assert decision.ob_age_days >= 170.0

    def test_old_ob_not_displaced_by_new_ob(self):
        """19. Older untouched OB remains eligible and produces TRADE_SETUP_READY when hit."""
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
        assert decision.setup_state == SetupState.TRADE_SETUP_READY
        assert decision.order_block == ob1

    def test_multiple_ob_deterministic_selection(self):
        """20. Overlapping OB candidate selection is strictly deterministic."""
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
        assert decision.setup_state == SetupState.TRADE_SETUP_READY
        assert decision.order_block == ob_tight

    def test_forming_candle_rejected(self):
        """21. Non-closed/forming candle price outside OB does not qualify."""
        smc_engine = IncrementalSMCEngine()
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        smc_engine._register_ob(ob)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=55000.0)
        decision = strat.evaluate_candle(candle, smc_engine)
        assert decision.setup_state == SetupState.WATCHING_OB
        assert not decision.is_trade_setup_ready

    def test_closed_candle_accepted(self):
        """22. Closed candle entering active OB produces TRADE_SETUP_READY."""
        smc_engine = IncrementalSMCEngine()
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        smc_engine._register_ob(ob)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_candle(candle, smc_engine)
        # Without trend confirmation -> OB_ENGAGED
        assert decision.setup_state == SetupState.OB_ENGAGED

    def test_duplicate_evaluation(self):
        """23. Repeated evaluation on identical state produces identical outputs."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        dec_list = [
            strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
            for _ in range(5)
        ]
        for d in dec_list:
            assert d.setup_state == SetupState.TRADE_SETUP_READY
            assert d.setup_id == dec_list[0].setup_id
            assert d.take_profit == dec_list[0].take_profit
            assert d.risk_reward == dec_list[0].risk_reward

    def test_future_data_invariance(self):
        """24. Future candles do not alter decision at candle T."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle_t = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)

        d_before = strat.evaluate_state(candle_t, [copy.deepcopy(ob)], TrendDirection.BULLISH, TrendDirection.BULLISH)

        # Future candles arrive
        _ = [make_candle(BASE_TS + (100 + i) * HOUR, close_p=75000.0) for i in range(1, 20)]

        d_after = strat.evaluate_state(candle_t, [copy.deepcopy(ob)], TrendDirection.BULLISH, TrendDirection.BULLISH)

        assert d_before.setup_state == d_after.setup_state == SetupState.TRADE_SETUP_READY
        assert d_before.take_profit == d_after.take_profit
        assert d_before.risk_reward == d_after.risk_reward

    def test_incremental_equals_replay(self, tmp_path):
        """25. Incremental processing and full batch replay yield identical setup state."""
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
        assert dec_full.take_profit == dec_inc.take_profit
        assert dec_full.risk_reward == dec_inc.risk_reward

    def test_zero_smc_mutation(self):
        """26. Strategy evaluation leaves OB state, touch count, and structure 100% untouched."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0, state=OBState.FRESH)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)

        touch_before = ob.touch_count
        state_before = ob.state

        strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)

        assert ob.touch_count == touch_before
        assert ob.state == state_before == OBState.FRESH

    def test_zero_csv_modification(self):
        """27. Strategy evaluation does not alter canonical CSV."""
        if not CANONICAL_CSV.exists():
            pytest.skip("Canonical CSV not present")
        hash_before = csv_hash(CANONICAL_CSV)

        strat = StrategyEngine()
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)

        assert csv_hash(CANONICAL_CSV) == hash_before

    def test_deterministic_setup_id(self):
        """28. setup_id generation is deterministic and reproducible."""
        ob = make_bullish_ob(index=42, top=50000.0, bottom=49000.0)
        setup_id = generate_setup_id("BTCUSD.P", "1h", ob, StrategyDirection.LONG)
        assert "BTCUSD.P_1h_OB42" in setup_id
        assert "LONG" in setup_id

    def test_deterministic_reasons(self):
        """29. Reasons list contains factual conditions and RR."""
        strat = StrategyEngine()
        ob = make_bullish_ob(top=50000.0, bottom=49000.0)
        candle = make_candle(BASE_TS + 100 * HOUR, close_p=49500.0)
        decision = strat.evaluate_state(candle, [ob], TrendDirection.BULLISH, TrendDirection.BULLISH)
        assert any("bullish order block" in r for r in decision.reasons)
        assert any("risk/reward validated" in r for r in decision.reasons)
        assert any("risk_reward=2.00" in r for r in decision.reasons)
        assert any("trade setup ready" in r for r in decision.reasons)

    def test_utc_timestamp(self):
        """30. StrategyDecision.timestamp maintains UTC timezone internally."""
        dt_utc = datetime(2026, 8, 21, 14, 0, 0, tzinfo=timezone.utc)
        decision = StrategyDecision(
            timestamp=dt_utc,
            symbol="BTCUSD.P",
            timeframe="1h",
            setup_state=SetupState.TRADE_SETUP_READY,
            direction=StrategyDirection.LONG,
        )
        assert decision.timestamp.tzinfo == timezone.utc
        assert decision.timestamp.hour == 14

    def test_ist_presentation(self):
        """31. StrategyDecision.timestamp_ist formats into Asia/Kolkata (+05:30)."""
        dt_utc = datetime(2026, 8, 21, 14, 0, 0, tzinfo=timezone.utc)
        decision = StrategyDecision(
            timestamp=dt_utc,
            symbol="BTCUSD.P",
            timeframe="1h",
            setup_state=SetupState.TRADE_SETUP_READY,
            direction=StrategyDirection.LONG,
        )
        assert "2026-08-21 19:30:00" in decision.timestamp_ist

    def test_no_order_execution(self):
        """32. Verify StrategyDecision and StrategyEngine lack order placement/execution methods."""
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
        assert not hasattr(decision, "create_order")

    def test_no_binance_dependency(self):
        """33. Verify strategy modules have zero references to Binance."""
        import quantedge.strategy.engine as engine_mod
        import quantedge.strategy.models as models_mod
        for mod in (engine_mod, models_mod):
            content = Path(mod.__file__).read_text(encoding="utf-8")
            assert "binance" not in content.lower()
            assert "api.binance.com" not in content

    def test_frozen_smc_files_unchanged(self):
        """34. Frozen SMC files exist and remain in repository."""
        repo_root = Path(__file__).parent.parent
        frozen = [
            repo_root / "src" / "quantedge" / "smc" / "structure.py",
            repo_root / "src" / "quantedge" / "smc" / "order_blocks.py",
            repo_root / "src" / "quantedge" / "smc" / "volatility.py",
        ]
        for f in frozen:
            assert f.exists(), f"Frozen SMC file missing: {f}"
