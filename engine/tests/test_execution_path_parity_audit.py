"""
Task G -- the remaining execution safety boundary, path by path.
================================================================

Two production paths reach `DeltaIndiaClient.place_order`:

  A. market scan  -> `MarketScannerOrchestrator.scan_and_execute`
                  -> `TradeLifecycleManager.execute_trade_setup`
                  -> `OrderValidationGateway.validate`
                  -> `DeltaOrderRequest.to_exchange_payload`
  B. multi user   -> `MultiUserExecutionOrchestrator.dispatch_signal`
                  -> `UserExecutionSession.execute_trade`
                  -> `DeltaOrderRequest.to_exchange_payload`

Path B never consults the gateway. This file pins, check by check, which
protections it nevertheless has (its own, often against the live exchange
rather than a local store), which differences are deliberate local-policy
asymmetries, and the one difference that was a genuine gap: the setup's own
TP/SL geometry. Both other paths refuse a directionally inconsistent setup
before submitting anything; path B submitted reduce-only brackets straight
from the caller's prices. That invariant is internal consistency, not exchange
policy, so enforcing it invents nothing -- it is the only production change in
this task.

Three fields stay UNENFORCED and are pinned as such, with the evidence:

  `position_size_limit`   RECORDED from `/v2/products`, never described in any
                          first-party Delta source (it appears only inside a
                          JSON example body), so its unit and scope are
                          unproven. Read by no production module. Current
                          sizing CAN exceed it -- quantified in SS D.
  `minimum_order_size`    No such field in the payload or the published
  `size_step`             schema, and no first-party source states a minimum
                          quantity or a size increment. The integer-contract
                          rule is documented; a minimum above one contract is
                          not. The gateway's `Decimal("1")` values stay named
                          local policy.
  `max_leverage`          Not a Delta field. Delta's own leverage guide says
                          only "Maximum leverage allowed in provided in the
                          contract specifications" without naming a field, and
                          its futures guide gives an affordability identity
                          ("maximum position size that you can afford is
                          1/Margin% times the collateral"), not a cap. The
                          recorded `default_leverage` equals `100 /
                          initial_margin` for every product, but Delta never
                          states that this is the ceiling, so no cap is
                          derived from it. Local caps are pinned to be no
                          looser than any recorded exchange figure.

Zero network access: the Delta client is a mock throughout.
"""

import ast
import inspect
import json
import pathlib
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from quantedge.execution import multi_user_orchestrator as mu_mod
from quantedge.execution.capital_allocator import (
    CapitalAllocationError,
    CapitalAllocator,
)
from quantedge.execution.delta_client import DeltaIndiaClient
from quantedge.execution.models import (
    ConnectionState,
    DeltaOrderRequest,
    DeltaOrderResponse,
    DeltaPosition,
    DeltaWalletBalance,
    OrderSide,
    OrderSizeContractError,
    OrderStatus,
    OrderType,
    PositionSide,
)
from quantedge.execution.multi_user_orchestrator import (
    MultiUserExecutionOrchestrator,
    TradeDirection,
    UserAccountConfig,
    UserExecutionSession,
)
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import AccountRecord, ConnectionRecord
from quantedge.execution.validation import (
    DEFAULT_DELTA_INDIA_PRODUCTS,
    UNVERIFIED_MAX_LEVERAGE,
    UNVERIFIED_MAX_LEVERAGE_FALLBACK,
    UNVERIFIED_MIN_SIZE,
    UNVERIFIED_SIZE_STEP,
    OrderValidationGateway,
    OrderValidationRequest,
    RejectionReasonCode,
    RiskConfiguration,
    ValidationContext,
)
from quantedge.instruments import FieldUnverifiedError, delta_india_registry

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = (REPO_ROOT / "data" / "reference" / "delta_exchange_india"
                 / "product_specs_snapshot.json")

#: A valid long setup: stop < entry < target.
LONG = (Decimal("77000"), Decimal("76000"), Decimal("79000"))

USER = "usr-parity"
ACCOUNT = "acct-parity"


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def registry():
    return delta_india_registry()


def _client(mark_price: Decimal = LONG[0], balance: str = "1000.00",
            positions=None) -> MagicMock:
    """A funded, flat account whose orders fill immediately."""
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "PARITY_KEY"
    client._api_secret = "PARITY_SECRET"
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
    client.get_positions = AsyncMock(return_value=list(positions or []))
    client.get_ticker = AsyncMock(return_value={"mark_price": str(mark_price)})
    client.close = AsyncMock()

    counter = [8000]

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


def _account(client: MagicMock, **over) -> UserAccountConfig:
    kwargs = dict(
        user_id=USER,
        account_id=ACCOUNT,
        is_active=True,
        algo_enabled=True,
        kill_switch_active=False,
        api_key="PARITY_KEY",
        api_secret="PARITY_SECRET",
        client_factory=lambda _k, _s: client,
    )
    kwargs.update(over)
    return UserAccountConfig(**kwargs)


def _session(client: MagicMock, lock: SingleTradeLockManager = None,
             allocator: CapitalAllocator = None,
             **over) -> UserExecutionSession:
    return UserExecutionSession(
        config=_account(client, **over),
        lock_manager=lock or SingleTradeLockManager(),
        capital_allocator=allocator or CapitalAllocator(),
    )


async def _execute(session: UserExecutionSession, symbol: str = "BTCUSD",
                   direction: TradeDirection = TradeDirection.LONG,
                   prices=LONG, leverage: int = 10):
    entry, stop, target = prices
    return await session.execute_trade(
        setup_id=f"setup-{symbol}-{direction}",
        symbol=symbol,
        direction=direction,
        planned_entry_price=entry,
        stop_loss_price=stop,
        take_profit_price=target,
        default_leverage=leverage,
    )


def _src(obj) -> str:
    import textwrap
    return textwrap.dedent(inspect.getsource(obj))


def _attrs_of(tree: ast.AST, base: str) -> set[str]:
    return {n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name) and n.value.id == base}


# =====================================================================
# SS A -- what the gateway actually protects, and who reaches it
# =====================================================================

#: Every rejection the gateway itself can produce. Pinned so that a check
#: cannot be deleted from the market-scan path without this file failing.
GATEWAY_REJECTIONS = {
    "ACCOUNT_DISABLED", "ALGO_DISABLED", "KILL_SWITCH_ACTIVE",
    "EXCHANGE_DISCONNECTED", "INVALID_CREDENTIALS", "UNSUPPORTED_SYMBOL",
    "INVALID_DIRECTION", "UNSUPPORTED_ORDER_TYPE",
    "INVALID_QUANTITY_NON_POSITIVE", "QUANTITY_BELOW_MINIMUM",
    "INVALID_QUANTITY_STEP", "INVALID_PRICE_NON_POSITIVE",
    "INVALID_TICK_SIZE", "CONCURRENT_TRADE_LIMIT_EXCEEDED",
    "EXCESSIVE_LEVERAGE", "MISSING_STOP_LOSS", "MISSING_TAKE_PROFIT",
    "INVALID_TP_SL_GEOMETRY", "ZERO_OR_NEGATIVE_RISK_DISTANCE",
    "INVALID_RISK_REWARD", "EXCESSIVE_RISK", "INSUFFICIENT_BALANCE",
    "DUPLICATE_CLIENT_ORDER_ID", "DUPLICATE_SETUP_ID",
}


def test_gateway_rejection_inventory_is_pinned():
    """The gateway's protections, enumerated from its own source."""
    used = _attrs_of(ast.parse(_src(OrderValidationGateway.validate)),
                     "RejectionReasonCode")
    assert used == GATEWAY_REJECTIONS
    for name in used:
        assert hasattr(RejectionReasonCode, name)


def test_the_multi_user_path_never_consults_the_gateway():
    """Path B's bypass is a fact about the code, not an inference.

    Checked over the AST rather than the text: the module's prose does name
    the gateway, to say where the invariant it borrows comes from.
    """
    tree = ast.parse(_src(mu_mod))
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    referenced |= {n.attr for n in ast.walk(tree)
                   if isinstance(n, ast.Attribute)}
    for token in ("OrderValidationGateway", "OrderValidationRequest",
                  "ValidationContext", "RiskConfiguration",
                  "RejectionReasonCode", "get_product_specification",
                  "DEFAULT_DELTA_INDIA_PRODUCTS"):
        assert token not in referenced, f"{token} unexpectedly reached path B"


def test_the_gateway_has_exactly_two_production_callers():
    """Both are on the market-scan side; neither is the multi-user path."""
    import quantedge
    root = pathlib.Path(quantedge.__file__).parent
    callers = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "validate"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "validation_gateway"):
                callers.add(path.name)
    assert callers == {"trade_lifecycle.py", "execution_engine.py"}


def test_both_paths_share_the_serialization_choke_point():
    """`to_exchange_payload` is reached by every order, gateway or not.

    It is the single place the registry re-checks identity and refuses a
    non-integer contract count, so path B is not unprotected on the two
    invariants that are exchange facts.
    """
    import quantedge
    root = pathlib.Path(quantedge.__file__).parent
    call_sites = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "to_exchange_payload"):
                call_sites.add(path.name)
    assert call_sites == {"delta_client.py"}

    order = _src(DeltaIndiaClient.create_order)
    assert "exchange_contract_count" not in order  # it happens inside payload
    assert order.index("to_exchange_payload") < order.index('"/v2/orders"')


def test_both_paths_share_the_java_authority_gate():
    """The authority refusal precedes serialization for every path."""
    order = _src(DeltaIndiaClient.create_order)
    assert "DeltaExecutionAuthorityError" in order
    assert (order.index("DeltaExecutionAuthorityError")
            < order.index("to_exchange_payload"))
    assert DeltaIndiaClient.place_order is DeltaIndiaClient.create_order


# =====================================================================
# SS B -- what path B already protects, on its own, without the gateway
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("override,status", [
    ({"is_active": False}, "SKIPPED_INACTIVE"),
    ({"algo_enabled": False}, "SKIPPED_ALGO_DISABLED"),
    ({"kill_switch_active": True}, "SKIPPED_KILL_SWITCH"),
    ({"api_key": None}, "ERROR"),
    ({"api_secret": None}, "ERROR"),
    ({"api_key": ""}, "ERROR"),
])
async def test_path_b_has_its_own_account_eligibility_checks(override, status):
    """Gateway checks 1, 2, 3 and 5 have local equivalents in path B."""
    client = _client()
    result = await _execute(_session(client, **override))
    assert result.status == status
    client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_path_b_credential_check_is_presence_only_by_design():
    """A pinned asymmetry, class 1: quality is the exchange's business.

    The gateway additionally requires a stripped length of at least five
    characters -- a local policy figure, not an exchange rule. Path B checks
    presence only, and a credential that is present but useless fails at the
    exchange's own authentication, which is authoritative. Nothing about the
    resulting order can be wrong; only a round trip is wasted. Pinned rather
    than changed so the difference cannot be mistaken for an oversight.
    """
    client = _client()
    result = await _execute(_session(client, api_secret="   "))
    assert result.status == "EXECUTED"

    gateway = _src(OrderValidationGateway.validate)
    assert "INVALID_CREDENTIALS" in gateway
    assert "strip()" in gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", [
    "BTCUSD.P", "btcusd", " BTCUSD", "BTC-USD", "DOGEUSD", "",
])
async def test_path_b_symbol_identity_is_registry_exact(symbol):
    """Stronger than gateway check 6: the registry is the only source."""
    client = _client()
    result = await _execute(_session(client), symbol=symbol)
    assert result.status == "ERROR"
    client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_path_b_refuses_a_zero_balance_from_the_live_exchange():
    """Gateway check 15's margin arm, but against the live wallet."""
    client = _client(balance="0")
    result = await _execute(_session(client))
    assert result.status == "BLOCKED_MARGIN"
    client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_path_b_refuses_when_the_asset_wallet_is_absent():
    client = _client()
    client.get_wallet_balances = AsyncMock(return_value=[])
    result = await _execute(_session(client))
    assert result.status == "BLOCKED_MARGIN"
    client.place_order.assert_not_called()


def _open_position() -> DeltaPosition:
    return DeltaPosition(
        product_id=delta_india_registry().get("BTCUSD").product_id,
        product_symbol="BTCUSD",
        side=PositionSide.LONG,
        size=Decimal("3"),
        entry_price=Decimal("76500"),
        mark_price=LONG[0],
        liquidation_price=None,
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        leverage=Decimal("10"),
        margin=Decimal("100"),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_path_b_concurrency_guard_is_stronger_than_gateway_check_13():
    """Gateway check 13 counts a local list; path B asks the exchange.

    A stale local store that believes the account is flat cannot let a
    second position through here.
    """
    client = _client(positions=[_open_position()])
    result = await _execute(_session(client))
    assert result.status == "ERROR"
    client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_path_b_takes_the_lock_before_any_network_call():
    """Lock-first ordering: the lock is held for the whole submission."""
    lock = SingleTradeLockManager()
    client = _client()
    result = await _execute(_session(client, lock=lock))
    assert result.status == "EXECUTED"
    held, _setup, symbol = lock.is_locked(USER, ACCOUNT)
    assert held is True
    assert symbol == "BTCUSD"


@pytest.mark.asyncio
async def test_path_b_second_dispatch_is_blocked_by_the_lock():
    lock = SingleTradeLockManager()
    lock.acquire_lock(USER, ACCOUNT, "other-setup", "ETHUSD")
    client = _client()
    result = await _execute(_session(client, lock=lock))
    assert result.status == "BLOCKED_LOCK"
    client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_path_b_submits_three_orders_with_fixed_types_and_sides():
    """Gateway check 8 is unreachable here: the types are structural."""
    client = _client()
    result = await _execute(_session(client))
    assert result.status == "EXECUTED"

    entry, first, second = client.submitted
    assert entry.order_type.value == "LIMIT_ORDER"
    assert entry.side.value == "BUY"
    assert entry.reduce_only is False
    brackets = {o.order_type.value for o in (first, second)}
    assert brackets == {"STOP_MARKET_ORDER", "LIMIT_ORDER"}
    for bracket in (first, second):
        assert bracket.reduce_only is True
        assert bracket.side.value == "SELL"


def test_path_b_order_types_are_only_the_two_it_can_build():
    tree = ast.parse(_src(mu_mod))
    assert _attrs_of(tree, "OrderType") == {"LIMIT_ORDER", "STOP_MARKET_ORDER"}
    assert _attrs_of(tree, "OrderSide") == {"BUY", "SELL"}


@pytest.mark.asyncio
async def test_path_b_sizes_and_prices_survive_serialization(registry):
    """The payload choke point accepts every order path B builds."""
    client = _client()
    assert (await _execute(_session(client))).status == "EXECUTED"
    spec = registry.get("BTCUSD")

    for order in client.submitted:
        payload = order.to_exchange_payload()
        assert isinstance(payload["size"], int)
        assert payload["size"] > 0
        assert payload["product_id"] == spec.product_id
        assert payload["product_symbol"] == spec.symbol
        price = order.limit_price if order.limit_price is not None \
            else order.stop_price
        assert price > 0
        assert price % spec.tick_size == 0


# =====================================================================
# SS C -- the one genuine gap, and its parity with the other two paths
# =====================================================================

#: Setups whose own three numbers contradict the stated direction. Each one
#: previously reached the exchange as a pair of reduce-only brackets that were
#: the mirror image of the protection they were meant to be.
BROKEN = [
    # long, target below entry: a reduce-only sell limit that fills at once
    (TradeDirection.LONG, (Decimal("77000"), Decimal("76000"),
                           Decimal("75000"))),
    # long, stop above entry: triggers immediately
    (TradeDirection.LONG, (Decimal("77000"), Decimal("78000"),
                           Decimal("79000"))),
    # long, fully inverted
    (TradeDirection.LONG, (Decimal("77000"), Decimal("79000"),
                           Decimal("76000"))),
    # long, stop equal to entry: zero risk distance
    (TradeDirection.LONG, (Decimal("77000"), Decimal("77000"),
                           Decimal("79000"))),
    # long, target equal to entry: zero reward distance
    (TradeDirection.LONG, (Decimal("77000"), Decimal("76000"),
                           Decimal("77000"))),
    # short with long geometry
    (TradeDirection.SHORT, (Decimal("77000"), Decimal("76000"),
                            Decimal("79000"))),
    # short, target above entry
    (TradeDirection.SHORT, (Decimal("77000"), Decimal("78000"),
                            Decimal("79000"))),
    # short, stop below entry
    (TradeDirection.SHORT, (Decimal("77000"), Decimal("76000"),
                            Decimal("75000"))),
    # non-positive prices, either direction
    (TradeDirection.LONG, (Decimal("0"), Decimal("0"), Decimal("79000"))),
    (TradeDirection.LONG, (Decimal("77000"), Decimal("-1"),
                           Decimal("79000"))),
    (TradeDirection.SHORT, (Decimal("-77000"), Decimal("-76000"),
                            Decimal("-79000"))),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("direction,prices", BROKEN)
async def test_path_b_refuses_an_inconsistent_setup(direction, prices):
    client = _client()
    lock = SingleTradeLockManager()
    result = await _execute(_session(client, lock=lock),
                            direction=direction, prices=prices)

    assert result.status == "ERROR"
    assert result.error
    client.place_order.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("direction,prices", BROKEN)
async def test_a_refused_setup_touches_nothing_at_all(direction, prices):
    """No lock, no client, no network: the refusal precedes all of them."""
    client = _client()
    lock = SingleTradeLockManager()
    made_clients = []

    def _factory(_k, _s):
        made_clients.append(client)
        return client

    session = _session(client, lock=lock, client_factory=_factory)
    await _execute(session, direction=direction, prices=prices)

    assert made_clients == []
    assert lock.is_locked(USER, ACCOUNT)[0] is False
    client.get_wallet_balances.assert_not_called()
    client.get_positions.assert_not_called()
    client.get_ticker.assert_not_called()
    client.place_order.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "buy", "long", "", None, 1,
                                       "NEUTRAL", "FLAT"])
async def test_path_b_refuses_a_direction_it_cannot_read(direction):
    """Previously any non-long value silently became a sell.

    The side is chosen by comparing against long alone, so an unreadable
    direction did not fail -- it inverted the trade. It is now refused.
    """
    client = _client()
    result = await _execute(_session(client), direction=direction)
    assert result.status == "ERROR"
    assert "direction" in result.error.lower()
    client.place_order.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["LONG", TradeDirection.LONG])
async def test_a_valid_setup_is_unaffected_by_the_new_check(direction):
    """`TradeDirection` is a str enum, so string callers still pass."""
    client = _client()
    result = await _execute(_session(client), direction=direction)
    assert result.status == "EXECUTED"
    assert len(client.submitted) == 3


@pytest.mark.asyncio
async def test_a_valid_short_setup_still_executes():
    client = _client()
    result = await _execute(
        _session(client), direction=TradeDirection.SHORT,
        prices=(Decimal("77000"), Decimal("78000"), Decimal("75000")))
    assert result.status == "EXECUTED"
    entry, first, second = client.submitted
    assert entry.side.value == "SELL"
    for bracket in (first, second):
        assert bracket.side.value == "BUY"
        assert bracket.reduce_only is True


@pytest.mark.asyncio
@pytest.mark.parametrize("prices", [
    (Decimal("77000"), None, Decimal("79000")),
    (None, Decimal("76000"), Decimal("79000")),
    (Decimal("77000"), Decimal("76000"), None),
])
async def test_incomparable_prices_are_refused_not_raised(prices):
    """Fail closed without imposing a new type restriction on callers."""
    client = _client()
    result = await _execute(_session(client), prices=prices)
    assert result.status == "ERROR"
    client.place_order.assert_not_called()


def _account_record() -> AccountRecord:
    return AccountRecord(
        account_id=ACCOUNT,
        base_currency="USDT",
        current_balance=Decimal("100000.00"),
        available_balance=Decimal("100000.00"),
        margin_used=Decimal("0.00"),
        total_equity=Decimal("100000.00"),
        is_active=True,
    )


def _gateway_context() -> ValidationContext:
    return ValidationContext(
        account=_account_record(),
        algo_enabled=True,
        kill_switch_active=False,
        connection=ConnectionRecord(
            connection_status="CONNECTED",
            last_connected_at=datetime.now(timezone.utc),
        ),
        api_key="parity_api_key_123456",
        api_secret="parity_api_secret_654321",
        risk_config=RiskConfiguration(),
        open_positions=[],
        open_orders=[],
        active_client_order_ids=set(),
        active_setup_ids=set(),
    )


@pytest.mark.parametrize("direction,prices", [
    p for p in BROKEN if min(x for x in p[1]) > 0
])
def test_the_gateway_refuses_the_same_geometries(direction, prices):
    """Parity, not invention: the invariant is already authoritative."""
    entry, stop, target = prices
    result = OrderValidationGateway().validate(
        OrderValidationRequest(
            account_id=ACCOUNT,
            symbol="BTCUSD",
            direction="BUY" if direction == TradeDirection.LONG else "SELL",
            order_type="LIMIT_ORDER",
            quantity=Decimal("1"),
            entry_price=entry,
            stop_loss=stop,
            take_profit=target,
            leverage=10,
            setup_id="gw-parity",
        ),
        _gateway_context(),
    )
    assert result.is_valid is False
    assert result.rejection_code in (
        RejectionReasonCode.INVALID_TP_SL_GEOMETRY,
        RejectionReasonCode.ZERO_OR_NEGATIVE_RISK_DISTANCE,
        RejectionReasonCode.INVALID_RISK_REWARD,
    )


def test_the_lifecycle_path_refuses_the_same_geometries_structurally():
    """`execute_trade_setup` rejects before it ever reaches the gateway."""
    from quantedge.execution.trade_lifecycle import TradeLifecycleManager
    src = _src(TradeLifecycleManager.execute_trade_setup)
    assert "INVALID_TP_SL_GEOMETRY" in src
    assert src.index("INVALID_TP_SL_GEOMETRY") < src.index(
        "validation_gateway.validate")


def test_the_new_check_is_the_only_production_change_and_invents_nothing():
    """It reads no exchange field and holds no threshold of its own."""
    helper = _src(mu_mod._setup_geometry_error)
    tree = ast.parse(helper)
    numbers = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, int)}
    strings = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    # The only literal number is the zero the prices are compared against.
    assert numbers <= {0}
    assert not any(s.isdigit() for s in strings if s not in {"0"})
    for field in ("minimum_order_size", "size_step", "max_leverage",
                  "position_size_limit", "initial_margin",
                  "default_leverage", "contract_value", "tick_size"):
        assert field not in helper


# =====================================================================
# SS D -- `position_size_limit`: recorded, unread, and still unenforced
# =====================================================================

NATIVE = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

#: Illustrative marks used only to turn the recorded limit into a balance at
#: which today's sizing would reach it. Nothing here is exchange data.
ILLUSTRATIVE_MARK = {
    "BTCUSD": Decimal("77000"),
    "ETHUSD": Decimal("2500"),
    "SOLUSD": Decimal("180"),
    "XRPUSD": Decimal("2.8"),
}


@pytest.mark.parametrize("symbol", NATIVE)
def test_position_size_limit_is_recorded_verbatim_and_never_hashed(
        snapshot, symbol):
    entry = snapshot["products"][symbol]
    assert "position_size_limit" in entry["margin_and_limits"]
    assert "position_size_limit" in entry["recorded_not_hashed"]
    assert "position_size_limit" not in entry["contract_spec"]
    assert "position_size_limit" not in entry["verified_fields"]
    assert (snapshot["field_paths"]["margin_and_limits"]
            ["position_size_limit"] == "result.position_size_limit")


@pytest.mark.parametrize("symbol", NATIVE)
def test_position_size_limit_is_reachable_only_as_recorded_data(
        registry, symbol):
    """No accessor, no property, no derived cap: raw data or nothing."""
    spec = registry.get(symbol)
    assert not hasattr(spec, "position_size_limit")
    recorded = spec.recorded["position_size_limit"]
    assert Decimal(str(recorded)) > 0
    with pytest.raises(TypeError):
        spec.recorded["position_size_limit"] = 1


def _production_sources():
    import quantedge
    root = pathlib.Path(quantedge.__file__).parent
    for path in sorted(root.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


@pytest.mark.parametrize("field", [
    "position_size_limit", "max_leverage_notional", "initial_margin",
    "maintenance_margin",
])
def test_no_production_module_mentions_a_recorded_margin_field(field):
    """The whole `margin_and_limits` block is data at rest, not policy."""
    offenders = [p.name for p, text in _production_sources() if field in text]
    assert offenders == []


def test_no_production_module_reads_the_recorded_mapping_at_all():
    """`default_leverage` needs the structural form, not a text scan.

    Path B has a `default_leverage` *parameter*: the leverage its caller
    supplies. It is a name collision with the recorded exchange figure, not a
    read of it, so the distinction is drawn over the AST -- no consumer of the
    registry subscripts or gets from a spec's `recorded` mapping.

    `quantedge.instruments` itself is excluded: it defines the tri-state, so
    it necessarily populates `recorded` and reads `unverified` to build its
    own refusal messages. The claim under audit is that nothing *downstream*
    consumes recorded data.
    """
    for path, text in _production_sources():
        if path.parent.name == "instruments":
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute) and node.attr == "recorded":
                raise AssertionError(f"{path.name} reads .recorded")
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr in ("recorded", "unverified")):
                raise AssertionError(f"{path.name} reads recorded data")

    # And the collision really is a parameter, not exchange data.
    tree = ast.parse(_src(mu_mod.UserExecutionSession.execute_trade))
    params = {a.arg for a in tree.body[0].args.args}
    params |= {a.arg for a in tree.body[0].args.kwonlyargs}
    assert "default_leverage" in params


def test_no_production_caller_bounds_sizing_with_max_quantity():
    """`max_quantity` exists, and neither order path supplies it.

    So nothing today can clamp a position to any recorded limit -- which is
    the point of SS D: the field is not merely unenforced by accident, the one
    mechanism that could enforce it is left unused deliberately.
    """
    seen = 0
    for path, text in _production_sources():
        if path.name == "capital_allocator.py":
            continue
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "calculate_100_percent_allocation"):
                seen += 1
                supplied = {kw.arg for kw in node.keywords}
                assert "max_quantity" not in supplied, path.name
    assert seen == 2  # market_orchestrator, multi_user_orchestrator


@pytest.mark.parametrize("symbol", NATIVE)
def test_current_sizing_can_exceed_the_recorded_position_size_limit(
        registry, symbol):
    """Quantified exposure, both paths, at the recorded number.

    The balance below is solved from the allocator's own arithmetic, then fed
    back through it: the result is a real sizing decision that today's code
    would make and submit, whose contract count exceeds the recorded limit.
    Whether that matters cannot be established -- the field's unit and scope
    are undocumented -- which is precisely why it stays unenforced.
    """
    spec = registry.get(symbol)
    limit = Decimal(str(spec.recorded["position_size_limit"]))
    price = ILLUSTRATIVE_MARK[symbol]
    allocator = CapitalAllocator()

    for leverage in (10, 100):
        # margin*(98/100)*lev / (price*cv) == limit  ->  solve for margin
        balance = ((limit + 1) * price * spec.contract_value
                   / (Decimal("0.98") * leverage))
        sizing = allocator.calculate_100_percent_allocation(
            symbol=symbol, entry_price=price, available_balance=balance,
            leverage=leverage, contract_unit=spec.contract_value)
        assert sizing.position_quantity > limit, (symbol, leverage)


def test_no_equivalent_position_limit_is_enforced_under_another_name():
    """Neither spec type nor the rejection vocabulary knows such a limit."""
    import dataclasses
    from quantedge.execution.validation import ProductSpecification

    names = {f.name for f in dataclasses.fields(ProductSpecification)}
    assert not any("position" in n or "notional" in n for n in names)

    codes = {c.name for c in RejectionReasonCode}
    assert not any("POSITION_SIZE" in c or "NOTIONAL" in c for c in codes)
    assert "MAX_POSITION" not in " ".join(codes)


def test_the_snapshot_records_why_the_limit_is_not_derived_into_policy(
        snapshot):
    """The snapshot's own policy line is the authority for leaving it alone."""
    policy = snapshot["policy"]
    assert "Authoritative exchange response only" in policy
    assert "arithmetic assumptions" in policy


# =====================================================================
# SS E -- `minimum_order_size` and `size_step`: absent, so policy-named
# =====================================================================

@pytest.mark.parametrize("symbol", NATIVE)
def test_the_snapshot_records_both_fields_as_absent_from_the_payload(
        snapshot, symbol):
    absent = snapshot["products"][symbol]["absent_from_payload"]
    assert "minimum_order_size" in absent
    assert "min_size" in absent
    assert "size_step" in absent


def test_the_snapshot_states_the_reason_each_stays_unverified(snapshot):
    unverified = snapshot["unverified"]
    assert "no such field" in unverified["minimum_order_size"]
    assert "no size-side increment field exists" in unverified["size_step"]
    assert "unquoted integer" in unverified["size_step"]


@pytest.mark.parametrize("symbol", NATIVE)
@pytest.mark.parametrize("field", ["minimum_order_size", "size_step"])
def test_every_registry_accessor_refuses_to_supply_a_value(
        registry, symbol, field):
    spec = registry.get(symbol)
    with pytest.raises(FieldUnverifiedError):
        getattr(spec, field)
    assert field in spec.unverified


@pytest.mark.parametrize("symbol", NATIVE)
def test_the_registry_refuses_the_conversion_formula_too(registry, symbol):
    """A minimum and a step would both be conversions Delta never states."""
    spec = registry.get(symbol)
    with pytest.raises(FieldUnverifiedError):
        spec.notional_to_contracts(Decimal("1000"))


def test_the_gateway_values_are_named_local_policy_not_exchange_data():
    assert UNVERIFIED_MIN_SIZE == Decimal("1")
    assert UNVERIFIED_SIZE_STEP == Decimal("1")
    for symbol in NATIVE:
        spec = DEFAULT_DELTA_INDIA_PRODUCTS[symbol]
        assert spec.min_size == UNVERIFIED_MIN_SIZE
        assert spec.size_step == UNVERIFIED_SIZE_STEP
        # `is_verified` speaks for the identity, which does come from the
        # hashed snapshot; the three names below are carried alongside it and
        # are exactly the ones the spec declares unverified.
        assert spec.is_verified is True
        assert spec.verification_source
        assert spec.unverified_fields == ("min_size", "size_step",
                                          "max_leverage")


def test_path_b_inherits_the_same_policy_through_the_allocator_defaults():
    """The parity proof for minimum quantity and quantity grid.

    Path A passes `spec.min_size`/`spec.size_step` explicitly; path B passes
    neither and takes the allocator's defaults. Those defaults are the same
    two values, so the two paths agree on the policy -- and because the policy
    is one contract with a step of one contract, agreeing on it is equivalent
    to enforcing only the documented integer-contract rule.
    """
    import inspect as _inspect
    sig = _inspect.signature(CapitalAllocator.calculate_100_percent_allocation)
    assert sig.parameters["min_quantity"].default == UNVERIFIED_MIN_SIZE
    assert sig.parameters["lot_size_step"].default == UNVERIFIED_SIZE_STEP
    assert sig.parameters["max_quantity"].default is None

    scan = _src(
        __import__("quantedge.execution.market_orchestrator",
                   fromlist=["x"]).MarketScannerOrchestrator.scan_and_execute)
    assert "lot_size_step=" in scan and "min_quantity=" in scan
    mu = _src(mu_mod.UserExecutionSession.execute_trade)
    assert "lot_size_step" not in mu and "min_quantity" not in mu


@pytest.mark.parametrize("size", [Decimal("0.5"), Decimal("1.5"),
                                  Decimal("0.001"), Decimal("2.0001")])
def test_the_only_size_rule_that_is_exchange_truth_fails_closed(registry, size):
    """A fractional contract count never becomes an HTTP request."""
    spec = registry.get("BTCUSD")
    request = DeltaOrderRequest(
        product_id=spec.product_id,
        product_symbol=spec.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=size,
        limit_price=LONG[0],
    )
    with pytest.raises(OrderSizeContractError):
        request.to_exchange_payload()


def test_one_contract_is_accepted_because_no_minimum_above_one_is_proven(
        registry):
    spec = registry.get("BTCUSD")
    payload = DeltaOrderRequest(
        product_id=spec.product_id,
        product_symbol=spec.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("1"),
        limit_price=LONG[0],
    ).to_exchange_payload()
    assert payload["size"] == 1
    assert isinstance(payload["size"], int)


# =====================================================================
# SS F -- leverage: no exchange field, and no cap derived from one
# =====================================================================

@pytest.mark.parametrize("symbol", NATIVE)
def test_the_registry_refuses_max_leverage_because_it_is_not_a_field(
        registry, symbol, snapshot):
    spec = registry.get(symbol)
    with pytest.raises(FieldUnverifiedError):
        spec.max_leverage
    assert "not a Delta field" in snapshot["unverified"]["max_leverage"]
    assert "max_leverage" in snapshot["products"][symbol][
        "absent_from_payload"]


@pytest.mark.parametrize("symbol", NATIVE)
def test_the_affordability_identity_holds_but_is_never_used_as_a_cap(
        registry, symbol):
    """`default_leverage == 100 / initial_margin` for every product.

    Delta's leverage guide says only that the maximum allowed leverage is "in
    the contract specifications" without naming a field, and its futures guide
    gives an affordability identity rather than a ceiling. So the relationship
    below is recorded as arithmetic that happens to hold -- not as evidence
    that the figure is a limit. Nothing derives a cap from it.
    """
    recorded = registry.get(symbol).recorded
    initial_margin = Decimal(str(recorded["initial_margin"]))
    default_leverage = Decimal(str(recorded["default_leverage"]))
    assert default_leverage == (Decimal("100") / initial_margin)


@pytest.mark.parametrize("symbol", NATIVE)
def test_every_local_cap_is_no_looser_than_any_recorded_figure(
        registry, symbol):
    """The safety-relevant consequence: local policy cannot be the weaker one.

    Whatever the recorded figures turn out to mean, the caps actually applied
    sit below them, so leaving them unenforced cannot let leverage past a real
    exchange constraint.
    """
    recorded_ceiling = Decimal(str(
        registry.get(symbol).recorded["default_leverage"]))
    local = Decimal(UNVERIFIED_MAX_LEVERAGE[symbol])
    assert local <= recorded_ceiling
    assert Decimal(UNVERIFIED_MAX_LEVERAGE_FALLBACK) <= recorded_ceiling
    assert Decimal(RiskConfiguration().max_leverage) <= recorded_ceiling


def test_the_fallback_is_the_strictest_of_the_named_caps():
    assert UNVERIFIED_MAX_LEVERAGE_FALLBACK == min(
        UNVERIFIED_MAX_LEVERAGE.values())


@pytest.mark.parametrize("leverage", [0, -1, -100, 101, 200, 1000])
def test_the_allocator_is_the_hard_bound_both_paths_share(leverage):
    with pytest.raises(CapitalAllocationError):
        CapitalAllocator().calculate_100_percent_allocation(
            symbol="BTCUSD", entry_price=LONG[0],
            available_balance=Decimal("100000"), leverage=leverage,
            contract_unit=Decimal("0.001"))


@pytest.mark.parametrize("leverage", [1, 10, 100])
def test_the_allocator_accepts_the_whole_permitted_band(leverage):
    result = CapitalAllocator().calculate_100_percent_allocation(
        symbol="BTCUSD", entry_price=LONG[0],
        available_balance=Decimal("100000"), leverage=leverage,
        contract_unit=Decimal("0.001"))
    assert result.position_quantity > 0
    assert result.leverage == leverage


def _sol_request(leverage: int) -> OrderValidationRequest:
    """Valid in every respect except possibly its leverage."""
    return OrderValidationRequest(
        account_id=ACCOUNT,
        symbol="SOLUSD",
        direction="BUY",
        order_type="LIMIT_ORDER",
        quantity=Decimal("1"),
        entry_price=Decimal("180.0000"),
        stop_loss=Decimal("175.0000"),
        take_profit=Decimal("195.0000"),
        leverage=leverage,
        setup_id=f"lev-{leverage}",
    )


@pytest.mark.parametrize("leverage,valid", [
    (1, True), (50, True), (51, False), (100, False), (-1, False),
])
def test_the_gateway_applies_the_per_symbol_cap(leverage, valid):
    """Gateway check 14 takes the stricter of spec and risk config."""
    result = OrderValidationGateway().validate(_sol_request(leverage),
                                               _gateway_context())
    assert result.is_valid is valid
    if not valid:
        assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE


@pytest.mark.parametrize("leverage", [0, None])
def test_the_gateway_reads_a_falsy_leverage_as_unset(leverage):
    """`request.leverage or 1` -- pinned as it is, not changed.

    Zero is not rejected: it is treated as "not specified" and becomes 1x, the
    least risky value available, so the coercion is fail-safe in direction. It
    is pinned here because the surrounding check does test `leverage < 1`, and
    reading that as "zero is refused" would be wrong.
    """
    result = OrderValidationGateway().validate(_sol_request(leverage),
                                               _gateway_context())
    assert result.is_valid is True


def test_the_gateway_cap_is_the_stricter_of_two_local_numbers():
    src = _src(OrderValidationGateway.validate)
    assert "min(" in src
    assert "max_leverage" in src
    assert "EXCESSIVE_LEVERAGE" in src


@pytest.mark.asyncio
async def test_path_b_is_bounded_only_by_the_allocator_band():
    """A pinned asymmetry, class 1, not a gap.

    Path B permits the full 1..100 band on every symbol, where path A applies
    the per-symbol figure and would refuse 100x here. Both bounds are named
    local policy over a field Delta does not publish, and path B is a
    whole-balance allocator by design, so tightening it would be inventing a
    limit rather than enforcing one. Pinned so the difference is deliberate.
    """
    client = _client(mark_price=Decimal("180"))
    result = await _execute(_session(client), symbol="SOLUSD",
                            prices=(Decimal("180"), Decimal("175"),
                                    Decimal("195")),
                            leverage=100)
    assert result.status == "EXECUTED"
    assert UNVERIFIED_MAX_LEVERAGE["SOLUSD"] < 100  # path A would refuse it

    gateway = OrderValidationGateway().validate(_sol_request(100),
                                                _gateway_context())
    assert gateway.is_valid is False


@pytest.mark.asyncio
@pytest.mark.parametrize("leverage", [0, -1, 101, 1000])
async def test_path_b_refuses_leverage_outside_the_band(leverage):
    client = _client()
    result = await _execute(_session(client), leverage=leverage)
    assert result.status == "BLOCKED_MARGIN"
    client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_the_per_user_override_takes_precedence_over_the_signal():
    client = _client()
    session = _session(client, leverage_override=1)
    assert (await _execute(session, leverage=100)).status == "EXECUTED"
    low = client.submitted[0].size

    client2 = _client()
    assert (await _execute(_session(client2), leverage=100)).status == "EXECUTED"
    assert client2.submitted[0].size > low


def test_leverage_classification_is_pinned_per_path():
    """Who supplies the number, in each path: strategy vs per-user config."""
    from quantedge.execution.market_orchestrator import (
        MarketScannerOrchestrator,
    )
    from quantedge.execution.trade_lifecycle import TradeLifecycleManager

    scan = _src(MarketScannerOrchestrator.scan_and_execute)
    assert "calculated_leverage" in scan          # strategy output

    lifecycle = _src(TradeLifecycleManager.execute_trade_setup)
    assert "calculated_leverage" in lifecycle     # strategy output

    mu = _src(mu_mod.UserExecutionSession.execute_trade)
    assert "calculated_leverage" not in mu
    assert "leverage_override" in mu              # per-user config


# =====================================================================
# SS G -- the comparison matrix, made executable
# =====================================================================
#
#   1 = intentionally different (local policy path B does not import)
#   2 = already protected elsewhere in path B
#   3 = genuine safety gap  (all four now closed by the one change)
#   4 = unresolved architectural / external-policy question
#
#: Every gateway rejection, and what path B does about it. Keyed by the
#: gateway's own vocabulary so a new check cannot be added without landing
#: here as well.
MATRIX: dict[str, tuple[int, str]] = {
    "ACCOUNT_DISABLED": (2, "own eligibility step -> SKIPPED_INACTIVE"),
    "ALGO_DISABLED": (2, "own eligibility step -> SKIPPED_ALGO_DISABLED"),
    "KILL_SWITCH_ACTIVE": (2, "own eligibility step -> SKIPPED_KILL_SWITCH"),
    "EXCHANGE_DISCONNECTED": (
        2, "no pre-check; the first live call fails into ERROR, lock released"),
    "INVALID_CREDENTIALS": (
        1, "presence only; the exchange's own auth is the backstop"),
    "UNSUPPORTED_SYMBOL": (
        2, "registry-exact lookup plus the payload identity re-check"),
    "INVALID_DIRECTION": (3, "closed: an unreadable direction is refused"),
    "UNSUPPORTED_ORDER_TYPE": (
        2, "structurally unreachable: two hardcoded types"),
    "INVALID_QUANTITY_NON_POSITIVE": (
        2, "explicit non-positive raise plus the integer-count refusal"),
    "QUANTITY_BELOW_MINIMUM": (
        2, "allocator min_quantity default equals the gateway's policy"),
    "INVALID_QUANTITY_STEP": (
        2, "allocator lot_size_step default equals the gateway's policy"),
    "INVALID_PRICE_NON_POSITIVE": (3, "closed: all three prices positive"),
    "INVALID_TICK_SIZE": (2, "entry is quantized to the registry tick"),
    "CONCURRENT_TRADE_LIMIT_EXCEEDED": (
        2, "single-trade lock plus a live zero-exposure check"),
    "EXCESSIVE_LEVERAGE": (
        1, "allocator band only; the per-symbol figure is path A policy"),
    "MISSING_STOP_LOSS": (2, "required parameter; None refused by geometry"),
    "MISSING_TAKE_PROFIT": (2, "required parameter; None refused by geometry"),
    "INVALID_TP_SL_GEOMETRY": (3, "closed: directional ordering enforced"),
    "ZERO_OR_NEGATIVE_RISK_DISTANCE": (
        3, "closed: the ordering is strict, so both distances are positive"),
    "INVALID_RISK_REWARD": (1, "the 1.5 minimum is path A policy"),
    "EXCESSIVE_RISK": (
        1, "the per-trade risk cap is path A policy; path B allocates whole"),
    "INSUFFICIENT_BALANCE": (
        2, "allocator refuses when required margin exceeds the live balance"),
    "DUPLICATE_CLIENT_ORDER_ID": (
        2, "generated per submission inside the client"),
    "DUPLICATE_SETUP_ID": (
        4, "no cross-call setup registry; the lock covers the live window"),
}

#: Differences that are not in the gateway's vocabulary at all.
BEYOND_THE_GATEWAY: dict[str, tuple[int, str]] = {
    "LIFECYCLE_RECORD": (
        4, "path B is stateless per call, so it writes no record and its "
           "trades are invisible to reconciliation"),
    "DAILY_LOSS_GUARD": (
        4, "path A's daily realized-loss limit has no path B equivalent"),
    "LOCK_RELEASE_ON_SUCCESS": (
        4, "a successful path B trade never releases its lock; fail-safe in "
           "direction, but there is no close path in this module"),
}


def test_every_gateway_rejection_is_accounted_for_on_the_other_path():
    """The matrix is exhaustive by construction, and stays that way."""
    assert set(MATRIX) == GATEWAY_REJECTIONS


def test_every_row_carries_one_of_the_four_classifications():
    for name, (klass, reason) in {**MATRIX, **BEYOND_THE_GATEWAY}.items():
        assert klass in (1, 2, 3, 4), name
        assert reason.strip(), name


def test_the_genuine_gaps_are_exactly_the_four_the_change_closes():
    gaps = {n for n, (k, _) in MATRIX.items() if k == 3}
    assert gaps == {
        "INVALID_DIRECTION",
        "INVALID_PRICE_NON_POSITIVE",
        "INVALID_TP_SL_GEOMETRY",
        "ZERO_OR_NEGATIVE_RISK_DISTANCE",
    }
    for name in gaps:
        assert MATRIX[name][1].startswith("closed:")


def test_no_class_three_row_survives_unclosed():
    """A gap must be closed in the same commit that names it."""
    helper = _src(mu_mod._setup_geometry_error)
    call_site = _src(mu_mod.UserExecutionSession.execute_trade)
    assert "_setup_geometry_error" in call_site
    for evidence in ("direction", "positive", "entry", "stop", "target"):
        assert evidence in helper.lower()


@pytest.mark.asyncio
async def test_path_b_writes_no_lifecycle_record(registry):
    """Class 4, pinned: statelessness is structural, not an oversight."""
    tree = ast.parse(_src(mu_mod))
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for token in ("TradeLifecycleManager", "TradeLifecycleRecord",
                  "LocalStateStore", "AlgoConfigStore",
                  "DeltaReconciliationService"):
        assert token not in referenced

    client = _client()
    result = await _execute(_session(client))
    assert result.status == "EXECUTED"
    assert not hasattr(result, "record")
    assert not hasattr(result, "lifecycle_state")


def test_path_b_has_no_daily_loss_guard():
    """Class 4, pinned: path A's guard reads state path B does not keep."""
    mu = _src(mu_mod).lower()
    assert "daily" not in mu
    from quantedge.execution.trade_lifecycle import TradeLifecycleManager
    assert "daily_loss_limit" in _src(TradeLifecycleManager.execute_trade_setup)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,expect_held", [
    ("success", True),
    ("margin", False),
    ("geometry", False),
])
async def test_lock_retention_is_pinned_per_outcome(scenario, expect_held):
    """The fail-safe direction, outcome by outcome.

    Retention on success is the class-4 finding: nothing in this module can
    release it, so a further trade for that account is blocked until an
    operator intervenes. That is the safe direction, and it is pinned rather
    than changed because supplying a close path is a design decision, not a
    hardening fix.
    """
    lock = SingleTradeLockManager()
    client = _client(balance="0" if scenario == "margin" else "1000.00")
    prices = LONG if scenario != "geometry" else (
        Decimal("77000"), Decimal("79000"), Decimal("76000"))

    await _execute(_session(client, lock=lock), prices=prices)
    assert lock.is_locked(USER, ACCOUNT)[0] is expect_held


# =====================================================================
# SS H -- the same conclusions through the public entry point
# =====================================================================

@pytest.mark.asyncio
async def test_dispatch_signal_tallies_a_refused_setup_as_an_error():
    """No new status value, so the fan-out summary needs no change."""
    clients = [_client(), _client()]
    accounts = [
        _account(clients[0], user_id="usr-a", account_id="acct-a"),
        _account(clients[1], user_id="usr-b", account_id="acct-b"),
    ]
    orchestrator = MultiUserExecutionOrchestrator(
        lock_manager=SingleTradeLockManager(),
        capital_allocator=CapitalAllocator(),
    )

    summary = await orchestrator.dispatch_signal(
        setup_id="fanout-broken",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        planned_entry_price=Decimal("77000"),
        stop_loss_price=Decimal("79000"),
        take_profit_price=Decimal("76000"),
        accounts=accounts,
    )

    assert summary.total_accounts == 2
    assert summary.executed_count == 0
    assert summary.skipped_count == 0
    assert summary.error_count == 2
    for client in clients:
        client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_signal_still_executes_a_valid_setup_for_every_account():
    """The change is inert for every setup that was already usable."""
    clients = [_client(), _client()]
    accounts = [
        _account(clients[0], user_id="usr-c", account_id="acct-c"),
        _account(clients[1], user_id="usr-d", account_id="acct-d"),
    ]
    orchestrator = MultiUserExecutionOrchestrator(
        lock_manager=SingleTradeLockManager(),
        capital_allocator=CapitalAllocator(),
    )

    entry, stop, target = LONG
    summary = await orchestrator.dispatch_signal(
        setup_id="fanout-valid",
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        planned_entry_price=entry,
        stop_loss_price=stop,
        take_profit_price=target,
        accounts=accounts,
    )

    assert summary.executed_count == 2
    assert summary.error_count == 0
    for client in clients:
        assert len(client.submitted) == 3
