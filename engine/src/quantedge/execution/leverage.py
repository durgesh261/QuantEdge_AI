"""
The authoritative leverage band for QuantEdge AI: 1x .. 100x inclusive.

WHY THIS MODULE EXISTS AT ALL
-----------------------------
The band used to be four independent literals -- `validation.py`'s per-symbol
policy table, `algo_config.py`'s `_validate`, `capital_allocator.py`'s guard,
and the `RiskConfiguration` default -- which is how they drifted apart. Three
of the four agreed on 1..100 while the per-symbol table capped SOLUSD and
XRPUSD at 50x, and no test compared them. One module holds the two numbers now
so a future edit cannot move one copy and leave another behind.

It deliberately imports nothing. `validation.py` builds
`DEFAULT_DELTA_INDIA_PRODUCTS` at import time and therefore needs the Delta
India snapshot present on disk; if the band lived there, `algo_config.py` --
which is pure configuration and touches no instrument data -- would inherit
that requirement just to learn the number 100.

THE BAND IS LOCAL POLICY, NOT AN EXCHANGE FACT
----------------------------------------------
Delta India publishes no leverage ceiling. `max_leverage` is listed in
`quantedge.instruments.PERMANENTLY_UNVERIFIED` and
`InstrumentSpec.max_leverage` raises `FieldUnverifiedError` rather than answer.
What the snapshot does record, unhashed, is `margin_and_limits.default_leverage`
-- 200 for BTCUSD and ETHUSD, 100 for SOLUSD and XRPUSD -- which establishes
only that `MAX_LEVERAGE = 100` is no looser than a figure Delta itself records.
That is corroboration of the direction, not verification of the value. Do not
relabel these constants as exchange-verified, and do not derive anything else
from `default_leverage`.

THE CONTRACT
------------
Two entry points, because a stored ceiling and a submitted request are not the
same question.

`validate_leverage` / `is_within_band` guard STORED CONFIGURATION. They are
fail-closed and substitute nothing:

    1x .. 100x   accepted, returned verbatim
    < 1x         rejected -- 0 does NOT become 1x
    > 100x       rejected -- 101 does NOT become 100x
    None         rejected -- "unset" is a caller-level meaning, so a caller
                 that has one must resolve it to an explicit int first
    non-int      rejected -- every stored ceiling in the repository is an int
                 (`RiskConfiguration.max_leverage`, the `max_leverage INTEGER`
                 column, Java's `Integer maxLeverage`), so a float or `Decimal`
                 arriving here means something upstream lost the type

`normalize_requested_leverage` guards a SUBMITTED REQUEST, where the value has
crossed a JSON or UI boundary and may legitimately arrive as `100.0` or
`Decimal("100")`. It returns the integer the request asked for:

    int in band              returned verbatim
    integral float/Decimal   returned as the equivalent int -- 100.0 -> 100
    fractional               rejected -- 1.5x is not a leverage the repository
                             can express; `OrderValidationRequest.leverage` is
                             `Optional[int]` and the column is INTEGER
    NaN / infinity           rejected before any comparison, because both slip
                             silently past `< MIN` and `> MAX`
    bool / str / anything     rejected

Range-checking stays with the caller: gateway check 14 composes
`min(spec.max_leverage, risk_config.max_leverage)` and needs to report which of
the two ceilings was hit, so this function normalises the type and nothing else.

`bool` is refused everywhere. `isinstance(True, int)` is True in Python, so
without an explicit guard `True` reads as a valid 1x while `False` reads as an
out-of-band 0 -- an ambiguous value answering a safety question. `True` is not
a leverage.

A per-symbol or per-account cap may be STRICTER than `MAX_LEVERAGE` -- gateway
check 14 still takes `min(spec, risk_config)`. Nothing may be LOOSER.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

#: Smallest leverage that is a trade. Below this there is no position.
MIN_LEVERAGE: int = 1

#: Authoritative maximum. Raising this raises the ceiling for every symbol on
#: every path; it is not a tuning knob.
MAX_LEVERAGE: int = 100


class LeverageBandError(ValueError):
    """Requested leverage is outside the authoritative 1x..100x band."""


def is_within_band(leverage: Any) -> bool:
    """True only for a non-bool `int` in `MIN_LEVERAGE..MAX_LEVERAGE` inclusive.

    Answers False for `None`, `bool`, floats, strings and `Decimal` rather than
    coercing them, so an ambiguous type can never read as in-band.
    """
    if isinstance(leverage, bool) or not isinstance(leverage, int):
        return False
    return MIN_LEVERAGE <= leverage <= MAX_LEVERAGE


def validate_leverage(leverage: Any, *, field_name: str = "leverage") -> int:
    """Return `leverage` unchanged, or raise `LeverageBandError`.

    Never clamps and never substitutes: the caller gets back the exact integer
    it passed, or an exception naming the band it missed.
    """
    if isinstance(leverage, int) and not isinstance(leverage, bool):
        if MIN_LEVERAGE <= leverage <= MAX_LEVERAGE:
            return leverage
        raise LeverageBandError(
            f"{field_name} must be between {MIN_LEVERAGE} and {MAX_LEVERAGE} "
            f"inclusive, got {leverage}")
    raise LeverageBandError(
        f"{field_name} must be an int between {MIN_LEVERAGE} and "
        f"{MAX_LEVERAGE} inclusive, got {leverage!r} "
        f"({type(leverage).__name__})")


def normalize_requested_leverage(leverage: Any,
                                 *, field_name: str = "leverage") -> int:
    """The whole number of turns a request asked for, or `LeverageBandError`.

    Accepts an `int`, or a `float`/`Decimal` that is exactly integral, and
    returns the equivalent `int`. Everything else is refused, including `bool`,
    `None`, strings, NaN and infinity.

    Deliberately does NOT range-check. The gateway composes the instrument cap
    and the account cap into one ceiling and reports which was hit, so widening
    this to a band check would either duplicate that message or lose it.
    """
    if isinstance(leverage, bool):
        raise LeverageBandError(
            f"Requested {field_name} {leverage!r} is a bool, not a leverage.")
    if isinstance(leverage, int):
        return leverage
    if isinstance(leverage, (float, Decimal)):
        try:
            finite = (leverage == leverage) and (leverage - leverage == 0)
        except (InvalidOperation, ValueError, ArithmeticError):
            finite = False
        if not finite:
            raise LeverageBandError(
                f"Requested {field_name} {leverage!r} is not a finite number. "
                f"NaN and infinity are refused before any range comparison, "
                f"because neither is reported as out of band by `<` or `>`.")
        as_int = int(leverage)
        if as_int != leverage:
            raise LeverageBandError(
                f"Requested {field_name} {leverage!r} is not a whole number of "
                f"turns. Leverage is an integer {MIN_LEVERAGE}x..{MAX_LEVERAGE}x; "
                f"fractional leverage is not representable.")
        return as_int
    raise LeverageBandError(
        f"Requested {field_name} must be a whole number between "
        f"{MIN_LEVERAGE} and {MAX_LEVERAGE} inclusive, got {leverage!r} "
        f"({type(leverage).__name__}).")


__all__ = [
    "MIN_LEVERAGE",
    "MAX_LEVERAGE",
    "LeverageBandError",
    "is_within_band",
    "normalize_requested_leverage",
    "validate_leverage",
]
