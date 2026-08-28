"""
Manual SMC — OB Geometry & Wick Predicates (Phase 1 Step 1 extraction).
======================================================================

VERBATIM EXTRACTION from the frozen research oracle:
    engine/src/quantedge/ai/research/displacement_gated_retest_engine.py

Extracted symbols (oracle line refs at extraction time):
    _make_manual_ob           (oracle L1290)
    _manual_distal_breached   (oracle L1343)
    _manual_entry_touched     (oracle L1355)
    _manual_sl_hit            (oracle L1362)
    _manual_tp_hit            (oracle L1369)

EXTRACTION CONTRACT
-------------------
Arithmetic is copied expression-for-expression. It has deliberately NOT
been "cleaned up":

  * float throughout — no Decimal, no rounding, no epsilon rework;
  * the `if entry > 1e-9` and `if sl_dist_pct > 1e-9` guards and their
    fallback values (0.0 and 1.0) are preserved exactly;
  * `applied_lev = min(cap, theo_lev)` CLAMPS and never raises. Raising
    when theoretical leverage exceeds the cap would be a behavioural
    change and is prohibited;
  * the ob_id format string is byte-identical.

No production wiring. No execution wiring. Nothing imports this module yet.
"""

from __future__ import annotations

from datetime import datetime

from quantedge.strategy.manual_smc.models import ManualOBRecord, ManualSpecConfig


# ---------------------------------------------------------------------------
# OB construction
# ---------------------------------------------------------------------------
def _make_manual_ob(
    asset: str,
    bos_bar_idx: int,
    bos_dt: datetime,
    origin_bar_idx: int,
    origin_dt: datetime,
    direction: str,
    ob_top: float,
    ob_bottom: float,
    cfg: ManualSpecConfig,
) -> ManualOBRecord:
    """Construct a ManualOBRecord from scanner-detected BOS event."""
    width = ob_top - ob_bottom
    if direction == "SHORT":
        proximal  = ob_bottom
        distal    = ob_top           # = origin.close (critical)
        entry     = ob_bottom + cfg.entry_depth_pct * width
        tp        = entry * (1.0 - cfg.fixed_tp_market_pct / 100.0)
    else:   # LONG
        proximal  = ob_top
        distal    = ob_bottom        # = origin.close (critical)
        entry     = ob_top - cfg.entry_depth_pct * width
        tp        = entry * (1.0 + cfg.fixed_tp_market_pct / 100.0)

    sl          = distal
    risk_dist   = abs(entry - sl)
    sl_dist_pct = (risk_dist / entry) * 100.0 if entry > 1e-9 else 0.0
    theo_lev    = cfg.max_sl_account_risk_pct / sl_dist_pct if sl_dist_pct > 1e-9 else 1.0
    applied_lev = min(cfg.applied_leverage_cap, theo_lev)

    ob_id = f"MANUAL_{asset}_{direction}_{origin_bar_idx}_{bos_bar_idx}"
    return ManualOBRecord(
        ob_id=ob_id,
        asset=asset,
        direction=direction,
        origin_bar_idx=origin_bar_idx,
        bos_bar_idx=bos_bar_idx,
        bos_dt=bos_dt,
        formation_dt=origin_dt,
        ob_top=ob_top,
        ob_bottom=ob_bottom,
        ob_width=width,
        proximal=proximal,
        distal=distal,
        entry_price=entry,
        sl_price=sl,
        tp_price=tp,
        sl_dist_pct=sl_dist_pct,
        theoretical_leverage=theo_lev,
        applied_leverage=applied_lev,
    )


# ---------------------------------------------------------------------------
# Wick-based lifecycle predicates
# ---------------------------------------------------------------------------
def _manual_distal_breached(ob: ManualOBRecord, c_h: float, c_l: float) -> bool:
    """
    Wick-based distal boundary check (pre-entry invalidation).

    SHORT: candle.high >= ob_top  (ob_top = origin.close)
    LONG:  candle.low  <= ob_bottom (ob_bottom = origin.close)
    """
    if ob.direction == "SHORT":
        return c_h >= ob.distal
    return c_l <= ob.distal


def _manual_entry_touched(ob: ManualOBRecord, c_h: float, c_l: float) -> bool:
    """Check if the 25%-depth entry level is touched by the candle wick."""
    if ob.direction == "SHORT":
        return c_h >= ob.entry_price
    return c_l <= ob.entry_price


def _manual_sl_hit(direction: str, c_h: float, c_l: float, sl: float) -> bool:
    """Post-entry stop-loss check (wick-based)."""
    if direction == "SHORT":
        return c_h >= sl
    return c_l <= sl


def _manual_tp_hit(direction: str, c_h: float, c_l: float, tp: float) -> bool:
    """Post-entry take-profit check (wick-based)."""
    if direction == "SHORT":
        return c_l <= tp
    return c_h >= tp


# ---------------------------------------------------------------------------
# Production-facing aliases.
# Provably behaviour-neutral: plain name bindings to the same function
# objects. No wrappers, no argument reordering, no defaults introduced.
# ---------------------------------------------------------------------------
make_manual_ob = _make_manual_ob
manual_distal_breached = _manual_distal_breached
manual_entry_touched = _manual_entry_touched
manual_sl_hit = _manual_sl_hit
manual_tp_hit = _manual_tp_hit

__all__ = [
    "_make_manual_ob",
    "_manual_distal_breached",
    "_manual_entry_touched",
    "_manual_sl_hit",
    "_manual_tp_hit",
    "make_manual_ob",
    "manual_distal_breached",
    "manual_entry_touched",
    "manual_sl_hit",
    "manual_tp_hit",
]
