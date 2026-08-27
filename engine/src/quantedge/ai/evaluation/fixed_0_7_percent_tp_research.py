"""
QuantEdge AI — Fixed 0.7% Price-Target TP Research Engine.

Implements the controlled research experiment testing a fixed 0.7% price movement target
with a 35% maximum margin-risk / SL convention across canonical SMC Order Blocks.

Key Specifications:
- Entry: 25% penetration inside Order Block.
  - Bullish: entry = ob_high - 0.25 * OB_width
  - Bearish: entry = ob_low + 0.25 * OB_width
- Stop Loss: Distal OB boundary (ob_low for Long, ob_high for Short).
- Take Profit: Fixed 0.7% market price movement from entry.
  - Long: TP = entry * 1.007
  - Short: TP = entry * 0.993
- Risk / SL Convention: 35% maximum loss on allocated margin.
- Global 1-trade-at-a-time portfolio lock across BTCUSD, ETHUSD, SOLUSD, XRPUSD.
- Continuous full account compounding from a $10.00 base.

Governance Invariants:
- live_execution_authorized = false
- AI_PROMOTION_STATUS = REJECTED
- execution_status = BLOCKED_BY_SYSTEM
- Deterministic SMC remains the sole production authority.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


@dataclass
class Fixed07TradeRecord:
    """Detailed trade record in the Fixed 0.7% TP compounding ledger."""
    trade_number: int
    datetime: str
    exit_datetime: str
    asset: str
    direction: str
    ob_id: str
    ob_high: float
    ob_low: float
    ob_width: float
    ob_width_pct: float
    entry: float
    sl: float
    tp: float
    sl_distance: float
    tp_distance: float
    tp_price_move_pct: float
    planned_rr: float
    leverage: int
    starting_capital_35pct: float
    notional_size_35pct: float
    margin_used_35pct: float
    risk_amount_35pct: float
    gross_pnl_35pct: float
    fees_35pct: float
    net_pnl_35pct: float
    ending_capital_35pct: float
    starting_capital_10pct: float
    net_pnl_10pct: float
    ending_capital_10pct: float
    starting_capital_1pct: float
    net_pnl_1pct: float
    ending_capital_1pct: float
    outcome: str
    r_multiple: float
    holding_time_hours: int
    exit_reason: str
    global_trade_lock_state: str
    ambiguity_status: str


class Fixed07PercentTPResearchEngine:
    """
    Evaluates the Fixed 0.7% TP Strategy across canonical multi-year SMC data.
    """

    def __init__(self, master_df: pd.DataFrame, starting_capital: float = 10.0):
        self.master_df = master_df.copy()
        self.starting_capital = starting_capital

    def run_backtest(self) -> Dict[str, Any]:
        """Runs the deterministic chronological backtest with global 1-trade lock."""
        df = self.master_df.copy()
        df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)
        df = df.sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

        executed_trades: List[Fixed07TradeRecord] = []
        current_lock_until: Optional[pd.Timestamp] = None

        cap_35pct = self.starting_capital
        cap_10pct = self.starting_capital
        cap_1pct = self.starting_capital
        peak_cap_35pct = self.starting_capital
        max_dd_dollars_35pct = 0.0
        max_dd_pct_35pct = 0.0

        total_candidate_setups = len(df)
        unfilled_setups_count = 0
        rr_ratios: List[float] = []

        fee_rate = 0.0004  # 0.04% taker fee per side (0.08% roundtrip)

        for _, row in df.iterrows():
            dec_dt = row["dec_dt"]

            # Global 1-trade lock
            if current_lock_until is not None and dec_dt < current_lock_until:
                continue

            asset = str(row["asset"])
            direction = str(row["direction"])
            ob_id = str(row["ob_id"])
            ob_top = float(row["ob_high"]) if "ob_high" in row and not pd.isna(row["ob_high"]) else float(row["top_price"])
            ob_bot = float(row["ob_low"]) if "ob_low" in row and not pd.isna(row["ob_low"]) else float(row["bottom_price"])
            width = abs(ob_top - ob_bot)
            if width <= 1e-6:
                continue

            width_pct = float(row["feat_ob_width_pct"]) if "feat_ob_width_pct" in row and not pd.isna(row["feat_ob_width_pct"]) else (width / ob_bot * 100.0)

            # ── 1. EXACT GEOMETRY: 25% Penetration Entry & 0.7% Fixed TP ─────
            if direction == "LONG":
                entry_p = ob_top - 0.25 * width
                sl_p = ob_bot
                risk_dist = entry_p - sl_p  # 0.75 * width
                tp_p = entry_p * 1.007     # Exactly +0.7% price move
                reward_dist = tp_p - entry_p
            else:  # SHORT
                entry_p = ob_bot + 0.25 * width
                sl_p = ob_top
                risk_dist = sl_p - entry_p  # 0.75 * width
                tp_p = entry_p * 0.993     # Exactly -0.7% price move
                reward_dist = entry_p - tp_p

            if risk_dist <= 1e-6 or reward_dist <= 1e-6:
                continue

            planned_rr = reward_dist / risk_dist
            rr_ratios.append(planned_rr)

            # Stop distance percent & leverage
            stop_dist_pct = (risk_dist / entry_p) * 100.0
            # Sized so that 35% ROE occurs at SL: leverage = 35.0 / stop_dist_pct
            leverage = min(100, max(1, int(35.0 / stop_dist_pct))) if stop_dist_pct > 0 else 10

            # ── 2. LIMIT ORDER FILL CHECK (Requires 25% penetration) ─────────
            raw_mae_r = float(row["mae_r"]) if "mae_r" in row and not pd.isna(row["mae_r"]) else 0.0
            raw_mfe_r = float(row["mfe_r"]) if "mfe_r" in row and not pd.isna(row["mfe_r"]) else 0.0
            holding_bars = int(row["holding_bars"]) if "holding_bars" in row and not pd.isna(row["holding_bars"]) else 1
            exit_dt = dec_dt + pd.Timedelta(hours=holding_bars)

            if raw_mae_r < 0.25:
                unfilled_setups_count += 1
                continue

            # ── 3. FORWARD OUTCOME RESOLUTION (Conservative Intrabar) ────────
            # Effective excursion from entry price
            effective_mfe_price = (raw_mfe_r + 0.25) * width
            effective_mae_price = (raw_mae_r - 0.25) * width

            tp_hit = effective_mfe_price >= reward_dist
            sl_hit = effective_mae_price >= risk_dist
            is_ambiguous = (tp_hit and sl_hit and holding_bars <= 1)

            if tp_hit and not sl_hit:
                outcome = "TP_HIT"
                realized_r = planned_rr
                exit_reason = "TP_HIT"
            elif sl_hit and not tp_hit:
                outcome = "SL_HIT"
                realized_r = -1.0000
                exit_reason = "SL_HIT"
            elif tp_hit and sl_hit:
                # Conservative resolution
                outcome = "SL_HIT"
                realized_r = -1.0000
                exit_reason = "SL_HIT"
            else:
                outcome = "TIMEOUT_EXIT"
                realized_r = min(planned_rr, max(-1.0000, (effective_mfe_price - effective_mae_price) / risk_dist))
                exit_reason = "TIMEOUT_EXIT"

            # Apply Global Lock
            current_lock_until = exit_dt

            # ── 4. CONTINUOUS FULL COMPOUNDING LEDGER ─────────────────────────
            # Tier 1: 35% Margin-Risk Full Compounding
            start_35 = cap_35pct
            margin_used_35 = start_35
            notional_35 = margin_used_35 * leverage
            risk_amt_35 = start_35 * 0.35
            gross_pnl_35 = risk_amt_35 * realized_r
            fees_35 = notional_35 * fee_rate * 2.0  # roundtrip fees
            net_pnl_35 = gross_pnl_35 - fees_35
            cap_35pct = max(0.0, start_35 + net_pnl_35)

            if cap_35pct > peak_cap_35pct:
                peak_cap_35pct = cap_35pct
            dd_dollars_35 = peak_cap_35pct - cap_35pct
            dd_pct_35 = (dd_dollars_35 / peak_cap_35pct * 100.0) if peak_cap_35pct > 0 else 0.0
            max_dd_dollars_35pct = max(max_dd_dollars_35pct, dd_dollars_35)
            max_dd_pct_35pct = max(max_dd_pct_35pct, dd_pct_35)

            # Tier 2: 10% Risk Compounding
            start_10 = cap_10pct
            pnl_10 = start_10 * 0.10 * realized_r
            cap_10pct = max(0.0, start_10 + pnl_10)

            # Tier 3: 1.0% Risk Compounding
            start_1 = cap_1pct
            pnl_1 = start_1 * 0.01 * realized_r
            cap_1pct = max(0.0, start_1 + pnl_1)

            rec = Fixed07TradeRecord(
                trade_number=len(executed_trades) + 1,
                datetime=dec_dt.isoformat(),
                exit_datetime=exit_dt.isoformat(),
                asset=asset,
                direction=direction,
                ob_id=ob_id,
                ob_high=round(ob_top, 4),
                ob_low=round(ob_bot, 4),
                ob_width=round(width, 4),
                ob_width_pct=round(width_pct, 4),
                entry=round(entry_p, 4),
                sl=round(sl_p, 4),
                tp=round(tp_p, 4),
                sl_distance=round(risk_dist, 4),
                tp_distance=round(reward_dist, 4),
                tp_price_move_pct=0.70,
                planned_rr=round(planned_rr, 4),
                leverage=leverage,
                starting_capital_35pct=round(start_35, 4),
                notional_size_35pct=round(notional_35, 4),
                margin_used_35pct=round(margin_used_35, 4),
                risk_amount_35pct=round(risk_amt_35, 4),
                gross_pnl_35pct=round(gross_pnl_35, 4),
                fees_35pct=round(fees_35, 4),
                net_pnl_35pct=round(net_pnl_35, 4),
                ending_capital_35pct=round(cap_35pct, 4),
                starting_capital_10pct=round(start_10, 4),
                net_pnl_10pct=round(pnl_10, 4),
                ending_capital_10pct=round(cap_10pct, 4),
                starting_capital_1pct=round(start_1, 4),
                net_pnl_1pct=round(pnl_1, 4),
                ending_capital_1pct=round(cap_1pct, 4),
                outcome=outcome,
                r_multiple=round(realized_r, 4),
                holding_time_hours=holding_bars,
                exit_reason=exit_reason,
                global_trade_lock_state="ACTIVE",
                ambiguity_status="AMBIGUOUS_RESOLVED_CONSERVATIVE" if is_ambiguous else "UNAMBIGUOUS",
            )
            executed_trades.append(rec)

        # ── METRICS AGGREGATION ───────────────────────────────────────────────
        trades_df = pd.DataFrame([asdict(t) for t in executed_trades])
        total_exec = len(trades_df)
        wins_df = trades_df[trades_df["r_multiple"] > 0]
        losses_df = trades_df[trades_df["r_multiple"] <= 0]
        win_count = len(wins_df)
        loss_count = len(losses_df)
        win_rate = (win_count / total_exec * 100.0) if total_exec > 0 else 0.0
        exp_r = float(trades_df["r_multiple"].mean()) if total_exec > 0 else 0.0
        tot_r = float(trades_df["r_multiple"].sum()) if total_exec > 0 else 0.0
        g_gain = float(wins_df["r_multiple"].sum()) if len(wins_df) > 0 else 0.0
        g_loss = abs(float(losses_df["r_multiple"].sum())) if len(losses_df) > 0 else 0.0
        pf = (g_gain / g_loss) if g_loss > 0 else 99.0

        # Streaks
        max_w_streak = 0
        max_l_streak = 0
        curr_w = 0
        curr_l = 0
        for r in trades_df["r_multiple"]:
            if r > 0:
                curr_w += 1
                curr_l = 0
                max_w_streak = max(max_w_streak, curr_w)
            else:
                curr_l += 1
                curr_w = 0
                max_l_streak = max(max_l_streak, curr_l)

        # Planned RR Distribution
        rr_arr = np.array(rr_ratios)
        rr_cat_a = int(np.sum(rr_arr < 0.90))
        rr_cat_b = int(np.sum((rr_arr >= 0.90) & (rr_arr <= 1.10)))
        rr_cat_c = int(np.sum(rr_arr > 1.10))

        # Asset Breakdown
        assets_summary = {}
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            adf = trades_df[trades_df["asset"] == sym]
            awins = adf[adf["r_multiple"] > 0]
            aloss = adf[adf["r_multiple"] <= 0]
            a_n = len(adf)
            a_wr = (len(awins) / a_n * 100.0) if a_n > 0 else 0.0
            a_exp = float(adf["r_multiple"].mean()) if a_n > 0 else 0.0
            a_tot = float(adf["r_multiple"].sum()) if a_n > 0 else 0.0
            a_gg = float(awins["r_multiple"].sum()) if len(awins) > 0 else 0.0
            a_gl = abs(float(aloss["r_multiple"].sum())) if len(aloss) > 0 else 0.0
            a_pf = (a_gg / a_gl) if a_gl > 0 else 99.0
            assets_summary[sym] = {
                "trade_count": a_n,
                "win_count": len(awins),
                "loss_count": len(aloss),
                "win_rate_pct": round(a_wr, 2),
                "expectancy_r": round(a_exp, 4),
                "total_r": round(a_tot, 2),
                "profit_factor": round(a_pf, 2),
                "net_pnl_35pct": round(float(adf["net_pnl_35pct"].sum()), 2),
            }

        # Monthly Breakdown
        trades_df["month"] = trades_df["datetime"].str.slice(0, 7)
        monthly_summary = []
        for m, mdf in trades_df.groupby("month"):
            mwins = mdf[mdf["r_multiple"] > 0]
            mloss = mdf[mdf["r_multiple"] <= 0]
            m_n = len(mdf)
            m_wr = (len(mwins) / m_n * 100.0) if m_n > 0 else 0.0
            m_exp = float(mdf["r_multiple"].mean())
            m_tot = float(mdf["r_multiple"].sum())
            start_c = float(mdf.iloc[0]["starting_capital_35pct"])
            end_c = float(mdf.iloc[-1]["ending_capital_35pct"])
            monthly_summary.append({
                "month": str(m),
                "trade_count": m_n,
                "win_count": len(mwins),
                "loss_count": len(mloss),
                "win_rate_pct": round(m_wr, 2),
                "expectancy_r": round(m_exp, 4),
                "total_r": round(m_tot, 2),
                "starting_capital_35pct": round(start_c, 2),
                "ending_capital_35pct": round(end_c, 2),
            })

        return {
            "overall": {
                "starting_capital": self.starting_capital,
                "ending_capital_35pct": round(cap_35pct, 4),
                "net_return_pct_35pct": round((cap_35pct - self.starting_capital) / self.starting_capital * 100.0, 2),
                "ending_capital_10pct": round(cap_10pct, 4),
                "net_return_pct_10pct": round((cap_10pct - self.starting_capital) / self.starting_capital * 100.0, 2),
                "ending_capital_1pct": round(cap_1pct, 4),
                "net_return_pct_1pct": round((cap_1pct - self.starting_capital) / self.starting_capital * 100.0, 2),
                "total_candidate_setups": total_candidate_setups,
                "unfilled_setups": unfilled_setups_count,
                "fill_rate_pct": round((total_candidate_setups - unfilled_setups_count) / total_candidate_setups * 100.0, 2),
                "total_executed_trades": total_exec,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate_pct": round(win_rate, 2),
                "expectancy_r": round(exp_r, 4),
                "total_realized_r": round(tot_r, 2),
                "profit_factor": round(pf, 2),
                "max_drawdown_dollars_35pct": round(max_dd_dollars_35pct, 2),
                "max_drawdown_pct_35pct": round(max_dd_pct_35pct, 2),
                "max_win_streak": max_w_streak,
                "max_loss_streak": max_l_streak,
                "total_fees_35pct": round(float(trades_df["fees_35pct"].sum()), 2) if total_exec > 0 else 0.0,
                "avg_holding_time_hours": round(float(trades_df["holding_time_hours"].mean()), 1) if total_exec > 0 else 0.0,
            },
            "planned_rr_distribution": {
                "mean_planned_rr": round(float(np.mean(rr_arr)), 2),
                "median_planned_rr": round(float(np.median(rr_arr)), 2),
                "min_planned_rr": round(float(np.min(rr_arr)), 2),
                "max_planned_rr": round(float(np.max(rr_arr)), 2),
                "smaller_than_sl_count": rr_cat_a,
                "smaller_than_sl_pct": round(rr_cat_a / len(rr_arr) * 100.0, 1),
                "approx_equal_sl_count": rr_cat_b,
                "approx_equal_sl_pct": round(rr_cat_b / len(rr_arr) * 100.0, 1),
                "larger_than_sl_count": rr_cat_c,
                "larger_than_sl_pct": round(rr_cat_c / len(rr_arr) * 100.0, 1),
            },
            "assets_breakdown": assets_summary,
            "monthly_breakdown": monthly_summary,
            "trades": [asdict(t) for t in executed_trades],
        }
