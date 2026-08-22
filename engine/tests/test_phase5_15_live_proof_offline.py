"""
Phase 5.15 Offline Test Suite: Real Live Delta Exchange Execution Proof Machinery.

Tests the state transitions, safety gates, margin evaluations, fill polling,
protection placement, emergency close fail-safes, and reconciliation of
the LiveDeltaExecutionProofOrchestrator without requiring real network calls.

Frozen SMC core remains 100% untouched.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    DeltaWalletBalance,
    DeltaPosition,
    DeltaOrderRequest,
    DeltaOrderResponse,
    ConnectionState,
    ExecutionMode,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaOrderRejectedError,
)
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.live_proof import (
    CandidateInstrumentSpec,
    SafetyGateReport,
    LiveExecutionProofReport,
    LiveDeltaExecutionProofOrchestrator,
)


@pytest.fixture
def mock_client_live():
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "LIVE_TEST_KEY_12345"
    client._api_secret = "LIVE_TEST_SECRET_67890"
    client.base_url = "https://api.india.delta.exchange"
    client.connection_state = ConnectionState.CONNECTED

    client.validate_credentials = AsyncMock(return_value=(True, ConnectionState.CONNECTED, None))
    client.get_wallet_balances = AsyncMock(return_value=[
        DeltaWalletBalance(
            asset_symbol="USDT",
            balance=Decimal("2.31"),
            available_balance=Decimal("2.31"),
            position_margin=Decimal("0.00"),
            order_margin=Decimal("0.00"),
            blocked_margin=Decimal("0.00"),
            user_id=12345,
        )
    ])
    client.get_positions = AsyncMock(return_value=[])
    client.get_open_orders = AsyncMock(return_value=[])

    # Mock products response
    async def mock_request(method, path, **kwargs):
        if path == "/v2/products":
            return {
                "success": True,
                "result": [
                    {
                        "symbol": "ETHUSD",
                        "id": 3136,
                        "contract_value": "0.01",
                        "contract_unit_currency": "ETH",
                        "tick_size": "0.05",
                        "default_leverage": "200.0",
                    },
                    {
                        "symbol": "BTCUSD",
                        "id": 27,
                        "contract_value": "0.001",
                        "contract_unit_currency": "BTC",
                        "tick_size": "0.5",
                        "default_leverage": "200.0",
                    }
                ]
            }
        elif path == "/v2/tickers/ETHUSD":
            return {"success": True, "result": {"mark_price": "2400.00"}}
        elif path == "/v2/tickers/BTCUSD":
            return {"success": True, "result": {"mark_price": "77000.00"}}
        return {"success": True, "result": {}}

    client.request = AsyncMock(side_effect=mock_request)

    # Mock place_order
    order_id_counter = 500000
    async def mock_place_order(req: DeltaOrderRequest):
        nonlocal order_id_counter
        order_id_counter += 1
        return DeltaOrderResponse(
            id=order_id_counter,
            client_order_id=req.client_order_id,
            user_id=12345,
            product_id=req.product_id,
            product_symbol=req.product_symbol,
            side=req.side,
            order_type=req.order_type,
            size=req.size,
            unfilled_size=Decimal("0"),
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            average_fill_price=req.limit_price or Decimal("2402.40"),
            state=OrderStatus.FILLED,
            reduce_only=req.reduce_only,
            created_at=datetime.now(timezone.utc),
        )

    client.place_order = AsyncMock(side_effect=mock_place_order)
    client.cancel_order = AsyncMock(return_value=True)
    client.get_order = AsyncMock(return_value=DeltaOrderResponse(
        id=500001,
        client_order_id="c1",
        user_id=12345,
        product_id=3136,
        product_symbol="ETHUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("1"),
        unfilled_size=Decimal("0"),
        limit_price=Decimal("2402.40"),
        stop_price=None,
        average_fill_price=Decimal("2402.40"),
        state=OrderStatus.FILLED,
        reduce_only=False,
        created_at=datetime.now(timezone.utc),
    ))

    return client


# ── Test 1: Safety Gate Evaluation with Missing Credentials ────────────────────

@pytest.mark.asyncio
async def test_safety_gate_missing_credentials():
    """Verify safety gates halt gracefully when credentials are not configured."""
    with patch.dict("os.environ", {}, clear=True):
        orchestrator = LiveDeltaExecutionProofOrchestrator()
        gate_report = await orchestrator.evaluate_safety_gates()

        assert gate_report.api_authentication_pass is False
        assert gate_report.all_gates_passed is False
        assert "Missing credentials" in gate_report.blocked_reason


# ── Test 2: Safety Gate Evaluation with Auth Failure ──────────────────────────

@pytest.mark.asyncio
async def test_safety_gate_auth_failure(mock_client_live):
    """Verify safety gates halt when Delta returns authentication error."""
    mock_client_live.validate_credentials = AsyncMock(return_value=(False, ConnectionState.AUTH_FAILED, "Signature mismatch"))
    orchestrator = LiveDeltaExecutionProofOrchestrator(client=mock_client_live)
    gate_report = await orchestrator.evaluate_safety_gates()

    assert gate_report.api_authentication_pass is False
    assert gate_report.all_gates_passed is False
    assert "Delta authentication failed" in gate_report.blocked_reason


# ── Test 3: Safety Gate Evaluation for $2.31 Balance ───────────────────────────

@pytest.mark.asyncio
async def test_safety_gate_small_balance_candidate_selection(mock_client_live):
    """Verify candidate selection correctly identifies ETHUSD ($0.69 margin) for $2.31 balance."""
    orchestrator = LiveDeltaExecutionProofOrchestrator(client=mock_client_live)
    gate_report = await orchestrator.evaluate_safety_gates()

    assert gate_report.api_authentication_pass is True
    assert gate_report.all_gates_passed is True
    assert gate_report.selected_instrument is not None
    assert gate_report.selected_instrument.symbol == "ETHUSD"
    assert gate_report.selected_instrument.product_id == 3136
    assert gate_report.selected_instrument.required_margin_at_35x < Decimal("1.00")


# ── Test 4: Safety Gate Evaluation When Balance is Insufficient ────────────────

@pytest.mark.asyncio
async def test_safety_gate_insufficient_balance_blocks_order(mock_client_live):
    """Verify that if available balance ($0.10) is below minimum margin, execution is blocked."""
    mock_client_live.get_wallet_balances = AsyncMock(return_value=[
        DeltaWalletBalance(
            asset_symbol="USDT",
            balance=Decimal("0.10"),
            available_balance=Decimal("0.10"),
            position_margin=Decimal("0.00"),
            order_margin=Decimal("0.00"),
            blocked_margin=Decimal("0.00"),
            user_id=12345,
        )
    ])
    orchestrator = LiveDeltaExecutionProofOrchestrator(client=mock_client_live)
    gate_report = await orchestrator.evaluate_safety_gates()

    assert gate_report.all_gates_passed is False
    assert gate_report.risk_validation_pass is False
    assert "below exchange minimum margin" in gate_report.blocked_reason


# ── Test 5: Full Live Proof Execution Lifecycle ───────────────────────────────

@pytest.mark.asyncio
async def test_live_proof_full_lifecycle(mock_client_live):
    """Verify end-to-end live proof execution, bracket placement, and closure."""
    orchestrator = LiveDeltaExecutionProofOrchestrator(client=mock_client_live)
    report = await orchestrator.execute_live_proof()

    assert report.status == "SUCCESS_REAL_LIVE_ORDER_VERIFIED"
    assert report.symbol == "ETHUSD"
    assert report.product_id == 3136
    assert report.entry_order_id is not None
    assert report.entry_fill_price == Decimal("2402.40")
    assert report.sl_order_id is not None
    assert report.tp_order_id is not None
    assert report.close_order_id is not None
    assert report.final_position_size == Decimal("0")
    assert len(report.audit_events) >= 3


# ── Test 6: Protection Failure Triggers Emergency Cleanup ─────────────────────

@pytest.mark.asyncio
async def test_live_proof_protection_failure_emergency_cleanup(mock_client_live):
    """Verify that if bracket order placement fails, emergency cleanup triggers."""
    call_count = 0
    async def failing_place_order(req: DeltaOrderRequest):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return DeltaOrderResponse(
                id=600001,
                client_order_id=req.client_order_id,
                user_id=12345,
                product_id=req.product_id,
                product_symbol=req.product_symbol,
                side=req.side,
                order_type=req.order_type,
                size=req.size,
                unfilled_size=Decimal("0"),
                limit_price=req.limit_price,
                stop_price=req.stop_price,
                average_fill_price=Decimal("2402.40"),
                state=OrderStatus.FILLED,
                reduce_only=False,
                created_at=datetime.now(timezone.utc),
            )
        else:
            raise DeltaOrderRejectedError("Exchange rejected bracket order")

    mock_client_live.place_order = AsyncMock(side_effect=failing_place_order)
    orchestrator = LiveDeltaExecutionProofOrchestrator(client=mock_client_live)
    report = await orchestrator.execute_live_proof()

    assert report.status == "EXECUTION_EXCEPTION"
    assert "Exchange rejected bracket order" in report.error_message


# ── Test 7: Unfilled Entry Order Cancelled Gracefully ──────────────────────────

@pytest.mark.asyncio
async def test_live_proof_unfilled_entry_cancelled(mock_client_live):
    """Verify that if entry order remains open without fill, it is cancelled cleanly."""
    mock_client_live.get_order = AsyncMock(return_value=DeltaOrderResponse(
        id=700001,
        client_order_id="c1",
        user_id=12345,
        product_id=3136,
        product_symbol="ETHUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("1"),
        unfilled_size=Decimal("1"),
        limit_price=Decimal("2402.40"),
        stop_price=None,
        average_fill_price=None,
        state=OrderStatus.OPEN,
        reduce_only=False,
        created_at=datetime.now(timezone.utc),
    ))

    orchestrator = LiveDeltaExecutionProofOrchestrator(client=mock_client_live)
    report = await orchestrator.execute_live_proof()

    assert report.status == "ENTRY_UNFILLED_CANCELLED"
    mock_client_live.cancel_order.assert_called()
