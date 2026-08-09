import React, { useEffect, useRef, useState } from 'react';
import {
  Activity, Zap, Pause, Square, ChevronDown,
  BarChart3, FileText, Layers, BookOpen, Send,
  Plus, BrainCircuit, RefreshCw, Play, Loader2,
  Lock, Wifi, WifiOff
} from 'lucide-react';
import { useScannerStore } from '../../store/useScannerStore';
import { useScannerSocket } from '../../hooks/useScannerSocket';
import { useDeltaStore } from '../../store/useDeltaStore';
import { useTerminalStore } from '../../store/useTerminalStore';
import { apiClient as api } from '../../services/api';
import { ErrorBoundary } from '../../components/ErrorBoundary';
import { useOrderBlocks } from '../../hooks/useOrderBlocks';
import { usePortfolioSummary } from '../../hooks/usePortfolioSummary';
import { useResizable } from '../../hooks/useResizable';
import { useNotificationStore } from '../../store/useNotificationStore';

// ═══════════════════════════════════════════════════════
// SAFE HELPERS — Prevent crashes, keep UI intact
// ═══════════════════════════════════════════════════════
const safeNum = (val: unknown, fallback = 0): number => {
  if (val === null || val === undefined) return fallback;
  if (typeof val === 'number') return Number.isFinite(val) ? val : fallback;
  if (typeof val === 'string') {
    const parsed = parseFloat(val);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
};

const safeStr = (val: unknown, fallback = '--'): string => {
  if (val === null || val === undefined) return fallback;
  return String(val) || fallback;
};

const fmtPrice = (val: unknown) => safeNum(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtQty = (val: unknown) => safeNum(val).toFixed(4);
const fmtCurr = (val: unknown) => `$${safeNum(val).toFixed(2)}`;

// ═══════════════════════════════════════════════════════
// TRADING VIEW WIDGET
// ═══════════════════════════════════════════════════════
function useTradingViewWidget(containerRef: React.RefObject<HTMLDivElement | null>, symbol: string, interval: string) {
  const loadedRef = useRef(false);
  useEffect(() => {
    const container = containerRef.current;
    if (!container || loadedRef.current) return;
    container.innerHTML = '';
    const widgetDiv = document.createElement('div');
    widgetDiv.className = 'tradingview-widget-container';
    widgetDiv.style.cssText = 'height:100%;width:100%;';
    const widget = document.createElement('div');
    widget.className = 'tradingview-widget-container__widget';
    widget.style.cssText = 'height:100%;';
    widgetDiv.appendChild(widget);
    container.appendChild(widgetDiv);
    const script = document.createElement('script');
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.type = 'text/javascript';
    script.async = true;
    const tvInterval = interval === '1m' ? '1' : interval === '5m' ? '5' : interval === '15m' ? '15' : interval === '1H' ? '60' : '240';
    script.innerHTML = JSON.stringify({
      autosize: true, symbol: `DELTAIN:${symbol}`, interval: tvInterval,
      timezone: 'Asia/Kolkata', theme: 'dark', style: '1', locale: 'en',
      enable_publishing: false, backgroundColor: 'rgba(11, 14, 20, 1)',
      gridColor: 'rgba(30, 41, 59, 0.5)', hide_top_toolbar: false,
      hide_legend: false, save_image: false, calendar: false,
      hide_volume: false, support_host: 'https://www.tradingview.com',
    });
    widgetDiv.appendChild(script);
    loadedRef.current = true;
    return () => { loadedRef.current = false; if (container) container.innerHTML = ''; };
  }, [containerRef, symbol, interval]);
}

// ═══════════════════════════════════════════════════════
// WATCHLIST DATA
// ═══════════════════════════════════════════════════════
const WATCHLIST = [
  { symbol: 'BTCUSD.P', name: 'Bitcoin Perpetual', price: 64951.00, change: 0.82 },
  { symbol: 'ETHUSD.P', name: 'Ethereum Perpetual', price: 1915.90, change: 0.47 },
  { symbol: 'SOLUSD.P', name: 'Solana Perpetual', price: 74.73, change: 1.48 },
  { symbol: 'XRPUSD.P', name: 'XRP Perpetual', price: 1.04, change: -0.78 },
];

// ═══════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════
const LiveTradingPageInner: React.FC = () => {
  const { activeSymbol, setActiveSymbol, activeTimeframe, isAlgoRunning } = useTerminalStore();
  const safeSymbol = activeSymbol || 'BTCUSD.P';
  const selectedSymbol = safeSymbol;
  const { isConnected, isDeltaEnabled } = useDeltaStore();
  const { addNotification } = useNotificationStore();
  const { data: summary } = usePortfolioSummary();

  // ═══════════════════════════════════════════════════════
  // TRADE GATE LOGIC — Unified with Header & StatusBar
  // ═══════════════════════════════════════════════════════
  // Gate 1: Delta must be ON & connected to trade at all
  const canTrade = isDeltaEnabled && isConnected;
  
  // Gate 2: If Algo is ON, manual execution is BLOCKED
  const isManualBlocked = isAlgoRunning;
  
  // Gate 3: Manual allowed only when Delta ON + Algo OFF
  const canTradeManual = canTrade && !isManualBlocked;
  
  const { global, pairs, isLoading } = useScannerStore();
  const { sendControl } = useScannerSocket();
  
  const stats = {
    ticks: global?.ticksTotal || 0,
    signals: global?.signalsTotal || 0,
    trades: global?.tradesTotal || 0,
    matrix: '4 Pairs (1H TF)',
  };
  
  const isGlobalPaused = global?.isPaused || false;
  const isGlobalStopped = !global?.isRunning || false;

  // positions from summary (overrides delta store positions for display)
  const positions = summary?.positions?.items || useDeltaStore.getState().positions || [];
  const ticker = useDeltaStore.getState().ticker;

  const selectedTimeframe = activeTimeframe || '1H';
  const [orderSide, setOrderSide] = useState<'buy' | 'sell'>('buy');
  const [orderType, setOrderType] = useState<'MKT' | 'LMT'>('MKT');
  const [quantity, setQuantity] = useState('');
  const [leverage, setLeverage] = useState(100);
  const [showTPSL, setShowTPSL] = useState(false);
  const [tpPrice, setTpPrice] = useState('');
  const [slPrice, setSlPrice] = useState('');
  const [tpType, setTpType] = useState<'Market' | 'Limit'>('Market');
  const [slType, setSlType] = useState<'Market' | 'Limit' | 'Trail'>('Market');
  const [activeTab, setActiveTab] = useState<'scanner' | 'risk' | 'positions' | 'pending' | 'ledger' | 'journal'>('scanner');

  const { width: rightPanelWidth, startResizing } = useResizable(288, 260, 500, 'left');

  const chartContainerRef = useRef<HTMLDivElement>(null);
  const { blocks: orderBlocks, isLoading: obLoading } = useOrderBlocks(safeSymbol);

  useTradingViewWidget(chartContainerRef, safeSymbol, selectedTimeframe);

  // Safe balance data
  const totalEquity = summary?.wallet?.totalEquity || 0;
  const availMargin = summary?.wallet?.availableMargin || 0;
  const usedMargin = totalEquity > 0 ? (totalEquity - availMargin) : 0;
  const unrealizedPnl = summary?.positions?.totalUnrealizedPnl || 0;
  const todayPnl = summary?.pnlBreakdown?.today || 0;

  const currentPrice = safeNum(ticker?.price) || WATCHLIST.find(w => w.symbol === selectedSymbol)?.price || 64951.00;
  const fundsRequired = quantity ? (safeNum(quantity) * currentPrice) / leverage : 0;

  const handleQtyPercent = (pct: number) => {
    // Use margin * leverage to get notional buying power, divide by price to get BTC size
    const buyingPower = availMargin * leverage;
    const btcQty = (buyingPower * (pct / 100)) / currentPrice;
    // Round to 3 decimal places, min 0.001
    const qty = Math.max(0, parseFloat(btcQty.toFixed(3)));
    setQuantity(qty > 0 ? qty.toFixed(3) : '');
  };

  const handleTpPercent = (pct: number) => {
    if (!currentPrice) return;
    const price = orderSide === 'buy' ? currentPrice * (1 + pct / 100) : currentPrice * (1 - pct / 100);
    setTpPrice(price.toFixed(2));
  };

  const handleSlPercent = (pct: number) => {
    if (!currentPrice) return;
    const price = orderSide === 'buy' ? currentPrice * (1 - pct / 100) : currentPrice * (1 + pct / 100);
    setSlPrice(price.toFixed(2));
  };

  const handleSubmit = async () => {
    // Gate 1: Delta must be ON & connected
    if (!isDeltaEnabled || !isConnected) {
      addNotification('Trade Blocked', 'Delta is offline. Enable Delta connection to trade.', 'error');
      return;
    }

    // Gate 2: Algo ON blocks manual execution
    if (isAlgoRunning) {
      addNotification('Manual Execution Blocked', 'Algo Trading is active. Manual orders are disabled while algorithmic trading is running.', 'warning');
      return;
    }

    if (!quantity || parseFloat(quantity) <= 0) return;

    try {
      await api.post('/orders', {
        symbol: safeSymbol,
        side: orderSide,
        type: orderType,
        size: parseFloat(quantity || '0'),
        leverage,
        price: orderType === 'LMT' ? currentPrice : undefined,
        stop_loss: slPrice ? parseFloat(slPrice) : undefined,
        take_profit: tpPrice ? parseFloat(tpPrice) : undefined,
        source: 'manual',
      });
      
      // Success notification
      addNotification('Order Submitted', `${orderSide.toUpperCase()} ${quantity} ${safeSymbol} @ ${orderType}`, 'success');
    } catch (err: any) {
      console.error('Order failed:', err);
      addNotification('Order Failed', err.response?.data?.message || err.message || 'Could not place order', 'error');
    }
  };

  const displayPositions = Array.isArray(positions) ? positions : [];

  return (
    <div className="w-full h-full bg-[#0B0E14] text-[#F8FAFC] overflow-y-auto overflow-x-hidden">
      
      {/* ═════════════════════════════════════════════════════
          TOP HEADER: Institutional Terminal + Equity Cards
          ═════════════════════════════════════════════════════ */}
      <div className="px-5 py-3 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 border-b border-[#1E293B] bg-[#0B0E14]">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-[#3B82F6]/20 flex items-center justify-center">
            <Activity className="w-4 h-4 text-[#3B82F6]" />
          </div>
          <div>
            <h1 className="text-[13px] font-bold text-[#F8FAFC]">{selectedSymbol} Institutional Terminal</h1>
            <p className="text-[9px] text-[#64748B]">
              Source: Delta Exchange India (DELTAIN) Δ Feed: 
              <span className={isConnected ? 'text-[#00C896]' : 'text-[#F6465D]'}>
                {isConnected ? ' CONNECTED' : ' DISCONNECTED'}
              </span>
            </p>
          </div>
        </div>

        {/* Equity Cards */}
        <div className="flex items-center space-x-2 overflow-x-auto no-scrollbar w-full md:w-auto pb-1 md:pb-0">
          {[
            { label: 'TOTAL EQUITY', value: fmtCurr(totalEquity) },
            { label: 'AVAIL MARGIN', value: fmtCurr(availMargin) },
            { label: 'USED MARGIN', value: fmtCurr(usedMargin) },
            { label: 'UNREALIZED PNL', value: (unrealizedPnl >= 0 ? '+' : '') + fmtCurr(unrealizedPnl), color: unrealizedPnl >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]' },
            { label: 'TODAY PNL', value: (todayPnl >= 0 ? '+' : '') + fmtCurr(todayPnl), color: todayPnl >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]' },
          ].map((card) => (
            <div key={card.label} className="bg-[#161D2A] border border-[#1E293B] rounded-lg px-3 py-1.5 min-w-[80px]">
              <div className="text-[8px] text-[#64748B] font-bold uppercase tracking-wider">{card.label}</div>
              <div className={`text-[11px] font-mono font-bold ${card.color || 'text-[#F8FAFC]'}`}>{card.value}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ═════════════════════════════════════════════════════
          MAIN WORKSPACE
          ═════════════════════════════════════════════════════ */}
      <div className="flex flex-col xl:flex-row px-4 py-3 gap-1 min-w-0">
        
        {/* CENTER: CHART AREA */}
        <div className="flex-1 min-w-0 flex flex-col gap-3 pr-2">
          {/* Symbol Tabs */}
          <div className="flex items-center space-x-1 bg-[#161D2A] border border-[#1E293B] rounded-lg p-1 w-fit">
            {['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD'].map((sym) => (
              <button
                key={sym}
                onClick={() => { const s = `${sym}.P`; setActiveSymbol(s); }}
                className={`px-3 py-1 rounded-md text-[10px] font-bold transition-colors ${
                  safeSymbol.startsWith(sym) ? 'bg-[#3B82F6] text-white' : 'text-[#94A3B8] hover:text-white'
                }`}
              >
                {sym}
              </button>
            ))}
            <button className="px-3 py-1 rounded-md text-[10px] font-bold bg-[#3B82F6]/20 text-[#3B82F6]">
              1H ONLY
            </button>
          </div>

          {/* Chart */}
          <div className="bg-[#0B0E14] border border-[#1E293B] rounded-xl overflow-hidden h-[400px] xl:h-[480px] w-full xl:flex-1">
            <div ref={chartContainerRef} className="h-full w-full" />
          </div>

          {/* LIVE ORDER BLOCKS — BACKEND NATIVE ENGINE */}
          <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4">
            <div className="flex items-center space-x-2 mb-3">
              <Zap className="w-3.5 h-3.5 text-[#F59E0B]" />
              <span className="text-[10px] font-bold text-[#94A3B8] uppercase">
                Live Order Blocks ({orderBlocks.length}) — Backend Native Engine
              </span>
              {obLoading && (
                <div className="w-3 h-3 border-2 border-[#F59E0B] border-t-transparent rounded-full animate-spin ml-2" />
              )}
            </div>

            {obLoading && orderBlocks.length === 0 ? (
              <div className="flex items-center justify-center py-6 space-x-2">
                <div className="w-4 h-4 border-2 border-[#3B82F6] border-t-transparent rounded-full animate-spin" />
                <span className="text-[10px] text-[#64748B]">Scanning {safeSymbol} for institutional order blocks...</span>
              </div>
            ) : orderBlocks.length === 0 ? (
              <div className="text-center py-6">
                <Zap className="w-6 h-6 text-[#334155] mx-auto mb-2" />
                <p className="text-[11px] text-[#64748B]">No active order blocks for {safeSymbol}</p>
                <p className="text-[9px] text-[#475569] mt-1">Scanner engine running — waiting for high-probability zones ≥85% AI score</p>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                {orderBlocks.map((ob) => (
                  <div 
                    key={ob.id} 
                    className={`bg-[#0B0E14] border rounded-lg p-3 ${
                      ob.type === 'DEMAND' ? 'border-[#00C896]/30' : 'border-[#F6465D]/30'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className={`text-[9px] font-bold uppercase ${
                        ob.type === 'DEMAND' ? 'text-[#00C896]' : 'text-[#F6465D]'
                      }`}>
                        {ob.type}
                      </span>
                      <div className="flex items-center space-x-2">
                        <span className="text-[9px] text-[#64748B]">{ob.freshness}% fresh</span>
                        <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold ${
                          ob.aiScore >= 85 ? 'bg-[#00C896]/20 text-[#00C896]' : 'bg-[#F59E0B]/20 text-[#F59E0B]'
                        }`}>
                          AI {ob.aiScore}
                        </span>
                      </div>
                    </div>
                    <div className="text-[11px] font-mono font-bold text-[#F8FAFC] mb-1">
                      {ob.priceLow.toFixed(2)} — {ob.priceHigh.toFixed(2)}
                    </div>
                    <div className="flex items-center space-x-3 text-[9px] text-[#64748B]">
                      <span>Strength: {ob.strength}</span>
                      <span>Touches: {ob.touches}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* RESIZER HANDLE */}
        <div 
          className="hidden xl:flex w-3 cursor-col-resize items-center justify-center hover:bg-slate-800/50 transition-colors z-10 shrink-0"
          onMouseDown={startResizing}
        >
          <div className="w-0.5 h-12 bg-slate-700 rounded-full group-hover:bg-[#3B82F6] transition-colors" />
        </div>

        {/* RIGHT: EXECUTION PANEL */}
        <div 
          className="w-full shrink-0 bg-[#0B0E14] border border-[#1E293B] rounded-xl flex flex-col p-4 gap-4"
          style={{ width: window.innerWidth >= 1280 ? rightPanelWidth : '100%' }}
        >
          {/* Execution Mode Banner */}
          <div className={`px-3 py-2 rounded-lg border flex items-center justify-center space-x-2 ${
            !canTrade ? 'bg-[#F6465D]/10 border-[#F6465D]/20' : isManualBlocked ? 'bg-[#F59E0B]/10 border-[#F59E0B]/20' : 'bg-[#00C896]/10 border-[#00C896]/20'
          }`}>
            {!canTrade ? (
              <>
                <WifiOff className="w-3 h-3 text-[#F6465D]" />
                <span className="text-[9px] font-bold text-[#F6465D] uppercase">Delta Offline — Trading Disabled</span>
              </>
            ) : isManualBlocked ? (
              <>
                <Lock className="w-3 h-3 text-[#F59E0B]" />
                <span className="text-[9px] font-bold text-[#F59E0B] uppercase">Algo Active — Manual Execution Locked</span>
              </>
            ) : (
              <>
                <Wifi className="w-3 h-3 text-[#00C896]" />
                <span className="text-[9px] font-bold text-[#00C896] uppercase">Manual Execution Enabled</span>
              </>
            )}
          </div>
          
          {/* Header & MKT/LMT */}
          <div className="flex justify-between items-start">
            <div>
              <div className="text-[14px] font-bold text-[#F8FAFC]">Execution —</div>
              <div className="text-[18px] font-black text-[#F8FAFC]">{selectedSymbol}</div>
            </div>
            <div className="flex bg-[#1E293B] rounded-lg p-0.5 overflow-hidden">
              {(['MKT', 'LMT'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setOrderType(t)}
                  className={`px-3 py-1 text-[12px] font-bold rounded-md transition-colors ${
                    orderType === t ? 'bg-[#3B82F6] text-white' : 'bg-transparent text-[#94A3B8] hover:text-white'
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* BUY/SELL Toggles */}
          <div className="flex gap-2">
            <button
              onClick={() => setOrderSide('buy')}
              className={`flex-1 py-3 rounded-lg text-[13px] font-bold uppercase transition-colors ${
                orderSide === 'buy' ? 'bg-[#00C896] text-[#064E3B]' : 'bg-[#1E293B] text-[#94A3B8] hover:bg-[#334155]'
              }`}
            >
              BUY / LONG
            </button>
            <button
              onClick={() => setOrderSide('sell')}
              className={`flex-1 py-3 rounded-lg text-[13px] font-bold uppercase transition-colors ${
                orderSide === 'sell' ? 'bg-[#F6465D] text-white' : 'bg-[#1E293B] text-[#94A3B8] hover:bg-[#334155]'
              }`}
            >
              SELL / SHORT
            </button>
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto">
            {/* Quantity Block */}
            <div className="space-y-1">
              <div className="bg-[#1A2232] border border-[#1E293B] rounded-lg overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 border-b border-[#1E293B]">
                  <input
                    type="number"
                    value={quantity}
                    onChange={(e) => setQuantity(e.target.value)}
                    placeholder="0.000"
                    className="flex-1 bg-transparent text-[14px] font-mono font-bold text-[#F8FAFC] outline-none placeholder-[#475569]"
                  />
                  <div className="flex items-center space-x-1 text-[#F8FAFC] text-[12px] font-bold">
                    <span>Lot</span>
                    <ChevronDown className="w-3 h-3 text-[#94A3B8]" />
                  </div>
                </div>
                
                <div className="flex bg-[#1A2232]">
                  {[10, 25, 50, 75, 100].map((p, i) => (
                    <button
                      key={p}
                      onClick={() => handleQtyPercent(p)}
                      className={`flex-1 py-1.5 text-[11px] font-bold text-[#3B82F6] hover:bg-[#1E293B] transition-colors ${
                        i !== 4 ? 'border-r border-[#1E293B]' : ''
                      }`}
                    >
                      {p}%
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex justify-between items-center text-[11px] text-[#64748B] px-1 pt-0.5">
                <span>~{(selectedSymbol || 'BTCUSD.P').split('USD')[0]}</span>
                <span>1 Lot = 0.01 {(selectedSymbol || 'BTCUSD.P').split('USD')[0]}</span>
              </div>
            </div>

            {/* Leverage Slider */}
            <div className="space-y-2">
              <div className="text-[12px] text-[#94A3B8]">
                Leverage: <span className="text-[#3B82F6] font-bold">{leverage}x</span>
              </div>
              <input
                type="range"
                min="1"
                max="100"
                value={leverage}
                onChange={(e) => setLeverage(parseInt(e.target.value))}
                className="w-full h-3 bg-[#1E293B] rounded-full appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-[#3B82F6]"
              />
            </div>

            {/* TP/SL Add Button */}
            <button
              onClick={() => setShowTPSL(!showTPSL)}
              className="w-full py-2.5 bg-[#0B0E14] border border-[#1E293B] rounded-lg text-[12px] font-bold text-[#F59E0B] hover:bg-[#1E293B] flex items-center justify-center space-x-1.5 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add TP/SL</span>
            </button>

            {/* TP/SL Block */}
            {showTPSL && (
              <div className="space-y-4 pt-1">
                {/* Take Profit */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[12px] font-bold text-[#F8FAFC] border-b border-dashed border-[#475569] pb-0.5">Take Profit</span>
                    <div className="flex bg-[#0B0E14] border border-[#1E293B] rounded overflow-hidden">
                      {(['Market', 'Limit'] as const).map(t => (
                        <button key={t} onClick={() => setTpType(t)} className={`px-3 py-1.5 text-[11px] font-bold transition-colors ${tpType === t ? 'bg-[#1E293B] text-[#F59E0B]' : 'text-[#64748B] hover:text-[#94A3B8]'}`}>{t}</button>
                      ))}
                    </div>
                  </div>
                  
                  <div className="space-y-1">
                    <span className="text-[11px] text-[#94A3B8] border-b border-dashed border-[#475569] pb-0.5">Trigger Price</span>
                    <div className="bg-[#1A2232] border border-[#1E293B] rounded overflow-hidden">
                      <input type="number" value={tpPrice} onChange={e => setTpPrice(e.target.value)} placeholder="Trigger Price USD" className="w-full bg-transparent px-3 py-2 text-[12px] text-[#F8FAFC] outline-none placeholder-[#475569]" />
                      <div className="flex border-t border-[#1E293B] bg-[#161D2A]">
                        {[0.25, 0.5, 1, 2].map(p => (
                          <button key={p} onClick={() => handleTpPercent(p)} className="flex-1 py-1.5 text-[11px] font-bold text-[#F8FAFC] border-r border-[#1E293B] hover:bg-[#1E293B] transition-colors">{p}%</button>
                        ))}
                        <div className="flex-1 flex items-center bg-[#0B0E14] px-1 hover:bg-[#161D2A] transition-colors focus-within:bg-[#1E293B]">
                          <input 
                            type="number"
                            placeholder={tpPrice && currentPrice ? Math.abs(((parseFloat(tpPrice) - currentPrice) / currentPrice) * 100).toFixed(2) : '0'}
                            onChange={(e) => {
                              const val = parseFloat(e.target.value);
                              if (!isNaN(val)) handleTpPercent(val);
                            }}
                            onBlur={(e) => e.target.value = ''}
                            className="w-full bg-transparent text-[11px] font-bold text-[#F8FAFC] outline-none text-center appearance-none [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                          />
                          <span className="text-[11px] text-[#64748B] font-bold pr-1">%</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                
                <div className="border-t border-dashed border-[#1E293B] my-2" />
                
                {/* Stop Loss */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[12px] font-bold text-[#F8FAFC] border-b border-dashed border-[#475569] pb-0.5">Stop Loss</span>
                    <div className="flex bg-[#0B0E14] border border-[#1E293B] rounded overflow-hidden">
                      {(['Market', 'Limit', 'Trail'] as const).map(t => (
                        <button key={t} onClick={() => setSlType(t)} className={`px-2.5 py-1.5 text-[11px] font-bold transition-colors ${slType === t ? 'bg-[#1E293B] text-[#F59E0B]' : 'text-[#64748B] hover:text-[#94A3B8]'}`}>{t}</button>
                      ))}
                    </div>
                  </div>
                  
                  <div className="space-y-1">
                    <span className="text-[11px] text-[#94A3B8] border-b border-dashed border-[#475569] pb-0.5">Trigger Price</span>
                    <div className="bg-[#1A2232] border border-[#1E293B] rounded overflow-hidden">
                      <input type="number" value={slPrice} onChange={e => setSlPrice(e.target.value)} placeholder="Trigger Price USD" className="w-full bg-transparent px-3 py-2 text-[12px] text-[#F8FAFC] outline-none placeholder-[#475569]" />
                      <div className="flex border-t border-[#1E293B] bg-[#161D2A]">
                        {[0.25, 0.5, 1, 2].map(p => (
                          <button key={p} onClick={() => handleSlPercent(p)} className="flex-1 py-1.5 text-[11px] font-bold text-[#F8FAFC] border-r border-[#1E293B] hover:bg-[#1E293B] transition-colors">{p}%</button>
                        ))}
                        <div className="flex-1 flex items-center bg-[#0B0E14] px-1 hover:bg-[#161D2A] transition-colors focus-within:bg-[#1E293B]">
                          <input 
                            type="number"
                            placeholder={slPrice && currentPrice ? Math.abs(((parseFloat(slPrice) - currentPrice) / currentPrice) * 100).toFixed(2) : '0'}
                            onChange={(e) => {
                              const val = parseFloat(e.target.value);
                              if (!isNaN(val)) handleSlPercent(val);
                            }}
                            onBlur={(e) => e.target.value = ''}
                            className="w-full bg-transparent text-[11px] font-bold text-[#F8FAFC] outline-none text-center appearance-none [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                          />
                          <span className="text-[11px] text-[#64748B] font-bold pr-1">%</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Estimates & Submit */}
            <div className="pt-2 space-y-4">
              <div className="space-y-1 border-t border-dashed border-[#1E293B] pt-3">
                <div className="flex justify-between items-center text-[12px]">
                  <span className="text-[#94A3B8] flex items-center space-x-1">
                    <span className="border-b border-dashed border-[#475569]">Funds req.</span>
                    <RefreshCw className="w-3 h-3 text-[#F59E0B]" />
                  </span>
                  <span className="font-mono font-bold text-[#F8FAFC]">{fundsRequired.toFixed(2)} USD</span>
                </div>
                <div className="flex justify-between items-center text-[12px]">
                  <span className="text-[#94A3B8]">Available Margin</span>
                  <span className="font-mono font-bold text-[#F8FAFC]">{availMargin.toFixed(2)} USD</span>
                </div>
              </div>

              <button
                onClick={handleSubmit}
                disabled={!canTradeManual || !quantity || parseFloat(quantity) <= 0}
                className={`w-full py-3 rounded-xl font-bold text-[11px] uppercase tracking-wider transition-colors flex items-center justify-center space-x-2 disabled:opacity-50 disabled:cursor-not-allowed ${
                  !canTrade
                    ? 'bg-[#334155] text-[#64748B]'
                    : isManualBlocked
                      ? 'bg-[#334155] text-[#64748B]'
                      : orderSide === 'buy'
                        ? 'bg-[#00C896] hover:bg-[#00B386] text-white'
                        : 'bg-[#F6465D] hover:bg-[#E03A4F] text-white'
                }`}
              >
                {!canTrade ? (
                  <>
                    <WifiOff className="w-3.5 h-3.5" />
                    <span>DELTA OFFLINE</span>
                  </>
                ) : isManualBlocked ? (
                  <>
                    <Lock className="w-3.5 h-3.5" />
                    <span>ALGO RUNNING</span>
                  </>
                ) : (
                  <>
                    <Send className="w-3.5 h-3.5" />
                    <span>SUBMIT {orderSide === 'buy' ? 'BUY' : 'SELL'} ORDER</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ═════════════════════════════════════════════════════
          BOTTOM: 24/7 MARKET SCANNER — EXACT FROM SCREENSHOT
          ═════════════════════════════════════════════════════ */}
      <div className="px-4 pb-4">
        <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl overflow-hidden">
          {/* Tabs */}
          <div className="flex items-center border-b border-[#1E293B] bg-[#0B0E14] overflow-x-auto">
            {[
              { id: 'scanner', label: '24/7 Market Scanner', icon: Zap },
              { id: 'risk', label: 'Risk & Leverage Calc', icon: BarChart3 },
              { id: 'positions', label: `Open Positions (${displayPositions.length})`, icon: Layers },
              { id: 'pending', label: 'Pending Orders (0)', icon: BookOpen },
              { id: 'ledger', label: 'Trade Accounting Ledger (0)', icon: FileText },
              { id: 'journal', label: 'Execution Journal (0)', icon: FileText },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`flex items-center space-x-1.5 px-4 py-2.5 text-[10px] font-bold uppercase border-b-2 transition-colors whitespace-nowrap shrink-0 ${
                  activeTab === tab.id
                    ? 'text-[#3B82F6] border-[#3B82F6] bg-[#3B82F6]/10'
                    : 'text-[#64748B] border-transparent hover:text-[#94A3B8]'
                }`}
              >
                <tab.icon className="w-3 h-3" />
                <span>{tab.label}</span>
              </button>
            ))}
          </div>

          {/* Scanner Content */}
          {activeTab === 'scanner' && (
            <div className="p-4">
              {/* Header with Global Controls */}
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center space-x-3">
                  <div className="w-10 h-10 rounded-lg bg-[#3B82F6]/20 flex items-center justify-center">
                    <BrainCircuit className="w-5 h-5 text-[#3B82F6]" />
                  </div>
                  <div>
                    <h3 className="text-[13px] font-bold text-[#F8FAFC]">24/7 Market Scanner Engine</h3>
                    <p className="text-[9px] text-[#64748B]">
                      1H Institutional Order Block Scanner · 9-Factor AI Gate (≥85%) · Individual Pair Control (Pause/Stop) · Delta Live Order Routing
                    </p>
                  </div>
                </div>

                {/* GLOBAL PAUSE / STOP / START */}
                <div className="flex items-center space-x-2">
                  {isGlobalStopped ? (
                    <button
                      onClick={() => sendControl('START_ALL')}
                      className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#00C896]/10 border border-[#00C896]/30 rounded-lg text-[10px] font-bold text-[#00C896] hover:bg-[#00C896]/20 transition-colors"
                    >
                      <Play className="w-3 h-3" />
                      <span>START ENGINE</span>
                    </button>
                  ) : (
                    <>
                      <button
                        onClick={() => sendControl(isGlobalPaused ? 'RESUME_ALL' : 'PAUSE_ALL')}
                        className={`flex items-center space-x-1.5 px-3 py-1.5 border rounded-lg text-[10px] font-bold transition-colors ${
                          isGlobalPaused
                            ? 'bg-[#00C896]/10 border-[#00C896]/30 text-[#00C896]'
                            : 'bg-[#F59E0B]/10 border-[#F59E0B]/30 text-[#F59E0B]'
                        }`}
                      >
                        {isGlobalPaused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                        <span>{isGlobalPaused ? 'RESUME ALL' : 'PAUSE ALL'}</span>
                      </button>
                      <button
                        onClick={() => sendControl('STOP_ALL')}
                        className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#F6465D]/10 border border-[#F6465D]/30 rounded-lg text-[10px] font-bold text-[#F6465D] hover:bg-[#F6465D]/20 transition-colors"
                      >
                        <Square className="w-3 h-3" />
                        <span>STOP ALL</span>
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* Stats */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
                {[
                  { label: 'TICKS PROCESSED', value: stats.ticks.toLocaleString() },
                  { label: 'SIGNALS TRIGGERED', value: stats.signals.toLocaleString() },
                  { label: 'TRADES EXECUTED', value: stats.trades.toLocaleString() },
                  { label: 'SCAN MATRIX', value: stats.matrix, color: 'text-[#3B82F6]' },
                ].map((stat) => (
                  <div key={stat.label} className="bg-[#0B0E14] border border-[#1E293B] rounded-lg p-3">
                    <div className="text-[8px] text-[#64748B] font-bold uppercase mb-1">{stat.label}</div>
                    <div className={`text-sm font-mono font-bold ${stat.color || 'text-[#F8FAFC]'}`}>{stat.value}</div>
                  </div>
                ))}
              </div>

              {/* Table */}
              <div className="border border-[#1E293B] rounded-lg overflow-x-auto">
                <table className="w-full text-left min-w-[700px]">
                  <thead>
                    <tr className="border-b border-[#1E293B] bg-[#0B0E14]">
                      {['SYMBOL', 'LIVE PRICE', 'ACTIVE OBS', 'OB WIDTH %', 'PAIR STATUS', 'AI SCORE', 'INDIVIDUAL CONTROL & ACTIONS'].map((h) => (
                        <th key={h} className="px-3 py-2 text-[8px] font-bold text-[#64748B] uppercase tracking-wider whitespace-nowrap">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#1E293B]">
                    {isLoading ? (
                      <tr>
                        <td colSpan={7} className="px-3 py-8 text-center">
                          <div className="flex items-center justify-center space-x-2">
                            <Loader2 className="w-4 h-4 text-[#3B82F6] animate-spin" />
                            <span className="text-[10px] text-[#64748B]">Loading scanner engine...</span>
                          </div>
                        </td>
                      </tr>
                    ) : pairs.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-3 py-8 text-center text-[10px] text-[#64748B]">
                          No pairs configured
                        </td>
                      </tr>
                    ) : (
                      pairs.map((pair) => {
                        const isPairPaused = pair.isPaused || pair.status === 'PAUSED';
                        const isPairStopped = !pair.isActive || pair.status === 'STOPPED';

                        return (
                          <tr key={pair.symbol} className="hover:bg-[#1E293B] transition-colors">
                            <td className="px-3 py-2.5">
                              <div className="flex items-center space-x-2">
                                <div className={`w-1.5 h-1.5 rounded-full ${
                                  isPairStopped ? 'bg-[#F6465D]' : isPairPaused ? 'bg-[#F59E0B]' : 'bg-[#00C896]'
                                }`} />
                                <span className="text-[10px] font-bold text-[#F8FAFC]">{pair.symbol}</span>
                              </div>
                            </td>
                            <td className="px-3 py-2.5">
                              <div className="text-[10px] font-mono font-bold text-[#F8FAFC]">
                                ${pair.livePrice.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                              </div>
                              <div className={`text-[9px] font-mono ${
                                pair.priceChange24h >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
                              }`}>
                                {pair.priceChange24h >= 0 ? '+' : ''}{pair.priceChange24h.toFixed(2)}%
                              </div>
                            </td>
                            <td className="px-3 py-2.5 text-[10px] text-[#94A3B8]">
                              {pair.activeOBs} Zones
                            </td>
                            <td className="px-3 py-2.5 text-[10px] font-mono text-[#94A3B8]">
                              {pair.obWidthPct ? `${pair.obWidthPct.toFixed(2)}%` : '---'}
                            </td>
                            <td className="px-3 py-2.5">
                              <span className={`flex items-center space-x-1 text-[9px] font-bold ${
                                isPairStopped ? 'text-[#F6465D]' : isPairPaused ? 'text-[#F59E0B]' : 'text-[#3B82F6]'
                              }`}>
                                <div className={`w-1 h-1 rounded-full ${
                                  isPairStopped ? 'bg-[#F6465D]' : isPairPaused ? 'bg-[#F59E0B]' : 'bg-[#3B82F6]'
                                }`} />
                                <span>{pair.status}</span>
                              </span>
                            </td>
                            <td className="px-3 py-2.5">
                              {pair.aiScore ? (
                                <span className={`text-[10px] font-mono font-bold ${
                                  pair.aiScore >= 85 ? 'text-[#00C896]' : 'text-[#F59E0B]'
                                }`}>
                                  {pair.aiScore.toFixed(0)}
                                </span>
                              ) : (
                                <span className="text-[10px] font-mono text-[#64748B]">---</span>
                              )}
                            </td>
                            <td className="px-3 py-2.5">
                              <div className="flex items-center space-x-1">
                                {isPairStopped ? (
                                  <button
                                    onClick={() => sendControl('RESUME', pair.symbol)}
                                    className="px-2 py-1 bg-[#00C896]/10 border border-[#00C896]/30 rounded text-[8px] font-bold text-[#00C896] hover:bg-[#00C896]/20 transition-colors flex items-center space-x-1"
                                  >
                                    <Play className="w-2.5 h-2.5" />
                                    <span>Start</span>
                                  </button>
                                ) : (
                                  <>
                                    <button
                                      onClick={() => sendControl(isPairPaused ? 'RESUME' : 'PAUSE', pair.symbol)}
                                      className={`px-2 py-1 border rounded text-[8px] font-bold transition-colors flex items-center space-x-1 ${
                                        isPairPaused
                                          ? 'bg-[#00C896]/10 border-[#00C896]/30 text-[#00C896]'
                                          : 'bg-[#F59E0B]/10 border-[#F59E0B]/30 text-[#F59E0B]'
                                      }`}
                                    >
                                      {isPairPaused ? <Play className="w-2.5 h-2.5" /> : <Pause className="w-2.5 h-2.5" />}
                                      <span>{isPairPaused ? 'Resume' : 'Pause'}</span>
                                    </button>
                                    <button
                                      onClick={() => sendControl('STOP', pair.symbol)}
                                      className="px-2 py-1 bg-[#F6465D]/10 border border-[#F6465D]/30 rounded text-[8px] font-bold text-[#F6465D] hover:bg-[#F6465D]/20 transition-colors flex items-center space-x-1"
                                    >
                                      <Square className="w-2.5 h-2.5" />
                                      <span>Stop</span>
                                    </button>
                                  </>
                                )}
                                <button
                                  onClick={() => sendControl('INSPECT', pair.symbol)}
                                  className="px-2 py-1 bg-[#3B82F6]/10 border border-[#3B82F6]/30 rounded text-[8px] font-bold text-[#3B82F6] hover:bg-[#3B82F6]/20 transition-colors"
                                >
                                  Inspect AI
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {activeTab === 'positions' && (
            <div className="p-4">
              {displayPositions.length === 0 ? (
                <div className="text-center py-12">
                  <Layers className="w-8 h-8 text-[#334155] mx-auto mb-3" />
                  <p className="text-[11px] text-[#64748B]">No open positions</p>
                  <p className="text-[9px] text-[#475569] mt-1">Use the order form to open a trade</p>
                </div>
              ) : (
                <div className="space-y-2 overflow-x-auto w-full">
                  {displayPositions.map((pos: any, i: number) => {
                    const side = safeStr(pos?.side, 'LONG').toUpperCase();
                    const isLong = side === 'BUY' || side === 'LONG';
                    const pnl = safeNum(pos?.unrealized_pnl);
                    const margin = safeNum(pos?.margin_amount);
                    const roe = margin > 0 ? (pnl / margin) * 100 : 0;
                    return (
                      <div key={i} className="bg-[#0B0E14] border border-[#1E293B] rounded-lg p-3 flex items-center justify-between min-w-[500px]">
                        <div className="flex items-center space-x-4">
                          <span className="text-[10px] font-bold text-[#F8FAFC]">{safeStr(pos?.product_symbol, safeSymbol)}</span>
                          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${isLong ? 'bg-[#00C896]/20 text-[#00C896]' : 'bg-[#F6465D]/20 text-[#F6465D]'}`}>
                            {isLong ? 'LONG' : 'SHORT'}
                          </span>
                          <span className="text-[10px] font-mono text-[#94A3B8]">{fmtQty(pos?.size)}</span>
                          <span className="text-[10px] font-mono text-[#94A3B8]">${fmtPrice(pos?.entry_price)}</span>
                        </div>
                        <div className="flex items-center space-x-4">
                          <span className={`text-[10px] font-mono font-bold ${pnl >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                            {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                          </span>
                          <span className={`text-[10px] font-mono font-bold ${roe >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                            {roe.toFixed(2)}%
                          </span>
                          <button className="p-1 hover:bg-[#F6465D]/20 rounded text-[#F6465D]">
                            <Square className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {activeTab !== 'scanner' && activeTab !== 'positions' && (
            <div className="p-8 text-center">
              <p className="text-[11px] text-[#64748B]">No data available for this tab.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export const LiveTradingPage: React.FC = () => (
  <ErrorBoundary>
    <LiveTradingPageInner />
  </ErrorBoundary>
);
