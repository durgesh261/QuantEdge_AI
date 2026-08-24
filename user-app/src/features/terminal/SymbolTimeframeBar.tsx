import React, { useEffect } from 'react'
import { useMarketStore } from '../../stores/marketStore'
import { TickerDto } from '../../types/market'
import { SUPPORTED_SYMBOLS, formatPrice, getInstrumentMeta } from '../../constants/instruments'
import { TrendingUp, TrendingDown, ShieldCheck, RefreshCw } from 'lucide-react'

interface SymbolTimeframeBarProps {
  onRefresh?: () => void
  isLoading?: boolean
}

export const SymbolTimeframeBar: React.FC<SymbolTimeframeBarProps> = ({ onRefresh, isLoading }) => {
  const { activeSymbol, activeInterval, setActiveSymbol, setActiveInterval, tickers, fetchTicker } = useMarketStore()

  const symbols = SUPPORTED_SYMBOLS
  const intervals = [
    { label: '1m', value: '1m' },
    { label: '5m', value: '5m' },
    { label: '15m', value: '15m' },
    { label: '1H', value: '1h', isCanonical: true },
    { label: '4H', value: '4h' },
    { label: '1D', value: '1d' },
  ]

  const currentTicker: TickerDto | undefined = tickers[activeSymbol]

  useEffect(() => {
    fetchTicker(activeSymbol)
    const intervalId = setInterval(() => {
      fetchTicker(activeSymbol)
    }, 4000)
    return () => clearInterval(intervalId)
  }, [activeSymbol, fetchTicker])

  const isPositive = (currentTicker?.priceChangePercent24h ?? 0) >= 0

  return (
    <div className="glass-panel p-2.5 rounded-lg flex flex-wrap items-center justify-between gap-3 text-xs">
      {/* Left: Symbol Selector & Timeframes */}
      <div className="flex flex-wrap items-center gap-2 sm:gap-4">
        {/* Symbol Selector Pills */}
        <div className="flex items-center p-0.5 rounded-md bg-background/80 border border-terminal-border">
          {symbols.map((sym) => {
            const symMeta = getInstrumentMeta(sym)
            return (
              <button
                key={sym}
                onClick={() => setActiveSymbol(sym)}
                className={`px-3 py-1 rounded text-xs font-mono font-bold transition-all ${
                  activeSymbol === sym
                    ? 'bg-brand-cyan text-background shadow-sm'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {symMeta.displaySymbol}
              </button>
            )
          })}
        </div>

        {/* Timeframe Selector Pills */}
        <div className="flex items-center p-0.5 rounded-md bg-background/80 border border-terminal-border">
          {intervals.map((tf) => (
            <button
              key={tf.value}
              onClick={() => setActiveInterval(tf.value)}
              className={`px-2.5 py-1 rounded text-xs font-mono transition-all relative ${
                activeInterval === tf.value
                  ? 'bg-background-elevated text-brand-cyan font-bold border border-brand-cyan/30'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {tf.label}
              {tf.isCanonical && (
                <span className="absolute -top-1 -right-1 w-1.5 h-1.5 rounded-full bg-bullish animate-pulse" title="Canonical SMC Stream"></span>
              )}
            </button>
          ))}
        </div>

        {/* Manual Refresh Action */}
        {onRefresh && (
          <button
            onClick={onRefresh}
            disabled={isLoading}
            className="p-1.5 rounded-md hover:bg-background-elevated text-slate-400 hover:text-brand-cyan transition-colors disabled:opacity-50"
            title="Refresh Market Data"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-brand-cyan' : ''}`} />
          </button>
        )}
      </div>

      {/* Right: Live Ticker Statistics */}
      <div className="flex flex-wrap items-center gap-4 sm:gap-6 font-mono text-xs">
        {/* Mark Price */}
        <div>
          <div className="text-[10px] text-slate-400 uppercase font-sans">Mark Price</div>
          <div className="font-bold text-white text-sm">
            ${formatPrice(currentTicker?.markPrice ?? currentTicker?.lastPrice, activeSymbol)}
          </div>
        </div>

        {/* 24h Change */}
        <div>
          <div className="text-[10px] text-slate-400 uppercase font-sans">24h Change</div>
          <div className={`font-bold flex items-center gap-0.5 ${isPositive ? 'text-bullish' : 'text-bearish'}`}>
            {isPositive ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
            {isPositive ? '+' : ''}
            {currentTicker?.priceChangePercent24h?.toFixed(2) ?? '0.00'}%
          </div>
        </div>

        {/* 24h High / Low */}
        <div className="hidden md:block">
          <div className="text-[10px] text-slate-400 uppercase font-sans">24h High / Low</div>
          <div className="text-slate-300">
            <span className="text-white">${formatPrice(currentTicker?.high24h, activeSymbol)}</span> /{' '}
            <span className="text-slate-400">${formatPrice(currentTicker?.low24h, activeSymbol)}</span>
          </div>
        </div>

        {/* 24h Volume */}
        <div className="hidden lg:block">
          <div className="text-[10px] text-slate-400 uppercase font-sans">24h Volume</div>
          <div className="text-slate-200">
            ${currentTicker?.turnover24h?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ||
              currentTicker?.volume24h?.toLocaleString() ||
              '—'}
          </div>
        </div>

        {/* Exchange Feed Status */}
        <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-bullish/10 border border-bullish/20 text-bullish text-[11px]">
          <ShieldCheck className="w-3 h-3" />
          <span>DELTAIN LIVE</span>
        </div>
      </div>
    </div>
  )
}
