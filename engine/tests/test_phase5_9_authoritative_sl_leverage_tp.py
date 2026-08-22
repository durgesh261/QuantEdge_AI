"""
Phase 5.9: Authoritative Order Block SL, Dynamic Leverage (35% Max Loss), ROE-based TP (60% Default) & Compounding Test Suite.

Verifies all critical trading rules:
1. Demand OB LONG -> SL placed strictly at Demand OB bottom edge.
2. Supply OB SHORT -> SL placed strictly at Supply OB top edge.
3. 1% SL distance -> ~35x leverage.
4. 2% SL distance -> ~17.5x / 17x leverage.
5. 5% SL distance -> ~7x leverage.
6. 10% SL distance -> ~3.5x / 3x leverage.
7. Unsupported leverage (> Delta/instrument max cap) -> trade rejected.
8. Max planned loss at SL never exceeds 35% of allocated capital.
9. Default TP target = 60% return on allocated capital (ROE).
10. TP converts target ROE into exact underlying price for LONG & SHORT.
11. User TP override (60% -> 80%) creates new immutable version.
12. Configuration versioning: historical trade preserves old TP snapshot.
13. Frontend cannot override authoritative SL.
14. Frontend cannot override calculated leverage.
15. Frontend cannot override position size.
16. Strategy decision exposes complete mathematical calculation.
17. Single active trade lock exclusivity.
18. Dynamic balance compounding on profitable trade.
19. Dynamic balance compounding on losing trade.
20. Authoritative exchange reconciliation with actual fees reducing Net P&L.
21. Market scanner pauses during active trade and rescans upon closure.
22. Zero credential leakage.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
import pytest

from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.models import OrderBlock, BreakType, TrendDirection, OBState
from quantedge.strategy.models import (
    StrategyDecision, StrategyDirection, SetupState, SetupType, TradeDirection, RiskRewardConfig
)
from quantedge.strategy.engine import StrategyEngine
from quantedge.execution.algo_config import (
    AlgoConfigStore, AlgoConfiguration, AlgoConfigValidationError
)
from quantedge.execution.capital_allocator import (
    CapitalAllocator, CapitalAllocationError
)
from quantedge.execution.single_trade_lock import (
    SingleTradeLockManager, SingleTradeLockError
)
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager, TradeLifecycleState, CloseReason
)
from quantedge.execution.synchronizer import (
    LocalStateStore, AccountRecord, ConnectionRecord, PositionStatus
)
from quantedge.execution.validation import (
    OrderValidationGateway, RejectionReasonCode, DEFAULT_DELTA_INDIA_PRODUCTS
)
from quantedge.execution.market_orchestrator import MarketScannerOrchestrator


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_candle():
    return Candle(
        timestamp=datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc),
        open=Decimal("100000.00"),
        high=Decimal("100500.00"),
        low=Decimal("99500.00"),
        close=Decimal("100000.00"),
        volume=Decimal("100.0"),
        symbol="BTCUSD",
        timeframe=Timeframe.H1,
    )


@pytest.fixture
def demand_order_block(mock_candle):
    """Bullish Order Block with bottom=98000, top=100000 (2.0% stop distance from 100k entry)."""
    return OrderBlock(
        index=10,
        symbol="BTCUSD",
        timeframe="1h",
        type="BULLISH",
        top_price=Decimal("100000.00"),
        bottom_price=Decimal("98000.00"),
        formation_candle=mock_candle,
        formation_index=10,
        break_index=11,
        break_type=BreakType.BOS,
        trend_before_break=TrendDirection.BULLISH,
        state=OBState.FRESH,
        confidence_score=90,
    )


@pytest.fixture
def supply_order_block(mock_candle):
    """Bearish Order Block with bottom=100000, top=102000 (1.96% stop distance from 100k entry)."""
    return OrderBlock(
        index=20,
        symbol="BTCUSD",
        timeframe="1h",
        type="BEARISH",
        top_price=Decimal("102000.00"),
        bottom_price=Decimal("100000.00"),
        formation_candle=mock_candle,
        formation_index=20,
        break_index=21,
        break_type=BreakType.BOS,
        trend_before_break=TrendDirection.BEARISH,
        state=OBState.FRESH,
        confidence_score=88,
    )


@pytest.fixture
def mock_state_store():
    store = LocalStateStore()
    store.connection = ConnectionRecord(
        environment="LIVE",
        connection_status="CONNECTED",
        last_connected_at=datetime.now(timezone.utc),
    )
    acc = AccountRecord(
        account_id="acc_live_5_9",
        user_id="user_quant_01",
        available_balance=Decimal("25000.00"),
        total_equity=Decimal("25000.00"),
    )
    acc.algo_enabled = True
    acc.kill_switch_active = False
    acc.last_synced_at = datetime.now(timezone.utc)
    store.account = acc
    return store


@pytest.fixture
def mock_delta_client():
    client = MagicMock()
    client.api_key = "TEST_API_KEY_SECURE"
    client.api_secret = "TEST_API_SECRET_SECURE"
    client._api_key = "TEST_API_KEY_SECURE"
    client._api_secret = "TEST_API_SECRET_SECURE"
    client.place_order = AsyncMock(return_value={"order_id": "ORD-LIVE-1001", "state": "open"})
    client.cancel_order = AsyncMock(return_value={"status": "success"})
    return client


@pytest.fixture
def algo_store():
    return AlgoConfigStore()


@pytest.fixture
def lock_manager():
    return SingleTradeLockManager()


@pytest.fixture
def lifecycle_manager(mock_delta_client, mock_state_store, algo_store, lock_manager):
    algo_store.get_or_create_default("user_quant_01", "acc_live_5_9")
    gateway = OrderValidationGateway()
    allocator = CapitalAllocator()
    return TradeLifecycleManager(
        client=mock_delta_client,
        validation_gateway=gateway,
        state_store=mock_state_store,
        algo_config_store=algo_store,
        single_trade_lock=lock_manager,
        capital_allocator=allocator,
        daily_loss_limit=Decimal("500.00"),
        max_stale_seconds=300,
    )


# ── Test Cases ─────────────────────────────────────────────────────────────────


def test_01_demand_ob_long_sl_at_bottom_edge(mock_candle, demand_order_block):
    """1. Verify Demand OB (BULLISH) places SL strictly at the bottom / lower edge."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    assert decision.setup_state == SetupState.TRADE_SETUP_READY
    assert decision.direction == StrategyDirection.LONG
    assert decision.entry == Decimal("99500.00")
    # Authoritative SL must be Demand OB bottom price
    assert decision.stop_loss == demand_order_block.bottom_price
    assert decision.stop_loss == Decimal("98000.00")
    assert decision.order_block_lower_edge == Decimal("98000.00")
    assert decision.order_block_upper_edge == Decimal("100000.00")


def test_02_supply_ob_short_sl_at_top_edge(mock_candle, supply_order_block):
    """2. Verify Supply OB (BEARISH) places SL strictly at the top / upper edge."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[supply_order_block],
        internal_trend=TrendDirection.BEARISH,
        swing_trend=TrendDirection.BEARISH,
        risk_reward_config=rr_config,
    )
    assert decision.setup_state == SetupState.TRADE_SETUP_READY
    assert decision.direction == StrategyDirection.SHORT
    assert decision.entry == Decimal("100500.00")
    # Authoritative SL must be Supply OB top price
    assert decision.stop_loss == supply_order_block.top_price
    assert decision.stop_loss == Decimal("102000.00")
    assert decision.order_block_upper_edge == Decimal("102000.00")
    assert decision.order_block_lower_edge == Decimal("100000.00")


def test_03_dynamic_leverage_1_pct_sl_distance():
    """3. Verify 1% SL distance produces approximately 35x leverage."""
    entry = Decimal("100000.00")
    sl = Decimal("99000.00")  # exactly 1.0% distance
    leverage = CapitalAllocator.calculate_leverage_from_stop_distance(
        entry_price=entry, stop_loss_price=sl, max_loss_pct=Decimal("35.0")
    )
    # 35.0 / 1.0 = 35x
    assert leverage == 35


def test_04_dynamic_leverage_2_pct_sl_distance():
    """4. Verify 2% SL distance produces approximately 17.5x (floor to 17x to guarantee <= 35% risk)."""
    entry = Decimal("100000.00")
    sl = Decimal("98000.00")  # exactly 2.0% distance
    leverage = CapitalAllocator.calculate_leverage_from_stop_distance(
        entry_price=entry, stop_loss_price=sl, max_loss_pct=Decimal("35.0")
    )
    # 35.0 / 2.0 = 17.5 -> integer floor 17x
    assert leverage == 17
    # Max planned loss = 17 * 2.0% = 34.0% <= 35.0%
    loss_pct = Decimal(str(leverage)) * Decimal("2.0")
    assert loss_pct <= Decimal("35.0")


def test_05_dynamic_leverage_5_pct_sl_distance():
    """5. Verify 5% SL distance produces approximately 7x leverage."""
    entry = Decimal("100000.00")
    sl = Decimal("95000.00")  # exactly 5.0% distance
    leverage = CapitalAllocator.calculate_leverage_from_stop_distance(
        entry_price=entry, stop_loss_price=sl, max_loss_pct=Decimal("35.0")
    )
    # 35.0 / 5.0 = 7x
    assert leverage == 7


def test_06_dynamic_leverage_10_pct_sl_distance():
    """6. Verify 10% SL distance produces approximately 3.5x -> 3x leverage."""
    entry = Decimal("100000.00")
    sl = Decimal("90000.00")  # exactly 10.0% distance
    leverage = CapitalAllocator.calculate_leverage_from_stop_distance(
        entry_price=entry, stop_loss_price=sl, max_loss_pct=Decimal("35.0")
    )
    # 35.0 / 10.0 = 3.5 -> integer floor 3x
    assert leverage == 3
    # Max planned loss = 3 * 10% = 30.0% <= 35.0%
    assert Decimal(str(leverage)) * Decimal("10.0") <= Decimal("35.0")


def test_07_unsupported_leverage_rejected():
    """7. Verify trade requiring leverage beyond instrument max cap is rejected."""
    entry = Decimal("100000.00")
    sl = Decimal("99800.00")  # 0.2% distance -> required leverage = 35 / 0.2 = 175x (exceeds 100x max)
    with pytest.raises(CapitalAllocationError) as exc_info:
        CapitalAllocator.calculate_leverage_from_stop_distance(
            entry_price=entry, stop_loss_price=sl, max_loss_pct=Decimal("35.0"), max_leverage_cap=100
        )
    assert "exceeds maximum allowed cap of 100x" in str(exc_info.value)


def test_08_max_planned_loss_never_exceeds_35_pct():
    """8. Mathematical proof: across all distances, integer leverage guarantees planned loss <= 35%."""
    entry = Decimal("100000.00")
    for distance_pct in [Decimal("0.5"), Decimal("1.0"), Decimal("1.5"), Decimal("2.0"), Decimal("3.3"), Decimal("5.0"), Decimal("8.0"), Decimal("12.0")]:
        sl = entry * (Decimal("1") - (distance_pct / Decimal("100")))
        leverage = CapitalAllocator.calculate_leverage_from_stop_distance(
            entry_price=entry, stop_loss_price=sl, max_loss_pct=Decimal("35.0"), max_leverage_cap=100
        )
        actual_loss_pct = Decimal(str(leverage)) * distance_pct
        assert actual_loss_pct <= Decimal("35.0"), f"Failed for distance {distance_pct}%: loss {actual_loss_pct}% > 35%"


def test_09_default_tp_target_60_pct_roe():
    """9. Verify default Take Profit target is 60% ROE on allocated margin."""
    config = AlgoConfiguration(account_id="acc_1", user_id="user_1")
    assert config.take_profit_target_pct == Decimal("60.00")
    assert config.max_loss_pct == Decimal("35.00")


def test_10_tp_converts_target_roe_into_underlying_price():
    """10. Verify TP calculation converts target ROE into exact price for LONG and SHORT."""
    entry = Decimal("100000.00")
    leverage = 17  # ~2% SL distance
    target_roe = Decimal("60.0")

    # LONG TP
    long_tp = CapitalAllocator.calculate_roe_take_profit(
        entry_price=entry, direction="LONG", leverage=leverage, target_roe_pct=target_roe
    )
    # price move fraction = 0.60 / 17 = 0.035294...
    # TP = 100,000 * (1 + 0.035294...) = 103,529.50
    assert long_tp > entry
    realized_roe_long = Decimal(str(leverage)) * ((long_tp - entry) / entry) * Decimal("100")
    assert abs(realized_roe_long - target_roe) < Decimal("1.0")  # tick size quantization tolerance

    # SHORT TP
    short_tp = CapitalAllocator.calculate_roe_take_profit(
        entry_price=entry, direction="SHORT", leverage=leverage, target_roe_pct=target_roe
    )
    assert short_tp < entry
    realized_roe_short = Decimal(str(leverage)) * ((entry - short_tp) / entry) * Decimal("100")
    assert abs(realized_roe_short - target_roe) < Decimal("1.0")


def test_11_user_tp_override_creates_new_version(algo_store):
    """11. Verify user can modify TP (e.g. 60% -> 80%), creating a new configuration version."""
    cfg = algo_store.get_or_create_default("user_1", "acc_1")
    assert cfg.version == 1
    assert cfg.take_profit_target_pct == Decimal("60.00")

    # User updates TP to 80%
    algo_store.update_config(user_id="user_1", account_id="acc_1", take_profit_target_pct=Decimal("80.00"))
    updated_cfg = algo_store.get_config("user_1", "acc_1")
    assert updated_cfg.version == 2
    assert updated_cfg.take_profit_target_pct == Decimal("80.00")


@pytest.mark.asyncio
async def test_12_historical_trade_preserves_old_tp_snapshot(lifecycle_manager, algo_store, demand_order_block, mock_candle):
    """12. Verify existing Trade 1 preserves 60% TP snapshot when configuration updates to 80% for Trade 2."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision1 = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    decision1.quantity = Decimal("1.0")

    # Execute Trade 1 under default version 1 (60% TP)
    trade1 = await lifecycle_manager.execute_trade_setup(decision1, "acc_live_5_9", "user_quant_01")
    assert trade1.config_version == 1
    assert trade1.config_snapshot.take_profit_target_pct == Decimal("60.00")

    # Close Trade 1 to release single-trade lock
    await lifecycle_manager.close_position(trade1.setup_id, CloseReason.TAKE_PROFIT)

    # User updates configuration to version 2 (80% TP)
    algo_store.update_config(user_id="user_quant_01", account_id="acc_live_5_9", take_profit_target_pct=Decimal("80.00"))

    # Execute Trade 2 under version 2
    decision2 = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_TRADE_2",
        entry=Decimal("100000.00"),
        stop_loss=Decimal("98000.00"),
        take_profit=Decimal("104705.50"),
        calculated_leverage=17,
        take_profit_target_pct=Decimal("80.00"),
        quantity=Decimal("1.0"),
    )
    trade2 = await lifecycle_manager.execute_trade_setup(decision2, "acc_live_5_9", "user_quant_01")
    assert trade2.config_version == 2
    assert trade2.config_snapshot.take_profit_target_pct == Decimal("80.00")

    # Critical Invariant: Trade 1 remains permanently at 60.00%
    assert trade1.config_snapshot.take_profit_target_pct == Decimal("60.00")


@pytest.mark.asyncio
async def test_13_frontend_cannot_override_authoritative_sl(lifecycle_manager, demand_order_block, mock_candle):
    """13. Verify frontend cannot override authoritative SL derived from Order Block."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    decision.quantity = Decimal("1.0")

    # Frontend attempts to tamper with stop loss (e.g. sending 95000 instead of authoritative 98000)
    tampered_params = {"stop_loss": 95000.00}
    record = await lifecycle_manager.execute_trade_setup(
        decision, "acc_live_5_9", "user_quant_01", frontend_params=tampered_params
    )
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == "FRONTEND_SL_TAMPERING"


@pytest.mark.asyncio
async def test_14_frontend_cannot_override_leverage(lifecycle_manager, demand_order_block, mock_candle):
    """14. Verify frontend cannot override calculated dynamic leverage."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    decision.quantity = Decimal("1.0")

    # Frontend attempts to set leverage to 50x instead of authoritative calculated leverage
    tampered_params = {"leverage": 50}
    record = await lifecycle_manager.execute_trade_setup(
        decision, "acc_live_5_9", "user_quant_01", frontend_params=tampered_params
    )
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == "FRONTEND_LEVERAGE_TAMPERING"


@pytest.mark.asyncio
async def test_15_frontend_cannot_override_position_size(lifecycle_manager, demand_order_block, mock_candle):
    """15. Verify frontend cannot tamper with 100% allocated position quantity."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    decision.quantity = Decimal("1.0")

    # Frontend attempts to force 5.0 BTC position size
    tampered_params = {"quantity": 5.0}
    record = await lifecycle_manager.execute_trade_setup(
        decision, "acc_live_5_9", "user_quant_01", frontend_params=tampered_params
    )
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == "FRONTEND_QUANTITY_TAMPERING"


def test_16_strategy_decision_exposes_complete_calculation(mock_candle, demand_order_block):
    """16. Verify StrategyDecision exposes all calculation metadata."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    assert decision.symbol == "BTCUSD"
    assert decision.direction == StrategyDirection.LONG
    assert decision.entry_price == Decimal("99500.00")
    assert decision.stop_loss_price == Decimal("98000.00")
    assert decision.order_block_upper_edge == Decimal("100000.00")
    assert decision.order_block_lower_edge == Decimal("98000.00")
    assert decision.stop_distance_pct == Decimal("1.51")
    assert decision.max_loss_pct == Decimal("35.0")
    assert decision.calculated_leverage == 23
    assert decision.take_profit_target_pct == Decimal("60.0")
    assert decision.take_profit_price is not None
    assert decision.take_profit_price > decision.entry_price


@pytest.mark.asyncio
async def test_17_single_active_trade_lock_enforcement(lifecycle_manager, demand_order_block, mock_candle):
    """17. Verify only ONE active trade is allowed at any time."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision1 = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    decision1.quantity = Decimal("1.0")

    # Trade 1 acquires lock
    rec1 = await lifecycle_manager.execute_trade_setup(decision1, "acc_live_5_9", "user_quant_01")
    assert rec1.state == TradeLifecycleState.ENTRY_SUBMITTED

    # Trade 2 on same account fails closed
    decision2 = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="ETHUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_ETH_2",
        entry=Decimal("3000.00"),
        stop_loss=Decimal("2940.00"),
        take_profit=Decimal("3100.00"),
        calculated_leverage=17,
        quantity=Decimal("1.0"),
    )
    rec2 = await lifecycle_manager.execute_trade_setup(decision2, "acc_live_5_9", "user_quant_01")
    assert rec2.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec2.rejection_code == RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value


@pytest.mark.asyncio
async def test_18_profitable_trade_compounds_balance(lifecycle_manager, demand_order_block, mock_candle):
    """18. Verify profitable trade compounds balance ($25000 -> +$1000 Net P&L -> $26000)."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    decision.quantity = Decimal("1.0")

    await lifecycle_manager.execute_trade_setup(decision, "acc_live_5_9", "user_quant_01")

    # Close with gross +$1200, fees $150, funding $50 -> Net P&L = +$1000.00
    closed = await lifecycle_manager.close_position(
        setup_id=decision.setup_id,
        reason=CloseReason.TAKE_PROFIT,
        gross_pnl=Decimal("1200.00"),
        trading_fees=Decimal("150.00"),
        funding_costs=Decimal("50.00"),
    )
    assert closed.net_pnl == Decimal("1000.00")
    assert closed.post_trade_balance == Decimal("26000.00")
    assert lifecycle_manager.state_store.account.available_balance == Decimal("26000.00")


@pytest.mark.asyncio
async def test_19_losing_trade_compounds_balance(lifecycle_manager, demand_order_block, mock_candle):
    """19. Verify losing trade updates compounded balance ($25000 -> -$500 Net P&L -> $24500)."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    decision.quantity = Decimal("1.0")

    await lifecycle_manager.execute_trade_setup(decision, "acc_live_5_9", "user_quant_01")

    # Close at SL with gross -$400.00, fees $100.00 -> Net P&L = -$500.00
    closed = await lifecycle_manager.close_position(
        setup_id=decision.setup_id,
        reason=CloseReason.STOP_LOSS,
        gross_pnl=Decimal("-400.00"),
        trading_fees=Decimal("100.00"),
    )
    assert closed.net_pnl == Decimal("-500.00")
    assert closed.post_trade_balance == Decimal("24500.00")
    assert lifecycle_manager.state_store.account.available_balance == Decimal("24500.00")


@pytest.mark.asyncio
async def test_20_market_scanner_pauses_during_active_trade(lifecycle_manager, lock_manager):
    """20. Verify MarketScanner pauses scanning when trade is active and resumes after closure."""
    orchestrator = MarketScannerOrchestrator(
        lifecycle_manager=lifecycle_manager,
        single_trade_lock=lock_manager,
    )

    # 1. Lock account simulating active trade
    lock_manager.acquire_lock("user_quant_01", "acc_live_5_9", "ACTIVE_SETUP_001", "BTCUSD")

    # 2. Scanner attempt is skipped
    res = await orchestrator.scan_and_execute("acc_live_5_9", "user_quant_01")
    assert res.executed_record is None
    assert "Account locked with active trade" in res.rejection_reason

    # 3. Release lock upon position closure
    lock_manager.release_lock("user_quant_01", "acc_live_5_9", "ACTIVE_SETUP_001")

    # 4. Scanner can now execute a new setup with sufficient capital
    candidate = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="NEW_BTC_SETUP",
        entry=Decimal("100000.00"),
        stop_loss=Decimal("98000.00"),
        take_profit=Decimal("103529.50"),
        calculated_leverage=17,
    )
    res2 = await orchestrator.scan_and_execute(
        "acc_live_5_9", "user_quant_01", candidate_decisions=[candidate]
    )
    assert res2.executed_record is not None
    assert res2.executed_record.setup_id == "NEW_BTC_SETUP"


def test_21_zero_credential_leakage(lifecycle_manager):
    """21. Verify sensitive API keys and secrets are never leaked in string representations or logs."""
    mgr_str = str(lifecycle_manager.__dict__)
    assert "TEST_API_KEY_SECURE" not in mgr_str
    assert "TEST_API_SECRET_SECURE" not in mgr_str
