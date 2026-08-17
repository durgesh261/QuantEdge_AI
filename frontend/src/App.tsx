import React, { useEffect } from 'react';
import { BrowserRouter, Routes, Route, useNavigate } from 'react-router-dom';
import { DesktopTerminalLayout } from './components/layout/DesktopTerminalLayout';
import { DashboardPage } from './features/dashboard/DashboardPage';
import { LivePortfolio } from './features/portfolio/LivePortfolio';
import { LiveTradingPage } from './features/live-trading/LiveTradingPage';
import { OrdersPage } from './features/orders/OrdersPage';
import { PositionsPage } from './features/positions/PositionsPage';
import { TradeHistoryPage } from './features/history/TradeHistoryPage';
import { TradeJournalPage } from './features/journal/TradeJournalPage';
import { AnalyticsPage } from './features/analytics/AnalyticsPage';
import { StrategyProfilesPage } from './features/strategy-profiles/StrategyProfilesPage';
import { SettingsPage } from './features/settings/SettingsPage';
import { LiveNewsCenterPage } from './features/news/LiveNewsCenterPage';
import LoginPage from './features/auth/LoginPage';

// Developer Mode Views
import { PaperTradingPage } from './features/paper-trading/PaperTradingPage';
import { ShadowLaboratoryPage } from './features/shadow/ShadowLaboratoryPage';
import { ReplayPage } from './features/replay/ReplayPage';
import { BacktestingPage } from './features/backtesting/BacktestingPage';
import { StrategyLaboratoryPage } from './features/laboratory/StrategyLaboratoryPage';
import { IndicatorValidationPage } from './features/validation/IndicatorValidationPage';
import { OperationsCenterPage } from './features/operations/OperationsCenterPage';
import { ProductionDashboardPage } from './features/production/ProductionDashboardPage';
import { SystemMonitorPage } from './features/system-monitor/SystemMonitorPage';
import { MarketDataStatusPage } from './features/tradingview/MarketDataStatusPage';
import { TradeAccountingPage } from './features/accounting/TradeAccountingPage';
import { TradeReviewPage } from './features/review/TradeReviewPage';
import { ChallengePage } from './features/challenge/ChallengePage';
import { AnalysisPage } from './features/analysis/AnalysisPage';

const navigate = useNavigate();

useEffect(() => {
  // Check authentication on app start
  // If has session cookie but not on /login, redirect to dashboard
  // If no session cookie and not on /login, redirect to login
  const checkAuth = async () => {
    // Check if session cookie exists
    const hasSession = document.cookie.split(';').some(cookie => cookie.trim().startsWith('session='));
    
    // If has session but not on dashboard/login, redirect
    if (hasSession && !window.location.pathname.startsWith('/dashboard') && window.location.pathname !== '/login') {
      navigate('/dashboard');
    }
    
    // If no session and on /dashboard or other protected routes, redirect to login
    if (!hasSession && window.location.pathname !== '/login' && window.location.pathname !== '/') {
      navigate('/login');
    }
  };

  checkAuth();
}, [navigate]);

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <DesktopTerminalLayout>
        <Routes>
          {/* Authentication Route */}
          <Route path="/login" element={<LoginPage />} />

          {/* Primary Live Trading Routes (protected by backend auth) */}
          <Route path="/" element={<DashboardPage />} />
          <Route path="/portfolio" element={<LivePortfolio />} />
          <Route path="/live-trading" element={<LiveTradingPage />} />
          <Route path="/news" element={<LiveNewsCenterPage />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/positions" element={<PositionsPage />} />
          <Route path="/history" element={<TradeHistoryPage />} />
          <Route path="/journal" element={<TradeJournalPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/strategy-profiles" element={<StrategyProfilesPage />} />
          <Route path="/settings" element={<SettingsPage />} />

          {/* Developer Mode Routes (protected by backend auth) */}
          <Route path="/paper-trading" element={<PaperTradingPage />} />
          <Route path="/shadow-laboratory" element={<ShadowLaboratoryPage />} />
          <Route path="/replay" element={<ReplayPage />} />
          <Route path="/backtest" element={<BacktestingPage />} />
          <Route path="/laboratory" element={<StrategyLaboratoryPage />} />
          <Route path="/indicator-validation" element={<IndicatorValidationPage />} />
          <Route path="/operations" element={<OperationsCenterPage />} />
          <Route path="/production-dashboard" element={<ProductionDashboardPage />} />
          <Route path="/system-monitor" element={<SystemMonitorPage />} />
          <Route path="/tradingview" element={<MarketDataStatusPage />} />
          <Route path="/trade-accounting" element={<TradeAccountingPage />} />
          <Route path="/trade-review" element={<TradeReviewPage />} />
          <Route path="/challenge" element={<ChallengePage />} />
          <Route path="/analysis" element={<AnalysisPage />} />

          {/* If no session cookie and trying to access anything besides /login, 
              the useEffect will redirect to /login above on app start. */}
        </Routes>
      </DesktopTerminalLayout>
    </BrowserRouter>
  );
};

export default App;
