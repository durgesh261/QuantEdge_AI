import React from 'react';
import { TopMarketTicker } from './TopMarketTicker';
import { Header } from './Header';
import { Sidebar } from './Sidebar';
import { MarketWatchPanel } from './MarketWatchPanel';
import { StatusBar } from './StatusBar';
import { CommandPalette } from './CommandPalette';
import { ToastContainer } from './ToastContainer';
import { GlobalNewsNotifier } from './GlobalNewsNotifier';
import { ConnectionBanner } from './ConnectionBanner';
import { OfflineOverlay } from './OfflineOverlay';

interface DesktopTerminalLayoutProps {
  children: React.ReactNode;
}

export const DesktopTerminalLayout: React.FC<DesktopTerminalLayoutProps> = ({ children }) => {
  return (
    <div className="flex flex-col h-[100dvh] w-full max-w-full overflow-hidden bg-[#0B0E14] text-[#F8FAFC]">
      <GlobalNewsNotifier />
      <TopMarketTicker />
      {/* Connection banner — only visible when offline/degraded */}
      <ConnectionBanner />
      <Header />

      <div className="flex flex-1 overflow-hidden relative">
        <Sidebar />
        <MarketWatchPanel />
        <main className="flex-1 min-w-0 min-h-0 bg-[#121722] overflow-y-auto overflow-x-hidden p-4 relative">
          {children}
        </main>
      </div>

      <StatusBar />
      <CommandPalette />
      <ToastContainer />

      {/* Full-screen overlay after persistent disconnect */}
      <OfflineOverlay />
    </div>
  );
};
