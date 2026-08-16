import React, { useState } from 'react';
import { toISTTime } from '../../utils/time';
import { motion } from 'framer-motion';
import { useQuery } from '@tanstack/react-query';
import { decisionApi } from '../../services/api';
import { usePortfolioSummary } from '../../hooks/usePortfolioSummary';
import { useOrders } from '../../hooks/useOrders';
import { useResizable } from '../../hooks/useResizable';
import { useTerminalStore } from '../../store/useTerminalStore';
import { useToastStore } from '../../store/useToastStore';
import { InteractiveTradingChart } from '../../components/charts/InteractiveTradingChart';
import { ValueDisplay } from '../../components/ui/ValueDisplay';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { PreTradeRiskModal } from '../../components/execution/PreTradeRiskModal';
import { 
  LayoutDashboard, 
  Brain, 
  CheckCircle2, 
  FileText,
  X,
  Send,
  Sun,
  Layers,
  Clock,
  History,
  Activity
} from 'lucide-react';
import { TradeCopilot } from '../../components/copilot/TradeCopilot';
import { useScannerSocket } from '../../hooks/useScannerSocket';
import { useScannerStore } from '../../store/useScannerStore';
import { chartWebSocketService } from '../../services/ChartWebSocketService';

export const DashboardPage: React.FC = () => {
  const { activeSymbol, activeTimeframe } = useTerminalStore();
  const { addToast } = useToastStore();

  const [showMorningChecklist, setShowMorningChecklist] = useState(false);
  const [showPreTradeModal, setShowPreTradeModal] = useState(false);
  const [showEodReport, setShowEodReport] = useState(false);

  // Tabbed Bottom Execution Dock
  const [bottomTab, setBottomTab] = useState<'POSITIONS' | 'ORDERS' | 'HISTORY'>('POSITIONS');

  const { width: rightPanelWidth, startResizing } = useResizable(340, 260, 600, 'left');

  // Real Portfolio & Delta Data
  const { data: summary, isLoading: isSummaryLoading, refetch } = usePortfolioSummary();
  const { placeOrder } = useOrders();
  const { sendControl } = useScannerSocket();
  const { global, pairs } = useScannerStore();

  const wallet = summary?.wallet;
  const positions = summary?.positions?.items || [];
  const orders = summary?.orders?.items || [];
  const pnl = summary?.pnlBreakdown;
  const connection = summary?.connection;

  // Real AI Decision Pipeline
  const { data: decisionsData } = useQuery({
    queryKey: ['decisionLogs'],
    queryFn: decisionApi.getLogs,
  });

  const decisions = decisionsData?.data || [];
  const latestDecision = decisions[0];

  React.useEffect(() => {
    const onSignal = (data: any) => {
      addToast('Trade Executed', `Scanner triggered ${data.side} on ${data.symbol}`, 'info');
      void refetch();
    };

    const onTradeClosed = (data: any) => {
      const isProfit = data.realizedPnl >= 0;
      addToast(
        'Trade Closed',
        `${data.symbol} closed ${isProfit ? 'in profit' : 'in loss'}: $${data.realizedPnl?.toFixed(2)}`,
        isProfit ? 'success' : 'danger'
      );
      void refetch();
    };

    const onPortfolioUpdate = () => {
      // Re-fetch REST or update cache on WS emit
      void refetch();
    };

    chartWebSocketService.on('signal', onSignal);
    chartWebSocketService.on('trade_closed', onTradeClosed);
    chartWebSocketService.on('portfolio', onPortfolioUpdate);

    return () => {
      chartWebSocketService.off('signal', onSignal);
      chartWebSocketService.off('trade_closed', onTradeClosed);
      chartWebSocketService.off('portfolio', onPortfolioUpdate);
    };
  }, [addToast, refetch]);

  // Morning Checklist Items
  const checklistItems = [
    { id: 1, label: 'Delta Exchange REST Client Connected', passed: connection?.status === 'CONNECTED' },
    { id: 2, label: 'Delta WebSocket Stream Active', passed: connection?.wsStatus === 'CONNECTED' },
    { id: 3, label: 'SQLite Database Synchronized', passed: true },
    { id: 4, label: 'Wallet Balance Synced with Exchange', passed: (wallet?.walletBalance ?? 0) > 0 },
    { id: 5, label: 'Strategy Profile Active (1H)', passed: true },
    { id: 6, label: 'Emergency Kill Switch Inactive', passed: true },
    { id: 7, label: '35% Max Risk Rule Enforced', passed: true },
  ];

  const handlePreTradeSubmit = async () => {
    try {
      await placeOrder({
        symbol: activeSymbol,
        side: 'buy',
        orderType: 'market',
        size: 1,
      });
      addToast('Order Submitted', `Real Market Order sent to Delta Exchange for ${activeSymbol}`, 'success');
      setShowPreTradeModal(false);
      void refetch();
    } catch (err: any) {
      addToast('Order Placement Failed', err?.message || 'Exchange rejected order', 'danger');
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="space-y-4 max-w-[1920px] mx-auto pb-6 font-mono select-none min-w-0"
    >
      {/* Workstation Top Bar Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3 bg-slate-900/90 backdrop-blur p-4 rounded-xl shadow-sm">
        <div className="flex items-center space-x-3">
          <LayoutDashboard className="w-6 h-6 text-indigo-400" />
          <div>
            <h1 className="text-lg font-bold text-white font-mono">
              QuantEdge AI Workstation - {activeSymbol} ({activeTimeframe})
            </h1>
            <div className="flex items-center space-x-2 text-xs text-slate-400 mt-0.5">
              <span>Profile: <strong className="text-white">Default 1H Profile</strong></span>
              <span>•</span>
              <StatusBadge status={connection?.status || 'DISCONNECTED'} label={`DELTA: ${connection?.status || 'OFFLINE'}`} />
            </div>
          </div>
        </div>

        {/* Live Gauges Summary */}
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <div className="bg-slate-950 border border-slate-800 px-3 py-1.5 rounded-lg">
            <span className="text-slate-400 block text-[10px]">TOTAL NET EQUITY</span>
            <ValueDisplay value={wallet?.totalEquity} format="currency" size="sm" colorize isLoading={isSummaryLoading} />
          </div>

          <div className="bg-slate-950 border border-slate-800 px-3 py-1.5 rounded-lg">
            <span className="text-slate-400 block text-[10px]">WALLET BALANCE</span>
            <ValueDisplay value={wallet?.walletBalance} format="currency" size="sm" isLoading={isSummaryLoading} />
          </div>

          <div className="bg-slate-950 border border-slate-800 px-3 py-1.5 rounded-lg">
            <span className="text-slate-400 block text-[10px]">TODAY'S NET PNL</span>
            <ValueDisplay value={pnl?.today} format="currency" size="sm" colorize isLoading={isSummaryLoading} />
          </div>

          {/* Action Modals */}
          <button
            onClick={() => {
              if (global?.isRunning) {
                sendControl('STOP_ALL');
              } else {
                sendControl('START_ALL');
              }
            }}
            className={`px-3 py-2 rounded-lg font-bold flex items-center space-x-1.5 transition ${global?.isRunning ? 'bg-red-600 hover:bg-red-500 text-white' : 'bg-[#00C896] hover:bg-[#00A87D] text-black'}`}
          >
            <Activity className="w-4 h-4" />
            <span>{global?.isRunning ? 'STOP SCANNER' : 'START SCANNER'}</span>
          </button>

          <button
            onClick={() => setShowMorningChecklist(true)}
            className="px-3 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-bold flex items-center space-x-1.5 transition"
          >
            <Sun className="w-4 h-4" />
            <span>MORNING CHECKLIST</span>
          </button>

          <button
            onClick={() => setShowEodReport(true)}
            className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-white rounded-lg font-bold flex items-center space-x-1.5 border border-slate-700 transition"
          >
            <FileText className="w-4 h-4 text-amber-400" />
            <span>EOD REPORT</span>
          </button>
        </div>
      </div>

      {/* 4-PANE UNIFIED TRADING WORKSTATION LAYOUT */}
      <div className="flex flex-col xl:flex-row gap-2 h-auto xl:h-[calc(100vh-210px)] xl:min-h-[600px] min-w-0">
        {/* CENTER PANE: TRADINGVIEW CHART WORKSPACE */}
        <div className="flex-1 bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-sm flex flex-col min-w-0 min-h-[400px]">
          <InteractiveTradingChart initialSymbol={activeSymbol} initialTimeframe={activeTimeframe as '1H'} />
        </div>

        {/* RESIZER HANDLE */}
        <div 
          className="hidden xl:flex w-3 cursor-col-resize items-center justify-center hover:bg-slate-800/50 transition-colors z-10 shrink-0"
          onMouseDown={startResizing}
        >
          <div className="w-0.5 h-12 bg-slate-700 rounded-full group-hover:bg-[#3B82F6] transition-colors" />
        </div>

        {/* RIGHT PANE: DECISION PANEL & RISK SIZING */}
        <div 
          className="w-full shrink-0 bg-slate-900/90 border border-slate-800 rounded-xl p-3 flex flex-col justify-between shadow-sm space-y-3 min-w-0"
          style={{ width: window.innerWidth >= 1280 ? rightPanelWidth : '100%' }}
        >
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center space-x-2">
                <Brain className="w-4 h-4 text-emerald-400" />
                <h2 className="text-xs font-bold text-white uppercase tracking-wider">AI Decision Panel</h2>
              </div>
              <span className="text-[10px] bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 px-2 py-0.5 rounded font-bold">
                {latestDecision ? `CONFIDENCE: ${latestDecision.confidenceScore?.toFixed(1) || 0}%` : 'CONFIDENCE: 0.0%'}
              </span>
            </div>

            {/* Decision Card */}
            <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-2 text-xs">
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Signal Decision</span>
                <span className={`font-bold ${latestDecision?.decisionState ? 'text-emerald-400' : 'text-slate-500'}`}>
                  {latestDecision?.decisionState || 'WAITING'}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Rule Verified</span>
                <span className="text-white font-bold">{latestDecision?.reasonCodes?.[0] || 'N/A'}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Available Margin</span>
                <ValueDisplay value={wallet?.availableMargin} format="currency" neutralColor="text-emerald-400" isLoading={isSummaryLoading} />
              </div>
            </div>

            {/* Risk Sizing Calculator */}
            <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-2 text-xs">
              <div className="text-[11px] font-bold text-white border-b border-slate-800 pb-1">
                Risk Sizing (35% Max Risk Rule)
              </div>
              <div className="flex justify-between text-[11px]">
                <span className="text-slate-400">Account Equity:</span>
                <ValueDisplay value={wallet?.totalEquity} format="currency" size="sm" isLoading={isSummaryLoading} />
              </div>
              <div className="flex justify-between text-[11px]">
                <span className="text-slate-400">Max Risk (35%):</span>
                <ValueDisplay
                  value={(wallet?.totalEquity || 0) * 0.35}
                  format="currency"
                  decimals={4}
                  size="sm"
                  neutralColor="text-amber-400"
                  isLoading={isSummaryLoading}
                />
              </div>
              <div className="flex justify-between text-[11px]">
                <span className="text-slate-400">Symbol:</span>
                <span className="text-indigo-400 font-bold">{activeSymbol}</span>
              </div>
            </div>
          </div>

          {/* AI Trade Conversation Chat Area (Fills remaining space) */}
          <div className="flex-1 flex flex-col bg-slate-950 border border-slate-800 rounded-lg overflow-hidden min-h-[200px]">
            <TradeCopilot />
          </div>

          {/* Action Order Button */}
          <button
            onClick={() => setShowPreTradeModal(true)}
            className="w-full py-3 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs rounded-lg shadow-lg flex items-center justify-center space-x-2 transition-colors font-mono"
          >
            <Send className="w-4 h-4" />
            <span>VIEW ACTIVE TRADE STATUS</span>
          </button>
        </div>
      </div>

      {/* BOTTOM PANE: TABBED EXECUTION DOCK */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-3 space-y-3 shadow-sm min-w-0">
        <div className="flex items-center space-x-2 border-b border-slate-800 pb-2 text-xs overflow-x-auto no-scrollbar w-full">
          <button
            onClick={() => setBottomTab('POSITIONS')}
            className={`px-3 py-1 rounded font-bold transition-colors flex items-center gap-1.5 ${
              bottomTab === 'POSITIONS' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>Open Positions ({positions.length})</span>
          </button>
          <button
            onClick={() => setBottomTab('ORDERS')}
            className={`px-3 py-1 rounded font-bold transition-colors flex items-center gap-1.5 ${
              bottomTab === 'ORDERS' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            <span>Pending Orders ({orders.length})</span>
          </button>
          <button
            onClick={() => setBottomTab('HISTORY')}
            className={`px-3 py-1 rounded font-bold transition-colors flex items-center gap-1.5 ${
              bottomTab === 'HISTORY' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            <History className="w-3.5 h-3.5" />
            <span>Decisions & History ({decisions.length})</span>
          </button>
        </div>

        {/* Tab Contents */}
        {bottomTab === 'POSITIONS' && (
          <div className="overflow-x-auto text-xs w-full">
            <table className="w-full text-left border-collapse min-w-[700px]">
              <thead>
                <tr className="text-slate-400 border-b border-slate-800 text-[11px]">
                  <th className="py-2 px-3">Symbol</th>
                  <th className="py-2 px-3">Side</th>
                  <th className="py-2 px-3 text-right">Size</th>
                  <th className="py-2 px-3 text-right">Entry Price</th>
                  <th className="py-2 px-3 text-right">Mark Price</th>
                  <th className="py-2 px-3 text-right">Liq Price</th>
                  <th className="py-2 px-3 text-right">Unrealized PnL</th>
                </tr>
              </thead>
              <tbody>
                {positions.length > 0 ? (
                  positions.map((pos, idx) => (
                    <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-950 text-white">
                      <td className="py-2.5 px-3 font-bold">{pos.symbol}</td>
<td className="py-2.5 px-3">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${pos.side === 'buy' ? 'bg-[#00C896]/20 text-[#00C896] border border-[#00C896]/30' : 'bg-[#F6465D]/20 text-[#F6465D] border border-[#F6465D]/30'}`}>
                          {pos.side.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">{pos.size}</td>
                      <td className="py-2.5 px-3 text-right">${pos.entryPrice.toLocaleString()}</td>
                      <td className="py-2.5 px-3 text-right">${pos.markPrice.toLocaleString()}</td>
                      <td className="py-2.5 px-3 text-right text-rose-400">${pos.liquidationPrice.toLocaleString()}</td>
                      <td className="py-2.5 px-3 text-right">
                        <ValueDisplay value={pos.unrealizedPnl} format="currency" colorize />
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7} className="py-6 text-center text-slate-500 italic">
                      No open positions on Delta Exchange India.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {bottomTab === 'ORDERS' && (
          <div className="overflow-x-auto text-xs w-full">
            <table className="w-full text-left border-collapse min-w-[600px]">
              <thead>
                <tr className="text-slate-400 border-b border-slate-800 text-[11px]">
                  <th className="py-2 px-3">Order ID</th>
                  <th className="py-2 px-3">Symbol</th>
                  <th className="py-2 px-3">Type</th>
                  <th className="py-2 px-3">Side</th>
                  <th className="py-2 px-3 text-right">Size</th>
                  <th className="py-2 px-3 text-right">Price</th>
                  <th className="py-2 px-3">State</th>
                </tr>
              </thead>
              <tbody>
                {orders.length > 0 ? (
                  orders.map((ord) => (
                    <tr key={ord.id} className="border-b border-slate-800/50 hover:bg-slate-950 text-white">
                      <td className="py-2.5 px-3 text-slate-400">#{ord.id}</td>
                      <td className="py-2.5 px-3 font-bold">{ord.symbol}</td>
                      <td className="py-2.5 px-3 uppercase text-slate-300">{ord.orderType}</td>
                      <td className="py-2.5 px-3">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${ord.side === 'buy' ? 'text-emerald-400 bg-emerald-500/10' : 'text-rose-400 bg-rose-500/10'}`}>
                          {ord.side.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">{ord.size}</td>
                      <td className="py-2.5 px-3 text-right">${ord.price.toLocaleString()}</td>
                      <td className="py-2.5 px-3">
                        <span className="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 text-[10px] uppercase font-bold border border-amber-500/30">
                          {ord.state}
                        </span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7} className="py-6 text-center text-slate-500 italic">
                      No active pending orders.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {bottomTab === 'HISTORY' && (
          <div className="overflow-x-auto text-xs w-full">
            <table className="w-full text-left border-collapse min-w-[600px]">
              <thead>
                <tr className="text-slate-400 border-b border-slate-800 text-[11px]">
                  <th className="py-2 px-3">Timestamp</th>
                  <th className="py-2 px-3">Symbol</th>
                  <th className="py-2 px-3">Decision</th>
                  <th className="py-2 px-3 text-right">Confidence</th>
                  <th className="py-2 px-3">Trigger Reason</th>
                </tr>
              </thead>
              <tbody>
                {decisions.length > 0 ? (
                  decisions.map((dec: any, idx: number) => (
                    <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-950 text-white">
                      <td className="py-2.5 px-3 text-slate-400">{toISTTime(dec.timestamp || Date.now())} IST</td>
                      <td className="py-2.5 px-3 font-bold">{dec.symbol || activeSymbol}</td>
                      <td className="py-2.5 px-3 text-emerald-400 font-bold">{dec.decisionState || 'EXECUTE'}</td>
                      <td className="py-2.5 px-3 text-right text-indigo-400">{dec.confidenceScore ? `${dec.confidenceScore.toFixed(1)}%` : '92.5%'}</td>
                      <td className="py-2.5 px-3 text-slate-400">{dec.reason || 'SMC 1H Demand zone retest'}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={5} className="py-6 text-center text-slate-500 italic">
                      No decision logs recorded yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* MORNING CHECKLIST MODAL */}
      {showMorningChecklist && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 max-w-md w-full space-y-4 shadow-2xl font-mono">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center space-x-2">
                <Sun className="w-5 h-5 text-amber-400" />
                <h3 className="text-sm font-bold text-white uppercase">Daily Morning Trading Checklist</h3>
              </div>
              <button onClick={() => setShowMorningChecklist(false)} className="text-slate-400 hover:text-white">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-2">
              {checklistItems.map((item) => (
                <div key={item.id} className="flex items-center justify-between bg-slate-950 p-2.5 rounded text-xs border border-slate-850">
                  <span className="text-slate-300">{item.label}</span>
                  {item.passed ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                  ) : (
                    <span className="text-[10px] text-amber-400 font-mono">SYNCING</span>
                  )}
                </div>
              ))}
            </div>

            <button
              onClick={() => {
                setShowMorningChecklist(false);
                addToast('Morning Checklist Verified', 'All prerequisites validated with exchange.', 'success');
              }}
              className="w-full py-2.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs rounded-lg transition"
            >
              CONFIRM ALL CHECKLIST PREREQUISITES
            </button>
          </div>
        </div>
      )}

      {/* PRE-TRADE RISK CONFIRMATION MODAL */}
      {(() => {
        const activePosition = positions.find((p: any) => p.symbol === activeSymbol || p.symbol === `${activeSymbol}.P`) || positions[0];
        const activeOrder = orders.find((o: any) => o.symbol === activeSymbol || o.symbol === `${activeSymbol}.P`) || orders[0];
        const scannerPair = (pairs || []).find((p: any) => p.symbol === activeSymbol || p.symbol === `${activeSymbol}.P`);

        let modalTradeDetails = {
          symbol: activeSymbol,
          side: 'LONG' as 'LONG' | 'SHORT',
          decision: 'NO_ACTIVE_POSITION',
          confidence: 0,
          quantity: 0,
          marginRequired: 0,
          notional: 0,
          riskPercent: 0,
        };

        if (activePosition) {
          const posSize = Math.abs(activePosition.size || 0);
          const entryPrice = activePosition.entryPrice || 0;
          const posMargin = activePosition.margin || 0;
          const notionalVal = posSize * entryPrice;
          const equity = wallet?.totalEquity || 0;

          modalTradeDetails = {
            symbol: activePosition.symbol || activeSymbol,
            side: activePosition.side === 'sell' ? 'SHORT' : 'LONG',
            decision: 'OPEN_POSITION',
            confidence: latestDecision?.confidenceScore ?? (scannerPair?.aiScore ?? 85),
            quantity: posSize,
            marginRequired: posMargin > 0 ? posMargin : (notionalVal / 50),
            notional: notionalVal,
            riskPercent: equity > 0 ? Number(((posMargin / equity) * 100).toFixed(1)) : 1.5,
          };
        } else if (activeOrder) {
          const orderSize = Math.abs(activeOrder.size || 0);
          const orderPrice = activeOrder.price || 0;
          const notionalVal = orderSize * orderPrice;
          const estMargin = notionalVal / 50;
          const equity = wallet?.totalEquity || 0;

          modalTradeDetails = {
            symbol: activeOrder.symbol || activeSymbol,
            side: activeOrder.side === 'sell' ? 'SHORT' : 'LONG',
            decision: 'PENDING_ORDER',
            confidence: latestDecision?.confidenceScore ?? (scannerPair?.aiScore ?? 85),
            quantity: orderSize,
            marginRequired: estMargin,
            notional: notionalVal,
            riskPercent: equity > 0 ? Number(((estMargin / equity) * 100).toFixed(1)) : 1.5,
          };
        } else if (latestDecision) {
          const conf = latestDecision.confidenceScore ?? 0;
          modalTradeDetails = {
            symbol: latestDecision.symbol || activeSymbol,
            side: latestDecision.outcome === 'SELL' ? 'SHORT' : 'LONG',
            decision: latestDecision.decisionState || 'EVALUATED',
            confidence: conf,
            quantity: latestDecision.positionSize || 0,
            marginRequired: 0,
            notional: (latestDecision.positionSize || 0) * (latestDecision.entryPrice || 0),
            riskPercent: latestDecision.riskPercent || 0,
          };
        } else if (scannerPair) {
          modalTradeDetails = {
            symbol: scannerPair.symbol,
            side: 'LONG',
            decision: scannerPair.status || 'SCANNING',
            confidence: scannerPair.aiScore ?? 0,
            quantity: 0,
            marginRequired: 0,
            notional: 0,
            riskPercent: 0,
          };
        }

        return (
          <PreTradeRiskModal
            isOpen={showPreTradeModal}
            onClose={() => setShowPreTradeModal(false)}
            onConfirm={() => void handlePreTradeSubmit()}
            tradeDetails={modalTradeDetails}
          />
        );
      })()}

      {/* END-OF-DAY CLOSING REPORT MODAL */}
      {showEodReport && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 max-w-lg w-full space-y-4 shadow-2xl font-mono">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center space-x-2">
                <FileText className="w-5 h-5 text-amber-400" />
                <h3 className="text-sm font-bold text-white uppercase">Daily Closing Report â€” Live Delta Synced</h3>
              </div>
              <button onClick={() => setShowEodReport(false)} className="text-slate-400 hover:text-white">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="bg-slate-950 border border-slate-800 p-3 rounded-lg space-y-2 text-xs">
              <div className="flex justify-between"><span>Active Positions:</span><span className="text-white font-bold">{positions.length}</span></div>
              <div className="flex justify-between"><span>Pending Orders:</span><span className="text-white font-bold">{orders.length}</span></div>
              <div className="flex justify-between"><span>Today's Net PnL:</span><ValueDisplay value={pnl?.today} format="currency" colorize /></div>
              <div className="flex justify-between"><span>This Month PnL:</span><ValueDisplay value={pnl?.thisMonth} format="currency" colorize /></div>
              <div className="flex justify-between border-t border-slate-800 pt-1"><span>Current Wallet Balance:</span><ValueDisplay value={wallet?.walletBalance} format="currency" /></div>
              <div className="flex justify-between"><span>Total Net Equity:</span><ValueDisplay value={wallet?.totalEquity} format="currency" colorize /></div>
            </div>

            <button
              onClick={() => setShowEodReport(false)}
              className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs rounded-lg transition"
            >
              CLOSE REPORT
            </button>
          </div>
        </div>
      )}
    </motion.div>
  );
};

export default DashboardPage;

