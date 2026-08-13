import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { productionApi } from '../../services/api';
import { useToastStore } from '../../store/useToastStore';
import { ExecutionMode } from '@algoapp/shared';
import { 
  Server, 
  ShieldCheck, 
  ShieldAlert, 
  CheckCircle2, 
  XCircle, 
  Cpu, 
  Database, 
  HardDrive, 
  Lock, 
  Unlock,
  Radio
} from 'lucide-react';

export const ProductionDashboardPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { addToast } = useToastStore();

  const [confirmLiveModal, setConfirmLiveModal] = useState(false);

  const { data: overviewData } = useQuery({
    queryKey: ['productionOverview'],
    queryFn: productionApi.getOverview,
    refetchInterval: 3000,
  });

  const setModeMutation = useMutation({
    mutationFn: ({ mode, userConfirmed }: { mode: ExecutionMode; userConfirmed: boolean }) =>
      productionApi.setMode(mode, userConfirmed),
    onSuccess: (res) => {
      addToast(
        'Execution Mode Updated',
        `Active Mode set to ${res.data.activeExecutionMode}`,
        res.data.activeExecutionMode === ExecutionMode.LIVE ? 'danger' : 'success'
      );
      setConfirmLiveModal(false);
      queryClient.invalidateQueries({ queryKey: ['productionOverview'] });
    },
    onError: (err: any) => {
      addToast('Mode Activation Rejected', err.response?.data?.error || 'Safety Guard rejection.', 'danger');
    },
  });

  const backupMutation = useMutation({
    mutationFn: productionApi.triggerBackup,
    onSuccess: (res) => {
      addToast(
        'Production Backup Complete',
        `Backup size: ${res.data.totalBackupSizeMb}MB | Status: ${res.data.status}`,
        'success'
      );
      queryClient.invalidateQueries({ queryKey: ['productionOverview'] });
    },
  });

  const overview = overviewData?.data;
  const safety = overview?.safetyCheck;
  const metrics = overview?.metrics;
  const backup = overview?.backupStatus;

  const safetyGuards = [
    { label: 'Explicit User Confirmation', pass: safety?.checks.explicitUserConfirmed },
    { label: 'Environment Profile (Production)', pass: safety?.checks.validEnvironment },
    { label: 'Production API Credentials', pass: safety?.checks.productionApiKeysPresent },
    { label: 'Emergency Kill Switch (Inactive)', pass: safety?.checks.killSwitchInactive },
    { label: 'Challenge Guard Rules', pass: safety?.checks.challengeGuardEnabled },
    { label: 'Live Execution Mode Flag', pass: safety?.checks.liveModeActive },
    { label: 'Delta Exchange Connectivity', pass: safety?.checks.deltaConnectionHealthy },
    { label: 'TradingView Webhook Connectivity', pass: safety?.checks.tradingViewConnectionHealthy },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="space-y-5 max-w-7xl mx-auto pb-6 font-mono select-none"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#1E293B] pb-3">
        <div>
          <h1 className="text-xl font-bold text-[#F8FAFC] flex items-center gap-2">
            <Server className="w-5 h-5 text-[#3B82F6]" />
            Production Deployment & Live Trading Activation
          </h1>
          <p className="text-xs text-[#94A3B8] mt-0.5">
            Production infrastructure, 8-Point Live Safety Guard Matrix, metrics monitoring & backups.
          </p>
        </div>
        <div className="flex items-center gap-2">
<div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-bold border ${
            overview?.activeExecutionMode === ExecutionMode.LIVE
              ? 'bg-[#EF4444]/15 border-[#EF4444]/40 text-[#EF4444]'
              : 'bg-[#00C896]/15 border-[#00C896]/40 text-[#00C896]'
        }`}>
            <Radio className="w-3.5 h-3.5 animate-pulse" />
            <span>MODE: {overview?.activeExecutionMode ?? 'PAPER'}</span>
          </div>
        </div>
      </div>

      {/* Mode Switcher Bar */}
      <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
        <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
          <span className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
            Execution Mode Selector
          </span>
          <span className="text-[10px] text-[#94A3B8]">
            ENV PROFILE: PRODUCTION
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <button
            onClick={() => setModeMutation.mutate({ mode: ExecutionMode.PAPER, userConfirmed: false })}
            className={`p-3 rounded-xl border text-left font-mono text-xs transition-colors ${
              overview?.activeExecutionMode === ExecutionMode.PAPER
                ? 'bg-[#00C896]/10 border-[#00C896] text-[#00C896]'
                : 'bg-[#0B0E14] border-[#1E293B] text-[#94A3B8] hover:border-[#334155]'
            }`}
          >
            <div className="flex items-center justify-between font-bold text-sm mb-1">
              <span>PAPER SIMULATION</span>
              <ShieldCheck className="w-4 h-4" />
            </div>
            <p className="text-[10px] opacity-80">Virtual balance simulation ($10.00 equity). Zero financial risk.</p>
          </button>

          <button
            onClick={() => setConfirmLiveModal(true)}
            className={`p-3 rounded-xl border text-left font-mono text-xs transition-colors ${
              overview?.activeExecutionMode === ExecutionMode.LIVE
                ? 'bg-[#EF4444]/10 border-[#EF4444] text-[#EF4444]'
                : 'bg-[#0B0E14] border-[#1E293B] text-[#94A3B8] hover:border-[#334155]'
            }`}
          >
            <div className="flex items-center justify-between font-bold text-sm mb-1">
              <span>LIVE TRADING (PROTECTED)</span>
              {overview?.activeExecutionMode === ExecutionMode.LIVE ? <Unlock className="w-4 h-4" /> : <Lock className="w-4 h-4" />}
            </div>
            <p className="text-[10px] opacity-80">Live exchange execution. Protected by 8-point safety guards.</p>
          </button>
        </div>
      </div>

      {/* 8-Point Live Trading Safety Guard Matrix */}
      <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
        <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
          <div className="flex items-center space-x-2">
            <ShieldAlert className="w-4 h-4 text-[#3B82F6]" />
            <h3 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
              8-Point Live Trading Safety Guard Matrix
            </h3>
          </div>
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${
            overview?.isLiveTradingAllowed
              ? 'bg-[#00C896]/20 text-[#00C896]'
              : 'bg-[#EF4444]/20 text-[#EF4444]'
          }`}>
            {overview?.isLiveTradingAllowed ? 'LIVE ACTIVATED' : 'LIVE GUARDED'}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
          {safetyGuards.map((guard, idx) => (
            <div key={guard.label} className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <span className="text-[9px] text-[#3B82F6] font-bold">#0{idx + 1}</span>
                <span className="text-[#F8FAFC] text-[11px] font-medium">{guard.label}</span>
              </div>
              {guard.pass ? (
                <CheckCircle2 className="w-4 h-4 text-[#00C896]" />
              ) : (
                <XCircle className="w-4 h-4 text-[#EF4444]" />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Infrastructure Metrics & Backup Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
        {/* Production Metrics */}
        <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
          <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
            <div className="flex items-center space-x-2">
              <Cpu className="w-4 h-4 text-[#3B82F6]" />
              <h3 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
                Production System Metrics
              </h3>
            </div>
            <span className="text-[10px] text-[#94A3B8]">REALTIME TELEMETRY</span>
          </div>

          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="bg-[#0B0E14] p-2.5 rounded-lg border border-[#1E293B]">
              <span className="text-[9px] text-[#94A3B8] block">CPU Usage</span>
              <span className="font-bold text-[#00C896] text-sm">{metrics?.cpuUsagePercent ?? 0}%</span>
            </div>
            <div className="bg-[#0B0E14] p-2.5 rounded-lg border border-[#1E293B]">
              <span className="text-[9px] text-[#94A3B8] block">Memory Heap</span>
              <span className="font-bold text-[#3B82F6] text-sm">{metrics?.memoryUsageMb ?? 0}MB</span>
            </div>
            <div className="bg-[#0B0E14] p-2.5 rounded-lg border border-[#1E293B]">
              <span className="text-[9px] text-[#94A3B8] block">Pipeline Latency</span>
              <span className="font-bold text-[#3B82F6] text-sm">{metrics?.pipelineLatencyMs ?? 0}ms</span>
            </div>
            <div className="bg-[#0B0E14] p-2.5 rounded-lg border border-[#1E293B]">
              <span className="text-[9px] text-[#94A3B8] block">System Uptime</span>
              <span className="font-bold text-[#F8FAFC] text-sm">{metrics?.uptimeSeconds ?? 0}s</span>
            </div>
          </div>
        </div>

        {/* Automated Backup Procedures */}
        <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
          <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
            <div className="flex items-center space-x-2">
              <Database className="w-4 h-4 text-[#3B82F6]" />
              <h3 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
                Automated Backup Manager
              </h3>
            </div>
            <button
              onClick={() => backupMutation.mutate()}
              disabled={backupMutation.isPending}
              className="px-2.5 py-1 bg-[#3B82F6] hover:bg-[#2563EB] text-white font-bold rounded text-xs transition-colors flex items-center gap-1"
            >
              <HardDrive className="w-3.5 h-3.5" />
              <span>{backupMutation.isPending ? 'BACKING UP...' : 'RUN BACKUP'}</span>
            </button>
          </div>

          <div className="space-y-2 text-[11px]">
            <div className="bg-[#0B0E14] p-2.5 rounded-lg border border-[#1E293B] flex items-center justify-between">
              <span className="text-[#94A3B8]">Database Snapshot</span>
              <span className="text-[#00C896] font-bold">{backup?.status ?? 'SUCCESS'}</span>
            </div>
            <div className="bg-[#0B0E14] p-2.5 rounded-lg border border-[#1E293B] flex items-center justify-between">
              <span className="text-[#94A3B8]">Execution Journal Backup</span>
              <span className="text-[#F8FAFC] font-mono text-[10px]">{backup?.journalBackupAt}</span>
            </div>
            <div className="bg-[#0B0E14] p-2.5 rounded-lg border border-[#1E293B] flex items-center justify-between">
              <span className="text-[#94A3B8]">Total Backup Archive Size</span>
              <span className="text-[#3B82F6] font-bold">{backup?.totalBackupSizeMb ?? 0}MB</span>
            </div>
          </div>
        </div>
      </div>

      {/* Confirmation Modal */}
      {confirmLiveModal && (
        <div className="fixed inset-0 bg-[#0B0E14]/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-[#161D2A] border border-[#EF4444]/40 rounded-xl max-w-md w-full p-5 space-y-4 font-mono shadow-2xl text-xs">
            <div className="flex items-center space-x-2 text-[#EF4444]">
              <ShieldAlert className="w-5 h-5" />
              <h3 className="font-bold text-sm text-[#F8FAFC]">Confirm Live Trading Activation</h3>
            </div>
            <p className="text-[#94A3B8] leading-relaxed text-[11px]">
              Live Mode sends real orders to Delta Exchange. Ensure your production environment variables and safety procedures are active.
            </p>
            <div className="flex justify-end gap-2 pt-2 border-t border-[#1E293B]">
              <button
                onClick={() => setConfirmLiveModal(false)}
                className="px-3 py-1.5 bg-[#1E293B] text-[#94A3B8] hover:text-white rounded font-bold"
              >
                CANCEL
              </button>
              <button
                onClick={() => setModeMutation.mutate({ mode: ExecutionMode.LIVE, userConfirmed: true })}
                disabled={setModeMutation.isPending}
                className="px-4 py-1.5 bg-[#EF4444] text-white rounded font-bold hover:bg-[#DC2626]"
              >
                ACTIVATE LIVE MODE
              </button>
            </div>
          </div>
        </div>
      )}
    </motion.div>
  );
};
