import React from 'react';
import { NavLink } from 'react-router-dom';
import { useTerminalStore } from '../../store/useTerminalStore';
import { 
  LayoutDashboard, 
  PieChart, 
  Activity, 
  ListOrdered, 
  Layers, 
  History, 
  BookOpen, 
  BarChart2, 
  Sliders, 
  Settings,
  Code2,
  ChevronDown,
  ChevronRight,
  Wallet,
  RotateCcw,
  BarChart3,
  ShieldCheck,
  Server,
  ShieldAlert,
  Radio,
  Calculator,
  FlaskConical,
  Cpu,
  FileText,
  LineChart,
  Trophy,
  Newspaper
} from 'lucide-react';

const liveNavigationItems = [
  { name: 'Dashboard', path: '/', icon: LayoutDashboard },
  { name: 'Live Portfolio', path: '/portfolio', icon: PieChart },
  { name: 'Live Trading', path: '/live-trading', icon: Activity },
  { name: 'Live News & Macro', path: '/news', icon: Newspaper },
  { name: 'Orders', path: '/orders', icon: ListOrdered },
  { name: 'Positions', path: '/positions', icon: Layers },
  { name: 'Trade History', path: '/history', icon: History },
  { name: 'Journal', path: '/journal', icon: BookOpen },
  { name: 'Analytics', path: '/analytics', icon: BarChart2 },
  { name: 'Strategy Profiles', path: '/strategy-profiles', icon: Sliders },
  { name: 'Settings', path: '/settings', icon: Settings },
];

const developerNavigationItems = [
  { name: 'Paper Trading', path: '/paper-trading', icon: Wallet },
  { name: 'Shadow Lab', path: '/shadow-laboratory', icon: ShieldCheck },
  { name: 'Replay Terminal', path: '/replay', icon: RotateCcw },
  { name: 'Backtesting', path: '/backtest', icon: BarChart3 },
  { name: 'Research Lab', path: '/laboratory', icon: FlaskConical },
  { name: 'Validation', path: '/indicator-validation', icon: ShieldCheck },
  { name: 'Operations NOC', path: '/operations', icon: Cpu },
  { name: 'Production', path: '/production-dashboard', icon: ShieldAlert },
  { name: 'System Monitor', path: '/system-monitor', icon: Server },
  { name: 'TradingView Alert', path: '/tradingview', icon: Radio },
  { name: 'Trade Accounting', path: '/trade-accounting', icon: Calculator },
  { name: 'Trade Review', path: '/trade-review', icon: FileText },
  { name: 'Challenge', path: '/challenge', icon: Trophy },
  { name: 'Analysis', path: '/analysis', icon: LineChart },
];

import { useQuery } from '@tanstack/react-query';
import { deltaApi } from '../../services/api';

export const Sidebar: React.FC = () => {
  const { isSidebarCollapsed, isDeveloperMode, toggleDeveloperMode } = useTerminalStore();

  const { data: deltaHealth } = useQuery({
    queryKey: ['deltaHealth'],
    queryFn: deltaApi.getHealth,
    refetchInterval: 5000,
  });

  const isDeltaConnected = deltaHealth?.data?.connectionState === 'CONNECTED';

  return (
    <aside
      className={`block md:flex bg-[#161D2A] border-r border-[#1E293B] flex-col select-none transition-all duration-200 ${
        isSidebarCollapsed ? 'w-16' : 'w-60'
      }`}
    >
      {/* App Branding Header */}
      <div className="h-14 px-3 flex items-center justify-between border-b border-[#1E293B] shrink-0">
        <div className="flex items-center space-x-2.5 overflow-hidden">
          <div className="w-8 h-8 bg-gradient-to-br from-[#3B82F6] to-[#6366F1] rounded-lg flex items-center justify-center font-extrabold text-white text-sm shadow-md shrink-0">
            Q
          </div>
          {!isSidebarCollapsed && (
            <div className="flex flex-col overflow-hidden">
              <span className="text-sm font-bold text-[#F8FAFC] tracking-wide leading-none truncate">
                QuantEdge <span className="text-[#3B82F6]">AI</span>
              </span>
              <span className="text-[10px] text-[#00C896] font-mono mt-0.5 font-semibold">Live Trading</span>
            </div>
          )}
        </div>
      </div>

      {/* Primary Navigation Links (Exactly 10 Items) */}
      <nav className="flex-1 py-3 px-2 space-y-1 overflow-y-auto no-scrollbar">
        <div className="px-3 pb-1 text-[10px] font-bold text-[#64748B] uppercase tracking-wider">
          {!isSidebarCollapsed && 'Trading Terminal'}
        </div>
        {liveNavigationItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            title={isSidebarCollapsed ? item.name : undefined}
            className={({ isActive }) =>
              `flex items-center space-x-3 px-3 py-2 rounded-lg text-xs font-medium transition-all relative ${
                isActive
                  ? 'bg-[#3B82F6]/15 text-[#3B82F6] font-semibold border-l-4 border-l-[#3B82F6] border-t border-r border-b border-[#3B82F6]/30'
                  : 'text-[#94A3B8] hover:bg-[#1E2638] hover:text-[#F8FAFC]'
              }`
            }
          >
            <item.icon className="w-4 h-4 shrink-0 text-[#94A3B8]" />
            {!isSidebarCollapsed && <span className="truncate">{item.name}</span>}
          </NavLink>
        ))}

        {/* Developer Mode Partition */}
        <div className="pt-3 border-t border-[#1E293B]/60 mt-3">
          <button
            onClick={toggleDeveloperMode}
            title={isSidebarCollapsed ? 'Toggle Developer Mode' : undefined}
            className="w-full flex items-center justify-between px-3 py-2 text-xs font-bold text-[#94A3B8] hover:text-white rounded-lg hover:bg-[#1E2638] transition-colors"
          >
            <div className="flex items-center space-x-2.5">
              <Code2 className={`w-4 h-4 shrink-0 ${isDeveloperMode ? 'text-[#F59E0B]' : 'text-[#64748B]'}`} />
              {!isSidebarCollapsed && <span className="truncate">Developer Mode</span>}
            </div>
            {!isSidebarCollapsed && (
              isDeveloperMode ? <ChevronDown className="w-3.5 h-3.5 text-[#F59E0B]" /> : <ChevronRight className="w-3.5 h-3.5 text-[#64748B]" />
            )}
          </button>

          {isDeveloperMode && (
            <div className="mt-1 space-y-0.5 pl-1">
              {!isSidebarCollapsed && (
                <div className="px-3 py-1 text-[9px] font-bold text-[#F59E0B] uppercase tracking-wider">
                  Dev & Lab Tools
                </div>
              )}
              {developerNavigationItems.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  title={isSidebarCollapsed ? item.name : undefined}
                  className={({ isActive }) =>
                    `flex items-center space-x-3 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                      isActive
                        ? 'bg-[#F59E0B]/15 text-[#F59E0B] font-semibold border-l-2 border-l-[#F59E0B]'
                        : 'text-[#94A3B8] hover:bg-[#1E2638] hover:text-[#F8FAFC]'
                    }`
                  }
                >
                  <item.icon className="w-3.5 h-3.5 shrink-0" />
                  {!isSidebarCollapsed && <span className="truncate text-[11px]">{item.name}</span>}
                </NavLink>
              ))}
            </div>
          )}
        </div>
      </nav>

      {/* Footer System Status */}
      <div className="p-3 border-t border-[#1E293B] bg-[#0B0E14] text-[10px] text-[#94A3B8] flex items-center justify-between font-mono shrink-0">
        <div className="flex items-center space-x-1.5 truncate">
          <span className={`w-2 h-2 rounded-full shrink-0 ${isDeltaConnected ? 'bg-[#00C896] animate-pulse' : 'bg-[#94A3B8]'}`}></span>
          {!isSidebarCollapsed && <span>Delta Exchange</span>}
        </div>
        {!isSidebarCollapsed && (
          <span className={`font-bold px-1.5 py-0.5 rounded border ${isDeltaConnected ? 'text-[#00C896] bg-[#00C896]/10 border-[#00C896]/30' : 'text-[#94A3B8] bg-[#94A3B8]/10 border-[#94A3B8]/30'}`}>
            {isDeltaConnected ? 'LIVE' : 'OFFLINE'}
          </span>
        )}
      </div>
    </aside>
  );
};
