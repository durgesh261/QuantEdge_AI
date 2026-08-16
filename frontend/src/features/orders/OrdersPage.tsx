import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { usePortfolioSummary } from '../../hooks/usePortfolioSummary';
import { useExecution } from '../../hooks/useExecution';
import { ExecutionModal } from '../../components/ui/ExecutionModal';
import { ValueDisplay } from '../../components/ui/ValueDisplay';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { 
  ListOrdered, 
  Trash2, 
  RefreshCw, 
  PlusCircle, 
  Layers, 
  History,
  XCircle,
  Clock
} from 'lucide-react';

export const OrdersPage: React.FC = () => {
  const [isExecutionModalOpen, setIsExecutionModalOpen] = useState(false);
  const [selectedSymbolForTrade, setSelectedSymbolForTrade] = useState('BTCUSD.P');
  const [selectedSideForTrade, setSelectedSideForTrade] = useState<'buy' | 'sell'>('buy');
  const [activeTab, setActiveTab] = useState<'ACTIVE' | 'POSITIONS' | 'HISTORY'>('POSITIONS');

  // Real Portfolio Data
  const { data: summary, isLoading: isLoadingSummary, refetch: refetchPortfolio, isFetching } = usePortfolioSummary();
  const { 
    cancelOrder, 
    isCancelling, 
    cancelAllOrders, 
    isCancellingAll, 
    closePosition, 
    isClosingPosition,
    executionHistory,
    refetchAll: refetchExecution
  } = useExecution();

  const wallet = summary?.wallet;
  const positions = summary?.positions?.items || [];
  const orders = summary?.orders?.items || [];
  const connection = summary?.connection;

  const handleRefresh = () => {
    void refetchPortfolio();
    void refetchExecution();
  };

  const handleOpenTrade = (symbol: string, side: 'buy' | 'sell') => {
    setSelectedSymbolForTrade(symbol);
    setSelectedSideForTrade(side);
    setIsExecutionModalOpen(true);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="space-y-4 max-w-[1920px] mx-auto pb-6 font-mono select-none"
    >
      {/* Top Action & Telemetry Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-slate-900/90 border border-slate-800 p-4 rounded-xl shadow-sm backdrop-blur">
        <div className="flex items-center space-x-3">
          <ListOrdered className="w-6 h-6 text-indigo-400" />
          <div>
            <h1 className="text-lg font-bold text-white uppercase font-mono">
              Delta Exchange Execution Workstation
            </h1>
            <div className="flex items-center space-x-2 text-xs text-slate-400 mt-0.5">
              <span>Real-Time Order Routing & Position Management</span>
              <span>•</span>
              <StatusBadge status={connection?.status || 'DISCONNECTED'} label={`DELTA: ${connection?.status || 'OFFLINE'}`} />
            </div>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex flex-wrap items-center gap-3 text-xs">
          {/* Refresh Button */}
          <button
            onClick={handleRefresh}
            disabled={isFetching}
            className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-white rounded-lg font-bold flex items-center space-x-1.5 border border-slate-700 transition"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            <span>SYNC WITH DELTA</span>
          </button>

          {/* Cancel All Orders */}
          {orders.length > 0 && (
            <button
              onClick={() => void cancelAllOrders()}
              disabled={isCancellingAll}
              className="px-3 py-2 bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 rounded-lg font-bold flex items-center space-x-1.5 border border-rose-500/30 transition disabled:opacity-50"
            >
              <Trash2 className="w-3.5 h-3.5 text-rose-400" />
              <span>{isCancellingAll ? 'CANCELLING ALL...' : `CANCEL ALL (${orders.length})`}</span>
            </button>
          )}

          {/* New Order Modal Trigger */}
          <button
            onClick={() => handleOpenTrade('BTCUSD.P', 'buy')}
            className="px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 rounded-lg font-bold flex items-center space-x-1.5 shadow-lg transition"
          >
            <PlusCircle className="w-4 h-4" />
            <span>NEW ORDER / POSITION</span>
          </button>
        </div>
      </div>

      {/* Metrics Strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-slate-900/90 border border-slate-800 p-3.5 rounded-xl">
          <span className="text-slate-400 text-[10px] uppercase font-bold block mb-1">Total Net Equity</span>
          <ValueDisplay value={wallet?.totalEquity} format="currency" size="lg" colorize isLoading={isLoadingSummary} />
        </div>
        <div className="bg-slate-900/90 border border-slate-800 p-3.5 rounded-xl">
          <span className="text-slate-400 text-[10px] uppercase font-bold block mb-1">Available Margin</span>
          <ValueDisplay value={wallet?.availableMargin} format="currency" size="lg" neutralColor="text-emerald-400" isLoading={isLoadingSummary} />
        </div>
        <div className="bg-slate-900/90 border border-slate-800 p-3.5 rounded-xl">
          <span className="text-slate-400 text-[10px] uppercase font-bold block mb-1">Open Positions Count</span>
          <span className="text-xl font-bold text-white">{positions.length}</span>
        </div>
        <div className="bg-slate-900/90 border border-slate-800 p-3.5 rounded-xl">
          <span className="text-slate-400 text-[10px] uppercase font-bold block mb-1">Working Orders Count</span>
          <span className="text-xl font-bold text-amber-400">{orders.length}</span>
        </div>
      </div>

      {/* Main Execution Tabs */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 space-y-4 shadow-sm">
        {/* Tab Buttons */}
        <div className="flex items-center space-x-2 border-b border-slate-800 pb-3 text-xs">
          <button
            onClick={() => setActiveTab('POSITIONS')}
            className={`px-3.5 py-1.5 rounded-lg font-bold transition flex items-center space-x-1.5 ${
              activeTab === 'POSITIONS' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>Open Positions ({positions.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('ACTIVE')}
            className={`px-3.5 py-1.5 rounded-lg font-bold transition flex items-center space-x-1.5 ${
              activeTab === 'ACTIVE' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            <span>Working Orders ({orders.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('HISTORY')}
            className={`px-3.5 py-1.5 rounded-lg font-bold transition flex items-center space-x-1.5 ${
              activeTab === 'HISTORY' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            <History className="w-3.5 h-3.5" />
            <span>Execution Audit Log ({executionHistory.length})</span>
          </button>
        </div>

        {/* TAB 1: POSITIONS TABLE */}
        {activeTab === 'POSITIONS' && (
          <div className="overflow-x-auto text-xs">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="text-slate-400 border-b border-slate-800 text-[11px]">
                  <th className="py-2.5 px-3">Symbol</th>
                  <th className="py-2.5 px-3">Side</th>
                  <th className="py-2.5 px-3 text-right">Size</th>
                  <th className="py-2.5 px-3 text-right">Entry Price</th>
                  <th className="py-2.5 px-3 text-right">Mark Price</th>
                  <th className="py-2.5 px-3 text-right">Liq Price</th>
                  <th className="py-2.5 px-3 text-right">Unrealized PnL</th>
                  <th className="py-2.5 px-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {positions.length > 0 ? (
                  positions.map((pos, idx) => (
                    <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-950 text-white">
                      <td className="py-3 px-3 font-bold">{pos.symbol}</td>
                      <td className="py-3 px-3">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          pos.side === 'buy' ? 'text-emerald-400 bg-emerald-500/10 border border-emerald-500/30' : 'text-rose-400 bg-rose-500/10 border border-rose-500/30'
                        }`}>
                          {pos.side.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-3 px-3 text-right font-mono">{pos.size}</td>
                      <td className="py-3 px-3 text-right font-mono">${pos.entryPrice.toLocaleString()}</td>
                      <td className="py-3 px-3 text-right font-mono">${pos.markPrice.toLocaleString()}</td>
                      <td className="py-3 px-3 text-right font-mono text-rose-400">${pos.liquidationPrice.toLocaleString()}</td>
                      <td className="py-3 px-3 text-right font-mono font-bold">
                        <ValueDisplay value={pos.unrealizedPnl} format="currency" colorize />
                      </td>
                      <td className="py-3 px-3 text-right">
                        <button
                          onClick={() => void closePosition(pos.symbol)}
                          disabled={isClosingPosition}
                          className="px-2.5 py-1 bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/30 rounded text-[11px] font-bold transition disabled:opacity-50"
                        >
                          CLOSE
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={8} className="py-12 text-center text-slate-500 italic">
                      No open positions currently active on Delta Exchange India.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* TAB 2: WORKING ORDERS TABLE */}
        {activeTab === 'ACTIVE' && (
          <div className="overflow-x-auto text-xs">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="text-slate-400 border-b border-slate-800 text-[11px]">
                  <th className="py-2.5 px-3">Order ID</th>
                  <th className="py-2.5 px-3">Symbol</th>
                  <th className="py-2.5 px-3">Type</th>
                  <th className="py-2.5 px-3">Side</th>
                  <th className="py-2.5 px-3 text-right">Size</th>
                  <th className="py-2.5 px-3 text-right">Price</th>
                  <th className="py-2.5 px-3">State</th>
                  <th className="py-2.5 px-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {orders.length > 0 ? (
                  orders.map((ord) => (
                    <tr key={ord.id} className="border-b border-slate-800/50 hover:bg-slate-950 text-white">
                      <td className="py-3 px-3 text-slate-400 font-mono">#{ord.id}</td>
                      <td className="py-3 px-3 font-bold">{ord.symbol}</td>
                      <td className="py-3 px-3 uppercase text-slate-300">{ord.orderType}</td>
                      <td className="py-3 px-3">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${ord.side === 'buy' ? 'bg-[#00C896]/20 text-[#00C896] border border-[#00C896]/30' : 'bg-[#F6465D]/20 text-[#F6465D] border border-[#F6465D]/30'}`}>
                          {ord.side.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-3 px-3 text-right font-mono">{ord.size}</td>
                      <td className="py-3 px-3 text-right font-mono">${ord.price.toLocaleString()}</td>
                      <td className="py-3 px-3">
                        <span className="px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 text-[10px] uppercase font-bold border border-amber-500/30">
                          {ord.state}
                        </span>
                      </td>
                      <td className="py-3 px-3 text-right">
                        <button
                          onClick={() => void cancelOrder(ord.id)}
                          disabled={isCancelling}
                          className="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-[11px] font-bold transition flex items-center space-x-1 ml-auto"
                        >
                          <XCircle className="w-3.5 h-3.5 text-rose-400" />
                          <span>CANCEL</span>
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={8} className="py-12 text-center text-slate-500 italic">
                      No pending working orders in queue.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* TAB 3: EXECUTION AUDIT HISTORY */}
        {activeTab === 'HISTORY' && (
          <div className="overflow-x-auto text-xs">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="text-slate-400 border-b border-slate-800 text-[11px]">
                  <th className="py-2.5 px-3">Client Order ID</th>
                  <th className="py-2.5 px-3">Symbol</th>
                  <th className="py-2.5 px-3">Side</th>
                  <th className="py-2.5 px-3">Type</th>
                  <th className="py-2.5 px-3 text-right">Size</th>
                  <th className="py-2.5 px-3 text-right">Latency</th>
                  <th className="py-2.5 px-3">Status</th>
                  <th className="py-2.5 px-3">Message</th>
                </tr>
              </thead>
              <tbody>
                {executionHistory.length > 0 ? (
                  executionHistory.map((item: any, idx: number) => (
                    <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-950 text-white">
                      <td className="py-3 px-3 text-slate-400 font-mono">{item.clientOrderId}</td>
                      <td className="py-3 px-3 font-bold">{item.symbol}</td>
                      <td className="py-3 px-3">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          item.side === 'buy' ? 'text-emerald-400 bg-emerald-500/10' : 'text-rose-400 bg-rose-500/10'
                        }`}>
                          {item.side?.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-3 px-3 uppercase text-slate-300">{item.orderType}</td>
                      <td className="py-3 px-3 text-right font-mono">{item.size}</td>
                      <td className="py-3 px-3 text-right font-mono text-indigo-400">{item.latencyMs}ms</td>
                      <td className="py-3 px-3">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          item.success ? 'text-emerald-400 bg-emerald-500/10 border border-emerald-500/30' : 'text-rose-400 bg-rose-500/10 border border-rose-500/30'
                        }`}>
                          {item.state}
                        </span>
                      </td>
                      <td className="py-3 px-3 text-slate-400">{item.message || '—'}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={8} className="py-12 text-center text-slate-500 italic">
                      No execution events recorded in this session yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Execution Modal */}
      <ExecutionModal
        isOpen={isExecutionModalOpen}
        onClose={() => setIsExecutionModalOpen(false)}
        defaultSymbol={selectedSymbolForTrade}
        defaultSide={selectedSideForTrade}
      />
    </motion.div>
  );
};

export default OrdersPage;
