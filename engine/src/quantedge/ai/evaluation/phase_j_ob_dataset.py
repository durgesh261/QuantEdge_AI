"""
QuantEdge AI — Phase J OB-Centric Causal Dataset Builder.

═══════════════════════════════════════════════════════════════════════════════
PURPOSE
═══════════════════════════════════════════════════════════════════════════════
Phase I proved that the frozen Phase H model produces statistically unusable
coverage (4/99 OOS acceptances) when applied to REAL Order-Block trades.  The
canonical-24 feature contract is candle-heavy and carries only thin OB-quality
information (order_block_strength, fvg_strength, entry_precision).

Phase J rebuilds the MODEL INPUT (not the strategy!) around the actual OB event:

    ONE DATASET ROW == ONE UNIQUE ORDER BLOCK TRADE OPPORTUNITY
    (the same unique setups extracted by the Phase I authoritative replay)

Every feature is computed STRICTLY from information available at the decision
bar T (candles, pivots and breaks with index <= T).  Labels are the REAL trade
outcomes produced by the application's own forward replay conventions
(replay_forward_outcome: second-edge SL, PHASE_I_OB_60TP_35SL TP, SL-first
intrabar policy, 72h horizon).

No synthetic candles. No parallel SMC detector. No future information.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from quantedge.ai.evaluation.phase_i_ob_replay import (
    PHASE_I_TP_RR_CONFIG,
    REPLAY_HORIZON_BARS,
    WARMUP_BARS,
    SMCContext,
    load_canonical_candles,
    extract_phase_i_setups,
)
from quantedge.ai.training.real_dataset_builder import (
    TradeReplayOutcome,
    replay_forward_outcome,
)
from quantedge.strategy.models import StrategyDecision, StrategyDirection

# ═════════════════════════════════════════════════════════════════════════════
# Feature contract (Phase J research contract — NOT the production canonical-24)
# ═════════════════════════════════════════════════════════════════════════════

OB_FEATURE_NAMES: Tuple[str, ...] = (
    # ── A. OB geometry (scale-invariant) ──────────────────────────────────────
    "ob_width_pct",            # OB zone width / entry price * 100
    "ob_width_atr",            # OB width / ATR(T)
    "stop_distance_pct",       # |entry-SL| / entry * 100   (second-edge SL)
    "stop_distance_atr",       # |entry-SL| / ATR(T)
    "entry_depth_in_zone",     # close position inside zone at decision (0=near far edge, 1=near entry edge)
    "mitigation_depth_pct",    # deepest penetration into zone before decision / width
    "formation_body_ratio",    # |close-open| / (high-low) of the OB candle
    "formation_range_atr",     # OB candle range / ATR(T)
    "displacement_atr",        # |break candle close-open| / ATR(T)
    "bars_since_formation",    # OB age at decision
    "bars_since_break",        # confirmation latency
    "pre_decision_retests",    # zone overlaps between break and decision
    "price_to_entry_atr",      # |close(T)-entry| / ATR(T)
    # ── B. Market structure ───────────────────────────────────────────────────
    "is_bos",                  # structural break was BOS
    "is_choch",                # structural break was CHOCH
    "origin_swing",            # OB originated from swing (vs internal) structure
    "trend_align_internal",    # setup direction agrees with internal trend proxy
    "trend_align_swing",       # setup direction agrees with swing trend proxy
    "premium_discount",        # close(T) position within trailing 100-bar range (0=low, 1=high)
    "dist_nearest_pivot_atr",  # distance to nearest confirmed pivot / ATR(T)
    # ── C. Volatility regime ──────────────────────────────────────────────────
    "atr_pct",                 # ATR(T) / close * 100
    "atr_percentile",          # percentile of ATR(T) within trailing 200 ATR values
    "realized_vol_20",         # stdev of last 20 one-bar returns (%)
    "vol_expansion",           # last closed bar range / ATR(T)
    # ── D. Momentum & participation ───────────────────────────────────────────
    "ret_5",                   # 5-bar return %
    "ret_15",                  # 15-bar return %
    "ret_50",                  # 50-bar return %
    "volume_ratio",            # avg(volume[-5:]) / avg(volume[-30:])
    # ── E. Direction ──────────────────────────────────────────────────────────
    "direction_long",          # 1.0 LONG / 0.0 SHORT
)

FEATURE_DIM = len(OB_FEATURE_NAMES)

LABEL_REALIZED_R = "label_realized_r"
LABEL_TP_FIRST = "label_tp_first"
LABEL_MFE_R = "label_mfe_r"
LABEL_MAE_R = "label_mae_r"
LABEL_HOLDING_BARS = "label_holding_bars"


# ═════════════════════════════════════════════════════════════════════════════
# Causal OB feature extraction
# ═════════════════════════════════════════════════════════════════════════════


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if abs(b) > 1e-12 else default


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def extract_ob_causal_features(
    setup,
    candles: Sequence,
    ctx: SMCContext,
) -> Tuple[float, ...]:
    """
    Extracts the Phase J OB-centric feature vector at the setup's decision bar.

    CAUSALITY: reads candles[<= decision_bar] only.  Pivots/breaks filtered by
    index <= decision_bar.  OB properties (formation/break indices) precede the
    decision bar by construction of the Phase I extractor.
    """
    i = setup.decision_bar
    c = candles[i]
    close = float(c.close)
    atr = float(ctx.parsed[i].atr_value) if ctx.parsed[i].atr_value else close * 0.01

    ob_high, ob_low = setup.ob_high, setup.ob_low
    width = max(ob_high - ob_low, 1e-12)
    is_long = setup.direction == "LONG"
    entry_edge = ob_high if is_long else ob_low      # near edge (where entry sits)
    far_edge = ob_low if is_long else ob_high

    # ── A. Geometry ───────────────────────────────────────────────────────────
    ob_width_pct = _safe_div(width, close) * 100.0
    ob_width_atr = _safe_div(width, atr)
    stop_pct = setup.stop_distance_percent
    stop_atr = _safe_div(setup.risk_distance, atr)

    close_in_zone = _clip(_safe_div(close - ob_low, width))
    entry_edge_frac = _clip(_safe_div(entry_edge - ob_low, width))
    # Depth of the current close measured FROM THE ENTRY EDGE back into the zone.
    entry_depth_in_zone = _clip(1.0 - abs(close - entry_edge) / width)

    # Deepest penetration into the zone between break+1 and decision (causal scan)
    brk_idx = setup.break_index if setup.break_index >= 0 else 0
    form_idx = setup.formation_index if setup.formation_index >= 0 else 0
    mit_depth = 0.0
    for k in range(brk_idx + 1, i + 1):
        ck = candles[k]
        if is_long:
            depth = _safe_div(ob_high - float(ck.low), width)
        else:
            depth = _safe_div(float(ck.high) - ob_low, width)
        mit_depth = max(mit_depth, _clip(depth))

    fc = candles[form_idx]
    f_range = max(float(fc.high) - float(fc.low), 1e-12)
    formation_body_ratio = _clip(abs(float(fc.close) - float(fc.open)) / f_range)
    formation_range_atr = _safe_div(f_range, atr)

    bc = candles[brk_idx]
    displacement_atr = _safe_div(abs(float(bc.close) - float(bc.open)), atr)

    bars_since_formation = float(i - form_idx)
    bars_since_break = float(i - brk_idx)

    retests = 0
    for k in range(brk_idx + 1, i):
        ck = candles[k]
        if float(ck.low) <= ob_high and float(ck.high) >= ob_low:
            retests += 1
    pre_decision_retests = float(retests)

    price_to_entry_atr = _safe_div(abs(close - setup.entry_price), atr)

    # ── B. Structure ──────────────────────────────────────────────────────────
    struct_id = setup.structural_event_id or ""
    is_bos = 1.0 if "|bos|" in struct_id else 0.0
    is_choch = 1.0 if "|choch|" in struct_id else 0.0
    origin_swing = 1.0 if getattr(setup, "structure_origin", "internal") == "swing" else 0.0

    int_trend_up = close > float(candles[max(0, i - 5)].close)
    sw_trend_up = close > float(candles[max(0, i - 50)].close)
    up_dir = is_long
    trend_align_internal = 1.0 if int_trend_up == up_dir else 0.0
    trend_align_swing = 1.0 if sw_trend_up == up_dir else 0.0

    lookback = candles[max(0, i - 99) : i + 1]
    hi100 = max(float(x.high) for x in lookback)
    lo100 = min(float(x.low) for x in lookback)
    premium_discount = _clip(_safe_div(close - lo100, max(hi100 - lo100, 1e-12)))

    pivots = [p for p in (ctx.int_pivots + ctx.sw_pivots) if p.index <= i]
    if pivots:
        nearest = min(pivots, key=lambda p: abs(float(p.price) - close))
        dist_nearest_pivot_atr = _safe_div(abs(float(nearest.price) - close), atr)
    else:
        dist_nearest_pivot_atr = 1.0

    # ── C. Volatility ─────────────────────────────────────────────────────────
    atr_pct = _safe_div(atr, close) * 100.0
    lo_atr = max(0, i - 199)
    atr_history = [
        float(ctx.parsed[k].atr_value)
        for k in range(lo_atr, i + 1)
        if ctx.parsed[k].atr_value
    ]
    if atr_history:
        cur = atr_history[-1]
        atr_percentile = 100.0 * sum(1 for v in atr_history if v <= cur) / len(atr_history)
    else:
        atr_percentile = 50.0

    rets = []
    for k in range(max(1, i - 19), i + 1):
        prev = float(candles[k - 1].close)
        rets.append(_safe_div(float(candles[k].close) - prev, prev) * 100.0)
    realized_vol_20 = float(np.std(rets)) if len(rets) > 1 else 0.0

    vol_expansion = _clip(_safe_div(float(c.high) - float(c.low), atr), 0.0, 5.0)

    # ── D. Momentum & participation ───────────────────────────────────────────
    def _ret(n: int) -> float:
        base = float(candles[max(0, i - n)].close)
        return _safe_div(close - base, base) * 100.0

    ret_5, ret_15, ret_50 = _ret(5), _ret(15), _ret(50)

    vols_rec = [float(candles[k].volume) for k in range(max(0, i - 4), i + 1)]
    vols_base = [float(candles[k].volume) for k in range(max(0, i - 29), i + 1)]
    volume_ratio = _safe_div(np.mean(vols_rec), np.mean(vols_base), 1.0)

    feats = (
        ob_width_pct, ob_width_atr, stop_pct, stop_atr,
        entry_depth_in_zone, mit_depth, formation_body_ratio, formation_range_atr,
        displacement_atr, bars_since_formation, bars_since_break,
        pre_decision_retests, price_to_entry_atr,
        is_bos, is_choch, origin_swing, trend_align_internal, trend_align_swing,
        premium_discount, dist_nearest_pivot_atr,
        atr_pct, atr_percentile, realized_vol_20, vol_expansion,
        ret_5, ret_15, ret_50, volume_ratio,
        1.0 if is_long else 0.0,
    )
    assert len(feats) == FEATURE_DIM, f"expected {FEATURE_DIM}, got {len(feats)}"
    return tuple(round(float(v), 6) for v in feats)


# ═════════════════════════════════════════════════════════════════════════════
# Dataset assembly — ONE ROW PER UNIQUE REAL OB TRADE OPPORTUNITY
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PhaseJSample:
    """One unique OB trade opportunity with causal features + real outcome."""
    setup_id: str
    asset: str
    decision_bar: int
    decision_time: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    stop_distance_percent: float
    leverage: int
    features: Tuple[float, ...]
    realized_r: float
    tp_first: float
    mfe_r: float
    mae_r: float
    holding_bars: int
    exit_reason: str
    net_r: float
    cost_r: float


def build_phase_j_dataset(
    canonical_base,
    symbols: Sequence[str] = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"),
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Builds the pooled Phase J dataset from the REAL Phase I OB setups.

    Reuses (unmodified):
      - extract_phase_i_setups  (authoritative setup universe, one row per OB)
      - replay_forward_outcome  (authoritative forward trade outcome)
      - compute_net_r cost conventions from Phase I
    """
    from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, compute_net_r

    rows: List[Dict[str, Any]] = []
    for sym in symbols:
        candles = load_canonical_candles(canonical_base, sym)
        ctx = build_smc_context(candles)
        setups, audit = extract_phase_i_setups(candles, sym, ctx=ctx)
        if verbose:
            print(f"  [PhaseJ dataset] {sym}: {len(setups)} unique OB setups")
        for s in setups:
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
            feats = extract_ob_causal_features(s, candles, ctx)
            stop_frac = s.stop_distance_percent / 100.0
            net_r, cost_r = compute_net_r(
                outcome.realized_r, s.entry_price, stop_frac, float(outcome.holding_bars)
            )
            rows.append(
                {
                    "setup_id": s.setup_id,
                    "asset": sym,
                    "decision_bar": s.decision_bar,
                    "decision_time": s.decision_time,
                    "direction": s.direction,
                    "entry_price": s.entry_price,
                    "sl_price": s.sl_price,
                    "tp_price": s.tp_price,
                    "stop_distance_percent": s.stop_distance_percent,
                    "leverage": s.leverage,
                    **{name: val for name, val in zip(OB_FEATURE_NAMES, feats)},
                    LABEL_REALIZED_R: round(outcome.realized_r, 6),
                    LABEL_TP_FIRST: 1.0 if outcome.exit_reason == "TP_HIT" else 0.0,
                    LABEL_MFE_R: round(outcome.mfe_r, 6),
                    LABEL_MAE_R: round(outcome.mae_r, 6),
                    LABEL_HOLDING_BARS: outcome.holding_bars,
                    "exit_reason": outcome.exit_reason,
                    "net_r": round(net_r, 6),
                    "cost_r": round(cost_r, 6),
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("Phase J dataset assembly produced zero samples")
    df.sort_values("decision_time", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Data hygiene guards
    feat_vals = df[list(OB_FEATURE_NAMES)].to_numpy(dtype=float)
    if not np.isfinite(feat_vals).all():
        bad = np.argwhere(~np.isfinite(feat_vals))
        raise ValueError(f"Non-finite feature values detected at rows: {bad[:5]}")
    return df


# ═════════════════════════════════════════════════════════════════════════════
# Ablation feature sets (Step 12)
# ═════════════════════════════════════════════════════════════════════════════

ABLATION_SETS: Dict[str, Tuple[str, ...]] = {
    "A_candle_only": (
        "atr_pct", "atr_percentile", "realized_vol_20", "vol_expansion",
        "ret_5", "ret_15", "ret_50", "volume_ratio", "premium_discount",
        "direction_long",
    ),
    "B_ob_geometry_only": (
        "ob_width_pct", "ob_width_atr", "stop_distance_pct", "stop_distance_atr",
        "entry_depth_in_zone", "mitigation_depth_pct", "formation_body_ratio",
        "formation_range_atr", "displacement_atr", "bars_since_formation",
        "bars_since_break", "pre_decision_retests", "price_to_entry_atr",
        "direction_long",
    ),
    "C_structure_only": (
        "is_bos", "is_choch", "origin_swing", "trend_align_internal",
        "trend_align_swing", "premium_discount", "dist_nearest_pivot_atr",
        "direction_long",
    ),
    "D_geometry_plus_structure": (
        "ob_width_pct", "ob_width_atr", "stop_distance_pct", "stop_distance_atr",
        "entry_depth_in_zone", "mitigation_depth_pct", "formation_body_ratio",
        "formation_range_atr", "displacement_atr", "bars_since_formation",
        "bars_since_break", "pre_decision_retests", "price_to_entry_atr",
        "is_bos", "is_choch", "origin_swing", "trend_align_internal",
        "trend_align_swing", "premium_discount", "dist_nearest_pivot_atr",
        "direction_long",
    ),
    "E_full_causal_ob": tuple(OB_FEATURE_NAMES),
}
