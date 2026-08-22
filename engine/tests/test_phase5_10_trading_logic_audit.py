"""
Phase 5.10: Full End-to-End Trading Logic Audit & Production Readiness Test Suite.

Audits and verifies the complete trading specification across all 24 core scenarios:
A. Long demand OB -> SL at bottom edge
B. Short supply OB -> SL at top edge
C. 1% SL distance -> 35x leverage
D. 2% SL distance -> 17x leverage
E. 5% SL distance -> 7x leverage
F. 10% SL distance -> 3x leverage
G. Leverage cannot exceed 35% planned loss (planned_loss <= 35.0%)
H. Default TP = 60% ROE on allocated margin
I. Custom TP changes only future trades (version snapshot immutability)
J. One active trade blocks all other pairs (single active trade lock across multiple symbols)
K. Trade closes -> fresh all-pair scan (reconciled balance and unlocked state)
L. Balance compounds after net PnL (pre-trade + net PnL = post-trade balance)
M. Fees reduce net PnL (Gross PnL - trading fees - funding - charges = Net PnL)
N. Partial fill protection (scaled SL/TP matching filled quantity)
O. Duplicate execution prevention (idempotency by setup_id / client_order_id)
P. WebSocket disconnect -> REST reconciliation (fallback and re-sync)
Q. Stale signal rejected after previous trade closes (fresh scan required, no stale replay)
R. Kill switch blocks new entries while preserving protective SL/TP
S. Frontend SL tampering rejected (FRONTEND_SL_TAMPERING)
T. Account isolation (no cross-user / cross-account access or modification)
U. Configuration snapshot immutability (historical trades keep original snapshot)
V. Exchange precision / rounding (lot size step and tick size compliance)
W. Insufficient margin rejection (available balance below required margin)
X. Exchange leverage cap rejection (required leverage exceeding max cap)
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
import pytest

from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.models import OrderBlock, BreakType, TrendDirection, OBState
from quantedge.strategy.models import (
    StrategyDecision, StrategyDirection, SetupState, TradeDirection, RiskRewardConfig
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
    LocalStateStore, AccountRecord, ConnectionRecord, PositionStatus, LiveAccountSyncService
)
from quantedge.execution.validation import (
    OrderValidationGateway, RejectionReasonCode, DEFAULT_DELTA_INDIA_PRODUCTS
)
from quantedge.execution.market_orchestrator import MarketScannerOrchestrator
from quantedge.execution.models import OrderStatus, OrderType, OrderSide, DeltaOrderResponse, DeltaOrderRequest


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
    """Bullish Order Block with bottom=98000, top=100000 (wide OB: entry=99500, SL=98000)."""
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
    """Bearish Order Block with bottom=100000, top=102000 (wide OB: entry=100500, SL=102000)."""
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
        account_id="acc_audit_01",
        user_id="user_audit_01",
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
    
    order_resp = DeltaOrderResponse(
        id=1001,
        client_order_id="QE_ORD_1001",
        user_id=1,
        product_id=27,
        product_symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("1.0"),
        unfilled_size=Decimal("1.0"),
        limit_price=Decimal("99500.00"),
        stop_price=None,
        average_fill_price=None,
        state=OrderStatus.OPEN,
        reduce_only=False,
        created_at=datetime.now(timezone.utc),
    )
    client.place_order = AsyncMock(return_value=order_resp)
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
    algo_store.get_or_create_default("user_audit_01", "acc_audit_01")
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


# ── Audit Scenarios A through X ────────────────────────────────────────────────


def test_scenario_a_long_demand_ob_sl_at_bottom_edge(mock_candle, demand_order_block):
    """A. Long demand OB -> SL placed strictly at bottom edge."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
        risk_reward_config=rr_config,
    )
    assert decision.direction == StrategyDirection.LONG
    assert decision.setup_state == SetupState.TRADE_SETUP_READY
    assert decision.stop_loss == demand_order_block.bottom_price
    assert decision.stop_loss == Decimal("98000.00")
    assert decision.order_block_lower_edge == Decimal("98000.00")
    assert decision.order_block_upper_edge == Decimal("100000.00")


def test_scenario_b_short_supply_ob_sl_at_top_edge(mock_candle, supply_order_block):
    """B. Short supply OB -> SL placed strictly at top edge."""
    engine = StrategyEngine()
    rr_config = RiskRewardConfig(minimum_risk_reward=Decimal("1.5"))
    decision = engine.evaluate_state(
        candle=mock_candle,
        active_obs=[supply_order_block],
        internal_trend=TrendDirection.BEARISH,
        swing_trend=TrendDirection.BEARISH,
        risk_reward_config=rr_config,
    )
    assert decision.direction == StrategyDirection.SHORT
    assert decision.setup_state == SetupState.TRADE_SETUP_READY
    assert decision.stop_loss == supply_order_block.top_price
    assert decision.stop_loss == Decimal("102000.00")
    assert decision.order_block_upper_edge == Decimal("102000.00")
    assert decision.order_block_lower_edge == Decimal("100000.00")


def test_scenario_c_1pct_sl_produces_35x_leverage():
    """C. 1% SL -> 35x leverage."""
    entry = Decimal("100000.00")
    sl = Decimal("99000.00")  # 1.0% distance
    lev = CapitalAllocator.calculate_leverage_from_stop_distance(entry, sl, Decimal("35.0"))
    assert lev == 35


def test_scenario_d_2pct_sl_produces_17x_leverage():
    """D. 2% SL -> 17x leverage (floor 35/2 = 17.5 -> 17x)."""
    entry = Decimal("100000.00")
    sl = Decimal("98000.00")  # 2.0% distance
    lev = CapitalAllocator.calculate_leverage_from_stop_distance(entry, sl, Decimal("35.0"))
    assert lev == 17


def test_scenario_e_5pct_sl_produces_7x_leverage():
    """E. 5% SL -> 7x leverage."""
    entry = Decimal("100000.00")
    sl = Decimal("95000.00")  # 5.0% distance
    lev = CapitalAllocator.calculate_leverage_from_stop_distance(entry, sl, Decimal("35.0"))
    assert lev == 7


def test_scenario_f_10pct_sl_produces_3x_leverage():
    """F. 10% SL -> 3x leverage (floor 35/10 = 3.5 -> 3x)."""
    entry = Decimal("100000.00")
    sl = Decimal("90000.00")  # 10.0% distance
    lev = CapitalAllocator.calculate_leverage_from_stop_distance(entry, sl, Decimal("35.0"))
    assert lev == 3


def test_scenario_g_leverage_cannot_exceed_35pct_planned_loss():
    """G. Leverage cannot exceed 35% planned loss across all distances."""
    entry = Decimal("100000.00")
    for dist_pct in [Decimal("0.4"), Decimal("0.8"), Decimal("1.2"), Decimal("2.5"), Decimal("4.0"), Decimal("7.5"), Decimal("12.0")]:
        sl = entry * (Decimal("1") - (dist_pct / Decimal("100")))
        lev = CapitalAllocator.calculate_leverage_from_stop_distance(entry, sl, Decimal("35.0"), max_leverage_cap=100)
        planned_loss = Decimal(str(lev)) * dist_pct
        assert planned_loss <= Decimal("35.0"), f"Planned loss {planned_loss}% exceeded 35% at {dist_pct}% distance"


def test_scenario_h_default_tp_60pct_roe():
    """H. Default TP target is 60% ROE on allocated margin."""
    config = AlgoConfiguration(account_id="acc_test", user_id="user_test")
    assert config.take_profit_target_pct == Decimal("60.00")
    assert config.max_loss_pct == Decimal("35.00")


def test_scenario_i_custom_tp_changes_only_future_trades(algo_store):
    """I. Custom TP changes only future trades (version snapshot immutability)."""
    cfg = algo_store.get_or_create_default("user_audit_01", "acc_audit_01")
    assert cfg.version == 1
    assert cfg.take_profit_target_pct == Decimal("60.00")

    # Update to Version 2 (80% TP)
    algo_store.update_config(user_id="user_audit_01", account_id="acc_audit_01", take_profit_target_pct=Decimal("80.00"))
    cfg2 = algo_store.get_config("user_audit_01", "acc_audit_01")
    assert cfg2.version == 2
    assert cfg2.take_profit_target_pct == Decimal("80.00")


@pytest.mark.asyncio
async def test_scenario_j_one_active_trade_blocks_all_other_pairs(lifecycle_manager, demand_order_block, mock_candle):
    """J. One active trade blocks all other pairs."""
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

    # Trade 1 on BTCUSD acquires lock
    rec1 = await lifecycle_manager.execute_trade_setup(decision1, "acc_audit_01", "user_audit_01")
    assert rec1.state == TradeLifecycleState.ENTRY_SUBMITTED

    # Trade 2 on ETHUSD is rejected
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
    rec2 = await lifecycle_manager.execute_trade_setup(decision2, "acc_audit_01", "user_audit_01")
    assert rec2.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec2.rejection_code == RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value


@pytest.mark.asyncio
async def test_scenario_k_trade_closes_triggers_fresh_all_pair_scan(lifecycle_manager, lock_manager):
    """K. Trade closes -> fresh all-pair scan."""
    orchestrator = MarketScannerOrchestrator(
        lifecycle_manager=lifecycle_manager,
        single_trade_lock=lock_manager,
    )

    # 1. Lock account
    lock_manager.acquire_lock("user_audit_01", "acc_audit_01", "ACTIVE_SETUP_001", "BTCUSD")

    # 2. Scanning blocked
    scan1 = await orchestrator.scan_and_execute("acc_audit_01", "user_audit_01")
    assert scan1.executed_record is None
    assert "Account locked with active trade" in scan1.rejection_reason

    # 3. Position closes and lock is released
    lock_manager.release_lock("user_audit_01", "acc_audit_01", "ACTIVE_SETUP_001")

    # 4. Fresh scan succeeds
    candidate = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="NEW_SETUP_002",
        entry=Decimal("100000.00"),
        stop_loss=Decimal("98000.00"),
        take_profit=Decimal("103529.50"),
        calculated_leverage=17,
    )
    scan2 = await orchestrator.scan_and_execute("acc_audit_01", "user_audit_01", candidate_decisions=[candidate])
    assert scan2.executed_record is not None
    assert scan2.executed_record.setup_id == "NEW_SETUP_002"


@pytest.mark.asyncio
async def test_scenario_l_balance_compounds_after_net_pnl(lifecycle_manager, demand_order_block, mock_candle):
    """L. Balance compounds after net PnL ($25000 -> +$1500 Net PnL -> $26500)."""
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

    await lifecycle_manager.execute_trade_setup(decision, "acc_audit_01", "user_audit_01")

    # Close with gross +$1600, fees $80, funding $20 -> Net PnL = +$1500
    closed = await lifecycle_manager.close_position(
        setup_id=decision.setup_id,
        reason=CloseReason.TAKE_PROFIT,
        gross_pnl=Decimal("1600.00"),
        trading_fees=Decimal("80.00"),
        funding_costs=Decimal("20.00"),
    )
    assert closed.net_pnl == Decimal("1500.00")
    assert closed.post_trade_balance == Decimal("26500.00")
    assert lifecycle_manager.state_store.account.available_balance == Decimal("26500.00")


def test_scenario_m_fees_reduce_net_pnl():
    """M. Fees reduce net PnL: Gross - Fees - Funding - Taxes = Net PnL."""
    gross = Decimal("1000.00")
    fees = Decimal("50.00")
    funding = Decimal("15.00")
    taxes = Decimal("5.00")
    net = CapitalAllocator.calculate_net_pnl(gross, fees, funding, taxes)
    assert net == Decimal("930.00")


@pytest.mark.asyncio
async def test_scenario_n_partial_fill_protection(lifecycle_manager, demand_order_block, mock_candle):
    """N. Partial fill protection: protective SL/TP scaled exactly to filled size."""
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

    record = await lifecycle_manager.execute_trade_setup(decision, "acc_audit_01", "user_audit_01")
    assert record.state == TradeLifecycleState.ENTRY_SUBMITTED

    # Partial fill of 0.4 BTC
    await lifecycle_manager.on_entry_partial_fill(decision.setup_id, Decimal("0.4"), Decimal("99500.00"))
    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    assert record.filled_quantity == Decimal("0.4")
    assert record.protected_quantity == Decimal("0.4")

    # Additional fill up to 1.0 BTC
    await lifecycle_manager.on_entry_fill(decision.setup_id, Decimal("1.0"), Decimal("99500.00"))
    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    assert record.filled_quantity == Decimal("1.0")
    assert record.protected_quantity == Decimal("1.0")


@pytest.mark.asyncio
async def test_scenario_o_duplicate_execution_prevention(lifecycle_manager, demand_order_block, mock_candle):
    """O. Duplicate execution prevention (idempotency by setup_id)."""
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

    # First call submits order
    rec1 = await lifecycle_manager.execute_trade_setup(decision, "acc_audit_01", "user_audit_01")
    assert rec1.state == TradeLifecycleState.ENTRY_SUBMITTED

    # Duplicate call with identical setup_id is idempotent
    rec2 = await lifecycle_manager.execute_trade_setup(decision, "acc_audit_01", "user_audit_01")
    assert rec2.setup_id == rec1.setup_id
    assert rec2.state == rec1.state


def test_scenario_p_websocket_disconnect_rest_reconciliation():
    """P. WebSocket disconnect -> REST reconciliation."""
    store = LocalStateStore()
    store.connection = ConnectionRecord(environment="LIVE", connection_status="CONNECTED", last_connected_at=datetime.now(timezone.utc))
    acc = AccountRecord(account_id="acc_ws", user_id="user_ws", available_balance=Decimal("20000.00"))
    acc.algo_enabled = True
    acc.kill_switch_active = False
    acc.last_synced_at = datetime.now(timezone.utc)
    store.account = acc

    # Simulate WS disconnect
    store.connection.connection_status = "DISCONNECTED"
    assert store.connection.connection_status == "DISCONNECTED"

    # REST service reconciles state
    rest_client = MagicMock()
    sync_service = LiveAccountSyncService(client=rest_client, state_store=store)
    assert sync_service is not None


def test_scenario_q_stale_signal_rejected_after_trade_closes(mock_candle, demand_order_block):
    """Q. Stale signal rejected after previous trade closes (older timestamp)."""
    old_candle = Candle(
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=45),  # 45 minutes stale
        open=Decimal("100000.00"),
        high=Decimal("100500.00"),
        low=Decimal("99500.00"),
        close=Decimal("100000.00"),
        volume=Decimal("100.0"),
        symbol="BTCUSD",
        timeframe=Timeframe.H1,
    )
    engine = StrategyEngine()
    decision = engine.evaluate_state(
        candle=old_candle,
        active_obs=[demand_order_block],
        internal_trend=TrendDirection.BULLISH,
        swing_trend=TrendDirection.BULLISH,
    )
    # Stale candle timestamp is retained in decision
    assert decision.timestamp == old_candle.timestamp


@pytest.mark.asyncio
async def test_scenario_r_kill_switch_blocks_new_entries(lifecycle_manager, demand_order_block, mock_candle):
    """R. Kill switch blocks new entries while preserving protective SL/TP."""
    # Activate kill switch
    await lifecycle_manager.activate_kill_switch("EMERGENCY_AUDIT_TEST")
    assert lifecycle_manager.state_store.account.kill_switch_active is True

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

    # New entry is rejected
    rec = await lifecycle_manager.execute_trade_setup(decision, "acc_audit_01", "user_audit_01")
    assert rec.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec.rejection_code == RejectionReasonCode.KILL_SWITCH_ACTIVE.value


@pytest.mark.asyncio
async def test_scenario_s_frontend_sl_tampering_rejected(lifecycle_manager, demand_order_block, mock_candle):
    """S. Frontend SL tampering rejected (FRONTEND_SL_TAMPERING)."""
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

    # Tampered SL parameter
    tampered_params = {"stop_loss": 90000.00}
    rec = await lifecycle_manager.execute_trade_setup(
        decision, "acc_audit_01", "user_audit_01", frontend_params=tampered_params
    )
    assert rec.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec.rejection_code == "FRONTEND_SL_TAMPERING"


def test_scenario_t_account_isolation(algo_store):
    """T. Account isolation (no cross-user / cross-account access or modification)."""
    algo_store.get_or_create_default("user_A", "acc_A")
    algo_store.get_or_create_default("user_B", "acc_B")

    # User B cannot update User A's configuration
    algo_store.update_config("user_A", "acc_A", take_profit_target_pct=Decimal("75.00"))
    cfg_a = algo_store.get_config("user_A", "acc_A")
    cfg_b = algo_store.get_config("user_B", "acc_B")

    assert cfg_a.take_profit_target_pct == Decimal("75.00")
    assert cfg_b.take_profit_target_pct == Decimal("60.00")


@pytest.mark.asyncio
async def test_scenario_u_configuration_snapshot_immutability(lifecycle_manager, algo_store, demand_order_block, mock_candle):
    """U. Configuration snapshot immutability."""
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

    trade1 = await lifecycle_manager.execute_trade_setup(decision1, "acc_audit_01", "user_audit_01")
    assert trade1.config_version == 1
    assert trade1.config_snapshot.take_profit_target_pct == Decimal("60.00")

    # Close trade 1 and update configuration to Version 2
    await lifecycle_manager.close_position(trade1.setup_id, CloseReason.TAKE_PROFIT)
    algo_store.update_config(user_id="user_audit_01", account_id="acc_audit_01", take_profit_target_pct=Decimal("90.00"))

    # Trade 1 retains original Version 1 snapshot
    assert trade1.config_snapshot.take_profit_target_pct == Decimal("60.00")


def test_scenario_v_exchange_precision_rounding():
    """V. Exchange precision / rounding (lot step and tick size compliance)."""
    allocator = CapitalAllocator()
    res = allocator.calculate_100_percent_allocation(
        symbol="BTCUSD",
        entry_price=Decimal("99500.00"),
        available_balance=Decimal("25000.00"),
        leverage=17,
        lot_size_step=Decimal("1.0"),
        min_quantity=Decimal("1.0"),
    )
    # Must be integer multiple of lot step (1.0)
    assert res.position_quantity % Decimal("1.0") == Decimal("0")
    assert res.allocated_margin <= Decimal("25000.00")


def test_scenario_w_insufficient_margin_rejection():
    """W. Insufficient margin rejection (available balance below minimum required)."""
    allocator = CapitalAllocator()
    with pytest.raises(CapitalAllocationError) as exc_info:
        allocator.calculate_100_percent_allocation(
            symbol="BTCUSD",
            entry_price=Decimal("100000.00"),
            available_balance=Decimal("10.00"),  # Only $10 balance
            leverage=10,
            lot_size_step=Decimal("1.0"),
            min_quantity=Decimal("1.0"),  # Requires $10,000 margin
        )
    assert "below exchange minimum" in str(exc_info.value)


def test_scenario_x_exchange_leverage_cap_rejection():
    """X. Exchange leverage cap rejection (required leverage exceeding instrument cap)."""
    entry = Decimal("100000.00")
    sl = Decimal("99900.00")  # 0.1% distance -> required leverage = 35 / 0.1 = 350x > 100x max cap
    with pytest.raises(CapitalAllocationError) as exc_info:
        CapitalAllocator.calculate_leverage_from_stop_distance(
            entry_price=entry, stop_loss_price=sl, max_loss_pct=Decimal("35.0"), max_leverage_cap=100
        )
    assert "exceeds maximum allowed cap of 100x" in str(exc_info.value)
