import React from 'react';
import { 
  Wallet, TrendingUp, AlertTriangle, 
  RefreshCw, Activity, Shield, Zap, BarChart3, 
  Percent, Receipt, Info, ArrowUpRight, ArrowDownRight,
  Clock, Unlock
} from 'lucide-react';
import { useDeltaStore } from '../../store/useDeltaStore';
import { useTerminalStore } from '../../store/useTerminalStore';
import { usePortfolioSummary } from '../../hooks/usePortfolioSummary';

export const LivePortfolio: React.FC = () => {
  const { isConnected } = useDeltaStore();
  const connectionError: string | null = null;
  const { activeSymbol } = useTerminalStore();
  
  const { data: summary, isLoading, dataUpdatedAt } = usePortfolioSummary();

  const stats = {
    totalEquity: summary?.wallet?.totalEquity || 0,
    availableMargin: summary?.wallet?.availableMargin || 0,
    usedMargin: summary?.wallet?.totalEquity ? summary.wallet.totalEquity - summary.wallet.availableMargin : 0,
    unrealizedPnL: summary?.positions?.totalUnrealizedPnl || 0,
    realizedPnL: summary?.positions?.totalRealizedPnl || 0,
    todayReturn: summary?.pnlBreakdown?.today || 0,
    marginUtilization: summary?.wallet?.marginUtilizationPercent || 0,
    totalFeesPaid: summary?.fundingAndFees?.totalFeesPaid || 0,
    estFunding24h: summary?.fundingAndFees?.estimatedFunding24h || 0,
  };

  const riskMetrics = summary?.analytics || null;
  const periodPnL = summary?.pnlBreakdown || null;
  const positions = summary?.positions?.items || [];
  const lastSync = dataUpdatedAt ? new Date(dataUpdatedAt) : null;

  // Delta Exchange India Fee Structure
  const FEE_STRUCTURE = {
    futures: { maker: 0.02, taker: 0.05 }, // %
    options: { maker: 0.03, taker: 0.03 },  // %
    gstOnFees: 18,                          // % on fee amount only
    liquidation: 0.5,                       // %
    settlement: 0.01,                       // % (approximate)
  };

  const formatCurrency = (val: number) => {
    if (val === 0 || !isFinite(val)) return '$0.00';
    return `$${val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };



  const isOffline = !isConnected;

  return (
    <div className="w-full h-full bg-[#0B0E14] text-[#F8FAFC] overflow-y-auto">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[#1E293B] flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-3">
            <div className="w-8 h-8 rounded-lg bg-[#3B82F6]/20 flex items-center justify-center">
              <Wallet className="w-4 h-4 text-[#3B82F6]" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-[#F8FAFC]">QuantEdge AI</h1>
              <p className="text-[10px] text-[#94A3B8]">
                Delta Exchange India Real-Time Synchronized Balances, Positions & Risk Analytics
              </p>
            </div>
          </div>
        </div>
        
        <div className="flex items-center space-x-3 overflow-x-auto no-scrollbar w-full md:w-auto pb-1 md:pb-0">
          {/* Connection Status */}
          <div className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-md text-[10px] font-bold border ${
            isConnected 
              ? 'bg-[#00C896]/10 text-[#00C896] border-[#00C896]/30' 
              : 'bg-[#F6465D]/10 text-[#F6465D] border-[#F6465D]/30'
          }`}>
            <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-[#00C896]' : 'bg-[#F6465D]'} animate-pulse`} />
            <span>DELTA: {isConnected ? 'ONLINE' : 'OFFLINE'}</span>
          </div>
          
          <button onClick={() => {}} className="p-1 hover:bg-[#1E293B] rounded transition-colors text-[#64748B] hover:text-white" title="Refresh portfolio">
              <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-[#00C896]' : ''}`} />
            </button>
        </div>
      </div>

      {/* Connection Error Banner */}
      {connectionError && (
        <div className="mx-6 mt-4 bg-[#F6465D]/10 border border-[#F6465D]/30 rounded-lg p-3 flex items-center space-x-2">
          <AlertTriangle className="w-4 h-4 text-[#F6465D] shrink-0" />
          <span className="text-[11px] text-[#F6465D]">
            {connectionError}. Reconnecting background daemon...
          </span>
        </div>
      )}

      {/* Top Stats Grid */}
      <div className="p-6 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Total Net Equity */}
        <div className="bg-[#0E121A] border border-[#1E293B] rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[10px] text-[#94A3B8] uppercase font-bold tracking-wider">Total Net Equity</span>
            <Shield className="w-3.5 h-3.5 text-[#3B82F6]" />
          </div>
          <div className="text-2xl font-bold font-mono text-[#F8FAFC] mb-1">
            {isOffline && !stats ? '--' : formatCurrency(stats?.totalEquity || 0)}
          </div>
          <div className="text-[10px] text-[#64748B]">Real-time Capital Valuation</div>
          <div className="mt-3 pt-3 border-t border-[#1E293B] flex justify-between text-[10px]">
            <span className="text-[#94A3B8]">Wallet Balance:</span>
            <span className="font-mono text-[#F8FAFC]">
              {isOffline ? '--' : formatCurrency(stats?.totalEquity || 0)}
            </span>
          </div>
        </div>

        {/* Available Margin */}
        <div className="bg-[#0E121A] border border-[#1E293B] rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[10px] text-[#94A3B8] uppercase font-bold tracking-wider">Available Margin</span>
            <Unlock className="w-3.5 h-3.5 text-[#00C896]" />
          </div>
          <div className="text-2xl font-bold font-mono text-[#00C896] mb-1">
            {isOffline && !stats ? '--' : formatCurrency(stats?.availableMargin || 0)}
          </div>
          <div className="text-[10px] text-[#64748B]">Free Trading Liquidity</div>
          <div className="mt-3 pt-3 border-t border-[#1E293B] flex justify-between text-[10px]">
            <span className="text-[#94A3B8]">Used Margin:</span>
            <span className="font-mono text-[#F8FAFC]">
              {isOffline ? '--' : formatCurrency(stats?.usedMargin || 0)}
            </span>
          </div>
        </div>

        {/* Today's PnL */}
        <div className="bg-[#0E121A] border border-[#1E293B] rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[10px] text-[#94A3B8] uppercase font-bold tracking-wider">Today's Realized + Unrealized</span>
            <Activity className="w-3.5 h-3.5 text-[#F59E0B]" />
          </div>
          <div className={`text-2xl font-bold font-mono mb-1 ${
            (stats?.todayReturn || 0) >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
          }`}>
            {isOffline && !stats ? '--' : formatCurrency(stats?.todayReturn || 0)}
          </div>
          <div className="text-[10px] text-[#64748B]">24h Rolling Net Return</div>
          <div className="mt-3 pt-3 border-t border-[#1E293B] flex justify-between text-[10px]">
            <span className="text-[#94A3B8]">Unrealized Open PnL:</span>
            <span className={`font-mono ${(stats?.unrealizedPnL || 0) >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
              {isOffline ? '--' : formatCurrency(stats?.unrealizedPnL || 0)}
            </span>
          </div>
        </div>

        {/* Margin Utilization */}
        <div className="bg-[#0E121A] border border-[#1E293B] rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[10px] text-[#94A3B8] uppercase font-bold tracking-wider">Margin Utilization</span>
            <Percent className="w-3.5 h-3.5 text-[#F59E0B]" />
          </div>
          <div className="text-2xl font-bold font-mono text-[#F59E0B] mb-1">
            {isOffline && !stats ? '0.00%' : `${(stats?.marginUtilization || 0).toFixed(2)}%`}
          </div>
          <div className="text-[10px] text-[#64748B]">Risk Exposure Ratio</div>
          <div className="mt-3 pt-3 border-t border-[#1E293B]">
            <div className="flex justify-between text-[10px] mb-1">
              <span className="text-[#94A3B8]">Utilized</span>
              <span className="text-[#64748B]">Max Safe: 35%</span>
            </div>
            <div className="w-full h-1.5 bg-[#1E293B] rounded-full overflow-hidden">
              <div 
                className={`h-full rounded-full transition-all ${
                  (stats?.marginUtilization || 0) > 35 ? 'bg-[#F6465D]' : 
                  (stats?.marginUtilization || 0) > 20 ? 'bg-[#F59E0B]' : 'bg-[#00C896]'
                }`}
                style={{ width: `${Math.min(stats?.marginUtilization || 0, 100)}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Middle Section: PnL Breakdown + Risk Metrics + Fees/Tax */}
      <div className="px-6 pb-6 grid grid-cols-1 lg:grid-cols-3 gap-4">
        
        {/* PnL Period Breakdown */}
        <div className="bg-[#0E121A] border border-[#1E293B] rounded-xl p-4">
          <div className="flex items-center space-x-2 mb-4">
            <BarChart3 className="w-4 h-4 text-[#3B82F6]" />
            <div>
              <h3 className="text-[11px] font-bold text-[#F8FAFC] uppercase tracking-wider">PnL Period Breakdown</h3>
              <p className="text-[9px] text-[#64748B]">Computed directly by backend engine</p>
            </div>
          </div>
          
          <div className="space-y-3">
            {[
              { label: 'Today', value: periodPnL?.today },
              { label: 'This Week', value: periodPnL?.thisWeek },
              { label: 'This Month', value: periodPnL?.thisMonth },
              { label: 'All-Time Lifetime', value: periodPnL?.allTime },
            ].map((item) => (
              <div key={item.label} className="flex items-center justify-between py-2 border-b border-[#1E293B] last:border-0">
                <span className="text-[11px] text-[#94A3B8]">{item.label}:</span>
                <span className={`text-[11px] font-mono font-bold ${
                  item.value === undefined ? 'text-[#64748B]' :
                  item.value > 0 ? 'text-[#00C896]' : 
                  item.value < 0 ? 'text-[#F6465D]' : 'text-[#F8FAFC]'
                }`}>
                  {item.value === undefined ? '--' : formatCurrency(item.value)}
                </span>
              </div>
            ))}
            
            <div className="flex items-center justify-between py-2 pt-3 border-t border-[#334155]">
              <span className="text-[11px] text-[#94A3B8] font-bold">Gross Profit / Loss:</span>
              <span className="text-[11px] font-mono font-bold text-[#F8FAFC]">
                {periodPnL ? `${formatCurrency(periodPnL.grossProfit)} / ${formatCurrency(periodPnL.grossLoss)}` : '-- / --'}
              </span>
            </div>
          </div>
        </div>

        {/* Institutional Risk & Performance */}
        <div className="bg-[#0E121A] border border-[#1E293B] rounded-xl p-4">
          <div className="flex items-center space-x-2 mb-4">
            <Zap className="w-4 h-4 text-[#A855F7]" />
            <div>
              <h3 className="text-[11px] font-bold text-[#F8FAFC] uppercase tracking-wider">Institutional Risk & Performance</h3>
              <p className="text-[9px] text-[#64748B]">Quant metrics calculated from trade ledger</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            {[
              { label: 'Sharpe Ratio', value: riskMetrics?.sharpeRatio, color: 'text-[#3B82F6]' },
              { label: 'Sortino Ratio', value: riskMetrics?.sortinoRatio, color: 'text-[#3B82F6]' },
              { label: 'Win Rate', value: riskMetrics?.winRatePercent, format: (v: number) => `${v.toFixed(1)}%`, color: 'text-[#00C896]' },
              { label: 'Profit Factor', value: riskMetrics?.profitFactor, color: 'text-[#00C896]' },
              { label: 'Expectancy', value: riskMetrics?.expectancy, format: (v: number) => formatCurrency(v), color: 'text-[#F59E0B]' },
              { label: 'Max Drawdown', value: riskMetrics?.maxDrawdownPercent, format: (v: number) => `${v.toFixed(2)}%`, color: 'text-[#F6465D]' },
            ].map((metric) => (
              <div key={metric.label} className="bg-[#0B0E14] border border-[#1E293B] rounded-lg p-3">
                <div className="text-[9px] text-[#64748B] uppercase font-bold mb-1">{metric.label}</div>
                <div className={`text-sm font-mono font-bold ${metric.color}`}>
                  {metric.value === null || metric.value === undefined ? '--' : 
                    metric.format ? metric.format(metric.value) : metric.value.toFixed(2)}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════
            CORRECTED: FUNDING, FEES & TAX LEDGER
            ═══════════════════════════════════════════════════════ */}
        <div className="bg-[#0E121A] border border-[#1E293B] rounded-xl p-4">
          <div className="flex items-center space-x-2 mb-4">
            <Receipt className="w-4 h-4 text-[#F59E0B]" />
            <div>
              <h3 className="text-[11px] font-bold text-[#F8FAFC] uppercase tracking-wider">Funding, Fees & Tax Ledger</h3>
              <p className="text-[9px] text-[#64748B]">Delta Exchange India Fee Structure & Tax Classification</p>
            </div>
          </div>

          <div className="space-y-3">
            {/* Trading Fees */}
            <div className="bg-[#0B0E14] border border-[#1E293B] rounded-lg p-3">
              <div className="text-[9px] text-[#64748B] uppercase font-bold mb-2 flex items-center space-x-1">
                <span>Trading Fees (Per Order)</span>
                <Info className="w-3 h-3 text-[#64748B]" />
              </div>
              <div className="grid grid-cols-2 gap-2 text-[10px]">
                <div className="flex justify-between">
                  <span className="text-[#94A3B8]">Futures Maker:</span>
                  <span className="font-mono text-[#00C896]">{FEE_STRUCTURE.futures.maker}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#94A3B8]">Futures Taker:</span>
                  <span className="font-mono text-[#F6465D]">{FEE_STRUCTURE.futures.taker}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#94A3B8]">Options Maker:</span>
                  <span className="font-mono text-[#00C896]">{FEE_STRUCTURE.options.maker}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#94A3B8]">Options Taker:</span>
                  <span className="font-mono text-[#F6465D]">{FEE_STRUCTURE.options.taker}%</span>
                </div>
              </div>
            </div>

            {/* Fee Summary */}
            <div className="space-y-2">
              <div className="flex justify-between items-center py-1.5 border-b border-[#1E293B]">
                <span className="text-[11px] text-[#94A3B8]">Total Fees Paid (YTD):</span>
                <span className="text-[11px] font-mono font-bold text-[#F8FAFC]">
                  {stats?.totalFeesPaid ? formatCurrency(stats.totalFeesPaid) : '--'}
                </span>
              </div>
              <div className="flex justify-between items-center py-1.5 border-b border-[#1E293B]">
                <span className="text-[11px] text-[#94A3B8]">Est. 24h Funding:</span>
                <span className="text-[11px] font-mono text-[#94A3B8]">
                  {stats?.estFunding24h ? formatCurrency(stats.estFunding24h) : '--'}
                </span>
              </div>
              <div className="flex justify-between items-center py-1.5 border-b border-[#1E293B]">
                <span className="text-[11px] text-[#94A3B8]">GST on Fees:</span>
                <span className="text-[11px] font-mono text-[#94A3B8]">{FEE_STRUCTURE.gstOnFees}% of fee amt</span>
              </div>
              <div className="flex justify-between items-center py-1.5">
                <span className="text-[11px] text-[#94A3B8]">Liquidation Fee:</span>
                <span className="text-[11px] font-mono text-[#F6465D]">{FEE_STRUCTURE.liquidation}%</span>
              </div>
            </div>

            {/* ═══════════════════════════════════════════════════════
                CORRECTED TAX SECTION — NO 30% VDA / NO 1% TDS
                ═══════════════════════════════════════════════════════ */}
            <div className="bg-[#00C896]/5 border border-[#00C896]/20 rounded-lg p-3">
              <div className="flex items-center space-x-1.5 mb-2">
                <Shield className="w-3.5 h-3.5 text-[#00C896]" />
                <span className="text-[10px] font-bold text-[#00C896] uppercase tracking-wider">Tax Classification</span>
              </div>
              
              <div className="space-y-2">
                <div className="flex items-start space-x-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00C896] mt-1.5 shrink-0" />
                  <p className="text-[10px] text-[#E2E8F0] leading-relaxed">
                    <strong className="text-[#00C896]">Speculative Business Income</strong> — Crypto futures on Delta Exchange India are INR-settled derivatives, <strong>not VDAs</strong>.
                  </p>
                </div>
                <div className="flex items-start space-x-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00C896] mt-1.5 shrink-0" />
                  <p className="text-[10px] text-[#E2E8F0] leading-relaxed">
                    Taxed at <strong className="text-[#F8FAFC]">normal slab rates</strong> (Section 43(5)), NOT the 30% flat VDA tax.
                  </p>
                </div>
                <div className="flex items-start space-x-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00C896] mt-1.5 shrink-0" />
                  <p className="text-[10px] text-[#E2E8F0] leading-relaxed">
                    <strong className="text-[#F8FAFC]">1% TDS does NOT apply</strong> to futures — only to spot VDA transfers.
                  </p>
                </div>
                <div className="flex items-start space-x-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#F59E0B] mt-1.5 shrink-0" />
                  <p className="text-[10px] text-[#E2E8F0] leading-relaxed">
                    Losses can be <strong className="text-[#F8FAFC]">set off</strong> against speculative gains & carried forward <strong className="text-[#F8FAFC]">4 years</strong>.
                  </p>
                </div>
              </div>

              <div className="mt-2 pt-2 border-t border-[#00C896]/20">
                <p className="text-[8px] text-[#64748B] leading-relaxed">
                  <strong>Disclaimer:</strong> Delta Exchange India is FIU-registered. Futures are treated as speculative business income under Indian tax law. Consult a qualified CA for filing ITR-3 Schedule P&L. This is not tax advice.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Live Open Positions */}
      <div className="px-6 pb-6">
        <div className="bg-[#0E121A] border border-[#1E293B] rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-[#1E293B] flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <Activity className="w-4 h-4 text-[#3B82F6]" />
              <div>
                <h3 className="text-[11px] font-bold text-[#F8FAFC] uppercase tracking-wider">
                  Live Open Positions ({positions.length})
                </h3>
                <p className="text-[9px] text-[#64748B]">Delta Exchange India Perpetual Contracts</p>
              </div>
            </div>
            {lastSync && (
              <span className="text-[9px] text-[#64748B] flex items-center space-x-1">
                <Clock className="w-3 h-3" />
                <span>Last sync: {lastSync.toLocaleTimeString()}</span>
              </span>
            )}
          </div>

          {positions.length === 0 ? (
            <div className="p-8 text-center">
              <div className="w-12 h-12 rounded-full bg-[#1E293B] flex items-center justify-center mx-auto mb-3">
                <TrendingUp className="w-5 h-5 text-[#64748B]" />
              </div>
              <p className="text-[11px] text-[#94A3B8] font-medium">No Open Positions</p>
              <p className="text-[10px] text-[#64748B] mt-1">Your Delta Exchange India account is currently flat.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-[#1E293B] bg-[#0B0E14]">
                    {['Symbol', 'Side', 'Size', 'Entry Price', 'Mark Price', 'Liq. Price', 'Margin', 'Unrealized PnL', 'ROE %'].map((h) => (
                      <th key={h} className="px-4 py-2.5 text-[9px] font-bold text-[#64748B] uppercase tracking-wider whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map((pos: any, i: number) => {
                    const side = pos.side?.toUpperCase() || 'LONG';
                    const isLong = side === 'BUY' || side === 'LONG';
                    const entry = parseFloat(pos.entry_price || '0');
                    const mark = parseFloat(pos.mark_price || '0');
                    const size = parseFloat(pos.size || '0');
                    const margin = parseFloat(pos.margin_amount || '0');
                    const pnl = parseFloat(pos.unrealized_pnl || '0');
                    const roe = margin > 0 ? (pnl / margin) * 100 : 0;
                    const liq = pos.liquidation_price ? parseFloat(pos.liquidation_price).toFixed(2) : '--';

                    return (
                      <tr key={i} className="border-b border-[#1E293B] hover:bg-[#161D2A] transition-colors">
                        <td className="px-4 py-3 text-[11px] font-bold text-[#F8FAFC] whitespace-nowrap">
                          {pos.product_symbol || activeSymbol}
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center px-2 py-0.5 rounded text-[9px] font-bold ${
                            isLong ? 'bg-[#00C896]/20 text-[#00C896]' : 'bg-[#F6465D]/20 text-[#F6465D]'
                          }`}>
                            {isLong ? <ArrowUpRight className="w-3 h-3 mr-0.5" /> : <ArrowDownRight className="w-3 h-3 mr-0.5" />}
                            {isLong ? 'LONG' : 'SHORT'}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-[11px] font-mono text-[#F8FAFC] whitespace-nowrap">{size}</td>
                        <td className="px-4 py-3 text-[11px] font-mono text-[#94A3B8] whitespace-nowrap">${entry.toLocaleString('en-US')}</td>
                        <td className="px-4 py-3 text-[11px] font-mono text-[#F8FAFC] whitespace-nowrap">${mark.toLocaleString('en-US')}</td>
                        <td className="px-4 py-3 text-[11px] font-mono text-[#F6465D] whitespace-nowrap">{liq}</td>
                        <td className="px-4 py-3 text-[11px] font-mono text-[#94A3B8] whitespace-nowrap">${margin.toFixed(2)}</td>
                        <td className={`px-4 py-3 text-[11px] font-mono font-bold whitespace-nowrap ${pnl >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                          {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                        </td>
                        <td className={`px-4 py-3 text-[11px] font-mono font-bold whitespace-nowrap ${roe >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                          {roe >= 0 ? '+' : ''}{roe.toFixed(2)}%
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
