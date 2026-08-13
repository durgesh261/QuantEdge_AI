import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { deltaApi } from '../../services/api';
import { useToastStore } from '../../store/useToastStore';
import { DeltaEnvironment, DeltaConnectionState } from '@algoapp/shared';
import { 
  Zap, 
  Wifi, 
  WifiOff, 
  ShieldAlert, 
  CheckCircle2, 
  RefreshCcw,
  Activity,
  RotateCcw,
  CheckCheck
} from 'lucide-react';

export const DeltaConnectionPanel: React.FC = () => {
  const queryClient = useQueryClient();
  const { addToast } = useToastStore();

  const { data: healthData, isLoading } = useQuery({
    queryKey: ['deltaHealth'],
    queryFn: deltaApi.getHealth,
    refetchInterval: 3000,
  });

  const { data: syncData } = useQuery({
    queryKey: ['deltaSyncStatus'],
    queryFn: deltaApi.getSyncStatus,
    refetchInterval: 5000,
  });

  const connectMutation = useMutation({
    mutationFn: (env: DeltaEnvironment) => deltaApi.connect(env),
    onSuccess: (res) => {
      addToast(
        'Delta Exchange Connected',
        `Environment set to ${res.data.environment} (${res.data.connectionState})`,
        'success'
      );
      queryClient.invalidateQueries({ queryKey: ['deltaHealth'] });
      queryClient.invalidateQueries({ queryKey: ['deltaSyncStatus'] });
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: deltaApi.disconnect,
    onSuccess: () => {
      addToast('Delta Exchange Disconnected', 'Adapter returned to DISCONNECTED state.', 'warning');
      queryClient.invalidateQueries({ queryKey: ['deltaHealth'] });
    },
  });

  const killSwitchMutation = useMutation({
    mutationFn: (active: boolean) => deltaApi.toggleKillSwitch(active),
    onSuccess: (res) => {
      const active = res.data.isKillSwitchActive;
      addToast(
        active ? 'Emergency Kill Switch ACTIVATED' : 'Kill Switch DEACTIVATED',
        active ? 'All live exchange submissions are BLOCKED.' : 'Normal trading operations restored.',
        active ? 'danger' : 'success'
      );
      queryClient.invalidateQueries({ queryKey: ['deltaHealth'] });
    },
  });

  const reconcileMutation = useMutation({
    mutationFn: deltaApi.reconcileState,
    onSuccess: (res) => {
      const isMatched = res.data.matched;
      addToast(
        isMatched ? 'State Reconciliation PASSED' : 'State Mismatch Detected',
        isMatched
          ? `Local Engine & Delta Exchange matched (${res.data.localOrdersCount} orders, ${res.data.localPositionsCount} positions)`
          : `Found ${res.data.mismatches.length} state mismatches.`,
        isMatched ? 'success' : 'danger'
      );
    },
  });

  const simulateRecoveryMutation = useMutation({
    mutationFn: (scenario: string) => deltaApi.simulateRecovery(scenario),
    onSuccess: (res) => {
      addToast(
        `Recovery Scenario: ${res.data.scenario}`,
        `${res.data.details} (${res.data.recoveryTimeMs}ms)`,
        'info'
      );
      queryClient.invalidateQueries({ queryKey: ['deltaHealth'] });
    },
  });

  const health = healthData?.data;
  const sync = syncData?.data;

  const isConnected = health?.connectionState === DeltaConnectionState.CONNECTED;
  const isKillSwitchActive = health?.isKillSwitchActive ?? false;

  return (
    <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-4 shadow-sm font-mono text-xs select-none">
      {/* Header & Controls */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#1E293B] pb-3">
        <div className="flex items-center space-x-2">
          <Zap className="w-5 h-5 text-[#3B82F6]" />
          <div>
            <h3 className="font-bold text-[#F8FAFC] text-sm">Delta Exchange India Production Specification</h3>
            <p className="text-[10px] text-[#94A3B8]">
              Target: https://api.india.delta.exchange | Authenticated via HMAC-SHA256 headers.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => connectMutation.mutate(DeltaEnvironment.PRODUCTION)}
            disabled={connectMutation.isPending}
            className="px-2.5 py-1 bg-[#1E293B] hover:bg-[#334155] text-[#3B82F6] font-bold rounded border border-[#3B82F6]/30 text-[11px] transition-colors"
          >
            ENV: PRODUCTION
          </button>

          {!isConnected ? (
            <button
              onClick={() => connectMutation.mutate(DeltaEnvironment.PRODUCTION)}
              disabled={connectMutation.isPending}
              className="px-3 py-1 bg-[#00C896] hover:bg-[#00B084] text-[#0B0E14] font-bold rounded text-xs transition-colors flex items-center gap-1.5"
            >
              <Wifi className="w-3.5 h-3.5" />
              <span>{connectMutation.isPending ? 'CONNECTING...' : 'CONNECT'}</span>
            </button>
          ) : (
            <button
              onClick={() => disconnectMutation.mutate()}
              disabled={disconnectMutation.isPending}
              className="px-3 py-1 bg-[#EF4444]/20 border border-[#EF4444]/40 hover:bg-[#EF4444]/30 text-[#EF4444] font-bold rounded text-xs transition-colors flex items-center gap-1.5"
            >
              <WifiOff className="w-3.5 h-3.5" />
              <span>DISCONNECT</span>
            </button>
          )}
        </div>
      </div>

      {/* Emergency Kill Switch Warning Banner */}
      <div className="bg-[#0B0E14] border border-[#1E293B] p-3 rounded-xl flex items-center justify-between">
        <div className="flex items-center space-x-2.5">
          <ShieldAlert className={`w-5 h-5 ${isKillSwitchActive ? 'text-[#EF4444] animate-pulse' : 'text-[#64748B]'}`} />
          <div>
            <span className="font-bold text-[#F8FAFC]">Platform Emergency Kill Switch</span>
            <p className="text-[10px] text-[#94A3B8]">
              {isKillSwitchActive
                ? 'ACTIVE: Blocks all live order submissions to Delta Exchange while maintaining Paper & Replay.'
                : 'DEACTIVATED: Normal pipeline execution enabled.'}
            </p>
          </div>
        </div>

        <button
          onClick={() => killSwitchMutation.mutate(!isKillSwitchActive)}
          disabled={killSwitchMutation.isPending}
          className={`px-3 py-1.5 font-bold rounded text-xs transition-colors ${
            isKillSwitchActive
              ? 'bg-[#00C896] text-[#0B0E14] hover:bg-[#00B084]'
              : 'bg-[#EF4444] text-[#F8FAFC] hover:bg-[#DC2626]'
          }`}
        >
          {isKillSwitchActive ? 'DEACTIVATE KILL SWITCH' : 'ACTIVATE KILL SWITCH'}
        </button>
      </div>

      {/* Connection & Latency Grid */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg">
          <span className="text-[9px] text-[#94A3B8] uppercase block">Connection State</span>
          <div className="text-xs font-bold text-[#00C896] flex items-center gap-1 mt-0.5">
            <CheckCircle2 className="w-3 h-3 text-[#00C896]" />
            <span>{isLoading ? 'LOADING...' : health?.connectionState ?? 'DISCONNECTED'}</span>
          </div>
        </div>

        <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg">
          <span className="text-[9px] text-[#94A3B8] uppercase block">API Latency</span>
          <div className="text-xs font-bold text-[#3B82F6] mt-0.5">
            {health?.apiLatencyMs ?? 0}ms
          </div>
        </div>

        <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg">
          <span className="text-[9px] text-[#94A3B8] uppercase block">State Synchronization</span>
          <div className="text-xs font-bold text-[#00C896] flex items-center gap-1 mt-0.5">
            <CheckCheck className="w-3 h-3 text-[#00C896]" />
            <span>{sync?.isSynchronized ? 'SYNCHRONIZED' : 'PENDING'}</span>
          </div>
        </div>

        <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg">
          <span className="text-[9px] text-[#94A3B8] uppercase block">Heartbeat Age</span>
          <div className="text-xs font-bold text-[#F8FAFC] flex items-center gap-1 mt-0.5">
            <Activity className="w-3 h-3 text-[#00C896]" />
            <span>{health?.heartbeatAgeMs ?? 0}ms</span>
          </div>
        </div>

        <div className="bg-[#0B0E14] border border-[#1E293B] p-2.5 rounded-lg">
          <span className="text-[9px] text-[#94A3B8] uppercase block">Reconnect Count</span>
          <div className="text-xs font-bold text-[#94A3B8] flex items-center gap-1 mt-0.5">
            <RefreshCcw className="w-3 h-3 text-[#94A3B8]" />
            <span>{health?.reconnectCount ?? 0}</span>
          </div>
        </div>
      </div>

      {/* State Reconciliation & Failure Simulator Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 bg-[#0B0E14] border border-[#1E293B] p-3 rounded-xl">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => reconcileMutation.mutate()}
            disabled={reconcileMutation.isPending}
            className="px-3 py-1.5 bg-[#3B82F6] hover:bg-[#2563EB] text-white font-bold rounded text-xs transition-colors flex items-center gap-1.5"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>{reconcileMutation.isPending ? 'RECONCILING...' : 'RECONCILE STATE'}</span>
          </button>
          <span className="text-[10px] text-[#94A3B8]">
            Compares Execution Engine state vs Delta Exchange state.
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-[10px] text-[#94A3B8] font-bold">RECOVERY SIMULATOR:</span>
          <button
            onClick={() => simulateRecoveryMutation.mutate('WS_DISCONNECT')}
            className="px-2 py-1 bg-[#1E293B] hover:bg-[#334155] text-[#F8FAFC] rounded text-[10px] font-bold"
          >
            WS DROP
          </button>
          <button
            onClick={() => simulateRecoveryMutation.mutate('DUPLICATE_MESSAGE')}
            className="px-2 py-1 bg-[#1E293B] hover:bg-[#334155] text-[#F8FAFC] rounded text-[10px] font-bold"
          >
            DUPLICATE MSG
          </button>
          <button
            onClick={() => simulateRecoveryMutation.mutate('DELAYED_ACK')}
            className="px-2 py-1 bg-[#1E293B] hover:bg-[#334155] text-[#F8FAFC] rounded text-[10px] font-bold"
          >
            DELAYED ACK
          </button>
        </div>
      </div>
    </div>
  );
};
