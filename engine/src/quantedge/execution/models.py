"""
Data models for Delta Exchange India execution client.

All currency and financial numerical quantities use Decimal for exact precision.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from enum import Enum
import math
from typing import Optional, Dict, Any, List

from quantedge.instruments import UnknownInstrumentError, delta_india_registry


class OrderSizeContractError(Exception):
    """An order size is not a positive whole number of contracts.

    Delta sizes an order in contracts and types `size` as an integer:
    its REST reference states "Integer numbers (like contract size, product_id
    and impact size) are unquoted" and sends `"size": 10`; its order-tool
    reference types `size` as `int`, "order size in contracts (positive)"; and
    the Delta India user guide defines an order as "an order to buy or sell a
    specified number of futures contracts".

    A fractional count is therefore not submittable. It is refused rather than
    truncated: flooring 98.492 to 98 here would silently change the exposure a
    validated order was approved for, and rounding up would exceed the margin
    the allocator sized against.
    """


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @classmethod
    def from_str(cls, val: str) -> "OrderSide":
        val_clean = val.strip().upper()
        if val_clean in ("BUY", "BID", "LONG"):
            return cls.BUY
        elif val_clean in ("SELL", "ASK", "SHORT"):
            return cls.SELL
        raise ValueError(f"Unknown order side: {val}")

    def to_exchange(self) -> str:
        """Delta Exchange India uses lowercase 'buy' or 'sell'."""
        return self.value.lower()


class OrderType(str, Enum):
    LIMIT_ORDER = "LIMIT_ORDER"
    MARKET_ORDER = "MARKET_ORDER"
    STOP_LIMIT_ORDER = "STOP_LIMIT_ORDER"
    STOP_MARKET_ORDER = "STOP_MARKET_ORDER"

    @classmethod
    def from_str(cls, val: str) -> "OrderType":
        val_clean = val.strip().upper()
        if val_clean in ("LIMIT", "LIMIT_ORDER"):
            return cls.LIMIT_ORDER
        elif val_clean in ("MARKET", "MARKET_ORDER"):
            return cls.MARKET_ORDER
        elif val_clean in ("STOP_LIMIT", "STOP_LIMIT_ORDER"):
            return cls.STOP_LIMIT_ORDER
        elif val_clean in ("STOP", "STOP_MARKET", "STOP_MARKET_ORDER", "STOP_LOSS_ORDER"):
            return cls.STOP_MARKET_ORDER
        raise ValueError(f"Unknown order type: {val}")

    def to_exchange(self) -> str:
        """Delta Exchange India API order type string."""
        mapping = {
            self.LIMIT_ORDER: "limit_order",
            self.MARKET_ORDER: "market_order",
            self.STOP_LIMIT_ORDER: "limit_order",
            self.STOP_MARKET_ORDER: "market_order",
        }
        return mapping[self]


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @classmethod
    def from_exchange(cls, val: str) -> "OrderStatus":
        val_clean = val.strip().lower()
        mapping = {
            "open": cls.OPEN,
            "pending": cls.PENDING,
            "partially_filled": cls.PARTIALLY_FILLED,
            "filled": cls.FILLED,
            "closed": cls.FILLED,  # When fully closed/filled
            "cancelled": cls.CANCELLED,
            "canceled": cls.CANCELLED,
            "rejected": cls.REJECTED,
            "expired": cls.EXPIRED,
        }
        if val_clean in mapping:
            return mapping[val_clean]
        return cls.PENDING


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"

    def to_exchange(self) -> str:
        return self.value.lower()


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class DeltaWalletBalance:
    """Wallet balance representation for an asset (e.g. USDT, BTC)."""
    asset_symbol: str
    balance: Decimal
    available_balance: Decimal
    position_margin: Decimal
    order_margin: Decimal
    blocked_margin: Decimal
    user_id: Optional[int] = None
    wallet_id: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeltaWalletBalance":
        return cls(
            asset_symbol=str(data.get("asset_symbol", "")).upper(),
            balance=Decimal(str(data.get("balance", "0"))),
            available_balance=Decimal(str(data.get("available_balance", "0"))),
            position_margin=Decimal(str(data.get("position_margin", "0"))),
            order_margin=Decimal(str(data.get("order_margin", "0"))),
            blocked_margin=Decimal(str(data.get("blocked_margin", "0"))),
            user_id=data.get("user_id"),
            wallet_id=data.get("id"),
        )


@dataclass(frozen=True)
class DeltaAccountSummary:
    """Aggregated account health and margin summary."""
    user_id: Optional[int]
    balances: Dict[str, DeltaWalletBalance]
    total_equity: Decimal
    available_balance: Decimal
    margin_used: Decimal
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class DeltaPosition:
    """Derivatives position on Delta Exchange India."""
    product_id: int
    product_symbol: str
    side: PositionSide
    size: Decimal
    entry_price: Decimal
    mark_price: Decimal
    liquidation_price: Optional[Decimal]
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    leverage: Decimal
    margin: Decimal
    adl_level: Optional[int] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeltaPosition":
        size_dec = Decimal(str(data.get("size", "0")))
        side = PositionSide.LONG if size_dec >= Decimal("0") else PositionSide.SHORT
        abs_size = abs(size_dec)
        
        entry_price = Decimal(str(data.get("entry_price", "0")))
        mark_price = Decimal(str(data.get("mark_price", "0")))
        liq_raw = data.get("liquidation_price")
        liquidation_price = Decimal(str(liq_raw)) if liq_raw is not None and str(liq_raw).strip() != "" else None
        
        unrealized_pnl = Decimal(str(data.get("unrealised_pnl", data.get("unrealized_pnl", "0"))))
        realized_pnl = Decimal(str(data.get("realised_pnl", data.get("realized_pnl", "0"))))
        leverage = Decimal(str(data.get("leverage", "1")))
        margin = Decimal(str(data.get("margin", "0")))

        # An inbound payload without a usable `product_symbol` must not be
        # handed a fabricated identity (this used to default to "BTCUSD").
        # The instrument registry is the single source of verified symbols and
        # refuses exactly this set of inputs -- missing, None, blank, non-string,
        # `.P` and unregistered symbols all raise `UnknownInstrumentError`
        # rather than resolving to some other product.
        raw_symbol = data.get("product_symbol", data.get("symbol"))
        spec = delta_india_registry().get(raw_symbol)

        # `product_id` used to default to 0, so a payload that never identified
        # its product still became a position. It now fails closed, and the two
        # identity fields must agree with each other -- the same contract
        # `DeltaOrderResponse.from_dict` enforces. Integral numeric strings and
        # floats are accepted because they are exact; fractional, non-numeric,
        # bool, zero and negative values are not.
        raw_product_id = data.get("product_id")
        try:
            if isinstance(raw_product_id, bool) or raw_product_id is None:
                raise ValueError(raw_product_id)
            as_decimal = Decimal(str(raw_product_id).strip())
            product_id = int(as_decimal)
            if as_decimal != product_id or product_id <= 0:
                raise ValueError(raw_product_id)
        except (ArithmeticError, TypeError, ValueError):
            raise UnknownInstrumentError(
                f"{raw_product_id!r} is not a usable product id for "
                f"{spec.symbol}; refusing to default to 0") from None

        # A payload naming one symbol under another symbol's product id
        # (`ETHUSD` + 27, say, where 27 is BTCUSD and ETHUSD is 3136) is
        # self-contradictory: whichever field downstream code trusts -- the
        # synchronizer keys `state_store.positions` by symbol, reconciliation
        # and the flatten path send by product id -- the other one misidentifies
        # the position. The verified id comes from the registry snapshot, so no
        # product id is written here.
        if product_id != spec.product_id:
            raise UnknownInstrumentError(
                f"product id {product_id} does not belong to {spec.symbol} "
                f"(verified id {spec.product_id}); refusing to accept a "
                f"position whose identity is self-contradictory")

        return cls(
            product_id=product_id,
            product_symbol=spec.symbol,
            side=side,
            size=abs_size,
            entry_price=entry_price,
            mark_price=mark_price,
            liquidation_price=liquidation_price,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            leverage=leverage,
            margin=margin,
            adl_level=data.get("adl_level"),
            updated_at=datetime.now(timezone.utc),
        )


@dataclass
class DeltaOrderRequest:
    """Request model to submit an order to Delta Exchange India."""
    product_id: int
    product_symbol: str
    side: OrderSide
    order_type: OrderType
    size: Decimal
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    stop_loss_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None

    def exchange_contract_count(self) -> int:
        """Return `size` as a positive whole number of contracts, or refuse.

        Delta's order `size` is an integer contract count (see
        `OrderSizeContractError`). Every value that is not exactly a positive
        whole number is refused here; nothing is floored, rounded or clamped,
        because changing the count would change the exposure the order was
        sized and validated for.

        This replaces a float fallback that serialized a fractional count as-is
        (`Decimal("127.272")` -> `127.272`). Only the market-scan path floors to
        whole contracts in the allocator and is independently re-checked by the
        validation gateway's step rule; the multi-user path does not use that
        gateway at all, so before this check a fractional count reached the wire.
        """
        raw = self.size
        if isinstance(raw, bool) or not isinstance(raw, (Decimal, int, float)):
            raise OrderSizeContractError(
                f"order size must be a whole number of contracts, got "
                f"{raw!r} of type {type(raw).__name__}")

        if isinstance(raw, Decimal):
            if not raw.is_finite():
                raise OrderSizeContractError(
                    f"order size must be a finite whole number of contracts, "
                    f"got {raw!r}")
            integral = raw.to_integral_value(rounding=ROUND_DOWN)
            if raw != integral:
                raise OrderSizeContractError(
                    f"order size {raw} is not a whole number of contracts; "
                    f"Delta accepts only integer contract counts and this is "
                    f"refused rather than truncated to {integral}")
            count = int(integral)
        elif isinstance(raw, float):
            if not math.isfinite(raw):
                raise OrderSizeContractError(
                    f"order size must be a finite whole number of contracts, "
                    f"got {raw!r}")
            truncated = math.floor(raw)
            if raw != truncated:
                raise OrderSizeContractError(
                    f"order size {raw} is not a whole number of contracts; "
                    f"Delta accepts only integer contract counts and this is "
                    f"refused rather than truncated to {truncated}")
            count = truncated
        else:
            count = raw

        if count <= 0:
            raise OrderSizeContractError(
                f"order size must be a positive contract count, got {count}")
        return count

    def to_exchange_payload(self) -> Dict[str, Any]:
        """Serialize into Delta Exchange REST API POST /v2/orders payload.

        This is the single choke point every production order passes through:
        `DeltaIndiaClient.create_order` (aliased `place_order`) is the only
        caller, so the identity invariant is enforced here rather than relying
        on each of the six upstream construction sites to source its fields
        correctly. `product_symbol` is resolved through the authoritative
        instrument registry with exact matching -- an unknown, blank, padded,
        lowercase, `.P`, separator-bearing or non-string symbol raises
        `UnknownInstrumentError` instead of being normalized onto a real
        product -- and `product_id` must be the verified id for that symbol, so
        a self-contradictory pair cannot be serialized. No normalized or
        defaulted identity is ever written into the payload.

        `size` is serialized as the positive integer contract count Delta
        documents, and a value that is not one raises `OrderSizeContractError`
        here -- before `DeltaIndiaClient.create_order` issues the POST -- rather
        than being silently truncated onto the grid.
        """
        spec = delta_india_registry().get(self.product_symbol)
        if self.product_id != spec.product_id:
            raise UnknownInstrumentError(
                f"product id {self.product_id!r} does not belong to "
                f"{spec.symbol} (verified id {spec.product_id}); refusing to "
                f"submit an order whose identity is self-contradictory")

        size_val = self.exchange_contract_count()

        payload: Dict[str, Any] = {
            "product_id": spec.product_id,
            "product_symbol": spec.symbol,
            "side": self.side.to_exchange(),
            "order_type": self.order_type.to_exchange(),
            "size": size_val,
            "time_in_force": self.time_in_force.to_exchange(),
            "reduce_only": self.reduce_only,
        }
        if self.limit_price is not None:
            payload["limit_price"] = str(self.limit_price)
        if self.stop_price is not None:
            payload["stop_price"] = str(self.stop_price)
        if self.client_order_id is not None:
            payload["client_order_id"] = self.client_order_id
        if self.stop_loss_price is not None:
            payload["stop_loss_price"] = str(self.stop_loss_price)
        if self.take_profit_price is not None:
            payload["take_profit_price"] = str(self.take_profit_price)
        return payload


@dataclass(frozen=True)
class DeltaOrderResponse:
    """Order response representation from Delta Exchange India."""
    id: int
    client_order_id: Optional[str]
    user_id: Optional[int]
    product_id: int
    product_symbol: str
    side: OrderSide
    order_type: OrderType
    size: Decimal
    unfilled_size: Decimal
    limit_price: Optional[Decimal]
    stop_price: Optional[Decimal]
    average_fill_price: Optional[Decimal]
    state: OrderStatus
    reduce_only: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    @property
    def filled_size(self) -> Decimal:
        return self.size - self.unfilled_size

    @property
    def status(self) -> OrderStatus:
        return self.state

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeltaOrderResponse":
        created_raw = data.get("created_at")
        if isinstance(created_raw, (int, float)):
            # Delta returns epoch microseconds or milliseconds or seconds
            if created_raw > 1e14:  # microseconds
                created_dt = datetime.fromtimestamp(created_raw / 1e6, tz=timezone.utc)
            elif created_raw > 1e11:  # milliseconds
                created_dt = datetime.fromtimestamp(created_raw / 1e3, tz=timezone.utc)
            else:  # seconds
                created_dt = datetime.fromtimestamp(created_raw, tz=timezone.utc)
        elif isinstance(created_raw, str):
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except Exception:
                created_dt = datetime.now(timezone.utc)
        else:
            created_dt = datetime.now(timezone.utc)

        updated_raw = data.get("updated_at")
        updated_dt = None
        if updated_raw is not None:
            if isinstance(updated_raw, (int, float)):
                if updated_raw > 1e14:
                    updated_dt = datetime.fromtimestamp(updated_raw / 1e6, tz=timezone.utc)
                elif updated_raw > 1e11:
                    updated_dt = datetime.fromtimestamp(updated_raw / 1e3, tz=timezone.utc)
                else:
                    updated_dt = datetime.fromtimestamp(updated_raw, tz=timezone.utc)
            elif isinstance(updated_raw, str):
                try:
                    updated_dt = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
                except Exception:
                    updated_dt = None

        limit_raw = data.get("limit_price")
        limit_price = Decimal(str(limit_raw)) if limit_raw is not None and str(limit_raw).strip() != "" else None

        stop_raw = data.get("stop_price")
        stop_price = Decimal(str(stop_raw)) if stop_raw is not None and str(stop_raw).strip() != "" else None

        avg_fill_raw = data.get("average_fill_price", data.get("avg_fill_price"))
        avg_fill_price = Decimal(str(avg_fill_raw)) if avg_fill_raw is not None and str(avg_fill_raw).strip() != "" else None

        # Inbound identity is resolved through the instrument registry -- the
        # single source of verified symbols -- exactly as `DeltaPosition` does.
        # A missing, None, blank, case-folded, padded, `.P`, separator, unknown
        # or non-string symbol raises `UnknownInstrumentError` here instead of
        # becoming "" (the previous default) or being `.upper()`-ed into a
        # registered product. `product_symbol` is Delta's field name; `symbol`
        # is the fallback key some payloads use.
        raw_symbol = data.get("product_symbol", data.get("symbol"))
        spec = delta_india_registry().get(raw_symbol)

        # A missing or malformed `product_id` used to become 0, i.e. an order
        # whose product could not be identified was still parsed. It now fails
        # closed. Integral numeric strings and floats are accepted because they
        # are exact; a fractional or non-numeric value is not.
        raw_product_id = data.get("product_id")
        try:
            if isinstance(raw_product_id, bool) or raw_product_id is None:
                raise ValueError(raw_product_id)
            as_decimal = Decimal(str(raw_product_id).strip())
            product_id = int(as_decimal)
            if as_decimal != product_id:
                raise ValueError(raw_product_id)
        except (ArithmeticError, TypeError, ValueError):
            raise UnknownInstrumentError(
                f"{raw_product_id!r} is not a usable product id for "
                f"{spec.symbol}; refusing to default to 0") from None

        # The two identity fields must agree. A payload naming one symbol under
        # another symbol's product id (`ETHUSD` + 27, say, where 27 is BTCUSD
        # and ETHUSD is 3136) is self-contradictory: whichever field downstream
        # code trusts, the other one misidentifies the order. The verified id
        # comes from the registry snapshot, so no product id is written here.
        if product_id != spec.product_id:
            raise UnknownInstrumentError(
                f"product id {product_id} does not belong to {spec.symbol} "
                f"(verified id {spec.product_id}); refusing to accept a "
                f"payload whose identity is self-contradictory")

        return cls(
            id=int(data.get("id", 0)),
            client_order_id=data.get("client_order_id"),
            user_id=data.get("user_id"),
            product_id=product_id,
            product_symbol=spec.symbol,
            side=OrderSide.from_str(str(data.get("side", "BUY"))),
            order_type=OrderType.from_str(str(data.get("order_type", "LIMIT_ORDER"))),
            size=Decimal(str(data.get("size", "0"))),
            unfilled_size=Decimal(str(data.get("unfilled_size", data.get("size", "0")))),
            limit_price=limit_price,
            stop_price=stop_price,
            average_fill_price=avg_fill_price,
            state=OrderStatus.from_exchange(str(data.get("state", "OPEN"))),
            reduce_only=bool(data.get("reduce_only", False)),
            created_at=created_dt,
            updated_at=updated_dt,
        )


class ConnectionState(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    EXCHANGE_ERROR = "EXCHANGE_ERROR"
    UNKNOWN = "UNKNOWN"


class ExecutionMode(str, Enum):
    LIVE = "LIVE"


class ReconciliationDiscrepancyType(str, Enum):
    LOCAL_TRADE_MISSING_ON_EXCHANGE = "LOCAL_TRADE_MISSING_ON_EXCHANGE"
    EXCHANGE_POSITION_MISSING_LOCALLY = "EXCHANGE_POSITION_MISSING_LOCALLY"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    SL_MISMATCH = "SL_MISMATCH"
    TP_MISMATCH = "TP_MISMATCH"
    ORDER_STATUS_MISMATCH = "ORDER_STATUS_MISMATCH"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    ORPHANED_POSITION = "ORPHANED_POSITION"
    STALE_LOCAL_STATE = "STALE_LOCAL_STATE"


@dataclass(frozen=True)
class ReconciliationDiscrepancy:
    """Individual discrepancy detected between local and exchange states."""
    discrepancy_type: ReconciliationDiscrepancyType
    resource_id: str
    details: str
    local_value: Optional[Any] = None
    exchange_value: Optional[Any] = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ReconciliationReport:
    """Audit report generated by DeltaReconciliationService."""
    account_id: str
    is_synchronized: bool
    discrepancies: List[ReconciliationDiscrepancy] = field(default_factory=list)
    actions_taken: List[str] = field(default_factory=list)
    exchange_equity: Decimal = Decimal("0")
    local_equity: Decimal = Decimal("0")
    exchange_positions_count: int = 0
    local_positions_count: int = 0
    exchange_open_orders_count: int = 0
    local_open_orders_count: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class TradeCostBreakdown:
    """Authoritative net P&L and fee accounting breakdown."""
    gross_pnl: Decimal
    entry_fee: Decimal = Decimal("0")
    exit_fee: Decimal = Decimal("0")
    funding_costs: Decimal = Decimal("0")
    other_costs: Decimal = Decimal("0")
    pre_trade_balance: Decimal = Decimal("0")

    @property
    def total_fees(self) -> Decimal:
        return self.entry_fee + self.exit_fee + self.funding_costs + self.other_costs

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.total_fees

    @property
    def post_trade_balance(self) -> Decimal:
        calc = self.pre_trade_balance + self.net_pnl
        return max(Decimal("0"), calc)

