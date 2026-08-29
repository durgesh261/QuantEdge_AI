"""
`quantedge.instruments` — the shared, provenance-carrying instrument registry.
=============================================================================

One source of truth for exchange contract metadata, owned by neither
`quantedge.execution` nor `quantedge.strategy.manual_smc`. Dependency
direction is strictly:

    quantedge.instruments  ->  callers

This package imports neither of those two, and neither imports it.
`manual_smc` stays injection-fed: a caller reads a verified `InstrumentSpec`
here and constructs whatever the strategy needs.

What is verified (from the checked-in authoritative Delta India snapshot,
each record hash-checked on load): product_id, symbol, tick_size,
contract_value, contract_unit_currency, notional_type, contract_type and the
underlying/quoting/settling assets, each carrying `Provenance`.

What is NOT: `minimum_order_size`, `size_step`, `max_leverage` and the
notional->contracts formula. Delta publishes none of them; every accessor
raises `FieldUnverifiedError`. A verified `contract_value` does NOT make an
order quantity computable — that boundary is intentional.

Nothing here reads the network, and no product value is hardcoded in this
package: it all comes from
`data/reference/delta_exchange_india/product_specs_snapshot.json`.
"""

from quantedge.instruments.models import (
    PERMANENTLY_UNVERIFIED,
    FieldUnverifiedError,
    InstrumentError,
    InstrumentSpec,
    Provenance,
    SnapshotIntegrityError,
    SnapshotUnavailableError,
    UnknownInstrumentError,
)
from quantedge.instruments.registry import (
    NO_ALIASES,
    SNAPSHOT_RELPATH,
    InstrumentRegistry,
    delta_india_registry,
    load_delta_india_registry,
)

__all__ = [
    "NO_ALIASES",
    "PERMANENTLY_UNVERIFIED",
    "SNAPSHOT_RELPATH",
    "FieldUnverifiedError",
    "InstrumentError",
    "InstrumentRegistry",
    "InstrumentSpec",
    "Provenance",
    "SnapshotIntegrityError",
    "SnapshotUnavailableError",
    "UnknownInstrumentError",
    "delta_india_registry",
    "load_delta_india_registry",
]
