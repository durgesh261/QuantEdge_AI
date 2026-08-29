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
        # Delta sizes an order in whole contracts. Its REST reference types
        # `size` as an unquoted integer ("Integer numbers (like contract size,
        # product_id and impact size) are unquoted"), its order-tool reference
        # types `size` as `int`, "order size in contracts (positive)", and its
        # India user guide defines an order as "an order to buy or sell a
        # specified number of futures contracts". No product on Delta accepts a
        # fractional contract count, and no size-side increment field exists in
        # the product payload -- so the grid is 1 contract for every product,
        # and the floor is the smallest positive integer.
        #
        # These defaults used to be 0.001, which is a BASE-ASSET-shaped grid.
        # That only made sense alongside the old `contract_unit=1.0` default:
        # once `contract_unit` is the verified `contract_value`, `raw_quantity`
        # is a contract count and a 0.001 grid produces fractional contracts
        # (e.g. 127.272 BTCUSD) that Delta cannot accept. `min_quantity` moves
        # for the same reason: below one contract there is no order.
        #
        # The parameters stay caller-overridable -- the stepping arithmetic is
        # general -- but the default is now the documented exchange rule instead
        # of a fractional grid no product has.
        lot_size_step: Decimal = Decimal("1"),
        min_quantity: Decimal = Decimal("1"),
        max_quantity: Optional[Decimal] = None,
        custom_safety_buffer: Optional[Decimal] = None,
    ) -> PositionSizingResult:
        """Calculate maximum contract quantity allocating 100% of available capital within exchange boundaries.

        Args:
            symbol: Trading pair symbol (e.g. 'BTCUSD').
            entry_price: Planned entry price.
            available_balance: Authoritative available/net margin balance.
            leverage: Configured leverage limit (1 to 100).
            contract_unit: Base-asset amount of ONE contract (`contract_value`).
            lot_size_step: Quantity increment; defaults to 1 whole contract.
            min_quantity: Minimum order quantity; defaults to 1 whole contract.
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
        if contract_unit <= Decimal("0"):
            # `contract_unit` is the divisor in step 3 and the multiplier in
            # step 6. Zero raised `decimal.DivisionByZero` straight out of this
            # method -- `market_orchestrator` catches only
            # `CapitalAllocationError`, so that escaped its scan loop while
            # `multi_user_orchestrator`'s broad handler absorbed it; the same
            # bad input failed closed in one caller and crashed the other. A
            # negative unit was worse: it produced a negative `raw_quantity`
            # that surfaced as the unrelated "below exchange minimum" message.
            # Raising here makes both callers fail closed on the same error
            # class with an accurate reason. No positive unit is affected, so
            # no accepted sizing result changes.
            raise CapitalAllocationError(
                f"Contract unit must be positive, got {contract_unit}")

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

    @staticmethod
    def calculate_leverage_from_stop_distance(
        entry_price: Decimal,
        stop_loss_price: Decimal,
        max_loss_pct: Decimal = Decimal("35.0"),
        max_leverage_cap: int = 100,
    ) -> int:
        """Calculate dynamic leverage such that maximum planned loss at SL is <= max_loss_pct of allocated margin.

        Formula:
            stopDistanceFraction = abs(entry - SL) / entry
            requiredLeverage = max_loss_pct / (stopDistanceFraction * 100) = 0.35 / stopDistanceFraction
            leverage = int(requiredLeverage)
        """
        if entry_price <= Decimal("0") or stop_loss_price <= Decimal("0"):
            raise CapitalAllocationError("Entry price and stop loss price must be positive")

        stop_dist_fraction = abs(entry_price - stop_loss_price) / entry_price
        if stop_dist_fraction <= Decimal("0"):
            raise CapitalAllocationError("Stop loss cannot equal entry price")

        stop_dist_pct = stop_dist_fraction * Decimal("100")
        raw_leverage = max_loss_pct / stop_dist_pct
        calculated_leverage = max(1, int(raw_leverage))

        if calculated_leverage > max_leverage_cap:
            raise CapitalAllocationError(
                f"Calculated leverage {calculated_leverage}x exceeds maximum allowed cap of {max_leverage_cap}x"
            )
        return calculated_leverage

    @staticmethod
    def calculate_roe_take_profit(
        entry_price: Decimal,
        direction: str,
        leverage: int,
        target_roe_pct: Decimal = Decimal("60.0"),
        tick_size: Decimal = Decimal("0.50"),
    ) -> Decimal:
        """Convert target return on margin (ROE) into the required underlying Take Profit price.

        Formula:
            price_movement_fraction = (target_roe_pct / 100) / leverage
            LONG:  TP = entry * (1 + price_movement_fraction)
            SHORT: TP = entry * (1 - price_movement_fraction)
        """
        if entry_price <= Decimal("0"):
            raise CapitalAllocationError("Entry price must be positive")
        if leverage < 1:
            raise CapitalAllocationError("Leverage must be at least 1x")

        price_move_fraction = (target_roe_pct / Decimal("100")) / Decimal(str(leverage))
        is_long = direction.upper() in ("LONG", "BUY")

        if is_long:
            raw_tp = entry_price * (Decimal("1") + price_move_fraction)
        else:
            raw_tp = entry_price * (Decimal("1") - price_move_fraction)

        return (raw_tp / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_size
