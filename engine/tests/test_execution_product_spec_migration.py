"""
Execution product specs come from the shared registry — migration contract.
===========================================================================

`quantedge.execution.validation` used to carry its own eight-row product table
(four Delta-native symbols plus four `.P` forms) with product ids, tick sizes
and a flat `contract_value = 1.0` written in Python. That table is gone. This
file pins what replaced it:

  * the shipped table is DERIVED from `quantedge.instruments`, and no product
    id, tick size or contract value survives as a literal in `validation.py`;
  * the verified trio (`product_id`, `tick_size`, `contract_value`) equals the
    authoritative snapshot exactly, as `Decimal` — SOL's tick is 0.0001, not
    the stale 0.01;
  * an unrecognised symbol RAISES instead of being answered with fabricated
    BTCUSD/product-27 metadata (safety rules #8, #15);
  * `min_size`, `size_step` and `max_leverage` are NOT exchange facts. They
    are unchanged local policy, explicitly labelled, and the registry refuses
    to supply them;
  * the dependency direction is `execution -> instruments` and never back.

What this file deliberately does NOT assert: any notional->contracts
conversion, any `.P` alias, or any new leverage / minimum-size semantics.
Those remain unverified, and a test that pinned them would be inventing
exchange behaviour.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import fields
from decimal import Decimal

import pytest

import quantedge.instruments as instruments_pkg
from quantedge.execution import validation as validation_module
from quantedge.execution.leverage import MAX_LEVERAGE, MIN_LEVERAGE
from quantedge.execution.validation import (
    DEFAULT_DELTA_INDIA_PRODUCTS,
    UNVERIFIED_MAX_LEVERAGE,
    UNVERIFIED_MAX_LEVERAGE_FALLBACK,
    UNVERIFIED_MIN_SIZE,
    UNVERIFIED_SIZE_STEP,
    ProductSpecification,
    UnknownProductError,
    get_product_specification,
    product_specification_from_instrument,
)
from quantedge.instruments import FieldUnverifiedError, delta_india_registry

#: symbol -> (product_id, tick_size, contract_value) per the checked-in
#: authoritative Delta India snapshot. Pinned here because these are the
#: assertion, not a source of truth for production code.
AUTHORITATIVE = {
    "BTCUSD": (27, "0.5", "0.001"),
    "ETHUSD": (3136, "0.05", "0.01"),
    "SOLUSD": (14823, "0.0001", "1"),
    "XRPUSD": (14969, "0.0001", "1"),
}

#: None of these may appear as a literal in `validation.py`: each is exchange
#: data that must arrive from the snapshot.
FORBIDDEN_LITERALS = {"27", "3136", "14823", "14969",
                      "0.5", "0.05", "0.0001", "0.001", "0.01"}

#: Every one of these must fail closed. They are NOT normalised, stripped,
#: upper-cased or de-suffixed on the way in.
MUST_FAIL = ("FOOUSD", "BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P",
             "btcusd", "BtcUsd", "BTC-USD", "BTC/USD", "BTCUSDT", "BTC",
             " BTCUSD", "BTCUSD ", " BTCUSD ", "BTCUSD\n", "BTCUSD.p",
             "", None, 27, 0.001, b"BTCUSD", ("BTCUSD",))

#: Method names that would mean the lookup normalises its argument.
NORMALISERS = {"upper", "lower", "casefold", "title", "strip", "lstrip",
               "rstrip", "removesuffix", "removeprefix", "replace"}

VALIDATION_PY = pathlib.Path(validation_module.__file__)
EXECUTION_DIR = VALIDATION_PY.parent
INSTRUMENTS_DIR = pathlib.Path(instruments_pkg.__file__).parent


def _parsed(path: pathlib.Path) -> ast.Module:
    """Parse a module with every docstring removed, so prose cannot fail a scan."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:]
    return tree


def _code_constants(path: pathlib.Path) -> dict:
    """value-as-string -> line numbers, for every non-docstring constant."""
    out: dict = {}
    for node in ast.walk(_parsed(path)):
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float, str))
                and not isinstance(node.value, bool)):
            out.setdefault(str(node.value), []).append(node.lineno)
    return out


def _module_imports(path: pathlib.Path) -> set:
    """Every module name imported by `path`, dotted and un-normalised."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _function_def(path: pathlib.Path, name: str) -> ast.FunctionDef:
    for node in ast.walk(_parsed(path)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in {path.name}")


@pytest.fixture(scope="module")
def registry():
    return delta_india_registry()


# ═════════════════════════════════════════════════════════════════════════════
# A. THE SHIPPED TABLE IS DERIVED, NOT DUPLICATED
# ═════════════════════════════════════════════════════════════════════════════
def test_the_table_holds_exactly_the_registry_symbols(registry):
    assert sorted(DEFAULT_DELTA_INDIA_PRODUCTS) == sorted(registry.symbols)
    assert sorted(DEFAULT_DELTA_INDIA_PRODUCTS) == sorted(AUTHORITATIVE)


def test_the_dot_p_rows_are_gone():
    """The old table carried four `.P` duplicates. Aliasing is undecided."""
    assert not [s for s in DEFAULT_DELTA_INDIA_PRODUCTS if s.endswith(".P")]
    assert len(DEFAULT_DELTA_INDIA_PRODUCTS) == 4


def test_validation_hardcodes_no_product_value():
    """
    Not one product id, tick size or contract value may be written in the
    module that used to own the table. Docstrings are stripped first, so the
    prose explaining the old BTCUSD/27 fallback does not trip the scan.
    """
    found = _code_constants(VALIDATION_PY)
    leaked = {value: found[value] for value in FORBIDDEN_LITERALS
              if value in found}
    assert leaked == {}, f"exchange data hardcoded in validation.py: {leaked}"


def test_the_only_spec_construction_site_is_the_adapter():
    """
    A second `ProductSpecification(...)` anywhere in `execution` would be a
    second source of truth. Production code may build one place only.
    """
    sites = []
    for path in sorted(EXECUTION_DIR.rglob("*.py")):
        for node in ast.walk(_parsed(path)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "ProductSpecification"):
                sites.append((path.name, node.lineno))
    assert len(sites) == 1, f"expected one construction site, got {sites}"
    name, lineno = sites[0]
    adapter = _function_def(VALIDATION_PY,
                            "product_specification_from_instrument")
    assert name == VALIDATION_PY.name
    assert adapter.lineno <= lineno <= adapter.end_lineno


def test_every_shipped_spec_carries_snapshot_provenance(registry):
    for symbol, spec in DEFAULT_DELTA_INDIA_PRODUCTS.items():
        assert spec.is_verified, f"{symbol} shipped without provenance"
        source = spec.verification_source
        assert source == registry.get(symbol).provenance.as_source_string()
        assert "sha256=" in source and symbol in source


def test_a_locally_built_spec_is_not_presented_as_an_exchange_record():
    """Default construction still works (fixtures need it) but claims nothing."""
    local = ProductSpecification(symbol="ZZZUSD", product_id=1,
                                 min_size=Decimal("1"), size_step=Decimal("1"),
                                 tick_size=Decimal("0"))
    assert local.verification_source is None
    assert local.is_verified is False


# ═════════════════════════════════════════════════════════════════════════════
# B. THE VERIFIED TRIO
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("symbol,expected", sorted(AUTHORITATIVE.items()))
def test_the_verified_trio_matches_the_snapshot(symbol, expected, registry):
    product_id, tick, contract_value = expected
    spec = get_product_specification(symbol)
    instrument = registry.get(symbol)

    assert spec.product_id == product_id == instrument.product_id
    assert spec.tick_size == Decimal(tick) == instrument.tick_size
    assert spec.contract_value == Decimal(contract_value) \
        == instrument.contract_value


def test_sol_tick_size_is_authoritative():
    """The pre-migration table said 0.01. The exchange says 0.0001."""
    assert get_product_specification("SOLUSD").tick_size == Decimal("0.0001")
    assert get_product_specification("SOLUSD").tick_size != Decimal("0.01")


def test_contract_values_are_no_longer_a_flat_one():
    """
    The old table gave every symbol `contract_value = 1.0`. BTCUSD is 0.001
    and ETHUSD is 0.01, so risk and notional arithmetic was overstated by
    1000x and 100x respectively — permissively, in the unsafe direction.
    """
    assert get_product_specification("BTCUSD").contract_value == Decimal("0.001")
    assert get_product_specification("ETHUSD").contract_value == Decimal("0.01")


@pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
def test_exchange_constants_never_cross_a_float(symbol):
    spec = get_product_specification(symbol)
    assert isinstance(spec.tick_size, Decimal)
    assert isinstance(spec.contract_value, Decimal)
    assert isinstance(spec.product_id, int)


@pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
def test_repeated_lookups_return_the_same_shipped_object(symbol):
    assert get_product_specification(symbol) is \
        DEFAULT_DELTA_INDIA_PRODUCTS[symbol]


# ═════════════════════════════════════════════════════════════════════════════
# C. UNKNOWN SYMBOLS FAIL CLOSED — NO FABRICATED BTCUSD
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("symbol", MUST_FAIL, ids=repr)
def test_an_unrecognised_symbol_raises(symbol):
    with pytest.raises(UnknownProductError):
        get_product_specification(symbol)


@pytest.mark.parametrize("symbol", MUST_FAIL, ids=repr)
def test_no_unrecognised_symbol_yields_btcusd_metadata(symbol):
    """The old fallback answered anything with BTCUSD / product 27 / tick 0.5."""
    try:
        spec = get_product_specification(symbol)
    except UnknownProductError:
        return
    pytest.fail(f"{symbol!r} fabricated {spec.symbol}/{spec.product_id}")


def test_the_lookup_does_no_normalising():
    """
    Behaviour above proves the refusals; this proves the mechanism. No
    case-folding, trimming or `.P` stripping exists in the lookup at all, so
    a near-miss symbol cannot become a different tradable product.
    """
    lookup = _function_def(VALIDATION_PY, "get_product_specification")
    used = {node.func.attr for node in ast.walk(lookup)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not (used & NORMALISERS), f"lookup normalises via {used & NORMALISERS}"


def test_the_table_itself_has_no_forgiving_default():
    for symbol in ("BTCUSD.P", "btcusd", "FOOUSD", ""):
        assert DEFAULT_DELTA_INDIA_PRODUCTS.get(symbol) is None


def test_the_refusal_is_a_runtime_error():
    """
    `trade_lifecycle`'s kill-switch and close paths wrap product lookup in
    `except Exception`. A RuntimeError subclass keeps those paths intact
    instead of escaping as something they do not catch.
    """
    assert issubclass(UnknownProductError, RuntimeError)
    assert issubclass(UnknownProductError, Exception)


def test_the_refusal_names_what_was_registered():
    with pytest.raises(UnknownProductError) as excinfo:
        get_product_specification("BTCUSD.P")
    message = str(excinfo.value)
    assert "BTCUSD.P" in message
    assert all(symbol in message for symbol in AUTHORITATIVE)


# ═════════════════════════════════════════════════════════════════════════════
# D. WHAT IS STILL UNVERIFIED STAYS UNVERIFIED
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
def test_the_unverified_fields_are_named_on_every_shipped_spec(symbol):
    spec = get_product_specification(symbol)
    assert set(spec.unverified_fields) == {"min_size", "size_step",
                                          "max_leverage"}


@pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
def test_the_registry_refuses_the_three_unverified_fields(symbol, registry):
    """
    Proof that execution's values are local policy: the exchange snapshot has
    nothing to say about any of them.
    """
    instrument = registry.get(symbol)
    for field in ("minimum_order_size", "size_step", "max_leverage"):
        with pytest.raises(FieldUnverifiedError):
            getattr(instrument, field)
        assert instrument.is_verified(field) is False


@pytest.mark.parametrize("symbol", ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"])
def test_no_gateway_quantity_bound_was_loosened(symbol):
    """
    The pre-registry gateway used min_size 1 and size_step 1. Both are retained
    verbatim, so no quantity check became more permissive in this migration.

    Leverage is asserted separately below: that bound DID move, deliberately.
    """
    spec = get_product_specification(symbol)
    assert spec.min_size == Decimal("1") == UNVERIFIED_MIN_SIZE
    assert spec.size_step == Decimal("1") == UNVERIFIED_SIZE_STEP


@pytest.mark.parametrize("symbol", ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"])
def test_the_leverage_cap_is_the_authorised_band_on_every_symbol(symbol):
    """
    The pre-registry gateway capped SOL and XRP at 50x and BTC and ETH at 100x.
    That per-symbol split was raised to a uniform `MAX_LEVERAGE` on the owner's
    explicit authorisation: a requested 100x was otherwise rejected on two of
    the four symbols while every other layer accepted it.

    Corroborated, not verified. `max_leverage` remains in
    `PERMANENTLY_UNVERIFIED`; the snapshot's recorded `default_leverage` is 100
    for SOLUSD/XRPUSD and 200 for BTCUSD/ETHUSD, so 100 is still no looser than
    a figure Delta itself records.
    """
    spec = get_product_specification(symbol)
    assert spec.max_leverage == MAX_LEVERAGE == UNVERIFIED_MAX_LEVERAGE[symbol]
    assert "max_leverage" in spec.unverified_fields


def test_an_unlisted_symbol_would_get_the_strictest_cap():
    assert UNVERIFIED_MAX_LEVERAGE_FALLBACK == min(UNVERIFIED_MAX_LEVERAGE.values())
    assert UNVERIFIED_MAX_LEVERAGE_FALLBACK == MAX_LEVERAGE


def test_no_quantity_semantics_were_invented():
    """
    A verified `contract_value` does not make an order quantity computable.
    The gateway schema still carries no quantity, contract-count or converter
    field, and nothing in it encodes a notional->contracts formula.
    """
    names = {f.name for f in fields(ProductSpecification)}
    assert names == {"symbol", "product_id", "min_size", "size_step",
                     "tick_size", "max_leverage", "contract_value",
                     "verification_source", "unverified_fields"}
    assert not [n for n in names if "quant" in n or "convert" in n]


def test_the_registrys_conversion_formula_is_still_closed(registry):
    with pytest.raises(FieldUnverifiedError):
        registry.get("BTCUSD").notional_to_contracts(1000.0)


# ═════════════════════════════════════════════════════════════════════════════
# E. THE ADAPTER
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("symbol", sorted(AUTHORITATIVE))
def test_the_adapter_copies_the_verified_trio_exactly(symbol, registry):
    instrument = registry.get(symbol)
    adapted = product_specification_from_instrument(instrument)

    assert adapted.symbol == instrument.symbol
    assert adapted.product_id == instrument.product_id
    assert adapted.tick_size == instrument.tick_size
    assert adapted.contract_value == instrument.contract_value
    assert adapted == DEFAULT_DELTA_INDIA_PRODUCTS[symbol]


def test_the_adapter_writes_no_exchange_literal_of_its_own():
    adapter = _function_def(VALIDATION_PY, "product_specification_from_instrument")
    literals = {str(node.value) for node in ast.walk(adapter)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float, str))}
    assert not (literals & FORBIDDEN_LITERALS)


# ═════════════════════════════════════════════════════════════════════════════
# F. DEPENDENCY DIRECTION: execution -> instruments, never back
# ═════════════════════════════════════════════════════════════════════════════
def test_execution_validation_imports_the_shared_registry():
    imported = _module_imports(VALIDATION_PY)
    assert "quantedge.instruments" in imported


def test_the_registry_imports_neither_execution_nor_strategy():
    """
    `quantedge.instruments` is a leaf. (A process-level proof is impossible:
    `quantedge/__init__.py` eagerly imports `execution`, so importing anything
    under `quantedge` pulls it in. The import graph is the real contract.)
    """
    offenders = {}
    for path in sorted(INSTRUMENTS_DIR.rglob("*.py")):
        bad = {name for name in _module_imports(path)
               if name.startswith(("quantedge.execution",
                                   "quantedge.strategy"))}
        if bad:
            offenders[path.name] = sorted(bad)
    assert offenders == {}, f"registry reached back into callers: {offenders}"


def test_the_registry_names_no_caller_even_dynamically():
    """
    Closes the `importlib.import_module("quantedge.execution")` loophole that
    an import-statement scan alone would miss. Docstrings are stripped, so the
    package's own prose about the dependency direction is not a violation.
    """
    offenders = {}
    for path in sorted(INSTRUMENTS_DIR.rglob("*.py")):
        bad = {value for value in _code_constants(path)
               if "quantedge.execution" in value or "manual_smc" in value}
        if bad:
            offenders[path.name] = sorted(bad)
    assert offenders == {}, f"registry code references callers: {offenders}"


# ═════════════════════════════════════════════════════════════════════════════
# G. CALLERS
# ═════════════════════════════════════════════════════════════════════════════
def test_the_public_execution_surface_exports_the_migration():
    import quantedge.execution as execution

    for name in ("ProductSpecification", "DEFAULT_DELTA_INDIA_PRODUCTS",
                 "UnknownProductError", "get_product_specification",
                 "product_specification_from_instrument"):
        assert name in execution.__all__
        assert getattr(execution, name) is not None


def test_the_orchestrators_lot_lookup_stays_none_tolerant():
    """
    `market_orchestrator` reads the table with `.get(...)` and falls back to
    Decimal("1") when a symbol is absent. The `.P` rows it used to find gave
    min_size 1 / size_step 1 — identical to that fallback, so removing them
    changed no sizing bound.
    """
    assert DEFAULT_DELTA_INDIA_PRODUCTS.get("BTCUSD.P") is None
    assert UNVERIFIED_MIN_SIZE == Decimal("1")
    assert UNVERIFIED_SIZE_STEP == Decimal("1")
