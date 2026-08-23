import React, { useState } from 'react'
import { Shield, AlertCircle, Info, Lock } from 'lucide-react'

interface OrderTicketCardProps {
  symbol: string
  currentPrice: number
  balance: number
  currency: string
}

export const OrderTicketCard: React.FC<OrderTicketCardProps> = ({
  symbol,
  currentPrice,
  balance,
  currency,
}) => {
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY')
  const [orderType, setOrderType] = useState<'LIMIT' | 'MARKET'>('LIMIT')
  const [price, setPrice] = useState<string>(currentPrice ? String(currentPrice) : '65000')
  const [quantity, setQuantity] = useState<string>('0.01')
  const [leverage, setLeverage] = useState<number>(10)
  const [showConfirmModal, setShowConfirmModal] = useState(false)
  const [noticeMessage, setNoticeMessage] = useState<string | null>(null)

  // Calculations
  const numericPrice = orderType === 'MARKET' ? currentPrice || 65000 : Number(price) || currentPrice || 65000
  const numericQty = Number(quantity) || 0
  const notionalValue = numericPrice * numericQty
  const marginRequired = leverage > 0 ? notionalValue / leverage : notionalValue

  const handlePercentage = (pct: number) => {
    if (currentPrice > 0 && leverage > 0) {
      const maxNotional = balance * leverage * (pct / 100)
      const calculatedQty = (maxNotional / currentPrice).toFixed(4)
      setQuantity(calculatedQty)
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setShowConfirmModal(true)
  }

  const handleConfirmOrder = () => {
    setShowConfirmModal(false)
    setNoticeMessage(
      `Order preview calculated: ${side} ${quantity} ${symbol} @ ${orderType === 'MARKET' ? 'MARKET' : '$' + price}. (Safety Invariant: Real orders dispatch strictly through OrderExecutionService.java:312).`
    )
    setTimeout(() => setNoticeMessage(null), 6000)
  }

  return (
    <div className="glass-panel p-4 rounded-lg flex flex-col justify-between space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between pb-2.5 border-b border-terminal-border/80">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-brand-cyan" />
          <span className="text-xs font-bold text-white uppercase tracking-wider">Order Ticket</span>
        </div>
        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-bullish/10 border border-bullish/20 text-bullish">
          SAFE MODE
        </span>
      </div>

      {noticeMessage && (
        <div className="p-2.5 rounded bg-brand-cyan/10 border border-brand-cyan/20 text-xs text-brand-cyan flex items-start gap-2">
          <Info className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{noticeMessage}</span>
        </div>
      )}

      {/* Side Selector (BUY vs SELL) */}
      <div className="grid grid-cols-2 gap-2 p-1 rounded-md bg-background/80 border border-terminal-border">
        <button
          type="button"
          onClick={() => setSide('BUY')}
          className={`py-1.5 rounded text-xs font-mono font-bold transition-all ${
            side === 'BUY'
              ? 'bg-bullish text-background shadow-sm'
              : 'text-slate-400 hover:text-white'
          }`}
        >
          BUY / LONG
        </button>
        <button
          type="button"
          onClick={() => setSide('SELL')}
          className={`py-1.5 rounded text-xs font-mono font-bold transition-all ${
            side === 'SELL'
              ? 'bg-bearish text-background shadow-sm'
              : 'text-slate-400 hover:text-white'
          }`}
        >
          SELL / SHORT
        </button>
      </div>

      {/* Order Type Tabs */}
      <div className="flex items-center justify-between text-xs font-mono">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setOrderType('LIMIT')}
            className={`px-2 py-0.5 rounded text-xs ${
              orderType === 'LIMIT'
                ? 'bg-background-elevated text-brand-cyan border border-brand-cyan/30'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            Limit
          </button>
          <button
            type="button"
            onClick={() => setOrderType('MARKET')}
            className={`px-2 py-0.5 rounded text-xs ${
              orderType === 'MARKET'
                ? 'bg-background-elevated text-brand-cyan border border-brand-cyan/30'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            Market
          </button>
        </div>
        <span className="text-[11px] text-slate-400">
          Avail: <strong className="text-white">${balance?.toFixed(2) || '0.00'}</strong> {currency}
        </span>
      </div>

      {/* Inputs Form */}
      <form onSubmit={handleSubmit} className="space-y-3">
        {/* Limit Price (if limit) */}
        {orderType === 'LIMIT' && (
          <div>
            <label className="block text-[10px] font-mono uppercase text-slate-400 mb-1">
              Order Price ($)
            </label>
            <input
              type="number"
              step="any"
              required
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              className="w-full px-3 py-1.5 bg-background/80 border border-terminal-border rounded text-xs font-mono text-white focus:outline-none focus:border-brand-cyan transition-colors"
              placeholder="65000.00"
            />
          </div>
        )}

        {/* Quantity */}
        <div>
          <label className="block text-[10px] font-mono uppercase text-slate-400 mb-1">
            Quantity ({symbol.replace('USD', '')})
          </label>
          <input
            type="number"
            step="any"
            required
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            className="w-full px-3 py-1.5 bg-background/80 border border-terminal-border rounded text-xs font-mono text-white focus:outline-none focus:border-brand-cyan transition-colors"
            placeholder="0.01"
          />
          {/* Quick % Buttons */}
          <div className="grid grid-cols-4 gap-1 mt-1.5">
            {[25, 50, 75, 100].map((pct) => (
              <button
                key={pct}
                type="button"
                onClick={() => handlePercentage(pct)}
                className="py-0.5 rounded bg-background/60 hover:bg-background-elevated border border-terminal-border text-[10px] font-mono text-slate-400 hover:text-white transition-colors"
              >
                {pct}%
              </button>
            ))}
          </div>
        </div>

        {/* Leverage Slider */}
        <div>
          <div className="flex justify-between text-[10px] font-mono uppercase text-slate-400 mb-1">
            <span>Leverage</span>
            <span className="text-brand-cyan font-bold">{leverage}x</span>
          </div>
          <input
            type="range"
            min="1"
            max="100"
            step="1"
            value={leverage}
            onChange={(e) => setLeverage(Number(e.target.value))}
            className="w-full accent-brand-cyan h-1 bg-slate-800 rounded-lg cursor-pointer"
          />
        </div>

        {/* Notional & Margin Calculation Card */}
        <div className="p-2.5 rounded bg-background/40 border border-terminal-border text-[11px] font-mono space-y-1">
          <div className="flex justify-between text-slate-400">
            <span>Order Value:</span>
            <span className="text-white">${notionalValue.toFixed(2)}</span>
          </div>
          <div className="flex justify-between text-slate-400">
            <span>Required Margin:</span>
            <span className="text-brand-cyan font-bold">${marginRequired.toFixed(2)}</span>
          </div>
        </div>

        {/* Submit Button */}
        <button
          type="submit"
          className={`w-full py-2 rounded text-xs font-mono font-bold transition-all shadow-md cursor-pointer ${
            side === 'BUY'
              ? 'bg-bullish hover:bg-bullish/90 text-background shadow-bullish/10'
              : 'bg-bearish hover:bg-bearish/90 text-background shadow-bearish/10'
          }`}
        >
          {side === 'BUY' ? 'PREVIEW BUY / LONG' : 'PREVIEW SELL / SHORT'}
        </button>
      </form>

      {/* Safety Badge */}
      <div className="pt-2 border-t border-terminal-border/60 flex items-center justify-between text-[10px] font-mono text-slate-500">
        <span className="flex items-center gap-1">
          <Lock className="w-3 h-3 text-brand-cyan" />
          Sole Authority: Spring Boot
        </span>
        <span className="text-slate-400">Zero Direct Exchange Calls</span>
      </div>

      {/* Confirmation Modal */}
      {showConfirmModal && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="glass-panel-elevated p-6 rounded-xl max-w-sm w-full space-y-4 shadow-2xl border border-terminal-border">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-5 h-5 text-warning" />
              <h3 className="text-sm font-bold text-white">Confirm Order Preview</h3>
            </div>
            <div className="p-3 rounded bg-background/80 border border-terminal-border text-xs font-mono space-y-1.5">
              <div className="flex justify-between text-slate-400">
                <span>Symbol:</span>
                <span className="text-white font-bold">{symbol}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Action:</span>
                <span className={side === 'BUY' ? 'text-bullish font-bold' : 'text-bearish font-bold'}>
                  {side} {orderType}
                </span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Quantity:</span>
                <span className="text-white">{quantity}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Leverage:</span>
                <span className="text-white">{leverage}x</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Estimated Margin:</span>
                <span className="text-brand-cyan font-bold">${marginRequired.toFixed(2)}</span>
              </div>
            </div>
            <div className="text-[11px] text-slate-400 font-sans leading-relaxed">
              Order calculations are verified locally before dispatch to the authoritative backend.
            </div>
            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowConfirmModal(false)}
                className="flex-1 py-2 rounded bg-background-elevated hover:bg-slate-700 text-xs font-semibold text-slate-300 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmOrder}
                className="flex-1 py-2 rounded bg-brand-cyan hover:bg-brand-cyan/90 text-xs font-bold text-background transition-colors"
              >
                Confirm Preview
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
