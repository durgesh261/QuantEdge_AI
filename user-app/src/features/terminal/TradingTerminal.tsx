import React, { useEffect, useState, useCallback } from 'react'
import { useMarketStore } from '../../stores/marketStore'
import { marketService } from '../../services/marketService'
import { tradingService } from '../../services/tradingService'
import { CandleDto } from '../../types/market'
import {
  SignalSetupDto,
  PositionDto,
  OrderDto,
  OrderFillDto,
  AccountSummaryDto,
} from '../../types/trading'
import { AiEnrichmentDto } from '../../types/ai'
import { SymbolTimeframeBar } from './SymbolTimeframeBar'
import { TradingViewChart } from './TradingViewChart'
import { AiSignalRadarCard } from './AiSignalRadarCard'
import { OrderTicketCard } from './OrderTicketCard'
import { BottomTradingTray } from './BottomTradingTray'

export const TradingTerminal: React.FC = () => {
  const { activeSymbol, activeInterval } = useMarketStore()

  // State
  const [candles, setCandles] = useState<CandleDto[]>([])
  const [candlesLoading, setCandlesLoading] = useState(true)
  const [candlesError, setCandlesError] = useState<string | null>(null)

  const [activeSetup, setActiveSetup] = useState<SignalSetupDto | null>(null)
  const [aiEnrichment, setAiEnrichment] = useState<AiEnrichmentDto | null>(null)

  const [positions, setPositions] = useState<PositionDto[]>([])
  const [orders, setOrders] = useState<OrderDto[]>([])
  const [fills, setFills] = useState<OrderFillDto[]>([])
  const [setups, setSetups] = useState<SignalSetupDto[]>([])
  const [accountSummary, setAccountSummary] = useState<AccountSummaryDto | null>(null)
  const [trayLoading, setTrayLoading] = useState(false)

  // 1. Fetch Candles
  const loadCandles = useCallback(async () => {
    try {
      setCandlesLoading(true)
      setCandlesError(null)
      const res = await marketService.getCandles(activeSymbol, activeInterval, 500)
      if (res && res.candles) {
        setCandles(res.candles)
      }
    } catch (err: any) {
      console.warn('Failed to load chart candles', err)
      setCandlesError(err.response?.data?.message || 'Unable to connect to Delta Exchange India candle feed')
    } finally {
      setCandlesLoading(false)
    }
  }, [activeSymbol, activeInterval])

  // 2. Fetch Trading & Setup Data
  const loadTradingData = useCallback(async () => {
    try {
      setTrayLoading(true)
      const [posRes, ordRes, fillRes, sigRes, sumRes] = await Promise.allSettled([
        tradingService.getPositions('OPEN'),
        tradingService.getOrders(undefined, undefined, 50),
        tradingService.getFills(undefined, 50),
        tradingService.getSignals(undefined, undefined, 50),
        tradingService.getAccountSummary(),
      ])

      if (posRes.status === 'fulfilled') setPositions(posRes.value)
      if (ordRes.status === 'fulfilled') setOrders(ordRes.value)
      if (fillRes.status === 'fulfilled') setFills(fillRes.value)
      if (sigRes.status === 'fulfilled') {
        const allSignals = sigRes.value
        setSetups(allSignals)

        // Find active setup for current symbol
        const matched = allSignals.find(
          (s) => s.symbol.toUpperCase() === activeSymbol.toUpperCase() && (s.setupState === 'QUALIFIED' || s.setupState === 'ACTIVE')
        ) || allSignals.find((s) => s.symbol.toUpperCase() === activeSymbol.toUpperCase()) || null

        setActiveSetup(matched)

        if (matched) {
          try {
            const aiRes = await tradingService.getAiIntelligence(matched.setupId)
            setAiEnrichment(aiRes)
          } catch (e) {
            // Optional AI intelligence
          }
        } else {
          setAiEnrichment(null)
        }
      }
      if (sumRes.status === 'fulfilled') setAccountSummary(sumRes.value)
    } catch (err) {
      console.warn('Notice loading trading ledger', err)
    } finally {
      setTrayLoading(false)
    }
  }, [activeSymbol])

  // Initial and reactive effects
  useEffect(() => {
    loadCandles()
  }, [loadCandles])

  useEffect(() => {
    loadTradingData()
    const interval = setInterval(loadTradingData, 5000)
    return () => clearInterval(interval)
  }, [loadTradingData])

  const latestCandle = candles.length > 0 ? candles[candles.length - 1] : null
  const currentPrice = latestCandle ? Number(latestCandle.close) : 65000

  return (
    <div className="space-y-4">
      {/* Top Bar: Symbol & Timeframe Selector + Live Ticker Metrics */}
      <SymbolTimeframeBar onRefresh={loadCandles} isLoading={candlesLoading} />

      {/* Main Terminal Workspace: 2-Column Split */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* Left 2 Columns: Candlestick Chart with SMC Visual Overlays */}
        <div className="xl:col-span-2 space-y-4">
          <TradingViewChart
            candles={candles}
            activeSetup={activeSetup}
            isLoading={candlesLoading}
            error={candlesError}
            onRetry={loadCandles}
            symbol={activeSymbol}
            interval={activeInterval}
          />
        </div>

        {/* Right 1 Column: AI Signal Radar & Safe Order Ticket */}
        <div className="space-y-4 flex flex-col justify-between">
          <AiSignalRadarCard
            setup={activeSetup}
            aiEnrichment={aiEnrichment}
            isLoading={candlesLoading}
            symbol={activeSymbol}
          />

          <OrderTicketCard
            symbol={activeSymbol}
            currentPrice={currentPrice}
            balance={accountSummary?.availableBalance ?? accountSummary?.balance ?? 10000}
            currency={accountSummary?.currency || 'USDT'}
          />
        </div>
      </div>

      {/* Bottom Tray: Positions, Open Orders, Fills, Strategy Setups Ledger */}
      <BottomTradingTray
        positions={positions}
        orders={orders}
        fills={fills}
        setups={setups}
        isLoading={trayLoading}
        onRefresh={loadTradingData}
      />
    </div>
  )
}
