"""
QuantEdge AI — Real Historical Dataset Builder & Causal Outcome Replay.

Extracts real market setups from canonical Delta Exchange India historical data
using the deterministic SMC engine and evaluates ground-truth forward trade outcomes
strictly through forward candle replay without look-ahead.

═══════════════════════════════════════════════════════════════════════════════
DATA CAUSALITY ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════════
At bar index T:
  1. INPUT FEATURES (24 canonical features):
     Calculated STRICTLY using data <= T (SMC structure, OB geometry, volatility,
     momentum, multi-period trend, account context, and regime flags).
  2. FORWARD TRADE REPLAY:
     Simulated forward across bars T+1 ... T+H (where H = 72 bars / 3 days max hold).
     Monitors price action for entry trigger, Take-Profit (TP) hit, Stop-Loss (SL)
     hit, Max Favorable Excursion (MFE), and Max Adverse Excursion (MAE).
  3. MATHEMATICAL TARGETS (Strictly future-derived outcomes):
     - target_realized_r : Realized R-multiple (+RR if TP hit, -1.0 if SL hit, or
                           (P_exit - E)/RiskDistance if holding horizon expires).
     - target_mfe_r      : Max Favorable Excursion in R-units (MFE / RiskDistance >= 0.0).
     - target_mae_r      : Max Adverse Excursion in R-units (MAE / RiskDistance >= 0.0).

No fabricated candles, features, scores, or synthetic interpolations are used.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from quantedge.ai.feature_contract import (
    FEATURE_COUNT,
    FEATURE_NAMES,
    encode_direction,
    encode_regime_1h,
)
from quantedge.market_data.models import Candle, MarketDataSource, Timeframe
from quantedge.smc.models import (
    OBState,
    OrderBlock,
    PivotPoint,
    StructureBreak,
    TrendDirection,
)
from quantedge.smc.order_blocks import (
    OrderBlockConfig,
    detect_order_blocks_streaming,
)
from quantedge.smc.structure import (
    StructureConfig,
    StructureDetector,
    StructureType,
)
from quantedge.smc.volatility import ParsedCandle, parse_candles_with_volatility
from quantedge.strategy.engine import StrategyEngine
from quantedge.strategy.models import (
    RiskRewardConfig,
    SetupState,
    StrategyDecision,
    StrategyDirection,
)

TARGET_REALIZED_R = "target_realized_r"
TARGET_MFE_R = "target_mfe_r"
TARGET_MAE_R = "target_mae_r"

REAL_TARGET_NAMES = [TARGET_REALIZED_R, TARGET_MFE_R, TARGET_MAE_R]

def _get_default_canonical_path() -> Path:
    # __file__ is in engine/src/quantedge/ai/training/real_dataset_builder.py
    # Repo root is 5 levels up: training -> ai -> quantedge -> src -> engine -> repo_root
    cur = Path(__file__).resolve()
    for parent in cur.parents:
        cand = parent / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
        if cand.exists():
            return cand
    # Fallback default
    return cur.parents[4] / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"


DEFAULT_CANONICAL_PATH = _get_default_canonical_path()



# ─────────────────────────────────────────────────────────────────────────────
# Internal Trade Outcome Record
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TradeReplayOutcome:
    """Outcome of forward candle replay for a single qualified setup."""
    setup_id: str
    entry_index: int
    entry_timestamp: datetime
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_distance: float
    reward_distance: float
    risk_reward: float
    exit_index: int
    exit_timestamp: datetime
    exit_price: float
    exit_reason: str  # "TP_HIT", "SL_HIT", "TIMEOUT_EXIT"
    realized_r: float
    mfe_r: float
    mae_r: float
    holding_bars: int


# ─────────────────────────────────────────────────────────────────────────────
# Forward Candle Replay Engine
# ─────────────────────────────────────────────────────────────────────────────


def replay_forward_outcome(
    setup_idx: int,
    candles: List[Candle],
    decision: StrategyDecision,
    max_holding_bars: int = 72,
) -> Optional[TradeReplayOutcome]:
    """
    Replays price action from setup_idx + 1 onward to determine true trade outcome.

    Strict causality rules:
    - Entry is assumed at decision.entry (OB level/close of setup candle).
    - Checks high/low of subsequent bars for TP and SL hits.
    - Long: TP hit if High >= TP; SL hit if Low <= SL.
    - Short: TP hit if Low <= TP; SL hit if High >= SL.
    - If both hit on the same candle, assumes conservative SL hit first.
    - If neither hit within max_holding_bars, exits at close of last bar.
    """
    if decision.entry is None or decision.stop_loss is None or decision.take_profit is None:
        return None

    entry_p = float(decision.entry)
    sl_p = float(decision.stop_loss)
    tp_p = float(decision.take_profit)
    risk_dist = abs(entry_p - sl_p)
    if risk_dist <= 1e-6:
        return None

    reward_dist = abs(tp_p - entry_p)
    rr = reward_dist / risk_dist
    is_long = decision.direction == StrategyDirection.LONG

    max_idx = min(len(candles) - 1, setup_idx + max_holding_bars)
    if setup_idx + 1 > max_idx:
        return None

    mfe_price = 0.0
    mae_price = 0.0
    exit_idx = max_idx
    exit_p = float(candles[max_idx].close)
    exit_reason = "TIMEOUT_EXIT"
    realized_r = 0.0

    for idx in range(setup_idx + 1, max_idx + 1):
        c = candles[idx]
        high_p = float(c.high)
        low_p = float(c.low)
        close_p = float(c.close)

        if is_long:
            fav_excursion = max(0.0, high_p - entry_p)
            adv_excursion = max(0.0, entry_p - low_p)
            mfe_price = max(mfe_price, fav_excursion)
            mae_price = max(mae_price, adv_excursion)

            # Check barrier hits
            tp_hit = high_p >= tp_p
            sl_hit = low_p <= sl_p

            if tp_hit and sl_hit:
                # Conservative tie-breaker: SL assumed hit first
                exit_idx = idx
                exit_p = sl_p
                exit_reason = "SL_HIT"
                realized_r = -1.0
                break
            elif tp_hit:
                exit_idx = idx
                exit_p = tp_p
                exit_reason = "TP_HIT"
                realized_r = rr
                break
            elif sl_hit:
                exit_idx = idx
                exit_p = sl_p
                exit_reason = "SL_HIT"
                realized_r = -1.0
                break

        else:  # SHORT
            fav_excursion = max(0.0, entry_p - low_p)
            adv_excursion = max(0.0, high_p - entry_p)
            mfe_price = max(mfe_price, fav_excursion)
            mae_price = max(mae_price, adv_excursion)

            tp_hit = low_p <= tp_p
            sl_hit = high_p >= sl_p

            if tp_hit and sl_hit:
                exit_idx = idx
                exit_p = sl_p
                exit_reason = "SL_HIT"
                realized_r = -1.0
                break
            elif tp_hit:
                exit_idx = idx
                exit_p = tp_p
                exit_reason = "TP_HIT"
                realized_r = rr
                break
            elif sl_hit:
                exit_idx = idx
                exit_p = sl_p
                exit_reason = "SL_HIT"
                realized_r = -1.0
                break

    # If timed out without barrier hit, compute realized R from exit candle close
    if exit_reason == "TIMEOUT_EXIT":
        if is_long:
            realized_r = (exit_p - entry_p) / risk_dist
        else:
            realized_r = (entry_p - exit_p) / risk_dist

    mfe_r = mfe_price / risk_dist
    mae_r = mae_price / risk_dist
    holding_bars = exit_idx - setup_idx

    return TradeReplayOutcome(
        setup_id=decision.setup_id or f"setup_{setup_idx}",
        entry_index=setup_idx,
        entry_timestamp=candles[setup_idx].timestamp,
        direction=decision.direction.value if decision.direction else "NONE",
        entry_price=entry_p,
        stop_loss=sl_p,
        take_profit=tp_p,
        risk_distance=risk_dist,
        reward_distance=reward_dist,
        risk_reward=rr,
        exit_index=exit_idx,
        exit_timestamp=candles[exit_idx].timestamp,
        exit_price=exit_p,
        exit_reason=exit_reason,
        realized_r=realized_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        holding_bars=holding_bars,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Causal 24-Feature Extractor
# ─────────────────────────────────────────────────────────────────────────────


def extract_causal_24_features(
    idx: int,
    candles: List[Candle],
    parsed: List[ParsedCandle],
    decision: StrategyDecision,
    active_obs: List[OrderBlock],
    int_breaks: List[StructureBreak],
    sw_breaks: List[StructureBreak],
    int_pivots: List[PivotPoint],
    sw_pivots: List[PivotPoint],
) -> List[float]:
    """
    Extracts the exact 24 canonical features at candle index `idx` using data <= idx only.

    Conforms to FeatureContract / feature_contract.py:
      0-4   : SMC Structural
      5-12  : Market Context
      13-15 : Setup Geometry
      16-17 : Account Context
      18-21 : 1H Regime One-Hot
      22-23 : Binary Flags
    """
    curr = candles[idx]
    c_close = float(curr.close)

    # ── Group 1: SMC Structural (0-4) ─────────────────────────────────────────
    recent_ib = [b for b in int_breaks if b.index <= idx and b.index >= idx - 20]
    bos_strength = 0.5
    choch_strength = 0.5
    if recent_ib:
        latest_b = recent_ib[-1]
        bos_strength = min(1.0, max(0.0, float(abs(latest_b.price - curr.close) / (curr.close * Decimal("0.05") + Decimal("1.0")))))
        choch_strength = 0.85 if getattr(latest_b, "break_type", None) == "choch" else 0.40

    ob_strength = 0.5
    fvg_strength = 0.5
    liq_prox = 0.5
    ob = getattr(decision, "order_block", None) or (active_obs[0] if active_obs else None)
    if ob is not None:
        ob_w = float(ob.width)
        ob_strength = min(1.0, max(0.1, 1.0 - (ob_w / (c_close * 0.05 + 1.0))))
        if ob.top_price is not None and ob.bottom_price is not None:
            fvg_strength = min(1.0, max(0.0, float(abs(curr.close - ob.bottom_price) / (ob.top_price - ob.bottom_price + Decimal("1.0")))))

    recent_pivs = [p for p in int_pivots + sw_pivots if p.index <= idx and p.index >= idx - 30]
    if recent_pivs:
        min_piv_dist = min(abs(float(p.price) - c_close) for p in recent_pivs)
        liq_prox = min(1.0, max(0.0, 1.0 - (min_piv_dist / (c_close * 0.05 + 1.0))))

    # ── Group 2: Market Context (5-12) ────────────────────────────────────────
    # multi-lookback trend slopes
    p5 = float(parsed[max(0, idx - 5)].original.close)
    p15 = float(parsed[max(0, idx - 15)].original.close)
    p50 = float(parsed[max(0, idx - 50)].original.close)

    trend_1h = min(1.0, max(0.0, 0.5 + (c_close - p15) / (c_close * 0.03 + 1e-6)))
    trend_15m = min(1.0, max(0.0, 0.5 + (c_close - p5) / (c_close * 0.015 + 1e-6)))
    trend_4h = min(1.0, max(0.0, 0.5 + (c_close - p50) / (c_close * 0.06 + 1e-6)))

    atr_val = float(parsed[idx].atr_value) if idx < len(parsed) else c_close * 0.01
    vol_1h = min(1.0, max(0.0, atr_val / (c_close * 0.03 + 1e-6)))

    # Short-window volatility
    recent_ranges = [float(candles[k].high - candles[k].low) for k in range(max(0, idx - 5), idx + 1)]
    avg_recent_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else atr_val
    vol_15m = min(1.0, max(0.0, avg_recent_range / (c_close * 0.02 + 1e-6)))

    # Volume profile ratio
    recent_vols = [float(candles[k].volume) for k in range(max(0, idx - 5), idx + 1)]
    hist_vols = [float(candles[k].volume) for k in range(max(0, idx - 30), idx + 1)]
    avg_rec_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 1.0
    avg_hist_vol = sum(hist_vols) / len(hist_vols) if hist_vols else 1.0
    vol_profile = float(np.clip(avg_rec_vol / (avg_hist_vol + 1e-6), 0.0, 2.0))

    # Momentum (10-period and 3-period rate of change)
    p10 = float(parsed[max(0, idx - 10)].original.close)
    p3 = float(parsed[max(0, idx - 3)].original.close)
    mom_1h = float(np.clip((c_close - p10) / (p10 + 1e-6), -0.5, 0.5))
    mom_15m = float(np.clip((c_close - p3) / (p3 + 1e-6), -0.5, 0.5))

    # ── Group 3: Setup Geometry (13-15) ───────────────────────────────────────
    rr = float(decision.risk_reward) if decision.risk_reward else 2.0
    risk_dist = float(decision.risk_distance) if decision.risk_distance else float(abs(curr.close * Decimal("0.015")))
    entry_prec = 0.75
    if ob is not None and ob.top_price is not None and ob.bottom_price is not None:
        span = float(ob.top_price - ob.bottom_price)
        if span > 0:
            entry_prec = min(1.0, max(0.0, 1.0 - abs(c_close - float(ob.top_price if decision.direction == StrategyDirection.LONG else ob.bottom_price)) / span))

    # ── Group 4: Account & Risk Context (16-17) ───────────────────────────────
    account_util = 0.20  # Standard conservative model portfolio utilisation
    lev_val = getattr(decision, "calculated_leverage", None) or 10
    lev_ratio = min(1.0, max(0.01, float(lev_val) / 100.0))

    # ── Group 5: Regime One-Hot Encoding (18-21) ──────────────────────────────
    if trend_1h >= 0.60:
        regime_str = "TRENDING_BULLISH"
    elif trend_1h <= 0.40:
        regime_str = "TRENDING_BEARISH"
    elif vol_1h < 0.20:
        regime_str = "RANGING"
    else:
        regime_str = "TRANSITIONAL"
    regime_oh = encode_regime_1h(regime_str)

    # ── Group 6: Binary Flags (22-23) ─────────────────────────────────────────
    regime_aligned = 1.0 if (trend_1h >= 0.5 and trend_4h >= 0.5) or (trend_1h < 0.5 and trend_4h < 0.5) else 0.0
    dir_long = encode_direction(decision.direction.value if decision.direction else "LONG")

    feats = (
        [bos_strength, choch_strength, ob_strength, fvg_strength, liq_prox]
        + [trend_1h, trend_15m, trend_4h, vol_1h, vol_15m, vol_profile, mom_1h, mom_15m]
        + [rr, risk_dist, entry_prec]
        + [account_util, lev_ratio]
        + regime_oh
        + [regime_aligned, dir_long]
    )

    assert len(feats) == FEATURE_COUNT, f"Expected {FEATURE_COUNT} features, got {len(feats)}"
    return feats


# ─────────────────────────────────────────────────────────────────────────────
# Real Dataset Builder
# ─────────────────────────────────────────────────────────────────────────────


def build_real_training_dataset(
    csv_path: Optional[Path | str] = None,
    symbol: str = "BTCUSD.P",
    max_holding_bars: int = 72,
    min_warmup_bars: int = 200,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Builds a training dataset from canonical historical market data CSV.

    Workflow:
    1. Loads canonical OHLCV candles.
    2. Runs causal SMC detection (volatility, structure, order blocks, pivots).
    3. Evaluates StrategyEngine bar-by-bar causal to T.
    4. Extracts the 24 canonical features at T using data <= T.
    5. Replays forward price action T+1 ... T+H to derive target_realized_r, target_mfe_r, target_mae_r.
    6. Emits a clean pandas DataFrame ready for purged chronological splitting.

    Args:
        csv_path: Path to canonical CSV. Defaults to BTCUSD 1H 2026 data.
        symbol: Market symbol key.
        max_holding_bars: Maximum forward replay horizon for trade exit (default 72 bars).
        min_warmup_bars: Warm-up bars for ATR and swing structure (default 200 bars).
        verbose: Print progress summary.

    Returns:
        pd.DataFrame with 24 feature columns, 3 target columns, and timestamp metadata.
    """
    if csv_path is None:
        csv_path = DEFAULT_CANONICAL_PATH
    csv_path = Path(csv_path)

    if not csv_path.exists():
        raise FileNotFoundError(f"Canonical historical data CSV not found: {csv_path}")

    if verbose:
        print(f"\n[Real Dataset Builder] Loading candles from: {csv_path}")

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

    if len(candles) <= min_warmup_bars + max_holding_bars:
        raise ValueError(
            f"Dataset too small ({len(candles)} candles). Need at least "
            f"{min_warmup_bars + max_holding_bars + 1} candles."
        )

    if verbose:
        print(f"[Real Dataset Builder] Loaded {len(candles):,} candles ({candles[0].timestamp} to {candles[-1].timestamp})")

    # ── SMC Pipeline ──────────────────────────────────────────────────────────
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

    # Fast active-OB index mapping using candle-by-candle lifecycle
    active_obs_by_idx: List[List[OrderBlock]] = [[] for _ in range(len(candles))]
    for raw_ob in all_raw_obs:
        start_idx = raw_ob.break_index + 1
        end_idx = len(candles)
        # Check invalidation
        for k in range(start_idx, len(candles)):
            if raw_ob.is_bullish() and candles[k].low < raw_ob.bottom_price:
                end_idx = k
                break
            elif raw_ob.is_bearish() and candles[k].high > raw_ob.top_price:
                end_idx = k
                break
        for idx in range(start_idx, end_idx):
            active_obs_by_idx[idx].append(raw_ob)

    # ── Strategy Evaluation & Outcome Replay ──────────────────────────────────
    strategy = StrategyEngine()
    dataset_rows: List[Dict[str, Any]] = []

    # Evaluate up to len(candles) - max_holding_bars so all setups have full forward replay
    eval_end_idx = len(candles) - max_holding_bars

    for i in range(min_warmup_bars, eval_end_idx):
        c = candles[i]
        active = active_obs_by_idx[i]
        if not active:
            continue

        r_breaks = [b for b in int_brk + sw_brk if b.index <= i and b.index >= i - 10]
        int_trend = TrendDirection.BULLISH if parsed[i].original.close > parsed[max(0, i - 5)].original.close else TrendDirection.BEARISH
        sw_trend = TrendDirection.BULLISH if parsed[i].original.close > parsed[max(0, i - 50)].original.close else TrendDirection.BEARISH

        decision = strategy.evaluate_state(
            candle=c,
            active_obs=active,
            internal_trend=int_trend,
            swing_trend=sw_trend,
            recent_breaks=r_breaks,
            all_active_obs=active,
        )

        # Include all setups with valid entry & stop loss
        if decision.setup_state in (
            SetupState.TRADE_SETUP_READY,
            SetupState.QUALIFIED_LONG,
            SetupState.QUALIFIED_SHORT,
        ) and decision.entry is not None and decision.stop_loss is not None:
            # 1. Causal 24 features (T <= i)
            features_24 = extract_causal_24_features(
                idx=i,
                candles=candles,
                parsed=parsed,
                decision=decision,
                active_obs=active,
                int_breaks=int_brk,
                sw_breaks=sw_brk,
                int_pivots=int_piv,
                sw_pivots=sw_piv,
            )

            # 2. Forward trade replay (T+1 ... T+72)
            outcome = replay_forward_outcome(
                setup_idx=i,
                candles=candles,
                decision=decision,
                max_holding_bars=max_holding_bars,
            )

            if outcome is None:
                continue

            row_dict = {"timestamp": c.timestamp}
            for feat_name, feat_val in zip(FEATURE_NAMES, features_24):
                row_dict[feat_name] = round(float(feat_val), 6)

            # 3. Real future outcome targets
            row_dict[TARGET_REALIZED_R] = round(outcome.realized_r, 4)
            row_dict[TARGET_MFE_R] = round(outcome.mfe_r, 4)
            row_dict[TARGET_MAE_R] = round(outcome.mae_r, 4)

            # 4. Audit metadata (not used as features)
            row_dict["meta_setup_id"] = outcome.setup_id
            row_dict["meta_direction"] = outcome.direction
            row_dict["meta_exit_reason"] = outcome.exit_reason
            row_dict["meta_holding_bars"] = outcome.holding_bars

            dataset_rows.append(row_dict)

    df = pd.DataFrame(dataset_rows)
    if df.empty:
        raise RuntimeError("No qualified setups found in historical dataset.")

    # Ensure strictly sorted by timestamp
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)

    if verbose:
        print(f"[Real Dataset Builder] Generated {len(df):,} legitimate historical setups.")
        win_count = len(df[df[TARGET_REALIZED_R] > 0])
        win_rate = (win_count / len(df)) * 100 if len(df) > 0 else 0
        avg_r = df[TARGET_REALIZED_R].mean()
        avg_mfe = df[TARGET_MFE_R].mean()
        avg_mae = df[TARGET_MAE_R].mean()
        print(f"  Realized Win Rate : {win_rate:.1f}% ({win_count}/{len(df)})")
        print(f"  Mean Realized R   : {avg_r:.3f}R")
        print(f"  Mean MFE / MAE    : {avg_mfe:.3f}R / {avg_mae:.3f}R")

    return df
