import React, { useEffect, useRef, useState } from 'react'
import {
  createChart,
  IChartApi,
  ISeriesApi,
  CandlestickSeries,
  HistogramSeries,
  CandlestickData,
  HistogramData,
  Time,
  ColorType,
  LineStyle,
} from 'lightweight-charts'
import { CandleDto } from '../../types/market'
import { SignalSetupDto } from '../../types/trading'
import { AlertCircle, RefreshCw } from 'lucide-react'

interface TradingViewChartProps {
  candles: CandleDto[]
  activeSetup?: SignalSetupDto | null
  isLoading?: boolean
  error?: string | null
  onRetry?: () => void
  symbol: string
  interval: string
}

export const TradingViewChart: React.FC<TradingViewChartProps> = ({
  candles,
  activeSetup,
  isLoading,
  error,
  onRetry,
  symbol,
  interval,
}) => {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const priceLinesRef = useRef<any[]>([])

  const [hoveredCandle, setHoveredCandle] = useState<{
    time: string
    open: number
    high: number
    low: number
    close: number
    volume: number
  } | null>(null)

  // Initialize Lightweight Charts
  useEffect(() => {
    if (!chartContainerRef.current) return

    // Clean up previous instance
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#080B11' },
        textColor: '#94A3B8',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
      },
      grid: {
        vertLines: { color: 'rgba(31, 41, 61, 0.5)', style: LineStyle.Dotted },
        horzLines: { color: 'rgba(31, 41, 61, 0.5)', style: LineStyle.Dotted },
      },
      crosshair: {
        vertLine: { color: '#06B6D4', width: 1, style: LineStyle.Dashed },
        horzLine: { color: '#06B6D4', width: 1, style: LineStyle.Dashed },
      },
      rightPriceScale: {
        borderColor: '#1F293D',
        scaleMargins: {
          top: 0.1,
          bottom: 0.2, // Leave space for volume histogram below
        },
      },
      timeScale: {
        borderColor: '#1F293D',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale: true,
    })

    // Candlestick Series in v5.2
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10B981',
      downColor: '#F43F5E',
      borderVisible: false,
      wickUpColor: '#10B981',
      wickDownColor: '#F43F5E',
    })

    // Volume Histogram Series in v5.2
    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#3B82F6',
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: '', // Overlay over bottom
    })
    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    volumeSeriesRef.current = volumeSeries

    // Crosshair move subscription for header OHLCV stats
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData) {
        setHoveredCandle(null)
        return
      }

      const candleData = param.seriesData.get(candleSeries) as any
      const volumeData = param.seriesData.get(volumeSeries) as any

      if (candleData) {
        setHoveredCandle({
          time: typeof param.time === 'number' ? new Date(param.time * 1000).toLocaleString() : String(param.time),
          open: candleData.open,
          high: candleData.high,
          low: candleData.low,
          close: candleData.close,
          volume: volumeData ? volumeData.value : 0,
        })
      }
    })

    // Resize Observer for auto-responsive chart canvas
    const resizeObserver = new ResizeObserver((entries) => {
      if (entries.length > 0 && chartContainerRef.current) {
        const { width, height } = entries[0].contentRect
        chart.applyOptions({ width, height })
      }
    })
    resizeObserver.observe(chartContainerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [])

  // Update Candle & Volume Data
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !candles || candles.length === 0) return

    try {
      const formattedCandles: CandlestickData[] = []
      const formattedVolumes: HistogramData[] = []

      // Deduplicate & sort candles by timestamp
      const sorted = [...candles].sort((a, b) => a.timestamp - b.timestamp)
      const seenTimes = new Set<number>()

      for (const c of sorted) {
        // Normalize timestamp: if in ms (> year 2033 in seconds), divide by 1000
        let timeInSeconds = c.timestamp > 2000000000 ? Math.floor(c.timestamp / 1000) : c.timestamp

        if (!seenTimes.has(timeInSeconds)) {
          seenTimes.add(timeInSeconds)
          const time = timeInSeconds as Time
          const isUp = c.close >= c.open

          formattedCandles.push({
            time,
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
          })

          formattedVolumes.push({
            time,
            value: Number(c.volume),
            color: isUp ? 'rgba(16, 185, 129, 0.35)' : 'rgba(244, 63, 94, 0.35)',
          })
        }
      }

      candleSeriesRef.current.setData(formattedCandles)
      volumeSeriesRef.current.setData(formattedVolumes)

      if (chartRef.current && formattedCandles.length > 0) {
        chartRef.current.timeScale().fitContent()
      }
    } catch (err) {
      console.warn('Error formatting chart candles', err)
    }
  }, [candles])

  // Update SMC Visual Overlays (Entry, SL, TP Lines & Order Block bounds from authoritative setup)
  useEffect(() => {
    if (!candleSeriesRef.current) return

    // Remove existing price lines
    priceLinesRef.current.forEach((line) => {
      try {
        candleSeriesRef.current?.removePriceLine(line)
      } catch (e) {
        // Ignore removal error
      }
    })
    priceLinesRef.current = []

    if (!activeSetup) return

    try {
      // Entry Price Line (Cyan)
      if (activeSetup.entryPrice) {
        const entryLine = candleSeriesRef.current.createPriceLine({
          price: Number(activeSetup.entryPrice),
          color: '#06B6D4',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: `ENTRY (${activeSetup.direction})`,
        })
        priceLinesRef.current.push(entryLine)
      }

      // Stop Loss Price Line (Rose/Red)
      if (activeSetup.stopLoss) {
        const slLine = candleSeriesRef.current.createPriceLine({
          price: Number(activeSetup.stopLoss),
          color: '#F43F5E',
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'STOP LOSS',
        })
        priceLinesRef.current.push(slLine)
      }

      // Take Profit Price Line (Emerald/Green)
      if (activeSetup.takeProfit) {
        const tpLine = candleSeriesRef.current.createPriceLine({
          price: Number(activeSetup.takeProfit),
          color: '#10B981',
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `TAKE PROFIT (RR ${activeSetup.riskReward?.toFixed(2) || '2.00'})`,
        })
        priceLinesRef.current.push(tpLine)
      }
    } catch (err) {
      console.warn('Error applying SMC visual lines', err)
    }
  }, [activeSetup])

  return (
    <div className="glass-panel rounded-lg overflow-hidden flex flex-col h-[520px] relative">
      {/* Top Chart Floating Bar with OHLC Tracker & Active SMC Overlay Badge */}
      <div className="h-9 px-3 border-b border-terminal-border/80 flex items-center justify-between text-xs font-mono bg-background-surface/80 select-none">
        <div className="flex items-center gap-3">
          <span className="font-bold text-white">{symbol}</span>
          <span className="text-slate-400 font-sans text-[11px] px-1.5 py-0.5 rounded bg-background border border-terminal-border">
            {interval.toUpperCase()}
          </span>

          {hoveredCandle ? (
            <div className="hidden sm:flex items-center gap-3 text-[11px]">
              <span>O: <strong className="text-white">${hoveredCandle.open.toFixed(2)}</strong></span>
              <span>H: <strong className="text-white">${hoveredCandle.high.toFixed(2)}</strong></span>
              <span>L: <strong className="text-white">${hoveredCandle.low.toFixed(2)}</strong></span>
              <span>C: <strong className={hoveredCandle.close >= hoveredCandle.open ? 'text-bullish' : 'text-bearish'}>${hoveredCandle.close.toFixed(2)}</strong></span>
              <span>Vol: <strong className="text-slate-300">{hoveredCandle.volume.toLocaleString()}</strong></span>
            </div>
          ) : (
            <span className="text-slate-500 text-[11px] hidden sm:inline">Hover over chart to view OHLCV data</span>
          )}
        </div>

        {activeSetup && (
          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-brand-cyan/10 border border-brand-cyan/20 text-brand-cyan text-[11px]">
            <span className="w-1.5 h-1.5 rounded-full bg-brand-cyan animate-ping"></span>
            <span>SMC SETUP: {activeSetup.setupId}</span>
          </div>
        )}
      </div>

      {/* Main Chart Canvas Container */}
      <div ref={chartContainerRef} className="flex-1 w-full relative" />

      {/* Loading Overlay */}
      {isLoading && (!candles || candles.length === 0) && (
        <div className="absolute inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center z-10 font-mono text-xs text-slate-300">
          <div className="flex flex-col items-center gap-3">
            <RefreshCw className="w-6 h-6 animate-spin text-brand-cyan" />
            <span>Loading {symbol} OHLCV Candlestick Feed...</span>
          </div>
        </div>
      )}

      {/* Error Overlay */}
      {error && (!candles || candles.length === 0) && (
        <div className="absolute inset-0 bg-background/90 backdrop-blur-sm flex items-center justify-center z-10 font-mono text-xs">
          <div className="flex flex-col items-center gap-3 p-6 text-center max-w-md">
            <AlertCircle className="w-8 h-8 text-bearish" />
            <div className="font-bold text-white">Market Feed Unavailable</div>
            <div className="text-slate-400 text-[11px]">{error}</div>
            {onRetry && (
              <button
                onClick={onRetry}
                className="mt-2 px-3 py-1.5 rounded bg-brand-cyan text-background font-bold text-xs hover:bg-brand-cyan/90 transition-all"
              >
                Retry Market Feed
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
