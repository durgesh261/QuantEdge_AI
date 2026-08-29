#!/usr/bin/env python3
"""
Authoritative Delta Exchange India product specifications — fetcher/verifier.

SOURCE (the only one this script trusts)
----------------------------------------
    GET https://api.india.delta.exchange/v2/products/{symbol}

Nothing here is read from, defaulted to, or reconciled against the
repository's existing hardcoded product tables (`execution/validation.py`,
Java `InstrumentRegistry`), old tests, comments, or arithmetic assumptions.
If the endpoint is unreachable or answers `success != true`, the script
writes NOTHING and exits non-zero (safety rules #8, #16).

WHAT THIS ESTABLISHES
---------------------
Per symbol, copied verbatim (unparsed, exactly as returned):
    id, symbol, contract_type, notional_type, tick_size,
    contract_value, contract_unit_currency,
    underlying_asset, quoting_asset, settling_asset

`contract_value` + `contract_unit_currency` are the two fields that
establish the base-asset meaning of ONE contract (BTCUSD -> 0.001 BTC per
contract). `notional_type: vanilla` is documented by Delta as "Contract is
quoted, settled, and margined in the quote currency".

WHAT THIS DOES *NOT* ESTABLISH (recorded as `unverified` in the artifact)
------------------------------------------------------------------------
  * `minimum_order_size` — NO SUCH FIELD exists in the /v2/products payload
    or in Delta's published product schema. Left absent, not invented.
  * the notional <-> order-size conversion formula — Delta's public docs
    never state it. They say only, under Types/Numbers, that "Integer
    numbers (like contract size, product_id and impact size) are unquoted".
    So `size` is an integer contract count, but no published formula ties it
    to `contract_value`. `manual_smc.sizing.resolve_order_quantity` must
    therefore keep demanding an explicitly injected converter.
  * `max_leverage` — not a Delta field at all. `initial_margin`,
    `default_leverage` and `max_leverage_notional` are recorded raw instead.

REPRODUCIBILITY
---------------
`contract_spec` is hashed per symbol (`pinned_sha256`, sha256 over the
canonical JSON of that block). `--verify` re-fetches and compares: a
mismatch means Delta changed the contract, not that the file drifted.
Margin/limit/status fields are recorded but deliberately NOT hashed, since
they move with price and exchange risk configuration.

Usage:
    python engine/scripts/fetch_delta_product_specs.py            # write
    python engine/scripts/fetch_delta_product_specs.py --verify   # compare
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ENGINE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_ROOT.parent
SNAPSHOT_PATH = (REPO_ROOT / "data" / "reference" / "delta_exchange_india"
                 / "product_specs_snapshot.json")

DELTA_INDIA_BASE = "https://api.india.delta.exchange"
ENDPOINT_TEMPLATE = "/v2/products/{symbol}"
SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
SNAPSHOT_VERSION = "1.0.0"

#: pinned field -> JSON path inside the /v2/products/{symbol} response.
#: Hashed. Any change here is a real contract change on Delta's side.
CONTRACT_SPEC_PATHS = {
    "id": "result.id",
    "symbol": "result.symbol",
    "contract_type": "result.contract_type",
    "notional_type": "result.notional_type",
    "tick_size": "result.tick_size",
    "contract_value": "result.contract_value",
    "contract_unit_currency": "result.contract_unit_currency",
    "underlying_asset": "result.underlying_asset.symbol",
    "quoting_asset": "result.quoting_asset.symbol",
    "settling_asset": "result.settling_asset.symbol",
}

#: Recorded for provenance, NOT hashed: these move with price and with
#: Delta's risk configuration, so drift here is not a contract change.
MARGIN_AND_LIMIT_PATHS = {
    "initial_margin": "result.initial_margin",
    "maintenance_margin": "result.maintenance_margin",
    "default_leverage": "result.default_leverage",
    "max_leverage_notional": "result.max_leverage_notional",
    "position_size_limit": "result.position_size_limit",
    "maker_commission_rate": "result.maker_commission_rate",
    "taker_commission_rate": "result.taker_commission_rate",
    "state": "result.state",
    "trading_status": "result.trading_status",
}

#: Asserted absent. If Delta ever adds one, the fetch fails loudly rather
#: than silently continuing to describe it as missing.
ABSENT_FIELDS = ("minimum_order_size", "min_size", "size_step", "max_leverage")

#: Everything a consumer of this artifact must NOT assume it has been given.
UNVERIFIED: Dict[str, str] = {
    "minimum_order_size": (
        "no such field in the /v2/products payload nor in Delta's published "
        "product schema; deliberately not invented"),
    "size_step": (
        "no size-side increment field exists in the payload; the docs state "
        "only that contract size is an unquoted integer"),
    "max_leverage": (
        "not a Delta field; initial_margin / default_leverage / "
        "max_leverage_notional are recorded raw instead of deriving a cap"),
    "notional_to_contracts_formula": (
        "Delta's public documentation never states how order size relates to "
        "contract_value; a converter must still be supplied explicitly"),
}


def _dig(payload: Dict[str, Any], path: str) -> Any:
    """Follow a dotted JSON path, raising if any segment is missing."""
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"{path!r} is absent from the response")
        node = node[part]
    return node


def fetch_product(symbol: str) -> Tuple[Dict[str, Any], str]:
    """One authoritative GET. Raises on anything less than a clean success."""
    url = DELTA_INDIA_BASE + ENDPOINT_TEMPLATE.format(symbol=symbol)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} -> HTTP {response.status}")
        http_date = response.headers.get("Date", "")
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("success") is not True:
        raise RuntimeError(f"{url} -> success is not true: {payload!r}")
    returned = _dig(payload, "result.symbol")
    if returned != symbol:
        raise RuntimeError(
            f"{url} -> asked for {symbol!r}, exchange returned {returned!r}")
    return payload, http_date


def pinned_sha256(block: Dict[str, Any]) -> str:
    canonical = json.dumps(block, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract(payload: Dict[str, Any], http_date: str) -> Dict[str, Any]:
    """Verbatim projection of the response onto the pinned field set."""
    spec = {k: _dig(payload, p) for k, p in CONTRACT_SPEC_PATHS.items()}
    limits = {k: _dig(payload, p) for k, p in MARGIN_AND_LIMIT_PATHS.items()}
    present = [f for f in ABSENT_FIELDS if f in payload.get("result", {})]
    if present:
        raise RuntimeError(
            f"{spec['symbol']}: {present} are documented here as absent but "
            f"the exchange now returns them; re-derive the snapshot schema")
    return {
        "endpoint": ENDPOINT_TEMPLATE.format(symbol=spec["symbol"]),
        "http_date": http_date,
        "contract_spec": spec,
        "pinned_sha256": pinned_sha256(spec),
        "margin_and_limits": limits,
        "verified_fields": sorted(spec),
        "recorded_not_hashed": sorted(limits),
        "absent_from_payload": list(ABSENT_FIELDS),
    }


def build_snapshot() -> Dict[str, Any]:
    products: Dict[str, Any] = {}
    for symbol in SYMBOLS:
        payload, http_date = fetch_product(symbol)
        products[symbol] = extract(payload, http_date)
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "exchange": "Delta Exchange India",
        "source_base_url": DELTA_INDIA_BASE,
        "endpoint_template": ENDPOINT_TEMPLATE,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "retrieved_by": "engine/scripts/fetch_delta_product_specs.py",
        "policy": (
            "Authoritative exchange response only. No value in this file is "
            "derived from repository tables, Java registries, existing tests, "
            "comments, third parties, or arithmetic assumptions."),
        "field_paths": {
            "contract_spec": CONTRACT_SPEC_PATHS,
            "margin_and_limits": MARGIN_AND_LIMIT_PATHS,
        },
        "unverified": UNVERIFIED,
        "products": products,
    }


def verify(existing: Dict[str, Any]) -> List[str]:
    """Re-fetch and compare hashed blocks. Returns human-readable drift."""
    problems: List[str] = []
    for symbol in SYMBOLS:
        record = existing.get("products", {}).get(symbol)
        if record is None:
            problems.append(f"{symbol}: absent from the snapshot")
            continue
        payload, _ = fetch_product(symbol)
        live = {k: _dig(payload, p) for k, p in CONTRACT_SPEC_PATHS.items()}
        if pinned_sha256(live) != record.get("pinned_sha256"):
            problems.append(
                f"{symbol}: live {live} != snapshot {record['contract_spec']}")
    return problems


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch or verify the Delta India product snapshot.")
    parser.add_argument("--verify", action="store_true",
                        help="re-fetch and compare; write nothing")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            existing = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
            problems = verify(existing)
            for problem in problems:
                print(f"DRIFT {problem}")
            print("VERIFY FAILED" if problems else "VERIFY OK")
            return 1 if problems else 0
        snapshot = build_snapshot()
    except (urllib.error.URLError, OSError, RuntimeError, KeyError,
            ValueError) as exc:
        print(f"REFUSING TO WRITE: authoritative source unavailable "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n",
                             encoding="utf-8")
    for symbol, record in snapshot["products"].items():
        spec = record["contract_spec"]
        print(f"{symbol}: id={spec['id']} tick={spec['tick_size']} "
              f"contract_value={spec['contract_value']} "
              f"{spec['contract_unit_currency']}/contract")
    print(f"wrote {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
