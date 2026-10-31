"""
Manual SMC -- LuxAlgo internal-structure reconstruction (parity detector).
========================================================================

A faithful, bar-by-bar Python reconstruction of the structure/order-block
execution path that the LuxAlgo "Smart Money Concepts [LuxAlgo]" Pine v5
indicator actually runs under the OPERATOR'S OWN TradingView settings:

    updateTrailingExtremes()             Show Strong/Weak High/Low = ON
    getCurrentStructure(50, false)       swing layer -- required because the
                                         internal layer's `extraCondition`
                                         compares internal vs swing levels,
                                         even though Swing OBs are OFF
    getCurrentStructure(5, false, true)  internal layer  (Internal Structure ON)
    getCurrentStructure(3, true)         Equal High/Low, Bars Confirmation 3
    displayStructure(true)               internal BOS/CHoCH + internal OB store
    displayStructure()                   swing BOS/CHoCH (swing OBs OFF)
    deleteOrderBlocks(true)              internal OB mitigation, High/Low

THIS MODULE IS PURE. No I/O, no exchange access, no credentials, no
environment reads, no clock, no randomness. `step()` is a deterministic
function of the candles it has already been shown.

THIS MODULE IS NOW THE PRODUCTION DETECTOR'S ENGINE. By an explicit authorised
decision it drives `LuxAlgoInternalOBScanner`, which is the default detector on
the production Manual SMC path; `ManualSpecBOSScanner` was ruled
non-representative of the operator's TradingView setup and is now reachable only
by NAMING `DETECTOR_MANUAL_SPEC` (see `lifecycle.py`). Nothing about the
reconstruction changed when it was promoted: the Pine semantics below are the
contract, and they may only be altered if a test proves an actual bug in the
reproduction -- never to make a downstream number look better.

PINE SEMANTICS REPRODUCED DELIBERATELY
--------------------------------------
  * `var leg = 0` is per CALL SITE, so the 50 / 5 / 3 invocations each carry
    an INDEPENDENT leg state. Sharing one would fuse three detectors.
  * `ta.highest(size)` / `ta.lowest(size)` INCLUDE the current bar, so
    `leg()` is a one-sided (right-hand-only) pivot: the bar `size` back must
    beat every one of the `size` bars after it. There is no left-side test.
  * a pivot is recorded `size` bars IN ARREARS: `barIndex = bar_index - size`.
  * `ta.change(leg)` is `na` on the first bar, so no pivot can fire there.
  * `ta.atr(200)` == `ta.rma(ta.tr, 200)`: `na` for the first 199 bars, the
    SMA of the first 200 true ranges at bar 199, Wilder recursion after. A
    comparison against `na` is false, so no warm-up bar is high-volatility.
  * `parsedHigh` / `parsedLow` are SWAPPED on a high-volatility bar. That
    swap IS the ATR filter: a volatile bar gets a tiny parsedHigh and a huge
    parsedLow, so it almost never wins the argmax/argmin -- and when it does
    win anyway the stored box is INVERTED (`barHigh < barLow`).
  * `parsedHighs.slice(pivot.barIndex, bar_index)` is `to`-EXCLUSIVE, so the
    BREAK BAR IS EXCLUDED from the origin search.
  * `array.indexof(array.max())` returns the FIRST occurrence, so ties
    resolve to the EARLIEST bar.
  * the bearish branch reads the trend bias AFTER the bullish branch may
    already have flipped it on the same bar.
  * `orderBlocks.unshift()` puts the newest at index 0; `pop()` drops the
    OLDEST; the algorithmic store is capped at 100.
  * `deleteOrderBlocks()` removes while iterating, which SKIPS the element
    that shifts into the freed slot.
  * `parsedHighs` / `parsedLows` are unbounded Pine arrays and are kept
    unbounded here. The origin search reaches back to a pivot that may be
    arbitrarily old, so a bounded ring buffer would silently truncate it.
    SNAPSHOTTING is a separate question and has a separate answer: see
    `capture_state()`, which drops only the prefix that a proof shows can
    never be read again. The LIVE object still keeps everything.

MITIGATION IS NOT INVALIDATION. LuxAlgo's High/Low mitigation removes a BOX
FROM A CHART. It is reproduced here because the 100-block cap, the eviction
order and the reported mitigation bar all depend on it -- but it is NOT
forwarded to the trading lifecycle. An order block still dies only from
price, from time after its first touch, from a trade, or from an operator.
No age rule and no structure-driven deletion is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields
from datetime import datetime
from typing import (
    Any, ClassVar, Dict, FrozenSet, List, Optional, Tuple)

# --- Pine constants (verbatim names/values from the source) -----------------
BULLISH_LEG: int = 1
BEARISH_LEG: int = 0
BULLISH: int = 1
BEARISH: int = -1

# --- the operator's exact TradingView configuration -------------------------
# These are NOT tunables. They are the settings the operator actually runs, and
# the reconstruction is only a parity reference while they match. Changing one
# stops this module reproducing the operator's chart.
SWINGS_LENGTH: int = 50            # Swing Length
INTERNAL_LENGTH: int = 5           # Internal Structure length
EQUAL_LENGTH: int = 3              # Equal High/Low, Bars Confirmation
EQUAL_THRESHOLD: float = 0.1       # Equal High/Low, Threshold
ATR_LENGTH: int = 200              # ta.atr(200) in the Pine source
ATR_HIGH_VOL_MULT: float = 2.0     # (high - low) >= 2 * atr
OB_MITIGATION_HIGH_LOW: bool = True  # Order Block Mitigation = High/Low
SHOW_INTERNAL_OB: bool = True       # Internal Order Blocks = ON
SHOW_SWING_OB: bool = False         # Swing Order Blocks = OFF
INTERNAL_OB_DISPLAY: int = 20       # visible box count (display only)
CONFLUENCE_FILTER: bool = False     # Internal Confluence Filter = OFF
ALGO_OB_CAP: int = 100              # hard-coded in the Pine source

TAG_BOS: str = "BOS"
TAG_CHOCH: str = "CHoCH"

# Pine's `for [index, x] in array` keeps incrementing `index` after
# `array.remove(index)` shrank the array, so the element that shifts into the
# freed slot is never tested on that bar. That is what the published script
# does, so it is what parity requires. Flipping this to True would "clean up"
# the quirk and stop reproducing the operator's chart, which is why it is a
# module constant and not a constructor argument.
CLEAN_MITIGATION: bool = False

# --- snapshot vocabulary ----------------------------------------------------
# The six Pine `var pivot`s and the three per-call-site `var leg`s, named once
# so `capture_state()` and `restore_state()` cannot enumerate different sets.
# Order is fixed so a snapshot of the same state is byte-identical every time.
_PIVOT_NAMES: Tuple[str, ...] = (
    "swing_high", "swing_low", "internal_high", "internal_low",
    "equal_high", "equal_low",
)
_LEG_NAMES: Tuple[Tuple[str, str], ...] = (
    ("swing", "_leg_swing"), ("internal", "_leg_internal"),
    ("equal", "_leg_equal"),
)
#: Keys of the `_PineATR` sub-block. Its state is six scalars behind
#: `__slots__`, and Wilder's recursion means they cannot be recomputed from a
#: truncated window -- which is the whole reason this codec exists.
_ATR_KEYS: Tuple[str, ...] = (
    "length", "value", "count", "warm_sum", "prev_close", "has_prev")
_PIVOT_KEYS: Tuple[str, ...] = (
    "currentLevel", "lastLevel", "crossed", "barTime", "barIndex")
_TRAILING_KEYS: Tuple[str, ...] = (
    "top", "bottom", "barTime", "barIndex", "lastTopTime", "lastBottomTime")
_CROSSOVER_KEYS: Tuple[str, ...] = (
    "close", "internal_high", "internal_low", "swing_high", "swing_low")
#: Top-level keys of a `capture_state()` blob.
LUXALGO_SNAPSHOT_KEYS: Tuple[str, ...] = (
    "asset", "stream_offset", "base_bar_idx", "last_bar_idx", "atr", "pivots",
    "trends", "trailing", "legs", "crossover", "ob_seq",
    "live_order_blocks", "arrays",
)


class LuxAlgoStructureError(ValueError):
    """A candle stream that this reconstruction refuses to process.

    Raised instead of guessing. The origin search slices an unbounded array by
    absolute bar index, so a gap, a repeat or an out-of-order bar would
    silently select the WRONG origin candle rather than fail. Safety rule #15:
    an unknown fails closed.
    """


class LuxAlgoPivot:
    """Pine `type pivot`: currentLevel, lastLevel, crossed, barTime, barIndex.

    `barIndex` is a POSITION in this object's own arrays (0 == the first candle
    ever shown to `step()`), not the caller's `bar_idx`. The two are related by
    `_base_bar_idx` and only differ when the caller does not start at 0.
    """

    __slots__ = ("currentLevel", "lastLevel", "crossed", "barTime", "barIndex")

    def __init__(self) -> None:
        self.currentLevel: Optional[float] = None   # na
        self.lastLevel: Optional[float] = None      # na
        self.crossed: bool = False
        self.barTime: Optional[datetime] = None     # na
        self.barIndex: Optional[int] = None         # na


class LuxAlgoTrend:
    """Pine `type trend`: bias. `var trend = trend.new()` leaves bias == 0.

    Zero is NEITHER bullish nor bearish, and that matters: the first break in a
    symbol's history compares `bias == BEARISH` / `bias == BULLISH` against 0,
    both of which are false, so the first internal break is always tagged BOS.
    """

    __slots__ = ("bias",)

    def __init__(self) -> None:
        self.bias: int = 0


class LuxAlgoTrailing:
    """Pine `type trailing` -- Strong/Weak High/Low bookkeeping.

    Written by `updateTrailingExtremes()` every bar AND overwritten by the
    swing (size-50, non-equal) branch of `getCurrentStructure`. It feeds no
    trading decision here; it is retained because it is the only cheap,
    independent cross-check against the operator's visible Strong High and
    Strong Low labels.
    """

    __slots__ = ("top", "bottom", "barTime", "barIndex",
                 "lastTopTime", "lastBottomTime")

    def __init__(self) -> None:
        self.top: Optional[float] = None
        self.bottom: Optional[float] = None
        self.barTime: Optional[datetime] = None
        self.barIndex: Optional[int] = None
        self.lastTopTime: Optional[datetime] = None
        self.lastBottomTime: Optional[datetime] = None


class _LegState:
    """`var leg = 0` inside Pine's `leg()`.

    Pine gives every CALL SITE its own copy of a `var`, so the 50 / 5 / 3
    invocations carry three INDEPENDENT states. Sharing one object would fuse
    three detectors into one and silently destroy parity, which is why this is
    a separate object per call site rather than a field on the structure.

    `prev is None` reproduces `leg[1]` being `na` on the first bar, so
    `ta.change(leg)` is `na` there and no pivot can fire on bar 0.
    """

    __slots__ = ("leg", "prev")

    def __init__(self) -> None:
        self.leg: int = BEARISH_LEG     # var leg = 0
        self.prev: Optional[int] = None


@dataclass
class LuxAlgoOrderBlock:
    """One entry of Pine's `internalOrderBlocks` array, plus an audit trail.

    `barHigh` / `barLow` / `barTime` / `bias` are the four fields Pine's
    `type orderBlock` actually carries. Everything after them is provenance
    that Pine does not need because it draws a box and forgets; the engine
    needs it to build a `ManualOBRecord` and to be auditable afterwards.

    THE ZONE IS `[barLow, barHigh]` AS STORED, NOT SORTED. On a
    high-volatility origin candle `parsedHigh`/`parsedLow` are swapped, so
    `barHigh < barLow` and the box is INVERTED. Pine draws the inverted box
    happily. The engine must NOT trade it, and `scanner.py` rejects it by an
    explicit named rule rather than quietly re-sorting the edges into a
    plausible-looking zone.
    """

    barHigh: float
    barLow: float
    barTime: datetime
    bias: int
    # --- provenance (not in the Pine type) ---------------------------------
    asset: str = ""
    origin_bar_idx: int = -1        # absolute caller bar index of the origin
    origin_dt: Optional[datetime] = None
    created_bar_idx: int = -1       # absolute caller bar index of the break
    created_dt: Optional[datetime] = None
    tag: str = ""                   # TAG_BOS or TAG_CHOCH
    pivot_bar_idx: int = -1         # absolute caller bar index of the pivot
    pivot_level: Optional[float] = None
    origin_high_volatility: bool = False
    mitigated_bar_idx: Optional[int] = None
    mitigated_dt: Optional[datetime] = None
    seq: int = -1                   # creation order within this instance

    @property
    def is_inverted(self) -> bool:
        """True when the stored box is strictly upside down.

        Only a high-volatility origin candle can do this, via the
        parsedHigh/parsedLow swap. Measured over the full four-symbol history:
        BTC 2, ETH 1, SOL 3, XRP 5.

        This is NOT the same question as "can this be a trade zone". A doji
        origin (`high == low`) gives a zero-width box that is not inverted but
        is equally untradeable; `has_tradeable_width` is the test for that.
        """
        return self.barHigh < self.barLow

    @property
    def has_tradeable_width(self) -> bool:
        """True when the box has a strictly positive width.

        False for both an inverted box and a zero-width doji box (XRP bar 335,
        `high == low == 0.4738`, is the one real example in the four-symbol
        history). The scanner refuses both by an explicit named rule.
        """
        return self.barHigh > self.barLow

    @property
    def width(self) -> float:
        """Signed width. NEGATIVE on an inverted box, deliberately."""
        return self.barHigh - self.barLow

    @property
    def direction(self) -> str:
        """The engine's word for `bias`. A BEARISH break sells the zone."""
        return "SHORT" if self.bias == BEARISH else "LONG"


class _PineATR:
    """Streaming `ta.atr(length)`, which is `ta.rma(ta.tr, length)`.

    `ta.tr` on the very first bar is `high - low`, because `close[1]` is `na`.
    `ta.rma` is `na` until index `length - 1`, where it equals the SIMPLE mean
    of the first `length` true ranges; from then on it is Wilder's recursion
    with alpha = 1/length.

    The warm-up mean is accumulated left-to-right in exactly the bar order the
    batch reference sums it, so the two agree to the last bit rather than to
    some tolerance. `value` is `None` while Pine would hold `na`, and every
    comparison against `na` is false in Pine, so no warm-up bar can ever be
    classified high-volatility.
    """

    __slots__ = ("length", "value", "_count", "_warm_sum", "_prev_close",
                 "_has_prev")

    def __init__(self, length: int = ATR_LENGTH) -> None:
        if length < 1:
            raise LuxAlgoStructureError(
                f"ATR length must be >= 1, got {length!r}")
        self.length = length
        self.value: Optional[float] = None
        self._count = 0
        self._warm_sum = 0.0
        self._prev_close = 0.0
        self._has_prev = False

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        if self._has_prev:
            pc = self._prev_close
            tr = max(high - low, abs(high - pc), abs(low - pc))
        else:
            tr = high - low                 # close[1] is na on the first bar
        self._prev_close = close
        self._has_prev = True
        self._count += 1
        if self._count < self.length:
            self._warm_sum += tr
            self.value = None               # ta.rma is still na
        elif self._count == self.length:
            self._warm_sum += tr
            self.value = self._warm_sum / self.length
        else:
            prior = self.value
            assert prior is not None        # unreachable: count > length
            self.value = (prior * (self.length - 1) + tr) / self.length
        return self.value


def _pine_ne(a: Optional[float], b: Optional[float]) -> bool:
    """Pine `a != b` where either side may be `na`.

    Reachable only before the first swing pivot exists, i.e. inside the first
    ~50 bars of a symbol's history, which is inside the 200-bar ATR warm-up
    anyway. Measured over the full four-symbol history, `extraCondition`
    suppressed ZERO breaks, so this convention is a no-op on the operator's
    data -- but it is implemented rather than assumed away, because "no-op on
    the data I happened to measure" is not "no-op".
    """
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return a != b


def _leg_step(state: _LegState, pos: int, size: int,
              highs: List[float], lows: List[float]) -> Tuple[bool, bool, bool]:
    """Pine `leg(size)` + startOfNewLeg / startOfBearishLeg / startOfBullishLeg.

        newLegHigh = high[size] > ta.highest(size)
        newLegLow  = low[size]  < ta.lowest(size)

    `ta.highest(size)` and `ta.lowest(size)` INCLUDE the current bar, so this
    is a one-sided (right-hand-only) pivot: the bar `size` back must beat every
    one of the `size` bars that followed it. There is NO left-side test, which
    is why the pivot count is roughly a third of a symmetric pivot's.

    Before bar `size`, `high[size]` is `na` and a comparison against `na` is
    false in Pine, so neither leg can start.

    Returns `(newPivot, pivotLow, pivotHigh)`.
    """
    if pos >= size:
        newLegHigh = highs[pos - size] > max(highs[pos - size + 1:pos + 1])
        newLegLow = lows[pos - size] < min(lows[pos - size + 1:pos + 1])
    else:
        newLegHigh = False
        newLegLow = False

    if newLegHigh:
        state.leg = BEARISH_LEG
    elif newLegLow:
        state.leg = BULLISH_LEG

    cur = state.leg
    change = None if state.prev is None else (cur - state.prev)
    state.prev = cur
    new_pivot = change is not None and change != 0
    pivot_low = change == +1       # startOfBullishLeg -> a pivot LOW is set
    pivot_high = change == -1      # startOfBearishLeg -> a pivot HIGH is set
    return new_pivot, pivot_low, pivot_high


def _require_keys(body: object, keys: Tuple[str, ...], where: str) -> None:
    """Refuse a snapshot sub-block whose key set is not EXACTLY `keys`.

    Both directions matter. A missing key means a field would silently keep its
    constructor default; an unknown key means the snapshot carries state this
    build has no place for. Either way the restored object would not be the
    captured one, so both refuse (safety rule #15).
    """
    if not isinstance(body, dict):
        raise LuxAlgoStructureError(
            f"{where}: expected a mapping, got {type(body).__name__}")
    want, got = set(keys), set(body)
    if want != got:
        raise LuxAlgoStructureError(
            f"{where}: key set mismatch; missing {sorted(want - got)}, "
            f"unexpected {sorted(got - want)}")


def _require_int(value: object, where: str) -> int:
    """A real `int`. `bool` is rejected: `True` must never become an index."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise LuxAlgoStructureError(
            f"{where}: expected an int, got {value!r}")
    return value


def _require_index(value: object, where: str) -> int:
    """A non-negative `int`. Positions, counters and bar counts are never < 0."""
    out = _require_int(value, where)
    if out < 0:
        raise LuxAlgoStructureError(f"{where}: {out} is negative")
    return out


def _require_bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise LuxAlgoStructureError(
            f"{where}: expected a bool, got {value!r}")
    return value


def _require_float(value: object, where: str) -> float:
    """A finite real number. NaN and the infinities are refused, as in
    `_validate_candle`: a non-finite level makes every comparison below it
    false and would silently disable a break instead of raising."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LuxAlgoStructureError(
            f"{where}: expected a number, got {value!r}")
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        raise LuxAlgoStructureError(f"{where}: {value!r} is not finite")
    return out


def _optional_float(value: object, where: str) -> Optional[float]:
    """`None` reproduces Pine `na`, which is not the same as 0.0."""
    return None if value is None else _require_float(value, where)


def _optional_datetime(value: object, where: str) -> Optional[datetime]:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise LuxAlgoStructureError(
            f"{where}: expected a datetime, got {value!r}")
    return value


def _check_bar_window(asset: str, s: "LuxAlgoInternalStructure",
                      stream_offset: int) -> None:
    """Re-derive everything about the retained bars that IS derivable.

    Four of the eight arrays are carried but fully determined by the other four
    plus `stream_offset`, so they are RE-COMPUTED here and compared. This is not
    redundancy for its own sake: `parsed_high`/`parsed_low` choose the origin
    candle and `high_volatility` decides whether they are swapped, so a single
    flipped flag in an edited payload would move a trade zone onto a different
    candle without any other symptom. Recomputing turns that into a refusal.

    `stream_offset` is the STREAM position of retained bar 0 — the count of
    bars this structure has consumed and no longer keeps, not the number of
    positions the most recent capture happened to drop. The two coincide only
    for a first capture; see `capture_state()`.

    The arithmetic is the same expression `step()` runs, on the same floats, so
    the comparison is exact and no tolerance is involved.
    """
    for i in range(len(s.times)):
        at = f"{asset}: retained bar {i} (stream bar {stream_offset + i})"
        high = _require_float(s.highs[i], f"{at} high")
        low = _require_float(s.lows[i], f"{at} low")
        _require_float(s.closes[i], f"{at} close")
        _optional_datetime(s.times[i], f"{at} ts")
        if s.times[i] is None:
            raise LuxAlgoStructureError(f"{at}: ts is missing")
        if high < low:
            raise LuxAlgoStructureError(
                f"{at}: high {high!r} < low {low!r}; _validate_candle refuses "
                f"such a candle, so it cannot be in a captured window")
        atr = _optional_float(s.atr_values[i], f"{at} atr")
        # `ta.rma` is `na` for the first `ATR_LENGTH - 1` bars of the STREAM,
        # which is bar `stream_offset + i` counting from zero.
        warm = (stream_offset + i + 1) < ATR_LENGTH
        if warm != (atr is None):
            raise LuxAlgoStructureError(
                f"{at}: atr={atr!r} but that stream bar is "
                f"{'inside' if warm else 'past'} the {ATR_LENGTH}-bar warm-up; "
                f"`stream_offset` and the ATR column disagree about where in "
                f"the stream this window sits")
        hi_vol = _require_bool(s.high_volatility[i], f"{at} high_volatility")
        want_vol = False if atr is None else (
            (high - low) >= (ATR_HIGH_VOL_MULT * atr))
        if hi_vol != want_vol:
            raise LuxAlgoStructureError(
                f"{at}: high_volatility={hi_vol!r} but (high - low) = "
                f"{high - low!r} against {ATR_HIGH_VOL_MULT} * atr = "
                f"{atr!r} gives {want_vol!r}; the volatility flag decides the "
                f"parsedHigh/parsedLow swap and therefore which candle can "
                f"become an origin")
        want_ph = low if hi_vol else high
        want_pl = high if hi_vol else low
        if (s.parsed_highs[i], s.parsed_lows[i]) != (want_ph, want_pl):
            raise LuxAlgoStructureError(
                f"{at}: parsed_high/parsed_low are "
                f"{(s.parsed_highs[i], s.parsed_lows[i])!r} but a "
                f"{'high-volatility' if hi_vol else 'normal'} candle at "
                f"[{low!r}, {high!r}] gives {(want_ph, want_pl)!r}")


class LuxAlgoInternalStructure:
    """One LuxAlgo indicator instance, driven one candle at a time.

    Usage is deliberately identical in shape to the production scanner: build
    one per asset, call `step()` once per CLOSED candle in strict chronological
    order, and read the order blocks it returns.

        structure = LuxAlgoInternalStructure("BTCUSD")
        for bar_idx, (ts, o, h, l, c) in enumerate(candles):
            for ob in structure.step(bar_idx, ts, o, h, l, c):
                ...

    `step()` reproduces the Pine script's per-bar execution order exactly:

        1. array pushes (parsedHigh/parsedLow, high, low, time)
        2. updateTrailingExtremes()
        3. getCurrentStructure(50, false)              swing
        4. getCurrentStructure(5,  false, true)        internal
        5. getCurrentStructure(3,  true)              equal high/low
        6. displayStructure(true)                      internal BOS/CHoCH + OBs
        7. displayStructure()                          swing BOS/CHoCH
        8. deleteOrderBlocks(true)                     High/Low mitigation

    The order is load-bearing, not cosmetic. Step 3 must precede step 6 because
    `extraCondition` compares the internal level against the SWING level as it
    stands on this bar. Step 8 must follow step 6 because a block can be
    mitigated on its own creation bar.

    Bars must arrive with `bar_idx` increasing by exactly one. A gap would
    misalign the unbounded `parsed_*` arrays against `pivot.barIndex` and the
    origin search would silently pick the wrong candle, so a gap raises.
    """

    # === THE SNAPSHOT CONTRACT ============================================
    # Every instance attribute is classified into exactly one of the four sets
    # below, and `capture_state()` compares `vars(self)` against their union
    # BEFORE it serialises anything. An attribute added to `__init__` and not
    # classified here therefore RAISES AT CAPTURE TIME instead of being
    # silently dropped into a snapshot that restores into a different object.
    # That mechanical guard is what replaces the old "replay the whole candle
    # log" strategy: replay reproduced every field by construction, so nothing
    # could be forgotten; a field-by-field codec must prove the same thing, and
    # this is the proof that cannot rot.
    #
    # These are CLASS attributes, so they never appear in `vars(instance)`.

    #: Reproduced EXACTLY. Scalars, Pine `var` objects and the live OB store.
    _SNAPSHOT_SCALARS: ClassVar[frozenset] = frozenset({
        "asset", "_base_bar_idx", "_last_bar_idx", "_atr", "_ob_seq",
        "swing_high", "swing_low", "internal_high", "internal_low",
        "equal_high", "equal_low", "swing_trend", "internal_trend",
        "trailing", "_leg_swing", "_leg_internal", "_leg_equal",
        "_prev_close", "_prev_internal_high", "_prev_internal_low",
        "_prev_swing_high", "_prev_swing_low", "live_order_blocks",
    })

    #: The eight parallel per-bar arrays. Reproduced exactly FROM THE CAUSAL
    #: WATERMARK ONWARD and truncated before it; `capture_state()` carries the
    #: proof that no future bar can read below that point.
    _SNAPSHOT_ARRAYS: ClassVar[Tuple[str, ...]] = (
        "parsed_highs", "parsed_lows", "highs", "lows", "closes", "times",
        "high_volatility", "atr_values",
    )

    #: Rebuilt from other captured state rather than carried. `step()` reads
    #: `all_order_blocks` only as `len(...)` and as a tail slice taken from
    #: that same length, both of which are correct for ANY starting content, so
    #: its history is behaviour-inert. `_ob_seq` carries the true creation
    #: count (`len(all_order_blocks) == _ob_seq` is an invariant of
    #: `_store_order_block`, which appends and increments together).
    _SNAPSHOT_DERIVED: ClassVar[frozenset] = frozenset({"all_order_blocks"})

    #: Append-only audit trails. NEVER read by `step()` and never read by any
    #: production caller, so they are behaviour-inert and are NOT carried. This
    #: is real, disclosed information loss: a restored structure reports the
    #: events that happen AFTER the restore, not the ones before it.
    _SNAPSHOT_AUDIT: ClassVar[frozenset] = frozenset({
        "internal_events", "swing_events", "equal_events", "internal_pivots",
        "swing_pivots", "suppressed_by_extra_condition", "pine_loop_skips",
        "mitigated_order_blocks", "evicted_at_cap",
    })

    def __init__(self, asset: str) -> None:
        self.asset = asset

        # --- unbounded per-bar arrays; index == position == bar_idx - base ---
        # Pine arrays are unbounded and the origin search reaches back to a
        # pivot that may be arbitrarily old, so a bounded ring buffer would
        # silently truncate the search window instead of failing.
        self.parsed_highs: List[float] = []
        self.parsed_lows: List[float] = []
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.closes: List[float] = []
        self.times: List[datetime] = []
        self.high_volatility: List[bool] = []
        self.atr_values: List[Optional[float]] = []

        self._atr = _PineATR(ATR_LENGTH)

        # --- Pine `var` state -------------------------------------------------
        self.swing_high, self.swing_low = LuxAlgoPivot(), LuxAlgoPivot()
        self.internal_high, self.internal_low = LuxAlgoPivot(), LuxAlgoPivot()
        self.equal_high, self.equal_low = LuxAlgoPivot(), LuxAlgoPivot()
        self.swing_trend, self.internal_trend = LuxAlgoTrend(), LuxAlgoTrend()
        self.trailing = LuxAlgoTrailing()

        # three INDEPENDENT leg states, one per Pine call site
        self._leg_swing = _LegState()
        self._leg_internal = _LegState()
        self._leg_equal = _LegState()

        # ta.crossover/crossunder need last bar's values as of THIS point in
        # the script, which is not the same as "the previous bar's final state"
        # for anything the script mutates later in the bar.
        self._prev_close: Optional[float] = None
        self._prev_internal_high: Optional[float] = None
        self._prev_internal_low: Optional[float] = None
        self._prev_swing_high: Optional[float] = None
        self._prev_swing_low: Optional[float] = None

        # --- order block store -----------------------------------------------
        # `live_order_blocks[0]` is the NEWEST (Pine unshift); the last element
        # is the oldest and is what `array.pop()` evicts at the 100 cap.
        self.live_order_blocks: List[LuxAlgoOrderBlock] = []
        self.all_order_blocks: List[LuxAlgoOrderBlock] = []
        self._ob_seq = 0

        # --- audit trails (behaviour-neutral) --------------------------------
        self.internal_events: List[Tuple] = []
        self.swing_events: List[Tuple] = []
        self.equal_events: List[Tuple] = []
        self.internal_pivots: List[Tuple] = []
        self.swing_pivots: List[Tuple] = []
        self.suppressed_by_extra_condition: List[Tuple] = []
        self.pine_loop_skips: List[Tuple[int, LuxAlgoOrderBlock]] = []
        self.mitigated_order_blocks: List[LuxAlgoOrderBlock] = []
        self.evicted_at_cap: List[LuxAlgoOrderBlock] = []

        # --- stream position --------------------------------------------------
        self._base_bar_idx: Optional[int] = None
        self._last_bar_idx: Optional[int] = None

    # -- stream position ----------------------------------------------------
    def __len__(self) -> int:
        """Number of candles processed so far."""
        return len(self.times)

    def _abs(self, pos: int) -> int:
        """Convert an internal array position back to the caller's bar index."""
        base = self._base_bar_idx
        if base is None:
            raise LuxAlgoStructureError("no candle has been processed yet")
        return base + pos

    def _advance(self, bar_idx: int) -> int:
        """Validate stream continuity and return this bar's array position.

        Fails closed on a gap, a repeat or an out-of-order bar. The origin
        search indexes `parsed_highs` by `pivot.barIndex`, so a silent
        misalignment would not raise -- it would return a DIFFERENT candle's
        high and low as a trade zone. Refusing is the only safe answer
        (safety rule #15).
        """
        if self._base_bar_idx is None:
            self._base_bar_idx = bar_idx
            self._last_bar_idx = bar_idx
            return 0
        last = self._last_bar_idx
        assert last is not None                 # set together with the base
        if bar_idx != last + 1:
            raise LuxAlgoStructureError(
                f"{self.asset}: non-contiguous candle stream; expected "
                f"bar_idx {last + 1}, got {bar_idx}. The LuxAlgo origin search "
                f"slices an unbounded array by absolute bar index, so a gap "
                f"would silently select the wrong origin candle. Refusing to "
                f"guess (safety rule #15).")
        self._last_bar_idx = bar_idx
        return bar_idx - self._base_bar_idx

    # -- display slice (chart fidelity only, never a trading input) ---------
    def display_slice(self) -> List[LuxAlgoOrderBlock]:
        """The boxes TradingView would actually draw right now.

        Pine's `drawOrderBlocks` takes `slice(0, min(20, size))` of the LIVE
        array and only runs on the last confirmed bar, so the 20 visible boxes
        are the newest 20 UN-MITIGATED blocks as of the final bar -- not a
        historical record of what existed at any earlier moment. Reporting only.
        """
        return self.live_order_blocks[:min(INTERNAL_OB_DISPLAY,
                                          len(self.live_order_blocks))]

    # -- the Pine per-bar execution order -----------------------------------
    def step(self, bar_idx: int, ts: datetime, open_: float, high: float,
             low: float, close: float) -> List[LuxAlgoOrderBlock]:
        """Process one CLOSED candle; return the order blocks it created.

        `open_` is accepted for interface symmetry with the production scanner
        and is deliberately unused: the LuxAlgo structure/order-block path
        reads only high, low and close. It is validated, not consumed.

        The returned list is in Pine's own emission order -- the bullish branch
        of `displayStructure` runs before the bearish branch, so on the rare bar
        where both fire the BULLISH block comes first. That is the OPPOSITE of
        `ManualSpecBOSScanner`, which emits SHORT before LONG. Both orders are
        internally consistent; neither is normalised to the other, because the
        one-trade-at-a-time slot downstream is awarded oldest-first and quietly
        re-ordering emissions would change which block wins it.

        Blocks mitigated on their own creation bar ARE returned. Mitigation is
        a LuxAlgo drawing concept; it is not an invalidation and it is not
        forwarded to the trading lifecycle.
        """
        self._validate_candle(bar_idx, open_, high, low, close)
        pos = self._advance(bar_idx)

        # 1. global preamble, evaluated before any function call in the script
        atr = self._atr.update(high, low, close)
        # `na >= x` is false in Pine, so no warm-up bar is high-volatility.
        hi_vol = False if atr is None else (
            (high - low) >= (ATR_HIGH_VOL_MULT * atr))
        # THE SWAP IS THE ATR FILTER. A volatile bar gets a tiny parsedHigh and
        # a huge parsedLow, so it almost never wins the argmax/argmin -- and
        # when it wins anyway the stored box is inverted.
        self.parsed_highs.append(low if hi_vol else high)
        self.parsed_lows.append(high if hi_vol else low)
        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)
        self.times.append(ts)
        self.high_volatility.append(hi_vol)
        self.atr_values.append(atr)

        # 2. updateTrailingExtremes()   (Show Strong/Weak High/Low = ON)
        trailing = self.trailing
        trailing.top = high if trailing.top is None else max(high, trailing.top)
        if trailing.top == high:
            trailing.lastTopTime = ts
        trailing.bottom = (low if trailing.bottom is None
                           else min(low, trailing.bottom))
        if trailing.bottom == low:
            trailing.lastBottomTime = ts

        # 3. getCurrentStructure(swingsLengthInput, false)  -- swing layer.
        #    Required even though Swing Order Blocks are OFF, because step 6's
        #    extraCondition compares the internal level against the swing level.
        self._get_current_structure(pos, SWINGS_LENGTH, self._leg_swing,
                                    equal_high_low=False, internal=False)
        # 4. getCurrentStructure(5, false, true)  -- internal layer.
        self._get_current_structure(pos, INTERNAL_LENGTH, self._leg_internal,
                                    equal_high_low=False, internal=True)
        # 5. getCurrentStructure(equalHighLowLengthInput, true)
        self._get_current_structure(pos, EQUAL_LENGTH, self._leg_equal,
                                    equal_high_low=True, internal=False)

        created_before = len(self.all_order_blocks)
        # 6. displayStructure(true)  -> internal BOS/CHoCH + internal OB store
        self._display_structure(pos, internal=True)
        # 7. displayStructure()      -> swing BOS/CHoCH (swing OBs are OFF)
        self._display_structure(pos, internal=False)
        created = self.all_order_blocks[created_before:]

        # 8. deleteOrderBlocks(true) -> High/Low mitigation. Runs AFTER the
        #    breaks, so a block can be mitigated on its own creation bar.
        self._delete_order_blocks(pos)

        # Snapshot the crossover history for the NEXT bar at exactly this point
        # in the script, which is what ta.crossover's `[1]` actually reads.
        self._prev_close = close
        self._prev_internal_high = self.internal_high.currentLevel
        self._prev_internal_low = self.internal_low.currentLevel
        self._prev_swing_high = self.swing_high.currentLevel
        self._prev_swing_low = self.swing_low.currentLevel
        return created

    def _validate_candle(self, bar_idx: int, open_: float, high: float,
                         low: float, close: float) -> None:
        """Refuse a candle whose geometry cannot produce a meaningful zone.

        `open_` is never consumed by this module, but a non-finite open means
        the feed is corrupt, and a corrupt feed's high/low cannot be trusted
        either. Refusing the whole candle is the fail-closed answer.
        """
        for name, value in (("open", open_), ("high", high), ("low", low),
                            ("close", close)):
            if value != value or value in (float("inf"), float("-inf")):
                raise LuxAlgoStructureError(
                    f"{self.asset} bar {bar_idx}: {name} is not a finite "
                    f"number ({value!r}); refusing to build structure from it")
        if high < low:
            raise LuxAlgoStructureError(
                f"{self.asset} bar {bar_idx}: high {high!r} < low {low!r}. "
                f"An inverted candle would invert every zone derived from it. "
                f"Refusing rather than repairing the data (safety rule #15).")

    # -- getCurrentStructure(size, equalHighLow, internal) ------------------
    def _get_current_structure(self, pos: int, size: int, state: _LegState,
                               equal_high_low: bool, internal: bool) -> None:
        """Record a pivot `size` bars IN ARREARS when a new leg starts.

        `barIndex := bar_index - size` is the whole reason LuxAlgo order blocks
        sit further back than the engine's: the pivot that anchors the origin
        search is the bar `size` back, not the bar the leg was detected on.
        """
        new_pivot, pivot_low, pivot_high = _leg_step(
            state, pos, size, self.highs, self.lows)
        if not new_pivot:
            return

        atr = self.atr_values[pos]
        origin_pos = pos - size
        origin_time = self.times[origin_pos]

        if pivot_low:
            if equal_high_low:
                pivot = self.equal_low
            elif internal:
                pivot = self.internal_low
            else:
                pivot = self.swing_low
            level = self.lows[origin_pos]
            if (equal_high_low and pivot.currentLevel is not None
                    and atr is not None
                    and abs(pivot.currentLevel - level)
                    < EQUAL_THRESHOLD * atr):
                self.equal_events.append(
                    ("EQL", self._abs(pos), self.times[pos],
                     self._abs(origin_pos), origin_time, level,
                     pivot.currentLevel))
            pivot.lastLevel = pivot.currentLevel
            pivot.currentLevel = level
            pivot.crossed = False
            pivot.barTime = origin_time
            pivot.barIndex = origin_pos
            if not equal_high_low and not internal:
                self.trailing.bottom = pivot.currentLevel
                self.trailing.barTime = pivot.barTime
                self.trailing.barIndex = self._abs(origin_pos)
                self.trailing.lastBottomTime = pivot.barTime
                self.swing_pivots.append(
                    ("LOW", self._abs(pos), self.times[pos],
                     self._abs(origin_pos), origin_time, level))
            if internal:
                self.internal_pivots.append(
                    ("LOW", self._abs(pos), self.times[pos],
                     self._abs(origin_pos), origin_time, level))

        elif pivot_high:
            if equal_high_low:
                pivot = self.equal_high
            elif internal:
                pivot = self.internal_high
            else:
                pivot = self.swing_high
            level = self.highs[origin_pos]
            if (equal_high_low and pivot.currentLevel is not None
                    and atr is not None
                    and abs(pivot.currentLevel - level)
                    < EQUAL_THRESHOLD * atr):
                self.equal_events.append(
                    ("EQH", self._abs(pos), self.times[pos],
                     self._abs(origin_pos), origin_time, level,
                     pivot.currentLevel))
            pivot.lastLevel = pivot.currentLevel
            pivot.currentLevel = level
            pivot.crossed = False
            pivot.barTime = origin_time
            pivot.barIndex = origin_pos
            if not equal_high_low and not internal:
                self.trailing.top = pivot.currentLevel
                self.trailing.barTime = pivot.barTime
                self.trailing.barIndex = self._abs(origin_pos)
                self.trailing.lastTopTime = pivot.barTime
                self.swing_pivots.append(
                    ("HIGH", self._abs(pos), self.times[pos],
                     self._abs(origin_pos), origin_time, level))
            if internal:
                self.internal_pivots.append(
                    ("HIGH", self._abs(pos), self.times[pos],
                     self._abs(origin_pos), origin_time, level))

    # -- displayStructure(internal) -----------------------------------------
    def _display_structure(self, pos: int, internal: bool) -> None:
        """Classify BOS vs CHoCH and, for the internal layer, store the block.

        The Confluence Filter is OFF, so Pine's `var bullishBar = true` and
        `var bearishBar = true` are never reassigned and cannot filter anything.
        The OTHER half of extraCondition survives and CAN filter:

            extraCondition = internal
                ? internalHigh.currentLevel != swingHigh.currentLevel
                  and bullishBar
                : true

        which is precisely why the swing-50 layer has to be modelled even
        though Swing Order Blocks are switched off. Measured across the full
        four-symbol history it suppressed ZERO breaks; it is implemented anyway,
        because a filter that happens to be inert on one dataset is not the
        same thing as a filter that does not exist.
        """
        close = self.closes[pos]
        prev_close = self._prev_close
        if internal:
            pivot_high, pivot_low = self.internal_high, self.internal_low
            trend = self.internal_trend
            prev_high_level = self._prev_internal_high
            prev_low_level = self._prev_internal_low
            extra_high = _pine_ne(self.internal_high.currentLevel,
                                  self.swing_high.currentLevel)
            extra_low = _pine_ne(self.internal_low.currentLevel,
                                 self.swing_low.currentLevel)
        else:
            pivot_high, pivot_low = self.swing_high, self.swing_low
            trend = self.swing_trend
            prev_high_level = self._prev_swing_high
            prev_low_level = self._prev_swing_low
            extra_high = extra_low = True

        # ---- bullish break: ta.crossover(close, pivot.currentLevel) --------
        # ta.crossover(a, b) == a > b and a[1] <= b[1]
        level = pivot_high.currentLevel
        crossover = (level is not None and prev_close is not None
                     and prev_high_level is not None
                     and close > level and prev_close <= prev_high_level)
        if internal and crossover and not pivot_high.crossed and not extra_high:
            self.suppressed_by_extra_condition.append(
                (self._abs(pos), self.times[pos], "BULLISH", level,
                 self._abs(pivot_high.barIndex)))
        if crossover and not pivot_high.crossed and extra_high:
            # CHoCH means the bias FLIPPED; BOS means it continued. A virgin
            # trend (bias == 0) is neither BEARISH nor BULLISH, so the first
            # break in a symbol's history is always BOS.
            tag = TAG_CHOCH if trend.bias == BEARISH else TAG_BOS
            pivot_high.crossed = True
            trend.bias = BULLISH
            block: Optional[LuxAlgoOrderBlock] = None
            if internal and SHOW_INTERNAL_OB:
                block = self._store_order_block(pos, pivot_high, BULLISH, tag)
            record = (self._abs(pos), self.times[pos], "BULLISH", tag, level,
                      self._abs(pivot_high.barIndex),
                      self.times[pivot_high.barIndex], block)
            (self.internal_events if internal else self.swing_events).append(
                record)

        # ---- bearish break: ta.crossunder(close, pivot.currentLevel) -------
        # PINE ORDERING QUIRK, REPRODUCED ON PURPOSE: `trend.bias` is read HERE,
        # after the bullish branch above may already have set it to BULLISH on
        # this same bar. A bar that breaks both ways therefore reports the
        # second break as CHoCH even though the bias was 0 or BEARISH when the
        # bar opened. Evaluating the two tags up-front would be "cleaner" and
        # would stop matching the operator's chart.
        level = pivot_low.currentLevel
        crossunder = (level is not None and prev_close is not None
                      and prev_low_level is not None
                      and close < level and prev_close >= prev_low_level)
        if internal and crossunder and not pivot_low.crossed and not extra_low:
            self.suppressed_by_extra_condition.append(
                (self._abs(pos), self.times[pos], "BEARISH", level,
                 self._abs(pivot_low.barIndex)))
        if crossunder and not pivot_low.crossed and extra_low:
            tag = TAG_CHOCH if trend.bias == BULLISH else TAG_BOS
            pivot_low.crossed = True
            trend.bias = BEARISH
            block = None
            if internal and SHOW_INTERNAL_OB:
                block = self._store_order_block(pos, pivot_low, BEARISH, tag)
            record = (self._abs(pos), self.times[pos], "BEARISH", tag, level,
                      self._abs(pivot_low.barIndex),
                      self.times[pivot_low.barIndex], block)
            (self.internal_events if internal else self.swing_events).append(
                record)

    # -- storeOrdeBlock(pivot, internal, bias) ------------------------------
    def _store_order_block(self, pos: int, pivot: LuxAlgoPivot, bias: int,
                           tag: str) -> LuxAlgoOrderBlock:
        """Pick the origin candle and store the block.

        Pine:
            if bias == BEARISH
                a_rray      := parsedHighs.slice(p.barIndex, bar_index)
                parsedIndex := p.barIndex + a_rray.indexof(a_rray.max())
            else
                a_rray      := parsedLows.slice(p.barIndex, bar_index)
                parsedIndex := p.barIndex + a_rray.indexof(a_rray.min())

        Three details decide which candle becomes the zone:
          * `slice(from, to)` is `to`-EXCLUSIVE, so THE BREAK BAR IS EXCLUDED.
          * `indexof(max())` returns the FIRST occurrence, so a tie resolves to
            the EARLIEST bar.
          * the values searched are `parsedHigh`/`parsedLow`, which are SWAPPED
            on a high-volatility bar -- that is how the ATR filter keeps
            volatile candles from becoming origins.
        """
        start = pivot.barIndex
        if start is None:
            raise LuxAlgoStructureError(
                f"{self.asset}: a break fired against a pivot with no bar "
                f"index; refusing to guess the origin window")
        if start >= pos:
            raise LuxAlgoStructureError(
                f"{self.asset}: empty origin window [{start}, {pos}); the "
                f"pivot is not strictly older than the break bar")
        if bias == BEARISH:
            window = self.parsed_highs[start:pos]
            offset = window.index(max(window))     # FIRST occurrence
        else:
            window = self.parsed_lows[start:pos]
            offset = window.index(min(window))     # FIRST occurrence
        origin = start + offset

        block = LuxAlgoOrderBlock(
            barHigh=self.parsed_highs[origin],
            barLow=self.parsed_lows[origin],
            barTime=self.times[origin],
            bias=bias,
            asset=self.asset,
            origin_bar_idx=self._abs(origin),
            origin_dt=self.times[origin],
            created_bar_idx=self._abs(pos),
            created_dt=self.times[pos],
            tag=tag,
            pivot_bar_idx=self._abs(start),
            pivot_level=pivot.currentLevel,
            origin_high_volatility=self.high_volatility[origin],
            seq=self._ob_seq,
        )
        self._ob_seq += 1
        if len(self.live_order_blocks) >= ALGO_OB_CAP:
            # array.pop() removes the LAST element, which is the OLDEST block.
            self.evicted_at_cap.append(self.live_order_blocks.pop())
        self.live_order_blocks.insert(0, block)     # unshift -> newest first
        self.all_order_blocks.append(block)
        return block

    # -- deleteOrderBlocks(internal), Order Block Mitigation = High/Low -----
    def _delete_order_blocks(self, pos: int) -> None:
        """Remove mitigated boxes from the CHART store.

        MITIGATION IS NOT INVALIDATION. This deletes a drawing. It is
        reproduced because the 100-block cap, the eviction order and the
        reported mitigation bar all depend on it, and it is NOT forwarded to
        the trading lifecycle: an order block still dies only from price, from
        time after its first touch, from a trade, or from an operator.

        High/Low mitigation triggers on the WICK: a bearish block dies when
        `high > barHigh`, a bullish block when `low < barLow`.
        """
        high, low = self.highs[pos], self.lows[pos]
        idx = 0
        while idx < len(self.live_order_blocks):
            block = self.live_order_blocks[idx]
            crossed = False
            if OB_MITIGATION_HIGH_LOW:
                if high > block.barHigh and block.bias == BEARISH:
                    crossed = True
                elif low < block.barLow and block.bias == BULLISH:
                    crossed = True
            if crossed:
                block.mitigated_bar_idx = self._abs(pos)
                block.mitigated_dt = self.times[pos]
                self.mitigated_order_blocks.append(block)
                self.live_order_blocks.pop(idx)
                # Pine's `for [index, x] in array` keeps incrementing `index`
                # even though the array just shrank, so the element that
                # shifted into this slot is SKIPPED on this bar. Reproduced.
                if idx < len(self.live_order_blocks):
                    self.pine_loop_skips.append(
                        (self._abs(pos), self.live_order_blocks[idx]))
                if CLEAN_MITIGATION:
                    continue        # would re-test the shifted-in element
            idx += 1

    # -- bounded causal snapshot --------------------------------------------
    def causal_watermark(self) -> int:
        """The number of leading positions that can NEVER be read again.

        THE PROOF. Enumerate every read of a per-bar array that a FUTURE
        `step(pos)` can perform, taken from the code above and nowhere else:

          `_leg_step`            `highs[pos-size]`, `max(highs[pos-size+1:pos+1])`
                                 `lows[pos-size]`,  `min(lows[pos-size+1:pos+1])`
                                 for size in (50, 5, 3) -> deepest `pos - 50`
          `_get_current_structure` `atr_values[pos]`, `times[pos]`,
                                 `times[pos-size]`, `lows[pos-size]`,
                                 `highs[pos-size]`      -> deepest `pos - 50`
          `_display_structure`   `closes[pos]`, `times[pos]`,
                                 `times[internal_high.barIndex]`,
                                 `times[internal_low.barIndex]`,
                                 `times[swing_high.barIndex]`,
                                 `times[swing_low.barIndex]`
          `_store_order_block`   `parsed_highs[pivot.barIndex:pos]` /
                                 `parsed_lows[pivot.barIndex:pos]`, then
                                 `parsed_highs[origin]`, `parsed_lows[origin]`,
                                 `times[origin]`, `high_volatility[origin]`,
                                 all with `origin >= pivot.barIndex`
          `_delete_order_blocks` `highs[pos]`, `lows[pos]`

        Nothing else indexes an array. `equal_high`/`equal_low`'s `barIndex` is
        written but never used as an index (only the swing and internal layers
        reach `_display_structure`), and `trailing.barIndex` plus every
        `LuxAlgoOrderBlock` index field are ABSOLUTE caller indices produced by
        `_abs()`, never array positions. So the deepest position any future bar
        can touch is

            min(pos - SWINGS_LENGTH, live pivot barIndexes)

        and since a future `pos` only grows, the bound at capture time is
        `len(self) - SWINGS_LENGTH`.

        WHY A CAPTURE-TIME BOUND STAYS VALID FOREVER. A pivot's `barIndex` is
        only ever written as `origin_pos = pos - size` in
        `_get_current_structure`, and `pos` increases by one per bar, so each
        pivot's `barIndex` is MONOTONICALLY NON-DECREASING over its lifetime. A
        pivot can therefore only move the bound FORWARD, never back onto a
        position this method already declared dead.

        `equal_high` and `equal_low` are included in the minimum anyway. They
        are not read, so including them can only retain MORE than necessary --
        the conservative direction. Measured over the real four-symbol history
        the two variants give an identical worst case, so this costs nothing.

        The `len(self) - SWINGS_LENGTH` term is also what keeps at least
        `SWINGS_LENGTH` positions in the arrays, so the `pos >= size` guard in
        `_leg_step` is satisfied on the first bar after a restore exactly as it
        was before the cut. Clamped at zero, so a short history prunes nothing.
        """
        n = len(self.times)
        if n == 0:
            return 0
        bound = n - SWINGS_LENGTH
        for pivot in (self.swing_high, self.swing_low,
                      self.internal_high, self.internal_low,
                      self.equal_high, self.equal_low):
            if pivot.barIndex is not None:
                bound = min(bound, pivot.barIndex)
        return max(0, bound)

    def _assert_snapshot_contract(self) -> None:
        """Refuse to snapshot an object whose attributes are not all classified.

        The old codec persisted the candle log and REPLAYED it, so every field
        was reproduced by construction and none could be forgotten. A
        field-by-field codec loses that guarantee, and the failure mode is the
        worst kind: a snapshot that restores successfully into a structure that
        quietly disagrees with the captured one. This check restores the
        guarantee mechanically -- an attribute nobody classified is a capture
        REFUSAL, not a silent omission (safety rules #13, #15).
        """
        declared = (self._SNAPSHOT_SCALARS | set(self._SNAPSHOT_ARRAYS)
                    | self._SNAPSHOT_DERIVED | self._SNAPSHOT_AUDIT)
        actual = set(vars(self))
        unclassified = sorted(actual - declared)
        vanished = sorted(declared - actual)
        if unclassified or vanished:
            raise LuxAlgoStructureError(
                f"{self.asset}: the snapshot contract no longer matches this "
                f"object. Unclassified attribute(s) {unclassified} would be "
                f"DROPPED by capture_state(); declared attribute(s) "
                f"{vanished} no longer exist. Classify every attribute in "
                f"_SNAPSHOT_SCALARS / _SNAPSHOT_ARRAYS / _SNAPSHOT_DERIVED / "
                f"_SNAPSHOT_AUDIT before capturing state.")

    def capture_state(self) -> Dict[str, Any]:
        """The MINIMUM causal state needed to resume exact future behaviour.

        Returns native Python objects (int, float, bool, str, datetime, None,
        list, dict) -- no JSON encoding, no Decimal, no rounding. `state.py`
        owns the serialisation and the integrity checks; this method owns the
        question of WHAT is enough, because that question is answered by the
        code in this module and nowhere else.

        Everything at a position below `causal_watermark()` is dropped and the
        surviving positions are REBASED: `k` positions removed means every
        pivot `barIndex` loses `k` and `_base_bar_idx` gains `k`, which leaves
        `_abs(p - k) == (base + k) + (p - k) == base + p` unchanged. Absolute
        indices -- `_last_bar_idx`, `trailing.barIndex` and every
        `LuxAlgoOrderBlock` field -- are therefore untouched.

        WHAT A TRUNCATED REPLAY WOULD NOT GIVE, AND WHY THIS IS NOT THAT. It
        would be much simpler to keep the last N candles and replay them. That
        cannot be exact: `_PineATR` is Wilder's recursion, whose memory is
        infinite, and the leg states, pivot levels, trend biases and the OB
        store are all path-dependent from bar 0. So the Pine `var` state is
        carried VERBATIM and only the arrays -- whose future reads are bounded
        by the proof in `causal_watermark()` -- are cut.

        `stream_offset` IS CUMULATIVE, NOT PER-CAPTURE. It is the stream
        position of retained bar 0, i.e. every bar this structure has ever
        consumed minus the ones it still holds -- NOT `k`. The two are equal
        only for a structure that has never been restored. Emitting `k` instead
        would make the field lie after a restore-then-recapture (the second
        blob would claim offset 0 for a window sitting thousands of bars into
        the stream), and both `_check_bar_window`'s ATR warm-up derivation and
        the `atr.count == stream_offset + m` cross-check read it, so the
        re-captured blob would be refused. Deriving it from `_atr._count` also
        means it cannot drift: the ATR is stepped exactly once per `step()`, so
        that counter IS the stream length, and the cross-check in
        `_restore_stream_position` is what proves the two agree.
        """
        self._assert_snapshot_contract()
        k = self.causal_watermark()
        base = self._base_bar_idx
        return {
            "asset": self.asset,
            "stream_offset": self._atr._count - (len(self.times) - k),
            "base_bar_idx": None if base is None else base + k,
            "last_bar_idx": self._last_bar_idx,
            "atr": {
                "length": self._atr.length,
                "value": self._atr.value,
                "count": self._atr._count,
                "warm_sum": self._atr._warm_sum,
                "prev_close": self._atr._prev_close,
                "has_prev": self._atr._has_prev,
            },
            "pivots": {name: self._capture_pivot(getattr(self, name), k)
                       for name in _PIVOT_NAMES},
            "trends": {"internal": self.internal_trend.bias,
                       "swing": self.swing_trend.bias},
            "trailing": {
                "top": self.trailing.top,
                "bottom": self.trailing.bottom,
                "barTime": self.trailing.barTime,
                # ABSOLUTE (written through `_abs`), so NOT rebased.
                "barIndex": self.trailing.barIndex,
                "lastTopTime": self.trailing.lastTopTime,
                "lastBottomTime": self.trailing.lastBottomTime,
            },
            "legs": {name: {"leg": getattr(self, attr).leg,
                            "prev": getattr(self, attr).prev}
                     for name, attr in _LEG_NAMES},
            "crossover": {
                "close": self._prev_close,
                "internal_high": self._prev_internal_high,
                "internal_low": self._prev_internal_low,
                "swing_high": self._prev_swing_high,
                "swing_low": self._prev_swing_low,
            },
            "ob_seq": self._ob_seq,
            # Newest first, exactly as stored: index 0 is Pine's `unshift`
            # target and the last element is what `pop()` evicts at the cap.
            "live_order_blocks": [
                {f.name: getattr(block, f.name)
                 for f in dc_fields(LuxAlgoOrderBlock)}
                for block in self.live_order_blocks
            ],
            "arrays": {name: list(getattr(self, name)[k:])
                       for name in self._SNAPSHOT_ARRAYS},
        }

    @staticmethod
    def _capture_pivot(pivot: LuxAlgoPivot, k: int) -> Dict[str, Any]:
        """One `LuxAlgoPivot`, with `barIndex` rebased into the pruned arrays."""
        return {
            "currentLevel": pivot.currentLevel,
            "lastLevel": pivot.lastLevel,
            "crossed": pivot.crossed,
            "barTime": pivot.barTime,
            "barIndex": (None if pivot.barIndex is None
                         else pivot.barIndex - k),
        }

    @classmethod
    def restore_state(cls, blob: Dict[str, Any]) -> "LuxAlgoInternalStructure":
        """Rebuild a structure from `capture_state()`. No replay, no guessing.

        Every cross-field invariant the capture side guarantees is re-checked
        here, because a payload that reaches this method has been through JSON
        and may have been edited on the way. A blob that cannot describe a
        structure `step()` could have produced raises `LuxAlgoStructureError`
        rather than being repaired into something plausible (rule #15):

          * the eight arrays must be the same length;
          * `_atr._count` must equal `len(arrays) + stream_offset`, which is
            what ties the ATR's own bar count to the window that was retained;
          * `last_bar_idx == base_bar_idx + len(arrays) - 1`;
          * a rebased pivot `barIndex` must land inside the retained window --
            the whole point of the watermark is that it does;
          * `parsed_high`/`parsed_low` must be the candle's own high/low, in
            the order `high_volatility` says, so a flipped volatility flag
            cannot silently move an origin candle;
          * a live block is never already mitigated, its `seq` is strictly
            descending (newest first) and below `ob_seq`, and the store is
            within `ALGO_OB_CAP`;
          * `stream_offset > 0` requires at least `SWINGS_LENGTH` retained
            bars, which is what makes `_leg_step`'s `pos >= size` guard behave
            after the cut exactly as it did before it.
        """
        _require_keys(blob, LUXALGO_SNAPSHOT_KEYS, "luxalgo snapshot")
        asset = blob["asset"]
        if not isinstance(asset, str) or not asset:
            raise LuxAlgoStructureError(
                f"luxalgo snapshot.asset: expected a non-empty symbol, got "
                f"{asset!r}")
        self = cls(asset)

        arrays = blob["arrays"]
        _require_keys(arrays, cls._SNAPSHOT_ARRAYS, "luxalgo snapshot.arrays")
        lengths = {name: len(arrays[name]) for name in cls._SNAPSHOT_ARRAYS}
        if len(set(lengths.values())) != 1:
            raise LuxAlgoStructureError(
                f"{asset}: the eight per-bar arrays disagree about how many "
                f"bars were retained ({lengths}); they are written in lockstep "
                f"by step() and indexed by the same position")
        m = next(iter(lengths.values()))
        offset = _require_index(blob["stream_offset"],
                               "luxalgo snapshot.stream_offset")
        if offset > 0 and m < SWINGS_LENGTH:
            raise LuxAlgoStructureError(
                f"{asset}: {m} retained bar(s) at stream offset {offset}, but "
                f"the causal watermark can never leave fewer than "
                f"SWINGS_LENGTH ({SWINGS_LENGTH}); a shorter window would "
                f"silently change the leg-{SWINGS_LENGTH} layer's "
                f"`pos >= size` behaviour")
        for name in cls._SNAPSHOT_ARRAYS:
            setattr(self, name, list(arrays[name]))
        _check_bar_window(asset, self, offset)

        self._restore_stream_position(blob, m, offset)
        self._restore_pine_vars(blob, m)
        self._restore_order_blocks(blob)
        self._assert_snapshot_contract()
        return self

    def _restore_stream_position(self, blob: Dict[str, Any], m: int,
                                 offset: int) -> None:
        """`_base_bar_idx` / `_last_bar_idx` / the streaming ATR."""
        base, last = blob["base_bar_idx"], blob["last_bar_idx"]
        if m == 0:
            if base is not None or last is not None or offset != 0:
                raise LuxAlgoStructureError(
                    f"{self.asset}: an empty snapshot cannot carry a stream "
                    f"position (base={base!r} last={last!r} "
                    f"stream_offset={offset})")
        else:
            base = _require_int(base, "luxalgo snapshot.base_bar_idx")
            last = _require_int(last, "luxalgo snapshot.last_bar_idx")
            if last != base + m - 1:
                raise LuxAlgoStructureError(
                    f"{self.asset}: last_bar_idx {last} != base_bar_idx "
                    f"{base} + {m} - 1; the stream position and the retained "
                    f"window disagree about which bar was last processed")
        self._base_bar_idx, self._last_bar_idx = base, last

        atr = blob["atr"]
        _require_keys(atr, _ATR_KEYS, "luxalgo snapshot.atr")
        if atr["length"] != ATR_LENGTH:
            raise LuxAlgoStructureError(
                f"{self.asset}: snapshot ATR length {atr['length']!r} != this "
                f"build's ATR_LENGTH {ATR_LENGTH}; Wilder's recursion cannot "
                f"be re-based onto a different period")
        count = _require_index(atr["count"], "luxalgo snapshot.atr.count")
        if count != m + offset:
            raise LuxAlgoStructureError(
                f"{self.asset}: the ATR has seen {count} bar(s) but the "
                f"snapshot describes {m} retained bar(s) at stream offset "
                f"{offset} = {m + offset} consumed; the ATR state and the "
                f"candle window would resume at different points in the stream")
        self._atr.value = _optional_float(atr["value"],
                                          "luxalgo snapshot.atr.value")
        self._atr._count = count
        self._atr._warm_sum = _require_float(atr["warm_sum"],
                                             "luxalgo snapshot.atr.warm_sum")
        self._atr._prev_close = _require_float(
            atr["prev_close"], "luxalgo snapshot.atr.prev_close")
        self._atr._has_prev = _require_bool(
            atr["has_prev"], "luxalgo snapshot.atr.has_prev")
        if self._atr._has_prev != (count > 0):
            raise LuxAlgoStructureError(
                f"{self.asset}: atr.has_prev={self._atr._has_prev!r} "
                f"contradicts atr.count={count}; `has_prev` is set on the "
                f"first bar and never cleared")

    def _restore_pine_vars(self, blob: Dict[str, Any], m: int) -> None:
        """The six pivots, two trends, trailing, three legs, five crossovers."""
        pivots = blob["pivots"]
        _require_keys(pivots, _PIVOT_NAMES, "luxalgo snapshot.pivots")
        for name in _PIVOT_NAMES:
            body = pivots[name]
            at = f"luxalgo snapshot.pivots.{name}"
            _require_keys(body, _PIVOT_KEYS, at)
            pivot = getattr(self, name)
            pivot.currentLevel = _optional_float(body["currentLevel"],
                                                 f"{at}.currentLevel")
            pivot.lastLevel = _optional_float(body["lastLevel"],
                                              f"{at}.lastLevel")
            pivot.crossed = _require_bool(body["crossed"], f"{at}.crossed")
            pivot.barTime = _optional_datetime(body["barTime"],
                                               f"{at}.barTime")
            idx = body["barIndex"]
            if idx is None:
                pivot.barIndex = None
            else:
                pos = _require_index(idx, f"{at}.barIndex")
                if pos >= m:
                    raise LuxAlgoStructureError(
                        f"{self.asset}: {at}.barIndex {pos} is outside the "
                        f"{m} retained bar(s); the origin search would index "
                        f"past the end of the window")
                pivot.barIndex = pos
            if (pivot.barIndex is None) != (pivot.currentLevel is None):
                raise LuxAlgoStructureError(
                    f"{self.asset}: {at} has currentLevel="
                    f"{pivot.currentLevel!r} but barIndex="
                    f"{pivot.barIndex!r}; both are written together in "
                    f"_get_current_structure and a break against a levelled "
                    f"pivot with no index cannot pick an origin window")

        trends = blob["trends"]
        _require_keys(trends, ("internal", "swing"),
                      "luxalgo snapshot.trends")
        for key, trend in (("internal", self.internal_trend),
                           ("swing", self.swing_trend)):
            bias = _require_int(trends[key], f"luxalgo snapshot.trends.{key}")
            if bias not in (0, BULLISH, BEARISH):
                raise LuxAlgoStructureError(
                    f"{self.asset}: trends.{key} bias {bias!r} is not one of "
                    f"0 (virgin), {BULLISH} (bullish), {BEARISH} (bearish); "
                    f"an unknown bias would change every BOS/CHoCH label that "
                    f"follows")
            trend.bias = bias

        trailing = blob["trailing"]
        _require_keys(trailing, _TRAILING_KEYS, "luxalgo snapshot.trailing")
        at = "luxalgo snapshot.trailing"
        self.trailing.top = _optional_float(trailing["top"], f"{at}.top")
        self.trailing.bottom = _optional_float(trailing["bottom"],
                                               f"{at}.bottom")
        self.trailing.barTime = _optional_datetime(trailing["barTime"],
                                                   f"{at}.barTime")
        # ABSOLUTE, never rebased: `_get_current_structure` writes it through
        # `_abs()`, and it is never used as an array index.
        self.trailing.barIndex = (
            None if trailing["barIndex"] is None
            else _require_int(trailing["barIndex"], f"{at}.barIndex"))
        self.trailing.lastTopTime = _optional_datetime(
            trailing["lastTopTime"], f"{at}.lastTopTime")
        self.trailing.lastBottomTime = _optional_datetime(
            trailing["lastBottomTime"], f"{at}.lastBottomTime")

        legs = blob["legs"]
        _require_keys(legs, tuple(name for name, _ in _LEG_NAMES),
                      "luxalgo snapshot.legs")
        for name, attr in _LEG_NAMES:
            body = legs[name]
            at = f"luxalgo snapshot.legs.{name}"
            _require_keys(body, ("leg", "prev"), at)
            state = getattr(self, attr)
            leg = _require_int(body["leg"], f"{at}.leg")
            if leg not in (BEARISH_LEG, BULLISH_LEG):
                raise LuxAlgoStructureError(
                    f"{self.asset}: {at}.leg {leg!r} is not one of "
                    f"{BEARISH_LEG} (bearish), {BULLISH_LEG} (bullish); "
                    f"`ta.change(leg)` against an unknown value would fire "
                    f"pivots that never happened")
            prev = body["prev"]
            if prev is not None:
                prev = _require_int(prev, f"{at}.prev")
                if prev not in (BEARISH_LEG, BULLISH_LEG):
                    raise LuxAlgoStructureError(
                        f"{self.asset}: {at}.prev {prev!r} is not one of "
                        f"{BEARISH_LEG}, {BULLISH_LEG}")
            state.leg, state.prev = leg, prev

        crossover = blob["crossover"]
        _require_keys(crossover, _CROSSOVER_KEYS, "luxalgo snapshot.crossover")
        at = "luxalgo snapshot.crossover"
        self._prev_close = _optional_float(crossover["close"], f"{at}.close")
        self._prev_internal_high = _optional_float(
            crossover["internal_high"], f"{at}.internal_high")
        self._prev_internal_low = _optional_float(
            crossover["internal_low"], f"{at}.internal_low")
        self._prev_swing_high = _optional_float(
            crossover["swing_high"], f"{at}.swing_high")
        self._prev_swing_low = _optional_float(
            crossover["swing_low"], f"{at}.swing_low")

    def _restore_order_blocks(self, blob: Dict[str, Any]) -> None:
        """The live store, `_ob_seq`, and the derived `all_order_blocks`.

        `all_order_blocks` is REBUILT, not carried: it holds every block ever
        created, which is unbounded, and `step()` reads it only as `len(...)`
        plus a tail slice taken from that same length -- both correct for any
        starting content. It is rebuilt as the SAME objects the live store
        holds, in creation order, which preserves the aliasing `step()` and
        `_delete_order_blocks` rely on (mitigating a block mutates it in
        place and both lists see it). Blocks mitigated or evicted before the
        capture are absent from both lists: they were already absent from the
        live store, so no behaviour reads them. Disclosed, not hidden.
        """
        self._ob_seq = _require_index(blob["ob_seq"], "luxalgo snapshot.ob_seq")
        names = tuple(f.name for f in dc_fields(LuxAlgoOrderBlock))
        blocks: List[LuxAlgoOrderBlock] = []
        for i, body in enumerate(blob["live_order_blocks"]):
            at = f"luxalgo snapshot.live_order_blocks[{i}]"
            _require_keys(body, names, at)
            try:
                block = LuxAlgoOrderBlock(**body)
            except TypeError as exc:      # unreachable: keys checked above
                raise LuxAlgoStructureError(f"{at}: {exc}") from exc
            if block.mitigated_bar_idx is not None:
                raise LuxAlgoStructureError(
                    f"{at}: a block in the LIVE store carries "
                    f"mitigated_bar_idx={block.mitigated_bar_idx!r}; "
                    f"_delete_order_blocks stamps that field and removes the "
                    f"block in the same step, so a live mitigated block never "
                    f"existed")
            if block.asset != self.asset:
                raise LuxAlgoStructureError(
                    f"{at}: block asset {block.asset!r} != structure asset "
                    f"{self.asset!r}; one structure carries one symbol")
            if not 0 <= block.seq < self._ob_seq:
                raise LuxAlgoStructureError(
                    f"{at}: seq {block.seq} is outside [0, ob_seq="
                    f"{self._ob_seq}); the creation counter and the store "
                    f"disagree about how many blocks were created")
            if blocks and block.seq >= blocks[-1].seq:
                raise LuxAlgoStructureError(
                    f"{at}: seq {block.seq} is not older than the preceding "
                    f"entry's {blocks[-1].seq}. The live store is newest-first "
                    f"(Pine `unshift`) and `pop()` evicts the LAST element, so "
                    f"a store in another order would evict a different block "
                    f"at the {ALGO_OB_CAP}-block cap")
            blocks.append(block)
        if len(blocks) > ALGO_OB_CAP:
            raise LuxAlgoStructureError(
                f"{self.asset}: {len(blocks)} live order block(s) exceed the "
                f"Pine cap of {ALGO_OB_CAP}; the store could not have grown "
                f"past it")
        self.live_order_blocks = blocks
        self.all_order_blocks = list(reversed(blocks))


# ---------------------------------------------------------------------------
# JSON PORTABILITY. `capture_state()` returns native objects, including
# `datetime`; a payload on disk cannot hold one. The two directions live HERE,
# beside the schema they convert, for one reason: a second copy of the field
# list in `state.py` could drift from this one, and the failure mode of drift is
# a snapshot that decodes into a structure quietly missing a timestamp. There is
# exactly one table, and the walker refuses anything it does not recognise.
# ---------------------------------------------------------------------------
def _snapshot_datetime_paths() -> FrozenSet[Tuple[str, ...]]:
    """Every place a `capture_state()` blob holds a `datetime`.

    A path is the sequence of dict keys from the root, with LIST indices
    collapsed to `*`. Derived from the same tables `capture_state()` enumerates
    -- `_PIVOT_NAMES` and `dataclasses.fields(LuxAlgoOrderBlock)` -- so adding a
    pivot or a block field cannot leave this set stale.
    """
    block_dt = tuple(f.name for f in dc_fields(LuxAlgoOrderBlock)
                     if "datetime" in str(f.type))
    if not block_dt:                                   # pragma: no cover
        raise LuxAlgoStructureError(
            "LuxAlgoOrderBlock declares no datetime field; the snapshot "
            "datetime table cannot be derived and must not be guessed")
    paths = {("arrays", "times", "*")}
    paths |= {("pivots", name, "barTime") for name in _PIVOT_NAMES}
    paths |= {("trailing", key) for key in _TRAILING_KEYS
              if key.endswith("Time")}
    paths |= {("live_order_blocks", "*", name) for name in block_dt}
    return frozenset(paths)


#: The single source of truth for where the timestamps are.
LUXALGO_SNAPSHOT_DATETIME_PATHS: FrozenSet[Tuple[str, ...]] = (
    _snapshot_datetime_paths())


def _walk_snapshot(value: Any, path: Tuple[str, ...], to_json: bool,
                   where: str) -> Any:
    """Convert one node, refusing anything the snapshot cannot legitimately hold.

    Shared by both directions so they cannot disagree about the shape. The
    checks are the point, not the conversion:

      * a `datetime` at a path NOT in the table is a refusal, which is what
        turns a forgotten new timestamp field into a capture-time failure
        instead of a silent `str(datetime)` in the payload;
      * a value at a path IN the table that is neither a timestamp nor `None`
        is a refusal in both directions;
      * any other type -- a Decimal, a tuple, a set, a NaN, a live object --
        is a refusal, because it either cannot survive JSON or cannot survive
        it EXACTLY.
    """
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise LuxAlgoStructureError(
                    f"{where}: non-string key {key!r} in the snapshot")
            out[key] = _walk_snapshot(item, path + (key,), to_json,
                                      f"{where}.{key}")
        return out
    if isinstance(value, list):
        return [_walk_snapshot(item, path + ("*",), to_json, f"{where}[{i}]")
                for i, item in enumerate(value)]
    if path in LUXALGO_SNAPSHOT_DATETIME_PATHS:
        if value is None:
            return None
        if to_json:
            if not isinstance(value, datetime):
                raise LuxAlgoStructureError(
                    f"{where}: expected a datetime, got "
                    f"{type(value).__name__} {value!r}")
            return value.isoformat()
        if not isinstance(value, str):
            raise LuxAlgoStructureError(
                f"{where}: expected an ISO-8601 timestamp string, got "
                f"{type(value).__name__} {value!r}")
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise LuxAlgoStructureError(
                f"{where}: {value!r} is not an ISO-8601 timestamp") from exc
    if isinstance(value, datetime):
        raise LuxAlgoStructureError(
            f"{where}: a datetime appears at snapshot path "
            f"{'.'.join(path) or '<root>'}, which is not in "
            f"LUXALGO_SNAPSHOT_DATETIME_PATHS; refusing to write a timestamp "
            f"this codec cannot read back")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise LuxAlgoStructureError(
                f"{where}: {value!r} is not finite and cannot be persisted")
        return value
    raise LuxAlgoStructureError(
        f"{where}: {type(value).__name__} {value!r} is not a portable "
        f"snapshot value")


def snapshot_to_json(blob: Dict[str, Any]) -> Dict[str, Any]:
    """A `capture_state()` blob with every timestamp as an ISO-8601 string."""
    _require_keys(blob, LUXALGO_SNAPSHOT_KEYS, "luxalgo snapshot")
    return _walk_snapshot(blob, (), True, "luxalgo snapshot")


def snapshot_from_json(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The inverse of `snapshot_to_json`, ready for `restore_state()`.

    Shape only. Every structural and cross-field invariant is checked by
    `restore_state`, which is where that argument lives; this function's job is
    to hand it native objects or refuse.
    """
    _require_keys(payload, LUXALGO_SNAPSHOT_KEYS, "luxalgo snapshot")
    return _walk_snapshot(payload, (), False, "luxalgo snapshot")


__all__ = [
    "BULLISH",
    "BEARISH",
    "BULLISH_LEG",
    "BEARISH_LEG",
    "TAG_BOS",
    "TAG_CHOCH",
    "SWINGS_LENGTH",
    "INTERNAL_LENGTH",
    "EQUAL_LENGTH",
    "EQUAL_THRESHOLD",
    "ATR_LENGTH",
    "ATR_HIGH_VOL_MULT",
    "OB_MITIGATION_HIGH_LOW",
    "SHOW_INTERNAL_OB",
    "SHOW_SWING_OB",
    "INTERNAL_OB_DISPLAY",
    "CONFLUENCE_FILTER",
    "ALGO_OB_CAP",
    "CLEAN_MITIGATION",
    "LUXALGO_SNAPSHOT_KEYS",
    "LUXALGO_SNAPSHOT_DATETIME_PATHS",
    "LuxAlgoStructureError",
    "LuxAlgoPivot",
    "LuxAlgoTrend",
    "LuxAlgoTrailing",
    "LuxAlgoOrderBlock",
    "LuxAlgoInternalStructure",
    "snapshot_to_json",
    "snapshot_from_json",
]
