"""
Durable, idempotent execution state: identity, persistence, and the startup gate.

Every test in this module is served by `httpx.MockTransport` and a temporary
directory. No credential is read, no live endpoint is contacted, and no order,
cancel or modify request leaves the process.

The three safety layers under test, and why each exists:

  A. DETERMINISTIC ORDER IDENTITY. A restarted process must be able to
     recompute the name of an order it has already sent. The random
     `generate_client_order_id` (wall-clock milliseconds + a uuid fragment) made
     that impossible: after a restart the engine could not distinguish its own
     resting entry from any other order on the account, so a replayed setup
     could open a SECOND position. Identity is now derived from
     `(account_id, setup_id, role)` and from nothing else.

  B. DURABLE EXECUTION STATE. `_active_trades`, `_trade_history`, the fill
     accounting, `_reconciliation_alerts` and the single-trade lock lived only
     in memory, so a restart presented a flat book to a manager that might have
     a live -- possibly unprotected -- position on the exchange. State is now
     written atomically and read back before it is trusted, and corrupt or
     incompatible state fails closed instead of degrading to "no trades".

  C. THE STARTUP RECONCILIATION GATE. Loading state is not sufficient: the file
     records what this process last observed, not what the exchange holds now.
     No new entry is admitted until persisted state has been loaded, AND
     reconciliation has returned clean, AND the alert list is empty, AND the
     lock agrees with the records, AND the strategy agrees with execution.
     Ambiguity blocks. Nothing here invents an order id, a fill, a quantity, a
     protection leg or a position in order to resolve it.

Out of scope by decision, so their absence is not an oversight: production
composition of `MarketScannerOrchestrator` / `TradeLifecycleManager`, private
websocket binding, `on_market_update`, and any live-execution flag. The gate is
a boundary, not a wiring -- constructing one wires nothing and opens no socket.
"""

import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

import quantedge.execution as execution_package
import quantedge.execution.trade_lifecycle as trade_lifecycle_module
from quantedge.execution.delta_client import (
    CLIENT_ORDER_ID_MAX_LENGTH,
    DELTA_INDIA_PRODUCTION_URL,
    DETERMINISTIC_CLIENT_ORDER_ID_DIGEST_CHARS,
    DeltaIndiaClient,
    generate_deterministic_client_order_id,
)
from quantedge.execution.execution_state_store import (
    EXECUTION_STATE_SCHEMA,
    EXECUTION_STATE_SCHEMA_VERSION,
    STATE_KEYS,
    ExecutionStateCorruptError,
    ExecutionStateError,
    ExecutionStateIncompatibleError,
    ExecutionStatePersistenceError,
    assert_manager_attributes_classified,
    execution_state_exists,
    export_execution_state,
    load_execution_state,
    persist_execution_state,
    restore_execution_state,
    restore_execution_state_from_file,
    serialize_execution_state_body,
    validate_execution_state_body,
    write_execution_state,
)
from quantedge.execution.market_orchestrator import MarketScannerOrchestrator
from quantedge.execution.models import OrderSide, OrderType, PositionSide
from quantedge.execution.single_trade_lock import (
    SingleTradeLockError,
    SingleTradeLockManager,
)
from quantedge.execution.startup_gate import (
    GATE_REFUSAL_CODE,
    NOT_ARMED_REASON,
    ExecutionStartupGate,
    ExecutionStartupGateError,
    StartupGateResult,
)
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import (
    MAX_PROTECTION_GENERATION,
    ORDER_ROLE_ENTRY,
    ORDER_ROLE_STOP_LOSS,
    ORDER_ROLE_TAKE_PROFIT,
    PROTECTION_SOURCE_ENGINE,
    PROTECTION_SOURCE_EXCHANGE_ADOPTED,
    REFUSAL_MISSING_ENTRY_PRICE,
    REFUSAL_MISSING_LEVERAGE,
    REFUSAL_MISSING_QUANTITY,
    REFUSAL_STARTUP_GATE,
    ProtectionGenerationExhaustedError,
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
    protection_role,
)
from quantedge.execution.validation import OrderValidationGateway, RejectionReasonCode
from quantedge.instruments import delta_india_registry
from quantedge.strategy.models import (
    SetupState,
    StrategyDecision,
    StrategyDirection,
    TradeDirection,
)

PRODUCTION_ROOT = Path(trade_lifecycle_module.__file__).resolve().parents[1]

ACCOUNT = "acc_exec_identity"
USER = "user_exec_identity"
SYMBOL = "BTCUSD"
PRODUCT_ID = delta_india_registry().get(SYMBOL).product_id
SETUP = "BTCUSD_1h_MANUAL_SMC_IDENT_LONG"
OTHER_SETUP = "ETHUSD_1h_MANUAL_SMC_IDENT_LONG"
OTHER_ACCOUNT = "acc2_exec_identity"

ENTRY_PRICE = Decimal("95000.00")
STOP_LOSS = Decimal("94000.00")
TAKE_PROFIT = Decimal("98000.00")
QUANTITY = Decimal("1")
LEVERAGE = 10

ENTRY_ORDER_ID = 970001
SL_ORDER_ID = 970002
TP_ORDER_ID = 970003

#: The three role tokens the canonical submission path may ever mint, and the
#: only three. The vocabulary is closed deliberately: one entry, one stop-loss
#: and one take-profit per setup generation.
ROLES = (ORDER_ROLE_ENTRY, ORDER_ROLE_STOP_LOSS, ORDER_ROLE_TAKE_PROFIT)


# ── Transport plumbing (same shape as §O8/§O13: MockTransport, never a socket) ─


def _ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": result})


def _order_json(order_id: int = ENTRY_ORDER_ID, **over) -> Dict[str, Any]:
    """A well-formed `/v2/orders` object.

    No `bracket_stop_loss_price` / `bracket_take_profit_price`: the exchange is
    stating it holds no attached bracket for this order, so
    `_adopt_exchange_attached_bracket` declines immediately and the engine places
    its own protective pair -- which is the path whose identities this module
    pins. It also means no test here sleeps through the adoption retries.
    """
    payload: Dict[str, Any] = {
        "id": order_id,
        "client_order_id": f"QE-EXEC-{order_id}",
        "user_id": 6021,
        "product_id": PRODUCT_ID,
        "product_symbol": SYMBOL,
        "side": "buy",
        "order_type": "limit_order",
        "size": "1",
        "unfilled_size": "1",
        "limit_price": str(ENTRY_PRICE),
        "stop_price": None,
        "average_fill_price": None,
        "state": "open",
        "reduce_only": False,
        "created_at": 1756339200000000,
    }
    payload.update(over)
    return payload

def _position_json(size: str = "1", **over) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "product_id": PRODUCT_ID,
        "product_symbol": SYMBOL,
        "size": size,
        "entry_price": str(ENTRY_PRICE),
        "mark_price": "95100.00",
        "liquidation_price": "80000.00",
        "unrealised_pnl": "1.00",
        "realised_pnl": "0.00",
        "leverage": str(LEVERAGE),
        "margin": "95.00",
    }
    payload.update(over)
    return payload


def _placed_json(body: Dict[str, Any], order_id: int) -> Dict[str, Any]:
    """Echo a POSTed order back the way Delta would, identity included."""
    return {
        "id": order_id,
        "client_order_id": body.get("client_order_id"),
        "product_id": body.get("product_id", PRODUCT_ID),
        "product_symbol": SYMBOL,
        "side": body.get("side", "buy"),
        "order_type": body.get("order_type", "limit_order"),
        "size": str(body.get("size", "1")),
        "unfilled_size": str(body.get("size", "1")),
        "limit_price": body.get("limit_price"),
        "stop_price": body.get("stop_price"),
        "average_fill_price": None,
        "state": "open",
        "reduce_only": bool(body.get("reduce_only", False)),
        "created_at": 1756339200000000,
    }


class FakeExchange:
    """A deterministic, in-memory Delta serving only the endpoints these paths hit.

    `positions` / `open_orders` are the authoritative world the reconciliation
    boundary reads. `unreachable=True` makes every call fail, which is how the
    "exchange could not be read" branch is exercised without a network.
    """

    def __init__(
        self,
        *,
        positions: Optional[List[Dict[str, Any]]] = None,
        open_orders: Optional[List[Dict[str, Any]]] = None,
        unreachable: bool = False,
    ) -> None:
        self.positions = list(positions or [])
        self.open_orders = list(open_orders or [])
        self.unreachable = unreachable
        self.requests: List[httpx.Request] = []
        self.placed: List[Dict[str, Any]] = []
        self._next_order_id = ENTRY_ORDER_ID

    # ── Observation helpers ───────────────────────────────────────────────────

    def paths(self) -> List[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]

    def reads(self) -> List[str]:
        """Every authoritative-state read: the calls reconciliation makes."""
        return [
            p for p in self.paths()
            if p in ("GET /v2/positions/margined", "GET /v2/orders")
        ]

    def client_order_ids(self) -> List[str]:
        return [body.get("client_order_id") for body in self.placed]

    # ── The responder ─────────────────────────────────────────────────────────

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.unreachable:
            return httpx.Response(503, json={"success": False, "error": "unreachable"})

        if request.method == "POST" and path == "/v2/orders":
            body = json.loads(request.content.decode())
            self.placed.append(body)
            order_id = self._next_order_id
            self._next_order_id += 1
            return _ok(_placed_json(body, order_id))

        if request.method == "GET" and path == "/v2/orders":
            return _ok(list(self.open_orders))

        if request.method == "GET" and path == "/v2/positions/margined":
            return _ok(list(self.positions))

        if request.method == "GET" and path.startswith("/v2/orders/"):
            tail = path.rsplit("/", 1)[-1]
            assert tail.isdigit(), f"unexpected order lookup {path}"
            return _ok(_order_json(order_id=int(tail)))

        raise AssertionError(f"unexpected request {request.method} {path}")


def _client(exchange: FakeExchange) -> DeltaIndiaClient:
    """A client whose every request is answered by `exchange`. Never a socket."""
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(exchange),
        base_url=DELTA_INDIA_PRODUCTION_URL,
    )
    return DeltaIndiaClient(
        api_key="TEST_KEY_EXEC_IDENTITY_0001",
        api_secret="TEST_SECRET_EXEC_IDENTITY_000000001",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        http_client=http,
    )

# ── Local state, manager, decision ────────────────────────────────────────────


def _store(account_id: str = ACCOUNT) -> LocalStateStore:
    """A store that passes the pre-trade gate, so submission is actually reached."""
    store = LocalStateStore(account_id=account_id)
    store.account.user_id = USER
    store.account.total_equity = Decimal("25000.00")
    store.account.available_balance = Decimal("20000.00")
    store.account.current_balance = Decimal("25000.00")
    store.account.margin_used = Decimal("5000.00")
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.account.is_active = True
    store.account.last_synced_at = datetime.now(timezone.utc)
    store.connection.connection_status = "CONNECTED"
    store.connection.api_key_status = "VALID"
    return store


def _manager(
    exchange: FakeExchange,
    *,
    store: Optional[LocalStateStore] = None,
    lock: Optional[SingleTradeLockManager] = None,
) -> TradeLifecycleManager:
    return TradeLifecycleManager(
        client=_client(exchange),
        validation_gateway=OrderValidationGateway(),
        state_store=store if store is not None else _store(),
        single_trade_lock=lock if lock is not None else SingleTradeLockManager(),
        daily_loss_limit=Decimal("5000.00"),
        max_stale_seconds=120,
    )


def _decision(setup_id: str = SETUP, symbol: str = SYMBOL, **over) -> StrategyDecision:
    fields: Dict[str, Any] = dict(
        timestamp=datetime.now(timezone.utc),
        symbol=symbol,
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id=setup_id,
        entry=ENTRY_PRICE,
        stop_loss=STOP_LOSS,
        take_profit=TAKE_PROFIT,
        risk_distance=Decimal("1000.00"),
        reward_distance=Decimal("3000.00"),
        risk_reward=Decimal("3.0"),
        confidence=85.0,
        quantity=QUANTITY,
        calculated_leverage=LEVERAGE,
    )
    fields.update(over)
    return StrategyDecision(**fields)

def _protected_record(setup_id: str = SETUP, **over) -> TradeLifecycleRecord:
    """A record in the state a restart most needs to survive: live and protected.

    Its order ids and client order ids are the real deterministic ones, so a
    round trip that mangled either would be visible rather than plausible.
    """
    record = TradeLifecycleRecord(
        setup_id=setup_id,
        account_id=ACCOUNT,
        user_id=USER,
        symbol=SYMBOL,
        direction=TradeDirection.LONG,
        requested_quantity=QUANTITY,
        entry_price=ENTRY_PRICE,
        stop_loss_price=STOP_LOSS,
        take_profit_price=TAKE_PROFIT,
        risk_reward_ratio=Decimal("3"),
        risk_amount=Decimal("1000"),
        reward_amount=Decimal("3000"),
    )
    record.state = TradeLifecycleState.PROTECTED_POSITION
    record.filled_quantity = QUANTITY
    record.protected_quantity = QUANTITY
    record.average_fill_price = ENTRY_PRICE
    record.entry_order_id = str(ENTRY_ORDER_ID)
    record.sl_order_id = str(SL_ORDER_ID)
    record.tp_order_id = str(TP_ORDER_ID)
    # P2-1: a record that carries exchange order ids was necessarily submitted
    # against a specific product, so it carries that product's id -- exactly as
    # `execute_trade_setup` records it before submitting. A record with order ids
    # and no product id is now refused as internally inconsistent (the product
    # those orders live on could then only be guessed from the current registry),
    # which is why the fixture states it rather than leaving it None.
    record.product_id = PRODUCT_ID
    record.protection_source = PROTECTION_SOURCE_ENGINE
    record.entry_client_order_id = generate_deterministic_client_order_id(
        ACCOUNT, setup_id, ORDER_ROLE_ENTRY)
    record.sl_client_order_id = generate_deterministic_client_order_id(
        ACCOUNT, setup_id, ORDER_ROLE_STOP_LOSS)
    record.tp_client_order_id = generate_deterministic_client_order_id(
        ACCOUNT, setup_id, ORDER_ROLE_TAKE_PROFIT)
    for key, value in over.items():
        setattr(record, key, value)
    return record


def _clean_world(setup_id: str = SETUP) -> FakeExchange:
    """The exchange as it must look for `_protected_record` to reconcile clean.

    One position of exactly the protected size, and both protective legs still
    resting. Anything less than this is a finding, not a clean book.
    """
    return FakeExchange(
        positions=[_position_json()],
        open_orders=[
            _order_json(order_id=SL_ORDER_ID, state="pending", reduce_only=True),
            _order_json(order_id=TP_ORDER_ID, reduce_only=True),
        ],
    )

# ── The strategy-runtime shape the gate compares against ──────────────────────


class _FakeOB:
    def __init__(self, ob_id: str = "OB-1") -> None:
        self.ob_id = ob_id


class _FakeActiveTrade:
    def __init__(self, asset: str = SYMBOL, ob_id: str = "OB-1") -> None:
        self.asset = asset
        self.ob = _FakeOB(ob_id)
        self.direction = "LONG"


class _FakeLifecycle:
    def __init__(self, active_trade: Optional[_FakeActiveTrade]) -> None:
        self.active_trade = active_trade


class _FakeStrategy:
    def __init__(
        self, active_trade: Optional[_FakeActiveTrade], *, raises: bool = False
    ) -> None:
        self.lifecycle = _FakeLifecycle(active_trade)
        self._raises = raises

    def has_active_trade(self) -> bool:
        if self._raises:
            raise RuntimeError("strategy open-trade state is unreadable")
        return self.lifecycle.active_trade is not None


class _FakeRuntime:
    """Exactly the `ManualSMCRuntime` surface the gate reads, and nothing else.

    A fake rather than the real runtime on purpose: the gate must be provable
    against a strategy that agrees, one that disagrees, and one that cannot be
    read at all, and only the disagreements matter here.
    """

    def __init__(
        self,
        *,
        setup_id: Optional[str] = None,
        holds_trade: bool = False,
        raises: bool = False,
        setup_id_raises: bool = False,
    ) -> None:
        self.strategy = _FakeStrategy(
            _FakeActiveTrade() if holds_trade else None, raises=raises
        )
        self._setup_id = setup_id
        self._setup_id_raises = setup_id_raises

    def setup_id_for(self, asset: Any, ob_id: Any, direction: Any) -> Optional[str]:
        if self._setup_id_raises:
            raise RuntimeError("setup id could not be derived")
        return self._setup_id

# ── Source inspection (AST, so prose never counts as use) ─────────────────────


def _source(relative: str) -> str:
    return (PRODUCTION_ROOT / relative).read_text(encoding="utf-8")


def _imported(relative: str) -> set:
    """Every name a production module actually imports, by AST not by substring."""
    tree = ast.parse(_source(relative))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.rpartition(".")[2] for a in node.names)
    return names


def _called(relative: str) -> set:
    """Every simple/attribute function name a production module actually calls."""
    tree = ast.parse(_source(relative))
    names: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _production_files() -> List[Path]:
    return sorted(PRODUCTION_ROOT.rglob("*.py"))


# ══ A. Deterministic order identity ═══════════════════════════════════════════


@pytest.mark.asyncio
async def test_a1_entry_sl_and_tp_are_named_by_the_deterministic_triple():
    """The reachable submission path mints exactly three ids, all derived.

    Not "three ids that happen to differ": each one is recomputed here from
    `(account_id, setup_id, role)` and compared to what was actually POSTed, so
    a reintroduced random or timestamped name fails rather than passes.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, USER)
    assert record.state is TradeLifecycleState.ENTRY_SUBMITTED, record.error_message

    await manager.on_entry_fill(SETUP, QUANTITY, ENTRY_PRICE)
    assert record.state is TradeLifecycleState.PROTECTED_POSITION

    assert exchange.client_order_ids() == [
        generate_deterministic_client_order_id(ACCOUNT, SETUP, role) for role in ROLES
    ]
    assert record.entry_client_order_id == exchange.client_order_ids()[0]
    assert record.sl_client_order_id == exchange.client_order_ids()[1]
    assert record.tp_client_order_id == exchange.client_order_ids()[2]

@pytest.mark.asyncio
async def test_a2_the_same_setup_restarted_submits_under_the_same_three_ids():
    """A restart recomputes the identity it already sent, rather than a new one.

    The second stack shares nothing with the first: new transport, new client,
    new store, new lock, new manager. The only inputs in common are the account
    id, the setup id and the role -- which is the whole point.
    """
    first = FakeExchange()
    manager_one = _manager(first)
    await manager_one.execute_trade_setup(_decision(), ACCOUNT, USER)
    await manager_one.on_entry_fill(SETUP, QUANTITY, ENTRY_PRICE)

    second = FakeExchange()
    manager_two = _manager(second)
    await manager_two.execute_trade_setup(_decision(), ACCOUNT, USER)
    await manager_two.on_entry_fill(SETUP, QUANTITY, ENTRY_PRICE)

    assert first.client_order_ids() == second.client_order_ids()
    assert len(first.client_order_ids()) == 3
    # The old random generator embedded `int(time.time() * 1000)` and six uuid
    # hex characters, so two runs of this test could never have agreed.
    assert all("-" in cid for cid in first.client_order_ids())


def test_a3_a_different_setup_role_or_account_gets_a_different_id():
    """Determinism is not collision: each of the three inputs separates ids."""
    base = {
        role: generate_deterministic_client_order_id(ACCOUNT, SETUP, role)
        for role in ROLES
    }
    assert len(set(base.values())) == 3, "one setup's three roles must not collide"
    for role, cid in base.items():
        assert cid.endswith(f"-{role}"), "every id must be role-scoped and readable"

    other_setup = {
        role: generate_deterministic_client_order_id(ACCOUNT, OTHER_SETUP, role)
        for role in ROLES
    }
    other_account = {
        role: generate_deterministic_client_order_id(OTHER_ACCOUNT, SETUP, role)
        for role in ROLES
    }
    assert set(base.values()).isdisjoint(other_setup.values())
    assert set(base.values()).isdisjoint(other_account.values())
    # Same triple, called again: identical. Called with the role in a different
    # case: still identical, because the generator upper-cases the role -- so a
    # caller cannot mint a second name for the same leg by spelling it "sl".
    for role in ROLES:
        assert generate_deterministic_client_order_id(ACCOUNT, SETUP, role) == base[role]
        assert generate_deterministic_client_order_id(
            ACCOUNT, SETUP, role.lower()) == base[role]

def test_a3b_the_old_truncation_collision_surface_is_closed():
    """The prefix-truncation collision this test once asserted is now impossible.

    Until the P0-1 fix, `generate_deterministic_client_order_id` stripped dashes
    and kept only the first 8 characters of the account id and the first 12 of
    the setup id, so two account ids -- or two setup ids -- agreeing within those
    prefixes produced the SAME order name. This test asserted that collision, on
    the reasoning that it was a deployment constraint to record rather than a
    defect to fix.

    That reasoning was wrong, and the assertions are inverted here rather than
    deleted, so the regression is pinned from the exact inputs that used to
    collide. `BTCUSD_1h_MANUAL_SMC_IDENT_LONG` and `..._IDENT_SHORT` share the
    first 12 characters `BTCUSD_1h_MA` and differ only past it; under the old
    scheme both sides of the same setup submitted under one name, and
    `_resolve_entry_order` could bind either one's exchange order into the other's
    record. The complete identity is now hashed, so equality of names requires
    equality of all three inputs.
    """
    base = generate_deterministic_client_order_id(ACCOUNT, SETUP, ORDER_ROLE_ENTRY)

    twin_account = generate_deterministic_client_order_id(
        "acc_exec_identity_TWO", SETUP, ORDER_ROLE_ENTRY)
    assert twin_account != base, (
        "two account ids sharing the first 8 characters must not share an order name")

    twin_setup = generate_deterministic_client_order_id(
        ACCOUNT, "BTCUSD_1h_MANUAL_SMC_IDENT_SHORT", ORDER_ROLE_ENTRY)
    assert twin_setup != base, (
        "LONG and SHORT setups sharing the first 12 characters must not share a name")

    # A longer shared prefix does not help either: identity is the whole string.
    assert generate_deterministic_client_order_id(
        ACCOUNT, SETUP + "_EXTRA", ORDER_ROLE_ENTRY) != base
    assert generate_deterministic_client_order_id(
        ACCOUNT + "_EXTRA", SETUP, ORDER_ROLE_ENTRY) != base

    # Field boundaries cannot be shifted to forge a name: the identity encoding
    # is length-prefixed, so moving a character from the account into the setup
    # is a different identity even though the concatenation is unchanged.
    assert generate_deterministic_client_order_id(
        "acc_exec_identit", "y" + SETUP, ORDER_ROLE_ENTRY) != base

    # And the cap the old scheme truncated to fit is still respected.
    for role in ROLES:
        assert len(generate_deterministic_client_order_id(ACCOUNT, SETUP, role)) <= 32


def test_a4_a_replacement_bracket_generation_is_deterministic_and_distinct():
    """Resizing protection re-names the legs without replaying a spent name.

    A resized bracket cancels the live pair and places a new one. Replaying the
    cancelled leg's own client_order_id would depend on Delta's replay semantics,
    which are UNVERIFIED, and a refusal there would leave a filled position
    unprotected. So the generation counter -- itself part of the durable record
    -- feeds the SAME generator: still deterministic across a restart, still one
    identity scheme.
    """
    assert protection_role(ORDER_ROLE_STOP_LOSS, 0) == ORDER_ROLE_STOP_LOSS
    assert protection_role(ORDER_ROLE_TAKE_PROFIT, 0) == ORDER_ROLE_TAKE_PROFIT
    assert protection_role(ORDER_ROLE_STOP_LOSS, 1) == "SL2"
    assert protection_role(ORDER_ROLE_TAKE_PROFIT, 1) == "TP2"

    generations = [
        generate_deterministic_client_order_id(
            ACCOUNT, SETUP, protection_role(ORDER_ROLE_STOP_LOSS, gen))
        for gen in range(0, MAX_PROTECTION_GENERATION + 1)
    ]
    assert len(set(generations)) == len(generations), (
        "every generation within the cap must have its own client_order_id")

    # This assertion used to run the other way: the generator truncated the role
    # to five characters, so "SL1000" and "SL100" were one name and generation
    # 999 collided with generation 99. The complete role is now hashed, so the
    # two are distinct -- inverted here rather than dropped, to pin it.
    assert generate_deterministic_client_order_id(
        ACCOUNT, SETUP, "SL1000") != generate_deterministic_client_order_id(
        ACCOUNT, SETUP, "SL100")
    # The cap is nevertheless retained at its original value, for a different and
    # still-good reason: a setup that has resized its protection a thousand times
    # is a runaway loop, not a trade, and failing closed on it costs nothing.
    with pytest.raises(ProtectionGenerationExhaustedError):
        protection_role(ORDER_ROLE_STOP_LOSS, MAX_PROTECTION_GENERATION + 1)
    with pytest.raises(ProtectionGenerationExhaustedError):
        protection_role(ORDER_ROLE_STOP_LOSS, -1)

# ══ B. Durable execution state ════════════════════════════════════════════════


def _populated(exchange: FakeExchange) -> TradeLifecycleManager:
    """A manager with EVERY durable collection non-empty.

    A round-trip test that only populated `_active_trades` would pass while the
    fill accounting silently vanished, and lost fill accounting is how a
    duplicate fill gets applied twice after a restart. So all ten classified
    durable attributes carry data here.
    """
    manager = _manager(exchange)
    record = _protected_record()
    manager._active_trades[SETUP] = record
    manager._trade_history.append(
        # P1-7: `closed_at` is stated alongside the terminal state because
        # `record_transition` sets the two together -- a POSITION_CLOSED record
        # with no close time is a contradiction the store now refuses, and a
        # fixture that reproduced it would be asserting on a record production
        # cannot produce.
        _protected_record(
            OTHER_SETUP,
            state=TradeLifecycleState.POSITION_CLOSED,
            closed_at=datetime(2026, 9, 4, 15, 30, tzinfo=timezone.utc),
        )
    )
    manager._reconciliation_alerts.append({
        "code": "ORPHAN_EXCHANGE_ORDER",
        "symbol": SYMBOL,
        "details": "exchange order 555 is not tracked locally",
        "timestamp": "2026-09-05T09:00:00+00:00",
    })
    manager._realized_daily_losses[date(2026, 9, 4)] = Decimal("-125.50")
    manager._observed_fill_fees[SETUP] = Decimal("0.47")
    manager._observed_fill_fees[OTHER_SETUP] = None
    manager._processed_fill_trade_ids.update({"trade-1", "trade-2"})
    manager._entry_fill_sizes[SETUP] = QUANTITY
    manager._exit_fill_sizes[OTHER_SETUP] = Decimal("0.5")
    manager._exit_fill_notional[OTHER_SETUP] = Decimal("47500.00")
    manager._exit_fill_roles[OTHER_SETUP] = {"tp", "sl"}
    manager.single_trade_lock.acquire_lock(
        user_id=USER, account_id=ACCOUNT, setup_id=SETUP, symbol=SYMBOL)
    return manager


def test_b1_the_state_body_round_trips_through_a_file_unchanged():
    """Export -> validate -> serialize -> write -> load -> restore -> export.

    The two bodies are compared as whole objects, so a field that decoded to a
    different type (a str where a Decimal belongs) fails here even though it
    would still serialize.
    """
    exported = export_execution_state(_populated(FakeExchange()))
    assert set(exported) == set(STATE_KEYS)
    assert exported["execution_state_schema"] == EXECUTION_STATE_SCHEMA
    assert exported["execution_state_schema_version"] == EXECUTION_STATE_SCHEMA_VERSION

    restored_into = _manager(FakeExchange())
    restore_execution_state(restored_into, copy.deepcopy(exported))
    assert export_execution_state(restored_into) == exported

def test_b2_persistence_is_atomic_and_read_back_before_it_is_trusted(tmp_path):
    """The bytes on disk are re-read and compared before the write is accepted.

    Also: no `.tmp` sibling survives, so a crashed write cannot be mistaken for
    state later; and two persists of the same manager produce byte-identical
    files, which is what makes the gate's fingerprint comparison meaningful.
    """
    manager = _populated(FakeExchange())
    path = tmp_path / "execution_state.json"
    assert execution_state_exists(path) is False

    written = persist_execution_state(manager, path)
    assert written == path
    assert execution_state_exists(path) is True

    expected = serialize_execution_state_body(export_execution_state(manager))
    assert path.read_bytes() == expected
    assert list(tmp_path.iterdir()) == [path], "no temporary file may be left behind"

    again = persist_execution_state(manager, path)
    assert again.read_bytes() == expected, "serialization must be deterministic"

    # The file is real JSON with sorted keys, so a human (or a diff) can read it.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert list(on_disk) == sorted(STATE_KEYS)


def test_b3_an_active_trade_survives_a_restart_with_its_identity_intact(tmp_path):
    """A restart restores the ORDER IDENTITY, not merely the fact of a trade.

    The order ids, the deterministic client order ids, the role separation, the
    fill/protection quantities and the protection generation all come back
    exactly. Anything less and the restarted process cannot recognise the orders
    it already has resting on the exchange.
    """
    path = tmp_path / "execution_state.json"
    before = _populated(FakeExchange())
    before._active_trades[SETUP].protection_generation = 2
    persist_execution_state(before, path)

    after = _manager(FakeExchange())
    assert after._active_trades == {}
    summary = restore_execution_state_from_file(after, path)
    assert set(summary) == set(STATE_KEYS)

    record = after._active_trades[SETUP]
    original = before._active_trades[SETUP]
    assert record is not original
    assert record.setup_id == SETUP
    assert record.state is TradeLifecycleState.PROTECTED_POSITION
    assert record.entry_order_id == str(ENTRY_ORDER_ID)
    assert record.sl_order_id == str(SL_ORDER_ID)
    assert record.tp_order_id == str(TP_ORDER_ID)
    assert record.entry_client_order_id == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, ORDER_ROLE_ENTRY)
    assert record.sl_client_order_id == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, ORDER_ROLE_STOP_LOSS)
    assert record.tp_client_order_id == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, ORDER_ROLE_TAKE_PROFIT)
    assert record.filled_quantity == QUANTITY
    assert record.protected_quantity == QUANTITY
    assert record.protection_generation == 2, (
        "the generation counter decides the NEXT bracket's name; losing it would "
        "replay a spent client_order_id")
    assert [r.setup_id for r in after._trade_history] == [OTHER_SETUP]

def test_b4_the_single_trade_lock_survives_a_restart(tmp_path):
    """A restart must not silently free the one-trade-at-a-time lock.

    A lock lost across a restart is the unsafe direction: the fresh process
    would happily open a SECOND concurrent trade. The restored lock must name
    the same setup and the same symbol, so the restarted process refuses a
    different setup and still recognises a replay of the same one.
    """
    path = tmp_path / "execution_state.json"
    before = _populated(FakeExchange())
    assert before.single_trade_lock.is_locked(USER, ACCOUNT) == (True, SETUP, SYMBOL)
    persist_execution_state(before, path)

    lock = SingleTradeLockManager()
    after = _manager(FakeExchange(), lock=lock)
    assert lock.is_locked(USER, ACCOUNT) == (False, None, None)

    restore_execution_state_from_file(after, path)
    assert lock.is_locked(USER, ACCOUNT) == (True, SETUP, SYMBOL)

    # The restored lock behaves like a held lock, not like a decorative record.
    with pytest.raises(SingleTradeLockError, match=SETUP):
        lock.acquire_lock(USER, ACCOUNT, OTHER_SETUP, SYMBOL)
    assert lock.acquire_lock(USER, ACCOUNT, SETUP, SYMBOL) is True, (
        "the same setup must still be able to continue after a restart")


def test_b5_a_body_that_fails_halfway_leaves_the_manager_untouched(tmp_path):
    """No partial state -- in memory, not only on disk.

    `realized_daily_losses` is decoded after the records and the alerts, so a
    bad date there proves the commit point is genuinely after ALL decoding: the
    victim manager keeps its own state instead of ending up half donor,
    half itself.
    """
    donor = _manager(FakeExchange())
    donor._active_trades[OTHER_SETUP] = _protected_record(setup_id=OTHER_SETUP)
    body = copy.deepcopy(export_execution_state(donor))
    body["realized_daily_losses"] = {"2026-13-99": "1.0"}

    victim = _populated(FakeExchange())
    untouched = copy.deepcopy(export_execution_state(victim))

    with pytest.raises(ExecutionStateCorruptError, match="not an ISO date"):
        restore_execution_state(victim, body)

    assert export_execution_state(victim) == untouched
    assert set(victim._active_trades) == {SETUP}
    assert OTHER_SETUP not in victim._active_trades


def test_b6_unknown_manager_state_refuses_to_serialize(tmp_path):
    """"Do not silently discard unknown execution state."

    Adding an attribute to the manager without classifying it as durable or
    non-durable makes export REFUSE, rather than quietly dropping it from every
    future restart. The same guard fires in the other direction when a
    classified attribute disappears, so a rename cannot silently stop being
    persisted either.
    """
    manager = _populated(FakeExchange())
    assert export_execution_state(manager)

    manager._pending_hedge_orders = {"BTCUSD": "1"}
    with pytest.raises(ExecutionStatePersistenceError) as unclassified:
        export_execution_state(manager)
    assert "_pending_hedge_orders" in str(unclassified.value)
    assert "unclassified=" in str(unclassified.value)

    del manager._pending_hedge_orders
    assert export_execution_state(manager)

    # The guard also protects RESTORE, so a stale binary cannot load a body it
    # would not be able to write back.
    body = copy.deepcopy(export_execution_state(manager))
    manager._pending_hedge_orders = {}
    with pytest.raises(ExecutionStatePersistenceError):
        restore_execution_state(manager, body)
    del manager._pending_hedge_orders

    removed = _populated(FakeExchange())
    del removed._observed_fill_fees
    with pytest.raises(ExecutionStatePersistenceError) as absent:
        export_execution_state(removed)
    assert "_observed_fill_fees" in str(absent.value)
    assert "absent=" in str(absent.value)

# ══════════════════════════════════════════════════════════════════════════════
# Section C -- corrupt or incompatible state FAILS CLOSED
#
# Every one of these bodies is refused rather than repaired. The unsafe
# direction is always the same: interpreting a damaged file as "no live trades"
# would let the process open a new position on top of an existing one.
# ══════════════════════════════════════════════════════════════════════════════


def _valid_body() -> Dict[str, Any]:
    """A body that validates, as the starting point for each mutation below."""
    return copy.deepcopy(export_execution_state(_populated(FakeExchange())))


def test_c1_a_body_that_is_not_this_schema_at_this_version_is_incompatible():
    """A foreign writer or a future build is INCOMPATIBLE, never best-effort.

    The distinction matters operationally: incompatible says "another program
    or another build owns this file", corrupt says "this file is damaged". Both
    block, but they point at different fixes.
    """
    assert validate_execution_state_body(_valid_body())

    foreign = _valid_body()
    foreign["execution_state_schema"] = "quantedge.structure_snapshot"
    with pytest.raises(ExecutionStateIncompatibleError, match="foreign file"):
        validate_execution_state_body(foreign)

    missing_schema = _valid_body()
    del missing_schema["execution_state_schema"]
    with pytest.raises(ExecutionStateIncompatibleError):
        validate_execution_state_body(missing_schema)

    future = _valid_body()
    future["execution_state_schema_version"] = EXECUTION_STATE_SCHEMA_VERSION + 1
    with pytest.raises(ExecutionStateIncompatibleError, match="not supported"):
        validate_execution_state_body(future)

    for bogus in ("1", 1.0, True, None):
        wrong_type = _valid_body()
        wrong_type["execution_state_schema_version"] = bogus
        with pytest.raises(ExecutionStateIncompatibleError, match="expected an int"):
            validate_execution_state_body(wrong_type)


def test_c2_a_body_of_the_wrong_shape_is_corrupt():
    """A key set or container type that is not exactly the schema is refused.

    An UNKNOWN key is refused as firmly as a missing one -- silently ignoring it
    is how a future build's live-trade facts would get discarded.
    """
    with pytest.raises(ExecutionStateCorruptError, match="must be an object"):
        validate_execution_state_body([])
    with pytest.raises(ExecutionStateCorruptError, match="must be an object"):
        validate_execution_state_body("{}")

    dropped = _valid_body()
    del dropped["active_trades"]
    with pytest.raises(ExecutionStateCorruptError, match="missing=\\['active_trades'\\]"):
        validate_execution_state_body(dropped)

    extra = _valid_body()
    extra["shadow_positions"] = {}
    with pytest.raises(ExecutionStateCorruptError, match="unknown=\\['shadow_positions'\\]"):
        validate_execution_state_body(extra)

    for key in ("active_trades", "single_trade_lock", "observed_fill_fees"):
        as_list = _valid_body()
        as_list[key] = []
        with pytest.raises(ExecutionStateCorruptError, match="expected an object"):
            validate_execution_state_body(as_list)

    for key in ("trade_history", "reconciliation_alerts", "processed_fill_trade_ids"):
        as_object = _valid_body()
        as_object[key] = {}
        with pytest.raises(ExecutionStateCorruptError, match="expected a list"):
            validate_execution_state_body(as_object)


def test_c3_an_ambiguous_active_trade_key_is_refused_not_resolved():
    """A key that disagrees with the record it holds is ambiguous -> refused.

    The manager looks trades up by this key, so a disagreement means two
    different answers to "which setup is live". Guessing either one could place
    a bracket against the wrong position.
    """
    body = _valid_body()
    record = body["active_trades"].pop(SETUP)
    body["active_trades"][OTHER_SETUP] = record

    with pytest.raises(ExecutionStateCorruptError, match="ambiguous"):
        restore_execution_state(_manager(FakeExchange()), body)

    victim = _populated(FakeExchange())
    unchanged = copy.deepcopy(export_execution_state(victim))
    with pytest.raises(ExecutionStateCorruptError):
        restore_execution_state(victim, body)
    assert export_execution_state(victim) == unchanged


def test_c4_a_lock_entry_that_is_not_exactly_six_fields_is_refused():
    """A truncated lock entry would load as `is_locked=False` -- the unsafe way.

    `AccountTradeLockState.from_dict` reads most of its fields with `.get()`, so
    a damaged entry does not fail loudly on its own; it quietly becomes an
    UNLOCKED account, which permits a second concurrent trade. The shape check
    is what turns that into a refusal.
    """
    body = _valid_body()
    key = f"{USER}:{ACCOUNT}"
    assert body["single_trade_lock"][key]["is_locked"] is True

    truncated = copy.deepcopy(body)
    del truncated["single_trade_lock"][key]["active_setup_id"]
    with pytest.raises(ExecutionStateCorruptError):
        restore_execution_state(_manager(FakeExchange()), truncated)

    padded = copy.deepcopy(body)
    padded["single_trade_lock"][key]["shadow_flag"] = True
    with pytest.raises(ExecutionStateCorruptError):
        restore_execution_state(_manager(FakeExchange()), padded)

    retyped = copy.deepcopy(body)
    retyped["single_trade_lock"][key]["is_locked"] = "true"
    with pytest.raises(ExecutionStateCorruptError):
        restore_execution_state(_manager(FakeExchange()), retyped)

    not_an_object = copy.deepcopy(body)
    not_an_object["single_trade_lock"][key] = ["locked"]
    with pytest.raises(ExecutionStateCorruptError):
        restore_execution_state(_manager(FakeExchange()), not_an_object)

    # The composite key must agree with the fields inside the entry, or which
    # account the lock belongs to has two answers.
    relabelled = copy.deepcopy(body)
    relabelled["single_trade_lock"][f"{USER}:{OTHER_ACCOUNT}"] = (
        relabelled["single_trade_lock"].pop(key))
    with pytest.raises(ExecutionStateCorruptError, match="composite key disagrees"):
        restore_execution_state(_manager(FakeExchange()), relabelled)


def test_c5_an_unreadable_file_is_a_refusal_not_an_empty_state(tmp_path):
    """Absence, truncation and garbage all raise. None of them means "no trades".

    `execution_state_exists` is the ONLY sanctioned way to ask whether a first
    ever start is legitimate; `load_execution_state` never answers that question
    with a default.
    """
    path = tmp_path / "execution_state.json"
    assert execution_state_exists(path) is False
    with pytest.raises(ExecutionStateCorruptError, match="no execution state"):
        load_execution_state(path)

    good = serialize_execution_state_body(_valid_body())
    path.write_bytes(good)
    assert load_execution_state(path)

    path.write_bytes(good[: len(good) // 2])
    with pytest.raises(ExecutionStateCorruptError, match="not valid JSON"):
        load_execution_state(path)

    path.write_bytes(b"\x00\x01\x02 not json at all")
    with pytest.raises(ExecutionStateCorruptError, match="not valid JSON"):
        load_execution_state(path)

    path.write_bytes(b"[]")
    with pytest.raises(ExecutionStateCorruptError, match="must be an object"):
        load_execution_state(path)

    # A write is validated on the way OUT as well, so a bad body never becomes
    # a file that a later start has to refuse.
    with pytest.raises(ExecutionStateError):
        write_execution_state(tmp_path / "rejected.json", {"nope": True})
    assert not (tmp_path / "rejected.json").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["execution_state.json"]

# ══════════════════════════════════════════════════════════════════════════════
# Section D -- the startup reconciliation gate
#
# The boundary being pinned: persisted state is loaded, reconciliation runs and
# returns clean, and BOTH internal cross-checks (lock vs records, strategy vs
# execution) agree -- otherwise no new entry is admitted. Every ambiguity blocks
# instead of being repaired: nothing here invents an order id, a fill, a
# quantity, a protection leg or a position.
#
# None of these tests compose the production execution stack. No orchestrator is
# built, no market-data or private stream is bound, and no live-execution flag
# exists to be set.
# ══════════════════════════════════════════════════════════════════════════════


def _live_state(path: Path, setup_id: str = SETUP) -> Path:
    """Persist the state of a process that went down holding a protected trade."""
    donor = _manager(FakeExchange())
    donor._active_trades[setup_id] = _protected_record(setup_id)
    donor._entry_fill_sizes[setup_id] = QUANTITY
    donor.single_trade_lock.acquire_lock(
        user_id=USER, account_id=ACCOUNT, setup_id=setup_id, symbol=SYMBOL)
    return persist_execution_state(donor, path)


def _flat_state(path: Path) -> Path:
    """Persist the state of a process that went down holding nothing."""
    return persist_execution_state(_manager(FakeExchange()), path)


def _gate(
    manager: TradeLifecycleManager,
    path: Path,
    runtime: Optional[_FakeRuntime] = None,
    **over,
) -> ExecutionStartupGate:
    """Install a gate. Account and user are read from the manager, not guessed."""
    return ExecutionStartupGate(manager, path, runtime, **over)


@pytest.mark.asyncio
async def test_d1_an_unarmed_gate_refuses_every_entry(tmp_path):
    """A composition that forgets to `await arm()` cannot trade.

    The gate blocks from the moment it is INSTALLED, and the refusal is stamped
    with `STARTUP_RECONCILIATION_REQUIRED` rather than a generic error, so an
    operator can tell a startup boundary apart from a validation rejection. No
    order is POSTed.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)
    gate = _gate(manager, tmp_path / "execution_state.json")

    assert gate.armed is False
    assert gate.result.admitted is False
    assert gate.result.block_reason == NOT_ARMED_REASON
    assert gate.result.state_loaded is False
    assert gate.admission_reason() == NOT_ARMED_REASON

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, USER)
    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == REFUSAL_STARTUP_GATE
    assert record.rejection_code == GATE_REFUSAL_CODE
    assert NOT_ARMED_REASON in (record.error_message or "")
    assert exchange.placed == []
    assert exchange.paths() == [], "a blocked entry must not touch the exchange"


@pytest.mark.asyncio
async def test_d2_a_missing_state_file_blocks_and_raises(tmp_path):
    """Absence is never read as "flat book" -- unless a first start says so.

    Both halves matter: the default refuses a file that should have been there
    (and raises, so a caller cannot mistake the refusal for a clean start), and
    `allow_missing_state=True` is an explicit, per-gate opt-in for a genuine
    first-ever start.
    """
    path = tmp_path / "execution_state.json"
    manager = _manager(FakeExchange())
    gate = _gate(manager, path, _FakeRuntime())

    with pytest.raises(ExecutionStartupGateError, match="allow_missing_state=True"):
        await gate.arm()
    assert gate.armed is False
    assert "refusing to assume a flat book" in (gate.result.block_reason or "")
    assert manager._entry_admission_reason() is not None

    first_ever = _manager(FakeExchange())
    permitted = _gate(first_ever, path, _FakeRuntime(), allow_missing_state=True)
    result = await permitted.arm()
    assert result.admitted is True
    assert result.state_loaded is False, "nothing was loaded; that is the point"
    assert permitted.armed is True


@pytest.mark.asyncio
async def test_d3_corrupt_or_incompatible_state_blocks_and_raises(tmp_path):
    """A damaged or foreign state file is refused, and the gate stays blocked.

    The gate both records the block AND raises, so a caller that swallows the
    exception still cannot trade: `admission_reason()` keeps refusing.
    """
    path = tmp_path / "execution_state.json"
    path.write_bytes(b"{ this is not json")
    manager = _manager(FakeExchange())
    gate = _gate(manager, path, _FakeRuntime())

    with pytest.raises(ExecutionStartupGateError, match="unusable"):
        await gate.arm()
    assert gate.armed is False
    assert "could not be loaded" in (gate.result.block_reason or "")

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, USER)
    assert record.rejection_code == REFUSAL_STARTUP_GATE

    future = copy.deepcopy(_valid_body())
    future["execution_state_schema_version"] = EXECUTION_STATE_SCHEMA_VERSION + 1
    path.write_bytes(json.dumps(future, indent=2, sort_keys=True).encode("utf-8"))
    second = _gate(_manager(FakeExchange()), path, _FakeRuntime())
    with pytest.raises(ExecutionStartupGateError):
        await second.arm()
    assert second.armed is False


@pytest.mark.asyncio
async def test_d4_a_clean_reconciliation_admits_and_only_then_can_an_entry_post(tmp_path):
    """The positive case, proved end to end: blocked -> armed -> the entry POSTs.

    Deliberately the FLAT restart: with no live trade restored, a brand-new
    entry is legitimate, so the same submission that section D1 refused now
    reaches the exchange under its deterministic name.
    """
    path = _flat_state(tmp_path / "execution_state.json")
    exchange = FakeExchange()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime())

    result = await gate.arm()
    assert result.admitted is True
    assert result.state_loaded is True
    assert result.block_reason is None
    assert result.active_setup_ids == ()
    assert result.strategy_setup_id is None
    assert result.reconciliation is not None
    assert result.reconciliation["exchange_unreachable"] is False
    assert result.reconciliation["unresolved"] == []
    assert result.reconciliation["orphan_positions"] == []
    assert result.reconciliation["orphan_orders"] == []
    assert manager._entry_admission_reason() is None

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, USER)
    assert record.state is TradeLifecycleState.ENTRY_SUBMITTED
    assert record.rejection_code is None
    assert exchange.client_order_ids() == [
        generate_deterministic_client_order_id(ACCOUNT, SETUP, ORDER_ROLE_ENTRY)
    ]


@pytest.mark.asyncio
async def test_d5_a_restored_live_trade_is_recognised_and_reconciles_clean(tmp_path):
    """A restart INTO a live protected position: state restored, world agrees.

    This is the case the whole layer exists for. The record, its order ids and
    the lock all come back, the exchange confirms one position with both legs
    resting, the strategy agrees on the same setup id, and the gate admits --
    without inventing anything.
    """
    path = _live_state(tmp_path / "execution_state.json")
    exchange = _clean_world()
    lock = SingleTradeLockManager()
    manager = _manager(exchange, lock=lock)
    runtime = _FakeRuntime(setup_id=SETUP, holds_trade=True)
    gate = _gate(manager, path, runtime)

    result = await gate.arm()
    assert result.admitted is True, result.block_reason
    assert result.active_setup_ids == (SETUP,)
    assert result.strategy_setup_id == SETUP
    assert lock.is_locked(USER, ACCOUNT) == (True, SETUP, SYMBOL)

    restored = manager._active_trades[SETUP]
    assert restored.entry_client_order_id == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, ORDER_ROLE_ENTRY)
    assert restored.sl_order_id == str(SL_ORDER_ID)
    assert restored.tp_order_id == str(TP_ORDER_ID)
    assert manager._reconciliation_alerts == []

    # Admitted does NOT mean "a second trade is allowed": the restored lock still
    # refuses a different setup. The two gates are independent, and both hold.
    other = await manager.execute_trade_setup(_decision(setup_id=OTHER_SETUP), ACCOUNT, USER)
    assert other.state is TradeLifecycleState.ENTRY_REJECTED
    assert exchange.placed == []

@pytest.mark.asyncio
async def test_d6_an_unreadable_exchange_blocks_rather_than_assuming_a_flat_book(tmp_path):
    """"Could not read the exchange" is not "the exchange holds nothing".

    Reconciliation reports `exchange_unreachable`, the gate blocks on it, and the
    subsequent entry is refused. Nothing about the exchange's real state was
    learned, so nothing about it is assumed.
    """
    path = _flat_state(tmp_path / "execution_state.json")
    exchange = FakeExchange(unreachable=True)
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime())

    result = await gate.arm()
    assert result.admitted is False
    assert "exchange_unreachable=True" in (result.block_reason or "")
    assert result.state_loaded is True, "state loaded; only the exchange was unreadable"
    assert result.reconciliation is not None
    assert result.reconciliation["unresolved"] == ["EXCHANGE_UNREACHABLE"]

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, USER)
    assert record.rejection_code == REFUSAL_STARTUP_GATE
    assert exchange.placed == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, build_exchange, expected",
    [
        (
            "orphan_position",
            lambda: FakeExchange(positions=[_position_json()]),
            "orphan_positions=['BTCUSD']",
        ),
        (
            "orphan_order",
            lambda: FakeExchange(open_orders=[_order_json(order_id=888777)]),
            "orphan_orders=['888777']",
        ),
    ],
)
async def test_d7_exchange_state_belonging_to_nothing_local_blocks(
    tmp_path, case, build_exchange, expected
):
    """An orphan position or order is REPORTED and blocks; it is never adopted.

    Local state says the book is flat and the exchange says otherwise. Adopting
    the difference would mean inventing which setup owns a live position, so the
    gate refuses and leaves it for a human.
    """
    path = _flat_state(tmp_path / f"execution_state_{case}.json")
    exchange = build_exchange()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime())

    result = await gate.arm()
    assert result.admitted is False
    assert "did not return clean" in (result.block_reason or "")
    assert expected in (result.block_reason or ""), result.block_reason
    assert manager._active_trades == {}, "nothing may be invented to explain it"
    assert manager._reconciliation_alerts, "the orphan is recorded, not swallowed"

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, USER)
    assert record.rejection_code == REFUSAL_STARTUP_GATE
    assert exchange.placed == []


def _lock_without_record(path: Path) -> Path:
    """State in which a lock was retained but no live record explains it."""
    donor = _manager(FakeExchange())
    donor.single_trade_lock.acquire_lock(
        user_id=USER, account_id=ACCOUNT, setup_id=SETUP, symbol=SYMBOL)
    return persist_execution_state(donor, path)


def _record_without_lock(path: Path) -> Path:
    """State in which a live record exists but the lock protecting it is gone."""
    donor = _manager(FakeExchange())
    donor._active_trades[SETUP] = _protected_record()
    return persist_execution_state(donor, path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, build_state, build_exchange, expected",
    [
        (
            "retained_lock_names_no_record",
            _lock_without_record,
            FakeExchange,
            "has no live execution record",
        ),
        (
            "live_record_holds_no_lock",
            _record_without_lock,
            _clean_world,
            "hold no single-trade lock",
        ),
    ],
)
async def test_d8_execution_state_that_disagrees_with_its_own_lock_blocks(
    tmp_path, case, build_state, build_exchange, expected
):
    """The two persisted execution stores must agree with each other.

    A retained lock naming no record is exactly the state safety rules #11/#14
    leave behind when an attempt failed with unknown exchange state -- something
    may still be open or unprotected. A record with no lock means the stores
    disagree about whether a trade is live. Both are ambiguous, so both block.
    """
    path = build_state(tmp_path / f"execution_state_{case}.json")
    exchange = build_exchange()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))

    result = await gate.arm()
    assert result.admitted is False
    assert expected in (result.block_reason or ""), result.block_reason
    assert result.state_loaded is True

    record = await manager.execute_trade_setup(_decision(setup_id=OTHER_SETUP), ACCOUNT, USER)
    assert record.rejection_code == REFUSAL_STARTUP_GATE
    assert exchange.placed == []


@pytest.mark.asyncio
async def test_d9_a_lock_held_for_no_named_setup_blocks(tmp_path):
    """A held lock that names nothing cannot be reasoned about, so it blocks.

    The shape check accepts it (`active_setup_id` is legitimately optional for an
    unlocked account), which is exactly why the gate has to catch the locked-but-
    anonymous combination rather than trusting the file's shape alone.
    """
    path = tmp_path / "execution_state.json"
    _lock_without_record(path)
    body = json.loads(path.read_text(encoding="utf-8"))
    entry = body["single_trade_lock"][f"{USER}:{ACCOUNT}"]
    entry["active_setup_id"] = None
    entry["active_symbol"] = None
    assert entry["is_locked"] is True
    write_execution_state(path, body)

    manager = _manager(FakeExchange())
    gate = _gate(manager, path, _FakeRuntime())
    result = await gate.arm()
    assert result.admitted is False
    assert "names no setup" in (result.block_reason or ""), result.block_reason

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, build_state, build_exchange, build_runtime, expected",
    [
        (
            "strategy_holds_execution_flat",
            _flat_state,
            FakeExchange,
            lambda: _FakeRuntime(setup_id=SETUP, holds_trade=True),
            "execution state holds none",
        ),
        (
            "execution_holds_strategy_flat",
            _live_state,
            _clean_world,
            lambda: _FakeRuntime(),
            "the strategy holds none",
        ),
        (
            "two_sides_name_different_setups",
            _live_state,
            _clean_world,
            lambda: _FakeRuntime(setup_id=OTHER_SETUP, holds_trade=True),
            "does not match",
        ),
        (
            "strategy_state_unreadable",
            _flat_state,
            FakeExchange,
            lambda: _FakeRuntime(raises=True),
            "could not be read",
        ),
        (
            "strategy_setup_id_underivable",
            _flat_state,
            FakeExchange,
            lambda: _FakeRuntime(holds_trade=True, setup_id_raises=True),
            "could not be derived",
        ),
        (
            "strategy_setup_id_empty",
            _flat_state,
            FakeExchange,
            lambda: _FakeRuntime(holds_trade=True, setup_id=None),
            "empty setup id",
        ),
    ],
)
async def test_d10_strategy_and_execution_state_must_agree(
    tmp_path, case, build_state, build_exchange, build_runtime, expected
):
    """Strategy-side open-trade state is compared with execution-side state.

    Six ways the two sides can fail to agree, and all six block. The comparison
    goes through the runtime's own `setup_id_for` formula so both sides are
    named identically -- a reconstruction of that formula here could agree with
    itself while disagreeing with production.

    Unreadable counts as disagreement: an invariant that cannot be CHECKED is
    not an invariant that holds.
    """
    path = build_state(tmp_path / f"execution_state_{case}.json")
    exchange = build_exchange()
    manager = _manager(exchange)
    gate = _gate(manager, path, build_runtime())

    result = await gate.arm()
    assert result.admitted is False
    assert expected in (result.block_reason or ""), result.block_reason
    assert result.state_loaded is True

    record = await manager.execute_trade_setup(_decision(setup_id=OTHER_SETUP), ACCOUNT, USER)
    assert record.rejection_code == REFUSAL_STARTUP_GATE
    assert exchange.placed == []


@pytest.mark.asyncio
async def test_d11_no_strategy_runtime_means_an_uncheckable_invariant_blocks(tmp_path):
    """A gate with nothing to compare against cannot admit.

    Omitting the runtime is not a way to skip the strategy/execution check --
    it is the reason the check fails. This is the fail-closed direction for a
    composition that has not been wired yet.
    """
    path = _flat_state(tmp_path / "execution_state.json")
    manager = _manager(FakeExchange())
    gate = _gate(manager, path, None)

    result = await gate.arm()
    assert result.admitted is False
    assert "no strategy runtime is bound" in (result.block_reason or "")
    assert manager._entry_admission_reason() is not None


@pytest.mark.asyncio
async def test_d12_arming_twice_is_idempotent_and_does_not_re_query_the_exchange(tmp_path):
    """Re-arming an armed gate whose state has not moved reuses the verdict.

    And the converse, which is what makes that safe: state that HAS moved
    invalidates the cached verdict and forces the whole sequence to run again
    from a blocked starting point.
    """
    path = _live_state(tmp_path / "execution_state.json")
    exchange = _clean_world()
    manager = _manager(exchange)
    gate = _gate(manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))

    first = await gate.arm()
    assert first.admitted is True, first.block_reason
    reads_after_first = list(exchange.reads())
    assert reads_after_first == ["GET /v2/positions/margined", "GET /v2/orders"]

    second = await gate.arm()
    assert second is first, "the cached verdict is returned, not a rebuilt one"
    assert exchange.reads() == reads_after_first, (
        "an unchanged, already-armed gate must not re-query the exchange")

    # State moves: a record appears in memory that the persisted file does not
    # know about. The cached verdict is invalidated, the whole sequence re-runs,
    # and the reload makes the FILE authoritative again -- the in-memory
    # divergence is discarded rather than blessed.
    manager._active_trades[OTHER_SETUP] = _protected_record(OTHER_SETUP)
    third = await gate.arm()
    assert third is not first
    assert len(exchange.reads()) > len(reads_after_first), "the sequence re-ran"
    assert set(manager._active_trades) == {SETUP}, (
        "the persisted file is authoritative on re-arm; the injected record is gone")
    assert third.admitted is True, third.block_reason

    # A re-run reaches a FRESH verdict, not a remembered one: with the exchange
    # now holding an order nothing local claims, the same gate blocks.
    exchange.open_orders.append(_order_json(order_id=888777))
    manager._active_trades[OTHER_SETUP] = _protected_record(OTHER_SETUP)
    fourth = await gate.arm()
    assert fourth.admitted is False
    assert "orphan_orders=['888777']" in (fourth.block_reason or ""), fourth.block_reason
    assert manager._entry_admission_reason() is not None


@pytest.mark.asyncio
async def test_d13_a_second_gate_cannot_replace_an_installed_one(tmp_path):
    """Installing a gate over an existing gate is refused, armed or not.

    Otherwise a second composition could quietly replace a BLOCK with a fresh
    gate and then arm it against different inputs.
    """
    path = _flat_state(tmp_path / "execution_state.json")
    manager = _manager(FakeExchange())
    first = _gate(manager, path, _FakeRuntime())
    installed = manager._entry_admission_gate

    with pytest.raises(ExecutionStartupGateError, match="refusing to replace"):
        _gate(manager, path, _FakeRuntime())
    assert manager._entry_admission_gate is installed

    assert (await first.arm()).admitted is True
    with pytest.raises(ExecutionStartupGateError, match="refusing to replace"):
        _gate(manager, path, _FakeRuntime())
    assert manager._entry_admission_gate is installed
    assert first.armed is True


@pytest.mark.asyncio
async def test_d14_a_gate_refusal_never_releases_a_retained_lock(tmp_path):
    """Refusing an entry must not free a lock the refusal did not take.

    The gate is checked BEFORE this attempt acquires anything, so a release here
    could only drop a lock a restored session still holds -- and a retained lock
    is the fail-safe direction (safety rules #11/#14). A released one permits a
    second trade beside a possibly-live, possibly-unprotected position.
    """
    lock = SingleTradeLockManager()
    lock.acquire_lock(user_id=USER, account_id=ACCOUNT, setup_id=SETUP, symbol=SYMBOL)
    exchange = FakeExchange()
    manager = _manager(exchange, lock=lock)
    _gate(manager, tmp_path / "execution_state.json", _FakeRuntime())

    record = await manager.execute_trade_setup(_decision(setup_id=SETUP), ACCOUNT, USER)
    assert record.rejection_code == REFUSAL_STARTUP_GATE
    assert lock.is_locked(USER, ACCOUNT) == (True, SETUP, SYMBOL), (
        "the retained lock survived the refusal")
    assert exchange.placed == []


@pytest.mark.asyncio
async def test_d15_a_gate_hook_that_raises_is_a_refusal_not_an_admission(tmp_path):
    """A broken boundary must not open the door.

    `_entry_admission_reason` treats an exception from the gate as a refusal, so
    a gate that cannot answer blocks instead of defaulting to yes. A default must
    never answer a safety question.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    def _explode() -> Optional[str]:
        raise RuntimeError("gate hook is broken")

    manager._entry_admission_gate = _explode
    assert "entry admission gate failed" in (manager._entry_admission_reason() or "")

    record = await manager.execute_trade_setup(_decision(), ACCOUNT, USER)
    assert record.rejection_code == REFUSAL_STARTUP_GATE
    assert "gate hook is broken" in (record.error_message or "")
    assert exchange.placed == []

def _alerting_state(path: Path) -> Path:
    """A live protected trade PLUS an unresolved alert carried across the restart."""
    donor = _manager(FakeExchange())
    donor._active_trades[SETUP] = _protected_record()
    donor.single_trade_lock.acquire_lock(
        user_id=USER, account_id=ACCOUNT, setup_id=SETUP, symbol=SYMBOL)
    donor._reconciliation_alerts.append({
        "code": "ORPHAN_EXCHANGE_ORDER",
        "symbol": SYMBOL,
        "details": "exchange order 888777 is not tracked locally",
        "timestamp": "2026-09-05T09:00:00+00:00",
    })
    return persist_execution_state(donor, path)


@pytest.mark.asyncio
async def test_d16_a_restored_alert_blocks_until_a_clean_run_resolves_it(tmp_path):
    """An alert crosses the restart and keeps blocking until the world agrees.

    Alerts are not cleared by restarting -- they are persisted, so the condition
    that raised one outlives the process that saw it. Only a reconciliation that
    actually comes back clean clears them, and it says so in the summary.
    """
    path = _alerting_state(tmp_path / "execution_state.json")

    still_dirty = _clean_world()
    still_dirty.open_orders.append(_order_json(order_id=888777))
    blocked_manager = _manager(still_dirty)
    blocked = _gate(blocked_manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))

    result = await blocked.arm()
    assert result.admitted is False
    assert "orphan_orders=['888777']" in (result.block_reason or "")
    assert blocked_manager._reconciliation_alerts, "the alert was not silently dropped"

    resolved_manager = _manager(_clean_world())
    resolved = _gate(resolved_manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))
    clean = await resolved.arm()
    assert clean.admitted is True, clean.block_reason
    assert clean.reconciliation is not None
    assert clean.reconciliation["alerts_cleared"] == 1
    assert resolved_manager._reconciliation_alerts == []


@pytest.mark.asyncio
async def test_d17_the_gate_re_checks_alerts_itself_rather_than_trusting_the_clearing(
    tmp_path, monkeypatch
):
    """Defence in depth: the gate's own alert check does not depend on clearing.

    A clean reconciliation normally clears alerts itself, so this branch is not
    reachable through the production path today. It is checked anyway, because
    the gate must not be the component that assumes another component cleaned
    up. The clearing is stubbed at CLASS level -- stubbing it on the instance is
    itself refused by the attribute-classification guard, which is the section B
    invariant doing its job.
    """
    path = _alerting_state(tmp_path / "execution_state.json")
    monkeypatch.setattr(
        TradeLifecycleManager, "clear_reconciliation_alerts",
        lambda self, authorized_by: 0,
    )
    manager = _manager(_clean_world())
    gate = _gate(manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))

    result = await gate.arm()
    assert result.admitted is False
    assert "unresolved reconciliation alerts block new entries" in (result.block_reason or "")
    assert "ORPHAN_EXCHANGE_ORDER" in (result.block_reason or "")


@pytest.mark.asyncio
async def test_d18_startup_reconciliation_is_idempotent_across_restarts(tmp_path):
    """Two restarts from the same file reach the same verdict and the same state.

    Idempotence is the property that makes a restart loop safe: re-running the
    boundary must not converge on a different answer, mutate the persisted file,
    or place anything. All three are asserted.
    """
    path = _live_state(tmp_path / "execution_state.json")
    original_bytes = path.read_bytes()

    verdicts = []
    fingerprints = []
    for _ in range(2):
        exchange = _clean_world()
        manager = _manager(exchange)
        gate = _gate(manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))
        result = await gate.arm()
        assert result.admitted is True, result.block_reason
        assert exchange.placed == [], "reconciliation placed nothing"
        verdicts.append((result.admitted, result.active_setup_ids, result.strategy_setup_id))
        fingerprints.append(serialize_execution_state_body(export_execution_state(manager)))

    assert verdicts[0] == verdicts[1]
    assert fingerprints[0] == fingerprints[1]
    assert path.read_bytes() == original_bytes, (
        "arming must not write state back; persisting is the caller's decision")

    # And reconciling twice inside one process is equally stable.
    exchange = _clean_world()
    manager = _manager(exchange)
    restore_execution_state_from_file(manager, path)
    first = await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)
    second = await manager.reconcile_active_trades_with_exchange(
        account_id=ACCOUNT, user_id=USER)
    assert first == second
    assert first["checked"] == 1
    assert first["unresolved"] == []
    assert exchange.placed == []
    assert manager._active_trades[SETUP].state is TradeLifecycleState.PROTECTED_POSITION

# ══ E. No silent default may stand in for an authoritative input ══════════════
#
# Each of the three inputs below used to carry a default on this path, and not
# one of them was a strategy output. The entry price fell back to
# `Decimal("100000.00")` -- a BTC-shaped number that would have priced a REAL
# order on any symbol at any price. The quantity fell back to one contract. The
# leverage fell back to `or 100`: the top of the authorised band, and so the one
# substitution guaranteed to pass the band check downstream while multiplying
# real margin exposure by up to a hundred. The replacement for each is refusal.
# No new default is chosen here, and the authorised 1x-100x policy is neither
# duplicated nor touched -- see E4.


@pytest.mark.parametrize("bad", [None, Decimal("0"), Decimal("-1"), "abc", True])
@pytest.mark.asyncio
async def test_e1_an_unusable_entry_price_is_refused_not_replaced(bad):
    """Absent, zero, negative, non-numeric and `True` all refuse identically.

    `True` is in the list because it is not a hypothetical: `bool` is an `int`,
    so a truthiness-based guard would have accepted it as 1 and priced an order
    at one dollar. The refused record carries zeros, not the value it rejected
    and not a substitute -- nothing downstream can mistake it for a real price.
    """
    exchange = FakeExchange()
    lock = SingleTradeLockManager()
    manager = _manager(exchange, lock=lock)

    record = await manager.execute_trade_setup(_decision(entry=bad), ACCOUNT, USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == REFUSAL_MISSING_ENTRY_PRICE
    assert repr(bad) in (record.error_message or ""), (
        "the refusal must name the value it saw rather than hide it")
    assert "substituted value" in (record.error_message or "")
    assert record.entry_price == Decimal("0")
    assert record.requested_quantity == Decimal("0")
    assert exchange.placed == [], "a refused setup must not reach the exchange"
    assert exchange.paths() == [], "and must not talk to it at all"
    assert lock.is_locked(USER, ACCOUNT)[0] is False, (
        "the lock this attempt acquired at step 0 must not be stranded")

@pytest.mark.parametrize("bad", [None, Decimal("0"), Decimal("-1"), "abc", True])
@pytest.mark.asyncio
async def test_e2_an_unusable_quantity_is_refused_not_replaced(bad):
    """The size of a real position is never chosen by this module.

    The old default of one contract was not wrong in the way a wrong price is
    wrong -- it was worse, because one contract is a plausible size, so a
    dropped quantity would have opened a small real position and looked correct.
    """
    exchange = FakeExchange()
    lock = SingleTradeLockManager()
    manager = _manager(exchange, lock=lock)

    record = await manager.execute_trade_setup(_decision(quantity=bad), ACCOUNT, USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == REFUSAL_MISSING_QUANTITY
    assert repr(bad) in (record.error_message or "")
    assert "substituted value" in (record.error_message or "")
    assert record.requested_quantity == Decimal("0")
    assert exchange.placed == []
    assert exchange.paths() == []
    assert lock.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_e3_an_absent_calculated_leverage_is_refused_not_replaced():
    """Only ABSENCE is refused here, and it is refused before validation runs.

    This matters for the closed leverage policy: the refusal deliberately does
    NOT re-implement the authorised band. It answers a different question --
    "did the strategy calculate a leverage at all?" -- and leaves "is this
    leverage inside 1x-100x?" to `CHECK_LEVERAGE_CAP`, which E4 exercises.
    """
    exchange = FakeExchange()
    lock = SingleTradeLockManager()
    manager = _manager(exchange, lock=lock)

    record = await manager.execute_trade_setup(
        _decision(calculated_leverage=None), ACCOUNT, USER)

    assert record.state is TradeLifecycleState.ENTRY_REJECTED
    assert record.rejection_code == REFUSAL_MISSING_LEVERAGE
    assert "substituted leverage" in (record.error_message or "")
    assert exchange.placed == []
    assert lock.is_locked(USER, ACCOUNT)[0] is False

@pytest.mark.parametrize("out_of_band", [0, 101, 1000])
@pytest.mark.asyncio
async def test_e4_a_present_but_out_of_band_leverage_is_still_the_closed_policys_job(
    out_of_band,
):
    """A leverage that IS present is judged by the authorised band, not by E3.

    The refusal added for absence must not become a second, parallel leverage
    policy. So a present value is passed through to `CHECK_LEVERAGE_CAP`, which
    rejects both ends of the violation and clamps neither: 0x is not raised to
    1x and 101x is not reduced to 100x. Nothing is placed either way.
    """
    exchange = FakeExchange()
    lock = SingleTradeLockManager()
    manager = _manager(exchange, lock=lock)

    record = await manager.execute_trade_setup(
        _decision(calculated_leverage=out_of_band), ACCOUNT, USER)

    assert record.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE.value
    assert record.rejection_code != REFUSAL_MISSING_LEVERAGE, (
        "absence and out-of-band are different findings and must stay distinct")
    assert f"{out_of_band}x" in (record.error_message or "")
    assert exchange.placed == [], "rejected at validation, before submission"
    assert lock.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.parametrize("in_band", [1, 100])
@pytest.mark.asyncio
async def test_e5_the_authorised_band_endpoints_still_submit_unchanged(in_band):
    """The refusal narrowed nothing: 1x and 100x are still both admitted.

    A refusal that also quietly moved the band would be a change to a closed
    policy. These two endpoints post real (mocked) orders under the deterministic
    identity, which is the only evidence that the band is still 1x-100x
    INCLUSIVE after this change.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)

    record = await manager.execute_trade_setup(
        _decision(calculated_leverage=in_band), ACCOUNT, USER)

    assert record.state is TradeLifecycleState.ENTRY_SUBMITTED, record.error_message
    assert record.rejection_code is None
    assert exchange.client_order_ids() == [
        generate_deterministic_client_order_id(ACCOUNT, SETUP, ORDER_ROLE_ENTRY)]
    assert "leverage" not in exchange.placed[0], (
        "the Delta payload still carries no leverage field")

@pytest.mark.parametrize(
    ("field", "fragment"),
    [
        ("entry", "carried no entry price"),
        ("calculated_leverage", "carried no calculated leverage"),
    ],
)
@pytest.mark.asyncio
async def test_e6_the_scanner_refuses_to_size_on_a_substituted_input(field, fragment):
    """The other reachable consumer of these inputs refuses them too.

    `MarketScannerOrchestrator` sizes at 100% of available capital, so it needs
    both a price and a leverage before the allocator runs. Its refusals are
    hoisted ABOVE the allocator on purpose: the allocator's own guards compare
    `entry_price <= 0` and `leverage < MIN_LEVERAGE`, and those comparisons
    against `None` raise TypeError, which its `except CapitalAllocationError`
    does not catch. An unhandled exception is not a rejection.
    """
    exchange = FakeExchange()
    lock = SingleTradeLockManager()
    manager = _manager(exchange, lock=lock)
    scanner = MarketScannerOrchestrator(manager, lock)

    decision = _decision(**{field: None})
    result = await scanner.scan_and_execute(
        ACCOUNT, USER, candidate_decisions=[decision])

    assert result.executed_record is None, "nothing was executed"
    assert fragment in (result.rejection_reason or ""), result.rejection_reason
    assert "substituted" in (result.rejection_reason or "")
    assert result.qualifying_symbol == SYMBOL, (
        "the setup did qualify; it is the missing input that refused it")
    assert result.decision is decision
    assert exchange.placed == []
    assert exchange.paths() == []
    assert lock.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_e7_the_scanner_still_executes_when_both_inputs_are_present():
    """The positive control for E6: the refusals block absence, not the path.

    Without this, E6 would pass just as well if the scanner had been broken
    outright. The size here is the allocator's own 100%-of-capital figure, never
    a default, and the order is named by the deterministic triple.
    """
    exchange = FakeExchange()
    lock = SingleTradeLockManager()
    manager = _manager(exchange, lock=lock)
    scanner = MarketScannerOrchestrator(manager, lock)

    result = await scanner.scan_and_execute(
        ACCOUNT, USER, candidate_decisions=[_decision()])

    assert result.rejection_reason is None
    assert result.executed_record is not None
    assert result.executed_record.state is TradeLifecycleState.ENTRY_SUBMITTED, (
        result.executed_record.error_message)
    assert result.executed_record.requested_quantity > QUANTITY, (
        "sized from capital, not from the decision's placeholder quantity")
    assert exchange.client_order_ids() == [
        generate_deterministic_client_order_id(ACCOUNT, SETUP, ORDER_ROLE_ENTRY)]

# ── Proving a default is GONE, not merely unreached by these fixtures ──────────
#
# A behavioural test can only show that today's inputs are refused. It cannot
# show that no fallback survives on some other branch, so the two reachable
# modules are read structurally as well. Everything below is AST work: comments
# are dropped by the parser, so the prose that documents the removed defaults --
# which necessarily still quotes `Decimal("100000.00")` and `or 100` -- can never
# make this test pass or fail. Only executable code counts.


def _function_node(relative: str, qualname: str):
    """The AST of `Class.method` (or a bare function) in a production module."""
    cls, _, fn = qualname.rpartition(".")
    tree = ast.parse(_source(relative))
    scope: Any = tree
    if cls:
        scope = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.ClassDef) and n.name == cls)
    return next(n for n in ast.walk(scope)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == fn)


def _assigned_expressions(node, target: str) -> List[Any]:
    """Every expression assigned to the bare local name `target` inside `node`."""
    out: List[Any] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            if target in [t.id for t in sub.targets if isinstance(t, ast.Name)]:
                out.append(sub.value)
        elif (isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name)
              and sub.target.id == target and sub.value is not None):
            out.append(sub.value)
    return out


def _substitutions(expression) -> List[str]:
    """Every numeric literal or `Decimal(...)` construction in an expression.

    These are the shapes a substituted default takes on this path: a bare number
    (`or 100`) or a constructed one (`or Decimal("100000.00")`). `None` is not
    one of them -- `getattr(x, "y", None)` asks a question, it does not answer it.
    """
    found: List[str] = []
    for sub in ast.walk(expression):
        if (isinstance(sub, ast.Constant) and isinstance(sub.value, (int, float))
                and not isinstance(sub.value, bool)):
            found.append(repr(sub.value))
        elif (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
              and sub.func.id == "Decimal"):
            found.append(ast.unparse(sub))
    return found

#: (module, function, the local names that must be read, never substituted)
#:
#: P1-4 RE-PIN: `execute_trade_setup` was split into an admission half (which
#: keeps that name and the manager lock) and `_execute_admitted_trade_setup`
#: (which does the submitting). The three authoritative reads live in the second
#: half now, so that is where they are pinned -- the property is identical, only
#: its address moved. `test_p2_5_the_admission_half_substitutes_nothing_either`
#: below keeps the wrapper itself under the same rule.
_AUTHORITATIVE_READS = (
    (
        "execution/trade_lifecycle.py",
        "TradeLifecycleManager._execute_admitted_trade_setup",
        ("entry_price", "quantity", "effective_leverage"),
    ),
    (
        "execution/market_orchestrator.py",
        "MarketScannerOrchestrator.scan_and_execute",
        ("entry_price", "effective_lev"),
    ),
)


def test_e8_no_substituted_value_remains_on_either_reachable_read():
    """Each of the five reads is a plain read of the strategy's own output.

    If someone reintroduces `or Decimal("100000.00")`, `or 10`, `or 100` or
    `or Decimal("1.0")` on any of these five lines, this fails and names the
    substitution -- including on a branch no fixture in this file happens to
    reach.
    """
    for relative, qualname, targets in _AUTHORITATIVE_READS:
        node = _function_node(relative, qualname)
        for target in targets:
            expressions = _assigned_expressions(node, target)
            assert expressions, f"{qualname} no longer assigns {target}"
            for expression in expressions:
                assert not _substitutions(expression), (
                    f"{qualname} substitutes a value for {target}: "
                    f"{ast.unparse(expression)}")


def test_p2_5_the_admission_half_substitutes_nothing_either():
    """The half that no longer reads those values must not start supplying them.

    After the P1-4 split the wrapper decides admission and delegates; if a future
    patch "helpfully" defaulted a missing quantity, price or leverage there before
    delegating, E8 would still pass because it now inspects the other half. So the
    wrapper is held to the same rule: it may pass values through, but it may not
    construct an execution-critical number.
    """
    node = _function_node(
        "execution/trade_lifecycle.py",
        "TradeLifecycleManager.execute_trade_setup",
    )
    for target in ("entry_price", "quantity", "effective_leverage"):
        for expression in _assigned_expressions(node, target):
            assert not _substitutions(expression), (
                f"the admission half substitutes a value for {target}: "
                f"{ast.unparse(expression)}")


def test_e9_the_fabricated_btc_price_is_absent_from_executable_code():
    """`Decimal("100000.00")` is gone from both modules, not merely unreached.

    It was the most dangerous of the removed defaults, because it is a plausible
    BTC price and an absurd one for every other symbol: an XRPUSD setup that
    lost its entry price would have been priced at a hundred thousand dollars
    and sized accordingly. `ast.dump` sees code and docstrings but not comments,
    so the surviving explanatory comments cannot mask a reintroduction.

    Each module is also required to keep DOCUMENTING the default it removed. A
    silent removal is how the same substitution comes back a year later; the
    comment is the only thing that tells the next reader the omission is a
    decision. The two modules removed different defaults, so each is checked for
    its own.
    """
    for relative, _, _ in _AUTHORITATIVE_READS:
        dumped = ast.dump(ast.parse(_source(relative)))
        assert "100000" not in dumped, (
            f"{relative} contains the fabricated price in executable code")

    lifecycle = _source("execution/trade_lifecycle.py")
    assert "The previous `or 100` silently substituted the" in lifecycle
    assert "one contract was not a strategy output." in lifecycle

    orchestrator = _source("execution/market_orchestrator.py")
    assert 'fall back to Decimal("100000.00")' in orchestrator
    assert "used to fall" in orchestrator and "back to 10x" in orchestrator

def test_e10_the_scanners_surviving_spec_fallbacks_are_unreachable_today():
    """The `if spec else` sizing fallbacks are named here rather than left silent.

    Three fallbacks remain in the scanner's sizing block -- `lot_step`, `min_qty`
    and `contract_unit` all fall back when `spec` is None. They were NOT part of
    this task's scope (which is quantity, leverage and entry price) and removing
    them is a separate change, so they stand. What is asserted instead is that no
    symbol the scanner actually scans can reach them: every default supported
    symbol resolves to a real Delta India specification. If a symbol is ever
    added without one, this fails and names it.
    """
    from quantedge.execution.validation import DEFAULT_DELTA_INDIA_PRODUCTS

    unspecified = [
        symbol for symbol in MarketScannerOrchestrator(
            _manager(FakeExchange()), SingleTradeLockManager()).supported_symbols
        if DEFAULT_DELTA_INDIA_PRODUCTS.get(symbol) is None
    ]
    assert unspecified == [], (
        f"these scanned symbols would be sized on fallback contract terms: "
        f"{unspecified}")


@pytest.mark.asyncio
async def test_e11_an_unspecified_symbol_still_cannot_reach_the_exchange():
    """And if one ever did reach them, the order is still refused before it posts.

    This is the fail-closed backstop behind E10: the scanner would size such a
    symbol on fallback contract terms, but `execute_trade_setup` resolves the
    product specification itself and rejects an unknown symbol as
    UNSUPPORTED_SYMBOL. The fallback can produce a number; it cannot produce an
    order.
    """
    exchange = FakeExchange()
    lock = SingleTradeLockManager()
    manager = _manager(exchange, lock=lock)
    scanner = MarketScannerOrchestrator(
        manager, lock, supported_symbols=["FAKEUSD"])

    result = await scanner.scan_and_execute(
        ACCOUNT, USER,
        candidate_decisions=[_decision(setup_id="FAKEUSD_1h_X", symbol="FAKEUSD")])

    assert exchange.placed == [], "nothing was submitted for an unknown product"
    record = result.executed_record
    assert record is not None, "the scan did reach submission; that is the point"
    assert record.rejection_code == RejectionReasonCode.UNSUPPORTED_SYMBOL.value
    assert "refusing to substitute another product" in (record.error_message or "")
    # The sizing fallback really did run and really did produce a number (2
    # contracts, on `contract_unit=Decimal("1.0")` rather than any verified
    # contract value). That number never became an order.
    assert record.requested_quantity > Decimal("0")
    assert lock.is_locked(USER, ACCOUNT)[0] is False

# ══ F. One canonical execution implementation, and the other one quarantined ═══
#
# Two implementations could place a real order through `DeltaIndiaClient` after
# `OrderValidationGateway` approval: `MarketScannerOrchestrator ->
# TradeLifecycleManager`, and `LiveOrderExecutionService`. Only the first
# implements the lifecycle semantics the production-facing safety work is built
# on -- protective bracket sized to the actual fill, single-trade account lock,
# reconciliation-alert entry block, durable lifecycle record, and now
# deterministic identity and the startup gate. The second has none of them and
# no caller anywhere in `quantedge/`.
#
# So the canonical choice is the first, and the second is quarantined rather
# than deleted: its own hardening tests still exercise it by its full dotted
# path, and deleting a module to prevent a hazard that a missing export already
# prevents would destroy coverage for nothing.

#: Everything `quantedge.execution` used to re-export from the orphan module.
QUARANTINED_NAMES = (
    "ExecutionState",
    "OrderExecutionRequest",
    "OrderExecutionResult",
    "LiveOrderExecutionService",
    "StrategyDecisionStore",
)


def test_f1_the_orphan_is_unreachable_from_the_package_root():
    """`from quantedge.execution import LiveOrderExecutionService` must not resolve.

    That exact line is the hazard: it is the shape a future composition would
    most plausibly reach for, and until this change it worked and read like the
    blessed entry point. Absence from the namespace AND from `__all__` are both
    asserted -- `__all__` alone would still leave the attribute importable.
    """
    for name in QUARANTINED_NAMES:
        assert not hasattr(execution_package, name), (
            f"{name} is re-exported from the package root again")
        assert name not in getattr(execution_package, "__all__", []), (
            f"{name} is advertised in quantedge.execution.__all__ again")

    with pytest.raises(ImportError):
        exec("from quantedge.execution import LiveOrderExecutionService", {})


def test_f2_the_canonical_path_is_still_exported_from_the_package_root():
    """Quarantine is not "hide the execution package": the canonical names stay.

    If both implementations were unreachable from the root, the next person
    composing production would have no signposted entry point at all, and would
    pick whichever module they found first. Exactly one is signposted.
    """
    for name in ("TradeLifecycleManager", "MarketScannerOrchestrator",
                 "MarketScanResult", "TradeLifecycleRecord", "TradeLifecycleState"):
        assert hasattr(execution_package, name), f"{name} must stay reachable"
        assert name in execution_package.__all__ or name in (
            "TradeLifecycleRecord", "TradeLifecycleState"), name

def test_f3_the_quarantined_module_is_intact_by_its_full_dotted_path():
    """Nothing was deleted. Quarantine removed one export, not one capability.

    The mandate is explicit that functionality must not be deleted blindly and
    that tests genuinely requiring the orphan must survive. Both of its hardening
    suites already import it by full path, so this is the import that has to keep
    working -- and every name they use has to still be there.
    """
    import quantedge.execution.execution_engine as orphan

    for name in QUARANTINED_NAMES:
        assert hasattr(orphan, name), f"{name} was deleted rather than quarantined"
    assert callable(getattr(orphan.LiveOrderExecutionService, "execute_order", None))


def test_f4_both_ends_of_the_quarantine_say_so_in_the_code():
    """The decision is recorded where a reader will actually meet it.

    Two places, because there are two ways in: someone reading the package root
    to find the entry point, and someone opening the orphan module directly. A
    decision recorded only in a report is a decision the next reader never sees.
    """
    banner = _source("execution/execution_engine.py")
    assert banner.lstrip().startswith('"""\nQUARANTINED'), (
        "the warning must be the first thing in the module, not buried")
    assert "DO NOT WIRE THIS INTO PRODUCTION" in banner
    assert "Canonical execution implementation: `MarketScannerOrchestrator`" in banner
    assert "Original module docstring" in banner, (
        "the original docstring must be preserved, not replaced")

    package = _source("execution/__init__.py")
    assert "QUARANTINE: `execution_engine` is NOT the canonical execution path" in package
    assert "intentionally" in package

#: The classes that would, between them, constitute a live execution stack.
_EXECUTION_STACK = (
    "TradeLifecycleManager",
    "MarketScannerOrchestrator",
    "LiveOrderExecutionService",
    "MultiUserExecutionOrchestrator",
    "ExecutionStartupGate",
)

#: The one production module authorized to construct the canonical three
#: (`TradeLifecycleManager`, `MarketScannerOrchestrator`, `ExecutionStartupGate`
#: via its `ProductionExecutionGate` subclass). The two quarantined classes
#: (`LiveOrderExecutionService`, `MultiUserExecutionOrchestrator`) must still
#: appear in NO production module.
_AUTHORIZED_COMPOSITION_MODULE = "runtime/production_composition.py"

#: Canonical classes whose construction is allowed only in the one authorized
#: module. Any other production module constructing them is a new wiring.
_CANONICAL_STACK = frozenset({
    "TradeLifecycleManager",
    "MarketScannerOrchestrator",
    "ExecutionStartupGate",
})

#: Quarantined classes that must never be constructed anywhere in production.
_QUARANTINED_STACK = frozenset({
    "LiveOrderExecutionService",
    "MultiUserExecutionOrchestrator",
})


def test_f5_no_production_module_constructs_an_execution_stack_at_all():
    """The authorized composition module constructs the canonical stack; nothing else does.

    `runtime/production_composition.py` is the ONE module that is allowed to
    construct `TradeLifecycleManager`, `MarketScannerOrchestrator`, and
    `ExecutionStartupGate` (via its `ProductionExecutionGate` subclass). Every
    other production module is still forbidden from constructing any of the five.

    The quarantined classes (`LiveOrderExecutionService`,
    `MultiUserExecutionOrchestrator`) must appear in NO production module at all,
    including the authorized one: they are quarantined, not promoted.
    """
    offenders: Dict[str, List[str]] = {}
    for path in _production_files():
        rel = path.relative_to(PRODUCTION_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else None)
            if name not in _EXECUTION_STACK:
                continue
            # Quarantined classes are forbidden everywhere in production.
            if name in _QUARANTINED_STACK:
                offenders.setdefault(rel, []).append(name)
                continue
            # Canonical classes are allowed only in the authorized module.
            if rel != _AUTHORIZED_COMPOSITION_MODULE:
                offenders.setdefault(rel, []).append(name)
    assert offenders == {}, (
        f"production code constructs an execution stack outside the authorized "
        f"composition module: {offenders}"
    )


def test_f6_only_the_canonical_path_took_the_deterministic_identity():
    """The identity asymmetry is deliberate, and stated rather than implied.

    The canonical module imports the deterministic generator and never calls the
    random one. The orphan still calls the random one -- which is part of why it
    is the orphan, and is left unchanged because changing it would be work on a
    path that must not become production.
    """
    canonical = _imported("execution/trade_lifecycle.py")
    assert "generate_deterministic_client_order_id" in canonical
    assert "generate_client_order_id" not in _called("execution/trade_lifecycle.py"), (
        "the canonical path called the random generator again")

    assert "generate_client_order_id" in _called("execution/execution_engine.py"), (
        "if the orphan ever stops needing this note, revisit the quarantine")

def test_f7_the_third_submission_path_is_recorded_as_a_known_gap():
    """`multi_user_orchestrator` still names its three orders randomly. Stated.

    There are THREE places in this repository that can POST an order: the
    canonical path (now deterministic), the quarantined orphan, and
    `UserExecutionSession.execute_trade`, which mints `generate_client_order_id`
    for entry, SL and TP. It was not in this task's scope, so it is unchanged --
    but "not in scope" must not read as "verified deterministic", so the gap is
    asserted here and carried in the report rather than left for someone to
    discover during a restart.

    What makes it safe TODAY is not the identity: it is that nothing in
    `quantedge/` constructs `MultiUserExecutionOrchestrator` either (F5), so this
    path has no production caller. If one is ever added, this test is the note
    that says the ids must be converted first.
    """
    called = _called("execution/multi_user_orchestrator.py")
    assert "generate_client_order_id" in called, (
        "if this path is now deterministic, update the report's known-gap list")
    assert "generate_deterministic_client_order_id" not in called
    assert "place_order" in called, "and it really can submit; the gap is not theoretical"


# ══ G. P0-1 regression: the whole identity, not a prefix of it ═════════════════
# The read-only execution-safety audit's P0-1 finding was that
# `generate_deterministic_client_order_id` hashed nothing. It pasted the first 8
# non-dash characters of the account id and the first 12 of the setup id into the
# name, so `XRPUSD_1h_MANUAL_SMC_MANUAL_XRPUSD_LONG_...` and its SHORT twin were
# one order as far as the exchange and `_resolve_entry_order` were concerned.
# Section A pins the general properties of the identity; this section pins that
# specific defect, case by case.


def _expected_client_order_id(account_id: str, setup_id: str, role: str) -> str:
    """Recompute the documented id independently of the implementation.

    Deliberately NOT a call into `delta_client`: the point of a pin is to
    disagree if the construction changes. The documented construction is SHA-256
    over the length-prefixed `(account, setup, role)` identity, the first 20 hex
    characters upper-cased, then the readable role -- truncated to 32.
    """
    normalized_role = str(role).strip().upper()
    identity = "|".join(
        f"{len(part)}:{part}"
        for part in (str(account_id), str(setup_id), normalized_role)
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20].upper()
    return f"QE-{digest}-{normalized_role}"[:32]


#: Two setup ids that were ONE name under the old scheme. Both are real Manual
#: SMC shapes: the direction is the last token, far beyond character 12.
COLLIDING_LONG = "XRPUSD_1h_MANUAL_SMC_MANUAL_XRPUSD_LONG_1756339200"
COLLIDING_SHORT = "XRPUSD_1h_MANUAL_SMC_MANUAL_XRPUSD_SHORT_1756339200"


def test_g1_the_generator_is_the_documented_function_of_the_whole_identity():
    """Every role's id equals the independently recomputed identity digest."""
    for role in (*ROLES, "SL2", "TP2", "SL999"):
        assert generate_deterministic_client_order_id(ACCOUNT, SETUP, role) == (
            _expected_client_order_id(ACCOUNT, SETUP, role)), (
            f"role {role}: id is no longer the documented digest of the full triple")

def test_g2_repeated_calls_with_identical_inputs_are_identical():
    """Requirement 1: same setup -> same id, called any number of times."""
    for role in (*ROLES, "SL2", "TP2"):
        ids = {
            generate_deterministic_client_order_id(ACCOUNT, SETUP, role)
            for _ in range(50)
        }
        assert len(ids) == 1, f"role {role} is not deterministic within one process"


def test_g3_the_id_is_stable_across_a_restart_not_merely_within_a_process():
    """Requirement 1, the part a same-process loop cannot prove.

    A fresh interpreter is the honest test of "stable across restart": it has a
    different `PYTHONHASHSEED`, so an id derived from `hash()` or from any
    process-local salt would differ here even though it looked deterministic in
    `test_g2`. The subprocess imports the production module and prints the id;
    nothing is mocked, no network is touched, and no exchange is contacted.
    """
    expected = generate_deterministic_client_order_id(ACCOUNT, SETUP, ORDER_ROLE_ENTRY)
    program = (
        "from quantedge.execution.delta_client import "
        "generate_deterministic_client_order_id as g\n"
        f"print(g({ACCOUNT!r}, {SETUP!r}, {ORDER_ROLE_ENTRY!r}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(PRODUCTION_ROOT.parent)},
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == expected, (
        "a restarted process computed a different name for the same order")


def test_g4_two_setups_sharing_the_old_twelve_character_prefix_differ():
    """Requirement 2, from the exact ids that used to collide.

    `XRPUSD_1h_MA` is all the old scheme kept of either name. Every role is
    checked, not just the entry, because the SL and TP of one setup colliding
    with another's is how a reduce-only order lands against the wrong position.
    """
    assert COLLIDING_LONG[:12] == COLLIDING_SHORT[:12] == "XRPUSD_1h_MA", (
        "this test's premise is that these ids share the old truncation prefix")
    for role in (*ROLES, "SL2", "TP2"):
        assert generate_deterministic_client_order_id(
            ACCOUNT, COLLIDING_LONG, role) != generate_deterministic_client_order_id(
            ACCOUNT, COLLIDING_SHORT, role), f"role {role} still collides"

def test_g5_the_same_setup_long_and_short_are_different_orders():
    """Requirement 3, stated on its own because it is the dangerous case.

    A LONG and a SHORT on one symbol are opposite trades. Sharing one
    `client_order_id` means `get_order_by_client_id` can hand a restart the wrong
    side's order, and the engine would then attach a reduce-only SELL bracket to a
    SHORT position -- adding to it instead of protecting it.
    """
    long_ids = {
        role: generate_deterministic_client_order_id(ACCOUNT, COLLIDING_LONG, role)
        for role in (*ROLES, "SL2", "TP2")
    }
    short_ids = {
        role: generate_deterministic_client_order_id(ACCOUNT, COLLIDING_SHORT, role)
        for role in (*ROLES, "SL2", "TP2")
    }
    assert set(long_ids.values()).isdisjoint(short_ids.values())
    assert len(set(long_ids.values()) | set(short_ids.values())) == 2 * len(long_ids)


def test_g6_entry_sl_and_tp_are_three_different_orders():
    """Requirement 4. One setup's legs must never share a name."""
    for setup_id in (SETUP, COLLIDING_LONG, COLLIDING_SHORT, OTHER_SETUP):
        ids = [
            generate_deterministic_client_order_id(ACCOUNT, setup_id, role)
            for role in ROLES
        ]
        assert len(set(ids)) == 3, f"{setup_id}: entry/sl/tp collapsed into fewer names"


def test_g7_replacement_bracket_roles_are_distinct_and_deterministic():
    """Requirement 5: SL2/TP2 are their own orders, and stably so.

    A resized bracket must not reuse the cancelled leg's name (Delta's replay
    semantics for a spent `client_order_id` are UNVERIFIED, and a refusal there
    would leave a filled position unprotected), and must not collide with the
    other leg's replacement either.
    """
    names = {}
    for generation in range(0, 5):
        for base in (ORDER_ROLE_STOP_LOSS, ORDER_ROLE_TAKE_PROFIT):
            role = protection_role(base, generation)
            cid = generate_deterministic_client_order_id(ACCOUNT, SETUP, role)
            assert cid == generate_deterministic_client_order_id(ACCOUNT, SETUP, role)
            assert cid == _expected_client_order_id(ACCOUNT, SETUP, role)
            names[(base, generation)] = cid
    assert len(set(names.values())) == len(names), "a generation reused another's name"
    assert names[(ORDER_ROLE_STOP_LOSS, 1)] == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, "SL2"), "SL generation 1 is not the SL2 identity"

#: Every id constraint this repository actually imposes on a `client_order_id`.
#: There is no separate validator: `delta_client` self-imposes the 32-character
#: cap, `validation.py` check 16 rejects a duplicate against
#: `active_client_order_ids`, and `models.py` passes the value through. No larger
#: exchange-documented maximum is recorded anywhere here, so the cap is treated
#: as binding rather than relaxed upward on a guess (rule #16).
_LEGAL_ID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")


def test_g8_generated_ids_satisfy_every_constraint_this_repository_imposes():
    """Requirement 6. Length, emptiness, charset, and the duplicate check."""
    setups = (SETUP, OTHER_SETUP, COLLIDING_LONG, COLLIDING_SHORT, "s", "x" * 400)
    accounts = (ACCOUNT, OTHER_ACCOUNT, "a", "acc-with-dashes-and_underscores")
    for account_id in accounts:
        for setup_id in setups:
            for role in (*ROLES, "SL2", "TP999"):
                cid = generate_deterministic_client_order_id(account_id, setup_id, role)
                assert 0 < len(cid) <= CLIENT_ORDER_ID_MAX_LENGTH, cid
                assert cid == cid.strip(), "no leading or trailing whitespace"
                assert cid.isascii(), cid
                assert set(cid) <= _LEGAL_ID_CHARS, (
                    f"{cid} introduces characters the old scheme never emitted")
                assert cid.startswith("QE-"), cid
                # The digest is intact: a shortened digest is a re-narrowed
                # identity, which is the defect being pinned.
                assert len(cid.split("-")[1]) == DETERMINISTIC_CLIENT_ORDER_ID_DIGEST_CHARS


@pytest.mark.asyncio
async def test_g9_two_previously_colliding_setups_post_two_different_ids():
    """The end-to-end consequence, through the real submission path.

    Not the generator in isolation: two managers on the SAME account submit the
    LONG and the SHORT of one symbol (two managers because the single-trade lock
    correctly refuses two at once -- this is the same account trading the second
    setup after the first is done). Under the old scheme both POSTed the same
    `client_order_id`, so check 16 would reject the second as a duplicate or, if
    the first had been archived, the exchange would hold two orders under one
    name. Two POSTs, two names.
    """
    long_exchange, short_exchange = FakeExchange(), FakeExchange()
    long_manager = _manager(long_exchange, store=_store())
    short_manager = _manager(short_exchange, store=_store())

    long_record = await long_manager.execute_trade_setup(
        _decision(setup_id=COLLIDING_LONG), ACCOUNT, USER)
    short_record = await short_manager.execute_trade_setup(
        _decision(
            setup_id=COLLIDING_SHORT,
            direction=StrategyDirection.SHORT,
            stop_loss=ENTRY_PRICE + Decimal("1000.00"),
            take_profit=ENTRY_PRICE - Decimal("3000.00"),
        ),
        ACCOUNT,
        USER,
    )

    assert long_record.state is TradeLifecycleState.ENTRY_SUBMITTED, long_record.error_message
    assert short_record.state is TradeLifecycleState.ENTRY_SUBMITTED, short_record.error_message
    assert long_exchange.client_order_ids() == [
        _expected_client_order_id(ACCOUNT, COLLIDING_LONG, ORDER_ROLE_ENTRY)]
    assert short_exchange.client_order_ids() == [
        _expected_client_order_id(ACCOUNT, COLLIDING_SHORT, ORDER_ROLE_ENTRY)]
    assert long_exchange.client_order_ids() != short_exchange.client_order_ids()

@pytest.mark.asyncio
async def test_g10_override_client_order_id_still_wins_untouched():
    """Requirement 7. The caller-supplied escape is preserved exactly.

    The override is taken verbatim -- not normalised, not hashed, not prefixed --
    and the generator is not consulted for the entry at all. The protective legs
    are unaffected by it, which is the pre-existing behaviour and is unchanged:
    the override names one order, not the bracket.
    """
    exchange = FakeExchange()
    manager = _manager(exchange)
    supplied = "operator-supplied-id-01"

    record = await manager.execute_trade_setup(
        _decision(), ACCOUNT, USER, override_client_order_id=supplied)

    assert record.state is TradeLifecycleState.ENTRY_SUBMITTED, record.error_message
    assert record.entry_client_order_id == supplied, "the override was rewritten"
    assert exchange.client_order_ids() == [supplied]
    assert supplied != generate_deterministic_client_order_id(
        ACCOUNT, SETUP, ORDER_ROLE_ENTRY), "this test would prove nothing otherwise"

    # And the legs still take the canonical identity.
    await manager.on_entry_fill(SETUP, QUANTITY, ENTRY_PRICE)
    assert exchange.client_order_ids()[1:] == [
        generate_deterministic_client_order_id(ACCOUNT, SETUP, ORDER_ROLE_STOP_LOSS),
        generate_deterministic_client_order_id(ACCOUNT, SETUP, ORDER_ROLE_TAKE_PROFIT),
    ]


def test_g11_one_generator_serves_every_canonical_call_site():
    """"No second parallel id scheme" is checked structurally, not asserted.

    Every `client_order_id` the canonical module assigns comes from either the one
    generator, the caller's override, or a value the EXCHANGE supplied (an adopted
    attached bracket). Nothing in it builds an id out of string pieces, which is
    what the old scheme did and what a well-meaning future patch might redo.
    """
    source = _source("execution/trade_lifecycle.py")
    assert "generate_deterministic_client_order_id" in source
    # The old construction, in any of its parts, must not reappear anywhere.
    for fragment in ('f"QE-{', "'QE-'", '"QE-" +', "replace(\"-\", \"\")[:8]",
                     "replace(\"-\", \"\")[:12]", "[:12]", "role.upper()[:5]"):
        assert fragment not in source, (
            f"{fragment!r} is back in the canonical path: an id is being built by "
            f"hand again rather than derived")

# ══ H. P0-2 regression: a zero-fill record cannot claim a position ═════════════
# The audit's P0-2 finding: `reconcile_active_trades_with_exchange` added every
# active record's product to `claimed_products` unconditionally -- before it had
# refreshed the fill state, and regardless of whether the record accounted for any
# position at all. Step 4 skips claimed products, so a record holding
# `filled_quantity == 0` (the state a timed-out entry submission leaves behind,
# and one that raises no alert of its own) hid a real, unprotected exchange
# position, the run reported CLEAN, and any prior evidence was cleared with it.


class _PostFailsExchange(FakeExchange):
    """Readable for state, unreachable for the entry POST.

    The ambiguous submission exactly as production meets it: the order may have
    reached the matching engine or may not have, so the engine records
    `RECONCILIATION_REQUIRED` with nothing filled and retains the lock. In these
    tests the order DID arrive and DID fill -- which is what `positions` says --
    and the engine has no way to know it.
    """

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            self.requests.append(request)
            return httpx.Response(
                503, json={"success": False, "error": "gateway timeout"})
        return super().__call__(request)


async def _after_ambiguous_submission(exchange: FakeExchange) -> TradeLifecycleManager:
    """Reach the zero-fill active record the production way, not by hand.

    Driving the real submission path into its timeout arm means the state under
    test is one the engine actually produces, rather than one a test invented.
    """
    manager = _manager(exchange)
    record = await manager.execute_trade_setup(_decision(), ACCOUNT, USER)
    assert record.state is TradeLifecycleState.RECONCILIATION_REQUIRED, record.state
    assert record.filled_quantity == Decimal("0")
    assert record.entry_client_order_id == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, ORDER_ROLE_ENTRY)
    assert SETUP in manager._active_trades, "the record must still be active"
    # P1-5 RE-PIN. This assertion previously read `== []` with the note "the
    # timeout arm raises no alert of its own -- which is why this masking defect
    # was invisible rather than merely unfixed". That WAS the state of the code:
    # the ambiguity was recorded only in the record's state, so nothing raised an
    # operator-visible alert. P1-5 makes the ambiguity a first-class alert, so the
    # helper now pins the alert instead of its absence -- the masking route the H
    # section tests is unchanged, it is simply no longer silent on the way in.
    ambiguous = [
        a for a in manager._reconciliation_alerts
        if a["code"] == "AMBIGUOUS_ENTRY_SUBMISSION"
    ]
    assert len(ambiguous) == 1, manager._reconciliation_alerts
    context = ambiguous[0]["context"]
    assert context["setup_id"] == SETUP
    assert context["client_order_id"] == generate_deterministic_client_order_id(
        ACCOUNT, SETUP, ORDER_ROLE_ENTRY), (
        "the alert must identify the order attempt that may be live")
    assert context["account_id"] == ACCOUNT
    assert context["error_type"] in ("DeltaConnectionError", "TimeoutError")
    return manager


def _posts(exchange: FakeExchange) -> List[str]:
    return [p for p in exchange.paths() if p.startswith("POST")]

@pytest.mark.asyncio
async def test_h1_a_zero_fill_record_does_not_hide_an_exchange_position():
    """Required case 1. The position is surfaced and the run is NOT clean.

    Local state holds one active setup that believes nothing filled. The exchange
    holds a live position on that setup's product. Before the fix this reported
    `orphan_positions=[]`, `unresolved=[]` and CLEAN -- a live, unprotected
    position declared reconciled.
    """
    exchange = _PostFailsExchange(positions=[_position_json()])
    manager = await _after_ambiguous_submission(exchange)

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert summary["orphan_positions"] == [SYMBOL], (
        "the position must be surfaced, not skipped as claimed")
    assert summary["unresolved"] == [SETUP], (
        "the setup that may have caused it must be named unresolved")
    clean = (
        not summary["unresolved"]
        and not summary["orphan_positions"]
        and not summary["orphan_orders"]
    )
    assert clean is False, "this run must not be able to compute clean"
    assert "alerts_cleared" not in summary
    codes = [a["code"] for a in manager.reconciliation_alerts]
    assert "POSITION_ON_ZERO_FILL_SETUP" in codes, codes
    assert any(
        e["action"] == "RECONCILIATION_ORPHAN_POSITION"
        and e["details"].get("zero_fill_local_setups") == [SETUP]
        for e in manager.state_store.audit_events
    ), "the finding must name the implicated setup in the audit trail"

    # Reported, not repaired: no adoption, no fabricated fill, no order.
    assert manager._active_trades[SETUP].filled_quantity == Decimal("0")
    assert manager._active_trades[SETUP].protected_quantity == Decimal("0")
    assert _posts(exchange) == ["POST /v2/orders"], (
        "reconciliation must not place anything; the only POST is the failed entry")

@pytest.mark.asyncio
async def test_h2_startup_stays_blocked_across_the_restart_that_matters(tmp_path):
    """Required case 1, at the boundary that decides whether trading resumes.

    The process that timed out goes down. Its state -- one active zero-fill record
    plus the retained lock -- is persisted and restored. The exchange still holds
    the position. The gate must refuse to admit, and the next entry must be
    rejected with the startup-gate code rather than submitted alongside a live
    unprotected position. The strategy runtime is made to AGREE about the setup so
    that the only thing left to block on is the reconciliation finding.
    """
    donor = await _after_ambiguous_submission(_PostFailsExchange())
    path = persist_execution_state(donor, tmp_path / "execution_state.json")

    exchange = FakeExchange(positions=[_position_json()])
    manager = _manager(exchange, lock=SingleTradeLockManager())
    gate = _gate(manager, path, _FakeRuntime(setup_id=SETUP, holds_trade=True))

    result = await gate.arm()

    assert result.admitted is False, "a masked position was admitted"
    assert result.state_loaded is True
    assert "did not return clean" in (result.block_reason or ""), result.block_reason
    assert f"orphan_positions=['{SYMBOL}']" in (result.block_reason or ""), (
        result.block_reason)
    assert gate.armed is False
    assert manager.reconciliation_alerts, "the finding must survive as evidence"

    record = await manager.execute_trade_setup(
        _decision(setup_id=OTHER_SETUP), ACCOUNT, USER)
    assert record.rejection_code == REFUSAL_STARTUP_GATE
    assert exchange.placed == [], "no order may be submitted behind a blocked gate"

@pytest.mark.asyncio
async def test_h3_a_zero_fill_record_against_a_flat_exchange_is_still_clean():
    """Required case 2. The fix must not turn every ambiguous entry into a block.

    Same local state as H1 -- one active record with nothing filled -- but this
    time the exchange listings are flat. Nothing is invented and nothing is
    orphaned: there is no position to explain and no untracked order.

    P1-5 RE-PIN. What this test originally also asserted -- `unresolved == []`,
    `reconciliation_alerts == []`, i.e. a fully CLEAN run -- is precisely the
    masking route P1-5 closes. Two momentarily empty listings do not prove the
    submission failed, and a run that computed clean here cleared the ambiguous
    alert that was protecting the account. So the P0-2 property this test exists
    for is pinned exactly as before (nothing orphaned, nothing invented, the
    exchange reachable, the record still active and unfilled), and the ambiguity
    itself is now correctly reported as unresolved instead of being erased.
    """
    exchange = _PostFailsExchange()
    manager = await _after_ambiguous_submission(exchange)

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert summary["orphan_positions"] == [], "nothing to orphan on a flat book"
    assert summary["orphan_orders"] == []
    assert summary["exchange_unreachable"] is False
    # The ambiguity, and ONLY the ambiguity, is what keeps this run unresolved.
    assert summary["unresolved"] == [SETUP], summary
    assert [a["code"] for a in manager.reconciliation_alerts] == [
        "AMBIGUOUS_ENTRY_SUBMISSION"], (
        "no NEW alert may be raised per run, and the original may not be cleared")
    assert "alerts_cleared" not in summary, (
        "a flat listing is not authoritative proof the submission failed")
    assert _posts(exchange) == ["POST /v2/orders"]
    # Still active and still unfilled: unresolved is not "closed", and nothing
    # was repaired, adopted or re-sent.
    assert manager._active_trades[SETUP].filled_quantity == Decimal("0")
    assert manager._active_trades[SETUP].protected_quantity == Decimal("0")


@pytest.mark.asyncio
async def test_h4_a_filled_record_with_a_matching_position_still_reconciles_clean():
    """Required case 3. The claim still happens -- for a record that earned it.

    The whole risk of this fix is over-blocking: if a genuinely filled, genuinely
    protected trade stopped claiming its product, every restart into a live trade
    would report its own position as an orphan and refuse to trade. It does not.
    """
    exchange = _clean_world()
    manager = _manager(exchange)
    manager._active_trades[SETUP] = _protected_record()
    manager._entry_fill_sizes[SETUP] = QUANTITY

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert summary["checked"] == 1
    assert summary["orphan_positions"] == [], "the record's own position was orphaned"
    assert summary["orphan_orders"] == []
    assert summary["unresolved"] == []
    assert summary["protection_restored"] == []
    assert manager.reconciliation_alerts == []
    assert exchange.placed == [], "a clean book needs no repair"
    assert manager._active_trades[SETUP].state is TradeLifecycleState.PROTECTED_POSITION

def _claim_guards() -> List[str]:
    """The `if` tests enclosing every `claimed_products.add(...)` in the function.

    Structural rather than behavioural, because "is not in the claim set" is a
    statement about a local variable no test can observe directly. What CAN be
    checked is that the claim is reachable only under a fill test, and that there
    is exactly one claim -- so a second, unguarded one cannot be added quietly.
    """
    node = _function_node(
        "execution/trade_lifecycle.py",
        "TradeLifecycleManager.reconcile_active_trades_with_exchange",
    )

    def _is_claim(inner) -> bool:
        return (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "add"
            and isinstance(inner.func.value, ast.Name)
            and inner.func.value.id == "claimed_products"
        )

    total = sum(1 for n in ast.walk(node) if _is_claim(n))
    assert total == 1, f"expected exactly one position claim, found {total}"
    return [
        ast.unparse(branch.test)
        for branch in ast.walk(node)
        if isinstance(branch, ast.If) and any(_is_claim(n) for n in ast.walk(branch))
    ]


def test_h5_only_a_filled_record_can_enter_the_position_claim_set():
    """Required case 4, pinned where the defect actually lived.

    The old code claimed the product on the line after the symbol lookup, outside
    any fill test and before step 1 had refreshed anything. Every route to the
    claim must now pass a `filled_quantity` test, and there must be only one.
    """
    guards = _claim_guards()
    assert guards, "the position claim is no longer guarded by anything"
    for test_source in guards:
        assert "filled_quantity" in test_source, (
            f"a position can be claimed without a fill: guarded only by {test_source}")

@pytest.mark.asyncio
async def test_h6_a_zero_fill_record_cannot_erase_earlier_reconciliation_evidence():
    """Required case 5. The masking defect did not merely hide the new finding.

    Clearing alerts is the layer's ONLY self-healing route, and it is gated on a
    clean run. A zero-fill record that made the run compute clean therefore also
    wiped every alert raised before it -- so an operator investigating a prior
    finding would return to a book that claimed to be reconciled. Both the earlier
    evidence and the new finding must survive.
    """
    exchange = _PostFailsExchange(positions=[_position_json()])
    manager = await _after_ambiguous_submission(exchange)
    manager._raise_reconciliation_alert(
        "PRIOR_EVIDENCE", SYMBOL, "raised by an earlier run and not yet resolved")
    # Two standing alerts now: the ambiguous submission P1-5 records at the point
    # of ambiguity, and this earlier unresolved finding.
    assert [a["code"] for a in manager.reconciliation_alerts] == [
        "AMBIGUOUS_ENTRY_SUBMISSION", "PRIOR_EVIDENCE"]

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    codes = [a["code"] for a in manager.reconciliation_alerts]
    assert "PRIOR_EVIDENCE" in codes, "earlier evidence was erased by a zero-fill record"
    assert "POSITION_ON_ZERO_FILL_SETUP" in codes, codes
    assert "alerts_cleared" not in summary
    assert "RECONCILIATION_ALERTS_CLEARED" not in [
        e["action"] for e in manager.state_store.audit_events]


@pytest.mark.asyncio
async def test_h7_an_unattributable_position_is_still_reported_as_a_plain_orphan():
    """The pre-existing case is untouched: no local record, no implicated setup.

    The new branch must not swallow the original finding. With nothing active at
    all, the alert code and the audit payload stay exactly what section D7 pins,
    and the payload gains no `zero_fill_local_setups` key to mislead an operator
    into looking for a setup that does not exist.
    """
    exchange = FakeExchange(positions=[_position_json()])
    manager = _manager(exchange)

    summary = await manager.reconcile_active_trades_with_exchange(ACCOUNT, USER)

    assert summary["checked"] == 0
    assert summary["orphan_positions"] == [SYMBOL]
    assert summary["unresolved"] == []
    assert [a["code"] for a in manager.reconciliation_alerts] == ["ORPHAN_EXCHANGE_POSITION"]
    orphan_audits = [
        e for e in manager.state_store.audit_events
        if e["action"] == "RECONCILIATION_ORPHAN_POSITION"
    ]
    assert len(orphan_audits) == 1
    assert "zero_fill_local_setups" not in orphan_audits[0]["details"]
    assert manager._active_trades == {}, "nothing may be invented to explain it"
