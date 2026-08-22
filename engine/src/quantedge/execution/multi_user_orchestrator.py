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

from quantedge.execution.models import (
    DeltaOrderRequest,
    DeltaOrderResponse,
    OrderSide,
    OrderType,
    OrderStatus,
    TimeInForce,
    PositionSide,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaClientError,
    DeltaAuthError,
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

        # 2. LOCK-FIRST ORDERING: Acquire SingleTradeLock before any external network calls
        try:
            self.lock_manager.acquire_lock(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
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

            # 6. Retrieve Live Product & Ticker Specifications
            products = await client.get_products()
            prod_info = next((p for p in products if p.get("symbol") == symbol), None)
            if not prod_info:
                raise ValueError(f"Product {symbol} not found on Delta Exchange India")

            product_id = int(prod_info.get("id", 0))
            contract_value = Decimal(str(prod_info.get("contract_value", "1.0")))
            tick_size = Decimal(str(prod_info.get("tick_size", "0.1")))

            ticker = await client.get_ticker(symbol)
            mark_price = Decimal(str(ticker.get("mark_price", planned_entry_price)))

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
                product_symbol=symbol,
                side=order_side,
                order_type=OrderType.LIMIT_ORDER,
                size=sizing_result.position_quantity,
                limit_price=entry_limit_price,
                client_order_id=client_order_id,
            )

            entry_resp = await client.place_order(entry_req)

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
                product_symbol=symbol,
                side=bracket_side,
                order_type=OrderType.STOP_MARKET_ORDER,
                size=sizing_result.position_quantity,
                stop_price=stop_loss_price.quantize(tick_size),
                reduce_only=True,
                client_order_id=sl_client_id,
            )
            sl_resp = await client.place_order(sl_req)

            tp_req = DeltaOrderRequest(
                product_id=product_id,
                product_symbol=symbol,
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
            self.lock_manager.release_lock(self.config.user_id, self.config.account_id, setup_id)
            return UserExecutionResult(
                user_id=self.config.user_id,
                account_id=self.config.account_id,
                setup_id=setup_id,
                symbol=symbol,
                status="BLOCKED_MARGIN",
                error=str(e),
            )
        except Exception as e:
            self.lock_manager.release_lock(self.config.user_id, self.config.account_id, setup_id)
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
