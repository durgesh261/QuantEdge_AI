"""
QuantEdge AI — Phase 7: Strategy Robustness, Execution Reality & Validation Research.
======================================================================================
Governance:
    live_execution_authorized = False
    AI_PROMOTION_STATUS = REJECTED
    execution_status = BLOCKED_BY_SYSTEM

The 445 canonical trades produced by the Displacement-Gated OB Engine (Mode A)
are completely FROZEN.

This module evaluates:
1. Fee sensitivity (0.00% to 0.50%)
2. Slippage sensitivity (0 to 50 bps adverse slippage)
3. Combined execution scenarios (Ideal, Backtest, Conservative, Realistic, Stress, Severe)
4. Best / worst trade concentration analysis
5. Asset removal / leave-one-asset-out
6. Time stability (4 chronological half-year blocks)
7. Rolling performance (25, 50, 100 trade rolling windows)
8. Monte Carlo with execution degradation (10,000 paths)
9. Risk sensitivity under realistic execution (2.5% to 15%)
10. Leverage cap robustness (10x to 100x)
11. Trade sequence dependency (Chronological, Permutation, Asset-block, Monthly-block)
12. Drawdown recovery duration analysis (trades, hours, days, months)
13. Statistical bootstrap confidence intervals (10,000 samples)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from quantedge.ai.research.displacement_gated_retest_engine import (
    DisplacementGatedConfig,
    run_displacement_gated_backtest,
)

# ---------------------------------------------------------------------------
# Governance invariants
# ---------------------------------------------------------------------------
live_execution_authorized: bool = False
AI_PROMOTION_STATUS: str = "REJECTED"
execution_status: str = "BLOCKED_BY_SYSTEM"

STARTING_CAPITAL: float = 10.0
BASE_FEE_RATE: float = 0.0008  # 0.08%
TP_MARKET_PCT: float = 0.60    # 0.60% fixed TP
START_DATE = datetime(2024, 6, 1, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 8, 26, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Immutable Frozen Canonical Trade Representation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FrozenTrade:
    trade_id: int
    asset: str
    direction: str          # LONG / SHORT
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    tp_price: float
    sl_price: float
    outcome: str            # FILLED_TP / FILLED_SL / FILLED_TIMEOUT
    realized_r: float       # Strategy R (gross, geometry-based)
    sl_dist_pct: float      # Distance from entry to SL (%)
    tp_dist_pct: float      # Distance from entry to TP (%)
    leverage: float         # Theoretical leverage from baseline


# ---------------------------------------------------------------------------
# Loader & Invariant Verification (Cached)
# ---------------------------------------------------------------------------
_CACHED_TRADES: Optional[List[FrozenTrade]] = None


def load_frozen_canonical_trades(
    data_base_dir: Optional[Path] = None,
    force_reload: bool = False,
) -> List[FrozenTrade]:
    """
    Loads and locks the 445 canonical trades from the displacement-gated engine.
    Caches in memory for instant reuse across unit tests and simulations.
    Verifies all invariants before returning.
    """
    global _CACHED_TRADES
    assert not live_execution_authorized, "Governance violation: live execution not permitted"

    if _CACHED_TRADES is not None and not force_reload and data_base_dir is None:
        return list(_CACHED_TRADES)

    cfg = DisplacementGatedConfig()
    cfg.displacement_mode = "A"
    cfg.displacement_ob_width_multiple = 1.0
    cfg.starting_capital = STARTING_CAPITAL
    cfg.max_holding_bars = 72

    result = run_displacement_gated_backtest(
        data_base_dir=data_base_dir,
        config=cfg,
        symbols=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"],
        start_date=START_DATE,
        end_date=END_DATE,
        audit_mode=False,
    )

    trades_df = result["trades_df"]

    # 1. Trade count check
    if len(trades_df) != 445:
        raise ValueError(f"Invariant failure: expected exactly 445 trades, got {len(trades_df)}")

    frozen_list: List[FrozenTrade] = []
    prev_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)

    for _, row in trades_df.iterrows():
        entry_dt = pd.to_datetime(row["entry_time"])
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)

        exit_dt = pd.to_datetime(row["exit_time"])
        if exit_dt.tzinfo is None:
            exit_dt = exit_dt.replace(tzinfo=timezone.utc)

        # 2. Chronological order verification
        if entry_dt < prev_dt:
            raise ValueError(f"Invariant failure: trade {row['trade_id']} out of chronological order ({entry_dt} < {prev_dt})")
        prev_dt = entry_dt

        frozen_list.append(FrozenTrade(
            trade_id=int(row["trade_id"]),
            asset=str(row["asset"]),
            direction=str(row["direction"]),
            entry_time=entry_dt,
            exit_time=exit_dt,
            entry_price=float(row["entry_price"]),
            tp_price=float(row["tp_price"]),
            sl_price=float(row["sl_price"]),
            outcome=str(row["outcome"]),
            realized_r=float(row["realized_r"]),
            sl_dist_pct=float(row["entry_to_sl_distance_pct"]),
            tp_dist_pct=float(row["entry_to_tp_distance_pct"]) if "entry_to_tp_distance_pct" in row else TP_MARKET_PCT,
            leverage=float(row["leverage"]),
        ))

    # 3. Canonical outcome verification (304 TP, 141 SL)
    wins = sum(1 for t in frozen_list if t.outcome == "FILLED_TP")
    losses = sum(1 for t in frozen_list if t.outcome == "FILLED_SL")
    if wins != 304 or losses != 141:
        raise ValueError(f"Invariant failure: expected 304 wins, 141 losses, got {wins}W, {losses}L")

    # 4. Canonical Gross R verification (+122.0586R)
    total_r = sum(t.realized_r for t in frozen_list)
    if abs(total_r - 122.0586) > 0.05:
        raise ValueError(f"Invariant failure: expected ~122.06 total R, got {total_r:.4f}")

    _CACHED_TRADES = list(frozen_list)
    return frozen_list



# ---------------------------------------------------------------------------
# Generalized Simulation Engine (Frictions & Sizing)
# ---------------------------------------------------------------------------
@dataclass
class SimTradeResult:
    trade_id: int
    asset: str
    direction: str
    entry_time: str
    exit_time: str
    outcome: str
    strategy_R: float
    degraded_gross_R: float
    net_R_after_fees: float
    entry_price_exec: float
    exit_price_exec: float
    effective_leverage: float
    leverage_capped: bool
    notional: float
    gross_pnl: float
    fees: float
    net_pnl: float
    starting_capital: float
    ending_capital: float
    return_pct: float


def simulate_trades(
    trades: List[FrozenTrade],
    risk_pct: float = 5.0,
    leverage_cap: float = 50.0,
    fee_rate: float = 0.0008,
    slippage_bps: float = 0.0,
    compounding: str = "compound",  # "compound" or "flat"
    initial_capital: float = STARTING_CAPITAL,
) -> Tuple[List[SimTradeResult], Dict[str, Any]]:
    """
    Simulates execution on a list of FrozenTrade records under specific friction/sizing rules.
    Slippage is applied directionally (adverse for both entry and exit).
    """
    assert not live_execution_authorized

    s = slippage_bps / 10000.0  # bps to fraction (e.g. 5 bps = 0.0005)
    capital = initial_capital
    peak_capital = initial_capital
    max_dd_pct = 0.0
    max_dd_dollar = 0.0
    min_equity = initial_capital

    cur_losing_streak = 0
    max_losing_streak = 0

    results: List[SimTradeResult] = []

    for t in trades:
        if capital <= 0.0:
            break

        # Basis
        basis = capital if compounding == "compound" else initial_capital

        # Leverage sizing
        sl_frac = t.sl_dist_pct / 100.0
        target_lev = (risk_pct / 100.0) / sl_frac if sl_frac > 0 else leverage_cap
        eff_lev = min(leverage_cap, target_lev)
        lev_capped = eff_lev < (target_lev - 1e-9)

        notional = basis * eff_lev
        fees = notional * fee_rate

        # Directional execution prices with adverse slippage
        if t.direction == "LONG":
            exec_entry = t.entry_price * (1.0 + s)  # Higher buy price
            if t.outcome == "FILLED_TP":
                exec_exit = t.tp_price * (1.0 - s)  # Lower sell price
                eff_ret = (exec_exit - exec_entry) / exec_entry
            elif t.outcome == "FILLED_SL":
                exec_exit = t.sl_price * (1.0 - s)  # Lower sell price
                eff_ret = (exec_exit - exec_entry) / exec_entry
            else:  # TIMEOUT
                exec_exit = t.entry_price * (1.0 - s)
                eff_ret = (exec_exit - exec_entry) / exec_entry
        else:  # SHORT
            exec_entry = t.entry_price * (1.0 - s)  # Lower sell price
            if t.outcome == "FILLED_TP":
                exec_exit = t.tp_price * (1.0 + s)  # Higher buyback price
                eff_ret = (exec_entry - exec_exit) / exec_entry
            elif t.outcome == "FILLED_SL":
                exec_exit = t.sl_price * (1.0 + s)  # Higher buyback price
                eff_ret = (exec_entry - exec_exit) / exec_entry
            else:  # TIMEOUT
                exec_exit = t.entry_price * (1.0 + s)
                eff_ret = (exec_entry - exec_exit) / exec_entry

        gross_pnl = notional * eff_ret
        net_pnl = gross_pnl - fees

        risk_dollar_nominal = basis * eff_lev * sl_frac
        degraded_gross_r = gross_pnl / risk_dollar_nominal if risk_dollar_nominal > 1e-12 else 0.0
        net_r = net_pnl / risk_dollar_nominal if risk_dollar_nominal > 1e-12 else 0.0

        ending_capital = max(0.0, capital + net_pnl)
        ret_pct = (net_pnl / capital * 100.0) if capital > 0 else 0.0

        # Drawdown tracking
        if ending_capital > peak_capital:
            peak_capital = ending_capital
        dd_pct = (peak_capital - ending_capital) / peak_capital * 100.0 if peak_capital > 0 else 0.0
        dd_dollar = peak_capital - ending_capital
        max_dd_pct = max(max_dd_pct, dd_pct)
        max_dd_dollar = max(max_dd_dollar, dd_dollar)
        min_equity = min(min_equity, ending_capital)

        # Streak tracking
        if t.outcome == "FILLED_SL":
            cur_losing_streak += 1
            max_losing_streak = max(max_losing_streak, cur_losing_streak)
        else:
            cur_losing_streak = 0

        results.append(SimTradeResult(
            trade_id=t.trade_id,
            asset=t.asset,
            direction=t.direction,
            entry_time=t.entry_time.isoformat(),
            exit_time=t.exit_time.isoformat(),
            outcome=t.outcome,
            strategy_R=round(t.realized_r, 6),
            degraded_gross_R=round(degraded_gross_r, 6),
            net_R_after_fees=round(net_r, 6),
            entry_price_exec=round(exec_entry, 6),
            exit_price_exec=round(exec_exit, 6),
            effective_leverage=round(eff_lev, 4),
            leverage_capped=lev_capped,
            notional=round(notional, 6),
            gross_pnl=round(gross_pnl, 8),
            fees=round(fees, 8),
            net_pnl=round(net_pnl, 8),
            starting_capital=round(capital, 8),
            ending_capital=round(ending_capital, 8),
            return_pct=round(ret_pct, 6),
        ))

        capital = ending_capital

    # Aggregate metrics
    n = len(results)
    if n == 0:
        return [], {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "total_strategy_R": 0.0,
            "total_degraded_gross_R": 0.0,
            "total_net_R": 0.0,
            "expectancy_strategy_R": 0.0,
            "expectancy_net_R": 0.0,
            "profit_factor": 0.0,
            "starting_capital": initial_capital,
            "ending_capital": initial_capital,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_dollar": 0.0,
            "min_equity_reached": initial_capital,
            "max_losing_streak": 0,
            "avg_leverage": 0.0,
            "median_leverage": 0.0,
            "capped_trades_count": 0,
        }

    df = pd.DataFrame([asdict(r) for r in results])
    wins = df[df["outcome"] == "FILLED_TP"]
    losses = df[df["outcome"] == "FILLED_SL"]
    w_count = len(wins)
    l_count = len(losses)

    tot_strat_r = float(df["strategy_R"].sum())
    tot_gross_deg_r = float(df["degraded_gross_R"].sum())
    tot_net_r = float(df["net_R_after_fees"].sum())

    gross_gains = float(wins["net_pnl"].sum()) if w_count > 0 else 0.0
    gross_losses = abs(float(losses["net_pnl"].sum())) if l_count > 0 else 1.0
    pf = round(gross_gains / gross_losses, 4) if gross_losses > 0 else 99.0

    summary = {
        "trades": n,
        "wins": w_count,
        "losses": l_count,
        "win_rate_pct": round(w_count / n * 100.0, 4),
        "total_strategy_R": round(tot_strat_r, 4),
        "total_degraded_gross_R": round(tot_gross_deg_r, 4),
        "total_net_R": round(tot_net_r, 4),
        "expectancy_strategy_R": round(tot_strat_r / n, 6),
        "expectancy_net_R": round(tot_net_r / n, 6),
        "profit_factor": pf,
        "starting_capital": initial_capital,
        "ending_capital": round(capital, 6),
        "total_return_pct": round((capital - initial_capital) / initial_capital * 100.0, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "max_drawdown_dollar": round(max_dd_dollar, 6),
        "min_equity_reached": round(min_equity, 6),
        "max_losing_streak": max_losing_streak,
        "avg_leverage": round(float(df["effective_leverage"].mean()), 4),
        "median_leverage": round(float(df["effective_leverage"].median()), 4),
        "capped_trades_count": int(df["leverage_capped"].sum()),
    }

    return results, summary


# ---------------------------------------------------------------------------
# EXPERIMENT 1 — Fee Sensitivity
# ---------------------------------------------------------------------------
def run_experiment_1_fee_sensitivity(trades: List[FrozenTrade]) -> List[Dict[str, Any]]:
    fee_levels = [0.0000, 0.0004, 0.0008, 0.0012, 0.0016, 0.0020, 0.0030, 0.0050]
    rows = []
    for f in fee_levels:
        _, s = simulate_trades(trades, risk_pct=5.0, leverage_cap=50.0, fee_rate=f, slippage_bps=0.0)
        rows.append({
            "fee_rate_pct": round(f * 100, 4),
            "trades": s["trades"],
            "win_rate_pct": s["win_rate_pct"],
            "total_strategy_R": s["total_strategy_R"],
            "total_net_R": s["total_net_R"],
            "profit_factor": s["profit_factor"],
            "expectancy_net_R": s["expectancy_net_R"],
            "ending_capital": s["ending_capital"],
            "total_return_pct": s["total_return_pct"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "is_profitable": s["ending_capital"] > STARTING_CAPITAL,
        })
    return rows


# ---------------------------------------------------------------------------
# EXPERIMENT 2 — Slippage Sensitivity
# ---------------------------------------------------------------------------
def run_experiment_2_slippage_sensitivity(trades: List[FrozenTrade]) -> Dict[str, Any]:
    slip_levels = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
    rows = []
    for slip in slip_levels:
        _, s = simulate_trades(trades, risk_pct=5.0, leverage_cap=50.0, fee_rate=BASE_FEE_RATE, slippage_bps=slip)
        rows.append({
            "slippage_bps": slip,
            "trades": s["trades"],
            "win_rate_pct": s["win_rate_pct"],
            "total_strategy_R": s["total_strategy_R"],
            "total_degraded_gross_R": s["total_degraded_gross_R"],
            "total_net_R": s["total_net_R"],
            "profit_factor": s["profit_factor"],
            "expectancy_net_R": s["expectancy_net_R"],
            "ending_capital": s["ending_capital"],
            "total_return_pct": s["total_return_pct"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "is_profitable": s["ending_capital"] > STARTING_CAPITAL,
        })

    # Find break-even slippage where PF >= 1.0 (fine search up to 100 bps)
    be_slippage = 0.0
    for test_s in np.arange(0.0, 100.0, 0.5):
        _, s = simulate_trades(trades, risk_pct=5.0, leverage_cap=50.0, fee_rate=BASE_FEE_RATE, slippage_bps=test_s)
        if s["profit_factor"] >= 1.0 and s["total_net_R"] >= 0:
            be_slippage = test_s
        else:
            break

    return {"table": rows, "breakeven_slippage_bps": be_slippage}


# ---------------------------------------------------------------------------
# EXPERIMENT 3 — Combined Fees + Slippage Scenarios
# ---------------------------------------------------------------------------
def run_experiment_3_combined_scenarios(trades: List[FrozenTrade]) -> List[Dict[str, Any]]:
    scenarios = [
        ("Scenario A: Ideal", 0.0000, 0.0),
        ("Scenario B: Current Backtest", 0.0008, 0.0),
        ("Scenario C: Conservative", 0.0008, 5.0),
        ("Scenario D: Realistic", 0.0008, 10.0),
        ("Scenario E: Stress", 0.0016, 20.0),
        ("Scenario F: Severe Stress", 0.0030, 50.0),
    ]
    rows = []
    for name, fee, slip in scenarios:
        _, s = simulate_trades(trades, risk_pct=5.0, leverage_cap=50.0, fee_rate=fee, slippage_bps=slip)
        rows.append({
            "scenario": name,
            "fee_rate_pct": round(fee * 100, 4),
            "slippage_bps": slip,
            "trades": s["trades"],
            "win_rate_pct": s["win_rate_pct"],
            "total_strategy_R": s["total_strategy_R"],
            "total_net_R": s["total_net_R"],
            "profit_factor": s["profit_factor"],
            "expectancy_net_R": s["expectancy_net_R"],
            "ending_capital": s["ending_capital"],
            "total_return_pct": s["total_return_pct"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "min_equity_reached": s["min_equity_reached"],
        })
    return rows


# ---------------------------------------------------------------------------
# EXPERIMENT 4 — Best/Worst Trade Concentration
# ---------------------------------------------------------------------------
def run_experiment_4_trade_concentration(trades: List[FrozenTrade]) -> Dict[str, Any]:
    # Baseline simulation
    recs, base_summary = simulate_trades(trades, risk_pct=5.0, leverage_cap=50.0)

    # Sort trades by realized net dollar PnL
    sorted_trades = sorted(zip(trades, recs), key=lambda pair: pair[1].net_pnl, reverse=True)

    total_net_r = base_summary["total_net_R"]
    total_strat_r = base_summary["total_strategy_R"]

    # Asset contribution to R
    asset_r = {}
    for t in trades:
        asset_r[t.asset] = asset_r.get(t.asset, 0.0) + t.realized_r

    asset_pct = {a: round(r / total_strat_r * 100.0, 2) for a, r in asset_r.items()}

    # Top trade contributions
    top_1pct_n = max(1, int(len(trades) * 0.01))
    top_5pct_n = max(1, int(len(trades) * 0.05))
    top_10pct_n = max(1, int(len(trades) * 0.10))

    top_1pct_r = sum(p[0].realized_r for p in sorted_trades[:top_1pct_n])
    top_5pct_r = sum(p[0].realized_r for p in sorted_trades[:top_5pct_n])
    top_10pct_r = sum(p[0].realized_r for p in sorted_trades[:top_10pct_n])

    removal_rows = []
    for n_remove in [1, 5, 10, 20, 50]:
        # Remove best N
        rem_best_trades = [p[0] for p in sorted_trades[n_remove:]]
        # preserve chronological order of remaining
        rem_best_chron = sorted(rem_best_trades, key=lambda t: t.entry_time)
        _, s_best = simulate_trades(rem_best_chron, risk_pct=5.0, leverage_cap=50.0)

        removal_rows.append({
            "experiment": f"Remove Best {n_remove}",
            "removed_count": n_remove,
            "remaining_trades": s_best["trades"],
            "win_rate_pct": s_best["win_rate_pct"],
            "total_strategy_R": s_best["total_strategy_R"],
            "total_net_R": s_best["total_net_R"],
            "profit_factor": s_best["profit_factor"],
            "expectancy_net_R": s_best["expectancy_net_R"],
            "ending_capital": s_best["ending_capital"],
            "total_return_pct": s_best["total_return_pct"],
            "max_drawdown_pct": s_best["max_drawdown_pct"],
        })

        # Remove worst N
        rem_worst_trades = [p[0] for p in sorted_trades[:-n_remove]]
        rem_worst_chron = sorted(rem_worst_trades, key=lambda t: t.entry_time)
        _, s_worst = simulate_trades(rem_worst_chron, risk_pct=5.0, leverage_cap=50.0)

        removal_rows.append({
            "experiment": f"Remove Worst {n_remove}",
            "removed_count": n_remove,
            "remaining_trades": s_worst["trades"],
            "win_rate_pct": s_worst["win_rate_pct"],
            "total_strategy_R": s_worst["total_strategy_R"],
            "total_net_R": s_worst["total_net_R"],
            "profit_factor": s_worst["profit_factor"],
            "expectancy_net_R": s_worst["expectancy_net_R"],
            "ending_capital": s_worst["ending_capital"],
            "total_return_pct": s_worst["total_return_pct"],
            "max_drawdown_pct": s_worst["max_drawdown_pct"],
        })

    # Check: does edge survive removing top 5% (22 trades)?
    rem_top5pct_trades = [p[0] for p in sorted_trades[top_5pct_n:]]
    rem_top5pct_chron = sorted(rem_top5pct_trades, key=lambda t: t.entry_time)
    _, s_top5pct = simulate_trades(rem_top5pct_chron, risk_pct=5.0, leverage_cap=50.0)
    edge_survives_top5pct = s_top5pct["total_net_R"] > 0 and s_top5pct["profit_factor"] > 1.0

    return {
        "removal_table": removal_rows,
        "asset_r_breakdown": asset_r,
        "asset_r_pct": asset_pct,
        "top_1pct_contribution_pct": round(top_1pct_r / total_strat_r * 100.0, 2),
        "top_5pct_contribution_pct": round(top_5pct_r / total_strat_r * 100.0, 2),
        "top_10pct_contribution_pct": round(top_10pct_r / total_strat_r * 100.0, 2),
        "survives_removing_top_5pct": edge_survives_top5pct,
        "top_5pct_removed_net_r": s_top5pct["total_net_R"],
        "top_5pct_removed_pf": s_top5pct["profit_factor"],
        "top_5pct_removed_capital": s_top5pct["ending_capital"],
    }


# ---------------------------------------------------------------------------
# EXPERIMENT 5 — Asset Removal / Leave-One-Asset-Out
# ---------------------------------------------------------------------------
def run_experiment_5_asset_exclusion(trades: List[FrozenTrade]) -> List[Dict[str, Any]]:
    assets = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    rows = []

    # 1. Full Universe
    _, s_full = simulate_trades(trades, risk_pct=5.0, leverage_cap=50.0)
    # OOS for full (2026-01-01+)
    oos_trades_full = [t for t in trades if t.entry_time >= datetime(2026, 1, 1, tzinfo=timezone.utc)]
    _, s_full_oos = simulate_trades(oos_trades_full, risk_pct=5.0, leverage_cap=50.0)

    rows.append({
        "universe": "Full (BTC+ETH+SOL+XRP)",
        "trades": s_full["trades"],
        "win_rate_pct": s_full["win_rate_pct"],
        "total_strategy_R": s_full["total_strategy_R"],
        "total_net_R": s_full["total_net_R"],
        "profit_factor": s_full["profit_factor"],
        "expectancy_net_R": s_full["expectancy_net_R"],
        "ending_capital": s_full["ending_capital"],
        "total_return_pct": s_full["total_return_pct"],
        "max_drawdown_pct": s_full["max_drawdown_pct"],
        "oos_trades": s_full_oos["trades"],
        "oos_wr_pct": s_full_oos["win_rate_pct"],
        "oos_net_R": s_full_oos["total_net_R"],
        "oos_pf": s_full_oos["profit_factor"],
    })

    # 2. Leave-One-Out
    for excluded in assets:
        sub_trades = [t for t in trades if t.asset != excluded]
        _, s_sub = simulate_trades(sub_trades, risk_pct=5.0, leverage_cap=50.0)
        oos_sub = [t for t in sub_trades if t.entry_time >= datetime(2026, 1, 1, tzinfo=timezone.utc)]
        _, s_sub_oos = simulate_trades(oos_sub, risk_pct=5.0, leverage_cap=50.0)

        rows.append({
            "universe": f"Exclude {excluded}",
            "trades": s_sub["trades"],
            "win_rate_pct": s_sub["win_rate_pct"],
            "total_strategy_R": s_sub["total_strategy_R"],
            "total_net_R": s_sub["total_net_R"],
            "profit_factor": s_sub["profit_factor"],
            "expectancy_net_R": s_sub["expectancy_net_R"],
            "ending_capital": s_sub["ending_capital"],
            "total_return_pct": s_sub["total_return_pct"],
            "max_drawdown_pct": s_sub["max_drawdown_pct"],
            "oos_trades": s_sub_oos["trades"],
            "oos_wr_pct": s_sub_oos["win_rate_pct"],
            "oos_net_R": s_sub_oos["total_net_R"],
            "oos_pf": s_sub_oos["profit_factor"],
        })

    # 3. Pair-only performance
    for pair in assets:
        pair_trades = [t for t in trades if t.asset == pair]
        _, s_pair = simulate_trades(pair_trades, risk_pct=5.0, leverage_cap=50.0)
        oos_pair = [t for t in pair_trades if t.entry_time >= datetime(2026, 1, 1, tzinfo=timezone.utc)]
        _, s_pair_oos = simulate_trades(oos_pair, risk_pct=5.0, leverage_cap=50.0)

        rows.append({
            "universe": f"Only {pair}",
            "trades": s_pair["trades"],
            "win_rate_pct": s_pair["win_rate_pct"],
            "total_strategy_R": s_pair["total_strategy_R"],
            "total_net_R": s_pair["total_net_R"],
            "profit_factor": s_pair["profit_factor"],
            "expectancy_net_R": s_pair["expectancy_net_R"],
            "ending_capital": s_pair["ending_capital"],
            "total_return_pct": s_pair["total_return_pct"],
            "max_drawdown_pct": s_pair["max_drawdown_pct"],
            "oos_trades": s_pair_oos["trades"],
            "oos_wr_pct": s_pair_oos["win_rate_pct"],
            "oos_net_R": s_pair_oos["total_net_R"],
            "oos_pf": s_pair_oos["profit_factor"],
        })

    return rows


# ---------------------------------------------------------------------------
# EXPERIMENT 6 — Time Stability (4 Chronological Blocks)
# ---------------------------------------------------------------------------
def run_experiment_6_time_stability(trades: List[FrozenTrade]) -> Dict[str, Any]:
    periods = [
        ("Period 1: 2024-H2 (Jun-Dec 2024)", datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, tzinfo=timezone.utc)),
        ("Period 2: 2025-H1 (Jan-Jun 2025)", datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2025, 7, 1, tzinfo=timezone.utc)),
        ("Period 3: 2025-H2 (Jul-Dec 2025)", datetime(2025, 7, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ("Period 4: 2026-H1+ (Jan-Aug 2026)", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 8, 27, tzinfo=timezone.utc)),
    ]
    rows = []
    for label, t_start, t_end in periods:
        p_trades = [t for t in trades if t_start <= t.entry_time < t_end]
        _, s = simulate_trades(p_trades, risk_pct=5.0, leverage_cap=50.0)
        rows.append({
            "period": label,
            "trades": s["trades"],
            "win_rate_pct": s["win_rate_pct"],
            "total_strategy_R": s["total_strategy_R"],
            "total_net_R": s["total_net_R"],
            "profit_factor": s["profit_factor"],
            "expectancy_net_R": s["expectancy_net_R"],
            "ending_capital": s["ending_capital"],
            "period_return_pct": s["total_return_pct"],
            "max_drawdown_pct": s["max_drawdown_pct"],
        })

    pf_gt_1_count = sum(1 for r in rows if r["profit_factor"] > 1.0)
    exp_gt_0_count = sum(1 for r in rows if r["expectancy_net_R"] > 0.0)
    weakest = min(rows, key=lambda r: r["profit_factor"])
    strongest = max(rows, key=lambda r: r["profit_factor"])

    return {
        "period_table": rows,
        "pct_periods_pf_gt_1": round(pf_gt_1_count / len(rows) * 100.0, 2),
        "pct_periods_exp_gt_0": round(exp_gt_0_count / len(rows) * 100.0, 2),
        "weakest_period": weakest["period"],
        "weakest_pf": weakest["profit_factor"],
        "strongest_period": strongest["period"],
        "strongest_pf": strongest["profit_factor"],
    }


# ---------------------------------------------------------------------------
# EXPERIMENT 7 — Rolling Performance
# ---------------------------------------------------------------------------
def run_experiment_7_rolling_performance(trades: List[FrozenTrade]) -> Dict[str, Any]:
    recs, _ = simulate_trades(trades, risk_pct=5.0, leverage_cap=50.0)
    df = pd.DataFrame([asdict(r) for r in recs])

    results_table = []
    window_stats = {}

    for w_size in [25, 50, 100]:
        rolling_metrics = []
        for i in range(len(df) - w_size + 1):
            chunk = df.iloc[i : i + w_size]
            w_count = len(chunk[chunk["outcome"] == "FILLED_TP"])
            l_count = len(chunk[chunk["outcome"] == "FILLED_SL"])
            gains = float(chunk[chunk["outcome"] == "FILLED_TP"]["net_pnl"].sum())
            losses = abs(float(chunk[chunk["outcome"] == "FILLED_SL"]["net_pnl"].sum()))
            pf = round(gains / losses, 4) if losses > 0 else 99.0
            wr = round(w_count / w_size * 100.0, 2)
            tot_r = round(float(chunk["net_R_after_fees"].sum()), 4)
            exp_r = round(tot_r / w_size, 6)

            start_dt = str(chunk["entry_time"].iloc[0])[:10]
            end_dt = str(chunk["exit_time"].iloc[-1])[:10]

            rolling_metrics.append({
                "window_size": w_size,
                "start_idx": i,
                "end_idx": i + w_size - 1,
                "start_dt": start_dt,
                "end_dt": end_dt,
                "win_rate_pct": wr,
                "profit_factor": pf,
                "total_net_R": tot_r,
                "expectancy_net_R": exp_r,
            })

        results_table.extend(rolling_metrics)

        # Min & Max windows
        if rolling_metrics:
            worst_w = min(rolling_metrics, key=lambda m: m["profit_factor"])
            best_w = max(rolling_metrics, key=lambda m: m["profit_factor"])
            window_stats[f"rolling_{w_size}"] = {
                "worst_window": worst_w,
                "best_window": best_w,
                "min_pf": worst_w["profit_factor"],
                "max_pf": best_w["profit_factor"],
                "min_wr": min(m["win_rate_pct"] for m in rolling_metrics),
                "max_wr": max(m["win_rate_pct"] for m in rolling_metrics),
            }

    return {"table": results_table, "window_stats": window_stats}


# ---------------------------------------------------------------------------
# EXPERIMENT 8 — Monte Carlo With Execution Degradation (10,000 Paths)
# ---------------------------------------------------------------------------
def run_experiment_8_monte_carlo_degraded(
    trades: List[FrozenTrade], n_sims: int = 10_000, seed: int = 42
) -> List[Dict[str, Any]]:
    rng = np.random.default_rng(seed)
    n_trades = len(trades)

    outcomes = np.array([t.outcome for t in trades])
    directions = np.array([t.direction for t in trades])
    entry_prices = np.array([t.entry_price for t in trades])
    tp_prices = np.array([t.tp_price for t in trades])
    sl_prices = np.array([t.sl_price for t in trades])
    sl_pcts = np.array([t.sl_dist_pct for t in trades])

    scenarios = [
        ("Baseline (0 bps)", 0.0),
        ("+5 bps slippage", 5.0),
        ("+10 bps slippage", 10.0),
        ("+20 bps slippage", 20.0),
    ]

    rows = []

    for label, slip in scenarios:
        s = slip / 10000.0
        # Precompute individual trade returns under slippage
        eff_returns = np.zeros(n_trades)
        for i in range(n_trades):
            if directions[i] == "LONG":
                ep = entry_prices[i] * (1.0 + s)
                if outcomes[i] == "FILLED_TP":
                    xp = tp_prices[i] * (1.0 - s)
                else:
                    xp = sl_prices[i] * (1.0 - s)
                eff_returns[i] = (xp - ep) / ep
            else:
                ep = entry_prices[i] * (1.0 - s)
                if outcomes[i] == "FILLED_TP":
                    xp = tp_prices[i] * (1.0 + s)
                else:
                    xp = sl_prices[i] * (1.0 + s)
                eff_returns[i] = (ep - xp) / ep

        final_caps = np.zeros(n_sims)
        max_dds = np.zeros(n_sims)

        for sim in range(n_sims):
            perm = rng.permutation(n_trades)
            sim_rets = eff_returns[perm]
            sim_sl = sl_pcts[perm]

            cap = STARTING_CAPITAL
            peak = STARTING_CAPITAL
            max_dd = 0.0

            for i in range(n_trades):
                if cap <= 0:
                    break
                sl_f = sim_sl[i] / 100.0
                eff_lev = min(50.0, 0.05 / sl_f) if sl_f > 0 else 50.0
                notional = cap * eff_lev
                fees = notional * BASE_FEE_RATE
                gross = notional * sim_rets[i]
                cap = max(0.0, cap + gross - fees)

                if cap > peak:
                    peak = cap
                dd = (peak - cap) / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd

            final_caps[sim] = cap
            max_dds[sim] = max_dd * 100.0

        rows.append({
            "scenario": label,
            "slippage_bps": slip,
            "median_final_capital": round(float(np.median(final_caps)), 6),
            "p5_final_capital": round(float(np.percentile(final_caps, 5)), 6),
            "p25_final_capital": round(float(np.percentile(final_caps, 25)), 6),
            "p75_final_capital": round(float(np.percentile(final_caps, 75)), 6),
            "p95_final_capital": round(float(np.percentile(final_caps, 95)), 6),
            "median_max_dd_pct": round(float(np.median(max_dds)), 4),
            "p95_max_dd_pct": round(float(np.percentile(max_dds, 95)), 4),
            "prob_dd_gt_25pct": round(float(np.mean(max_dds > 25.0)), 4),
            "prob_dd_gt_40pct": round(float(np.mean(max_dds > 40.0)), 4),
            "prob_dd_gt_50pct": round(float(np.mean(max_dds > 50.0)), 4),
            "prob_capital_below_5": round(float(np.mean(final_caps < 5.0)), 4),
            "prob_capital_below_1": round(float(np.mean(final_caps < 1.0)), 4),
            "prob_capital_above_10": round(float(np.mean(final_caps > 10.0)), 4),
            "prob_capital_above_100": round(float(np.mean(final_caps > 100.0)), 4),
        })

    return rows


# ---------------------------------------------------------------------------
# EXPERIMENT 9 — Risk Sensitivity Under Realistic Execution (Scenario D)
# ---------------------------------------------------------------------------
def run_experiment_9_risk_sensitivity(
    trades: List[FrozenTrade], n_sims: int = 10_000, seed: int = 42
) -> List[Dict[str, Any]]:
    # Scenario D: 0.08% fee + 10 bps slippage, 50x cap
    fee = 0.0008
    slip = 10.0
    s_frac = slip / 10000.0
    risk_tiers = [2.5, 5.0, 7.5, 10.0, 15.0]

    n_trades = len(trades)
    directions = np.array([t.direction for t in trades])
    entry_prices = np.array([t.entry_price for t in trades])
    tp_prices = np.array([t.tp_price for t in trades])
    sl_prices = np.array([t.sl_price for t in trades])
    outcomes = np.array([t.outcome for t in trades])
    sl_pcts = np.array([t.sl_dist_pct for t in trades])

    # Precompute returns
    eff_returns = np.zeros(n_trades)
    for i in range(n_trades):
        if directions[i] == "LONG":
            ep = entry_prices[i] * (1.0 + s_frac)
            xp = tp_prices[i] * (1.0 - s_frac) if outcomes[i] == "FILLED_TP" else sl_prices[i] * (1.0 - s_frac)
            eff_returns[i] = (xp - ep) / ep
        else:
            ep = entry_prices[i] * (1.0 - s_frac)
            xp = tp_prices[i] * (1.0 + s_frac) if outcomes[i] == "FILLED_TP" else sl_prices[i] * (1.0 + s_frac)
            eff_returns[i] = (ep - xp) / ep

    rng = np.random.default_rng(seed)
    rows = []

    for risk in risk_tiers:
        # 1. Chronological simulation
        _, s_chron = simulate_trades(trades, risk_pct=risk, leverage_cap=50.0, fee_rate=fee, slippage_bps=slip)

        # 2. OOS simulation (Jan 2026+)
        oos_trades = [t for t in trades if t.entry_time >= datetime(2026, 1, 1, tzinfo=timezone.utc)]
        _, s_oos = simulate_trades(oos_trades, risk_pct=risk, leverage_cap=50.0, fee_rate=fee, slippage_bps=slip)

        # 3. Monte Carlo
        final_caps = np.zeros(n_sims)
        max_dds = np.zeros(n_sims)

        for sim in range(n_sims):
            perm = rng.permutation(n_trades)
            sim_rets = eff_returns[perm]
            sim_sl = sl_pcts[perm]

            cap = STARTING_CAPITAL
            peak = STARTING_CAPITAL
            max_dd = 0.0

            for i in range(n_trades):
                if cap <= 0:
                    break
                sl_f = sim_sl[i] / 100.0
                eff_lev = min(50.0, (risk / 100.0) / sl_f) if sl_f > 0 else 50.0
                notional = cap * eff_lev
                fees = notional * fee
                gross = notional * sim_rets[i]
                cap = max(0.0, cap + gross - fees)

                if cap > peak:
                    peak = cap
                dd = (peak - cap) / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd

            final_caps[sim] = cap
            max_dds[sim] = max_dd * 100.0

        rows.append({
            "target_risk_pct": risk,
            "ending_capital": s_chron["ending_capital"],
            "total_return_pct": s_chron["total_return_pct"],
            "chron_max_dd_pct": s_chron["max_drawdown_pct"],
            "oos_max_dd_pct": s_oos["max_drawdown_pct"],
            "mc_median_max_dd_pct": round(float(np.median(max_dds)), 4),
            "mc_p95_max_dd_pct": round(float(np.percentile(max_dds, 95)), 4),
            "prob_dd_gt_50pct": round(float(np.mean(max_dds > 50.0)), 4),
            "prob_capital_below_1": round(float(np.mean(final_caps < 1.0)), 4),
            "prob_capital_above_100": round(float(np.mean(final_caps > 100.0)), 4),
        })

    return rows


# ---------------------------------------------------------------------------
# EXPERIMENT 10 — Leverage Cap Robustness (At 5% Risk)
# ---------------------------------------------------------------------------
def run_experiment_10_leverage_caps(trades: List[FrozenTrade]) -> List[Dict[str, Any]]:
    caps = [10.0, 15.0, 20.0, 25.0, 35.0, 50.0, 75.0, 100.0]
    rows = []
    for cap in caps:
        _, s = simulate_trades(trades, risk_pct=5.0, leverage_cap=cap, fee_rate=BASE_FEE_RATE, slippage_bps=0.0)
        rows.append({
            "leverage_cap": cap,
            "ending_capital": s["ending_capital"],
            "total_return_pct": s["total_return_pct"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "profit_factor": s["profit_factor"],
            "total_net_R": s["total_net_R"],
            "expectancy_net_R": s["expectancy_net_R"],
            "capped_trades_count": s["capped_trades_count"],
            "capped_trades_pct": round(s["capped_trades_count"] / s["trades"] * 100.0, 2),
            "avg_leverage": s["avg_leverage"],
            "median_leverage": s["median_leverage"],
        })
    return rows


# ---------------------------------------------------------------------------
# EXPERIMENT 11 — Trade Sequence Dependency
# ---------------------------------------------------------------------------
def _fast_sim_path(
    returns_arr: np.ndarray, sl_fracs_arr: np.ndarray, risk_pct: float = 5.0, leverage_cap: float = 50.0, fee_rate: float = 0.0008
) -> Tuple[float, float]:
    cap = STARTING_CAPITAL
    peak = STARTING_CAPITAL
    max_dd = 0.0
    n = len(returns_arr)
    for i in range(n):
        if cap <= 0.0:
            break
        sl_f = sl_fracs_arr[i]
        eff_lev = min(leverage_cap, (risk_pct / 100.0) / sl_f) if sl_f > 0 else leverage_cap
        notional = cap * eff_lev
        fees = notional * fee_rate
        gross = notional * returns_arr[i]
        cap = max(0.0, cap + gross - fees)
        if cap > peak:
            peak = cap
        dd = (peak - cap) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return cap, max_dd * 100.0


def run_experiment_11_sequence_dependency(
    trades: List[FrozenTrade], n_sims: int = 10_000, seed: int = 42
) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    n_trades = len(trades)

    # Precompute per-trade returns under baseline (0.08% fee, 0 bps slippage)
    eff_returns = np.zeros(n_trades)
    sl_fracs = np.array([t.sl_dist_pct / 100.0 for t in trades])

    for i, t in enumerate(trades):
        if t.direction == "LONG":
            xp = t.tp_price if t.outcome == "FILLED_TP" else t.sl_price
            eff_returns[i] = (xp - t.entry_price) / t.entry_price
        else:
            xp = t.tp_price if t.outcome == "FILLED_TP" else t.sl_price
            eff_returns[i] = (t.entry_price - xp) / t.entry_price

    # A: Chronological
    cap_chron, dd_chron = _fast_sim_path(eff_returns, sl_fracs, risk_pct=5.0, leverage_cap=50.0)

    # B: Random Permutation (10,000 paths)
    b_caps = np.zeros(n_sims)
    b_dds = np.zeros(n_sims)
    for s in range(n_sims):
        perm = rng.permutation(n_trades)
        b_caps[s], b_dds[s] = _fast_sim_path(eff_returns[perm], sl_fracs[perm], risk_pct=5.0, leverage_cap=50.0)

    # C: Asset-block Shuffle
    # Pre-group trade indices by asset
    assets = list(set(t.asset for t in trades))
    asset_indices = {a: np.array([i for i, t in enumerate(trades) if t.asset == a]) for a in assets}
    c_caps = np.zeros(n_sims)
    c_dds = np.zeros(n_sims)
    for s in range(n_sims):
        shuffled_a = rng.permutation(assets)
        perm = np.concatenate([asset_indices[a] for a in shuffled_a])
        c_caps[s], c_dds[s] = _fast_sim_path(eff_returns[perm], sl_fracs[perm], risk_pct=5.0, leverage_cap=50.0)

    # D: Monthly-block Shuffle
    df_temp = pd.DataFrame([{"idx": i, "m": t.entry_time.strftime("%Y-%m")} for i, t in enumerate(trades)])
    months = list(df_temp["m"].unique())
    month_indices = {m: df_temp[df_temp["m"] == m]["idx"].to_numpy() for m in months}
    d_caps = np.zeros(n_sims)
    d_dds = np.zeros(n_sims)
    for s in range(n_sims):
        shuffled_m = rng.permutation(months)
        perm = np.concatenate([month_indices[m] for m in shuffled_m])
        d_caps[s], d_dds[s] = _fast_sim_path(eff_returns[perm], sl_fracs[perm], risk_pct=5.0, leverage_cap=50.0)


    rows = [
        {
            "sequence_model": "A: Original Chronological",
            "ending_capital_median": round(cap_chron, 4),
            "ending_capital_p5": round(cap_chron, 4),
            "ending_capital_p95": round(cap_chron, 4),
            "max_dd_median_pct": round(dd_chron, 4),
            "max_dd_p95_pct": round(dd_chron, 4),
            "prob_ruin_lt_1": 0.0,
        },
        {
            "sequence_model": "B: Random Permutation (10k)",
            "ending_capital_median": round(float(np.median(b_caps)), 4),
            "ending_capital_p5": round(float(np.percentile(b_caps, 5)), 4),
            "ending_capital_p95": round(float(np.percentile(b_caps, 95)), 4),
            "max_dd_median_pct": round(float(np.median(b_dds)), 4),
            "max_dd_p95_pct": round(float(np.percentile(b_dds, 95)), 4),
            "prob_ruin_lt_1": round(float(np.mean(np.array(b_caps) < 1.0)), 4),
        },
        {
            "sequence_model": "C: Asset-Block Shuffle (10k)",
            "ending_capital_median": round(float(np.median(c_caps)), 4),
            "ending_capital_p5": round(float(np.percentile(c_caps, 5)), 4),
            "ending_capital_p95": round(float(np.percentile(c_caps, 95)), 4),
            "max_dd_median_pct": round(float(np.median(c_dds)), 4),
            "max_dd_p95_pct": round(float(np.percentile(c_dds, 95)), 4),
            "prob_ruin_lt_1": round(float(np.mean(np.array(c_caps) < 1.0)), 4),
        },
        {
            "sequence_model": "D: Monthly-Block Shuffle (10k)",
            "ending_capital_median": round(float(np.median(d_caps)), 4),
            "ending_capital_p5": round(float(np.percentile(d_caps, 5)), 4),
            "ending_capital_p95": round(float(np.percentile(d_caps, 95)), 4),
            "max_dd_median_pct": round(float(np.median(d_dds)), 4),
            "max_dd_p95_pct": round(float(np.percentile(d_dds, 95)), 4),
            "prob_ruin_lt_1": round(float(np.mean(np.array(d_caps) < 1.0)), 4),
        },
    ]

    return {"table": rows}


# ---------------------------------------------------------------------------
# EXPERIMENT 12 — Drawdown Recovery Analysis (5% Risk / 50x Cap)
# ---------------------------------------------------------------------------
def run_experiment_12_recovery_analysis(trades: List[FrozenTrade]) -> Dict[str, Any]:
    recs, _ = simulate_trades(trades, risk_pct=5.0, leverage_cap=50.0)

    episodes = []
    peak_cap = STARTING_CAPITAL
    peak_idx = 0
    peak_time = trades[0].entry_time

    in_dd = False
    trough_cap = STARTING_CAPITAL
    trough_time = peak_time
    trough_idx = 0

    for i, r in enumerate(recs):
        cap = r.ending_capital
        dt = trades[i].exit_time

        if cap > peak_cap:
            if in_dd:
                # Recovered to new high!
                trades_to_rec = i - peak_idx
                duration = dt - peak_time
                hours_to_rec = duration.total_seconds() / 3600.0
                days_to_rec = hours_to_rec / 24.0
                months_to_rec = days_to_rec / 30.4375

                dd_pct = (peak_cap - trough_cap) / peak_cap * 100.0
                dd_dollar = peak_cap - trough_cap

                episodes.append({
                    "episode_id": len(episodes) + 1,
                    "peak_time": peak_time.isoformat(),
                    "trough_time": trough_time.isoformat(),
                    "recovery_time": dt.isoformat(),
                    "drawdown_pct": round(dd_pct, 4),
                    "drawdown_dollar": round(dd_dollar, 4),
                    "trades_to_recovery": trades_to_rec,
                    "hours_to_recovery": round(hours_to_rec, 2),
                    "days_to_recovery": round(days_to_rec, 2),
                    "months_to_recovery": round(months_to_rec, 2),
                    "recovered": True,
                })
                in_dd = False

            peak_cap = cap
            peak_idx = i
            peak_time = dt
            trough_cap = cap
            trough_time = dt
            trough_idx = i
        else:
            in_dd = True
            if cap < trough_cap:
                trough_cap = cap
                trough_time = dt
                trough_idx = i

    # If ending while in a drawdown
    unrecovered_count = 0
    if in_dd:
        unrecovered_count = 1
        trades_to_rec = len(recs) - peak_idx
        duration = trades[-1].exit_time - peak_time
        hours_to_rec = duration.total_seconds() / 3600.0
        days_to_rec = hours_to_rec / 24.0
        months_to_rec = days_to_rec / 30.4375

        dd_pct = (peak_cap - trough_cap) / peak_cap * 100.0
        dd_dollar = peak_cap - trough_cap

        episodes.append({
            "episode_id": len(episodes) + 1,
            "peak_time": peak_time.isoformat(),
            "trough_time": trough_time.isoformat(),
            "recovery_time": "UNRECOVERED",
            "drawdown_pct": round(dd_pct, 4),
            "drawdown_dollar": round(dd_dollar, 4),
            "trades_to_recovery": trades_to_rec,
            "hours_to_recovery": round(hours_to_rec, 2),
            "days_to_recovery": round(days_to_rec, 2),
            "months_to_recovery": round(months_to_rec, 2),
            "recovered": False,
        })

    # Summary
    recovered_eps = [e for e in episodes if e["recovered"]]
    worst_dd = max((e["drawdown_pct"] for e in episodes), default=0.0)
    longest_rec_trades = max((e["trades_to_recovery"] for e in recovered_eps), default=0)
    longest_rec_days = max((e["days_to_recovery"] for e in recovered_eps), default=0.0)
    med_rec_trades = float(np.median([e["trades_to_recovery"] for e in recovered_eps])) if recovered_eps else 0.0
    med_rec_days = float(np.median([e["days_to_recovery"] for e in recovered_eps])) if recovered_eps else 0.0

    return {
        "episodes": episodes,
        "total_drawdown_episodes": len(episodes),
        "worst_drawdown_pct": worst_dd,
        "longest_recovery_trades": longest_rec_trades,
        "longest_recovery_days": longest_rec_days,
        "median_recovery_trades": med_rec_trades,
        "median_recovery_days": med_rec_days,
        "unrecovered_count": unrecovered_count,
    }


# ---------------------------------------------------------------------------
# EXPERIMENT 13 — Statistical Bootstrap Confidence (10,000 Samples)
# ---------------------------------------------------------------------------
def run_experiment_13_bootstrap_confidence(
    trades: List[FrozenTrade], n_boot: int = 10_000, seed: int = 42
) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = len(trades)

    outcomes = np.array([1 if t.outcome == "FILLED_TP" else 0 for t in trades])
    strat_rs = np.array([t.realized_r for t in trades])

    boot_wr = np.zeros(n_boot)
    boot_exp = np.zeros(n_boot)
    boot_pf = np.zeros(n_boot)
    boot_tot_r = np.zeros(n_boot)

    for b in range(n_boot):
        idx = rng.choice(n, size=n, replace=True)
        b_outcomes = outcomes[idx]
        b_rs = strat_rs[idx]

        w_count = np.sum(b_outcomes)
        boot_wr[b] = (w_count / n) * 100.0

        tot_r = np.sum(b_rs)
        boot_tot_r[b] = tot_r
        boot_exp[b] = tot_r / n

        pos_r = np.sum(b_rs[b_rs > 0])
        neg_r = np.abs(np.sum(b_rs[b_rs < 0]))
        boot_pf[b] = (pos_r / neg_r) if neg_r > 0 else 99.0

    ci_table = [
        {
            "metric": "Win Rate (%)",
            "p2_5": round(float(np.percentile(boot_wr, 2.5)), 4),
            "median": round(float(np.median(boot_wr)), 4),
            "p97_5": round(float(np.percentile(boot_wr, 97.5)), 4),
        },
        {
            "metric": "Expectancy R/trade",
            "p2_5": round(float(np.percentile(boot_exp, 2.5)), 6),
            "median": round(float(np.median(boot_exp)), 6),
            "p97_5": round(float(np.percentile(boot_exp, 97.5)), 6),
        },
        {
            "metric": "Profit Factor",
            "p2_5": round(float(np.percentile(boot_pf, 2.5)), 4),
            "median": round(float(np.median(boot_pf)), 4),
            "p97_5": round(float(np.percentile(boot_pf, 97.5)), 4),
        },
        {
            "metric": "Total Strategy R",
            "p2_5": round(float(np.percentile(boot_tot_r, 2.5)), 4),
            "median": round(float(np.median(boot_tot_r)), 4),
            "p97_5": round(float(np.percentile(boot_tot_r, 97.5)), 4),
        },
    ]

    prob_exp_le_0 = round(float(np.mean(boot_exp <= 0.0)), 6)
    prob_pf_le_1 = round(float(np.mean(boot_pf <= 1.0)), 6)

    return {
        "ci_table": ci_table,
        "prob_expectancy_le_0": prob_exp_le_0,
        "prob_pf_le_1": prob_pf_le_1,
        "n_bootstraps": n_boot,
    }


# ---------------------------------------------------------------------------
# Master Orchestrator (All 13 Experiments)
# ---------------------------------------------------------------------------
def run_all_robustness_experiments(
    data_base_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Executes the entire Phase 7 suite and outputs all 13 CSVs and master JSON.
    """
    assert not live_execution_authorized

    out = output_dir or Path("docs/ai")
    out.mkdir(parents=True, exist_ok=True)

    print("Step 1: Loading & validating 445 frozen canonical trades...")
    trades = load_frozen_canonical_trades(data_base_dir)
    print(f"  Passed all invariant checks (count={len(trades)}, W=304, L=141, R=+122.06R)")

    print("Experiment 1: Fee Sensitivity...")
    e1 = run_experiment_1_fee_sensitivity(trades)
    pd.DataFrame(e1).to_csv(out / "robustness_fee_sensitivity.csv", index=False)

    print("Experiment 2: Slippage Sensitivity...")
    e2 = run_experiment_2_slippage_sensitivity(trades)
    pd.DataFrame(e2["table"]).to_csv(out / "robustness_slippage_sensitivity.csv", index=False)

    print("Experiment 3: Combined Execution Scenarios...")
    e3 = run_experiment_3_combined_scenarios(trades)
    pd.DataFrame(e3).to_csv(out / "robustness_execution_scenarios.csv", index=False)

    print("Experiment 4: Best/Worst Trade Concentration...")
    e4 = run_experiment_4_trade_concentration(trades)
    pd.DataFrame(e4["removal_table"]).to_csv(out / "robustness_trade_concentration.csv", index=False)

    print("Experiment 5: Asset Exclusion...")
    e5 = run_experiment_5_asset_exclusion(trades)
    pd.DataFrame(e5).to_csv(out / "robustness_asset_exclusion.csv", index=False)

    print("Experiment 6: Time Stability...")
    e6 = run_experiment_6_time_stability(trades)
    pd.DataFrame(e6["period_table"]).to_csv(out / "robustness_time_stability.csv", index=False)

    print("Experiment 7: Rolling Performance...")
    e7 = run_experiment_7_rolling_performance(trades)
    pd.DataFrame(e7["table"]).to_csv(out / "robustness_rolling_performance.csv", index=False)

    print("Experiment 8: Monte Carlo With Degradation...")
    e8 = run_experiment_8_monte_carlo_degraded(trades, n_sims=10_000)
    pd.DataFrame(e8).to_csv(out / "robustness_monte_carlo.csv", index=False)

    print("Experiment 9: Risk Sensitivity under Realistic Execution...")
    e9 = run_experiment_9_risk_sensitivity(trades, n_sims=10_000)
    pd.DataFrame(e9).to_csv(out / "robustness_risk_sensitivity.csv", index=False)

    print("Experiment 10: Leverage Cap Robustness...")
    e10 = run_experiment_10_leverage_caps(trades)
    pd.DataFrame(e10).to_csv(out / "robustness_leverage_caps.csv", index=False)

    print("Experiment 11: Trade Sequence Dependency...")
    e11 = run_experiment_11_sequence_dependency(trades, n_sims=10_000)
    pd.DataFrame(e11["table"]).to_csv(out / "robustness_sequence_dependency.csv", index=False)

    print("Experiment 12: Recovery Duration Analysis...")
    e12 = run_experiment_12_recovery_analysis(trades)
    pd.DataFrame(e12["episodes"]).to_csv(out / "robustness_recovery.csv", index=False)

    print("Experiment 13: Statistical Bootstrap...")
    e13 = run_experiment_13_bootstrap_confidence(trades, n_boot=10_000)
    pd.DataFrame(e13["ci_table"]).to_csv(out / "robustness_bootstrap.csv", index=False)

    # Master JSON
    master = {
        "canonical_trade_count": len(trades),
        "experiment_1_fee_sensitivity": e1,
        "experiment_2_slippage_sensitivity": e2,
        "experiment_3_combined_scenarios": e3,
        "experiment_4_trade_concentration": e4,
        "experiment_5_asset_exclusion": e5,
        "experiment_6_time_stability": e6,
        "experiment_7_rolling_performance": e7["window_stats"],
        "experiment_8_monte_carlo_degraded": e8,
        "experiment_9_risk_sensitivity_realistic": e9,
        "experiment_10_leverage_caps": e10,
        "experiment_11_sequence_dependency": e11,
        "experiment_12_recovery_analysis": {
            "total_episodes": e12["total_drawdown_episodes"],
            "worst_drawdown_pct": e12["worst_drawdown_pct"],
            "longest_recovery_trades": e12["longest_recovery_trades"],
            "longest_recovery_days": e12["longest_recovery_days"],
            "median_recovery_trades": e12["median_recovery_trades"],
            "median_recovery_days": e12["median_recovery_days"],
            "unrecovered_count": e12["unrecovered_count"],
        },
        "experiment_13_bootstrap_confidence": e13,
    }

    with open(out / "strategy_robustness_results.json", "w") as f:
        json.dump(master, f, indent=2, default=str)

    print("All Phase 7 deliverables successfully generated.")
    return master
