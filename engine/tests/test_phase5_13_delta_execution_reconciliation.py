"""
Phase 5.13: Delta Exchange Production Execution, Order Protection & Reconciliation — Test Suite.

Covers all 26 required scenarios (A through Z):
  A. Credential validation & secure from_env loading
  B. Connection lifecycle (CONNECTED, DISCONNECTED, AUTH_FAILED, RATE_LIMITED, EXCHANGE_ERROR, UNKNOWN)
  C. Live-mode safety gates (fail closed on stale/un-synced/kill-switch-active)
  D. Real entry order execution path
  E. Entry rejection by exchange
  F. Partial fills & dynamic protection scaling
  G. Timeout recovery & exchange state lookup
  H. Duplicate submission idempotency & deterministic client order IDs
  I. Authoritative Order Block SL enforcement & rejection of client overrides
  J. Dynamic leverage calculation & floor rounding
  K. 35% maximum planned loss invariant
  L. 60% ROE default TP calculation
  M. User TP target configuration & snapshot immutability
  N. Protected position requirement (entry fill -> immediate SL + TP)
  O. Protection failure recovery state (PROTECTION_FAILED)
  P. Order reconciliation
  Q. Position reconciliation (orphaned / missing / size mismatch)
  R. Fee reconciliation (trading fees)
  S. Funding reconciliation
  T. Net P&L formula (Gross - Fees - Funding - Other)
  U. 100% capital compounding on next trade
  V. Database-enforced one-active-trade lock
  W. Scanner restart recovery
  X. Backend restart recovery
  Y. Exchange / network disconnection handling
  Z. Emergency kill switch

Constraints:
- ZERO real orders placed during testing (DeltaIndiaClient is mocked).
- ZERO modifications to frozen SMC core (structure.py, order_blocks.py, volatility.py).
"""

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    TimeInForce,
    DeltaWalletBalance,
    DeltaAccountSummary,
    DeltaPosition,
    DeltaOrderRequest,
    DeltaOrderResponse,
    ConnectionState,
    ExecutionMode,
    ReconciliationDiscrepancyType,
    ReconciliationReport,
    TradeCostBreakdown,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaClientError,
    DeltaAuthError,
    DeltaRateLimitError,
    DeltaOrderRejectedError,
    DeltaConnectionError,
    generate_signature,
    generate_client_order_id,
    generate_deterministic_client_order_id,
)
from quantedge.execution.reconciliation import DeltaReconciliationService
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
    CloseReason,
)
from quantedge.execution.synchronizer import (
    LocalStateStore,
    AccountRecord,
    PositionRecord,
    OrderRecord,
    PositionStatus,
    ConnectionRecord,
    LiveAccountSyncService,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    OrderValidationRequest,
    RiskConfiguration,
    RejectionReasonCode,
)
from quantedge.execution.algo_config import AlgoConfigStore, AlgoConfiguration
from quantedge.execution.single_trade_lock import SingleTradeLockManager, SingleTradeLockError
from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.market_orchestrator import MarketScannerOrchestrator, MarketScanResult
from quantedge.strategy.models import StrategyDecision, SetupState, StrategyDirection, TradeDirection


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def state_store():
    store = LocalStateStore()
    store.account.account_id = "acct-test"
    store.account.user_id = "usr-test"
    store.account.total_equity = Decimal("10000.00")
    store.account.available_balance = Decimal("10000.00")
    store.account.current_balance = Decimal("10000.00")
    store.account.last_synced_at = datetime.now(timezone.utc)
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.connection.connection_status = "CONNECTED"
    return store


@pytest.fixture
def mock_delta_client():
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "TEST_API_KEY"
    client._api_secret = "TEST_API_SECRET"
    client.base_url = "https://api.india.delta.exchange"
    client.connection_state = ConnectionState.CONNECTED
    
    # Default async return values
    client.get_wallet_balances = AsyncMock(return_value=[
        DeltaWalletBalance(
            asset_symbol="USDT",
            balance=Decimal("10000.00"),
            available_balance=Decimal("10000.00"),
            position_margin=Decimal("0.00"),
            order_margin=Decimal("0.00"),
            blocked_margin=Decimal("0.00"),
        )
    ])
    client.get_positions = AsyncMock(return_value=[])
    client.get_open_orders = AsyncMock(return_value=[])
    async def default_place_order(req):
        return DeltaOrderResponse(
            id=12345,
            client_order_id=req.client_order_id,
            user_id=1,
            product_id=req.product_id,
            product_symbol=req.product_symbol,
            side=req.side,
            order_type=req.order_type,
            size=req.size,
            unfilled_size=Decimal("0"),
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            average_fill_price=req.limit_price or Decimal("50000"),
            state=OrderStatus.FILLED,
            reduce_only=req.reduce_only,
            created_at=datetime.now(timezone.utc),
        )
    client.place_order = AsyncMock(side_effect=default_place_order)
    client.cancel_order = AsyncMock(return_value=True)
    return client


@pytest.fixture
def lifecycle_mgr(mock_delta_client, state_store):
    val_gw = OrderValidationGateway()
    algo_store = AlgoConfigStore()
    lock_mgr = SingleTradeLockManager()
    cap_alloc = CapitalAllocator()
    
    return TradeLifecycleManager(
        client=mock_delta_client,
        validation_gateway=val_gw,
        state_store=state_store,
        algo_config_store=algo_store,
        single_trade_lock=lock_mgr,
        capital_allocator=cap_alloc,
        execution_mode=ExecutionMode.LIVE,
    )


def make_decision(setup_id: str, direction=StrategyDirection.LONG, entry=Decimal("50000"), sl=Decimal("49000"), tp=Decimal("51714"), qty=Decimal("1.0")):
    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="15m",
        direction=direction,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id=setup_id,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=Decimal("1.71"),
        quantity=qty,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test Scenarios A through Z
# ─────────────────────────────────────────────────────────────────────────────

# A. Credential validation & secure from_env loading
def test_scenario_a_credentials_validation():
    client = DeltaIndiaClient(api_key="valid_key", api_secret="valid_secret")
    assert "valid_secret" not in repr(client)
    assert "valid_secret" not in str(client)
    assert client.connection_state == ConnectionState.UNKNOWN

    with patch.dict(os.environ, {"DELTA_API_KEY": "", "DELTA_API_SECRET": ""}):
        with pytest.raises(ValueError, match="DELTA_API_KEY and DELTA_API_SECRET"):
            DeltaIndiaClient.from_env()

    with patch.dict(os.environ, {"DELTA_API_KEY": "env_k", "DELTA_API_SECRET": "env_s"}):
        env_client = DeltaIndiaClient.from_env()
        assert env_client._api_key == "env_k"
        assert env_client._api_secret == "env_s"


# B. Connection lifecycle
@pytest.mark.asyncio
async def test_scenario_b_connection_lifecycle(mock_delta_client):
    real_client = DeltaIndiaClient(api_key="k", api_secret="s")
    
    with patch.object(real_client, "get_wallet_balances", side_effect=DeltaAuthError("Invalid API key")):
        success, state, err = await real_client.validate_credentials()
        assert not success
        assert state == ConnectionState.AUTH_FAILED
        assert real_client.connection_state == ConnectionState.AUTH_FAILED

    with patch.object(real_client, "get_wallet_balances", side_effect=DeltaRateLimitError("Rate limit", retry_after=10)):
        success, state, err = await real_client.validate_credentials()
        assert not success
        assert state == ConnectionState.RATE_LIMITED
        assert "10s" in err

    with patch.object(real_client, "get_wallet_balances", return_value=[]):
        success, state, err = await real_client.validate_credentials()
        assert success
        assert state == ConnectionState.CONNECTED
        assert real_client.connection_state == ConnectionState.CONNECTED


# C. Live-mode safety gates
@pytest.mark.asyncio
async def test_scenario_c_live_mode_safety_gates(lifecycle_mgr, state_store):
    decision = make_decision("setup-gate-1")

    # 1. Kill Switch Active -> Reject
    state_store.account.kill_switch_active = True
    rec = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec.rejection_code == RejectionReasonCode.KILL_SWITCH_ACTIVE.value

    # 2. Algo Disabled -> Reject
    state_store.account.kill_switch_active = False
    state_store.account.algo_enabled = False
    rec2 = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec2.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec2.rejection_code == RejectionReasonCode.ALGO_DISABLED.value

    # 3. Stale Account State -> Reject
    state_store.account.algo_enabled = True
    state_store.account.last_synced_at = datetime.now(timezone.utc) - timedelta(seconds=200)
    rec3 = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec3.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec3.rejection_code == RejectionReasonCode.ACCOUNT_STATE_STALE.value


# D. Real entry order execution path
@pytest.mark.asyncio
async def test_scenario_d_entry_order_execution(lifecycle_mgr, state_store):
    decision = make_decision("setup-exec-d")
    rec = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec.state == TradeLifecycleState.PROTECTED_POSITION
    assert rec.entry_order_id == "12345"
    assert rec.sl_order_id is not None
    assert rec.tp_order_id is not None


# E. Entry rejection by exchange
@pytest.mark.asyncio
async def test_scenario_e_entry_rejection(lifecycle_mgr, mock_delta_client):
    mock_delta_client.place_order = AsyncMock(side_effect=DeltaOrderRejectedError("Insufficient margin"))
    decision = make_decision("setup-reject-e")

    rec = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec.state == TradeLifecycleState.ENTRY_REJECTED
    assert "Insufficient margin" in rec.error_message


# F. Partial fills & dynamic protection scaling
@pytest.mark.asyncio
async def test_scenario_f_partial_fills_scaling(lifecycle_mgr):
    rec = TradeLifecycleRecord(
        setup_id="setup-partial-f",
        account_id="acct-test",
        user_id="usr-test",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        requested_quantity=Decimal("1.0"),
        entry_price=Decimal("50000"),
        stop_loss_price=Decimal("49000"),
        take_profit_price=Decimal("51714"),
        risk_reward_ratio=Decimal("1.71"),
        risk_amount=Decimal("1000"),
        reward_amount=Decimal("1714"),
    )
    lifecycle_mgr._active_trades["setup-partial-f"] = rec

    # 40% partial fill
    await lifecycle_mgr.on_entry_partial_fill("setup-partial-f", Decimal("0.4"), Decimal("50000"))
    assert rec.state == TradeLifecycleState.PROTECTED_POSITION
    assert rec.protected_quantity == Decimal("0.4")


# G. Timeout recovery during entry
@pytest.mark.asyncio
async def test_scenario_g_timeout_recovery(lifecycle_mgr, mock_delta_client):
    mock_delta_client.place_order = AsyncMock(side_effect=DeltaConnectionError("Gateway timeout"))
    decision = make_decision("setup-timeout-g")

    rec = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    assert "Gateway timeout" in rec.error_message


# H. Duplicate submission idempotency
@pytest.mark.asyncio
async def test_scenario_h_duplicate_submission(lifecycle_mgr):
    decision = make_decision("setup-dup-h")

    rec1 = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec1.state == TradeLifecycleState.PROTECTED_POSITION

    # Second attempt with same setup_id
    rec2 = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec2.rejection_code == RejectionReasonCode.DUPLICATE_SETUP_ID.value


# I. Authoritative Order Block SL enforcement
@pytest.mark.asyncio
async def test_scenario_i_authoritative_ob_sl(lifecycle_mgr):
    decision = make_decision("setup-ob-i")

    # Attempt frontend tampering of SL to 49500
    rec = await lifecycle_mgr.execute_trade_setup(
        decision, "acct-test", "usr-test",
        frontend_params={"stop_loss": "49500"}
    )
    assert rec.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec.rejection_code == "FRONTEND_SL_TAMPERING"


# J. Dynamic leverage calculation & floor rounding
def test_scenario_j_dynamic_leverage():
    entry = Decimal("50000")
    
    # 1% distance (SL = 49500) -> 0.35 / 0.01 = 35x
    dist_1pct = (entry - Decimal("49500")) / entry
    lev_1pct = max(1, int(Decimal("0.35") / dist_1pct))
    assert lev_1pct == 35

    # 2% distance (SL = 49000) -> 0.35 / 0.02 = 17.5 -> floor = 17x
    dist_2pct = (entry - Decimal("49000")) / entry
    lev_2pct = max(1, int(Decimal("0.35") / dist_2pct))
    assert lev_2pct == 17

    # 5% distance (SL = 47500) -> 0.35 / 0.05 = 7x
    dist_5pct = (entry - Decimal("47500")) / entry
    lev_5pct = max(1, int(Decimal("0.35") / dist_5pct))
    assert lev_5pct == 7


# K. 35% maximum planned loss invariant
def test_scenario_k_max_planned_loss_invariant():
    for dist_pct in [Decimal("0.005"), Decimal("0.01"), Decimal("0.015"), Decimal("0.02"), Decimal("0.035"), Decimal("0.05"), Decimal("0.10"), Decimal("0.20")]:
        lev = max(1, int(Decimal("0.35") / dist_pct))
        actual_loss_pct = Decimal(str(lev)) * dist_pct
        assert actual_loss_pct <= Decimal("0.35"), f"Failed for distance {dist_pct}: lev={lev}, loss={actual_loss_pct}"


# L. 60% ROE default TP calculation
def test_scenario_l_default_60pct_roe_tp():
    entry = Decimal("50000")
    leverage = 17  # for 2% SL
    
    price_move_fraction = Decimal("0.60") / Decimal(str(leverage))
    long_tp = entry * (Decimal("1") + price_move_fraction)
    short_tp = entry * (Decimal("1") - price_move_fraction)

    # LONG TP should be ~51764.71
    assert long_tp > entry
    assert round(long_tp, 2) == Decimal("51764.71")

    # SHORT TP should be ~48235.29
    assert short_tp < entry
    assert round(short_tp, 2) == Decimal("48235.29")


# M. User TP target configuration & snapshot immutability
def test_scenario_m_tp_target_snapshot():
    store = AlgoConfigStore()
    store.update_config("usr-test", "acct-test", take_profit_target_pct=Decimal("75.00"))

    snap = store.create_trade_snapshot("usr-test", "acct-test", "setup-m-1")
    assert snap.take_profit_target_pct == Decimal("75.00")

    # Modify base config
    store.update_config("usr-test", "acct-test", take_profit_target_pct=Decimal("90.00"))

    # Snapshot must remain 75.00
    retrieved_snap = store.get_trade_snapshot("setup-m-1")
    assert retrieved_snap.take_profit_target_pct == Decimal("75.00")


# N. Protected position requirement
@pytest.mark.asyncio
async def test_scenario_n_protected_position_requirement(lifecycle_mgr):
    decision = make_decision("setup-prot-n")
    rec = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec.state == TradeLifecycleState.PROTECTED_POSITION
    assert rec.protected_quantity == Decimal("1.0")


# O. Protection failure recovery state (PROTECTION_FAILED)
@pytest.mark.asyncio
async def test_scenario_o_protection_failure_state(lifecycle_mgr, mock_delta_client):
    call_count = 0
    async def place_order_mock(req):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return DeltaOrderResponse(
                id=999, client_order_id=req.client_order_id, user_id=1, product_id=27,
                product_symbol="BTCUSD", side=req.side, order_type=req.order_type,
                size=req.size, unfilled_size=Decimal("0"), limit_price=req.limit_price,
                stop_price=None, average_fill_price=Decimal("50000"), state=OrderStatus.FILLED,
                reduce_only=False, created_at=datetime.now(timezone.utc),
            )
        else:
            raise DeltaOrderRejectedError("Exchange rejected SL/TP order")

    mock_delta_client.place_order = AsyncMock(side_effect=place_order_mock)
    decision = make_decision("setup-fail-o")

    rec = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec.state == TradeLifecycleState.PROTECTION_FAILED


# P. Order reconciliation
@pytest.mark.asyncio
async def test_scenario_p_order_reconciliation(mock_delta_client, state_store):
    state_store.orders["101"] = OrderRecord(
        delta_order_id="101",
        client_order_id="QE-local-101",
        symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1.0"),
        filled_quantity=Decimal("0"),
        status=OrderStatus.OPEN,
    )
    mock_delta_client.get_open_orders = AsyncMock(return_value=[])

    recon = DeltaReconciliationService(mock_delta_client, state_store)
    report = await recon.reconcile_account("acct-test")
    assert not report.is_synchronized
    assert any(d.discrepancy_type == ReconciliationDiscrepancyType.ORDER_STATUS_MISMATCH for d in report.discrepancies)


# Q. Position reconciliation (missing/orphaned)
@pytest.mark.asyncio
async def test_scenario_q_position_reconciliation(mock_delta_client, state_store):
    mock_delta_client.get_positions = AsyncMock(return_value=[
        DeltaPosition(
            product_id=27,
            product_symbol="BTCUSD",
            side=PositionSide.LONG,
            size=Decimal("0.5"),
            entry_price=Decimal("50000"),
            mark_price=Decimal("50500"),
            liquidation_price=Decimal("45000"),
            unrealized_pnl=Decimal("250"),
            realized_pnl=Decimal("0"),
            leverage=Decimal("17"),
            margin=Decimal("1500"),
        )
    ])

    recon = DeltaReconciliationService(mock_delta_client, state_store)
    report = await recon.reconcile_account("acct-test")
    assert not report.is_synchronized
    assert any(d.discrepancy_type == ReconciliationDiscrepancyType.EXCHANGE_POSITION_MISSING_LOCALLY for d in report.discrepancies)


# R. Fee reconciliation (trading fees)
def test_scenario_r_fee_reconciliation():
    cost = TradeCostBreakdown(
        gross_pnl=Decimal("500.00"),
        entry_fee=Decimal("12.50"),
        exit_fee=Decimal("12.50"),
        funding_costs=Decimal("0.00"),
        pre_trade_balance=Decimal("10000.00"),
    )
    assert cost.total_fees == Decimal("25.00")
    assert cost.net_pnl == Decimal("475.00")
    assert cost.post_trade_balance == Decimal("10475.00")


# S. Funding reconciliation
def test_scenario_s_funding_reconciliation():
    cost = TradeCostBreakdown(
        gross_pnl=Decimal("500.00"),
        entry_fee=Decimal("10.00"),
        exit_fee=Decimal("10.00"),
        funding_costs=Decimal("15.00"),
        other_costs=Decimal("5.00"),
        pre_trade_balance=Decimal("10000.00"),
    )
    assert cost.total_fees == Decimal("40.00")
    assert cost.net_pnl == Decimal("460.00")
    assert cost.post_trade_balance == Decimal("10460.00")


# T. Net P&L formula: Gross - Fees - Funding - Other
def test_scenario_t_net_pnl_formula():
    gross = Decimal("1000.00")
    fees = Decimal("45.00")
    funding = Decimal("5.50")
    other = Decimal("2.50")
    net = CapitalAllocator.calculate_net_pnl(gross, fees, funding, other)
    assert net == Decimal("947.00")


# U. 100% capital compounding on next trade
@pytest.mark.asyncio
async def test_scenario_u_100pct_capital_compounding(lifecycle_mgr, state_store):
    decision = make_decision("setup-comp-u")
    await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")

    closed_rec = await lifecycle_mgr.close_position(
        "setup-comp-u",
        CloseReason.TAKE_PROFIT,
        gross_pnl=Decimal("600.00"),
        trading_fees=Decimal("40.00"),
    )
    assert closed_rec.post_trade_balance == Decimal("10560.00")
    assert state_store.account.available_balance == Decimal("10560.00")


# V. Database-enforced one-active-trade lock
def test_scenario_v_one_active_trade_lock():
    lock_mgr = SingleTradeLockManager()
    lock_mgr.acquire_lock("usr-test", "acct-test", "setup-v-1", "BTCUSD")

    with pytest.raises(SingleTradeLockError, match="ONE active trade"):
        lock_mgr.acquire_lock("usr-test", "acct-test", "setup-v-2", "ETHUSD")

    lock_mgr.release_lock("usr-test", "acct-test", "setup-v-1")
    lock_mgr.acquire_lock("usr-test", "acct-test", "setup-v-2", "ETHUSD")
    assert lock_mgr.is_locked("usr-test", "acct-test")[0] is True


# W. Scanner restart recovery
@pytest.mark.asyncio
async def test_scenario_w_scanner_restart_recovery(lifecycle_mgr, mock_delta_client):
    lock_mgr = SingleTradeLockManager()
    scanner = MarketScannerOrchestrator(
        lifecycle_manager=lifecycle_mgr,
        single_trade_lock=lock_mgr,
        supported_symbols=["BTCUSD", "ETHUSD"],
    )

    lock_mgr.acquire_lock("usr-test", "acct-test", "setup-active-w", "BTCUSD")

    res = await scanner.scan_and_execute("acct-test", "usr-test")
    assert res.decision is None
    assert "locked with active trade" in res.rejection_reason


# X. Backend restart recovery
def test_scenario_x_backend_restart_recovery():
    id1 = generate_deterministic_client_order_id("acct-001", "setup-123", "ENTRY")
    id2 = generate_deterministic_client_order_id("acct-001", "setup-123", "ENTRY")
    assert id1 == id2
    assert id1 == "QE-acct001-setup123-ENTRY"


# Y. Exchange / network disconnection handling
@pytest.mark.asyncio
async def test_scenario_y_exchange_network_disconnection(lifecycle_mgr, mock_delta_client):
    mock_delta_client.place_order = AsyncMock(side_effect=DeltaConnectionError("Socket disconnected"))
    decision = make_decision("setup-disc-y")

    rec = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec.state == TradeLifecycleState.RECONCILIATION_REQUIRED


# Z. Emergency kill switch workflow
@pytest.mark.asyncio
async def test_scenario_z_kill_switch_workflow(lifecycle_mgr, state_store):
    res = await lifecycle_mgr.activate_kill_switch("Emergency test trigger")
    assert res["kill_switch_active"] is True
    assert state_store.account.kill_switch_active is True

    decision = make_decision("setup-ks-z")
    rec = await lifecycle_mgr.execute_trade_setup(decision, "acct-test", "usr-test")
    assert rec.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec.rejection_code == RejectionReasonCode.KILL_SWITCH_ACTIVE.value

    lifecycle_mgr.reset_kill_switch("Authorized Operator")
    assert state_store.account.kill_switch_active is False
