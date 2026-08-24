import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react'
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
  IPriceLine,
} from 'lightweight-charts'
import { CandleDto } from '../../types/market'
import { SignalSetupDto } from '../../types/trading'
import { formatPrice, tryGetInstrumentMeta } from '../../constants/instruments'
import { AlertCircle, RefreshCw, Activity, ShieldCheck, Maximize2, Minimize2, RotateCcw, Clock } from 'lucide-react'

interface TradingViewChartProps {
  candles: CandleDto[]
  activeSetup?: SignalSetupDto | null
  isLoading?: boolean
  error?: string | null
  onRetry?: () => void
  symbol: string
  interval: string
  showOverlays?: boolean
  lastUpdate?: number | null
  feedLatency?: number | null
}

// SMC Overlay types for visualization
interface SmcZone {
  id: string
  type: 'OB' | 'FVG' | 'LIQUIDITY' | 'BOS' | 'CHOCH'
  top: number
  bottom: number
  left: number
  right: number
  color: string
  label: string
  direction: 'BULLISH' | 'BEARISH'
  mitigated: boolean
}

interface SmcLine {
  id: string
  type: 'BOS' | 'CHOCH'
  price: number
  time: Time
  direction: 'BULLISH' | 'BEARISH'
  color: string
  label: string
}

export const TradingViewChart: React.FC<TradingViewChartProps> = ({
  candles,
  activeSetup,
  isLoading,
  error,
  onRetry,
  symbol,
  interval,
  showOverlays = true,
  lastUpdate,
  feedLatency,
}) => {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  const smcPriceLinesRef = useRef<IPriceLine[]>([])
  const isInitializedRef = useRef(false)
  const lastCandleCountRef = useRef(0)
  const [isFullscreen, setIsFullscreen] = useState(false)

  const [hoveredCandle, setHoveredCandle] = useState<{
    time: string
    open: number
    high: number
    low: number
    close: number
    volume: number
  } | null>(null)

  const [chartState, setChartState] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading')

  const meta = tryGetInstrumentMeta(symbol)
  const displaySymbol = meta?.displaySymbol ?? symbol

  // Determine feed freshness - use 30 seconds for live, 5 minutes for stale
  const latestCandle = candles && candles.length > 0 ? candles[candles.length - 1] : null
  const now = Date.now() / 1000
  const candleAge = latestCandle ? now - (latestCandle.timestamp > 2000000000 ? latestCandle.timestamp / 1000 : latestCandle.timestamp) : Infinity
  const isLive = candleAge < 30 // 30 seconds for live trading
  const isStale = candleAge >= 30 && candleAge < 300 // 5 minutes stale
  const feedState = isLive ? 'LIVE' : isStale ? 'STALE' : 'UNAVAILABLE'

  // Generate SMC zones from active setup and candles
  const smcZones = useMemo((): SmcZone[] => {
    if (!activeSetup || !candles || candles.length === 0) return []
    
    const zones: SmcZone[] = []
    const entry = activeSetup.entryPrice ? Number(activeSetup.entryPrice) : 0
    const isLong = activeSetup.direction?.toUpperCase() === 'LONG' || activeSetup.direction?.toUpperCase() === 'BUY'
    
    // Order Block zone (around entry)
    if (activeSetup.orderBlockPrice) {
      const obPrice = Number(activeSetup.orderBlockPrice)
      zones.push({
        id: 'ob-main',
        type: 'OB',
        top: obPrice * 1.001,
        bottom: obPrice * 0.999,
        left: 0,
        right: candles.length,
        color: isLong ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.15)',
        label: `OB ${isLong ? 'Bullish' : 'Bearish'}`,
        direction: isLong ? 'BULLISH' : 'BEARISH',
        mitigated: activeSetup.obMitigated || false,
      })
    }
    
    // FVG zone
    if (activeSetup.fvgPrice) {
      const fvgPrice = Number(activeSetup.fvgPrice)
      zones.push({
        id: 'fvg-main',
        type: 'FVG',
        top: fvgPrice * 1.002,
        bottom: fvgPrice * 0.998,
        left: 0,
        right: candles.length,
        color: isLong ? 'rgba(6, 182, 212, 0.15)' : 'rgba(236, 72, 153, 0.15)',
        label: `FVG ${isLong ? 'Bullish' : 'Bearish'}`,
        direction: isLong ? 'BULLISH' : 'BEARISH',
        mitigated: activeSetup.fvgMitigated || false,
      })
    }
    
    // Liquidity levels (swing highs/lows)
    const recentCandles = candles.slice(-50)
    const highs = recentCandles.map(c => Number(c.high))
    const lows = recentCandles.map(c => Number(c.low))
    const swingHigh = Math.max(...highs)
    const swingLow = Math.min(...lows)
    
    if (swingHigh > entry) {
      zones.push({
        id: 'liq-high',
        type: 'LIQUIDITY',
        top: swingHigh * 1.0005,
        bottom: swingHigh * 0.9995,
        left: 0,
        right: candles.length,
        color: 'rgba(244, 63, 94, 0.2)',
        label: 'Sell-side Liquidity',
        direction: 'BEARISH',
        mitigated: false,
      })
    }
    if (swingLow < entry) {
      zones.push({
        id: 'liq-low',
        type: 'LIQUIDITY',
        top: swingLow * 1.0005,
        bottom: swingLow * 0.9995,
        left: 0,
        right: candles.length,
        color: 'rgba(16, 185, 129, 0.2)',
        label: 'Buy-side Liquidity',
        direction: 'BULLISH',
        mitigated: false,
      })
    }
    
    return zones
  }, [activeSetup, candles])

  // Generate BOS/CHOCH lines
  const smcLines = useMemo((): SmcLine[] => {
    if (!activeSetup || !candles || candles.length === 0) return []
    
    const lines: SmcLine[] = []
    const isLong = activeSetup.direction?.toUpperCase() === 'LONG' || activeSetup.direction?.toUpperCase() === 'BUY'
    
    // BOS line at structure break
    if (activeSetup.structureBreakPrice) {
      const lastTimestamp = candles[candles.length - 1].timestamp
      const timeInSeconds = lastTimestamp > 2000000000 ? Math.floor(lastTimestamp / 1000) : lastTimestamp
      lines.push({
        id: 'bos-main',
        type: 'BOS',
        price: Number(activeSetup.structureBreakPrice),
        time: timeInSeconds as Time,
        direction: isLong ? 'BULLISH' : 'BEARISH',
        color: isLong ? '#10B981' : '#F43F5E',
        label: `BOS ${isLong ? '↑' : '↓'}`,
      })
    }
    
    // CHOCH line
    if (activeSetup.chochPrice) {
      const lastTimestamp = candles[candles.length - 1].timestamp
      const timeInSeconds = lastTimestamp > 2000000000 ? Math.floor(lastTimestamp / 1000) : lastTimestamp
      lines.push({
        id: 'choch-main',
        type: 'CHOCH',
        price: Number(activeSetup.chochPrice),
        time: timeInSeconds as Time,
        direction: isLong ? 'BULLISH' : 'BEARISH',
        color: isLong ? '#06B6D4' : '#EC4899',
        label: `CHOCH ${isLong ? '↑' : '↓'}`,
      })
    }
    
    return lines
  }, [activeSetup, candles])

  // Initialize Lightweight Charts
  useEffect(() => {
    if (!chartContainerRef.current) return

    // Clean up previous instance
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    const container = chartContainerRef.current
    const { width, height } = container.getBoundingClientRect()

    if (width === 0 || height === 0) {
      // Container not ready, will retry on resize
      setChartState('loading')
      return
    }

    const chart = createChart(container, {
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
          bottom: 0.2,
        },
      },
      timeScale: {
        borderColor: '#1F293D',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale: true,
      width,
      height,
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
      priceScaleId: '',
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
    isInitializedRef.current = true
    setChartState(candles && candles.length > 0 ? 'ready' : 'empty')

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
        if (width > 0 && height > 0) {
          chart.applyOptions({ width, height })
        }
      }
    })
    resizeObserver.observe(container)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      isInitializedRef.current = false
    }
  }, [])

  // Clear chart on symbol/timeframe switch - but preserve viewport if same symbol
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return

    // Only clear if we're switching to a different symbol/interval
    // This prevents blank transition on data refresh
    candleSeriesRef.current.setData([])
    volumeSeriesRef.current.setData([])
    setHoveredCandle(null)
    priceLinesRef.current.forEach((line) => {
      try {
        candleSeriesRef.current?.removePriceLine(line)
      } catch {}
    })
    priceLinesRef.current = []
    smcPriceLinesRef.current.forEach((line) => {
      try {
        candleSeriesRef.current?.removePriceLine(line)
      } catch {}
    })
    smcPriceLinesRef.current = []
    lastCandleCountRef.current = 0
    setChartState('loading')
  }, [symbol, interval])

  // Update Candle & Volume Data - preserve viewport on refresh
  const updateChartData = useCallback(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !candles || candles.length === 0) {
      if (candles && candles.length === 0) {
        setChartState('empty')
      }
      return
    }

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

      // Only fitContent on initial load or significant data change
      const shouldFitContent = !isInitializedRef.current || 
        formattedCandles.length !== lastCandleCountRef.current ||
        (formattedCandles.length > 0 && lastCandleCountRef.current === 0)

      candleSeriesRef.current.setData(formattedCandles)
      volumeSeriesRef.current.setData(formattedVolumes)
      lastCandleCountRef.current = formattedCandles.length

      if (chartRef.current && shouldFitContent && formattedCandles.length > 0) {
        chartRef.current.timeScale().fitContent()
      }

      setChartState('ready')
    } catch (err) {
      console.error('Error formatting chart candles', err)
      setChartState('error')
    }
  }, [candles])

  useEffect(() => {
    updateChartData()
  }, [updateChartData])

  // Render SMC Zones (OB, FVG, Liquidity) using price lines
  useEffect(() => {
    if (!candleSeriesRef.current || !showOverlays) return

    // Remove existing SMC price lines
    smcPriceLinesRef.current.forEach((line) => {
      try {
        candleSeriesRef.current?.removePriceLine(line)
      } catch {}
    })
    smcPriceLinesRef.current = []

    // Add OB and FVG zones as price lines (top and bottom boundaries)
    smcZones.forEach(zone => {
      try {
        // Top boundary
        const topLine = candleSeriesRef.current?.createPriceLine({
          price: zone.top,
          color: zone.color,
          lineWidth: 1,
          lineStyle: zone.mitigated ? LineStyle.Dashed : LineStyle.Solid,
          axisLabelVisible: false,
        })
        if (topLine) smcPriceLinesRef.current.push(topLine)

        // Bottom boundary
        const bottomLine = candleSeriesRef.current?.createPriceLine({
          price: zone.bottom,
          color: zone.color,
          lineWidth: 1,
          lineStyle: zone.mitigated ? LineStyle.Dashed : LineStyle.Solid,
          axisLabelVisible: false,
        })
        if (bottomLine) smcPriceLinesRef.current.push(bottomLine)

        // Zone label (center line with title)
        const labelLine = candleSeriesRef.current?.createPriceLine({
          price: (zone.top + zone.bottom) / 2,
          color: zone.color,
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: zone.label,
        })
        if (labelLine) smcPriceLinesRef.current.push(labelLine)
      } catch (e) {
        console.warn('Failed to render SMC zone', e)
      }
    })
  }, [smcZones, showOverlays])

  // Render BOS/CHOCH Lines as price lines at the break level
  useEffect(() => {
    if (!candleSeriesRef.current || !showOverlays) return

    // Remove existing SMC price lines (they share the same ref)
    // Note: This will also remove zone lines, but they'll be re-added by the zone effect
    // For now, we add BOS/CHOCH as additional price lines

    smcLines.forEach(line => {
      try {
        const breakLine = candleSeriesRef.current?.createPriceLine({
          price: line.price,
          color: line.color,
          lineWidth: line.type === 'BOS' ? 2 : 1,
          lineStyle: line.type === 'BOS' ? LineStyle.Solid : LineStyle.Dashed,
          axisLabelVisible: true,
          title: line.label,
        })
        if (breakLine) smcPriceLinesRef.current.push(breakLine)
      } catch (e) {
        console.warn('Failed to render SMC line', e)
      }
    })
  }, [smcLines, showOverlays])

  // Update SMC Visual Overlays (Entry, SL, TP Lines from authoritative setup)
  useEffect(() => {
    if (!candleSeriesRef.current || !showOverlays) return

    // Remove existing price lines
    priceLinesRef.current.forEach((line) => {
      try {
        candleSeriesRef.current?.removePriceLine(line)
      } catch {}
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
          title: `TAKE PROFIT (RR ${activeSetup.riskReward ? activeSetup.riskReward.toFixed(2) : '2.00'})`,
        })
        priceLinesRef.current.push(tpLine)
      }
    } catch (err) {
      console.error('Error applying SMC visual lines', err)
    }
  }, [activeSetup, showOverlays])

  const handleFullscreenToggle = () => {
    setIsFullscreen(!isFullscreen)
  }

  const handleResetView = () => {
    if (chartRef.current && candleSeriesRef.current) {
      chartRef.current.timeScale().fitContent()
    }
  }

  const formatFeedState = () => {
    switch (feedState) {
      case 'LIVE':
        return <span className="flex items-center gap-1.5 text-bullish font-bold"><span className="w-1.5 h-1.5 rounded-full bg-bullish animate-pulse"></span>LIVE</span>
      case 'STALE':
        return <span className="flex items-center gap-1.5 text-warning font-bold"><span className="w-1.5 h-1.5 rounded-full bg-warning animate-ping"></span>STALE ({Math.round(candleAge)}s)</span>
      default:
        return <span className="flex items-center gap-1.5 text-slate-400"><span className="w-1.5 h-1.5 rounded-full bg-slate-400"></span>UNAVAILABLE</span>
    }
  }

  const formatLastUpdate = () => {
    if (!lastUpdate) return <span className="text-slate-500">—</span>
    const diff = Math.round((Date.now() - lastUpdate) / 1000)
    if (diff < 60) return <span className="text-bullish">{diff}s ago</span>
    if (diff < 3600) return <span className="text-warning">{Math.round(diff / 60)}m ago</span>
    return <span className="text-slate-400">{Math.round(diff / 3600)}h ago</span>
  }

  return (
    <div className={`glass-panel rounded-lg overflow-hidden flex flex-col relative ${isFullscreen ? 'fixed inset-0 z-50 h-full w-full rounded-none' : 'h-[520px]'}`}>
      {/* Top Chart Floating Bar with OHLC Tracker & Active SMC Overlay Badge */}
      <div className="h-9 px-3 border-b border-terminal-border/80 flex items-center justify-between text-xs font-mono bg-background-surface/80 select-none">
        <div className="flex items-center gap-3">
          <span className="font-bold text-white">{displaySymbol}</span>
          <span className="text-slate-400 font-sans text-[11px] px-1.5 py-0.5 rounded bg-background border border-terminal-border">
            {interval.toUpperCase()}
          </span>

          {/* Freshness Badge */}
          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px]">
            {formatFeedState()}
          </div>

          {/* SMC 1H Invariant Notice */}
          <div className="hidden lg:flex items-center gap-1 text-[10px] text-slate-400 font-sans">
            <ShieldCheck className="w-3 h-3 text-brand-cyan" />
            <span>1H Canonical SMC Invariant</span>
          </div>

          {hoveredCandle ? (
            <div className="hidden sm:flex items-center gap-3 text-[11px]">
              <span>O: <strong className="text-white">${formatPrice(hoveredCandle.open, symbol)}</strong></span>
              <span>H: <strong className="text-white">${formatPrice(hoveredCandle.high, symbol)}</strong></span>
              <span>L: <strong className="text-white">${formatPrice(hoveredCandle.low, symbol)}</strong></span>
              <span>C: <strong className={hoveredCandle.close >= hoveredCandle.open ? 'text-bullish' : 'text-bearish'}>${formatPrice(hoveredCandle.close, symbol)}</strong></span>
              <span>Vol: <strong className="text-slate-300">{hoveredCandle.volume.toLocaleString()}</strong></span>
            </div>
          ) : (
            <span className="text-slate-500 text-[11px] hidden xl:inline">Hover over chart to inspect candle values</span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {activeSetup && (
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-brand-cyan/10 border border-brand-cyan/20 text-brand-cyan text-[11px]">
              <Activity className="w-3.5 h-3.5 animate-pulse text-brand-cyan" />
              <span>SMC: {activeSetup.setupId}</span>
            </div>
          )}
          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-slate-400">
            <Clock className="w-3 h-3" />
            <span>Last: </span>
            {formatLastUpdate()}
          </div>
          {feedLatency !== null && feedLatency !== undefined && (
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] text-slate-400">
              <span className={feedLatency < 100 ? 'text-bullish' : feedLatency < 500 ? 'text-warning' : 'text-bearish'}>
                Lat: {feedLatency}ms
              </span>
            </div>
          )}
          {!isFullscreen && (
            <button
              onClick={handleFullscreenToggle}
              className="p-1.5 rounded hover:bg-background-elevated text-slate-400 hover:text-white transition-colors"
              title="Fullscreen"
            >
              <Maximize2 className="w-4 h-4" />
            </button>
          )}
          {isFullscreen && (
            <button
              onClick={handleFullscreenToggle}
              className="p-1.5 rounded hover:bg-background-elevated text-slate-400 hover:text-white transition-colors"
              title="Exit Fullscreen"
            >
              <Minimize2 className="w-4 h-4" />
            </button>
          )}
          <button
            onClick={handleResetView}
            className="p-1.5 rounded hover:bg-background-elevated text-slate-400 hover:text-white transition-colors"
            title="Reset View"
          >
            <RotateCcw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Main Chart Canvas Container */}
      <div ref={chartContainerRef} className="flex-1 w-full relative" />

      {/* State Overlays */}
      {chartState === 'loading' && (
        <div className="absolute inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center z-10 font-mono text-xs text-slate-300">
          <div className="flex flex-col items-center gap-3">
            <RefreshCw className="w-6 h-6 animate-spin text-brand-cyan" />
            <span>Loading {displaySymbol} OHLCV Candlestick Feed...</span>
          </div>
        </div>
      )}

      {chartState === 'empty' && !isLoading && (
        <div className="absolute inset-0 bg-background/90 backdrop-blur-sm flex items-center justify-center z-10 font-mono text-xs">
          <div className="flex flex-col items-center gap-3 p-6 text-center max-w-md">
            <AlertCircle className="w-8 h-8 text-slate-500" />
            <div className="font-bold text-white">No Chart Data</div>
            <div className="text-slate-400 text-[11px]">Waiting for {displaySymbol} {interval.toUpperCase()} candles...</div>
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

      {chartState === 'error' && (
        <div className="absolute inset-0 bg-background/90 backdrop-blur-sm flex items-center justify-center z-10 font-mono text-xs">
          <div className="flex flex-col items-center gap-3 p-6 text-center max-w-md">
            <AlertCircle className="w-8 h-8 text-bearish" />
            <div className="font-bold text-white">Market Feed Error</div>
            <div className="text-slate-400 text-[11px]">{error || 'Unknown error'}</div>
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

      {isFullscreen && (
        <div className="absolute bottom-4 right-4 flex gap-2 z-20">
          <button
            onClick={handleResetView}
            className="px-3 py-1.5 rounded bg-background/90 backdrop-blur-sm border border-terminal-border text-xs font-mono text-white hover:bg-brand-cyan/20 hover:border-brand-cyan/50 transition-all flex items-center gap-1.5"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Reset View</span>
          </button>
          <button
            onClick={handleFullscreenToggle}
            className="px-3 py-1.5 rounded bg-brand-cyan text-background font-mono text-xs hover:bg-brand-cyan/90 transition-all flex items-center gap-1.5"
          >
            <Minimize2 className="w-3.5 h-3.5" />
            <span>Exit Fullscreen</span>
          </button>
        </div>
      )}
    </div>
  )
}
