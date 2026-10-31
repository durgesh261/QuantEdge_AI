"""
Manual SMC — Operator OB Invalidation Ledger (Task N).
======================================================

A dashboard/operator command that makes ONE order block PERMANENTLY
untradable, recorded in a form that survives a dashboard refresh, a backend
restart AND a full historical rescan.

WHY A LEDGER AND NOT A STATE FLAG
---------------------------------
`ManualOBState.INVALIDATED` already exists, but it cannot express this
decision, for two independent reasons:

  1. `state.py:_PERSISTABLE_OB_STATES` REFUSES to persist an `INVALIDATED`
     record — a snapshot containing one is treated as torn (captured
     mid-candle, before `process_candle` popped it). So an operator
     invalidation stored that way would either be rejected on restore or
     would have to weaken that check.
  2. `ManualSMCLifecycle` drops dead OBs out of `live_obs` entirely. A
     rescan of the same candles re-runs the structure detector from a clean
     state, so the SAME order block is re-created as a brand new tradable
     setup. A flag on an object that no longer exists cannot prevent that.
     This is true of BOTH detectors and for different reasons: the
     oracle-extracted `ManualSpecBOSScanner` has an empty `_consumed` set in a
     fresh process, and the production `LuxAlgoInternalOBScanner` has no
     consumed-origin concept at all -- it rebuilds the whole Pine structure
     deterministically from the candles it is shown.

The ledger is therefore a SEPARATE, additive, append-only record keyed by a
rescan-invariant fingerprint. It is consulted at every point where an OB
could become tradable.

THE FINGERPRINT — WHY NOT `ob_id`
---------------------------------
`ob_id` is `MANUAL_{asset}_{direction}_{origin_bar_idx}_{bos_bar_idx}`, and
`ManualOBRecord` documents those indices as "absolute index in asset
DataFrame". They are stable only while the candle history is not extended
BACKWARDS: back-filling earlier candles renumbers every bar and therefore
changes every `ob_id`, even though the order block on the chart is the same
one. The canonical Delta history in this repository starts at
2024-06-01T00:00Z, so a backfill is a realistic future event, not a
hypothetical.

The fingerprint uses the OB's own market characteristics instead:

    MANUAL_SMC_OB|v1|{asset}|{direction}|{origin_dt}|{bos_dt}

`(asset, direction, origin_dt, bos_dt)` is a strict refinement of a key each
detector already treats as unique, so it cannot collide:

  * `ManualSpecBOSScanner` keys uniqueness on `(asset, origin_candle)` in its
    own `_consumed` set, and one origin candle yields at most one SHORT and one
    LONG.
  * `LuxAlgoInternalOBScanner` -- the production detector -- runs
    `_display_structure(..., internal=True)` exactly once per bar, with one
    bullish and one bearish branch, so it stores at most one block per
    `(direction, break bar)`. `bos_dt` IS that break bar, so adding `origin_dt`
    only narrows the key further.

Every component is a timestamp or a label; none is an index, a row number or a
float price, so the fingerprint is invariant under rescan, backfill, reordering
and re-slicing.

Timestamps are normalised to UTC before formatting, which is lossless. A
NAIVE datetime is REFUSED rather than assumed to be UTC: deciding which
instant an unlabelled timestamp denotes is exactly the kind of ambiguity
safety rule #13 says a default must not answer.

DELIBERATELY ABSENT
-------------------
No exchange calls, no order cancellation, no database, no HTTP. This module
records a decision. Acting on a resting exchange order behind an invalidated
setup is already the runtime's `cancel_expired_entries` job, which keys off
the `INVALIDATED` lifecycle event this decision also emits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

#: Fingerprint format version. Part of the key itself, so a future format can
#: never be confused with this one.
FINGERPRINT_VERSION: str = "v1"
#: Fingerprint prefix. Keeps a Manual SMC key distinguishable from the
#: pre-existing LuxAlgo "SMC" / "2.1" strategy's identifiers.
FINGERPRINT_PREFIX: str = "MANUAL_SMC_OB"
#: Field separator. `|` cannot occur in an asset name, a direction or an
#: ISO-8601 timestamp, so the key is unambiguously parseable.
FINGERPRINT_SEP: str = "|"

#: The two directions the strategy produces. An unknown one fails closed.
VALID_DIRECTIONS: frozenset = frozenset({"LONG", "SHORT"})

#: Default provenance strings, used when a caller supplies none.
DEFAULT_REQUESTED_BY: str = "OPERATOR"
DEFAULT_REASON: str = "MANUAL_DASHBOARD_INVALIDATION"


class InvalidationError(RuntimeError):
    """Base class for every invalidation-ledger refusal."""


class InvalidObIdentityError(InvalidationError):
    """The OB characteristics cannot form a stable fingerprint."""


class NaiveTimestampError(InvalidObIdentityError):
    """
    A timestamp carried no timezone.

    Refused rather than assumed to be UTC: two OBs whose timestamps differ
    only by an unlabelled offset would collide or diverge silently, and the
    fingerprint is the only thing standing between a rescan and a trade on an
    order block the operator deleted.
    """


class LedgerIntegrityError(InvalidationError):
    """A persisted ledger is not self-consistent (duplicate or malformed)."""


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------
def normalise_dt(value: object, where: str) -> datetime:
    """
    Return `value` as an aware UTC datetime, or refuse.

    An aware non-UTC datetime is converted, which is lossless and denotes the
    same instant. A naive one raises `NaiveTimestampError`.
    """
    if not isinstance(value, datetime):
        raise InvalidObIdentityError(
            f"{where}: expected datetime, got {type(value).__name__} "
            f"{value!r}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NaiveTimestampError(
            f"{where}: {value!r} has no timezone. Refusing to assume UTC — an "
            f"order-block identity built on an unlabelled instant could match "
            f"the wrong block (safety rule #13)")
    return value.astimezone(timezone.utc)


def fingerprint_parts(asset: object, direction: object, origin_dt: object,
                      bos_dt: object) -> Tuple[str, str, datetime, datetime]:
    """Validate and normalise the four identity components."""
    if not isinstance(asset, str) or not asset.strip():
        raise InvalidObIdentityError(
            f"asset: expected a non-empty str, got {asset!r}")
    if not isinstance(direction, str) or direction not in VALID_DIRECTIONS:
        raise InvalidObIdentityError(
            f"direction: {direction!r} is not one of "
            f"{sorted(VALID_DIRECTIONS)}; refusing to fingerprint an order "
            f"block whose side is unknown")
    if FINGERPRINT_SEP in asset:
        raise InvalidObIdentityError(
            f"asset {asset!r} contains the reserved separator "
            f"{FINGERPRINT_SEP!r}")
    return (asset, direction,
            normalise_dt(origin_dt, "origin_dt"),
            normalise_dt(bos_dt, "bos_dt"))


def make_fingerprint(asset: object, direction: object, origin_dt: object,
                     bos_dt: object) -> str:
    """
    The stable, rescan-invariant OB key. See the module docstring.

    Built from market characteristics only — never from a bar index, a row
    number or a dashboard position.
    """
    asset_s, direction_s, origin, bos = fingerprint_parts(
        asset, direction, origin_dt, bos_dt)
    return FINGERPRINT_SEP.join((
        FINGERPRINT_PREFIX, FINGERPRINT_VERSION, asset_s, direction_s,
        origin.isoformat(), bos.isoformat()))


def fingerprint_for_ob(ob: Any) -> str:
    """
    Fingerprint one `ManualOBRecord`.

    Duck-typed on purpose: `models.py` is imported by everything in this
    package and this module must stay importable from `lifecycle.py` without
    creating a cycle.
    """
    try:
        asset = ob.asset
        direction = ob.direction
        origin_dt = ob.formation_dt
        bos_dt = ob.bos_dt
    except AttributeError as exc:
        raise InvalidObIdentityError(
            f"{type(ob).__name__} is not an order-block record: {exc}"
        ) from exc
    return make_fingerprint(asset, direction, origin_dt, bos_dt)


# ---------------------------------------------------------------------------
# The persisted record
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ManualOBInvalidation:
    """
    One operator decision: this order block is permanently untradable.

    Frozen, so a record cannot be edited into a different OB's decision after
    the fact. `ob_id_at_request`, `ob_top` and `ob_bottom` are DIAGNOSTIC only
    — they record what the operator was looking at. `fingerprint` is the key,
    and it is the only field consulted when admitting a setup.
    """
    fingerprint: str
    asset: str
    direction: str
    origin_dt: datetime
    bos_dt: datetime
    requested_at: datetime
    requested_by: str = DEFAULT_REQUESTED_BY
    reason: str = DEFAULT_REASON
    ob_id_at_request: Optional[str] = None
    ob_top: Optional[float] = None
    ob_bottom: Optional[float] = None


def build_record(
    asset: object,
    direction: object,
    origin_dt: object,
    bos_dt: object,
    requested_at: object,
    requested_by: str = DEFAULT_REQUESTED_BY,
    reason: str = DEFAULT_REASON,
    ob_id_at_request: Optional[str] = None,
    ob_top: Optional[float] = None,
    ob_bottom: Optional[float] = None,
) -> ManualOBInvalidation:
    """Validate everything, then build the frozen record."""
    asset_s, direction_s, origin, bos = fingerprint_parts(
        asset, direction, origin_dt, bos_dt)
    if not isinstance(requested_by, str) or not requested_by.strip():
        raise InvalidationError(
            f"requested_by: expected a non-empty str naming who asked for the "
            f"invalidation, got {requested_by!r}")
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidationError(
            f"reason: expected a non-empty str, got {reason!r}")
    return ManualOBInvalidation(
        fingerprint=make_fingerprint(asset_s, direction_s, origin, bos),
        asset=asset_s,
        direction=direction_s,
        origin_dt=origin,
        bos_dt=bos,
        requested_at=normalise_dt(requested_at, "requested_at"),
        requested_by=requested_by,
        reason=reason,
        ob_id_at_request=(None if ob_id_at_request is None
                          else str(ob_id_at_request)),
        ob_top=None if ob_top is None else float(ob_top),
        ob_bottom=None if ob_bottom is None else float(ob_bottom),
    )


def record_for_ob(
    ob: Any,
    requested_at: object,
    requested_by: str = DEFAULT_REQUESTED_BY,
    reason: str = DEFAULT_REASON,
) -> ManualOBInvalidation:
    """Build a record from a live `ManualOBRecord`, carrying its diagnostics."""
    return build_record(
        asset=getattr(ob, "asset", None),
        direction=getattr(ob, "direction", None),
        origin_dt=getattr(ob, "formation_dt", None),
        bos_dt=getattr(ob, "bos_dt", None),
        requested_at=requested_at,
        requested_by=requested_by,
        reason=reason,
        ob_id_at_request=getattr(ob, "ob_id", None),
        ob_top=getattr(ob, "ob_top", None),
        ob_bottom=getattr(ob, "ob_bottom", None),
    )


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------
class ManualOBInvalidationLedger:
    """
    The authoritative set of operator-invalidated order blocks.

    APPEND-ONLY BY DESIGN. There is no `remove()` and no `un_invalidate()`:
    requirement A2 is "PERMANENTLY INVALID", and an API that could restore
    tradability would be the one line of code capable of breaking that
    promise. Reversal, if it is ever authorised, must be an explicit new
    feature with its own audit trail — not a method that already exists.

    IDEMPOTENT: `add()` on a fingerprint already present keeps the FIRST
    record and reports `created=False`. The first decision is the one with the
    real timestamp and the real operator; overwriting it would lose the audit
    trail and could produce two conflicting records for one order block
    (requirement A8 and the "no conflicting records" test).
    """

    __slots__ = ("_records",)

    def __init__(self,
                 records: Optional[Iterable[ManualOBInvalidation]] = None
                 ) -> None:
        self._records: Dict[str, ManualOBInvalidation] = {}
        for record in records or ():
            self._insert(record)

    # -- construction ------------------------------------------------------
    def _insert(self, record: ManualOBInvalidation) -> None:
        if not isinstance(record, ManualOBInvalidation):
            raise LedgerIntegrityError(
                f"expected ManualOBInvalidation, got {type(record).__name__} "
                f"{record!r}")
        existing = self._records.get(record.fingerprint)
        if existing is not None and existing != record:
            raise LedgerIntegrityError(
                f"two different invalidation records share the fingerprint "
                f"{record.fingerprint!r}; refusing to guess which decision is "
                f"authoritative")
        self._records[record.fingerprint] = record

    def add(self, record: ManualOBInvalidation
            ) -> Tuple[ManualOBInvalidation, bool]:
        """
        Record a decision. Returns `(effective_record, created)`.

        `created is False` means the OB was already invalidated and the stored
        record was left untouched — the idempotent path, which is a success and
        never an error.
        """
        if not isinstance(record, ManualOBInvalidation):
            raise LedgerIntegrityError(
                f"expected ManualOBInvalidation, got {type(record).__name__}")
        existing = self._records.get(record.fingerprint)
        if existing is not None:
            return existing, False
        self._records[record.fingerprint] = record
        return record, True

    # -- queries -----------------------------------------------------------
    def contains(self, fingerprint: object) -> bool:
        """Membership by fingerprint. No normalisation, no fuzzy matching."""
        return isinstance(fingerprint, str) and fingerprint in self._records

    def covers(self, ob: Any) -> bool:
        """
        Is this order block operator-invalidated?

        Computes the fingerprint from the OB's own characteristics, so it
        answers correctly for an OB that a rescan just re-created under a
        different `ob_id`.
        """
        if not self._records:
            return False
        return fingerprint_for_ob(ob) in self._records

    def get(self, fingerprint: object) -> Optional[ManualOBInvalidation]:
        if not isinstance(fingerprint, str):
            return None
        return self._records.get(fingerprint)

    def record_for(self, ob: Any) -> Optional[ManualOBInvalidation]:
        if not self._records:
            return None
        return self._records.get(fingerprint_for_ob(ob))

    def records(self, asset: Optional[str] = None
                ) -> Tuple[ManualOBInvalidation, ...]:
        """
        Every decision, in a canonical order.

        Sorted by fingerprint so two ledgers holding the same decisions always
        project identically — the same reason `encode_scanner` sorts
        `consumed`.
        """
        rows = self._records.values()
        if asset is not None:
            rows = [r for r in rows if r.asset == asset]
        return tuple(sorted(rows, key=lambda r: r.fingerprint))

    @property
    def fingerprints(self) -> Tuple[str, ...]:
        return tuple(sorted(self._records))

    def __len__(self) -> int:
        return len(self._records)

    def __bool__(self) -> bool:
        return bool(self._records)

    def __contains__(self, fingerprint: object) -> bool:
        return self.contains(fingerprint)

    def __repr__(self) -> str:                     # pragma: no cover - trivial
        return f"<ManualOBInvalidationLedger n={len(self._records)}>"


def verify_fingerprint(record: ManualOBInvalidation, where: str) -> None:
    """
    Re-derive a decoded record's fingerprint and refuse a mismatch.

    A persisted record carries its fingerprint verbatim so the key is readable
    on disk, but that means a hand-edited or corrupted payload could pair one
    order block's characteristics with another's key. Recomputing closes that
    gap: the fingerprint is the ONLY thing that blocks a trade, so it must be
    provably derived from the characteristics stored beside it.
    """
    expected = make_fingerprint(record.asset, record.direction,
                                record.origin_dt, record.bos_dt)
    if record.fingerprint != expected:
        raise LedgerIntegrityError(
            f"{where}: fingerprint {record.fingerprint!r} is not the one its "
            f"own characteristics produce ({expected!r}); refusing a record "
            f"whose key does not match the order block it describes")


__all__ = [
    "FINGERPRINT_VERSION",
    "FINGERPRINT_PREFIX",
    "FINGERPRINT_SEP",
    "VALID_DIRECTIONS",
    "DEFAULT_REQUESTED_BY",
    "DEFAULT_REASON",
    "InvalidationError",
    "InvalidObIdentityError",
    "NaiveTimestampError",
    "LedgerIntegrityError",
    "normalise_dt",
    "fingerprint_parts",
    "make_fingerprint",
    "fingerprint_for_ob",
    "ManualOBInvalidation",
    "build_record",
    "record_for_ob",
    "ManualOBInvalidationLedger",
    "verify_fingerprint",
]
