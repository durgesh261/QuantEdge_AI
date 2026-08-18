"""
QuantEdge Trading Strategy Models.

Implements the 9-factor confidence model and strategy logic per specification.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from quantedge.smc.models import OrderBlock, MarketStructureState, TrendDirection


class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class StrategySignal(str, Enum):
    VALID = "VALID"
    INVALID_OB = "INVALID_OB"
    NOT_FIRST_TOUCH = "NOT_FIRST_TOUCH"
    OB_USED = "OB_USED"
    RANGING_MARKET = "RANGING_MARKET"
    OPPOSING_ZONE = "OPPOSING_ZONE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    RISK_REJECTION = "RISK_REJECTION"
    ONE_TRADE_ACTIVE = "ONE_TRADE_ACTIVE"


@dataclass(frozen=True)
class ConfidenceFactors:
    """Nine-factor confidence scoring (total = 100)."""
    trend_alignment: int = 0      # 15 max
    ob_freshness: int = 0         # 15 max
    first_touch: int = 0          # 15 max
    bos_choch: int = 0            # 15 max
    liquidity_sweep: int = 0      # 10 max
    premium_discount: int = 0     # 10 max
    session_volatility: int = 0   # 5 max
    risk_reward: int = 0          # 10 max
    news_macro_safety: int = 0    # 5 max

    @property
    def total(self) -> int:
        return (
            self.trend_alignment + self.ob_freshness + self.first_touch +
            self.bos_choch + self.liquidity_sweep + self.premium_discount +
            self.session_volatility + self.risk_reward + self.news_macro_safety
        )

    def meets_threshold(self, threshold: int = 85) -> bool:
        return self.total >= threshold


@dataclass(frozen=True)
class TradeSetup:
    """Complete trade setup with all calculated parameters."""
    symbol: str
    timeframe: str
    direction: TradeDirection
    order_block: OrderBlock
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    position_size: Decimal
    leverage: int
    risk_amount: Decimal
    reward_amount: Decimal
    risk_reward_ratio: Decimal
    confidence: ConfidenceFactors
    market_regime: str
    signal: StrategySignal
    timestamp: datetime


@dataclass
class StrategyConfig:
    """Strategy configuration parameters."""
    confidence_threshold: int = 85
    timeframe: str = "1h"
    symbols: list[str] = field(default_factory=lambda: ["BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P"])
    risk_per_trade_pct: float = 35.0
    target_reward_pct: float = 60.0
    max_leverage: int = 100
    ob_width_threshold_pct: float = 0.6
    opposing_zone_threshold_pct: float = 0.5
    atr_period: int = 200
    atr_multiplier: float = 2.0
    internal_length: int = 5
    swing_length: int = 50


@dataclass
class AccountState:
    """Account state for risk calculations."""
    balance: Decimal
    equity: Decimal
    free_margin: Decimal
    used_margin: Decimal
    open_positions: int = 0
    daily_pnl: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")


@dataclass(frozen=True)
class RiskValidationResult:
    """Result of risk validation."""
    is_valid: bool
    rejection_reason: Optional[str] = None
    max_position_size: Optional[Decimal] = None
    max_leverage: Optional[int] = None