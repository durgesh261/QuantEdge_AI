import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { strategyOptimizationApi } from '../../services/api';
import { useToastStore } from '../../store/useToastStore';
import { useTerminalStore } from '../../store/useTerminalStore';
import { 
  FlaskConical, 
  Play, 
  Download, 
  Award, 
  BarChart3
} from 'lucide-react';
import { ParameterSweepInput } from '@algoapp/shared';

export const StrategyLaboratoryPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { addToast } = useToastStore();
  const { activeSymbol, activeTimeframe, setActiveSymbol, setActiveTimeframe } = useTerminalStore();

  const [selectedSymbol, setSelectedSymbol] = useState<string>(activeSymbol || 'BTCUSD.P');
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>(activeTimeframe || '1H');

  React.useEffect(() => {
    if (activeSymbol) setSelectedSymbol(activeSymbol);
    if (activeTimeframe) setSelectedTimeframe(activeTimeframe);
  }, [activeSymbol, activeTimeframe]);

  const { data: historyData } = useQuery({
    queryKey: ['optimizationHistory'],
    queryFn: strategyOptimizationApi.getHistory,
  });

  const runSweepMutation = useMutation({
    mutationFn: (input: ParameterSweepInput) => strategyOptimizationApi.runSweep(input),
    onSuccess: (res) => {
      addToast('Optimization Complete', `Generated ${res.data.length} strategy profile permutations.`, 'success');
      queryClient.invalidateQueries({ queryKey: ['optimizationHistory'] });
    },
  });

  const handleRunSweep = () => {
    runSweepMutation.mutate({
      symbol: selectedSymbol,
      timeframe: selectedTimeframe as any,
      strategyProfileId: 'DEF-1H-PROF',
      patLenRange: [5, 9, 14],
      liquidityLenRange: [15, 30],
      mergeThresholdRange: [0.005, 0.01],
      minConfidenceRange: [75, 85, 90],
    });
  };

  const handleExportCsv = () => {
    const downloadUrl = '/api/v1/strategy-optimization/export-csv';
    window.open(downloadUrl, '_blank');
    addToast('Export Started', 'Optimization Results CSV export initiated', 'info');
  };

  const runs = historyData?.data || [];
  const bestRun = runs.length > 0 ? runs[0] : null;

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
            <FlaskConical className="w-5 h-5 text-[#8B5CF6]" />
            Strategy Optimization Laboratory
          </h1>
          <p className="text-xs text-[#94A3B8] mt-0.5">
            Quantitative parameter sweeps, Walk-Forward Testing, Monte Carlo risk simulation, and profile benchmarking.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleRunSweep}
            disabled={runSweepMutation.isPending}
            className="px-3.5 py-1.5 bg-[#8B5CF6] hover:bg-[#7C3AED] text-white font-bold rounded-lg text-xs transition-colors flex items-center gap-1.5 shadow-md"
          >
            <Play className={`w-3.5 h-3.5 ${runSweepMutation.isPending ? 'animate-spin' : ''}`} />
            <span>RUN PARAMETER SWEEP</span>
          </button>

          <button
            onClick={handleExportCsv}
            className="px-3.5 py-1.5 bg-[#1E2638] hover:bg-[#2D3748] text-[#F8FAFC] font-bold rounded-lg text-xs border border-[#1E293B] transition-colors flex items-center gap-1.5"
          >
            <Download className="w-3.5 h-3.5 text-[#3B82F6]" />
            <span>EXPORT CSV</span>
          </button>
        </div>
      </div>

      {/* Sweep Configuration Bar */}
      <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl flex items-center justify-between text-xs">
        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-2">
            <span className="text-[#94A3B8]">Target Symbol:</span>
            <select
              value={selectedSymbol}
              onChange={(e) => {
                setSelectedSymbol(e.target.value);
                setActiveSymbol(e.target.value);
              }}
              className="bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] font-bold rounded px-2.5 py-1"
            >
              <option value="BTCUSD.P">BTCUSD.P</option>
              <option value="ETHUSD.P">ETHUSD.P</option>
              <option value="SOLUSD.P">SOLUSD.P</option>
              <option value="XRPUSD.P">XRPUSD.P</option>
            </select>
          </div>

          <div className="flex items-center space-x-2">
            <span className="text-[#94A3B8]">Timeframe:</span>
            <select
              value={selectedTimeframe}
              onChange={(e) => {
                setSelectedTimeframe(e.target.value);
                setActiveTimeframe(e.target.value as any);
              }}
              className="bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] font-bold rounded px-2.5 py-1"
            >
              <option value="15M">15M</option>
              <option value="1H">1H</option>
            </select>
          </div>
        </div>

        <span className="text-[11px] text-[#94A3B8]">
          Sweeping PAT Length (5-14), Liquidity Length (15-30), Merge Threshold (0.5-1.0%), and Confidence (75-90%)
        </span>
      </div>

      {/* Best Performing Configuration Spotlight */}
      {bestRun && (
        <div className="bg-[#161D2A] border border-[#8B5CF6]/40 rounded-xl p-4 space-y-3 shadow-md bg-gradient-to-r from-[#8B5CF6]/10 to-transparent">
          <div className="flex items-center justify-between border-b border-[#8B5CF6]/30 pb-2">
            <div className="flex items-center space-x-2">
              <Award className="w-5 h-5 text-[#F59E0B]" />
              <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
                Optimal Strategy Configuration Spotlight
              </h2>
            </div>
            <span className="px-2.5 py-0.5 rounded text-[10px] font-bold bg-[#00C896]/20 text-[#00C896] border border-[#00C896]">
              RANK #1 OPTIMAL
            </span>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-6 gap-3 text-xs">
            <div>
              <span className="text-[10px] text-[#94A3B8] uppercase block">Net PnL</span>
              <span className="text-base font-bold text-[#00C896] font-mono-tabular">${bestRun.metrics.netPnL}</span>
            </div>

            <div>
              <span className="text-[10px] text-[#94A3B8] uppercase block">Win Rate</span>
              <span className="text-base font-bold text-[#3B82F6] font-mono-tabular">{bestRun.metrics.winRatePercent}%</span>
            </div>

            <div>
              <span className="text-[10px] text-[#94A3B8] uppercase block">Sharpe Ratio</span>
              <span className="text-base font-bold text-[#F8FAFC] font-mono-tabular">{bestRun.metrics.sharpeRatio}</span>
            </div>

            <div>
              <span className="text-[10px] text-[#94A3B8] uppercase block">Walk-Forward Score</span>
              <span className="text-base font-bold text-[#00C896] font-mono-tabular">{bestRun.walkForward.robustnessScore}/100</span>
            </div>

            <div>
              <span className="text-[10px] text-[#94A3B8] uppercase block">Probability of Ruin</span>
              <span className="text-base font-bold text-[#00C896] font-mono-tabular">{bestRun.monteCarlo.probabilityOfRuinPercent}%</span>
            </div>

            <div>
              <span className="text-[10px] text-[#94A3B8] uppercase block">Max Drawdown</span>
              <span className="text-base font-bold text-[#F59E0B] font-mono-tabular">{bestRun.metrics.maxDrawdownPercent}%</span>
            </div>
          </div>
        </div>
      )}

      {/* Optimization Runs Leaderboard Table */}
      <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
        <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
          <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-[#8B5CF6]" />
            Strategy Profile Optimization Leaderboard ({runs.length})
          </h2>
          <span className="text-[10px] text-[#94A3B8]">SORTED BY NET REALIZED PNL</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-[#0B0E14] text-[#94A3B8] border-b border-[#1E293B]">
              <tr>
                <th className="py-2.5 px-3">Rank</th>
                <th className="py-2.5 px-3">Profile / Parameters</th>
                <th className="py-2.5 px-3 text-right">Win Rate</th>
                <th className="py-2.5 px-3 text-right">Net PnL</th>
                <th className="py-2.5 px-3 text-right">Profit Factor</th>
                <th className="py-2.5 px-3 text-right">Sharpe</th>
                <th className="py-2.5 px-3 text-right">Max DD</th>
                <th className="py-2.5 px-3 text-right">Walk-Forward</th>
                <th className="py-2.5 px-3 text-right">Monte Carlo Ruin</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E293B]">
              {runs.map((r, idx) => (
                <tr key={r.id} className="hover:bg-[#1E2638]/50 transition-colors">
                  <td className="py-2.5 px-3 font-bold text-[#F8FAFC]">#{idx + 1}</td>
                  <td className="py-2.5 px-3">
                    <span className="font-bold text-[#8B5CF6] block">{r.strategyProfileName}</span>
                    <span className="text-[10px] text-[#94A3B8]">
                      ZigZag:{r.parameters.zigzagLen} | Liq:{r.parameters.liquidityLen} | Conf:{r.parameters.minConfidence}%
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono-tabular text-[#3B82F6]">{r.metrics.winRatePercent}%</td>
                  <td className="py-2.5 px-3 text-right font-mono-tabular font-bold text-[#00C896]">${r.metrics.netPnL}</td>
                  <td className="py-2.5 px-3 text-right font-mono-tabular text-[#F8FAFC]">{r.metrics.profitFactor}</td>
                  <td className="py-2.5 px-3 text-right font-mono-tabular text-[#F8FAFC]">{r.metrics.sharpeRatio}</td>
                  <td className="py-2.5 px-3 text-right font-mono-tabular text-[#F59E0B]">{r.metrics.maxDrawdownPercent}%</td>
                  <td className="py-2.5 px-3 text-right font-mono-tabular text-[#00C896]">{r.walkForward.robustnessScore}/100</td>
                  <td className="py-2.5 px-3 text-right font-mono-tabular text-[#00C896]">{r.monteCarlo.probabilityOfRuinPercent}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </motion.div>
  );
};
