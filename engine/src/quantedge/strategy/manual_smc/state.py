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
    scanners               per asset: lookback, min_width, the bar history
                           deque and the `_consumed` origin key set
    live_obs               every live `ManualOBRecord`, IN INSERTION ORDER,
                           with its `ManualOBState`
    active_trade           the single `ManualActiveTrade`, or null
    exits                  every resolved `ManualTradeExit`
    last_trade_closed_dt   the oracle's same-timestamp re-entry watermark
    watermark              the processed-candle watermark, per asset

Insertion order is captured as a LIST, not a mapping: `candidate_obs()` and
`_step2_update_obs()` both iterate `live_obs` in insertion order, so an order
change is a behaviour change.

`ManualActiveTrade.ob` IS the same object as `live_obs[ob_id]` — the lifecycle
mutates it through both paths. It is therefore persisted BY REFERENCE
(`ob_ref`) and re-linked to the identical restored object; a dangling ref is a
refusal, never a silently duplicated OB.

Lifecycle EVENTS are deliberately not captured. They are diagnostics emitted
per call and are never read back as a control signal.

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
import json
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

from quantedge.strategy.manual_smc.lifecycle import (
    ManualActiveTrade,
    ManualSMCLifecycle,
    ManualTradeExit,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBRecord,
    ManualOBState,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.scanner import ManualSpecBOSScanner

#: Payload discriminator. A payload without exactly this name is refused, so a
#: snapshot of some other strategy can never be loaded as Manual SMC state.
MANUAL_SMC_STATE_SCHEMA: str = "MANUAL_SMC_STATE"

#: Current schema version. Bump on ANY change to the payload shape.
MANUAL_SMC_STATE_SCHEMA_VERSION: int = 1

#: Versions this build can restore. Unknown -> refusal, never best-effort.
SUPPORTED_SCHEMA_VERSIONS: frozenset = frozenset({1})

#: Exact top-level key set. Missing -> MissingFieldError, extra -> UnknownFieldError.
_TOP_LEVEL_KEYS: Tuple[str, ...] = (
    "schema",
    "schema_version",
    "strategy_name",
    "strategy_version",
    "config",
    "scanners",
    "live_obs",
    "active_trade",
    "exits",
    "last_trade_closed_dt",
    "watermark",
)

#: The only OB states a between-candles snapshot may contain. TRADE_CLOSED and
#: INVALIDATED OBs are popped from the pool inside `process_candle`, so seeing
#: one persisted means the capture was torn mid-candle.
_PERSISTABLE_OB_STATES: Tuple[ManualOBState, ...] = (
    ManualOBState.AWAITING_DISPLACEMENT,
    ManualOBState.LIMIT_RESTING,
    ManualOBState.TRADE_ACTIVE,
)


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
# Scanner state. The history deque and the consumed-origin set ARE the
# scanner's memory: without them a restored scanner would re-emit an OB for an
# origin it already consumed, violating "one origin, one setup, forever".
# ---------------------------------------------------------------------------
#: `(bar_idx, open, high, low, close, ts)` — the tuple `scan()` appends.
_HISTORY_CODECS: Tuple[Tuple[str, Callable[..., Any], Callable[..., Any]], ...] = (
    ("bar_idx", encode_int, encode_int),
    ("open", encode_float, encode_float),
    ("high", encode_float, encode_float),
    ("low", encode_float, encode_float),
    ("close", encode_float, encode_float),
    ("ts", encode_datetime, decode_datetime),
)

_SCANNER_KEYS: Tuple[str, ...] = (
    "asset", "lookback", "min_width", "history", "consumed")


def encode_scanner(asset: str, scanner: ManualSpecBOSScanner) -> Dict[str, Any]:
    """
    Encode one scanner.

    `consumed` is sorted so the payload is canonical: the set's iteration order
    is arbitrary but irrelevant to behaviour, and an arbitrary order would make
    two snapshots of identical state differ as bytes.
    """
    if not isinstance(scanner, ManualSpecBOSScanner):
        raise MalformedStateError(
            f"scanner[{asset!r}]: expected ManualSpecBOSScanner, got "
            f"{type(scanner).__name__}")
    history: List[List[Any]] = []
    for i, row in enumerate(scanner._history):
        where = f"scanner[{asset!r}].history[{i}]"
        if not isinstance(row, tuple) or len(row) != len(_HISTORY_CODECS):
            raise MalformedStateError(
                f"{where}: expected a {len(_HISTORY_CODECS)}-tuple, got "
                f"{row!r}")
        history.append([enc(value, f"{where}.{name}")
                        for value, (name, enc, _) in zip(row, _HISTORY_CODECS)])

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


def decode_scanner(payload: object, cfg: ManualSpecConfig,
                   where: str) -> Tuple[str, ManualSpecBOSScanner]:
    """
    Rebuild one scanner, verifying it agrees with the restored config.

    A scanner whose `lookback`/`min_width` differ from the config's would not
    be the scanner `ManualSMCLifecycle._scanner_for` builds, so subsequent
    behaviour would diverge from uninterrupted execution: refuse.
    """
    body = require_mapping(payload, where)
    require_exact_keys(body, _SCANNER_KEYS, where)
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

    scanner = ManualSpecBOSScanner(lookback=lookback, min_width=min_width)
    rows = require_list(body["history"], f"{where}.history")
    maxlen = scanner._history.maxlen
    if maxlen is not None and len(rows) > maxlen:
        raise StateIntegrityError(
            f"{where}.history: {len(rows)} rows exceed the deque's capacity "
            f"{maxlen}; restoring would silently discard the oldest bars")
    for i, row in enumerate(rows):
        row_where = f"{where}.history[{i}]"
        cells = require_list(row, row_where)
        if len(cells) != len(_HISTORY_CODECS):
            raise MalformedStateError(
                f"{row_where}: expected {len(_HISTORY_CODECS)} cells, got "
                f"{len(cells)}")
        scanner._history.append(tuple(
            dec(cell, f"{row_where}.{name}")
            for cell, (name, _, dec) in zip(cells, _HISTORY_CODECS)))

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
    return {
        "schema": MANUAL_SMC_STATE_SCHEMA,
        "schema_version": MANUAL_SMC_STATE_SCHEMA_VERSION,
        "strategy_name": MANUAL_SMC_STRATEGY_NAME,
        "strategy_version": MANUAL_SMC_STRATEGY_VERSION,
        "config": encode_dataclass(lifecycle.cfg),
        "scanners": [encode_scanner(asset, lifecycle._scanners[asset])
                     for asset in sorted(lifecycle._scanners)],
        "live_obs": [encode_dataclass(ob)
                     for ob in lifecycle.live_obs.values()],
        "active_trade": None if at is None else encode_dataclass(at),
        "exits": [encode_dataclass(x) for x in lifecycle.exits],
        "last_trade_closed_dt": _optional(encode_datetime)(
            lifecycle._last_trade_closed_dt, "last_trade_closed_dt"),
        "watermark": encode_watermark(marker),
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
    scanners: Dict[str, ManualSpecBOSScanner],
) -> None:
    """
    Detect a TORN capture: state ahead of the watermark.

    The lifecycle mutation and the watermark advance are two operations (see
    the module's ATOMICITY REQUIREMENT). If a snapshot contains an OB or a
    scanner bar from a candle the watermark does not yet cover, resuming would
    replay that candle — re-running its BOS scan against a scanner that already
    saw it. Refuse instead. Assets with no mark are not checked; gaps are fine.
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
        bars = [row[0] for row in scanner._history if row[0] > mark.bar_idx]
        if bars:
            raise StateIntegrityError(
                f"{asset}: scanner history contains bar(s) {sorted(bars)} after "
                f"the processed watermark {mark.bar_idx}; the capture is torn")


def _check_scanner_coverage(pool: Dict[str, ManualOBRecord],
                            scanners: Dict[str, ManualSpecBOSScanner]) -> None:
    """
    Every asset with a live OB must have its scanner in the snapshot.

    A missing scanner would be lazily rebuilt EMPTY on the next candle, losing
    the consumed-origin set and re-emitting an OB for an origin already used.
    """
    missing = sorted({ob.asset for ob in pool.values()} - set(scanners))
    if missing:
        raise StateIntegrityError(
            f"live OBs exist for asset(s) {missing} with no scanner state; the "
            f"scanner would be rebuilt empty and could re-emit a consumed "
            f"origin")


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

    scanners: Dict[str, ManualSpecBOSScanner] = {}
    for i, entry in enumerate(require_list(body["scanners"], "scanners")):
        asset, scanner = decode_scanner(entry, config, f"scanners[{i}]")
        if asset in scanners:
            raise StateIntegrityError(
                f"scanners[{i}]: duplicate scanner for asset {asset!r}")
        scanners[asset] = scanner

    pool = _decode_live_obs(body["live_obs"])
    active = (None if body["active_trade"] is None
              else decode_dataclass(ManualActiveTrade, body["active_trade"],
                                    "active_trade", refs=pool))
    _check_active_trade(active, pool)
    _check_scanner_coverage(pool, scanners)

    exits = [decode_dataclass(ManualTradeExit, entry, f"exits[{i}]")
             for i, entry in enumerate(require_list(body["exits"], "exits"))]
    watermark = decode_watermark(body["watermark"])
    _check_watermark_covers_state(watermark, pool, scanners)

    lifecycle = ManualSMCLifecycle(config=config)
    lifecycle.live_obs = pool
    lifecycle.active_trade = active
    lifecycle.exits = exits
    lifecycle._scanners = scanners
    lifecycle._last_trade_closed_dt = _optional(decode_datetime)(
        body["last_trade_closed_dt"], "last_trade_closed_dt")
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
    "encode_scanner",
    "decode_scanner",
    "validate_header",
    "assert_config_compatible",
    "capture_state",
    "restore_state",
    "dumps_state",
    "loads_state",
    "capture_state_json",
    "restore_state_json",
]
