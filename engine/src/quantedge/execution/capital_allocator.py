"""
100% Capital Allocation & Compounding Position Sizer for QuantEdge AI.

Phase 5.8 Implementation:
1. Sizing Target: Allocates 100% of the account's currently available trading capital.
2. Safety & Exchange Constraints:
   - Configurable safety buffer (default 98.0%) to absorb minor price fluctuations and prevent immediate margin call rejections.
   - Respects contract lot sizes, step sizes, and minimum/maximum order quantities.
   - Respects Delta's initial margin requirements and maximum user leverage.
   - Strict fail-closed verification: never returns a position size requiring more margin than available.
3. Compounding & Net P&L Mathematics:
   - Compounds dynamically using the latest authoritative reconciled net balance after each completed trade.
   - Net Realized P&L = Gross Realized P&L - Trading Fees - Funding Costs - Exchange Taxes/Charges.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import logging
from typing import Optional, Tuple

logger = logging.getLogger("capital_allocator")


class CapitalAllocationError(Exception):
    """Raised when position sizing or capital allocation fails safety checks."""
    pass


@dataclass(frozen=True)
class PositionSizingResult:
    """Detailed result of 100% capital allocation calculation."""
    symbol: str
    entry_price: Decimal
    available_balance: Decimal
    leverage: int
    contract_unit: Decimal
    allocated_margin: Decimal
    position_quantity: Decimal
    notional_value: Decimal
    effective_leverage: Decimal
    safety_buffer_pct: Decimal


class CapitalAllocator:
    """Authoritative capital allocation and compounding engine."""

    def __init__(self, default_safety_buffer_pct: Decimal = Decimal("98.00")):
        """
        Args:
            default_safety_buffer_pct: Percentage of available margin to utilize (e.g. 98.0% leaves a 2.0% buffer for fees/slippage).
        """
        self.safety_buffer_pct = default_safety_buffer_pct

    def calculate_100_percent_allocation(
        self,
        symbol: str,
        entry_price: Decimal,
        available_balance: Decimal,
        leverage: int = 10,
        contract_unit: Decimal = Decimal("1.0"),
        lot_size_step: Decimal = Decimal("0.001"),
        min_quantity: Decimal = Decimal("0.001"),
        max_quantity: Optional[Decimal] = None,
        custom_safety_buffer: Optional[Decimal] = None,
    ) -> PositionSizingResult:
        """Calculate maximum contract quantity allocating 100% of available capital within exchange boundaries.

        Args:
            symbol: Trading pair symbol (e.g. 'BTCUSD').
            entry_price: Planned entry price.
            available_balance: Authoritative available/net margin balance.
            leverage: Configured leverage limit (1 to 100).
            contract_unit: Multiplier for contract value (e.g. 1.0 or 0.001).
            lot_size_step: Minimum quantity increment (e.g. 0.001 or 1).
            min_quantity: Exchange minimum order quantity.
            max_quantity: Exchange maximum order quantity (optional).
            custom_safety_buffer: Override safety buffer percentage (optional).

        Returns:
            PositionSizingResult with exact quantities, margin, and notional value.
        """
        if available_balance <= Decimal("0"):
            raise CapitalAllocationError(f"Cannot allocate capital with non-positive balance: {available_balance}")
        if entry_price <= Decimal("0"):
            raise CapitalAllocationError(f"Cannot calculate position size with non-positive price: {entry_price}")
        if leverage < 1 or leverage > 100:
            raise CapitalAllocationError(f"Invalid leverage: {leverage}. Must be between 1 and 100")
        if lot_size_step <= Decimal("0") or min_quantity <= Decimal("0"):
            raise CapitalAllocationError("Lot size step and minimum quantity must be positive")

        buffer_pct = custom_safety_buffer if custom_safety_buffer is not None else self.safety_buffer_pct
        if buffer_pct <= Decimal("0") or buffer_pct > Decimal("100.00"):
            raise CapitalAllocationError(f"Invalid safety buffer percentage: {buffer_pct}")

        # 1. Usable margin with safety buffer
        usable_margin = available_balance * (buffer_pct / Decimal("100.00"))
        
        # 2. Maximum allowable notional buying power
        max_notional = usable_margin * Decimal(str(leverage))

        # 3. Raw theoretical contract quantity
        raw_quantity = max_notional / (entry_price * contract_unit)

        # 4. Step down to exchange lot size increment (ROUND_DOWN to never exceed capital)
        num_steps = (raw_quantity / lot_size_step).quantize(Decimal("1"), rounding=ROUND_DOWN)
        stepped_quantity = num_steps * lot_size_step

        # 5. Boundary checks
        if stepped_quantity < min_quantity:
            raise CapitalAllocationError(
                f"Calculated quantity ({stepped_quantity}) is below exchange minimum ({min_quantity}). "
                f"Available balance (${available_balance}) is insufficient at current price (${entry_price})."
            )

        if max_quantity is not None and stepped_quantity > max_quantity:
            stepped_quantity = max_quantity

        # 6. Recompute actual notional value and exact required margin
        actual_notional = stepped_quantity * contract_unit * entry_price
        required_margin = (actual_notional / Decimal(str(leverage))).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

        # 7. Fail-closed assertion: required margin must never exceed available balance
        if required_margin > available_balance:
            raise CapitalAllocationError(
                f"Sizing anomaly: required margin ({required_margin}) exceeds available balance ({available_balance})"
            )

        effective_leverage = (actual_notional / available_balance).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return PositionSizingResult(
            symbol=symbol,
            entry_price=entry_price,
            available_balance=available_balance,
            leverage=leverage,
            contract_unit=contract_unit,
            allocated_margin=required_margin,
            position_quantity=stepped_quantity,
            notional_value=actual_notional,
            effective_leverage=effective_leverage,
            safety_buffer_pct=buffer_pct,
        )

    @staticmethod
    def calculate_net_pnl(
        gross_pnl: Decimal,
        trading_fees: Decimal = Decimal("0.0"),
        funding_costs: Decimal = Decimal("0.0"),
        taxes_and_charges: Decimal = Decimal("0.0"),
    ) -> Decimal:
        """Calculate authoritative net P&L after all exchange fees, funding, and taxes.

        Formula: Net P&L = Gross P&L - Fees - Funding - Taxes
        """
        total_costs = trading_fees + funding_costs + taxes_and_charges
        return gross_pnl - total_costs

    @staticmethod
    def calculate_compounded_balance(
        pre_trade_balance: Decimal,
        net_pnl: Decimal,
    ) -> Decimal:
        """Calculate new compounded account balance after trade closure."""
        new_balance = pre_trade_balance + net_pnl
        if new_balance < Decimal("0"):
            return Decimal("0.00")
        return new_balance.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
