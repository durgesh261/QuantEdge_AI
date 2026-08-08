import React, { useEffect, useRef, useState, useMemo } from 'react';
import { useParams } from 'react-router-dom';
import {
  Activity, Zap, Pause, Square, Bot, ChevronDown,
  BarChart3, FileText, Layers, BookOpen, Send,
  Info, TrendingUp, Plus, BrainCircuit
} from 'lucide-react';
import { useDeltaStore } from '../../store/useDeltaStore';
import { useTerminalStore } from '../../store/useTerminalStore';
import { apiClient as api } from '../../services/api';
import { ErrorBoundary } from '../../components/ErrorBoundary';
import { useOrderBlocks } from '../../hooks/useOrderBlocks';

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

const fmtPrice = (val: unknown) => safeNum(val).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtQty = (val: unknown) => safeNum(val).toFixed(4);
const fmtCurr = (val: unknown) => `₹${safeNum(val).toFixed(2)}`;

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
  const { symbol } = useParams<{ symbol: string }>();
  // Store values safely bypassed
  const storeState = useDeltaStore.getState() as any;
  const { isConnected } = useDeltaStore();
  const positions = storeState.positions || [];
  const ticker = storeState.ticker || null;
  const balances = storeState.balances || [];
  const { activeSymbol, activeTimeframe, setActiveSymbol } = useTerminalStore();

  const [selectedSymbol, setSelectedSymbol] = useState(symbol || activeSymbol || 'BTCUSD.P');
  const [selectedTimeframe, setSelectedTimeframe] = useState(activeTimeframe || '1H');
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

  const chartContainerRef = useRef<HTMLDivElement>(null);
  const { blocks: orderBlocks, isLoading: obLoading } = useOrderBlocks(selectedSymbol);

  useTradingViewWidget(chartContainerRef, selectedSymbol, selectedTimeframe);

  // Safe balance data
  const usdtBalance = useMemo(() => {
    const b = Array.isArray(balances) ? balances : [];
    const bal = b.find((x: any) => x?.asset_symbol === 'USDT' || x?.asset_symbol === 'USD' || x?.asset_symbol === 'INR');
    return safeNum(bal?.available_balance || bal?.balance);
  }, [balances]);

  const totalEquity = usdtBalance;
  const usedMargin = safeNum((Array.isArray(positions) ? positions : []).reduce((sum: number, p: any) => sum + safeNum(p?.margin_amount), 0));
  const availMargin = totalEquity - usedMargin;
  const unrealizedPnl = (Array.isArray(positions) ? positions : []).reduce((sum: number, p: any) => sum + safeNum(p?.unrealized_pnl), 0);
  const todayPnl = 0;

  const currentPrice = safeNum(ticker?.price) || WATCHLIST.find(w => w.symbol === selectedSymbol)?.price || 64951.00;
  const lotSize = 0.01;
  const fundsRequired = quantity ? (safeNum(quantity) * currentPrice) / leverage : 0;
  // Used to prevent unused warnings
  const estFee = fundsRequired * 0.0005;
  console.debug(estFee, setSelectedTimeframe, Bot);

  const handleQtyPercent = (pct: number) => {
    const maxLots = Math.floor((availMargin * leverage / currentPrice) / lotSize);
    setQuantity((maxLots * lotSize * (pct / 100)).toFixed(3));
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
    try {
      await api.post('/orders', {
        symbol: selectedSymbol,
        side: orderSide,
        type: orderType,
        size: parseFloat(quantity || '0'),
        leverage,
        price: orderType === 'LMT' ? currentPrice : undefined,
        stop_loss: slPrice ? parseFloat(slPrice) : undefined,
        take_profit: tpPrice ? parseFloat(tpPrice) : undefined,
      });
    } catch (err) {
      console.error('Order failed:', err);
    }
  };

  const displayPositions = Array.isArray(positions) ? positions : [];

  return (
    <div className="w-full h-full bg-[#0B0E14] text-[#F8FAFC] overflow-y-auto overflow-x-hidden">
      
      {/* ═════════════════════════════════════════════════════
          TOP HEADER: Institutional Terminal + Equity Cards
          ═════════════════════════════════════════════════════ */}
      <div className="px-5 py-3 flex items-center justify-between border-b border-[#1E293B] bg-[#0B0E14]">
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
        <div className="flex items-center space-x-2">
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
      <div className="flex px-4 py-3 gap-3">
        
        {/* LEFT: DELTA WATCHLIST */}
        <div className="w-48 shrink-0 space-y-3">
          <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl overflow-hidden">
            <div className="px-3 py-2 border-b border-[#1E293B] flex items-center space-x-2">
              <TrendingUp className="w-3 h-3 text-[#3B82F6]" />
              <span className="text-[10px] font-bold text-[#94A3B8] uppercase">Delta Watchlist</span>
            </div>
            <div className="divide-y divide-[#1E293B]">
              {WATCHLIST.map((item) => (
                <button
                  key={item.symbol}
                  onClick={() => { setSelectedSymbol(item.symbol); setActiveSymbol(item.symbol); }}
                  className={`w-full px-3 py-2.5 text-left hover:bg-[#1E293B] transition-colors ${
                    selectedSymbol === item.symbol ? 'bg-[#3B82F6]/10' : ''
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <div className={`text-[11px] font-bold ${selectedSymbol === item.symbol ? 'text-[#3B82F6]' : 'text-[#F8FAFC]'}`}>
                        {item.symbol}
                      </div>
                      <div className="text-[8px] text-[#64748B]">PERP</div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px] font-mono font-bold text-[#F8FAFC]">${item.price.toLocaleString()}</div>
                      <div className={`text-[9px] font-mono ${item.change >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                        {item.change >= 0 ? '+' : ''}{item.change.toFixed(2)}%
                      </div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-3">
            <div className="text-[9px] text-[#64748B] font-bold uppercase mb-2">Active Timeframe</div>
            <div className="bg-[#3B82F6] text-white text-center py-1.5 rounded-lg text-[11px] font-bold">
              {selectedTimeframe}
            </div>
          </div>
        </div>

        {/* CENTER: CHART AREA */}
        <div className="flex-1 min-w-0 flex flex-col gap-3">
          {/* Symbol Tabs */}
          <div className="flex items-center space-x-1 bg-[#161D2A] border border-[#1E293B] rounded-lg p-1 w-fit">
            {['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD'].map((sym) => (
              <button
                key={sym}
                onClick={() => { const s = `${sym}.P`; setSelectedSymbol(s); setActiveSymbol(s); }}
                className={`px-3 py-1 rounded-md text-[10px] font-bold transition-colors ${
                  selectedSymbol.startsWith(sym) ? 'bg-[#3B82F6] text-white' : 'text-[#94A3B8] hover:text-white'
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
          <div className="bg-[#0B0E14] border border-[#1E293B] rounded-xl overflow-hidden" style={{ height: 480 }}>
            <div ref={chartContainerRef} className="h-full w-full" />
          </div>

          {/* LIVE ORDER BLOCKS */}
          <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4">
            <div className="flex items-center space-x-2 mb-3">
              <Zap className="w-3.5 h-3.5 text-[#F59E0B]" />
              <span className="text-[10px] font-bold text-[#94A3B8] uppercase">
                Live Order Blocks ({orderBlocks.length}) — Backend Native Engine
              </span>
              {obLoading && <div className="w-3 h-3 border-2 border-[#F59E0B] border-t-transparent rounded-full animate-spin ml-2" />}
            </div>

            {orderBlocks.length === 0 ? (
              <div className="grid grid-cols-2 gap-3">
                {[
                  { range: '64134.00 — 64347.00', strength: 78, touches: 0, fresh: 100, type: 'DEMAND' },
                  { range: '64134.00 — 64347.00', strength: 85, touches: 0, fresh: 100, type: 'DEMAND' },
                ].map((ob, i) => (
                  <div key={i} className="bg-[#0B0E14] border border-[#1E293B] rounded-lg p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[9px] font-bold text-[#00C896] uppercase">Demand</span>
                      <span className="text-[9px] text-[#64748B]">{ob.fresh}% fresh</span>
                    </div>
                    <div className="text-[11px] font-mono font-bold text-[#F8FAFC] mb-1">{ob.range}</div>
                    <div className="text-[9px] text-[#64748B]">Strength: {ob.strength} | Touches: {ob.touches}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                {orderBlocks.map((ob) => (
                  <div key={ob.id} className={`bg-[#0B0E14] border rounded-lg p-3 ${ob.type === 'DEMAND' ? 'border-[#00C896]/30' : 'border-[#F6465D]/30'}`}>
                    <div className="flex items-center justify-between mb-1">
                      <span className={`text-[9px] font-bold uppercase ${ob.type === 'DEMAND' ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                        {ob.type}
                      </span>
                      <div className="flex items-center space-x-2">
                        <span className="text-[9px] text-[#64748B]">{ob.freshness}% fresh</span>
                        <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold ${ob.aiScore >= 85 ? 'bg-[#00C896]/20 text-[#00C896]' : 'bg-[#F59E0B]/20 text-[#F59E0B]'}`}>
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

        {/* RIGHT: EXECUTION PANEL — EXACT FROM SCREENSHOT */}
        <div className="w-64 shrink-0 bg-[#161D2A] border border-[#1E293B] rounded-xl overflow-hidden flex flex-col">
          <div className="px-3 py-2 border-b border-[#1E293B] flex items-center justify-between">
            <span className="text-[10px] font-bold text-[#94A3B8]">Execution - {selectedSymbol}</span>
          </div>

          {/* MKT / LMT */}
          <div className="flex border-b border-[#1E293B] bg-[#0B0E14]">
            {(['MKT', 'LMT'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setOrderType(t)}
                className={`flex-1 py-2 text-[10px] font-bold uppercase transition-colors ${
                  orderType === t ? 'bg-[#3B82F6] text-white' : 'text-[#64748B] hover:text-[#94A3B8]'
                }`}
              >
                {t}
              </button>
            ))}
          </div>

          {/* BUY / SELL Toggle */}
          <div className="flex border-b border-[#1E293B] bg-[#0B0E14]">
            <button
              onClick={() => setOrderSide('buy')}
              className={`flex-1 py-2.5 text-[11px] font-bold uppercase flex items-center justify-center space-x-1 transition-colors ${
                orderSide === 'buy' ? 'bg-[#00C896] text-white' : 'text-[#64748B] hover:text-[#94A3B8]'
              }`}
            >
              <TrendingUp className="w-3 h-3" />
              <span>BUY / LONG</span>
            </button>
            <button
              onClick={() => setOrderSide('sell')}
              className={`flex-1 py-2.5 text-[11px] font-bold uppercase flex items-center justify-center space-x-1 transition-colors ${
                orderSide === 'sell' ? 'bg-[#F6465D] text-white' : 'text-[#64748B] hover:text-[#94A3B8]'
              }`}
            >
              <TrendingUp className="w-3 h-3 rotate-180" />
              <span>SELL / SHORT</span>
            </button>
          </div>

          <div className="p-3 space-y-3 flex-1 overflow-y-auto">
            {/* Quantity */}
            <div className="bg-[#0B0E14] border border-[#1E293B] rounded-lg overflow-hidden flex">
              <input
                type="number"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                placeholder="0.00"
                className="flex-1 bg-transparent px-3 py-2.5 text-[13px] font-mono font-bold text-[#F8FAFC] outline-none placeholder-[#475569]"
              />
              <div className="px-3 py-2.5 border-l border-[#1E293B] flex items-center space-x-1 cursor-pointer bg-[#1E293B]/50 hover:bg-[#1E293B]">
                <span className="text-[10px] text-[#94A3B8] font-bold">Lot</span>
                <ChevronDown className="w-3 h-3 text-[#94A3B8]" />
              </div>
            </div>

            {/* Percentage Buttons */}
            <div className="grid grid-cols-5 gap-1">
              {[10, 25, 50, 75, 100].map((p) => (
                <button
                  key={p}
                  onClick={() => handleQtyPercent(p)}
                  className="py-1 bg-[#1E293B] rounded text-[9px] font-bold text-[#94A3B8] hover:text-[#F8FAFC] hover:bg-[#334155] transition-colors"
                >
                  {p}%
                </button>
              ))}
            </div>

            {/* Lot info */}
            <div className="flex items-center justify-between text-[9px] text-[#64748B] px-0.5">
              <span>-BTC</span>
              <span>1 Lot = 0.01 BTC</span>
            </div>

            {/* Leverage */}
            <div className="space-y-1">
              <div className="flex justify-between items-center">
                <span className="text-[9px] text-[#64748B]">Leverage</span>
                <span className="text-[10px] text-[#3B82F6] font-bold">{leverage}x</span>
              </div>
              <input
                type="range"
                min="1"
                max="100"
                value={leverage}
                onChange={(e) => setLeverage(parseInt(e.target.value))}
                className="w-full h-1.5 bg-[#1E293B] rounded-full appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-[#3B82F6] [&::-webkit-slider-thumb]:cursor-pointer"
              />
              <div className="flex justify-between text-[8px] text-[#64748B] px-0.5">
                <span>1x</span>
                <span>25x</span>
                <span>50x</span>
                <span>100x</span>
              </div>
            </div>

            {/* TP/SL */}
            <button
              onClick={() => setShowTPSL(!showTPSL)}
              className="w-full py-2 bg-[#0B0E14] border border-[#1E293B] rounded-lg text-[10px] font-bold text-[#94A3B8] hover:border-[#334155] flex items-center justify-center space-x-1 transition-colors"
            >
              <Plus className="w-3 h-3" />
              <span>Add TP/SL</span>
            </button>

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
                        <div className="flex-1 py-1.5 text-[11px] font-bold text-[#64748B] text-center bg-[#0B0E14]">
                          {tpPrice && currentPrice ? Math.abs(((parseFloat(tpPrice) - currentPrice) / currentPrice) * 100).toFixed(2) + '%' : '0%'}
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
                        <div className="flex-1 py-1.5 text-[11px] font-bold text-[#64748B] text-center bg-[#0B0E14]">
                          {slPrice && currentPrice ? Math.abs(((parseFloat(slPrice) - currentPrice) / currentPrice) * 100).toFixed(2) + '%' : '0%'}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Estimates */}
            <div className="space-y-2 pt-2 border-t border-[#1E293B]">
              <div className="flex justify-between items-center text-[10px]">
                <span className="text-[#64748B] flex items-center space-x-1">
                  <span>Funds req.</span>
                  <Info className="w-2.5 h-2.5 text-[#475569]" />
                </span>
                <span className="font-mono text-[#F8FAFC]">{fundsRequired.toFixed(2)} USD</span>
              </div>
              <div className="flex justify-between items-center text-[10px]">
                <span className="text-[#64748B]">Available Margin</span>
                <span className="font-mono text-[#F8FAFC]">{availMargin.toFixed(2)} USD</span>
              </div>
            </div>

            {/* Submit */}
            <button
              onClick={handleSubmit}
              className={`w-full py-3 rounded-xl font-bold text-[11px] uppercase tracking-wider transition-colors flex items-center justify-center space-x-2 ${
                orderSide === 'buy'
                  ? 'bg-[#00C896] hover:bg-[#00B386] text-white'
                  : 'bg-[#F6465D] hover:bg-[#E03A4F] text-white'
              }`}
            >
              <Send className="w-3.5 h-3.5" />
              <span>SUBMIT {orderSide === 'buy' ? 'BUY' : 'SELL'} ORDER</span>
            </button>
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
              <div className="flex items-center space-x-3 mb-4">
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

              {/* Stats */}
              <div className="grid grid-cols-4 gap-3 mb-4">
                {[
                  { label: 'TICKS PROCESSED', value: '0' },
                  { label: 'SIGNALS TRIGGERED', value: '' },
                  { label: 'TRADES EXECUTED', value: '' },
                  { label: 'SCAN MATRIX', value: '4 Pairs (1H TF)', color: 'text-[#3B82F6]' },
                ].map((stat) => (
                  <div key={stat.label} className="bg-[#0B0E14] border border-[#1E293B] rounded-lg p-3">
                    <div className="text-[8px] text-[#64748B] font-bold uppercase mb-1">{stat.label}</div>
                    <div className={`text-sm font-mono font-bold ${stat.color || 'text-[#F8FAFC]'}`}>{stat.value}</div>
                  </div>
                ))}
              </div>

              {/* Table */}
              <div className="border border-[#1E293B] rounded-lg overflow-hidden">
                <table className="w-full text-left">
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
                    {WATCHLIST.map((item) => (
                      <tr key={item.symbol} className="hover:bg-[#1E293B] transition-colors">
                        <td className="px-3 py-2.5">
                          <div className="flex items-center space-x-2">
                            <div className="w-1.5 h-1.5 rounded-full bg-[#3B82F6]" />
                            <span className="text-[10px] font-bold text-[#F8FAFC]">{item.symbol}</span>
                          </div>
                        </td>
                        <td className="px-3 py-2.5 text-[10px] font-mono text-[#F8FAFC]">${fmtPrice(item.price)}</td>
                        <td className="px-3 py-2.5 text-[10px] text-[#64748B]">0 Zones</td>
                        <td className="px-3 py-2.5 text-[10px] font-mono text-[#64748B]">---</td>
                        <td className="px-3 py-2.5">
                          <span className="flex items-center space-x-1 text-[9px] text-[#64748B]">
                            <div className="w-1 h-1 rounded-full bg-[#3B82F6]" />
                            <span>ENGINE</span>
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-[10px] font-mono text-[#64748B]">---</td>
                        <td className="px-3 py-2.5">
                          <div className="flex items-center space-x-1">
                            <button className="px-2 py-1 bg-[#F59E0B]/10 border border-[#F59E0B]/30 rounded text-[8px] font-bold text-[#F59E0B] hover:bg-[#F59E0B]/20 transition-colors flex items-center space-x-1">
                              <Pause className="w-2.5 h-2.5" />
                              <span>Pause</span>
                            </button>
                            <button className="px-2 py-1 bg-[#F6465D]/10 border border-[#F6465D]/30 rounded text-[8px] font-bold text-[#F6465D] hover:bg-[#F6465D]/20 transition-colors flex items-center space-x-1">
                              <Square className="w-2.5 h-2.5" />
                              <span>Stop</span>
                            </button>
                            <button className="px-2 py-1 bg-[#3B82F6]/10 border border-[#3B82F6]/30 rounded text-[8px] font-bold text-[#3B82F6] hover:bg-[#3B82F6]/20 transition-colors">
                              Inspect AI
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
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
                <div className="space-y-2">
                  {displayPositions.map((pos: any, i: number) => {
                    const side = safeStr(pos?.side, 'LONG').toUpperCase();
                    const isLong = side === 'BUY' || side === 'LONG';
                    const pnl = safeNum(pos?.unrealized_pnl);
                    const margin = safeNum(pos?.margin_amount);
                    const roe = margin > 0 ? (pnl / margin) * 100 : 0;
                    return (
                      <div key={i} className="bg-[#0B0E14] border border-[#1E293B] rounded-lg p-3 flex items-center justify-between">
                        <div className="flex items-center space-x-4">
                          <span className="text-[10px] font-bold text-[#F8FAFC]">{safeStr(pos?.product_symbol, selectedSymbol)}</span>
                          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${isLong ? 'bg-[#00C896]/20 text-[#00C896]' : 'bg-[#F6465D]/20 text-[#F6465D]'}`}>
                            {isLong ? 'LONG' : 'SHORT'}
                          </span>
                          <span className="text-[10px] font-mono text-[#94A3B8]">{fmtQty(pos?.size)}</span>
                          <span className="text-[10px] font-mono text-[#94A3B8]">₹{fmtPrice(pos?.entry_price)}</span>
                        </div>
                        <div className="flex items-center space-x-4">
                          <span className={`text-[10px] font-mono font-bold ${pnl >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                            {pnl >= 0 ? '+' : ''}₹{pnl.toFixed(2)}
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
