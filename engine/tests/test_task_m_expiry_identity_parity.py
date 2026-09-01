"""
Task M §M3 — the expiry path must target the SAME setup the engine is tracking.

`cancel_expired_entries` looks up the trade to cancel by setup id. The strategy
has no expiry event of its own: a 3-candle entry window that closes unfilled is
reported as `INVALIDATED`, so the runtime rebuilds the id from that event. If
that reconstruction differed from the id the adapter published at submission
time -- by one separator, one symbol label, one direction spelling -- the lookup
would silently miss and the real exchange order would stay resting forever while
the local state believed the setup was dead. That is the §M3 defect this file
pins shut, end to end, against the real execution stack with only the exchange
transport mocked.

Nothing here changes strategy behaviour: the window calculation, the
invalidation rules and the 1H closed-candle model are read, never altered.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from quantedge.execution.algo_config import AlgoConfigStore
from quantedge.execution.capital_allocator import CapitalAllocator
from quantedge.execution.market_orchestrator import MarketScannerOrchestrator
from quantedge.execution.models import DeltaOrderResponse, OrderStatus
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleState,
)
from quantedge.execution.validation import OrderValidationGateway
from quantedge.runtime import manual_smc_runtime as RT
from quantedge.strategy.manual_smc.lifecycle import ManualLifecycleEventType

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)

# The synthetic SHORT sequence: bar 0 bullish origin (OB = [low, close] =
# [99000, 99400]), bar 1 BOS (closes strictly below 99000), bar 2 first touch of
# the proximal edge (arms the 99100 resting LIMIT for bars 2..4), bars 3-5 stay
# inside the zone without ever reaching the entry, so the window closes unfilled
# and the OB is INVALIDATED at bar 5.
#
# Prices are realistic BTCUSD levels with a tight order block on purpose: the
# real OrderValidationGateway enforces a 1.5 minimum risk/reward, and Manual SMC
# takes profit at a fixed, authorized 0.60% market move, so only a narrow OB
# clears the gate.
# That gate is production behaviour and is not relaxed here.
S_ORIGIN = (99000.0, 99600.0, 99000.0, 99400.0)
S_BOS = (99300.0, 99350.0, 98500.0, 98600.0)
S_TOUCH = (98750.0, 99000.0, 98600.0, 98700.0)
S_NEAR = (99050.0, 99080.0, 98600.0, 99000.0)

USER = "u"
ACCOUNT = "acc"


class _Stack:
    """The real Path-A execution stack; only the exchange client is a mock."""

    def __init__(self, balance="100000"):
        self.sent = []
        self.cancelled = []
        self.client = MagicMock()
        self.client._api_key = "MOCKED_TEST_KEY_TASK_M"
        self.client._api_secret = "MOCKED_TEST_SECRET_TASK_M"
        self.client.place_order = AsyncMock(side_effect=self._place)
        self.client.cancel_order = AsyncMock(side_effect=self._cancel)
        # The exchange's authoritative answer after the cancel: dead, zero fill.
        self.client.get_order = AsyncMock(side_effect=self._get_order)
        self.client.get_positions = AsyncMock(return_value=[])
        self.client.get_open_orders = AsyncMock(return_value=[])
        self.order_state = OrderStatus.CANCELLED
        self.order_filled = Decimal("0")

        self.store = LocalStateStore(account_id=ACCOUNT)
        self.store.account.user_id = USER
        self.store.account.total_equity = Decimal(balance)
        self.store.account.available_balance = Decimal(balance)
        self.store.account.algo_enabled = True
        self.store.account.kill_switch_active = False
        self.store.account.last_synced_at = datetime.now(timezone.utc)
        self.store.connection.connection_status = "CONNECTED"
        self.algo = AlgoConfigStore()
        self.algo.update_config(
            user_id=USER, account_id=ACCOUNT, algo_enabled=True,
            kill_switch_active=False, risk_per_trade_pct=Decimal("100.00"),
            max_daily_loss_usd=Decimal("50000"))
        self.lock = SingleTradeLockManager()
        self.lifecycle = TradeLifecycleManager(
            client=self.client, validation_gateway=OrderValidationGateway(),
            state_store=self.store, algo_config_store=self.algo,
            single_trade_lock=self.lock, capital_allocator=CapitalAllocator(),
            daily_loss_limit=Decimal("50000"))
        self.orchestrator = MarketScannerOrchestrator(
            lifecycle_manager=self.lifecycle, single_trade_lock=self.lock,
            supported_symbols=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"])

    def _place(self, request):
        self.sent.append(request)
        return DeltaOrderResponse(
            id=len(self.sent), client_order_id=request.client_order_id,
            user_id=1, product_id=request.product_id,
            product_symbol=request.product_symbol, side=request.side,
            order_type=request.order_type, size=request.size,
            unfilled_size=request.size, limit_price=request.limit_price,
            stop_price=request.stop_price, average_fill_price=None,
            state=OrderStatus.OPEN, reduce_only=request.reduce_only,
            created_at=datetime.now(timezone.utc))

    def _cancel(self, order_id, product_id):
        self.cancelled.append((order_id, product_id))
        return {"success": True}

    def _get_order(self, order_id):
        entry = self.sent[0]
        return DeltaOrderResponse(
            id=order_id, client_order_id=entry.client_order_id, user_id=1,
            product_id=entry.product_id, product_symbol=entry.product_symbol,
            side=entry.side, order_type=entry.order_type, size=entry.size,
            unfilled_size=entry.size - self.order_filled,
            limit_price=entry.limit_price, stop_price=entry.stop_price,
            average_fill_price=(entry.limit_price if self.order_filled else None),
            state=self.order_state, reduce_only=entry.reduce_only,
            created_at=datetime.now(timezone.utc))


def _ts(bar: int) -> datetime:
    return BASE + timedelta(hours=bar)


async def _feed(runtime, bar, row):
    return await runtime.process_closed_candle(
        RT.ClosedCandle(symbol="BTCUSD", ts=_ts(bar), open=row[0], high=row[1],
                        low=row[2], close=row[3]),
        user_id=USER, account_id=ACCOUNT)


@pytest.fixture
def wired():
    """A runtime driving the real execution stack for one pair."""
    stack = _Stack()
    runtime = RT.ManualSMCRuntime(
        symbols=["BTCUSD"], timeframe="1h", account_balance=100000.0,
        orchestrator=stack.orchestrator, account_id=ACCOUNT)
    return runtime, stack


async def _resting_entry(runtime, stack):
    """Drive the sequence up to a real resting LIMIT on the mock exchange."""
    await _feed(runtime, 0, S_ORIGIN)
    await _feed(runtime, 1, S_BOS)
    step = await _feed(runtime, 2, S_TOUCH)
    assert step.submitted, "the first touch must submit a resting entry"
    return step.submitted[0]


# ── 1. Identity parity between submission and expiry ──────────────────────────


@pytest.mark.asyncio
async def test_the_expiry_path_rebuilds_the_exact_submitted_setup_id(wired):
    """The id §M3 cancels by is the id §M2 submitted with -- character for
    character, rebuilt from the strategy's own INVALIDATED event."""
    runtime, stack = wired
    submitted_id = await _resting_entry(runtime, stack)

    await _feed(runtime, 3, S_NEAR)
    await _feed(runtime, 4, S_NEAR)
    step = runtime.on_closed_candle(RT.ClosedCandle(
        symbol="BTCUSD", ts=_ts(5), open=S_NEAR[0], high=S_NEAR[1],
        low=S_NEAR[2], close=S_NEAR[3]))

    assert [e.event_type for e in step.evaluation.events] == [
        ManualLifecycleEventType.INVALIDATED]
    assert runtime.expired_setup_ids(step) == (submitted_id,)
    assert runtime.lifecycle_manager.get_active_trade(submitted_id) is not None


@pytest.mark.asyncio
async def test_the_rebuilt_id_carries_the_registry_symbol_and_the_timeframe(
    wired,
):
    """No local `.P` label, no case folding, no invented separator."""
    runtime, stack = wired
    submitted_id = await _resting_entry(runtime, stack)

    assert submitted_id.startswith("BTCUSD_1h_")
    assert ".P" not in submitted_id
    assert submitted_id.split("_")[0] == "BTCUSD"


# ── 2. The expiry actually cancels the real exchange order ────────────────────


@pytest.mark.asyncio
async def test_an_expired_window_cancels_the_real_order_and_frees_the_slot(
    wired,
):
    """§M3 end to end: window closes -> real cancel -> exchange confirms ->
    the local trade is terminal and the portfolio slot is released."""
    runtime, stack = wired
    submitted_id = await _resting_entry(runtime, stack)
    entry_order_id = int(
        runtime.lifecycle_manager.get_active_trade(submitted_id).entry_order_id)

    await _feed(runtime, 3, S_NEAR)
    await _feed(runtime, 4, S_NEAR)
    step = await _feed(runtime, 5, S_NEAR)

    assert [o["outcome"] for o in step.expiries] == ["CANCELLED"]
    assert stack.cancelled and stack.cancelled[0][0] == entry_order_id
    assert runtime.lifecycle_manager.get_active_trade(submitted_id) is None
    assert stack.lock.is_locked(USER, ACCOUNT)[0] is False
    assert runtime.expiries_cancelled == 1


@pytest.mark.asyncio
async def test_an_expiry_that_loses_the_race_to_a_fill_is_reconciled_as_a_fill(
    wired,
):
    """§M3's explicitly required race: the cancel is requested, but the exchange
    reports the entry FILLED. That is NOT a cancellation -- the fill is applied
    and the position gets real exchange-side protection."""
    runtime, stack = wired
    submitted_id = await _resting_entry(runtime, stack)
    record = runtime.lifecycle_manager.get_active_trade(submitted_id)
    stack.order_state = OrderStatus.FILLED
    stack.order_filled = stack.sent[0].size

    await _feed(runtime, 3, S_NEAR)
    await _feed(runtime, 4, S_NEAR)
    step = await _feed(runtime, 5, S_NEAR)

    assert [o["outcome"] for o in step.expiries] == ["FILLED_NOT_CANCELLED"]
    assert [o["cancelled"] for o in step.expiries] == [False]
    assert record.filled_quantity == stack.sent[0].size
    assert record.state == TradeLifecycleState.PROTECTED_POSITION
    assert record.sl_order_id and record.tp_order_id
    # The slot stays held: a real position exists.
    assert stack.lock.is_locked(USER, ACCOUNT)[0] is True
    assert runtime.expiries_cancelled == 0


@pytest.mark.asyncio
async def test_an_unconfirmed_cancel_keeps_the_slot_and_blocks_new_entries(
    wired,
):
    """§M15: the exchange still reports the order OPEN, so nothing may assume it
    is gone -- a stale resting order could still create an untracked position."""
    runtime, stack = wired
    submitted_id = await _resting_entry(runtime, stack)
    stack.order_state = OrderStatus.OPEN

    await _feed(runtime, 3, S_NEAR)
    await _feed(runtime, 4, S_NEAR)
    step = await _feed(runtime, 5, S_NEAR)

    assert [o["outcome"] for o in step.expiries] == ["RECONCILIATION_REQUIRED"]
    assert [o["lock_retained"] for o in step.expiries] == [True]
    assert stack.lock.is_locked(USER, ACCOUNT)[0] is True
    record = runtime.lifecycle_manager.get_active_trade(submitted_id)
    assert record.state == TradeLifecycleState.RECONCILIATION_REQUIRED
    codes = [a["code"] for a in runtime.lifecycle_manager.reconciliation_alerts]
    assert "ENTRY_EXPIRY_CANCEL_UNCONFIRMED" in codes


@pytest.mark.asyncio
async def test_no_expiry_means_no_cancel_is_ever_sent(wired):
    """Only an invalidated setup may trigger a cancel. A live window is not one."""
    runtime, stack = wired
    await _resting_entry(runtime, stack)

    step = await _feed(runtime, 3, S_NEAR)

    assert step.expiries == ()
    assert stack.cancelled == []
    assert runtime.expiries_cancelled == 0


@pytest.mark.asyncio
async def test_the_strategy_still_records_no_trade_for_an_expired_window(wired):
    """§M0/§M9: the execution consequence must not become a strategy outcome.
    An expired window is not a loss: no exit row, no -1R, no fee, no balance
    change -- exactly as the frozen baseline records it."""
    runtime, stack = wired
    balance_before = runtime.strategy.account_balance
    await _resting_entry(runtime, stack)

    await _feed(runtime, 3, S_NEAR)
    await _feed(runtime, 4, S_NEAR)
    await _feed(runtime, 5, S_NEAR)

    assert runtime.strategy.lifecycle.exits == []
    assert runtime.strategy.account_balance == pytest.approx(balance_before)
    assert runtime.strategy.lifecycle.active_trade is None
