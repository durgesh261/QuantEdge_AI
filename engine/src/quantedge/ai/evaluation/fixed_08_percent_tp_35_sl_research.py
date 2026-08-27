"""
QuantEdge AI — Fixed 0.8% Price-Target TP + 35% SL Leverage Compounding Research Engine.

Implements the exact research experiment reproducing the user's TradingView/LuxAlgo trade construction:
- Entry: 25% penetration limit order inside the Order Block.
  - Bullish (Long):  entry = ob_high - 0.25 * (ob_high - ob_low)
  - Bearish (Short): entry = ob_low  + 0.25 * (ob_high - ob_low)
- Stop Loss: Second edge / distal boundary of the Order Block.
  - Long:  SL = ob_low   (risk_distance = entry - ob_low = 0.75 * OB_width)
  - Short: SL = ob_high  (risk_distance = ob_high - entry = 0.75 * OB_width)
- Take Profit: Fixed 0.80% market price movement from entry.
  - Long:  TP = entry * 1.008
  - Short: TP = entry * 0.992
- Leverage Calculation:
  - leverage = 0.35 / (risk_distance / entry)
  - Gross SL return = -35.0% of trading capital
  - Gross TP return = 0.008 * leverage * 100.0% of trading capital
- Global 1-Trade-at-a-Time Lock across BTCUSD, ETHUSD, SOLUSD, XRPUSD.
- Continuous full account compounding from $10.00 initial capital.

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
class Fixed08TradeRecord:
    """Detailed trade record in the Fixed 0.8% TP + 35% SL compounding ledger."""
    trade_id: int
    asset: str
    direction: str
    ob_timestamp: str
    entry_timestamp: str
    exit_timestamp: str
    ob_high: float
    ob_low: float
    ob_width: float
    ob_width_pct: float
    entry_price: float
    sl_price: float
    tp_price: float
    entry_to_sl_distance_pct: float
    tp_price_distance_pct: float
    calculated_leverage: float
    starting_capital_gross: float
    gross_sl_return_pct: float
    gross_tp_return_pct: float
    gross_pnl_usd: float
    starting_capital_net: float
    notional_size_usd: float
    fees_usd: float
    net_pnl_usd: float
    ending_capital_gross: float
    ending_capital_net: float
    outcome: str
    realized_r: float
    exit_reason: str
    ambiguous_intrabar: bool
    sl_bucket: str


class Fixed08PercentTP35SLResearchEngine:
    """
    Evaluates the Fixed 0.8% TP + 35% SL Strategy across canonical multi-year SMC data.
    """

    def __init__(self, master_df: pd.DataFrame, starting_capital: float = 10.0):
        self.master_df = master_df.copy()
        self.starting_capital = starting_capital

    @staticmethod
    def calculate_trade_parameters(
        direction: str,
        ob_high: float,
        ob_low: float,
    ) -> Tuple[float, float, float, float, float, float, float, float]:
        """
        Pure calculation helper to determine exact trade parameters.
        Returns:
            (entry, sl, tp, risk_dist, reward_dist, sl_dist_pct, leverage, gross_tp_return_pct)
        """
        width = abs(ob_high - ob_low)
        if direction.upper() == "LONG":
            entry = ob_high - 0.25 * width
            sl = ob_low
            risk_dist = entry - sl
            tp = entry * 1.008
            reward_dist = tp - entry
        else:  # SHORT
            entry = ob_low + 0.25 * width
            sl = ob_high
            risk_dist = sl - entry
            tp = entry * 0.992
            reward_dist = entry - tp

        sl_dist_dec = risk_dist / entry if entry > 0 else 0.01
        sl_dist_pct = sl_dist_dec * 100.0
        leverage = 0.35 / sl_dist_dec if sl_dist_dec > 0 else 1.0
        gross_tp_return_pct = 0.008 * leverage * 100.0

        return (entry, sl, tp, risk_dist, reward_dist, sl_dist_pct, leverage, gross_tp_return_pct)

    def run_backtest(self) -> Dict[str, Any]:
        """Runs the deterministic chronological backtest with global 1-trade lock."""
        df = self.master_df.copy()
        df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)
        df = df.sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

        executed_trades: List[Fixed08TradeRecord] = []
        current_lock_until: Optional[pd.Timestamp] = None

        capital_gross = self.starting_capital
        capital_net = self.starting_capital
        peak_capital_gross = self.starting_capital
        peak_capital_net = self.starting_capital
        max_dd_dollars_gross = 0.0
        max_dd_pct_gross = 0.0
        max_dd_dollars_net = 0.0
        max_dd_pct_net = 0.0

        total_candidate_setups = len(df)
        unfilled_setups_count = 0
        skipped_lock_count = 0
        ambiguous_trade_count = 0
        optimistic_wins = 0

        fee_rate_roundtrip = 0.0008  # 0.04% maker/taker per side = 0.08% roundtrip on notional

        exact_070_count = 0
        exact_50x_count = 0

        sl_buckets_data: Dict[str, List[Fixed08TradeRecord]] = {
            "<0.50%": [],
            "0.50-0.60%": [],
            "0.60-0.70%": [],
            "0.70-0.80%": [],
            "0.80-1.00%": [],
            "1.00-1.50%": [],
            ">1.50%": [],
        }

        for _, row in df.iterrows():
            dec_dt = row["dec_dt"]

            # Global 1-trade-at-a-time lock
            if current_lock_until is not None and dec_dt < current_lock_until:
                skipped_lock_count += 1
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

            # ── 1. EXACT TRADE PARAMETERS ─────────────────────────────────────
            (entry_p, sl_p, tp_p, risk_dist, reward_dist, sl_dist_pct, leverage, gross_tp_return_pct) = (
                self.calculate_trade_parameters(direction, ob_top, ob_bot)
            )

            if risk_dist <= 1e-6 or reward_dist <= 1e-6:
                continue

            # Check 0.70% and 50x frequency
            if 0.65 <= sl_dist_pct <= 0.75:
                exact_070_count += 1
            if 45.0 <= leverage <= 55.0:
                exact_50x_count += 1

            # ── 2. LIMIT ORDER FILL CHECK (Requires 25% penetration) ─────────
            raw_mae_r = float(row["mae_r"]) if "mae_r" in row and not pd.isna(row["mae_r"]) else 0.0
            raw_mfe_r = float(row["mfe_r"]) if "mfe_r" in row and not pd.isna(row["mfe_r"]) else 0.0
            holding_bars = int(row["holding_bars"]) if "holding_bars" in row and not pd.isna(row["holding_bars"]) else 1
            exit_dt = dec_dt + pd.Timedelta(hours=holding_bars)

            if raw_mae_r < 0.25:
                unfilled_setups_count += 1
                continue

            # ── 3. FORWARD OUTCOME RESOLUTION (Deterministic Conservative) ──
            eff_mfe_price = (raw_mfe_r + 0.25) * width
            eff_mae_price = (raw_mae_r - 0.25) * width

            tp_hit = eff_mfe_price >= reward_dist
            sl_hit = eff_mae_price >= risk_dist
            is_ambiguous = bool(tp_hit and sl_hit and holding_bars <= 1)

            if is_ambiguous:
                ambiguous_trade_count += 1

            planned_rr = reward_dist / risk_dist

            if tp_hit and not sl_hit:
                outcome = "TP_HIT"
                realized_ret_pct = gross_tp_return_pct
                realized_r = planned_rr
                exit_reason = "TP_HIT"
                optimistic_wins += 1
            elif sl_hit and not tp_hit:
                outcome = "SL_HIT"
                realized_ret_pct = -35.0
                realized_r = -1.0000
                exit_reason = "SL_HIT"
            elif tp_hit and sl_hit:
                # Conservative resolution: SL hit first
                outcome = "SL_HIT"
                realized_ret_pct = -35.0
                realized_r = -1.0000
                exit_reason = "SL_HIT_CONSERVATIVE_AMBIGUITY"
                optimistic_wins += 1  # would be win in optimistic mode
            else:
                outcome = "TIMEOUT_EXIT"
                eff_r = (eff_mfe_price - eff_mae_price) / risk_dist
                realized_r = min(planned_rr, max(-1.0000, eff_r))
                realized_ret_pct = realized_r * 35.0
                exit_reason = "TIMEOUT_EXIT"

            # Apply Global Lock
            current_lock_until = exit_dt

            # ── 4. CONTINUOUS FULL COMPOUNDING LEDGER ─────────────────────────
            # Gross calculation
            start_cap_g = capital_gross
            gross_pnl = start_cap_g * (realized_ret_pct / 100.0)
            capital_gross = max(0.0, start_cap_g + gross_pnl)

            if capital_gross > peak_capital_gross:
                peak_capital_gross = capital_gross
            dd_g = peak_capital_gross - capital_gross
            dd_pct_g = (dd_g / peak_capital_gross * 100.0) if peak_capital_gross > 0 else 0.0
            max_dd_dollars_gross = max(max_dd_dollars_gross, dd_g)
            max_dd_pct_gross = max(max_dd_pct_gross, dd_pct_g)

            # Net calculation (with transaction fees)
            start_cap_n = capital_net
            notional_usd = start_cap_n * leverage
            fees_usd = notional_usd * fee_rate_roundtrip
            gross_pnl_on_net = start_cap_n * (realized_ret_pct / 100.0)
            net_pnl = gross_pnl_on_net - fees_usd
            capital_net = max(0.0, start_cap_n + net_pnl)

            if capital_net > peak_capital_net:
                peak_capital_net = capital_net
            dd_n = peak_capital_net - capital_net
            dd_pct_n = (dd_n / peak_capital_net * 100.0) if peak_capital_net > 0 else 0.0
            max_dd_dollars_net = max(max_dd_dollars_net, dd_n)
            max_dd_pct_net = max(max_dd_pct_net, dd_pct_n)

            # Determine SL bucket
            if sl_dist_pct < 0.50:
                bucket_name = "<0.50%"
            elif sl_dist_pct < 0.60:
                bucket_name = "0.50-0.60%"
            elif sl_dist_pct < 0.70:
                bucket_name = "0.60-0.70%"
            elif sl_dist_pct < 0.80:
                bucket_name = "0.70-0.80%"
            elif sl_dist_pct < 1.00:
                bucket_name = "0.80-1.00%"
            elif sl_dist_pct < 1.50:
                bucket_name = "1.00-1.50%"
            else:
                bucket_name = ">1.50%"

            trade_rec = Fixed08TradeRecord(
                trade_id=len(executed_trades) + 1,
                asset=asset,
                direction=direction,
                ob_timestamp=str(row["decision_timestamp"]),
                entry_timestamp=dec_dt.isoformat(),
                exit_timestamp=exit_dt.isoformat(),
                ob_high=round(ob_top, 4),
                ob_low=round(ob_bot, 4),
                ob_width=round(width, 4),
                ob_width_pct=round(width_pct, 4),
                entry_price=round(entry_p, 4),
                sl_price=round(sl_p, 4),
                tp_price=round(tp_p, 4),
                entry_to_sl_distance_pct=round(sl_dist_pct, 4),
                tp_price_distance_pct=0.80,
                calculated_leverage=round(leverage, 4),
                starting_capital_gross=round(start_cap_g, 6),
                gross_sl_return_pct=-35.00,
                gross_tp_return_pct=round(gross_tp_return_pct, 4),
                gross_pnl_usd=round(gross_pnl, 6),
                starting_capital_net=round(start_cap_n, 6),
                notional_size_usd=round(notional_usd, 6),
                fees_usd=round(fees_usd, 6),
                net_pnl_usd=round(net_pnl, 6),
                ending_capital_gross=round(capital_gross, 6),
                ending_capital_net=round(capital_net, 6),
                outcome=outcome,
                realized_r=round(realized_r, 4),
                exit_reason=exit_reason,
                ambiguous_intrabar=is_ambiguous,
                sl_bucket=bucket_name,
            )
            executed_trades.append(trade_rec)
            sl_buckets_data[bucket_name].append(trade_rec)

        # ── COMPUTE SUMMARY METRICS ──────────────────────────────────────────
        trades_df = pd.DataFrame([asdict(t) for t in executed_trades])
        total_exec = len(trades_df)
        wins_df = trades_df[trades_df["realized_r"] > 0]
        losses_df = trades_df[trades_df["realized_r"] <= 0]
        win_count = len(wins_df)
        loss_count = len(losses_df)
        win_rate = (win_count / total_exec * 100.0) if total_exec > 0 else 0.0
        exp_r = float(trades_df["realized_r"].mean()) if total_exec > 0 else 0.0
        tot_r = float(trades_df["realized_r"].sum()) if total_exec > 0 else 0.0
        g_gain = float(wins_df["realized_r"].sum()) if len(wins_df) > 0 else 0.0
        g_loss = abs(float(losses_df["realized_r"].sum())) if len(losses_df) > 0 else 0.0
        pf = (g_gain / g_loss) if g_loss > 0 else 99.0

        # Streaks
        max_w_streak = 0
        max_l_streak = 0
        curr_w = 0
        curr_l = 0
        for r in trades_df["realized_r"]:
            if r > 0:
                curr_w += 1
                curr_l = 0
                max_w_streak = max(max_w_streak, curr_w)
            else:
                curr_l += 1
                curr_w = 0
                max_l_streak = max(max_l_streak, curr_l)

        # Leverage & Return stats
        lev_arr = trades_df["calculated_leverage"].values if total_exec > 0 else np.array([0.0])
        tp_ret_arr = trades_df["gross_tp_return_pct"].values if total_exec > 0 else np.array([0.0])

        # Asset Breakdown
        assets_summary = {}
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            adf = trades_df[trades_df["asset"] == sym]
            awins = adf[adf["realized_r"] > 0]
            aloss = adf[adf["realized_r"] <= 0]
            a_n = len(adf)
            a_wr = (len(awins) / a_n * 100.0) if a_n > 0 else 0.0
            a_exp = float(adf["realized_r"].mean()) if a_n > 0 else 0.0
            a_tot = float(adf["realized_r"].sum()) if a_n > 0 else 0.0
            a_gg = float(awins["realized_r"].sum()) if len(awins) > 0 else 0.0
            a_gl = abs(float(aloss["realized_r"].sum())) if len(aloss) > 0 else 0.0
            a_pf = (a_gg / a_gl) if a_gl > 0 else 99.0
            assets_summary[sym] = {
                "trade_count": a_n,
                "win_count": len(awins),
                "loss_count": len(aloss),
                "win_rate_pct": round(a_wr, 2),
                "expectancy_r": round(a_exp, 4),
                "total_r": round(a_tot, 2),
                "profit_factor": round(a_pf, 2),
                "avg_leverage": round(float(adf["calculated_leverage"].mean()), 2) if a_n > 0 else 0.0,
                "gross_pnl_usd": round(float(adf["gross_pnl_usd"].sum()), 6) if a_n > 0 else 0.0,
                "net_pnl_usd": round(float(adf["net_pnl_usd"].sum()), 6) if a_n > 0 else 0.0,
            }

        # SL Distance Buckets Summary
        buckets_summary = {}
        for b_name, b_records in sl_buckets_data.items():
            if len(b_records) > 0:
                b_df = pd.DataFrame([asdict(r) for r in b_records])
                b_w = len(b_df[b_df["realized_r"] > 0])
                b_l = len(b_df[b_df["realized_r"] <= 0])
                b_n = len(b_df)
                b_wr = (b_w / b_n * 100.0) if b_n > 0 else 0.0
                b_exp = float(b_df["realized_r"].mean())
                b_tot = float(b_df["realized_r"].sum())
                b_gg = float(b_df[b_df["realized_r"] > 0]["realized_r"].sum())
                b_gl = abs(float(b_df[b_df["realized_r"] <= 0]["realized_r"].sum()))
                b_pf = (b_gg / b_gl) if b_gl > 0 else 99.0
                buckets_summary[b_name] = {
                    "trade_count": b_n,
                    "win_count": b_w,
                    "loss_count": b_l,
                    "win_rate_pct": round(b_wr, 2),
                    "avg_leverage": round(float(b_df["calculated_leverage"].mean()), 2),
                    "avg_tp_return_pct": round(float(b_df["gross_tp_return_pct"].mean()), 2),
                    "expectancy_r": round(b_exp, 4),
                    "profit_factor": round(b_pf, 2),
                    "total_realized_r": round(b_tot, 2),
                }
            else:
                buckets_summary[b_name] = {
                    "trade_count": 0,
                    "win_count": 0,
                    "loss_count": 0,
                    "win_rate_pct": 0.0,
                    "avg_leverage": 0.0,
                    "avg_tp_return_pct": 0.0,
                    "expectancy_r": 0.0,
                    "profit_factor": 0.0,
                    "total_realized_r": 0.0,
                }

        # Monthly Summary
        trades_df["month"] = trades_df["entry_timestamp"].str.slice(0, 7)
        monthly_summary = []
        for m, mdf in trades_df.groupby("month"):
            mwins = mdf[mdf["realized_r"] > 0]
            mloss = mdf[mdf["realized_r"] <= 0]
            m_n = len(mdf)
            m_wr = (len(mwins) / m_n * 100.0) if m_n > 0 else 0.0
            m_exp = float(mdf["realized_r"].mean())
            m_tot = float(mdf["realized_r"].sum())
            start_g = float(mdf.iloc[0]["starting_capital_gross"])
            end_g = float(mdf.iloc[-1]["ending_capital_gross"])
            start_n = float(mdf.iloc[0]["starting_capital_net"])
            end_n = float(mdf.iloc[-1]["ending_capital_net"])
            monthly_summary.append({
                "month": str(m),
                "trade_count": m_n,
                "win_count": len(mwins),
                "loss_count": len(mloss),
                "win_rate_pct": round(m_wr, 2),
                "expectancy_r": round(m_exp, 4),
                "total_r": round(m_tot, 2),
                "starting_capital_gross": round(start_g, 4),
                "ending_capital_gross": round(end_g, 4),
                "starting_capital_net": round(start_n, 4),
                "ending_capital_net": round(end_n, 4),
            })

        return {
            "overall": {
                "starting_capital": self.starting_capital,
                "ending_capital_gross": round(capital_gross, 6),
                "net_return_pct_gross": round((capital_gross - self.starting_capital) / self.starting_capital * 100.0, 2),
                "ending_capital_net": round(capital_net, 6),
                "net_return_pct_net": round((capital_net - self.starting_capital) / self.starting_capital * 100.0, 2),
                "total_candidate_setups": total_candidate_setups,
                "unfilled_setups": unfilled_setups_count,
                "skipped_lock_count": skipped_lock_count,
                "total_executed_trades": total_exec,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate_pct": round(win_rate, 2),
                "expectancy_r": round(exp_r, 4),
                "total_realized_r": round(tot_r, 2),
                "profit_factor": round(pf, 2),
                "max_drawdown_dollars_gross": round(max_dd_dollars_gross, 2),
                "max_drawdown_pct_gross": round(max_dd_pct_gross, 2),
                "max_drawdown_dollars_net": round(max_dd_dollars_net, 2),
                "max_drawdown_pct_net": round(max_dd_pct_net, 2),
                "max_win_streak": max_w_streak,
                "max_loss_streak": max_l_streak,
                "total_fees_usd": round(float(trades_df["fees_usd"].sum()), 4) if total_exec > 0 else 0.0,
                "avg_leverage": round(float(np.mean(lev_arr)), 2),
                "median_leverage": round(float(np.median(lev_arr)), 2),
                "max_leverage": round(float(np.max(lev_arr)), 2),
                "min_leverage": round(float(np.min(lev_arr)), 2),
                "avg_gross_tp_return_pct": round(float(np.mean(tp_ret_arr)), 2),
                "median_gross_tp_return_pct": round(float(np.median(tp_ret_arr)), 2),
                "avg_gross_sl_loss_pct": -35.00,
                "exact_070_sl_count": exact_070_count,
                "exact_50x_leverage_count": exact_50x_count,
                "ambiguous_trades_count": ambiguous_trade_count,
                "optimistic_win_rate_pct": round(optimistic_wins / total_exec * 100.0, 2) if total_exec > 0 else 0.0,
            },
            "sl_buckets_breakdown": buckets_summary,
            "assets_breakdown": assets_summary,
            "monthly_breakdown": monthly_summary,
            "trades": [asdict(t) for t in executed_trades],
        }
