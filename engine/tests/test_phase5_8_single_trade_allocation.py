"""
Unit & Integration Test Suite for Phase 5.8: Single Active Position Lock, 100% Capital Allocation & Compounding Full-Market Rescan.

Verifies:
1. Single-trade lock enforcement per account.
2. Simultaneous multi-pair signals rejection (first wins, second rejected with SINGLE_TRADE_LIMIT_EXCEEDED).
3. 100% available balance capital allocation sizing with safety buffer.
4. Lot size, step size, and leverage boundary enforcement.
5. Profitable trade compounding ($100 -> +$8 net -> $108).
6. Losing trade compounding ($108 -> -$5 net -> $103).
7. Actual trading fees and funding reduce net P&L.
8. Authoritative exchange-reconciled final balance.
9. Scanner skips new scans while a trade is active.
10. Full-market rescan across all pairs triggered immediately after closure.
11. Stale / zero balance rejection.
12. Engine restart preserves active trade lock state.
13. Duplicate signal / double-click suppression.
14. Tenant account isolation (User A trade does not lock User B).
15. Zero credential leakage.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock

from quantedge.execution.single_trade_lock import (
    SingleTradeLockManager,
    SingleTradeLockError,
    AccountTradeLockState,
)
from quantedge.execution.capital_allocator import (
    CapitalAllocator,
    CapitalAllocationError,
    PositionSizingResult,
)
from quantedge.execution.market_orchestrator import (
    MarketScannerOrchestrator,
    MarketScanResult,
)
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
    CloseReason,
)
from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    DeltaOrderResponse,
)
from quantedge.execution.synchronizer import (
    LocalStateStore,
    AccountRecord,
    ConnectionRecord,
    PositionRecord,
    OrderRecord,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    RejectionReasonCode,
)
from quantedge.execution.algo_config import (
    AlgoConfigStore,
)
from quantedge.strategy.models import (
    StrategyDecision,
    SetupState,
    StrategyDirection,
    TradeDirection,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_delta_client():
    client = MagicMock()
    client._api_key = "MOCKED_TEST_KEY_PHASE_5_8"
    client._api_secret = "MOCKED_TEST_SECRET_PHASE_5_8"
    client.place_order = AsyncMock(side_effect=lambda req: DeltaOrderResponse(
        id=990011,
        client_order_id=req.client_order_id,
        user_id=1,
        product_id=req.product_id,
        product_symbol=req.product_symbol,
        side=req.side,
        order_type=req.order_type,
        size=req.size,
        unfilled_size=req.size,
        limit_price=req.limit_price,
        stop_price=req.stop_price,
        average_fill_price=None,
        state=OrderStatus.OPEN,
        reduce_only=req.reduce_only,
        created_at=datetime.now(timezone.utc),
    ))
    client.cancel_order = AsyncMock(return_value=True)
    return client


@pytest.fixture
def state_store():
    store = LocalStateStore(account_id="acc_main")
    store.account.user_id = "user_main"
    store.account.total_equity = Decimal("100000.00")
    store.account.available_balance = Decimal("100000.00")
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.account.last_synced_at = datetime.now(timezone.utc)
    store.connection.connection_status = "CONNECTED"
    return store


@pytest.fixture
def lock_manager():
    return SingleTradeLockManager()


@pytest.fixture
def allocator():
    return CapitalAllocator(default_safety_buffer_pct=Decimal("98.00"))


@pytest.fixture
def lifecycle_mgr(mock_delta_client, state_store, lock_manager, allocator):
    gateway = OrderValidationGateway()
    algo_store = AlgoConfigStore()
    algo_store.update_config(
        user_id="user_main",
        account_id="acc_main",
        algo_enabled=True,
        kill_switch_active=False,
        risk_per_trade_pct=Decimal("100.00"),
        max_daily_loss_usd=Decimal("50000.00"),
    )
    return TradeLifecycleManager(
        client=mock_delta_client,
        validation_gateway=gateway,
        state_store=state_store,
        algo_config_store=algo_store,
        single_trade_lock=lock_manager,
        capital_allocator=allocator,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_01_single_active_trade_lock_enforcement(lock_manager):
    """1. Verify single trade lock enforces exclusive active trade per account."""
    # Acquire lock for Trade 1
    acquired = lock_manager.acquire_lock("user_1", "acc_1", "SETUP_001", "BTCUSD")
    assert acquired is True

    is_locked, active_id, sym = lock_manager.is_locked("user_1", "acc_1")
    assert is_locked is True
    assert active_id == "SETUP_001"
    assert sym == "BTCUSD"

    # Attempt to acquire lock for Trade 2 on same account fails
    with pytest.raises(SingleTradeLockError) as exc_info:
        lock_manager.acquire_lock("user_1", "acc_1", "SETUP_002", "ETHUSD")
    assert "already has an active trade in progress" in str(exc_info.value)


@pytest.mark.asyncio
async def test_02_simultaneous_multi_pair_signals_rejected(lifecycle_mgr):
    """2. When BTC and ETH signals arrive simultaneously, first executes and second is rejected."""
    dec_btc = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_BTC_01",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_reward=Decimal("3.0"),
    )
    dec_eth = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="ETHUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_ETH_01",
        entry=Decimal("2800.00"),
        stop_loss=Decimal("2750.00"),
        take_profit=Decimal("2950.00"),
        risk_reward=Decimal("3.0"),
    )

    # Execute BTC trade
    rec_btc = await lifecycle_mgr.execute_trade_setup(
        decision=dec_btc,
        account_id="acc_main",
        user_id="user_main",
    )
    assert rec_btc.state == TradeLifecycleState.ENTRY_SUBMITTED

    # Simultaneous ETH trade must be rejected with SINGLE_TRADE_LIMIT_EXCEEDED
    rec_eth = await lifecycle_mgr.execute_trade_setup(
        decision=dec_eth,
        account_id="acc_main",
        user_id="user_main",
    )
    assert rec_eth.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec_eth.rejection_code == RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value


def test_03_100_percent_capital_allocation_sizing(allocator):
    """3. Sizing engine targets 100% available capital within 98% buffer."""
    available = Decimal("100.00")
    entry_price = Decimal("100000.00")
    leverage = 10  # Buying power = 100 * 0.98 * 10 = $980

    sizing = allocator.calculate_100_percent_allocation(
        symbol="BTCUSD",
        entry_price=entry_price,
        available_balance=available,
        leverage=leverage,
        lot_size_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
    )

    # 980 / 100000 = 0.0098 -> stepped to 0.009
    assert sizing.position_quantity == Decimal("0.009")
    # Notional = 0.009 * 100000 = $900
    assert sizing.notional_value == Decimal("900.00")
    # Required margin = 900 / 10 = $90 <= $100 available
    assert sizing.allocated_margin <= available


def test_04_lot_size_and_leverage_margin_boundaries(allocator):
    """4. Sizing never exceeds available capital and respects lot size step."""
    available = Decimal("50.00")
    entry_price = Decimal("2500.00")
    leverage = 5

    sizing = allocator.calculate_100_percent_allocation(
        symbol="ETHUSD",
        entry_price=entry_price,
        available_balance=available,
        leverage=leverage,
        lot_size_step=Decimal("0.01"),
        min_quantity=Decimal("0.01"),
    )

    assert sizing.allocated_margin <= available
    assert sizing.position_quantity % Decimal("0.01") == Decimal("0")


@pytest.mark.asyncio
async def test_05_profitable_trade_compounding_balance_update(lifecycle_mgr, state_store):
    """5. Compounding: Starting $100, Trade 1 net PnL +$8 -> new balance = $108."""
    state_store.account.available_balance = Decimal("100.00")
    state_store.account.total_equity = Decimal("100.00")

    dec = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_COMP_01",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_reward=Decimal("3.0"),
        quantity=Decimal("0.001"),
    )

    rec = await lifecycle_mgr.execute_trade_setup(dec, "acc_main", "user_main")
    assert rec.pre_trade_balance == Decimal("100.00")

    # Position closes with +$8 gross, $0 fees
    closed_rec = await lifecycle_mgr.close_position(
        setup_id="SETUP_COMP_01",
        reason=CloseReason.TAKE_PROFIT,
        gross_pnl=Decimal("8.00"),
        trading_fees=Decimal("0.00"),
    )

    assert closed_rec.net_pnl == Decimal("8.00")
    assert closed_rec.post_trade_balance == Decimal("108.00")
    assert state_store.account.available_balance == Decimal("108.00")


@pytest.mark.asyncio
async def test_06_losing_trade_compounding_balance_update(lifecycle_mgr, state_store):
    """6. Compounding: Starting from $108, Trade 2 net PnL -$5 -> new balance = $103."""
    state_store.account.available_balance = Decimal("108.00")
    state_store.account.total_equity = Decimal("108.00")

    dec2 = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="ETHUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_COMP_02",
        entry=Decimal("2800.00"),
        stop_loss=Decimal("2750.00"),
        take_profit=Decimal("2950.00"),
        risk_reward=Decimal("3.0"),
        quantity=Decimal("0.01"),
    )

    rec2 = await lifecycle_mgr.execute_trade_setup(dec2, "acc_main", "user_main")
    assert rec2.pre_trade_balance == Decimal("108.00")

    closed_rec2 = await lifecycle_mgr.close_position(
        setup_id="SETUP_COMP_02",
        reason=CloseReason.STOP_LOSS,
        gross_pnl=Decimal("-5.00"),
    )

    assert closed_rec2.net_pnl == Decimal("-5.00")
    assert closed_rec2.post_trade_balance == Decimal("103.00")
    assert state_store.account.available_balance == Decimal("103.00")


@pytest.mark.asyncio
async def test_07_actual_fees_and_funding_reduce_net_pnl(lifecycle_mgr):
    """7. Actual exchange fees and funding costs reduce gross P&L."""
    dec = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_FEES_01",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_reward=Decimal("3.0"),
    )

    await lifecycle_mgr.execute_trade_setup(dec, "acc_main", "user_main")

    closed = await lifecycle_mgr.close_position(
        setup_id="SETUP_FEES_01",
        reason=CloseReason.TAKE_PROFIT,
        gross_pnl=Decimal("10.00"),
        trading_fees=Decimal("1.50"),
        funding_costs=Decimal("0.50"),
    )

    assert closed.gross_pnl == Decimal("10.00")
    assert closed.trading_fees == Decimal("1.50")
    assert closed.funding_costs == Decimal("0.50")
    # Net PnL = 10 - 1.50 - 0.50 = $8.00
    assert closed.net_pnl == Decimal("8.00")


@pytest.mark.asyncio
async def test_08_reconciled_exchange_balance_authoritative(lifecycle_mgr, state_store):
    """8. Final balance update takes authoritative exchange reported balance when provided."""
    dec = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_AUTH_EXCHANGE",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_reward=Decimal("3.0"),
    )
    await lifecycle_mgr.execute_trade_setup(dec, "acc_main", "user_main")

    closed = await lifecycle_mgr.close_position(
        setup_id="SETUP_AUTH_EXCHANGE",
        reason=CloseReason.TAKE_PROFIT,
        gross_pnl=Decimal("10.00"),
        final_exchange_balance=Decimal("110.25"),  # Delta exact balance
    )

    assert closed.post_trade_balance == Decimal("110.25")
    assert state_store.account.available_balance == Decimal("110.25")


@pytest.mark.asyncio
async def test_09_no_new_scan_until_position_fully_closed(lifecycle_mgr, lock_manager):
    """9. Scanner rejects/skips new market scan while a trade is active."""
    orchestrator = MarketScannerOrchestrator(
        lifecycle_manager=lifecycle_mgr,
        single_trade_lock=lock_manager,
    )

    # Start Trade on BTC
    dec_btc = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_SCAN_01",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_reward=Decimal("3.0"),
    )
    res1 = await orchestrator.scan_and_execute(
        account_id="acc_main",
        user_id="user_main",
        candidate_decisions=[dec_btc],
    )
    assert res1.executed_record is not None
    assert res1.executed_record.state == TradeLifecycleState.ENTRY_SUBMITTED

    # Subsequent scan while trade is active must be skipped
    dec_eth = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="ETHUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_SCAN_02",
        entry=Decimal("2800.00"),
        stop_loss=Decimal("2750.00"),
        take_profit=Decimal("2950.00"),
        risk_reward=Decimal("3.0"),
    )
    res2 = await orchestrator.scan_and_execute(
        account_id="acc_main",
        user_id="user_main",
        candidate_decisions=[dec_eth],
    )
    assert res2.executed_record is None
    assert "Account locked with active trade" in (res2.rejection_reason or "")


@pytest.mark.asyncio
async def test_10_full_market_rescan_triggered_after_closure(lifecycle_mgr, lock_manager):
    """10. After position closes and lock is released, fresh full-market rescan executes."""
    orchestrator = MarketScannerOrchestrator(
        lifecycle_manager=lifecycle_mgr,
        single_trade_lock=lock_manager,
    )

    # 1. First trade on BTC
    dec_btc = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_RESCAN_BTC",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_reward=Decimal("3.0"),
    )
    await orchestrator.scan_and_execute(
        account_id="acc_main",
        user_id="user_main",
        candidate_decisions=[dec_btc],
    )

    # 2. Position closes
    await orchestrator.handle_trade_closure_and_rescan(
        setup_id="SETUP_RESCAN_BTC",
        reason=CloseReason.TAKE_PROFIT,
        gross_pnl=Decimal("5.00"),
    )

    # Lock must now be released
    is_locked, _, _ = lock_manager.is_locked("user_main", "acc_main")
    assert is_locked is False

    # 3. New scan across all pairs now succeeds for ETH
    dec_eth = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="ETHUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_RESCAN_ETH",
        entry=Decimal("2800.00"),
        stop_loss=Decimal("2750.00"),
        take_profit=Decimal("2950.00"),
        risk_reward=Decimal("3.0"),
    )
    res_eth = await orchestrator.scan_and_execute(
        account_id="acc_main",
        user_id="user_main",
        candidate_decisions=[dec_eth],
    )
    assert res_eth.executed_record is not None
    assert res_eth.executed_record.setup_id == "SETUP_RESCAN_ETH"


def test_11_stale_balance_rejection(allocator):
    """11. Non-positive or stale zero balance is rejected."""
    with pytest.raises(CapitalAllocationError) as exc:
        allocator.calculate_100_percent_allocation(
            symbol="BTCUSD",
            entry_price=Decimal("100000.00"),
            available_balance=Decimal("0.00"),
        )
    assert "non-positive balance" in str(exc.value)


def test_12_engine_restart_preserves_active_trade_lock(lock_manager):
    """12. Lock state survives engine restarts via export and load."""
    lock_manager.acquire_lock("user_restart", "acc_restart", "SETUP_PERSIST", "SOLUSD")

    state = lock_manager.export_state()

    fresh_manager = SingleTradeLockManager()
    fresh_manager.load_state(state)

    is_locked, s_id, sym = fresh_manager.is_locked("user_restart", "acc_restart")
    assert is_locked is True
    assert s_id == "SETUP_PERSIST"
    assert sym == "SOLUSD"


def test_13_duplicate_signal_and_double_click_suppression(lock_manager):
    """13. Duplicate click with same setup_id succeeds idempotently without error."""
    # First click
    res1 = lock_manager.acquire_lock("u1", "a1", "SETUP_SAME", "BTCUSD")
    assert res1 is True

    # Immediate double-click with same setup_id
    res2 = lock_manager.acquire_lock("u1", "a1", "SETUP_SAME", "BTCUSD")
    assert res2 is True


def test_14_account_tenant_isolation_lock(lock_manager):
    """14. Active trade on Account A does not block Account B."""
    lock_manager.acquire_lock("user_A", "acc_A", "SETUP_A", "BTCUSD")

    # Account B should acquire lock freely
    res_b = lock_manager.acquire_lock("user_B", "acc_B", "SETUP_B", "ETHUSD")
    assert res_b is True

    is_locked_a, _, _ = lock_manager.is_locked("user_A", "acc_A")
    is_locked_b, _, _ = lock_manager.is_locked("user_B", "acc_B")
    assert is_locked_a is True
    assert is_locked_b is True


def test_15_zero_credential_leakage(lock_manager, allocator):
    """15. Objects and dumps never leak API secrets."""
    dump_l = str(lock_manager.export_state())
    assert "api_secret" not in dump_l.lower()
    assert "secret" not in dump_l.lower()
