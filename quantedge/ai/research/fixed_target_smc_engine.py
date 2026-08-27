"""
Fixed-Target Dynamic-Leverage SMC Engine.
=========================================
Research engine implementing the validated 0.60% Fixed-Target SMC Strategy:
- Canonical Deterministic Order Block (OB) Detection
- 25% Zone Depth Limit Entry
- Distal OB Boundary Stop Loss
- Dynamic Leverage targeting max 35% SL risk, strictly capped at 100x max
- Fixed +0.60% Take Profit price expansion
- Transparent fee deduction (0.08% roundtrip) and compounding ledger tracking
- IST (UTC+5:30) and UTC timestamp support
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple, Any
import pandas as pd

from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, extract_phase_i_setups, SMCReplayContext, PhaseISetup
from quantedge.ai.evaluation.phase_l_research import CanonicalCandle

IST_TZ = timezone(timedelta(hours=5, minutes=30))

@dataclass
class FixedSMCConfig:
    fixed_tp_pct: float = 0.60       # Fixed +0.60% price move target
    max_sl_risk_pct: float = 35.0     # Target capital risk on stop loss (35%)
    max_leverage: float = 100.0       # Strict leverage cap (100x)
    penetration_depth: float = 0.25   # 25% penetration depth into OB zone
    fee_rate: float = 0.0008          # 0.08% roundtrip taker fee (0.04% entry + 0.04% exit)
    max_holding_bars: int = 72        # 72 hours max holding horizon
    starting_capital: float = 10.0    # Initial account capital

@dataclass
class FixedSMCTrade:
    trade_number: int
    asset: str
    direction: str
    ob_id: str
    ob_low: float
    ob_high: float
    ob_width: float
    setup_time_utc: datetime
    setup_time_ist: str
    entry_time_utc: Optional[datetime]
    entry_time_ist: str
    exit_time_utc: Optional[datetime]
    exit_time_ist: str
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    stop_loss_distance_pct: float
    leverage: float
    actual_sl_risk_pct: float
    tp_target_return_pct: float
    outcome: str                      # FILLED_TP, FILLED_SL, FILLED_TIMEOUT
    realized_r: float
    cumulative_realized_r: float
    starting_account_balance: float
    position_notional: float
    exchange_fees_usd: float
    gross_pnl_usd: float
    net_pnl_usd: float
    ending_account_balance: float
    loss_mechanism: str
    trade_narrative: str

def to_ist_string(dt: Optional[datetime]) -> str:
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist_dt = dt.astimezone(IST_TZ)
    return ist_dt.strftime("%Y-%m-%d %H:%M IST")

def run_fixed_target_smc_backtest(
    candles: List[CanonicalCandle],
    symbol: str,
    config: Optional[FixedSMCConfig] = None,
    start_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Executes a deterministic backtest for a single asset using the Fixed-Target SMC setup.
    """
    cfg = config or FixedSMCConfig()
    
    # 1. Build canonical SMC context and extract qualified setups
    ctx = build_smc_context(candles)
    setups, _ = extract_phase_i_setups(candles, symbol=symbol, ctx=ctx)
    
    # Prepare candle dataframe
    c_records = []
    for c in candles:
        c_records.append({
            "timestamp": c.timestamp,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
        })
    c_df = pd.DataFrame(c_records).sort_values("timestamp").reset_index(drop=True)
    
    # Filter setups chronologically
    qual_setups = []
    for s in setups:
        dec_ts = candles[s.decision_bar].timestamp
        if start_time is not None and dec_ts < start_time:
            continue
        qual_setups.append({
            "ob_id": s.setup_id,
            "asset": symbol,
            "direction": s.direction,
            "decision_dt": dec_ts,
            "entry_price": float(s.entry_price),
            "sl_price": float(s.sl_price),
            "tp_price": float(s.tp_price),
            "ob_high": float(s.ob_high),
            "ob_low": float(s.ob_low),
            "decision_bar": s.decision_bar,
        })
        
    qual_setups = sorted(qual_setups, key=lambda x: (x["decision_dt"], x["ob_id"]))
    
    capital_net = cfg.starting_capital
    active_lock_until = None
    executed_trades: List[FixedSMCTrade] = []
    cum_r = 0.0
    
    for s in qual_setups:
        dec_dt = s["decision_dt"]
        dir_ = s["direction"]
        top = s["ob_high"]
        bot = s["ob_low"]
        w = top - bot
        
        # 25% zone depth penetration limit order
        if dir_ == "LONG":
            entry_p = top - cfg.penetration_depth * w
            sl_p = bot
            risk_dist = entry_p - sl_p
            tp_p = entry_p * (1.0 + cfg.fixed_tp_pct / 100.0)
            reward_dist = tp_p - entry_p
        else:
            entry_p = bot + cfg.penetration_depth * w
            sl_p = top
            risk_dist = sl_p - entry_p
            tp_p = entry_p * (1.0 - cfg.fixed_tp_pct / 100.0)
            reward_dist = entry_p - tp_p
            
        if risk_dist <= 1e-6:
            continue
            
        sl_dist_dec = risk_dist / entry_p
        sl_dist_pct = sl_dist_dec * 100.0
        
        # Dynamic leverage capped at 100x max
        uncapped_lev = cfg.max_sl_risk_pct / sl_dist_pct
        leverage = min(cfg.max_leverage, uncapped_lev)
        actual_sl_risk_pct = leverage * sl_dist_pct
        actual_sl_ret_pct = -1.0 * actual_sl_risk_pct
        tp_ret_pct = cfg.fixed_tp_pct * leverage
        
        # Check asset lock
        if active_lock_until is not None and dec_dt < active_lock_until:
            continue
            
        future_candles = c_df[c_df["timestamp"] >= dec_dt].reset_index(drop=True)
        
        filled = False
        fill_bar = None
        fill_dt = None
        outcome = "NO_FILL"
        exit_dt = None
        exit_p = None
        loss_mech = "N/A"
        narrative = ""
        
        max_b = min(cfg.max_holding_bars, len(future_candles))
        for b in range(max_b):
            c = future_candles.iloc[b]
            c_dt = c["timestamp"]
            c_o, c_h, c_l, c_c = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            
            if not filled:
                if dir_ == "LONG" and c_l <= entry_p:
                    filled = True
                    fill_bar = b
                    fill_dt = c_dt
                elif dir_ == "SHORT" and c_h >= entry_p:
                    filled = True
                    fill_bar = b
                    fill_dt = c_dt
                    
                if not filled:
                    if dir_ == "LONG" and c_l <= sl_p:
                        narrative = "Invalidated (breached distal SL before entry fill)."
                        break
                    elif dir_ == "SHORT" and c_h >= sl_p:
                        narrative = "Invalidated (breached distal SL before entry fill)."
                        break
                    continue
                    
            if filled:
                hit_tp = (c_h >= tp_p) if dir_ == "LONG" else (c_l <= tp_p)
                hit_sl = (c_l <= sl_p) if dir_ == "LONG" else (c_h >= sl_p)
                
                if hit_tp and hit_sl:
                    outcome = "FILLED_SL"
                    exit_dt = c_dt
                    exit_p = sl_p
                    loss_mech = "DUAL_TOUCH_AMBIGUITY"
                    narrative = "Dual-touch ambiguous candle. SL conservative tie-break."
                    break
                elif hit_tp and not hit_sl:
                    outcome = "FILLED_TP"
                    exit_dt = c_dt
                    exit_p = tp_p
                    narrative = f"Price reached +{cfg.fixed_tp_pct:.2f}% TP target at {tp_p:.4f}."
                    break
                elif hit_sl and not hit_tp:
                    outcome = "FILLED_SL"
                    exit_dt = c_dt
                    exit_p = sl_p
                    if b == fill_bar:
                        loss_mech = "INSTANT_BLOWTHROUGH"
                        narrative = f"Instant penetration blowthrough. Breached distal SL at {sl_p:.4f}."
                    else:
                        loss_mech = "CONSOLIDATION_REVERSAL"
                        narrative = f"Consolidated for {b - fill_bar} bars, then reversed to breach distal SL at {sl_p:.4f}."
                    break
                    
        if filled and outcome not in ["FILLED_TP", "FILLED_SL"]:
            outcome = "FILLED_TIMEOUT"
            last_c = future_candles.iloc[max_b - 1]
            exit_dt = last_c["timestamp"]
            exit_p = float(last_c["close"])
            narrative = f"{cfg.max_holding_bars}-hour horizon expired. Closed at {exit_p:.4f}."
            
        if filled:
            if outcome == "FILLED_TP":
                ret_pct = tp_ret_pct
                realized_r = reward_dist / risk_dist
            elif outcome == "FILLED_SL":
                ret_pct = actual_sl_ret_pct
                realized_r = -1.0
            else:
                p_diff = (exit_p - entry_p) if dir_ == "LONG" else (entry_p - exit_p)
                realized_r = p_diff / risk_dist
                ret_pct = realized_r * actual_sl_risk_pct
                
            active_lock_until = exit_dt
            cum_r += realized_r
            
            # Net compounding with fee deduction
            start_bal = capital_net
            notional = start_bal * leverage
            fees_usd = notional * cfg.fee_rate
            gross_pnl_usd = start_bal * (ret_pct / 100.0)
            net_pnl_usd = gross_pnl_usd - fees_usd
            capital_net = max(0.0, start_bal + net_pnl_usd)
            
            trade = FixedSMCTrade(
                trade_number=len(executed_trades) + 1,
                asset=symbol,
                direction=dir_,
                ob_id=s["ob_id"],
                ob_low=bot,
                ob_high=top,
                ob_width=w,
                setup_time_utc=dec_dt,
                setup_time_ist=to_ist_string(dec_dt),
                entry_time_utc=fill_dt,
                entry_time_ist=to_ist_string(fill_dt),
                exit_time_utc=exit_dt,
                exit_time_ist=to_ist_string(exit_dt),
                entry_price=entry_p,
                stop_loss_price=sl_p,
                take_profit_price=tp_p,
                stop_loss_distance_pct=sl_dist_pct,
                leverage=leverage,
                actual_sl_risk_pct=actual_sl_risk_pct,
                tp_target_return_pct=tp_ret_pct,
                outcome=outcome,
                realized_r=realized_r,
                cumulative_realized_r=cum_r,
                starting_account_balance=start_bal,
                position_notional=notional,
                exchange_fees_usd=fees_usd,
                gross_pnl_usd=gross_pnl_usd,
                net_pnl_usd=net_pnl_usd,
                ending_account_balance=capital_net,
                loss_mechanism=loss_mech,
                trade_narrative=narrative,
            )
            executed_trades.append(trade)
            
    wins = [t for t in executed_trades if t.outcome == "FILLED_TP"]
    losses = [t for t in executed_trades if t.outcome == "FILLED_SL"]
    total_exec = len(executed_trades)
    wr = (len(wins) / total_exec * 100.0) if total_exec > 0 else 0.0
    exp_r = cum_r / total_exec if total_exec > 0 else 0.0
    gain_r = sum(t.realized_r for t in wins)
    loss_r = abs(sum(t.realized_r for t in losses))
    pf = (gain_r / loss_r) if loss_r > 0 else 99.0
    
    return {
        "symbol": symbol,
        "config": cfg,
        "starting_capital": cfg.starting_capital,
        "ending_capital_net": capital_net,
        "total_trades": total_exec,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": wr,
        "total_realized_r": cum_r,
        "expectancy_r": exp_r,
        "profit_factor": pf,
        "trades": executed_trades,
    }
