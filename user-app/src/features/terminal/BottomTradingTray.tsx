import React, { useState } from 'react'
import { PositionDto, OrderDto, OrderFillDto, SignalSetupDto } from '../../types/trading'
import { Layers, BookOpen, Clock, Radio, RefreshCw } from 'lucide-react'

interface BottomTradingTrayProps {
  positions: PositionDto[]
  orders: OrderDto[]
  fills: OrderFillDto[]
  setups: SignalSetupDto[]
  isLoading?: boolean
  onRefresh?: () => void
}

export const BottomTradingTray: React.FC<BottomTradingTrayProps> = ({
  positions,
  orders,
  fills,
  setups,
  isLoading,
  onRefresh,
}) => {
  const [activeTab, setActiveTab] = useState<'positions' | 'orders' | 'fills' | 'setups'>('positions')

  return (
    <div className="glass-panel rounded-lg overflow-hidden flex flex-col min-h-[280px]">
      {/* Tab Navigation Header */}
      <div className="h-10 px-3 border-b border-terminal-border/80 flex items-center justify-between bg-background-surface/90 text-xs select-none">
        <div className="flex items-center gap-1 sm:gap-2">
          <button
            onClick={() => setActiveTab('positions')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-mono text-xs font-semibold transition-all ${
              activeTab === 'positions'
                ? 'bg-background-elevated text-brand-cyan border border-brand-cyan/30'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>Positions</span>
            <span className="px-1.5 py-0.2 rounded-full bg-background text-[10px] text-slate-300">
              {positions.length}
            </span>
          </button>

          <button
            onClick={() => setActiveTab('orders')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-mono text-xs font-semibold transition-all ${
              activeTab === 'orders'
                ? 'bg-background-elevated text-brand-cyan border border-brand-cyan/30'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <BookOpen className="w-3.5 h-3.5" />
            <span>Open Orders</span>
            <span className="px-1.5 py-0.2 rounded-full bg-background text-[10px] text-slate-300">
              {orders.filter((o) => o.status === 'OPEN' || o.status === 'PENDING').length}
            </span>
          </button>

          <button
            onClick={() => setActiveTab('fills')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-mono text-xs font-semibold transition-all ${
              activeTab === 'fills'
                ? 'bg-background-elevated text-brand-cyan border border-brand-cyan/30'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            <span>Fills</span>
            <span className="px-1.5 py-0.2 rounded-full bg-background text-[10px] text-slate-300">
              {fills.length}
            </span>
          </button>

          <button
            onClick={() => setActiveTab('setups')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-mono text-xs font-semibold transition-all ${
              activeTab === 'setups'
                ? 'bg-background-elevated text-brand-cyan border border-brand-cyan/30'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <Radio className="w-3.5 h-3.5" />
            <span>SMC Setups</span>
            <span className="px-1.5 py-0.2 rounded-full bg-background text-[10px] text-slate-300">
              {setups.length}
            </span>
          </button>
        </div>

        {/* Refresh Button */}
        {onRefresh && (
          <button
            onClick={onRefresh}
            disabled={isLoading}
            className="p-1.5 rounded-md hover:bg-background-elevated text-slate-400 hover:text-brand-cyan transition-colors disabled:opacity-50"
            title="Refresh Ledger"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-brand-cyan' : ''}`} />
          </button>
        )}
      </div>

      {/* Tab Content Tables */}
      <div className="flex-1 overflow-x-auto p-2">
        {/* Tab 1: Positions */}
        {activeTab === 'positions' && (
          positions.length > 0 ? (
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                  <th className="py-2 px-3">Symbol</th>
                  <th className="py-2 px-3">Side</th>
                  <th className="py-2 px-3">Size</th>
                  <th className="py-2 px-3">Entry Price</th>
                  <th className="py-2 px-3">Mark Price</th>
                  <th className="py-2 px-3">Unrealized P&L</th>
                  <th className="py-2 px-3">Liq. Price</th>
                  <th className="py-2 px-3">Leverage</th>
                  <th className="py-2 px-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                {positions.map((pos) => {
                  const isLong = pos.side?.toUpperCase() === 'LONG' || pos.side?.toUpperCase() === 'BUY'
                  const isProfitable = (pos.unrealizedPnl || 0) >= 0
                  return (
                    <tr key={pos.id} className="hover:bg-background-elevated/40 transition-colors">
                      <td className="py-2 px-3 font-bold text-white">{pos.symbol}</td>
                      <td className="py-2 px-3">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${isLong ? 'bg-bullish/15 text-bullish' : 'bg-bearish/15 text-bearish'}`}>
                          {pos.side}
                        </span>
                      </td>
                      <td className="py-2 px-3">{pos.quantity}</td>
                      <td className="py-2 px-3">${pos.entryPrice?.toFixed(2)}</td>
                      <td className="py-2 px-3">${pos.currentPrice?.toFixed(2) || '—'}</td>
                      <td className={`py-2 px-3 font-bold ${isProfitable ? 'text-bullish' : 'text-bearish'}`}>
                        {isProfitable ? '+' : ''}${pos.unrealizedPnl?.toFixed(2) || '0.00'}
                      </td>
                      <td className="py-2 px-3 text-slate-400">${pos.liquidationPrice?.toFixed(2) || '—'}</td>
                      <td className="py-2 px-3">{pos.leverage}x</td>
                      <td className="py-2 px-3">
                        <span className="px-1.5 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-slate-300">
                          {pos.status}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <div className="py-12 text-center text-slate-500 font-mono text-xs">
              No open positions. Active positions will appear here once executed.
            </div>
          )
        )}

        {/* Tab 2: Open Orders */}
        {activeTab === 'orders' && (
          orders.length > 0 ? (
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                  <th className="py-2 px-3">Time</th>
                  <th className="py-2 px-3">Symbol</th>
                  <th className="py-2 px-3">Side</th>
                  <th className="py-2 px-3">Type</th>
                  <th className="py-2 px-3">Price</th>
                  <th className="py-2 px-3">Quantity</th>
                  <th className="py-2 px-3">Filled</th>
                  <th className="py-2 px-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                {orders.map((o) => (
                  <tr key={o.id} className="hover:bg-background-elevated/40 transition-colors">
                    <td className="py-2 px-3 text-slate-400">{new Date(o.placedAt).toLocaleTimeString()}</td>
                    <td className="py-2 px-3 font-bold text-white">{o.symbol}</td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${o.side === 'BUY' ? 'bg-bullish/15 text-bullish' : 'bg-bearish/15 text-bearish'}`}>
                        {o.side}
                      </span>
                    </td>
                    <td className="py-2 px-3">{o.orderType}</td>
                    <td className="py-2 px-3">${o.price ? o.price.toFixed(2) : 'MARKET'}</td>
                    <td className="py-2 px-3">{o.quantity}</td>
                    <td className="py-2 px-3">{o.filledQuantity || 0}</td>
                    <td className="py-2 px-3">
                      <span className="px-1.5 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-slate-300">
                        {o.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="py-12 text-center text-slate-500 font-mono text-xs">
              No active open orders found for this account.
            </div>
          )
        )}

        {/* Tab 3: Fills */}
        {activeTab === 'fills' && (
          fills.length > 0 ? (
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                  <th className="py-2 px-3">Time</th>
                  <th className="py-2 px-3">Symbol</th>
                  <th className="py-2 px-3">Side</th>
                  <th className="py-2 px-3">Fill Price</th>
                  <th className="py-2 px-3">Fill Quantity</th>
                  <th className="py-2 px-3">Fee</th>
                  <th className="py-2 px-3">Fill ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                {fills.map((f) => (
                  <tr key={f.id} className="hover:bg-background-elevated/40 transition-colors">
                    <td className="py-2 px-3 text-slate-400">{new Date(f.filledAt).toLocaleTimeString()}</td>
                    <td className="py-2 px-3 font-bold text-white">{f.symbol}</td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${f.side === 'BUY' ? 'bg-bullish/15 text-bullish' : 'bg-bearish/15 text-bearish'}`}>
                        {f.side}
                      </span>
                    </td>
                    <td className="py-2 px-3">${f.fillPrice.toFixed(2)}</td>
                    <td className="py-2 px-3">{f.fillQuantity}</td>
                    <td className="py-2 px-3 text-slate-400">${f.fee.toFixed(4)} {f.feeAsset}</td>
                    <td className="py-2 px-3 text-slate-500 text-[10px] truncate max-w-[120px]">{f.exchangeFillId || f.id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="py-12 text-center text-slate-500 font-mono text-xs">
              No recent execution fills recorded.
            </div>
          )
        )}

        {/* Tab 4: Strategy Setups */}
        {activeTab === 'setups' && (
          setups.length > 0 ? (
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border text-slate-400 text-[11px]">
                  <th className="py-2 px-3">Setup ID</th>
                  <th className="py-2 px-3">Symbol</th>
                  <th className="py-2 px-3">Direction</th>
                  <th className="py-2 px-3">Entry Price</th>
                  <th className="py-2 px-3">Stop Loss</th>
                  <th className="py-2 px-3">Take Profit</th>
                  <th className="py-2 px-3">RR Ratio</th>
                  <th className="py-2 px-3">Confidence</th>
                  <th className="py-2 px-3">State</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                {setups.map((s) => (
                  <tr key={s.id} className="hover:bg-background-elevated/40 transition-colors">
                    <td className="py-2 px-3 font-semibold text-brand-cyan">{s.setupId}</td>
                    <td className="py-2 px-3 text-white font-bold">{s.symbol}</td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${s.direction?.toUpperCase() === 'LONG' || s.direction?.toUpperCase() === 'BUY' ? 'bg-bullish/15 text-bullish' : 'bg-bearish/15 text-bearish'}`}>
                        {s.direction}
                      </span>
                    </td>
                    <td className="py-2 px-3">${s.entryPrice.toFixed(2)}</td>
                    <td className="py-2 px-3 text-bearish">${s.stopLoss.toFixed(2)}</td>
                    <td className="py-2 px-3 text-bullish">${s.takeProfit.toFixed(2)}</td>
                    <td className="py-2 px-3 font-bold">{s.riskReward.toFixed(2)}</td>
                    <td className="py-2 px-3 text-brand-cyan">{Math.round(s.confidence * (s.confidence <= 1 ? 100 : 1))}%</td>
                    <td className="py-2 px-3">
                      <span className="px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-slate-300 font-bold">
                        {s.setupState}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="py-12 text-center text-slate-500 font-mono text-xs">
              No qualified 1H SMC setups currently active. Scanning order blocks...
            </div>
          )
        )}
      </div>
    </div>
  )
}
