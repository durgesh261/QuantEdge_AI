"""
The Python execution layer's product-identity invariant, pinned as contract.

THE INVARIANT
    For every order the Python execution layer submits:

        payload["product_symbol"] == registry.get(payload["product_symbol"]).symbol
        payload["product_id"]     == registry.get(payload["product_symbol"]).product_id

    with no aliasing and no normalisation. A malformed, unknown, `.P`, padded,
    lowercase, separator-bearing or otherwise non-canonical symbol can never
    reach the exchange.

WHERE IT IS ENFORCED
    `DeltaOrderRequest.to_exchange_payload()`. That is the single choke point:
    `DeltaIndiaClient.create_order` (aliased `place_order`) is its only caller
    in the whole repository, so the six upstream construction sites cannot
    collectively be trusted to get identity right -- the serializer proves it.

WHAT THIS FILE DELIBERATELY DOES NOT DUPLICATE
    `test_instrument_registry.py` proves registry-level fail-closed lookup and
    the empty alias map. `test_symbol_policy_contract.py` proves the `.P`
    refusal at the gateway, lifecycle and scanner boundaries.
    `test_order_response_identity_fail_closed.py` and
    `test_position_identity_fail_closed.py` prove the two inbound parse
    boundaries. This file covers the OUTBOUND boundary those four do not
    reach, plus the structural bans that keep the audited state from
    regressing.
"""

import ast
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from quantedge.execution.delta_client import (
    DELTA_INDIA_PRODUCTION_URL,
    DeltaIndiaClient,
)
from quantedge.execution.models import (
    DeltaOrderRequest,
    OrderSide,
    OrderSizeContractError,
    OrderType,
    PositionSide,
    TimeInForce,
)
from quantedge.execution.reconciliation import DeltaReconciliationService
from quantedge.execution.synchronizer import (
    LocalStateStore,
    PositionRecord,
    PositionStatus,
)
from quantedge.instruments import UnknownInstrumentError, delta_india_registry

EXECUTION_DIR = Path(__file__).resolve().parents[1] / "src" / "quantedge" / \
    "execution"

#: The entire tradable set for this phase.
NATIVE = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

#: Non-canonical spellings. Every one must fail closed at the choke point.
NON_CANONICAL = (
    "btcusd", "BtcUsd", "BTCUSд", " BTCUSD", "BTCUSD ", " BTCUSD ",
    "\tBTCUSD\n", "BTCUSD\n", "BTCUSD.P", "BTCUSD.p", "BTCUSD-P", "BTCUSDP",
    "BTC-USD", "BTC/USD", "BTC_USD", "BTCUSDT", "BTC", "FOOUSD", "",
    "   ", "ethusd", "ETHUSD.P", "solusd", "SOLUSD.P", "xrpusd", "XRPUSD.P",
)

#: Symbol values that are not even strings.
NON_STRING = (None, 27, 0.001, Decimal("27"), b"BTCUSD", ("BTCUSD",),
              ["BTCUSD"], {"symbol": "BTCUSD"}, True)

#: Product ids that belong to no registered symbol, including off-by-one
#: neighbours of each verified id and the two stale fixture values (28, 29)
#: this audit's predecessor corrected.
FOREIGN_IDS = (0, -1, -27, 1, 26, 28, 29, 30, 3135, 3137, 14822, 14824,
               14968, 14970, 999999)


@pytest.fixture
def registry():
    return delta_india_registry()


def _request(symbol, product_id, **kw) -> DeltaOrderRequest:
    """A request valid in every respect except possibly its identity fields."""
    return DeltaOrderRequest(
        product_id=product_id,
        product_symbol=symbol,
        side=kw.pop("side", OrderSide.BUY),
        order_type=kw.pop("order_type", OrderType.LIMIT_ORDER),
        size=kw.pop("size", Decimal("1")),
        limit_price=kw.pop("limit_price", Decimal("95000.0")),
        client_order_id=kw.pop("client_order_id", "QE-AUDIT-0001"),
        **kw,
    )


def _native_request(symbol: str) -> DeltaOrderRequest:
    return _request(symbol, delta_india_registry().get(symbol).product_id)


# ---------------------------------------------------------------------------
# §A  The choke point emits registry identity, and only registry identity.
# ---------------------------------------------------------------------------
class TestTheServializedPayloadCarriesRegistryIdentity:
    @pytest.mark.parametrize("symbol", NATIVE)
    def test_a_verified_pair_serializes_to_its_own_identity(self, registry,
                                                            symbol):
        spec = registry.get(symbol)
        payload = _request(symbol, spec.product_id).to_exchange_payload()
        assert payload["product_symbol"] == spec.symbol
        assert payload["product_id"] == spec.product_id

    @pytest.mark.parametrize("symbol", NATIVE)
    def test_the_invariant_holds_on_the_payload_itself(self, registry, symbol):
        """The invariant restated exactly as specified, read off the payload."""
        payload = _native_request(symbol).to_exchange_payload()
        resolved = registry.get(payload["product_symbol"])
        assert payload["product_symbol"] == resolved.symbol
        assert payload["product_id"] == resolved.product_id

    def test_every_native_symbol_serializes_a_distinct_product_id(self):
        ids = [_native_request(s).to_exchange_payload()["product_id"]
               for s in NATIVE]
        assert sorted(ids) == sorted(set(ids))
        assert len(ids) == len(NATIVE)

    def test_the_payload_id_is_an_int_not_a_string_or_decimal(self):
        payload = _native_request("BTCUSD").to_exchange_payload()
        assert type(payload["product_id"]) is int

    def test_the_serialized_identity_comes_from_the_spec_not_the_request(self):
        """
        Structural: both identity keys are written from the resolved `spec`, so
        a caller cannot smuggle a differently-spelled value past the lookup
        even if it happens to compare equal.
        """
        source = _to_exchange_payload_source()
        assert "'product_symbol': spec.symbol" in source
        assert "'product_id': spec.product_id" in source
        assert "self.product_symbol" not in source.split("payload:")[-1]

    def test_the_rest_of_the_payload_is_unchanged(self, registry):
        """Hardening identity changed nothing else about serialization."""
        req = DeltaOrderRequest(
            product_id=registry.get("ETHUSD").product_id,
            product_symbol="ETHUSD",
            side=OrderSide.SELL,
            order_type=OrderType.STOP_MARKET_ORDER,
            size=Decimal("7"),
            limit_price=Decimal("2400.50"),
            stop_price=Decimal("2350.25"),
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
            client_order_id="QE-AUDIT-UNCHANGED",
            stop_loss_price=Decimal("2340.00"),
            take_profit_price=Decimal("2500.00"),
        )
        payload = req.to_exchange_payload()
        assert payload == {
            "product_id": registry.get("ETHUSD").product_id,
            "product_symbol": "ETHUSD",
            "side": "sell",
            "order_type": "market_order",
            "size": 7,
            "time_in_force": "ioc",
            "reduce_only": True,
            "limit_price": "2400.50",
            "stop_price": "2350.25",
            "client_order_id": "QE-AUDIT-UNCHANGED",
            "stop_loss_price": "2340.00",
            "take_profit_price": "2500.00",
        }

    def test_optional_fields_are_still_omitted_when_absent(self, registry):
        payload = DeltaOrderRequest(
            product_id=registry.get("XRPUSD").product_id,
            product_symbol="XRPUSD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET_ORDER,
            size=Decimal("100"),
        ).to_exchange_payload()
        for absent in ("limit_price", "stop_price", "client_order_id",
                       "stop_loss_price", "take_profit_price"):
            assert absent not in payload

    def test_fractional_sizes_are_refused_rather_than_serialized(self, registry):
        """
        Was `test_fractional_sizes_are_still_serialized_as_floats`, which pinned
        `Decimal("1.5")` -> `1.5` as unchanged-and-not-our-concern while this
        file was closing the *identity* question only.

        The quantity question has since been settled: Delta types order `size` as
        a positive integer contract count (REST reference: `"size": 10` unquoted,
        "Integer numbers (like contract size, product_id and impact size) are
        unquoted"; order-tool reference: `int`, "order size in contracts
        (positive)"). A fractional count is not expressible, so the choke point
        now refuses it -- it is NOT truncated, because flooring 1.5 to 1 would
        change the exposure without anybody choosing to.

        The integral-size serialization this file already pins elsewhere
        (`size=Decimal("7")` -> `7`, `Decimal("100")`, `Decimal("1")`) is
        unchanged. Full coverage of the rule lives in
        `test_execution_sizing_semantics_audit.py` §J.
        """
        with pytest.raises(OrderSizeContractError):
            _request("BTCUSD", registry.get("BTCUSD").product_id,
                     size=Decimal("1.5")).to_exchange_payload()

    def test_integral_sizes_are_still_serialized_as_integers(self, registry):
        for size, expected in ((Decimal("1"), 1), (Decimal("7.000"), 7),
                               (Decimal("100"), 100), (3, 3)):
            payload = _request("BTCUSD", registry.get("BTCUSD").product_id,
                               size=size).to_exchange_payload()
            assert payload["size"] == expected
            assert type(payload["size"]) is int

    def test_the_payload_is_json_serializable(self):
        json.dumps(_native_request("SOLUSD").to_exchange_payload())


# ---------------------------------------------------------------------------
# §B  A non-canonical symbol cannot be serialized at all.
# ---------------------------------------------------------------------------
class TestANonCanonicalSymbolFailsClosedAtTheChokePoint:
    @pytest.mark.parametrize("symbol", NON_CANONICAL)
    def test_it_raises_instead_of_normalising(self, symbol):
        with pytest.raises(UnknownInstrumentError):
            _request(symbol, 27).to_exchange_payload()

    @pytest.mark.parametrize("symbol", NON_CANONICAL)
    def test_pairing_it_with_a_verified_id_does_not_rescue_it(self, registry,
                                                              symbol):
        """A correct-looking id must not launder an incorrect symbol."""
        for native in NATIVE:
            with pytest.raises(UnknownInstrumentError):
                _request(symbol,
                         registry.get(native).product_id).to_exchange_payload()

    @pytest.mark.parametrize("symbol", NON_STRING)
    def test_a_non_string_symbol_fails_closed(self, symbol):
        with pytest.raises(UnknownInstrumentError):
            _request(symbol, 27).to_exchange_payload()

    def test_a_dot_p_symbol_is_never_stripped_to_its_native_form(self,
                                                                 registry):
        """
        `.P` is a display label. The serializer used to emit
        `self.product_symbol` verbatim, and `validation.py` built that value
        with `spec.symbol.replace(".P", "")`; neither conversion exists now.
        """
        for native in NATIVE:
            with pytest.raises(UnknownInstrumentError):
                _request(f"{native}.P",
                         registry.get(native).product_id).to_exchange_payload()

    def test_nothing_non_canonical_silently_becomes_btcusd(self, registry):
        """The specific fabrication this audit exists to rule out."""
        btc = registry.get("BTCUSD")
        for symbol in NON_CANONICAL + NON_STRING:
            try:
                payload = _request(symbol, btc.product_id).to_exchange_payload()
            except UnknownInstrumentError:
                continue
            pytest.fail(f"{symbol!r} serialized as "
                        f"{payload['product_symbol']!r}/{payload['product_id']}")


# ---------------------------------------------------------------------------
# §C  A self-contradictory identity cannot be serialized either.
# ---------------------------------------------------------------------------
class TestAContradictoryIdentityFailsClosedAtTheChokePoint:
    @pytest.mark.parametrize("symbol", NATIVE)
    @pytest.mark.parametrize("other", NATIVE)
    def test_the_full_pairing_matrix_holds(self, registry, symbol, other):
        """Only the diagonal serializes: 4 accepted, 12 refused."""
        spec, foreign = registry.get(symbol), registry.get(other)
        request = _request(symbol, foreign.product_id)
        if symbol == other:
            assert request.to_exchange_payload()["product_id"] == spec.product_id
        else:
            with pytest.raises(UnknownInstrumentError):
                request.to_exchange_payload()

    @pytest.mark.parametrize("product_id", FOREIGN_IDS)
    @pytest.mark.parametrize("symbol", NATIVE)
    def test_an_unrelated_or_off_by_one_id_fails_closed(self, symbol,
                                                        product_id):
        with pytest.raises(UnknownInstrumentError):
            _request(symbol, product_id).to_exchange_payload()

    @pytest.mark.parametrize("symbol", NATIVE)
    def test_a_missing_style_zero_id_fails_closed(self, symbol):
        """0 was the old fabricated default at both inbound parse boundaries."""
        with pytest.raises(UnknownInstrumentError):
            _request(symbol, 0).to_exchange_payload()

    def test_no_foreign_id_belongs_to_any_registered_symbol(self, registry):
        """The premise of the matrix above, stated once."""
        verified = {registry.get(s).product_id for s in NATIVE}
        assert verified.isdisjoint(FOREIGN_IDS)

    def test_the_contradiction_message_names_both_sides(self, registry):
        with pytest.raises(UnknownInstrumentError) as excinfo:
            _request("ETHUSD", registry.get("BTCUSD").product_id) \
                .to_exchange_payload()
        message = str(excinfo.value)
        assert "ETHUSD" in message
        assert str(registry.get("BTCUSD").product_id) in message
        assert str(registry.get("ETHUSD").product_id) in message


# ---------------------------------------------------------------------------
# §D  Nothing non-canonical reaches `place_order` / the exchange.
# ---------------------------------------------------------------------------
def _recording_client():
    """A live client whose transport records every request it is given."""
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        spec = delta_india_registry().get("BTCUSD")
        return httpx.Response(200, json={"success": True, "result": {
            "id": 9001,
            "client_order_id": "QE-AUDIT-0001",
            "product_id": spec.product_id,
            "product_symbol": spec.symbol,
            "side": "buy",
            "order_type": "limit_order",
            "size": "1",
            "unfilled_size": "1",
            "limit_price": "95000.0",
            "state": "open",
            "created_at": 1724261234000000,
        }})

    client = DeltaIndiaClient(
        api_key="test_delta_api_key_123456789",
        api_secret="test_delta_api_secret_987654321_abcdef",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url=DELTA_INDIA_PRODUCTION_URL,
        ),
    )
    return client, sent


class TestNoNonCanonicalOrderReachesTheExchange:
    def test_the_serializer_has_exactly_one_production_caller(self):
        """
        The premise of enforcing the invariant at the serializer: if anything
        else in production built a payload, that path would bypass this file.
        """
        callers = []
        for path in sorted(EXECUTION_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            callers += [path.name for n in ast.walk(tree)
                        if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "to_exchange_payload"]
        assert callers == ["delta_client.py"], callers

    def test_place_order_is_the_same_function_as_create_order(self):
        assert DeltaIndiaClient.place_order is DeltaIndiaClient.create_order

    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", ("btcusd", " BTCUSD ", "BTCUSD.P",
                                        "BTC-USD", "FOOUSD", "", None))
    async def test_place_order_makes_no_http_request_for_it(self, symbol):
        client, sent = _recording_client()
        with pytest.raises(UnknownInstrumentError):
            await client.place_order(_request(symbol, 27))
        assert sent == []

    @pytest.mark.asyncio
    async def test_place_order_makes_no_http_request_for_a_contradiction(self,
                                                                        registry):
        client, sent = _recording_client()
        with pytest.raises(UnknownInstrumentError):
            await client.place_order(
                _request("ETHUSD", registry.get("BTCUSD").product_id))
        assert sent == []

    @pytest.mark.asyncio
    async def test_a_canonical_order_does_reach_the_exchange(self, registry):
        """The counter-test: the refusals above are identity-specific."""
        client, sent = _recording_client()
        response = await client.place_order(_native_request("BTCUSD"))
        assert len(sent) == 1
        body = json.loads(sent[0].content.decode())
        assert body["product_symbol"] == "BTCUSD"
        assert body["product_id"] == registry.get("BTCUSD").product_id
        assert response.product_symbol == "BTCUSD"

    @pytest.mark.asyncio
    async def test_the_auto_generated_client_order_id_is_still_supplied(self):
        client, sent = _recording_client()
        request = _native_request("BTCUSD")
        request.client_order_id = None
        await client.place_order(request)
        assert request.client_order_id is not None
        assert json.loads(sent[0].content.decode())["client_order_id"] == \
            request.client_order_id


# ---------------------------------------------------------------------------
# Structural helpers. AST rather than raw text: production now carries
# comments naming each removed anti-pattern, and a text scan would trip on
# them.
# ---------------------------------------------------------------------------
def _module_trees():
    for path in sorted(EXECUTION_DIR.glob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _without_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.fix_missing_locations(tree)


def _to_exchange_payload_source() -> str:
    tree = ast.parse((EXECUTION_DIR / "models.py").read_text(encoding="utf-8"))
    func = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "to_exchange_payload")
    return ast.unparse(_without_docstrings(func))


def _order_request_sites():
    """Every production `DeltaOrderRequest(...)` construction, with its file."""
    for path, tree in _module_trees():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "DeltaOrderRequest"):
                yield path.name, node


# ---------------------------------------------------------------------------
# §E  Structural bans. Nothing in execution may reintroduce a fabricated,
#     literal, normalised or independently-tabulated product identity.
# ---------------------------------------------------------------------------
#: The four verified ids. Named here, in a test, so production can name none.
VERIFIED_IDS = frozenset({27, 3136, 14823, 14969})

#: The two places a tradable-symbol literal legitimately appears, recorded by
#: the audit. Neither participates in product identity:
#:   validation.py           -- the named local `UNVERIFIED_MAX_LEVERAGE`
#:                              policy map, and `RiskConfiguration
#:                              .supported_symbols`, a dead field nothing reads.
#:   market_orchestrator.py  -- the scanner's own candidate list, used for scan
#:                              bookkeeping and a membership filter only.
SYMBOL_LITERAL_WHITELIST = {"validation.py", "market_orchestrator.py"}

#: Receivers a normaliser may legitimately be applied to even though their
#: name contains "symbol": collateral asset codes, which are not products.
COLLATERAL_RECEIVERS = {"str(data.get('asset_symbol', ''))",
                        "str(data.get('asset_symbol', 'USDT'))"}

NORMALISERS = {"upper", "lower", "casefold", "title", "swapcase", "strip",
               "lstrip", "rstrip", "replace", "removesuffix", "removeprefix"}


class TestProductIdentityCannotBeReintroducedByLiteral:
    def test_no_execution_module_contains_a_verified_product_id_literal(self):
        offenders = {}
        for path, tree in _module_trees():
            hits = [n.lineno for n in ast.walk(_without_docstrings(tree))
                    if isinstance(n, ast.Constant)
                    and type(n.value) is int
                    and n.value in VERIFIED_IDS]
            if hits:
                offenders[path.name] = hits
        assert offenders == {}

    def test_tradable_symbol_literals_stay_in_the_recorded_non_identity_sites(
            self):
        offenders = {}
        for path, tree in _module_trees():
            hits = sorted({n.value for n in ast.walk(_without_docstrings(tree))
                           if isinstance(n, ast.Constant)
                           and isinstance(n.value, str)
                           and any(s in n.value for s in NATIVE)})
            if hits and path.name not in SYMBOL_LITERAL_WHITELIST:
                offenders[path.name] = hits
        assert offenders == {}

    def test_no_identity_bearing_module_names_a_tradable_symbol_at_all(self):
        """The modules that construct or parse identity carry no symbol text."""
        for name in ("models.py", "delta_client.py", "trade_lifecycle.py",
                     "multi_user_orchestrator.py", "reconciliation.py",
                     "synchronizer.py", "private_websocket.py",
                     "execution_engine.py"):
            tree = _without_docstrings(
                ast.parse((EXECUTION_DIR / name).read_text(encoding="utf-8")))
            hits = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant)
                    and isinstance(n.value, str)
                    and any(s in n.value for s in NATIVE)]
            assert hits == [], f"{name}: {hits}"

    def test_no_order_construction_passes_a_literal_identity(self):
        offenders = []
        for name, call in _order_request_sites():
            for kw in call.keywords:
                if kw.arg in ("product_id", "product_symbol") and \
                        isinstance(kw.value, (ast.Constant, ast.JoinedStr)):
                    offenders.append(f"{name}:{call.lineno} {kw.arg}")
        assert offenders == []


class TestProductSymbolsAreNeverNormalised:
    def test_no_normaliser_is_applied_to_a_product_symbol_anywhere(self):
        """
        Case folding, whitespace trimming and suffix stripping are what turn a
        non-canonical spelling into a real product. Two sites used to do it:
        `validation.py`'s `spec.symbol.replace(".P", "")` when building the
        outbound order, and `reconciliation.py`'s `.upper()` on both position-map
        keys. Neither remains, and none may return.
        """
        offenders = []
        for path, tree in _module_trees():
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in NORMALISERS):
                    continue
                receiver = ast.unparse(node.func.value)
                if "symbol" not in receiver:
                    continue
                if receiver in COLLATERAL_RECEIVERS:
                    continue
                offenders.append(f"{path.name}:{node.lineno} {receiver}")
        assert offenders == []

    def test_reconciliation_normalises_nothing_at_all(self):
        tree = ast.parse(
            (EXECUTION_DIR / "reconciliation.py").read_text(encoding="utf-8"))
        calls = [f"{n.lineno} {ast.unparse(n)}" for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr in NORMALISERS]
        assert calls == []

    def test_the_serializer_normalises_nothing_either(self):
        source = _to_exchange_payload_source()
        for normaliser in sorted(NORMALISERS):
            assert f".{normaliser}(" not in source


class TestNoProductTableExistsOutsideTheRegistry:
    def test_the_execution_product_table_is_derived_from_the_registry(self):
        from quantedge.execution.validation import (
            DEFAULT_DELTA_INDIA_PRODUCTS,
        )

        registry = delta_india_registry()
        assert tuple(sorted(DEFAULT_DELTA_INDIA_PRODUCTS)) == registry.symbols
        for symbol, spec in DEFAULT_DELTA_INDIA_PRODUCTS.items():
            verified = registry.get(symbol)
            assert spec.symbol == verified.symbol
            assert spec.product_id == verified.product_id
            assert spec.tick_size == verified.tick_size
            assert spec.contract_value == verified.contract_value

    def test_the_loader_reads_the_registry_and_nothing_else(self):
        tree = ast.parse(
            (EXECUTION_DIR / "validation.py").read_text(encoding="utf-8"))
        loader = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "_load_delta_india_products")
        called = {n.func.id for n in ast.walk(loader)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "delta_india_registry" in called

    def test_no_dict_literal_in_execution_maps_a_tradable_symbol_to_an_id(self):
        """An independent catalogue would silently outrank the snapshot."""
        offenders = []
        for path, tree in _module_trees():
            for node in ast.walk(_without_docstrings(tree)):
                if not isinstance(node, ast.Dict):
                    continue
                keys = [k.value for k in node.keys
                        if isinstance(k, ast.Constant)
                        and isinstance(k.value, str)]
                if not any(k in NATIVE for k in keys):
                    continue
                if any(isinstance(v, ast.Constant) and type(v.value) is int
                       and v.value in VERIFIED_IDS for v in node.values):
                    offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == []


# ---------------------------------------------------------------------------
# §F  Every production order-construction site sources identity structurally
#     from the one resolved authority object, not from a raw caller symbol.
# ---------------------------------------------------------------------------
#: Names bound to a resolved authority object: `delta_india_registry().get(...)`
#: in `multi_user_orchestrator`, `get_product_specification(...)` in
#: `validation` and `trade_lifecycle`. Both are exact, fail-closed lookups over
#: tables keyed by each record's own `spec.symbol`.
AUTHORITY_NAMES = {"spec", "product_spec"}

#: The six production sites, recorded so a seventh cannot appear unnoticed.
EXPECTED_SITES = {
    "multi_user_orchestrator.py": 3,
    "trade_lifecycle.py": 3,
    "validation.py": 1,
}


def _traces_to_authority(name: str, attr: str, tree: ast.AST) -> bool:
    """True if `name` is only ever assigned `<authority>.<attr>` in `tree`."""
    assignments = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == name
                           for t in n.targets)]
    return bool(assignments) and all(
        isinstance(n.value, ast.Attribute)
        and n.value.attr == attr
        and isinstance(n.value.value, ast.Name)
        and n.value.value.id in AUTHORITY_NAMES
        for n in assignments)


class TestEveryOrderConstructionSourcesIdentityFromTheAuthority:
    def test_the_recorded_set_of_construction_sites_is_complete(self):
        counts = {}
        for name, _ in _order_request_sites():
            counts[name] = counts.get(name, 0) + 1
        assert counts == EXPECTED_SITES

    def test_product_symbol_is_always_the_resolved_specs_own_symbol(self):
        """
        The change this audit made: three lifecycle sites passed the raw
        `decision.symbol` / `record.symbol`, and three multi-user sites passed
        the raw dispatch argument. Those were equal to the spec's symbol only
        derivatively -- because the lookup that produced the accompanying
        `product_id` happened to be exact. Now the equality is structural.
        """
        offenders = []
        for name, call in _order_request_sites():
            passed = {kw.arg: kw.value for kw in call.keywords}
            value = passed.get("product_symbol")
            ok = (isinstance(value, ast.Attribute)
                  and value.attr == "symbol"
                  and isinstance(value.value, ast.Name)
                  and value.value.id in AUTHORITY_NAMES)
            if not ok:
                rendered = ast.unparse(value) if value is not None else "MISSING"
                offenders.append(f"{name}:{call.lineno} -> {rendered}")
        assert offenders == []

    def test_product_id_always_traces_to_the_same_authority(self):
        offenders = []
        for path, tree in _module_trees():
            for call in [n for n in ast.walk(tree)
                         if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name)
                         and n.func.id == "DeltaOrderRequest"]:
                value = {kw.arg: kw.value
                         for kw in call.keywords}.get("product_id")
                direct = (isinstance(value, ast.Attribute)
                          and value.attr == "product_id"
                          and isinstance(value.value, ast.Name)
                          and value.value.id in AUTHORITY_NAMES)
                indirect = (isinstance(value, ast.Name)
                            and _traces_to_authority(value.id, "product_id",
                                                     tree))
                if not (direct or indirect):
                    rendered = ast.unparse(value) if value else "MISSING"
                    offenders.append(f"{path.name}:{call.lineno} -> {rendered}")
        assert offenders == []

    def test_both_identity_fields_come_from_one_shared_authority_object(self):
        """
        Per site, the `spec` supplying `product_symbol` is the same name as the
        one supplying `product_id`, so the two fields cannot disagree by
        construction -- before the serializer even checks.
        """
        for path, tree in _module_trees():
            for call in [n for n in ast.walk(tree)
                         if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name)
                         and n.func.id == "DeltaOrderRequest"]:
                passed = {kw.arg: kw.value for kw in call.keywords}
                symbol_owner = passed["product_symbol"].value.id
                id_value = passed["product_id"]
                id_owner = (id_value.value.id
                            if isinstance(id_value, ast.Attribute)
                            else _authority_of(id_value.id, tree))
                assert symbol_owner == id_owner, \
                    f"{path.name}:{call.lineno} {symbol_owner} vs {id_owner}"


def _authority_of(name: str, tree: ast.AST) -> str:
    """The authority name a bare `product_id`-style local was assigned from."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name
                        for t in node.targets)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)):
            return node.value.value.id
    return f"<unresolved:{name}>"


# ---------------------------------------------------------------------------
# §G  End to end: what the validation gateway approves serializes cleanly.
# ---------------------------------------------------------------------------
GEOMETRY = {
    "BTCUSD": (Decimal("95000.0"), Decimal("94000.0"), Decimal("97000.0")),
    "ETHUSD": (Decimal("2400.00"), Decimal("2350.00"), Decimal("2500.00")),
    "SOLUSD": (Decimal("200.0000"), Decimal("195.0000"), Decimal("210.0000")),
    "XRPUSD": (Decimal("2.0000"), Decimal("1.9000"), Decimal("2.2000")),
}


def _gateway_context():
    from quantedge.execution.synchronizer import AccountRecord, ConnectionRecord
    from quantedge.execution.validation import (
        RiskConfiguration,
        ValidationContext,
    )

    return ValidationContext(
        account=AccountRecord(
            account_id="acc_audit_01",
            base_currency="USDT",
            current_balance=Decimal("10000.00"),
            available_balance=Decimal("10000.00"),
            margin_used=Decimal("0.00"),
            total_equity=Decimal("10000.00"),
            is_active=True,
        ),
        algo_enabled=True,
        kill_switch_active=False,
        connection=ConnectionRecord(
            connection_status="CONNECTED",
            last_connected_at=datetime.now(timezone.utc),
        ),
        api_key="valid_delta_api_key_123456",
        api_secret="valid_delta_api_secret_654321",
        risk_config=RiskConfiguration(),
        open_positions=[],
        open_orders=[],
        active_client_order_ids=set(),
        active_setup_ids=set(),
    )


def _gateway_request(symbol: str):
    from quantedge.execution.validation import OrderValidationRequest
    from quantedge.strategy.models import TradeDirection

    entry, sl, tp = GEOMETRY[symbol]
    return OrderValidationRequest(
        account_id="acc_audit_01",
        symbol=symbol,
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        leverage=50,
        client_order_id=f"QE-AUDIT-{symbol}",
        setup_id=f"SETUP-AUDIT-{symbol}",
    )


class TestTheApprovedOrderSatisfiesTheInvariant:
    @pytest.mark.parametrize("symbol", NATIVE)
    def test_a_gateway_approved_order_serializes_to_registry_identity(
            self, registry, symbol):
        from quantedge.execution.validation import OrderValidationGateway

        result = OrderValidationGateway().validate(_gateway_request(symbol),
                                                   _gateway_context())
        assert result.is_valid, result.rejection_reason
        payload = result.order_request.to_exchange_payload()
        spec = registry.get(symbol)
        assert payload["product_symbol"] == spec.symbol
        assert payload["product_id"] == spec.product_id
        assert ".P" not in payload["product_symbol"]


# ---------------------------------------------------------------------------
# §H  Reconciliation compares symbols exactly, so a non-canonical local
#     symbol is reported rather than folded onto a real product.
# ---------------------------------------------------------------------------
def _reconciliation_service(local_symbol: str, exchange_symbol: str):
    from unittest.mock import AsyncMock, MagicMock

    from quantedge.execution.models import DeltaPosition

    spec = delta_india_registry().get(exchange_symbol)
    exchange_position = DeltaPosition.from_dict({
        "product_id": spec.product_id,
        "product_symbol": spec.symbol,
        "size": "1",
        "entry_price": "95000.0",
        "mark_price": "95100.0",
        "margin": "1000.0",
        "leverage": "50",
    })

    client = MagicMock()
    client.get_wallet_balances = AsyncMock(return_value=[])
    client.get_positions = AsyncMock(return_value=[exchange_position])
    client.get_open_orders = AsyncMock(return_value=[])

    store = LocalStateStore(account_id="acc_audit_01")
    store.positions[local_symbol] = PositionRecord(
        symbol=local_symbol,
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("95000.0"),
        current_price=Decimal("95100.0"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        leverage=Decimal("50"),
        margin_used=Decimal("1000.0"),
        status=PositionStatus.OPEN,
    )
    return DeltaReconciliationService(client=client, state_store=store)


class TestReconciliationComparesSymbolsExactly:
    @pytest.mark.asyncio
    async def test_a_matching_native_symbol_reconciles_clean(self):
        service = _reconciliation_service("BTCUSD", "BTCUSD")
        report = await service.reconcile_account("acc_audit_01")
        assert [d.discrepancy_type.value for d in report.discrepancies] == []
        assert report.is_synchronized is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("local", ("btcusd", "BtcUsd", " BTCUSD",
                                       "BTCUSD ", "BTCUSD.P"))
    async def test_a_non_canonical_local_symbol_is_reported_not_folded(self,
                                                                      local):
        """
        Both position-map keys used to be `.upper()`-ed, which folded a
        lowercase local symbol onto the real product and reported nothing. The
        exchange side is registry-resolved and the local side is written from
        the same gated symbols, so a divergence here is a real defect and must
        surface as two discrepancies -- untracked on one side, absent on the
        other -- never as silent agreement.
        """
        service = _reconciliation_service(local, "BTCUSD")
        report = await service.reconcile_account("acc_audit_01")
        kinds = {d.discrepancy_type.value for d in report.discrepancies}
        assert "EXCHANGE_POSITION_MISSING_LOCALLY" in kinds
        assert "LOCAL_TRADE_MISSING_ON_EXCHANGE" in kinds
        assert report.is_synchronized is False

    @pytest.mark.asyncio
    async def test_a_quantity_mismatch_is_still_detected_on_an_exact_match(self):
        """The exactness change did not disable the comparison it gates."""
        service = _reconciliation_service("BTCUSD", "BTCUSD")
        service.state_store.positions["BTCUSD"].quantity = Decimal("2")
        report = await service.reconcile_account("acc_audit_01")
        assert [d.discrepancy_type.value for d in report.discrepancies] == \
            ["QUANTITY_MISMATCH"]
