"""
Backtesting Engine - Deterministic historical replay.

Implements the strategy pipeline for historical validation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional
import logging

from quantedge.market_data.models import Candle, Timeframe
from quantedge.strategy.engine import StrategyEngine, StrategyEngineConfig
from quantedge.strategy.models import TradeSetup, StrategySignal, AccountState
from quantedge.smc.analyzer import SMCAnalyzerConfig

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Backtest configuration."""
    start_date: datetime
    end_date: datetime
    initial_balance: Decimal = Decimal("10000")
    commission_pct: Decimal = Decimal("0.02")  # 0.02%
    slippage_pct: Decimal = Decimal("0.01")    # 0.01%
    timeframe: Timeframe = Timeframe.H1
    symbols: list[str] = field(default_factory=lambda: ["BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P"])


@dataclass
class BacktestTrade:
    """Record of a single backtest trade."""
    symbol: str
    entry_time: datetime
    exit_time: Optional[datetime]
    direction: str
    entry_price: Decimal
    exit_price: Optional[Decimal]
    stop_loss: Decimal
    take_profit: Decimal
    position_size: Decimal
    leverage: int
    pnl: Decimal
    pnl_pct: Decimal
    r_multiple: Decimal
    exit_reason: str  # TP, SL, TIME, MANUAL
    confidence_score: int


@dataclass
class BacktestResult:
    """Complete backtest results."""
    config: BacktestConfig
    start_date: datetime
    end_date: datetime
    initial_balance: Decimal
    final_balance: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    total_pnl: Decimal
    total_pnl_pct: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Optional[Decimal]
    profit_factor: Optional[Decimal]
    avg_r_multiple: Decimal
    trades: list[BacktestTrade]
    equity_curve: list[tuple[datetime, Decimal]]


class BacktestEngine:
    """
    Deterministic backtesting engine.

    Key principles:
    - No look-ahead bias: decisions at T use only data up to T
    - Realistic execution: commission, slippage
    - Proper position sizing per risk model
    - One active trade rule enforced
    """

    def __init__(self, config: BacktestConfig, strategy_engine: StrategyEngine):
        self.config = config
        self.strategy_engine = strategy_engine

    def run(self, candles_by_symbol: dict[str, list[Candle]]) -> BacktestResult:
        """
        Run backtest on historical data.

        Processes candles chronologically across all symbols.
        At each timestamp, evaluates strategy and manages positions.
        """
        # Align all candles by timestamp
        all_timestamps = set()
        for candles in candles_by_symbol.values():
            for c in candles:
                all_timestamps.add(c.timestamp)

        sorted_timestamps = sorted(all_timestamps)

        # Filter to date range
        sorted_timestamps = [
            ts for ts in sorted_timestamps
            if self.config.start_date <= ts <= self.config.end_date
        ]

        # Initialize state
        balance = self.config.initial_balance
        equity = balance
        open_trade: Optional[BacktestTrade] = None
        trades: list[BacktestTrade] = []
        equity_curve = [(self.config.start_date, balance)]

        # Build candle windows for each symbol at each timestamp
        candle_windows = self._build_candle_windows(candles_by_symbol, sorted_timestamps)

        for current_time in sorted_timestamps:
            # Update equity with mark-to-market
            if open_trade:
                current_price = self._get_current_price(candles_by_symbol[open_trade.symbol], current_time)
                if current_price:
                    unrealized_pnl = self._calculate_unrealized_pnl(open_trade, current_price)
                    equity = balance + unrealized_pnl
                    equity_curve.append((current_time, equity))

                    # Check exit conditions
                    exit_result = self._check_exit_conditions(open_trade, current_price, current_time)
                    if exit_result:
                        exit_price, exit_reason = exit_result
                        closed_trade = self._close_trade(open_trade, exit_price, current_time, exit_reason)
                        trades.append(closed_trade)
                        balance += closed_trade.pnl
                        equity = balance
                        open_trade = None
                        equity_curve.append((current_time, equity))

            # Scan for new entries (only if no open trade)
            if not open_trade:
                account_state = AccountState(
                    balance=balance,
                    equity=equity,
                    free_margin=balance,
                    used_margin=Decimal("0"),
                    open_positions=0,
                )

                # Get candles up to current time for each symbol
                current_candles = {}
                for symbol in self.config.symbols:
                    symbol_candles = [
                        c for c in candles_by_symbol.get(symbol, [])
                        if c.timestamp <= current_time
                    ]
                    if len(symbol_candles) >= 300:  # Minimum for SMC analysis
                        current_candles[symbol] = symbol_candles

                if current_candles:
                    candidates = self.strategy_engine.scan_all_symbols(current_candles, account_state)
                    best = self.strategy_engine.select_best_candidate(candidates, has_active_trade=False)

                    if best:
                        # Enter trade
                        open_trade = self._open_trade(best, current_time)
                        logger.info(f"Backtest entry: {best.symbol} {best.direction.value} @ {best.entry_price}")

        # Close any remaining open trade at end
        if open_trade:
            final_price = self._get_final_price(candles_by_symbol[open_trade.symbol])
            if final_price:
                closed_trade = self._close_trade(open_trade, final_price, sorted_timestamps[-1], "END_OF_DATA")
                trades.append(closed_trade)
                balance += closed_trade.pnl

        return self._compile_results(trades, equity_curve, balance)

    def _build_candle_windows(self, candles_by_symbol: dict[str, list[Candle]], timestamps: list[datetime]) -> dict:
        """Pre-build candle windows for efficient lookback."""
        # For now, return empty - will use slicing in scan
        return {}

    def _get_current_price(self, candles: list[Candle], timestamp: datetime) -> Optional[Decimal]:
        """Get candle close price at or before timestamp."""
        for c in reversed(candles):
            if c.timestamp <= timestamp:
                return c.close
        return None

    def _get_final_price(self, candles: list[Candle]) -> Optional[Decimal]:
        """Get last available price."""
        if candles:
            return candles[-1].close
        return None

    def _calculate_unrealized_pnl(self, trade: BacktestTrade, current_price: Decimal) -> Decimal:
        """Calculate unrealized P&L for open trade."""
        if trade.direction == "LONG":
            price_diff = current_price - trade.entry_price
        else:
            price_diff = trade.entry_price - current_price

        return trade.position_size * price_diff

    def _check_exit_conditions(
        self,
        trade: BacktestTrade,
        current_price: Decimal,
        current_time: datetime,
    ) -> Optional[tuple[Decimal, str]]:
        """Check if trade should be exited."""
        # Check SL
        if trade.direction == "LONG":
            if current_price <= trade.stop_loss:
                return trade.stop_loss, "SL"
            if current_price >= trade.take_profit:
                return trade.take_profit, "TP"
        else:
            if current_price >= trade.stop_loss:
                return trade.stop_loss, "SL"
            if current_price <= trade.take_profit:
                return trade.take_profit, "TP"

        return None

    def _open_trade(self, setup: TradeSetup, entry_time: datetime) -> BacktestTrade:
        """Create a new backtest trade from setup."""
        return BacktestTrade(
            symbol=setup.symbol,
            entry_time=entry_time,
            exit_time=None,
            direction=setup.direction.value,
            entry_price=setup.entry_price,
            exit_price=None,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            position_size=setup.position_size,
            leverage=setup.leverage,
            pnl=Decimal("0"),
            pnl_pct=Decimal("0"),
            r_multiple=Decimal("0"),
            exit_reason="",
            confidence_score=setup.confidence.total,
        )

    def _close_trade(
        self,
        trade: BacktestTrade,
        exit_price: Decimal,
        exit_time: datetime,
        exit_reason: str,
    ) -> BacktestTrade:
        """Close a trade and calculate P&L."""
        if trade.direction == "LONG":
            price_diff = exit_price - trade.entry_price
        else:
            price_diff = trade.entry_price - exit_price

        gross_pnl = trade.position_size * price_diff
        commission = trade.position_size * trade.entry_price * (self.config.commission_pct / Decimal("100"))
        commission += trade.position_size * exit_price * (self.config.commission_pct / Decimal("100"))
        slippage = trade.position_size * trade.entry_price * (self.config.slippage_pct / Decimal("100"))
        slippage += trade.position_size * exit_price * (self.config.slippage_pct / Decimal("100"))

        net_pnl = gross_pnl - commission - slippage
        pnl_pct = (net_pnl / (trade.position_size * trade.entry_price)) * Decimal("100")
        r_multiple = net_pnl / (trade.position_size * abs(trade.entry_price - trade.stop_loss))

        return BacktestTrade(
            symbol=trade.symbol,
            entry_time=trade.entry_time,
            exit_time=exit_time,
            direction=trade.direction,
            entry_price=trade.entry_price,
            exit_price=exit_price,
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit,
            position_size=trade.position_size,
            leverage=trade.leverage,
            pnl=net_pnl,
            pnl_pct=pnl_pct,
            r_multiple=r_multiple,
            exit_reason=exit_reason,
            confidence_score=trade.confidence_score,
        )

    def _compile_results(
        self,
        trades: list[BacktestTrade],
        equity_curve: list[tuple[datetime, Decimal]],
        final_balance: Decimal,
    ) -> BacktestResult:
        """Compile final backtest statistics."""
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t.pnl > 0)
        losing_trades = total_trades - winning_trades

        win_rate = Decimal(str(winning_trades / total_trades * 100)) if total_trades > 0 else Decimal("0")
        total_pnl = sum(t.pnl for t in trades)
        total_pnl_pct = (total_pnl / self.config.initial_balance) * Decimal("100")

        # Max drawdown
        peak = self.config.initial_balance
        max_dd = Decimal("0")
        for _, equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        max_dd_pct = (max_dd / peak) * Decimal("100")

        # Profit factor
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

        # Average R-multiple
        avg_r = sum(t.r_multiple for t in trades) / Decimal(str(total_trades)) if total_trades > 0 else Decimal("0")

        return BacktestResult(
            config=self.config,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            initial_balance=self.config.initial_balance,
            final_balance=final_balance,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            sharpe_ratio=None,  # TODO: implement
            profit_factor=profit_factor,
            avg_r_multiple=avg_r,
            trades=trades,
            equity_curve=equity_curve,
        )
