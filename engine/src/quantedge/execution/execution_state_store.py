"""
Durable execution state for the canonical execution path.

`MarketScannerOrchestrator -> TradeLifecycleManager` is the canonical
production-facing execution implementation (see the QUARANTINE note in
`quantedge/execution/__init__.py`), and every safety property it enforces
lives in process memory: which setup is live, which client_order_ids were
already sent, which bracket generation is resting, which fills were already
counted, and which reconciliation alerts are blocking new entries. A restart
loses all of it, and a lost lock or a lost order id is how a real position
becomes untracked.

This module is the durable side of that state, and nothing else. It does not
place, cancel or modify anything, it does not talk to the exchange, and it
does not compose the execution stack.

Discipline copied verbatim in spirit from the structure snapshot
(`ManualSMCRuntime.save_state` + `restart_recovery.persist_state`), because
that discipline is already proven in this repository:

  * atomic write -- serialize to `<target>.tmp`, then `Path.replace` it over
    the target, so a reader never observes a half-written file;
  * read-back -- the file is re-read and compared against the body that was
    meant to be written, and a mismatch raises rather than returning;
  * deterministic serialization -- `json.dumps(..., indent=2, sort_keys=True)`,
    so the same state always produces byte-identical output and an
    idempotency check can be a content comparison;
  * fail closed -- an unreadable file, a non-dict root, a key set that is not
    exactly the expected one, a foreign schema or an unsupported version are
    all refusals. Execution state that cannot be trusted is never partially
    accepted, and nothing is repaired or defaulted.

WHY NOT `AlgoConfigurationSnapshot.to_dict()`: it is lossy. It omits
`take_profit_target_pct` and `max_loss_pct`, and `from_dict` silently
re-defaults them, so a round trip through it would rewrite the risk
parameters a live trade was admitted under. This module therefore carries its
own explicit field tables, and each table is asserted at import time to equal
`{f.name for f in dataclasses.fields(...)}` -- so a field added to
`TradeLifecycleRecord` or `AlgoConfigurationSnapshot` breaks the import
instead of being silently dropped from persisted state.
"""

import dataclasses
import itertools
import json
import logging
import os
import threading
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from quantedge.execution.algo_config import AlgoConfigurationSnapshot
from quantedge.execution.single_trade_lock import AccountTradeLockState
from quantedge.execution.trade_lifecycle import (
    MAX_PROTECTION_GENERATION,
    PROTECTION_SOURCES,
    CloseReason,
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
)
from quantedge.strategy.models import TradeDirection

logger = logging.getLogger("execution_state_store")


#: Identifies files written by THIS state writer. A file carrying any other
#: value is refused rather than interpreted -- the same guard
#: `restart_recovery` applies with its own `wiring_schema`.
EXECUTION_STATE_SCHEMA = "quantedge.execution_state"

#: Bumped when the on-disk body changes shape, or when the MEANING of a
#: persisted value changes. A file carrying a version this build does not
#: implement is refused, never best-effort parsed.
#:
#: Version 2: `generate_deterministic_client_order_id` no longer derives an order
#: name from truncated account/setup prefixes (it hashes the complete
#: `(account_id, setup_id, role)` triple). A version 1 body therefore carries
#: `entry_client_order_id` / `sl_client_order_id` / `tp_client_order_id` strings
#: minted under a scheme whose names were not unique per setup, and a restart
#: cannot tell whether such a name addresses this record's order or another
#: setup's. The stored strings are still what the exchange was told, so they
#: could not simply be recomputed either. Rather than reinterpret an ambiguous
#: name as a current identity -- or invent a migration for state whose real
#: exchange counterpart is unknown -- a version 1 file is refused outright with
#: `ExecutionStateIncompatibleError`, which the startup gate treats as a hard
#: block requiring operator resolution. There is no in-place upgrade path and
#: none is guessed.
#:
#: Version 3: the body gained three things a version 2 body cannot supply, all
#: of them safety-relevant identity rather than decoration.
#:
#:   * `active_trades[*].product_id` (P2-1) -- the exchange product a record's
#:     orders were actually submitted against. Version 2 re-derived it from the
#:     product registry on every restore, so a registry edit could repoint a
#:     persisted record at a different instrument and every subsequent cancel or
#:     bracket would act on the wrong one.
#:   * `active_trades[*].protection_source` (P2-2) -- whether resting protection
#:     was placed by this engine or adopted from an exchange attached bracket.
#:     Version 2 remembered that protection existed but not what kind, and the
#:     two are handled differently.
#:   * `account_identity` (P2-4) -- the account the state belongs to. Version 2
#:     bodies are account-anonymous, so a file could be restored into a process
#:     configured for a different account and reconciled against the wrong
#:     exchange inventory.
#:
#: A version 2 body cannot be upgraded: the missing values are facts about what
#: was sent to an exchange, and inventing them is exactly what a migration would
#: have to do. So it is refused with `ExecutionStateIncompatibleError`, the same
#: way version 1 is.
EXECUTION_STATE_SCHEMA_VERSION = 3


class ExecutionStateError(RuntimeError):
    """Base: persisted execution state cannot be trusted, so it is refused."""


class ExecutionStatePersistenceError(ExecutionStateError):
    """A write could not be proved durable, or a value could not be encoded."""


class ExecutionStateIncompatibleError(ExecutionStateError):
    """The state on disk was not written by this schema/version."""


class ExecutionStateCorruptError(ExecutionStateError):
    """The state on disk is unreadable, malformed, incomplete or unknown."""

#: The exact top-level key set of a persisted body. A loaded body whose keys
#: are not EXACTLY these is refused, which catches a missing key (partial
#: state) and an unknown key (state this build would silently discard) with
#: one comparison.
STATE_KEYS = (
    "execution_state_schema",
    "execution_state_schema_version",
    "account_identity",
    "active_trades",
    "trade_history",
    "reconciliation_alerts",
    "realized_daily_losses",
    "observed_fill_fees",
    "processed_fill_trade_ids",
    "entry_fill_sizes",
    "exit_fill_sizes",
    "exit_fill_notional",
    "exit_fill_roles",
    "single_trade_lock",
)


# ── Free-form value codec (tagged, fail-closed) ───────────────────────────────
#
# `TradeLifecycleRecord.history[*]["metadata"]` and the
# `_reconciliation_alerts` dicts are free-form by design, so they cannot get a
# per-field table. They get a TAGGED codec instead: a type JSON cannot express
# is wrapped in a single-key marker dict and restored to its original type, and
# a type this codec cannot round-trip EXACTLY raises. Nothing is coerced to a
# nearby type -- a tuple is not silently returned as a list, and an arbitrary
# object is not silently returned as its `repr`.

_TAG_DECIMAL = "__decimal__"
_TAG_DATETIME = "__datetime__"
_TAG_DATE = "__date__"
_TAGS = (_TAG_DECIMAL, _TAG_DATETIME, _TAG_DATE)


def _encode_freeform(value: Any, *, where: str) -> Any:
    """Encode a free-form value, raising on anything not exactly restorable."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return {_TAG_DECIMAL: str(value)}
    if isinstance(value, datetime):
        return {_TAG_DATETIME: value.isoformat()}
    if isinstance(value, date):
        return {_TAG_DATE: value.isoformat()}
    if isinstance(value, dict):
        encoded: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExecutionStatePersistenceError(
                    f"{where}: non-string mapping key {key!r} cannot be persisted"
                )
            if key in _TAGS:
                raise ExecutionStatePersistenceError(
                    f"{where}: key {key!r} collides with a codec tag"
                )
            encoded[key] = _encode_freeform(item, where=f"{where}.{key}")
        return encoded
    if isinstance(value, list):
        return [
            _encode_freeform(item, where=f"{where}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ExecutionStatePersistenceError(
        f"{where}: value of type {type(value).__name__!r} has no exact "
        f"round-trip encoding; refusing to persist a lossy substitute"
    )

def _decode_freeform(value: Any, *, where: str) -> Any:
    """Restore a value produced by `_encode_freeform`."""
    if isinstance(value, dict):
        if len(value) == 1:
            (only_key,) = value
            if only_key in _TAGS:
                raw = value[only_key]
                if not isinstance(raw, str):
                    raise ExecutionStateCorruptError(
                        f"{where}: tag {only_key!r} carries {type(raw).__name__!r}, "
                        f"expected a string"
                    )
                try:
                    if only_key == _TAG_DECIMAL:
                        return Decimal(raw)
                    if only_key == _TAG_DATETIME:
                        return datetime.fromisoformat(raw)
                    return date.fromisoformat(raw)
                except (InvalidOperation, ValueError) as exc:
                    raise ExecutionStateCorruptError(
                        f"{where}: tag {only_key!r} carries unparsable {raw!r}"
                    ) from exc
        return {
            key: _decode_freeform(item, where=f"{where}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _decode_freeform(item, where=f"{where}[{index}]")
            for index, item in enumerate(value)
        ]
    return value


# ── Typed field codec ─────────────────────────────────────────────────────────
#
# Every persisted dataclass field is listed with an explicit kind, and the
# tables below are asserted against `dataclasses.fields(...)` at import time.
# A trailing "?" means the field may legitimately be `None`; without it, a
# `None` is a refusal, because a required execution fact going missing is the
# exact condition this module exists to catch.

_K_STR = "str"
_K_STR_OPT = "str?"
_K_DEC = "dec"
_K_DEC_OPT = "dec?"
_K_INT = "int"
_K_INT_OPT = "int?"
_K_BOOL = "bool"
_K_DT = "dt"
_K_DT_OPT = "dt?"
_K_DIRECTION = "direction"
_K_STATE = "state"
_K_CLOSE_REASON = "close_reason?"
_K_SNAPSHOT = "snapshot?"
_K_HISTORY = "history"

def _encode_field(kind: str, value: Any, *, where: str) -> Any:
    """Encode one typed field, refusing anything the kind does not allow."""
    if value is None:
        if kind.endswith("?"):
            return None
        raise ExecutionStatePersistenceError(
            f"{where}: required field is None; refusing to persist a substitute"
        )
    if kind in (_K_STR, _K_STR_OPT):
        if not isinstance(value, str):
            raise ExecutionStatePersistenceError(
                f"{where}: expected str, got {type(value).__name__!r}"
            )
        return value
    if kind in (_K_DEC, _K_DEC_OPT):
        # A numeric field is persisted as its exact decimal STRING, never as a
        # JSON number: a float round trip would silently perturb a price or a
        # quantity. `str()` is exact for Decimal and repr-exact for int/float,
        # and the value is restored as the field's declared `Decimal`.
        if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
            raise ExecutionStatePersistenceError(
                f"{where}: expected a numeric value, got {type(value).__name__!r}"
            )
        try:
            return str(Decimal(str(value)))
        except (InvalidOperation, ValueError) as exc:
            raise ExecutionStatePersistenceError(
                f"{where}: {value!r} is not an exact decimal"
            ) from exc
    if kind in (_K_INT, _K_INT_OPT):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExecutionStatePersistenceError(
                f"{where}: expected int, got {type(value).__name__!r}"
            )
        return value
    if kind == _K_BOOL:
        if not isinstance(value, bool):
            raise ExecutionStatePersistenceError(
                f"{where}: expected bool, got {type(value).__name__!r}"
            )
        return value
    if kind in (_K_DT, _K_DT_OPT):
        if not isinstance(value, datetime):
            raise ExecutionStatePersistenceError(
                f"{where}: expected datetime, got {type(value).__name__!r}"
            )
        return value.isoformat()
    if kind == _K_DIRECTION:
        if not isinstance(value, TradeDirection):
            raise ExecutionStatePersistenceError(
                f"{where}: expected TradeDirection, got {type(value).__name__!r}"
            )
        return value.value

    if kind == _K_STATE:
        if not isinstance(value, TradeLifecycleState):
            raise ExecutionStatePersistenceError(
                f"{where}: expected TradeLifecycleState, got {type(value).__name__!r}"
            )
        return value.value
    if kind == _K_CLOSE_REASON:
        if not isinstance(value, CloseReason):
            raise ExecutionStatePersistenceError(
                f"{where}: expected CloseReason, got {type(value).__name__!r}"
            )
        return value.value
    if kind == _K_SNAPSHOT:
        return _encode_snapshot(value, where=where)
    if kind == _K_HISTORY:
        if not isinstance(value, list):
            raise ExecutionStatePersistenceError(
                f"{where}: expected list, got {type(value).__name__!r}"
            )
        return _encode_freeform(value, where=where)
    raise ExecutionStatePersistenceError(f"{where}: unknown field kind {kind!r}")


def _decode_field(kind: str, value: Any, *, where: str) -> Any:
    """Restore one typed field, refusing anything the kind does not allow."""
    if value is None:
        if kind.endswith("?"):
            return None
        raise ExecutionStateCorruptError(
            f"{where}: required field is null in persisted state"
        )
    if kind in (_K_STR, _K_STR_OPT):
        if not isinstance(value, str):
            raise ExecutionStateCorruptError(
                f"{where}: expected str, got {type(value).__name__!r}"
            )
        return value
    if kind in (_K_DEC, _K_DEC_OPT):
        if not isinstance(value, str):
            raise ExecutionStateCorruptError(
                f"{where}: expected a decimal string, got {type(value).__name__!r}"
            )
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ExecutionStateCorruptError(
                f"{where}: {value!r} is not a decimal"
            ) from exc
    if kind in (_K_INT, _K_INT_OPT):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExecutionStateCorruptError(
                f"{where}: expected int, got {type(value).__name__!r}"
            )
        return value

    if kind == _K_BOOL:
        if not isinstance(value, bool):
            raise ExecutionStateCorruptError(
                f"{where}: expected bool, got {type(value).__name__!r}"
            )
        return value
    if kind in (_K_DT, _K_DT_OPT):
        if not isinstance(value, str):
            raise ExecutionStateCorruptError(
                f"{where}: expected an ISO timestamp, got {type(value).__name__!r}"
            )
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ExecutionStateCorruptError(
                f"{where}: {value!r} is not an ISO timestamp"
            ) from exc
    if kind in (_K_DIRECTION, _K_STATE, _K_CLOSE_REASON):
        enum_cls = {
            _K_DIRECTION: TradeDirection,
            _K_STATE: TradeLifecycleState,
            _K_CLOSE_REASON: CloseReason,
        }[kind]
        if not isinstance(value, str):
            raise ExecutionStateCorruptError(
                f"{where}: expected {enum_cls.__name__} value as str, "
                f"got {type(value).__name__!r}"
            )
        try:
            return enum_cls(value)
        except ValueError as exc:
            # An unrecognised state/reason is NOT mapped to a nearby member:
            # a state this build does not know about is ambiguous, and an
            # ambiguous lifecycle state must block, not be guessed.
            raise ExecutionStateCorruptError(
                f"{where}: {value!r} is not a known {enum_cls.__name__}"
            ) from exc
    if kind == _K_SNAPSHOT:
        return _decode_snapshot(value, where=where)
    if kind == _K_HISTORY:
        if not isinstance(value, list):
            raise ExecutionStateCorruptError(
                f"{where}: expected list, got {type(value).__name__!r}"
            )
        return _decode_freeform(value, where=where)
    raise ExecutionStateCorruptError(f"{where}: unknown field kind {kind!r}")

# ── `AlgoConfigurationSnapshot` field table ────────────────────────────────────

_SNAPSHOT_FIELDS: Dict[str, str] = {
    "setup_id": _K_STR_OPT,
    "account_id": _K_STR,
    "user_id": _K_STR_OPT,
    "version": _K_INT,
    "take_profit_pct": _K_DEC,
    "stop_loss_pct": _K_DEC,
    "risk_per_trade_pct": _K_DEC,
    "max_risk_usd": _K_DEC_OPT,
    "max_daily_loss_usd": _K_DEC,
    "max_leverage": _K_INT,
    "algo_enabled_at_snapshot": _K_BOOL,
    "kill_switch_active_at_snapshot": _K_BOOL,
    "take_profit_target_pct": _K_DEC,
    "max_loss_pct": _K_DEC,
    "snapshot_timestamp": _K_DT,
}

#: A field added to the snapshot dataclass must be added here too. Asserting
#: at IMPORT time is what makes "no silently dropped execution state" a
#: property of the build rather than a promise: the engine will not start with
#: an out-of-date table.
_declared_snapshot_fields = {f.name for f in dataclasses.fields(AlgoConfigurationSnapshot)}
assert set(_SNAPSHOT_FIELDS) == _declared_snapshot_fields, (
    "execution_state_store._SNAPSHOT_FIELDS is out of date with "
    f"AlgoConfigurationSnapshot: missing={sorted(_declared_snapshot_fields - set(_SNAPSHOT_FIELDS))} "
    f"unknown={sorted(set(_SNAPSHOT_FIELDS) - _declared_snapshot_fields)}"
)


def _encode_snapshot(snapshot: Any, *, where: str) -> Optional[Dict[str, Any]]:
    """Encode an `AlgoConfigurationSnapshot` field-by-field (never `to_dict`)."""
    if snapshot is None:
        return None
    if not isinstance(snapshot, AlgoConfigurationSnapshot):
        raise ExecutionStatePersistenceError(
            f"{where}: expected AlgoConfigurationSnapshot, "
            f"got {type(snapshot).__name__!r}"
        )
    return {
        name: _encode_field(kind, getattr(snapshot, name), where=f"{where}.{name}")
        for name, kind in _SNAPSHOT_FIELDS.items()
    }

def _decode_snapshot(payload: Any, *, where: str) -> Optional[AlgoConfigurationSnapshot]:
    """Restore an `AlgoConfigurationSnapshot`, refusing an inexact payload."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ExecutionStateCorruptError(
            f"{where}: expected an object, got {type(payload).__name__!r}"
        )
    _require_exact_keys(payload, _SNAPSHOT_FIELDS, where=where)
    kwargs = {
        name: _decode_field(kind, payload[name], where=f"{where}.{name}")
        for name, kind in _SNAPSHOT_FIELDS.items()
    }
    try:
        return AlgoConfigurationSnapshot(**kwargs)
    except Exception as exc:
        # `__post_init__` band-checks `max_leverage`, so a tampered or legacy
        # export fails closed here. Re-raised as a state error so every
        # refusal from this module has one type.
        raise ExecutionStateCorruptError(
            f"{where}: persisted snapshot rejected by AlgoConfigurationSnapshot: {exc}"
        ) from exc


def _require_exact_keys(payload: Dict[str, Any], expected: Any, *, where: str) -> None:
    """Refuse a payload whose key set is not EXACTLY `expected`.

    Missing keys mean partial state; unknown keys mean state this build would
    silently discard. Both are refusals, and the message names them.
    """
    seen = set(payload)
    wanted = set(expected)
    if seen == wanted:
        return
    raise ExecutionStateCorruptError(
        f"{where}: key set mismatch -- missing={sorted(wanted - seen)} "
        f"unknown={sorted(seen - wanted)}"
    )


# ── `TradeLifecycleRecord` field table ─────────────────────────────────────────

_RECORD_FIELDS: Dict[str, str] = {
    "setup_id": _K_STR,
    "account_id": _K_STR,
    "user_id": _K_STR_OPT,
    "symbol": _K_STR,
    "direction": _K_DIRECTION,
    "requested_quantity": _K_DEC,
    "entry_price": _K_DEC,
    "stop_loss_price": _K_DEC,
    "take_profit_price": _K_DEC,
    "risk_reward_ratio": _K_DEC,
    "risk_amount": _K_DEC,
    "reward_amount": _K_DEC,
    "entry_order_id": _K_STR_OPT,
    "entry_client_order_id": _K_STR_OPT,
    #: P2-1. The exchange product this record's orders were actually submitted
    #: against, persisted as part of the execution identity instead of being
    #: re-derived from the product registry on every restore. `Optional` in the
    #: dataclass, but NOT optional on disk for a record that has reached the
    #: exchange -- see `_composite_record_inconsistency`.
    "product_id": _K_INT_OPT,
    "filled_quantity": _K_DEC,
    "average_fill_price": _K_DEC_OPT,
    "sl_order_id": _K_STR_OPT,
    "sl_client_order_id": _K_STR_OPT,
    "tp_order_id": _K_STR_OPT,
    "tp_client_order_id": _K_STR_OPT,
    "protected_quantity": _K_DEC,
    "protection_generation": _K_INT,
    #: P2-2. Whether the resting protection was placed by this engine or adopted
    #: from an exchange-side attached bracket. Without it a restored record knows
    #: that protection exists but not what kind of contract it is, and the two
    #: are handled differently -- so the safe-looking guess is the one that
    #: places a second bracket on top of a live one.
    "protection_source": _K_STR_OPT,

    "state": _K_STATE,
    "close_reason": _K_CLOSE_REASON,
    "realized_pnl": _K_DEC_OPT,
    "gross_pnl": _K_DEC_OPT,
    "trading_fees": _K_DEC_OPT,
    "trading_fees_source": _K_STR_OPT,
    "funding_costs": _K_DEC,
    "net_pnl": _K_DEC_OPT,
    "net_pnl_is_cost_complete": _K_BOOL,
    "gross_pnl_source": _K_STR_OPT,
    "pre_trade_balance": _K_DEC,
    "post_trade_balance": _K_DEC_OPT,
    "daily_loss_at_entry": _K_DEC,
    "rejection_code": _K_STR_OPT,
    "error_message": _K_STR_OPT,
    "config_version": _K_INT_OPT,
    "config_snapshot": _K_SNAPSHOT,
    "strategy_name": _K_STR,
    "strategy_version": _K_STR,
    "history": _K_HISTORY,
    "created_at": _K_DT,
    "updated_at": _K_DT,
    "closed_at": _K_DT_OPT,
}

_declared_record_fields = {f.name for f in dataclasses.fields(TradeLifecycleRecord)}
assert set(_RECORD_FIELDS) == _declared_record_fields, (
    "execution_state_store._RECORD_FIELDS is out of date with "
    f"TradeLifecycleRecord: missing={sorted(_declared_record_fields - set(_RECORD_FIELDS))} "
    f"unknown={sorted(set(_RECORD_FIELDS) - _declared_record_fields)}"
)


# ── P1-7: composite consistency at the persistence boundaries ─────────────────
#
# `record_transition` is deliberately still permissive: it records whatever
# transition it is given, and the audit says so. A strict transition whitelist
# was NOT invented here, because the legitimate transition set cannot be proved
# from this repository -- `ENTRY_REJECTED` records stay in `active_trades` with
# their portfolio lock released, `POSITION_CLOSED` records keep their bracket
# order ids, and a protection retry after `PROTECTION_FAILED` re-enters an
# earlier state on purpose. A guessed whitelist would refuse real behaviour.
#
# What CAN be proved is that certain COMBINATIONS of fields cannot be produced
# by any path in `trade_lifecycle.py`, and each of them describes a record that
# would make an unsafe decision if it were restored:
#
#   * protection recorded with no order id to cancel or resize -- a resize would
#     place a second protective pair on top of a live one (§M2 duplicate
#     protection);
#   * protection recorded for a position with no fill at all -- protection of
#     nothing, which hides an unprotected real position behind a "protected"
#     number;
#   * a protection source outside the closed P2-2 vocabulary, or a generation
#     outside the range `protection_role()` can name -- either one makes the
#     next protective client_order_id unnameable;
#   * an order id with no persisted product id (P2-1) -- the record reached the
#     exchange, so the product it reached is a fact, and re-deriving it from the
#     current registry is exactly the silent rebinding P2-1 forbids;
#   * `POSITION_CLOSED` without `closed_at`, which `record_transition` sets on
#     that transition unconditionally.
#
# Both boundaries are checked: the encode side so a corrupt in-memory record is
# never written, and the decode side so a hand-edited or truncated file is never
# restored. Neither side repairs anything.


def _as_decimal_or_none(value: Any) -> Optional[Decimal]:
    """`value` as a Decimal, or None when it is not a comparable number.

    A value this cannot compare is left to the field codec, which refuses it with
    a precise message -- the consistency check never guesses at a type.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None


def _composite_record_inconsistency(record: TradeLifecycleRecord) -> Optional[str]:
    """Why this record's fields contradict each other, or None if they do not."""
    filled = _as_decimal_or_none(record.filled_quantity)
    protected = _as_decimal_or_none(record.protected_quantity)
    has_bracket_id = bool(record.sl_order_id) or bool(record.tp_order_id)

    if filled is not None and filled < 0:
        return f"filled_quantity is negative ({record.filled_quantity})"
    if protected is not None and protected < 0:
        return f"protected_quantity is negative ({record.protected_quantity})"
    if protected is not None and protected > 0 and not has_bracket_id:
        return (
            f"protected_quantity is {record.protected_quantity} but neither "
            f"sl_order_id nor tp_order_id is set, so the record claims protection "
            f"it cannot cancel, resize or verify"
        )
    if (
        protected is not None
        and protected > 0
        and filled is not None
        and filled == 0
    ):
        return (
            f"protected_quantity is {record.protected_quantity} while "
            f"filled_quantity is 0, so the record claims protection for a "
            f"position it does not account for"
        )
    if record.protection_source is not None and record.protection_source not in PROTECTION_SOURCES:
        return (
            f"protection_source is {record.protection_source!r}, which is not one "
            f"of {list(PROTECTION_SOURCES)}"
        )
    if isinstance(record.protection_generation, bool) or not isinstance(
        record.protection_generation, int
    ):
        return (
            f"protection_generation is {record.protection_generation!r}, which is "
            f"not an int"
        )
    if not (0 <= record.protection_generation <= MAX_PROTECTION_GENERATION):
        return (
            f"protection_generation is {record.protection_generation}, outside the "
            f"nameable range 0..{MAX_PROTECTION_GENERATION}"
        )
    if record.product_id is not None and (
        isinstance(record.product_id, bool)
        or not isinstance(record.product_id, int)
        or record.product_id <= 0
    ):
        return f"product_id is {record.product_id!r}, which is not a positive int"
    if record.product_id is None and (record.entry_order_id or has_bracket_id):
        return (
            "the record carries exchange order id(s) "
            f"(entry={record.entry_order_id!r} sl={record.sl_order_id!r} "
            f"tp={record.tp_order_id!r}) but no product_id, so the product those "
            "orders live on could only be re-derived from the current registry"
        )
    if record.state == TradeLifecycleState.POSITION_CLOSED and record.closed_at is None:
        return "state is POSITION_CLOSED but closed_at is not set"
    return None


def _encode_record(record: TradeLifecycleRecord, *, where: str) -> Dict[str, Any]:
    """Encode a `TradeLifecycleRecord` field-by-field."""
    if not isinstance(record, TradeLifecycleRecord):
        raise ExecutionStatePersistenceError(
            f"{where}: expected TradeLifecycleRecord, got {type(record).__name__!r}"
        )
    inconsistency = _composite_record_inconsistency(record)
    if inconsistency is not None:
        raise ExecutionStatePersistenceError(
            f"{where}: {inconsistency}; refusing to persist internally "
            f"inconsistent execution state"
        )
    return {
        name: _encode_field(kind, getattr(record, name), where=f"{where}.{name}")
        for name, kind in _RECORD_FIELDS.items()
    }


def _decode_record(payload: Any, *, where: str) -> TradeLifecycleRecord:
    """Restore a `TradeLifecycleRecord`, refusing an inexact payload."""
    if not isinstance(payload, dict):
        raise ExecutionStateCorruptError(
            f"{where}: expected an object, got {type(payload).__name__!r}"
        )
    _require_exact_keys(payload, _RECORD_FIELDS, where=where)
    record = TradeLifecycleRecord(**{
        name: _decode_field(kind, payload[name], where=f"{where}.{name}")
        for name, kind in _RECORD_FIELDS.items()
    })
    inconsistency = _composite_record_inconsistency(record)
    if inconsistency is not None:
        raise ExecutionStateCorruptError(
            f"{where}: {inconsistency}; persisted execution state is internally "
            f"inconsistent and is refused rather than repaired"
        )
    return record

# ── Collection codecs ─────────────────────────────────────────────────────────


def _encode_decimal_map(
    values: Any, *, where: str, optional_values: bool = False
) -> Dict[str, Optional[str]]:
    """Encode a `Dict[str, Decimal]` (or `Dict[str, Optional[Decimal]]`)."""
    if not isinstance(values, dict):
        raise ExecutionStatePersistenceError(
            f"{where}: expected dict, got {type(values).__name__!r}"
        )
    out: Dict[str, Optional[str]] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise ExecutionStatePersistenceError(
                f"{where}: non-string key {key!r}"
            )
        # `_observed_fill_fees` distinguishes THREE states -- key absent, value
        # None (an observed execution carried no commission, so the total is
        # unknowable and sticky), and a Decimal. JSON null carries the middle
        # state through unchanged; it must never be flattened to zero.
        kind = _K_DEC_OPT if optional_values else _K_DEC
        out[key] = _encode_field(kind, value, where=f"{where}[{key!r}]")
    return out


def _decode_decimal_map(
    payload: Any, *, where: str, optional_values: bool = False
) -> Dict[str, Optional[Decimal]]:
    """Restore a map encoded by `_encode_decimal_map`."""
    if not isinstance(payload, dict):
        raise ExecutionStateCorruptError(
            f"{where}: expected an object, got {type(payload).__name__!r}"
        )
    kind = _K_DEC_OPT if optional_values else _K_DEC
    return {
        key: _decode_field(kind, value, where=f"{where}[{key!r}]")
        for key, value in payload.items()
    }


def _encode_string_set(values: Any, *, where: str) -> List[str]:
    """Encode a `Set[str]` as a SORTED list, so the output is deterministic."""
    if not isinstance(values, (set, frozenset)):
        raise ExecutionStatePersistenceError(
            f"{where}: expected set, got {type(values).__name__!r}"
        )
    for item in values:
        if not isinstance(item, str):
            raise ExecutionStatePersistenceError(
                f"{where}: non-string member {item!r}"
            )
    return sorted(values)

def _decode_string_set(payload: Any, *, where: str) -> Set[str]:
    """Restore a set encoded by `_encode_string_set`."""
    if not isinstance(payload, list):
        raise ExecutionStateCorruptError(
            f"{where}: expected a list, got {type(payload).__name__!r}"
        )
    for item in payload:
        if not isinstance(item, str):
            raise ExecutionStateCorruptError(
                f"{where}: non-string member {item!r}"
            )
    return set(payload)


def _encode_daily_losses(values: Any, *, where: str) -> Dict[str, str]:
    """Encode `Dict[date, Decimal]` with ISO date keys."""
    if not isinstance(values, dict):
        raise ExecutionStatePersistenceError(
            f"{where}: expected dict, got {type(values).__name__!r}"
        )
    out: Dict[str, str] = {}
    for key, value in values.items():
        if isinstance(key, datetime) or not isinstance(key, date):
            raise ExecutionStatePersistenceError(
                f"{where}: expected date key, got {type(key).__name__!r}"
            )
        out[key.isoformat()] = _encode_field(
            _K_DEC, value, where=f"{where}[{key.isoformat()}]"
        )
    return out


def _decode_daily_losses(payload: Any, *, where: str) -> Dict[date, Decimal]:
    """Restore a map encoded by `_encode_daily_losses`."""
    if not isinstance(payload, dict):
        raise ExecutionStateCorruptError(
            f"{where}: expected an object, got {type(payload).__name__!r}"
        )
    out: Dict[date, Decimal] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ExecutionStateCorruptError(f"{where}: non-string key {key!r}")
        try:
            day = date.fromisoformat(key)
        except ValueError as exc:
            raise ExecutionStateCorruptError(
                f"{where}: {key!r} is not an ISO date"
            ) from exc
        out[day] = _decode_field(_K_DEC, value, where=f"{where}[{key}]")
    return out

# ── Single-trade lock ─────────────────────────────────────────────────────────
#
# The lock is persisted through its OWN `export_state()` / `load_state()`, as
# mandated -- this module does not reimplement the lock's serialization. What
# it DOES add is a shape check on the way back in, because
# `AccountTradeLockState.from_dict` reads four of its six fields with `.get()`:
# a truncated entry would load as `is_locked=False`, which is the unsafe
# direction (a released lock permits a second concurrent trade). An entry that
# is not exactly the six fields `to_dict()` writes is therefore refused.

_LOCK_ENTRY_FIELDS: Dict[str, str] = {
    "account_id": _K_STR,
    "user_id": _K_STR,
    "is_locked": _K_BOOL,
    "active_setup_id": _K_STR_OPT,
    "active_symbol": _K_STR_OPT,
    "acquired_at": _K_DT_OPT,
}

_declared_lock_fields = {f.name for f in dataclasses.fields(AccountTradeLockState)}
assert set(_LOCK_ENTRY_FIELDS) == _declared_lock_fields, (
    "execution_state_store._LOCK_ENTRY_FIELDS is out of date with "
    f"AccountTradeLockState: missing={sorted(_declared_lock_fields - set(_LOCK_ENTRY_FIELDS))} "
    f"unknown={sorted(set(_LOCK_ENTRY_FIELDS) - _declared_lock_fields)}"
)


def _validate_lock_state(payload: Any, *, where: str) -> Dict[str, Any]:
    """Check an exported lock map entry-by-entry and return it unchanged."""
    if not isinstance(payload, dict):
        raise ExecutionStateCorruptError(
            f"{where}: expected an object, got {type(payload).__name__!r}"
        )
    for key, entry in payload.items():
        if not isinstance(key, str):
            raise ExecutionStateCorruptError(f"{where}: non-string key {key!r}")
        location = f"{where}[{key!r}]"
        if not isinstance(entry, dict):
            raise ExecutionStateCorruptError(
                f"{location}: expected an object, got {type(entry).__name__!r}"
            )
        _require_exact_keys(entry, _LOCK_ENTRY_FIELDS, where=location)
        for name, kind in _LOCK_ENTRY_FIELDS.items():
            # Decoding is used purely to VALIDATE: the payload is handed to
            # `load_state` exactly as written, so the lock owns its own parsing.
            _decode_field(kind, entry[name], where=f"{location}.{name}")
        expected_key = f"{entry['user_id']}:{entry['account_id']}"
        if key != expected_key:
            raise ExecutionStateCorruptError(
                f"{location}: composite key disagrees with its fields "
                f"(expected {expected_key!r}); persisted lock state is ambiguous"
            )
    return payload

# ── P2-4: account identity ────────────────────────────────────────────────────
#
# A version 2 body was account-anonymous. Nothing in it said which trading
# account the live trades, the fill accounting and the portfolio lock belonged
# to, so a file could be restored into a process configured for a DIFFERENT
# account -- and then reconciled against that other account's exchange
# inventory, where the restored setups have no positions and the real positions
# have no setups. Every verdict downstream would be drawn from the wrong
# account: orphan positions that are not orphans, protection judged missing on a
# position this process never opened.
#
# The identity is not invented here. It is read from the state store the manager
# was constructed with -- the same `state_store.account_id` that
# `reconcile_active_trades_with_exchange` already uses to name the account in its
# audit trail -- and it is compared, not repaired: a mismatch is refused.
#
# `account_record_id` is `state_store.account.account_id`, the id on the
# `AccountRecord` the sync service maintains. It is normally equal to
# `account_id`; both are persisted because either one changing means the process
# is pointed somewhere else than the file was written for.

_ACCOUNT_IDENTITY_FIELDS: Dict[str, str] = {
    "account_id": _K_STR,
    "account_record_id": _K_STR,
    "user_id": _K_STR_OPT,
}


def _authoritative_identifier(value: Any) -> Optional[str]:
    """`value` as a non-empty identifier string, or None if it is not one.

    Fail-closed by construction: `None`, a non-string, an empty string and a
    whitespace-only string all answer None, and the caller refuses. Nothing is
    substituted -- there is no "default_account" fallback here, because an
    account identity this module had to invent would prove nothing on restore.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def manager_account_identity(manager: TradeLifecycleManager) -> Dict[str, Any]:
    """The account identity `manager` is configured for, or raise.

    Read-only: touches no exchange, no credential and no file. `user_id` is
    genuinely optional (`AccountRecord.user_id` defaults to `None`), so it is
    persisted as `None` rather than as an invented value; the two account ids are
    mandatory, and their absence is a refusal.
    """
    store = getattr(manager, "state_store", None)
    if store is None:
        raise ExecutionStatePersistenceError(
            "account_identity: the manager has no state_store, so the account "
            "this execution state belongs to cannot be established"
        )
    account_id = _authoritative_identifier(getattr(store, "account_id", None))
    if account_id is None:
        raise ExecutionStatePersistenceError(
            "account_identity: state_store.account_id is not a non-empty string; "
            "execution state is not persisted without the account it belongs to"
        )
    account = getattr(store, "account", None)
    record_id = _authoritative_identifier(getattr(account, "account_id", None))
    if record_id is None:
        raise ExecutionStatePersistenceError(
            "account_identity: state_store.account.account_id is not a non-empty "
            "string; execution state is not persisted without the account it "
            "belongs to"
        )
    user_id = getattr(account, "user_id", None)
    if user_id is not None and _authoritative_identifier(user_id) is None:
        raise ExecutionStatePersistenceError(
            "account_identity: state_store.account.user_id is present but is not a "
            "non-empty string"
        )
    return {
        "account_id": account_id,
        "account_record_id": record_id,
        "user_id": user_id,
    }


def _validate_account_identity(payload: Any, *, where: str) -> Dict[str, Any]:
    """Check a persisted identity's shape and return it unchanged."""
    if not isinstance(payload, dict):
        raise ExecutionStateCorruptError(
            f"{where}: expected an object, got {type(payload).__name__!r}"
        )
    _require_exact_keys(payload, _ACCOUNT_IDENTITY_FIELDS, where=where)
    for name, kind in _ACCOUNT_IDENTITY_FIELDS.items():
        _decode_field(kind, payload[name], where=f"{where}.{name}")
    for name in ("account_id", "account_record_id"):
        if _authoritative_identifier(payload[name]) is None:
            raise ExecutionStateCorruptError(
                f"{where}.{name}: persisted account identity is empty, so the file "
                f"cannot prove which account it belongs to"
            )
    if payload["user_id"] is not None and _authoritative_identifier(payload["user_id"]) is None:
        raise ExecutionStateCorruptError(
            f"{where}.user_id: present but empty"
        )
    return payload


def assert_account_identity_matches(
    manager: TradeLifecycleManager, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Refuse to restore a body written for a different account.

    Called BEFORE anything is decoded or mutated, so a mismatched file leaves the
    manager exactly as it was. `ExecutionStateIncompatibleError` rather than
    `ExecutionStateCorruptError`: the file is not damaged, it simply is not this
    account's, and the two failures call for different operator actions.
    """
    persisted = _validate_account_identity(
        body.get("account_identity"), where="account_identity"
    )
    current = manager_account_identity(manager)
    differing = sorted(
        name for name in _ACCOUNT_IDENTITY_FIELDS if persisted[name] != current[name]
    )
    if differing:
        raise ExecutionStateIncompatibleError(
            "execution state was written for a different account -- "
            + ", ".join(
                f"{name}: file={persisted[name]!r} process={current[name]!r}"
                for name in differing
            )
            + ". Refusing to restore one account's live trades, fill accounting "
            "and portfolio lock into another account's process"
        )
    return persisted


# ── Manager attribute classification ──────────────────────────────────────────
#
# Every instance attribute `TradeLifecycleManager.__init__` creates is
# classified here as durable or not. `export_execution_state` asserts that the
# live object's `vars()` is EXACTLY this union, so a collection added to the
# manager later cannot be silently left out of persisted state: the export
# refuses instead, and the omission is found by a test run rather than by a
# restart that forgot a live trade.

_DURABLE_MANAGER_ATTRS = frozenset({
    "_active_trades",
    "_trade_history",
    "_realized_daily_losses",
    "_observed_fill_fees",
    "_processed_fill_trade_ids",
    "_entry_fill_sizes",
    "_exit_fill_sizes",
    "_exit_fill_notional",
    "_exit_fill_roles",
    "_reconciliation_alerts",
})

#: Deliberately NOT persisted, with the reason each one is safe to lose:
#: injected collaborators (`client`, `validation_gateway`, `state_store`,
#: `sync_service`, `algo_config_store`, `capital_allocator`) are rebuilt by
#: whatever composes the stack; `single_trade_lock` IS persisted, but through
#: its own `export_state()` rather than as a manager attribute; the configured
#: limits (`daily_loss_limit`, `max_stale_seconds`, `execution_mode`) come from
#: configuration, and reading them from a stale state file instead would let a
#: persisted file override current configuration; `_lock` is a
#: `threading.Lock`; `_private_stream` and `_closure_handler` are live wiring;
#: `_entry_admission_gate` is the startup reconciliation boundary itself, and
#: persisting it would be actively unsafe -- restoring a remembered "admitted"
#: verdict would let a restarted process skip the very reconciliation this
#: state file exists to force. A restart must re-arm the gate from scratch.
#: `_submissions_in_flight` is a live in-process counter of entry submissions
#: currently between admission and completion (P1-4): after a restart the answer
#: is necessarily zero, and persisting a non-zero value would either block a
#: fresh process forever or -- worse -- be restored as zero while the ambiguity
#: it represented is recorded in the records themselves.
_NON_DURABLE_MANAGER_ATTRS = frozenset({
    "client",
    "validation_gateway",
    "state_store",
    "sync_service",
    "algo_config_store",
    "single_trade_lock",
    "capital_allocator",
    "daily_loss_limit",
    "max_stale_seconds",
    "execution_mode",
    "_lock",
    "_private_stream",
    "_closure_handler",
    "_entry_admission_gate",
    "_submissions_in_flight",
})

_CLASSIFIED_MANAGER_ATTRS = _DURABLE_MANAGER_ATTRS | _NON_DURABLE_MANAGER_ATTRS

def assert_manager_attributes_classified(manager: TradeLifecycleManager) -> None:
    """Refuse to serialize a manager this module does not fully understand."""
    seen = set(vars(manager))
    unknown = seen - _CLASSIFIED_MANAGER_ATTRS
    missing = _CLASSIFIED_MANAGER_ATTRS - seen
    if unknown or missing:
        raise ExecutionStatePersistenceError(
            "TradeLifecycleManager attributes are not classified for "
            f"persistence: unclassified={sorted(unknown)} absent={sorted(missing)}. "
            "Classify each one in execution_state_store as durable or "
            "non-durable before persisting execution state."
        )


# ── Export / restore ──────────────────────────────────────────────────────────


def export_execution_state(manager: TradeLifecycleManager) -> Dict[str, Any]:
    """Serialize the manager's execution state (plus its lock) to a JSON body.

    Pure: nothing is written, nothing is mutated, and no exchange is contacted.
    Raises `ExecutionStatePersistenceError` rather than emitting a body that
    would not restore exactly.

    P2-3: the whole body is built while holding `manager._lock`, the same lock
    the entry path holds across admission and submission. Without it, an export
    that runs beside a live entry can read `_active_trades` after a record was
    inserted but before its order ids were recorded, or read `_entry_fill_sizes`
    from one fill and `_active_trades` from the next -- a body internally
    consistent with no moment in time. The lock is re-entrant precisely so that
    the admission path, which already holds it, can fingerprint its own state
    (P1-4/P1-3), and nothing here waits on the network while holding it.
    """
    assert_manager_attributes_classified(manager)
    account_identity = manager_account_identity(manager)

    with manager._lock:
        for attribute in ("_trade_history", "_reconciliation_alerts"):
            value = getattr(manager, attribute)
            if not isinstance(value, list):
                raise ExecutionStatePersistenceError(
                    f"{attribute}: expected list, got {type(value).__name__!r}"
                )
        for attribute in ("_active_trades", "_exit_fill_roles"):
            value = getattr(manager, attribute)
            if not isinstance(value, dict):
                raise ExecutionStatePersistenceError(
                    f"{attribute}: expected dict, got {type(value).__name__!r}"
                )

        active_trades: Dict[str, Any] = {}
        for setup_id, record in manager._active_trades.items():
            if not isinstance(setup_id, str):
                raise ExecutionStatePersistenceError(
                    f"active_trades: non-string setup id {setup_id!r}"
                )
            encoded = _encode_record(record, where=f"active_trades[{setup_id!r}]")
            if encoded["setup_id"] != setup_id:
                raise ExecutionStatePersistenceError(
                    f"active_trades[{setup_id!r}]: record carries setup_id "
                    f"{encoded['setup_id']!r}; refusing to persist a mismatched key"
                )
            active_trades[setup_id] = encoded

        body: Dict[str, Any] = {
            "execution_state_schema": EXECUTION_STATE_SCHEMA,
            "execution_state_schema_version": EXECUTION_STATE_SCHEMA_VERSION,
            "account_identity": account_identity,
            "active_trades": active_trades,
            "trade_history": [
                _encode_record(record, where=f"trade_history[{index}]")
                for index, record in enumerate(manager._trade_history)
            ],

            "reconciliation_alerts": _encode_freeform(
                manager._reconciliation_alerts, where="reconciliation_alerts"
            ),
            "realized_daily_losses": _encode_daily_losses(
                manager._realized_daily_losses, where="realized_daily_losses"
            ),
            "observed_fill_fees": _encode_decimal_map(
                manager._observed_fill_fees,
                where="observed_fill_fees",
                optional_values=True,
            ),
            "processed_fill_trade_ids": _encode_string_set(
                manager._processed_fill_trade_ids, where="processed_fill_trade_ids"
            ),
            "entry_fill_sizes": _encode_decimal_map(
                manager._entry_fill_sizes, where="entry_fill_sizes"
            ),
            "exit_fill_sizes": _encode_decimal_map(
                manager._exit_fill_sizes, where="exit_fill_sizes"
            ),
            "exit_fill_notional": _encode_decimal_map(
                manager._exit_fill_notional, where="exit_fill_notional"
            ),
            "exit_fill_roles": {
                setup_id: _encode_string_set(roles, where=f"exit_fill_roles[{setup_id!r}]")
                for setup_id, roles in manager._exit_fill_roles.items()
            },
            "single_trade_lock": manager.single_trade_lock.export_state(),
        }

    if sorted(body) != sorted(STATE_KEYS):
        raise ExecutionStatePersistenceError(
            f"exported body key set is wrong: missing={sorted(set(STATE_KEYS) - set(body))} "
            f"unknown={sorted(set(body) - set(STATE_KEYS))}"
        )
    # Proves the body is JSON-encodable BEFORE it reaches a file, and validates
    # the lock export by the same rules the loader will apply.
    _validate_lock_state(body["single_trade_lock"], where="single_trade_lock")
    return body

def restore_execution_state(manager: TradeLifecycleManager, body: Dict[str, Any]) -> None:
    """Restore a body produced by `export_execution_state` into `manager`.

    All-or-nothing: every collection is decoded into local variables FIRST, and
    the manager is only mutated once every one of them has been accepted. A
    body that fails halfway therefore leaves the manager exactly as it was,
    instead of half-restored -- "no partial state accepted" applies to the
    in-memory object as well as to the file.

    Restoring the same body twice is idempotent: each collection is REPLACED,
    not merged, and the lock's own `load_state` overwrites by
    `(user_id, account_id)`.

    P2-3, on why the single-trade lock is the one structure restored by merge:
    `SingleTradeLockManager.load_state` overwrites the entries the file names and
    KEEPS an in-memory entry the file does not mention. For every other
    collection, replacement is the safe direction -- a stale in-memory record that
    survived a restore is a trade this process would act on without authority.
    For the lock it is the opposite: dropping a held in-memory lock would permit a
    second concurrent trade, which is precisely what the lock exists to prevent
    (safety rules #11, #14). So the merge is deliberate, and the disagreement it
    can leave behind -- a lock held for a setup the restored state does not know
    -- is what `ExecutionStartupGate._lock_disagreement()` already blocks on
    before any entry is admitted.
    """
    assert_manager_attributes_classified(manager)
    validate_execution_state_body(body)
    # P2-4 FIRST, and before anything is decoded: a body written for another
    # account must not be partially interpreted, let alone applied.
    assert_account_identity_matches(manager, body)

    active_trades: Dict[str, TradeLifecycleRecord] = {}
    for setup_id, payload in body["active_trades"].items():
        if not isinstance(setup_id, str):
            raise ExecutionStateCorruptError(
                f"active_trades: non-string setup id {setup_id!r}"
            )
        record = _decode_record(payload, where=f"active_trades[{setup_id!r}]")
        if record.setup_id != setup_id:
            # The manager looks trades up by this key; a key that disagrees
            # with the record is ambiguous about which setup is live, and
            # ambiguity is refused rather than resolved.
            raise ExecutionStateCorruptError(
                f"active_trades[{setup_id!r}]: record carries setup_id "
                f"{record.setup_id!r}; persisted state is ambiguous"
            )
        active_trades[setup_id] = record

    history = body["trade_history"]
    if not isinstance(history, list):
        raise ExecutionStateCorruptError(
            f"trade_history: expected a list, got {type(history).__name__!r}"
        )
    trade_history = [
        _decode_record(payload, where=f"trade_history[{index}]")
        for index, payload in enumerate(history)
    ]

    raw_alerts = body["reconciliation_alerts"]
    if not isinstance(raw_alerts, list):
        raise ExecutionStateCorruptError(
            f"reconciliation_alerts: expected a list, got {type(raw_alerts).__name__!r}"
        )
    alerts = _decode_freeform(raw_alerts, where="reconciliation_alerts")

    realized_daily_losses = _decode_daily_losses(
        body["realized_daily_losses"], where="realized_daily_losses"
    )
    observed_fill_fees = _decode_decimal_map(
        body["observed_fill_fees"], where="observed_fill_fees", optional_values=True
    )
    processed_fill_trade_ids = _decode_string_set(
        body["processed_fill_trade_ids"], where="processed_fill_trade_ids"
    )
    entry_fill_sizes = _decode_decimal_map(
        body["entry_fill_sizes"], where="entry_fill_sizes"
    )
    exit_fill_sizes = _decode_decimal_map(
        body["exit_fill_sizes"], where="exit_fill_sizes"
    )
    exit_fill_notional = _decode_decimal_map(
        body["exit_fill_notional"], where="exit_fill_notional"
    )
    exit_fill_roles: Dict[str, Set[str]] = {}
    for setup_id, roles in body["exit_fill_roles"].items():
        if not isinstance(setup_id, str):
            raise ExecutionStateCorruptError(
                f"exit_fill_roles: non-string setup id {setup_id!r}"
            )
        exit_fill_roles[setup_id] = _decode_string_set(
            roles, where=f"exit_fill_roles[{setup_id!r}]"
        )
    lock_state = _validate_lock_state(body["single_trade_lock"], where="single_trade_lock")

    # ── Commit point: every collection above was accepted ─────────────────────
    with manager._lock:
        # Mutated IN PLACE rather than rebound, so any collaborator that
        # captured one of these collections still observes the restored state.
        manager._active_trades.clear()
        manager._active_trades.update(active_trades)
        manager._trade_history[:] = trade_history
        manager._reconciliation_alerts[:] = alerts
        manager._realized_daily_losses.clear()
        manager._realized_daily_losses.update(realized_daily_losses)
        manager._observed_fill_fees.clear()
        manager._observed_fill_fees.update(observed_fill_fees)
        manager._processed_fill_trade_ids.clear()
        manager._processed_fill_trade_ids.update(processed_fill_trade_ids)
        manager._entry_fill_sizes.clear()
        manager._entry_fill_sizes.update(entry_fill_sizes)
        manager._exit_fill_sizes.clear()
        manager._exit_fill_sizes.update(exit_fill_sizes)
        manager._exit_fill_notional.clear()
        manager._exit_fill_notional.update(exit_fill_notional)
        manager._exit_fill_roles.clear()
        manager._exit_fill_roles.update(exit_fill_roles)
    manager.single_trade_lock.load_state(lock_state)

# ── Body / schema validation ──────────────────────────────────────────────────

_OBJECT_KEYS = (
    "account_identity",
    "active_trades",
    "realized_daily_losses",
    "observed_fill_fees",
    "entry_fill_sizes",
    "exit_fill_sizes",
    "exit_fill_notional",
    "exit_fill_roles",
    "single_trade_lock",
)
_ARRAY_KEYS = ("trade_history", "reconciliation_alerts", "processed_fill_trade_ids")


def validate_execution_state_body(body: Any) -> Dict[str, Any]:
    """Refuse a body that is not exactly this schema at this version.

    Order matters: the SCHEMA is checked before the key set, so a file written
    by some other writer is reported as incompatible rather than as corrupt.
    """
    if not isinstance(body, dict):
        raise ExecutionStateCorruptError(
            f"execution state root must be an object, got {type(body).__name__!r}"
        )

    schema = body.get("execution_state_schema")
    if schema != EXECUTION_STATE_SCHEMA:
        raise ExecutionStateIncompatibleError(
            f"execution state schema is {schema!r}, expected "
            f"{EXECUTION_STATE_SCHEMA!r}; refusing to interpret a foreign file"
        )

    version = body.get("execution_state_schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ExecutionStateIncompatibleError(
            f"execution state schema version is {version!r}, expected an int"
        )
    if version != EXECUTION_STATE_SCHEMA_VERSION:
        # Never best-effort parsed: a future version may carry execution facts
        # this build cannot see, and acting on a partial view of a live trade
        # is exactly what this module exists to prevent.
        raise ExecutionStateIncompatibleError(
            f"execution state schema version {version} is not supported by this "
            f"build (expects {EXECUTION_STATE_SCHEMA_VERSION})"
        )

    if sorted(body) != sorted(STATE_KEYS):
        raise ExecutionStateCorruptError(
            f"execution state key set mismatch -- "
            f"missing={sorted(set(STATE_KEYS) - set(body))} "
            f"unknown={sorted(set(body) - set(STATE_KEYS))}"
        )

    for key in _OBJECT_KEYS:
        if not isinstance(body[key], dict):
            raise ExecutionStateCorruptError(
                f"{key}: expected an object, got {type(body[key]).__name__!r}"
            )
    for key in _ARRAY_KEYS:
        if not isinstance(body[key], list):
            raise ExecutionStateCorruptError(
                f"{key}: expected a list, got {type(body[key]).__name__!r}"
            )
    # P2-4: the identity's SHAPE is validated here, for every reader, so a body
    # that cannot say which account it belongs to is refused even by a caller
    # that has no manager to compare it against. WHICH account it must be is
    # `assert_account_identity_matches`, on the restore path.
    _validate_account_identity(body["account_identity"], where="account_identity")
    return body

# ── File I/O: atomic write, read-back, fail-closed load ───────────────────────


def serialize_execution_state_body(body: Dict[str, Any]) -> bytes:
    """Render a body to its canonical bytes.

    `sort_keys=True` plus `indent=2` is the structure snapshot's own format,
    and it is what makes an idempotency check a content comparison: the same
    state always produces the same bytes.
    """
    try:
        text = json.dumps(body, indent=2, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ExecutionStatePersistenceError(
            f"execution state body is not JSON-encodable: {exc}"
        ) from exc
    return text.encode("utf-8")


# ── P2-3: one writer at a time, and a uniquely named temp file ────────────────
#
# Two problems, both real with a single fixed `<target>.tmp`:
#
#   * two writers of the same path interleave their writes INTO THE SAME temp
#     file, so the bytes `os.replace` promotes are a mixture of two bodies --
#     atomic promotion of a torn file is still a torn file;
#   * the read-back verification cannot tell "my bytes did not land" from
#     "another writer's valid body landed after mine", so a benign race and a
#     real durability failure produce the same message.
#
# Both are fixed without adding a distributed lock or a lock file. Each writer
# gets its own temp name, and writers of the same resolved path are serialized
# in-process by a per-path lock, which makes the read-back deterministic again.
#
# The scope of that guarantee is stated rather than assumed: ONE process owns a
# state file. Nothing here makes two processes writing the same path safe, and
# nothing here pretends to -- cross-process ownership is a composition decision,
# and the composition does not exist yet.

_TEMP_SEQUENCE = itertools.count()
_WRITE_LOCKS_GUARD = threading.Lock()
_WRITE_LOCKS: Dict[str, threading.Lock] = {}


def _write_lock_for(target: Path) -> threading.Lock:
    """The one lock serializing writers of `target` inside this process."""
    key = str(target)
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _WRITE_LOCKS[key] = lock
        return lock


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync of the directory entry `os.replace` just created.

    The file's own contents are already fsynced before the rename, so the data is
    durable; what this adds is durability of the RENAME, without which a
    host-level crash can leave the target pointing at the pre-write inode.

    Deliberately best-effort. `O_DIRECTORY` does not exist on Windows, and some
    filesystems refuse `fsync` on a directory handle outright. Turning either into
    a failed write would be the UNSAFE direction: the state file is what a restart
    reads to discover a live trade, and refusing to persist it at all is worse
    than persisting it with the same rename exposure this repository's structure
    snapshot already carries. So the exposure is logged, once, with its reason.
    """
    if not hasattr(os, "O_DIRECTORY"):                       # pragma: no cover
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:                                    # pragma: no cover
        logger.warning(
            "EXECUTION_STATE_DIR_FSYNC_UNAVAILABLE dir=%s error=%s: the rename is "
            "not proved durable against a host crash", directory, exc,
        )
        return
    try:
        os.fsync(fd)
    except OSError as exc:                                    # pragma: no cover
        logger.warning(
            "EXECUTION_STATE_DIR_FSYNC_FAILED dir=%s error=%s: the rename is not "
            "proved durable against a host crash", directory, exc,
        )
    finally:
        os.close(fd)


def write_execution_state(path: Any, body: Dict[str, Any]) -> Path:
    """Write `body` atomically and prove it landed, or raise.

    Sequence: validate -> serialize -> write `<target>.<unique>.tmp` -> fsync ->
    replace -> fsync the directory -> re-read -> compare bytes -> re-validate. A
    reader therefore never sees a partially written file, and this function never
    returns having written something it did not verify.

    A failed write leaves the PREVIOUS file untouched: the target is only ever
    replaced by a fully written, fsynced temp file, so an interrupted or refused
    write means the last good state is still there to be recovered.
    """
    validate_execution_state_body(body)
    payload = serialize_execution_state_body(body)

    target = Path(path)
    tmp = target.with_name(
        f"{target.name}.{os.getpid()}.{threading.get_ident()}."
        f"{next(_TEMP_SEQUENCE)}.tmp"
    )
    with _write_lock_for(target):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Written as BYTES: `write_text` would translate newlines on Windows,
            # and the read-back below compares bytes.
            with open(tmp, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # Atomic on both POSIX and Windows.
            os.replace(tmp, target)
        except OSError as exc:
            # The temp file is the only thing that can be left behind, and it is
            # not the state file: the previous target is still intact.
            try:
                tmp.unlink()
            except OSError:                                   # pragma: no cover
                pass
            raise ExecutionStatePersistenceError(
                f"could not write execution state to {target}: {exc}"
            ) from exc
        _fsync_directory(target.parent)

        try:
            written = target.read_bytes()
        except OSError as exc:
            raise ExecutionStatePersistenceError(
                f"could not read back execution state from {target}: {exc}"
            ) from exc
    if written != payload:
        raise ExecutionStatePersistenceError(
            f"execution state read-back mismatch at {target}: "
            f"{len(written)} bytes on disk, {len(payload)} bytes intended"
        )
    try:
        validate_execution_state_body(json.loads(written.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutionStatePersistenceError(
            f"execution state at {target} is not readable after writing: {exc}"
        ) from exc
    logger.info(
        "EXECUTION_STATE_PERSISTED path=%s active_trades=%d history=%d alerts=%d",
        target,
        len(body["active_trades"]),
        len(body["trade_history"]),
        len(body["reconciliation_alerts"]),
    )
    return target


def load_execution_state(path: Any) -> Dict[str, Any]:
    """Read and validate a persisted body, or raise.

    Every failure mode is a refusal, and none of them is repaired: a missing
    file, an unreadable file, invalid JSON, a non-object root, a foreign
    schema, an unsupported version and a wrong key set all raise. A caller can
    ask `execution_state_exists(...)` first if absence is legitimate (a first
    ever start); absence is never silently treated as "no live trades".
    """
    target = Path(path)
    try:
        raw = target.read_bytes()
    except FileNotFoundError as exc:
        raise ExecutionStateCorruptError(
            f"no execution state at {target}"
        ) from exc
    except OSError as exc:
        raise ExecutionStateCorruptError(
            f"could not read execution state at {target}: {exc}"
        ) from exc
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutionStateCorruptError(
            f"execution state at {target} is not valid JSON: {exc}"
        ) from exc
    return validate_execution_state_body(body)


def execution_state_exists(path: Any) -> bool:
    """Is there a state file at `path`? (Says nothing about its validity.)"""
    return Path(path).is_file()

# ── Convenience: the two operations a caller actually performs ────────────────


def persist_execution_state(manager: TradeLifecycleManager, path: Any) -> Path:
    """Export `manager`'s execution state and write it atomically to `path`."""
    return write_execution_state(path, export_execution_state(manager))


def restore_execution_state_from_file(
    manager: TradeLifecycleManager, path: Any
) -> Dict[str, Any]:
    """Load `path` and restore it into `manager`, returning the loaded body."""
    body = load_execution_state(path)
    restore_execution_state(manager, body)
    return body


__all__ = [
    "EXECUTION_STATE_SCHEMA",
    "EXECUTION_STATE_SCHEMA_VERSION",
    "STATE_KEYS",
    "ExecutionStateError",
    "ExecutionStatePersistenceError",
    "ExecutionStateIncompatibleError",
    "ExecutionStateCorruptError",
    "assert_manager_attributes_classified",
    "manager_account_identity",
    "assert_account_identity_matches",
    "export_execution_state",
    "restore_execution_state",
    "validate_execution_state_body",
    "serialize_execution_state_body",
    "write_execution_state",
    "load_execution_state",
    "execution_state_exists",
    "persist_execution_state",
    "restore_execution_state_from_file",
]
