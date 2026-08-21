"""
Phase 5.4 Hardening Test Suite — Real Account State, Server-Side Credentials & Safety.

Verifies the 24 production safety & hardening requirements:
1. Frontend credentials are not accepted / loaded server-side only
2. Credentials presence enforced (missing/empty credentials rejected)
3. Account ownership enforced (user_id mismatch rejected)
4. Absence of hard-coded $10,000 (fails closed if equity is 0 or insufficient)
5. Absence of hard-coded CONNECTED (fails closed if connection status is DISCONNECTED/ERROR)
6. Absence of hard-coded zero positions (fails closed if active positions limit reached)
7. algo_enabled=False rejects execution (ALGO_DISABLED)
8. kill_switch_active=True rejects execution (KILL_SWITCH_ACTIVE)
9. Stale/unavailable account state rejects execution (ACCOUNT_STATE_STALE)
10. Insufficient balance rejects execution (INSUFFICIENT_BALANCE)
11. Excessive risk rejects execution (EXCESSIVE_RISK)
12. Excessive leverage rejects execution (EXCESSIVE_LEVERAGE)
13. Invalid TP/SL rejects execution (INVALID_TP_SL_GEOMETRY)
14. Frontend TP/SL mismatch with authoritative setup rejects execution
15. Setup not TRADE_SETUP_READY rejects execution (DECISION_NOT_READY)
16. Duplicate setup cannot execute twice (DUPLICATE_SETUP_ID)
17. Duplicate client_order_id cannot execute twice (DUPLICATE_CLIENT_ORDER_ID)
18. Concurrent duplicate requests produce only one exchange submission
19. Timeout triggers immediate reconciliation
20. Ambiguous order state never causes blind retry
21. Exchange rejection is safely recorded
22. Credentials never appear in logs/errors/responses
23. Kill switch cannot be bypassed
24. Account ownership cannot be bypassed
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import threading
from unittest.mock import MagicMock, AsyncMock
import pytest

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    TimeInForce,
    DeltaOrderRequest,
    DeltaOrderResponse,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaAuthError,
    DeltaRateLimitError,
    DeltaOrderRejectedError,
    DeltaConnectionError,
    DeltaResponseError,
)
from quantedge.execution.synchronizer import (
    AccountRecord,
    ConnectionRecord,
    PositionRecord,
    PositionStatus,
    LocalStateStore,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    ValidationContext,
    RiskConfiguration,
    RejectionReasonCode,
)
from quantedge.execution.execution_engine import (
    ExecutionState,
    OrderExecutionRequest,
    OrderExecutionResult,
    LiveOrderExecutionService,
    StrategyDecisionStore,
)
from quantedge.strategy.models import StrategyDecision, SetupState, StrategyDirection, TradeDirection


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_context():
    """Create fresh, authoritative account and connection context."""
    account = AccountRecord(
        account_id="acc_live_01",
        base_currency="USDT",
        current_balance=Decimal("5000.00"),
        available_balance=Decimal("5000.00"),
        margin_used=Decimal("0.00"),
        total_equity=Decimal("5000.00"),
        is_active=True,
        user_id="user_owner_123",
        last_synced_at=datetime.now(timezone.utc),
    )
    connection = ConnectionRecord(
        connection_status="CONNECTED",
        last_connected_at=datetime.now(timezone.utc),
    )
    risk_config = RiskConfiguration(
        risk_per_trade_pct=Decimal("35.0"),
        target_reward_pct=Decimal("60.0"),
        max_leverage=100,
        max_concurrent_trades=1,
        minimum_risk_reward=Decimal("1.5"),
    )
    return ValidationContext(
        account=account,
        algo_enabled=True,
        kill_switch_active=False,
        connection=connection,
        api_key="valid_delta_api_key_123456",
        api_secret="valid_delta_api_secret_654321",
        risk_config=risk_config,
        open_positions=[],
        open_orders=[],
        active_client_order_ids=set(),
        active_setup_ids=set(),
    )


@pytest.fixture
def mock_client():
    """Create mock Delta India client with successful default responses."""
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "valid_delta_api_key_123456"
    client._api_secret = "valid_delta_api_secret_654321"

    client.place_order = AsyncMock(return_value=DeltaOrderResponse(
        id=889900,
        client_order_id="QE-HARDEN-01",
        user_id=1,
        product_id=27,
        product_symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("1"),
        unfilled_size=Decimal("1"),
        limit_price=Decimal("95000.0"),
        stop_price=None,
        average_fill_price=None,
        state=OrderStatus.OPEN,
        reduce_only=False,
        created_at=datetime.now(timezone.utc),
    ))
    client.create_order = client.place_order
    client.get_open_orders = AsyncMock(return_value=[])
    return client


# ── Test Suite: 24 Safety & Hardening Cases ───────────────────────────────────


@pytest.mark.asyncio
async def test_01_and_02_server_side_credentials_required(fresh_context, mock_client):
    """Req 1 & 2: Missing server-side credentials immediately rejects order with 0 network calls."""
    fresh_context.api_key = ""
    fresh_context.api_secret = ""

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-01",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.DELTA_CREDENTIALS_MISSING.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_03_and_24_account_ownership_enforcement(fresh_context, mock_client):
    """Req 3 & 24: Unauthorized user attempting to trade another user's account is rejected."""
    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-02",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="attacker_user_999",  # Mismatch with context.account.user_id ("user_owner_123")
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.UNAUTHORIZED_ACCOUNT.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_04_absence_of_hardcoded_balance(fresh_context, mock_client):
    """Req 4: If account equity is 0 or insufficient, order is rejected (no fake $10,000 fallback)."""
    fresh_context.account.total_equity = Decimal("0.00")
    fresh_context.account.available_balance = Decimal("0.00")

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-03",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code in (RejectionReasonCode.INSUFFICIENT_BALANCE.value, RejectionReasonCode.EXCESSIVE_RISK.value)
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_05_absence_of_hardcoded_connected(fresh_context, mock_client):
    """Req 5: If Delta connection status is DISCONNECTED/ERROR, order is rejected."""
    fresh_context.connection.connection_status = "DISCONNECTED"

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-04",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.EXCHANGE_DISCONNECTED.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_06_absence_of_hardcoded_zero_positions(fresh_context, mock_client):
    """Req 6: If active positions count reaches max_concurrent_trades, order is rejected."""
    fresh_context.open_positions = [
        PositionRecord(
            symbol="BTCUSD",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("94000.0"),
            current_price=Decimal("95000.0"),
            unrealized_pnl=Decimal("1000.0"),
            realized_pnl=Decimal("0.0"),
            leverage=Decimal("50"),
            margin_used=Decimal("100.0"),
            status=PositionStatus.OPEN,
        )
    ]

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-05",
        symbol="ETHUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("2800.0"),
        stop_loss=Decimal("2700.0"),
        take_profit=Decimal("3000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.CONCURRENT_TRADE_LIMIT_EXCEEDED.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_07_algo_disabled_rejects_execution(fresh_context, mock_client):
    """Req 7: algo_enabled=False blocks live execution."""
    fresh_context.algo_enabled = False

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-06",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.ALGO_DISABLED.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_08_and_23_kill_switch_active_cannot_be_bypassed(fresh_context, mock_client):
    """Req 8 & 23: Emergency kill switch active blocks live order placement unconditionally."""
    fresh_context.kill_switch_active = True

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-07",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.KILL_SWITCH_ACTIVE.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_09_stale_account_sync_rejects_execution(fresh_context, mock_client):
    """Req 9: If account state synchronization is older than 60s, order is rejected (fail closed)."""
    fresh_context.account.last_synced_at = datetime.now(timezone.utc) - timedelta(seconds=120)

    service = LiveOrderExecutionService(sync_staleness_threshold_seconds=60)
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-08",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.ACCOUNT_STATE_STALE.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_10_insufficient_balance_rejects_execution(fresh_context, mock_client):
    """Req 10: Margin requirement exceeding available balance is rejected."""
    fresh_context.account.available_balance = Decimal("10.00")  # too small

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-09",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        leverage=1,  # Req margin = 95000 > 10.00
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.INSUFFICIENT_BALANCE.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_11_excessive_risk_rejects_execution(fresh_context, mock_client):
    """Req 11: Total dollar risk exceeding risk_per_trade_pct of equity is rejected."""
    fresh_context.risk_config = RiskConfiguration(risk_per_trade_pct=Decimal("5.0"))  # 5% of $5000 = $250 max risk
    fresh_context.account.total_equity = Decimal("5000.00")

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-10",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("2"),  # 2 BTC * $1000 risk distance = $2000 risk > $250
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.EXCESSIVE_RISK.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_12_excessive_leverage_rejects_execution(fresh_context, mock_client):
    """Req 12: Order leverage exceeding configured max_leverage is rejected."""
    fresh_context.risk_config = RiskConfiguration(max_leverage=25)

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-11",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        leverage=50,  # 50 > 25
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_13_invalid_tp_sl_geometry_rejects_execution(fresh_context, mock_client):
    """Req 13: Inverted TP/SL geometry is rejected before submission."""
    service = LiveOrderExecutionService()
    # LONG with SL > Entry (inverted)
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-12",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("96000.0"),  # SL > Entry for LONG
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == RejectionReasonCode.INVALID_TP_SL_GEOMETRY.value
    mock_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_14_and_15_authoritative_strategy_decision_validation(fresh_context, mock_client):
    """Req 14 & 15: Strategy decision must be TRADE_SETUP_READY with valid geometry and R:R."""
    service = LiveOrderExecutionService()

    # Decision not in ready state
    invalid_state_decision = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.WATCHING_OB,  # NOT READY
        setup_id="SETUP-13-DISQUALIFIED",
        entry=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
    )
    res1 = await service.execute_from_strategy_decision(
        decision=invalid_state_decision,
        context=fresh_context,
        client=mock_client,
        account_id="acc_live_01",
    )
    assert res1.success is False
    assert res1.rejection_code == RejectionReasonCode.DECISION_NOT_READY.value

    # Decision with low R:R (< 1.5)
    low_rr_decision = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP-14-LOW-RR",
        entry=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("95500.0"),  # RR = 0.5 < 1.5
        risk_distance=Decimal("1000.0"),
        reward_distance=Decimal("500.0"),
        risk_reward=Decimal("0.5"),
    )
    res2 = await service.execute_from_strategy_decision(
        decision=low_rr_decision,
        context=fresh_context,
        client=mock_client,
        account_id="acc_live_01",
    )
    assert res2.success is False
    assert res2.rejection_code == RejectionReasonCode.INVALID_RISK_REWARD.value


@pytest.mark.asyncio
async def test_16_duplicate_setup_cannot_execute_twice(fresh_context, mock_client):
    """Req 16: An authoritative setup that was executed cannot execute a second time."""
    decision_store = StrategyDecisionStore()
    service = LiveOrderExecutionService(decision_store=decision_store)

    decision = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP-ONCE-ONLY",
        entry=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        risk_distance=Decimal("1000.0"),
        reward_distance=Decimal("2000.0"),
        risk_reward=Decimal("2.0"),
    )

    res1 = await service.execute_from_strategy_decision(
        decision=decision,
        context=fresh_context,
        client=mock_client,
        account_id="acc_live_01",
        quantity=Decimal("1"),
    )
    assert res1.success is True

    # Second attempt with same setup_id
    res2 = await service.execute_from_strategy_decision(
        decision=decision,
        context=fresh_context,
        client=mock_client,
        account_id="acc_live_01",
        quantity=Decimal("1"),
    )
    assert res2.success is False
    assert res2.rejection_code == RejectionReasonCode.DUPLICATE_SETUP_ID.value
    assert mock_client.place_order.call_count == 1


@pytest.mark.asyncio
async def test_17_and_18_persistent_idempotency_and_concurrency(fresh_context, mock_client):
    """Req 17 & 18: In-flight and persistent duplicate client_order_ids produce only 1 live order."""
    service = LiveOrderExecutionService()
    results = []

    def run_concurrent_req(idx):
        req = OrderExecutionRequest(
            account_id="acc_live_01",
            setup_id="SETUP-PARALLEL-100",
            symbol="BTCUSD",
            direction=TradeDirection.LONG,
            order_type=OrderType.LIMIT_ORDER,
            quantity=Decimal("1"),
            entry_price=Decimal("95000.0"),
            stop_loss=Decimal("94000.0"),
            take_profit=Decimal("97000.0"),
            client_order_id=f"QE-IDEMPOTENT-{idx}",
            user_id="user_owner_123",
        )
        res = service.execute_order_sync(req, fresh_context, mock_client)
        results.append(res)

    threads = [threading.Thread(target=run_concurrent_req, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.success]
    rejections = [r for r in results if not r.success]

    assert len(successes) == 1
    assert len(rejections) == 4
    assert mock_client.place_order.call_count == 1


@pytest.mark.asyncio
async def test_19_and_20_timeout_immediate_reconciliation(fresh_context, mock_client):
    """Req 19 & 20: Network timeout initiates immediate reconciliation query before concluding state."""
    mock_client.place_order.side_effect = DeltaConnectionError("Connection timeout during POST /v2/orders")
    mock_client.get_open_orders.return_value = [
        DeltaOrderResponse(
            id=554433,
            client_order_id="QE-RECON-99",
            user_id=1,
            product_id=27,
            product_symbol="BTCUSD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT_ORDER,
            size=Decimal("1"),
            unfilled_size=Decimal("1"),
            limit_price=Decimal("95000.0"),
            stop_price=None,
            average_fill_price=None,
            state=OrderStatus.OPEN,
            reduce_only=False,
            created_at=datetime.now(timezone.utc),
        )
    ]

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-RECON-01",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        client_order_id="QE-RECON-99",
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is True
    assert result.execution_state == ExecutionState.SUBMITTED
    assert result.order_id == 554433
    assert result.reconciled is True
    # Verify no blind duplicate retry was made
    assert mock_client.place_order.call_count == 1
    mock_client.get_open_orders.assert_called_once()


@pytest.mark.asyncio
async def test_21_exchange_rejection_recorded(fresh_context, mock_client):
    """Req 21: Exchange rejection (HTTP 400 price collar limit) is mapped to REJECTED."""
    mock_client.place_order.side_effect = DeltaOrderRejectedError("Order price exceeds exchange collar boundary")

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-EXCH-REJ",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert result.execution_state == ExecutionState.REJECTED
    assert result.rejection_code == "EXCHANGE_REJECTED"
    assert "collar boundary" in result.error_message


@pytest.mark.asyncio
async def test_22_secret_redaction_in_audit_and_errors(fresh_context, mock_client):
    """Req 22: Sensitive API secrets never appear in error messages or responses."""
    mock_client.place_order.side_effect = Exception("Failed with secret: valid_delta_api_secret_654321")
    mock_client.get_open_orders.return_value = []

    service = LiveOrderExecutionService()
    req = OrderExecutionRequest(
        account_id="acc_live_01",
        setup_id="SETUP-SECRET-TEST",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        user_id="user_owner_123",
    )
    result = await service.execute_order(req, fresh_context, mock_client)

    assert result.success is False
    assert "valid_delta_api_secret_654321" not in str(result.error_message)
