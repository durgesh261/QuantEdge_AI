"""
Shared instrument specifications — provenance-carrying models.
=============================================================

Owned by neither `quantedge.execution` nor `quantedge.strategy.manual_smc`.
This package is a LEAF of the dependency graph: it imports nothing from
either of them, and neither of them imports it. Callers read a verified spec
here and inject what they need downstream — `manual_smc` in particular stays
injection-fed and never learns this package exists.

VERIFIED vs RECORDED vs UNVERIFIED
----------------------------------
VERIFIED    fields come from the hashed `contract_spec` block of the
            authoritative snapshot and carry `Provenance`.
RECORDED    fields (margin, commissions, limits, state) are kept verbatim but
            deliberately NOT hashed: they move with price and with Delta's
            risk configuration, so drift in them is not a contract change.
UNVERIFIED  fields are named, given a reason, and every accessor for them
            RAISES. No default, no placeholder, no guess.

The permanently-unverified names are `minimum_order_size`, `size_step`,
`max_leverage` and `notional_to_contracts_formula`. Delta publishes none of
them, so a verified `contract_value` does NOT mean an order quantity can be
computed. That boundary stays closed (safety rules #8, #16).

Exact values only: `tick_size` and `contract_value` are `Decimal`, built from
the exchange's own strings. No float ever touches them here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, ClassVar, FrozenSet, Mapping


class InstrumentError(RuntimeError):
    """Base class for every refusal in this package."""


class UnknownInstrumentError(InstrumentError):
    """Symbol is not explicitly registered. Fails closed (safety rule #15)."""


class FieldUnverifiedError(InstrumentError):
    """Delta does not publish this field, so it must not be guessed."""


class SnapshotUnavailableError(InstrumentError):
    """The checked-in authoritative snapshot is missing or unreadable."""


class SnapshotIntegrityError(InstrumentError):
    """The snapshot's own recorded hash does not match its content."""


#: Names Delta does not publish, mapped to why they stay unverified. The
#: registry cross-checks this set against the snapshot's own declaration, so
#: a snapshot that quietly stopped declaring them cannot load.
PERMANENTLY_UNVERIFIED: FrozenSet[str] = frozenset({
    "minimum_order_size",
    "size_step",
    "max_leverage",
    "notional_to_contracts_formula",
})


@dataclass(frozen=True)
class Provenance:
    """Where one instrument's verified fields came from."""

    source_url: str
    endpoint: str
    retrieved_at: datetime
    http_date: str
    pinned_sha256: str
    snapshot_version: str
    snapshot_path: str

    def __post_init__(self) -> None:
        for name in ("source_url", "endpoint", "pinned_sha256",
                     "snapshot_version", "snapshot_path"):
            if not str(getattr(self, name)).strip():
                raise SnapshotIntegrityError(f"provenance is missing {name}")
        if self.retrieved_at.tzinfo is None:
            raise SnapshotIntegrityError(
                "provenance retrieved_at must be timezone-aware")
        if len(self.pinned_sha256) != 64:
            raise SnapshotIntegrityError(
                f"pinned_sha256 is not a sha256 digest: {self.pinned_sha256!r}")

    def as_source_string(self) -> str:
        """One line a caller can hand to a downstream `verification_source`."""
        return (f"{self.source_url} "
                f"retrieved_at={self.retrieved_at.isoformat()} "
                f"sha256={self.pinned_sha256}")


@dataclass(frozen=True)
class InstrumentSpec:
    """
    One exchange contract, as the exchange itself described it.

    `recorded` and `unverified` are exposed as read-only mappings so a caller
    cannot mutate a spec into looking more verified than it is.
    """

    symbol: str
    product_id: int
    tick_size: Decimal
    contract_value: Decimal
    contract_unit_currency: str
    notional_type: str
    contract_type: str
    underlying_asset: str
    quoting_asset: str
    settling_asset: str
    provenance: Provenance
    recorded: Mapping[str, Any] = field(default_factory=dict)
    unverified: Mapping[str, str] = field(default_factory=dict)

    VERIFIED_FIELDS: ClassVar[FrozenSet[str]] = frozenset({
        "symbol", "product_id", "tick_size", "contract_value",
        "contract_unit_currency", "notional_type", "contract_type",
        "underlying_asset", "quoting_asset", "settling_asset",
    })

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.strip():
            raise UnknownInstrumentError(
                f"instrument symbol {self.symbol!r} is empty or padded")
        if isinstance(self.product_id, bool) or not isinstance(self.product_id, int):
            raise SnapshotIntegrityError(
                f"{self.symbol}: product_id must be an int, got "
                f"{self.product_id!r}")
        if self.product_id <= 0:
            raise SnapshotIntegrityError(
                f"{self.symbol}: product_id must be positive")
        for name in ("tick_size", "contract_value"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise SnapshotIntegrityError(
                    f"{self.symbol}: {name} must be an exact Decimal, got "
                    f"{type(value).__name__} — a float would lose the "
                    f"exchange's own value")
            if value <= 0:
                raise SnapshotIntegrityError(
                    f"{self.symbol}: {name} must be positive, got {value}")
        if self.notional_type not in ("vanilla", "inverse"):
            raise SnapshotIntegrityError(
                f"{self.symbol}: notional_type {self.notional_type!r} is not "
                f"one of Delta's documented values")
        missing = PERMANENTLY_UNVERIFIED - set(self.unverified)
        if missing:
            raise SnapshotIntegrityError(
                f"{self.symbol}: {sorted(missing)} must be declared "
                f"unverified, not omitted")
        object.__setattr__(self, "recorded", MappingProxyType(dict(self.recorded)))
        object.__setattr__(self, "unverified",
                           MappingProxyType(dict(self.unverified)))

    # -- the closed side -------------------------------------------------
    def _refuse(self, name: str) -> Any:
        reason = self.unverified.get(name, "not published by Delta")
        raise FieldUnverifiedError(
            f"{self.symbol}: {name} is UNVERIFIED — {reason}. Refusing to "
            f"supply a value (safety rules #8, #16).")

    @property
    def minimum_order_size(self) -> Any:
        return self._refuse("minimum_order_size")

    @property
    def size_step(self) -> Any:
        return self._refuse("size_step")

    @property
    def max_leverage(self) -> Any:
        return self._refuse("max_leverage")

    def notional_to_contracts(self, notional_usd: Any = None) -> Any:
        """
        Never returns a quantity. A verified `contract_value` does NOT make
        the notional->contracts conversion verified; that formula is not in
        Delta's published documentation.
        """
        return self._refuse("notional_to_contracts_formula")

    # -- the open side ---------------------------------------------------
    def is_verified(self, name: str) -> bool:
        return name in self.VERIFIED_FIELDS

    def require(self, name: str) -> Any:
        """Read a verified field by name; anything else refuses."""
        if name in self.unverified:
            return self._refuse(name)
        if name not in self.VERIFIED_FIELDS:
            raise FieldUnverifiedError(
                f"{self.symbol}: {name!r} is not a verified field of this "
                f"snapshot; verified fields are "
                f"{sorted(self.VERIFIED_FIELDS)}")
        return getattr(self, name)

    @property
    def one_contract_description(self) -> str:
        """e.g. '1 BTCUSD contract = 0.001 BTC'."""
        return (f"1 {self.symbol} contract = {self.contract_value} "
                f"{self.contract_unit_currency}")


__all__ = [
    "PERMANENTLY_UNVERIFIED",
    "FieldUnverifiedError",
    "InstrumentError",
    "InstrumentSpec",
    "Provenance",
    "SnapshotIntegrityError",
    "SnapshotUnavailableError",
    "UnknownInstrumentError",
]
