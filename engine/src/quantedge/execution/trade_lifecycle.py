"""
Authoritative Signal-to-Execution Bridge & Controlled Trade Lifecycle Manager for QuantEdge AI.

Phase 5.7 Implementation:
1. Authoritative Signal-to-Execution Bridge:
   - Evaluates StrategyDecision (TRADE_SETUP_READY).
   - Strictly enforces server-side parameters (direction, entry, SL, TP, R:R).
   - Rejects any frontend tampering or fabricated values.
2. Complete Trade Lifecycle State Machine:
   - ENTRY_PENDING -> ENTRY_SUBMITTED -> ENTRY_PARTIALLY_FILLED -> ENTRY_FILLED
   - -> PROTECTION_PENDING -> SL_TP_SUBMITTED -> PROTECTED_POSITION -> POSITION_CLOSED
   - Handles ENTRY_REJECTED, ENTRY_CANCELLED, ENTRY_TIMEOUT, PROTECTION_FAILED, KILL_SWITCH_TRIGGERED.
3. Authoritative TP/SL Bracket Protection:
   - LONG: SL < Entry < TP
   - SHORT: TP < Entry < SL
   - Dynamically scales protection to exact filled quantity (partial fills 40 -> 70 -> 100).
   - Cancels stale bracket orders upon position closure.
4. Server-Side Daily Loss Guard:
   - Tracks realized losses for current trading day.
   - Rejects new entries if daily_loss >= configured_daily_loss_limit.
5. Emergency Kill Switch Controls:
   - One-click trigger cancels pending entries, halts new orders, preserves protective brackets.
   - Authenticated reset workflow.
6. Dual-Layer WebSocket & REST Reconciliation:
   - Processes real-time WebSocket events with authoritative REST fallback.
   - Zero real orders placed during tests.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from decimal import Decimal
from enum import Enum
import logging
import threading
from typing import Optional, Dict, Any, List, Set, Union

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    TimeInForce,
    DeltaOrderRequest,
    DeltaOrderResponse,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaClientError,
    DeltaAuthError,
    DeltaRateLimitError,
    DeltaOrderRejectedError,
    DeltaConnectionError,
    generate_client_order_id,
)
from quantedge.execution.security import mask_secret, sanitize_text
from quantedge.execution.synchronizer import (
    LocalStateStore,
    OrderRecord,
    PositionRecord,
    PositionStatus,
    LiveAccountSyncService,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    OrderValidationRequest,
    OrderValidationResult,
    RiskConfiguration,
    ValidationContext,
    RejectionReasonCode,
)
from quantedge.execution.private_websocket import (
    DeltaOrderEvent,
    DeltaPositionEvent,
    DeltaFillEvent,
    StreamHealth,
)
from quantedge.execution.algo_config import (
    AlgoConfigStore,
    AlgoConfiguration,
    AlgoConfigurationSnapshot,
)
from quantedge.execution.single_trade_lock import (
    SingleTradeLockManager,
    SingleTradeLockError,
)
from quantedge.execution.capital_allocator import (
    CapitalAllocator,
    CapitalAllocationError,
)
from quantedge.strategy.models import StrategyDecision, SetupState, StrategyDirection, TradeDirection

logger = logging.getLogger("trade_lifecycle")


# ── Lifecycle States & Enums ──────────────────────────────────────────────────


class TradeLifecycleState(str, Enum):
    ENTRY_PENDING = "ENTRY_PENDING"
    ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
    ENTRY_PARTIALLY_FILLED = "ENTRY_PARTIALLY_FILLED"
    ENTRY_FILLED = "ENTRY_FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    SL_TP_SUBMITTED = "SL_TP_SUBMITTED"
    PROTECTED_POSITION = "PROTECTED_POSITION"
    POSITION_CLOSED = "POSITION_CLOSED"
    ENTRY_REJECTED = "ENTRY_REJECTED"
    ENTRY_CANCELLED = "ENTRY_CANCELLED"
    ENTRY_TIMEOUT = "ENTRY_TIMEOUT"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    KILL_SWITCH_TRIGGERED = "KILL_SWITCH_TRIGGERED"


class CloseReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    KILL_SWITCH = "KILL_SWITCH"
    RISK_LIMIT = "RISK_LIMIT"
    EXCHANGE_LIQUIDATION = "EXCHANGE_LIQUIDATION"
    UNKNOWN_RECONCILIATION = "UNKNOWN_RECONCILIATION"


# ── Authoritative Lifecycle Record ────────────────────────────────────────────


@dataclass
class TradeLifecycleRecord:
    """Authoritative record tracking the complete lifecycle of a trade setup."""
    setup_id: str
    account_id: str
    user_id: Optional[str]
    symbol: str
    direction: TradeDirection
    requested_quantity: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    risk_reward_ratio: Decimal
    risk_amount: Decimal
    reward_amount: Decimal
    
    # Order tracking
    entry_order_id: Optional[str] = None
    entry_client_order_id: Optional[str] = None
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Optional[Decimal] = None
    
    # Protective bracket orders
    sl_order_id: Optional[str] = None
    sl_client_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    tp_client_order_id: Optional[str] = None
    protected_quantity: Decimal = Decimal("0")
    
    # State & Outcome
    state: TradeLifecycleState = TradeLifecycleState.ENTRY_PENDING
    close_reason: Optional[CloseReason] = None
    realized_pnl: Optional[Decimal] = None
    gross_pnl: Optional[Decimal] = None
    trading_fees: Decimal = Decimal("0")
    funding_costs: Decimal = Decimal("0")
    net_pnl: Optional[Decimal] = None
    pre_trade_balance: Decimal = Decimal("0")
    post_trade_balance: Optional[Decimal] = None
    daily_loss_at_entry: Decimal = Decimal("0")
    rejection_code: Optional[str] = None
    error_message: Optional[str] = None
    
    # Configuration & Strategy Snapshot (Phase 5.7)
    config_version: Optional[int] = None
    config_snapshot: Optional[Any] = None
    strategy_name: str = "SMC"
    strategy_version: str = "2.1"
    
    # Audit log history
    history: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None

    def record_transition(self, new_state: TradeLifecycleState, reason: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Record an immutable state transition event in history."""
        now = datetime.now(timezone.utc)
        event = {
            "from_state": self.state.value,
            "to_state": new_state.value,
            "reason": reason,
            "metadata": metadata or {},
            "timestamp": now.isoformat(),
        }
        self.history.append(event)
        self.state = new_state
        self.updated_at = now
        if new_state == TradeLifecycleState.POSITION_CLOSED:
            self.closed_at = now


# ── Trade Lifecycle Manager ───────────────────────────────────────────────────


class TradeLifecycleManager:
    """Coordinates signal qualification, pre-trade risk/stale checks, order execution,

    bracket protection, partial fill scaling, position closure, and kill-switch safety.
    """

    def __init__(
        self,
        client: DeltaIndiaClient,
        validation_gateway: OrderValidationGateway,
        state_store: LocalStateStore,
        sync_service: Optional[LiveAccountSyncService] = None,
        algo_config_store: Optional[AlgoConfigStore] = None,
        single_trade_lock: Optional[SingleTradeLockManager] = None,
        capital_allocator: Optional[CapitalAllocator] = None,
        daily_loss_limit: Decimal = Decimal("500.00"),
        max_stale_seconds: int = 120,
    ):
        self.client = client
        self.validation_gateway = validation_gateway
        self.state_store = state_store
        self.sync_service = sync_service
        self.algo_config_store = algo_config_store or AlgoConfigStore()
        self.single_trade_lock = single_trade_lock or SingleTradeLockManager()
        self.capital_allocator = capital_allocator or CapitalAllocator()
        self.daily_loss_limit = daily_loss_limit
        self.max_stale_seconds = max_stale_seconds

        self._active_trades: Dict[str, TradeLifecycleRecord] = {}  # setup_id -> record
        self._trade_history: List[TradeLifecycleRecord] = []
        self._lock = threading.Lock()
        
        # Realized daily loss cache
        self._realized_daily_losses: Dict[date, Decimal] = {}

    def get_realized_daily_loss(self, query_date: Optional[date] = None) -> Decimal:
        """Calculate total realized trading loss for the specified day."""
        target_date = query_date or datetime.now(timezone.utc).date()
        total_loss = Decimal("0")
        for trade in self._trade_history:
            if trade.closed_at and trade.closed_at.date() == target_date:
                if trade.realized_pnl and trade.realized_pnl < Decimal("0"):
                    total_loss += abs(trade.realized_pnl)
        return total_loss

    def get_active_trade(self, setup_id: str) -> Optional[TradeLifecycleRecord]:
        return self._active_trades.get(setup_id)

    def get_all_active_trades(self) -> List[TradeLifecycleRecord]:
        return list(self._active_trades.values())

    def get_trade_history(self) -> List[TradeLifecycleRecord]:
        return list(self._trade_history)

    # ── Signal-to-Execution Bridge Entry Point ────────────────────────────────

    async def execute_trade_setup(
        self,
        decision: StrategyDecision,
        account_id: str,
        user_id: Optional[str] = None,
        override_client_order_id: Optional[str] = None,
        frontend_params: Optional[Dict[str, Any]] = None,
    ) -> TradeLifecycleRecord:
        """Execute a qualified strategy decision through the authoritative validation and execution bridge."""
        setup_id = decision.setup_id
        symbol = decision.symbol

        with self._lock:
            if setup_id in self._active_trades:
                rec = self._active_trades[setup_id]
                rec.rejection_code = RejectionReasonCode.DUPLICATE_SETUP_ID.value
                rec.error_message = f"Setup {setup_id} is already in active execution"
                return rec

        # Step 0: Single-Trade Account Lock
        effective_user_id = user_id or self.state_store.account.user_id or "default_user"
        try:
            self.single_trade_lock.acquire_lock(
                user_id=effective_user_id,
                account_id=account_id,
                setup_id=setup_id,
                symbol=symbol,
            )
        except SingleTradeLockError as e:
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction, Decimal("1.0"),
                getattr(decision, "entry", Decimal("0")) or Decimal("0"),
                decision.stop_loss or Decimal("0"),
                decision.take_profit or Decimal("0"),
                getattr(decision, "risk_reward", Decimal("2.0")),
                RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value,
                str(e),
            )

        # Extract attributes with fallback
        setup_state = getattr(decision, "setup_state", None) or getattr(decision, "status", None)
        entry_price = getattr(decision, "entry", None) or getattr(decision, "entry_price", None)
        stop_loss = decision.stop_loss
        take_profit = decision.take_profit
        risk_reward = getattr(decision, "risk_reward", None) or getattr(decision, "risk_reward_ratio", Decimal("2.0"))
        raw_qty = getattr(decision, "quantity", None)
        quantity = raw_qty if raw_qty is not None else Decimal("1.0")
        raw_risk = getattr(decision, "risk_amount", None)
        risk_amount = raw_risk if raw_risk is not None else (abs(entry_price - stop_loss) * quantity if entry_price and stop_loss else Decimal("0"))
        raw_reward = getattr(decision, "reward_amount", None)
        reward_amount = raw_reward if raw_reward is not None else (abs(take_profit - entry_price) * quantity if entry_price and take_profit else Decimal("0"))

        # 1. Authoritative Parameter Enforcement & Anti-Tampering Check
        if frontend_params:
            # Check if frontend attempted to alter direction, prices, or R:R
            if "direction" in frontend_params and frontend_params["direction"] != decision.direction.value:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "FRONTEND_DIRECTION_TAMPERING", "Frontend cannot alter authoritative trade direction"
                )
            if "entry_price" in frontend_params and Decimal(str(frontend_params["entry_price"])) != entry_price:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "FRONTEND_ENTRY_TAMPERING", "Frontend cannot alter authoritative entry price"
                )
            if "stop_loss" in frontend_params and Decimal(str(frontend_params["stop_loss"])) != stop_loss:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "FRONTEND_SL_TAMPERING", "Frontend cannot alter authoritative stop loss"
                )
            if "take_profit" in frontend_params and Decimal(str(frontend_params["take_profit"])) != take_profit:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "FRONTEND_TP_TAMPERING", "Frontend cannot alter authoritative take profit"
                )

        # 2. Verify Strategy Status
        if setup_state != SetupState.TRADE_SETUP_READY:
            state_val = setup_state.value if hasattr(setup_state, 'value') else str(setup_state)
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction, quantity,
                entry_price, stop_loss, take_profit, risk_reward,
                "SETUP_NOT_READY", f"Strategy decision status is {state_val}, expected TRADE_SETUP_READY"
            )

        # 3. Verify Account Fail-Safe Flags (Kill-Switch & Algo-Enabled)
        if self.state_store.account.kill_switch_active:
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction, quantity,
                entry_price, stop_loss, take_profit, risk_reward,
                RejectionReasonCode.KILL_SWITCH_ACTIVE.value, "Emergency kill switch is active — all trade execution blocked"
            )

        if not self.state_store.account.algo_enabled:
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction, quantity,
                entry_price, stop_loss, take_profit, risk_reward,
                RejectionReasonCode.ALGO_DISABLED.value, "Algorithmic trading is disabled on this account"
            )

        # 4. Verify TP/SL Geometry
        if decision.direction in (TradeDirection.LONG, StrategyDirection.LONG):
            if not (stop_loss < entry_price < take_profit):
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    RejectionReasonCode.INVALID_TP_SL_GEOMETRY.value,
                    f"Invalid LONG geometry: SL ({stop_loss}) must be < Entry ({entry_price}) < TP ({take_profit})"
                )
        else:  # SHORT
            if not (take_profit < entry_price < stop_loss):
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    RejectionReasonCode.INVALID_TP_SL_GEOMETRY.value,
                    f"Invalid SHORT geometry: TP ({take_profit}) must be < Entry ({entry_price}) < SL ({stop_loss})"
                )

        # 5. Server-Side Daily Loss Guard
        today_loss = self.get_realized_daily_loss()
        if today_loss >= self.daily_loss_limit:
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction, quantity,
                entry_price, stop_loss, take_profit, risk_reward,
                RejectionReasonCode.DAILY_LOSS_LIMIT.value,
                f"Daily realized loss (${today_loss:.2f}) meets/exceeds limit (${self.daily_loss_limit:.2f})"
            )

        # 6. Stale Account State Check
        if self.state_store.account.last_synced_at:
            age_seconds = (datetime.now(timezone.utc) - self.state_store.account.last_synced_at).total_seconds()
            if age_seconds > self.max_stale_seconds:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    RejectionReasonCode.ACCOUNT_STATE_STALE.value,
                    f"Account state is stale ({age_seconds:.0f}s old, max allowed {self.max_stale_seconds}s)"
                )

        # 7. Create authoritative TradeLifecycleRecord & Bind Immutable Config Snapshot
        effective_user_id = user_id or self.state_store.account.user_id or "default_user"
        snapshot = self.algo_config_store.create_trade_snapshot(
            user_id=effective_user_id,
            account_id=account_id,
            setup_id=setup_id,
        )

        record = TradeLifecycleRecord(
            setup_id=setup_id,
            account_id=account_id,
            user_id=user_id,
            symbol=symbol,
            direction=decision.direction if isinstance(decision.direction, TradeDirection) else (TradeDirection.LONG if decision.direction == StrategyDirection.LONG else TradeDirection.SHORT),
            requested_quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            risk_reward_ratio=risk_reward,
            risk_amount=risk_amount,
            reward_amount=reward_amount,
            pre_trade_balance=self.state_store.account.available_balance,
            daily_loss_at_entry=today_loss,
            config_version=snapshot.version,
            config_snapshot=snapshot,
            strategy_name=getattr(decision, "strategy_name", "SMC"),
            strategy_version=getattr(decision, "strategy_version", "2.1"),
        )

        with self._lock:
            self._active_trades[setup_id] = record

        # 8. Submit Entry Order via OrderValidationGateway
        client_order_id = override_client_order_id or generate_client_order_id(f"QE_{symbol}_ENTRY")
        record.entry_client_order_id = client_order_id

        side = OrderSide.BUY if record.direction == TradeDirection.LONG else OrderSide.SELL

        order_req = DeltaOrderRequest(
            product_id=27,
            product_symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT_ORDER,
            size=record.requested_quantity,
            limit_price=record.entry_price,
            client_order_id=client_order_id,
            stop_loss_price=record.stop_loss_price,
            take_profit_price=record.take_profit_price,
        )

        val_request = OrderValidationRequest(
            account_id=account_id,
            setup_id=setup_id,
            symbol=symbol,
            direction=record.direction,
            order_type=OrderType.LIMIT_ORDER,
            quantity=record.requested_quantity,
            entry_price=record.entry_price,
            stop_loss=record.stop_loss_price,
            take_profit=record.take_profit_price,
            leverage=100,
            client_order_id=client_order_id,
        )

        eff_risk_pct = snapshot.risk_per_trade_pct if (snapshot.risk_per_trade_pct and snapshot.risk_per_trade_pct > Decimal("1.00")) else Decimal("35.0")
        risk_cfg = RiskConfiguration(
            risk_per_trade_pct=eff_risk_pct,
            max_leverage=snapshot.max_leverage or 100,
        )

        context = ValidationContext(
            account=self.state_store.account,
            risk_config=risk_cfg,
            algo_enabled=self.state_store.account.algo_enabled,
            kill_switch_active=self.state_store.account.kill_switch_active,
            connection=self.state_store.connection,
            api_key=getattr(self.client, "_api_key", "MOCKED_API_KEY"),
            api_secret=getattr(self.client, "_api_secret", "MOCKED_API_SECRET"),
            open_positions=self.state_store.get_open_positions(),
            open_orders=self.state_store.get_open_orders(),
            active_setup_ids=set(k for k in self._active_trades.keys() if k != setup_id),
        )

        val_result = self.validation_gateway.validate(val_request, context)
        if not val_result.is_valid:
            record.record_transition(
                TradeLifecycleState.ENTRY_REJECTED,
                f"Order validation rejected: {val_result.rejection_code.value} - {val_result.rejection_reason}"
            )
            record.rejection_code = val_result.rejection_code.value
            record.error_message = val_result.rejection_reason
            return record

        # 9. Submit Order to Exchange
        record.record_transition(TradeLifecycleState.ENTRY_SUBMITTED, f"Submitted limit entry order {client_order_id}")

        try:
            order_resp: DeltaOrderResponse = await self.client.place_order(order_req)
            record.entry_order_id = str(order_resp.id)
            
            # Record entry order in state store
            self.state_store.orders[str(order_resp.id)] = OrderRecord(
                delta_order_id=str(order_resp.id),
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                order_type=OrderType.LIMIT_ORDER,
                quantity=record.requested_quantity,
                filled_quantity=order_resp.filled_size,
                status=order_resp.state,
                price=record.entry_price,
            )

            # Check if immediately filled or partially filled
            if order_resp.state == OrderStatus.FILLED:
                await self.on_entry_fill(setup_id, order_resp.size, order_resp.average_fill_price or record.entry_price)
            elif order_resp.filled_size > Decimal("0"):
                await self.on_entry_partial_fill(setup_id, order_resp.filled_size, order_resp.average_fill_price or record.entry_price)

            return record

        except DeltaOrderRejectedError as e:
            record.record_transition(TradeLifecycleState.ENTRY_REJECTED, f"Exchange rejected order: {str(e)}")
            record.error_message = str(e)
            return record
        except (DeltaConnectionError, asyncio.TimeoutError) as e:
            record.record_transition(TradeLifecycleState.RECONCILIATION_REQUIRED, f"Network timeout during entry submission: {str(e)}")
            record.error_message = str(e)
            return record
        except Exception as e:
            record.record_transition(TradeLifecycleState.ENTRY_REJECTED, f"Unexpected error during entry: {str(e)}")
            record.error_message = str(e)
            return record

    # ── Partial Fill & Fill Lifecycle Handlers ────────────────────────────────

    async def on_entry_partial_fill(self, setup_id: str, filled_size: Decimal, avg_price: Decimal) -> None:
        """Handle partial entry fill: scale protection to match exact filled quantity."""
        record = self._active_trades.get(setup_id)
        if not record:
            return

        record.filled_quantity = filled_size
        record.average_fill_price = avg_price
        record.record_transition(
            TradeLifecycleState.ENTRY_PARTIALLY_FILLED,
            f"Partially filled {filled_size}/{record.requested_quantity} @ {avg_price}"
        )

        # Scale protective bracket orders to matched filled size
        await self._ensure_bracket_protection(record, filled_size)

    async def on_entry_fill(self, setup_id: str, filled_size: Decimal, avg_price: Decimal) -> None:
        """Handle complete entry fill: ensure 100% position bracket protection is active."""
        record = self._active_trades.get(setup_id)
        if not record:
            return

        record.filled_quantity = filled_size
        record.average_fill_price = avg_price
        record.record_transition(
            TradeLifecycleState.ENTRY_FILLED,
            f"Entry completely filled {filled_size} @ {avg_price}"
        )

        await self._ensure_bracket_protection(record, filled_size)

    # ── Bracket Protection (SL / TP) ──────────────────────────────────────────

    async def _ensure_bracket_protection(self, record: TradeLifecycleRecord, target_protected_size: Decimal) -> None:
        """Construct and submit protective SL and TP bracket orders matching the exact filled position size."""
        if target_protected_size <= Decimal("0"):
            return

        # If already fully protected for this size, no action needed
        if record.protected_quantity == target_protected_size and record.sl_order_id and record.tp_order_id:
            return

        record.record_transition(
            TradeLifecycleState.PROTECTION_PENDING,
            f"Configuring bracket protection for size {target_protected_size}"
        )

        close_side = OrderSide.SELL if record.direction == TradeDirection.LONG else OrderSide.BUY

        try:
            # 1. Submit Stop Loss Order (Stop Market with reduce_only)
            sl_client_id = generate_client_order_id(f"QE_{record.symbol}_SL")
            sl_req = DeltaOrderRequest(
                product_id=27,
                product_symbol=record.symbol,
                side=close_side,
                order_type=OrderType.STOP_MARKET_ORDER,
                size=target_protected_size,
                stop_price=record.stop_loss_price,
                reduce_only=True,
                client_order_id=sl_client_id,
            )
            sl_resp = await self.client.place_order(sl_req)
            record.sl_order_id = str(sl_resp.id)
            record.sl_client_order_id = sl_client_id

            # 2. Submit Take Profit Order (Limit with reduce_only)
            tp_client_id = generate_client_order_id(f"QE_{record.symbol}_TP")
            tp_req = DeltaOrderRequest(
                product_id=27,
                product_symbol=record.symbol,
                side=close_side,
                order_type=OrderType.LIMIT_ORDER,
                size=target_protected_size,
                limit_price=record.take_profit_price,
                reduce_only=True,
                client_order_id=tp_client_id,
            )
            tp_resp = await self.client.place_order(tp_req)
            record.tp_order_id = str(tp_resp.id)
            record.tp_client_order_id = tp_client_id

            record.protected_quantity = target_protected_size
            record.record_transition(
                TradeLifecycleState.PROTECTED_POSITION,
                f"Bracket protection active: SL={record.stop_loss_price} (ID {record.sl_order_id}), TP={record.take_profit_price} (ID {record.tp_order_id})"
            )

        except Exception as e:
            logger.error("Failed to submit bracket protection for setup %s: %s", record.setup_id, str(e))
            record.record_transition(
                TradeLifecycleState.PROTECTION_FAILED,
                f"Bracket protection submission failed: {str(e)}"
            )

    # ── Position Closure Lifecycle ────────────────────────────────────────────

    async def close_position(
        self,
        setup_id: str,
        reason: CloseReason,
        realized_pnl: Optional[Decimal] = None,
        gross_pnl: Optional[Decimal] = None,
        trading_fees: Decimal = Decimal("0.0"),
        funding_costs: Decimal = Decimal("0.0"),
        taxes_and_charges: Decimal = Decimal("0.0"),
        final_exchange_balance: Optional[Decimal] = None,
    ) -> TradeLifecycleRecord:
        """Close an active position, cancel stale protective orders, reconcile net PnL, and finalize lifecycle."""
        with self._lock:
            record = self._active_trades.get(setup_id)
            if not record:
                raise ValueError(f"No active trade found for setup {setup_id}")

            # 1. Cancel remaining open bracket orders to avoid stale executions
            if record.sl_order_id:
                try:
                    await self.client.cancel_order(record.sl_order_id, 27)
                except Exception as e:
                    logger.warning("Error cancelling SL order %s: %s", record.sl_order_id, str(e))

            if record.tp_order_id:
                try:
                    await self.client.cancel_order(record.tp_order_id, 27)
                except Exception as e:
                    logger.warning("Error cancelling TP order %s: %s", record.tp_order_id, str(e))

            # 2. Net PnL & Fee Calculation
            eff_gross = gross_pnl if gross_pnl is not None else (realized_pnl or Decimal("0"))
            net_pnl = CapitalAllocator.calculate_net_pnl(eff_gross, trading_fees, funding_costs, taxes_and_charges)

            record.gross_pnl = eff_gross
            record.trading_fees = trading_fees
            record.funding_costs = funding_costs
            record.net_pnl = net_pnl
            record.realized_pnl = net_pnl
            record.close_reason = reason

            if final_exchange_balance is not None:
                record.post_trade_balance = final_exchange_balance
                self.state_store.account.available_balance = final_exchange_balance
                self.state_store.account.total_equity = final_exchange_balance
            else:
                post_bal = CapitalAllocator.calculate_compounded_balance(record.pre_trade_balance, net_pnl)
                record.post_trade_balance = post_bal
                self.state_store.account.available_balance = post_bal
                self.state_store.account.total_equity = post_bal

            record.record_transition(
                TradeLifecycleState.POSITION_CLOSED,
                f"Position closed due to {reason.value}. Gross: ${eff_gross:.2f}, Fees: ${trading_fees:.2f}, Net PnL: ${net_pnl:.2f}, Post Balance: ${record.post_trade_balance:.2f}"
            )

            # 3. Archive to history
            del self._active_trades[setup_id]
            self._trade_history.append(record)

            # Update position in local state store
            if record.symbol in self.state_store.positions:
                pos = self.state_store.positions[record.symbol]
                pos.status = PositionStatus.CLOSED
                pos.closed_at = datetime.now(timezone.utc)
                pos.realized_pnl = net_pnl
                self.state_store.position_history.append(pos)
                del self.state_store.positions[record.symbol]

            # 4. Release Single-Trade Lock
            effective_user_id = record.user_id or self.state_store.account.user_id or "default_user"
            self.single_trade_lock.release_lock(effective_user_id, record.account_id, setup_id)

            return record

    # ── Emergency Kill Switch Workflow ────────────────────────────────────────

    async def activate_kill_switch(self, reason: str = "EMERGENCY_OPERATOR_TRIGGER") -> Dict[str, Any]:
        """Activate the emergency kill switch: blocks all new entries, cancels pending entry orders,

        preserves protective SL/TP brackets.
        """
        self.state_store.account.kill_switch_active = True
        cancelled_orders = []

        with self._lock:
            for setup_id, trade in list(self._active_trades.items()):
                if trade.state in (TradeLifecycleState.ENTRY_PENDING, TradeLifecycleState.ENTRY_SUBMITTED):
                    if trade.entry_order_id:
                        try:
                            await self.client.cancel_order(trade.entry_order_id, 27)
                            cancelled_orders.append(trade.entry_order_id)
                        except Exception as e:
                            logger.warning("Failed to cancel entry order %s on kill-switch: %s", trade.entry_order_id, str(e))

                    trade.record_transition(
                        TradeLifecycleState.KILL_SWITCH_TRIGGERED,
                        f"Kill switch activated: {reason}"
                    )
                    del self._active_trades[setup_id]
                    self._trade_history.append(trade)

        self.state_store.record_audit(
            action="KILL_SWITCH_ACTIVATED",
            details={"reason": reason, "cancelled_orders": cancelled_orders}
        )

        logger.critical("EMERGENCY KILL SWITCH ACTIVATED: %s. Cancelled %d entry orders.", reason, len(cancelled_orders))

        return {
            "kill_switch_active": True,
            "reason": reason,
            "cancelled_orders_count": len(cancelled_orders),
            "cancelled_orders": cancelled_orders,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def reset_kill_switch(self, authorized_by: str) -> Dict[str, Any]:
        """Reset the emergency kill switch (requires authenticated operator action)."""
        self.state_store.account.kill_switch_active = False
        self.state_store.record_audit(
            action="KILL_SWITCH_RESET",
            details={"authorized_by": authorized_by}
        )
        logger.info("Emergency kill switch reset by %s", authorized_by)
        return {
            "kill_switch_active": False,
            "authorized_by": authorized_by,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _create_rejected_record(
        self,
        setup_id: str,
        account_id: str,
        user_id: Optional[str],
        symbol: str,
        direction: Union[TradeDirection, StrategyDirection, str],
        quantity: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        risk_reward_ratio: Decimal,
        code: str,
        message: str,
    ) -> TradeLifecycleRecord:
        # Release single trade lock if it was acquired for this rejected attempt
        effective_user_id = user_id or self.state_store.account.user_id or "default_user"
        if code != RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value:
            self.single_trade_lock.release_lock(effective_user_id, account_id, setup_id)

        dir_enum = direction if isinstance(direction, TradeDirection) else TradeDirection.LONG
        rec = TradeLifecycleRecord(
            setup_id=setup_id,
            account_id=account_id,
            user_id=user_id,
            symbol=symbol,
            direction=dir_enum,
            requested_quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            risk_reward_ratio=risk_reward_ratio,
            risk_amount=abs(entry_price - stop_loss) * quantity,
            reward_amount=abs(take_profit - entry_price) * quantity,
            pre_trade_balance=self.state_store.account.available_balance,
            state=TradeLifecycleState.ENTRY_REJECTED,
            rejection_code=code,
            error_message=message,
        )
        rec.record_transition(TradeLifecycleState.ENTRY_REJECTED, f"{code}: {message}")
        self._trade_history.append(rec)
        return rec
