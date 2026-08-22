"""
QuantEdge Strategy Engine.

Orchestrates SMC analysis, confidence scoring, and trade setup generation.
Implements the complete strategy logic per specification.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, Union, List, Any
import logging

from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.analyzer import SMCAnalyzer, SMCAnalyzerConfig
from quantedge.smc.models import (
    MarketStructureState, OrderBlock, TrendDirection, OBState, StructureBreak
)
from quantedge.strategy.models import (
    TradeSetup, StrategyConfig, StrategySignal, TradeDirection,
    ConfidenceFactors, AccountState, RiskValidationResult,
    StrategyDecision, StrategyDirection, SetupType, SetupState,
    RiskRewardConfig, generate_setup_id
)
from quantedge.strategy.confidence import ConfidenceScorer
from quantedge.strategy.risk import RiskCalculator

logger = logging.getLogger(__name__)


@dataclass
class StrategyEngineConfig:
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    smc: SMCAnalyzerConfig = field(default_factory=SMCAnalyzerConfig)
    risk_reward: RiskRewardConfig = field(default_factory=RiskRewardConfig)


class StrategyEngine:
    """
    Main Strategy Engine.

    Supports:
    1. Phase 4.2 Deterministic Risk/Reward & Trade Setup Validation (evaluate_candle / evaluate_state)
    2. Multi-symbol candidate scanning and ranking
    """

    def __init__(
        self,
        config: Optional[Union[StrategyEngineConfig, StrategyConfig, RiskRewardConfig]] = None,
        risk_reward_config: Optional[RiskRewardConfig] = None,
    ):
        if config is None:
            self.config = StrategyEngineConfig(risk_reward=risk_reward_config or RiskRewardConfig())
        elif isinstance(config, StrategyConfig):
            self.config = StrategyEngineConfig(strategy=config, risk_reward=risk_reward_config or RiskRewardConfig())
        elif isinstance(config, RiskRewardConfig):
            self.config = StrategyEngineConfig(risk_reward=config)
        else:
            self.config = config
            if risk_reward_config is not None:
                self.config.risk_reward = risk_reward_config
        self.smc_analyzer = SMCAnalyzer(self.config.smc)
        self.confidence_scorer = ConfidenceScorer(self.config.strategy)
        self.risk_calculator = RiskCalculator(self.config.strategy)

    def evaluate_candle(
        self,
        candle: Candle,
        smc_engine: Any,
        risk_reward_config: Optional[RiskRewardConfig] = None,
    ) -> StrategyDecision:
        """
        Evaluate a single closed candle against the current IncrementalSMCEngine state.

        Reads existing SMC state in a strictly read-only, non-mutating manner:
        1. Queries all active order blocks and order blocks engaged at current closed candle price.
        2. Retrieves trend and structure break context.
        3. Evaluates deterministic Phase 4.2 risk/reward & trade setup ready rules.
        """
        all_active_obs = smc_engine.get_active_obs()
        engaged_obs = smc_engine.get_active_obs_at_price(candle.close)
        internal_trend = smc_engine._internal_detector.get_current_trend()
        swing_trend = smc_engine._swing_detector.get_current_trend()
        recent_breaks = smc_engine.get_recent_breaks(lookback=10)

        return self.evaluate_state(
            candle=candle,
            active_obs=engaged_obs,
            internal_trend=internal_trend,
            swing_trend=swing_trend,
            recent_breaks=recent_breaks,
            all_active_obs=all_active_obs,
            risk_reward_config=risk_reward_config,
        )

    def evaluate_state(
        self,
        candle: Candle,
        active_obs: list[OrderBlock],
        internal_trend: TrendDirection,
        swing_trend: TrendDirection,
        recent_breaks: Optional[list[StructureBreak]] = None,
        all_active_obs: Optional[list[OrderBlock]] = None,
        risk_reward_config: Optional[RiskRewardConfig] = None,
    ) -> StrategyDecision:
        """
        Evaluate a closed candle against explicit SMC structure state.

        Setup States:
        - NO_SETUP: No valid active OB in pool.
        - WATCHING_OB: Valid active OB exists but price is outside.
        - OB_ENGAGED: Price inside valid active OB, but confirmation is incomplete.
        - QUALIFIED_LONG: Bullish OB + price inside OB + bullish confirmation (RR < min).
        - QUALIFIED_SHORT: Bearish OB + price inside OB + bearish confirmation (RR < min).
        - TRADE_SETUP_READY: Qualified setup with positive risk/reward >= minimum_risk_reward.
        """
        symbol = candle.symbol
        timeframe = candle.timeframe.value if hasattr(candle.timeframe, "value") else str(candle.timeframe)
        timestamp = candle.timestamp
        rr_cfg = risk_reward_config or getattr(self.config, "risk_reward", RiskRewardConfig())

        # Pool of all active OBs available in the engine
        pool = all_active_obs if all_active_obs is not None else active_obs
        valid_pool = [ob for ob in pool if ob.is_eligible_for_entry() and not ob.is_invalidated() and not ob.is_used()]

        if not valid_pool:
            return StrategyDecision(
                timestamp=timestamp,
                symbol=symbol,
                timeframe=timeframe,
                direction=StrategyDirection.NONE,
                setup_state=SetupState.NO_SETUP,
                setup_type=None,
                reasons=["Price outside any active order block", "No active order blocks in pool"],
                candle=candle,
            )

        # Filter engaged OBs (price inside OB boundary)
        engaged = [ob for ob in active_obs if ob.is_eligible_for_entry() and not ob.is_invalidated() and not ob.is_used() and ob.contains_price(candle.close)]

        if not engaged:
            return StrategyDecision(
                timestamp=timestamp,
                symbol=symbol,
                timeframe=timeframe,
                direction=StrategyDirection.NONE,
                setup_state=SetupState.WATCHING_OB,
                setup_type=None,
                reasons=[f"Watching {len(valid_pool)} active order block(s); price {candle.close} outside zones", "Price outside any active order block"],
                candle=candle,
            )

        breaks = recent_breaks or []

        # Sort engaged OBs deterministically (Step 17)
        def _ob_priority_key(o: OrderBlock):
            matches_internal = (o.is_bullish() and internal_trend == TrendDirection.BULLISH) or (o.is_bearish() and internal_trend == TrendDirection.BEARISH)
            matches_swing = (o.is_bullish() and swing_trend == TrendDirection.BULLISH) or (o.is_bearish() and swing_trend == TrendDirection.BEARISH)
            matches_trend = 1 if (matches_internal or matches_swing) else 0
            conf = o.confidence_score or 0
            w = float(o.width)
            formation_idx = o.formation_index
            return (-matches_trend, -conf, w, -formation_idx)

        sorted_engaged = sorted(engaged, key=_ob_priority_key)

        for ob in sorted_engaged:
            if ob.is_bullish():
                has_bullish_trend = (internal_trend == TrendDirection.BULLISH or swing_trend == TrendDirection.BULLISH)
                has_bullish_break = any(b.direction == TrendDirection.BULLISH for b in breaks[-3:]) if breaks else False
                setup_id = generate_setup_id(symbol, timeframe, ob, StrategyDirection.LONG)

                if has_bullish_trend or has_bullish_break:
                    entry = ob.calculate_entry_price()
                    stop_loss = ob.calculate_stop_loss()

                    if entry is None or stop_loss is None:
                        return StrategyDecision(
                            timestamp=timestamp,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=StrategyDirection.LONG,
                            setup_state=SetupState.QUALIFIED_LONG,
                            setup_id=setup_id,
                            setup_type=SetupType.BULLISH_OB_RETEST.value,
                            entry=entry,
                            stop_loss=stop_loss,
                            confidence=float(ob.confidence_score) if ob.confidence_score else None,
                            reasons=[
                                "valid bullish order block",
                                "active bullish order block",
                                f"price {candle.close} entered bullish order block zone [{ob.bottom_price}, {ob.top_price}]",
                                "bullish structure confirmation present",
                                "entry or stop loss could not be calculated",
                            ],
                            order_block=ob,
                            candle=candle,
                        )

                    # Price geometry validation for LONG: entry > stop_loss
                    if entry <= stop_loss:
                        return StrategyDecision(
                            timestamp=timestamp,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=StrategyDirection.LONG,
                            setup_state=SetupState.QUALIFIED_LONG,
                            setup_id=setup_id,
                            setup_type=SetupType.BULLISH_OB_RETEST.value,
                            entry=entry,
                            stop_loss=stop_loss,
                            confidence=float(ob.confidence_score) if ob.confidence_score else None,
                            reasons=[
                                "valid bullish order block",
                                "active bullish order block",
                                f"price {candle.close} entered bullish order block zone [{ob.bottom_price}, {ob.top_price}]",
                                "bullish structure confirmation present",
                                "invalid risk geometry: entry must be > stop_loss for LONG",
                            ],
                            order_block=ob,
                            candle=candle,
                        )

                    risk_distance = entry - stop_loss
                    take_profit = (entry + (risk_distance * rr_cfg.reward_multiple)).quantize(Decimal("0.01"))
                    reward_distance = take_profit - entry
                    risk_reward = reward_distance / risk_distance
                    stop_distance_fraction = risk_distance / entry
                    stop_distance_pct = (stop_distance_fraction * Decimal("100")).quantize(Decimal("0.01"))
                    max_loss_pct = Decimal("35.0")
                    raw_lev = max_loss_pct / stop_distance_pct if stop_distance_pct > Decimal("0") else Decimal("1")
                    calculated_leverage = max(1, int(raw_lev))
                    take_profit_target_pct = Decimal("60.0")

                    if risk_reward >= rr_cfg.minimum_risk_reward:
                        setup_state = SetupState.TRADE_SETUP_READY
                        reasons = [
                            "valid bullish order block",
                            "active bullish order block",
                            f"price {candle.close} entered bullish order block zone [{ob.bottom_price}, {ob.top_price}]",
                            "bullish structure confirmation present",
                            "entry calculated",
                            "stop loss calculated",
                            "risk/reward validated",
                            f"risk_reward={risk_reward:.2f}",
                            "trade setup ready",
                        ]
                    else:
                        setup_state = SetupState.QUALIFIED_LONG
                        reasons = [
                            "valid bullish order block",
                            "active bullish order block",
                            f"price {candle.close} entered bullish order block zone [{ob.bottom_price}, {ob.top_price}]",
                            "bullish structure confirmation present",
                            "entry calculated",
                            "stop loss calculated",
                            "risk_reward below minimum threshold",
                        ]

                    return StrategyDecision(
                        timestamp=timestamp,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=StrategyDirection.LONG,
                        setup_state=setup_state,
                        setup_id=setup_id,
                        setup_type=SetupType.BULLISH_OB_RETEST.value,
                        entry=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        risk_distance=risk_distance,
                        reward_distance=reward_distance,
                        risk_reward=risk_reward,
                        minimum_risk_reward=rr_cfg.minimum_risk_reward,
                        order_block_upper_edge=ob.top_price,
                        order_block_lower_edge=ob.bottom_price,
                        stop_distance_pct=stop_distance_pct,
                        max_loss_pct=max_loss_pct,
                        calculated_leverage=calculated_leverage,
                        take_profit_target_pct=take_profit_target_pct,
                        take_profit_price=take_profit,
                        confidence=float(ob.confidence_score) if ob.confidence_score else None,
                        reasons=reasons,
                        order_block=ob,
                        candle=candle,
                    )
                else:
                    return StrategyDecision(
                        timestamp=timestamp,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=StrategyDirection.NONE,
                        setup_state=SetupState.OB_ENGAGED,
                        setup_id=setup_id,
                        setup_type=None,
                        entry=None,
                        stop_loss=None,
                        confidence=float(ob.confidence_score) if ob.confidence_score else None,
                        reasons=[
                            "valid bullish order block",
                            "active bullish order block",
                            f"price {candle.close} entered bullish order block zone [{ob.bottom_price}, {ob.top_price}]",
                            "no bullish structure confirmation present",
                        ],
                        order_block=ob,
                        candle=candle,
                    )

            elif ob.is_bearish():
                has_bearish_trend = (internal_trend == TrendDirection.BEARISH or swing_trend == TrendDirection.BEARISH)
                has_bearish_break = any(b.direction == TrendDirection.BEARISH for b in breaks[-3:]) if breaks else False
                setup_id = generate_setup_id(symbol, timeframe, ob, StrategyDirection.SHORT)

                if has_bearish_trend or has_bearish_break:
                    entry = ob.calculate_entry_price()
                    stop_loss = ob.calculate_stop_loss()

                    if entry is None or stop_loss is None:
                        return StrategyDecision(
                            timestamp=timestamp,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=StrategyDirection.SHORT,
                            setup_state=SetupState.QUALIFIED_SHORT,
                            setup_id=setup_id,
                            setup_type=SetupType.BEARISH_OB_RETEST.value,
                            entry=entry,
                            stop_loss=stop_loss,
                            confidence=float(ob.confidence_score) if ob.confidence_score else None,
                            reasons=[
                                "valid bearish order block",
                                "active bearish order block",
                                f"price {candle.close} entered bearish order block zone [{ob.bottom_price}, {ob.top_price}]",
                                "bearish structure confirmation present",
                                "entry or stop loss could not be calculated",
                            ],
                            order_block=ob,
                            candle=candle,
                        )

                    # Price geometry validation for SHORT: stop_loss > entry
                    if stop_loss <= entry:
                        return StrategyDecision(
                            timestamp=timestamp,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=StrategyDirection.SHORT,
                            setup_state=SetupState.QUALIFIED_SHORT,
                            setup_id=setup_id,
                            setup_type=SetupType.BEARISH_OB_RETEST.value,
                            entry=entry,
                            stop_loss=stop_loss,
                            confidence=float(ob.confidence_score) if ob.confidence_score else None,
                            reasons=[
                                "valid bearish order block",
                                "active bearish order block",
                                f"price {candle.close} entered bearish order block zone [{ob.bottom_price}, {ob.top_price}]",
                                "bearish structure confirmation present",
                                "invalid risk geometry: stop_loss must be > entry for SHORT",
                            ],
                            order_block=ob,
                            candle=candle,
                        )

                    risk_distance = stop_loss - entry
                    take_profit = (entry - (risk_distance * rr_cfg.reward_multiple)).quantize(Decimal("0.01"))
                    reward_distance = entry - take_profit
                    risk_reward = reward_distance / risk_distance
                    stop_distance_fraction = risk_distance / entry
                    stop_distance_pct = (stop_distance_fraction * Decimal("100")).quantize(Decimal("0.01"))
                    max_loss_pct = Decimal("35.0")
                    raw_lev = max_loss_pct / stop_distance_pct if stop_distance_pct > Decimal("0") else Decimal("1")
                    calculated_leverage = max(1, int(raw_lev))
                    take_profit_target_pct = Decimal("60.0")

                    if risk_reward >= rr_cfg.minimum_risk_reward:
                        setup_state = SetupState.TRADE_SETUP_READY
                        reasons = [
                            "valid bearish order block",
                            "active bearish order block",
                            f"price {candle.close} entered bearish order block zone [{ob.bottom_price}, {ob.top_price}]",
                            "bearish structure confirmation present",
                            "entry calculated",
                            "stop loss calculated",
                            "risk/reward validated",
                            f"risk_reward={risk_reward:.2f}",
                            "trade setup ready",
                        ]
                    else:
                        setup_state = SetupState.QUALIFIED_SHORT
                        reasons = [
                            "valid bearish order block",
                            "active bearish order block",
                            f"price {candle.close} entered bearish order block zone [{ob.bottom_price}, {ob.top_price}]",
                            "bearish structure confirmation present",
                            "entry calculated",
                            "stop loss calculated",
                            "risk_reward below minimum threshold",
                        ]

                    return StrategyDecision(
                        timestamp=timestamp,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=StrategyDirection.SHORT,
                        setup_state=setup_state,
                        setup_id=setup_id,
                        setup_type=SetupType.BEARISH_OB_RETEST.value,
                        entry=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        risk_distance=risk_distance,
                        reward_distance=reward_distance,
                        risk_reward=risk_reward,
                        minimum_risk_reward=rr_cfg.minimum_risk_reward,
                        order_block_upper_edge=ob.top_price,
                        order_block_lower_edge=ob.bottom_price,
                        stop_distance_pct=stop_distance_pct,
                        max_loss_pct=max_loss_pct,
                        calculated_leverage=calculated_leverage,
                        take_profit_target_pct=take_profit_target_pct,
                        take_profit_price=take_profit,
                        confidence=float(ob.confidence_score) if ob.confidence_score else None,
                        reasons=reasons,
                        order_block=ob,
                        candle=candle,
                    )
                else:
                    return StrategyDecision(
                        timestamp=timestamp,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=StrategyDirection.NONE,
                        setup_state=SetupState.OB_ENGAGED,
                        setup_id=setup_id,
                        setup_type=None,
                        entry=None,
                        stop_loss=None,
                        confidence=float(ob.confidence_score) if ob.confidence_score else None,
                        reasons=[
                            "valid bearish order block",
                            "active bearish order block",
                            f"price {candle.close} entered bearish order block zone [{ob.bottom_price}, {ob.top_price}]",
                            "no bearish structure confirmation present",
                        ],
                        order_block=ob,
                        candle=candle,
                    )

        return StrategyDecision(
            timestamp=timestamp,
            symbol=symbol,
            timeframe=timeframe,
            direction=StrategyDirection.NONE,
            setup_state=SetupState.NO_SETUP,
            setup_type=None,
            reasons=["No valid setup"],
            candle=candle,
        )

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

        # 2. Get eligible order blocks (FRESH or TOUCHED)
        eligible_obs = state.get_eligible_order_blocks()

        # 3. Filter and score each OB
        setups = []
        for ob in eligible_obs:
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

        # Hard Filter 1: OB must not be invalidated (already filtered by get_eligible_order_blocks)
        if ob.is_invalidated():
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

        # Hard Filter 2: OB must not be used (already filtered)
        if ob.is_used():
            return self._reject_setup(ob, symbol, timeframe, StrategySignal.OB_USED, "OB already used")

        # Hard Filter 3: OB must be eligible for entry (FRESH or TOUCHED)
        if not ob.is_eligible_for_entry():
            return self._reject_setup(ob, symbol, timeframe, StrategySignal.NOT_ELIGIBLE, "OB not in FRESH/TOUCHED state")

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
            if other_ob == ob or other_ob.is_invalidated() or other_ob.is_used():
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