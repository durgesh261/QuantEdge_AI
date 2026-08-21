"""
QuantEdge Trading Strategy Models.

Implements the 9-factor confidence model and strategy logic per specification.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from quantedge.smc.models import OrderBlock, MarketStructureState, TrendDirection, OBState


class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class StrategyDirection(str, Enum):
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


class SetupType(str, Enum):
    BULLISH_OB_RETEST = "BULLISH_OB_RETEST"
    BEARISH_OB_RETEST = "BEARISH_OB_RETEST"
    NONE = "NONE"


class SetupState(str, Enum):
    """
    Trade setup lifecycle states per Phase 4.2 specification.

    - NO_SETUP: No valid OB or setup currently qualifies.
    - WATCHING_OB: Valid active OB exists but current price is outside its zone.
    - OB_ENGAGED: Current closed price is inside a valid active OB, but confirmation is incomplete.
    - QUALIFIED_LONG: Bullish OB + price inside OB + required bullish confirmation satisfied.
    - QUALIFIED_SHORT: Bearish OB + price inside OB + required bearish confirmation satisfied.
    - TRADE_SETUP_READY: Qualified setup with valid geometry and risk_reward >= minimum_risk_reward.
    """
    NO_SETUP = "NO_SETUP"
    WATCHING_OB = "WATCHING_OB"
    OB_ENGAGED = "OB_ENGAGED"
    QUALIFIED_LONG = "QUALIFIED_LONG"
    QUALIFIED_SHORT = "QUALIFIED_SHORT"
    TRADE_SETUP_READY = "TRADE_SETUP_READY"


@dataclass(frozen=True)
class RiskRewardConfig:
    """Configurable risk/reward validation parameters."""
    minimum_risk_reward: Decimal = Decimal("2.0")
    reward_multiple: Decimal = Decimal("2.0")

    def __post_init__(self):
        min_rr = self.minimum_risk_reward if isinstance(self.minimum_risk_reward, Decimal) else Decimal(str(self.minimum_risk_reward))
        rew_mult = self.reward_multiple if isinstance(self.reward_multiple, Decimal) else Decimal(str(self.reward_multiple))
        if min_rr <= Decimal("0"):
            raise ValueError("minimum_risk_reward must be > 0")
        if rew_mult <= Decimal("0"):
            raise ValueError("reward_multiple must be > 0")
        object.__setattr__(self, "minimum_risk_reward", min_rr)
        object.__setattr__(self, "reward_multiple", rew_mult)


@dataclass
class StrategyDecision:
    """
    Deterministic Strategy Decision generated from SMC state and candle price action.
    
    Adheres strictly to the Phase 4.2 specification:
    - SetupState: NO_SETUP, WATCHING_OB, OB_ENGAGED, QUALIFIED_LONG, QUALIFIED_SHORT, TRADE_SETUP_READY
    - Direction: NONE, LONG, or SHORT (default NONE)
    - Deterministic setup_id traceability
    - Retains authoritative UTC timestamp internally
    - Computes user-facing Asia/Kolkata display timestamp dynamically
    - Captures factual reasons derived directly from SMC state
    - Validated entry, stop_loss, take_profit, risk_distance, reward_distance, risk_reward
    - UI-ready properties (ob_zone, ob_formation_ts, ob_age_days, etc.)
    - Contains strictly no order execution or private exchange logic
    """
    timestamp: datetime
    symbol: str
    timeframe: str
    direction: StrategyDirection = StrategyDirection.NONE
    setup_state: SetupState = SetupState.NO_SETUP
    setup_id: Optional[str] = None
    setup_type: Optional[str] = None
    entry: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    risk_distance: Optional[Decimal] = None
    reward_distance: Optional[Decimal] = None
    risk_reward: Optional[Decimal] = None
    minimum_risk_reward: Optional[Decimal] = None
    confidence: Optional[float] = None
    reasons: list[str] = field(default_factory=list)
    order_block: Optional[OrderBlock] = None
    candle: Optional[object] = None

    @property
    def timestamp_ist(self) -> str:
        """User-facing timestamp formatted in Asia/Kolkata (UTC+05:30)."""
        from quantedge.utils.timezone import format_ist
        return format_ist(self.timestamp)

    @property
    def is_signal(self) -> bool:
        return self.direction in (StrategyDirection.LONG, StrategyDirection.SHORT)

    @property
    def is_long(self) -> bool:
        return self.direction == StrategyDirection.LONG

    @property
    def is_short(self) -> bool:
        return self.direction == StrategyDirection.SHORT

    @property
    def is_trade_setup_ready(self) -> bool:
        return self.setup_state == SetupState.TRADE_SETUP_READY

    @property
    def trade_setup_ready(self) -> bool:
        return self.is_trade_setup_ready

    @property
    def is_qualified(self) -> bool:
        return self.setup_state in (SetupState.QUALIFIED_LONG, SetupState.QUALIFIED_SHORT, SetupState.TRADE_SETUP_READY)

    @property
    def is_engaged(self) -> bool:
        return self.setup_state in (SetupState.OB_ENGAGED, SetupState.QUALIFIED_LONG, SetupState.QUALIFIED_SHORT, SetupState.TRADE_SETUP_READY)

    @property
    def is_watching(self) -> bool:
        return self.setup_state == SetupState.WATCHING_OB

    @property
    def ob_zone(self) -> Optional[tuple[Decimal, Decimal]]:
        if self.order_block is not None:
            return (self.order_block.bottom_price, self.order_block.top_price)
        return None

    @property
    def ob_formation_ts(self) -> Optional[datetime]:
        if self.order_block is not None and self.order_block.formation_candle is not None:
            return self.order_block.formation_candle.timestamp
        return None

    @property
    def ob_age_days(self) -> Optional[float]:
        if self.ob_formation_ts is not None:
            return (self.timestamp - self.ob_formation_ts).total_seconds() / 86400.0
        return None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "timestamp_ist": self.timestamp_ist,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "setup_state": self.setup_state.value,
            "setup_id": self.setup_id,
            "setup_type": self.setup_type,
            "trade_setup_ready": self.trade_setup_ready,
            "entry": str(self.entry) if self.entry is not None else None,
            "stop_loss": str(self.stop_loss) if self.stop_loss is not None else None,
            "take_profit": str(self.take_profit) if self.take_profit is not None else None,
            "risk_distance": str(self.risk_distance) if self.risk_distance is not None else None,
            "reward_distance": str(self.reward_distance) if self.reward_distance is not None else None,
            "risk_reward": str(self.risk_reward) if self.risk_reward is not None else None,
            "minimum_risk_reward": str(self.minimum_risk_reward) if self.minimum_risk_reward is not None else None,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "ob_id": self.order_block.index if self.order_block is not None else None,
            "ob_zone": [str(p) for p in self.ob_zone] if self.ob_zone is not None else None,
            "ob_age_days": round(self.ob_age_days, 2) if self.ob_age_days is not None else None,
        }


def generate_setup_id(symbol: str, timeframe: str, ob: OrderBlock, direction: StrategyDirection) -> str:
    """Generate a deterministic setup identifier traceable to symbol, timeframe, OB, and direction."""
    ts_str = ob.formation_candle.timestamp.strftime("%Y%m%d%H%M%S") if (ob.formation_candle and hasattr(ob.formation_candle, 'timestamp')) else f"idx{ob.index}"
    return f"{symbol}_{timeframe}_OB{ob.index}_{ts_str}_{direction.value}"


class StrategySignal(str, Enum):
    VALID = "VALID"
    INVALID_OB = "INVALID_OB"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"  # OB not in FRESH/TOUCHED state
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
    ob_state: int = 0             # 15 max (FRESH=15, TOUCHED=10, USED/INVALIDATED=0)
    bos_choch: int = 0            # 15 max
    liquidity_sweep: int = 0      # 10 max
    premium_discount: int = 0     # 10 max
    session_volatility: int = 0   # 5 max
    risk_reward: int = 0          # 10 max
    news_macro_safety: int = 0    # 5 max

    @property
    def total(self) -> int:
        return (
            self.trend_alignment + self.ob_state + self.bos_choch +
            self.liquidity_sweep + self.premium_discount +
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