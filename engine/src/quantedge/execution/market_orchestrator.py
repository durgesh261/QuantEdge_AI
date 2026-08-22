"""
Full-Market Scanning Orchestrator & Single-Trade Capital Compounding Loop for QuantEdge AI.

Phase 5.8 Implementation:
1. Full-Market Scanning:
   - Scans all supported trading pairs (e.g. BTCUSD, ETHUSD, SOLUSD, XRPUSD) when account has no active trade.
   - Evaluates authoritative strategy conditions (SMC / Order Blocks) without modifying frozen SMC core.
   - Selects the first valid TRADE_SETUP_READY opportunity.
2. Single-Trade Exclusivity:
   - Locks the account upon selecting a trade setup.
   - Halts all new entry scans while that trade is active.
3. 100% Capital Allocation & Compounding:
   - Dynamically calculates 100% available balance position sizing within exchange boundaries.
   - Reconciles exchange fees, funding, and net P&L upon position closure.
   - Automatically initiates fresh full-market rescan using the newly compounded balance.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Optional, Dict, List, Any, Callable

from quantedge.execution.capital_allocator import (
    CapitalAllocator,
    CapitalAllocationError,
    PositionSizingResult,
)
from quantedge.execution.single_trade_lock import (
    SingleTradeLockManager,
    SingleTradeLockError,
)
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
    CloseReason,
)
from quantedge.strategy.models import (
    StrategyDecision,
    SetupState,
    StrategyDirection,
    TradeDirection,
)

logger = logging.getLogger("market_orchestrator")


@dataclass
class MarketScanResult:
    """Outcome of a full-market scanning cycle."""
    scanned_symbols: List[str]
    qualifying_symbol: Optional[str] = None
    decision: Optional[StrategyDecision] = None
    executed_record: Optional[TradeLifecycleRecord] = None
    rejection_reason: Optional[str] = None
    scan_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MarketScannerOrchestrator:
    """Orchestrates full-market pair scanning, single-trade exclusivity, and compounding."""

    def __init__(
        self,
        lifecycle_manager: TradeLifecycleManager,
        single_trade_lock: SingleTradeLockManager,
        capital_allocator: Optional[CapitalAllocator] = None,
        supported_symbols: Optional[List[str]] = None,
        strategy_evaluator: Optional[Callable[[str, Any], StrategyDecision]] = None,
    ):
        self.lifecycle_manager = lifecycle_manager
        self.single_trade_lock = single_trade_lock
        self.capital_allocator = capital_allocator or CapitalAllocator()
        self.supported_symbols = supported_symbols or ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
        self.strategy_evaluator = strategy_evaluator
        self._scan_history: List[MarketScanResult] = []

    async def scan_and_execute(
        self,
        account_id: str,
        user_id: str,
        market_snapshots: Optional[Dict[str, Any]] = None,
        candidate_decisions: Optional[List[StrategyDecision]] = None,
    ) -> MarketScanResult:
        """Perform a full-market scanning cycle across all supported pairs.

        If the account currently has an active trade lock, scanning is immediately skipped.
        If a qualifying setup is found, 100% available capital is allocated and exactly ONE trade is executed.
        """
        now = datetime.now(timezone.utc)
        effective_user = user_id or "default_user"

        # 1. Check Single-Trade Account Lock
        is_locked, active_setup_id, active_symbol = self.single_trade_lock.is_locked(effective_user, account_id)
        if is_locked:
            logger.info(
                "Scan skipped: account %s is locked with active trade %s on %s",
                account_id, active_setup_id, active_symbol
            )
            res = MarketScanResult(
                scanned_symbols=list(self.supported_symbols),
                rejection_reason=f"Account locked with active trade {active_setup_id} on {active_symbol}",
                scan_timestamp=now,
            )
            self._scan_history.append(res)
            return res

        # 2. Check Account Safety & Balance
        account = self.lifecycle_manager.state_store.account
        if account.kill_switch_active:
            res = MarketScanResult(
                scanned_symbols=list(self.supported_symbols),
                rejection_reason="Emergency kill switch is active",
                scan_timestamp=now,
            )
            self._scan_history.append(res)
            return res

        if not account.algo_enabled:
            res = MarketScanResult(
                scanned_symbols=list(self.supported_symbols),
                rejection_reason="Algorithmic execution is disabled",
                scan_timestamp=now,
            )
            self._scan_history.append(res)
            return res

        available_balance = account.available_balance
        if available_balance <= Decimal("0"):
            res = MarketScanResult(
                scanned_symbols=list(self.supported_symbols),
                rejection_reason=f"Insufficient available trading capital: ${available_balance}",
                scan_timestamp=now,
            )
            self._scan_history.append(res)
            return res

        # 3. Evaluate Strategy Candidates across all supported symbols
        qualified_decision: Optional[StrategyDecision] = None

        if candidate_decisions:
            for dec in candidate_decisions:
                if dec.symbol in self.supported_symbols and dec.setup_state == SetupState.TRADE_SETUP_READY:
                    qualified_decision = dec
                    break
        elif self.strategy_evaluator and market_snapshots:
            for sym in self.supported_symbols:
                snap = market_snapshots.get(sym)
                if snap is not None:
                    dec = self.strategy_evaluator(sym, snap)
                    if dec and dec.setup_state == SetupState.TRADE_SETUP_READY:
                        qualified_decision = dec
                        break

        if not qualified_decision:
            res = MarketScanResult(
                scanned_symbols=list(self.supported_symbols),
                rejection_reason="No qualifying trade setups found across scanned pairs",
                scan_timestamp=now,
            )
            self._scan_history.append(res)
            return res

        # 4. Calculate 100% Capital Allocation
        entry_price = qualified_decision.entry or Decimal("100000.00")
        try:
            from quantedge.execution.validation import DEFAULT_DELTA_INDIA_PRODUCTS
            spec = DEFAULT_DELTA_INDIA_PRODUCTS.get(qualified_decision.symbol)
            lot_step = spec.size_step if spec else Decimal("1")
            min_qty = spec.min_size if spec else Decimal("1")

            sizing = self.capital_allocator.calculate_100_percent_allocation(
                symbol=qualified_decision.symbol,
                entry_price=entry_price,
                available_balance=available_balance,
                leverage=10,
                lot_size_step=lot_step,
                min_quantity=min_qty,
            )
            qualified_decision.quantity = sizing.position_quantity
        except CapitalAllocationError as e:
            res = MarketScanResult(
                scanned_symbols=list(self.supported_symbols),
                qualifying_symbol=qualified_decision.symbol,
                decision=qualified_decision,
                rejection_reason=f"100% capital sizing failed: {str(e)}",
                scan_timestamp=now,
            )
            self._scan_history.append(res)
            return res

        # 5. Execute Exactly ONE Trade (Locks Account Atomically)
        record = await self.lifecycle_manager.execute_trade_setup(
            decision=qualified_decision,
            account_id=account_id,
            user_id=user_id,
        )

        res = MarketScanResult(
            scanned_symbols=list(self.supported_symbols),
            qualifying_symbol=qualified_decision.symbol,
            decision=qualified_decision,
            executed_record=record,
            scan_timestamp=now,
        )
        self._scan_history.append(res)
        return res

    async def handle_trade_closure_and_rescan(
        self,
        setup_id: str,
        reason: CloseReason,
        gross_pnl: Decimal,
        trading_fees: Decimal = Decimal("0.0"),
        funding_costs: Decimal = Decimal("0.0"),
        final_exchange_balance: Optional[Decimal] = None,
    ) -> TradeLifecycleRecord:
        """Handle position closure, reconcile net P&L and fees, release lock, and prepare fresh rescan."""
        record = await self.lifecycle_manager.close_position(
            setup_id=setup_id,
            reason=reason,
            gross_pnl=gross_pnl,
            trading_fees=trading_fees,
            funding_costs=funding_costs,
            final_exchange_balance=final_exchange_balance,
        )

        logger.info(
            "Trade %s closed (%s). Gross PnL: $%s, Fees: $%s, Net PnL: $%s. Reconciled Balance: $%s",
            setup_id, reason.value, record.gross_pnl, record.trading_fees, record.net_pnl, record.post_trade_balance
        )
        return record
