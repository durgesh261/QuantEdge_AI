"""
The symbol policy, pinned as executable contract.

POLICY FOR THIS PHASE
    1. Native Delta India symbols are the ONLY tradable Python execution
       symbols: BTCUSD, ETHUSD, SOLUSD, XRPUSD.
    2. `.P` symbols are display / persistence / local-label symbols. They are
       NOT valid execution symbols.
    3. No implicit `.P -> native` conversion is permitted at the execution
       boundary.
    4. No unknown-symbol normalisation is permitted.
    5. No product-id fallback is permitted.

WHAT THIS FILE DOES *NOT* DO
    It does not add an alias. `.P` equivalence is not established by any
    authoritative Delta source in this repo, so this file pins the refusal
    rather than the alias (safety rules #8, #15, #16).

WHAT THIS FILE DELIBERATELY DOES NOT DUPLICATE
    `test_instrument_registry.py` already proves registry-level fail-closed
    lookup and the empty alias map; `test_execution_product_spec_migration.py`
    already proves `get_product_specification` derives from the registry and
    normalises nothing. This file covers the boundaries those two do not
    reach: the validation GATEWAY, the trade LIFECYCLE, the market
    ORCHESTRATOR, and the Manual SMC decision symbol -- plus §E, which pins
    the gateway to the registry's exact-match contract.
"""

import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from quantedge.execution.models import (
    ConnectionState,
    ExecutionMode,
    OrderType,
)
from quantedge.execution.synchronizer import (
    AccountRecord,
    ConnectionRecord,
    LocalStateStore,
)
from quantedge.execution.trade_lifecycle import TradeLifecycleState
from quantedge.execution.validation import (
    DEFAULT_DELTA_INDIA_PRODUCTS,
    OrderValidationGateway,
    OrderValidationRequest,
    RejectionReasonCode,
    RiskConfiguration,
    UnknownProductError,
    ValidationContext,
    get_product_specification,
)
from quantedge.instruments import UnknownInstrumentError, delta_india_registry
from quantedge.strategy.models import (
    SetupState,
    StrategyDecision,
    StrategyDirection,
    TradeDirection,
)

MANUAL_SMC_DIR = Path(__file__).resolve().parents[1] / "src" / "quantedge" / \
    "strategy" / "manual_smc"

#: The entire tradable set for this phase.
NATIVE = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

#: The display labels. Every one of these must be refused.
DOT_P = tuple(f"{s}.P" for s in NATIVE)

#: Case, separator and unknown-symbol variants that must never resolve to a
#: product at the instrument / product-spec boundary.
NOT_A_PRODUCT = ("btcusd", "BtcUsd", "BTC-USD", "BTC/USD", "BTCUSDT", "BTC",
                 "FOOUSD", "BTCUSD.p", "BTCUSD-P", "BTCUSDP", "")

#: Tick-aligned, RR-2.0 LONG geometry per symbol. Sized for a 10 000 USDT
#: account: contract values differ (BTC 0.001, ETH 0.01, SOL 1, XRP 1) so the
#: quantities below are contracts, not coins.
GEOMETRY = {
    "BTCUSD": (Decimal("95000.0"), Decimal("94000.0"), Decimal("97000.0")),
    "ETHUSD": (Decimal("2400.00"), Decimal("2350.00"), Decimal("2500.00")),
    "SOLUSD": (Decimal("200.0000"), Decimal("195.0000"), Decimal("210.0000")),
    "XRPUSD": (Decimal("2.0000"), Decimal("1.9000"), Decimal("2.2000")),
}


def _context() -> ValidationContext:
    """A healthy live account. Nothing here is symbol-specific."""
    return ValidationContext(
        account=AccountRecord(
            account_id="acc_policy_01",
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


def _request(symbol: str, geometry_of: str = None) -> OrderValidationRequest:
    """
    A request that is valid in every respect EXCEPT possibly its symbol, so a
    rejection can only ever be attributable to the symbol.
    """
    entry, sl, tp = GEOMETRY[geometry_of or symbol]
    return OrderValidationRequest(
        account_id="acc_policy_01",
        symbol=symbol,
        direction=TradeDirection.LONG,
        order_type=OrderType.LIMIT_ORDER,
        quantity=Decimal("1"),
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        leverage=50,
        client_order_id=f"QE-POLICY-{symbol or 'EMPTY'}",
        setup_id=f"SETUP-POLICY-{symbol or 'EMPTY'}",
    )


@pytest.fixture
def gateway():
    return OrderValidationGateway()


@pytest.fixture
def registry():
    return delta_india_registry()


# ---------------------------------------------------------------------------
# §A  The four native symbols are tradable, end to end.
# ---------------------------------------------------------------------------
class TestNativeSymbolsRemainTradable:
    def test_the_tradable_set_is_exactly_the_four_native_symbols(self, registry):
        assert registry.symbols == NATIVE
        assert tuple(sorted(DEFAULT_DELTA_INDIA_PRODUCTS)) == NATIVE

    @pytest.mark.parametrize("symbol", NATIVE)
    def test_a_native_symbol_resolves_to_its_own_product(self, symbol):
        spec = get_product_specification(symbol)
        assert spec.symbol == symbol
        assert spec.is_verified

    @pytest.mark.parametrize("symbol", NATIVE)
    def test_a_native_request_is_approved_by_the_gateway(self, gateway, symbol):
        result = gateway.validate(_request(symbol), _context())
        assert result.is_valid, result.rejection_reason
        assert result.order_request is not None

    @pytest.mark.parametrize("symbol", NATIVE)
    def test_an_approved_order_carries_the_native_symbol_and_its_own_id(
            self, gateway, symbol):
        """The symbol that leaves the gateway is native and unsuffixed."""
        order = gateway.validate(_request(symbol), _context()).order_request
        assert order.product_symbol == symbol
        assert ".P" not in order.product_symbol
        assert order.product_id == get_product_specification(symbol).product_id

    def test_every_native_symbol_owns_a_distinct_product_id(self):
        ids = [get_product_specification(s).product_id for s in NATIVE]
        assert len(set(ids)) == len(NATIVE)


# ---------------------------------------------------------------------------
# §B  `.P` is refused at every Python boundary. No conversion, anywhere.
# ---------------------------------------------------------------------------
class TestDotPIsNotAnExecutionSymbol:
    @pytest.mark.parametrize("symbol", DOT_P)
    def test_the_instrument_registry_refuses_it(self, registry, symbol):
        with pytest.raises(UnknownInstrumentError):
            registry.get(symbol)
        assert symbol not in registry

    @pytest.mark.parametrize("symbol", DOT_P)
    def test_the_product_spec_boundary_refuses_it(self, symbol):
        with pytest.raises(UnknownProductError):
            get_product_specification(symbol)

    @pytest.mark.parametrize("symbol", DOT_P)
    def test_it_is_absent_from_the_execution_product_table(self, symbol):
        assert symbol not in DEFAULT_DELTA_INDIA_PRODUCTS
        assert DEFAULT_DELTA_INDIA_PRODUCTS.get(symbol) is None

    @pytest.mark.parametrize("symbol", DOT_P)
    def test_the_gateway_rejects_it_as_an_unsupported_symbol(
            self, gateway, symbol):
        native = symbol[:-2]
        result = gateway.validate(_request(symbol, geometry_of=native),
                                  _context())
        assert result.is_valid is False
        assert result.rejection_code == RejectionReasonCode.UNSUPPORTED_SYMBOL
        assert result.failed_check == "CHECK_SUPPORTED_SYMBOL"

    @pytest.mark.parametrize("symbol", DOT_P)
    def test_no_dot_p_request_produces_a_delta_order_request(
            self, gateway, symbol):
        """Policy 5: a refused symbol must not reach order construction."""
        result = gateway.validate(_request(symbol, geometry_of=symbol[:-2]),
                                  _context())
        assert result.order_request is None

    @pytest.mark.parametrize("symbol", DOT_P)
    def test_no_dot_p_symbol_selects_a_product_id_by_any_route(self, symbol):
        """Policy 5: no fallback, not even to BTCUSD / product 27."""
        for lookup in (delta_india_registry().get, get_product_specification):
            with pytest.raises((UnknownInstrumentError, UnknownProductError)):
                lookup(symbol)


# ---------------------------------------------------------------------------
# §C  The live-order boundary. A `.P` decision reaches no exchange call.
# ---------------------------------------------------------------------------
def _decision(symbol: str, geometry_of: str) -> StrategyDecision:
    entry, sl, tp = GEOMETRY[geometry_of]
    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol=symbol,
        timeframe="15m",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id=f"setup-policy-{symbol}",
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=Decimal("2.0"),
        quantity=Decimal("1"),
    )


@pytest.fixture
def lifecycle():
    """A live-mode lifecycle manager whose client records every call."""
    from quantedge.execution.algo_config import AlgoConfigStore
    from quantedge.execution.capital_allocator import CapitalAllocator
    from quantedge.execution.delta_client import DeltaIndiaClient
    from quantedge.execution.single_trade_lock import SingleTradeLockManager
    from quantedge.execution.trade_lifecycle import TradeLifecycleManager

    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "POLICY_KEY"
    client._api_secret = "POLICY_SECRET"
    client.connection_state = ConnectionState.CONNECTED
    client.place_order = AsyncMock()
    client.cancel_order = AsyncMock(return_value=True)

    store = LocalStateStore()
    store.account.account_id = "acc_policy_01"
    store.account.user_id = "usr_policy"
    store.account.total_equity = Decimal("10000.00")
    store.account.available_balance = Decimal("10000.00")
    store.account.current_balance = Decimal("10000.00")
    store.account.last_synced_at = datetime.now(timezone.utc)
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.connection.connection_status = "CONNECTED"

    return TradeLifecycleManager(
        client=client,
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        sync_service=None,
        algo_config_store=AlgoConfigStore(),
        single_trade_lock=SingleTradeLockManager(),
        capital_allocator=CapitalAllocator(),
        execution_mode=ExecutionMode.LIVE,
    )


class TestNoDotPOrderCanReachTheExchange:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", DOT_P)
    async def test_the_lifecycle_refuses_before_building_an_order(
            self, lifecycle, symbol):
        """
        `trade_lifecycle` resolves the product specification BEFORE it builds
        the `DeltaOrderRequest`, so a `.P` decision is refused there and no
        order object is ever constructed or submitted. The refusal is returned
        as an UNSUPPORTED_SYMBOL record, the same fail-closed outcome the
        gateway produces for the same condition.
        """
        record = await lifecycle.execute_trade_setup(
            _decision(symbol, symbol[:-2]), "acc_policy_01", "usr_policy")
        assert record.state == TradeLifecycleState.ENTRY_REJECTED
        assert record.rejection_code == \
            RejectionReasonCode.UNSUPPORTED_SYMBOL.value
        lifecycle.client.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_refusal_releases_the_single_trade_lock(self, lifecycle):
        """
        Nothing reached the exchange, so the lock this call acquired is
        released rather than stranded, and the next setup can trade.
        """
        await lifecycle.execute_trade_setup(
            _decision("BTCUSD.P", "BTCUSD"), "acc_policy_01", "usr_policy")
        is_locked, _, _ = lifecycle.single_trade_lock.is_locked(
            "usr_policy", "acc_policy_01")
        assert is_locked is False

    @pytest.mark.asyncio
    async def test_a_native_decision_does_reach_the_exchange(self, lifecycle):
        """The counter-test: the refusal above is symbol-specific, not a stub."""
        await lifecycle.execute_trade_setup(
            _decision("BTCUSD", "BTCUSD"), "acc_policy_01", "usr_policy")
        lifecycle.client.place_order.assert_awaited()
        submitted = lifecycle.client.place_order.await_args_list[0].args[0]
        assert submitted.product_symbol == "BTCUSD"
        assert submitted.product_id == get_product_specification(
            "BTCUSD").product_id

    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", DOT_P)
    async def test_the_orchestrator_never_qualifies_a_dot_p_decision(
            self, symbol):
        from quantedge.execution.market_orchestrator import (
            MarketScannerOrchestrator,
        )
        from quantedge.execution.single_trade_lock import SingleTradeLockManager

        mgr = MagicMock()
        mgr.execute_trade_setup = AsyncMock()
        orch = MarketScannerOrchestrator(
            lifecycle_manager=mgr,
            single_trade_lock=SingleTradeLockManager(),
        )
        assert symbol not in orch.supported_symbols

        result = await orch.scan_and_execute(
            account_id="acc_policy_01",
            user_id="usr_policy",
            candidate_decisions=[_decision(symbol, symbol[:-2])],
        )
        mgr.execute_trade_setup.assert_not_awaited()
        assert result.executed_record is None
        assert result.qualifying_symbol is None


# ---------------------------------------------------------------------------
# §D  The one remaining `.P` list in execution is vestigial and inert.
# ---------------------------------------------------------------------------
VALIDATION_PY = Path(__file__).resolve().parents[1] / "src" / "quantedge" / \
    "execution" / "validation.py"


class TestTheVestigialDotPListCannotReEnableDotP:
    def test_risk_configuration_still_carries_the_legacy_list(self):
        """Recorded as-is: the field exists and still names both forms."""
        assert set(DOT_P) <= set(RiskConfiguration().supported_symbols)

    def test_nothing_in_validation_ever_reads_that_field(self):
        """Proven structurally: no attribute load of it anywhere in the module."""
        tree = ast.parse(VALIDATION_PY.read_text(encoding="utf-8"))
        reads = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute)
                 and n.attr == "supported_symbols"]
        assert reads == []

    @pytest.mark.parametrize("symbol", DOT_P)
    def test_listing_dot_p_there_does_not_make_it_tradable(
            self, gateway, symbol):
        """Behavioural counterpart: the gateway consults `product_specs` only."""
        context = _context()
        context.risk_config = RiskConfiguration(
            supported_symbols=list(DOT_P))
        result = gateway.validate(_request(symbol, geometry_of=symbol[:-2]),
                                  context)
        assert result.is_valid is False
        assert result.rejection_code == RejectionReasonCode.UNSUPPORTED_SYMBOL

    def test_the_orchestrators_own_symbol_list_is_native_only(self):
        from quantedge.execution.market_orchestrator import (
            MarketScannerOrchestrator,
        )
        from quantedge.execution.single_trade_lock import SingleTradeLockManager

        orch = MarketScannerOrchestrator(
            lifecycle_manager=MagicMock(),
            single_trade_lock=SingleTradeLockManager(),
        )
        assert sorted(orch.supported_symbols) == sorted(NATIVE)


# ---------------------------------------------------------------------------
# §E  Normalisation. Exact match at every boundary, gateway included.
# ---------------------------------------------------------------------------
class TestNoSilentNormalisationIntoAValidProduct:
    @pytest.mark.parametrize("symbol", NOT_A_PRODUCT)
    def test_the_product_spec_boundary_normalises_nothing(self, symbol):
        with pytest.raises(UnknownProductError):
            get_product_specification(symbol)

    @pytest.mark.parametrize("symbol", NOT_A_PRODUCT)
    def test_the_registry_normalises_nothing(self, registry, symbol):
        with pytest.raises(UnknownInstrumentError):
            registry.get(symbol)

    @pytest.mark.parametrize(
        "symbol", ("BTC-USD", "BTC/USD", "BTCUSDT", "BTC", "FOOUSD",
                   "BTCUSD.p", "BTCUSD-P", "BTCUSDP", ""))
    def test_the_gateway_rejects_separators_and_unknowns(self, gateway, symbol):
        result = gateway.validate(_request(symbol, geometry_of="BTCUSD"),
                                 _context())
        assert result.is_valid is False
        assert result.rejection_code == RejectionReasonCode.UNSUPPORTED_SYMBOL
        assert result.order_request is None

    @pytest.mark.parametrize("symbol", ("btcusd", "BtcUsd", " BTCUSD ",
                                       "\tBTCUSD\n", "BTCUSD\n", " BTCUSD"))
    def test_the_gateway_folds_nothing_either(self, gateway, registry, symbol):
        """
        The gateway now applies the registry's exact-match contract: no case
        conversion and no whitespace trimming before the membership test.
        Previously `validation.py` did `.strip().upper()` here and accepted all
        of these; that divergence is closed.
        """
        with pytest.raises(UnknownInstrumentError):
            registry.get(symbol)
        with pytest.raises(UnknownProductError):
            get_product_specification(symbol)

        result = gateway.validate(_request(symbol, geometry_of="BTCUSD"),
                                 _context())
        assert result.is_valid is False
        assert result.rejection_code == RejectionReasonCode.UNSUPPORTED_SYMBOL
        assert result.failed_check == "CHECK_SUPPORTED_SYMBOL"
        assert result.order_request is None

    @pytest.mark.parametrize("symbol", (None, 27, 0.001, b"BTCUSD",
                                        ("BTCUSD",), ["BTCUSD"]))
    def test_a_non_string_symbol_fails_closed_instead_of_raising(
            self, gateway, symbol):
        """No `AttributeError`, no `TypeError` -- a structured rejection."""
        result = gateway.validate(_request(symbol, geometry_of="BTCUSD"),
                                 _context())
        assert result.is_valid is False
        assert result.rejection_code == RejectionReasonCode.UNSUPPORTED_SYMBOL
        assert result.order_request is None

    def test_the_gateway_never_calls_a_symbol_normaliser_on_the_request(self):
        """
        Structural: no method is ever invoked on `request.symbol` inside
        `validate`, so the request symbol cannot be transformed before the
        membership test. (The `spec.symbol.replace(".P","")` that used to build
        the outbound order symbol further down has since been removed;
        `test_execution_identity_audit.py` now bans every normaliser applied to
        any product symbol anywhere in the execution package.)
        """
        tree = ast.parse(VALIDATION_PY.read_text(encoding="utf-8"))
        validate = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef)
                        and n.name == "validate")
        offenders = [
            f"{n.func.attr} @ line {n.lineno}"
            for n in ast.walk(validate)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Attribute)
            and n.func.value.attr == "symbol"
            and isinstance(n.func.value.value, ast.Name)
            and n.func.value.value.id == "request"
        ]
        assert offenders == []

    def test_folding_can_never_turn_a_dot_p_into_a_native_product(self, gateway):
        """The bound on §E: nothing here strips a suffix either."""
        for symbol in DOT_P + ("btcusd.p", " BTCUSD.P "):
            native = "BTCUSD"
            result = gateway.validate(_request(symbol, geometry_of=native),
                                     _context())
            assert result.is_valid is False
            assert result.order_request is None


# ---------------------------------------------------------------------------
# §F  Manual SMC emits native symbols, and appends no suffix ever.
# ---------------------------------------------------------------------------
def _stripped_string_constants(path: Path):
    """Every string constant in the module with docstrings removed."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
    for node in ast.walk(ast.fix_missing_locations(tree)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


class TestManualSMCDecisionsStayNative:
    def test_the_manual_smc_asset_set_is_the_native_set(self):
        from quantedge.strategy.manual_smc.sizing import MANUAL_SMC_SYMBOLS

        assert sorted(MANUAL_SMC_SYMBOLS) == sorted(NATIVE)

    def test_a_decision_symbol_defaults_to_the_asset_verbatim(self):
        from quantedge.strategy.manual_smc.adapter import ManualSMCAdapter

        adapter = ManualSMCAdapter(timeframe="15m")
        assert adapter.symbol_map is None
        for asset in NATIVE:
            assert adapter.symbol_for(asset) == asset

    def test_an_explicit_caller_mapping_is_the_only_way_to_change_it(self):
        from quantedge.strategy.manual_smc.adapter import (
            ManualSMCAdapter,
            UnknownSymbolError,
        )

        mapped = ManualSMCAdapter(timeframe="15m",
                                  symbol_map={"BTCUSD": "BTCUSD.P"})
        assert mapped.symbol_for("BTCUSD") == "BTCUSD.P"
        # ... and only for the assets the caller actually covered.
        with pytest.raises(UnknownSymbolError):
            mapped.symbol_for("ETHUSD")

    def test_no_manual_smc_module_contains_a_dot_p_literal(self):
        modules = sorted(MANUAL_SMC_DIR.glob("*.py"))
        assert len(modules) >= 5, "expected the Manual SMC package to be here"
        offenders = {
            path.name: [s for s in _stripped_string_constants(path)
                        if ".P" in s]
            for path in modules
        }
        assert {k: v for k, v in offenders.items() if v} == {}

    def test_the_adapter_passes_the_resolved_symbol_and_builds_none_other(self):
        """
        Every `StrategyDecision` the adapter constructs takes `symbol=symbol`
        -- a plain name, never a concatenation, f-string or literal.
        """
        tree = ast.parse((MANUAL_SMC_DIR / "adapter.py").read_text(
            encoding="utf-8"))
        sites = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == "StrategyDecision"]
        assert sites, "expected the adapter to construct StrategyDecision"
        for call in sites:
            passed = {kw.arg: kw.value for kw in call.keywords}
            assert "symbol" in passed, f"line {call.lineno}"
            assert isinstance(passed["symbol"], ast.Name), \
                f"line {call.lineno} does not pass a bare name as symbol"
            assert passed["symbol"].id == "symbol", \
                f"line {call.lineno} passes {passed['symbol'].id!r}"

    def test_the_adapter_never_calls_a_symbol_normaliser(self):
        tree = ast.parse((MANUAL_SMC_DIR / "adapter.py").read_text(
            encoding="utf-8"))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)}
        assert not called & {"upper", "lower", "casefold", "strip", "lstrip",
                             "rstrip", "removesuffix", "removeprefix"}
