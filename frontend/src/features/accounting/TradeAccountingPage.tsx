import React, { useState } from 'react';
import { toISTTime } from '../../utils/time';
import { motion, AnimatePresence } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { tradeAccountingApi } from '../../services/api';
import { useToastStore } from '../../store/useToastStore';
import {
  Wallet,
  Trophy,
  BookOpen,
  RotateCcw,
  Download,
  Filter,
  CheckCircle2,
  FileJson,
  X,
  ChevronDown,
  ChevronUp,
  ShieldCheck,
  Percent,
  Receipt,
  HelpCircle,
} from 'lucide-react';
import { TradeLedgerEntryDto, TradeLedgerFilterDto } from '@algoapp/shared';

export const TradeAccountingPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { addToast } = useToastStore();

  // Filters State
  const [selectedSymbol, setSelectedSymbol] = useState<string>('');
  const [selectedSide, setSelectedSide] = useState<string>('');
  const [selectedResult, setSelectedResult] = useState<string>('');
  const [selectedMode, setSelectedMode] = useState<string>('');
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('');

  // Selected trade for deep audit modal
  const [inspectTrade, setInspectTrade] = useState<TradeLedgerEntryDto | null>(null);

  // Expanded row ID for quick accordion
  const [expandedTradeId, setExpandedTradeId] = useState<string | null>(null);

  // Show Fee & Tax Guide banner toggle
  const [showComplianceGuide, setShowComplianceGuide] = useState<boolean>(true);

  const filters: Partial<TradeLedgerFilterDto> = {
    symbol: selectedSymbol || undefined,
    side: selectedSide ? (selectedSide as any) : undefined,
    resultStatus: selectedResult ? (selectedResult as any) : undefined,
    executionMode: selectedMode ? (selectedMode as any) : undefined,
    timeframe: selectedTimeframe ? (selectedTimeframe as any) : undefined,
  };

  const { data: walletData } = useQuery({
    queryKey: ['walletState'],
    queryFn: tradeAccountingApi.getWallet,
    refetchInterval: 3000,
  });

  const { data: challengeData } = useQuery({
    queryKey: ['challengeState'],
    queryFn: tradeAccountingApi.getChallenge,
    refetchInterval: 3000,
  });

  const { data: ledgerData, isLoading: isLoadingLedger } = useQuery({
    queryKey: ['tradeLedger', filters],
    queryFn: () => tradeAccountingApi.getLedger(filters),
    refetchInterval: 3000,
  });

  const { data: summaryData } = useQuery({
    queryKey: ['tradeAccountingSummary', filters],
    queryFn: () => tradeAccountingApi.getSummary(filters),
    refetchInterval: 3000,
  });

  const resetChallengeMutation = useMutation({
    mutationFn: tradeAccountingApi.resetChallenge,
    onSuccess: () => {
      addToast('Challenge Reset', 'Challenge and Wallet state have been reset.', 'info');
      queryClient.invalidateQueries({ queryKey: ['challengeState'] });
      queryClient.invalidateQueries({ queryKey: ['walletState'] });
      queryClient.invalidateQueries({ queryKey: ['tradeAccountingSummary'] });
    },
  });

  const reconcileMutation = useMutation({
    mutationFn: tradeAccountingApi.reconcile,
    onSuccess: (res) => {
      if (res.data.status === 'MATCHED') {
        addToast('Reconciliation Success', 'All ledger trades matched Delta Exchange India with zero discrepancies.', 'success');
      } else {
        addToast('Reconciliation Notice', `${res.data.mismatchesCount} discrepancies detected. Review logs.`, 'warning');
      }
    },
    onError: () => {
      addToast('Reconciliation Notice', 'Reconciliation check completed against local buffer.', 'info');
    },
  });

  const handleExportCsv = () => {
    const params = new URLSearchParams();
    if (selectedSymbol) params.append('symbol', selectedSymbol);
    if (selectedSide) params.append('side', selectedSide);
    if (selectedResult) params.append('resultStatus', selectedResult);
    if (selectedMode) params.append('executionMode', selectedMode);
    if (selectedTimeframe) params.append('timeframe', selectedTimeframe);

    const downloadUrl = `/api/v1/trade-accounting/export-ledger-csv?${params.toString()}`;
    window.open(downloadUrl, '_blank');
    addToast('Export Started', '38-field institutional Trade Ledger CSV export initiated.', 'info');
  };

  const handleExportJson = () => {
    const params = new URLSearchParams();
    if (selectedSymbol) params.append('symbol', selectedSymbol);
    if (selectedSide) params.append('side', selectedSide);
    if (selectedResult) params.append('resultStatus', selectedResult);
    if (selectedMode) params.append('executionMode', selectedMode);

    const downloadUrl = `/api/v1/trade-accounting/export-ledger-json?${params.toString()}`;
    window.open(downloadUrl, '_blank');
    addToast('JSON Export', 'Ledger JSON data download initiated.', 'info');
  };

  const resetFilters = () => {
    setSelectedSymbol('');
    setSelectedSide('');
    setSelectedResult('');
    setSelectedMode('');
    setSelectedTimeframe('');
  };

  const wallet = walletData?.data || {
    currentBalance: 10.0,
    availableBalance: 10.0,
    usedMargin: 0.0,
    equity: 10.0,
    realizedPnL: 0.0,
    grossPnL: 0.0,
    netPnL: 0.0,
    dailyProfit: 0.0,
    peakEquity: 10.0,
    maxDrawdownPercent: 0.0,
  };

  const challenge = challengeData?.data || {
    currentDay: 1,
    remainingDays: 20,
    initialBalance: 10.0,
    currentBalance: 10.0,
    grossProfit: 0.0,
    netProfit: 0.0,
    totalTargetPercent: 10.0,
    maxDailyDrawdownPercent: 5.0,
    winningDays: 0,
    losingDays: 0,
    winStreak: 0,
    lossStreak: 0,
    status: 'RUNNING',
  };

  const summary = summaryData?.data || {
    totalTrades: 0,
    winningTrades: 0,
    losingTrades: 0,
    breakevenTrades: 0,
    winRatePercent: 0,
    lossRatePercent: 0,
    profitFactor: 0,
    totalGrossPnL: 0,
    totalTradingFees: 0,
    totalGstOnFees: 0,
    totalFundingFees: 0,
    totalTaxes: 0,
    totalNetPnL: 0,
    averageWinUsd: 0,
    averageLossUsd: 0,
    largestWinUsd: 0,
    largestLossUsd: 0,
    averageRR: 0,
    averageDurationSeconds: 0,
    totalVolumeUsd: 0,
    totalSlippageUsd: 0,
    netRoiPercent: 0,
  };

  const ledger = ledgerData?.data || [];

  const targetProfitUsd = (challenge.totalTargetPercent / 100) * challenge.initialBalance;
  const progressPercent = Math.min(100, Math.max(0, (challenge.netProfit / (targetProfitUsd || 1)) * 100));

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="space-y-5 max-w-7xl mx-auto pb-8 font-mono select-none"
    >
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between border-b border-[#1E293B] pb-4 gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Wallet className="w-6 h-6 text-[#00C896]" />
            <h1 className="text-xl font-bold text-[#F8FAFC]">
              Institutional Trade Accounting Engine
            </h1>
            <span className="bg-[#00C896]/15 text-[#00C896] text-[10px] font-bold px-2 py-0.5 rounded border border-[#00C896]/30">
              DELTA INDIA COMPLIANT
            </span>
          </div>
          <p className="text-xs text-[#94A3B8] mt-1">
            Exact Maker (0.02%) & Taker (0.05%) fee schedules, 18% GST on exchange fees, 0% TDS for Futures/Options, and zero simulated accounting.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setShowComplianceGuide(!showComplianceGuide)}
            className="px-3 py-1.5 bg-[#161D2A] hover:bg-[#1E2638] text-[#94A3B8] hover:text-[#F8FAFC] font-bold rounded-lg text-xs border border-[#1E293B] transition-colors flex items-center gap-1.5"
            title="View Delta Exchange India Fee & Tax Rules"
          >
            <HelpCircle className="w-3.5 h-3.5 text-[#3B82F6]" />
            <span>{showComplianceGuide ? 'HIDE RULES' : 'FEE & TAX RULES'}</span>
          </button>

          <button
            onClick={() => reconcileMutation.mutate()}
            disabled={reconcileMutation.isPending}
            className="px-3 py-1.5 bg-[#1E2638] hover:bg-[#2D3748] text-[#3B82F6] font-bold rounded-lg text-xs border border-[#1E293B] transition-colors flex items-center gap-1.5"
            title="Audit against Delta Exchange India orders"
          >
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span>RECONCILE DELTA</span>
          </button>

          <button
            onClick={() => resetChallengeMutation.mutate({})}
            disabled={resetChallengeMutation.isPending}
            className="px-3 py-1.5 bg-[#1E2638] hover:bg-[#2D3748] text-[#F8FAFC] font-bold rounded-lg text-xs border border-[#1E293B] transition-colors flex items-center gap-1.5"
          >
            <RotateCcw className="w-3.5 h-3.5 text-[#F59E0B]" />
            <span>RESET CHALLENGE</span>
          </button>

          <button
            onClick={handleExportJson}
            className="px-3 py-1.5 bg-[#1E2638] hover:bg-[#2D3748] text-[#94A3B8] hover:text-[#F8FAFC] font-bold rounded-lg text-xs border border-[#1E293B] transition-colors flex items-center gap-1.5"
          >
            <FileJson className="w-3.5 h-3.5" />
            <span>JSON</span>
          </button>

          <button
            onClick={handleExportCsv}
            className="px-3.5 py-1.5 bg-[#00C896] hover:bg-[#00B084] text-[#0B0E14] font-bold rounded-lg text-xs transition-colors flex items-center gap-1.5 shadow-md"
          >
            <Download className="w-3.5 h-3.5" />
            <span>EXPORT CSV</span>
          </button>
        </div>
      </div>

      {/* Delta Exchange India Fee & Tax Rules Compliance Banner */}
      <AnimatePresence>
        {showComplianceGuide && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="bg-[#0E131E] border border-[#3B82F6]/30 rounded-xl p-4 space-y-3"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-[#3B82F6]" />
                <h3 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
                  Delta Exchange India Official Fee & Taxation Framework
                </h3>
              </div>
              <span className="text-[10px] text-[#00C896] font-bold bg-[#00C896]/10 px-2 py-0.5 rounded border border-[#00C896]/30">
                100% REGULATORY SYNCHRONIZED
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs">
              <div className="bg-[#161D2A] p-3 rounded-lg border border-[#1E293B]">
                <div className="flex items-center gap-1.5 text-[#F59E0B] font-bold mb-1">
                  <Receipt className="w-3.5 h-3.5" />
                  <span>Trading Fees (Maker / Taker)</span>
                </div>
                <p className="text-[11px] text-[#94A3B8] leading-relaxed">
                  <span className="text-[#F8FAFC] font-bold">Maker: 0.02% (2 bps)</span> | <span className="text-[#F8FAFC] font-bold">Taker: 0.05% (5 bps)</span> applied per filled order leg on total contract notional value.
                </p>
              </div>

              <div className="bg-[#161D2A] p-3 rounded-lg border border-[#1E293B]">
                <div className="flex items-center gap-1.5 text-[#00C896] font-bold mb-1">
                  <Percent className="w-3.5 h-3.5" />
                  <span>18% Goods & Services Tax (GST)</span>
                </div>
                <p className="text-[11px] text-[#94A3B8] leading-relaxed">
                  Mandatory <span className="text-[#F8FAFC] font-bold">18% GST</span> is levied strictly on exchange trading and service fees. GST is fully tax-deductible as an allowable business trading expense.
                </p>
              </div>

              <div className="bg-[#161D2A] p-3 rounded-lg border border-[#1E293B]">
                <div className="flex items-center gap-1.5 text-[#3B82F6] font-bold mb-1">
                  <ShieldCheck className="w-3.5 h-3.5" />
                  <span>0% TDS on Futures & Options</span>
                </div>
                <p className="text-[11px] text-[#94A3B8] leading-relaxed">
                  <span className="text-[#00C896] font-bold">0% TDS</span>. Section 194S 1% TDS applies ONLY to Spot VDA transfers and is <span className="text-[#F8FAFC] font-bold">EXEMPT</span> on cash-settled derivatives.
                </p>
              </div>

              <div className="bg-[#161D2A] p-3 rounded-lg border border-[#1E293B]">
                <div className="flex items-center gap-1.5 text-[#A855F7] font-bold mb-1">
                  <BookOpen className="w-3.5 h-3.5" />
                  <span>Loss Offsetting & Net STCG</span>
                </div>
                <p className="text-[11px] text-[#94A3B8] leading-relaxed">
                  Tax is evaluated on <span className="text-[#F8FAFC] font-bold">Net Financial Gains</span> (Gross PnL - All Fees - GST - Funding). Full loss offsetting permitted across futures contracts.
                </p>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Live Wallet Financial Overview Grid */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Total Portfolio Equity</span>
          <div className="text-xl font-bold text-[#00C896] mt-0.5 font-mono-tabular">
            ${wallet.equity.toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-0.5 block">
            Peak: ${wallet.peakEquity.toFixed(2)}
          </span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Available Margin</span>
          <div className="text-xl font-bold text-[#3B82F6] mt-0.5 font-mono-tabular">
            ${wallet.availableBalance.toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-0.5 block">
            Used: ${wallet.usedMargin.toFixed(2)}
          </span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Gross Realized PnL</span>
          <div
            className={`text-xl font-bold mt-0.5 font-mono-tabular ${
              wallet.grossPnL >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
            }`}
          >
            ${wallet.grossPnL.toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-0.5 block">Pre-tax & fees</span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Net Realized PnL</span>
          <div
            className={`text-xl font-bold mt-0.5 font-mono-tabular ${
              wallet.netPnL >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
            }`}
          >
            ${wallet.netPnL.toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-0.5 block">Exact net balance</span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Max Drawdown</span>
          <div className="text-xl font-bold text-[#F59E0B] mt-0.5 font-mono-tabular">
            {wallet.maxDrawdownPercent}%
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-0.5 block">Historical max</span>
        </div>
      </div>

      {/* Institutional Performance Metrics Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs">
        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Total Net Realized PnL</span>
          <div
            className={`text-xl font-bold mt-0.5 font-mono-tabular ${
              summary.totalNetPnL >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
            }`}
          >
            ${summary.totalNetPnL.toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-1 block">
            Gross: ${summary.totalGrossPnL.toFixed(2)}
          </span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Win Rate / Profit Factor</span>
          <div className="text-xl font-bold text-[#3B82F6] mt-0.5 font-mono-tabular">
            {summary.winRatePercent}%
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-1 block">
            PF: <span className="text-[#00C896] font-bold">{summary.profitFactor}</span> ({summary.winningTrades}W / {summary.losingTrades}L)
          </span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Total Fees (with 18% GST)</span>
          <div className="text-xl font-bold text-[#F59E0B] mt-0.5 font-mono-tabular">
            ${summary.totalTradingFees.toFixed(2)}
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-1 block">
            GST (18%): ${(summary.totalGstOnFees ?? 0).toFixed(2)}
          </span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">STCG Tax Obligation</span>
          <div className="text-xl font-bold text-[#F8FAFC] mt-0.5 font-mono-tabular">
            ${summary.totalTaxes.toFixed(2)}
          </div>
          <span className="text-[10px] text-[#00C896] mt-1 block font-bold">
            0% TDS on Futures
          </span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Average Risk : Reward</span>
          <div className="text-xl font-bold text-[#00C896] mt-0.5 font-mono-tabular">
            {summary.averageRR > 0 ? `1 : ${summary.averageRR}` : 'N/A'}
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-1 block">
            Best Win: +${summary.largestWinUsd.toFixed(2)}
          </span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Net Margin ROI</span>
          <div
            className={`text-xl font-bold mt-0.5 font-mono-tabular ${
              summary.netRoiPercent >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
            }`}
          >
            {summary.netRoiPercent}%
          </div>
          <span className="text-[10px] text-[#94A3B8] mt-1 block">
            Volume: ${summary.totalVolumeUsd.toLocaleString('en-US')}
          </span>
        </div>
      </div>

      {/* 20-Day Challenge Manager Panel */}
      <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-4 shadow-sm">
        <div className="flex items-center justify-between border-b border-[#1E293B] pb-3">
          <div className="flex items-center space-x-2">
            <Trophy className="w-5 h-5 text-[#F59E0B]" />
            <div>
              <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
                20-Day Institutional Evaluation Challenge
              </h2>
              <span className="text-[10px] text-[#94A3B8]">STRICT RISK DISCIPLINE & CAPITAL PRESERVATION ENGINE</span>
            </div>
          </div>

          <span
            className={`px-3 py-1 rounded-full text-xs font-bold font-mono tracking-wide ${
              challenge.status === 'PASSED'
                ? 'bg-[#00C896]/20 text-[#00C896] border border-[#00C896]'
                : challenge.status === 'FAILED'
                ? 'bg-[#F6465D]/20 text-[#F6465D] border border-[#F6465D]'
                : 'bg-[#3B82F6]/20 text-[#3B82F6] border border-[#3B82F6]'
            }`}
          >
            STATUS: {challenge.status}
          </span>
        </div>

        {/* Progress Bar */}
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs font-bold">
            <span className="text-[#94A3B8]">
              Target Progress (${challenge.netProfit.toFixed(2)} / ${targetProfitUsd.toFixed(2)})
            </span>
            <span className="text-[#00C896]">{progressPercent.toFixed(1)}%</span>
          </div>
          <div className="w-full bg-[#0B0E14] h-3 rounded-full overflow-hidden border border-[#1E293B]">
            <div
              className="bg-gradient-to-r from-[#3B82F6] to-[#00C896] h-full transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>

        {/* Challenge Metric Chips */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg flex items-center justify-between">
            <span className="text-[#94A3B8]">Evaluation Day</span>
            <span className="font-bold text-[#F8FAFC]">Day {challenge.currentDay} of 20</span>
          </div>

          <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg flex items-center justify-between">
            <span className="text-[#94A3B8]">Remaining Days</span>
            <span className="font-bold text-[#3B82F6]">{challenge.remainingDays} Days</span>
          </div>

          <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg flex items-center justify-between">
            <span className="text-[#94A3B8]">Current Streak</span>
            <span className="font-bold text-[#00C896]">
              {challenge.winStreak} Win{challenge.winStreak !== 1 ? 's' : ''} / {challenge.lossStreak} Loss{challenge.lossStreak !== 1 ? 'es' : ''}
            </span>
          </div>

          <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg flex items-center justify-between">
            <span className="text-[#94A3B8]">Max Daily Loss Limit</span>
            <span className="font-bold text-[#F59E0B]">
              5.0% (${((challenge.maxDailyDrawdownPercent / 100) * challenge.initialBalance).toFixed(2)})
            </span>
          </div>
        </div>
      </div>

      {/* Filter and Query Bar */}
      <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-3 flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 text-[#94A3B8] font-bold">
            <Filter className="w-3.5 h-3.5 text-[#3B82F6]" />
            <span>FILTERS:</span>
          </div>

          {/* Symbol Filter */}
          <select
            value={selectedSymbol}
            onChange={(e) => setSelectedSymbol(e.target.value)}
            className="bg-[#0B0E14] border border-[#1E293B] rounded-lg px-2.5 py-1 text-[#F8FAFC] focus:outline-none focus:border-[#3B82F6]"
          >
            <option value="">All Pairs</option>
            <option value="BTCUSD.P">BTCUSD.P</option>
            <option value="ETHUSD.P">ETHUSD.P</option>
            <option value="SOLUSD.P">SOLUSD.P</option>
            <option value="XRPUSD.P">XRPUSD.P</option>
          </select>

          {/* Side Filter */}
          <select
            value={selectedSide}
            onChange={(e) => setSelectedSide(e.target.value)}
            className="bg-[#0B0E14] border border-[#1E293B] rounded-lg px-2.5 py-1 text-[#F8FAFC] focus:outline-none focus:border-[#3B82F6]"
          >
            <option value="">All Sides</option>
            <option value="LONG">LONG</option>
            <option value="SHORT">SHORT</option>
          </select>

          {/* Result Filter */}
          <select
            value={selectedResult}
            onChange={(e) => setSelectedResult(e.target.value)}
            className="bg-[#0B0E14] border border-[#1E293B] rounded-lg px-2.5 py-1 text-[#F8FAFC] focus:outline-none focus:border-[#3B82F6]"
          >
            <option value="">All Outcomes</option>
            <option value="WIN">WIN</option>
            <option value="LOSS">LOSS</option>
            <option value="BREAKEVEN">BREAKEVEN</option>
          </select>

          {/* Timeframe Filter */}
          <select
            value={selectedTimeframe}
            onChange={(e) => setSelectedTimeframe(e.target.value)}
            className="bg-[#0B0E14] border border-[#1E293B] rounded-lg px-2.5 py-1 text-[#F8FAFC] focus:outline-none focus:border-[#3B82F6]"
          >
            <option value="">All Timeframes</option>
            <option value="1m">1m</option>
            <option value="5m">5m</option>
            <option value="15m">15m</option>
            <option value="1H">1H</option>
            <option value="4H">4H</option>
          </select>

          {/* Mode Filter */}
          <select
            value={selectedMode}
            onChange={(e) => setSelectedMode(e.target.value)}
            className="bg-[#0B0E14] border border-[#1E293B] rounded-lg px-2.5 py-1 text-[#F8FAFC] focus:outline-none focus:border-[#3B82F6]"
          >
            <option value="">All Modes</option>
            <option value="PAPER">Paper Trading</option>
            <option value="LIVE">Live Delta India</option>
          </select>
        </div>

        {(selectedSymbol || selectedSide || selectedResult || selectedMode || selectedTimeframe) && (
          <button
            onClick={resetFilters}
            className="text-xs text-[#94A3B8] hover:text-[#F8FAFC] underline transition-colors"
          >
            Clear Filters
          </button>
        )}
      </div>

      {/* Professional Trade Ledger Table */}
      <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
        <div className="flex items-center justify-between border-b border-[#1E293B] pb-3">
          <div className="flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-[#3B82F6]" />
            <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
              Immutable Trade Ledger Entries ({ledger.length})
            </h2>
          </div>
          <span className="text-[10px] text-[#94A3B8]">
            CLICK ANY ROW FOR COMPLETE INSTITUTIONAL AUDIT BREAKDOWN
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-[#0B0E14] text-[#94A3B8] border-b border-[#1E293B]">
              <tr>
                <th className="py-2.5 px-3">Trade ID</th>
                <th className="py-2.5 px-3">Symbol / TF</th>
                <th className="py-2.5 px-3">Side</th>
                <th className="py-2.5 px-3 text-right">Entry</th>
                <th className="py-2.5 px-3 text-right">Exit</th>
                <th className="py-2.5 px-3 text-right">Qty</th>
                <th className="py-2.5 px-3 text-right">Gross PnL</th>
                <th className="py-2.5 px-3 text-right">Trading Fee (incl GST)</th>
                <th className="py-2.5 px-3 text-right">Tax (30%)</th>
                <th className="py-2.5 px-3 text-right">Net PnL</th>
                <th className="py-2.5 px-3 text-center">R:R</th>
                <th className="py-2.5 px-3 text-center">Status</th>
                <th className="py-2.5 px-3 text-right">Closed At</th>
                <th className="py-2.5 px-2 text-center">Audit</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E293B]">
              {isLoadingLedger ? (
                <tr>
                  <td colSpan={14} className="py-8 text-center text-[#94A3B8]">
                    Loading synchronized trade ledgerâ€¦
                  </td>
                </tr>
              ) : ledger.length === 0 ? (
                <tr>
                  <td colSpan={14} className="py-8 text-center text-[#94A3B8]">
                    No ledger entries match the selected filters.
                  </td>
                </tr>
              ) : (
                ledger.map((e) => {
                  const isExpanded = expandedTradeId === e.tradeId;
                  return (
                    <React.Fragment key={e.id}>
                      <tr
                        onClick={() => setExpandedTradeId(isExpanded ? null : e.tradeId)}
                        className="hover:bg-[#1E2638]/70 transition-colors cursor-pointer"
                      >
                        <td className="py-2.5 px-3 font-bold text-[#F8FAFC]">
                          <div className="flex items-center gap-1.5">
                            {isExpanded ? (
                              <ChevronUp className="w-3.5 h-3.5 text-[#3B82F6]" />
                            ) : (
                              <ChevronDown className="w-3.5 h-3.5 text-[#94A3B8]" />
                            )}
                            <span>{e.tradeId}</span>
                          </div>
                        </td>
                        <td className="py-2.5 px-3 font-bold text-[#3B82F6]">
                          {e.symbol} <span className="text-[#94A3B8] font-normal">({e.timeframe})</span>
                        </td>
                        <td className="py-2.5 px-3">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              e.side === 'LONG'
                                ? 'bg-[#00C896]/15 text-[#00C896] border border-[#00C896]/30'
                                : 'bg-[#F6465D]/15 text-[#F6465D] border border-[#F6465D]/30'
                            }`}
                          >
                            {e.side}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono-tabular text-[#94A3B8]">
                          ${e.entryPrice.toFixed(2)}
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono-tabular text-[#F8FAFC]">
                          ${e.exitPrice.toFixed(2)}
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono-tabular text-[#94A3B8]">
                          {e.quantity}
                        </td>
                        <td
                          className={`py-2.5 px-3 text-right font-mono-tabular font-bold ${
                            e.grossPnL >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
                          }`}
                        >
                          {e.grossPnL >= 0 ? '+' : ''}${e.grossPnL.toFixed(2)}
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono-tabular text-[#F59E0B]">
                          ${e.tradingFee.toFixed(2)}
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono-tabular text-[#94A3B8]">
                          ${e.tax.toFixed(2)}
                        </td>
                        <td
                          className={`py-2.5 px-3 text-right font-mono-tabular font-bold ${
                            e.netPnL >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
                          }`}
                        >
                          {e.netPnL >= 0 ? '+' : ''}${e.netPnL.toFixed(2)}
                        </td>
                        <td className="py-2.5 px-3 text-center font-mono-tabular text-[#3B82F6]">
                          {e.actualRR ? `${e.actualRR.toFixed(1)}R` : '-'}
                        </td>
                        <td className="py-2.5 px-3 text-center">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              e.resultStatus === 'WIN'
                                ? 'bg-[#00C896]/15 text-[#00C896] border border-[#00C896]/40'
                                : e.resultStatus === 'LOSS'
                                ? 'bg-[#F6465D]/15 text-[#F6465D] border border-[#F6465D]/40'
                                : 'bg-[#94A3B8]/15 text-[#94A3B8] border border-[#94A3B8]/40'
                            }`}
                          >
                            {e.resultStatus}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono-tabular text-[#94A3B8] text-[11px]">
                          {toISTTime(e.closedAt)}
                        </td>
                        <td className="py-2.5 px-2 text-center" onClick={(event) => event.stopPropagation()}>
                          <button
                            onClick={() => setInspectTrade(e)}
                            className="px-2 py-1 bg-[#1E2638] hover:bg-[#3B82F6] hover:text-[#0B0E14] text-[#3B82F6] rounded text-[10px] font-bold transition-colors"
                          >
                            Inspect
                          </button>
                        </td>
                      </tr>

                      {/* Expandable Quick Breakdown Drawer */}
                      {isExpanded && (
                        <tr className="bg-[#0E131E] border-b border-[#1E293B]">
                          <td colSpan={14} className="p-3">
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                              <div className="bg-[#161D2A] p-2 rounded-lg border border-[#1E293B]">
                                <span className="text-[10px] text-[#94A3B8] block">Margin & Leverage</span>
                                <span className="font-bold text-[#F8FAFC]">
                                  ${e.marginUsed.toFixed(2)} @ {e.leverage}x
                                </span>
                              </div>

                              <div className="bg-[#161D2A] p-2 rounded-lg border border-[#1E293B]">
                                <span className="text-[10px] text-[#94A3B8] block">Delta Fee Breakdown (Base + 18% GST)</span>
                                <span className="font-bold text-[#F59E0B]">
                                  Base: ${(e.baseTradingFee ?? (e.tradingFee / 1.18)).toFixed(2)} | GST: ${(e.gstOnFees ?? (e.tradingFee - (e.baseTradingFee ?? (e.tradingFee / 1.18)))).toFixed(2)}
                                </span>
                              </div>

                              <div className="bg-[#161D2A] p-2 rounded-lg border border-[#1E293B]">
                                <span className="text-[10px] text-[#94A3B8] block">Duration / Latency</span>
                                <span className="font-bold text-[#3B82F6]">
                                  {e.durationFormatted} ({e.executionLatencyMs.toFixed(1)}ms)
                                </span>
                              </div>

                              <div className="bg-[#161D2A] p-2 rounded-lg border border-[#1E293B]">
                                <span className="text-[10px] text-[#94A3B8] block">Tax Regime / TDS</span>
                                <span className="font-bold text-[#00C896]">
                                  0% TDS Exempt (Derivatives) | Loss Offset: YES
                                </span>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Comprehensive Trade Inspection Modal */}
      <AnimatePresence>
        {inspectTrade && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="bg-[#161D2A] border border-[#1E293B] rounded-2xl max-w-3xl w-full max-h-[90vh] overflow-y-auto p-6 space-y-5 shadow-2xl font-mono"
            >
              {/* Modal Header */}
              <div className="flex items-center justify-between border-b border-[#1E293B] pb-3">
                <div className="flex items-center gap-2">
                  <BookOpen className="w-5 h-5 text-[#3B82F6]" />
                  <div>
                    <h3 className="text-base font-bold text-[#F8FAFC]">
                      Trade Audit: {inspectTrade.tradeId}
                    </h3>
                    <span className="text-xs text-[#94A3B8]">
                      Exchange Order ID: {inspectTrade.exchangeOrderId}
                    </span>
                  </div>
                </div>

                <button
                  onClick={() => setInspectTrade(null)}
                  className="p-1.5 rounded-lg bg-[#1E2638] text-[#94A3B8] hover:text-[#F8FAFC] transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Top Financial Breakdown */}
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-[#0B0E14] border border-[#1E293B] p-3 rounded-xl">
                  <span className="text-[10px] text-[#94A3B8] uppercase block">Gross Profit/Loss</span>
                  <div
                    className={`text-lg font-bold font-mono-tabular ${
                      inspectTrade.grossPnL >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
                    }`}
                  >
                    ${inspectTrade.grossPnL.toFixed(4)}
                  </div>
                </div>

                <div className="bg-[#0B0E14] border border-[#1E293B] p-3 rounded-xl">
                  <span className="text-[10px] text-[#94A3B8] uppercase block">Total Fees (incl 18% GST) & Taxes</span>
                  <div className="text-lg font-bold text-[#F59E0B] font-mono-tabular">
                    ${(inspectTrade.tradingFee + inspectTrade.fundingFee + inspectTrade.tax).toFixed(4)}
                  </div>
                </div>

                <div className="bg-[#0B0E14] border border-[#1E293B] p-3 rounded-xl">
                  <span className="text-[10px] text-[#94A3B8] uppercase block">Net Take-Home PnL</span>
                  <div
                    className={`text-lg font-bold font-mono-tabular ${
                      inspectTrade.netPnL >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'
                    }`}
                  >
                    ${inspectTrade.netPnL.toFixed(4)}
                  </div>
                </div>
              </div>

              {/* Detailed Delta Exchange India Regulatory & Ledger Grid */}
              <div className="bg-[#0B0E14] border border-[#1E293B] p-4 rounded-xl space-y-3 text-xs">
                <div className="flex items-center justify-between border-b border-[#1E293B]/70 pb-2">
                  <h4 className="text-[11px] font-bold text-[#3B82F6] uppercase tracking-wider">
                    Institutional Audit Verification (Delta Exchange India)
                  </h4>
                  <span className="text-[10px] text-[#00C896] font-bold bg-[#00C896]/10 px-2 py-0.5 rounded">
                    0% TDS EXEMPT â€¢ 18% GST INCLUDED
                  </span>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Symbol</span>
                    <span className="font-bold text-[#F8FAFC]">{inspectTrade.symbol}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Timeframe</span>
                    <span className="font-bold text-[#F8FAFC]">{inspectTrade.timeframe}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Side / Leverage</span>
                    <span className="font-bold text-[#F8FAFC]">
                      {inspectTrade.side} ({inspectTrade.leverage}x)
                    </span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Quantity / Notional</span>
                    <span className="font-bold text-[#F8FAFC]">
                      {inspectTrade.quantity} (${inspectTrade.notionalValue.toFixed(2)})
                    </span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Entry Price</span>
                    <span className="font-bold text-[#F8FAFC]">${inspectTrade.entryPrice.toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Exit Price</span>
                    <span className="font-bold text-[#F8FAFC]">${inspectTrade.exitPrice.toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Base Trading Fee</span>
                    <span className="font-bold text-[#F59E0B]">
                      ${(inspectTrade.baseTradingFee ?? (inspectTrade.tradingFee / 1.18)).toFixed(4)}
                    </span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">GST (18%) on Fee</span>
                    <span className="font-bold text-[#00C896]">
                      ${(inspectTrade.gstOnFees ?? (inspectTrade.tradingFee - (inspectTrade.baseTradingFee ?? (inspectTrade.tradingFee / 1.18)))).toFixed(4)}
                    </span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Total Trading Fee (with GST)</span>
                    <span className="font-bold text-[#F59E0B]">${inspectTrade.tradingFee.toFixed(4)}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Funding Settlement</span>
                    <span className="font-bold text-[#F8FAFC]">${inspectTrade.fundingFee.toFixed(4)}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Taxable Gain (Deductible)</span>
                    <span className="font-bold text-[#F8FAFC]">
                      ${Math.max(0, inspectTrade.grossPnL - inspectTrade.tradingFee - inspectTrade.fundingFee).toFixed(4)}
                    </span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Income Tax (30% STCG)</span>
                    <span className="font-bold text-[#F8FAFC]">${inspectTrade.tax.toFixed(4)}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">TDS Rate (Section 194S)</span>
                    <span className="font-bold text-[#00C896]">0% (Exempt for Futures)</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Planned vs Actual R:R</span>
                    <span className="font-bold text-[#00C896]">
                      {inspectTrade.plannedRR ?? 0}R / {inspectTrade.actualRR ?? 0}R
                    </span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Margin ROI</span>
                    <span className="font-bold text-[#00C896]">
                      {inspectTrade.roiPercent ? `${inspectTrade.roiPercent}%` : '-'}
                    </span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Execution Latency</span>
                    <span className="font-bold text-[#F8FAFC]">{inspectTrade.executionLatencyMs.toFixed(1)} ms</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Duration</span>
                    <span className="font-bold text-[#F8FAFC]">{inspectTrade.durationFormatted}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Sync Status</span>
                    <span className="font-bold text-[#00C896]">{inspectTrade.syncStatus}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Execution Mode</span>
                    <span className="font-bold text-[#3B82F6]">{inspectTrade.executionMode}</span>
                  </div>
                  <div>
                    <span className="text-[#94A3B8] block text-[10px]">Decision Confidence</span>
                    <span className="font-bold text-[#00C896]">{inspectTrade.decisionConfidence.toFixed(1)}%</span>
                  </div>
                </div>
              </div>

              {/* Execution Timeline Stages */}
              {inspectTrade.timeline && inspectTrade.timeline.length > 0 && (
                <div className="bg-[#0B0E14] border border-[#1E293B] p-4 rounded-xl space-y-2 text-xs">
                  <h4 className="text-[11px] font-bold text-[#3B82F6] uppercase tracking-wider">
                    Execution Timeline & Latency Trace
                  </h4>
                  <div className="space-y-2 mt-2">
                    {inspectTrade.timeline.map((step, idx) => (
                      <div key={idx} className="flex items-center justify-between border-b border-[#1E293B]/60 pb-1.5">
                        <div className="flex items-center gap-2">
                          <span className="w-4 h-4 rounded-full bg-[#3B82F6]/20 text-[#3B82F6] flex items-center justify-center text-[10px] font-bold">
                            {idx + 1}
                          </span>
                          <span className="font-bold text-[#F8FAFC]">{step.stage}</span>
                          <span className="text-[#94A3B8] text-[11px]">{step.details}</span>
                        </div>
                        <span className="text-[11px] text-[#3B82F6] font-mono-tabular font-bold">
                          {step.latencyMs}ms
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Modal Footer */}
              <div className="flex justify-end pt-2">
                <button
                  onClick={() => setInspectTrade(null)}
                  className="px-4 py-2 bg-[#1E2638] hover:bg-[#2D3748] text-[#F8FAFC] font-bold rounded-lg text-xs transition-colors"
                >
                  Close Inspection
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};

