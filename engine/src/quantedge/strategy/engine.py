"""
QuantEdge Strategy Engine.

Orchestrates SMC analysis, confidence scoring, and trade setup generation.
Implements the complete strategy logic per specification.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
import logging

from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.analyzer import SMCAnalyzer, SMCAnalyzerConfig
from quantedge.smc.models import MarketStructureState, OrderBlock, TrendDirection
from quantedge.strategy.models import (
    TradeSetup, StrategyConfig, StrategySignal, TradeDirection,
    ConfidenceFactors, AccountState, RiskValidationResult
)
from quantedge.strategy.confidence import ConfidenceScorer
from quantedge.strategy.risk import RiskCalculator

logger = logging.getLogger(__name__)


@dataclass
class StrategyEngineConfig:
    strategy: StrategyConfig
    smc: SMCAnalyzerConfig


class StrategyEngine:
    """
    Main Strategy Engine.

    Pipeline:
    1. SMC Analysis -> MarketStructureState
    2. Filter Order Blocks -> Eligible OBs
    3. Score Confidence -> ConfidenceFactors
    4. Validate Risk -> TradeSetup
    5. Rank Candidates -> Select Best
    """

    def __init__(self, config: StrategyEngineConfig):
        self.config = config
        self.smc_analyzer = SMCAnalyzer(config.smc)
        self.confidence_scorer = ConfidenceScorer(config.strategy)
        self.risk_calculator = RiskCalculator(config.strategy)

    def scan_symbol(
        self,
        candles: list[Candle],
        symbol: str,
        account_state: AccountState,
    ) -> list[TradeSetup]:
        """
        Scan a single symbol for trade setups.

        Returns list of valid trade setups (may be empty).
        """
        timeframe = Timeframe(self.config.strategy.timeframe)

        # 1. SMC Analysis
        state = self.smc_analyzer.analyze(candles, symbol, timeframe)

        # 2. Get active order blocks
        active_obs = state.get_active_order_blocks()

        # 3. Filter and score each OB
        setups = []
        for ob in active_obs:
            setup = self._evaluate_order_block(ob, state, account_state, symbol, timeframe)
            if setup and setup.signal == StrategySignal.VALID:
                setups.append(setup)

        # 4. Rank by confidence
        setups.sort(key=lambda s: s.confidence.total, reverse=True)

        return setups

    def scan_all_symbols(
        self,
        candles_by_symbol: dict[str, list[Candle]],
        account_state: AccountState,
    ) -> list[TradeSetup]:
        """
        Scan all configured symbols and return ranked candidates.

        Implements: evaluate all -> apply hard filters -> discard <85 -> rank -> select highest
        """
        all_setups = []

        for symbol in self.config.strategy.symbols:
            if symbol not in candles_by_symbol:
                logger.warning(f"No candle data for {symbol}")
                continue

            setups = self.scan_symbol(candles_by_symbol[symbol], symbol, account_state)
            all_setups.extend(setups)

        # Filter by confidence threshold
        qualified = [s for s in all_setups if s.confidence.meets_threshold(self.config.strategy.confidence_threshold)]

        # Sort by confidence (highest first)
        qualified.sort(key=lambda s: s.confidence.total, reverse=True)

        return qualified

    def select_best_candidate(
        self,
        candidates: list[TradeSetup],
        has_active_trade: bool,
    ) -> Optional[TradeSetup]:
        """
        Select the best candidate per strategy rules.

        Rules:
        - If has_active_trade: return None (one active trade rule)
        - Otherwise: return highest confidence candidate
        """
        if has_active_trade:
            logger.info("Active trade exists - blocking new entry (one trade rule)")
            return None

        if not candidates:
            return None

        return candidates[0]

    def _evaluate_order_block(
        self,
        ob: OrderBlock,
        state: MarketStructureState,
        account_state: AccountState,
        symbol: str,
        timeframe: Timeframe,
    ) -> Optional[TradeSetup]:
        """Evaluate a single order block for trade setup."""

        # Hard Filter 1: OB must not be invalidated
        if ob.is_invalidated:
            return TradeSetup(
                symbol=symbol, timeframe=timeframe.value,
                direction=TradeDirection.LONG if ob.is_bullish() else TradeDirection.SHORT,
                order_block=ob, entry_price=Decimal("0"), stop_loss=Decimal("0"),
                take_profit=Decimal("0"), position_size=Decimal("0"), leverage=0,
                risk_amount=Decimal("0"), reward_amount=Decimal("0"),
                risk_reward_ratio=Decimal("0"), confidence=ConfidenceFactors(),
                market_regime="INVALID_OB", signal=StrategySignal.INVALID_OB,
                timestamp=datetime.now()
            )

        # Hard Filter 2: OB must not be used
        if ob.is_used:
            return self._reject_setup(ob, symbol, timeframe, StrategySignal.OB_USED, "OB already used")

        # Hard Filter 3: First touch only (touch_count must be 0)
        if ob.touch_count >= 1:
            return self._reject_setup(ob, symbol, timeframe, StrategySignal.NOT_FIRST_TOUCH, "Not first touch")

        # Hard Filter 4: Market regime - reject ranging
        if state.swing_trend == TrendDirection.RANGING or state.internal_trend == TrendDirection.RANGING:
            # Check for conflicting structure
            if state.swing_trend != state.internal_trend:
                return self._reject_setup(ob, symbol, timeframe, StrategySignal.RANGING_MARKET, "Conflicting structure (ranging)")

        # Hard Filter 5: Opposing zone check
        if self._has_opposing_zone(ob, state):
            return self._reject_setup(ob, symbol, timeframe, StrategySignal.OPPOSING_ZONE, "Opposing zone nearby")

        # Calculate entry, SL, TP
        entry_price = ob.calculate_entry_price()
        stop_loss = ob.calculate_stop_loss()

        # Validate entry/SL distance
        if entry_price == stop_loss:
            return self._reject_setup(ob, symbol, timeframe, StrategySignal.RISK_REJECTION, "Zero distance entry/SL")

        # Calculate position sizing
        risk_validation = self.risk_calculator.validate_risk(
            entry_price=entry_price,
            stop_loss=stop_loss,
            account_balance=account_state.balance,
            account_equity=account_state.equity,
        )

        if not risk_validation.is_valid:
            return self._reject_setup(ob, symbol, timeframe, StrategySignal.RISK_REJECTION, risk_validation.rejection_reason or "Risk validation failed")

        # Calculate take profit
        take_profit = self.risk_calculator.calculate_take_profit(
            entry_price=entry_price,
            stop_loss=stop_loss,
            direction=TradeDirection.LONG if ob.is_bullish() else TradeDirection.SHORT,
            account_balance=account_state.balance,
            risk_amount=risk_validation.max_position_size * abs(entry_price - stop_loss),
        )

        # Score confidence
        confidence = self.confidence_scorer.score(ob, state, account_state.balance)

        # Hard Filter 6: Confidence threshold
        if not confidence.meets_threshold(self.config.strategy.confidence_threshold):
            return self._reject_setup(ob, symbol, timeframe, StrategySignal.LOW_CONFIDENCE, f"Confidence {confidence.total} < {self.config.strategy.confidence_threshold}")

        # Determine market regime
        market_regime = self._determine_regime(state)

        # Build valid setup
        direction = TradeDirection.LONG if ob.is_bullish() else TradeDirection.SHORT

        return TradeSetup(
            symbol=symbol,
            timeframe=timeframe.value,
            direction=direction,
            order_block=ob,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=risk_validation.max_position_size,
            leverage=risk_validation.max_leverage or self.config.strategy.max_leverage,
            risk_amount=risk_validation.max_position_size * abs(entry_price - stop_loss),
            reward_amount=risk_validation.max_position_size * abs(take_profit - entry_price),
            risk_reward_ratio=abs(take_profit - entry_price) / abs(entry_price - stop_loss),
            confidence=confidence,
            market_regime=market_regime,
            signal=StrategySignal.VALID,
            timestamp=datetime.now(),
        )

    def _reject_setup(
        self,
        ob: OrderBlock,
        symbol: str,
        timeframe: Timeframe,
        signal: StrategySignal,
        reason: str,
    ) -> TradeSetup:
        """Create a rejected trade setup for logging/analysis."""
        logger.debug(f"Rejected {symbol} {ob.type} OB: {reason}")
        return TradeSetup(
            symbol=symbol,
            timeframe=timeframe.value,
            direction=TradeDirection.LONG if ob.is_bullish() else TradeDirection.SHORT,
            order_block=ob,
            entry_price=Decimal("0"),
            stop_loss=Decimal("0"),
            take_profit=Decimal("0"),
            position_size=Decimal("0"),
            leverage=0,
            risk_amount=Decimal("0"),
            reward_amount=Decimal("0"),
            risk_reward_ratio=Decimal("0"),
            confidence=ConfidenceFactors(),
            market_regime=signal.value,
            signal=signal,
            timestamp=datetime.now(),
        )

    def _has_opposing_zone(self, ob: OrderBlock, state: MarketStructureState) -> bool:
        """
        Check for opposing supply/demand zone within threshold.

        Threshold: ~0.5% proximity per strategy spec.
        """
        threshold = Decimal(str(self.config.strategy.opposing_zone_threshold_pct / 100))
        ob_mid = ob.midline

        for other_ob in state.order_blocks:
            if other_ob == ob or other_ob.is_invalidated or other_ob.is_used:
                continue

            # Opposing type
            if ob.is_bullish() and other_ob.is_bearish():
                other_mid = other_ob.midline
                distance_pct = abs(ob_mid - other_mid) / ob_mid
                if distance_pct <= threshold:
                    return True
            elif ob.is_bearish() and other_ob.is_bullish():
                other_mid = other_ob.midline
                distance_pct = abs(ob_mid - other_mid) / ob_mid
                if distance_pct <= threshold:
                    return True

        return False

    def _determine_regime(self, state: MarketStructureState) -> str:
        """Determine market regime from structure."""
        swing = state.swing_trend
        internal = state.internal_trend

        if swing == TrendDirection.RANGING or internal == TrendDirection.RANGING:
            return "RANGING"

        if swing == internal:
            return f"{swing.value.upper()}_TRENDING"

        return f"CONFLICTING_{swing.value.upper()}_{internal.value.upper()}"
