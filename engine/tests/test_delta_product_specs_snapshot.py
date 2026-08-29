"""
Delta Exchange India product specifications — authoritative snapshot pin.
========================================================================

The Phase 2 investigation established that the repository held four parallel
product tables and no verified `contract_value` for any symbol. This file
pins the one artifact that is sourced from the exchange itself:

    data/reference/delta_exchange_india/product_specs_snapshot.json
    produced by engine/scripts/fetch_delta_product_specs.py

What this file asserts:

  §A  The artifact exists, is well formed, and carries its own provenance:
      source endpoint, retrieval timestamp, and a per-symbol content hash.
  §B  All four Manual SMC symbols are present, and their ids, tick sizes and
      contract values equal what the exchange returned. The expected numbers
      below were transcribed from that response — NOT from
      `execution/validation.py`, NOT from the Java InstrumentRegistry, NOT
      from older tests, and NOT from any arithmetic assumption.
  §C  `contract_value` is explicitly populated per symbol and is NOT the
      1.0 placeholder the execution layer still defaults to.
  §D  What the exchange does not publish is recorded as unverified rather
      than filled in: no `minimum_order_size`, no size increment, no
      `max_leverage`, no documented notional->contracts formula.
  §E  Feeding the artifact into the repository's existing fail-closed schema
      (`manual_smc.sizing.ContractSpec`) yields verified specs that carry
      provenance, while any symbol absent from the artifact stays UNVERIFIED
      and raises on use.
  §F  No unknown symbol can silently become BTCUSD / product 27.

This file adds NO production module and NO second schema. The lookup helpers
below live here on purpose: the shared instrument registry is a later,
separately approved step, and until it exists nothing in production reads
this artifact.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quantedge.strategy.manual_smc.sizing import (
    MANUAL_SMC_SYMBOLS,
    UNVERIFIED,
    ContractSpec,
    ContractSpecRegistry,
    ContractValueUnverifiedError,
    UnknownSymbolError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = (REPO_ROOT / "data" / "reference" / "delta_exchange_india"
                 / "product_specs_snapshot.json")
FETCHER_PATH = REPO_ROOT / "engine" / "scripts" / "fetch_delta_product_specs.py"

#: Transcribed from GET https://api.india.delta.exchange/v2/products/{symbol}
#: on 2026-08-28. Values are the exchange's own strings, kept as strings so a
#: float never becomes the pin. Two of these deliberately CONTRADICT the
#: repository's pre-existing hardcoded tables, which is the entire point:
#:   * contract_value was 1.0 everywhere in `execution/validation.py`;
#:     BTCUSD is really 0.001 BTC and ETHUSD 0.01 ETH per contract.
#:   * SOLUSD tick_size was 0.01 in both hardcoded tables; it is 0.0001.
AUTHORITATIVE = {
    "BTCUSD": {"id": 27, "tick_size": "0.5",
               "contract_value": "0.001", "unit": "BTC"},
    "ETHUSD": {"id": 3136, "tick_size": "0.05",
               "contract_value": "0.01", "unit": "ETH"},
    "SOLUSD": {"id": 14823, "tick_size": "0.0001",
               "contract_value": "1", "unit": "SOL"},
    "XRPUSD": {"id": 14969, "tick_size": "0.0001",
               "contract_value": "1", "unit": "XRP"},
}

#: sha256 over the canonical JSON of each `contract_spec` block. An edit to
#: the artifact that is not a fresh authoritative fetch fails here.
PINNED_SHA256 = {
    "BTCUSD": "8eb7d511aebac0ebdd1b27938c432ef0d9f53b78ceaaab5778107059e5ec2ab5",
    "ETHUSD": "1c13b0968676ee422e5b5edc1735ec9ae3db50b2638789e32fe2b988d8b9defb",
    "SOLUSD": "becb5595f257379fd2bb3a645b12032fed0c713257cd6ce2a73cc6dacffaa34b",
    "XRPUSD": "9164cbf960465d81fdc330ca906ecd2b96950699ea5e07a0d32297dae1450b8d",
}

UNKNOWN_SYMBOLS = ("FOOUSD", "BTCUSD.P", "btcusd", "BTC-USD", "BTCUSDT", "")


@pytest.fixture(scope="module")
def snapshot():
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def lookup_contract_spec(snap, symbol):
    """
    Fail-closed lookup. No case folding, no `.P` stripping, no default
    record: an unrecognised symbol raises instead of resolving to anything.
    """
    record = snap["products"].get(symbol)
    if record is None:
        raise UnknownSymbolError(
            f"{symbol!r} is not in the authoritative Delta India snapshot; "
            f"refusing to substitute another product")
    return record["contract_spec"]


def provenance(snap, symbol):
    record = snap["products"][symbol]
    return (f"{snap['source_base_url']}{record['endpoint']} "
            f"retrieved_at={snap['retrieved_at_utc']} "
            f"sha256={record['pinned_sha256']}")


def contract_spec_from_snapshot(snap, symbol):
    """The artifact expressed in the repository's existing fail-closed schema."""
    spec = lookup_contract_spec(snap, symbol)
    return ContractSpec(
        symbol=spec["symbol"],
        contract_value=float(Decimal(spec["contract_value"])),
        verification_source=provenance(snap, symbol),
        verified_at=datetime.fromisoformat(snap["retrieved_at_utc"]),
    )


# ---------------------------------------------------------------------------
# §A  The artifact and its provenance.
# ---------------------------------------------------------------------------
class TestArtifactAndProvenance:
    def test_the_snapshot_and_its_fetcher_are_both_checked_in(self):
        assert SNAPSHOT_PATH.is_file(), SNAPSHOT_PATH
        assert FETCHER_PATH.is_file(), FETCHER_PATH

    def test_it_names_the_authoritative_endpoint_it_came_from(self, snapshot):
        assert snapshot["exchange"] == "Delta Exchange India"
        assert snapshot["source_base_url"] == "https://api.india.delta.exchange"
        assert snapshot["endpoint_template"] == "/v2/products/{symbol}"
        assert snapshot["retrieved_by"].endswith("fetch_delta_product_specs.py")
        for symbol in AUTHORITATIVE:
            record = snapshot["products"][symbol]
            assert record["endpoint"] == f"/v2/products/{symbol}"
            assert record["http_date"], f"{symbol}: no HTTP Date recorded"

    def test_the_retrieval_date_is_recorded_and_parseable(self, snapshot):
        retrieved = datetime.fromisoformat(snapshot["retrieved_at_utc"])
        assert retrieved.tzinfo is not None
        assert retrieved.year >= 2026

    def test_the_policy_forbids_the_sources_we_must_not_trust(self, snapshot):
        policy = snapshot["policy"].lower()
        assert "authoritative exchange response only" in policy
        for forbidden in ("repository tables", "java", "existing tests",
                          "comments", "third part", "arithmetic assumptions"):
            assert forbidden in policy, forbidden

    def test_every_pinned_field_declares_its_path_in_the_raw_response(
            self, snapshot):
        paths = snapshot["field_paths"]["contract_spec"]
        assert paths["contract_value"] == "result.contract_value"
        assert paths["tick_size"] == "result.tick_size"
        assert paths["id"] == "result.id"
        assert paths["contract_unit_currency"] == "result.contract_unit_currency"
        for symbol in AUTHORITATIVE:
            spec = snapshot["products"][symbol]["contract_spec"]
            assert set(spec) == set(paths)


# ---------------------------------------------------------------------------
# §B  All four symbols, with the ids and ticks the exchange returned.
# ---------------------------------------------------------------------------
class TestAllFourSymbolsArePresent:
    def test_the_snapshot_holds_exactly_the_four_manual_smc_symbols(
            self, snapshot):
        assert set(snapshot["products"]) == set(AUTHORITATIVE)
        assert set(snapshot["products"]) == set(MANUAL_SMC_SYMBOLS)

    def test_no_alias_records_exist_to_mask_a_typo(self, snapshot):
        """
        Delta-native symbols only. A `BTCUSD.P` alias would have to be
        invented here, and symbol mapping belongs to the future registry.
        """
        assert not [s for s in snapshot["products"] if "." in s]

    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_the_product_id_and_tick_size_are_the_authoritative_ones(
            self, snapshot, symbol):
        expected = AUTHORITATIVE[symbol]
        spec = lookup_contract_spec(snapshot, symbol)
        assert spec["symbol"] == symbol
        assert spec["id"] == expected["id"]
        assert isinstance(spec["id"], int)
        assert spec["tick_size"] == expected["tick_size"]
        assert Decimal(spec["tick_size"]) > 0

    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_the_contract_spec_hash_matches_the_pinned_snapshot(
            self, snapshot, symbol):
        assert snapshot["products"][symbol]["pinned_sha256"] == \
            PINNED_SHA256[symbol]

    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_each_product_is_a_live_usd_settled_perpetual(
            self, snapshot, symbol):
        spec = lookup_contract_spec(snapshot, symbol)
        assert spec["contract_type"] == "perpetual_futures"
        assert spec["notional_type"] == "vanilla"
        assert spec["quoting_asset"] == "USD"
        assert spec["settling_asset"] == "USD"
        limits = snapshot["products"][symbol]["margin_and_limits"]
        assert limits["state"] == "live"
        assert limits["trading_status"] == "operational"


# ---------------------------------------------------------------------------
# §C  contract_value is populated from the exchange, not defaulted to 1.0.
# ---------------------------------------------------------------------------
class TestContractValueIsPopulatedNotDefaulted:
    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_the_contract_value_is_the_authoritative_string(
            self, snapshot, symbol):
        expected = AUTHORITATIVE[symbol]
        spec = lookup_contract_spec(snapshot, symbol)
        assert spec["contract_value"] == expected["contract_value"]
        assert isinstance(spec["contract_value"], str), (
            "kept as the exchange's own string so no float rounding enters "
            "the artifact")
        assert Decimal(spec["contract_value"]) > 0

    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_one_contract_has_an_explicit_base_asset_meaning(
            self, snapshot, symbol):
        """
        `contract_value` + `contract_unit_currency` are the two fields that
        say what one contract IS: 1 BTCUSD contract = 0.001 BTC.
        """
        spec = lookup_contract_spec(snapshot, symbol)
        assert spec["contract_unit_currency"] == AUTHORITATIVE[symbol]["unit"]
        assert spec["contract_unit_currency"] == spec["underlying_asset"]

    def test_the_values_are_not_the_one_point_zero_placeholder(self, snapshot):
        """
        The execution layer still defaults `contract_value` to 1.0 for every
        symbol. That is wrong for BTCUSD and ETHUSD by 1000x and 100x.
        """
        values = {s: Decimal(lookup_contract_spec(snapshot, s)["contract_value"])
                  for s in AUTHORITATIVE}
        assert values["BTCUSD"] == Decimal("0.001")
        assert values["ETHUSD"] == Decimal("0.01")
        assert len(set(values.values())) > 1, (
            "a single value shared by all four symbols would be the "
            "placeholder, not exchange data")


# ---------------------------------------------------------------------------
# §D  What Delta does not publish is declared missing, never filled in.
# ---------------------------------------------------------------------------
class TestWhatIsNotPublishedStaysUnverified:
    def test_minimum_order_size_is_absent_and_declared_so(self, snapshot):
        for symbol in AUTHORITATIVE:
            record = snapshot["products"][symbol]
            assert "minimum_order_size" in record["absent_from_payload"]
            assert "minimum_order_size" not in record["contract_spec"]
            assert "minimum_order_size" not in record["margin_and_limits"]
        assert "minimum_order_size" in snapshot["unverified"]

    @pytest.mark.parametrize("field", ["minimum_order_size", "size_step",
                                       "max_leverage",
                                       "notional_to_contracts_formula"])
    def test_each_missing_field_says_why_it_is_missing(self, snapshot, field):
        reason = snapshot["unverified"][field]
        assert reason and len(reason) > 20, field

    def test_no_size_or_leverage_number_was_invented_anywhere(self, snapshot):
        """
        The exchange publishes no size increment and no leverage cap, so the
        artifact must contain no field that looks like one.
        """
        for symbol in AUTHORITATIVE:
            record = snapshot["products"][symbol]
            fields = set(record["contract_spec"]) | set(record["margin_and_limits"])
            for banned in ("minimum_order_size", "min_size", "size_step",
                           "max_leverage", "lot_size", "quantity"):
                assert banned not in fields, f"{symbol}: {banned}"

    def test_leverage_is_recorded_raw_rather_than_derived(self, snapshot):
        """`initial_margin` etc. are recorded; no cap is computed from them."""
        limits = snapshot["products"]["SOLUSD"]["margin_and_limits"]
        assert set(limits) >= {"initial_margin", "maintenance_margin",
                               "default_leverage", "max_leverage_notional"}
        assert "SOLUSD" not in snapshot["unverified"]
        assert snapshot["products"]["SOLUSD"]["recorded_not_hashed"] == \
            sorted(limits)


# ---------------------------------------------------------------------------
# §E  Into the existing fail-closed schema; anything unverified still refuses.
# ---------------------------------------------------------------------------
class TestFailsClosedOnUnverifiedContractValues:
    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_a_snapshot_backed_spec_is_verified_and_carries_provenance(
            self, snapshot, symbol):
        spec = contract_spec_from_snapshot(snapshot, symbol)
        assert spec.is_verified
        assert spec.contract_value is not UNVERIFIED
        assert spec.require_verified() == float(
            Decimal(AUTHORITATIVE[symbol]["contract_value"]))
        assert "api.india.delta.exchange/v2/products/" in spec.verification_source
        assert PINNED_SHA256[symbol] in spec.verification_source
        assert spec.verified_at is not None

    def test_a_registry_built_from_the_snapshot_verifies_all_four(
            self, snapshot):
        registry = ContractSpecRegistry({
            s: contract_spec_from_snapshot(snapshot, s) for s in AUTHORITATIVE})
        assert set(registry.symbols) == set(MANUAL_SMC_SYMBOLS)
        assert all(registry.is_verified(s) for s in MANUAL_SMC_SYMBOLS)

    def test_the_default_registry_is_still_unverified_until_it_is_wired(self):
        """
        The snapshot exists but nothing in production consumes it yet, so the
        package default must still be UNVERIFIED — not quietly populated.
        """
        registry = ContractSpecRegistry.default()
        for symbol in MANUAL_SMC_SYMBOLS:
            spec = registry.get(symbol)
            assert not spec.is_verified
            assert spec.contract_value is UNVERIFIED
            with pytest.raises(ContractValueUnverifiedError):
                spec.require_verified()

    def test_a_symbol_missing_from_the_snapshot_cannot_become_verified(
            self, snapshot):
        assert "DOGEUSD" not in snapshot["products"]
        with pytest.raises(UnknownSymbolError):
            contract_spec_from_snapshot(snapshot, "DOGEUSD")
        with pytest.raises(ContractValueUnverifiedError):
            ContractSpec(symbol="DOGEUSD").require_verified()

    def test_a_number_without_provenance_is_rejected_outright(self):
        with pytest.raises(ContractValueUnverifiedError):
            ContractSpec(symbol="BTCUSD", contract_value=0.001)


# ---------------------------------------------------------------------------
# §F  No unknown symbol may resolve to BTCUSD / product 27.
# ---------------------------------------------------------------------------
class TestNoUnknownSymbolBecomesBTCUSD:
    @pytest.mark.parametrize("symbol", UNKNOWN_SYMBOLS)
    def test_an_unrecognised_symbol_raises_instead_of_resolving(
            self, snapshot, symbol):
        with pytest.raises(UnknownSymbolError) as excinfo:
            lookup_contract_spec(snapshot, symbol)
        assert "refusing to substitute another product" in str(excinfo.value)

    @pytest.mark.parametrize("symbol", UNKNOWN_SYMBOLS)
    def test_a_registry_built_from_the_snapshot_also_refuses(
            self, snapshot, symbol):
        registry = ContractSpecRegistry({
            s: contract_spec_from_snapshot(snapshot, s) for s in AUTHORITATIVE})
        with pytest.raises(UnknownSymbolError):
            registry.get(symbol)

    def test_product_27_belongs_to_btcusd_and_to_nothing_else(self, snapshot):
        owners = [s for s, r in snapshot["products"].items()
                  if r["contract_spec"]["id"] == 27]
        assert owners == ["BTCUSD"]
        ids = {r["contract_spec"]["id"] for r in snapshot["products"].values()}
        assert len(ids) == len(snapshot["products"]), "duplicate product ids"

    def test_the_lookup_has_no_normalising_or_fallback_behaviour(
            self, snapshot):
        """
        The three ways the legacy execution helper reaches BTCUSD/27 —
        upper-casing, stripping a `.P` suffix, and an unconditional default
        record — must all be absent here.
        """
        for symbol in ("btcusd", "BTCUSD.P", " BTCUSD "):
            with pytest.raises(UnknownSymbolError):
                lookup_contract_spec(snapshot, symbol)
        source = Path(__file__).read_text(encoding="utf-8")
        body = source.split("def lookup_contract_spec")[1].split("def provenance")[0]
        for banned in (".upper()", ".strip()", 'replace(".P"', "product_id=27"):
            assert banned not in body, banned
