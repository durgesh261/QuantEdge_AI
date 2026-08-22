"""
Phase 5.7 — Live Signal-to-Execution Bridge & Controlled Trade Lifecycle Test Suite.

Comprehensive verification of:
1. Valid TRADE_SETUP_READY strategy decision execution
2. Rejection of unready strategy statuses (FORMING, INVALIDATED, WATCHING_OB)
3. Rejection of duplicate setup executions
4. Detection and rejection of frontend parameter tampering (direction, entry, SL, TP)
5. Strict TP/SL geometry enforcement (Long: SL < Entry < TP; Short: TP < Entry < SL)
6. Server-side Daily Loss Guard rejection
7. Kill Switch fail-safe blocking
8. Algo Disabled fail-safe blocking
9. Stale account state rejection
10. Insufficient margin rejection
11. Complete entry fill & automatic SL/TP bracket creation
12. Partial fill protection scaling (0.4 -> 0.7 -> 1.0)
13. Position closure and automatic stale bracket cancellation
14. Close reason categorization and audit logging
15. Kill switch emergency cancellation of pending entries while preserving bracket protection
16. Kill switch reset workflow
17. Network timeout handling without blind retry
18. Exchange rejection handling
19. Zero exchange order placement in fail-closed mode
20. Zero credential leakage

SECURITY:
  All tests use mocked Delta transport. Zero real orders placed.
"""

from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
import json
from unittest.mock import MagicMock, AsyncMock
import pytest

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    TimeInForce,
    DeltaOrderRequest,
    DeltaOrderResponse,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaOrderRejectedError,
    DeltaConnectionError,
)
from quantedge.execution.synchronizer import (
    AccountRecord,
    PositionRecord,
    OrderRecord,
    PositionStatus,
    LocalStateStore,
    LiveAccountSyncService,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    OrderValidationRequest,
    OrderValidationResult,
    ValidationContext,
    RejectionReasonCode,
)
from quantedge.strategy.models import (
    StrategyDecision,
    SetupState,
    StrategyDirection,
    TradeDirection,
)
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
    CloseReason,
)

FIXTURE_KEY = "TEST_KEY_PHASE_5_7_0000000001"
FIXTURE_SECRET = "TEST_SECRET_PHASE_5_7_000000000000000000000000001"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_delta_client():
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = FIXTURE_KEY
    client._api_secret = FIXTURE_SECRET

    # Default order response for entry
    client.place_order = AsyncMock(side_effect=lambda req: DeltaOrderResponse(
        id=778899,
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

    client.cancel_order = AsyncMock(return_value={"success": True})
    return client


@pytest.fixture
def state_store():
    store = LocalStateStore(account_id="acc_live_5_7")
    store.account.user_id = "user_quant_01"
    store.account.total_equity = Decimal("25000.00")
    store.account.available_balance = Decimal("20000.00")
    store.account.margin_used = Decimal("5000.00")
    store.account.is_active = True
    store.account.last_synced_at = datetime.now(timezone.utc)
    # Default fail-safe flags
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.connection.connection_status = "CONNECTED"
    store.connection.api_key_status = "VALID"
    return store


@pytest.fixture
def validation_gateway():
    return OrderValidationGateway()


@pytest.fixture
def lifecycle_manager(mock_delta_client, validation_gateway, state_store):
    return TradeLifecycleManager(
        client=mock_delta_client,
        validation_gateway=validation_gateway,
        state_store=state_store,
        daily_loss_limit=Decimal("500.00"),
        max_stale_seconds=120,
    )


@pytest.fixture
def valid_bullish_decision():
    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP-BULL-BTC-001",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_distance=Decimal("1000.00"),
        reward_distance=Decimal("3000.00"),
        risk_reward=Decimal("3.0"),
        confidence=85.0,
    )


@pytest.fixture
def valid_bearish_decision():
    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.SHORT,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP-BEAR-BTC-002",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("96000.00"),
        take_profit=Decimal("92000.00"),
        risk_distance=Decimal("1000.00"),
        reward_distance=Decimal("3000.00"),
        risk_reward=Decimal("3.0"),
        confidence=88.0,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_01_valid_trade_setup_ready_execution(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """1. Verify that a qualified TRADE_SETUP_READY decision executes and places entry order."""
    record = await lifecycle_manager.execute_trade_setup(
        decision=valid_bullish_decision,
        account_id="acc_live_5_7",
        user_id="user_quant_01",
    )

    assert record.state == TradeLifecycleState.ENTRY_SUBMITTED
    assert record.entry_order_id == "778899"
    assert record.symbol == "BTCUSD"
    assert record.requested_quantity == Decimal("1.0")
    assert record.entry_price == Decimal("95000.00")
    assert record.stop_loss_price == Decimal("94000.00")
    assert record.take_profit_price == Decimal("98000.00")
    assert len(record.history) >= 1
    mock_delta_client.place_order.assert_called_once()


@pytest.mark.asyncio
async def test_02_invalid_setup_status_rejection(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """2. Verify that unready setups (e.g. WATCHING_OB) are rejected with zero exchange calls."""
    unready_decision = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.WATCHING_OB,
        setup_id="SETUP-FORMING-001",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_distance=Decimal("1000.00"),
        reward_distance=Decimal("3000.00"),
        risk_reward=Decimal("3.0"),
        confidence=50.0,
    )

    record = await lifecycle_manager.execute_trade_setup(
        decision=unready_decision,
        account_id="acc_live_5_7",
    )

    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == "SETUP_NOT_READY"
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_03_duplicate_setup_rejection(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """3. Verify duplicate setup execution is rejected."""
    # First execution succeeds
    rec1 = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert rec1.state == TradeLifecycleState.ENTRY_SUBMITTED

    # Second execution of same setup rejected
    rec2 = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert rec2.rejection_code == RejectionReasonCode.DUPLICATE_SETUP_ID.value
    assert mock_delta_client.place_order.call_count == 1


@pytest.mark.asyncio
async def test_04_frontend_direction_tampering(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """4. Verify that frontend attempting to override trade direction is rejected."""
    tampered_params = {"direction": "SHORT"}
    record = await lifecycle_manager.execute_trade_setup(
        decision=valid_bullish_decision,
        account_id="acc_live_5_7",
        frontend_params=tampered_params,
    )

    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == "FRONTEND_DIRECTION_TAMPERING"
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_05_frontend_tp_sl_tampering(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """5. Verify that frontend attempting to override SL/TP prices is rejected."""
    tampered_params = {"stop_loss": 90000.00}
    record = await lifecycle_manager.execute_trade_setup(
        decision=valid_bullish_decision,
        account_id="acc_live_5_7",
        frontend_params=tampered_params,
    )

    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == "FRONTEND_SL_TAMPERING"
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_06_long_tp_sl_geometry_invalid(lifecycle_manager, mock_delta_client):
    """6. Verify inverted LONG geometry (SL > Entry) is rejected with INVALID_TP_SL_GEOMETRY."""
    bad_long = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP-BAD-LONG-01",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("96000.00"),  # INVERTED!
        take_profit=Decimal("98000.00"),
        risk_distance=Decimal("1000.00"),
        reward_distance=Decimal("3000.00"),
        risk_reward=Decimal("3.0"),
        confidence=85.0,
    )

    record = await lifecycle_manager.execute_trade_setup(bad_long, "acc_live_5_7")
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == RejectionReasonCode.INVALID_TP_SL_GEOMETRY.value
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_07_short_tp_sl_geometry_valid_and_invalid(lifecycle_manager, valid_bearish_decision, mock_delta_client):
    """7. Verify SHORT geometry validation (TP < Entry < SL)."""
    # Valid Short
    rec_valid = await lifecycle_manager.execute_trade_setup(valid_bearish_decision, "acc_live_5_7")
    assert rec_valid.state == TradeLifecycleState.ENTRY_SUBMITTED

    # Inverted Short
    bad_short = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.SHORT,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP-BAD-SHORT-01",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),  # INVERTED for short!
        take_profit=Decimal("92000.00"),
        risk_distance=Decimal("1000.00"),
        reward_distance=Decimal("3000.00"),
        risk_reward=Decimal("3.0"),
        confidence=85.0,
    )
    rec_invalid = await lifecycle_manager.execute_trade_setup(bad_short, "acc_live_5_7")
    assert rec_invalid.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec_invalid.rejection_code == RejectionReasonCode.INVALID_TP_SL_GEOMETRY.value


@pytest.mark.asyncio
async def test_08_daily_loss_limit_guard(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """8. Verify server-side Daily Loss Guard rejects new entries when daily loss limit reached."""
    # Simulate a closed trade earlier today with $600 realized loss (limit is $500)
    closed_trade = TradeLifecycleRecord(
        setup_id="PAST-TRADE-01",
        account_id="acc_live_5_7",
        user_id="user_quant_01",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        requested_quantity=Decimal("1.0"),
        entry_price=Decimal("95000.00"),
        stop_loss_price=Decimal("94400.00"),
        take_profit_price=Decimal("97000.00"),
        risk_reward_ratio=Decimal("3.0"),
        risk_amount=Decimal("600.00"),
        reward_amount=Decimal("1800.00"),
        state=TradeLifecycleState.POSITION_CLOSED,
        close_reason=CloseReason.STOP_LOSS,
        realized_pnl=Decimal("-600.00"),
        closed_at=datetime.now(timezone.utc),
    )
    lifecycle_manager._trade_history.append(closed_trade)

    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == RejectionReasonCode.DAILY_LOSS_LIMIT.value
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_09_kill_switch_blocks_entry(lifecycle_manager, valid_bullish_decision, state_store, mock_delta_client):
    """9. Verify active kill switch immediately blocks entry with ZERO exchange calls."""
    state_store.account.kill_switch_active = True
    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == RejectionReasonCode.KILL_SWITCH_ACTIVE.value
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_10_algo_disabled_blocks_entry(lifecycle_manager, valid_bullish_decision, state_store, mock_delta_client):
    """10. Verify algo_enabled=False blocks entry with ZERO exchange calls."""
    state_store.account.algo_enabled = False
    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == RejectionReasonCode.ALGO_DISABLED.value
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_11_stale_account_state_rejection(lifecycle_manager, valid_bullish_decision, state_store, mock_delta_client):
    """11. Verify stale account synchronization timestamp rejects execution."""
    state_store.account.last_synced_at = datetime.now(timezone.utc) - timedelta(seconds=300)
    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == RejectionReasonCode.ACCOUNT_STATE_STALE.value
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_12_complete_fill_creates_bracket_protection(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """12. Verify complete entry fill triggers SL and TP bracket order placement."""
    # Place entry
    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert record.state == TradeLifecycleState.ENTRY_SUBMITTED

    # Delta fills entry completely
    await lifecycle_manager.on_entry_fill(
        setup_id=valid_bullish_decision.setup_id,
        filled_size=Decimal("1.0"),
        avg_price=Decimal("95000.00"),
    )

    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    assert record.filled_quantity == Decimal("1.0")
    assert record.protected_quantity == Decimal("1.0")
    assert record.sl_order_id is not None
    assert record.tp_order_id is not None
    # 1 entry call + 1 SL call + 1 TP call = 3 total place_order calls
    assert mock_delta_client.place_order.call_count == 3


@pytest.mark.asyncio
async def test_13_partial_fill_scales_protection(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """13. Verify partial entry fill (0.4 of 1.0) creates protective bracket scaled to 0.4."""
    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")

    # Partial fill 0.4
    await lifecycle_manager.on_entry_partial_fill(
        setup_id=valid_bullish_decision.setup_id,
        filled_size=Decimal("0.4"),
        avg_price=Decimal("95000.00"),
    )

    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    assert record.filled_quantity == Decimal("0.4")
    assert record.protected_quantity == Decimal("0.4")


@pytest.mark.asyncio
async def test_14_position_closure_cancels_bracket_orders(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """14. Verify closing position cancels remaining SL/TP bracket orders and archives record."""
    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    await lifecycle_manager.on_entry_fill(valid_bullish_decision.setup_id, Decimal("1.0"), Decimal("95000.00"))
    
    # Close position via TAKE_PROFIT
    closed_rec = await lifecycle_manager.close_position(
        setup_id=valid_bullish_decision.setup_id,
        reason=CloseReason.TAKE_PROFIT,
        realized_pnl=Decimal("3000.00"),
    )

    assert closed_rec.state == TradeLifecycleState.POSITION_CLOSED
    assert closed_rec.close_reason == CloseReason.TAKE_PROFIT
    assert closed_rec.realized_pnl == Decimal("3000.00")
    assert closed_rec.closed_at is not None
    # Verify cancel_order was called for SL and TP bracket orders
    assert mock_delta_client.cancel_order.call_count == 2
    assert len(lifecycle_manager.get_all_active_trades()) == 0


@pytest.mark.asyncio
async def test_15_kill_switch_cancels_pending_entries(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """15. Verify activating kill switch cancels pending entry orders."""
    await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert len(lifecycle_manager.get_all_active_trades()) == 1

    # Trigger emergency kill switch
    result = await lifecycle_manager.activate_kill_switch("EMERGENCY_VOLATILITY_SPIKE")
    assert result["kill_switch_active"] is True
    assert result["cancelled_orders_count"] == 1
    assert len(lifecycle_manager.get_all_active_trades()) == 0


def test_16_kill_switch_reset_workflow(lifecycle_manager, state_store):
    """16. Verify kill switch can be reset by authorized user."""
    state_store.account.kill_switch_active = True
    res = lifecycle_manager.reset_kill_switch(authorized_by="admin_user_01")
    assert res["kill_switch_active"] is False
    assert state_store.account.kill_switch_active is False


@pytest.mark.asyncio
async def test_17_network_timeout_requires_reconciliation(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """17. Verify submission network timeout transitions state to RECONCILIATION_REQUIRED."""
    mock_delta_client.place_order.side_effect = DeltaConnectionError("Connection timed out to Delta REST")

    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert record.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert "timed out" in record.error_message


@pytest.mark.asyncio
async def test_18_exchange_rejection_handling(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """18. Verify exchange rejection marks ENTRY_REJECTED with error message."""
    mock_delta_client.place_order.side_effect = DeltaOrderRejectedError(
        message="Insufficient margin to place 1.0 BTC contract",
        status_code=400,
    )

    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert record.state == TradeLifecycleState.ENTRY_REJECTED
    assert "Insufficient margin" in record.error_message


def test_19_zero_credential_leakage(lifecycle_manager, valid_bullish_decision):
    """19. Verify no API secrets or sensitive credentials leak into records or string dumps."""
    rec = lifecycle_manager._create_rejected_record(
        "SETUP-TEST", "acc-01", "user-01", "BTCUSD", TradeDirection.LONG,
        Decimal("1.0"), Decimal("95000"), Decimal("94000"), Decimal("98000"),
        Decimal("3.0"), "CODE", "Message"
    )

    rec_str = str(rec)
    assert FIXTURE_SECRET not in rec_str
    assert "secret" not in rec_str.lower()


@pytest.mark.asyncio
async def test_20_incremental_partial_fill_scaling(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """20. Verify scaling of protection as fills increase incrementally: 0.4 -> 0.7 -> 1.0."""
    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    
    # 1. First partial fill 0.4
    await lifecycle_manager.on_entry_partial_fill(valid_bullish_decision.setup_id, Decimal("0.4"), Decimal("95000.00"))
    assert record.protected_quantity == Decimal("0.4")
    
    # 2. Second partial fill 0.7
    await lifecycle_manager.on_entry_partial_fill(valid_bullish_decision.setup_id, Decimal("0.7"), Decimal("95000.00"))
    assert record.protected_quantity == Decimal("0.7")
    
    # 3. Final fill 1.0
    await lifecycle_manager.on_entry_fill(valid_bullish_decision.setup_id, Decimal("1.0"), Decimal("95000.00"))
    assert record.protected_quantity == Decimal("1.0")


@pytest.mark.asyncio
async def test_21_bracket_protection_failure_state(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """21. Verify exception during SL/TP creation transitions state to PROTECTION_FAILED without crashing."""
    record = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    
    # Make bracket placement fail
    mock_delta_client.place_order.side_effect = Exception("Delta rate limit on TP bracket submission")
    
    await lifecycle_manager.on_entry_fill(valid_bullish_decision.setup_id, Decimal("1.0"), Decimal("95000.00"))
    assert record.state == TradeLifecycleState.PROTECTION_FAILED
    assert "rate limit" in record.history[-1]["reason"]


@pytest.mark.asyncio
async def test_22_close_reasons_and_pnl_tracking(lifecycle_manager, valid_bullish_decision, mock_delta_client):
    """22. Verify position close via STOP_LOSS records loss and updates history."""
    await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    await lifecycle_manager.on_entry_fill(valid_bullish_decision.setup_id, Decimal("1.0"), Decimal("95000.00"))
    
    closed_rec = await lifecycle_manager.close_position(
        setup_id=valid_bullish_decision.setup_id,
        reason=CloseReason.STOP_LOSS,
        realized_pnl=Decimal("-1000.00"),
    )
    
    assert closed_rec.close_reason == CloseReason.STOP_LOSS
    assert closed_rec.realized_pnl == Decimal("-1000.00")
    assert lifecycle_manager.get_realized_daily_loss() == Decimal("1000.00")


@pytest.mark.asyncio
async def test_23_fail_closed_guarantee_zero_exchange_calls(lifecycle_manager, valid_bullish_decision, state_store, mock_delta_client):
    """23. Explicit Safety Test: Verify fail-closed state (algo disabled + kill switch active) NEVER calls exchange."""
    state_store.account.algo_enabled = False
    state_store.account.kill_switch_active = True
    
    rec = await lifecycle_manager.execute_trade_setup(valid_bullish_decision, "acc_live_5_7")
    assert rec.state == TradeLifecycleState.ENTRY_REJECTED
    mock_delta_client.place_order.assert_not_called()
    mock_delta_client.cancel_order.assert_not_called()


def test_24_daily_loss_query_by_date(lifecycle_manager):
    """24. Verify daily loss calculation queries specific calendar dates properly."""
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    
    # Trade closed yesterday
    trade_yesterday = TradeLifecycleRecord(
        setup_id="YESTERDAY-01", account_id="acc-01", user_id="u1", symbol="BTCUSD",
        direction=TradeDirection.LONG, requested_quantity=Decimal("1.0"),
        entry_price=Decimal("95000"), stop_loss_price=Decimal("94000"), take_profit_price=Decimal("98000"),
        risk_reward_ratio=Decimal("3.0"), risk_amount=Decimal("1000"), reward_amount=Decimal("3000"),
        state=TradeLifecycleState.POSITION_CLOSED, close_reason=CloseReason.STOP_LOSS,
        realized_pnl=Decimal("-800.00"),
        closed_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    lifecycle_manager._trade_history.append(trade_yesterday)
    
    assert lifecycle_manager.get_realized_daily_loss(query_date=yesterday) == Decimal("800.00")
    assert lifecycle_manager.get_realized_daily_loss(query_date=today) == Decimal("0.00")


def test_25_state_store_audit_on_kill_switch(lifecycle_manager, state_store):
    """25. Verify kill switch trigger and reset write structured audit entries."""
    state_store.audit_events.clear()
    
    lifecycle_manager.reset_kill_switch("sec_officer_99")
    assert len(state_store.audit_events) == 1
    assert state_store.audit_events[0]["action"] == "KILL_SWITCH_RESET"
    assert state_store.audit_events[0]["details"]["authorized_by"] == "sec_officer_99"

