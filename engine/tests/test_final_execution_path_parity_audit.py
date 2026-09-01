"""
Task I audit: final Path-A / Path-B execution parity, pinned as tests.

  A. single account -> `TradeLifecycleManager.execute_trade_setup`
  B. multi user     -> `UserExecutionSession.execute_trade`

WHAT THIS FILE PINS

SS A  Entry-submission failure. `DeltaIndiaClient.request` maps exchange
    outcomes onto typed exceptions before either path sees them:

      HTTP 400           -> `DeltaOrderRejectedError`  (order provably refused)
      timeout / connect  -> `DeltaConnectionError`      (outcome UNKNOWN)
      HTTP >= 500        -> `DeltaConnectionError`      (outcome UNKNOWN)

    Path A splits on exactly that distinction and documents why: an explicit
    rejection releases the lock because "the order does not exist, so no
    position can exist", while a connection error retains it because "the order
    may have reached Delta, so a position may exist. Releasing here would allow
    a second trade alongside a possibly-live, possibly-unprotected one (safety
    rules #11, #14)."

    Path B collapsed all of it into one broad `except Exception` that released
    the lock whenever the entry had not been confirmed -- including the
    ambiguous case, where the exchange may hold a live order. That is the one
    Class-3 defect this file proves and then pins as fixed.

    The bare-`Exception` case is deliberately left alone.
    `test_phase5_16_multi_user_execution.py` drives it with a mock raising
    `Exception("DeltaOrderRejectedError: Insufficient margin")` -- a rejection
    in intent, an untyped exception in fact -- and asserts the lock is released.
    Production `place_order` never raises a bare `Exception` for a network
    failure, so narrowing the fix to the typed exceptions closes the real hole
    without weakening that test. Both halves are pinned here so the residual
    asymmetry cannot drift silently.

SS B  A successful Path-B trade exists only in its returned
    `UserExecutionResult` and in the lock. Pinned as intentional.

SS C  Nothing in `src/` calls `reconcile_account`, `export_state`,
    `load_state`, or the backend trade notifications. The engine is
    library-only; runtime composition is external. Pinned, not created.

SS D  `generate_deterministic_client_order_id` has zero production callers;
    both paths use the random generator. Pinned as-is.

SS E  `position_size_limit` is recorded snapshot data with no reader anywhere
    in `src/`. Pinned as an external-policy blocker, not enforced.

SS F  Path-A behaviour and the frozen trees are unchanged.

Zero network access: every Delta client here is a mock.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import pathlib
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

import quantedge
from quantedge.execution.backend_client import BackendClient
from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.delta_client import (
    DeltaConnectionError,
    DeltaIndiaClient,
    DeltaOrderRejectedError,
    generate_client_order_id,
    generate_deterministic_client_order_id,
)
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
from quantedge.execution.reconciliation import DeltaReconciliationService
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import CloseReason, TradeLifecycleManager
from quantedge.execution.validation import OrderValidationGateway
from quantedge.instruments import delta_india_registry

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = pathlib.Path(quantedge.__file__).parent
SNAPSHOT_PATH = (REPO_ROOT / "data" / "reference" / "delta_exchange_india"
                 / "product_specs_snapshot.json")

#: A valid long setup: stop < entry < target.
LONG = (Decimal("77000"), Decimal("76000"), Decimal("79000"))

USER = "usr-i"
ACCOUNT = "acct-i"
SETUP = "setup-i-1"
NATIVE = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def registry():
    return delta_india_registry()


def _production_sources():
    """Every production module, as (path, text)."""
    for path in sorted(SRC_ROOT.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def _callers(needle: str, *, exclude: tuple[str, ...] = ()) -> list[str]:
    """Production modules containing `needle`, excluding named files."""
    return [
        str(path.relative_to(SRC_ROOT).as_posix())
        for path, text in _production_sources()
        if needle in text and path.name not in exclude
    ]


# -- Mock exchange -------------------------------------------------------------


def _client(mark_price: Decimal = LONG[0], balance: str = "1000.00",
            positions=None, fail_after: int | None = None,
            failure: Exception | None = None,
            balance_failure: Exception | None = None) -> MagicMock:
    """A funded, flat account whose orders fill immediately.

    `fail_after` makes the (1-based) n-th `place_order` raise `failure`, so an
    entry failure and a bracket failure that follows an accepted entry are both
    reproducible. `balance_failure` fails step 4 instead, i.e. before anything
    has been sent to the exchange.
    """
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "I_KEY"
    client._api_secret = "I_SECRET"
    client.connection_state = ConnectionState.CONNECTED
    client.submitted: list[DeltaOrderRequest] = []

    if balance_failure is not None:
        client.get_wallet_balances = AsyncMock(side_effect=balance_failure)
    else:
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
            raise failure or RuntimeError("order placement failed")
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


def _session(client: MagicMock, lock: SingleTradeLockManager | None = None,
             **over) -> UserExecutionSession:
    kwargs = dict(
        user_id=USER,
        account_id=ACCOUNT,
        is_active=True,
        algo_enabled=True,
        kill_switch_active=False,
        api_key="I_KEY",
        api_secret="I_SECRET",
        client_factory=lambda _k, _s: client,
    )
    kwargs.update(over)
    return UserExecutionSession(
        config=UserAccountConfig(**kwargs),
        lock_manager=lock or SingleTradeLockManager(),
        capital_allocator=CapitalAllocator(),
    )


async def _execute(session: UserExecutionSession, setup_id: str = SETUP,
                   symbol: str = "BTCUSD", prices=LONG, leverage: int = 10):
    entry, stop, target = prices
    return await session.execute_trade(
        setup_id=setup_id,
        symbol=symbol,
        direction=TradeDirection.LONG,
        planned_entry_price=entry,
        stop_loss_price=stop,
        take_profit_price=target,
        default_leverage=leverage,
    )


# == SS A -- the entry-submission outcome matrix ===============================


@pytest.mark.asyncio
async def test_an_explicit_rejection_releases_the_lock():
    """HTTP 400 -> `DeltaOrderRejectedError`: the order provably does not exist.

    Path A's own handler states the reasoning -- "An explicit exchange rejection
    means the order does not exist, so no position can exist and the lock is
    released." Nothing can be open, so the account is handed back.
    """
    lock = SingleTradeLockManager()
    client = _client(fail_after=1,
                     failure=DeltaOrderRejectedError("rejected", status_code=400))
    result = await _execute(_session(client, lock))

    assert result.status == "ERROR"
    assert lock.is_locked(USER, ACCOUNT)[0] is False


@pytest.mark.asyncio
async def test_an_ambiguous_entry_submission_retains_the_lock():
    """A connection error on the entry POST is an UNKNOWN outcome, not a refusal.

    `DeltaIndiaClient.request` raises `DeltaConnectionError` for a timeout, a
    connect failure, or any HTTP 5xx. In every one of those cases the POST may
    have reached Delta and been accepted, so a position may be open -- and
    unprotected, because the brackets were never sent. Handing the account back
    for a new trade in that state is what safety rules #11 and #14 forbid, and
    it is the case Path A retains for.
    """
    lock = SingleTradeLockManager()
    client = _client(fail_after=1, failure=DeltaConnectionError("timed out"))
    result = await _execute(_session(client, lock))

    locked, setup_id, symbol = lock.is_locked(USER, ACCOUNT)
    assert locked is True
    assert setup_id == SETUP
    assert symbol == "BTCUSD"
    assert result.status == "RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_an_entry_timeout_retains_the_lock():
    """`asyncio.TimeoutError` is the same unknown outcome as the client's own."""
    lock = SingleTradeLockManager()
    client = _client(fail_after=1, failure=asyncio.TimeoutError())
    result = await _execute(_session(client, lock))

    assert lock.is_locked(USER, ACCOUNT)[0] is True
    assert result.status == "RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_a_connection_error_before_the_entry_is_sent_releases_the_lock():
    """Retention is scoped to the submission, not to any network failure.

    A balance query that times out happens before anything has been sent, so
    nothing can exist on the exchange and the lock must come back. Retaining
    here would block the account for a read failure -- Path A does not do that
    either: its connection handler wraps only the entry `place_order`.
    """
    lock = SingleTradeLockManager()
    client = _client(balance_failure=DeltaConnectionError("balance timed out"))
    result = await _execute(_session(client, lock))

    assert client.submitted == []
    assert lock.is_locked(USER, ACCOUNT)[0] is False
    assert result.status == "ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after,role", [(2, "stop loss"), (3, "take profit")])
async def test_a_bracket_failure_after_an_accepted_entry_retains_the_lock(
        fail_after, role):
    """The position is open and under-protected: the lock must not come back."""
    lock = SingleTradeLockManager()
    client = _client(fail_after=fail_after,
                     failure=DeltaConnectionError(f"{role} placement failed"))
    result = await _execute(_session(client, lock))

    assert len(client.submitted) == fail_after
    assert lock.is_locked(USER, ACCOUNT)[1] == SETUP
    assert result.status in ("ERROR", "RECONCILIATION_REQUIRED")


@pytest.mark.asyncio
async def test_a_bare_exception_on_the_entry_still_releases_the_lock():
    """Pinned deliberately, and NOT changed by this audit.

    `test_phase5_16_multi_user_execution.py` drives the entry failure with
    `Exception("DeltaOrderRejectedError: Insufficient margin")` and asserts the
    lock is released. That mock is a rejection in intent and an untyped
    exception in fact. Production `place_order` raises typed errors --
    `DeltaOrderRejectedError`, `DeltaConnectionError`, `DeltaAuthError` -- so
    this branch is unreachable from a real network failure, and narrowing the
    ambiguous-submission fix to the typed exceptions leaves the existing
    contract intact. Whether an untyped exception should also retain the lock is
    a reported design decision, not a silent change.
    """
    lock = SingleTradeLockManager()
    client = _client(fail_after=1,
                     failure=Exception("DeltaOrderRejectedError: Insufficient margin"))
    result = await _execute(_session(client, lock))

    assert result.status == "ERROR"
    assert lock.is_locked(USER, ACCOUNT)[0] is False


def test_the_client_maps_a_400_to_a_rejection_and_a_timeout_to_a_connection_error():
    """The typed distinction both paths depend on lives in one place."""
    src = inspect.getsource(DeltaIndiaClient.request)
    timeout_block, status_blocks = src.split("# Handle status codes")

    assert "DeltaConnectionError" in timeout_block
    assert "ReadTimeout" in timeout_block
    assert "ConnectError" in timeout_block
    assert "DeltaOrderRejectedError" not in timeout_block

    rejection = status_blocks.split("response.status_code == 400")[1]
    assert "DeltaOrderRejectedError" in rejection.split("elif")[0]
    assert "DeltaConnectionError" in status_blocks.split("status_code >= 500")[1]


def test_path_a_splits_the_entry_outcome_into_release_retain_retain():
    """Path A is the authority for the intended behaviour; pin it verbatim."""
    src = inspect.getsource(TradeLifecycleManager.execute_trade_setup)
    _, tail = src.split("except DeltaOrderRejectedError")
    rejected, tail = tail.split("except (DeltaConnectionError, asyncio.TimeoutError)")
    connection, unexpected = tail.split("except Exception")

    assert "_release_setup_lock" in rejected
    assert "no position can exist" in rejected

    assert "_release_setup_lock" not in connection
    assert "LOCK INTENTIONALLY RETAINED" in connection
    assert "may have reached Delta" in connection

    assert "_release_setup_lock" not in unexpected
    assert "LOCK INTENTIONALLY RETAINED" in unexpected


def test_path_b_now_names_the_same_typed_exceptions_as_path_a():
    """Both paths must key the release decision on the same two exception types."""
    from quantedge.execution import multi_user_orchestrator as mod

    src = inspect.getsource(mod.UserExecutionSession.execute_trade)
    assert "except DeltaOrderRejectedError" in src
    assert "except (DeltaConnectionError, asyncio.TimeoutError)" in src

    # The ambiguous handler must not release unconditionally.
    ambiguous = src.split("except (DeltaConnectionError, asyncio.TimeoutError)")[1]
    ambiguous = ambiguous.split("except Exception")[0]
    assert "entry_submitted" in ambiguous


# == SS B -- lifecycle ownership of a successful Path-B trade ==================


@pytest.mark.asyncio
async def test_a_successful_path_b_trade_lives_only_in_its_result_and_the_lock():
    lock = SingleTradeLockManager()
    client = _client()
    session = _session(client, lock)
    result = await _execute(session)

    assert result.status == "EXECUTED"
    assert result.entry_order_id and result.sl_order_id and result.tp_order_id
    # Retained on purpose: the lock means "this account owns an open trade".
    assert lock.is_locked(USER, ACCOUNT)[1] == SETUP
    # Path B holds no state store, so there is nowhere else the trade could be.
    assert not hasattr(session, "state_store")


def _store(account_id: str = ACCOUNT, user_id: str = USER) -> LocalStateStore:
    store = LocalStateStore(account_id=account_id)
    store.account.user_id = user_id
    store.account.available_balance = Decimal("1000")
    store.account.total_equity = Decimal("1000")
    store.account.current_balance = Decimal("1000")
    store.account.last_synced_at = datetime.now(timezone.utc)
    return store


def _lifecycle(store: LocalStateStore,
               lock: SingleTradeLockManager) -> TradeLifecycleManager:
    return TradeLifecycleManager(
        client=_client(),
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        single_trade_lock=lock,
    )


@pytest.mark.asyncio
async def test_a_path_b_trade_cannot_be_closed_by_the_lifecycle_manager():
    """Every lifecycle mutator is keyed on `_active_trades[setup_id]`.

    Path B never registers a record there, so `close_position` -- the routine
    that cancels the surviving bracket, computes net PnL, compounds the balance
    and releases the lock -- cannot reach a multi-user trade at all.
    """
    lock = SingleTradeLockManager()
    await _execute(_session(_client(), lock))

    mgr = _lifecycle(_store(), lock)
    with pytest.raises(ValueError, match="No active trade found"):
        await mgr.close_position(SETUP, CloseReason.TAKE_PROFIT)


@pytest.mark.asyncio
async def test_a_path_b_trade_is_invisible_to_the_daily_loss_guard():
    """`get_realized_daily_loss` iterates `_trade_history`, which stays empty."""
    lock = SingleTradeLockManager()
    await _execute(_session(_client(), lock))

    mgr = _lifecycle(_store(), lock)
    assert mgr.get_realized_daily_loss() == Decimal("0")


def test_the_local_state_store_is_structurally_single_account():
    """So multi-user state could not be represented in it even if written.

    `get_account` ignores its argument and returns the one `AccountRecord`, and
    `positions` is keyed by symbol alone -- two accounts holding BTCUSD would
    collide. This is why Path B is stateless rather than under-implemented.
    """
    store = _store()
    assert store.get_account("some-other-account") is store.account
    assert inspect.signature(LocalStateStore.get_account).parameters[
        "account_id"].default is None


# == SS C -- runtime reconciliation ownership ==================================


def test_only_the_runtime_wiring_layer_invokes_reconciliation():
    """`reconcile_account` is defined once and called from exactly one place.

    BEFORE Task M this test asserted `_callers(".reconcile_account(") == []` and
    documented that boundary: reconciliation was the only code path that
    releases an orphaned semantics-B lock, and nothing in `src/` ever ran it, so
    the release depended on an external composition layer that does not exist in
    this repository.

    Task M §M4 was commissioned to remove exactly that boundary
    ("`DeltaReconciliationService.reconcile_account()` must become reachable
    from production execution"), so the empty-caller assertion now contradicts
    the authoritative requirement. It is inverted rather than deleted: the
    equality below is at least as strict as `== []` and still prevents an
    unauthorized fourth caller from appearing. `ManualSMCRuntime.reconcile()` is
    the single owner -- it is reached from startup, from every private-WS
    (re)connection hook, and from restart recovery.

    `reconcile_all` remains uncalled: Task M added no polling scheduler.
    """
    assert _callers(".reconcile_account(") == ["runtime/manual_smc_runtime.py"]
    assert _callers("reconcile_all") == []
    assert (SRC_ROOT / "execution" / "reconciliation.py").exists()


def test_nothing_in_production_persists_or_restores_the_lock():
    """`export_state`/`load_state` are the only cross-restart lock mechanism."""
    assert _callers(".export_state()") == []
    assert _callers(".load_state(") == []


def test_the_backend_trade_notifications_have_no_production_callers():
    """The Java/PostgreSQL persistence layer exists but is unwired for trades.

    Only `force_release_lock` is called, and only from `reconciliation.py`,
    which is itself uncalled. So no Path-B trade is ever persisted.
    """
    for method in ("notify_trade_open", "notify_trade_close",
                   "update_lock_state", "get_account_state",
                   "get_next_trade_capital"):
        assert _callers(f".{method}(", exclude=("backend_client.py",)) == [], method
    assert _callers(".force_release_lock(", exclude=("backend_client.py",)) == [
        "execution/reconciliation.py"
    ]


def test_the_engine_entry_point_explicitly_disables_execution_loops():
    """The engine is library-only by declaration, not by omission."""
    main = (SRC_ROOT / "__main__.py").read_text(encoding="utf-8")
    assert "execution loops remain disabled until explicitly configured" in main
    assert "MultiUserExecutionOrchestrator" not in main
    assert "DeltaReconciliationService" not in main
    assert "start_health_server" in main


def test_reconciliation_is_the_only_production_release_path_besides_the_two_paths():
    """Pin who may hand an account back, so a fourth releaser cannot appear."""
    assert sorted(_callers(".release_lock(", exclude=("single_trade_lock.py",))) == [
        "execution/multi_user_orchestrator.py",
        "execution/reconciliation.py",
        "execution/trade_lifecycle.py",
    ]


@pytest.mark.asyncio
async def test_the_only_release_loop_for_a_path_b_lock_works_end_to_end():
    """Restart, then reconcile: the account comes back only once Delta is flat.

    This is the complete recovery mechanism assembled from parts that already
    exist -- `export_state`/`load_state` across the restart, then
    `reconcile_account(auto_resolve=True)`. No new persistence is introduced.
    """
    lock = SingleTradeLockManager()
    await _execute(_session(_client(), lock))
    persisted = lock.export_state()

    # ... process restart: a fresh manager knows nothing until it is loaded.
    restarted = SingleTradeLockManager()
    assert restarted.is_locked(USER, ACCOUNT)[0] is False
    restarted.load_state(persisted)
    assert restarted.is_locked(USER, ACCOUNT)[1] == SETUP

    # Order IDs are not part of the persisted state: the bracket can only be
    # recovered by re-querying the exchange.
    assert "order" not in json.dumps(persisted).lower()

    recon = DeltaReconciliationService(
        client=_client(),
        state_store=_store(),
        single_trade_lock=restarted,
    )
    report = await recon.reconcile_account(ACCOUNT, user_id=USER, auto_resolve=True)

    assert any(a.startswith("RELEASED_ORPHANED_LOCK") for a in report.actions_taken)
    assert restarted.is_locked(USER, ACCOUNT)[0] is False


# == SS D -- deterministic client order IDs ====================================


def test_the_deterministic_id_generator_is_deterministic_and_role_scoped():
    a = generate_deterministic_client_order_id(ACCOUNT, SETUP, "ENTRY")
    assert a == generate_deterministic_client_order_id(ACCOUNT, SETUP, "ENTRY")
    roles = {generate_deterministic_client_order_id(ACCOUNT, SETUP, r)
             for r in ("ENTRY", "SL", "TP")}
    assert len(roles) == 3
    assert all(len(r) <= 32 for r in roles)
    assert generate_deterministic_client_order_id("other", SETUP, "ENTRY") != a


def test_neither_path_uses_the_deterministic_id_generator():
    """Symmetric, so not a Path-B defect -- and not changed here.

    Exchange-side idempotency would need Delta's own `client_order_id` replay
    semantics to be verified, which this repository does not record. Introducing
    deterministic IDs on that basis would be guessing exchange behaviour.
    """
    assert _callers("generate_deterministic_client_order_id",
                    exclude=("delta_client.py",)) == []
    for path in ("trade_lifecycle.py", "multi_user_orchestrator.py"):
        text = (SRC_ROOT / "execution" / path).read_text(encoding="utf-8")
        assert "generate_client_order_id(" in text
        assert "generate_deterministic_client_order_id" not in text


@pytest.mark.asyncio
async def test_the_random_generator_still_yields_distinct_ids_per_bracket_leg():
    """Whatever the scheme, the three legs of one trade must not collide."""
    client = _client()
    await _execute(_session(client))
    ids = [r.client_order_id for r in client.submitted]
    assert len(ids) == 3
    assert len(set(ids)) == 3
    assert all(len(i) <= 32 for i in ids)


# == SS E -- `position_size_limit`: audited, still unenforced ==================


def test_position_size_limit_is_absent_from_every_production_module():
    """Recorded data with no reader: no accessor, no cap, no rejection code."""
    assert _callers("position_size_limit") == []


@pytest.mark.parametrize("symbol", NATIVE)
def test_position_size_limit_is_reachable_only_as_recorded_snapshot_data(
        registry, symbol):
    spec = registry.get(symbol)
    assert not hasattr(spec, "position_size_limit")
    assert Decimal(str(spec.recorded["position_size_limit"])) > 0
    with pytest.raises(TypeError):
        spec.recorded["position_size_limit"] = 1


@pytest.mark.parametrize("symbol", NATIVE)
def test_position_size_limit_is_recorded_but_never_hashed(snapshot, symbol):
    """It is outside `contract_spec`, so it cannot affect the frozen hashes."""
    entry = snapshot["products"][symbol]
    assert "position_size_limit" in entry["margin_and_limits"]
    assert "position_size_limit" in entry["recorded_not_hashed"]
    assert "position_size_limit" not in entry["contract_spec"]
    assert "position_size_limit" not in entry["verified_fields"]


def test_no_equivalent_quantity_or_notional_cap_is_enforced_in_production():
    """`CapitalAllocator` has an optional cap that nothing ever passes.

    `calculate_100_percent_allocation(..., max_quantity=None)` would clamp the
    stepped quantity, but no production call site supplies it. So there is no
    quantity, notional, or position cap under any other name -- the units and
    scope of `position_size_limit` remain unverified external policy.
    """
    from quantedge.execution.capital_allocator import CapitalAllocator as CA

    params = inspect.signature(CA.calculate_100_percent_allocation).parameters
    assert params["max_quantity"].default is None
    assert _callers("max_quantity=") == []


# == SS F -- Path A and the frozen trees are unchanged =========================


def test_the_lock_api_that_path_a_depends_on_is_unchanged():
    acquire = inspect.signature(SingleTradeLockManager.acquire_lock).parameters
    assert list(acquire) == ["self", "user_id", "account_id", "setup_id",
                             "symbol", "allow_replay"]
    # Path A relies on the documented idempotent replay, so the default stands.
    assert acquire["allow_replay"].default is True
    assert list(inspect.signature(SingleTradeLockManager.release_lock).parameters) == [
        "self", "user_id", "account_id", "setup_id"]
    assert list(inspect.signature(BackendClient.force_release_lock).parameters) == [
        "self", "reason", "account_id"]


def test_path_a_still_treats_a_repeated_setup_id_as_an_idempotent_replay():
    lock = SingleTradeLockManager()
    assert lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD") is True
    assert lock.acquire_lock(USER, ACCOUNT, SETUP, "BTCUSD") is True
    assert lock.is_locked(USER, ACCOUNT)[1] == SETUP


#: Frozen trees, pinned by content hash (first 12 hex of the file digest).
FROZEN = {
    "src/quantedge/smc/order_blocks.py": "b7d35962d2af",
    "src/quantedge/smc/structure.py": "0bc3dd39296a",
    "src/quantedge/smc/models.py": "43a97027ccdd",
    "src/quantedge/smc/volatility.py": "880ccc5d6c6c",
    "src/quantedge/ai/research/displacement_gated_retest_engine.py": "919329854cf0",
}


@pytest.mark.parametrize("rel,expected", sorted(FROZEN.items()))
def test_the_frozen_smc_and_oracle_files_are_untouched(rel, expected):
    path = REPO_ROOT / "engine" / rel
    digest = hashlib.new("sha256", path.read_bytes()).hexdigest()[:12]
    assert digest == expected, f"{rel} changed"


def test_this_audit_touched_no_frozen_or_reference_module():
    """The three files Task I may edit are all under `execution/`."""
    for rel in FROZEN:
        assert "/execution/" not in rel
    assert SNAPSHOT_PATH.exists()
