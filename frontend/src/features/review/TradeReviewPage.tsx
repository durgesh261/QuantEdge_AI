import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { tradeReviewApi } from '../../services/api';
import { useToastStore } from '../../store/useToastStore';
import { 
  BookOpen, 
  Sparkles, 
  Download, 
  Save, 
  CheckCircle2, 
  BarChart2
} from 'lucide-react';

import { useTerminalStore } from '../../store/useTerminalStore';

export const TradeReviewPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { addToast } = useToastStore();
  const { activeSymbol } = useTerminalStore();

  const [activeTradeId, setActiveTradeId] = useState<string>('SAMPLE-TRD-1');

  const { data: reviewData } = useQuery({
    queryKey: ['tradeReview', activeTradeId],
    queryFn: () => tradeReviewApi.getReview(activeTradeId),
  });

  const { data: perfData } = useQuery({
    queryKey: ['performanceSummary'],
    queryFn: tradeReviewApi.getPerformanceSummary,
  });

  const detail = reviewData?.data;
  const perf = perfData?.data || {
    dailyReviewNetPnL: 0.0,
    weeklyReviewNetPnL: 0.0,
    monthlyReviewNetPnL: 0.0,
    bestTradePnL: 0.0,
    worstTradePnL: 0.0,
    avgHoldTimeMinutes: 0,
    avgWinUsd: 0.0,
    avgLossUsd: 0.0,
  };

  const [journalForm, setJournalForm] = useState({
    idea: '1H Demand Zone Retest with Liquidity Sweep',
    whyEntered: 'Strong bullish engulfing candle following PAT Order Block mitigation.',
    whyExited: 'Take Profit hit at supply zone boundary target.',
    mistakes: 'None observed. Followed trade plan strictly.',
    lessons: 'Patience at demand zone boundaries improves win rate.',
    emotion: 'CALM',
    confidenceBefore: 9,
    confidenceAfter: 10,
    improvementNotes: 'Continue using strict stop-loss rules.',
    tags: 'DEMAND_ZONE, SMC_SWEEP, WINNER',
    isFavorite: true,
  });

  const saveJournalMutation = useMutation({
    mutationFn: (noteData: any) => tradeReviewApi.saveJournalNote(activeTradeId, noteData),
    onSuccess: () => {
      addToast('Journal Saved', 'Trader review notes updated successfully', 'success');
      queryClient.invalidateQueries({ queryKey: ['tradeReview', activeTradeId] });
    },
  });

  const handleSaveJournal = () => {
    saveJournalMutation.mutate({
      ...journalForm,
      tags: journalForm.tags.split(',').map((t) => t.trim()),
    });
  };

  const handleExportCsv = () => {
    const downloadUrl = `/api/v1/trade-review/${activeTradeId}/export-csv`;
    window.open(downloadUrl, '_blank');
    addToast('Export Started', 'Trade Review CSV export initiated', 'info');
  };

  const handleExportJson = () => {
    const downloadUrl = `/api/v1/trade-review/${activeTradeId}/export-json`;
    window.open(downloadUrl, '_blank');
    addToast('Export Started', 'Trade Review JSON export initiated', 'info');
  };

  const ledger = detail?.ledgerEntry || {
    id: activeTradeId,
    tradeId: activeTradeId,
    symbol: activeSymbol || 'BTCUSD.P',
    side: 'LONG',
    entryPrice: 0.0,
    exitPrice: 0.0,
    marginUsed: 0.0,
    leverage: 10.0,
    stopLoss: 0.0,
    takeProfit: 0.0,
    grossPnL: 0.0,
    tradingFee: 0.0,
    netPnL: 0.0,
    decisionConfidence: 0.0,
    resultStatus: 'UNREVIEWED',
  };

  const aiReview = detail?.aiReview || {
    tradeSummary: 'Select a trade or run a paper trade cycle to inspect AI trade review metrics.',
    decisionSummary: 'Signal analysis and decision explanation are populated from live trade ledger records.',
    strengths: ['No historical trade selected.'],
    weaknesses: ['No weaknesses recorded.'],
    riskAnalysis: 'Risk analysis will be calculated upon trade execution.',
    challengeImpact: 'Challenge progress will update on trade settlement.',
    improvementSuggestions: ['Execute trades via Paper Trading terminal to populate AI review.'],
  };

  const chartSnap = detail?.chartSnapshot || {
    entryPrice: 63850.0,
    exitPrice: 65200.0,
    stopLossPrice: 63250.0,
    takeProfitPrice: 65800.0,
    supplyZoneRange: '[65800.0 - 66787.0]',
    demandZoneRange: '[63211.5 - 63850.0]',
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="space-y-5 max-w-7xl mx-auto pb-6 font-mono select-none"
    >
      {/* Page Header */}
      <div className="flex items-center justify-between border-b border-[#1E293B] pb-3">
        <div>
          <h1 className="text-xl font-bold text-[#F8FAFC] flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-[#3B82F6]" />
            Professional Trade Review Workspace & Journal
          </h1>
          <p className="text-xs text-[#94A3B8] mt-0.5">
            Post-trade review, deterministic chart reconstruction, AI performance analysis, and trader journal.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleExportCsv}
            className="px-3 py-1.5 bg-[#1E2638] hover:bg-[#2D3748] text-[#F8FAFC] font-bold rounded-lg text-xs border border-[#1E293B] transition-colors flex items-center gap-1.5"
          >
            <Download className="w-3.5 h-3.5 text-[#3B82F6]" />
            <span>EXPORT CSV</span>
          </button>

          <button
            onClick={handleExportJson}
            className="px-3 py-1.5 bg-[#3B82F6] hover:bg-[#2563EB] text-white font-bold rounded-lg text-xs transition-colors flex items-center gap-1.5 shadow-md"
          >
            <Download className="w-3.5 h-3.5" />
            <span>EXPORT JSON</span>
          </button>
        </div>
      </div>

      {/* Trade Selector Bar & Financial Metrics Row */}
      <div className="bg-[#161D2A] border border-[#1E293B] p-4 rounded-xl space-y-3">
        <div className="flex items-center justify-between border-b border-[#1E293B] pb-2 text-xs">
          <div className="flex items-center space-x-3">
            <span className="text-[#94A3B8]">Select Completed Trade:</span>
            <input
              type="text"
              value={activeTradeId}
              onChange={(e) => setActiveTradeId(e.target.value)}
              className="bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] font-bold rounded px-2.5 py-1 text-xs"
            />
          </div>

          <div className="flex items-center space-x-3">
            <span className="text-[11px] text-[#94A3B8]">Weekly Net: ${perf.weeklyReviewNetPnL}</span>
            <span
              className={`px-3 py-1 rounded-full text-xs font-bold font-mono tracking-wide ${
                ledger.resultStatus === 'WIN'
                  ? 'bg-[#00C896]/20 text-[#00C896] border border-[#00C896]'
                  : 'bg-[#F6465D]/20 text-[#F6465D] border border-[#F6465D]'
              }`}
            >
              STATUS: {ledger.resultStatus} (+${ledger.netPnL})
            </span>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 text-xs">
          <div>
            <span className="text-[10px] text-[#94A3B8] uppercase block">Symbol / Side</span>
            <span className="text-sm font-bold text-[#3B82F6] font-mono-tabular">{ledger.symbol} ({ledger.side})</span>
          </div>

          <div>
            <span className="text-[10px] text-[#94A3B8] uppercase block">Entry / Exit</span>
            <span className="text-sm font-bold text-[#F8FAFC] font-mono-tabular">${ledger.entryPrice} → ${ledger.exitPrice}</span>
          </div>

          <div>
            <span className="text-[10px] text-[#94A3B8] uppercase block">Margin / Lev</span>
            <span className="text-sm font-bold text-[#F8FAFC] font-mono-tabular">${ledger.marginUsed} ({ledger.leverage}x)</span>
          </div>

          <div>
            <span className="text-[10px] text-[#94A3B8] uppercase block">Gross PnL</span>
            <span className="text-sm font-bold text-[#00C896] font-mono-tabular">${ledger.grossPnL}</span>
          </div>

          <div>
            <span className="text-[10px] text-[#94A3B8] uppercase block">Trading Fees</span>
            <span className="text-sm font-bold text-[#F59E0B] font-mono-tabular">${ledger.tradingFee}</span>
          </div>

          <div>
            <span className="text-[10px] text-[#94A3B8] uppercase block">Net PnL (Post-Fees)</span>
            <span className="text-sm font-bold text-[#00C896] font-mono-tabular">${ledger.netPnL}</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Deterministic Chart Reconstruction & Zone Snapshot */}
        <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
          <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
            <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider flex items-center gap-2">
              <BarChart2 className="w-4 h-4 text-[#3B82F6]" />
              Deterministic Chart Reconstruction & Zone Snapshot
            </h2>
            <span className="text-[10px] text-[#94A3B8]">PAT & SMC ZONES AT ENTRY</span>
          </div>

          <div className="bg-[#0B0E14] border border-[#1E293B] p-3 rounded-lg text-xs space-y-2">
            <div className="flex justify-between border-b border-[#1E293B] pb-1.5">
              <span className="text-[#94A3B8]">Active Supply Zone (Target):</span>
              <span className="font-bold text-[#F6465D] font-mono-tabular">{chartSnap.supplyZoneRange}</span>
            </div>

            <div className="flex justify-between border-b border-[#1E293B] pb-1.5">
              <span className="text-[#94A3B8]">Active Demand Zone (Entry):</span>
              <span className="font-bold text-[#00C896] font-mono-tabular">{chartSnap.demandZoneRange}</span>
            </div>

            <div className="flex justify-between">
              <span className="text-[#94A3B8]">Stop Loss Level:</span>
              <span className="font-bold text-[#F59E0B] font-mono-tabular">${chartSnap.stopLossPrice}</span>
            </div>
          </div>
        </div>

        {/* AI Performance Review Engine */}
        <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
          <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
            <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-[#8B5CF6]" />
              AI Trade Performance Review
            </h2>
            <span className="text-[10px] text-[#94A3B8]">DETERMINISTIC ANALYSIS</span>
          </div>

          <div className="space-y-2 text-xs">
            <p className="text-[#F8FAFC] font-bold">{aiReview.tradeSummary}</p>
            <p className="text-[#94A3B8] text-[11px]">{aiReview.riskAnalysis}</p>
            <p className="text-[#00C896] text-[11px]">{aiReview.challengeImpact}</p>

            <div className="border-t border-[#1E293B] pt-2 space-y-1">
              <span className="text-[10px] text-[#94A3B8] uppercase block">Key Improvement Suggestions:</span>
              {aiReview.improvementSuggestions.map((s, idx) => (
                <div key={idx} className="flex items-center space-x-1.5 text-[11px] text-[#F8FAFC]">
                  <CheckCircle2 className="w-3 h-3 text-[#3B82F6] shrink-0" />
                  <span>{s}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Personal Trade Journal Editor */}
      <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-4 shadow-sm">
        <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
          <div className="flex items-center space-x-2">
            <BookOpen className="w-4 h-4 text-[#3B82F6]" />
            <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
              Personal Trader Journal & Emotion Log
            </h2>
          </div>

          <button
            onClick={handleSaveJournal}
            disabled={saveJournalMutation.isPending}
            className="px-3.5 py-1.5 bg-[#00C896] hover:bg-[#00B084] text-[#0B0E14] font-bold rounded-lg text-xs transition-colors flex items-center gap-1.5 shadow-md"
          >
            <Save className="w-3.5 h-3.5" />
            <span>SAVE JOURNAL NOTE</span>
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <div className="space-y-3">
            <div>
              <label className="text-[#94A3B8] block mb-1">Trade Idea / Thesis:</label>
              <textarea
                value={journalForm.idea}
                onChange={(e) => setJournalForm({ ...journalForm, idea: e.target.value })}
                rows={2}
                className="w-full bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] rounded p-2 focus:outline-none"
              />
            </div>

            <div>
              <label className="text-[#94A3B8] block mb-1">Why Entered:</label>
              <textarea
                value={journalForm.whyEntered}
                onChange={(e) => setJournalForm({ ...journalForm, whyEntered: e.target.value })}
                rows={2}
                className="w-full bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] rounded p-2 focus:outline-none"
              />
            </div>

            <div>
              <label className="text-[#94A3B8] block mb-1">Mistakes / Lessons Learned:</label>
              <textarea
                value={journalForm.lessons}
                onChange={(e) => setJournalForm({ ...journalForm, lessons: e.target.value })}
                rows={2}
                className="w-full bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] rounded p-2 focus:outline-none"
              />
            </div>
          </div>

          <div className="space-y-3">
            <div>
              <label className="text-[#94A3B8] block mb-1">Trader Emotion:</label>
              <select
                value={journalForm.emotion}
                onChange={(e) => setJournalForm({ ...journalForm, emotion: e.target.value as any })}
                className="w-full bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] rounded p-2 focus:outline-none font-bold"
              >
                <option value="CALM">CALM / DISCIPLINED</option>
                <option value="CONFIDENT">CONFIDENT</option>
                <option value="FOMO">FOMO / IMPULSIVE</option>
                <option value="ANXIOUS">ANXIOUS</option>
              </select>
            </div>

            <div>
              <label className="text-[#94A3B8] block mb-1">Confidence Score Before Trade (1 to 10): {journalForm.confidenceBefore}</label>
              <input
                type="range"
                min="1"
                max="10"
                value={journalForm.confidenceBefore}
                onChange={(e) => setJournalForm({ ...journalForm, confidenceBefore: parseInt(e.target.value) })}
                className="w-full accent-[#3B82F6]"
              />
            </div>

            <div>
              <label className="text-[#94A3B8] block mb-1">Tags (comma-separated):</label>
              <input
                type="text"
                value={journalForm.tags}
                onChange={(e) => setJournalForm({ ...journalForm, tags: e.target.value })}
                className="w-full bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] rounded p-2 focus:outline-none"
              />
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
};
