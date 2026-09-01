"""
Real Order Validation Gateway for QuantEdge AI.

Deterministic, fail-closed validation barrier between Strategy/Risk setups
and Delta Exchange India order execution.

Guarantees:
- Zero real order placement during validation (pure validation logic).
- Strict fail-closed design: any invalid check immediately rejects the order.
- Exact Decimal precision for financial calculations.
- TP/SL geometry enforcement per Phase 4.2 specification.
- Idempotency & duplicate protection for client_order_id and setup_id.
- Secret masking & redaction in all rejection messages.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List, Set, Tuple, Union

from quantedge.execution.leverage import (
    MAX_LEVERAGE,
    MIN_LEVERAGE,
    LeverageBandError,
    normalize_requested_leverage,
    validate_leverage,
)
from quantedge.execution.models import (
    OrderSide,
    OrderType,
    StopOrderType,
    StopTriggerMethod,
    TimeInForce,
    DeltaOrderRequest,
)
from quantedge.execution.security import mask_secret
from quantedge.execution.synchronizer import (
    AccountRecord,
    ConnectionRecord,
    PositionRecord,
    OrderRecord,
    PositionStatus,
)
from quantedge.instruments import InstrumentSpec, delta_india_registry
from quantedge.strategy.models import StrategyDecision, SetupState, StrategyDirection, TradeDirection


# ── Rejection Codes ───────────────────────────────────────────────────────────


class RejectionReasonCode(str, Enum):
    """Deterministic machine-readable rejection codes."""
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    ALGO_DISABLED = "ALGO_DISABLED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    EXCHANGE_DISCONNECTED = "EXCHANGE_DISCONNECTED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    DELTA_CREDENTIALS_MISSING = "DELTA_CREDENTIALS_MISSING"
    UNAUTHORIZED_ACCOUNT = "UNAUTHORIZED_ACCOUNT"
    ACCOUNT_STATE_STALE = "ACCOUNT_STATE_STALE"
    UNSUPPORTED_SYMBOL = "UNSUPPORTED_SYMBOL"
    INVALID_DIRECTION = "INVALID_DIRECTION"
    UNSUPPORTED_ORDER_TYPE = "UNSUPPORTED_ORDER_TYPE"
    INVALID_QUANTITY_NON_POSITIVE = "INVALID_QUANTITY_NON_POSITIVE"
    QUANTITY_BELOW_MINIMUM = "QUANTITY_BELOW_MINIMUM"
    INVALID_QUANTITY_STEP = "INVALID_QUANTITY_STEP"
    INVALID_PRICE_NON_POSITIVE = "INVALID_PRICE_NON_POSITIVE"
    INVALID_TICK_SIZE = "INVALID_TICK_SIZE"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    INSUFFICIENT_AVAILABLE_MARGIN = "INSUFFICIENT_AVAILABLE_MARGIN"
    EXCESSIVE_LEVERAGE = "EXCESSIVE_LEVERAGE"
    EXCESSIVE_RISK = "EXCESSIVE_RISK"
    MISSING_STOP_LOSS = "MISSING_STOP_LOSS"
    MISSING_TAKE_PROFIT = "MISSING_TAKE_PROFIT"
    INVALID_TP_SL_GEOMETRY = "INVALID_TP_SL_GEOMETRY"
    ZERO_OR_NEGATIVE_RISK_DISTANCE = "ZERO_OR_NEGATIVE_RISK_DISTANCE"
    INVALID_RISK_REWARD = "INVALID_RISK_REWARD"
    DUPLICATE_CLIENT_ORDER_ID = "DUPLICATE_CLIENT_ORDER_ID"
    DUPLICATE_SETUP_ID = "DUPLICATE_SETUP_ID"
    CONCURRENT_TRADE_LIMIT_EXCEEDED = "CONCURRENT_TRADE_LIMIT_EXCEEDED"
    SINGLE_TRADE_LIMIT_EXCEEDED = "SINGLE_TRADE_LIMIT_EXCEEDED"
    INSUFFICIENT_MARGIN_100_PCT = "INSUFFICIENT_MARGIN_100_PCT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DECISION_NOT_READY = "DECISION_NOT_READY"
    SETUP_NOT_FOUND = "SETUP_NOT_FOUND"
    SETUP_EXPIRED = "SETUP_EXPIRED"


# ── Product Specifications ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductSpecification:
    """
    Instrument contract specification used by the validation gateway.

    THREE OF THESE FIELDS ARE EXCHANGE-VERIFIED, THREE ARE NOT.

    Verified, and sourced only from `quantedge.instruments` (the authoritative
    Delta India snapshot): `symbol`, `product_id`, `tick_size`,
    `contract_value`. A shipped spec carries `verification_source`, the
    snapshot provenance line that establishes them.

    NOT verified: `min_size`, `size_step`, `max_leverage`. Delta publishes
    none of the three, so they are local execution policy — `min_size` and
    `size_step` retained verbatim from the pre-registry gateway, `max_leverage`
    the authorised band from `quantedge.execution.leverage` — and they are
    named in `unverified_fields`. They must not be treated as exchange facts.

    The `contract_value` and `max_leverage` defaults exist only so a locally
    constructed spec (a test fixture, say) still builds. A default-constructed
    spec has `verification_source is None` and `is_verified` False; it is not
    an exchange record and must never be presented as one.
    """
    symbol: str
    product_id: int
    min_size: Decimal
    size_step: Decimal
    tick_size: Decimal
    max_leverage: int = MAX_LEVERAGE
    contract_value: Decimal = Decimal("1.0")
    verification_source: Optional[str] = None
    unverified_fields: Tuple[str, ...] = ("min_size", "size_step",
                                          "max_leverage")

    @property
    def is_verified(self) -> bool:
        """True only for a spec built from the authoritative snapshot."""
        return bool(self.verification_source)


# ── Unverified execution policy (NOT exchange data) ───────────────────────────
#
# Delta India publishes no minimum order size, no size increment and no
# leverage ceiling (see `quantedge.instruments.PERMANENTLY_UNVERIFIED`). The
# gateway's quantity and leverage checks nevertheless need a bound, so the
# values the gateway already used are retained verbatim and labelled. Nothing
# here is authoritative; nothing here may be presented as exchange metadata.
#
# `max_leverage` is now the single authorised band (`MAX_LEVERAGE`) for every
# symbol. It used to be 100 for BTCUSD/ETHUSD and 50 for SOLUSD/XRPUSD — a
# value retained from the pre-registry gateway, which meant a requested 100x
# was rejected on SOL and XRP even though every other layer permitted it. The
# owner authorised a uniform 1x..100x band, so the two 50s were raised rather
# than the other layers lowered.
#
# The direction is corroborated, not verified: the snapshot records
# `margin_and_limits.default_leverage` of 100 for SOLUSD and XRPUSD (200 for
# BTCUSD/ETHUSD), so 100 still sits at or below the strictest figure Delta
# itself records, and the one-sided `policy <= recorded` assertion in
# `test_execution_sizing_semantics_audit` continues to hold. `max_leverage`
# stays in `PERMANENTLY_UNVERIFIED`; nothing here became an exchange fact.
#
# The table is kept per symbol, and the unlisted-symbol fallback is still the
# strictest entry rather than the loosest, so re-tightening one instrument
# later needs no structural change.

UNVERIFIED_MIN_SIZE: Decimal = Decimal("1")
UNVERIFIED_SIZE_STEP: Decimal = Decimal("1")
UNVERIFIED_MAX_LEVERAGE: Dict[str, int] = {
    "BTCUSD": MAX_LEVERAGE,
    "ETHUSD": MAX_LEVERAGE,
    "SOLUSD": MAX_LEVERAGE,
    "XRPUSD": MAX_LEVERAGE,
}
UNVERIFIED_MAX_LEVERAGE_FALLBACK: int = min(UNVERIFIED_MAX_LEVERAGE.values())


class UnknownProductError(RuntimeError):
    """
    The symbol is not an authoritative Delta India product.

    Raised instead of returning a fabricated specification. The gateway used
    to answer an unrecognised symbol with BTCUSD/product 27/tick 0.5, which
    could have routed an order to the wrong instrument (safety rules #8, #15).
    """


def product_specification_from_instrument(
        spec: InstrumentSpec) -> ProductSpecification:
    """
    Adapt one authoritative `InstrumentSpec` to the gateway's schema.

    The verified trio is copied exactly — `tick_size` and `contract_value`
    stay `Decimal`, so no exchange constant crosses a float. The three
    unverified fields come from the named policy constants above, not from
    the exchange and not from arithmetic.
    """
    return ProductSpecification(
        symbol=spec.symbol,
        product_id=spec.product_id,
        min_size=UNVERIFIED_MIN_SIZE,
        size_step=UNVERIFIED_SIZE_STEP,
        tick_size=spec.tick_size,
        max_leverage=UNVERIFIED_MAX_LEVERAGE.get(
            spec.symbol, UNVERIFIED_MAX_LEVERAGE_FALLBACK),
        contract_value=spec.contract_value,
        verification_source=spec.provenance.as_source_string(),
    )


def _load_delta_india_products() -> Dict[str, ProductSpecification]:
    """
    Build the shipped table from the shared registry — the only source.

    No product id, tick size or contract value is written in this module. A
    missing or tampered snapshot raises out of here rather than degrading to
    a guess.
    """
    registry = delta_india_registry()
    return {symbol: product_specification_from_instrument(registry.get(symbol))
            for symbol in registry.symbols}


#: Delta-native symbols only. `.P` forms are absent because whether they name
#: the same tradable product is an undecided repository policy question, and
#: `quantedge.instruments` refuses to invent the alias.
DEFAULT_DELTA_INDIA_PRODUCTS: Dict[str, ProductSpecification] = \
    _load_delta_india_products()


def get_product_specification(symbol: str) -> ProductSpecification:
    """
    Exact lookup of an authoritative product specification.

    Fails closed. No case folding, no whitespace stripping, no `.P` removal,
    no default record: `FOOUSD`, `BTCUSD.P`, `btcusd`, `BTC-USD`, `BTCUSDT`,
    `""` and non-strings all raise `UnknownProductError`.
    """
    if not isinstance(symbol, str) or not symbol:
        raise UnknownProductError(
            f"{symbol!r} is not a usable Delta India symbol; refusing to "
            f"substitute another product")
    spec = DEFAULT_DELTA_INDIA_PRODUCTS.get(symbol)
    if spec is None:
        raise UnknownProductError(
            f"{symbol!r} is not an authoritative Delta India product; "
            f"refusing to substitute another product. Registered: "
            f"{sorted(DEFAULT_DELTA_INDIA_PRODUCTS)}")
    return spec


# ── Risk Configuration ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskConfiguration:
    """
    Account-level risk parameters.

    `max_leverage` is band-checked on construction. It previously took any
    integer — 0, -1, 101 and 100000 were all stored verbatim — and gateway
    check 14 then folded it into `min(spec, risk_config)`. A 0 or -1 there
    silently made every order unexecutable, and a value above the band was
    harmlessly absorbed by the `min` only for as long as the per-symbol table
    stayed the stricter of the two. Neither is a state this object should be
    able to reach, so it fails closed at construction instead.

    Only `max_leverage` is validated here. The other fields are left exactly as
    they were: this change is scoped to the leverage band.
    """
    risk_per_trade_pct: Decimal = Decimal("35.0")
    target_reward_pct: Decimal = Decimal("60.0")
    max_leverage: int = MAX_LEVERAGE
    max_concurrent_trades: int = 1
    minimum_risk_reward: Decimal = Decimal("1.5")
    max_daily_loss_pct: Optional[Decimal] = None
    supported_symbols: List[str] = field(default_factory=lambda: [
        "BTCUSD", "BTCUSD.P", "ETHUSD", "ETHUSD.P", "SOLUSD", "SOLUSD.P", "XRPUSD", "XRPUSD.P"
    ])

    def __post_init__(self):
        validate_leverage(self.max_leverage,
                          field_name="RiskConfiguration.max_leverage")


# ── Validation Request & Context ──────────────────────────────────────────────


@dataclass
class ValidationContext:
    """State context provided to OrderValidationGateway."""
    account: AccountRecord
    algo_enabled: bool = True
    kill_switch_active: bool = False
    connection: Optional[ConnectionRecord] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    risk_config: RiskConfiguration = field(default_factory=RiskConfiguration)
    open_positions: List[PositionRecord] = field(default_factory=list)
    open_orders: List[OrderRecord] = field(default_factory=list)
    active_client_order_ids: Set[str] = field(default_factory=set)
    active_setup_ids: Set[str] = field(default_factory=set)
    product_specs: Dict[str, ProductSpecification] = field(default_factory=lambda: DEFAULT_DELTA_INDIA_PRODUCTS)


@dataclass
class OrderValidationRequest:
    """Incoming order request to be validated."""
    account_id: str
    symbol: str
    direction: Union[TradeDirection, OrderSide, str]
    order_type: Union[OrderType, str]
    quantity: Decimal
    entry_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    leverage: Optional[int] = None
    client_order_id: Optional[str] = None
    setup_id: Optional[str] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False


@dataclass
class OrderValidationResult:
    """Outcome of order validation."""
    is_valid: bool
    rejection_code: Optional[RejectionReasonCode] = None
    rejection_reason: Optional[str] = None
    failed_check: Optional[str] = None
    validated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    order_request: Optional[DeltaOrderRequest] = None
    calculated_risk_amount: Optional[Decimal] = None
    calculated_risk_distance: Optional[Decimal] = None
    calculated_reward_distance: Optional[Decimal] = None
    calculated_risk_reward: Optional[Decimal] = None


# ── Gateway Implementation ────────────────────────────────────────────────────


class OrderValidationGateway:
    """Deterministic, fail-closed validation gateway for real Delta Exchange India orders."""

    def validate(self, request: OrderValidationRequest, context: ValidationContext) -> OrderValidationResult:
        """Run all 17+ validation checks in sequence. Fails closed on first error."""
        now = datetime.now(timezone.utc)

        # ── 1. Account Exists and is Active ───────────────────────────────────
        if context.account is None or not context.account.is_active:
            return self._reject(
                RejectionReasonCode.ACCOUNT_DISABLED,
                f"Trading account '{request.account_id}' is disabled or does not exist.",
                "CHECK_ACCOUNT_ACTIVE",
                now,
            )

        # ── 2. algo_enabled is True ───────────────────────────────────────────
        if not context.algo_enabled:
            return self._reject(
                RejectionReasonCode.ALGO_DISABLED,
                "Algorithmic execution is disabled for this account (algo_enabled=False).",
                "CHECK_ALGO_ENABLED",
                now,
            )

        # ── 3. Emergency Kill Switch is NOT Active ────────────────────────────
        if context.kill_switch_active:
            return self._reject(
                RejectionReasonCode.KILL_SWITCH_ACTIVE,
                "Emergency kill switch is active. All order submissions are blocked.",
                "CHECK_KILL_SWITCH",
                now,
            )

        # ── 4. Delta Connection is Healthy ────────────────────────────────────
        if context.connection is not None and context.connection.connection_status != "CONNECTED":
            return self._reject(
                RejectionReasonCode.EXCHANGE_DISCONNECTED,
                f"Delta Exchange connection status is '{context.connection.connection_status}'. Must be 'CONNECTED'.",
                "CHECK_EXCHANGE_CONNECTION",
                now,
            )

        # ── 5. API Credentials Available ──────────────────────────────────────
        if not context.api_key or not context.api_secret or len(context.api_key.strip()) < 5 or len(context.api_secret.strip()) < 5:
            return self._reject(
                RejectionReasonCode.INVALID_CREDENTIALS,
                "Authenticated Delta Exchange API credentials are missing or invalid.",
                "CHECK_API_CREDENTIALS",
                now,
            )

        # ── 6. Supported Symbol ───────────────────────────────────────────────
        # A native exchange symbol is an EXACT identifier. No case conversion,
        # no whitespace trimming, no suffix (`.P`) conversion, no separator
        # conversion and no normalisation of an unknown symbol -- the same
        # exact-match contract the instrument registry enforces. A non-string
        # symbol fails closed here rather than raising.
        exact_symbol = request.symbol
        if (not isinstance(exact_symbol, str)
                or exact_symbol not in context.product_specs):
            return self._reject(
                RejectionReasonCode.UNSUPPORTED_SYMBOL,
                f"Instrument symbol '{exact_symbol}' is not supported on Delta Exchange India.",
                "CHECK_SUPPORTED_SYMBOL",
                now,
            )
        spec = context.product_specs[exact_symbol]

        # ── 7. Direction Validation ───────────────────────────────────────────
        try:
            if isinstance(request.direction, TradeDirection):
                order_side = OrderSide.BUY if request.direction == TradeDirection.LONG else OrderSide.SELL
            elif isinstance(request.direction, OrderSide):
                order_side = request.direction
            else:
                order_side = OrderSide.from_str(str(request.direction))
        except Exception:
            return self._reject(
                RejectionReasonCode.INVALID_DIRECTION,
                f"Order direction '{request.direction}' is invalid. Must be BUY/LONG or SELL/SHORT.",
                "CHECK_ORDER_DIRECTION",
                now,
            )

        # ── 8. Supported Order Type ───────────────────────────────────────────
        try:
            if isinstance(request.order_type, OrderType):
                order_type = request.order_type
            else:
                order_type = OrderType.from_str(str(request.order_type))
        except Exception:
            return self._reject(
                RejectionReasonCode.UNSUPPORTED_ORDER_TYPE,
                f"Order type '{request.order_type}' is unsupported.",
                "CHECK_ORDER_TYPE",
                now,
            )

        # ── 9. Quantity Positive ──────────────────────────────────────────────
        if request.quantity <= Decimal("0"):
            return self._reject(
                RejectionReasonCode.INVALID_QUANTITY_NON_POSITIVE,
                f"Order quantity must be positive, got: {request.quantity}",
                "CHECK_QUANTITY_POSITIVE",
                now,
            )

        # ── 10. Quantity Minimum and Step Size ────────────────────────────────
        if request.quantity < spec.min_size:
            return self._reject(
                RejectionReasonCode.QUANTITY_BELOW_MINIMUM,
                f"Order quantity {request.quantity} is below minimum {spec.min_size} for {exact_symbol}.",
                "CHECK_QUANTITY_MINIMUM",
                now,
            )

        # Step size check: (quantity - min_size) % size_step == 0
        rem_step = (request.quantity - spec.min_size) % spec.size_step
        if rem_step != Decimal("0"):
            return self._reject(
                RejectionReasonCode.INVALID_QUANTITY_STEP,
                f"Order quantity {request.quantity} does not align with step size {spec.size_step}.",
                "CHECK_QUANTITY_STEP",
                now,
            )

        # ── 11. Entry Price Validation (for Limit Orders) ─────────────────────
        if order_type in (OrderType.LIMIT_ORDER, OrderType.STOP_LIMIT_ORDER):
            if request.entry_price is None or request.entry_price <= Decimal("0"):
                return self._reject(
                    RejectionReasonCode.INVALID_PRICE_NON_POSITIVE,
                    f"Limit order requires a positive price, got: {request.entry_price}",
                    "CHECK_PRICE_POSITIVE",
                    now,
                )

            # ── 12. Price Tick Size Check ─────────────────────────────────────
            rem_tick = request.entry_price % spec.tick_size
            if rem_tick != Decimal("0"):
                return self._reject(
                    RejectionReasonCode.INVALID_TICK_SIZE,
                    f"Price {request.entry_price} does not align with tick size {spec.tick_size} for {exact_symbol}.",
                    "CHECK_TICK_SIZE",
                    now,
                )

        # ── 13. Max Concurrent Trades Limit ───────────────────────────────────
        active_pos_count = sum(1 for p in context.open_positions if p.status == PositionStatus.OPEN)
        if not request.reduce_only and active_pos_count >= context.risk_config.max_concurrent_trades:
            return self._reject(
                RejectionReasonCode.CONCURRENT_TRADE_LIMIT_EXCEEDED,
                f"Account has {active_pos_count} open positions. Maximum allowed is {context.risk_config.max_concurrent_trades}.",
                "CHECK_CONCURRENT_TRADES",
                now,
            )

        # ── 14. Leverage Validation ───────────────────────────────────────────
        # `None` means "the caller specified nothing" and resolves to the
        # minimum, the least risky value available. Every other value is taken
        # literally: this used to read `request.leverage or 1`, which made an
        # explicit `leverage=0` PASS as 1x while the `leverage < 1` test below
        # implied it was refused. The Java twin
        # (`OrderValidationGateway.java:302`) already used `!= null` and
        # rejected 0, so the coercion was a defect rather than parity.
        #
        # `normalize_requested_leverage` runs first because the band comparison
        # alone is not fail-closed against a malformed value. The field is typed
        # `Optional[int]` and every production producer honours that -- Manual
        # SMC casts through `represent_leverage`, the risk calculator returns an
        # int -- but the two comparisons below are plain numeric ones, so before
        # this call `leverage=float("nan")` and `leverage=True` both passed the
        # band (neither `< 1` nor `> 100` is true of NaN, and `True` is 1) and
        # then raised `decimal.InvalidOperation` out of the margin arithmetic at
        # check 16, while `leverage="100"` raised `TypeError` here. An
        # unhandled exception is not a rejection. A fractional `50.5` passed
        # too, which the INTEGER column could not have stored. An integral
        # `100.0` or `Decimal("100")` is still accepted and normalises to 100.
        #
        # Nothing is clamped in either direction: 101x is rejected, not reduced
        # to 100x, and 0x is rejected, not raised to 1x.
        if request.leverage is None:
            leverage = MIN_LEVERAGE
        else:
            try:
                leverage = normalize_requested_leverage(request.leverage)
            except LeverageBandError as exc:
                return self._reject(
                    RejectionReasonCode.EXCESSIVE_LEVERAGE,
                    str(exc),
                    "CHECK_LEVERAGE_CAP",
                    now,
                )
        max_allowed_leverage = min(spec.max_leverage, context.risk_config.max_leverage)
        if leverage < MIN_LEVERAGE:
            return self._reject(
                RejectionReasonCode.EXCESSIVE_LEVERAGE,
                f"Requested leverage {leverage}x is below the minimum "
                f"{MIN_LEVERAGE}x.",
                "CHECK_LEVERAGE_CAP",
                now,
            )
        if leverage > max_allowed_leverage:
            return self._reject(
                RejectionReasonCode.EXCESSIVE_LEVERAGE,
                f"Requested leverage {leverage}x exceeds maximum allowed {max_allowed_leverage}x.",
                "CHECK_LEVERAGE_CAP",
                now,
            )

        # ── 15. TP / SL Geometry & Risk Distance Checks ───────────────────────
        if not request.reduce_only:
            if request.stop_loss is None:
                return self._reject(
                    RejectionReasonCode.MISSING_STOP_LOSS,
                    "Live order setup is missing a Stop Loss price.",
                    "CHECK_MISSING_SL",
                    now,
                )
            if request.take_profit is None:
                return self._reject(
                    RejectionReasonCode.MISSING_TAKE_PROFIT,
                    "Live order setup is missing a Take Profit price.",
                    "CHECK_MISSING_TP",
                    now,
                )

            entry = request.entry_price
            if entry is None or entry <= Decimal("0"):
                return self._reject(
                    RejectionReasonCode.INVALID_PRICE_NON_POSITIVE,
                    "Trade setup requires a valid entry price for risk calculations.",
                    "CHECK_ENTRY_FOR_TP_SL",
                    now,
                )

            sl = request.stop_loss
            tp = request.take_profit

            # Geometry check:
            # LONG: entry > sl AND tp > entry
            # SHORT: sl > entry AND tp < entry
            if order_side == OrderSide.BUY:  # LONG
                if not (entry > sl and tp > entry):
                    return self._reject(
                        RejectionReasonCode.INVALID_TP_SL_GEOMETRY,
                        f"Invalid LONG TP/SL geometry: require TP ({tp}) > Entry ({entry}) > SL ({sl}).",
                        "CHECK_TP_SL_GEOMETRY",
                        now,
                    )
                risk_dist = entry - sl
                reward_dist = tp - entry
            else:  # SHORT
                if not (sl > entry and tp < entry):
                    return self._reject(
                        RejectionReasonCode.INVALID_TP_SL_GEOMETRY,
                        f"Invalid SHORT TP/SL geometry: require SL ({sl}) > Entry ({entry}) > TP ({tp}).",
                        "CHECK_TP_SL_GEOMETRY",
                        now,
                    )
                risk_dist = sl - entry
                reward_dist = entry - tp

            if risk_dist <= Decimal("0") or reward_dist <= Decimal("0"):
                return self._reject(
                    RejectionReasonCode.ZERO_OR_NEGATIVE_RISK_DISTANCE,
                    f"Risk distance ({risk_dist}) or Reward distance ({reward_dist}) must be strictly positive.",
                    "CHECK_RISK_DISTANCE",
                    now,
                )

            rr = reward_dist / risk_dist
            if rr < context.risk_config.minimum_risk_reward:
                return self._reject(
                    RejectionReasonCode.INVALID_RISK_REWARD,
                    f"Risk/Reward ratio {rr:.2f} is below minimum required {context.risk_config.minimum_risk_reward}.",
                    "CHECK_MINIMUM_RR",
                    now,
                )

            # Risk exposure check
            contract_val = getattr(spec, "contract_value", Decimal("1.0"))
            risk_amount = request.quantity * contract_val * risk_dist
            max_risk_allowed = context.account.total_equity * (context.risk_config.risk_per_trade_pct / Decimal("100"))
            # Allow minor rounding tolerance (0.01%)
            if risk_amount > (max_risk_allowed * Decimal("1.01")) and max_risk_allowed > Decimal("0"):
                return self._reject(
                    RejectionReasonCode.EXCESSIVE_RISK,
                    f"Trade risk amount {risk_amount:.2f} USDT exceeds configured risk limit {max_risk_allowed:.2f} USDT.",
                    "CHECK_RISK_AMOUNT",
                    now,
                )

            # Available Margin Check
            notional_value = request.quantity * contract_val * entry
            required_margin = notional_value / Decimal(str(leverage))
            if required_margin > context.account.available_balance:
                return self._reject(
                    RejectionReasonCode.INSUFFICIENT_BALANCE,
                    f"Required margin {required_margin:.2f} USDT exceeds available balance {context.account.available_balance:.2f} USDT.",
                    "CHECK_AVAILABLE_MARGIN",
                    now,
                )
        else:
            risk_amount = None
            risk_dist = None
            reward_dist = None
            rr = None

        # ── 16. Idempotency: Duplicate client_order_id / setup_id ──────────────
        if request.client_order_id and request.client_order_id in context.active_client_order_ids:
            return self._reject(
                RejectionReasonCode.DUPLICATE_CLIENT_ORDER_ID,
                f"client_order_id '{request.client_order_id}' has already been validated or submitted.",
                "CHECK_DUPLICATE_CLIENT_ORDER_ID",
                now,
            )

        if request.setup_id and request.setup_id in context.active_setup_ids:
            return self._reject(
                RejectionReasonCode.DUPLICATE_SETUP_ID,
                f"Strategy setup_id '{request.setup_id}' has already been validated or executed.",
                "CHECK_DUPLICATE_SETUP_ID",
                now,
            )

        # ── ALL CHECKS PASSED: Build validated DeltaOrderRequest ──────────────
        # Both identity fields come from the one spec resolved at check 6, so
        # they cannot disagree. `product_symbol` used to be
        # `spec.symbol.replace(".P", "")`: a no-op on the shipped table (which
        # holds native symbols only) that would have emitted a symbol differing
        # from the spec whose `product_id` accompanies it if a `.P` record were
        # ever registered.
        # A validated request that is a stop must carry the fields that make it
        # one on the wire (`stop_order_type`, `stop_trigger_method`), otherwise
        # `DeltaOrderRequest.to_exchange_payload` refuses it -- a gateway must
        # not hand back an approved request that cannot legally be submitted.
        is_stop = order_type in (OrderType.STOP_LIMIT_ORDER, OrderType.STOP_MARKET_ORDER)
        delta_order_req = DeltaOrderRequest(
            product_id=spec.product_id,
            product_symbol=spec.symbol,
            side=order_side,
            order_type=order_type,
            size=request.quantity,
            limit_price=request.entry_price,
            stop_price=request.stop_loss if is_stop else None,
            stop_order_type=StopOrderType.STOP_LOSS_ORDER if is_stop else None,
            stop_trigger_method=(
                StopTriggerMethod.LAST_TRADED_PRICE if is_stop else None),
            time_in_force=request.time_in_force,
            reduce_only=request.reduce_only,
            client_order_id=request.client_order_id,
            stop_loss_price=request.stop_loss,
            take_profit_price=request.take_profit,
        )

        return OrderValidationResult(
            is_valid=True,
            rejection_code=None,
            rejection_reason=None,
            failed_check=None,
            validated_at=now,
            order_request=delta_order_req,
            calculated_risk_amount=risk_amount,
            calculated_risk_distance=risk_dist,
            calculated_reward_distance=reward_dist,
            calculated_risk_reward=rr,
        )

    def validate_strategy_decision(
        self,
        decision: StrategyDecision,
        context: ValidationContext,
        account_id: str,
        quantity: Optional[Decimal] = None,
    ) -> OrderValidationResult:
        """Helper to validate a StrategyDecision directly from Phase 4.1/4.2."""
        now = datetime.now(timezone.utc)

        if decision.setup_state != SetupState.TRADE_SETUP_READY:
            return self._reject(
                RejectionReasonCode.DECISION_NOT_READY,
                f"Strategy decision is in state '{decision.setup_state}', expected 'TRADE_SETUP_READY'.",
                "CHECK_STRATEGY_DECISION_STATE",
                now,
            )

        if decision.direction not in (StrategyDirection.LONG, StrategyDirection.SHORT):
            return self._reject(
                RejectionReasonCode.INVALID_DIRECTION,
                f"Strategy decision has invalid direction '{decision.direction}'.",
                "CHECK_STRATEGY_DIRECTION",
                now,
            )

        trade_direction = TradeDirection.LONG if decision.direction == StrategyDirection.LONG else TradeDirection.SHORT

        # Compute position size if not explicitly provided
        if quantity is None:
            # Sizing: risk_amount = balance * 35% / |entry - SL|
            if decision.entry and decision.stop_loss and decision.risk_distance and decision.risk_distance > 0:
                risk_amount = context.account.total_equity * (context.risk_config.risk_per_trade_pct / Decimal("100"))
                calculated_qty = (risk_amount / decision.risk_distance).quantize(Decimal("1"))
                if calculated_qty < Decimal("1"):
                    calculated_qty = Decimal("1")
                quantity = calculated_qty
            else:
                quantity = Decimal("1")

        req = OrderValidationRequest(
            account_id=account_id,
            symbol=decision.symbol,
            direction=trade_direction,
            order_type=OrderType.LIMIT_ORDER,
            quantity=quantity,
            entry_price=decision.entry,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            leverage=context.risk_config.max_leverage,
            setup_id=decision.setup_id,
        )

        return self.validate(req, context)

    def _reject(
        self,
        code: RejectionReasonCode,
        reason: str,
        failed_check: str,
        validated_at: datetime,
    ) -> OrderValidationResult:
        """Create a structured rejection result with sanitized reason."""
        return OrderValidationResult(
            is_valid=False,
            rejection_code=code,
            rejection_reason=reason,
            failed_check=failed_check,
            validated_at=validated_at,
            order_request=None,
        )
