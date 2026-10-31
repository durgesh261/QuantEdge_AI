"""
Manual SMC — State Capture & Restore (Phase 1 Step 5).
======================================================

Crash-safe persistence of everything `ManualSMCLifecycle` needs to resume
deterministically, as a plain JSON-compatible payload. NO database, no
execution, no runtime wiring: this module turns live objects into a dict and
back, and refuses anything it cannot restore exactly.

WHAT IS CAPTURED
----------------
    config                 every `ManualSpecConfig` field
    activation             the activation mode and entry-window length the
                           snapshot was produced under (see below)
    detector               WHICH structure detector defines an order block
                           here — `LUXALGO_INTERNAL` (production) or
                           `MANUAL_SPEC_BOS` (see below)
    scanners               per asset, in whichever shape that detector needs:
                           ManualSpec persists lookback, min_width, the bar
                           history deque and the `_consumed` origin key set;
                           LuxAlgo persists lookback, min_width, the accepted
                           candle log and a verification digest
    live_obs               every live `ManualOBRecord`, IN INSERTION ORDER,
                           with its `ManualOBState`
    active_trade           the single `ManualActiveTrade`, or null
    exits                  every resolved `ManualTradeExit`
    last_trade_closed_dt   the oracle's same-timestamp re-entry watermark
    watermark              the processed-candle watermark, per asset
    invalidations          the operator's permanent "never trade this order
                           block again" decisions

THE DETECTOR IS PART OF THE STATE
---------------------------------
`DETECTOR_LUXALGO_INTERNAL` is the production default. The two detectors do not
merely disagree about thresholds: they disagree about which candle becomes an
order block and where that block's edges are. The LuxAlgo zone is the FULL
origin candle, so its distal edge — the stop — sits roughly twice as far from
the entry as ManualSpec's half-candle zone does, which roughly halves the
leverage the sizing rule derives. A snapshot resumed under the wrong detector
would therefore re-risk a LIVE position at a different size.

Nothing else in the payload can settle the question. A `ManualOBRecord` looks
identical whichever detector made it (`bos_or_choch` is populated only by
LuxAlgo, and it is blank for a legitimate ManualSpec record), and a lifecycle
that has not yet seen a candle has no scanner to inspect at all. So `detector`
is a MANDATORY top-level key, validated against the same `DETECTORS` frozenset
the `ManualSMCLifecycle` constructor validates against, passed into the rebuilt
lifecycle by NAME, and cross-checked against the real type of every scanner
object on both the capture and the restore side. That is why the schema version
moved to 4 and v3 payloads are refused rather than upgraded.

HOW THE LUXALGO SCANNER IS PERSISTED — REPLAY, NOT FIELDS
---------------------------------------------------------
`LuxAlgoInternalOBScanner` wraps a Pine reconstruction carrying roughly
twenty-five mutable attributes (eight unbounded parallel series, an RMA-based
ATR(200), six pivots, two trend biases, three independent leg states) plus nine
audit trails. Serialising those field-by-field would mean that a single
forgotten field restores a scanner which silently disagrees with the one that
was captured — and the disagreement would surface as a different trade, not as
an error. So the snapshot stores the ACCEPTED CANDLE LOG and replays it, which
rebuilds every one of those attributes BY CONSTRUCTION, then verifies the
result against a digest captured alongside it (stream position, order-block
bookkeeping, both trend biases, the rejection counters and a hash over the live
order-block store in store order). A mismatch is a refusal, not a warning.

The cost is disclosed rather than engineered away: this payload is O(bars) per
asset — roughly 19,700 rows per symbol over the canonical history since
2024-06-01 — and it CANNOT be bounded, because the origin search slices the
parsed-high/parsed-low series from a pivot that may be arbitrarily old, so
dropping the oldest candles would silently change which candle becomes a trade
zone.

Insertion order is captured as a LIST, not a mapping: `candidate_obs()` and
`_step2_update_obs()` both iterate `live_obs` in insertion order, so an order
change is a behaviour change.

`ManualActiveTrade.ob` IS the same object as `live_obs[ob_id]` — the lifecycle
mutates it through both paths. It is therefore persisted BY REFERENCE
(`ob_ref`) and re-linked to the identical restored object; a dangling ref is a
refusal, never a silently duplicated OB.

Lifecycle EVENTS are deliberately not captured. They are diagnostics emitted
per call and are never read back as a control signal.

THE ACTIVATION POLICY IS PART OF THE STATE
------------------------------------------
A `LIMIT_RESTING` OB means two different things depending on the activation
mode it was armed under: under `FIRST_TOUCH_WINDOW` its limit dies at
`limit_active_from_bar + entry_window_candles - 1`, under the oracle's
`C_PROBE_PULLBACK` it never times out. Neither the mode nor the window length
is recoverable from the OB pool, and defaulting one would let a restore silently
extend or cancel a live order (safety rule #13). Both are therefore persisted in
a mandatory `activation` block and validated on restore — which is exactly why
the schema version moved to 2 and v1 payloads are refused rather than upgraded.

THE PROCESSED-CANDLE WATERMARK IS OWNED HERE, NOT BY THE LIFECYCLE
------------------------------------------------------------------
`ManualSMCLifecycle` has no notion of "the last candle I processed" — it
mutates state per call and returns events. Replaying one candle after a crash
would therefore re-run the BOS scan and re-fill an entry. `CandleWatermark`
supplies the missing monotonic per-asset marker; `lifecycle.py` is NOT modified
to carry it (Step 5 forbids that), so the caller must advance it. See the
ATOMICITY REQUIREMENT below — it is a real architectural constraint on Step 6.

EXACTNESS
---------
    float     JSON round-trips a finite Python float exactly (`repr` emits the
              shortest string that round-trips). Non-finite floats are REFUSED
              at capture rather than written as the invalid JSON tokens
              `NaN`/`Infinity`, and `json.dumps(..., allow_nan=False)` is a
              second barrier.
    Decimal   encoded with `str()`, which preserves the SCALE as well as the
              value: `Decimal("0.5000")` restores with its trailing zeros and
              an identical `as_tuple()`. No lifecycle field is Decimal today;
              the codec is registered so that a future quantized price cannot
              be persisted through `float` by accident.
    datetime  `isoformat()` / `fromisoformat()` — exact to the microsecond,
              tz-aware and naive alike, no normalisation applied.
    Enum      persisted as `.value` and restored by exact value match. No case
              folding, no aliases, no numeric ordinals.

FAIL CLOSED
-----------
Every refusal raises a `StateError` subclass. Malformed payloads, missing
fields, UNKNOWN/EXTRA fields, unknown enum values, unsupported schema
versions, non-finite numbers, duplicate OB ids, dangling active-trade refs,
two TRADE_ACTIVE OBs (safety rules #13/#14), a scanner whose lookback
disagrees with the config, a history longer than the deque can hold, and a
watermark behind the state it claims to cover are all rejected. Nothing is
coerced, defaulted or guessed.

The detector adds four more refusals, all of them by name: an unknown detector
string; a scanner block whose key set matches neither detector's shape; a
declared detector that disagrees with the real type of the scanner object beside
it (checked in BOTH directions, at capture and at restore); and a LuxAlgo candle
log that does not replay back into the digest captured with it.

The field codec is driven by `dataclasses.fields()` and an explicit annotation
table. A new field of an unsupported type makes capture RAISE instead of
silently dropping it from the snapshot.

ATOMICITY REQUIREMENT (architectural, for Step 6+)
--------------------------------------------------
`process_candle()` mutates the lifecycle and `advance()` moves the watermark.
They are two operations, so a crash between them leaves the state ahead of the
watermark and the candle is replayed on resume — re-scanning the BOS and
possibly re-filling an entry. This module cannot fix that (it must not modify
`lifecycle.py`), so instead it DETECTS it: a capture whose OBs or scanner
history run ahead of the watermark is refused by `restore_state`. Step 6 must
persist the lifecycle mutation and the watermark advance in ONE atomic write.

DELIBERATELY ABSENT
-------------------
No database, no SQL, no ORM, no file I/O, no exchange calls, no HTTP, no
order placement, no runtime/live wiring. `capture_state` returns a dict and
`dumps_state` returns a string; where those bytes are stored is Step 6+.
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from quantedge.strategy.manual_smc.lifecycle import (
    ACTIVATION_MODES,
    DETECTOR_LUXALGO_INTERNAL,
    DETECTOR_MANUAL_SPEC,
    DETECTORS,
    ManualActiveTrade,
    ManualSMCLifecycle,
    ManualTradeExit,
)
from quantedge.strategy.manual_smc.invalidation import (
    ManualOBInvalidation,
    ManualOBInvalidationLedger,
    verify_fingerprint,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBRecord,
    ManualOBState,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.scanner import (
    LuxAlgoInternalOBScanner,
    ManualOBScanner,
    ManualSpecBOSScanner,
)
from quantedge.strategy.manual_smc.luxalgo_structure import (
    TAG_BOS,
    TAG_CHOCH,
    LuxAlgoInternalStructure,
    LuxAlgoStructureError,
    snapshot_from_json,
    snapshot_to_json,
)

#: Payload discriminator. A payload without exactly this name is refused, so a
#: snapshot of some other strategy can never be loaded as Manual SMC state.
MANUAL_SMC_STATE_SCHEMA: str = "MANUAL_SMC_STATE"

#: Current schema version. Bump on ANY change to the payload shape.
#: v2 added the mandatory `activation` block (activation mode + entry-window
#: length). A v1 payload does not record which activation mode produced it, and
#: guessing one would decide a safety question by default (rule #13), so v1 is
#: deliberately NOT restorable by this build.
#: v3 added the mandatory `invalidations` list — the operator's record of order
#: blocks that must NEVER trade again (Task N). A v2 payload carries no such
#: list, and restoring one would have to assume the list is empty; that
#: assumption resurrects every deleted order block as tradable, which is the
#: exact failure the feature exists to prevent. So v2 is refused too. The
#: consequence is deliberate and must not be hidden: an on-disk v2 snapshot
#: written by an earlier build cannot be resumed and has to be recaptured.
#: v4 added the mandatory `detector` key and a second scanner payload shape.
#: A v3 payload records no detector, and this build's default detector is
#: `DETECTOR_LUXALGO_INTERNAL` rather than the `DETECTOR_MANUAL_SPEC` that was
#: the default when v3 was written. There is no evidence inside a v3 payload
#: that could settle the question either: `capture_state` does not refuse a
#: lifecycle with an empty `_scanners` map, so a v3 snapshot can be entirely
#: free of scanner blocks and therefore free of type evidence. Assuming either
#: detector would resume a live position under a structure detector that draws
#: DIFFERENT zones, at DIFFERENT edges, with a DIFFERENT stop distance and
#: therefore DIFFERENT leverage — exactly the safety question a default must
#: never answer (rule #13). So v3 is refused too, and the consequence is stated
#: rather than hidden: an on-disk v3 snapshot must be recaptured.
#: v5 added the mandatory `ob_structure_tags` side map — the BOS/CHoCH label of
#: every live LuxAlgo order block — and replaced the O(bars) LuxAlgo scanner
#: body with the bounded causal one (`structure` + pruned `bars`). A v4 payload
#: carries neither. The tag could not be inferred: it is a property of the break
#: that CREATED the block, and nothing else in the payload records whether that
#: break was a continuation or a reversal, so restoring a v4 snapshot would have
#: to assume a label. The scanner body cannot be read either — a v4 body carries
#: the full candle log and no `structure` block, so this build's decoder has no
#: key set to match it against, and inventing the Pine scalars from a replay of
#: the log would be a reconstruction this build cannot prove equals the one that
#: was captured. Both are refusals rather than guesses (rules #13, #15). The
#: consequence is deliberate and must not be hidden: an on-disk v4 snapshot
#: written by an earlier build cannot be resumed and has to be recaptured.
MANUAL_SMC_STATE_SCHEMA_VERSION: int = 5

#: Versions this build can restore. Unknown -> refusal, never best-effort.
SUPPORTED_SCHEMA_VERSIONS: frozenset = frozenset({5})

#: Exact key set of the `activation` block.
_ACTIVATION_KEYS: Tuple[str, ...] = ("mode", "entry_window_candles")

#: Exact top-level key set. Missing -> MissingFieldError, extra -> UnknownFieldError.
_TOP_LEVEL_KEYS: Tuple[str, ...] = (
    "schema",
    "schema_version",
    "strategy_name",
    "strategy_version",
    "config",
    "activation",
    "detector",
    "scanners",
    "live_obs",
    # The BOS/CHoCH label of every live order block that has one. A SIDE MAP and
    # not a 30th record field, because `ManualOBRecord`'s field list is pinned
    # against the frozen oracle's; see `encode_ob_structure_tags`.
    "ob_structure_tags",
    "active_trade",
    "exits",
    "last_trade_closed_dt",
    "watermark",
    "invalidations",
)

#: The only OB states a between-candles snapshot may contain. TRADE_CLOSED and
#: INVALIDATED OBs are popped from the pool inside `process_candle`, so seeing
#: one persisted means the capture was torn mid-candle.
_PERSISTABLE_OB_STATES: Tuple[ManualOBState, ...] = (
    ManualOBState.AWAITING_DISPLACEMENT,
    ManualOBState.LIMIT_RESTING,
    ManualOBState.TRADE_ACTIVE,
)

#: The ONLY structure labels a payload may carry, taken from the LuxAlgo module
#: rather than re-spelled here so the two can never drift apart. An empty label
#: is NOT a member: it is represented by ABSENCE from the side map, so a
#: ManualSpec snapshot — whose records are all unlabelled — carries an empty
#: list and stays byte-compatible with its own history. Anything else is
#: refused by name; a label this build cannot name is never coerced to "", to
#: BOS, or to the closest spelling (rules #15, #16).
_OB_STRUCTURE_TAGS: frozenset = frozenset({TAG_BOS, TAG_CHOCH})


# ---------------------------------------------------------------------------
# Errors. Every refusal is a StateError; nothing is ever coerced or defaulted.
# ---------------------------------------------------------------------------
class StateError(RuntimeError):
    """Base class for every state capture/restore refusal."""


class MalformedStateError(StateError):
    """The payload is not shaped like a Manual SMC snapshot."""


class MissingFieldError(MalformedStateError):
    """A required field is absent."""


class UnknownFieldError(MalformedStateError):
    """
    An unrecognised field is present.

    Rejected rather than ignored: an extra key normally means the payload was
    written by a different schema, and silently dropping it would restore a
    partial state that looks complete.
    """


class UnknownEnumValueError(MalformedStateError):
    """An enum value is not an exact member value of its enum."""


class UnsupportedSchemaVersionError(StateError):
    """The payload's schema name or version is not restorable by this build."""


class StateIntegrityError(StateError):
    """
    The payload parsed, but the state it describes is not self-consistent.

    Two TRADE_ACTIVE OBs, a dangling active-trade reference, a duplicate OB id,
    a scanner disagreeing with the config, or state running ahead of the
    watermark.
    """


class StateSchemaError(StateError):
    """
    A persisted dataclass has a field this module has no codec for.

    Raised at CAPTURE time as well as restore time, so a new lifecycle field
    can never be silently omitted from a snapshot.
    """


class WatermarkRegressionError(StateError):
    """A candle was replayed or arrived out of order for its asset."""


# ---------------------------------------------------------------------------
# Primitive codecs. `where` is a dotted path used verbatim in error messages.
# ---------------------------------------------------------------------------
def encode_str(value: object, where: str = "value") -> str:
    if not isinstance(value, str):
        raise MalformedStateError(
            f"{where}: expected str, got {type(value).__name__} {value!r}")
    return value


def encode_int(value: object, where: str = "value") -> int:
    # bool is a subclass of int; accepting it would turn True into 1 silently.
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedStateError(
            f"{where}: expected int, got {type(value).__name__} {value!r}")
    return value


def encode_bool(value: object, where: str = "value") -> bool:
    if not isinstance(value, bool):
        raise MalformedStateError(
            f"{where}: expected bool, got {type(value).__name__} {value!r}")
    return value


def encode_float(value: object, where: str = "value") -> float:
    """
    Finite floats only.

    A NaN or Infinity would be written as the invalid JSON tokens `NaN` /
    `Infinity`, and no price, distance or leverage may legitimately be
    non-finite, so this refuses instead.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedStateError(
            f"{where}: expected float, got {type(value).__name__} {value!r}")
    out = float(value)
    if not math.isfinite(out):
        raise MalformedStateError(
            f"{where}: {value!r} is not finite and cannot be persisted")
    return out


def encode_datetime(value: object, where: str = "value") -> str:
    """`isoformat()` — exact to the microsecond; tz offset preserved as-is."""
    if not isinstance(value, datetime):
        raise MalformedStateError(
            f"{where}: expected datetime, got {type(value).__name__} {value!r}")
    return value.isoformat()


def decode_datetime(value: object, where: str = "value") -> datetime:
    if not isinstance(value, str):
        raise MalformedStateError(
            f"{where}: expected an ISO-8601 datetime string, got "
            f"{type(value).__name__} {value!r}")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise MalformedStateError(
            f"{where}: {value!r} is not an ISO-8601 datetime") from exc


def encode_decimal(value: object, where: str = "value") -> str:
    """
    `str(Decimal)` — preserves the SCALE, not merely the value.

    `Decimal("0.5000")` restores with the same `as_tuple()`, so an on-grid
    exchange price keeps the tick's own scale. A float is refused outright: it
    would have to be rounded to become a Decimal, and this module never rounds.
    """
    if not isinstance(value, Decimal):
        raise MalformedStateError(
            f"{where}: expected Decimal, got {type(value).__name__} "
            f"{value!r}; floats are refused because converting one here would "
            f"silently round an exchange price")
    if not value.is_finite():
        raise MalformedStateError(
            f"{where}: Decimal {value!r} is not finite")
    return str(value)


def decode_decimal(value: object, where: str = "value") -> Decimal:
    if not isinstance(value, str):
        raise MalformedStateError(
            f"{where}: expected a Decimal string, got "
            f"{type(value).__name__} {value!r}")
    try:
        out = Decimal(value)
    except InvalidOperation as exc:
        raise MalformedStateError(
            f"{where}: {value!r} is not a Decimal") from exc
    if not out.is_finite():
        raise MalformedStateError(f"{where}: Decimal {value!r} is not finite")
    return out


def encode_ob_state(value: object, where: str = "value") -> str:
    if not isinstance(value, ManualOBState):
        raise MalformedStateError(
            f"{where}: expected ManualOBState, got {type(value).__name__} "
            f"{value!r}")
    return value.value


def decode_ob_state(value: object, where: str = "value") -> ManualOBState:
    """
    Exact member-value match only.

    No case folding, no aliases, no numeric ordinals: an unrecognised state
    string is a refusal, because guessing which state a crashed process was in
    is exactly the ambiguity that must never be resolved by inference.
    """
    if not isinstance(value, str):
        raise MalformedStateError(
            f"{where}: expected a ManualOBState value string, got "
            f"{type(value).__name__} {value!r}")
    try:
        return ManualOBState(value)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in ManualOBState)
        raise UnknownEnumValueError(
            f"{where}: {value!r} is not a ManualOBState; allowed: "
            f"{allowed}") from exc


def _optional(codec: Callable[..., Any]) -> Callable[..., Any]:
    """Lift a codec to accept None, and ONLY None, as an absent value."""
    def _wrapped(value: object, where: str = "value") -> Any:
        if value is None:
            return None
        return codec(value, where)
    return _wrapped


# ---------------------------------------------------------------------------
# Field-driven dataclass codec.
#
# Keyed by the ANNOTATION STRING, which is what `dataclasses.fields()` reports
# because every Manual SMC module uses `from __future__ import annotations`.
# An annotation absent from this table raises `StateSchemaError` at capture
# time, so a field added to `ManualOBRecord` / `ManualActiveTrade` /
# `ManualTradeExit` / `ManualSpecConfig` tomorrow cannot silently vanish from a
# snapshot and be restored as its dataclass default.
# ---------------------------------------------------------------------------
_ENCODERS: Dict[str, Callable[..., Any]] = {
    "str": encode_str,
    "int": encode_int,
    "bool": encode_bool,
    "float": encode_float,
    "Decimal": encode_decimal,
    "datetime": encode_datetime,
    "ManualOBState": encode_ob_state,
    "Optional[str]": _optional(encode_str),
    "Optional[int]": _optional(encode_int),
    "Optional[float]": _optional(encode_float),
    "Optional[Decimal]": _optional(encode_decimal),
    "Optional[datetime]": _optional(encode_datetime),
}

_DECODERS: Dict[str, Callable[..., Any]] = {
    "str": encode_str,
    "int": encode_int,
    "bool": encode_bool,
    "float": encode_float,
    "Decimal": decode_decimal,
    "datetime": decode_datetime,
    "ManualOBState": decode_ob_state,
    "Optional[str]": _optional(encode_str),
    "Optional[int]": _optional(encode_int),
    "Optional[float]": _optional(encode_float),
    "Optional[Decimal]": _optional(decode_decimal),
    "Optional[datetime]": _optional(decode_datetime),
}

#: Annotations persisted by reference instead of inline. The referenced object
#: must already exist in the restore's object table, so identity is preserved.
_BY_REFERENCE: Dict[str, str] = {"ManualOBRecord": "ob_id"}

#: Suffix appended to a by-reference field's key: `ob` -> `ob_ref`.
_REF_SUFFIX: str = "_ref"


def _payload_key(fld: dc.Field) -> str:
    """The payload key a dataclass field is stored under."""
    if fld.type in _BY_REFERENCE:
        return fld.name + _REF_SUFFIX
    return fld.name


def expected_keys(cls: type) -> Tuple[str, ...]:
    """The exact key set a dataclass's payload must have, in field order."""
    return tuple(_payload_key(f) for f in dc.fields(cls))


def _codec_for(fld: dc.Field, table: Dict[str, Callable[..., Any]],
               owner: type) -> Callable[..., Any]:
    codec = table.get(fld.type)
    if codec is None:
        raise StateSchemaError(
            f"{owner.__name__}.{fld.name}: no state codec for annotation "
            f"{fld.type!r}. Add one deliberately — a field without a codec "
            f"would be dropped from the snapshot and silently restored as its "
            f"dataclass default.")
    return codec


def encode_dataclass(obj: Any) -> Dict[str, Any]:
    """
    Encode a dataclass instance field-by-field, in declaration order.

    By-reference fields become `<name>_ref` holding the referent's `ob_id`.
    Declaration order makes the payload key order deterministic, which is what
    lets two snapshots be compared as bytes.
    """
    cls = type(obj)
    if not dc.is_dataclass(obj) or isinstance(obj, type):
        raise StateSchemaError(
            f"expected a dataclass instance, got {cls.__name__} {obj!r}")
    out: Dict[str, Any] = {}
    for fld in dc.fields(obj):
        value = getattr(obj, fld.name)
        where = f"{cls.__name__}.{fld.name}"
        if fld.type in _BY_REFERENCE:
            attr = _BY_REFERENCE[fld.type]
            if not isinstance(value, ManualOBRecord):
                raise MalformedStateError(
                    f"{where}: expected {fld.type}, got "
                    f"{type(value).__name__} {value!r}")
            out[_payload_key(fld)] = encode_str(getattr(value, attr), where)
            continue
        out[fld.name] = _codec_for(fld, _ENCODERS, cls)(value, where)
    return out


def require_mapping(payload: object, where: str) -> Dict[str, Any]:
    """A JSON object, with string keys. Lists, None and scalars are refused."""
    if not isinstance(payload, dict):
        raise MalformedStateError(
            f"{where}: expected an object, got {type(payload).__name__} "
            f"{payload!r}")
    bad = [k for k in payload if not isinstance(k, str)]
    if bad:
        raise MalformedStateError(f"{where}: non-string keys {bad!r}")
    return payload


def require_exact_keys(payload: Dict[str, Any], keys: Tuple[str, ...],
                       where: str) -> None:
    """Missing keys and unknown keys are BOTH refusals."""
    present = set(payload)
    wanted = set(keys)
    missing = sorted(wanted - present)
    if missing:
        raise MissingFieldError(f"{where}: missing required field(s) {missing}")
    unknown = sorted(present - wanted)
    if unknown:
        raise UnknownFieldError(
            f"{where}: unknown field(s) {unknown}; refused rather than "
            f"ignored, because an unexpected key means this payload was "
            f"written by a different schema")


def require_list(payload: object, where: str) -> List[Any]:
    if not isinstance(payload, list):
        raise MalformedStateError(
            f"{where}: expected a list, got {type(payload).__name__} "
            f"{payload!r}")
    return payload


def decode_dataclass(cls: type, payload: object, where: str,
                     refs: Optional[Dict[str, Any]] = None) -> Any:
    """
    Rebuild a dataclass from its payload, with an exact key-set check.

    By-reference fields are resolved out of `refs`, so the restored object is
    the IDENTICAL object already restored elsewhere in the snapshot, not a copy.
    """
    body = require_mapping(payload, where)
    require_exact_keys(body, expected_keys(cls), where)
    kwargs: Dict[str, Any] = {}
    for fld in dc.fields(cls):
        key = _payload_key(fld)
        field_where = f"{where}.{key}"
        if fld.type in _BY_REFERENCE:
            ref = encode_str(body[key], field_where)
            table = refs or {}
            if ref not in table:
                raise StateIntegrityError(
                    f"{field_where}: {ref!r} does not name any restored "
                    f"{fld.type} — the reference is dangling and the state "
                    f"cannot be resumed")
            kwargs[fld.name] = table[ref]
            continue
        kwargs[fld.name] = _codec_for(fld, _DECODERS, cls)(
            body[key], field_where)
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# Processed-candle watermark.
#
# `ManualSMCLifecycle` does not track which candle it last processed, so after
# a crash there is nothing in the lifecycle itself to stop the same candle
# being fed twice — which would re-run the BOS scan and could re-fill an entry.
# This is that missing marker. It lives here because Step 5 must not modify
# `lifecycle.py`.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CandleMark:
    """The last candle processed for one asset."""
    asset: str
    bar_idx: int
    ts: datetime


class CandleWatermark:
    """
    Per-asset monotonic processed-candle marker.

    `advance()` is strictly increasing in BOTH `bar_idx` and `ts` per asset:
    a replayed or out-of-order candle raises rather than being applied twice.
    Gaps ARE allowed — missing candles in the feed are a data property, not a
    state corruption.
    """

    def __init__(self, marks: Optional[Dict[str, CandleMark]] = None) -> None:
        self._marks: Dict[str, CandleMark] = dict(marks or {})

    def __len__(self) -> int:
        return len(self._marks)

    def assets(self) -> List[str]:
        return list(self._marks)

    def last(self, asset: str) -> Optional[CandleMark]:
        return self._marks.get(encode_str(asset, "asset"))

    def is_processed(self, asset: str, bar_idx: int) -> bool:
        """True when this bar has already been processed for this asset."""
        mark = self.last(asset)
        if mark is None:
            return False
        return encode_int(bar_idx, "bar_idx") <= mark.bar_idx

    def advance(self, asset: str, bar_idx: int, ts: datetime) -> CandleMark:
        """Record a processed candle. Fails closed on replay or regression."""
        key = encode_str(asset, "asset")
        idx = encode_int(bar_idx, "bar_idx")
        when = ts if isinstance(ts, datetime) else None
        if when is None:
            raise MalformedStateError(
                f"watermark.ts: expected datetime, got {type(ts).__name__} "
                f"{ts!r}")
        prev = self._marks.get(key)
        if prev is not None:
            if idx <= prev.bar_idx:
                raise WatermarkRegressionError(
                    f"{key}: bar {idx} is not after the processed watermark "
                    f"{prev.bar_idx}; replaying a candle would re-scan the BOS "
                    f"and could re-fill an entry")
            try:
                regressed = when <= prev.ts
            except TypeError as exc:      # naive vs tz-aware, already fatal
                raise MalformedStateError(
                    f"{key}: cannot compare candle timestamp "
                    f"{when!r} with watermark {prev.ts!r}") from exc
            if regressed:
                raise WatermarkRegressionError(
                    f"{key}: candle timestamp {when.isoformat()} is not after "
                    f"the watermark {prev.ts.isoformat()}")
        mark = CandleMark(asset=key, bar_idx=idx, ts=when)
        self._marks[key] = mark
        return mark


def encode_watermark(watermark: CandleWatermark) -> List[Dict[str, Any]]:
    """Sorted by asset so the payload is canonical, not insertion-ordered."""
    if not isinstance(watermark, CandleWatermark):
        raise MalformedStateError(
            f"watermark: expected CandleWatermark, got "
            f"{type(watermark).__name__} {watermark!r}")
    return [encode_dataclass(watermark.last(asset))
            for asset in sorted(watermark.assets())]


def decode_watermark(payload: object) -> CandleWatermark:
    entries = require_list(payload, "watermark")
    marks: Dict[str, CandleMark] = {}
    for i, entry in enumerate(entries):
        mark = decode_dataclass(CandleMark, entry, f"watermark[{i}]")
        if mark.asset in marks:
            raise StateIntegrityError(
                f"watermark[{i}]: duplicate mark for asset {mark.asset!r}")
        marks[mark.asset] = mark
    return CandleWatermark(marks)


# ---------------------------------------------------------------------------
# Scanner state. TWO detectors, TWO payload shapes, ONE declared identity.
#
# `ManualSpecBOSScanner`'s whole memory is a bounded history deque plus a
# consumed-origin set, and both are persisted verbatim: without them a restored
# scanner would re-emit an OB for an origin it already consumed, violating "one
# origin, one setup, forever".
#
# `LuxAlgoInternalOBScanner` — the production detector — cannot be persisted
# that way. Behind it sits a Pine reconstruction carrying eight unbounded
# parallel arrays, a streaming ATR, six pivots, two trend biases, three
# independent leg states, five crossover look-backs, two order-block stores and
# nine audit trails. Serialising that field by field would mean a silent
# divergence the day a field is added and forgotten, and it is also the LARGER
# payload. So its accepted candles are persisted instead and the structure is
# rebuilt BY REPLAY, which reproduces every one of those fields by construction.
# A verification digest captured beside the candles is re-checked afterwards, so
# a replay that does NOT land where the capture was is a refusal rather than a
# plausible-looking scanner (safety rules #13, #15).
# ---------------------------------------------------------------------------
#: `(bar_idx, open, high, low, close, ts)` — the tuple `scan()` appends. Shared
#: by both scanners: `LuxAlgoInternalOBScanner.bars_for()` logs the same shape.
_HISTORY_CODECS: Tuple[Tuple[str, Callable[..., Any], Callable[..., Any]], ...] = (
    ("bar_idx", encode_int, encode_int),
    ("open", encode_float, encode_float),
    ("high", encode_float, encode_float),
    ("low", encode_float, encode_float),
    ("close", encode_float, encode_float),
    ("ts", encode_datetime, decode_datetime),
)

#: Exact key set of a `MANUAL_SPEC_BOS` scanner block.
_SCANNER_KEYS: Tuple[str, ...] = (
    "asset", "lookback", "min_width", "history", "consumed")

#: Exact key set of a `LUXALGO_INTERNAL` scanner block. `structure` is the
#: BOUNDED causal snapshot of the Pine reconstruction and IS the state;
#: `rejections` carries the two counters that no longer come from a replay;
#: `bars` is the pruned candle window that goes with the structure; `digest` is
#: the verification, never a substitute for either.
_LUXALGO_SCANNER_KEYS: Tuple[str, ...] = (
    "asset", "lookback", "min_width", "structure", "rejections", "bars",
    "digest")

#: Exact key set of the persisted rejection counters.
_LUXALGO_REJECTION_KEYS: Tuple[str, ...] = (
    "non_positive_width", "below_min_width")

#: Exact key set of the LuxAlgo verification digest.
_LUXALGO_DIGEST_KEYS: Tuple[str, ...] = (
    "stream_bars", "retained_bars", "bars_sha256", "structure_sha256",
    "base_bar_idx", "last_bar_idx", "ob_seq", "live_obs", "internal_trend",
    "swing_trend", "rejected_non_positive_width", "rejected_below_min_width",
    "live_ob_sha256")


def detector_for_scanner(scanner: object, where: str = "scanner") -> str:
    """The detector name a scanner object IS, read from its own type.

    This is evidence, not a default: the object either is one of the two
    supported scanners or it is neither, in which case there is nothing to
    persist and the answer is a refusal (safety rule #15).
    """
    if isinstance(scanner, ManualSpecBOSScanner):
        return DETECTOR_MANUAL_SPEC
    if isinstance(scanner, LuxAlgoInternalOBScanner):
        return DETECTOR_LUXALGO_INTERNAL
    raise MalformedStateError(
        f"{where}: expected one of ManualSpecBOSScanner, "
        f"LuxAlgoInternalOBScanner; got {type(scanner).__name__}")


def _scanner_bar_indices(scanner: ManualOBScanner, asset: str) -> List[int]:
    """Every bar index the scanner currently holds for `asset`.

    Type-aware because the two scanners hold their bars differently: a bounded
    `_history` deque against the LuxAlgo per-asset candle log. The torn-capture
    check needs the same answer from both.

    BOTH ARE NOW WINDOWS, NOT COMPLETE HISTORIES, and that does not weaken the
    torn-capture check. Each store keeps a SUFFIX of the stream — the deque
    drops the oldest, and the LuxAlgo payload keeps only the candles at or after
    the causal watermark — while the check looks for bars NEWER than the
    processed watermark. A bar ahead of the mark is by definition among the
    newest, so it is always present to be found. Only the ENUMERATION in a
    refusal message can be shorter than the full history.
    """
    if isinstance(scanner, ManualSpecBOSScanner):
        return [row[0] for row in scanner._history]
    if isinstance(scanner, LuxAlgoInternalOBScanner):
        return [row[0] for row in scanner.bars_for(asset)]
    raise MalformedStateError(
        f"scanner[{asset!r}]: expected one of ManualSpecBOSScanner, "
        f"LuxAlgoInternalOBScanner; got {type(scanner).__name__}")


def encode_activation(lifecycle: "ManualSMCLifecycle") -> Dict[str, Any]:
    """
    Encode the activation policy the snapshot was produced under.

    Persisted because it is not derivable from anything else in the payload: the
    same OB pool means different things under `FIRST_TOUCH_WINDOW` (a resting
    limit has a deadline) and under `C_PROBE_PULLBACK` (it does not). Restoring
    under the wrong one would silently change whether live orders expire, so it
    is recorded and checked rather than defaulted (rule #13).
    """
    return {
        "mode": encode_str(lifecycle.activation_mode, "activation.mode"),
        "entry_window_candles": encode_int(
            lifecycle.entry_window_candles, "activation.entry_window_candles"),
    }


def decode_activation(payload: object, where: str = "activation"
                      ) -> Tuple[str, int]:
    """Decode and validate the activation block. Returns `(mode, window)`."""
    body = require_mapping(payload, where)
    require_exact_keys(body, _ACTIVATION_KEYS, where)
    mode = encode_str(body["mode"], f"{where}.mode")
    if mode not in ACTIVATION_MODES:
        raise StateIntegrityError(
            f"{where}.mode: {mode!r} is not a known activation mode "
            f"({sorted(ACTIVATION_MODES)}); refusing to restore state whose "
            f"entry-activation policy this build cannot reproduce")
    window = encode_int(body["entry_window_candles"],
                        f"{where}.entry_window_candles")
    if window < 1:
        raise StateIntegrityError(
            f"{where}.entry_window_candles: {window} would cancel every order "
            f"before it could fill")
    return mode, window


def decode_detector(payload: object, where: str = "detector") -> str:
    """Decode and validate the declared structure detector.

    The detector is the one payload field that decides WHICH ALGORITHM defines
    an order block. It is validated against `DETECTORS` — the same frozenset the
    `ManualSMCLifecycle` constructor validates against — so an unknown or
    aliased name is refused here rather than reaching the lifecycle, and no
    name is ever mapped to a "closest" known detector.
    """
    name = encode_str(payload, where)
    if name not in DETECTORS:
        raise StateIntegrityError(
            f"{where}: {name!r} is not a known detector "
            f"({sorted(DETECTORS)}); refusing to restore state whose structure "
            f"detector this build cannot reproduce (safety rule #15)")
    return name


def encode_scanner(asset: str, scanner: ManualOBScanner,
                   detector: Optional[str] = None) -> Dict[str, Any]:
    """
    Encode one scanner, in the shape its own detector requires.

    `detector` is the identity the SNAPSHOT declares. It is optional so a caller
    holding only a scanner can still encode it, but when supplied it is
    CROSS-CHECKED against the object's real type: a lifecycle whose `detector`
    says one thing while its `_scanners` hold the other is a bug that would
    otherwise be persisted as if it were fine.
    """
    kind = detector_for_scanner(scanner, f"scanner[{asset!r}]")
    if detector is not None and detector != kind:
        raise StateIntegrityError(
            f"scanner[{asset!r}]: the snapshot declares detector "
            f"{detector!r} but this scanner is a {type(scanner).__name__}, "
            f"which is {kind!r}; refusing to persist a snapshot whose declared "
            f"detector disagrees with the scanner that produced it")
    if kind == DETECTOR_MANUAL_SPEC:
        return _encode_manual_spec_scanner(asset, scanner)
    return _encode_luxalgo_scanner(asset, scanner)


def _encode_manual_spec_scanner(asset: str,
                                scanner: ManualSpecBOSScanner) -> Dict[str, Any]:
    """
    Encode a `MANUAL_SPEC_BOS` scanner: the history deque and consumed set.

    `consumed` is sorted so the payload is canonical: the set's iteration order
    is arbitrary but irrelevant to behaviour, and an arbitrary order would make
    two snapshots of identical state differ as bytes.
    """
    history = _encode_bar_rows(scanner._history,
                               f"scanner[{asset!r}].history")

    consumed: List[List[Any]] = []
    for key in scanner._consumed:
        where = f"scanner[{asset!r}].consumed"
        if not isinstance(key, tuple) or len(key) != 2:
            raise MalformedStateError(
                f"{where}: expected (asset, origin_bar_idx) tuples, got "
                f"{key!r}")
        consumed.append([encode_str(key[0], f"{where}.asset"),
                         encode_int(key[1], f"{where}.origin_bar_idx")])
    consumed.sort(key=lambda pair: (pair[0], pair[1]))

    return {
        "asset": encode_str(asset, "scanner.asset"),
        "lookback": encode_int(scanner.lookback, "scanner.lookback"),
        "min_width": encode_float(scanner.min_width, "scanner.min_width"),
        "history": history,
        "consumed": consumed,
    }


def _encode_bar_rows(rows: Iterable[Any], where: str) -> List[List[Any]]:
    """Encode `(bar_idx, o, h, l, c, ts)` tuples. Shared by both scanner shapes."""
    out: List[List[Any]] = []
    for i, row in enumerate(rows):
        row_where = f"{where}[{i}]"
        if not isinstance(row, tuple) or len(row) != len(_HISTORY_CODECS):
            raise MalformedStateError(
                f"{row_where}: expected a {len(_HISTORY_CODECS)}-tuple, got "
                f"{row!r}")
        out.append([enc(value, f"{row_where}.{name}")
                    for value, (name, enc, _) in zip(row, _HISTORY_CODECS)])
    return out


def _luxalgo_bars_sha256(rows: List[List[Any]]) -> str:
    """A checksum over the PERSISTED candle rows, hashed in their encoded form.

    Takes the encoded rows rather than the scanner on purpose: what must be
    covered is what the payload actually carries, which is the pruned window, so
    that capture -> restore -> capture hashes the same bytes for the same
    window. Hashing the scanner's whole in-memory log instead would make the
    re-captured digest disagree with the restored one for no reason other than
    that the running process had seen more candles than it persisted.

    It hashes `repr()`-exact floats and `isoformat()` timestamps precisely so
    that capture-side and restore-side hashing see the same bytes for the same
    numbers.

    Being a CHECKSUM, it makes tampering require two coordinated edits instead
    of one, and it catches accidental corruption (a truncated list, a float
    mangled by a lossy writer) outright. It is not, and does not claim to be, a
    signature.
    """
    payload = "\n".join("|".join(str(cell) for cell in row) for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _luxalgo_structure_sha256(wire: Dict[str, Any]) -> str:
    """A checksum over the bounded structure block, in canonical JSON form.

    The structure block IS the scanner's state under this codec, so it gets the
    same treatment the candle log used to get. `sort_keys` makes the bytes
    independent of dict ordering, and `allow_nan=False` means a non-finite float
    raises here rather than being hashed into a payload that no reader could
    accept — `snapshot_to_json` has already refused one, so this is the second
    lock on the same door, not a new policy.

    One hash over the whole block rather than a field-by-field digest: the block
    is generated from a single enumerated contract
    (`_assert_snapshot_contract`), so a per-field list here could only go stale
    against it, and a stale list would silently stop covering a field.
    """
    return hashlib.sha256(
        json.dumps(wire, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _luxalgo_retained_rows(scanner: LuxAlgoInternalOBScanner, asset: str,
                           retained: int) -> List[List[Any]]:
    """The encoded candle rows that go with a bounded structure snapshot.

    The last `retained` rows of the scanner's log — the window the structure
    still holds after `causal_watermark()` pruning, and nothing older. The
    scanner's own in-memory log is NOT pruned: `scan()` appends to it and the
    structure prunes only at capture time, so the running process keeps its full
    log and only the PAYLOAD is bounded. That is the smaller change and it keeps
    `bars_for()`'s contract intact for the parity tests that read it.

    A log shorter than the structure's retained window is a refusal, not a pad:
    `scan()` steps the structure and appends the row together, so the two can
    only disagree if one of them was tampered with.
    """
    where = f"scanner[{asset!r}].bars"
    log = scanner.bars_for(asset)
    if len(log) < retained:
        raise StateIntegrityError(
            f"{where}: the scanner holds {len(log)} candle(s) but its Pine "
            f"structure retains {retained}; scan() steps the structure and "
            f"appends the candle together, so the two cannot disagree")
    return _encode_bar_rows(log[len(log) - retained:], where)


def _luxalgo_live_ob_sha256(scanner: LuxAlgoInternalOBScanner,
                            asset: str) -> str:
    """A canonical hash over the LIVE order-block store, in store order.

    Order is part of the state, not an artefact: index 0 is the newest (Pine's
    `unshift`) and the last element is what `pop()` evicts at the 100-block cap,
    so a store holding the same blocks in a different order would behave
    differently on the next eviction.

    Prices go in through `repr()`, which is the shortest string that round-trips
    a float exactly, so the hash is as exact as the payload's own numbers.
    """
    structure = scanner.structure_for(asset)
    lines = [
        "|".join((
            block.direction,
            block.tag,
            str(block.origin_bar_idx),
            str(block.created_bar_idx),
            repr(block.barHigh),
            repr(block.barLow),
            str(block.mitigated_bar_idx),
            str(block.seq),
        ))
        for block in structure.live_order_blocks
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _luxalgo_payload(asset: str, scanner: LuxAlgoInternalOBScanner
                     ) -> Tuple[Dict[str, Any], List[List[Any]]]:
    """The two halves of a LuxAlgo scanner body: structure block and candles.

    One function so that the encoder and the restore-side verification derive
    them THE SAME WAY. If the digest were computed from the live scanner while
    the payload were written from a capture, the two could disagree about a
    field for reasons that have nothing to do with corruption.
    """
    structure = scanner.structure_for(asset)
    try:
        blob = structure.capture_state()
        wire = snapshot_to_json(blob)
    except LuxAlgoStructureError as exc:
        raise StateIntegrityError(
            f"scanner[{asset!r}].structure: {exc}") from exc
    rows = _luxalgo_retained_rows(scanner, asset,
                                  len(blob["arrays"]["times"]))
    return wire, rows


def _luxalgo_digest(asset: str, scanner: LuxAlgoInternalOBScanner
                    ) -> Dict[str, Any]:
    """The verification digest of a LuxAlgo scanner as it would be persisted."""
    wire, rows = _luxalgo_payload(asset, scanner)
    return _luxalgo_digest_of(asset, scanner, wire, rows)


def _luxalgo_digest_of(asset: str, scanner: LuxAlgoInternalOBScanner,
                       wire: Dict[str, Any], rows: List[List[Any]]
                       ) -> Dict[str, Any]:
    """The verification digest for one LuxAlgo scanner block.

    WHAT IT IS NOW. The old digest checked that REPLAYING the candle log landed
    where the capture was. There is no replay any more: the bounded structure
    block is installed field-by-field, so this digest checks that the INSTALL
    produced the structure the payload declares. Two hashes cover the payload
    itself (`structure_sha256`, `bars_sha256`) and the remaining fields are the
    headline numbers a reader would use to recognise the structure — the stream
    position, the order-block bookkeeping, both trend biases and the two
    rejection counters.

    It is a CHECK, not the state: correctness comes from `restore_state`, which
    re-verifies every cross-field invariant the capture side guarantees. A
    forgotten digest field would weaken this check, never the restore.

    `base_bar_idx` IS READ OUT OF `wire`, not off the live structure, and that is
    load-bearing. Capture rebases it by the watermark (`base + k`), so the live
    attribute and the persisted one differ by exactly the number of pruned bars;
    taking the live one would make every re-captured digest disagree with the
    payload it came from.

    `all_obs` IS GONE, and the reason is stated rather than hidden.
    `all_order_blocks` is an audit list of every block ever created, including
    long-mitigated ones; a bounded snapshot deliberately does not carry them, and
    `_restore_order_blocks` rebuilds the list as `reversed(live_order_blocks)`.
    Its length after a restore is therefore the LIVE count, not the historical
    one, so the old field could only ever mismatch. The creation count is still
    covered — `ob_seq` is the counter `scan()` actually increments, and it is
    checked here.
    """
    structure = scanner.structure_for(asset)
    where = f"scanner[{asset!r}].digest"
    return {
        "stream_bars": encode_int(structure._atr._count,
                                  f"{where}.stream_bars"),
        "retained_bars": encode_int(len(rows), f"{where}.retained_bars"),
        "bars_sha256": _luxalgo_bars_sha256(rows),
        "structure_sha256": _luxalgo_structure_sha256(wire),
        "base_bar_idx": _optional(encode_int)(
            wire["base_bar_idx"], f"{where}.base_bar_idx"),
        "last_bar_idx": _optional(encode_int)(
            wire["last_bar_idx"], f"{where}.last_bar_idx"),
        "ob_seq": encode_int(structure._ob_seq, f"{where}.ob_seq"),
        "live_obs": encode_int(len(structure.live_order_blocks),
                               f"{where}.live_obs"),
        "internal_trend": encode_int(structure.internal_trend.bias,
                                     f"{where}.internal_trend"),
        "swing_trend": encode_int(structure.swing_trend.bias,
                                  f"{where}.swing_trend"),
        "rejected_non_positive_width": encode_int(
            scanner.rejected_non_positive_width,
            f"{where}.rejected_non_positive_width"),
        "rejected_below_min_width": encode_int(
            scanner.rejected_below_min_width,
            f"{where}.rejected_below_min_width"),
        "live_ob_sha256": _luxalgo_live_ob_sha256(scanner, asset),
    }


def _encode_luxalgo_scanner(asset: str, scanner: LuxAlgoInternalOBScanner
                            ) -> Dict[str, Any]:
    """
    Encode a `LUXALGO_INTERNAL` scanner: bounded structure state plus its window.

    THE PAYLOAD IS BOUNDED BY THE CAUSAL WATERMARK, NOT BY THE STREAM LENGTH.
    The Pine `var` state — the ATR recursion, three independent leg states, six
    pivots, two trend biases, the trailing extremes, five crossover memories, the
    OB store and its sequence counter — is carried VERBATIM in `structure`,
    because it is path-dependent from bar 0 and no truncated replay could
    reproduce it. Only the per-bar ARRAYS are cut, and only below
    `LuxAlgoInternalStructure.causal_watermark()`, which is the position proved
    in that method to be unreachable by any future read.

    THE BOUND IS CAUSAL, NOT CONSTANT. Retention is `len(bars) - watermark`, and
    the watermark sits at the oldest live pivot, so a market that never crosses
    a swing level retains almost everything: a measured trending sequence kept
    1,496 of 1,500 bars. The worst case is still O(bars) and is NOT hidden — see
    `test_manual_smc_luxalgo_snapshot_parity.py::TestTheWorstCaseIsNotHidden`,
    which pins that sequence. What is gone is the UNCONDITIONAL O(bars) cost:
    over the real 19,762-bar four-symbol history the retained window measures
    112-114 bars per symbol.

    WHY `bars` SURVIVES AT ALL. The structure keeps `high`, `low`, `close` and
    `time` per retained bar but NO `open` — `step()` validates the open and never
    consumes it — so the candle window is the only place an open is recorded, and
    `_scanner_bar_indices` reads this log for the torn-capture check. It is
    pruned to exactly the structure's retained window, so it inherits the same
    bound, and the two copies are cross-checked against each other on restore.

    `rejections` is REAL STATE here, not audit noise: the old codec re-derived
    the two counters by replaying every candle, and a bounded snapshot has no
    replay to re-derive them from. `scanner.rejected` — the list of refused
    blocks — is NOT persisted and does not survive a restore; it is read by no
    production code path, only by parity tests, and inventing entries for it
    would be manufacturing an audit trail this build never produced.

    A scanner holding candles for an asset OTHER than the one being encoded is
    refused: `ManualSMCLifecycle._scanner_for` builds one instance per asset, so
    a second asset in the log means the instance was shared and its Pine
    structure is not the structure this block claims to describe.
    """
    logged = sorted(a for a, rows in scanner._bars.items() if rows)
    if logged and logged != [asset]:
        raise StateIntegrityError(
            f"scanner[{asset!r}]: this scanner also holds candles for "
            f"{[a for a in logged if a != asset]!r}; one LuxAlgo scanner "
            f"instance carries one asset's Pine structure and cannot describe "
            f"another's")
    structures = sorted(a for a in scanner._structures)
    if structures and structures != [asset]:
        raise StateIntegrityError(
            f"scanner[{asset!r}]: this scanner also holds Pine structures for "
            f"{[a for a in structures if a != asset]!r}; only {asset!r} would "
            f"be persisted, so restoring would silently drop the others")
    wire, bars = _luxalgo_payload(asset, scanner)
    return {
        "asset": encode_str(asset, "scanner.asset"),
        "lookback": encode_int(scanner.lookback, "scanner.lookback"),
        "min_width": encode_float(scanner.min_width, "scanner.min_width"),
        "structure": wire,
        "rejections": {
            "non_positive_width": encode_int(
                scanner.rejected_non_positive_width,
                "scanner.rejections.non_positive_width"),
            "below_min_width": encode_int(
                scanner.rejected_below_min_width,
                "scanner.rejections.below_min_width"),
        },
        "bars": bars,
        "digest": _luxalgo_digest_of(asset, scanner, wire, bars),
    }


def _infer_scanner_kind(body: Dict[str, Any], where: str) -> str:
    """Read a scanner block's detector off its OWN key set.

    Used only when no detector was declared. An exact key-set match is evidence;
    anything else is a refusal, because picking the "closest" shape would be a
    guess about which algorithm decides what an order block is (rule #13).
    """
    keys = set(body)
    if keys == set(_SCANNER_KEYS):
        return DETECTOR_MANUAL_SPEC
    if keys == set(_LUXALGO_SCANNER_KEYS):
        return DETECTOR_LUXALGO_INTERNAL
    raise MalformedStateError(
        f"{where}: key set {sorted(keys)} matches neither a "
        f"{DETECTOR_MANUAL_SPEC} block {sorted(_SCANNER_KEYS)} nor a "
        f"{DETECTOR_LUXALGO_INTERNAL} block {sorted(_LUXALGO_SCANNER_KEYS)}")


def _decode_scanner_head(body: Dict[str, Any], cfg: ManualSpecConfig,
                         where: str) -> Tuple[str, int, float]:
    """The three fields both shapes share, plus the config agreement check."""
    asset = encode_str(body["asset"], f"{where}.asset")
    lookback = encode_int(body["lookback"], f"{where}.lookback")
    min_width = encode_float(body["min_width"], f"{where}.min_width")
    if lookback != cfg.lookback:
        raise StateIntegrityError(
            f"{where}: scanner lookback {lookback} != config lookback "
            f"{cfg.lookback}; the restored scanner would not match the one the "
            f"lifecycle builds")
    if min_width != cfg.min_ob_width:
        raise StateIntegrityError(
            f"{where}: scanner min_width {min_width} != config min_ob_width "
            f"{cfg.min_ob_width}")
    return asset, lookback, min_width


def _decode_bar_rows(payload: object, where: str) -> List[Tuple[Any, ...]]:
    """Decode a list of `(bar_idx, o, h, l, c, ts)` rows. Shared by both shapes."""
    out: List[Tuple[Any, ...]] = []
    for i, row in enumerate(require_list(payload, where)):
        row_where = f"{where}[{i}]"
        cells = require_list(row, row_where)
        if len(cells) != len(_HISTORY_CODECS):
            raise MalformedStateError(
                f"{row_where}: expected {len(_HISTORY_CODECS)} cells, got "
                f"{len(cells)}")
        out.append(tuple(dec(cell, f"{row_where}.{name}")
                         for cell, (name, _, dec) in zip(cells,
                                                         _HISTORY_CODECS)))
    return out


def decode_scanner(payload: object, cfg: ManualSpecConfig, where: str,
                   detector: Optional[str] = None
                   ) -> Tuple[str, ManualOBScanner]:
    """
    Rebuild one scanner, verifying it agrees with the restored config.

    A scanner whose `lookback`/`min_width` differ from the config's would not
    be the scanner `ManualSMCLifecycle._scanner_for` builds, so subsequent
    behaviour would diverge from uninterrupted execution: refuse.

    `detector` is the identity the snapshot declared. When it is supplied the
    block MUST have that detector's exact key set, so a payload that declares
    one detector and carries the other's scanner shape is refused by name rather
    than half-decoded. When it is omitted the shape itself decides.
    """
    body = require_mapping(payload, where)
    kind = detector if detector is not None else _infer_scanner_kind(body, where)
    if kind == DETECTOR_MANUAL_SPEC:
        require_exact_keys(body, _SCANNER_KEYS, where)
        return _decode_manual_spec_scanner(body, cfg, where)
    if kind == DETECTOR_LUXALGO_INTERNAL:
        require_exact_keys(body, _LUXALGO_SCANNER_KEYS, where)
        return _decode_luxalgo_scanner(body, cfg, where)
    raise StateIntegrityError(
        f"{where}: {kind!r} is not a known detector ({sorted(DETECTORS)}); "
        f"refusing to guess which algorithm produced this scanner state "
        f"(safety rule #13)")


def _decode_manual_spec_scanner(body: Dict[str, Any], cfg: ManualSpecConfig,
                                where: str
                                ) -> Tuple[str, ManualSpecBOSScanner]:
    """Rebuild a `MANUAL_SPEC_BOS` scanner from its deque and consumed set."""
    asset, lookback, min_width = _decode_scanner_head(body, cfg, where)
    scanner = ManualSpecBOSScanner(lookback=lookback, min_width=min_width)
    rows = require_list(body["history"], f"{where}.history")
    maxlen = scanner._history.maxlen
    if maxlen is not None and len(rows) > maxlen:
        raise StateIntegrityError(
            f"{where}.history: {len(rows)} rows exceed the deque's capacity "
            f"{maxlen}; restoring would silently discard the oldest bars")
    for row in _decode_bar_rows(body["history"], f"{where}.history"):
        scanner._history.append(row)

    for i, key in enumerate(require_list(body["consumed"],
                                         f"{where}.consumed")):
        key_where = f"{where}.consumed[{i}]"
        cells = require_list(key, key_where)
        if len(cells) != 2:
            raise MalformedStateError(
                f"{key_where}: expected [asset, origin_bar_idx], got {cells!r}")
        scanner._consumed.add((
            encode_str(cells[0], f"{key_where}.asset"),
            encode_int(cells[1], f"{key_where}.origin_bar_idx"),
        ))
    return asset, scanner


def _check_luxalgo_bars_agree(asset: str, structure: LuxAlgoInternalStructure,
                              rows: List[Tuple[Any, ...]], where: str) -> None:
    """The candle window and the structure's own arrays must be the same candles.

    The payload carries the retained window TWICE — once as `bars`, once as the
    structure's `highs`/`lows`/`closes`/`times` — because the structure needs no
    `open` and the torn-capture check needs a bar-index log. Two copies is not a
    cost to be optimised away here: it is the only check that can catch a payload
    whose two halves were written by DIFFERENT runs, which neither hash can see
    because each would be individually valid.

    Exact float equality is correct, not fragile: both copies come from the same
    `scan()` call and are encoded through the same `repr()`-exact path, so any
    difference at all means they are not the same candle.

    `open` IS UNCORROBORATED and that is stated rather than papered over. The
    structure records no open, so nothing in the payload can confirm it; it is
    only checked to be a finite float by `_decode_bar_rows`. Nothing downstream
    of the detector reads it — `step()` validates and discards it — so a wrong
    open cannot move a zone, but it also cannot be detected here.
    """
    base = structure._base_bar_idx
    if len(rows) != len(structure.times):
        raise StateIntegrityError(
            f"{where}: {len(rows)} candle row(s) but the structure retains "
            f"{len(structure.times)} bar(s); the pruned window and the "
            f"structure it belongs to must be the same length")
    if not rows:
        return
    if base is None:                                       # unreachable
        raise StateIntegrityError(
            f"{where}: {len(rows)} candle row(s) against a structure with no "
            f"stream position")
    for i, row in enumerate(rows):
        bar_idx, _open, high, low, close, ts = row
        want = (base + i, structure.highs[i], structure.lows[i],
                structure.closes[i], structure.times[i])
        got = (bar_idx, high, low, close, ts)
        if got != want:
            raise StateIntegrityError(
                f"{where}[{i}]: the candle row {got!r} does not match the "
                f"structure's own retained bar {want!r}; the two halves of "
                f"this scanner block describe different candles, so at least "
                f"one of them was not written by the run that captured it")


def _decode_luxalgo_scanner(body: Dict[str, Any], cfg: ManualSpecConfig,
                            where: str
                            ) -> Tuple[str, LuxAlgoInternalOBScanner]:
    """Rebuild a `LUXALGO_INTERNAL` scanner by INSTALLING its bounded state.

    No replay. The Pine reconstruction is restored field-by-field from the
    `structure` block by `LuxAlgoInternalStructure.restore_state`, which re-checks
    every cross-field invariant the capture side guarantees — array lengths, the
    ATR bar count against the stream offset, the pivot rebasing, the
    parsed-high/parsed-low swap against the volatility flag, the live store's
    ordering and cap. A blob that cannot describe a structure `step()` could have
    produced is refused there.

    NO ORDER BLOCKS ARE ADMITTED TO ANY POOL, exactly as before: the live OB pool
    is restored from `live_obs`, which is the pool as it stood AFTER the lifecycle
    had admitted, armed, filled, expired or dropped each block. This function
    rebuilds the DETECTOR's memory only.

    `LuxAlgoStructureError` is translated, not propagated: a structurally
    impossible snapshot means the payload cannot describe a structure this build
    could have produced, which is a state refusal.

    THE COUNTERS COME FROM `rejections`, NOT FROM THE DIGEST. The digest's two
    copies then verify that the body's copies survived the round trip — the same
    "two coordinated edits" property `bars_sha256` has. Reading state out of a
    block documented as a verification would blur what refuses what.
    """
    asset, lookback, min_width = _decode_scanner_head(body, cfg, where)
    expected = _decode_luxalgo_digest(body["digest"], f"{where}.digest")
    rows = _decode_bar_rows(body["bars"], f"{where}.bars")
    wire = require_mapping(body["structure"], f"{where}.structure")
    rejections = require_mapping(body["rejections"], f"{where}.rejections")
    require_exact_keys(rejections, _LUXALGO_REJECTION_KEYS,
                       f"{where}.rejections")

    scanner = LuxAlgoInternalOBScanner(lookback=lookback, min_width=min_width)
    try:
        structure = LuxAlgoInternalStructure.restore_state(
            snapshot_from_json(wire))
    except LuxAlgoStructureError as exc:
        raise StateIntegrityError(
            f"{where}.structure: {exc}; this snapshot does not describe a "
            f"LuxAlgo structure this build can rebuild") from exc
    if structure.asset != asset:
        raise StateIntegrityError(
            f"{where}: the structure block carries asset "
            f"{structure.asset!r} but the scanner block declares {asset!r}; "
            f"one LuxAlgo scanner instance carries one asset's Pine structure")
    scanner._structures[asset] = structure
    scanner._bars[asset] = list(rows)
    scanner.rejected_non_positive_width = encode_int(
        rejections["non_positive_width"],
        f"{where}.rejections.non_positive_width")
    scanner.rejected_below_min_width = encode_int(
        rejections["below_min_width"], f"{where}.rejections.below_min_width")
    _check_luxalgo_bars_agree(asset, structure, rows, f"{where}.bars")

    rebuilt = _luxalgo_digest(asset, scanner)
    diffs = [f"{key}: snapshot={expected[key]!r} restored={rebuilt[key]!r}"
             for key in _LUXALGO_DIGEST_KEYS if expected[key] != rebuilt[key]]
    if diffs:
        raise StateIntegrityError(
            f"{where}: installing the bounded LuxAlgo state did NOT reproduce "
            f"the captured structure: " + "; ".join(diffs) +
            ". The structure block is the scanner's state, so a mismatch "
            f"means the payload was edited or was written by a different "
            f"reconstruction; refusing to resume on a structure that is not "
            f"the captured one")
    return asset, scanner


def _decode_luxalgo_digest(payload: object, where: str) -> Dict[str, Any]:
    """Decode the verification digest. Every field is required and typed."""
    body = require_mapping(payload, where)
    require_exact_keys(body, _LUXALGO_DIGEST_KEYS, where)
    out: Dict[str, Any] = {}
    for key in ("stream_bars", "retained_bars", "ob_seq", "live_obs",
                "internal_trend", "swing_trend",
                "rejected_non_positive_width", "rejected_below_min_width"):
        out[key] = encode_int(body[key], f"{where}.{key}")
    for key in ("base_bar_idx", "last_bar_idx"):
        out[key] = _optional(encode_int)(body[key], f"{where}.{key}")
    for key in ("bars_sha256", "structure_sha256", "live_ob_sha256"):
        out[key] = encode_str(body[key], f"{where}.{key}")
    return out


# ---------------------------------------------------------------------------
# BOS/CHoCH structure labels. A side map, deliberately NOT a record field.
# ---------------------------------------------------------------------------
def encode_ob_structure_tags(pool: Dict[str, ManualOBRecord],
                             detector: str) -> List[List[str]]:
    """
    Persist the BOS/CHoCH label of every live order block that carries one.

    WHY A SIDE MAP AND NOT A 30TH FIELD ON `ManualOBRecord`
    ------------------------------------------------------
    `bos_or_choch` is a `ClassVar` shadowed per instance (see `models.py` and
    `scanner.py`'s `_to_record`). Promoting it to a real dataclass field would
    break two gates that exist on purpose:

      * `test_manual_smc_oracle_equivalence.py` asserts this record's field
        NAMES, ORDER and TYPES are identical to the frozen oracle's. A 30th
        field moves that list, and the oracle cannot be edited to match.
      * `expected_keys()` / `encode_dataclass()` / `require_exact_keys()` refuse
        BOTH missing and unknown keys, so a 30th field would make every
        previously written per-record block un-decodable.

    A top-level side map keyed by `ob_id` leaves `dataclasses.fields()` and
    `expected_keys()` at 29 entries, so neither gate moves, and the label still
    survives capture -> JSON -> restore. It is the smallest representation that
    is compatible with both.

    WHY THE LABEL IS NOT RE-DERIVED ON RESTORE INSTEAD
    -------------------------------------------------
    The LuxAlgo scanner is rebuilt by replay, so its structure does re-classify
    the breaks it re-sees. But the label would then have to be re-attached to a
    live record by MATCHING geometry, and two blocks from the same origin candle
    at the same edges are indistinguishable that way. A near-miss match would
    silently stamp a live block with another block's classification, so the
    label is carried explicitly instead of recomputed (rule #16).

    SHAPE. A list of `[ob_id, tag]` pairs sorted by `ob_id`, so two lifecycles
    holding the same labels capture BYTE-IDENTICALLY — the same canonical-order
    discipline `encode_scanner` applies to `consumed` and `capture_state`
    applies to `invalidations`.

    An unlabelled record is represented by ABSENCE, never by `["id", ""]`. So a
    ManualSpec snapshot emits `[]`, and the pool -> payload mapping stays
    lossless in both directions without `""` becoming a third legal tag.
    """
    tags: List[List[str]] = []
    for ob_id, ob in pool.items():
        raw = getattr(ob, "bos_or_choch", "")
        tag = encode_str(raw, f"live_obs[{ob_id!r}].bos_or_choch")
        if tag == "":
            continue
        if tag not in _OB_STRUCTURE_TAGS:
            raise StateIntegrityError(
                f"live_obs[{ob_id!r}].bos_or_choch: {tag!r} is not a structure "
                f"label this build can name ({sorted(_OB_STRUCTURE_TAGS)}); "
                f"refusing to capture a classification that cannot be restored")
        if detector == DETECTOR_MANUAL_SPEC:
            raise StateIntegrityError(
                f"live_obs[{ob_id!r}].bos_or_choch: {tag!r} on a "
                f"{DETECTOR_MANUAL_SPEC} snapshot; that detector has no trend "
                f"bias and cannot classify a break, so a labelled record under "
                f"it means the pool and the declared detector disagree")
        tags.append([encode_str(ob_id, "live_obs[].ob_id"), tag])
    tags.sort(key=lambda pair: pair[0])
    return tags


def decode_ob_structure_tags(payload: object, pool: Dict[str, ManualOBRecord],
                             detector: str,
                             where: str = "ob_structure_tags") -> None:
    """
    Re-attach persisted BOS/CHoCH labels to the restored live pool, IN PLACE.

    Applied exactly as `scanner.py` applies it at creation time — an instance
    attribute shadowing the `ClassVar` — so a restored record is
    indistinguishable from a freshly detected one.

    Every one of these is a refusal, not a repair:
      * a malformed entry (not a 2-element list, not strings);
      * a tag outside `{BOS, CHoCH}` — including `""`, which must be absence;
      * a duplicate `ob_id`, which would make the winning label depend on
        payload order;
      * an `ob_id` with no matching live OB, which means the map and the pool
        were written by different runs and neither can be trusted;
      * ANY entry at all under `DETECTOR_MANUAL_SPEC`.
    """
    entries = require_list(payload, where)
    seen: Dict[str, str] = {}
    for i, entry in enumerate(entries):
        at = f"{where}[{i}]"
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise MalformedStateError(
                f"{at}: expected a 2-element [ob_id, tag] pair, got {entry!r}")
        ob_id = encode_str(entry[0], f"{at}[0]")
        tag = encode_str(entry[1], f"{at}[1]")
        if tag not in _OB_STRUCTURE_TAGS:
            raise MalformedStateError(
                f"{at}: structure label {tag!r} is not one of "
                f"{sorted(_OB_STRUCTURE_TAGS)}; an unlabelled order block is "
                f"represented by ABSENCE from this map, and an unknown label is "
                f"never coerced to a known one")
        if ob_id in seen:
            raise StateIntegrityError(
                f"{at}: duplicate ob_id {ob_id!r} (already labelled "
                f"{seen[ob_id]!r}); which label wins would depend on payload "
                f"order")
        if ob_id not in pool:
            raise StateIntegrityError(
                f"{at}: ob_id {ob_id!r} is labelled {tag!r} but is not in the "
                f"live pool; the label map and the pool disagree about which "
                f"order blocks exist")
        if detector == DETECTOR_MANUAL_SPEC:
            raise StateIntegrityError(
                f"{at}: a {DETECTOR_MANUAL_SPEC} snapshot carries structure "
                f"label {tag!r} for {ob_id!r}; that detector cannot classify a "
                f"break, so the payload's detector and its labels disagree")
        seen[ob_id] = tag
    for ob_id, tag in seen.items():
        # Instance attribute shadowing the ClassVar; mirrors scanner.py.
        pool[ob_id].bos_or_choch = tag


# ---------------------------------------------------------------------------
# Snapshot capture.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RestoredState:
    """
    Everything `restore_state` hands back.

    `lifecycle` is a live `ManualSMCLifecycle` positioned exactly where the
    captured one was; `watermark` is the processed-candle marker the caller must
    keep advancing; `config` is the config the snapshot was taken under.
    """
    lifecycle: ManualSMCLifecycle
    watermark: CandleWatermark
    config: ManualSpecConfig


def capture_state(
    lifecycle: ManualSMCLifecycle,
    watermark: Optional[CandleWatermark] = None,
) -> Dict[str, Any]:
    """
    Capture a JSON-ready snapshot. The lifecycle is NOT mutated.

    Must be called BETWEEN candles: `process_candle` pops dead OBs at the end
    of its sweep, so a capture taken mid-sweep can contain an INVALIDATED OB,
    which `restore_state` then refuses.
    """
    if not isinstance(lifecycle, ManualSMCLifecycle):
        raise MalformedStateError(
            f"lifecycle: expected ManualSMCLifecycle, got "
            f"{type(lifecycle).__name__} {lifecycle!r}")
    marker = watermark if watermark is not None else CandleWatermark()
    at = lifecycle.active_trade
    detector = encode_str(lifecycle.detector, "detector")
    if detector not in DETECTORS:
        raise StateIntegrityError(
            f"detector: {detector!r} is not a known detector "
            f"({sorted(DETECTORS)}); refusing to capture state whose structure "
            f"detector this build cannot name")
    return {
        "schema": MANUAL_SMC_STATE_SCHEMA,
        "schema_version": MANUAL_SMC_STATE_SCHEMA_VERSION,
        "strategy_name": MANUAL_SMC_STRATEGY_NAME,
        "strategy_version": MANUAL_SMC_STRATEGY_VERSION,
        "config": encode_dataclass(lifecycle.cfg),
        "activation": encode_activation(lifecycle),
        # WHICH ALGORITHM DECIDED WHAT AN ORDER BLOCK IS. Not derivable from the
        # rest of the payload: an OB record looks the same either way, and a
        # lifecycle that has seen no candles yet has no scanner to inspect. The
        # two detectors disagree about zone edges, so they disagree about the
        # stop distance and therefore the leverage — restoring under the wrong
        # one would silently re-risk a live position (rule #13).
        "detector": detector,
        "scanners": [encode_scanner(asset, lifecycle._scanners[asset], detector)
                     for asset in sorted(lifecycle._scanners)],
        "live_obs": [encode_dataclass(ob)
                     for ob in lifecycle.live_obs.values()],
        # The BOS/CHoCH classification of each labelled live block, carried
        # beside the records rather than inside them. `active_trade.ob` is the
        # IDENTICAL object as its pool entry (`_check_active_trade` enforces
        # that), so covering the pool covers the open position too.
        "ob_structure_tags": encode_ob_structure_tags(lifecycle.live_obs,
                                                      detector),
        "active_trade": None if at is None else encode_dataclass(at),
        "exits": [encode_dataclass(x) for x in lifecycle.exits],
        "last_trade_closed_dt": _optional(encode_datetime)(
            lifecycle._last_trade_closed_dt, "last_trade_closed_dt"),
        "watermark": encode_watermark(marker),
        # The operator's permanent "never trade this order block again"
        # decisions. `records()` is sorted by fingerprint, so the payload is
        # canonical: two lifecycles holding the same decisions capture
        # byte-identically, exactly as `encode_scanner` sorts `consumed`.
        "invalidations": [encode_dataclass(r)
                          for r in lifecycle.invalidations.records()],
    }


# ---------------------------------------------------------------------------
# Header / identity validation. Refuses before anything is reconstructed.
# ---------------------------------------------------------------------------
def validate_header(body: Dict[str, Any]) -> int:
    """
    Check the schema discriminator, the version and the strategy identity.

    The identity check enforces the approved policy that Manual SMC state must
    never be confused with the pre-existing LuxAlgo "SMC" / "2.1" strategy's.
    """
    schema = encode_str(body["schema"], "schema")
    if schema != MANUAL_SMC_STATE_SCHEMA:
        raise UnsupportedSchemaVersionError(
            f"schema: expected {MANUAL_SMC_STATE_SCHEMA!r}, got {schema!r}; "
            f"this payload is not Manual SMC state")
    version = encode_int(body["schema_version"], "schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedSchemaVersionError(
            f"schema_version: {version} is not restorable by this build; "
            f"supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}")
    name = encode_str(body["strategy_name"], "strategy_name")
    if name != MANUAL_SMC_STRATEGY_NAME:
        raise UnsupportedSchemaVersionError(
            f"strategy_name: expected {MANUAL_SMC_STRATEGY_NAME!r}, got "
            f"{name!r}; Manual SMC state must stay distinguishable from any "
            f"other strategy's state")
    strategy_version = encode_str(body["strategy_version"], "strategy_version")
    if strategy_version != MANUAL_SMC_STRATEGY_VERSION:
        raise UnsupportedSchemaVersionError(
            f"strategy_version: expected {MANUAL_SMC_STRATEGY_VERSION!r}, got "
            f"{strategy_version!r}")
    return version


def assert_config_compatible(restored: ManualSpecConfig,
                             expected: ManualSpecConfig) -> None:
    """
    Every config field must match, field by field.

    A config change between a crash and a resume would silently alter strategy
    behaviour on the restored state, so it is a refusal rather than a warning.
    """
    if not isinstance(expected, ManualSpecConfig):
        raise MalformedStateError(
            f"expected_config: expected ManualSpecConfig, got "
            f"{type(expected).__name__} {expected!r}")
    diffs = [f"{f.name}: snapshot={getattr(restored, f.name)!r} "
             f"expected={getattr(expected, f.name)!r}"
             for f in dc.fields(ManualSpecConfig)
             if getattr(restored, f.name) != getattr(expected, f.name)]
    if diffs:
        raise StateIntegrityError(
            "config in the snapshot differs from the expected config; "
            "resuming would change strategy behaviour: " + "; ".join(diffs))


# ---------------------------------------------------------------------------
# Snapshot restore.
# ---------------------------------------------------------------------------
def _decode_live_obs(payload: object) -> Dict[str, ManualOBRecord]:
    """Rebuild the live pool, PRESERVING insertion order."""
    pool: Dict[str, ManualOBRecord] = {}
    for i, entry in enumerate(require_list(payload, "live_obs")):
        where = f"live_obs[{i}]"
        ob = decode_dataclass(ManualOBRecord, entry, where)
        if ob.ob_id in pool:
            raise StateIntegrityError(
                f"{where}: duplicate ob_id {ob.ob_id!r}; the live pool is keyed "
                f"by ob_id and cannot hold two")
        if ob.state not in _PERSISTABLE_OB_STATES:
            allowed = ", ".join(s.value for s in _PERSISTABLE_OB_STATES)
            raise StateIntegrityError(
                f"{where}: ob {ob.ob_id!r} is {ob.state.value}, which "
                f"process_candle removes from the pool before it returns — the "
                f"snapshot was captured mid-candle. Allowed: {allowed}")
        pool[ob.ob_id] = ob
    return pool


def _check_active_trade(active: Optional[ManualActiveTrade],
                        pool: Dict[str, ManualOBRecord]) -> None:
    """
    Enforce the single-active-trade invariant (safety rules #13, #14).

    Exactly one OB may be TRADE_ACTIVE and it must be the referenced one; the
    trade's own prices must still agree with the OB they were copied from.
    """
    live_active = [ob for ob in pool.values()
                   if ob.state is ManualOBState.TRADE_ACTIVE]
    if active is None:
        if live_active:
            raise StateIntegrityError(
                f"active_trade is null but {len(live_active)} OB(s) are "
                f"TRADE_ACTIVE: {[ob.ob_id for ob in live_active]}; a filled "
                f"position would be resumed with no trade to close")
        return
    if len(live_active) != 1:
        raise StateIntegrityError(
            f"active_trade is set but {len(live_active)} OB(s) are "
            f"TRADE_ACTIVE: {[ob.ob_id for ob in live_active]}; exactly one is "
            f"required (safety rule #13)")
    if live_active[0] is not active.ob:
        raise StateIntegrityError(
            f"active_trade references {active.ob.ob_id!r} but the "
            f"TRADE_ACTIVE OB in the pool is {live_active[0].ob_id!r}")
    mismatched = [name for name in ("asset", "direction", "entry_price",
                                    "sl_price", "tp_price")
                  if getattr(active, name) != getattr(active.ob, name)]
    if mismatched:
        raise StateIntegrityError(
            f"active_trade disagrees with its OB {active.ob.ob_id!r} on "
            f"{mismatched}; the trade's prices are copied from the OB at fill "
            f"and can never diverge")


def _check_watermark_covers_state(
    watermark: CandleWatermark,
    pool: Dict[str, ManualOBRecord],
    scanners: Dict[str, ManualOBScanner],
) -> None:
    """
    Detect a TORN capture: state ahead of the watermark.

    The lifecycle mutation and the watermark advance are two operations (see
    the module's ATOMICITY REQUIREMENT). If a snapshot contains an OB or a
    scanner bar from a candle the watermark does not yet cover, resuming would
    replay that candle — re-running its BOS scan against a scanner that already
    saw it. Refuse instead. Assets with no mark are not checked; gaps are fine.

    The bar indices come from `_scanner_bar_indices`, which reads whichever
    store the scanner actually keeps — the bounded ManualSpec deque or the
    LuxAlgo candle window — so the check is equally binding under both
    detectors. Both stores are suffixes of the stream, so a bar AHEAD of the
    mark is always still present to be found (see `_scanner_bar_indices`).
    """
    for asset, mark in ((a, watermark.last(a)) for a in watermark.assets()):
        if mark is None:                                   # unreachable
            continue
        ahead = [ob.ob_id for ob in pool.values()
                 if ob.asset == asset and ob.bos_bar_idx > mark.bar_idx]
        if ahead:
            raise StateIntegrityError(
                f"{asset}: OB(s) {ahead} were created on bar(s) after the "
                f"processed watermark {mark.bar_idx}; the capture is torn — "
                f"the lifecycle advanced but the watermark did not")
        scanner = scanners.get(asset)
        if scanner is None:
            continue
        bars = [idx for idx in _scanner_bar_indices(scanner, asset)
                if idx > mark.bar_idx]
        if bars:
            raise StateIntegrityError(
                f"{asset}: scanner history contains bar(s) {sorted(bars)} after "
                f"the processed watermark {mark.bar_idx}; the capture is torn")


def _check_scanner_coverage(pool: Dict[str, ManualOBRecord],
                            scanners: Dict[str, ManualOBScanner]) -> None:
    """
    Every asset with a live OB must have its scanner in the snapshot.

    A missing scanner would be lazily rebuilt EMPTY on the next candle, losing
    the consumed-origin set and re-emitting an OB for an origin already used.
    Under the LuxAlgo detector the loss is larger, not smaller: an empty Pine
    structure has no ATR window, no pivots and no leg states, so the next candle
    would be evaluated against a structure that has seen no history at all.
    """
    missing = sorted({ob.asset for ob in pool.values()} - set(scanners))
    if missing:
        raise StateIntegrityError(
            f"live OBs exist for asset(s) {missing} with no scanner state; the "
            f"scanner would be rebuilt empty and could re-emit a consumed "
            f"origin")


def _decode_invalidations(payload: object) -> ManualOBInvalidationLedger:
    """
    Rebuild the operator invalidation ledger.

    Each record's fingerprint is RE-DERIVED from the characteristics stored
    beside it (`verify_fingerprint`) rather than trusted. The fingerprint is the
    only thing that stops a deleted order block from trading again, so a payload
    that pairs one block's key with another's characteristics is refused instead
    of silently protecting the wrong block.

    A duplicate fingerprint carrying a DIFFERENT decision is refused by the
    ledger itself (`LedgerIntegrityError`): with two conflicting records there is
    no non-arbitrary way to pick the authoritative one.
    """
    ledger = ManualOBInvalidationLedger()
    for i, entry in enumerate(require_list(payload, "invalidations")):
        where = f"invalidations[{i}]"
        record = decode_dataclass(ManualOBInvalidation, entry, where)
        verify_fingerprint(record, where)
        existing = ledger.get(record.fingerprint)
        if existing is not None and existing != record:
            raise StateIntegrityError(
                f"{where}: fingerprint {record.fingerprint!r} already appears "
                f"in this snapshot with a different decision; refusing to guess "
                f"which one is authoritative")
        ledger.add(record)
    return ledger


def _check_invalidations_vs_pool(ledger: ManualOBInvalidationLedger,
                                 pool: Dict[str, ManualOBRecord]) -> None:
    """
    A ledgered order block must not still be sitting in the live pool as a
    tradable setup.

    `invalidate_ob` evicts a matching AWAITING_DISPLACEMENT / LIMIT_RESTING OB
    at the moment the decision is recorded, so a snapshot holding one is
    internally inconsistent — either the capture is torn or the payload was
    edited. Restoring it would put an operator-deleted block back on the path to
    a fill, which is precisely what this feature forbids, so it is a refusal.

    TRADE_ACTIVE is ALLOWED through: that is the documented disclosed case.
    Invalidating a block whose position is already in the market records the
    decision but deliberately does NOT abandon the open trade, because dropping
    it would strand a live position without a stop. The block still cannot be
    entered again once that trade settles.
    """
    if not ledger:
        return
    stuck = sorted(
        ob.ob_id for ob in pool.values()
        if ob.state is not ManualOBState.TRADE_ACTIVE and ledger.covers(ob))
    if stuck:
        raise StateIntegrityError(
            f"live_obs still holds operator-invalidated, non-active order "
            f"block(s) {stuck}; they are evicted when the decision is recorded, "
            f"so this snapshot is inconsistent and restoring it could let a "
            f"deleted setup fill")


def restore_state(
    payload: object,
    expected_config: Optional[ManualSpecConfig] = None,
) -> RestoredState:
    """
    Rebuild a `ManualSMCLifecycle` and its watermark from a snapshot.

    Order matters: the header and the config are validated before anything is
    reconstructed, the OB pool is restored before the active trade so the trade
    can be re-linked to the IDENTICAL OB object, and the integrity checks run
    before the lifecycle is handed back — a payload that fails any of them
    yields an exception, never a partially-populated lifecycle.

    The rebuilt lifecycle is constructed with the PERSISTED activation mode and
    entry-window length, not this build's defaults: a snapshot taken under one
    activation policy must not resume under another, or a resting limit's
    deadline would move. For the same reason it is constructed with the
    PERSISTED invalidation ledger: resuming with an empty ledger would make
    every operator-deleted order block tradable again.

    Private lifecycle attributes (`_scanners`, `_last_trade_closed_dt`) are
    assigned directly. Step 5 must not modify `lifecycle.py`, so there is no
    public setter to use; the assignments are values only and change no logic.
    """
    body = require_mapping(payload, "state")
    require_exact_keys(body, _TOP_LEVEL_KEYS, "state")
    validate_header(body)

    config = decode_dataclass(ManualSpecConfig, body["config"], "config")
    if expected_config is not None:
        assert_config_compatible(config, expected_config)

    activation_mode, entry_window = decode_activation(body["activation"])
    detector = decode_detector(body["detector"])

    scanners: Dict[str, ManualOBScanner] = {}
    for i, entry in enumerate(require_list(body["scanners"], "scanners")):
        asset, scanner = decode_scanner(entry, config, f"scanners[{i}]",
                                        detector)
        if asset in scanners:
            raise StateIntegrityError(
                f"scanners[{i}]: duplicate scanner for asset {asset!r}")
        scanners[asset] = scanner

    pool = _decode_live_obs(body["live_obs"])
    # Re-attach the structure labels BEFORE the active trade is linked, so the
    # trade's OB — the identical object — is already labelled when it is handed
    # back. Refuses on a label the pool cannot account for.
    decode_ob_structure_tags(body["ob_structure_tags"], pool, detector)
    active = (None if body["active_trade"] is None
              else decode_dataclass(ManualActiveTrade, body["active_trade"],
                                    "active_trade", refs=pool))
    _check_active_trade(active, pool)
    _check_scanner_coverage(pool, scanners)

    invalidations = _decode_invalidations(body["invalidations"])
    _check_invalidations_vs_pool(invalidations, pool)

    exits = [decode_dataclass(ManualTradeExit, entry, f"exits[{i}]")
             for i, entry in enumerate(require_list(body["exits"], "exits"))]
    watermark = decode_watermark(body["watermark"])
    _check_watermark_covers_state(watermark, pool, scanners)

    lifecycle = ManualSMCLifecycle(
        config=config,
        activation_mode=activation_mode,
        entry_window_candles=entry_window,
        invalidations=invalidations,
        # NAMED, never defaulted. This is the last construction site in the
        # package that could silently pick up a library default, and a resumed
        # lifecycle running the wrong detector would draw different zones at
        # different edges from the snapshot it just loaded.
        detector=detector,
    )
    lifecycle.live_obs = pool
    lifecycle.active_trade = active
    lifecycle.exits = exits
    lifecycle._scanners = scanners
    lifecycle._last_trade_closed_dt = _optional(decode_datetime)(
        body["last_trade_closed_dt"], "last_trade_closed_dt")
    # Belt and braces on criterion D: the declared identity, the decoded scanner
    # objects and the constructed lifecycle must all agree. `decode_scanner`
    # already enforced the payload SHAPE; this proves the OBJECTS are the type
    # the snapshot declared, so no later `_scanner_for` call can find a scanner
    # of the other kind already cached under an asset.
    if lifecycle.detector != detector:                     # pragma: no cover
        raise StateIntegrityError(
            f"detector: the rebuilt lifecycle reports {lifecycle.detector!r} "
            f"but the snapshot declared {detector!r}")
    for asset, scanner in scanners.items():
        actual = detector_for_scanner(scanner, f"scanners[{asset!r}]")
        if actual != detector:                             # pragma: no cover
            raise StateIntegrityError(
                f"scanners[{asset!r}]: rebuilt a {type(scanner).__name__} "
                f"({actual!r}) for a snapshot declaring {detector!r}")
    return RestoredState(lifecycle=lifecycle, watermark=watermark,
                         config=config)


# ---------------------------------------------------------------------------
# JSON serialisation. Text in, text out — no file or database access.
# ---------------------------------------------------------------------------
def dumps_state(payload: Dict[str, Any]) -> str:
    """
    Serialise a snapshot deterministically.

    `allow_nan=False` is the second barrier against a non-finite number (the
    codecs are the first): `NaN` and `Infinity` are not valid JSON and would
    not survive a round-trip through any other reader. Key order is the
    payload's own insertion order, which the codecs make deterministic, so two
    snapshots of identical state are byte-identical.
    """
    try:
        return json.dumps(payload, allow_nan=False, ensure_ascii=False,
                          separators=(",", ":"))
    except ValueError as exc:
        raise MalformedStateError(
            f"snapshot is not serialisable as strict JSON: {exc}") from exc


def loads_state(text: object) -> Dict[str, Any]:
    """Parse a snapshot string. Refuses anything that is not a JSON object."""
    if not isinstance(text, (str, bytes, bytearray)):
        raise MalformedStateError(
            f"state text: expected str or bytes, got {type(text).__name__} "
            f"{text!r}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedStateError(f"state text is not valid JSON: {exc}") from exc
    return require_mapping(parsed, "state")


def capture_state_json(
    lifecycle: ManualSMCLifecycle,
    watermark: Optional[CandleWatermark] = None,
) -> str:
    """`capture_state` followed by `dumps_state`."""
    return dumps_state(capture_state(lifecycle, watermark))


def restore_state_json(
    text: object,
    expected_config: Optional[ManualSpecConfig] = None,
) -> RestoredState:
    """`loads_state` followed by `restore_state`."""
    return restore_state(loads_state(text), expected_config=expected_config)


__all__ = [
    "MANUAL_SMC_STATE_SCHEMA",
    "MANUAL_SMC_STATE_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "StateError",
    "MalformedStateError",
    "MissingFieldError",
    "UnknownFieldError",
    "UnknownEnumValueError",
    "UnsupportedSchemaVersionError",
    "StateIntegrityError",
    "StateSchemaError",
    "WatermarkRegressionError",
    "CandleMark",
    "CandleWatermark",
    "RestoredState",
    "encode_str",
    "encode_int",
    "encode_bool",
    "encode_float",
    "encode_datetime",
    "decode_datetime",
    "encode_decimal",
    "decode_decimal",
    "encode_ob_state",
    "decode_ob_state",
    "expected_keys",
    "encode_dataclass",
    "decode_dataclass",
    "require_mapping",
    "require_exact_keys",
    "require_list",
    "encode_watermark",
    "decode_watermark",
    "encode_activation",
    "decode_activation",
    "decode_detector",
    "detector_for_scanner",
    "encode_scanner",
    "decode_scanner",
    "encode_ob_structure_tags",
    "decode_ob_structure_tags",
    "validate_header",
    "assert_config_compatible",
    "capture_state",
    "restore_state",
    "dumps_state",
    "loads_state",
    "capture_state_json",
    "restore_state_json",
]
