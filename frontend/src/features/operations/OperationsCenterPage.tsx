import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { operationsCenterApi } from '../../services/api';
import { useToastStore } from '../../store/useToastStore';
import { 
  Server, 
  Cpu, 
  Database, 
  Download, 
  Save, 
  Clock, 
  AlertOctagon,
  FileCheck2
} from 'lucide-react';

export const OperationsCenterPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { addToast } = useToastStore();

  const [categoryFilter, setCategoryFilter] = useState<string>('ALL');
  const [severityFilter, setSeverityFilter] = useState<string>('ALL');

  const { data: nocStatusData } = useQuery({
    queryKey: ['nocStatus'],
    queryFn: operationsCenterApi.getNocStatus,
    refetchInterval: 2000,
  });

  const { data: errorsData } = useQuery({
    queryKey: ['nocErrors', categoryFilter, severityFilter],
    queryFn: () => operationsCenterApi.getErrors(categoryFilter, severityFilter),
    refetchInterval: 3000,
  });

  const { data: dbData } = useQuery({
    queryKey: ['databaseDiagnostics'],
    queryFn: operationsCenterApi.getDatabaseDiagnostics,
    refetchInterval: 5000,
  });

  const backupMutation = useMutation({
    mutationFn: operationsCenterApi.createBackup,
    onSuccess: (res) => {
      addToast('Backup Created', `Backup file ${res.data.filename} saved successfully`, 'success');
      queryClient.invalidateQueries({ queryKey: ['backupHistory'] });
    },
  });

  const reportMutation = useMutation({
    mutationFn: operationsCenterApi.generateDiagnosticsReport,
    onSuccess: (res) => {
      addToast(
        'Diagnostics Report Generated',
        `Overall Status: ${res.data.overallStatus} (${res.data.passedChecks}/${res.data.subsystemsChecked} passed)`,
        'success'
      );
    },
  });

  const handleExportErrorsCsv = () => {
    const downloadUrl = '/api/v1/operations-center/export-errors-csv';
    window.open(downloadUrl, '_blank');
    addToast('Export Started', 'System Error Log CSV export initiated', 'info');
  };

  const services = nocStatusData?.data?.services || [];
  const metrics = nocStatusData?.data?.metrics || {
    eventsPerSecond: 28.5,
    avgPipelineLatencyMs: 14.2,
    maxPipelineLatencyMs: 42.8,
    memoryUsageMb: 148.5,
    heapUsedMb: 68.2,
    cpuUsagePercent: 4.8,
    activeConnections: 12,
  };

  const errors = errorsData?.data || [];
  const dbDiag = dbData?.data || {
    tablesCount: 10,
    totalRecords: 84,
    tableDetails: [],
    slowQueriesCount: 0,
    storageSizeMb: 4.8,
  };

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
            Professional Operations Center (NOC)
          </h1>
          <p className="text-xs text-[#94A3B8] mt-0.5">
            Centralized operational telemetry, 15-service health monitoring, error center, database diagnostics, and backup management.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => reportMutation.mutate()}
            disabled={reportMutation.isPending}
            className="px-3.5 py-1.5 bg-[#00C896] hover:bg-[#00B084] text-[#0B0E14] font-bold rounded-lg text-xs transition-colors flex items-center gap-1.5 shadow-md"
          >
            <FileCheck2 className={`w-3.5 h-3.5 ${reportMutation.isPending ? 'animate-spin' : ''}`} />
            <span>ONE-CLICK HEALTH REPORT</span>
          </button>

          <button
            onClick={() => backupMutation.mutate()}
            disabled={backupMutation.isPending}
            className="px-3.5 py-1.5 bg-[#3B82F6] hover:bg-[#2563EB] text-white font-bold rounded-lg text-xs transition-colors flex items-center gap-1.5 shadow-md"
          >
            <Save className={`w-3.5 h-3.5 ${backupMutation.isPending ? 'animate-spin' : ''}`} />
            <span>CREATE BACKUP</span>
          </button>
        </div>
      </div>

      {/* System Metrics Performance Row */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3 text-xs">
        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Events / Sec</span>
          <div className="text-xl font-bold text-[#00C896] mt-0.5 font-mono-tabular">
            {metrics.eventsPerSecond} ev/s
          </div>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Avg Pipeline Latency</span>
          <div className="text-xl font-bold text-[#3B82F6] mt-0.5 font-mono-tabular">
            {metrics.avgPipelineLatencyMs}ms
          </div>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Max Latency</span>
          <div className="text-xl font-bold text-[#F59E0B] mt-0.5 font-mono-tabular">
            {metrics.maxPipelineLatencyMs}ms
          </div>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Heap Used</span>
          <div className="text-xl font-bold text-[#F8FAFC] mt-0.5 font-mono-tabular">
            {metrics.heapUsedMb} MB
          </div>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">CPU Usage</span>
          <div className="text-xl font-bold text-[#00C896] mt-0.5 font-mono-tabular">
            {metrics.cpuUsagePercent}%
          </div>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Active Conns</span>
          <div className="text-xl font-bold text-[#3B82F6] mt-0.5 font-mono-tabular">
            {metrics.activeConnections}
          </div>
        </div>
      </div>

      {/* 15-Service NOC Telemetry Status Grid */}
      <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
        <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
          <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider flex items-center gap-2">
            <Cpu className="w-4 h-4 text-[#3B82F6]" />
            15 Core Subsystems NOC Status ({services.length})
          </h2>
          <span className="text-[10px] text-[#94A3B8]">REAL-TIME TELEMETRY MONITORS</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-2.5 text-xs">
          {services.map((s) => (
            <div key={s.serviceName} className="bg-[#0B0E14] border border-[#1E293B] p-3 rounded-lg flex items-center justify-between">
              <div>
                <span className="font-bold text-[#F8FAFC] block">{s.serviceName}</span>
                <span className="text-[10px] text-[#94A3B8] flex items-center gap-1 mt-0.5">
                  <Clock className="w-3 h-3 text-[#3B82F6]" />
                  <span>{s.latencyMs}ms | {s.processedEvents} events</span>
                </span>
              </div>

              <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-[#00C896]/15 text-[#00C896] border border-[#00C896]/40">
                {s.health}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Error Center Log Panel (2 cols) */}
        <div className="lg:col-span-2 bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
          <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
            <div className="flex items-center space-x-2">
              <AlertOctagon className="w-4 h-4 text-[#F59E0B]" />
              <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
                Centralized Error Center Log ({errors.length})
              </h2>
            </div>

            <div className="flex items-center space-x-2">
              <select
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
                className="bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] rounded px-2 py-1 text-[11px] focus:outline-none"
              >
                <option value="ALL">ALL CATEGORIES</option>
                <option value="VALIDATION">VALIDATION</option>
                <option value="EXECUTION">EXECUTION</option>
                <option value="EXCHANGE">EXCHANGE</option>
                <option value="WEBHOOK">WEBHOOK</option>
                <option value="PIPELINE">PIPELINE</option>
              </select>

              <select
                value={severityFilter}
                onChange={(e) => setSeverityFilter(e.target.value)}
                className="bg-[#0B0E14] border border-[#334155] text-[#F8FAFC] rounded px-2 py-1 text-[11px] focus:outline-none"
              >
                <option value="ALL">ALL SEVERITIES</option>
                <option value="LOW">LOW</option>
                <option value="MEDIUM">MEDIUM</option>
                <option value="HIGH">HIGH</option>
                <option value="CRITICAL">CRITICAL</option>
              </select>

              <button
                onClick={handleExportErrorsCsv}
                className="px-2.5 py-1 bg-[#0B0E14] hover:bg-[#1E2638] text-[#F8FAFC] rounded text-[11px] border border-[#1E293B] transition-colors flex items-center gap-1"
              >
                <Download className="w-3 h-3 text-[#3B82F6]" />
                <span>CSV</span>
              </button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-[#0B0E14] text-[#94A3B8] border-b border-[#1E293B]">
                <tr>
                  <th className="py-2.5 px-3">ID</th>
                  <th className="py-2.5 px-3">Category</th>
                  <th className="py-2.5 px-3">Severity</th>
                  <th className="py-2.5 px-3">Message</th>
                  <th className="py-2.5 px-3 text-right">Timestamp</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1E293B]">
                {errors.map((e) => (
                  <tr key={e.id} className="hover:bg-[#1E2638]/50 transition-colors">
                    <td className="py-2.5 px-3 font-bold text-[#F8FAFC]">{e.id}</td>
                    <td className="py-2.5 px-3 text-[#3B82F6]">{e.category}</td>
                    <td className="py-2.5 px-3">
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-[#3B82F6]/15 text-[#3B82F6]">
                        {e.severity}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-[#94A3B8] max-w-xs truncate">{e.message}</td>
                    <td className="py-2.5 px-3 text-right font-mono-tabular text-[#64748B]">{e.timestamp.slice(11, 19)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Database Inspector Panel (1 col) */}
        <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
          <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
            <h2 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider flex items-center gap-2">
              <Database className="w-4 h-4 text-[#3B82F6]" />
              Database Diagnostics Inspector
            </h2>
            <span className="text-[10px] text-[#94A3B8]">{dbDiag.storageSizeMb} MB</span>
          </div>

          <div className="space-y-2">
            <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg flex items-center justify-between text-xs">
              <span className="text-[#94A3B8]">Total Database Tables</span>
              <span className="font-bold text-[#F8FAFC]">{dbDiag.tablesCount} Tables</span>
            </div>

            <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg flex items-center justify-between text-xs">
              <span className="text-[#94A3B8]">Total Records</span>
              <span className="font-bold text-[#00C896]">{dbDiag.totalRecords} Records</span>
            </div>

            <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg flex items-center justify-between text-xs">
              <span className="text-[#94A3B8]">Slow Queries</span>
              <span className="font-bold text-[#3B82F6]">{dbDiag.slowQueriesCount}</span>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
};
