"""
`MultiUserExecutionOrchestrator` product identity comes from the registry.

Step 6 of `execute_trade` used to read identity out of the account's live
`get_products()` payload:

    product_id    = int(prod_info.get("id", 0))
    contract_value = Decimal(str(prod_info.get("contract_value", "1.0")))
    tick_size      = Decimal(str(prod_info.get("tick_size", "0.1")))

so whatever the exchange (or a mock) said a symbol's product id was became the
id on the entry order and both reduce-only brackets. The existing multi-user
mock claims `ETHUSD` is product 27 with BTCUSD's contract value, which is how an
ETHUSD order carrying product id 27 became reachable. Identity is now resolved
through `delta_india_registry()`, the single verified source.

What these tests pin: the four verified symbol -> product id pairs reaching the
actual order requests, fail-closed behaviour for unregistered and `.P` symbols,
registry values winning over a contradictory products payload, and the absence
of any second product table or fabricated identity in the module.

Zero network access: the Delta client is a mock throughout.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
import pytest

from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.delta_client import DeltaIndiaClient
from quantedge.execution.models import (
    ConnectionState,
    DeltaOrderRequest,
    DeltaOrderResponse,
    DeltaWalletBalance,
    OrderStatus,
)
from quantedge.execution.multi_user_orchestrator import (
    TradeDirection,
    UserAccountConfig,
    UserExecutionSession,
)
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.instruments import delta_india_registry

#: The leverage `execute_trade` defaults to, reused when recomputing sizing.
LEVERAGE = 10

#: symbol -> (verified product id, entry price, stop, target)
GEOMETRY = {
    "BTCUSD": (27, Decimal("77000"), Decimal("76000"), Decimal("79000")),
    "ETHUSD": (3136, Decimal("2500"), Decimal("2400"), Decimal("2700")),
    "SOLUSD": (14823, Decimal("180"), Decimal("174"), Decimal("192")),
    "XRPUSD": (14969, Decimal("2.8"), Decimal("2.7"), Decimal("3.0")),
}

#: The contradictory catalogue the existing multi-user mock serves: every
#: symbol is claimed to be product 27 with BTCUSD's contract value.
LYING_CATALOGUE = [
    {"id": 27, "symbol": s, "contract_value": "0.001", "tick_size": "0.1"}
    for s in GEOMETRY
]


def _client(mark_price: Decimal, balance: str = "1000.00") -> MagicMock:
    """A funded, flat account whose orders fill immediately."""
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "MU_KEY"
    client._api_secret = "MU_SECRET"
    client.connection_state = ConnectionState.CONNECTED
    client.submitted: list[DeltaOrderRequest] = []

    client.get_wallet_balances = AsyncMock(return_value=[
        DeltaWalletBalance(
            asset_symbol="USDT",
            balance=Decimal(balance),
            available_balance=Decimal(balance),
            position_margin=Decimal("0"),
            order_margin=Decimal("0"),
            blocked_margin=Decimal("0"),
        )
    ])
    client.get_positions = AsyncMock(return_value=[])
    client.get_products = AsyncMock(return_value=list(LYING_CATALOGUE))
    client.get_ticker = AsyncMock(return_value={"mark_price": str(mark_price)})
    client.close = AsyncMock()

    counter = [7000]

    async def _place(req: DeltaOrderRequest) -> DeltaOrderResponse:
        client.submitted.append(req)
        counter[0] += 1
        return DeltaOrderResponse(
            id=counter[0],
            client_order_id=req.client_order_id,
            user_id=1,
            product_id=req.product_id,
            product_symbol=req.product_symbol,
            side=req.side,
            order_type=req.order_type,
            size=req.size,
            unfilled_size=Decimal("0"),
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            average_fill_price=req.limit_price or mark_price,
            state=OrderStatus.FILLED,
            reduce_only=req.reduce_only,
            created_at=datetime.now(timezone.utc),
        )

    client.place_order = AsyncMock(side_effect=_place)

    async def _get_order(order_id: int) -> DeltaOrderResponse:
        req = client.submitted[0]
        return DeltaOrderResponse(
            id=order_id,
            client_order_id=req.client_order_id,
            user_id=1,
            product_id=req.product_id,
            product_symbol=req.product_symbol,
            side=req.side,
            order_type=req.order_type,
            size=req.size,
            unfilled_size=Decimal("0"),
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            average_fill_price=req.limit_price or mark_price,
            state=OrderStatus.FILLED,
            reduce_only=False,
            created_at=datetime.now(timezone.utc),
        )

    client.get_order = AsyncMock(side_effect=_get_order)
    return client


def _account(client: MagicMock, user_id: str = "user_ident") -> UserAccountConfig:
    return UserAccountConfig(
        user_id=user_id,
        account_id=f"acct-{user_id}",
        is_active=True,
        algo_enabled=True,
        kill_switch_active=False,
        api_key="MU_KEY",
        api_secret="MU_SECRET",
        client_factory=lambda _k, _s: client,
    )


async def _execute(symbol: str, client: MagicMock):
    session = UserExecutionSession(
        config=_account(client),
        lock_manager=SingleTradeLockManager(),
        capital_allocator=CapitalAllocator(),
    )
    _pid, entry, stop, target = GEOMETRY.get(
        symbol, (0, Decimal("100"), Decimal("95"), Decimal("110")))
    return await session.execute_trade(
        setup_id=f"setup-{symbol}",
        symbol=symbol,
        direction=TradeDirection.LONG,
        planned_entry_price=entry,
        stop_loss_price=stop,
        take_profit_price=target,
        default_leverage=LEVERAGE,
    )


# ---------------------------------------------------------------------------
# Verified identity reaches every order the orchestrator submits.
# ---------------------------------------------------------------------------
class TestVerifiedIdentityIsUsed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", tuple(GEOMETRY))
    async def test_the_registry_product_id_is_used(self, symbol):
        expected_id, entry, _s, _t = GEOMETRY[symbol]
        client = _client(entry)
        result = await _execute(symbol, client)

        assert result.status == "EXECUTED", result.error
        assert client.submitted, "no order was submitted"
        assert {r.product_id for r in client.submitted} == {expected_id}
        assert {r.product_symbol for r in client.submitted} == {symbol}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", tuple(GEOMETRY))
    async def test_the_id_matches_the_registry_not_the_payload(self, symbol):
        """The catalogue says 27 for every symbol; the registry must win."""
        expected_id, entry, _s, _t = GEOMETRY[symbol]
        client = _client(entry)
        await _execute(symbol, client)
        assert client.submitted[0].product_id == \
            delta_india_registry().get(symbol).product_id
        if symbol != "BTCUSD":
            assert client.submitted[0].product_id != 27

    @pytest.mark.asyncio
    async def test_the_exact_existing_contradiction_is_gone(self):
        """
        `ETHUSD` + `product_id=27` was the reachable contradiction. It must not
        be constructible any more, on the entry order or either bracket.
        """
        client = _client(Decimal("2500"))
        await _execute("ETHUSD", client)
        pairs = {(r.product_symbol, r.product_id) for r in client.submitted}
        assert ("ETHUSD", 27) not in pairs
        assert pairs == {("ETHUSD", 3136)}

    @pytest.mark.asyncio
    async def test_entry_and_both_brackets_share_one_identity(self):
        client = _client(Decimal("77000"))
        result = await _execute("BTCUSD", client)
        assert result.sl_order_id is not None
        assert result.tp_order_id is not None
        assert len(client.submitted) == 3
        assert {(r.product_symbol, r.product_id) for r in client.submitted} == \
            {("BTCUSD", 27)}
        assert sum(1 for r in client.submitted if r.reduce_only) == 2


# ---------------------------------------------------------------------------
# Unusable symbols fail closed, and nothing is submitted.
# ---------------------------------------------------------------------------
class TestAnUnusableSymbolFailsClosed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", ("FOOUSD", "BTCUSDT", "BTC-USD",
                                       "DOGEUSD", "btcusd", " BTCUSD ", ""))
    async def test_an_unregistered_symbol_is_refused(self, symbol):
        client = _client(Decimal("100"))
        result = await _execute(symbol, client)
        assert result.status == "ERROR"
        assert client.place_order.await_count == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", ("BTCUSD.P", "ETHUSD.P", "SOLUSD.P",
                                       "XRPUSD.P"))
    async def test_a_display_suffix_symbol_is_refused(self, symbol):
        """`.P` is display/persistence only; it is not a tradable alias."""
        client = _client(Decimal("100"))
        result = await _execute(symbol, client)
        assert result.status == "ERROR"
        assert "not a registered" in result.error or "not a usable" in result.error
        assert client.place_order.await_count == 0

    @pytest.mark.asyncio
    async def test_a_refusal_does_not_consult_the_products_payload(self):
        """
        The old code discovered the symbol in `get_products()` and raised only
        if it was absent -- a catalogue entry was enough to trade an unknown
        symbol. The registry decides now, so a lying catalogue cannot help.
        """
        client = _client(Decimal("100"))
        client.get_products = AsyncMock(return_value=[
            {"id": 999, "symbol": "FOOUSD", "contract_value": "1",
             "tick_size": "0.1"}])
        result = await _execute("FOOUSD", client)
        assert result.status == "ERROR"
        assert client.place_order.await_count == 0


# ---------------------------------------------------------------------------
# Verified metadata comes from the snapshot; nothing is guessed.
# ---------------------------------------------------------------------------
class TestVerifiedMetadataIsNotGuessed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", tuple(GEOMETRY))
    async def test_the_sizing_uses_the_registry_contract_value(self, symbol):
        """
        The old defaults were `contract_value "1.0"` and `tick_size "0.1"`.
        Quantity is now derived from the snapshot's contract value, so it must
        match a recomputation from the registry rather than from the payload.
        """
        _pid, entry, _s, _t = GEOMETRY[symbol]
        client = _client(entry)
        result = await _execute(symbol, client)
        spec = delta_india_registry().get(symbol)

        expected = CapitalAllocator().calculate_100_percent_allocation(
            symbol=symbol,
            entry_price=entry,
            available_balance=Decimal("1000.00"),
            leverage=LEVERAGE,
            contract_unit=spec.contract_value,
        )
        assert result.allocated_quantity == expected.position_quantity
        assert client.submitted[0].size == expected.position_quantity

    @pytest.mark.asyncio
    async def test_a_lying_contract_value_does_not_reach_the_sizing(self):
        client = _client(Decimal("2500"))
        client.get_products = AsyncMock(return_value=[
            {"id": 3136, "symbol": "ETHUSD", "contract_value": "12345",
             "tick_size": "99"}])
        result = await _execute("ETHUSD", client)
        spec = delta_india_registry().get("ETHUSD")
        expected = CapitalAllocator().calculate_100_percent_allocation(
            symbol="ETHUSD",
            entry_price=Decimal("2500"),
            available_balance=Decimal("1000.00"),
            leverage=LEVERAGE,
            contract_unit=spec.contract_value,
        )
        assert result.allocated_quantity == expected.position_quantity

    def test_no_unverified_field_is_read(self):
        """
        `minimum_order_size`, `size_step`, `max_leverage` and
        `notional_to_contracts` are RECORDED/UNVERIFIED and refuse to be read.
        The orchestrator must not name any of them.
        """
        import inspect
        from quantedge.execution import multi_user_orchestrator as mod

        src = inspect.getsource(mod)
        for name in ("minimum_order_size", "size_step", "max_leverage",
                     "notional_to_contracts"):
            assert name not in src, name


# ---------------------------------------------------------------------------
# No second product table, no fabricated identity.
# ---------------------------------------------------------------------------
class TestNoSecondProductTable:
    def _source(self) -> str:
        import inspect
        from quantedge.execution import multi_user_orchestrator as mod
        return inspect.getsource(mod)

    def test_no_verified_product_id_is_hardcoded(self):
        src = self._source()
        for pid in ("27", "3136", "14823", "14969"):
            assert pid not in src, f"product id {pid} is hardcoded"

    def test_no_native_symbol_is_hardcoded(self):
        src = self._source()
        for symbol in GEOMETRY:
            assert symbol not in src, f"{symbol} is hardcoded"

    def test_identity_is_read_only_from_the_registry(self):
        """
        Structural: the module reads `product_id` from exactly one expression,
        `spec.product_id`, and never from a payload `.get(...)`.
        """
        import ast
        import inspect
        from quantedge.execution import multi_user_orchestrator as mod

        tree = ast.parse(inspect.getsource(mod))
        sources = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "product_id":
                if isinstance(node.value, ast.Name):
                    sources.add(node.value.id)
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and any(isinstance(a, ast.Constant)
                            and a.value in ("id", "contract_value",
                                            "tick_size")
                            for a in node.args)):
                pytest.fail(f"payload identity read at line {node.lineno}")
        assert sources == {"spec", "req", "r"} & sources or sources <= {
            "spec", "req", "r", "self"}, sources

    def test_the_registry_is_the_only_identity_import(self):
        """
        Structural, on the AST rather than raw text so that a comment naming
        the old call cannot satisfy or trip this: the module calls
        `delta_india_registry()` and calls no `get_products` at all.
        """
        import ast
        import inspect
        from quantedge.execution import multi_user_orchestrator as mod

        tree = ast.parse(inspect.getsource(mod))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called.add(node.func.attr)
        assert "delta_india_registry" in called
        assert "get_products" not in called


# ---------------------------------------------------------------------------
# No live API capability was removed.
# ---------------------------------------------------------------------------
class TestNoClientCapabilityWasRemoved:
    def test_get_products_was_never_a_client_method(self):
        """
        Requirement: do not delete a live API capability that has unrelated
        consumers. There is none to delete -- `get_products` is not defined on
        `DeltaIndiaClient` (it exists only on test mocks), so the removed
        `await client.get_products()` could not have run against the real
        client. Pinned so that adding a genuine catalogue endpoint later is a
        deliberate, visible change rather than an accidental revival of an
        identity source.
        """
        assert getattr(DeltaIndiaClient, "get_products", None) is None

    def test_no_production_module_calls_get_products(self):
        """AST-wide, so a comment describing the removed call cannot trip it."""
        import ast
        import pathlib
        import quantedge

        root = pathlib.Path(quantedge.__file__).parent
        offenders = []
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - not expected
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get_products"):
                    offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == [], offenders

    @pytest.mark.asyncio
    async def test_the_orchestrator_does_not_need_a_catalogue(self):
        """A client that cannot list products can still execute."""
        client = _client(Decimal("77000"))
        client.get_products = AsyncMock(
            side_effect=AssertionError("get_products must not be called"))
        result = await _execute("BTCUSD", client)
        assert result.status == "EXECUTED", result.error
        assert client.submitted[0].product_id == 27
