"""
Data models for Delta Exchange India execution client.

All currency and financial numerical quantities use Decimal for exact precision.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from enum import Enum
import math
from typing import Optional, Dict, Any, List

from quantedge.instruments import UnknownInstrumentError, delta_india_registry


def optional_decimal(data: Dict[str, Any], *keys: str) -> Optional[Decimal]:
    """Read a financial field that the exchange may simply not have sent.

    Returns the first key present with a non-blank value, as an exact `Decimal`
    with its sign preserved, and `None` when no key carries a value.

    This exists because the alternative -- `Decimal(str(data.get(key, "0")))` --
    makes "the exchange reported zero" and "the exchange reported nothing"
    numerically identical. For commission (Task O §O2) and realized PnL (§O3)
    those are different financial facts: one is an observation, the other is a
    gap in the accounting record that must stay visible to reconciliation.
    """
    for key in keys:
        if key not in data:
            continue
        raw = data[key]
        if raw is None or str(raw).strip() == "":
            continue
        return Decimal(str(raw))
    return None


#: Internal alias kept so call sites read as a private detail of this package.
_optional_decimal = optional_decimal


def required_decimal(data: Dict[str, Any], *keys: str, field_name: str,
                     context: str) -> Decimal:
    """Read a financial field the caller is about to do ARITHMETIC on, or refuse.

    The mandatory counterpart of `optional_decimal`, with identical key
    precedence and identical exactness: the first present, non-blank key wins and
    its value becomes an exact `Decimal` with its sign preserved. The difference
    is what happens when nothing is there. `optional_decimal` answers `None`,
    which is correct for a fact a consumer can carry as "unobserved". This
    answers a refusal, which is correct for a fact a consumer immediately
    consumes as a number.

    Task O §O7. Wallet numerics are that second kind. `position_margin +
    order_margin` is computed straight into `DeltaAccountSummary.margin_used`,
    `balance` is returned by `_authoritative_exchange_balance` as the
    post-closure equity of record, and `available_balance` is written over the
    local `AccountRecord` by both the synchronizer and reconciliation. A
    fabricated `Decimal("0")` in any of those is not a degraded reading, it is an
    invented one: zero margin used, zero equity, zero collateral. Widening them
    to `Optional[Decimal]` the way §O6 widened position numerics is not available
    here precisely because they are consumed arithmetically -- `None + None` is
    the wrong failure, in the wrong place, long after the observation was lost.

    Refused, in this order: absent, `None`, blank/whitespace, `bool` (an `int`
    subclass, so `True` would otherwise be a balance), unparseable, and
    non-finite. `NaN` and `Infinity` are refused on the `get_ticker` precedent
    (`delta_client.get_ticker`, `DeltaOrder.exchange_contract_count`): they
    parse, they propagate silently through comparison and addition, and they
    poison every downstream margin decision. An observed zero is NOT refused --
    a genuinely empty wallet is a real fact, and the whole point of this helper
    is to keep it distinguishable from an absent one. Sign is likewise preserved
    rather than validated: this repository has no evidence about whether Delta
    can report a negative wallet field, and inventing that rule would be
    guessing exchange semantics.
    """
    # Deferred to break the import cycle: `delta_client` imports this module.
    # Same idiom as `DeltaPosition.from_dict` (§O6).
    from quantedge.execution.delta_client import DeltaResponseError

    for key in keys:
        if key not in data:
            continue
        raw = data[key]
        if raw is None or str(raw).strip() == "":
            continue
        if isinstance(raw, bool):
            raise DeltaResponseError(
                f"{context} {field_name} must be a number, got {raw!r}")
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError) as e:
            raise DeltaResponseError(
                f"{context} has an unparseable {field_name} {raw!r}") from e
        if not value.is_finite():
            raise DeltaResponseError(
                f"{context} has a non-finite {field_name} {raw!r}")
        return value

    raise DeltaResponseError(
        f"{context} carries no {field_name}; refusing to treat an unobserved "
        f"wallet figure as {field_name} zero"
    )


#: Internal alias, mirroring `_optional_decimal`.
_required_decimal = required_decimal


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


class StopOrderContractError(Exception):
    """A stop order is not expressible under Delta's documented order contract.

    Delta expresses a stop as an ordinary `order_type` (`limit_order` or
    `market_order`) carrying THREE additional fields (`POST /orders` body
    reference):

        "order_type":           "market_order",
        "stop_order_type":      "stop_loss_order",
        "stop_price":           "56000",
        "stop_trigger_method":  "last_traded_price",

    `stop_order_type` is what makes the order a stop. Without it the payload is
    a plain market order carrying an ignored `stop_price`, which executes
    immediately at the best available price instead of resting until the stop is
    hit -- so a stop-loss submitted that way would close the position it was
    meant to protect the instant it was placed.

    That shape is refused here rather than corrected, because there is no safe
    correction: guessing `stop_order_type` would mean guessing whether the
    caller intended a loss-limiting or a profit-taking trigger, and guessing
    `stop_trigger_method` would mean guessing which price series the exchange
    should watch. Both are execution semantics, not formatting.
    """


class UnknownOrderStateError(ValueError):
    """The exchange named an order state this engine cannot interpret.

    Task O §O5. Delta documents exactly four order states -- `open`, `pending`,
    `closed`, `cancelled` -- and `OrderStatus.from_exchange` used to answer
    anything else with `PENDING`. That default answered a safety question: an
    order the exchange had filled, rejected, liquidated or expired under a name
    this engine does not know would be adopted as *still resting*, so the
    bracket logic would keep waiting for a fill that already happened and
    reconciliation would compare local state against a state the exchange never
    reported.

    There is no safe correction. `PENDING` is not a conservative guess in either
    direction: it under-reports a terminal order (protection believed live when
    it is gone) and over-reports a resting one. So an unrecognized state is
    refused, and the caller that owns the boundary -- the REST parse path or the
    private-stream funnel -- decides how to fail closed and which alert to
    raise.

    A `ValueError` subclass so the existing normalization guards that already
    treat a `ValueError` as a quarantine condition keep working unchanged.
    """


class StopOrderType(str, Enum):
    """Delta `stop_order_type` -- the field that makes an order a stop.

    Documented enumerated values (Orders API, "Enumerated Values"):
      * `stop_loss_order`   - "Order triggered when stop price is hit to limit losses"
      * `take_profit_order` - "Order triggered when take profit price is hit to lock in gains"
    """
    STOP_LOSS_ORDER = "stop_loss_order"
    TAKE_PROFIT_ORDER = "take_profit_order"

    def to_exchange(self) -> str:
        return self.value


class StopTriggerMethod(str, Enum):
    """Delta `stop_trigger_method` -- which price series arms the trigger.

    Documented enumerated values (Orders API, "Enumerated Values"):
      * `mark_price`        - "Order triggered against the mark price"
      * `last_traded_price` - "Order triggered against the last traded price"
      * `spot_price`        - "Order triggered against the spot index price"
    """
    MARK_PRICE = "mark_price"
    LAST_TRADED_PRICE = "last_traded_price"
    SPOT_PRICE = "spot_price"

    def to_exchange(self) -> str:
        return self.value


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
        """Map a Delta order state onto a lifecycle status, or refuse.

        Task O §O5: this used to end in `return cls.PENDING`, which turned every
        state name this engine does not know into "still resting". See
        `UnknownOrderStateError` for why that default is unsafe in both
        directions. The documented state set is `open` / `pending` / `closed` /
        `cancelled`; the additional entries below are states this engine has
        long accepted and are kept exactly as they were -- only the fallback is
        gone.

        Absence is NOT handled here. A missing state is a different fact from an
        unrecognized one, and it belongs to whichever parse boundary observed
        the absence, so callers must decide before calling.
        """
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
        raise UnknownOrderStateError(
            f"{val!r} is not a Delta order state this engine can interpret "
            f"(documented states: open, pending, closed, cancelled); refusing "
            f"to adopt a lifecycle status the exchange did not report"
        )


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
    """Wallet balance representation for an asset (e.g. USDT, BTC).

    Task O §O7: every numeric field stays a mandatory `Decimal`. Unlike the §O6
    position numerics these are consumed arithmetically the moment they arrive,
    so the fail-closed direction is refusal at the parse boundary rather than an
    `Optional` a consumer would have to remember to check.
    """
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
        """Parse one `/v2/wallet/balances` entry, or refuse it (Task O §O7).

        Six fabrications were removed. The five numerics each defaulted to
        `Decimal("0")`, and the asset defaulted to `""`:

        * `balance` -- `_authoritative_exchange_balance` returns this as the
          post-closure equity of record, and `handle_exchange_closure` writes it
          to `post_trade_balance`, `available_balance` and `total_equity`. That
          function already has a `None` channel meaning "unavailable"; a
          fabricated zero bypassed it and booked a total loss of equity as an
          authoritative reading.
        * `available_balance` -- overwritten onto the local `AccountRecord` by
          `_reconcile_balances` and by `reconcile_account`, and read by the
          validation gateway, the capital allocator, the market orchestrator and
          the multi-user pre-trade gate.
        * `position_margin` / `order_margin` -- summed into
          `DeltaAccountSummary.margin_used`, so an absent pair reported zero
          margin in use against real open exposure.
        * `blocked_margin` -- reported to the operator by `connection_test`.
        * `asset_symbol` -- `get_account_summary` keys `balance_map` on it and
          looks up `"USDT"`, and `_authoritative_exchange_balance` matches
          `("USDT", "USD")`. An unnamed wallet became `""`: it silently missed
          both lookups, and two unnamed wallets collapsed onto one key so one of
          them vanished from the account summary entirely.

        Identity is resolved before any numeric is touched, matching §O6's
        ordering in `DeltaPosition.from_dict`, so an unusable payload is reported
        as the identity failure it is. `.upper()` folding of a PRESENT asset is
        deliberately unchanged: unlike a product symbol there is no pinned asset
        registry to resolve against, and every consumer keys on `"USDT"`.

        Raises:
            DeltaResponseError: the asset is unnamed, or any of the five
                numerics is absent, blank, non-numeric or non-finite.
        """
        # Deferred to break the import cycle: `delta_client` imports this module.
        from quantedge.execution.delta_client import DeltaResponseError

        raw_asset = data.get("asset_symbol")
        if raw_asset is None or str(raw_asset).strip() == "":
            raise DeltaResponseError(
                "Wallet balance entry carries no asset_symbol; refusing to "
                "adopt an unnamed wallet as account collateral"
            )
        asset_symbol = str(raw_asset).strip().upper()
        context = f"Wallet balance {asset_symbol}"

        return cls(
            asset_symbol=asset_symbol,
            balance=_required_decimal(
                data, "balance", field_name="balance", context=context),
            available_balance=_required_decimal(
                data, "available_balance", field_name="available_balance",
                context=context),
            position_margin=_required_decimal(
                data, "position_margin", field_name="position_margin",
                context=context),
            order_margin=_required_decimal(
                data, "order_margin", field_name="order_margin",
                context=context),
            blocked_margin=_required_decimal(
                data, "blocked_margin", field_name="blocked_margin",
                context=context),
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
    # Task O §O6: five optional numerics. The exchange may simply not report
    # these on a `/v2/positions/margined` entry, and an unreported value is not
    # an observed one -- a fabricated `Decimal("0")` entry price, mark price,
    # unrealized PnL or margin, or a fabricated `Decimal("1")` leverage, is an
    # invented exchange observation. `size` stays mandatory: it decides
    # open-versus-flat, so its absence is refused in `from_dict` instead.
    entry_price: Optional[Decimal]
    mark_price: Optional[Decimal]
    liquidation_price: Optional[Decimal]
    unrealized_pnl: Optional[Decimal]
    # Task O §O3: absent realized PnL is `None`, not zero. An observed
    # break-even close and an unreported value are different financial facts.
    realized_pnl: Optional[Decimal]
    leverage: Optional[Decimal]
    margin: Optional[Decimal]
    adl_level: Optional[int] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeltaPosition":
        # Task O §O6: identity is resolved FIRST, before any numeric refusal.
        # The order is load-bearing rather than stylistic -- an unusable payload
        # must be reported as an identity failure, so `UnknownInstrumentError`
        # has to win over the size refusal added below.
        #
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

        # Task O §O6: `size` is mandatory at this boundary. It used to read
        # `Decimal(str(data.get("size", "0")))`, so an entry the exchange sent
        # without a size became a flat LONG -- and `get_positions` filters on
        # `pos.size > 0`, so that fabricated zero deleted the row from the
        # snapshot entirely. False flatness is the most dangerous answer this
        # parse can give: the synchronizer CLOSES every local position missing
        # from a snapshot, the trade lifecycle CLEARS blocking reconciliation
        # alerts on a clean run, reconciliation force-releases the single-trade
        # lock when the exchange looks flat, and the pre-trade gate AUTHORIZES a
        # new order when it sees no exposure. There is no conservative default
        # in either direction, so the absence is refused.
        #
        # REST-boundary rule only. A private-stream `delete` frame may
        # legitimately omit `size` and §O5 decides closure from
        # `DeltaPositionEvent.is_closure`, so refusing on the WebSocket path
        # would weaken closure detection instead of strengthening it.
        #
        # Imported here rather than at module scope because `delta_client`
        # imports this module; the deferred lookup keeps the dependency
        # one-directional at import time.
        from quantedge.execution.delta_client import DeltaResponseError

        raw_size = data.get("size")
        if raw_size is None or str(raw_size).strip() == "":
            raise DeltaResponseError(
                f"position {spec.symbol} (product {product_id}) arrived with no "
                f"size; refusing to report it as flat")
        size_dec = Decimal(str(raw_size))
        side = PositionSide.LONG if size_dec >= Decimal("0") else PositionSide.SHORT
        abs_size = abs(size_dec)

        # Task O §O6: five fields that used to fabricate an exchange observation
        # -- `entry_price`/`mark_price` defaulted to `"0"`, `unrealised_pnl` and
        # `margin` to `"0"`, `leverage` to `"1"` -- now go through the §O2/§O3
        # helper, so absence is `None` and an observed zero stays a distinct,
        # visible fact. No consumer performs arithmetic on any of them: the
        # synchronizer and the private-stream funnel copy them into
        # `PositionRecord` or compare them for equality, the lifecycle logs
        # them, and sizing reads `mark_price` from the ticker (which is refused
        # outright when absent) rather than from a position.
        entry_price = _optional_decimal(data, "entry_price")
        mark_price = _optional_decimal(data, "mark_price")
        liq_raw = data.get("liquidation_price")
        liquidation_price = Decimal(str(liq_raw)) if liq_raw is not None and str(liq_raw).strip() != "" else None

        unrealized_pnl = _optional_decimal(data, "unrealised_pnl", "unrealized_pnl")
        realized_pnl = _optional_decimal(data, "realised_pnl", "realized_pnl")
        leverage = _optional_decimal(data, "leverage")
        margin = _optional_decimal(data, "margin")

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
    stop_order_type: Optional[StopOrderType] = None
    stop_trigger_method: Optional[StopTriggerMethod] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    # Attached-bracket levels. Serialized as Delta's documented
    # `bracket_stop_loss_price` / `bracket_take_profit_price` (plus
    # `bracket_stop_trigger_method`) -- see `to_exchange_payload`.
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

    def _assert_stop_contract(self) -> None:
        """Refuse any order that is a stop in intent but not in payload.

        Four ways the stop contract can be violated, all refused here at the one
        choke point every production order passes through:

        1. `stop_price` present, `stop_order_type` absent -- the exchange sees a
           plain market/limit order with an ignored trigger price. This is the
           shape that turned stop-loss protection into an immediate market exit.
        2. A `STOP_*` order type without `stop_order_type` -- same wire result;
           the local type name is not transmitted, `to_exchange()` maps both
           `STOP_MARKET_ORDER` and `STOP_LIMIT_ORDER` onto the plain
           `market_order`/`limit_order` strings Delta documents.
        3. `stop_order_type` present without `stop_price` -- a trigger with no
           trigger level.
        4. `stop_order_type` present without `stop_trigger_method` -- the price
           series that arms the trigger would be whatever the exchange defaults
           to, which is an unverified execution semantic (safety rule: an
           unknown exchange semantic is never resolved by a default).
        """
        is_stop_type = self.order_type in (
            OrderType.STOP_MARKET_ORDER, OrderType.STOP_LIMIT_ORDER)

        if self.stop_order_type is None:
            if self.stop_price is not None:
                raise StopOrderContractError(
                    f"order for {self.product_symbol} carries stop_price "
                    f"{self.stop_price} but no stop_order_type; Delta would "
                    f"treat this as an ordinary "
                    f"{self.order_type.to_exchange()} and execute it "
                    f"immediately. Refusing to submit an unprotected order "
                    f"that claims to be a stop.")
            if is_stop_type:
                raise StopOrderContractError(
                    f"order for {self.product_symbol} is typed "
                    f"{self.order_type.value} but carries no stop_order_type; "
                    f"the local type name is not transmitted, so this would "
                    f"reach Delta as a plain "
                    f"{self.order_type.to_exchange()}")
            return

        if self.stop_price is None:
            raise StopOrderContractError(
                f"order for {self.product_symbol} declares stop_order_type "
                f"{self.stop_order_type.value} but carries no stop_price; a "
                f"trigger without a trigger level is not submittable")

        if self.stop_trigger_method is None:
            raise StopOrderContractError(
                f"order for {self.product_symbol} declares stop_order_type "
                f"{self.stop_order_type.value} but no stop_trigger_method; the "
                f"price series that arms the trigger (mark_price / "
                f"last_traded_price / spot_price) is an execution semantic and "
                f"is not defaulted here")

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

        self._assert_stop_contract()

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
        # `stop_order_type` is what makes Delta treat the order as a stop, and
        # `stop_trigger_method` names the price series that arms it. Both are
        # emitted only when set, and `_assert_stop_contract` above has already
        # refused every combination in which one of them is missing while the
        # order is a stop in intent.
        if self.stop_order_type is not None:
            payload["stop_order_type"] = self.stop_order_type.to_exchange()
        if self.stop_trigger_method is not None:
            payload["stop_trigger_method"] = self.stop_trigger_method.to_exchange()
        if self.client_order_id is not None:
            payload["client_order_id"] = self.client_order_id
        # Attached bracket -- Delta spells these fields `bracket_*`.
        #
        # The previous spelling (`stop_loss_price` / `take_profit_price`) is not
        # a parameter of POST /v2/orders in any authoritative Delta source, is
        # absent from the order object the exchange returns, and appeared on none
        # of 600 orders in this account's history: it created no protection at
        # all, leaving the entry unprotected until the separate reduce-only pair
        # landed after a fill (`_ensure_bracket_protection`).
        #
        # Read off orders Delta itself accepted (97 parents carrying an attached
        # bracket, 13 of them XRPUSD limit orders) rather than assumed:
        #   * a trigger price alone is accepted -- 57 parents carry exactly
        #     bracket_stop_loss_price + bracket_take_profit_price and no
        #     `*_limit_price` -- so the limit companion is not mandatory and no
        #     price the engine never computed has to be invented here;
        #   * the leg Delta then creates is a reduce-only `market_order` with
        #     `stop_order_type` stop_loss_order / take_profit_order and a
        #     `stop_price`, the same shape `_ensure_bracket_protection` builds.
        #
        # `bracket_stop_trigger_method` is emitted alongside the prices rather
        # than omitted: every bracket leg in that history arms on `mark_price`,
        # so leaving the field out would silently move the trigger series off the
        # `last_traded_price` this codebase deliberately chose for stops
        # (trade_lifecycle.py:806-811 -- Manual SMC's stop level is derived from,
        # and its backtest measured against, traded prices). The price series
        # that arms a trigger is an execution semantic and is not left to an
        # exchange default, exactly as `_assert_stop_contract` refuses to default
        # it for a standalone stop.
        if self.stop_loss_price is not None:
            payload["bracket_stop_loss_price"] = str(self.stop_loss_price)
        if self.take_profit_price is not None:
            payload["bracket_take_profit_price"] = str(self.take_profit_price)
        if self.stop_loss_price is not None or self.take_profit_price is not None:
            payload["bracket_stop_trigger_method"] = (
                StopTriggerMethod.LAST_TRADED_PRICE.to_exchange())
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

    # Task O §O13 -- the fields that let a caller CONFIRM exchange-side
    # protection instead of assuming it.
    #
    # All five are read straight off the order object Delta returns (verified
    # present on `GET /v2/orders/history`: `stop_order_type`,
    # `stop_trigger_method`, `bracket_order`, `bracket_stop_loss_price`,
    # `bracket_take_profit_price` are 5 of its 34 keys). They are additive and
    # default to None, so nothing that already reads this dataclass changes.
    #
    # `None` means *the exchange did not state it*, which is never read as a
    # value: the adoption path in `trade_lifecycle` requires a positive match on
    # every one of them and falls back to placing its own protection otherwise
    # (safety rules #13, #15). The two stop descriptors are kept as the raw wire
    # strings rather than coerced into `StopOrderType` / `StopTriggerMethod`,
    # because an unrecognised value here must not raise out of a plain order
    # query -- `get_open_orders` feeds reconciliation -- and must not be mapped
    # onto a neighbouring enum member either. A string this engine does not
    # recognise simply fails to match, which is the fail-closed direction.
    stop_order_type: Optional[str] = None
    stop_trigger_method: Optional[str] = None
    # `True` on a row that IS a bracket leg the exchange created (313 such rows
    # in this account's history). Informational: the adoption path records it
    # but does not require it, because a leg that matches side, size, product,
    # reduce-only, stop type, trigger series and stop price is already
    # protection at the authoritative level whatever created it.
    bracket_order: Optional[bool] = None
    # Set on a PARENT order that carries an attached bracket -- the echo that
    # proves Delta accepted the `bracket_*` levels submitted with the entry.
    bracket_stop_loss_price: Optional[Decimal] = None
    bracket_take_profit_price: Optional[Decimal] = None

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

        # Task O §O13: the exchange's own description of protection. Absent,
        # blank and unparseable all become `None` -- "the exchange did not state
        # it" -- because the only reader requires a positive match and places
        # its own protection when it does not get one. Raising here instead
        # would take a plain order query down with it, and `get_open_orders` is
        # what reconciliation reads to decide whether protection is still live.
        def _stated_decimal(key: str) -> Optional[Decimal]:
            raw = data.get(key)
            if raw is None or isinstance(raw, bool) or str(raw).strip() == "":
                return None
            try:
                value = Decimal(str(raw).strip())
            except (ArithmeticError, TypeError, ValueError):
                return None
            # NaN and the infinities parse without error but state no price. A
            # NaN also compares unequal to every level, so letting one through
            # would read as "the exchange holds a bracket at levels we did not
            # authorise" rather than "the exchange stated nothing".
            if not value.is_finite():
                return None
            return value

        def _stated_text(key: str) -> Optional[str]:
            raw = data.get(key)
            if not isinstance(raw, str) or raw.strip() == "":
                return None
            return raw.strip()

        # Only a real boolean counts. A truthy string ("false" is truthy) must
        # never become `True` here.
        raw_bracket_flag = data.get("bracket_order")
        bracket_flag = raw_bracket_flag if isinstance(raw_bracket_flag, bool) else None

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

        # Task O §O6: the state used to be read as
        # `str(data.get("state", "OPEN"))`. §O5 closed the unknown-*value* hole
        # in `OrderStatus.from_exchange`, but absence was still answered with a
        # fabricated `OPEN`, so an order the exchange did not describe at all was
        # adopted as still resting -- the bracket logic would keep waiting for a
        # fill that may already have happened, and reconciliation would compare
        # local state against a state the exchange never reported.
        #
        # `from_exchange` documents that absence is not its problem: it "belongs
        # to whichever parse boundary observed the absence, so callers must
        # decide before calling". This is that boundary, and this is the same
        # refusal `private_websocket._normalize_order` makes on the stream side.
        # Placed after identity resolution so `UnknownInstrumentError` still wins
        # on an unusable payload.
        raw_state = data.get("state")
        if raw_state is None or str(raw_state).strip() == "":
            raw_state = data.get("status")
        if raw_state is None or str(raw_state).strip() == "":
            raise UnknownOrderStateError(
                f"order {data.get('id')!r} on {spec.symbol} arrived with no "
                f"state and no status; refusing to assume it is open")

        # Task O §O8: the exchange order id used to be read as
        # `int(data.get("id", 0))`, so an order response that did not state its
        # id was parsed as order 0 -- and because `int()` truncates, `3.7` became
        # order 3 and `True` became order 1, i.e. a DIFFERENT REAL order. An
        # identity cannot be defaulted the way a number can. Every consumer
        # stringifies this field, and `"0"` is a truthy dict key in
        # `state_store.orders`, a member of the reconciliation membership sets
        # that decide whether a local order is still open, a real REST path
        # segment (`GET /v2/orders/0`), a real cancel body (`{"id": 0}`), and a
        # value that satisfies `trade_lifecycle`'s existing "Exchange failed to
        # confirm SL/TP bracket order IDs" guard. A fabricated identity is
        # therefore silently ACTIONABLE in a way a fabricated number is not.
        #
        # Zero is refused: no repository or exchange evidence establishes it as a
        # legitimate Delta order id, and it is the exact value the old default
        # fabricated. Sign is otherwise not judged and no upper bound is
        # invented; only EXACTNESS is required, so an integral numeric string or
        # float is accepted while a fractional one is not -- truncation must
        # never be what decides which order this is. This mirrors the
        # `product_id` refusal above, and `DeltaResponseError` is the contract
        # the client already raises for an order-identity violation
        # (`get_order_by_client_id`: "refusing to adopt an order that is not
        # ours"). The import is function-local because `delta_client` imports
        # this module (the §O6/§O7 idiom).
        #
        # Placed LAST so the symbol -> product_id -> state ordering above still
        # wins on an unusable payload: `from_dict({})` remains
        # `UnknownInstrumentError`.
        from quantedge.execution.delta_client import DeltaResponseError

        raw_id = data.get("id")
        try:
            if isinstance(raw_id, bool) or raw_id is None:
                raise ValueError(raw_id)
            id_decimal = Decimal(str(raw_id).strip())
            if not id_decimal.is_finite():
                raise ValueError(raw_id)
            order_id = int(id_decimal)
            if id_decimal != order_id:
                raise ValueError(raw_id)
        except (ArithmeticError, TypeError, ValueError):
            raise DeltaResponseError(
                f"{raw_id!r} is not a usable exchange order id for "
                f"{spec.symbol}; refusing to default to order 0") from None

        if order_id == 0:
            raise DeltaResponseError(
                f"order response for {spec.symbol} reported exchange order id "
                f"0; refusing to adopt a fabricated identity as an order")

        return cls(
            id=order_id,
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
            state=OrderStatus.from_exchange(str(raw_state)),
            reduce_only=bool(data.get("reduce_only", False)),
            created_at=created_dt,
            updated_at=updated_dt,
            stop_order_type=_stated_text("stop_order_type"),
            stop_trigger_method=_stated_text("stop_trigger_method"),
            bracket_order=bracket_flag,
            bracket_stop_loss_price=_stated_decimal("bracket_stop_loss_price"),
            bracket_take_profit_price=_stated_decimal("bracket_take_profit_price"),
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

