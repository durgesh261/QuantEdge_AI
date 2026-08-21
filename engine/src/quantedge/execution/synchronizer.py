"""
Live Account, Balance, Open Orders, and Position Synchronization & Reconciliation Engine.

Responsibilities:
1. Reconcile exchange wallet balances against local account state (USDT/BTC).
2. Reconcile active derivatives positions (LONG/SHORT, size changes, reversals, closures).
3. Reconcile active open orders (idempotent matching by exchange_id / client_order_id, partial fills, closures).
4. Detect and record discrepancies without deleting historical database records.
5. Guarantee complete idempotency (repeated sync cycles produce 0 duplicate records).
6. Guarantee safety: network failures, auth errors, and malformed responses never corrupt local state.
7. Strictly real-trading architecture: 0 paper-trading, 0 simulated execution.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List, Set

from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaClientError,
    DeltaAuthError,
    DeltaRateLimitError,
    DeltaConnectionError,
    DeltaResponseError,
)
from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    DeltaWalletBalance,
    DeltaAccountSummary,
    DeltaPosition,
    DeltaOrderResponse,
)

logger = logging.getLogger("live_account_sync")


# ── State Records (Mirroring PostgreSQL Schema) ───────────────────────────────


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    LIQUIDATED = "LIQUIDATED"


@dataclass
class PositionRecord:
    """Local position record matching PostgreSQL positions table."""
    symbol: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    leverage: Decimal
    margin_used: Decimal
    liquidation_price: Optional[Decimal] = None
    status: PositionStatus = PositionStatus.OPEN
    delta_position_id: Optional[str] = None
    stop_loss_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OrderRecord:
    """Local order record matching PostgreSQL orders table."""
    delta_order_id: Optional[str]
    client_order_id: Optional[str]
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    filled_quantity: Decimal
    status: OrderStatus
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    average_fill_price: Optional[Decimal] = None
    reduce_only: bool = False
    error_message: Optional[str] = None
    placed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AccountRecord:
    """Local trading account record matching PostgreSQL trading_accounts table."""
    account_id: str
    base_currency: str = "USDT"
    current_balance: Decimal = Decimal("0")
    available_balance: Decimal = Decimal("0")
    margin_used: Decimal = Decimal("0")
    total_equity: Decimal = Decimal("0")
    is_active: bool = True
    user_id: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ConnectionRecord:
    """Connection status matching PostgreSQL delta_connections table."""
    environment: str = "LIVE"
    connection_status: str = "DISCONNECTED"  # CONNECTED, DISCONNECTED, ERROR
    last_connected_at: Optional[datetime] = None
    last_error: Optional[str] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Local State Store Interface ───────────────────────────────────────────────


class LocalStateStore:
    """In-memory state store with deterministic ACID semantics mirroring PostgreSQL."""

    def __init__(self, account_id: str = "default_account"):
        self.account_id = account_id
        self.account: AccountRecord = AccountRecord(account_id=account_id)
        self.connection: ConnectionRecord = ConnectionRecord()
        self.positions: Dict[str, PositionRecord] = {}  # symbol -> PositionRecord (OPEN positions)
        self.position_history: List[PositionRecord] = []
        self.orders: Dict[str, OrderRecord] = {}  # (delta_order_id or client_order_id) -> OrderRecord
        self.audit_events: List[Dict[str, Any]] = []

    def get_open_positions(self) -> List[PositionRecord]:
        return [p for p in self.positions.values() if p.status == PositionStatus.OPEN]

    def get_open_orders(self) -> List[OrderRecord]:
        return [o for o in self.orders.values() if o.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING)]

    def record_audit(self, action: str, details: Dict[str, Any]) -> None:
        self.audit_events.append({
            "action": action,
            "details": details,
            "timestamp": datetime.now(timezone.utc),
        })


# ── Synchronization Result ────────────────────────────────────────────────────


@dataclass
class SyncResult:
    """Result of a live account synchronization cycle."""
    success: bool
    synced_at: datetime
    account_id: str
    equity: Decimal = Decimal("0")
    available_balance: Decimal = Decimal("0")
    margin_used: Decimal = Decimal("0")
    positions_synced: int = 0
    orders_synced: int = 0
    discrepancies: List[str] = field(default_factory=list)
    error: Optional[str] = None


# ── Live Account Synchronization Service ──────────────────────────────────────


class LiveAccountSyncService:
    """Authoritative synchronization service reconciling Delta Exchange state with local database."""

    def __init__(self, client: DeltaIndiaClient, state_store: Optional[LocalStateStore] = None):
        self.client = client
        self.state_store = state_store or LocalStateStore()

    async def synchronize(self, account_id: Optional[str] = None) -> SyncResult:
        """Perform a complete, idempotent synchronization cycle from Delta Exchange India."""
        return await self._do_sync(account_id)

    async def sync(self, account_id: Optional[str] = None) -> SyncResult:
        """Alias for synchronize."""
        return await self._do_sync(account_id)

    async def _do_sync(self, account_id: Optional[str] = None) -> SyncResult:
        sync_time = datetime.now(timezone.utc)
        target_account_id = account_id or self.state_store.account_id
        discrepancies: List[str] = []

        try:
            # 1. Verify exchange connectivity & credentials
            if not self.client._api_key or not self.client._api_secret:
                raise DeltaAuthError("Missing Delta Exchange API key or secret in client configuration.")

            # 2. Fetch live data from Delta Exchange
            summary = await self.client.get_account_summary()
            exchange_positions = await self.client.get_positions()
            exchange_orders = await self.client.get_open_orders()

            # 3. Reconcile Account Balances & Equity
            self._reconcile_balances(summary, sync_time)

            # 4. Reconcile Positions
            pos_count, pos_discrepancies = self._reconcile_positions(exchange_positions, sync_time)
            discrepancies.extend(pos_discrepancies)

            # 5. Reconcile Open Orders
            ord_count, ord_discrepancies = self._reconcile_orders(exchange_orders, sync_time)
            discrepancies.extend(ord_discrepancies)

            # 6. Update Connection Status to CONNECTED
            self.state_store.connection.connection_status = "CONNECTED"
            self.state_store.connection.last_connected_at = sync_time
            self.state_store.connection.last_error = None
            self.state_store.connection.updated_at = sync_time

            self.state_store.record_audit("SYNC_SUCCESS", {
                "synced_at": sync_time.isoformat(),
                "equity": str(summary.total_equity),
                "positions_count": pos_count,
                "orders_count": ord_count,
                "discrepancies_count": len(discrepancies),
            })

            return SyncResult(
                success=True,
                synced_at=sync_time,
                account_id=target_account_id,
                equity=summary.total_equity,
                available_balance=summary.available_balance,
                margin_used=summary.margin_used,
                positions_synced=pos_count,
                orders_synced=ord_count,
                discrepancies=discrepancies,
                error=None,
            )

        except (DeltaAuthError, DeltaClientError, DeltaConnectionError, DeltaRateLimitError, DeltaResponseError, Exception) as e:
            error_msg = str(e)
            logger.error("Live account synchronization failed: %s", error_msg)

            # Safe failure: update connection error without corrupting existing records
            self.state_store.connection.connection_status = "ERROR"
            self.state_store.connection.last_error = error_msg
            self.state_store.connection.updated_at = sync_time

            self.state_store.record_audit("SYNC_FAILED", {
                "timestamp": sync_time.isoformat(),
                "error": error_msg,
            })

            return SyncResult(
                success=False,
                synced_at=sync_time,
                account_id=target_account_id,
                equity=self.state_store.account.total_equity,
                available_balance=self.state_store.account.available_balance,
                margin_used=self.state_store.account.margin_used,
                positions_synced=0,
                orders_synced=0,
                discrepancies=[],
                error=error_msg,
            )

    # ── Reconciliation Helpers ────────────────────────────────────────────────

    def _reconcile_balances(self, summary: DeltaAccountSummary, sync_time: datetime) -> None:
        """Update local account record with exchange balances."""
        account = self.state_store.account
        account.current_balance = summary.total_equity
        account.available_balance = summary.available_balance
        account.margin_used = summary.margin_used
        account.total_equity = summary.total_equity
        account.last_synced_at = sync_time
        account.updated_at = sync_time

    def _reconcile_positions(
        self, exchange_positions: List[DeltaPosition], sync_time: datetime
    ) -> tuple[int, List[str]]:
        """Reconcile active positions against local state store."""
        discrepancies: List[str] = []
        exchange_symbols: Set[str] = set()

        for ep in exchange_positions:
            exchange_symbols.add(ep.product_symbol)
            local_pos = self.state_store.positions.get(ep.product_symbol)

            if local_pos is None:
                # Discrepancy: Position exists on exchange but not locally -> import it
                new_pos = PositionRecord(
                    symbol=ep.product_symbol,
                    side=ep.side,
                    quantity=ep.size,
                    entry_price=ep.entry_price,
                    current_price=ep.mark_price,
                    unrealized_pnl=ep.unrealized_pnl,
                    realized_pnl=ep.realized_pnl,
                    leverage=ep.leverage,
                    margin_used=ep.margin,
                    liquidation_price=ep.liquidation_price,
                    status=PositionStatus.OPEN,
                    opened_at=sync_time,
                    updated_at=sync_time,
                )
                self.state_store.positions[ep.product_symbol] = new_pos
                discrepancies.append(f"Position for {ep.product_symbol} imported from exchange (size={ep.size})")

            else:
                # Position already exists locally -> update live mark price, PnL, leverage, margin
                if local_pos.side != ep.side:
                    discrepancies.append(f"Position reversal detected on {ep.product_symbol}: {local_pos.side} -> {ep.side}")
                    local_pos.side = ep.side

                if local_pos.quantity != ep.size:
                    discrepancies.append(f"Position size adjustment on {ep.product_symbol}: {local_pos.quantity} -> {ep.size}")
                    local_pos.quantity = ep.size

                local_pos.entry_price = ep.entry_price
                local_pos.current_price = ep.mark_price
                local_pos.unrealized_pnl = ep.unrealized_pnl
                local_pos.realized_pnl = ep.realized_pnl
                local_pos.leverage = ep.leverage
                local_pos.margin_used = ep.margin
                local_pos.liquidation_price = ep.liquidation_price
                local_pos.status = PositionStatus.OPEN
                local_pos.updated_at = sync_time

        # Check for local positions that are no longer reported open on exchange (closed positions)
        local_open_symbols = list(self.state_store.positions.keys())
        for sym in local_open_symbols:
            if sym not in exchange_symbols:
                closed_pos = self.state_store.positions.pop(sym)
                closed_pos.status = PositionStatus.CLOSED
                closed_pos.closed_at = sync_time
                closed_pos.updated_at = sync_time
                self.state_store.position_history.append(closed_pos)
                discrepancies.append(f"Position on {sym} closed on exchange; local state transitioned to CLOSED")

        return len(exchange_positions), discrepancies

    def _reconcile_orders(
        self, exchange_orders: List[DeltaOrderResponse], sync_time: datetime
    ) -> tuple[int, List[str]]:
        """Reconcile open orders against local state store."""
        discrepancies: List[str] = []
        exchange_order_keys: Set[str] = set()

        for eo in exchange_orders:
            order_key = str(eo.id)
            if eo.client_order_id:
                exchange_order_keys.add(eo.client_order_id)
            exchange_order_keys.add(order_key)

            # Match locally by client_order_id or exchange ID
            local_order = (
                self.state_store.orders.get(eo.client_order_id)
                if eo.client_order_id and eo.client_order_id in self.state_store.orders
                else self.state_store.orders.get(order_key)
            )

            if local_order is None:
                # Order exists on exchange but not locally -> import it
                new_order = OrderRecord(
                    delta_order_id=str(eo.id),
                    client_order_id=eo.client_order_id,
                    symbol=eo.product_symbol,
                    side=eo.side,
                    order_type=eo.order_type,
                    quantity=eo.size,
                    filled_quantity=eo.filled_size,
                    status=eo.state,
                    price=eo.limit_price,
                    stop_price=eo.stop_price,
                    average_fill_price=eo.average_fill_price,
                    reduce_only=eo.reduce_only,
                    placed_at=eo.created_at,
                    updated_at=sync_time,
                )
                self.state_store.orders[order_key] = new_order
                if eo.client_order_id:
                    self.state_store.orders[eo.client_order_id] = new_order
                discrepancies.append(f"Open order {eo.id} ({eo.product_symbol} {eo.side}) imported from exchange")

            else:
                # Update existing order state
                if local_order.status != eo.state:
                    discrepancies.append(f"Order {eo.id} status changed: {local_order.status} -> {eo.state}")
                    local_order.status = eo.state

                if local_order.filled_quantity != eo.filled_size:
                    discrepancies.append(f"Order {eo.id} fill update: {local_order.filled_quantity} -> {eo.filled_size}")
                    local_order.filled_quantity = eo.filled_size

                local_order.delta_order_id = str(eo.id)
                local_order.average_fill_price = eo.average_fill_price
                local_order.updated_at = sync_time

        # Check for locally OPEN orders that are no longer reported as OPEN by exchange
        for key, local_order in list(self.state_store.orders.items()):
            if local_order.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
                is_present_on_exchange = (
                    (local_order.delta_order_id and local_order.delta_order_id in exchange_order_keys)
                    or (local_order.client_order_id and local_order.client_order_id in exchange_order_keys)
                )
                if not is_present_on_exchange:
                    # Order is no longer open on exchange (either fully filled, cancelled, or expired)
                    # If filled_quantity == quantity, mark FILLED, else CANCELLED
                    if local_order.filled_quantity >= local_order.quantity and local_order.quantity > Decimal("0"):
                        local_order.status = OrderStatus.FILLED
                        local_order.filled_at = sync_time
                        discrepancies.append(f"Order {local_order.delta_order_id or local_order.client_order_id} completed (FILLED)")
                    else:
                        local_order.status = OrderStatus.CANCELLED
                        local_order.cancelled_at = sync_time
                        discrepancies.append(f"Order {local_order.delta_order_id or local_order.client_order_id} no longer open on exchange (CANCELLED)")
                    local_order.updated_at = sync_time

        return len(exchange_orders), discrepancies
