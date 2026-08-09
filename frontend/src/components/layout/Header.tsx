import React, { useState, useEffect, useRef } from 'react';
import {
  Search, Command, Bell, Terminal, Wifi, WifiOff,
  Bot, ChevronDown, Loader2, Trash2
} from 'lucide-react';
import { useTerminalStore } from '../../store/useTerminalStore';
import { useDeltaStore } from '../../store/useDeltaStore';
import { useNotificationStore } from '../../store/useNotificationStore';
import { useDeltaConnection } from '../../hooks/useDeltaConnection';
import { NotificationPanel } from '../notifications/NotificationPanel';
import { KillSwitchModal } from '../dev/KillSwitchModal';

export const Header: React.FC = () => {
  const {
    activeSymbol, setActiveSymbol, isAlgoRunning, toggleAlgo,
    isDeveloperMode, toggleDeveloperMode,
  } = useTerminalStore();

  const { isDeltaEnabled, isConnected, isConnecting } = useDeltaStore();
  const { unreadCount, togglePanel, isPanelOpen } = useNotificationStore();
  const { toggleConnection } = useDeltaConnection();

  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [showKillSwitch, setShowKillSwitch] = useState(false);
  const [timeframe, setTimeframe] = useState('1H');
  const searchRef = useRef<HTMLDivElement>(null);

  const SYMBOLS = ['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'];
  const TIMEFRAMES = ['1m', '5m', '15m', '1H', '4H', '1D'];

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) setSearchOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        setSearchOpen(true);
      }
      if (e.key === 'Escape') setSearchOpen(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const filteredSymbols = SYMBOLS.filter((s) =>
    s.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <>
      <header className="h-12 bg-[#0B0E14] border-b border-[#1E293B] flex items-center justify-between px-3 shrink-0 select-none relative z-50">
        
        {/* LEFT */}
        <div className="flex items-center space-x-2">
          <button className="w-8 h-8 rounded-lg bg-[#1E293B] hover:bg-[#334155] flex items-center justify-center transition-colors">
            <Terminal className="w-4 h-4 text-[#3B82F6]" />
          </button>

          <button
            onClick={toggleAlgo}
            className={`flex items-center space-x-2 px-3 h-8 rounded-lg font-bold text-[10px] uppercase tracking-wider border transition-all ${
              isAlgoRunning
                ? 'bg-[#00C896]/10 text-[#00C896] border-[#00C896]/30'
                : 'bg-[#F6465D]/10 text-[#F6465D] border-[#F6465D]/30'
            }`}
          >
            <div className={`w-1.5 h-1.5 rounded-full ${isAlgoRunning ? 'bg-[#00C896]' : 'bg-[#F6465D]'} animate-pulse`} />
            <span>ALGO: {isAlgoRunning ? 'ON' : 'OFF'}</span>
            <Bot className="w-3 h-3" />
          </button>

          <div className="relative group">
            <button className="h-8 px-3 bg-[#3B82F6] hover:bg-[#2563EB] text-white rounded-lg font-bold text-[11px] flex items-center space-x-1 transition-colors">
              <span>{timeframe}</span>
              <ChevronDown className="w-3 h-3" />
            </button>
            <div className="absolute top-full left-0 mt-1 w-24 bg-[#161D2A] border border-[#334155] rounded-lg shadow-xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-50 overflow-hidden">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  onClick={() => setTimeframe(tf as any)}
                  className={`w-full text-left px-3 py-1.5 text-[11px] hover:bg-[#3B82F6]/10 transition-colors ${
                    timeframe === tf ? 'text-[#3B82F6] bg-[#3B82F6]/5' : 'text-[#94A3B8]'
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* CENTER: Search */}
        <div className="flex-1 max-w-xl mx-4" ref={searchRef}>
          <div className="relative">
            <div
              onClick={() => setSearchOpen(true)}
              className={`flex items-center space-x-2 w-full h-8 rounded-lg border px-3 cursor-text transition-colors ${
                searchOpen ? 'bg-[#0B0E14] border-[#3B82F6] ring-1 ring-[#3B82F6]/30' : 'bg-[#161D2A] border-[#1E293B]'
              }`}
            >
              <Search className="w-3.5 h-3.5 text-[#64748B]" />
              <span className="text-[11px] text-[#64748B] flex-1">
                {searchOpen ? '' : 'Search symbol...'}
              </span>
              {!searchOpen && (
                <kbd className="hidden sm:flex items-center space-x-0.5 px-1.5 py-0.5 bg-[#0B0E14] border border-[#334155] rounded text-[9px] text-[#64748B] font-mono">
                  <Command className="w-2.5 h-2.5" /><span>K</span>
                </kbd>
              )}
            </div>

            {searchOpen && (
              <div className="absolute top-full left-0 right-0 mt-1.5 bg-[#161D2A] border border-[#334155] rounded-lg shadow-2xl z-50 overflow-hidden">
                <input
                  autoFocus
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Type symbol..."
                  className="w-full bg-transparent border-0 border-b border-[#1E293B] px-3 py-2.5 text-[11px] text-[#F8FAFC] placeholder-[#64748B] outline-none"
                />
                <div className="max-h-48 overflow-y-auto py-1">
                  {filteredSymbols.map((sym) => (
                    <button
                      key={sym}
                      onClick={() => { setActiveSymbol(sym); setSearchOpen(false); setSearchQuery(''); }}
                      className={`w-full text-left px-3 py-2 text-[11px] hover:bg-[#3B82F6]/10 flex items-center justify-between transition-colors ${
                        activeSymbol === sym ? 'text-[#3B82F6] bg-[#3B82F6]/5' : 'text-[#94A3B8]'
                      }`}
                    >
                      <span className="font-mono font-bold">{sym}</span>
                      {activeSymbol === sym && <div className="w-1.5 h-1.5 rounded-full bg-[#3B82F6]" />}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* RIGHT */}
        <div className="flex items-center space-x-2">
          <button
            onClick={toggleDeveloperMode}
            className={`h-8 px-3 rounded-lg font-bold text-[10px] uppercase tracking-wider border transition-all ${
              isDeveloperMode
                ? 'bg-[#A855F7]/10 text-[#A855F7] border-[#A855F7]/30'
                : 'bg-[#1E293B] text-[#64748B] border-[#334155]'
            }`}
          >
            DEV MODE {isDeveloperMode ? 'ON' : 'OFF'}
          </button>

          {isDeveloperMode && (
            <button
              onClick={() => setShowKillSwitch(true)}
              className="h-8 px-3 rounded-lg font-bold text-[10px] uppercase tracking-wider border bg-[#F6465D]/10 text-[#F6465D] border-[#F6465D]/30 hover:bg-[#F6465D]/20 transition-all flex items-center space-x-1"
            >
              <Trash2 className="w-3 h-3" />
              <span>KILL</span>
            </button>
          )}

          {/* DELTA TOGGLE — Unified State */}
          <button
            onClick={toggleConnection}
            disabled={isConnecting}
            className={`flex items-center space-x-1.5 h-8 px-3 rounded-lg border text-[10px] font-bold uppercase transition-all disabled:opacity-50 ${
              isDeltaEnabled
                ? isConnected
                  ? 'bg-[#00C896]/10 text-[#00C896] border-[#00C896]/30'
                  : 'bg-[#F59E0B]/10 text-[#F59E0B] border-[#F59E0B]/30'
                : 'bg-[#1E293B] text-[#64748B] border-[#334155]'
            }`}
          >
            {isConnecting ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : isDeltaEnabled ? (
              <Wifi className="w-3 h-3" />
            ) : (
              <WifiOff className="w-3 h-3" />
            )}
            <span>
              {isConnecting ? 'SYNCING' : isDeltaEnabled ? (isConnected ? 'DELTA ON' : 'DELTA...') : 'DELTA OFF'}
            </span>
          </button>

          {/* NOTIFICATIONS */}
          <div className="relative">
            <button
              onClick={togglePanel}
              className="w-8 h-8 rounded-lg bg-[#1E293B] hover:bg-[#334155] border border-[#334155] flex items-center justify-center transition-colors relative"
            >
              <Bell className="w-4 h-4 text-[#94A3B8]" />
              {unreadCount > 0 && (
                <span className="absolute -top-0.5 -right-0.5 min-w-[14px] h-[14px] px-0.5 bg-[#F6465D] text-white rounded-full text-[8px] font-bold flex items-center justify-center border-2 border-[#0B0E14]">
                  {unreadCount > 9 ? '9+' : unreadCount}
                </span>
              )}
            </button>
            {isPanelOpen && <NotificationPanel />}
          </div>
        </div>
      </header>

      <KillSwitchModal isOpen={showKillSwitch} onClose={() => setShowKillSwitch(false)} />
    </>
  );
};
