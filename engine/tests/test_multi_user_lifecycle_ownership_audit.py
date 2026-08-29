"""
Task H audit: multi-user lifecycle ownership, lock ownership, and setup identity.

Scope: the two production order paths and the lock that arbitrates both.

  A. single account -> `MarketScannerOrchestrator.scan_and_execute`
                    -> `TradeLifecycleManager.execute_trade_setup`
  B. multi user     -> `MultiUserExecutionOrchestrator.dispatch_signal`
                    -> `UserExecutionSession.execute_trade`

WHAT THE LOCK MEANS (proven, not assumed). `single_trade_lock.py`'s module
docstring states the rule as "Exactly ONE active trade per trading account at
any given time" and the release protocol as "Lock is released ONLY when the
trade is confirmed POSITION_CLOSED by Delta exchange". Path A's
`_release_setup_lock` docstring says the same thing from the caller's side:
"Call this only on exits where nothing can exist on the exchange. When an order
may be live, or a position may be open or unprotected, the lock is retained on
purpose (safety rules #11, #14) and released by `close_position` /
reconciliation instead."

So the lock means "this account currently owns an open trade", not "a setup is
currently being executed". Two consequences are pinned below:

  * retaining the lock after a *successful* execution is correct on both paths
    (§B, §C) -- it is not a leak, and this audit does not change it;
  * the mechanisms that release it must actually work, because they are the only
    way it ever comes back. §E covers the reconciliation release path.

The four defects this file first reproduces and then pins as fixed, each proven
from evidence inside this repository rather than from exchange semantics:

  1. `reconciliation.py` called `release_lock` with four positional arguments
     against a three-argument signature. The guaranteed `TypeError` was
     swallowed by the surrounding `except Exception`, so the orphaned lock was
     never released -- contradicting that module's own stated responsibility,
     "Auto-reconciles orphaned locks", and leaving semantics-B locks permanent.
  2. `reconciliation.py` called `force_release_lock(account_id, reason)` against
     `force_release_lock(reason, account_id=None)`. The arguments were swapped,
     so the authoritative persistence layer was told to release the lock of an
     account literally named "DELTA_RECONCILED_FLAT". This raised nothing.
  3. The same backend force-release ran on *any* discrepancy under
     `auto_resolve`, including a bare equity mismatch, rather than only when the
     exchange is flat -- releasing the authoritative lock while a position may
     still be open, against the documented release protocol.
  4. Path B released the lock in its broad failure handler even when the entry
     order had already been accepted by the exchange, i.e. exactly the state
     Path A retains it for: a position that may be open and unprotected.

And one identity defect, proven by execution rather than by reading:

  5. Re-dispatching the *same* `setup_id` to the same account placed a second
     entry order and a second bracket. Path A refuses this with
     `DUPLICATE_SETUP_ID`; Path B's lock replay is idempotent by design and its
     exposure check reads positions only, so a resting unfilled entry order let
     the duplicate through.

Zero network access: every Delta client here is a mock.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from quantedge.execution.backend_client import BackendClient
from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.delta_client import DeltaIndiaClient
from quantedge.execution.models import (
    ConnectionState,
    DeltaOrderRequest,
    DeltaOrderResponse,
    DeltaPosition,
    DeltaWalletBalance,
    OrderStatus,
    PositionSide,
    ReconciliationDiscrepancyType,
)
from quantedge.execution.multi_user_orchestrator import (
    MultiUserExecutionOrchestrator,
    TradeDirection,
    UserAccountConfig,
    UserExecutionSession,
)
from quantedge.execution.reconciliation import DeltaReconciliationService
from quantedge.execution.single_trade_lock import (
    SingleTradeLockError,
    SingleTradeLockManager,
)
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import CloseReason, TradeLifecycleManager
from quantedge.execution.validation import OrderValidationGateway

#: A valid long setup: stop < entry < target.
LONG = (Decimal("77000"), Decimal("76000"), Decimal("79000"))

USER = "usr-h"
ACCOUNT = "acct-h"
SETUP = "setup-h-1"


# ── Mock exchange ─────────────────────────────────────────────────────────────


def _client(mark_price: Decimal = LONG[0], balance: str = "1000.00",
            positions=None, fail_after: int | None = None,
            failure: Exception | None = None) -> MagicMock:
    """A funded, flat account whose orders fill immediately.

    `fail_after` makes the (1-based) n-th `place_order` raise `failure`, so a
    bracket failure that follows an accepted entry order can be reproduced.
    """
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "H_KEY"
    client._api_secret = "H_SECRET"
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

    counter = [9000]

    async def _place(req: DeltaOrderRequest) -> DeltaOrderResponse:
        client.submitted.append(req)
        if fail_after is not None and len(client.submitted) >= fail_after:
            raise failure or RuntimeError("bracket placement failed")
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
    client.get_open_orders = AsyncMock(return_value=[])
    return client


def _account(client: MagicMock, **over) -> UserAccountConfig:
    kwargs = dict(
        user_id=USER,
        account_id=ACCOUNT,
        is_active=True,
        algo_enabled=True,
        kill_switch_active=False,
        api_key="H_KEY",
        api_secret="H_SECRET",
        client_factory=lambda _k, _s: client,
    )
    kwargs.update(over)
    return UserAccountConfig(**kwargs)


def _session(client: MagicMock, lock: SingleTradeLockManager | None = None,
             **over) -> UserExecutionSession:
    return UserExecutionSession(
        config=_account(client, **over),
        lock_manager=lock or SingleTradeLockManager(),
        capital_allocator=CapitalAllocator(),
    )


async def _execute(session: UserExecutionSession, setup_id: str = SETUP,
                   symbol: str = "BTCUSD",
                   direction: TradeDirection = TradeDirection.LONG,
                   prices=LONG, leverage: int = 10):
    entry, stop, target = prices
    return await session.execute_trade(
        setup_id=setup_id,
        symbol=symbol,
        direction=direction,
        planned_entry_price=entry,
        stop_loss_price=stop,
        take_profit_price=target,
        default_leverage=leverage,
    )


def _open_position(symbol: str = "BTCUSD") -> DeltaPosition:
    return DeltaPosition(
        product_id=27,
        product_symbol=symbol,
        side=PositionSide.LONG,
        size=Decimal("100"),
        entry_price=LONG[0],
        mark_price=LONG[0],
        liquidation_price=Decimal("70000"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        leverage=Decimal("10"),
        margin=Decimal("100"),
    )


def _store(account_id: str = ACCOUNT, user_id: str = USER) -> LocalStateStore:
    store = LocalStateStore(account_id=account_id)
    store.account.user_id = user_id
    store.account.available_balance = Decimal("1000")
    store.account.total_equity = Decimal("1000")
    store.account.current_balance = Decimal("1000")
    store.account.last_synced_at = datetime.now(timezone.utc)
    return store


def _recon_client(positions=None, orders=None, balance: str = "1000.00") -> MagicMock:
    client = MagicMock(spec=DeltaIndiaClient)
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
    client.get_open_orders = AsyncMock(return_value=list(orders or []))
    return client


# ── §A. What the lock means, from repository evidence ─────────────────────────


def test_the_lock_documents_one_open_trade_per_account_not_one_execution():
    """Semantics B: the lock tracks an open trade, not an in-flight execution."""
    from quantedge.execution import single_trade_lock as lock_mod

    doc = inspect.getdoc(lock_mod) or ""
    assert "Exactly ONE active trade per trading account" in doc
    assert "released ONLY when the trade is confirmed POSITION_CLOSED" in doc


def test_path_a_documents_the_same_ownership_rule_from_the_caller_side():
    doc = inspect.getdoc(TradeLifecycleManager._release_setup_lock) or ""
    # Released only where nothing can exist on the exchange ...
    assert "only on exits where nothing can exist on the exchange" in doc
    # ... otherwise retained on purpose, and released elsewhere.
    assert "retained on purpose" in doc
    assert "reconciliation" in doc


def test_release_lock_is_setup_scoped_and_takes_exactly_three_arguments():
    """The arity every caller must respect. Defect 1 violated it."""
    params = list(inspect.signature(SingleTradeLockManager.release_lock).parameters)
    assert params == ["self", "user_id", "account_id", "setup_id"]


def test_backend_force_release_takes_reason_first_then_account():
    """The order every caller must respect. Defect 2 violated it."""
    params = list(inspect.signature(BackendClient.force_release_lock).parameters)
    assert params == ["self", "reason", "account_id"]


def test_the_lock_is_keyed_per_user_and_account_so_accounts_cannot_collide():
    lock = SingleTradeLockManager()
    lock.acquire_lock("u1", "a1", "s1", "BTCUSD")
    lock.acquire_lock("u2", "a2", "s2", "BTCUSD")
    assert lock.is_locked("u1", "a1")[0] is True
    assert lock.is_locked("u2", "a2")[0] is True
    assert lock.is_locked("u1", "a2")[0] is False


# ── §B. Where a successful Path-B trade exists afterwards ─────────────────────


@pytest.mark.asyncio
async def test_path_b_success_keeps_the_lock_which_is_correct_under_semantics_b():
    lock = SingleTradeLockManager()
    res = await _execute(_session(_client(), lock))
    assert res.status == "EXECUTED"
    locked, setup_id, symbol = lock.is_locked(USER, ACCOUNT)
    assert (locked, setup_id, symbol) == (True, SETUP, "BTCUSD")


@pytest.mark.asyncio
async def test_the_only_record_of_a_path_b_trade_is_its_returned_result():
    """Path B is a stateless direct executor: the result object is the record.

    Entry, SL and TP order ids, the fill price, the sized quantity and the
    balance the sizing was computed from all survive only in the returned
    `UserExecutionResult`. Nothing is written to a lifecycle manager, a
    `LocalStateStore`, or the backend. Whoever calls `dispatch_signal` owns that
    object; if it is dropped, the ids are gone. This is the architectural fact
    behind §E and §F, and it is pinned here rather than changed.
    """
    session = _session(_client())
    res = await _execute(session)

    assert res.entry_order_id and res.sl_order_id and res.tp_order_id
    assert res.sl_price == LONG[1] and res.tp_price == LONG[2]
    assert res.allocated_quantity > 0
    assert res.live_balance_queried == Decimal("1000.00")
    assert res.executed_at is not None

    # No lifecycle-manager-shaped state exists on either object.
    for attr in ("state_store", "lifecycle_manager", "_active_trades",
                 "backend_client", "_trade_history"):
        assert not hasattr(session, attr)


@pytest.mark.asyncio
async def test_a_path_b_trade_is_invisible_to_the_single_account_state_store():
    """`LocalStateStore` holds exactly one account, so it cannot represent Path B."""
    store = _store()
    await _execute(_session(_client()))
    assert store.get_open_positions() == []
    assert store.get_open_orders() == []
    # get_account ignores the id it is given: one store == one account.
    assert store.get_account("some-other-account") is store.account


@pytest.mark.asyncio
async def test_a_path_b_trade_cannot_be_closed_or_counted_by_the_lifecycle_manager():
    """Every lifecycle mutator is keyed on `_active_trades[setup_id]`.

    Path B never registers there, so `close_position`, `on_entry_fill` and the
    daily realized-loss accumulator cannot see a Path-B trade. The daily-loss
    guard therefore protects Path A only. Pinned as an architecture fact.
    """
    lock = SingleTradeLockManager()
    mgr = TradeLifecycleManager(
        client=_client(),
        validation_gateway=OrderValidationGateway(),
        state_store=_store(),
        single_trade_lock=lock,
    )
    await _execute(_session(_client(), lock))

    assert mgr.get_active_trade(SETUP) is None
    assert mgr.get_all_active_trades() == []
    assert mgr.get_realized_daily_loss() == Decimal("0")
    with pytest.raises(ValueError, match="No active trade found"):
        await mgr.close_position(SETUP, CloseReason.TAKE_PROFIT)


@pytest.mark.asyncio
async def test_the_kill_switch_is_read_per_account_before_any_path_b_order():
    """Path B's kill switch is its own per-user config flag, checked pre-lock."""
    lock = SingleTradeLockManager()
    client = _client()
    res = await _execute(_session(client, lock, kill_switch_active=True))
    assert res.status == "SKIPPED_KILL_SWITCH"
    assert client.submitted == []
    # Refused before the lock was taken, so nothing to release.
    assert lock.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_an_inactive_or_algo_disabled_account_is_skipped_pre_lock():
    for over, expected in ((dict(is_active=False), "SKIPPED_INACTIVE"),
                           (dict(algo_enabled=False), "SKIPPED_ALGO_DISABLED")):
        lock = SingleTradeLockManager()
        client = _client()
        res = await _execute(_session(client, lock, **over))
        assert res.status == expected
        assert client.submitted == []
        assert lock.is_locked(USER, ACCOUNT)[0] is False


# ── §C. Lock retention per outcome on Path B ──────────────────────────────────


@pytest.mark.asyncio
async def test_insufficient_margin_releases_the_lock_because_nothing_was_sent():
    lock = SingleTradeLockManager()
    client = _client(balance="0.00")
    res = await _execute(_session(client, lock))
    assert res.status == "BLOCKED_MARGIN"
    assert client.submitted == []
    assert lock.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_a_pre_existing_exchange_position_releases_the_lock_it_just_took():
    """Refused before any order: the account is left exactly as it was found."""
    lock = SingleTradeLockManager()
    client = _client(positions=[_open_position()])
    res = await _execute(_session(client, lock))
    assert res.status == "ERROR"
    assert "open positions on exchange" in res.error
    assert client.submitted == []
    assert lock.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_a_refused_entry_order_releases_the_lock():
    """The first `place_order` fails, so nothing can exist on the exchange."""
    lock = SingleTradeLockManager()
    client = _client(fail_after=1, failure=Exception("entry refused"))
    res = await _execute(_session(client, lock))
    assert res.status == "ERROR"
    assert len(client.submitted) == 1
    assert lock.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_a_failed_stop_loss_retains_the_lock_because_the_entry_is_live():
    """Defect 4. The entry order was accepted; the position may be unprotected.

    Path A retains the lock in exactly this state (`PROTECTION_FAILED`, safety
    rules #11 and #14). Path B must not hand the account back for a new trade
    while an unprotected position may be open on the exchange.
    """
    lock = SingleTradeLockManager()
    client = _client(fail_after=2, failure=Exception("stop-loss rejected"))
    res = await _execute(_session(client, lock))
    assert res.status == "ERROR"
    assert len(client.submitted) == 2
    locked, setup_id, _ = lock.is_locked(USER, ACCOUNT)
    assert locked is True
    assert setup_id == SETUP


@pytest.mark.asyncio
async def test_a_failed_take_profit_also_retains_the_lock():
    lock = SingleTradeLockManager()
    client = _client(fail_after=3, failure=Exception("take-profit rejected"))
    res = await _execute(_session(client, lock))
    assert res.status == "ERROR"
    assert len(client.submitted) == 3
    assert lock.is_locked(USER, ACCOUNT)[0] is True


@pytest.mark.asyncio
async def test_a_geometry_refusal_never_takes_the_lock_at_all():
    lock = SingleTradeLockManager()
    client = _client()
    # target below entry for a long: refused before the lock is acquired.
    res = await _execute(_session(client, lock),
                         prices=(LONG[0], LONG[1], Decimal("75000")))
    assert res.status == "ERROR"
    assert client.submitted == []
    assert lock.is_locked(USER, ACCOUNT)[0] is False


# ── §D. Setup identity, replay, concurrency, isolation ────────────────────────


@pytest.mark.asyncio
async def test_replaying_one_setup_id_cannot_place_a_second_bracket():
    """Defect 5. Path A refuses a duplicate `setup_id`; Path B now does too.

    The lock's same-setup replay is idempotent by design (it exists so a retried
    *acquisition* is not an error), and Path B's exposure check reads positions
    only -- so before the entry order fills, a replayed dispatch used to sail
    through and place a second entry plus a second bracket.
    """
    lock = SingleTradeLockManager()
    client = _client()
    orchestrator = MultiUserExecutionOrchestrator(lock, CapitalAllocator())
    account = _account(client)

    kwargs = dict(
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        planned_entry_price=LONG[0],
        stop_loss_price=LONG[1],
        take_profit_price=LONG[2],
        accounts=[account],
    )
    first = await orchestrator.dispatch_signal(setup_id=SETUP, **kwargs)
    second = await orchestrator.dispatch_signal(setup_id=SETUP, **kwargs)

    assert first.executed_count == 1
    assert second.executed_count == 0
    assert second.user_results[USER].status == "BLOCKED_LOCK"
    # Exactly one entry + one SL + one TP reached the exchange.
    assert len(client.submitted) == 3


@pytest.mark.asyncio
async def test_two_concurrent_dispatches_of_one_setup_place_one_bracket():
    """The check must be atomic, not read-then-acquire."""
    import asyncio

    lock = SingleTradeLockManager()
    client = _client()
    orchestrator = MultiUserExecutionOrchestrator(lock, CapitalAllocator())
    account = _account(client)

    kwargs = dict(
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        planned_entry_price=LONG[0],
        stop_loss_price=LONG[1],
        take_profit_price=LONG[2],
        accounts=[account],
    )
    a, b = await asyncio.gather(
        orchestrator.dispatch_signal(setup_id=SETUP, **kwargs),
        orchestrator.dispatch_signal(setup_id=SETUP, **kwargs),
    )
    assert {a.executed_count, b.executed_count} == {0, 1}
    assert len(client.submitted) == 3


@pytest.mark.asyncio
async def test_a_duplicate_account_inside_one_dispatch_executes_once():
    """The same account listed twice is the same replay hazard, in parallel."""
    lock = SingleTradeLockManager()
    client = _client()
    orchestrator = MultiUserExecutionOrchestrator(lock, CapitalAllocator())

    summary = await orchestrator.dispatch_signal(
        setup_id=SETUP,
        symbol="BTCUSD",
        direction=TradeDirection.LONG,
        planned_entry_price=LONG[0],
        stop_loss_price=LONG[1],
        take_profit_price=LONG[2],
        accounts=[_account(client), _account(client)],
    )
    assert summary.executed_count == 1
    assert len(client.submitted) == 3


@pytest.mark.asyncio
async def test_a_different_setup_is_blocked_while_the_account_owns_a_trade():
    lock = SingleTradeLockManager()
    client = _client()
    assert (await _execute(_session(client, lock), setup_id="setup-first")).status == "EXECUTED"
    second = await _execute(_session(client, lock), setup_id="setup-second")
    assert second.status == "BLOCKED_LOCK"
    assert len(client.submitted) == 3


@pytest.mark.asyncio
async def test_one_locked_account_does_not_block_another_account():
    lock = SingleTradeLockManager()
    client_a, client_b = _client(), _client()
    a = await _execute(_session(client_a, lock, user_id="usr-a", account_id="acct-a"))
    b = await _execute(_session(client_b, lock, user_id="usr-b", account_id="acct-b"))
    assert (a.status, b.status) == ("EXECUTED", "EXECUTED")
    assert lock.is_locked("usr-a", "acct-a")[0] is True
    assert lock.is_locked("usr-b", "acct-b")[0] is True


@pytest.mark.asyncio
async def test_identity_travels_end_to_end_on_path_b():
    """Setup, user and account identity are echoed on every result and lock."""
    lock = SingleTradeLockManager()
    client = _client()
    res = await _execute(_session(client, lock))
    assert (res.setup_id, res.user_id, res.account_id) == (SETUP, USER, ACCOUNT)
    assert lock.is_locked(USER, ACCOUNT)[1] == SETUP

    roles = [req.client_order_id.split("-")[0] for req in client.submitted]
    assert roles == ["EN", "SL", "TP"]
    # Ids are unique per order; they are not derived from the setup id, so they
    # are not idempotency keys on either path (`generate_client_order_id` is
    # timestamp+random on Path A too).
    assert len({req.client_order_id for req in client.submitted}) == 3


# ── §E. The only automated release path for a Path-B lock ─────────────────────


@pytest.mark.asyncio
async def test_reconciliation_detects_a_lock_held_over_a_flat_exchange():
    lock = SingleTradeLockManager()
    lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD")
    recon = DeltaReconciliationService(
        client=_recon_client(), state_store=_store(), single_trade_lock=lock,
    )
    report = await recon.reconcile_account(ACCOUNT, user_id=USER)
    kinds = {d.discrepancy_type for d in report.discrepancies}
    assert ReconciliationDiscrepancyType.ORPHANED_POSITION in kinds
    # Detection only: a read-only reconcile must not mutate the lock.
    assert lock.is_locked(USER, ACCOUNT)[0] is True


@pytest.mark.asyncio
async def test_auto_resolve_actually_releases_an_orphaned_lock():
    """Defect 1. The 4-argument call raised `TypeError` into a bare `except`."""
    lock = SingleTradeLockManager()
    lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD")
    recon = DeltaReconciliationService(
        client=_recon_client(), state_store=_store(), single_trade_lock=lock,
    )
    report = await recon.reconcile_account(ACCOUNT, user_id=USER, auto_resolve=True)
    assert lock.is_locked(USER, ACCOUNT)[0] is False
    assert f"RELEASED_ORPHANED_LOCK_{SETUP}" in report.actions_taken


@pytest.mark.asyncio
async def test_auto_resolve_keeps_the_lock_while_a_position_is_still_open():
    """Semantics B: the exchange, not the local store, decides when it is over."""
    lock = SingleTradeLockManager()
    lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD")
    store = _store()
    store.account.available_balance = Decimal("1.00")  # force a discrepancy
    recon = DeltaReconciliationService(
        client=_recon_client(positions=[_open_position()]),
        state_store=store,
        single_trade_lock=lock,
    )
    report = await recon.reconcile_account(ACCOUNT, user_id=USER, auto_resolve=True)
    assert lock.is_locked(USER, ACCOUNT)[0] is True
    assert not any(a.startswith("RELEASED_ORPHANED_LOCK") for a in report.actions_taken)


@pytest.mark.asyncio
async def test_the_backend_force_release_names_the_right_account():
    """Defect 2. The arguments were swapped, so `reason` received the account id."""
    lock = SingleTradeLockManager()
    lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD")
    backend = MagicMock(spec=BackendClient)
    backend.force_release_lock = MagicMock(return_value=True)

    recon = DeltaReconciliationService(
        client=_recon_client(), state_store=_store(),
        single_trade_lock=lock, backend_client=backend,
    )
    report = await recon.reconcile_account(ACCOUNT, user_id=USER, auto_resolve=True)

    assert backend.force_release_lock.call_count == 1
    args, kwargs = backend.force_release_lock.call_args
    bound = inspect.signature(BackendClient.force_release_lock).bind(
        backend, *args, **kwargs)
    bound.apply_defaults()
    assert bound.arguments["reason"] == "DELTA_RECONCILED_FLAT"
    assert bound.arguments["account_id"] == ACCOUNT
    assert "BACKEND_PERSISTENCE_FORCE_RELEASED" in report.actions_taken


@pytest.mark.asyncio
async def test_the_backend_lock_is_not_force_released_over_an_open_position():
    """Defect 3. It fired on any discrepancy, e.g. a bare equity mismatch."""
    store = _store()
    store.account.available_balance = Decimal("1.00")
    store.account.total_equity = Decimal("1.00")
    backend = MagicMock(spec=BackendClient)
    backend.force_release_lock = MagicMock(return_value=True)

    recon = DeltaReconciliationService(
        client=_recon_client(positions=[_open_position()]),
        state_store=store,
        backend_client=backend,
    )
    report = await recon.reconcile_account(ACCOUNT, user_id=USER, auto_resolve=True)

    assert report.discrepancies, "an equity mismatch should still be reported"
    backend.force_release_lock.assert_not_called()
    assert "BACKEND_PERSISTENCE_FORCE_RELEASED" not in report.actions_taken


@pytest.mark.asyncio
async def test_reconciliation_reports_a_path_b_position_as_untracked_locally():
    """Answer to "can a Path-B trade be reconciled": yes, as a discrepancy.

    Pointing a reconciler at the account shows the position the exchange holds
    and reports it as missing locally, because Path B wrote no local record. The
    exchange remains the authority; nothing is silently adopted.
    """
    recon = DeltaReconciliationService(
        client=_recon_client(positions=[_open_position()]), state_store=_store(),
    )
    report = await recon.reconcile_account(ACCOUNT, user_id=USER)
    missing = [d for d in report.discrepancies
               if d.discrepancy_type
               == ReconciliationDiscrepancyType.EXCHANGE_POSITION_MISSING_LOCALLY]
    assert [d.resource_id for d in missing] == ["BTCUSD"]
    assert report.is_synchronized is False
    assert report.exchange_positions_count == 1
    assert report.local_positions_count == 0


# ── §F. Restart and recovery, using only mechanisms that already exist ────────


@pytest.mark.asyncio
async def test_a_path_b_lock_survives_a_restart_through_export_and_load_state():
    """`export_state` / `load_state` is the existing cross-restart mechanism."""
    lock = SingleTradeLockManager()
    await _execute(_session(_client(), lock))
    exported = lock.export_state()

    restarted = SingleTradeLockManager()
    assert restarted.is_locked(USER, ACCOUNT)[0] is False
    restarted.load_state(exported)
    locked, setup_id, symbol = restarted.is_locked(USER, ACCOUNT)
    assert (locked, setup_id, symbol) == (True, SETUP, "BTCUSD")


@pytest.mark.asyncio
async def test_order_ids_do_not_survive_a_restart_and_must_come_from_the_exchange():
    """Only the lock is persistable; ids live in the returned result object.

    Recovery of the bracket therefore means re-querying the exchange
    (`get_positions` / `get_open_orders`), which is what reconciliation does.
    """
    lock = SingleTradeLockManager()
    res = await _execute(_session(_client(), lock))
    restarted = SingleTradeLockManager()
    restarted.load_state(lock.export_state())

    state = restarted.export_state()[f"{USER}:{ACCOUNT}"]
    assert state["active_setup_id"] == SETUP
    for field in ("entry_order_id", "sl_order_id", "tp_order_id"):
        assert field not in state
    assert res.entry_order_id is not None  # known only to the caller


@pytest.mark.asyncio
async def test_after_a_restart_reconciliation_can_free_an_account_that_is_flat():
    """The full recovery loop with existing parts only: load_state + reconcile."""
    lock = SingleTradeLockManager()
    await _execute(_session(_client(), lock))

    restarted = SingleTradeLockManager()
    restarted.load_state(lock.export_state())
    recon = DeltaReconciliationService(
        client=_recon_client(), state_store=_store(), single_trade_lock=restarted,
    )
    report = await recon.reconcile_account(ACCOUNT, user_id=USER, auto_resolve=True)
    assert restarted.is_locked(USER, ACCOUNT)[0] is False
    assert f"RELEASED_ORPHANED_LOCK_{SETUP}" in report.actions_taken


def test_stale_local_state_is_reported_rather_than_trusted():
    """A restarted process with old state is flagged, not silently accepted."""
    store = _store()
    store.account.last_synced_at = datetime.now(timezone.utc) - timedelta(hours=2)
    recon = DeltaReconciliationService(client=_recon_client(), state_store=store)
    import asyncio
    report = asyncio.run(recon.reconcile_account(ACCOUNT, user_id=USER))
    kinds = {d.discrepancy_type for d in report.discrepancies}
    assert ReconciliationDiscrepancyType.STALE_LOCAL_STATE in kinds


# ── §G. Path A is untouched ───────────────────────────────────────────────────


def test_same_setup_reacquisition_is_still_idempotent_by_default():
    """Path A relies on the documented replay behaviour; it must not change."""
    lock = SingleTradeLockManager()
    assert lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD") is True
    assert lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD") is True
    assert lock.is_locked(USER, ACCOUNT)[1] == SETUP


def test_a_different_setup_still_raises_for_every_caller():
    lock = SingleTradeLockManager()
    lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD")
    with pytest.raises(SingleTradeLockError, match="ONE active trade"):
        lock.acquire_lock(USER, ACCOUNT, "setup-other", "ETHUSD")


def test_release_and_force_release_keep_their_existing_signatures():
    assert list(inspect.signature(SingleTradeLockManager.force_release).parameters) == [
        "self", "user_id", "account_id"]
    assert list(inspect.signature(SingleTradeLockManager.acquire_lock).parameters)[:5] == [
        "self", "user_id", "account_id", "setup_id", "symbol"]
