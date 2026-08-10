import React, { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { strategyApi, indicatorApi } from '../../services/api';
import { useTerminalStore } from '../../store/useTerminalStore';
import { Maximize2, Minimize2, TrendingUp, TrendingDown } from 'lucide-react';
import { useOrderBlocksChart } from '../../hooks/useOrderBlocksChart';
import { useOrderBlocks } from '../../hooks/useOrderBlocks';
import { OrderBlockDto } from '@algoapp/shared';

// Strategy §2: ONLY these 4 pairs
const DELTA_SYMBOL_MAP: Record<string, string> = {
  'BTCUSD.P': 'DELTAIN:BTCUSD.P',
  'ETHUSD.P': 'DELTAIN:ETHUSD.P',
  'SOLUSD.P': 'DELTAIN:SOLUSD.P',
  'XRPUSD.P': 'DELTAIN:XRPUSD.P',
};

const toDeltaSymbol = (symbol: string): string => {
  const key = symbol.toUpperCase();
  return DELTA_SYMBOL_MAP[key] ?? `DELTAIN:${key}`;
};

const ALLOWED_SYMBOLS = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];

interface TradingViewChartWorkspaceProps {
  initialSymbol?: string;
}

export const TradingViewChartWorkspace: React.FC<TradingViewChartWorkspaceProps> = ({
  initialSymbol = 'BTCUSD.P',
}) => {
  const { activeSymbol, setActiveSymbol } = useTerminalStore();
  const currentSymbol = ALLOWED_SYMBOLS.includes(activeSymbol || '') 
    ? (activeSymbol || initialSymbol) 
    : 'BTCUSD.P';

  const widgetContainerRef = useRef<HTMLDivElement>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // ── TradingView Advanced Chart Widget (visualization ONLY per Strategy §5) ──
  useEffect(() => {
    const container = widgetContainerRef.current;
    if (!container) return;
    container.innerHTML = '';

    const script = document.createElement('script');
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.type = 'text/javascript';
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: toDeltaSymbol(currentSymbol),
      interval: '60', // Strategy §8: ONLY 1H
      timezone: 'Asia/Kolkata',
      theme: 'dark',
      style: '1',
      locale: 'en',
      toolbar_bg: '#0B0E14',
      withdateranges: false, // No timeframe switching allowed
      hide_side_toolbar: false,
      allow_symbol_change: false, // Strategy §2: Only 4 pairs
      save_image: true,
      details: true,
      hotlist: false,
      calendar: false,
      support_host: 'https://www.tradingview.com',
    });

    container.appendChild(script);
    return () => { container.innerHTML = ''; };
  }, [currentSymbol]);

  // ── Live Zones & Signals from BACKEND (not TradingView) ──
  const { data: signalsData } = useQuery({
    queryKey: ['signals', currentSymbol],
    queryFn: () => strategyApi.getSignals(),
    staleTime: 30_000,
    refetchInterval: 10_000,
  });

  const { data: indicatorRes } = useQuery({
    queryKey: ['indicator-engine', currentSymbol],
    queryFn: () => indicatorApi.evaluate(currentSymbol, '1H'),
    refetchInterval: 15_000,
    staleTime: 10_000,
  });

  const indicators = indicatorRes?.data;
  const marketStructure = indicators?.marketStructure;

  const { activeBullishOBs, activeBearishOBs, mitigatedOBs, loading: obsLoading } = useOrderBlocksChart(currentSymbol);
  const { blocks: rawScannerBlocks } = useOrderBlocks(currentSymbol);

  const fallbackOBs: OrderBlockDto[] = (rawScannerBlocks || []).map((b, idx) => {
    const isBull = b.type === 'DEMAND' || (b.type as string) === 'BULLISH';
    const low = b.priceLow || (b as any).lowerPrice || 0;
    const high = b.priceHigh || (b as any).upperPrice || 0;
    const rawWidth = Math.max(0.0001, high - low);
    const widthPct = low > 0 ? Number(((rawWidth / Math.max(0.0001, high)) * 100).toFixed(3)) : 0.25;

    // Entry: if width <= 0.6% enter at first edge; otherwise 25% inside
    const entryPrice = isBull
      ? (widthPct <= 0.6 ? high : high - 0.25 * rawWidth)
      : (widthPct <= 0.6 ? low  : low  + 0.25 * rawWidth);
    const stopLossPrice = isBull ? low : high;

    // Rule: 35% account risk → leverage = 35 / slDist%, capped at 100x
    const slDistPct = Math.max(0.01, Math.abs(entryPrice - stopLossPrice) / Math.max(0.0001, entryPrice) * 100);
    const calculatedLeverage = Math.min(100, Math.max(1, Math.round(35 / slDistPct)));

    // TP: 60% account growth → tpDist% = 60 / leverage
    const tpDistPct = 60 / calculatedLeverage;
    const takeProfitPrice = isBull
      ? entryPrice * (1 + tpDistPct / 100)
      : entryPrice * (1 - tpDistPct / 100);

    return {
      id: b.id || `ob-${b.symbol}-${idx}`,
      symbol: b.symbol,
      timeframe: '1H',
      type: isBull ? 'BULLISH' : 'BEARISH',
      upperPrice: high,
      lowerPrice: low,
      widthPercent: widthPct,
      entryPrice: Number(entryPrice.toFixed(4)),
      stopLossPrice: Number(stopLossPrice.toFixed(4)),
      takeProfitPrice: Number(takeProfitPrice.toFixed(4)),
      calculatedLeverage,
      baseCandleIndex: 0,
      breakCandleIndex: 0,
      isMitigated: false,
      isInvalidated: false,
      isUsed: false,
      touchCount: b.touches || 0,
      source: 'SMC',
      createdAt: b.createdAt || new Date().toISOString(),
    };
  });

  const primaryOBs = [...activeBullishOBs, ...activeBearishOBs, ...mitigatedOBs];
  const allDisplayOBs = (primaryOBs.length > 0 ? primaryOBs : fallbackOBs).sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''));

  const latestSignal = signalsData?.data?.filter((s) => s.symbol === currentSymbol)[0] ?? null;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: isFullscreen ? '100vh' : '100%',
        minHeight: isFullscreen ? '100vh' : '360px',
      }}
      className={`bg-[#0B0E14] border border-[#1E293B] rounded-xl overflow-hidden font-mono text-xs ${
        isFullscreen ? 'fixed inset-0 z-50 rounded-none border-none' : 'relative'
      }`}
    >
      {/* ── Toolbar ── */}
      <div className="h-9 bg-[#0E121A] border-b border-[#1E293B] px-3 flex items-center justify-between shrink-0 overflow-x-auto no-scrollbar gap-2 whitespace-nowrap">
        <div className="flex items-center space-x-2.5 shrink-0">
          {/* Symbol Switcher — ONLY 4 pairs */}
          <div className="flex items-center bg-[#161D2A] border border-[#1E293B] rounded p-0.5 space-x-0.5">
            {ALLOWED_SYMBOLS.map((sym) => (
              <button
                key={sym}
                onClick={() => setActiveSymbol(sym)}
                className={`px-2.5 py-0.5 rounded text-[10px] font-semibold transition-colors ${
                  currentSymbol === sym
                    ? 'bg-[#3B82F6] text-white'
                    : 'text-[#94A3B8] hover:text-white'
                }`}
              >
                {sym.replace('.P', '')}
              </button>
            ))}
          </div>

          <div className="h-4 w-px bg-[#1E293B] shrink-0" />

          {/* 1H Badge — locked per strategy */}
          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-[#3B82F6]/20 text-[#3B82F6] border border-[#3B82F6]/30">
            1H ONLY
          </span>
        </div>

        <div className="flex items-center space-x-2 shrink-0">
          {marketStructure && (
            <div className={`flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-bold border shrink-0 ${
              marketStructure.trend === 'BULLISH'
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                : 'bg-red-500/10 border-red-500/30 text-red-400'
            }`}>
              {marketStructure.trend === 'BULLISH' ? (
                <TrendingUp className="w-3 h-3 text-emerald-400" />
              ) : (
                <TrendingDown className="w-3 h-3 text-red-400" />
              )}
              <span>SMC {marketStructure.trend}</span>
            </div>
          )}

          {latestSignal && latestSignal.outcome !== 'WAIT' && (
            <div className={`px-2 py-0.5 rounded text-[10px] font-bold border shrink-0 ${
              latestSignal.outcome === 'BUY'
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                : 'bg-red-500/10 border-red-500/30 text-red-400'
            }`}>
              ⚡ {latestSignal.outcome} {latestSignal.confidenceScore?.toFixed(0)}%
            </div>
          )}

          <div className="h-4 w-px bg-[#1E293B] shrink-0" />

          <button
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="text-[#94A3B8] hover:text-white transition-colors"
          >
            {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* ── Chart Widget ── */}
      <div className="flex-1 relative min-h-0">
        <div ref={widgetContainerRef} className="absolute inset-0" />
      </div>

      {/* ── Zone Overlay Panel ── */}
      <div className="h-32 bg-[#0E121A] border-t border-[#1E293B] px-3 py-2 overflow-y-auto shrink-0">
        <div className="text-[10px] text-[#94A3B8] uppercase font-bold mb-1.5 flex justify-between">
          <span>Live Order Blocks ({allDisplayOBs.length}) — Backend Native Engine</span>
          {obsLoading && <span className="text-emerald-500 animate-pulse">Syncing...</span>}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {allDisplayOBs.map((ob) => {
            const isBullish = ob.type === 'BULLISH' || (ob.type as string) === 'DEMAND';
            const isActive = !ob.isMitigated && !ob.isUsed;
            const low = Number(ob.lowerPrice ?? (ob as any).priceLow ?? (ob as any).low ?? 0);
            const high = Number(ob.upperPrice ?? (ob as any).priceHigh ?? (ob as any).high ?? 0);
            const entry = Number(ob.entryPrice ?? (isBullish ? high : low));
            const stop = Number(ob.stopLossPrice ?? (isBullish ? low * 0.995 : high * 1.005));
            const tp = Number(ob.takeProfitPrice ?? (isBullish ? high * 1.02 : low * 0.98));
            const slDistPct = entry > 0 ? Math.max(0.01, Math.abs(entry - stop) / entry * 100) : 1;
            const leverage = ob.calculatedLeverage ?? (ob as any).leverage ?? Math.min(100, Math.max(1, Math.round(35 / slDistPct)));
            const widthPct = ob.widthPercent ?? (low > 0 ? Number((((high - low) / high) * 100).toFixed(2)) : 0);

            const bgClass = !isActive 
              ? 'bg-gray-500/10 border-gray-500/30 opacity-60' 
              : isBullish 
                ? 'bg-emerald-500/10 border-emerald-500/30' 
                : 'bg-red-500/10 border-red-500/30';
            
            return (
              <div key={ob.id} className={`p-2 rounded border text-[10px] ${bgClass}`}>
                <div className="flex justify-between items-center mb-1">
                  <div className="flex items-center space-x-1.5">
                    <span className={`font-bold ${!isActive ? 'text-gray-400' : isBullish ? 'text-emerald-400' : 'text-red-400'}`}>
                      {isBullish ? 'DEMAND' : 'SUPPLY'} OB
                    </span>
                    <span className="bg-[#1E293B] text-[#94A3B8] px-1 rounded text-[9px]">{ob.source || 'SMC'}</span>
                  </div>
                  {!isActive && (
                    <span className="text-amber-500 font-semibold">{ob.isUsed ? 'USED' : 'MITIGATED'}</span>
                  )}
                  {isActive && (
                    <span className="text-[#94A3B8] font-semibold">{widthPct}% wide</span>
                  )}
                </div>
                
                <div className="font-mono-tabular flex justify-between text-[#94A3B8] mb-0.5">
                  <span>Zone: ${low.toFixed(2)} - ${high.toFixed(2)}</span>
                  <span>Leverage: {leverage}x</span>
                </div>
                
                <div className="font-mono-tabular grid grid-cols-3 gap-1 mt-1.5 pt-1.5 border-t border-[#1E293B]/50">
                  <div>
                    <div className="text-[9px] text-[#94A3B8]">ENTRY</div>
                    <div className={isBullish ? 'text-emerald-300' : 'text-red-300'}>${entry.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className="text-[9px] text-[#94A3B8]">STOP</div>
                    <div className="text-rose-400">${stop.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className="text-[9px] text-[#94A3B8]">TARGET</div>
                    <div className="text-emerald-400">${tp.toFixed(2)}</div>
                  </div>
                </div>
              </div>
            );
          })}
          
          {!obsLoading && allDisplayOBs.length === 0 && (
            <div className="col-span-full text-[10px] text-[#94A3B8] italic p-2 border border-dashed border-[#1E293B] rounded text-center">
              No recent Order Blocks found for {currentSymbol}. Waiting for market structure...
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
