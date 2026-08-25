"""
QuantEdge AI — Phase I Real Order-Block Historical Trade Replay (core library).

════════════════════════════════════════════════════════════════════════════════
AUTHORITY & SCOPE
════════════════════════════════════════════════════════════════════════════════
Phase I answers ONE question:

    "Does the AI improve the REAL QuantEdge SMC / Order-Block trading strategy?"

It is an OB-centric historical trade replay built EXCLUSIVELY on the existing,
authoritative production components.  NO parallel SMC/OB detector, NO synthetic
setups, NO manufactured labels exists in this module:

    SMC volatility parsing   -> quantedge.smc.volatility.parse_candles_with_volatility
    Structure / BOS / CHOCH  -> quantedge.smc.structure.StructureDetector (streaming)
    Order Block creation     -> quantedge.smc.order_blocks.OrderBlockDetector
                                (LuxAlgo slice semantics)
    Entry rule               -> quantedge.strategy.engine.StrategyEngine.evaluate_state
                                entry = OrderBlock.calculate_entry_price()
    Stop loss (2nd edge)     -> quantedge.strategy.engine / smc.models
                                SL = OrderBlock.calculate_stop_loss()
    Trade outcome replay     -> quantedge.ai.training.real_dataset_builder.replay_forward_outcome
                                (forward-only barriers; same-candle TP+SL => SL FIRST)
    Causal AI features       -> quantedge.ai.training.real_dataset_builder.extract_causal_24_features
    Frozen AI model          -> backend/src/main/resources/models/quantedge-ai-v2.onnx
    Performance metrics      -> quantedge.ai.evaluation.smc_baseline.calculate_performance_metrics

════════════════════════════════════════════════════════════════════════════════
PHASE I EXPERIMENTAL CONFIGURATION (declared BEFORE OOS evaluation)
════════════════════════════════════════════════════════════════════════════════
PHASE_I_OB_60TP_35SL:
    The user-requested "TP = 60% / SL = 35%" is resolved against the existing
    QuantEdge account-level risk conventions (strategy/risk.py):

        risk_per_trade_pct   = 35.0   (max loss per trade = 35% of balance)
        target_reward_pct    = 60.0   (target reward        = 60% of balance)
        max_leverage         = 100

    With the authoritative dynamic-leverage formula
    (strategy/engine.py):
        leverage = max(1, floor(35.0 / stop_distance_pct))
    the loss at SL is exactly 35% of balance.  For the TP to realise exactly
    +60% of balance under that same leverage the required price move is

        tp_distance = (0.60 / leverage) * entry
                    = (0.60 * stop_distance_pct / 35) * entry
                    = (60/35) * stop_distance.

    Therefore "TP 60% / SL 35%" == reward_multiple = 60/35 = 1.714286R.
    This is implemented as an explicit Phase-I-only RiskRewardConfig and does
    NOT touch the production default (reward_multiple = 2.0).

Leverage experiment:
    leverage(trade) = min(LEV_CAP, max(1, floor(35.0 / stop_distance_pct)))
    i.e. leverage = 35x when the OB second-edge stop distance is exactly 1% of
    entry — derived from actual per-trade stop distance, never assigned flat.

Costs (documented research assumptions; repo has no fee constants —
STRATEGY_SPECIFICATION.md marks fees/funding as UNRESOLVED):
    taker fee  0.05 % per side  (Delta Exchange India published taker fee)
    slippage   0.01 % per side  (reuses engine/src/quantedge/config.py slippage_pct)
    funding    0.01 % per 8h on notional, charged |absolute| (conservative)

All randomness is seeded; two runs with identical inputs are byte-identical.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from quantedge.ai.evaluation.smc_baseline import (
    PerformanceMetrics,
    calculate_performance_metrics,
)
from quantedge.ai.feature_contract import FEATURE_NAMES
from quantedge.ai.training.real_dataset_builder import (
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    TradeReplayOutcome,
    extract_causal_24_features,
    replay_forward_outcome,
)
from quantedge.market_data.models import Candle, MarketDataSource, Timeframe
from quantedge.smc.models import PivotPoint, StructureBreak, TrendDirection
from quantedge.smc.order_blocks import OrderBlockConfig, detect_order_blocks_streaming
from quantedge.smc.structure import StructureConfig, StructureDetector, StructureType
from quantedge.smc.volatility import ParsedCandle, parse_candles_with_volatility
from quantedge.strategy.engine import StrategyEngine
from quantedge.strategy.models import RiskRewardConfig, SetupState, StrategyDecision, StrategyDirection

# ═════════════════════════════════════════════════════════════════════════════
# Phase I frozen experimental configuration (declared before any evaluation)
# ═════════════════════════════════════════════════════════════════════════════

PHASE_I_CONFIG_NAME = "PHASE_I_OB_60TP_35SL"

#: TP = 60% / SL = 35% of account => reward_multiple = 60/35 (see module docstring).
#: minimum_risk_reward is set to a token positive value: Phase I trades ALL
#: geometrically valid OB setups (matching the Phase H dataset convention) and
#: must not silently drop the 60/35 TP geometry for being below production's 2.0 gate.
PHASE_I_TP_RR_CONFIG = RiskRewardConfig(
    minimum_risk_reward="0.0001",
    reward_multiple=Decimal(60) / Decimal(35),
)

#: Account-level risk constants reused verbatim from production StrategyConfig.
MAX_LOSS_PCT_OF_BALANCE = Decimal("35.0")
TARGET_REWARD_PCT_OF_BALANCE = Decimal("60.0")
PRODUCTION_MAX_LEVERAGE = 100

#: Costs — documented research assumptions (see module docstring).
TAKER_FEE_RATE_PER_SIDE = 0.0005          # Delta Exchange India taker fee 0.05%
SLIPPAGE_RATE_PER_SIDE = 0.0001           # repo config.py slippage_pct = 0.01%
FUNDING_RATE_PER_HOUR = 0.0001 / 8.0      # 0.01% per 8h baseline, charged |abs|

#: Liquidation analysis — isolated-margin approximation research assumptions.
MAINTENANCE_MARGIN_RATE = 0.005           # 0.5% maintenance margin (research assumption)

#: Statistics.
BOOTSTRAP_N = 2000                        # >= 1000 required by Phase I spec
BOOTSTRAP_SEED = 42
MIN_AI_COVERAGE_PCT = 10.0                # pre-declared promotion-gate coverage floor

REPLAY_HORIZON_BARS = 72                  # identical to frozen Phase H replay horizon
WARMUP_BARS = 200                         # ATR(200) + structure warm-up


# ═════════════════════════════════════════════════════════════════════════════
# Deterministic records
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PhaseISetup:
    """One deterministic historical OB setup extracted from the real SMC engine."""
    setup_id: str
    asset: str
    timeframe: str
    direction: str                      # LONG / SHORT
    decision_bar: int                   # candle index of the decision (entry bar)
    decision_time: str                  # decision candle timestamp (UTC ISO)
    creation_time: str                  # OB formation candle timestamp (UTC ISO)
    confirmation_time: str              # structural break candle timestamp (UTC ISO)
    ob_high: float                      # top_price
    ob_low: float                       # bottom_price
    entry_price: float
    sl_price: float                     # second edge of the OB (authoritative)
    tp_price: float                     # PHASE_I_OB_60TP_35SL take profit
    risk_distance: float
    stop_distance_percent: float
    atr_normalized_stop_distance: float
    leverage: int                       # dynamic production formula, capped
    structural_event_id: str
    features_24: Tuple[float, ...]      # causal canonical-24 vector (data <= T)


@dataclass(frozen=True)
class PhaseITradeRecord:
    """Setup + forward replay outcome + AI decision (decision made BEFORE outcome)."""
    setup: PhaseISetup
    outcome: TradeReplayOutcome
    predicted_r: float                  # ONNX pred_realized_r at decision time
    predicted_mfe_r: float
    predicted_mae_r: float
    ai_threshold: float
    ai_decision: str                    # ACCEPT / REJECT
    gross_r: float                      # outcome.realized_r
    net_r: float                        # after fees/slippage/funding
    cost_r: float
    liquidation_price: float
    liq_distance_fraction: float
    liquidation_before_sl: bool
    margin_fraction_of_balance: float   # notional / (balance) = lev^-1 … informational


# ═════════════════════════════════════════════════════════════════════════════
# Canonical data loading (real data only — zero synthetic candles)
# ═════════════════════════════════════════════════════════════════════════════


def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


def load_canonical_candles(canonical_base: Path, symbol: str) -> List[Candle]:
    """Loads the canonical Delta Exchange India 1h dataset for one asset."""
    csv_path = Path(canonical_base) / symbol / "1h" / "2026.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Canonical dataset missing for {symbol}: {csv_path}")
    candles: List[Candle] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=Timeframe.H1,
                    timestamp=ts,
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row.get("volume", "0")),
                    source=MarketDataSource.HISTORICAL,
                )
            )
    # Data integrity guards (sorted, unique timestamps)
    timestamps = [c.timestamp for c in candles]
    if timestamps != sorted(timestamps):
        raise ValueError(f"{symbol}: canonical candles are not sorted ascending")
    if len(set(timestamps)) != len(timestamps):
        raise ValueError(f"{symbol}: duplicate timestamps in canonical data")
    return candles


# ═════════════════════════════════════════════════════════════════════════════
# SMC context — exact reuse of the authoritative detection pipeline
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class SMCContext:
    parsed: List[ParsedCandle]
    int_breaks: List[StructureBreak]
    sw_breaks: List[StructureBreak]
    int_pivots: List[PivotPoint]
    sw_pivots: List[PivotPoint]
    order_blocks: list
    active_obs_by_idx: List[list]
    ob_structure_origin: Dict[int, str]  # id(OrderBlock) -> "internal" | "swing"


def build_smc_context(candles: List[Candle]) -> SMCContext:
    """
    Runs the authoritative deterministic SMC pipeline over the full history.

    Mirrors quantedge.ai.training.real_dataset_builder.build_real_training_dataset
    exactly (the Phase H causal dataset convention): same detectors, same
    parameters (ATR-200 x2.0, internal 5, swing 50), same active-OB lifecycle
    mapping (activation at break_index+1, wick-through invalidation).
    """
    parsed = parse_candles_with_volatility(candles, atr_period=200, atr_multiplier=2.0)
    int_cfg = StructureConfig(5, StructureType.INTERNAL)
    sw_cfg = StructureConfig(50, StructureType.SWING)

    int_det = StructureDetector(int_cfg)
    sw_det = StructureDetector(sw_cfg)
    int_brk, sw_brk = [], []
    for i, pc in enumerate(parsed):
        int_brk.extend(int_det.process_candle(pc, i))
        sw_brk.extend(sw_det.process_candle(pc, i))

    int_piv, sw_piv = [], []
    prev_iph = prev_ipl = prev_sph = prev_spl = None
    int_det_p = StructureDetector(int_cfg)
    sw_det_p = StructureDetector(sw_cfg)
    for i, pc in enumerate(parsed):
        int_det_p.process_candle(pc, i)
        sw_det_p.process_candle(pc, i)
        iph, ipl = int_det_p.state.pivot_high, int_det_p.state.pivot_low
        sph, spl = sw_det_p.state.pivot_high, sw_det_p.state.pivot_low
        if iph and iph.index != prev_iph:
            int_piv.append(PivotPoint(iph.index, iph.timestamp, iph.price, True, iph.candle))
            prev_iph = iph.index
        if ipl and ipl.index != prev_ipl:
            int_piv.append(PivotPoint(ipl.index, ipl.timestamp, ipl.price, False, ipl.candle))
            prev_ipl = ipl.index
        if sph and sph.index != prev_sph:
            sw_piv.append(PivotPoint(sph.index, sph.timestamp, sph.price, True, sph.candle))
            prev_sph = sph.index
        if spl and spl.index != prev_spl:
            sw_piv.append(PivotPoint(spl.index, spl.timestamp, spl.price, False, spl.candle))
            prev_spl = spl.index

    ob_cfg = OrderBlockConfig(internal_length=5, swing_length=50, atr_period=200, atr_multiplier=2.0)
    int_obs = detect_order_blocks_streaming(parsed, int_brk, [], int_piv, sw_piv, ob_cfg)
    sw_obs = detect_order_blocks_streaming(parsed, [], sw_brk, int_piv, sw_piv, ob_cfg)
    all_raw_obs = int_obs + sw_obs
    ob_origin = {id(ob): "internal" for ob in int_obs}
    ob_origin.update({id(ob): "swing" for ob in sw_obs})

    # Active-OB index mapping — identical lifecycle convention to Phase H builder.
    active_obs_by_idx: List[List[Any]] = [[] for _ in range(len(candles))]
    for raw_ob in all_raw_obs:
        start_idx = raw_ob.break_index + 1
        end_idx = len(candles)
        for k in range(start_idx, len(candles)):
            if raw_ob.is_bullish() and candles[k].low < raw_ob.bottom_price:
                end_idx = k
                break
            elif raw_ob.is_bearish() and candles[k].high > raw_ob.top_price:
                end_idx = k
                break
        for idx in range(start_idx, min(end_idx, len(candles))):
            active_obs_by_idx[idx].append(raw_ob)

    return SMCContext(
        parsed=parsed,
        int_breaks=int_brk,
        sw_breaks=sw_brk,
        int_pivots=int_piv,
        sw_pivots=sw_piv,
        order_blocks=all_raw_obs,
        active_obs_by_idx=active_obs_by_idx,
        ob_structure_origin=ob_origin,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Historical OB setup extraction (adapter around StrategyEngine.evaluate_state)
# ═════════════════════════════════════════════════════════════════════════════


def extract_phase_i_setups(
    candles: List[Candle],
    symbol: str,
    ctx: Optional[SMCContext] = None,
    warmup_bars: int = WARMUP_BARS,
) -> Tuple[List[PhaseISetup], Dict[str, Any]]:
    """
    Replays the authoritative strategy decision function bar-by-bar across the
    full history and extracts EVERY valid historical OB setup.

    Duplicate handling: the application executes at most one trade per OB
    (OrderBlock USED state).  The first qualifying decision for each OB is kept;
    later decisions referencing the same OB are counted as duplicates and skipped.

    The AI feature vector is extracted causally at the exact decision bar using
    data <= T only (extract_causal_24_features).
    """
    if ctx is None:
        ctx = build_smc_context(candles)

    strategy = StrategyEngine()
    setups: List[PhaseISetup] = []
    duplicates_skipped = 0
    skipped_geometry = 0
    seen_ob_ids: set = set()

    for i in range(warmup_bars, len(candles)):
        c = candles[i]
        active = ctx.active_obs_by_idx[i]
        if not active:
            continue

        r_breaks = [b for b in ctx.int_breaks + ctx.sw_breaks if b.index <= i and b.index >= i - 10]
        int_trend = TrendDirection.BULLISH if c.close > candles[max(0, i - 5)].close else TrendDirection.BEARISH
        sw_trend = TrendDirection.BULLISH if c.close > candles[max(0, i - 50)].close else TrendDirection.BEARISH

        decision = strategy.evaluate_state(
            candle=c,
            active_obs=active,
            internal_trend=int_trend,
            swing_trend=sw_trend,
            recent_breaks=r_breaks,
            all_active_obs=active,
            risk_reward_config=PHASE_I_TP_RR_CONFIG,
        )

        if decision.setup_state not in (
            SetupState.TRADE_SETUP_READY,
            SetupState.QUALIFIED_LONG,
            SetupState.QUALIFIED_SHORT,
        ):
            continue
        if decision.entry is None or decision.stop_loss is None or decision.take_profit is None:
            continue

        ob = decision.order_block
        ob_key = id(ob)
        if ob_key in seen_ob_ids:
            duplicates_skipped += 1
            continue
        seen_ob_ids.add(ob_key)

        entry_f = float(decision.entry)
        sl_f = float(decision.stop_loss)
        tp_f = float(decision.take_profit)
        risk_dist = abs(entry_f - sl_f)
        if risk_dist <= 1e-9 or entry_f <= 0:
            skipped_geometry += 1
            continue
        is_long = decision.direction == StrategyDirection.LONG
        if (is_long and sl_f >= entry_f) or ((not is_long) and sl_f <= entry_f):
            skipped_geometry += 1
            continue
        stop_frac = risk_dist / entry_f
        atr_val = float(ctx.parsed[i].atr_value) if ctx.parsed[i].atr_value else entry_f * 0.01

        features = tuple(
            round(float(v), 6)
            for v in extract_causal_24_features(
                idx=i,
                candles=candles,
                parsed=ctx.parsed,
                decision=decision,
                active_obs=active,
                int_breaks=ctx.int_breaks,
                sw_breaks=ctx.sw_breaks,
                int_pivots=ctx.int_pivots,
                sw_pivots=ctx.sw_pivots,
            )
        )

        structure_type = ctx.ob_structure_origin.get(ob_key, "internal")
        structural_event_id = f"{structure_type}|{ob.break_type.value if hasattr(ob.break_type, 'value') else ob.break_type}|brk{ob.break_index}|form{ob.formation_index}"

        setups.append(
            PhaseISetup(
                setup_id=f"{symbol}_{i}_{ob.type}_{ob.formation_index}_{ob.break_index}",
                asset=symbol,
                timeframe="1h",
                direction="LONG" if decision.direction == StrategyDirection.LONG else "SHORT",
                decision_bar=i,
                decision_time=c.timestamp.isoformat(),
                creation_time=ob.formation_candle.timestamp.isoformat(),
                confirmation_time=candles[ob.break_index].timestamp.isoformat(),
                ob_high=float(ob.top_price),
                ob_low=float(ob.bottom_price),
                entry_price=round(entry_f, 8),
                sl_price=round(sl_f, 8),
                tp_price=round(tp_f, 8),
                risk_distance=round(risk_dist, 8),
                stop_distance_percent=round(stop_frac * 100.0, 6),
                atr_normalized_stop_distance=round(risk_dist / (atr_val + 1e-12), 6),
                leverage=compute_dynamic_leverage(stop_frac * 100.0),
                structural_event_id=structural_event_id,
                features_24=features,
            )
        )

    audit = {
        "symbol": symbol,
        "candles": len(candles),
        "raw_ob_detections": len(ctx.order_blocks),
        "internal_breaks": len(ctx.int_breaks),
        "swing_breaks": len(ctx.sw_breaks),
        "duplicate_decisions_skipped": duplicates_skipped,
        "invalid_geometry_skipped": skipped_geometry,
        "unique_setups": len(setups),
    }
    return setups, audit


# ═════════════════════════════════════════════════════════════════════════════
# Leverage & liquidation mathematics (production formulas)
# ═════════════════════════════════════════════════════════════════════════════


def compute_dynamic_leverage(stop_distance_percent: float, cap: int = PRODUCTION_MAX_LEVERAGE) -> int:
    """
    Authoritative production formula (strategy/engine.py):

        raw_lev = 35.0 / stop_distance_pct ; leverage = max(1, int(raw_lev))

    e.g. stop distance 1% of entry  => 35x.  Phase I adds the production
    StrategyConfig.max_leverage cap (100x) for the safety analysis.
    """
    if stop_distance_percent <= 0:
        return 1
    raw = MAX_LOSS_PCT_OF_BALANCE / Decimal(str(stop_distance_percent))
    lev = max(1, int(raw))  # int() truncates toward zero == floor for positives
    return min(cap, lev)


def estimate_liquidation(
    entry_price: float,
    stop_distance_fraction: float,
    leverage: int,
    direction: str,
    mmr: float = MAINTENANCE_MARGIN_RATE,
) -> Dict[str, Any]:
    """
    Isolated-margin liquidation approximation (research assumption, mmr=0.5%).

        adverse move to liquidation ~= 1/leverage - mmr   (as fraction of entry)

    liquidation_before_SL is True when the estimated liquidation move is at or
    inside the intended SL distance (liquidation would occur before the stop).
    """
    inv_lev = 1.0 / max(1, leverage)
    liq_move = max(inv_lev - mmr, 0.0)
    if direction == "LONG":
        liq_price = entry_price * (1.0 - liq_move)
    else:
        liq_price = entry_price * (1.0 + liq_move)
    before_sl = bool(liq_move <= stop_distance_fraction + 1e-12) if stop_distance_fraction > 0 else False
    return {
        "liquidation_price": liq_price,
        "liq_distance_fraction": liq_move,
        "liquidation_before_sl": before_sl,
        "margin_fraction_of_balance": inv_lev,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Cost model (research assumptions, applied deterministically)
# ═════════════════════════════════════════════════════════════════════════════


def compute_net_r(
    gross_r: float,
    entry_price: float,
    stop_distance_fraction: float,
    holding_hours: float,
    taker_fee: float = TAKER_FEE_RATE_PER_SIDE,
    slippage: float = SLIPPAGE_RATE_PER_SIDE,
    funding_per_hour: float = FUNDING_RATE_PER_HOUR,
) -> Tuple[float, float]:
    """
    Converts round-trip costs into R units and returns (net_r, cost_r).

        cost_notional_fraction = 2*(taker+slippage) + funding*hours
        cost_r                 = cost_notional_fraction / stop_distance_fraction
    """
    if stop_distance_fraction <= 0:
        return gross_r, 0.0
    cost_frac = 2.0 * (taker_fee + slippage) + funding_per_hour * holding_hours
    cost_r = cost_frac / stop_distance_fraction
    return gross_r - cost_r, cost_r


# ═════════════════════════════════════════════════════════════════════════════
# Full trade replay: setup + causal AI decision + forward-only outcome
# ═════════════════════════════════════════════════════════════════════════════


def replay_phase_i_trades(
    candles: List[Candle],
    setups: Sequence[PhaseISetup],
    predicted_r_by_setup: Dict[str, Tuple[float, float, float]],
    ai_threshold: float,
) -> List[PhaseITradeRecord]:
    """
    For every setup:
      1. The AI decision is taken from predictions computed at the decision bar
         (features contain data <= T only) — strictly BEFORE any outcome info.
      2. The trade is replayed forward via the authoritative
         replay_forward_outcome (barrier checks from T+1 onward, same-candle
         TP+SL resolved SL-first, timeout at horizon exits mark-to-market).
    """
    records: List[PhaseITradeRecord] = []
    for s in setups:
        pred_r, pred_mfe, pred_mae = predicted_r_by_setup[s.setup_id]
        ai_decision = "ACCEPT" if pred_r >= ai_threshold else "REJECT"

        decision = StrategyDecision(
            timestamp=datetime.fromisoformat(s.creation_time),
            symbol=s.asset,
            timeframe=s.timeframe,
            direction=StrategyDirection.LONG if s.direction == "LONG" else StrategyDirection.SHORT,
            setup_id=s.setup_id,
            entry=Decimal(str(s.entry_price)),
            stop_loss=Decimal(str(s.sl_price)),
            take_profit=Decimal(str(s.tp_price)),
            risk_distance=Decimal(str(s.risk_distance)),
        )
        outcome = replay_forward_outcome(
            setup_idx=s.decision_bar,
            candles=candles,
            decision=decision,
            max_holding_bars=REPLAY_HORIZON_BARS,
        )
        if outcome is None:
            continue

        stop_frac = s.stop_distance_percent / 100.0
        holding_hours = float(outcome.holding_bars)  # 1h candles
        net_r, cost_r = compute_net_r(outcome.realized_r, s.entry_price, stop_frac, holding_hours)

        liq = estimate_liquidation(s.entry_price, stop_frac, s.leverage, s.direction)

        records.append(
            PhaseITradeRecord(
                setup=s,
                outcome=outcome,
                predicted_r=float(pred_r),
                predicted_mfe_r=float(pred_mfe),
                predicted_mae_r=float(pred_mae),
                ai_threshold=ai_threshold,
                ai_decision=ai_decision,
                gross_r=outcome.realized_r,
                net_r=net_r,
                cost_r=cost_r,
                liquidation_price=liq["liquidation_price"],
                liq_distance_fraction=liq["liq_distance_fraction"],
                liquidation_before_sl=liq["liquidation_before_sl"],
                margin_fraction_of_balance=liq["margin_fraction_of_balance"],
            )
        )
    return records


# ═════════════════════════════════════════════════════════════════════════════
# Extended metrics (wraps the shared calculate_performance_metrics)
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ExtendedMetrics:
    base: PerformanceMetrics
    best_trade_r: float
    worst_trade_r: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    max_drawdown_duration_trades: int
    avg_sl_distance_pct: float
    avg_tp_distance_pct: float
    avg_leverage: float

    def to_dict(self) -> Dict[str, Any]:
        d = self.base.to_dict()
        d.update(
            {
                "best_trade_r": round(self.best_trade_r, 4),
                "worst_trade_r": round(self.worst_trade_r, 4),
                "max_consecutive_wins": self.max_consecutive_wins,
                "max_consecutive_losses": self.max_consecutive_losses,
                "max_drawdown_duration_trades": self.max_drawdown_duration_trades,
                "avg_sl_distance_pct": round(self.avg_sl_distance_pct, 4),
                "avg_tp_distance_pct": round(self.avg_tp_distance_pct, 4),
                "avg_leverage": round(self.avg_leverage, 2),
            }
        )
        return d


def compute_extended_metrics(records: Sequence[PhaseITradeRecord], r_key: str = "gross_r") -> ExtendedMetrics:
    """Computes Phase I metrics for a group of trades (gross or net accounting)."""
    n_total = len(records)
    if n_total == 0:
        empty_df = _records_to_dataframe([])
        return ExtendedMetrics(
            base=calculate_performance_metrics(empty_df, total_eligible_setups=0),
            best_trade_r=0.0,
            worst_trade_r=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            max_drawdown_duration_trades=0,
            avg_sl_distance_pct=0.0,
            avg_tp_distance_pct=0.0,
            avg_leverage=0.0,
        )

    df = _records_to_dataframe(records, r_key=r_key)
    base = calculate_performance_metrics(df, total_eligible_setups=n_total)

    r_vals = np.array([getattr(t, r_key) for t in records], dtype=float)

    best = float(np.max(r_vals))
    worst = float(np.min(r_vals))

    max_cw = max_cl = cur_w = cur_l = 0
    for r in r_vals:
        if r > 0:
            cur_w += 1
            cur_l = 0
        elif r < 0:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        max_cw = max(max_cw, cur_w)
        max_cl = max(max_cl, cur_l)

    cum = np.cumsum(r_vals)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    dd_max = float(np.max(dd)) if len(dd) else 0.0
    duration = cur_dur = 0
    for d in dd:
        if d > 1e-12:
            cur_dur += 1
            duration = max(duration, cur_dur)
        else:
            cur_dur = 0

    avg_sl_pct = float(np.mean([t.setup.stop_distance_percent for t in records]))
    avg_tp_pct = float(
        np.mean([abs(t.setup.tp_price - t.setup.entry_price) / t.setup.entry_price * 100.0 for t in records])
    )
    avg_lev = float(np.mean([t.setup.leverage for t in records]))

    return ExtendedMetrics(
        base=base,
        best_trade_r=best,
        worst_trade_r=worst,
        max_consecutive_wins=max_cw,
        max_consecutive_losses=max_cl,
        max_drawdown_duration_trades=duration,
        avg_sl_distance_pct=avg_sl_pct,
        avg_tp_distance_pct=avg_tp_pct,
        avg_leverage=avg_lev,
    )


def _records_to_dataframe(records: Sequence[PhaseITradeRecord], r_key: str = "gross_r"):
    import pandas as pd

    rows = []
    for t in records:
        rows.append(
            {
                "setup_id": t.setup.setup_id,
                "asset": t.setup.asset,
                "exit_reason": t.outcome.exit_reason,
                TARGET_REALIZED_R: getattr(t, r_key),
                TARGET_MFE_R: t.outcome.mfe_r,
                TARGET_MAE_R: t.outcome.mae_r,
                "holding_bars": t.outcome.holding_bars,
            }
        )
    return pd.DataFrame(rows)


def equity_curve(records: Sequence[PhaseITradeRecord], r_key: str = "gross_r") -> np.ndarray:
    rs = [getattr(t, r_key) for t in sorted(records, key=lambda t: t.outcome.exit_index)]
    return np.cumsum(np.array(rs, dtype=float))


# ═════════════════════════════════════════════════════════════════════════════
# Moving Block Bootstrap (consistent with Phases E–H methodology)
# ═════════════════════════════════════════════════════════════════════════════


def mbb_block_size(n: int) -> int:
    """Same block-size rule used by phase_e_gate / phase_f_gate."""
    return max(3, int(math.ceil(n ** (1.0 / 3.0)))) if n > 0 else 3


def moving_block_bootstrap_groups(
    r_all: np.ndarray,
    accepted_mask: np.ndarray,
    n_boot: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    """
    Paired Moving Block Bootstrap over the full OOS trade sequence.

    Each bootstrap replicate resamples blocks of indices from the FULL Group-A
    sequence; the SMC mean, AI mean (accepted subset within the resample) and
    their difference are computed on the SAME resampled indices (paired), so
    the incremental CI accounts for coverage and correlation jointly.
    """
    n = len(r_all)
    bs = mbb_block_size(n)
    num_blocks = int(math.ceil(n / bs))
    rng = np.random.default_rng(seed)

    smc_means = np.empty(n_boot)
    ai_means = np.empty(n_boot)
    inc_means = np.empty(n_boot)
    rej_means = np.empty(n_boot)

    for b in range(n_boot):
        starts = rng.integers(0, max(1, n - bs + 1), size=num_blocks)
        idx = np.concatenate([np.arange(s, s + bs) for s in starts])[:n]
        idx = np.mod(idx, n)
        r_s = r_all[idx]
        m_a = accepted_mask[idx]
        smc_means[b] = np.mean(r_s) if len(r_s) else 0.0
        ai_means[b] = np.mean(r_s[m_a]) if m_a.any() else 0.0
        rej_means[b] = np.mean(r_s[~m_a]) if (~m_a).any() else 0.0
        inc_means[b] = ai_means[b] - smc_means[b]

    def ci(arr: np.ndarray) -> Tuple[float, float]:
        return (
            round(float(np.percentile(arr, 2.5)), 4),
            round(float(np.percentile(arr, 97.5)), 4),
        )

    return {
        "smc_mean_r_95ci": ci(smc_means),
        "ai_mean_r_95ci": ci(ai_means),
        "incremental_mean_r_95ci": ci(inc_means),
        "rejected_mean_r_95ci": ci(rej_means),
        "mbb_block_size": bs,
        "n_bootstraps": n_boot,
        "seed": seed,
    }


# ═════════════════════════════════════════════════════════════════════════════
# AI score buckets (calibration vs realised performance)
# ═════════════════════════════════════════════════════════════════════════════

BUCKET_EDGES = [
    ("< 0R", lambda p: p < 0.0),
    ("0-0.25R", lambda p: 0.0 <= p < 0.25),
    ("0.25-0.50R", lambda p: 0.25 <= p < 0.50),
    ("0.50-1.00R", lambda p: 0.50 <= p < 1.00),
    (">= 1.00R", lambda p: p >= 1.00),
]


def compute_score_buckets(records: Sequence[PhaseITradeRecord]) -> Dict[str, Any]:
    buckets: Dict[str, Any] = {}
    realized_means = []
    for label, pred_fn in BUCKET_EDGES:
        subset = [t for t in records if pred_fn(t.predicted_r)]
        if subset:
            r = np.array([t.gross_r for t in subset], dtype=float)
            wins = int(np.sum(r > 0))
            gp = float(np.sum(r[r > 0]))
            gl = float(abs(np.sum(r[r < 0])))
            pf = round(gp / gl, 3) if gl > 1e-9 else (999.0 if gp > 0 else 0.0)
            stats = {
                "count": len(subset),
                "predicted_mean_r": round(float(np.mean([t.predicted_r for t in subset])), 4),
                "realized_mean_r": round(float(np.mean(r)), 4),
                "win_rate_pct": round(wins / len(subset) * 100.0, 2),
                "profit_factor": pf,
                "median_r": round(float(np.median(r)), 4),
                "mean_mfe_r": round(float(np.mean([t.outcome.mfe_r for t in subset])), 4),
                "mean_mae_r": round(float(np.mean([t.outcome.mae_r for t in subset])), 4),
            }
            realized_means.append((label, stats["realized_mean_r"], stats["count"]))
        else:
            stats = {
                "count": 0,
                "predicted_mean_r": None,
                "realized_mean_r": None,
                "win_rate_pct": None,
                "profit_factor": None,
                "median_r": None,
                "mean_mfe_r": None,
                "mean_mae_r": None,
            }
        buckets[label] = stats

    populated = [(lbl, val, cnt) for lbl, val, cnt in realized_means if cnt >= 5]
    monotonic = all(
        populated[i][1] <= populated[i + 1][1] + 1e-9 for i in range(len(populated) - 1)
    ) if len(populated) >= 2 else False

    return {
        "buckets": buckets,
        "monotonic_calibration": bool(monotonic) if len(populated) >= 2 else None,
        "note": "monotonic=True requires realised mean R non-decreasing across buckets (>=5 samples each)",
    }


# ═════════════════════════════════════════════════════════════════════════════
# Phase I promotion gate (research gate — NEVER authorises live trading)
# ═════════════════════════════════════════════════════════════════════════════


def evaluate_phase_i_gate(
    oos_smc: ExtendedMetrics,
    oos_ai: ExtendedMetrics,
    incremental_ci_low: float,
    per_asset_incremental: Dict[str, float],
    rejected_expectancy: float,
    accepted_expectancy: float,
    liquidation_violations: int,
    ai_coverage_pct: Optional[float] = None,
) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {}

    criteria["C1_oos_incremental_expectancy_positive"] = {
        "passed": oos_ai.base.expectancy_r > oos_smc.base.expectancy_r,
        "detail": f"SMC {oos_smc.base.expectancy_r:+.4f}R vs AI {oos_ai.base.expectancy_r:+.4f}R",
    }
    criteria["C2_oos_profit_factor_improvement"] = {
        "passed": oos_ai.base.profit_factor > oos_smc.base.profit_factor,
        "detail": f"SMC PF {oos_smc.base.profit_factor:.3f} vs AI PF {oos_ai.base.profit_factor:.3f}",
    }
    criteria["C3_oos_drawdown_improvement"] = {
        "passed": oos_ai.base.max_drawdown_r < oos_smc.base.max_drawdown_r,
        "detail": f"SMC MDD {oos_smc.base.max_drawdown_r:.2f}R vs AI MDD {oos_ai.base.max_drawdown_r:.2f}R",
    }
    # Coverage is measured against ALL OOS SMC setups (Group A), not the AI subset.
    coverage = (
        ai_coverage_pct
        if ai_coverage_pct is not None
        else oos_ai.base.coverage_pct
    )
    criteria["C4_minimum_ai_coverage"] = {
        "passed": coverage >= MIN_AI_COVERAGE_PCT,
        "detail": f"AI coverage {coverage:.2f}% of SMC setups (floor {MIN_AI_COVERAGE_PCT}%)",
    }
    criteria["C5_bootstrap_ci_lower_bound_positive"] = {
        "passed": incremental_ci_low > 0,
        "detail": f"Incremental expectancy 95% CI lower bound {incremental_ci_low:+.4f}R",
    }
    assets_ok = sum(1 for v in per_asset_incremental.values() if v >= 0)
    worst = min(per_asset_incremental.values()) if per_asset_incremental else -99.0
    no_accept = [a for a, v in per_asset_incremental.items() if v <= -99.0]
    robustness_detail = f"{assets_ok}/{len(per_asset_incremental)} assets non-negative incremental; worst {worst:+.4f}R"
    if no_accept:
        robustness_detail += f"; no accepted trades on {no_accept}"
    criteria["C6_cross_asset_robustness"] = {
        "passed": (assets_ok >= 3) and (worst > -0.50),
        "detail": robustness_detail,
    }
    criteria["C7_rejected_trades_materially_worse"] = {
        "passed": rejected_expectancy < accepted_expectancy - 0.10,
        "detail": f"Accepted {accepted_expectancy:+.4f}R vs rejected {rejected_expectancy:+.4f}R (need gap >= 0.10R)",
    }
    criteria["C8_no_unacceptable_liquidation_risk"] = {
        "passed": liquidation_violations == 0,
        "detail": f"{liquidation_violations} trades where estimated liquidation precedes SL (capped leverage)",
    }

    all_pass = all(c["passed"] for c in criteria.values())
    return {
        "criteria": criteria,
        "all_pass": all_pass,
        "status": "CANDIDATE_FOR_GOVERNANCE_REVIEW" if all_pass else "REJECTED",
        "live_execution_authorized": False,
        "execution_authority": "DETERMINISTIC_SMC",
        "ai_live_execution": "BLOCKED_BY_SYSTEM" if not all_pass else "BLOCKED_BY_SYSTEM (governance review required)",
    }
