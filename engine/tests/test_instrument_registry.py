"""
`quantedge.instruments` — the shared instrument registry.
=========================================================

The snapshot test file next door pins the ARTIFACT. This file pins the
PRODUCTION READER of that artifact:

    engine/src/quantedge/instruments/{models,registry}.py

What is asserted here:

  §A  Loading — all four Delta India products, with the ids, tick sizes,
      contract values and currencies the exchange itself returned.
  §B  Provenance — every spec names its endpoint, retrieval time and content
      hash, and no product value is hardcoded in the package: the registry
      follows the file, it does not remember the numbers.
  §C  Fail closed — unknown, lower-cased, dashed, `.P`-suffixed, padded,
      empty and non-string symbols all raise. Nothing becomes BTCUSD.
  §D  Still unverified — `minimum_order_size`, `size_step`, `max_leverage`
      and the notional->contracts formula refuse, even though
      `contract_value` is verified. That boundary is deliberate.
  §E  Exactness — Decimal from the exchange's own strings; a float in the
      snapshot is refused outright.
  §F  Integrity — an edited snapshot, a foreign exchange, a foreign endpoint
      or a snapshot that stopped declaring its unverified fields all refuse
      to load rather than degrade.
  §G  Alias policy — empty by default, expressible only as explicit data,
      validated when supplied.
  §H  Boundary — proven by AST, because a process-level proof is impossible:
      `quantedge/__init__.py` eagerly imports `quantedge.execution`, so
      importing ANY `quantedge.*` module pulls execution into `sys.modules`
      regardless of what this package does. The AST proofs therefore assert
      what the source itself imports, plus a runtime proof that loading the
      registry performs no network I/O.
  §I  Injection — a caller reads a verified spec here and injects it into
      `manual_smc`; the order quantity stays refused.

No network access, and no second copy of the four product values.
"""

from __future__ import annotations

import ast
import hashlib
import json
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from quantedge.instruments import (
    NO_ALIASES,
    PERMANENTLY_UNVERIFIED,
    FieldUnverifiedError,
    InstrumentRegistry,
    InstrumentSpec,
    SnapshotIntegrityError,
    SnapshotUnavailableError,
    UnknownInstrumentError,
    delta_india_registry,
    load_delta_india_registry,
)

from quantedge.strategy.manual_smc.sizing import (
    ContractSpec,
    PositionSizing,
    QuantitySemanticsUnverifiedError,
    resolve_order_quantity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = (REPO_ROOT / "data" / "reference" / "delta_exchange_india"
                 / "product_specs_snapshot.json")
PKG_DIR = REPO_ROOT / "engine" / "src" / "quantedge" / "instruments"
MANUAL_SMC_DIR = (REPO_ROOT / "engine" / "src" / "quantedge" / "strategy"
                  / "manual_smc")

#: (product_id, tick_size, contract_value, contract_unit_currency), as the
#: exchange returned them. Independent of the package under test.
AUTHORITATIVE = {
    "BTCUSD": (27, "0.5", "0.001", "BTC"),
    "ETHUSD": (3136, "0.05", "0.01", "ETH"),
    "SOLUSD": (14823, "0.0001", "1", "SOL"),
    "XRPUSD": (14969, "0.0001", "1", "XRP"),
}

PINNED_SHA256 = {
    "BTCUSD": "8eb7d511aebac0ebdd1b27938c432ef0d9f53b78ceaaab5778107059e5ec2ab5",
    "ETHUSD": "1c13b0968676ee422e5b5edc1735ec9ae3db50b2638789e32fe2b988d8b9defb",
    "SOLUSD": "becb5595f257379fd2bb3a645b12032fed0c713257cd6ce2a73cc6dacffaa34b",
    "XRPUSD": "9164cbf960465d81fdc330ca906ecd2b96950699ea5e07a0d32297dae1450b8d",
}

#: Every one of these must raise. None of them may become BTCUSD.
BAD_SYMBOLS = ("FOOUSD", "BTCUSD.P", "btcusd", "BTC-USD", "BTCUSDT",
               " BTCUSD ", "BTCUSD\n", "", None, 27, 0.001, b"BTCUSD",
               ["BTCUSD"])

#: Values that must not appear as literals in the package source.
FORBIDDEN_LITERALS = {"0.001", "0.01", "0.05", "0.0001", "0.5",
                      "27", "3136", "14823", "14969"}

NETWORK_MODULE_PREFIXES = ("socket", "ssl", "http", "urllib", "requests",
                           "httpx", "aiohttp", "websocket", "websockets",
                           "ftplib", "telnetlib", "smtplib", "xmlrpc")

# ---------------------------------------------------------------------------
# Helpers. The hashing convention is recomputed here rather than imported, so
# a change to the package's own hashing would not silently satisfy the tests.
# ---------------------------------------------------------------------------
def canonical_sha256(block) -> str:
    canonical = json.dumps(block, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def raw_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def write_snapshot(tmp_path: Path, snap: dict, rehash: bool = False) -> Path:
    """Persist a mutated snapshot. `rehash` makes the edit self-consistent."""
    if rehash:
        for record in snap["products"].values():
            record["pinned_sha256"] = canonical_sha256(record["contract_spec"])
    path = tmp_path / "product_specs_snapshot.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    return path


def module_imports(path: Path) -> set:
    """Every module name the file imports, by AST — not by text search."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def code_literals(path: Path) -> set:
    """
    Constants in EXECUTABLE code, with docstrings stripped. Comments never
    reach the AST, so prose mentioning 0.001 or product 27 is exempt while an
    actual hardcoded value is not.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:]
    return {str(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float, str))
            and not isinstance(n.value, bool)}


def package_files() -> list:
    return sorted(PKG_DIR.glob("*.py"))


@pytest.fixture(scope="module")
def registry() -> InstrumentRegistry:
    return load_delta_india_registry()

# ---------------------------------------------------------------------------
# §A  Loading the checked-in snapshot.
# ---------------------------------------------------------------------------
class TestSnapshotLoading:
    def test_it_loads_exactly_the_four_manual_smc_products(self, registry):
        assert registry.symbols == tuple(sorted(AUTHORITATIVE))

    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_the_verified_fields_are_the_authoritative_ones(
            self, registry, symbol):
        product_id, tick, contract_value, unit = AUTHORITATIVE[symbol]
        spec = registry.get(symbol)
        assert spec.symbol == symbol
        assert spec.product_id == product_id
        assert spec.tick_size == Decimal(tick)
        assert spec.contract_value == Decimal(contract_value)
        assert spec.contract_unit_currency == unit
        assert spec.underlying_asset == unit
        assert spec.notional_type == "vanilla"
        assert spec.contract_type == "perpetual_futures"
        assert spec.quoting_asset == "USD"
        assert spec.settling_asset == "USD"

    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_require_reads_verified_fields_by_name(self, registry, symbol):
        spec = registry.get(symbol)
        assert spec.require("product_id") == AUTHORITATIVE[symbol][0]
        assert spec.require("tick_size") == Decimal(AUTHORITATIVE[symbol][1])
        for name in InstrumentSpec.VERIFIED_FIELDS:
            assert spec.is_verified(name)

    def test_one_contract_states_its_base_asset_meaning(self, registry):
        assert registry.get("BTCUSD").one_contract_description == \
            "1 BTCUSD contract = 0.001 BTC"

    def test_recorded_margin_fields_are_kept_but_read_only(self, registry):
        recorded = registry.get("SOLUSD").recorded
        assert {"initial_margin", "maintenance_margin", "default_leverage",
                "state"} <= set(recorded)
        with pytest.raises(TypeError):
            recorded["initial_margin"] = "0"

    def test_the_process_wide_registry_is_built_once(self):
        assert delta_india_registry() is delta_india_registry()
        assert delta_india_registry().aliases == NO_ALIASES

    def test_a_missing_snapshot_refuses_rather_than_defaulting(self, tmp_path):
        with pytest.raises(SnapshotUnavailableError):
            load_delta_india_registry(tmp_path / "absent.json")

# ---------------------------------------------------------------------------
# §B  Provenance, and no second hardcoded table.
# ---------------------------------------------------------------------------
class TestProvenance:
    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_each_spec_names_where_its_values_came_from(self, registry, symbol):
        prov = registry.get(symbol).provenance
        assert prov.endpoint == f"/v2/products/{symbol}"
        assert prov.source_url == \
            f"https://api.india.delta.exchange/v2/products/{symbol}"
        assert prov.pinned_sha256 == PINNED_SHA256[symbol]
        assert prov.snapshot_path.endswith("product_specs_snapshot.json")
        assert prov.snapshot_version
        assert prov.http_date
        assert prov.retrieved_at.tzinfo is not None

    def test_the_injectable_source_string_carries_url_time_and_hash(
            self, registry):
        line = registry.get("ETHUSD").provenance.as_source_string()
        assert "api.india.delta.exchange/v2/products/ETHUSD" in line
        assert "retrieved_at=" in line
        assert PINNED_SHA256["ETHUSD"] in line

    @pytest.mark.parametrize("path", package_files(), ids=lambda p: p.name)
    def test_no_product_value_is_hardcoded_in_the_package(self, path):
        """
        Prose may mention 0.001 or product 27; executable code may not. The
        snapshot is the only place these numbers exist.
        """
        offenders = FORBIDDEN_LITERALS & code_literals(path)
        assert not offenders, f"{path.name} hardcodes {sorted(offenders)}"

    def test_the_registry_follows_the_file_instead_of_remembering_values(
            self, tmp_path):
        """
        A self-consistent edit to the snapshot must change what the registry
        reports. If it did not, the package would be holding its own copy.
        """
        snap = raw_snapshot()
        snap["products"]["BTCUSD"]["contract_spec"]["contract_value"] = "0.002"
        loaded = load_delta_india_registry(
            write_snapshot(tmp_path, snap, rehash=True))
        assert loaded.get("BTCUSD").contract_value == Decimal("0.002")
        assert load_delta_india_registry().get("BTCUSD").contract_value == \
            Decimal("0.001")

# ---------------------------------------------------------------------------
# §C  Fail closed. Nothing resolves to BTCUSD / product 27.
# ---------------------------------------------------------------------------
class TestFailsClosedOnUnknownSymbols:
    @pytest.mark.parametrize("symbol", BAD_SYMBOLS, ids=repr)
    def test_an_unrecognised_symbol_raises(self, registry, symbol):
        with pytest.raises(UnknownInstrumentError) as excinfo:
            registry.get(symbol)
        assert "refusing to substitute another product" in str(excinfo.value)

    @pytest.mark.parametrize("symbol", BAD_SYMBOLS, ids=repr)
    def test_membership_is_false_and_never_falls_back(self, registry, symbol):
        assert symbol not in registry

    def test_the_lookup_does_no_normalising(self, registry):
        """
        Upper-casing, stripping and `.P` removal are absent from the lookup
        module — proven structurally, then behaviourally.
        """
        tree = ast.parse((PKG_DIR / "registry.py").read_text(encoding="utf-8"))
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        assert not called & {"upper", "lower", "casefold", "strip", "lstrip",
                            "rstrip", "removesuffix", "replace"}
        for symbol in ("btcusd", "BTCUSD.P", " BTCUSD "):
            with pytest.raises(UnknownInstrumentError):
                registry.get(symbol)

    def test_no_symbol_outside_the_snapshot_acquires_product_27(self, registry):
        owners = [s for s in registry.symbols if registry.get(s).product_id == 27]
        assert owners == ["BTCUSD"]
        ids = {registry.get(s).product_id for s in registry.symbols}
        assert len(ids) == len(registry.symbols)

    def test_a_snapshot_with_no_products_refuses_to_load(self, tmp_path):
        snap = raw_snapshot()
        snap["products"] = {}
        with pytest.raises(SnapshotIntegrityError):
            load_delta_india_registry(write_snapshot(tmp_path, snap))

# ---------------------------------------------------------------------------
# §D  A verified contract value does NOT make a quantity computable.
# ---------------------------------------------------------------------------
class TestWhatStaysUnverified:
    def test_the_four_unpublished_names_are_the_declared_set(self):
        assert PERMANENTLY_UNVERIFIED == {
            "minimum_order_size", "size_step", "max_leverage",
            "notional_to_contracts_formula"}

    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    @pytest.mark.parametrize("name", ["minimum_order_size", "size_step",
                                      "max_leverage"])
    def test_every_unpublished_accessor_refuses(self, registry, symbol, name):
        spec = registry.get(symbol)
        with pytest.raises(FieldUnverifiedError) as excinfo:
            getattr(spec, name)
        assert "UNVERIFIED" in str(excinfo.value)
        with pytest.raises(FieldUnverifiedError):
            spec.require(name)
        assert not spec.is_verified(name)

    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_the_notional_to_contracts_conversion_stays_refused(
            self, registry, symbol):
        """
        The intentional safety boundary: `contract_value` is verified and the
        conversion still is not, because Delta publishes no formula.
        """
        spec = registry.get(symbol)
        assert spec.contract_value == Decimal(AUTHORITATIVE[symbol][2])
        with pytest.raises(FieldUnverifiedError):
            spec.notional_to_contracts(10_000.0)
        with pytest.raises(FieldUnverifiedError):
            spec.notional_to_contracts()

    def test_each_unverified_name_says_why(self, registry):
        unverified = registry.get("BTCUSD").unverified
        assert PERMANENTLY_UNVERIFIED <= set(unverified)
        for name in PERMANENTLY_UNVERIFIED:
            assert len(unverified[name]) > 20, name

    def test_an_unknown_field_name_is_not_quietly_verified(self, registry):
        with pytest.raises(FieldUnverifiedError):
            registry.get("BTCUSD").require("lot_size")

    def test_a_snapshot_that_stopped_declaring_them_refuses_to_load(
            self, tmp_path):
        snap = raw_snapshot()
        snap["unverified"].pop("max_leverage")
        with pytest.raises(SnapshotIntegrityError) as excinfo:
            load_delta_india_registry(write_snapshot(tmp_path, snap))
        assert "max_leverage" in str(excinfo.value)

# ---------------------------------------------------------------------------
# §E  Exact values only. A float never becomes an exchange constant.
# ---------------------------------------------------------------------------
class TestDecimalExactness:
    @pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
    def test_tick_size_and_contract_value_are_exact_decimals(
            self, registry, symbol):
        spec = registry.get(symbol)
        assert isinstance(spec.tick_size, Decimal)
        assert isinstance(spec.contract_value, Decimal)
        assert str(spec.tick_size) == AUTHORITATIVE[symbol][1]
        assert str(spec.contract_value) == AUTHORITATIVE[symbol][2]

    def test_the_solusd_tick_is_the_authoritative_one_not_the_legacy_value(
            self, registry):
        """
        Both pre-existing hardcoded tables carried 0.01 for SOLUSD. The
        exchange returns 0.0001.
        """
        assert registry.get("SOLUSD").tick_size == Decimal("0.0001")
        assert registry.get("SOLUSD").tick_size != Decimal("0.01")

    def test_the_contract_values_are_not_the_one_point_zero_placeholder(
            self, registry):
        values = {s: registry.get(s).contract_value for s in registry.symbols}
        assert values["BTCUSD"] == Decimal("0.001")
        assert values["ETHUSD"] == Decimal("0.01")
        assert len(set(values.values())) > 1

    @pytest.mark.parametrize("name", ["tick_size", "contract_value"])
    def test_a_float_in_the_snapshot_is_refused_outright(self, tmp_path, name):
        snap = raw_snapshot()
        snap["products"]["BTCUSD"]["contract_spec"][name] = 0.001
        with pytest.raises(SnapshotIntegrityError) as excinfo:
            load_delta_india_registry(write_snapshot(tmp_path, snap,
                                                     rehash=True))
        assert "float" in str(excinfo.value)

    def test_a_non_decimal_value_cannot_be_constructed_directly(self, registry):
        spec = registry.get("BTCUSD")
        with pytest.raises(SnapshotIntegrityError):
            InstrumentSpec(
                symbol="BTCUSD", product_id=27, tick_size=0.5,
                contract_value=Decimal("0.001"),
                contract_unit_currency="BTC", notional_type="vanilla",
                contract_type="perpetual_futures", underlying_asset="BTC",
                quoting_asset="USD", settling_asset="USD",
                provenance=spec.provenance, unverified=dict(spec.unverified))

    def test_an_unparseable_value_refuses(self, tmp_path):
        snap = raw_snapshot()
        snap["products"]["XRPUSD"]["contract_spec"]["tick_size"] = "not-a-number"
        with pytest.raises(SnapshotIntegrityError):
            load_delta_india_registry(write_snapshot(tmp_path, snap,
                                                     rehash=True))

# ---------------------------------------------------------------------------
# §F  Integrity. An edited snapshot refuses; it does not degrade.
# ---------------------------------------------------------------------------
class TestSnapshotIntegrity:
    def test_an_edit_without_a_fresh_fetch_is_detected(self, tmp_path):
        snap = raw_snapshot()
        snap["products"]["BTCUSD"]["contract_spec"]["contract_value"] = "1"
        with pytest.raises(SnapshotIntegrityError) as excinfo:
            load_delta_india_registry(write_snapshot(tmp_path, snap))
        assert "pinned_sha256" in str(excinfo.value)

    def test_a_relabelled_record_is_detected(self, tmp_path):
        snap = raw_snapshot()
        snap["products"]["ETHUSD"]["contract_spec"]["symbol"] = "BTCUSD"
        with pytest.raises(SnapshotIntegrityError):
            load_delta_india_registry(write_snapshot(tmp_path, snap,
                                                     rehash=True))

    @pytest.mark.parametrize("key,value", [
        ("exchange", "Delta Exchange"),
        ("source_base_url", "https://api.delta.exchange"),
        ("endpoint_template", "/v2/products"),
    ])
    def test_a_foreign_exchange_or_endpoint_refuses(self, tmp_path, key, value):
        snap = raw_snapshot()
        snap[key] = value
        with pytest.raises(SnapshotIntegrityError):
            load_delta_india_registry(write_snapshot(tmp_path, snap))

    def test_unreadable_json_refuses(self, tmp_path):
        path = tmp_path / "product_specs_snapshot.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(SnapshotIntegrityError):
            load_delta_india_registry(path)

    def test_provenance_cannot_be_faked_with_a_short_digest(self, registry):
        prov = registry.get("BTCUSD").provenance
        assert len(prov.pinned_sha256) == 64
        with pytest.raises(SnapshotIntegrityError):
            type(prov)(source_url=prov.source_url, endpoint=prov.endpoint,
                       retrieved_at=prov.retrieved_at,
                       http_date=prov.http_date, pinned_sha256="deadbeef",
                       snapshot_version=prov.snapshot_version,
                       snapshot_path=prov.snapshot_path)


# ---------------------------------------------------------------------------
# §G  Aliases are policy expressed as data — empty until a decision is made.
# ---------------------------------------------------------------------------
class TestAliasPolicy:
    def test_the_default_registry_has_no_aliases(self, registry):
        assert dict(registry.aliases) == {}
        assert dict(NO_ALIASES) == {}

    def test_dot_p_is_not_an_alias_by_default(self, registry):
        with pytest.raises(UnknownInstrumentError):
            registry.get("BTCUSD.P")

    def test_an_alias_only_works_when_explicitly_supplied_as_data(self):
        loaded = load_delta_india_registry(aliases={"BTCUSD.P": "BTCUSD"})
        assert loaded.get("BTCUSD.P").product_id == 27
        assert loaded.aliases == {"BTCUSD.P": "BTCUSD"}
        with pytest.raises(UnknownInstrumentError):
            loaded.get("ETHUSD.P")

    def test_a_dangling_alias_is_rejected(self):
        with pytest.raises(UnknownInstrumentError):
            load_delta_india_registry(aliases={"BTCUSD.P": "DOGEUSD"})

    def test_an_alias_may_not_shadow_a_native_symbol(self):
        with pytest.raises(UnknownInstrumentError):
            load_delta_india_registry(aliases={"BTCUSD": "ETHUSD"})

# ---------------------------------------------------------------------------
# §H  The dependency boundary, proven by AST (see this file's docstring for
#     why a process-level proof is not available).
# ---------------------------------------------------------------------------
class TestDependencyBoundary:
    @pytest.mark.parametrize("path", package_files(), ids=lambda p: p.name)
    def test_the_registry_imports_neither_execution_nor_manual_smc(self, path):
        imported = module_imports(path)
        offenders = {name for name in imported
                     if name.startswith("quantedge.execution")
                     or name.startswith("quantedge.strategy")}
        assert not offenders, f"{path.name} imports {sorted(offenders)}"

    @pytest.mark.parametrize("path", package_files(), ids=lambda p: p.name)
    def test_the_registry_imports_only_itself_from_quantedge(self, path):
        internal = {name for name in module_imports(path)
                    if name.startswith("quantedge")}
        assert all(name.startswith("quantedge.instruments")
                   for name in internal), sorted(internal)

    @pytest.mark.parametrize("path", package_files(), ids=lambda p: p.name)
    def test_the_registry_imports_no_network_client(self, path):
        imported = module_imports(path)
        offenders = {name for name in imported
                     if name.split(".")[0] in NETWORK_MODULE_PREFIXES}
        assert not offenders, f"{path.name} imports {sorted(offenders)}"

    @pytest.mark.parametrize(
        "path", sorted(MANUAL_SMC_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_manual_smc_stays_injection_fed(self, path):
        """
        `manual_smc` must not learn this package exists, and must not reach
        `execution` through it. A caller injects; the strategy never pulls.
        """
        imported = module_imports(path)
        assert not {n for n in imported if n.startswith("quantedge.instruments")}
        assert not {n for n in imported if n.startswith("quantedge.execution")}

    def test_loading_the_registry_performs_no_network_io(self, monkeypatch):
        def refuse(*_args, **_kwargs):
            raise AssertionError("the registry attempted network access")

        monkeypatch.setattr(socket, "socket", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)
        assert load_delta_india_registry().get("BTCUSD").product_id == 27

# ---------------------------------------------------------------------------
# §I  Injection into `manual_smc`, with the quantity boundary still closed.
# ---------------------------------------------------------------------------
def _sizing(asset: str = "BTCUSD") -> PositionSizing:
    """A non-degenerate sized trade. Numbers are illustrative, not oracle."""
    return PositionSizing(
        asset=asset, direction="LONG", entry_price=100.0, sl_price=99.0,
        tp_price=102.0, risk_dist=1.0, reward_dist=2.0, sl_dist_pct=1.0,
        theoretical_leverage=35.0, applied_leverage=35.0,
        account_balance=1000.0, margin_usd=1000.0, notional_usd=35000.0,
        fee_usd=28.0, gross_sl_return_pct=35.0, gross_tp_return_pct=21.0,
        leverage_clamped=False, degenerate_sl_distance=False)


class TestInjectionIntoManualSMC:
    def test_a_caller_can_build_a_verified_manual_smc_contract_spec(
            self, registry):
        spec = registry.get("BTCUSD")
        injected = ContractSpec(
            symbol=spec.symbol,
            contract_value=spec.contract_value,
            verification_source=spec.provenance.as_source_string(),
            verified_at=spec.provenance.retrieved_at)
        assert injected.is_verified
        assert injected.require_verified_exact() == Decimal("0.001")
        assert injected.require_verified() == 0.001
        assert PINNED_SHA256["BTCUSD"] in injected.verification_source

    def test_the_exact_accessor_never_crosses_a_float(self, registry):
        injected = ContractSpec(
            symbol="ETHUSD", contract_value=registry.get("ETHUSD").contract_value,
            verification_source=registry.get("ETHUSD").provenance
            .as_source_string())
        exact = injected.require_verified_exact()
        assert isinstance(exact, Decimal)
        assert str(exact) == "0.01"

    def test_a_verified_contract_value_still_yields_no_order_quantity(
            self, registry):
        """
        Safety rule #16 at the boundary: the value is verified, the semantics
        are not, so a quantity remains unobtainable without an explicit,
        injected converter.
        """
        spec = registry.get("BTCUSD")
        injected = ContractSpec(
            symbol=spec.symbol, contract_value=spec.contract_value,
            verification_source=spec.provenance.as_source_string())
        with pytest.raises(QuantitySemanticsUnverifiedError):
            resolve_order_quantity(_sizing(), injected)

