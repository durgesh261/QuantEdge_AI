"""
Delta Exchange India instrument registry — loads the checked-in snapshot.
========================================================================

The authoritative data lives in ONE place and is not duplicated here:

    data/reference/delta_exchange_india/product_specs_snapshot.json
    (produced by engine/scripts/fetch_delta_product_specs.py)

This module contains no product values at all. It reads that file, verifies
each record against the content hash the file itself carries, and refuses to
load anything it cannot verify. No network access: the snapshot is pinned
data, and this module imports no HTTP client.

FAIL-CLOSED LOOKUP
------------------
`get()` performs an EXACT match. It does not upper-case, does not strip
whitespace, does not remove a `.P` suffix, and has no default record. An
unrecognised symbol raises `UnknownInstrumentError` — it never becomes
BTCUSD, never becomes product 27, never acquires tick size 0.5.

SYMBOL ALIASES ARE POLICY, NOT DATA
-----------------------------------
Delta's own symbols are BTCUSD / ETHUSD / SOLUSD / XRPUSD. The application
also uses local `.P` forms such as `BTCUSD.P`. Whether those name the same
tradable product is a REPOSITORY DECISION THAT HAS NOT BEEN MADE, so the
alias map is empty by default and every non-native symbol fails closed. The
mechanism exists (`aliases=`) so the decision can be expressed as explicit
data later; this module does not make it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

from quantedge.instruments.models import (
    PERMANENTLY_UNVERIFIED,
    InstrumentSpec,
    Provenance,
    SnapshotIntegrityError,
    SnapshotUnavailableError,
    UnknownInstrumentError,
)

#: Repo-relative location of the authoritative snapshot.
SNAPSHOT_RELPATH = Path("data") / "reference" / "delta_exchange_india" / \
    "product_specs_snapshot.json"

#: Empty on purpose. See "SYMBOL ALIASES ARE POLICY" above.
NO_ALIASES: Mapping[str, str] = MappingProxyType({})

_EXPECTED_BASE_URL = "https://api.india.delta.exchange"
_EXPECTED_ENDPOINT_TEMPLATE = "/v2/products/{symbol}"


def _find_snapshot() -> Path:
    """Walk up from this module until the checked-in snapshot appears."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / SNAPSHOT_RELPATH
        if candidate.is_file():
            return candidate
    raise SnapshotUnavailableError(
        f"{SNAPSHOT_RELPATH.as_posix()} was not found above {here}; the "
        f"authoritative snapshot must be checked in before any instrument "
        f"specification exists")


def _canonical_sha256(block: Mapping[str, Any]) -> str:
    canonical = json.dumps(block, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _exact(symbol: str, name: str, raw: Any) -> Decimal:
    """Exchange string -> exact Decimal. A float input is refused outright."""
    if isinstance(raw, float):
        raise SnapshotIntegrityError(
            f"{symbol}: {name} was stored as a float ({raw!r}); the snapshot "
            f"must keep the exchange's own string so nothing is rounded")
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise SnapshotIntegrityError(
            f"{symbol}: {name} {raw!r} is not a decimal value") from exc


class InstrumentRegistry:
    """Symbol -> `InstrumentSpec`. Exact match only; unknown symbols raise."""

    def __init__(self, specs: Mapping[str, InstrumentSpec],
                 aliases: Optional[Mapping[str, str]] = None) -> None:
        self._specs: Dict[str, InstrumentSpec] = dict(specs)
        supplied = dict(aliases or {})
        for alias, target in supplied.items():
            if target not in self._specs:
                raise UnknownInstrumentError(
                    f"alias {alias!r} points at unregistered symbol "
                    f"{target!r}; refusing a dangling alias")
            if alias in self._specs:
                raise UnknownInstrumentError(
                    f"alias {alias!r} would shadow a native symbol")
        self._aliases: Mapping[str, str] = MappingProxyType(supplied)

    def get(self, symbol: Any) -> InstrumentSpec:
        """
        Exact lookup. No case folding, no whitespace stripping, no `.P`
        removal, no default record.
        """
        if not isinstance(symbol, str) or not symbol:
            raise UnknownInstrumentError(
                f"{symbol!r} is not a usable symbol; refusing to substitute "
                f"another product")
        target = self._aliases.get(symbol, symbol)
        spec = self._specs.get(target)
        if spec is None:
            raise UnknownInstrumentError(
                f"{symbol!r} is not a registered Delta India instrument; "
                f"refusing to substitute another product. Registered: "
                f"{sorted(self._specs)}")
        return spec

    def __contains__(self, symbol: Any) -> bool:
        try:
            self.get(symbol)
        except UnknownInstrumentError:
            return False
        return True

    @property
    def symbols(self) -> Tuple[str, ...]:
        return tuple(sorted(self._specs))

    @property
    def aliases(self) -> Mapping[str, str]:
        return self._aliases


def _spec_from_record(symbol: str, record: Mapping[str, Any],
                      header: Mapping[str, Any],
                      snapshot_path: Path) -> InstrumentSpec:
    spec = record["contract_spec"]
    recorded_hash = record["pinned_sha256"]
    if _canonical_sha256(spec) != recorded_hash:
        raise SnapshotIntegrityError(
            f"{symbol}: contract_spec does not match its recorded "
            f"pinned_sha256; the snapshot was edited without a fresh "
            f"authoritative fetch")
    if spec["symbol"] != symbol:
        raise SnapshotIntegrityError(
            f"snapshot key {symbol!r} disagrees with record symbol "
            f"{spec['symbol']!r}")
    provenance = Provenance(
        source_url=f"{header['source_base_url']}{record['endpoint']}",
        endpoint=record["endpoint"],
        retrieved_at=datetime.fromisoformat(header["retrieved_at_utc"]),
        http_date=record.get("http_date", ""),
        pinned_sha256=recorded_hash,
        snapshot_version=header["snapshot_version"],
        snapshot_path=snapshot_path.as_posix(),
    )
    return InstrumentSpec(
        symbol=spec["symbol"],
        product_id=spec["id"],
        tick_size=_exact(symbol, "tick_size", spec["tick_size"]),
        contract_value=_exact(symbol, "contract_value", spec["contract_value"]),
        contract_unit_currency=spec["contract_unit_currency"],
        notional_type=spec["notional_type"],
        contract_type=spec["contract_type"],
        underlying_asset=spec["underlying_asset"],
        quoting_asset=spec["quoting_asset"],
        settling_asset=spec["settling_asset"],
        provenance=provenance,
        recorded=record.get("margin_and_limits", {}),
        unverified=header["unverified"],
    )


def load_delta_india_registry(
    snapshot_path: Optional[Path] = None,
    aliases: Optional[Mapping[str, str]] = None,
) -> InstrumentRegistry:
    """
    Build a registry from the checked-in authoritative snapshot.

    Raises rather than degrading: a missing file, an unreadable file, a
    header from a different exchange or endpoint, a record whose hash does
    not match, or a snapshot that stopped declaring the unverified fields all
    refuse to load.
    """
    path = Path(snapshot_path) if snapshot_path is not None else _find_snapshot()
    try:
        header = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SnapshotUnavailableError(
            f"cannot read the authoritative snapshot at {path}: {exc}") from exc
    except ValueError as exc:
        raise SnapshotIntegrityError(
            f"{path} is not valid JSON: {exc}") from exc

    if header.get("exchange") != "Delta Exchange India":
        raise SnapshotIntegrityError(
            f"{path}: exchange is {header.get('exchange')!r}, not Delta "
            f"Exchange India")
    if header.get("source_base_url") != _EXPECTED_BASE_URL:
        raise SnapshotIntegrityError(
            f"{path}: source_base_url is {header.get('source_base_url')!r}")
    if header.get("endpoint_template") != _EXPECTED_ENDPOINT_TEMPLATE:
        raise SnapshotIntegrityError(
            f"{path}: endpoint_template is "
            f"{header.get('endpoint_template')!r}")
    undeclared = PERMANENTLY_UNVERIFIED - set(header.get("unverified", {}))
    if undeclared:
        raise SnapshotIntegrityError(
            f"{path}: {sorted(undeclared)} are no longer declared unverified; "
            f"refusing to treat them as verified")
    products = header.get("products") or {}
    if not products:
        raise SnapshotIntegrityError(f"{path}: no product records")

    specs = {symbol: _spec_from_record(symbol, record, header, path)
             for symbol, record in products.items()}
    return InstrumentRegistry(specs, aliases=aliases)


@lru_cache(maxsize=1)
def delta_india_registry() -> InstrumentRegistry:
    """Process-wide registry over the checked-in snapshot. No aliases."""
    return load_delta_india_registry()


__all__ = [
    "NO_ALIASES",
    "SNAPSHOT_RELPATH",
    "InstrumentRegistry",
    "delta_india_registry",
    "load_delta_india_registry",
]
