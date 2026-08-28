"""
Manual SMC — Price Quantization (Phase 1 Step 4).
=================================================

ONE canonical tick-size quantizer for the Manual SMC strategy, and nothing
else. Every price that will ever be sent to an exchange passes through
`quantize_price`; no other function in this package rounds a price.

WHY ONE QUANTIZER
-----------------
Price rounding is currently re-implemented per call site in the pre-existing
execution package, in three mutually inconsistent ways:

    algo_config.py / capital_allocator.py
        (raw / tick).quantize(1, ROUND_HALF_UP) * tick   grid-correct, but
        half-tick behaviour is implicit and the tick defaults to a hardcoded
        Decimal("0.50").
    multi_user_orchestrator.py
        price.quantize(tick_size)                        NOT a grid snap. It
        rounds to the tick's DECIMAL PLACES, so with BTC's 0.5 tick it happily
        returns 100.3 — an off-grid price the exchange must reject.

This module is the single correct implementation. It does not modify, import
or depend on any of those call sites; migrating them is not part of Step 4.

TICK SIZE COMES FROM THE PRODUCT SPEC — NEVER FROM A SYMBOL TABLE HERE
----------------------------------------------------------------------
The tick size is read from `ProductSpecification.tick_size`
(`quantedge.execution.validation`) through the structural `TickSizeSpec`
protocol. There are NO per-symbol rounding rules in this file: no symbol
constants, no per-asset branches, no default tick. A caller that cannot
supply a tick size gets an exception, not a guess.

The protocol is structural on purpose. `execution.validation` transitively
imports `execution.synchronizer` → `execution.delta_client` (httpx, signed
Delta REST calls) and `execution.security` (AESGCM credential decryption).
Importing it here would drag live-exchange transport into the strategy
package, which Phase 1 forbids. `ProductSpecification` satisfies
`TickSizeSpec` structurally, so the real object works unchanged — the tests
prove it against the real class and all four real Delta tick sizes.

DECIMAL ONLY — NO FLOAT PRICE ROUNDING
--------------------------------------
`quantize_price` accepts `Decimal` and refuses `float` outright. All grid
arithmetic is exact `Decimal` divmod inside a high-precision local context;
no binary floating point is involved at any point.

The strategy's own prices are floats (the frozen oracle is float, and Step 1
deliberately kept it that way). `price_from_strategy_float` is the ONE
sanctioned crossing from that world into this one. It converts via
`Decimal(str(value))` — the shortest decimal string that round-trips — and
performs NO rounding of its own. `Decimal(0.1)` is deliberately not used: it
would inject binary noise (0.1000000000000000055511151231257827) into an
exchange price.

QUANTITY AND CONTRACT VALUE ARE NOT COMPUTED HERE
-------------------------------------------------
Order size, contract value, `min_size` and `size_step` are absent by design.
Delta's contract semantics are still unverified (safety rules #8 and #16);
`sizing.py` already refuses to invent them and this module does not reopen
that door. It quantizes prices — that is all.

DELIBERATELY ABSENT
-------------------
No exchange calls, no HTTP, no database, no order placement, no bracket
submission, no runtime/live wiring, no persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from typing import Protocol, Union, runtime_checkable

from quantedge.strategy.manual_smc.models import ManualOBRecord

#: Working precision for the exact divmod. Large enough that no realistic
#: price/tick pair can overflow the quotient, small enough to stay cheap.
QUANTIZE_PRECISION: int = 60

#: The two direction strings used throughout the Manual SMC package.
DIRECTION_LONG: str = "LONG"
DIRECTION_SHORT: str = "SHORT"


class QuantizationError(RuntimeError):
    """Base class for every quantization refusal. Always fails closed."""


class InvalidTickSizeError(QuantizationError):
    """Tick size is missing, non-Decimal, non-finite, zero or negative."""


class InvalidPriceError(QuantizationError):
    """Price is missing, non-Decimal, non-finite, zero or negative."""


class SubTickPriceError(QuantizationError):
    """
    The price is smaller than one tick and would quantize to zero.

    A zero price is not a tradeable price, so this is a refusal rather than a
    silently returned 0.
    """


class BracketGeometryError(QuantizationError):
    """Quantization collapsed or inverted the entry/SL/TP ordering."""


# ---------------------------------------------------------------------------
# Rounding direction and half-tick behaviour — both fully explicit.
# ---------------------------------------------------------------------------
class TickRounding(Enum):
    """
    How a price that sits between two ticks is resolved.

    Prices are validated to be strictly positive before any of these apply,
    so "down" always means toward zero and "up" always means away from zero;
    there is no signed ambiguity to resolve.

        DOWN               floor onto the grid. 100.3 @ 0.5 -> 100.0
        UP                 ceil onto the grid.  100.3 @ 0.5 -> 100.5
        NEAREST_HALF_UP    nearest tick; an EXACT half-tick goes UP.
                           100.25 @ 0.5 -> 100.5
        NEAREST_HALF_DOWN  nearest tick; an EXACT half-tick goes DOWN.
                           100.25 @ 0.5 -> 100.0

    There is intentionally no `NEAREST` alias: "nearest" alone does not say
    what happens on the half-tick, and an unstated tie rule is exactly the
    kind of ambiguity that must be verified rather than assumed.
    """
    DOWN = "DOWN"
    UP = "UP"
    NEAREST_HALF_UP = "NEAREST_HALF_UP"
    NEAREST_HALF_DOWN = "NEAREST_HALF_DOWN"


@runtime_checkable
class TickSizeSpec(Protocol):
    """
    Anything carrying an exchange tick size — structurally satisfied by
    `quantedge.execution.validation.ProductSpecification`.
    """
    tick_size: Decimal


# ---------------------------------------------------------------------------
# Validation. Every entry point validates; none of them coerce.
# ---------------------------------------------------------------------------
def validate_tick_size(tick_size: object) -> Decimal:
    """
    Return the tick size, or raise `InvalidTickSizeError`.

    `float` is rejected as firmly as a string: a float tick size cannot
    express 0.05 or 0.0001 exactly, and admitting one here would make the
    whole grid approximate.
    """
    if isinstance(tick_size, bool) or not isinstance(tick_size, Decimal):
        raise InvalidTickSizeError(
            f"tick size must be a Decimal, got {type(tick_size).__name__} "
            f"{tick_size!r}; float tick sizes are refused because they cannot "
            f"represent Delta's 0.05 / 0.0001 grids exactly")
    if not tick_size.is_finite():
        raise InvalidTickSizeError(f"tick size is not finite: {tick_size!r}")
    if tick_size <= 0:
        raise InvalidTickSizeError(
            f"tick size must be strictly positive, got {tick_size!r}")
    return tick_size


def validate_price(price: object) -> Decimal:
    """Return the price, or raise `InvalidPriceError`. Decimal only."""
    if isinstance(price, bool) or not isinstance(price, Decimal):
        raise InvalidPriceError(
            f"price must be a Decimal, got {type(price).__name__} {price!r}; "
            f"cross from the strategy's float world with "
            f"price_from_strategy_float() so the conversion is explicit")
    if not price.is_finite():
        raise InvalidPriceError(f"price is not finite: {price!r}")
    if price <= 0:
        raise InvalidPriceError(
            f"price must be strictly positive, got {price!r}")
    return price


def tick_size_of(spec: TickSizeSpec) -> Decimal:
    """
    Read and validate `spec.tick_size`.

    A spec without the attribute raises rather than falling back to a default
    tick — there is no default tick anywhere in this module.
    """
    try:
        raw = spec.tick_size
    except AttributeError as exc:
        raise InvalidTickSizeError(
            f"{type(spec).__name__} exposes no tick_size; a product "
            f"specification is required and no default tick exists") from exc
    return validate_tick_size(raw)


def price_from_strategy_float(value: Union[float, int]) -> Decimal:
    """
    The ONE sanctioned float -> Decimal crossing. Converts, never rounds.

    Uses `Decimal(str(value))`, the shortest decimal string that round-trips
    the float, so 0.1 becomes exactly Decimal("0.1"). `Decimal(0.1)` would
    instead yield 0.1000000000000000055511151231257827 and leak binary noise
    into an exchange price.

    This function performs NO tick rounding: the result is still an arbitrary
    off-grid price and must be passed through `quantize_price`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidPriceError(
            f"expected a float or int strategy price, got "
            f"{type(value).__name__} {value!r}")
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidPriceError(
            f"cannot represent {value!r} as a Decimal price") from exc
    return validate_price(converted)


# ---------------------------------------------------------------------------
# THE canonical quantizer. Nothing else in this package rounds a price.
# ---------------------------------------------------------------------------
def quantize_price(
    price: Decimal,
    tick_size: Decimal,
    rounding: TickRounding,
) -> Decimal:
    """
    Snap `price` onto the `tick_size` grid using `rounding`.

    `rounding` is required — there is no default, because a silent default
    would decide risk direction on the caller's behalf.

    Exact by construction: `divmod` on two Decimals inside a precision-60
    local context yields `units * tick + remainder == price` with
    `0 <= remainder < tick`, so the grid decision is made on an exact
    remainder and the result is an exact integer multiple of the tick. The
    returned Decimal is re-expressed at the tick's own scale, so BTC's 0.5
    grid yields "100.5" and XRP's 0.0001 grid yields "0.5000".

    Raises `SubTickPriceError` rather than returning 0 when a positive price
    rounds off the bottom of the grid.
    """
    p = validate_price(price)
    tick = validate_tick_size(tick_size)
    if not isinstance(rounding, TickRounding):
        raise QuantizationError(
            f"rounding must be a TickRounding member, got "
            f"{type(rounding).__name__} {rounding!r}")

    with localcontext() as ctx:
        ctx.prec = QUANTIZE_PRECISION
        try:
            units, remainder = divmod(p, tick)
        except (InvalidOperation, ArithmeticError) as exc:
            raise QuantizationError(
                f"cannot place price {p} on the {tick} grid exactly at "
                f"precision {QUANTIZE_PRECISION}") from exc

        if remainder == 0:
            chosen = units
        elif rounding is TickRounding.DOWN:
            chosen = units
        elif rounding is TickRounding.UP:
            chosen = units + 1
        else:
            twice = remainder * 2
            if twice > tick:
                chosen = units + 1
            elif twice < tick:
                chosen = units
            else:                                  # exact half tick
                chosen = (units + 1
                          if rounding is TickRounding.NEAREST_HALF_UP
                          else units)

        result = chosen * tick

        if result <= 0:
            raise SubTickPriceError(
                f"price {p} is below one tick ({tick}) and rounding "
                f"{rounding.value} takes it to {result}; a zero price is not "
                f"tradeable")
        # Defensive invariants. Neither can trigger given exact divmod; they
        # exist so a future change cannot silently emit an off-grid price.
        if result % tick != 0:
            raise QuantizationError(
                f"internal error: {result} is not a multiple of {tick}")
        if abs(result - p) >= tick:
            raise QuantizationError(
                f"internal error: {result} is more than one tick from {p}")
    return result


def is_on_tick_grid(price: Decimal, tick_size: Decimal) -> bool:
    """True when `price` is already an exact multiple of `tick_size`."""
    p = validate_price(price)
    tick = validate_tick_size(tick_size)
    with localcontext() as ctx:
        ctx.prec = QUANTIZE_PRECISION
        return p % tick == 0


# ---------------------------------------------------------------------------
# Which direction is conservative for each leg of a Manual SMC bracket.
# ---------------------------------------------------------------------------
class PriceRole(Enum):
    """The role a price plays in a Manual SMC bracket."""
    ENTRY = "ENTRY"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


#: (role, direction) -> rounding. Rule: the ENTRY rounds AWAY from the
#: position's profit direction, and the SL and TP round TOWARD the entry.
#:
#: The load-bearing consequence is on the stop: because the entry moves toward
#: the SL and the SL moves toward the entry, |entry - SL| can only shrink.
#: `sizing.py` derived `applied_leverage` from the UNQUANTIZED SL distance
#: under a 35% risk budget, so a distance that could widen would let a stop
#: cost more than 35% of the balance. This table makes that impossible.
_CONSERVATIVE_ROUNDING = {
    (PriceRole.ENTRY, DIRECTION_LONG): TickRounding.DOWN,
    (PriceRole.ENTRY, DIRECTION_SHORT): TickRounding.UP,
    (PriceRole.STOP_LOSS, DIRECTION_LONG): TickRounding.UP,
    (PriceRole.STOP_LOSS, DIRECTION_SHORT): TickRounding.DOWN,
    (PriceRole.TAKE_PROFIT, DIRECTION_LONG): TickRounding.DOWN,
    (PriceRole.TAKE_PROFIT, DIRECTION_SHORT): TickRounding.UP,
}


def validate_direction(direction: object) -> str:
    """Accept exactly "LONG" or "SHORT". No case folding, no aliases."""
    if direction not in (DIRECTION_LONG, DIRECTION_SHORT):
        raise QuantizationError(
            f"direction must be {DIRECTION_LONG!r} or {DIRECTION_SHORT!r}, "
            f"got {direction!r}")
    return str(direction)


def conservative_rounding(role: PriceRole, direction: str) -> TickRounding:
    """The rounding that cannot flatter this leg's price. Raises on garbage."""
    if not isinstance(role, PriceRole):
        raise QuantizationError(
            f"role must be a PriceRole member, got {role!r}")
    return _CONSERVATIVE_ROUNDING[(role, validate_direction(direction))]


@dataclass(frozen=True)
class QuantizedBracket:
    """
    One bracket, on-grid, with the raw inputs retained for audit.

    `risk_dist` is guaranteed <= the unquantized risk distance.
    `reward_dist` is NOT monotone: the entry and the TP both move, so the
    reward distance can differ from the raw one by up to one tick in either
    direction. Only the risk side carries a directional guarantee.
    """
    asset: str
    direction: str
    tick_size: Decimal
    entry_price: Decimal
    sl_price: Decimal
    tp_price: Decimal
    raw_entry_price: Decimal
    raw_sl_price: Decimal
    raw_tp_price: Decimal
    entry_rounding: TickRounding
    sl_rounding: TickRounding
    tp_rounding: TickRounding

    @property
    def risk_dist(self) -> Decimal:
        return abs(self.entry_price - self.sl_price)

    @property
    def reward_dist(self) -> Decimal:
        return abs(self.tp_price - self.entry_price)


def quantize_bracket(
    asset: str,
    direction: str,
    entry_price: Decimal,
    sl_price: Decimal,
    tp_price: Decimal,
    tick_size: Decimal,
) -> QuantizedBracket:
    """
    Quantize all three legs conservatively, then re-check the geometry.

    Each leg goes through `quantize_price` — the single canonical quantizer —
    with the direction from `_CONSERVATIVE_ROUNDING`. Because the entry and
    the stop move toward each other, a bracket narrower than about two ticks
    can collapse or invert; that raises `BracketGeometryError` instead of
    producing a stop on the wrong side of the entry.
    """
    validate_direction(direction)
    tick = validate_tick_size(tick_size)
    legs = {}
    for role, raw in ((PriceRole.ENTRY, entry_price),
                      (PriceRole.STOP_LOSS, sl_price),
                      (PriceRole.TAKE_PROFIT, tp_price)):
        rounding = conservative_rounding(role, direction)
        legs[role] = (quantize_price(raw, tick, rounding), rounding)

    entry_q, entry_r = legs[PriceRole.ENTRY]
    sl_q, sl_r = legs[PriceRole.STOP_LOSS]
    tp_q, tp_r = legs[PriceRole.TAKE_PROFIT]

    if direction == DIRECTION_LONG:
        ordered = tp_q > entry_q > sl_q
        shape = f"TP ({tp_q}) > entry ({entry_q}) > SL ({sl_q})"
    else:
        ordered = tp_q < entry_q < sl_q
        shape = f"TP ({tp_q}) < entry ({entry_q}) < SL ({sl_q})"
    if not ordered:
        raise BracketGeometryError(
            f"{asset} {direction}: quantizing onto the {tick} grid destroyed "
            f"the bracket geometry — require {shape}. Raw legs were "
            f"entry={entry_price} sl={sl_price} tp={tp_price}; the bracket is "
            f"too narrow for this tick size and must not be sent.")

    return QuantizedBracket(
        asset=asset,
        direction=direction,
        tick_size=tick,
        entry_price=entry_q,
        sl_price=sl_q,
        tp_price=tp_q,
        raw_entry_price=validate_price(entry_price),
        raw_sl_price=validate_price(sl_price),
        raw_tp_price=validate_price(tp_price),
        entry_rounding=entry_r,
        sl_rounding=sl_r,
        tp_rounding=tp_r,
    )


def quantize_ob_bracket(
    ob: ManualOBRecord,
    spec: TickSizeSpec,
) -> QuantizedBracket:
    """
    Quantize a `ManualOBRecord`'s float bracket against a product spec.

    This is the documented strategy -> exchange boundary: the OB's float
    prices cross into Decimal via `price_from_strategy_float` (representation
    only) and are then snapped by `quantize_price` (the only rounding step).
    The OB itself is never mutated, so the float lifecycle and every oracle
    equivalence property are untouched.
    """
    tick = tick_size_of(spec)
    return quantize_bracket(
        asset=ob.asset,
        direction=validate_direction(ob.direction),
        entry_price=price_from_strategy_float(ob.entry_price),
        sl_price=price_from_strategy_float(ob.sl_price),
        tp_price=price_from_strategy_float(ob.tp_price),
        tick_size=tick,
    )


__all__ = [
    "QUANTIZE_PRECISION",
    "DIRECTION_LONG",
    "DIRECTION_SHORT",
    "QuantizationError",
    "InvalidTickSizeError",
    "InvalidPriceError",
    "SubTickPriceError",
    "BracketGeometryError",
    "TickRounding",
    "TickSizeSpec",
    "PriceRole",
    "QuantizedBracket",
    "validate_tick_size",
    "validate_price",
    "validate_direction",
    "tick_size_of",
    "price_from_strategy_float",
    "quantize_price",
    "is_on_tick_grid",
    "conservative_rounding",
    "quantize_bracket",
    "quantize_ob_bracket",
]
