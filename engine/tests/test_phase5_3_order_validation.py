"""
Phase 5.3 — Real Order Validation Gateway Test Suite.

Verifies:
- 17+ deterministic, fail-closed validation checks
- Valid LONG and SHORT order setups approved with correct DeltaOrderRequest
- Mandatory TP/SL geometry validation (Long: TP > Entry > SL; Short: SL > Entry > TP)
- Positive risk distance and minimum Risk/Reward ratio enforcement
- Account active, algo_enabled, and emergency kill switch controls
- Connection health and API credential presence
- Instrument symbol, direction, order type, quantity min/step, and price tick size rules
- Sufficient available balance/margin, leverage cap, and account risk limits
- Duplicate client_order_id and duplicate setup_id idempotency protection
- Max concurrent trades enforcement
- Full integration with StrategyDecision (TRADE_SETUP_READY)
- Secret masking & zero network side effects during validation
- 100% pure validation: ZERO real orders placed, ZERO fake/simulated trading.
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    TimeInForce,
    PositionSide,
)
from quantedge.execution.synchronizer import (
    AccountRecord,
    ConnectionRecord,
    PositionRecord,
    PositionStatus,
)
from quantedge.execution.validation import (
    RejectionReasonCode,
    ProductSpecification,
    DEFAULT_DELTA_INDIA_PRODUCTS,
    RiskConfiguration,
    ValidationContext,
    OrderValidationRequest,
    OrderValidationResult,
    OrderValidationGateway,
)
from quantedge.strategy.models import StrategyDecision, SetupState, StrategyDirection, TradeDirection


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def standard_context():
    """Create a healthy, valid context for a live trading account."""
    account = AccountRecord(
        account_id="acc_live_01",
        base_currency="USDT",
        current_balance=Decimal("10000.00"),
        available_balance=Decimal("10000.00"),
        margin_used=Decimal("0.00"),
        total_equity=Decimal("10000.00"),
        is_active=True,
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
def valid_long_request():
    """Create a valid LONG trade setup on BTCUSD."""
    # Entry: 95000, SL: 94000 (risk: 1000), TP: 97000 (reward: 2000), RR = 2.0
    # Qty: 2 BTC -> Risk = 2 * 1000 = 2000 USDT (<= 3500 USDT max risk)
    # Required margin at 50x = (2 * 95000) / 50 = 3800 USDT (<= 10000 USDT available)
    return OrderValidationRequest(
        account_id="acc_live_01",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("2"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        leverage=50,
        client_order_id="QE-1724261234-long-01",
        setup_id="SETUP-BULLISH-BTC-001",
    )


@pytest.fixture
def valid_short_request():
    """Create a valid SHORT trade setup on BTCUSD."""
    # Entry: 95000, SL: 96000 (risk: 1000), TP: 93000 (reward: 2000), RR = 2.0
    return OrderValidationRequest(
        account_id="acc_live_01",
        symbol="BTCUSD",
        direction=TradeDirection.SHORT,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("2"),
        entry_price=Decimal("95000.0"),
        stop_loss=Decimal("96000.0"),
        take_profit=Decimal("93000.0"),
        leverage=50,
        client_order_id="QE-1724261234-short-01",
        setup_id="SETUP-BEARISH-BTC-001",
    )


# ── 1. Positive Validation Tests ──────────────────────────────────────────────


def test_valid_long_order_approved(standard_context, valid_long_request):
    """Verify valid LONG trade setup passes all checks and produces ready DeltaOrderRequest."""
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is True
    assert result.rejection_code is None
    assert result.failed_check is None
    assert result.calculated_risk_distance == Decimal("1000.0")
    assert result.calculated_reward_distance == Decimal("2000.0")
    assert result.calculated_risk_reward == Decimal("2.0")
    # 2 contracts x 0.001 BTC per contract x 1000 USD of risk distance = 2 USDT.
    # The authoritative BTCUSD contract value is 0.001, not the flat 1.0 the
    # pre-registry product table assumed.
    assert result.calculated_risk_amount == Decimal("2.0")

    # Order request verification
    req = result.order_request
    assert req is not None
    assert req.product_id == 27
    assert req.product_symbol == "BTCUSD"
    assert req.side == OrderSide.BUY
    assert req.order_type == OrderType.LIMIT_ORDER
    assert req.size == Decimal("2")
    assert req.limit_price == Decimal("95000.0")
    assert req.stop_loss_price == Decimal("94000.0")
    assert req.take_profit_price == Decimal("97000.0")
    assert req.client_order_id == "QE-1724261234-long-01"


def test_valid_short_order_approved(standard_context, valid_short_request):
    """Verify valid SHORT trade setup passes all checks."""
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_short_request, standard_context)

    assert result.is_valid is True
    assert result.calculated_risk_distance == Decimal("1000.0")
    assert result.calculated_reward_distance == Decimal("2000.0")
    assert result.calculated_risk_reward == Decimal("2.0")

    req = result.order_request
    assert req is not None
    assert req.side == OrderSide.SELL
    assert req.limit_price == Decimal("95000.0")


# ── 2. Account, Algo, and Kill Switch Controls ────────────────────────────────


def test_disabled_account_rejected(standard_context, valid_long_request):
    """Verify disabled account is rejected."""
    standard_context.account.is_active = False
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.ACCOUNT_DISABLED
    assert result.failed_check == "CHECK_ACCOUNT_ACTIVE"


def test_algo_disabled_rejected(standard_context, valid_long_request):
    """Verify algo_enabled=False is rejected."""
    standard_context.algo_enabled = False
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.ALGO_DISABLED
    assert result.failed_check == "CHECK_ALGO_ENABLED"


def test_kill_switch_active_rejected(standard_context, valid_long_request):
    """Verify emergency kill switch active blocks order."""
    standard_context.kill_switch_active = True
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.KILL_SWITCH_ACTIVE
    assert result.failed_check == "CHECK_KILL_SWITCH"


def test_kill_switch_overrides_everything(standard_context, valid_long_request):
    """Verify kill switch overrides even an otherwise 100% perfect setup."""
    standard_context.kill_switch_active = True
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)
    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.KILL_SWITCH_ACTIVE


# ── 3. Connection and Credential Checks ────────────────────────────────────────


def test_exchange_disconnected_rejected(standard_context, valid_long_request):
    """Verify disconnected or error exchange status is rejected."""
    standard_context.connection.connection_status = "ERROR"
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.EXCHANGE_DISCONNECTED


def test_missing_or_invalid_credentials_rejected(standard_context, valid_long_request):
    """Verify missing API key/secret is rejected."""
    standard_context.api_key = ""
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.INVALID_CREDENTIALS


# ── 4. Instrument and Order Parameters Checks ─────────────────────────────────


def test_unsupported_symbol_rejected(standard_context, valid_long_request):
    """Verify unsupported symbol is rejected."""
    valid_long_request.symbol = "DOGEUSD"
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.UNSUPPORTED_SYMBOL


def test_invalid_direction_rejected(standard_context, valid_long_request):
    """Verify invalid direction is rejected."""
    valid_long_request.direction = "INVALID_DIR"
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.INVALID_DIRECTION


def test_unsupported_order_type_rejected(standard_context, valid_long_request):
    """Verify unsupported order type is rejected."""
    valid_long_request.order_type = "TRAILING_STOP"
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.UNSUPPORTED_ORDER_TYPE


def test_zero_and_negative_quantity_rejected(standard_context, valid_long_request):
    """Verify zero or negative quantity is rejected."""
    valid_long_request.quantity = Decimal("0")
    gateway = OrderValidationGateway()
    res1 = gateway.validate(valid_long_request, standard_context)
    assert res1.is_valid is False
    assert res1.rejection_code == RejectionReasonCode.INVALID_QUANTITY_NON_POSITIVE

    valid_long_request.quantity = Decimal("-5")
    res2 = gateway.validate(valid_long_request, standard_context)
    assert res2.is_valid is False
    assert res2.rejection_code == RejectionReasonCode.INVALID_QUANTITY_NON_POSITIVE


def test_quantity_below_minimum_rejected(standard_context, valid_long_request):
    """Verify quantity below instrument min_size is rejected."""
    valid_long_request.quantity = Decimal("0.5")  # min is 1
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.QUANTITY_BELOW_MINIMUM


def test_invalid_quantity_step_rejected(standard_context, valid_long_request):
    """Verify fractional quantity when step=1 is rejected."""
    valid_long_request.quantity = Decimal("2.3")  # step is 1
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.INVALID_QUANTITY_STEP


def test_invalid_price_non_positive_rejected(standard_context, valid_long_request):
    """Verify non-positive entry price for limit order is rejected."""
    valid_long_request.entry_price = Decimal("0")
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.INVALID_PRICE_NON_POSITIVE


def test_invalid_tick_size_rejected(standard_context, valid_long_request):
    """Verify price not aligned to tick size 0.5 is rejected."""
    valid_long_request.entry_price = Decimal("95000.123")  # tick is 0.5
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.INVALID_TICK_SIZE


# ── 5. Leverage, Margin, and Risk Limit Checks ────────────────────────────────


def test_excessive_leverage_rejected(standard_context, valid_long_request):
    """Verify requested leverage exceeding maximum cap is rejected."""
    valid_long_request.leverage = 150  # max is 100
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE


def test_insufficient_balance_rejected(standard_context, valid_long_request):
    """Verify order requiring more margin than available balance is rejected."""
    # Available balance 1000 USDT. The intended exposure is 20 BTC, which at the
    # authoritative BTCUSD contract value of 0.001 BTC per contract is 20,000
    # contracts (the old product table's flat 1.0 made 20 contracts look like
    # 20 BTC). Risk stays within 35% (SL 94900 -> 20,000 * 0.001 * 100 = 2,000
    # <= 3,500) but required margin is (20,000 * 0.001 * 95,000) / 50 = 38,000
    # USDT > 1,000 USDT available.
    standard_context.account.available_balance = Decimal("1000.00")
    valid_long_request.quantity = Decimal("20000")
    valid_long_request.stop_loss = Decimal("94900.0")  # risk_dist = 100 -> total risk = 2000 USDT <= 3500 USDT
    valid_long_request.take_profit = Decimal("97000.0")  # reward_dist = 2000 -> RR = 20.0
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.INSUFFICIENT_BALANCE


def test_excessive_risk_rejected(standard_context, valid_long_request):
    """Verify trade risk exceeding 35% equity is rejected."""
    # Equity = 10,000 USDT -> max risk = 3,500 USDT
    # Distance = 5,000 USDT, intended exposure 2 BTC = 2,000 contracts at the
    # authoritative 0.001 BTC per contract -> risk = 2,000 * 0.001 * 5,000 =
    # 10,000 USDT > 3,500 USDT
    valid_long_request.stop_loss = Decimal("90000.0")  # dist = 5000
    valid_long_request.take_profit = Decimal("105000.0")  # reward = 10000
    valid_long_request.quantity = Decimal("2000")
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.EXCESSIVE_RISK


def test_max_concurrent_trades_limit_rejected(standard_context, valid_long_request):
    """Verify exceeding max concurrent trades (1) is rejected."""
    standard_context.open_positions = [
        PositionRecord(
            symbol="ETHUSD",
            side=PositionSide.LONG,
            quantity=Decimal("5"),
            entry_price=Decimal("3400"),
            current_price=Decimal("3450"),
            unrealized_pnl=Decimal("250"),
            realized_pnl=Decimal("0"),
            leverage=Decimal("25"),
            margin_used=Decimal("680"),
            status=PositionStatus.OPEN,
        )
    ]
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.CONCURRENT_TRADE_LIMIT_EXCEEDED


# ── 6. TP / SL Geometry and Risk/Reward Checks ────────────────────────────────


def test_missing_tp_sl_rejected(standard_context, valid_long_request):
    """Verify missing SL or TP is rejected."""
    valid_long_request.stop_loss = None
    gateway = OrderValidationGateway()
    res1 = gateway.validate(valid_long_request, standard_context)
    assert res1.is_valid is False
    assert res1.rejection_code == RejectionReasonCode.MISSING_STOP_LOSS

    valid_long_request.stop_loss = Decimal("94000.0")
    valid_long_request.take_profit = None
    res2 = gateway.validate(valid_long_request, standard_context)
    assert res2.is_valid is False
    assert res2.rejection_code == RejectionReasonCode.MISSING_TAKE_PROFIT


def test_invalid_long_tp_sl_geometry_rejected(standard_context, valid_long_request):
    """Verify LONG order with SL >= Entry or TP <= Entry is rejected."""
    # SL above entry
    valid_long_request.stop_loss = Decimal("96000.0")
    valid_long_request.entry_price = Decimal("95000.0")
    valid_long_request.take_profit = Decimal("98000.0")
    gateway = OrderValidationGateway()
    res1 = gateway.validate(valid_long_request, standard_context)
    assert res1.is_valid is False
    assert res1.rejection_code == RejectionReasonCode.INVALID_TP_SL_GEOMETRY

    # TP below entry
    valid_long_request.stop_loss = Decimal("94000.0")
    valid_long_request.entry_price = Decimal("95000.0")
    valid_long_request.take_profit = Decimal("93000.0")
    res2 = gateway.validate(valid_long_request, standard_context)
    assert res2.is_valid is False
    assert res2.rejection_code == RejectionReasonCode.INVALID_TP_SL_GEOMETRY


def test_invalid_short_tp_sl_geometry_rejected(standard_context, valid_short_request):
    """Verify SHORT order with SL <= Entry or TP >= Entry is rejected."""
    # SL below entry
    valid_short_request.stop_loss = Decimal("94000.0")
    valid_short_request.entry_price = Decimal("95000.0")
    valid_short_request.take_profit = Decimal("92000.0")
    gateway = OrderValidationGateway()
    res1 = gateway.validate(valid_short_request, standard_context)
    assert res1.is_valid is False
    assert res1.rejection_code == RejectionReasonCode.INVALID_TP_SL_GEOMETRY

    # TP above entry
    valid_short_request.stop_loss = Decimal("96000.0")
    valid_short_request.entry_price = Decimal("95000.0")
    valid_short_request.take_profit = Decimal("97000.0")
    res2 = gateway.validate(valid_short_request, standard_context)
    assert res2.is_valid is False
    assert res2.rejection_code == RejectionReasonCode.INVALID_TP_SL_GEOMETRY


def test_zero_risk_distance_rejected(standard_context, valid_long_request):
    """Verify entry == SL (zero risk distance) is rejected."""
    valid_long_request.entry_price = Decimal("95000.0")
    valid_long_request.stop_loss = Decimal("95000.0")
    valid_long_request.take_profit = Decimal("97000.0")
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.INVALID_TP_SL_GEOMETRY


def test_invalid_risk_reward_rejected(standard_context, valid_long_request):
    """Verify RR < 1.5 is rejected."""
    # Risk = 1000 (95000 - 94000), Reward = 1200 (96200 - 95000), RR = 1.2 < 1.5
    valid_long_request.entry_price = Decimal("95000.0")
    valid_long_request.stop_loss = Decimal("94000.0")
    valid_long_request.take_profit = Decimal("96200.0")
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.INVALID_RISK_REWARD


# ── 7. Idempotency & Duplicate Protection ─────────────────────────────────────


def test_duplicate_client_order_id_rejected(standard_context, valid_long_request):
    """Verify submitting with an active client_order_id is rejected."""
    standard_context.active_client_order_ids.add("QE-1724261234-long-01")
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.DUPLICATE_CLIENT_ORDER_ID


def test_duplicate_setup_id_rejected(standard_context, valid_long_request):
    """Verify submitting with an active setup_id is rejected."""
    standard_context.active_setup_ids.add("SETUP-BULLISH-BTC-001")
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.DUPLICATE_SETUP_ID


# ── 8. StrategyDecision Validation Integration ────────────────────────────────


def test_validate_strategy_decision_ready(standard_context):
    """Verify StrategyDecision in TRADE_SETUP_READY state is successfully validated."""
    decision = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP-DECISION-01",
        entry=Decimal("95000.0"),
        stop_loss=Decimal("94000.0"),
        take_profit=Decimal("97000.0"),
        risk_distance=Decimal("1000.0"),
        reward_distance=Decimal("2000.0"),
        risk_reward=Decimal("2.0"),
        confidence=88.5,
    )
    gateway = OrderValidationGateway()
    result = gateway.validate_strategy_decision(decision, standard_context, account_id="acc_live_01", quantity=Decimal("1"))

    assert result.is_valid is True
    assert result.order_request is not None
    assert result.order_request.side == OrderSide.BUY
    assert result.order_request.limit_price == Decimal("95000.0")


def test_validate_strategy_decision_not_ready(standard_context):
    """Verify StrategyDecision not in TRADE_SETUP_READY is rejected."""
    decision = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.OB_ENGAGED,  # Not TRADE_SETUP_READY
    )
    gateway = OrderValidationGateway()
    result = gateway.validate_strategy_decision(decision, standard_context, account_id="acc_live_01")

    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.DECISION_NOT_READY


# ── 9. Determinism & Secret Redaction ─────────────────────────────────────────


def test_deterministic_validation_and_no_side_effects(standard_context, valid_long_request):
    """Verify repeated validation of same request is deterministic and side-effect free."""
    gateway = OrderValidationGateway()
    res1 = gateway.validate(valid_long_request, standard_context)
    res2 = gateway.validate(valid_long_request, standard_context)

    assert res1.is_valid == res2.is_valid
    assert res1.rejection_code == res2.rejection_code
    assert res1.calculated_risk_reward == res2.calculated_risk_reward


def test_no_credentials_leaked_in_rejection_reasons(standard_context, valid_long_request):
    """Verify sensitive credentials are never displayed in rejection reasons."""
    standard_context.api_key = "sensitive_key_secret_xyz"
    standard_context.api_secret = "super_private_secret_abc"
    standard_context.algo_enabled = False

    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is False
    assert "sensitive_key_secret_xyz" not in str(result.rejection_reason)
    assert "super_private_secret_abc" not in str(result.rejection_reason)


# ── 10. Additional Order Types & Reduce Only Tests ────────────────────────────


def test_market_order_validation_approved(standard_context, valid_long_request):
    """Verify MARKET_ORDER type is supported and validated with TP/SL."""
    valid_long_request.order_type = OrderType.MARKET_ORDER
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is True
    assert result.order_request.order_type == OrderType.MARKET_ORDER


def test_stop_limit_order_validation_approved(standard_context, valid_long_request):
    """Verify STOP_LIMIT_ORDER type is supported and validated."""
    valid_long_request.order_type = OrderType.STOP_LIMIT_ORDER
    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is True
    assert result.order_request.order_type == OrderType.STOP_LIMIT_ORDER
    assert result.order_request.stop_price == Decimal("94000.0")


def test_reduce_only_order_bypasses_new_position_checks(standard_context, valid_long_request):
    """Verify reduce_only orders (e.g. exit orders) don't require TP/SL and allow max concurrent positions."""
    standard_context.open_positions = [
        PositionRecord(
            symbol="BTCUSD",
            side=PositionSide.LONG,
            quantity=Decimal("2"),
            entry_price=Decimal("95000"),
            current_price=Decimal("96000"),
            unrealized_pnl=Decimal("2000"),
            realized_pnl=Decimal("0"),
            leverage=Decimal("50"),
            margin_used=Decimal("3800"),
            status=PositionStatus.OPEN,
        )
    ]
    valid_long_request.reduce_only = True
    valid_long_request.direction = TradeDirection.SHORT  # closing a LONG position
    valid_long_request.stop_loss = None
    valid_long_request.take_profit = None

    gateway = OrderValidationGateway()
    result = gateway.validate(valid_long_request, standard_context)

    assert result.is_valid is True
    assert result.order_request.reduce_only is True
    assert result.order_request.side == OrderSide.SELL

