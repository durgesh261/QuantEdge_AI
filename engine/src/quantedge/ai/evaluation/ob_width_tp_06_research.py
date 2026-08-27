"""
QuantEdge AI — OB Width-Based TP/SL Strategy with $10 Full Compounding.

Implements the controlled research experiment for an OB width-dependent Take-Profit strategy:
- Regime A (OB width <= 0.6%): 60% target (60/35 ROE target = 1.7143R per repository convention).
- Regime B (OB width > 0.6%): 1:1 Risk/Reward (TP distance = SL risk distance = 1.0R).
- Global 1-trade-at-a-time portfolio lock across BTCUSD, ETHUSD, SOLUSD, XRPUSD.
- Continuous full-account compounding on a $10.00 starting balance.

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
class ExecutedTradeRecord:
    """Individual trade execution and compounding record in the research backtest."""
    trade_number: int
    timestamp: str
    exit_timestamp: str
    asset: str
    direction: str
    ob_id: str
    ob_high: float
    ob_low: float
    ob_width: float
    ob_width_percent: float
    tp_regime: str
    entry_price: float
    sl_price: float
    tp_price: float
    risk_distance: float
    reward_distance: float
    planned_rr: float
    outcome: str  # TP_HIT, SL_HIT, TIMEOUT_EXIT
    realized_R: float
    starting_capital_10pct: float
    risk_amount_10pct: float
    pnl_dollar_10pct: float
    ending_capital_10pct: float
    starting_capital_1pct: float
    risk_amount_1pct: float
    pnl_dollar_1pct: float
    ending_capital_1pct: float
    holding_time_hours: int
    exit_reason: str
    ambiguity_status: str  # UNAMBIGUOUS, AMBIGUOUS_RESOLVED_CONSERVATIVE


class OBWidthTP06ResearchEngine:
    """
    Executes the deterministic OB Width TP research backtest with full continuous compounding.
    """

    def __init__(self, master_df: pd.DataFrame, starting_capital: float = 10.0):
        self.master_df = master_df.copy()
        self.starting_capital = starting_capital

    def run_backtest(self) -> Dict[str, Any]:
        """
        Runs the chronological single-trade simulation with global portfolio lock.
        """
        # Ensure chronological ordering by decision timestamp
        df = self.master_df.copy()
        df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)
        df = df.sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

        executed_trades: List[ExecutedTradeRecord] = []
        current_lock_until: Optional[pd.Timestamp] = None

        cap_10pct = self.starting_capital
        cap_1pct = self.starting_capital
        peak_cap_10pct = self.starting_capital
        peak_cap_1pct = self.starting_capital
        max_dd_dollars_10pct = 0.0
        max_dd_pct_10pct = 0.0

        for _, row in df.iterrows():
            dec_dt = row["dec_dt"]
            
            # Global 1-trade-at-a-time lock check
            if current_lock_until is not None and dec_dt < current_lock_until:
                continue

            asset = str(row["asset"])
            direction = str(row["direction"])
            ob_id = str(row["ob_id"])
            ob_top = float(row["ob_high"]) if "ob_high" in row and not pd.isna(row["ob_high"]) else float(row["top_price"])
            ob_bot = float(row["ob_low"]) if "ob_low" in row and not pd.isna(row["ob_low"]) else float(row["bottom_price"])
            width = abs(ob_top - ob_bot)
            width_pct = float(row["feat_ob_width_pct"]) if "feat_ob_width_pct" in row and not pd.isna(row["feat_ob_width_pct"]) else (width / ob_bot * 100.0)

            # Regime classification: <= 0.6% vs > 0.6%
            is_narrow = width_pct <= 0.60
            regime_name = "REGIME_A_LE_06" if is_narrow else "REGIME_B_GT_06"

            # Entry & SL construction per existing QuantEdge SMC rules:
            # - Narrow (<= 0.6%): Edge entry
            # - Wide (> 0.6%): 25% penetration entry
            if direction == "LONG":
                entry_p = ob_top if is_narrow else (ob_top - 0.25 * width)
                sl_p = ob_bot
                risk_dist = entry_p - sl_p
                if risk_dist <= 1e-6:
                    continue

                if is_narrow:
                    # Regime A: 60% TP target (60/35 ROE = 1.7143R)
                    reward_dist = (60.0 / 35.0) * risk_dist
                    planned_rr = 60.0 / 35.0
                    tp_p = entry_p + reward_dist
                else:
                    # Regime B: Exactly 1:1 risk/reward
                    reward_dist = risk_dist
                    planned_rr = 1.0000
                    tp_p = entry_p + reward_dist

            else:  # SHORT
                entry_p = ob_bot if is_narrow else (ob_bot + 0.25 * width)
                sl_p = ob_top
                risk_dist = sl_p - entry_p
                if risk_dist <= 1e-6:
                    continue

                if is_narrow:
                    reward_dist = (60.0 / 35.0) * risk_dist
                    planned_rr = 60.0 / 35.0
                    tp_p = entry_p - reward_dist
                else:
                    reward_dist = risk_dist
                    planned_rr = 1.0000
                    tp_p = entry_p - reward_dist

            # Forward Replay Evaluation
            mfe_r = float(row["mfe_r"]) if "mfe_r" in row and not pd.isna(row["mfe_r"]) else 0.0
            mae_r = float(row["mae_r"]) if "mae_r" in row and not pd.isna(row["mae_r"]) else 0.0
            holding_bars = int(row["holding_bars"]) if "holding_bars" in row and not pd.isna(row["holding_bars"]) else 1
            exit_dt = dec_dt + pd.Timedelta(hours=holding_bars)

            # Intrabar ambiguity & outcome resolution
            is_ambiguous = (mfe_r >= planned_rr and mae_r >= 1.0 and holding_bars <= 1)

            if mfe_r >= planned_rr and mae_r < 1.0:
                outcome = "TP_HIT"
                realized_r = planned_rr
                exit_reason = "TP_HIT"
            elif mae_r >= 1.0 and mfe_r < planned_rr:
                outcome = "SL_HIT"
                realized_r = -1.0000
                exit_reason = "SL_HIT"
            elif mfe_r >= planned_rr and mae_r >= 1.0:
                # Conservative resolution: SL hit first
                outcome = "SL_HIT"
                realized_r = -1.0000
                exit_reason = "SL_HIT"
            else:
                outcome = "TIMEOUT_EXIT"
                realized_r = min(planned_rr, max(-1.0000, mfe_r - mae_r * 0.5))
                exit_reason = "TIMEOUT_EXIT"

            # Apply Global Lock
            current_lock_until = exit_dt

            # Continuous Compounding Accounting
            # Tier 1: 10% risk per trade
            start_cap_10 = cap_10pct
            risk_amt_10 = start_cap_10 * 0.10
            pnl_10 = risk_amt_10 * realized_r
            cap_10pct = max(0.0, start_cap_10 + pnl_10)

            if cap_10pct > peak_cap_10pct:
                peak_cap_10pct = cap_10pct
            dd_dollars_10 = peak_cap_10pct - cap_10pct
            dd_pct_10 = (dd_dollars_10 / peak_cap_10pct * 100.0) if peak_cap_10pct > 0 else 0.0
            max_dd_dollars_10pct = max(max_dd_dollars_10pct, dd_dollars_10)
            max_dd_pct_10pct = max(max_dd_pct_10pct, dd_pct_10)

            # Tier 2: 1.0% risk per trade
            start_cap_1 = cap_1pct
            risk_amt_1 = start_cap_1 * 0.01
            pnl_1 = risk_amt_1 * realized_r
            cap_1pct = max(0.0, start_cap_1 + pnl_1)

            if cap_1pct > peak_cap_1pct:
                peak_cap_1pct = cap_1pct

            trade_rec = ExecutedTradeRecord(
                trade_number=len(executed_trades) + 1,
                timestamp=dec_dt.isoformat(),
                exit_timestamp=exit_dt.isoformat(),
                asset=asset,
                direction=direction,
                ob_id=ob_id,
                ob_high=round(ob_top, 4),
                ob_low=round(ob_bot, 4),
                ob_width=round(width, 4),
                ob_width_percent=round(width_pct, 4),
                tp_regime=regime_name,
                entry_price=round(entry_p, 4),
                sl_price=round(sl_p, 4),
                tp_price=round(tp_p, 4),
                risk_distance=round(risk_dist, 4),
                reward_distance=round(reward_dist, 4),
                planned_rr=round(planned_rr, 4),
                outcome=outcome,
                realized_R=round(realized_r, 4),
                starting_capital_10pct=round(start_cap_10, 4),
                risk_amount_10pct=round(risk_amt_10, 4),
                pnl_dollar_10pct=round(pnl_10, 4),
                ending_capital_10pct=round(cap_10pct, 4),
                starting_capital_1pct=round(start_cap_1, 4),
                risk_amount_1pct=round(risk_amt_1, 4),
                pnl_dollar_1pct=round(pnl_1, 4),
                ending_capital_1pct=round(cap_1pct, 4),
                holding_time_hours=holding_bars,
                exit_reason=exit_reason,
                ambiguity_status="AMBIGUOUS_RESOLVED_CONSERVATIVE" if is_ambiguous else "UNAMBIGUOUS",
            )
            executed_trades.append(trade_rec)

        # ── COMPUTE SUMMARY METRICS ──────────────────────────────────────────
        trades_df = pd.DataFrame([asdict(t) for t in executed_trades])
        total_n = len(trades_df)
        wins_df = trades_df[trades_df["realized_R"] > 0]
        losses_df = trades_df[trades_df["realized_R"] <= 0]
        win_count = len(wins_df)
        loss_count = len(losses_df)
        win_rate = (win_count / total_n * 100.0) if total_n > 0 else 0.0
        exp_r = float(trades_df["realized_R"].mean()) if total_n > 0 else 0.0
        tot_r = float(trades_df["realized_R"].sum()) if total_n > 0 else 0.0
        gross_gain = float(wins_df["realized_R"].sum()) if len(wins_df) > 0 else 0.0
        gross_loss = abs(float(losses_df["realized_R"].sum())) if len(losses_df) > 0 else 0.0
        pf = (gross_gain / gross_loss) if gross_loss > 0 else 99.0

        # Streaks
        max_win_streak = 0
        max_loss_streak = 0
        curr_w = 0
        curr_l = 0
        for r in trades_df["realized_R"]:
            if r > 0:
                curr_w += 1
                curr_l = 0
                max_win_streak = max(max_win_streak, curr_w)
            else:
                curr_l += 1
                curr_w = 0
                max_loss_streak = max(max_loss_streak, curr_l)

        # Regime Breakdown
        regimes_summary = {}
        for reg in ["REGIME_A_LE_06", "REGIME_B_GT_06"]:
            rdf = trades_df[trades_df["tp_regime"] == reg]
            rwins = rdf[rdf["realized_R"] > 0]
            rlosses = rdf[rdf["realized_R"] <= 0]
            r_n = len(rdf)
            r_wr = (len(rwins) / r_n * 100.0) if r_n > 0 else 0.0
            r_exp = float(rdf["realized_R"].mean()) if r_n > 0 else 0.0
            r_tot = float(rdf["realized_R"].sum()) if r_n > 0 else 0.0
            r_gg = float(rwins["realized_R"].sum()) if len(rwins) > 0 else 0.0
            r_gl = abs(float(rlosses["realized_R"].sum())) if len(rlosses) > 0 else 0.0
            r_pf = (r_gg / r_gl) if r_gl > 0 else 99.0
            regimes_summary[reg] = {
                "trade_count": r_n,
                "win_count": len(rwins),
                "loss_count": len(rlosses),
                "win_rate_pct": round(r_wr, 2),
                "expectancy_r": round(r_exp, 4),
                "total_r": round(r_tot, 2),
                "profit_factor": round(r_pf, 2),
                "avg_ob_width_pct": round(float(rdf["ob_width_percent"].mean()), 2) if r_n > 0 else 0.0,
                "avg_holding_hours": round(float(rdf["holding_time_hours"].mean()), 1) if r_n > 0 else 0.0,
            }

        # Asset Breakdown
        assets_summary = {}
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            adf = trades_df[trades_df["asset"] == sym]
            awins = adf[adf["realized_R"] > 0]
            alosses = adf[adf["realized_R"] <= 0]
            a_n = len(adf)
            a_wr = (len(awins) / a_n * 100.0) if a_n > 0 else 0.0
            a_exp = float(adf["realized_R"].mean()) if a_n > 0 else 0.0
            a_tot = float(adf["realized_R"].sum()) if a_n > 0 else 0.0
            a_gg = float(awins["realized_R"].sum()) if len(awins) > 0 else 0.0
            a_gl = abs(float(alosses["realized_R"].sum())) if len(alosses) > 0 else 0.0
            a_pf = (a_gg / a_gl) if a_gl > 0 else 99.0
            assets_summary[sym] = {
                "trade_count": a_n,
                "win_count": len(awins),
                "loss_count": len(alosses),
                "win_rate_pct": round(a_wr, 2),
                "expectancy_r": round(a_exp, 4),
                "total_r": round(a_tot, 2),
                "profit_factor": round(a_pf, 2),
            }

        # Monthly Breakdown
        trades_df["month"] = trades_df["timestamp"].str.slice(0, 7)
        monthly_summary = []
        for m, mdf in trades_df.groupby("month"):
            mwins = mdf[mdf["realized_R"] > 0]
            mlosses = mdf[mdf["realized_R"] <= 0]
            m_n = len(mdf)
            m_wr = (len(mwins) / m_n * 100.0) if m_n > 0 else 0.0
            m_exp = float(mdf["realized_R"].mean())
            m_tot = float(mdf["realized_R"].sum())
            start_c = float(mdf.iloc[0]["starting_capital_10pct"])
            end_c = float(mdf.iloc[-1]["ending_capital_10pct"])
            monthly_summary.append({
                "month": str(m),
                "trade_count": m_n,
                "win_count": len(mwins),
                "loss_count": len(mlosses),
                "win_rate_pct": round(m_wr, 2),
                "expectancy_r": round(m_exp, 4),
                "total_r": round(m_tot, 2),
                "starting_capital_10pct": round(start_c, 2),
                "ending_capital_10pct": round(end_c, 2),
            })

        return {
            "overall": {
                "starting_capital": self.starting_capital,
                "ending_capital_10pct": round(cap_10pct, 4),
                "net_return_pct_10pct": round((cap_10pct - self.starting_capital) / self.starting_capital * 100.0, 2),
                "ending_capital_1pct": round(cap_1pct, 4),
                "net_return_pct_1pct": round((cap_1pct - self.starting_capital) / self.starting_capital * 100.0, 2),
                "total_executed_trades": total_n,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate_pct": round(win_rate, 2),
                "expectancy_r": round(exp_r, 4),
                "total_realized_r": round(tot_r, 2),
                "profit_factor": round(pf, 2),
                "max_drawdown_dollars_10pct": round(max_dd_dollars_10pct, 2),
                "max_drawdown_pct_10pct": round(max_dd_pct_10pct, 2),
                "max_win_streak": max_win_streak,
                "max_loss_streak": max_loss_streak,
                "avg_holding_time_hours": round(float(trades_df["holding_time_hours"].mean()), 1) if total_n > 0 else 0.0,
            },
            "regimes_breakdown": regimes_summary,
            "assets_breakdown": assets_summary,
            "monthly_breakdown": monthly_summary,
            "trades": [asdict(t) for t in executed_trades],
        }
