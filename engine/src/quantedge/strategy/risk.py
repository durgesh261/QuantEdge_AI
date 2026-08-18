"""
Risk Calculator - Implements QuantEdge risk model.

Risk Model:
- Risk per trade = 35% of account balance
- Target reward = 60% of account balance
- Max leverage = 100x
- Account-level R:R = 60/35 ≈ 1.71

Position Sizing:
- positionSize = riskAmount / |entry - stopLoss|
- leverage = min(maxLeverage, positionSize * entryPrice / accountBalance)
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional
from quantedge.strategy.models import TradeDirection, AccountState, RiskValidationResult, StrategyConfig


@dataclass
class RiskCalculator:
    """Calculates position sizing, leverage, SL/TP per risk model."""

    config: StrategyConfig

    def validate_risk(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        account_balance: Decimal,
        account_equity: Decimal,
    ) -> RiskValidationResult:
        """
        Validate trade against risk rules and calculate max position size.

        Returns RiskValidationResult with position size and leverage.
        """
        # Basic validations
        if entry_price <= 0 or stop_loss <= 0:
            return RiskValidationResult(False, "Invalid price levels")

        if account_balance <= 0:
            return RiskValidationResult(False, "Insufficient account balance")

        # Calculate risk distance
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance == 0:
            return RiskValidationResult(False, "Zero risk distance (entry == stop loss)")

        # Risk amount = 35% of account balance
        risk_amount = account_balance * Decimal(str(self.config.risk_per_trade_pct / 100))

        # Position size = riskAmount / |entry - stopLoss|
        position_size = risk_amount / risk_distance

        # Minimum position size check
        min_position_size = Decimal("0.001")  # Adjust per symbol
        if position_size < min_position_size:
            return RiskValidationResult(False, f"Position size too small: {position_size}")

        # Calculate required leverage
        # Notional value = positionSize * entryPrice
        notional_value = position_size * entry_price
        required_leverage = (notional_value / account_balance).quantize(Decimal("1"), rounding=ROUND_DOWN)

        # Cap at max leverage
        max_leverage = min(int(required_leverage), self.config.max_leverage)

        if max_leverage < 1:
            max_leverage = 1

            # Recalculate position size with capped leverage
            max_notional = account_balance * max_leverage
            position_size = (max_notional / entry_price).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)

            # Recalculate actual risk with adjusted position size
            actual_risk = position_size * risk_distance
            if actual_risk > risk_amount:
                return RiskValidationResult(False, "Cannot achieve target risk with max leverage")

        return RiskValidationResult(
            is_valid=True,
            max_position_size=position_size,
            max_leverage=max_leverage,
        )

    def calculate_take_profit(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        direction: TradeDirection,
        account_balance: Decimal,
        risk_amount: Decimal,
    ) -> Decimal:
        """
        Calculate take profit price for target account growth.

        Target: 60% account growth
        rewardAmount = accountBalance * 60%
        priceMove = rewardAmount / positionSize
        """
        # Target reward = 60% of account balance
        target_reward = account_balance * Decimal(str(self.config.target_reward_pct / 100))

        # Position size from risk
        risk_distance = abs(entry_price - stop_loss)
        position_size = risk_amount / risk_distance

        if position_size == 0:
            raise ValueError("Position size is zero")

        # Price movement needed
        price_move = target_reward / position_size

        if direction == TradeDirection.LONG:
            return entry_price + price_move
        else:
            return entry_price - price_move

    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        direction: TradeDirection,
        order_block_top: Decimal,
        order_block_bottom: Decimal,
    ) -> Decimal:
        """
        Calculate stop loss per strategy spec.

        Bullish: SL = lower OB boundary
        Bearish: SL = upper OB boundary
        """
        if direction == TradeDirection.LONG:
            return order_block_bottom
        else:
            return order_block_top

    def calculate_account_rr(self) -> Decimal:
        """Calculate account-level risk/reward ratio."""
        risk = Decimal(str(self.config.risk_per_trade_pct))
        reward = Decimal(str(self.config.target_reward_pct))
        return reward / risk
