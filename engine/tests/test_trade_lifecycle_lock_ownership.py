"""
Single-trade lock ownership across every exit of `execute_trade_setup`.

The ownership model this pins:

  RELEASED  -- the call acquired the lock and then failed strictly before
               anything could exist on the exchange: an unregistered product,
               a gateway rejection, an explicit exchange order rejection, and
               the pre-existing `_create_rejected_record` paths.
  RETAINED  -- an order may be live, or a position may be open or unprotected:
               a successful entry (ownership passes to the active trade until
               `close_position`/reconciliation), a network timeout leaving
               exchange state unknown, and any unexpected error inside the
               submission block. Retaining is the fail-safe direction under
               safety rules #11 and #14 and must not be "fixed" into a release.
  UNTOUCHED -- acquisition failed because another setup owns the lock
               (SINGLE_TRADE_LIMIT_EXCEEDED); that lock is never stolen.

`SingleTradeLockManager.release_lock` is setup-scoped and idempotent, so a
double release is a no-op rather than a way to free somebody else's lock (§D).

Zero network access: the Delta client is a mock throughout.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
import pytest

from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.algo_config import AlgoConfigStore
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaConnectionError,
    DeltaOrderRejectedError,
)
from quantedge.execution.models import (
    ConnectionState,
    DeltaOrderRequest,
    DeltaOrderResponse,
    ExecutionMode,
    OrderStatus,
)
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import (
    CloseReason,
    TradeLifecycleManager,
    TradeLifecycleState,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    RejectionReasonCode,
)
from quantedge.strategy.models import (
    SetupState,
    StrategyDecision,
    StrategyDirection,
)

USER = "usr-lock-owner"
ACCOUNT = "acct-lock-owner"

#: Refused by `get_product_specification`, so refused before an order exists.
UNREGISTERED = ("BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P", "btcusd",
                " BTCUSD ", "BTC-USD", "BTCUSDT", "FOOUSD")


def _store(balance: str = "500.00") -> LocalStateStore:
    store = LocalStateStore()
    store.account.account_id = ACCOUNT
    store.account.user_id = USER
    store.account.total_equity = Decimal(balance)
    store.account.available_balance = Decimal(balance)
    store.account.current_balance = Decimal(balance)
    store.account.last_synced_at = datetime.now(timezone.utc)
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.connection.connection_status = "CONNECTED"
    return store


def _filled_client() -> MagicMock:
    """A client whose entry order fills immediately."""
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "LOCK_TEST_KEY"
    client._api_secret = "LOCK_TEST_SECRET"
    client.connection_state = ConnectionState.CONNECTED

    async def _place(req: DeltaOrderRequest) -> DeltaOrderResponse:
        return DeltaOrderResponse(
            id=910001,
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
            average_fill_price=req.limit_price or Decimal("77000.0"),
            state=OrderStatus.FILLED,
            reduce_only=req.reduce_only,
            created_at=datetime.now(timezone.utc),
        )

    client.place_order = AsyncMock(side_effect=_place)
    client.cancel_order = AsyncMock(return_value=True)
    client.get_positions = AsyncMock(return_value=[])
    client.get_open_orders = AsyncMock(return_value=[])
    return client


def _manager(client: MagicMock, store: LocalStateStore,
             lock: SingleTradeLockManager) -> TradeLifecycleManager:
    return TradeLifecycleManager(
        client=client,
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        sync_service=None,
        algo_config_store=AlgoConfigStore(),
        single_trade_lock=lock,
        capital_allocator=CapitalAllocator(),
        execution_mode=ExecutionMode.LIVE,
    )


def _decision(symbol: str, setup_id: str,
              quantity: Decimal = Decimal("1")) -> StrategyDecision:
    """
    BTCUSD geometry (entry on the 0.5 tick, RR 2.0), reused verbatim for the
    unregistered symbols so the only thing under test is the symbol itself.
    """
    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol=symbol,
        timeframe="15m",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id=setup_id,
        entry=Decimal("77000.0"),
        stop_loss=Decimal("76000.0"),
        take_profit=Decimal("79000.0"),
        risk_reward=Decimal("2.0"),
        quantity=quantity,
    )


@pytest.fixture
def lock() -> SingleTradeLockManager:
    return SingleTradeLockManager()


@pytest.fixture
def store() -> LocalStateStore:
    return _store()


@pytest.fixture
def client() -> MagicMock:
    return _filled_client()


@pytest.fixture
def manager(client, store, lock) -> TradeLifecycleManager:
    return _manager(client, store, lock)


def _held_by(lock: SingleTradeLockManager):
    is_locked, setup_id, symbol = lock.is_locked(USER, ACCOUNT)
    return is_locked, setup_id


# ---------------------------------------------------------------------------
# §A  The successful path keeps owning the lock. Unchanged behaviour.
# ---------------------------------------------------------------------------
class TestAnActiveTradeOwnsTheLock:
    @pytest.mark.asyncio
    async def test_a_successful_entry_retains_the_lock(self, manager, lock,
                                                       client):
        record = await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-ok"), ACCOUNT, USER)
        assert record.state == TradeLifecycleState.PROTECTED_POSITION
        client.place_order.assert_awaited()
        assert _held_by(lock) == (True, "setup-ok")

    @pytest.mark.asyncio
    async def test_a_second_setup_cannot_start_while_the_trade_is_open(
            self, manager, lock):
        await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-ok"), ACCOUNT, USER)
        blocked = await manager.execute_trade_setup(
            _decision("ETHUSD", "setup-second"), ACCOUNT, USER)
        assert blocked.rejection_code == \
            RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value
        # The rejection must not have stolen or freed the owner's lock.
        assert _held_by(lock) == (True, "setup-ok")

    @pytest.mark.asyncio
    async def test_closing_the_position_hands_the_lock_back(self, manager,
                                                            lock):
        await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-ok"), ACCOUNT, USER)
        await manager.close_position(
            "setup-ok", CloseReason.TAKE_PROFIT,
            gross_pnl=Decimal("10.00"), trading_fees=Decimal("1.00"))
        assert _held_by(lock) == (False, None)


# ---------------------------------------------------------------------------
# §B  Failures strictly before submission release the lock.
# ---------------------------------------------------------------------------
class TestAPreSubmissionFailureReleasesTheLock:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", UNREGISTERED)
    async def test_a_failed_product_lookup_releases_the_lock(
            self, manager, lock, client, symbol):
        record = await manager.execute_trade_setup(
            _decision(symbol, "setup-unregistered"), ACCOUNT, USER)
        assert record.state == TradeLifecycleState.ENTRY_REJECTED
        assert record.rejection_code == \
            RejectionReasonCode.UNSUPPORTED_SYMBOL.value
        client.place_order.assert_not_awaited()
        assert _held_by(lock) == (False, None)

    @pytest.mark.asyncio
    async def test_a_failed_product_lookup_no_longer_raises(self, manager):
        """
        `UnknownProductError` used to propagate out of `execute_trade_setup`,
        stranding the lock it had just acquired. The condition is now returned
        the same way every other fail-closed condition is: as a record.
        """
        record = await manager.execute_trade_setup(
            _decision("FOOUSD", "setup-no-raise"), ACCOUNT, USER)
        assert record.rejection_code == \
            RejectionReasonCode.UNSUPPORTED_SYMBOL.value
        assert "FOOUSD" in record.error_message

    @pytest.mark.asyncio
    async def test_a_gateway_rejection_releases_the_lock(self, client, lock):
        """Balance far too small for the requested size: rejected pre-order."""
        manager = _manager(client, _store("2.31"), lock)
        record = await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-too-big", quantity=Decimal("200000")),
            ACCOUNT, USER)
        assert record.state == TradeLifecycleState.ENTRY_REJECTED
        assert record.rejection_code is not None
        client.place_order.assert_not_awaited()
        assert _held_by(lock) == (False, None)

    @pytest.mark.asyncio
    async def test_an_exchange_order_rejection_releases_the_lock(
            self, client, store, lock):
        client.place_order = AsyncMock(
            side_effect=DeltaOrderRejectedError("insufficient margin"))
        manager = _manager(client, store, lock)
        record = await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-exchange-reject"), ACCOUNT, USER)
        assert record.state == TradeLifecycleState.ENTRY_REJECTED
        assert _held_by(lock) == (False, None)


# ---------------------------------------------------------------------------
# §C  The account is not bricked: the next setup can trade.
# ---------------------------------------------------------------------------
class TestTheAccountRecoversAfterAReleasedFailure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_symbol", ("BTCUSD.P", "FOOUSD", "btcusd"))
    async def test_the_next_setup_trades_after_a_product_failure(
            self, manager, lock, client, bad_symbol):
        await manager.execute_trade_setup(
            _decision(bad_symbol, "setup-bad"), ACCOUNT, USER)
        good = await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-good"), ACCOUNT, USER)
        assert good.state == TradeLifecycleState.PROTECTED_POSITION
        assert _held_by(lock) == (True, "setup-good")
        client.place_order.assert_awaited()

    @pytest.mark.asyncio
    async def test_the_next_setup_trades_after_an_exchange_rejection(
            self, client, store, lock):
        rejected_once = {"done": False}
        good_client = _filled_client()

        async def _place(req: DeltaOrderRequest):
            if not rejected_once["done"]:
                rejected_once["done"] = True
                raise DeltaOrderRejectedError("insufficient margin")
            return await good_client.place_order(req)

        client.place_order = AsyncMock(side_effect=_place)
        manager = _manager(client, store, lock)

        first = await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-rejected"), ACCOUNT, USER)
        assert first.state == TradeLifecycleState.ENTRY_REJECTED
        assert _held_by(lock) == (False, None)

        second = await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-retry"), ACCOUNT, USER)
        assert second.state == TradeLifecycleState.PROTECTED_POSITION
        assert _held_by(lock) == (True, "setup-retry")


# ---------------------------------------------------------------------------
# §D  Ambiguous exchange state keeps the lock. Deliberate, do not "fix".
# ---------------------------------------------------------------------------
class TestUnknownExchangeStateRetainsTheLock:
    @pytest.mark.asyncio
    async def test_a_network_timeout_retains_the_lock(self, client, store,
                                                      lock):
        """
        The order may have reached Delta, so a position may exist. Releasing
        would permit a second trade beside a possibly-live one (rules #11/#14).
        """
        client.place_order = AsyncMock(
            side_effect=DeltaConnectionError("read timeout"))
        manager = _manager(client, store, lock)
        record = await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-timeout"), ACCOUNT, USER)
        assert record.state == TradeLifecycleState.RECONCILIATION_REQUIRED
        assert _held_by(lock) == (True, "setup-timeout")

    @pytest.mark.asyncio
    async def test_an_unexpected_error_in_the_submission_block_retains_it(
            self, client, store, lock):
        """
        The generic handler also covers failures after `place_order` returned
        (response parsing, fill handling, bracket placement), so exchange state
        is unknown and the lock is kept.
        """
        client.place_order = AsyncMock(side_effect=RuntimeError("boom"))
        manager = _manager(client, store, lock)
        record = await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-boom"), ACCOUNT, USER)
        assert record.state == TradeLifecycleState.ENTRY_REJECTED
        assert _held_by(lock) == (True, "setup-boom")

    @pytest.mark.asyncio
    async def test_a_protection_failure_retains_the_lock(self, client, store,
                                                         lock):
        """A filled entry whose bracket is refused stays locked (rule #10)."""
        calls = {"n": 0}
        filled = _filled_client()

        async def _place(req: DeltaOrderRequest):
            calls["n"] += 1
            if calls["n"] == 1:
                return await filled.place_order(req)
            raise DeltaOrderRejectedError("bracket refused")

        client.place_order = AsyncMock(side_effect=_place)
        manager = _manager(client, store, lock)
        record = await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-naked"), ACCOUNT, USER)
        assert record.state == TradeLifecycleState.PROTECTION_FAILED
        assert _held_by(lock) == (True, "setup-naked")


# ---------------------------------------------------------------------------
# §E  One release mechanism, and it cannot double-release or steal.
# ---------------------------------------------------------------------------
class TestTheReleaseIsIdempotentAndScoped:
    def test_the_helper_is_idempotent(self, manager, lock):
        lock.acquire_lock(user_id=USER, account_id=ACCOUNT,
                          setup_id="setup-x", symbol="BTCUSD")
        manager._release_setup_lock(USER, ACCOUNT, "setup-x")
        assert _held_by(lock) == (False, None)
        manager._release_setup_lock(USER, ACCOUNT, "setup-x")
        assert _held_by(lock) == (False, None)

    def test_a_release_cannot_free_another_setups_lock(self, manager, lock):
        lock.acquire_lock(user_id=USER, account_id=ACCOUNT,
                          setup_id="setup-owner", symbol="BTCUSD")
        manager._release_setup_lock(USER, ACCOUNT, "setup-intruder")
        assert _held_by(lock) == (True, "setup-owner")

    @pytest.mark.asyncio
    async def test_a_released_failure_calls_release_exactly_once(
            self, client, store):
        real = SingleTradeLockManager()
        spy = MagicMock(wraps=real)
        spy.release_lock = MagicMock(side_effect=real.release_lock)
        manager = _manager(client, store, spy)

        await manager.execute_trade_setup(
            _decision("FOOUSD", "setup-once"), ACCOUNT, USER)
        assert spy.release_lock.call_count == 1
        assert real.is_locked(USER, ACCOUNT)[0] is False

    @pytest.mark.asyncio
    async def test_a_retained_failure_never_calls_release(self, client, store):
        real = SingleTradeLockManager()
        spy = MagicMock(wraps=real)
        spy.release_lock = MagicMock(side_effect=real.release_lock)
        client.place_order = AsyncMock(
            side_effect=DeltaConnectionError("read timeout"))
        manager = _manager(client, store, spy)

        await manager.execute_trade_setup(
            _decision("BTCUSD", "setup-kept"), ACCOUNT, USER)
        spy.release_lock.assert_not_called()

    def test_the_lifecycle_has_exactly_one_release_mechanism(self):
        """
        Structural: `release_lock` is only ever called from
        `_release_setup_lock` (the single mechanism every rejection path uses)
        and from `close_position` (the trade's own end of life). No third,
        competing release site exists.
        """
        import ast
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "src" / "quantedge" / \
            "execution" / "trade_lifecycle.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owners = set()
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "release_lock"):
                    owners.add(func.name)
        assert owners == {"_release_setup_lock", "close_position"}, owners
