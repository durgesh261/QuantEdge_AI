import React, { useState, useEffect, useRef } from 'react';
import { useTerminalStore } from '../../store/useTerminalStore';
import { useMarketPairs } from '../../hooks/useMarketPairs';
import {
  TrendingUp,
  TrendingDown,
  Search,
  Flame,
  SlidersHorizontal,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';

export const MarketWatchPanel: React.FC = () => {
  const { 
    activeSymbol, 
    setActiveSymbol, 
    isMarketWatchOpen, 
    toggleMarketWatch,
    marketWatchWidth,
    setMarketWatchWidth,
  } = useTerminalStore();
  const [search, setSearch] = useState('');
  const { pairList, isLoading } = useMarketPairs();
  const [isDragging, setIsDragging] = useState(false);
  const startDragRef = useRef<{ startX: number; startWidth: number }>({ startX: 0, startWidth: 260 });

  const pairs = pairList.filter(
    (p) =>
      p.symbol.toLowerCase().includes(search.toLowerCase()) ||
      p.name.toLowerCase().includes(search.toLowerCase())
  );

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    startDragRef.current = {
      startX: e.clientX,
      startWidth: marketWatchWidth,
    };
  };

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      const delta = e.clientX - startDragRef.current.startX;
      const nextWidth = startDragRef.current.startWidth + delta;
      setMarketWatchWidth(nextWidth);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, setMarketWatchWidth]);

  if (!isMarketWatchOpen) {
    return (
      <div className="hidden md:flex bg-[#161D2A] border-r border-[#1E293B] flex-col items-center py-3 px-1 z-10 shrink-0 select-none">
        <button
          onClick={toggleMarketWatch}
          className="p-1.5 text-[#94A3B8] hover:text-[#F8FAFC] hover:bg-[#1E2638] rounded-md transition-colors"
          title="Expand Market Watch Panel"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
        <span className="text-[10px] font-mono font-bold text-[#64748B] uppercase tracking-wider rotate-90 mt-8 whitespace-nowrap">
          MARKET WATCH
        </span>
      </div>
    );
  }

  return (
    <aside 
      style={{ width: `${marketWatchWidth}px` }} 
      className="hidden md:flex relative bg-[#161D2A] border-r border-[#1E293B] flex-col z-10 select-none shrink-0 overflow-hidden"
    >
      {/* Draggable Vertical Column Resizer (Red Line Splitter) */}
      <div
        onMouseDown={handleMouseDown}
        onDoubleClick={() => setMarketWatchWidth(260)}
        className={`absolute top-0 right-0 w-2 h-full cursor-col-resize z-30 transition-all group flex items-center justify-center select-none ${
          isDragging 
            ? 'bg-[#3B82F6] shadow-[0_0_12px_#3B82F6]' 
            : 'hover:bg-[#3B82F6]/40 bg-transparent'
        }`}
        title="Drag to resize column (Double-click to reset width)"
      >
        <div className={`w-0.5 h-7 rounded transition-colors ${isDragging ? 'bg-white' : 'bg-[#475569] group-hover:bg-[#3B82F6]'}`} />
      </div>

      {/* Panel Header */}
      <div className="p-3 border-b border-[#1E293B] flex items-center justify-between bg-[#0E121A]">
        <div className="flex items-center space-x-2 min-w-0">
          <SlidersHorizontal className="w-4 h-4 text-[#3B82F6] shrink-0" />
          <span className="text-xs font-bold text-[#F8FAFC] uppercase font-mono tracking-wider truncate">
            Market Watch
          </span>
        </div>
        <button
          onClick={toggleMarketWatch}
          className="p-1 text-[#94A3B8] hover:text-[#F8FAFC] hover:bg-[#1E2638] rounded transition-colors shrink-0"
          title="Collapse Watchlist"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
      </div>

      {/* Search Input */}
      <div className="p-2 border-b border-[#1E293B] bg-[#121722]">
        <div className="flex items-center bg-[#0B0E14] border border-[#334155] rounded-md px-2.5 py-1">
          <Search className="w-3.5 h-3.5 text-[#94A3B8] mr-2 shrink-0" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search perpetual pair..."
            className="bg-transparent border-none outline-none text-xs text-[#F8FAFC] placeholder-[#64748B] font-mono w-full min-w-0"
          />
        </div>
      </div>

      {/* Watchlist Item List */}
      <div className="flex-1 overflow-y-auto divide-y divide-[#1E293B]/70">
        {isLoading && pairs.length === 0 ? (
          <div className="p-4 text-center text-xs text-[#64748B] font-mono">Loading live pairs...</div>
        ) : pairs.length === 0 ? (
          <div className="p-4 text-center text-xs text-[#64748B] font-mono">No matching pairs.</div>
        ) : (
          pairs.map((pair) => {
            const isSelected = activeSymbol === pair.symbol;

            return (
              <button
                key={pair.symbol}
                onClick={() => setActiveSymbol(pair.symbol)}
                className={`w-full text-left p-2.5 transition-colors ${
                  isSelected
                    ? 'bg-[#1E2638] border-l-4 border-[#3B82F6]'
                    : 'hover:bg-[#1A2232] text-[#94A3B8]'
                }`}
              >
                <div className="flex items-center justify-between font-mono gap-1.5 min-w-0">
                  <div className="flex items-center space-x-1.5 min-w-0 truncate">
                    <span className="font-bold text-xs text-[#F8FAFC] truncate">{pair.symbol}</span>
                    {isSelected ? (
                      <span className="text-[8px] bg-[#3B82F6] text-white px-1.5 py-0.2 rounded font-bold uppercase tracking-wider shrink-0">
                        OPEN
                      </span>
                    ) : (
                      <span className="text-[9px] bg-[#1E293B] text-[#94A3B8] px-1 py-0.2 rounded shrink-0">
                        PERP
                      </span>
                    )}
                  </div>
                  <span className="text-xs font-bold text-[#F8FAFC] font-mono-tabular shrink-0">{pair.priceLabel}</span>
                </div>

                <div className="flex items-center justify-between font-mono text-[11px] mt-1 gap-1 min-w-0">
                  <span className="text-[#64748B] truncate text-[10px]">{pair.name}</span>
                  <span
                    className={`flex items-center font-semibold shrink-0 text-[10px] ${
                      pair.isPositive ? 'text-[#00C896]' : 'text-[#F6465D]'
                    }`}
                  >
                    {pair.isPositive ? (
                      <TrendingUp className="w-2.5 h-2.5 mr-0.5" />
                    ) : (
                      <TrendingDown className="w-2.5 h-2.5 mr-0.5" />
                    )}
                    {pair.changeLabel}
                  </span>
                </div>

                <div className="flex items-center justify-between text-[10px] font-mono mt-1.5 pt-1 border-t border-[#1E293B]/50 min-w-0">
                  <span className="text-[#3B82F6] bg-[#3B82F6]/10 px-1.5 py-0.2 rounded truncate text-[9px]">
                    {pair.topZone ? pair.topZone.type.replace('_', ' ') : 'NO ACTIVE ZONE'}
                  </span>
                  {pair.topZone && (
                    <span className="flex items-center text-[#F59E0B] font-semibold text-[9px] shrink-0">
                      <Flame className="w-2.5 h-2.5 mr-0.5" />
                      {pair.topZone.strength}/100
                    </span>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>
    </aside>
  );
};
