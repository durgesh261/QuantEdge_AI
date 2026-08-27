"""
QuantEdge AI — August 2026 Isolated Diagnostic Backtest Engine.

Isolates August 1–26, 2026 to perform a candle-by-candle autopsy of trade execution,
investigating why Order Blocks hit Stop Loss vs Take Profit under the fixed 0.8% TP + 35% SL rule.

Key Rules:
- Scope: 2026-08-01 00:00:00 to 2026-08-26 23:59:59 (completed candles only).
- Entry: 25% penetration limit order inside Order Block.
- Stop Loss: Second edge / distal boundary of Order Block.
- Take Profit: Fixed 0.80% price move from entry.
- Dynamic Leverage: leverage = 0.35 / (risk_distance / entry).
- Global 1-Trade-at-a-Time Lock.
- Continuous Compounding from $10.00 initial capital.

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
class AugustTradeAutopsyRecord:
    """Complete diagnostic and autopsy record for an August 2026 trade."""
    trade_number: int
    asset: str
    direction: str
    ob_creation_time: str
    entry_time: str
    exit_time: str
    entry_price: float
    ob_high: float
    ob_low: float
    sl_price: float
    tp_price: float
    sl_distance_pct: float
    leverage: float
    tp_return_pct: float
    fill_candle: Dict[str, Any]
    exit_candle: Dict[str, Any]
    exit_price: float
    outcome: str  # FILLED_TP, FILLED_SL, FILLED_TIMEOUT
    realized_r: float
    starting_capital_gross: float
    gross_pnl_usd: float
    ending_capital_gross: float
    starting_capital_net: float
    notional_size_usd: float
    fees_usd: float
    net_pnl_usd: float
    ending_capital_net: float
    is_ambiguous: bool
    sl_bucket: str
    autopsy_narrative: str
    loss_mechanism: str  # INSTANT_BLOWTHROUGH, CONSOLIDATION_REVERSAL, DUAL_TOUCH_AMBIGUITY, N/A


class August2026Fixed08DiagnosticEngine:
    """
    Diagnostic backtest engine for August 2026 candle-by-candle evaluation.
    """

    def __init__(self, master_df: pd.DataFrame, candles_dict: Dict[str, pd.DataFrame], starting_capital: float = 10.0):
        self.master_df = master_df.copy()
        self.candles_dict = candles_dict
        self.starting_capital = starting_capital

    def run_diagnostic(self) -> Dict[str, Any]:
        """Runs the candle-by-candle August 2026 autopsy."""
        df = self.master_df.copy()
        df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)

        # Filter strictly for August 2026 (Aug 1 to Aug 26)
        aug_mask = (df["dec_dt"] >= "2026-08-01 00:00:00+00:00") & (df["dec_dt"] <= "2026-08-26 23:59:59+00:00")
        aug_df = df[aug_mask].sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

        capital_gross = self.starting_capital
        capital_net = self.starting_capital
        peak_gross = self.starting_capital
        peak_net = self.starting_capital
        max_dd_gross = 0.0
        max_dd_net = 0.0

        current_lock_until: Optional[pd.Timestamp] = None
        fee_rate_roundtrip = 0.0008  # 0.04% maker/taker per side

        executed_trades: List[AugustTradeAutopsyRecord] = []
        no_fill_setups: List[Dict[str, Any]] = []
        skipped_lock_setups: List[Dict[str, Any]] = []

        loss_mechanisms_count = {
            "INSTANT_BLOWTHROUGH": 0,
            "CONSOLIDATION_REVERSAL": 0,
            "DUAL_TOUCH_AMBIGUITY": 0,
        }

        sl_buckets_data: Dict[str, List[Dict[str, Any]]] = {
            "<0.30%": [],
            "0.30-0.50%": [],
            "0.50-0.70%": [],
            "0.70-1.00%": [],
            "1.00-1.50%": [],
            ">1.50%": [],
        }

        for _, row in aug_df.iterrows():
            asset = str(row["asset"])
            dec_dt = row["dec_dt"]
            direction = str(row["direction"])
            ob_top = float(row["ob_high"]) if "ob_high" in row and not pd.isna(row["ob_high"]) else float(row["top_price"])
            ob_bot = float(row["ob_low"]) if "ob_low" in row and not pd.isna(row["ob_low"]) else float(row["bottom_price"])
            width = abs(ob_top - ob_bot)
            if width <= 1e-6:
                continue

            width_pct = float(row["feat_ob_width_pct"]) if "feat_ob_width_pct" in row and not pd.isna(row["feat_ob_width_pct"]) else (width / ob_bot * 100.0)

            # ── 1. EXACT GEOMETRY ─────────────────────────────────────────────
            if direction == "LONG":
                entry_p = ob_top - 0.25 * width
                sl_p = ob_bot
                risk_dist = entry_p - sl_p
                tp_p = entry_p * 1.008
                reward_dist = tp_p - entry_p
            else:  # SHORT
                entry_p = ob_bot + 0.25 * width
                sl_p = ob_top
                risk_dist = sl_p - entry_p
                tp_p = entry_p * 0.992
                reward_dist = entry_p - tp_p

            if risk_dist <= 1e-6:
                continue

            sl_dist_dec = risk_dist / entry_p
            sl_dist_pct = sl_dist_dec * 100.0
            leverage = 0.35 / sl_dist_dec
            tp_ret_pct = 0.80 * leverage

            # Bucket classification
            if sl_dist_pct < 0.30:
                b_name = "<0.30%"
            elif sl_dist_pct < 0.50:
                b_name = "0.30-0.50%"
            elif sl_dist_pct < 0.70:
                b_name = "0.50-0.70%"
            elif sl_dist_pct < 1.00:
                b_name = "0.70-1.00%"
            elif sl_dist_pct < 1.50:
                b_name = "1.00-1.50%"
            else:
                b_name = ">1.50%"

            # ── 2. GLOBAL LOCK CHECK ─────────────────────────────────────────
            if current_lock_until is not None and dec_dt < current_lock_until:
                setup_meta = {
                    "ob_id": str(row["ob_id"]),
                    "asset": asset,
                    "direction": direction,
                    "dec_dt": str(dec_dt),
                    "entry_price": round(entry_p, 4),
                    "sl_price": round(sl_p, 4),
                    "tp_price": round(tp_p, 4),
                    "sl_dist_pct": round(sl_dist_pct, 4),
                    "leverage": round(leverage, 2),
                    "tp_return_pct": round(tp_ret_pct, 2),
                    "outcome": "SKIPPED_LOCK",
                    "narrative": f"Locked out by active position open until {current_lock_until}",
                }
                skipped_lock_setups.append(setup_meta)
                continue

            # ── 3. CANDLE-BY-CANDLE FORWARD REPLAY ───────────────────────────
            c_df = self.candles_dict[asset]
            future_candles = c_df[c_df["dt"] >= dec_dt].reset_index(drop=True)

            filled = False
            fill_bar_idx = None
            fill_dt = None
            fill_candle_dict = {}

            outcome = "NO_FILL"
            exit_dt = None
            exit_p = None
            exit_candle_dict = {}
            narrative = ""
            is_ambiguous = False
            loss_mech = "N/A"

            max_b = min(72, len(future_candles))
            for b in range(max_b):
                c = future_candles.iloc[b]
                c_dt = c["dt"]
                c_o, c_h, c_l, c_c = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])

                if not filled:
                    if direction == "LONG" and c_l <= entry_p:
                        filled = True
                        fill_bar_idx = b
                        fill_dt = c_dt
                        fill_candle_dict = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                    elif direction == "SHORT" and c_h >= entry_p:
                        filled = True
                        fill_bar_idx = b
                        fill_dt = c_dt
                        fill_candle_dict = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}

                    if not filled:
                        if direction == "LONG" and c_l <= sl_p:
                            narrative = "Invalidated (price breached distal SL before reaching 25% limit entry)."
                            break
                        elif direction == "SHORT" and c_h >= sl_p:
                            narrative = "Invalidated (price breached distal SL before reaching 25% limit entry)."
                            break
                        continue

                # Once filled, evaluate exit conditions
                if filled:
                    hit_tp = (c_h >= tp_p) if direction == "LONG" else (c_l <= tp_p)
                    hit_sl = (c_l <= sl_p) if direction == "LONG" else (c_h >= sl_p)

                    if hit_tp and hit_sl:
                        is_ambiguous = True
                        outcome = "FILLED_SL"
                        exit_dt = c_dt
                        exit_p = sl_p
                        exit_candle_dict = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                        loss_mech = "DUAL_TOUCH_AMBIGUITY"
                        narrative = f"Dual-touch ambiguous 1H candle. Both TP ({tp_p:.2f}) and SL ({sl_p:.2f}) touched in candle [{c_l:.2f} - {c_h:.2f}]. Conservative rule resolves to SL."
                        break
                    elif hit_tp and not hit_sl:
                        outcome = "FILLED_TP"
                        exit_dt = c_dt
                        exit_p = tp_p
                        exit_candle_dict = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                        narrative = f"Price expanded in trade direction to hit fixed 0.8% TP ({tp_p:.2f}) in candle [{c_l:.2f} - {c_h:.2f}]."
                        break
                    elif hit_sl and not hit_tp:
                        outcome = "FILLED_SL"
                        exit_dt = c_dt
                        exit_p = sl_p
                        exit_candle_dict = {"dt": str(c_dt), "open": c_o, "high": c_h, "low": c_l, "close": c_c}
                        if b == fill_bar_idx:
                            loss_mech = "INSTANT_BLOWTHROUGH"
                            narrative = f"Instant penetration blowthrough. Candle entered OB, filled limit order at {entry_p:.2f}, and pierced distal SL at {sl_p:.2f} in the same candle."
                        else:
                            loss_mech = "CONSOLIDATION_REVERSAL"
                            narrative = f"Filled at {entry_p:.2f}, moved inside zone for {b - fill_bar_idx} bars, then reversed and breached distal SL at {sl_p:.2f}."
                        break

            if filled and outcome not in ["FILLED_TP", "FILLED_SL"]:
                outcome = "FILLED_TIMEOUT"
                last_c = future_candles.iloc[max_b - 1]
                exit_dt = last_c["dt"]
                exit_p = float(last_c["close"])
                exit_candle_dict = {"dt": str(exit_dt), "open": float(last_c["open"]), "high": float(last_c["high"]), "low": float(last_c["low"]), "close": exit_p}
                narrative = f"72-hour holding limit reached without touching TP or SL. Closed at market {exit_p:.2f}."

            setup_meta = {
                "ob_id": str(row["ob_id"]),
                "asset": asset,
                "direction": direction,
                "dec_dt": str(dec_dt),
                "entry_price": round(entry_p, 4),
                "sl_price": round(sl_p, 4),
                "tp_price": round(tp_p, 4),
                "sl_dist_pct": round(sl_dist_pct, 4),
                "leverage": round(leverage, 2),
                "tp_return_pct": round(tp_ret_pct, 2),
                "outcome": outcome,
                "narrative": narrative or "Price never penetrated 25% depth into zone.",
            }

            if not filled:
                no_fill_setups.append(setup_meta)
            else:
                if loss_mech in loss_mechanisms_count:
                    loss_mechanisms_count[loss_mech] += 1

                # Trade executed in global portfolio
                if outcome == "FILLED_TP":
                    realized_ret_pct = tp_ret_pct
                    realized_r = reward_dist / risk_dist
                elif outcome == "FILLED_SL":
                    realized_ret_pct = -35.00
                    realized_r = -1.0000
                else:
                    p_diff = (exit_p - entry_p) if direction == "LONG" else (entry_p - exit_p)
                    realized_r = p_diff / risk_dist
                    realized_ret_pct = realized_r * 35.00

                current_lock_until = exit_dt

                # Continuous compounding ledger
                # Gross
                start_g = capital_gross
                gross_pnl = start_g * (realized_ret_pct / 100.0)
                capital_gross = max(0.0, start_g + gross_pnl)
                if capital_gross > peak_gross:
                    peak_gross = capital_gross
                dd_g = (peak_gross - capital_gross) / peak_gross * 100.0 if peak_gross > 0 else 0.0
                max_dd_gross = max(max_dd_gross, dd_g)

                # Net
                start_n = capital_net
                notional = start_n * leverage
                fees = notional * fee_rate_roundtrip
                net_pnl = (start_n * (realized_ret_pct / 100.0)) - fees
                capital_net = max(0.0, start_n + net_pnl)
                if capital_net > peak_net:
                    peak_net = capital_net
                dd_n = (peak_net - capital_net) / peak_net * 100.0 if peak_net > 0 else 0.0
                max_dd_net = max(max_dd_net, dd_n)

                rec = AugustTradeAutopsyRecord(
                    trade_number=len(executed_trades) + 1,
                    asset=asset,
                    direction=direction,
                    ob_creation_time=str(dec_dt),
                    entry_time=str(fill_dt),
                    exit_time=str(exit_dt),
                    entry_price=round(entry_p, 4),
                    ob_high=round(ob_top, 4),
                    ob_low=round(ob_bot, 4),
                    sl_price=round(sl_p, 4),
                    tp_price=round(tp_p, 4),
                    sl_distance_pct=round(sl_dist_pct, 4),
                    leverage=round(leverage, 4),
                    tp_return_pct=round(tp_ret_pct, 4),
                    fill_candle=fill_candle_dict,
                    exit_candle=exit_candle_dict,
                    exit_price=round(exit_p if exit_p is not None else 0.0, 4),
                    outcome=outcome,
                    realized_r=round(realized_r, 4),
                    starting_capital_gross=round(start_g, 6),
                    gross_pnl_usd=round(gross_pnl, 6),
                    ending_capital_gross=round(capital_gross, 6),
                    starting_capital_net=round(start_n, 6),
                    notional_size_usd=round(notional, 6),
                    fees_usd=round(fees, 6),
                    net_pnl_usd=round(net_pnl, 6),
                    ending_capital_net=round(capital_net, 6),
                    is_ambiguous=is_ambiguous,
                    sl_bucket=b_name,
                    autopsy_narrative=narrative,
                    loss_mechanism=loss_mech,
                )
                executed_trades.append(rec)
                sl_buckets_data[b_name].append(asdict(rec))

        # ── COMPILE SUMMARY METRICS ──────────────────────────────────────────
        trades_df = pd.DataFrame([asdict(t) for t in executed_trades])
        total_exec = len(trades_df)
        wins_df = trades_df[trades_df["outcome"] == "FILLED_TP"]
        losses_df = trades_df[trades_df["outcome"] == "FILLED_SL"]
        win_count = len(wins_df)
        loss_count = len(losses_df)
        win_rate = (win_count / total_exec * 100.0) if total_exec > 0 else 0.0
        exp_r = float(trades_df["realized_r"].mean()) if total_exec > 0 else 0.0
        tot_r = float(trades_df["realized_r"].sum()) if total_exec > 0 else 0.0
        g_gain = float(wins_df["realized_r"].sum()) if len(wins_df) > 0 else 0.0
        g_loss = abs(float(losses_df["realized_r"].sum())) if len(losses_df) > 0 else 0.0
        pf = (g_gain / g_loss) if g_loss > 0 else 99.0

        # Streaks
        max_w = 0
        max_l = 0
        curr_w = 0
        curr_l = 0
        for r in trades_df["realized_r"]:
            if r > 0:
                curr_w += 1
                curr_l = 0
                max_w = max(max_w, curr_w)
            else:
                curr_l += 1
                curr_w = 0
                max_l = max(max_l, curr_l)

        # Asset Breakdown
        assets_summary = {}
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            adf = trades_df[trades_df["asset"] == sym] if total_exec > 0 else pd.DataFrame()
            if len(adf) > 0:
                awins = adf[adf["outcome"] == "FILLED_TP"]
                aloss = adf[adf["outcome"] == "FILLED_SL"]
                a_n = len(adf)
                a_wr = len(awins) / a_n * 100.0
                a_exp = float(adf["realized_r"].mean())
                a_tot = float(adf["realized_r"].sum())
                a_gg = float(awins["realized_r"].sum()) if len(awins) > 0 else 0.0
                a_gl = abs(float(aloss["realized_r"].sum())) if len(aloss) > 0 else 0.0
                a_pf = (a_gg / a_gl) if a_gl > 0 else 99.0
                assets_summary[sym] = {
                    "trade_count": a_n,
                    "win_count": len(awins),
                    "loss_count": len(aloss),
                    "win_rate_pct": round(a_wr, 2),
                    "avg_leverage": round(float(adf["leverage"].mean()), 2),
                    "expectancy_r": round(a_exp, 4),
                    "total_r": round(a_tot, 2),
                    "profit_factor": round(a_pf, 2),
                }
            else:
                assets_summary[sym] = {
                    "trade_count": 0,
                    "win_count": 0,
                    "loss_count": 0,
                    "win_rate_pct": 0.0,
                    "avg_leverage": 0.0,
                    "expectancy_r": 0.0,
                    "total_r": 0.0,
                    "profit_factor": 0.0,
                }

        # Daily Breakdown
        trades_df["day"] = trades_df["entry_time"].str.slice(0, 10)
        daily_summary = []
        for d_str, ddf in trades_df.groupby("day"):
            dwins = ddf[ddf["outcome"] == "FILLED_TP"]
            dloss = ddf[ddf["outcome"] == "FILLED_SL"]
            d_n = len(ddf)
            d_wr = len(dwins) / d_n * 100.0 if d_n > 0 else 0.0
            daily_summary.append({
                "date": str(d_str),
                "trades": d_n,
                "wins": len(dwins),
                "losses": len(dloss),
                "win_rate_pct": round(d_wr, 2),
                "ending_capital_net": round(float(ddf.iloc[-1]["ending_capital_net"]), 4),
                "ending_capital_gross": round(float(ddf.iloc[-1]["ending_capital_gross"]), 4),
            })

        # SL Buckets Summary
        buckets_summary = {}
        for b_name, b_list in sl_buckets_data.items():
            if len(b_list) > 0:
                b_df = pd.DataFrame(b_list)
                bw = len(b_df[b_df["outcome"] == "FILLED_TP"])
                bl = len(b_df[b_df["outcome"] == "FILLED_SL"])
                bn = len(b_df)
                b_wr = bw / bn * 100.0
                b_exp = float(b_df["realized_r"].mean())
                b_tot = float(b_df["realized_r"].sum())
                b_gg = float(b_df[b_df["realized_r"] > 0]["realized_r"].sum())
                b_gl = abs(float(b_df[b_df["realized_r"] <= 0]["realized_r"].sum()))
                b_pf = (b_gg / b_gl) if b_gl > 0 else 99.0
                buckets_summary[b_name] = {
                    "trade_count": bn,
                    "win_count": bw,
                    "loss_count": bl,
                    "win_rate_pct": round(b_wr, 2),
                    "avg_leverage": round(float(b_df["leverage"].mean()), 2),
                    "avg_tp_return_pct": round(float(b_df["tp_return_pct"].mean()), 2),
                    "expectancy_r": round(b_exp, 4),
                    "profit_factor": round(b_pf, 2),
                    "total_r": round(b_tot, 2),
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
                    "total_r": 0.0,
                }

        return {
            "overall": {
                "starting_capital": self.starting_capital,
                "ending_capital_gross": round(capital_gross, 4),
                "net_return_pct_gross": round((capital_gross - self.starting_capital) / self.starting_capital * 100.0, 2),
                "ending_capital_net": round(capital_net, 4),
                "net_return_pct_net": round((capital_net - self.starting_capital) / self.starting_capital * 100.0, 2),
                "total_august_setups": len(aug_df),
                "unfilled_setups_count": len(no_fill_setups),
                "skipped_lock_count": len(skipped_lock_setups),
                "executed_trades_count": total_exec,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate_pct": round(win_rate, 2),
                "expectancy_r": round(exp_r, 4),
                "total_realized_r": round(tot_r, 2),
                "profit_factor": round(pf, 2),
                "max_drawdown_pct_gross": round(max_dd_gross, 2),
                "max_drawdown_pct_net": round(max_dd_net, 2),
                "max_win_streak": max_w,
                "max_loss_streak": max_l,
                "total_fees_usd": round(float(trades_df["fees_usd"].sum()), 4) if total_exec > 0 else 0.0,
                "avg_leverage": round(float(trades_df["leverage"].mean()), 2) if total_exec > 0 else 0.0,
                "median_leverage": round(float(trades_df["leverage"].median()), 2) if total_exec > 0 else 0.0,
                "max_leverage": round(float(trades_df["leverage"].max()), 2) if total_exec > 0 else 0.0,
                "min_leverage": round(float(trades_df["leverage"].min()), 2) if total_exec > 0 else 0.0,
                "avg_tp_return_pct": round(float(trades_df["tp_return_pct"].mean()), 2) if total_exec > 0 else 0.0,
                "loss_mechanisms": loss_mechanisms_count,
            },
            "inventory": {
                "total_setups": len(aug_df),
                "no_fill_count": len(no_fill_setups),
                "skipped_lock_count": len(skipped_lock_setups),
                "executed_count": total_exec,
                "no_fill_details": no_fill_setups,
                "skipped_lock_details": skipped_lock_setups,
            },
            "sl_buckets_breakdown": buckets_summary,
            "assets_breakdown": assets_summary,
            "daily_breakdown": daily_summary,
            "trades": [asdict(t) for t in executed_trades],
        }
