"""
Multi-User Live Execution Orchestrator & Isolated User Session Engine (Phase 5.16).

Architectural Principles:
1. Shared SMC / Strategy Logic:
   - Shared deterministic Order Block detection, volatility calculations, and signal qualification.
2. Per-User Execution Isolation:
   - Dedicated Delta Exchange client instance per user.
   - Independent user balance queries (NO hardcoded balances).
   - Independent SingleTradeLock per account (lock-first sequencing).
   - User A failure (authentication, insufficient margin, lock contention) never impacts User B.
3. Lock-First Ordering:
   - Acquire SingleTradeLock before making network balance queries or calculating sizing to eliminate race conditions.
4. Fail-Closed Safety:
   - Inactive, kill-switched, or disabled accounts are skipped safely.
   - Reduce-only SL and TP brackets placed immediately upon entry fill.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Dict, List, Optional, Any

from quantedge.instruments import delta_india_registry
from quantedge.execution.models import (
    DeltaOrderRequest,
    DeltaOrderResponse,
    OrderSide,
    OrderType,
    OrderStatus,
    StopOrderType,
    StopTriggerMethod,
    TimeInForce,
    PositionSide,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaClientError,
    DeltaAuthError,
    DeltaConnectionError,
    DeltaOrderRejectedError,
    generate_client_order_id,
)
from quantedge.execution.single_trade_lock import (
    SingleTradeLockManager,
    SingleTradeLockError,
)
from quantedge.execution.capital_allocator import (
    CapitalAllocator,
    CapitalAllocationError,
    PositionSizingResult,
)
from quantedge.execution.security import mask_secret
from quantedge.strategy.models import StrategyDecision, TradeDirection

logger = logging.getLogger("multi_user_orchestrator")


@dataclass
class UserAccountConfig:
    """Configuration and credentials for an authenticated user trading account."""
    user_id: str
    account_id: str
    is_active: bool = True
    algo_enabled: bool = False
    kill_switch_active: bool = True
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    leverage_override: Optional[int] = None
    custom_safety_buffer_pct: Optional[Decimal] = None
    client_factory: Optional[Any] = None  # Allows injecting mock client in tests


@dataclass
class UserExecutionResult:
    """Outcome of live execution attempt for a specific user."""
    user_id: str
    account_id: str
    setup_id: str
    symbol: str
    status: str  # EXECUTED, SKIPPED_INACTIVE, SKIPPED_ALGO_DISABLED, SKIPPED_KILL_SWITCH, BLOCKED_LOCK, BLOCKED_MARGIN, ERROR
    entry_order_id: Optional[str] = None
    entry_fill_price: Optional[Decimal] = None
    allocated_quantity: Optional[Decimal] = None
    notional_value: Optional[Decimal] = None
    sl_order_id: Optional[str] = None
    sl_price: Optional[Decimal] = None
    tp_order_id: Optional[str] = None
    tp_price: Optional[Decimal] = None
    live_balance_queried: Optional[Decimal] = None
    error: Optional[str] = None
    executed_at: Optional[datetime] = None


@dataclass
class MultiUserDispatchSummary:
    """Summary of a multi-user strategy signal execution dispatch."""
    setup_id: str
    symbol: str
    total_accounts: int
    executed_count: int
    skipped_count: int
    error_count: int
    user_results: Dict[str, UserExecutionResult] = field(default_factory=dict)


def _setup_geometry_error(
    direction: Any,
    entry_price: Any,
    stop_loss_price: Any,
    take_profit_price: Any,
) -> Optional[str]:
    """Return why a setup's own three prices are unusable, or None if usable.

    The other two production order paths both refuse a directionally
    inconsistent setup before submitting anything:
    `TradeLifecycleManager.execute_trade_setup` rejects it as
    INVALID_TP_SL_GEOMETRY, and `OrderValidationGateway.validate` enforces
    `entry > stop and target > entry` for a long plus the mirror for a short,
    alongside a non-positive-price refusal. This path builds its own
    `DeltaOrderRequest` objects and never consults that gateway, so the same
    invariant is enforced here.

    This is not exchange policy and no value is invented: it is the internal
    consistency of the caller's own numbers, and it is the invariant the rest
    of the repository already treats as authoritative. Without it the
    reduce-only brackets are submitted from these prices unchecked, so a long
    whose "take profit" sits below the market places a reduce-only sell limit
    that fills immediately at a loss, and a "stop loss" above the market
    triggers at once -- the mirror image of the protection they are meant to be.

    A direction that is neither long nor short is refused rather than mapped:
    the side is otherwise chosen by comparing against long alone, so any other
    value silently becomes a sell.
    """
    if direction not in (TradeDirection.LONG, TradeDirection.SHORT):
        return (f"Trade direction {direction!r} is neither "
                f"{TradeDirection.LONG.value} nor {TradeDirection.SHORT.value}; "
                f"refusing to infer an order side")

    try:
        if min(entry_price, stop_loss_price, take_profit_price) <= Decimal("0"):
            return (f"Setup prices must all be positive: entry={entry_price}, "
                    f"stop={stop_loss_price}, target={take_profit_price}")

        if direction == TradeDirection.LONG:
            ordered = stop_loss_price < entry_price < take_profit_price
            shape = "stop < entry < target"
        else:
            ordered = take_profit_price < entry_price < stop_loss_price
            shape = "target < entry < stop"
    except TypeError as exc:
        return (f"Setup prices are not mutually comparable "
                f"({exc}); refusing to submit an order")

    if not ordered:
        return (f"Invalid {direction.value} setup geometry: require {shape}, "
                f"got entry={entry_price}, stop={stop_loss_price}, "
                f"target={take_profit_price}")
    return None


class UserExecutionSession:
    """Isolated execution session for a single user account."""

    def __init__(
        self,
        config: UserAccountConfig,
        lock_manager: SingleTradeLockManager,
        capital_allocator: CapitalAllocator,
    ):
        self.config = config
        self.lock_manager = lock_manager
        self.capital_allocator = capital_allocator

    async def execute_trade(
        self,
        setup_id: str,
        symbol: str,
        direction: TradeDirection,
        planned_entry_price: Decimal,
        stop_loss_price: Decimal,
        take_profit_price: Decimal,
        default_leverage: int = 10,
    ) -> UserExecutionResult:
        """Execute a qualified trade setup for this specific user with lock-first ordering."""
        # 1. Eligibility Checks
        if not self.config.is_active:
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="SKIPPED_INACTIVE",
                error="Account is not marked active in backend",
            )

        if not self.config.algo_enabled:
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="SKIPPED_ALGO_DISABLED",
                error="Algo trading is disabled for this user",
            )

        if self.config.kill_switch_active:
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="SKIPPED_KILL_SWITCH",
                error="Account kill switch is active",
            )

        if not self.config.api_key or not self.config.api_secret:
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="ERROR",
                error="Delta Exchange API credentials missing or unconfigured",
            )

        # 1e. Setup Geometry (the invariant both other order paths enforce)
        # Checked before the lock: nothing external has happened yet, so an
        # unusable setup takes no lock and strands none.
        geometry_error = _setup_geometry_error(
            direction, planned_entry_price, stop_loss_price, take_profit_price)
        if geometry_error:
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="ERROR",
                error=geometry_error,
            )

        # 2. LOCK-FIRST ORDERING: Acquire SingleTradeLock before any external network calls
        #
        # `allow_replay=False`: on this path the lock acquisition *is* the
        # duplicate-signal gate. There is no local record of dispatched setups
        # here, and step 5 below can only see exposure the exchange has already
        # turned into a position -- a resting, unfilled entry order is invisible
        # to it. So a re-arriving setup_id that this account already holds must
        # be refused rather than waved through, or the same setup places a second
        # entry and a second bracket.
        try:
            self.lock_manager.acquire_lock(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                allow_replay=False,
            )
        except SingleTradeLockError as e:
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="BLOCKED_LOCK",
                error=str(e),
            )

        client: Optional[DeltaIndiaClient] = None

        # Lock ownership for the rest of this call.
        #
        # The lock means "this account currently owns an open trade" (see
        # `single_trade_lock`: released ONLY when the trade is confirmed
        # POSITION_CLOSED by the exchange). Once the entry order has been
        # accepted, a position may be open -- and if a bracket order then fails,
        # it may be open *unprotected*. Handing the account back for a new trade
        # in that state is exactly what the retention rules (#11, #14) forbid, so
        # the lock is released on failure only while nothing can exist on the
        # exchange yet. Beyond that point it is retained, and released by
        # reconciliation once the exchange is confirmed flat -- the same division
        # of labour the single-account path documents in `_release_setup_lock`.
        entry_accepted = False

        # Whether the entry POST has been *attempted*. `entry_accepted` says a
        # response came back; this says one may never come back. A
        # `DeltaConnectionError` raised from the submission is an UNKNOWN
        # outcome, not a refusal -- `DeltaIndiaClient.request` raises it for a
        # timeout, a connect failure, and every HTTP 5xx, in all of which the
        # order may already be resting on Delta. Before the attempt, the same
        # exception means only that a read failed and nothing was sent.
        entry_submitted = False

        def _release_lock_if_nothing_can_exist() -> None:
            if entry_accepted:
                return
            self.lock_manager.release_lock(
                self.config.user_id, self.config.account_id, setup_id
            )

        try:
            # 3. Initialize Isolated Per-User Delta Client
            if self.config.client_factory:
                client = self.config.client_factory(self.config.api_key, self.config.api_secret)
            else:
                client = DeltaIndiaClient(
                    api_key=self.config.api_key,
                    api_secret=self.config.api_secret,
                )

            # 4. Authoritative Live Balance Query (Dynamic, NEVER hardcoded)
            balances = await client.get_wallet_balances()
            collateral_bal = next((b for b in balances if b.asset_symbol in ("USD", "USDT")), None)
            if not collateral_bal or collateral_bal.available_balance <= Decimal("0"):
                avail_str = str(collateral_bal.available_balance) if collateral_bal else "0"
                raise CapitalAllocationError(f"Insufficient available balance on exchange: {avail_str} USDT")

            available_margin = collateral_bal.available_balance

            # 5. Verify Zero Existing Exposure on Exchange
            positions = await client.get_positions()
            active_pos = [p for p in positions if p.size > Decimal("0")]
            if active_pos:
                raise SingleTradeLockError(f"User account currently has {len(active_pos)} open positions on exchange")

            # 6. Verified Product Identity (registry) & Live Ticker
            # Product identity and its verified metadata come from the pinned
            # instrument registry -- never from a live or mocked product
            # catalogue payload. A payload that disagrees with the snapshot must
            # not be able to send an order for one symbol under another symbol's
            # product id. An unregistered or `.P` symbol fails closed here.
            #
            # This step previously awaited `client.get_products()`. That is not
            # a method on `DeltaIndiaClient` -- it exists only on the test
            # mocks -- so no live API capability is removed by resolving
            # identity from the registry instead.
            spec = delta_india_registry().get(symbol)
            product_id = spec.product_id
            contract_value = spec.contract_value
            tick_size = spec.tick_size

            # `get_ticker` is fail-closed: it raises unless the exchange
            # returned this exact symbol's ticker with a finite, positive
            # `mark_price`. So there is nothing left to default to here -- and
            # a default must never answer a safety question. Falling back to the
            # strategy's *planned* entry price would silently size real capital
            # off a theoretical number the exchange never confirmed.
            ticker = await client.get_ticker(symbol)
            raw_mark_price = ticker.get("mark_price")
            if raw_mark_price is None:
                raise CapitalAllocationError(
                    f"Live ticker for {symbol} carries no mark_price; refusing "
                    f"to size a position without an authoritative mark"
                )
            mark_price = Decimal(str(raw_mark_price))

            # 7. Dynamic Capital Allocation & Position Sizing
            effective_leverage = self.config.leverage_override or default_leverage
            sizing_result: PositionSizingResult = self.capital_allocator.calculate_100_percent_allocation(
                symbol=symbol,
                entry_price=mark_price,
                available_balance=available_margin,
                leverage=effective_leverage,
                contract_unit=contract_value,
                custom_safety_buffer=self.config.custom_safety_buffer_pct,
            )

            if sizing_result.position_quantity <= Decimal("0"):
                raise CapitalAllocationError(f"Calculated position size is 0 for balance ${available_margin}")

            # 8. Submit Marketable Limit Entry Order
            order_side = OrderSide.BUY if direction == TradeDirection.LONG else OrderSide.SELL
            price_offset = Decimal("1.001") if order_side == OrderSide.BUY else Decimal("0.999")
            entry_limit_price = (mark_price * price_offset).quantize(tick_size)
            client_order_id = generate_client_order_id("EN")

            entry_req = DeltaOrderRequest(
                product_id=product_id,
                # Both identity fields come from the one spec resolved at step 6.
                # `symbol` is necessarily equal to `spec.symbol` -- the registry
                # lookup is exact and every record is keyed by its own symbol --
                # so this is the same value, sourced structurally from the
                # authority rather than from the raw dispatch argument.
                product_symbol=spec.symbol,
                side=order_side,
                order_type=OrderType.LIMIT_ORDER,
                size=sizing_result.position_quantity,
                limit_price=entry_limit_price,
                client_order_id=client_order_id,
            )

            entry_submitted = True
            entry_resp = await client.place_order(entry_req)
            entry_accepted = True

            # 9. Verify Fill Confirmation
            fill_price = mark_price
            for _ in range(5):
                await asyncio.sleep(0.5)
                try:
                    ord_status = await client.get_order(entry_resp.id)
                    if ord_status.state == OrderStatus.FILLED:
                        fill_price = ord_status.average_fill_price or mark_price
                        break
                except Exception:
                    pass

            # 10. Establish SL & TP Brackets (Reduce-Only)
            bracket_side = OrderSide.SELL if order_side == OrderSide.BUY else OrderSide.BUY
            sl_client_id = generate_client_order_id("SL")
            tp_client_id = generate_client_order_id("TP")

            sl_req = DeltaOrderRequest(
                product_id=product_id,
                product_symbol=spec.symbol,
                side=bracket_side,
                order_type=OrderType.STOP_MARKET_ORDER,
                size=sizing_result.position_quantity,
                stop_price=stop_loss_price.quantize(tick_size),
                # Delta reads `stop_order_type` to decide an order is a stop and
                # `stop_trigger_method` for the series that arms it; without
                # them this reaches the exchange as a plain `market_order` with
                # an ignored `stop_price` and closes the position immediately.
                # Same contract, same trigger series as the Path-A protection in
                # `trade_lifecycle._place_bracket_protection`.
                stop_order_type=StopOrderType.STOP_LOSS_ORDER,
                stop_trigger_method=StopTriggerMethod.LAST_TRADED_PRICE,
                reduce_only=True,
                client_order_id=sl_client_id,
            )
            sl_resp = await client.place_order(sl_req)

            tp_req = DeltaOrderRequest(
                product_id=product_id,
                product_symbol=spec.symbol,
                side=bracket_side,
                order_type=OrderType.LIMIT_ORDER,
                size=sizing_result.position_quantity,
                limit_price=take_profit_price.quantize(tick_size),
                reduce_only=True,
                client_order_id=tp_client_id,
            )
            tp_resp = await client.place_order(tp_req)

            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="EXECUTED",
                entry_order_id=str(entry_resp.id),
                entry_fill_price=fill_price,
                allocated_quantity=sizing_result.position_quantity,
                notional_value=sizing_result.notional_value,
                sl_order_id=str(sl_resp.id),
                sl_price=stop_loss_price,
                tp_order_id=str(tp_resp.id),
                tp_price=take_profit_price,
                live_balance_queried=available_margin,
                executed_at=datetime.now(timezone.utc),
            )

        except CapitalAllocationError as e:
            _release_lock_if_nothing_can_exist()
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="BLOCKED_MARGIN",
                error=str(e),
            )
        except DeltaOrderRejectedError as e:
            # An explicit rejection (HTTP 400) means Delta refused the request
            # outright, so the order does not exist and no position can exist.
            # The account is handed back. This is the same reasoning
            # `TradeLifecycleManager`'s `DeltaOrderRejectedError` handler states:
            # "An explicit exchange rejection means the order does not exist, so
            # no position can exist and the lock is released."
            _release_lock_if_nothing_can_exist()
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="ERROR",
                error=str(e),
            )
        except (DeltaConnectionError, asyncio.TimeoutError) as e:
            # UNKNOWN outcome. If the entry POST had already been attempted, the
            # order may have reached Delta, so a position may be open and -- with
            # no bracket sent -- unprotected. LOCK INTENTIONALLY RETAINED
            # (safety rules #11, #14); reconciliation releases it once the
            # exchange is confirmed flat. Path A retains for exactly this case:
            # "the order may have reached Delta, so a position may exist.
            # Releasing here would allow a second trade alongside a
            # possibly-live, possibly-unprotected one."
            #
            # Before the attempt the same exception means a balance or ticker
            # read failed and nothing was sent, so retention would block the
            # account for a read error. Path A does not retain there either --
            # its handler wraps only the entry submission and what follows it.
            if not entry_submitted:
                _release_lock_if_nothing_can_exist()
                return UserExecutionResult(
                    user_id=self.config.user_id,
                    account_id=self.config.account_id,
                    setup_id=setup_id,
                    symbol=symbol,
                    status="ERROR",
                    error=str(e),
                )
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="RECONCILIATION_REQUIRED",
                error=str(e),
            )
        except Exception as e:
            _release_lock_if_nothing_can_exist()
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="ERROR",
                error=str(e),
            )
        finally:
            if client and hasattr(client, "close"):
                try:
                    await client.close()
                except Exception:
                    pass


class MultiUserExecutionOrchestrator:
    """Orchestrates strategy signal dispatch across multiple independent user accounts."""

    def __init__(
        self,
        lock_manager: Optional[SingleTradeLockManager] = None,
        capital_allocator: Optional[CapitalAllocator] = None,
    ):
        self.lock_manager = lock_manager or SingleTradeLockManager()
        self.capital_allocator = capital_allocator or CapitalAllocator()

    async def dispatch_signal(
        self,
        setup_id: str,
        symbol: str,
        direction: TradeDirection,
        planned_entry_price: Decimal,
        stop_loss_price: Decimal,
        take_profit_price: Decimal,
        accounts: List[UserAccountConfig],
        default_leverage: int = 10,
    ) -> MultiUserDispatchSummary:
        """Dispatch a single qualified strategy signal across all registered user accounts concurrently."""
        summary = MultiUserDispatchSummary(
            setup_id=setup_id,
            symbol=symbol,
            total_accounts=len(accounts),
            executed_count=0,
            skipped_count=0,
            error_count=0,
        )

        async def _run_user(acct: UserAccountConfig) -> UserExecutionResult:
            session = UserExecutionSession(acct, self.lock_manager, self.capital_allocator)
            return await session.execute_trade(
                setup_id=setup_id,
                symbol=symbol,
                direction=direction,
                planned_entry_price=planned_entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                default_leverage=default_leverage,
            )

        # Run all users in parallel isolated tasks
        tasks = [_run_user(acct) for acct in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for acct, res in zip(accounts, results):
            if isinstance(res, Exception):
                summary.error_count += 1
                summary.user_results[acct.user_id] = UserExecutionResult(
                    user_id=acct.user_id,
                    account_id=acct.account_id,
                    setup_id=setup_id,
                    symbol=symbol,
                    status="ERROR",
                    error=str(res),
                )
            elif isinstance(res, UserExecutionResult):
                summary.user_results[acct.user_id] = res
                if res.status == "EXECUTED":
                    summary.executed_count += 1
                elif res.status.startswith("SKIPPED") or res.status.startswith("BLOCKED"):
                    summary.skipped_count += 1
                else:
                    summary.error_count += 1

        return summary
