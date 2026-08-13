import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { tradingRulesApi, settingsApi, deltaApi } from '../../services/api';
import { DeltaEnvironment } from '@algoapp/shared';
import { useToastStore } from '../../store/useToastStore';
import { 
  Sliders, 
  BookOpen, 
  ShieldCheck, 
  Calculator, 
  Zap,
  Key,
  Eye,
  EyeOff,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Trash2,
  ExternalLink,
  Shield,
  Radio,
  Server,
  Activity
} from 'lucide-react';

export const SettingsPage: React.FC = () => {
  const { addToast } = useToastStore();
  const queryClient = useQueryClient();

  // Dynamic Leverage Calculator State
  const [entryPrice, setEntryPrice] = useState('64000.00');
  const [stopLossPrice, setStopLossPrice] = useState('62720.00');
  const [riskPercent, setRiskPercent] = useState('2.0');

  // Delta API Credentials Form State
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    latencyMs?: number;
    message: string;
  } | null>(null);

  // Queries
  const { data: configData } = useQuery({
    queryKey: ['tradingRuleConfig'],
    queryFn: tradingRulesApi.getConfig,
  });

  const { data: registryData } = useQuery({
    queryKey: ['tradingRuleRegistry'],
    queryFn: tradingRulesApi.getRegistry,
  });

  const { data: settingsData, refetch: refetchSettings } = useQuery({
    queryKey: ['systemSettings'],
    queryFn: settingsApi.getSettings,
    refetchInterval: 5000,
  });

  // Populate local state when settings load
  useEffect(() => {
    if (settingsData?.data) {
      if (settingsData.data.deltaApiKey && !apiKey) {
        setApiKey(settingsData.data.deltaApiKey);
      }
    }
  }, [settingsData?.data]);

  // Mutations
  const testConnectionMutation = useMutation({
    mutationFn: settingsApi.testDeltaCredentials,
    onSuccess: (res) => {
      setTestResult(res.data);
      if (res.data.success) {
        addToast(
          'Delta API Verified',
          `Ping: ${res.data.latencyMs}ms. Credentials are fully authorized.`,
          'success'
        );
      } else {
        addToast('Verification Failed', res.data.message, 'danger');
      }
    },
    onError: (err: any) => {
      const msg = err?.response?.data?.error?.message || err?.message || 'Connection test failed';
      setTestResult({ success: false, message: msg });
      addToast('Connection Test Error', msg, 'danger');
    },
  });

  const saveCredentialsMutation = useMutation({
    mutationFn: settingsApi.saveDeltaCredentials,
    onSuccess: async () => {
      addToast('Credentials Saved', 'Delta API Key and Secret updated and synchronized.', 'success');
      setTestResult(null);
      
      // Auto-connect the execution adapter so the UI immediately shows DELTA LIVE
      try {
        await deltaApi.connect(DeltaEnvironment.PRODUCTION);
      } catch (err) {
        console.error('Failed to auto-connect adapter', err);
      }

      queryClient.invalidateQueries({ queryKey: ['systemSettings'] });
      queryClient.invalidateQueries({ queryKey: ['deltaHealth'] });
      queryClient.invalidateQueries({ queryKey: ['deltaSyncStatus'] });
      queryClient.invalidateQueries({ queryKey: ['portfolioSync'] });
    },
    onError: (err: any) => {
      const msg = err?.response?.data?.error?.message || err?.message || 'Failed to save credentials';
      addToast('Save Error', msg, 'danger');
    },
  });

  const deleteCredentialsMutation = useMutation({
    mutationFn: settingsApi.deleteDeltaCredentials,
    onSuccess: () => {
      addToast('Credentials Cleared', 'Delta API Key and Secret removed from system.', 'warning');
      setApiKey('');
      setApiSecret('');
      setTestResult(null);
      queryClient.invalidateQueries({ queryKey: ['systemSettings'] });
      queryClient.invalidateQueries({ queryKey: ['deltaHealth'] });
      queryClient.invalidateQueries({ queryKey: ['deltaSyncStatus'] });
    },
  });

  const calcLeverageMutation = useMutation({
    mutationFn: tradingRulesApi.calculateLeverage,
    onSuccess: (res) => {
      addToast(
        'Dynamic Leverage Computed',
        `Recommended: ${res.data.recommendedLeverage}x (SL Distance: ${res.data.stopLossDistancePercent}%)`,
        'success'
      );
    },
  });

  const handleTestConnection = (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey || !apiSecret) {
      addToast('Input Required', 'Please provide both API Key and API Secret to test connection.', 'warning');
      return;
    }
    setTestResult(null);
    testConnectionMutation.mutate({
      apiKey,
      apiSecret,
    });
  };

  const handleSaveCredentials = (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey || !apiSecret) {
      addToast('Input Required', 'Please enter your Delta API Key and API Secret.', 'warning');
      return;
    }
    saveCredentialsMutation.mutate({
      apiKey,
      apiSecret,
    });
  };

  const handlePasteApiKey = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        setApiKey(text.trim());
        addToast('Clipboard Pasted', 'API Key pasted from clipboard.', 'info');
      }
    } catch {
      addToast('Clipboard Permission', 'Please paste manually into the field.', 'warning');
    }
  };

  const handlePasteApiSecret = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        setApiSecret(text.trim());
        addToast('Clipboard Pasted', 'API Secret pasted from clipboard.', 'info');
      }
    } catch {
      addToast('Clipboard Permission', 'Please paste manually into the field.', 'warning');
    }
  };

  const handleCalcLeverage = (e: React.FormEvent) => {
    e.preventDefault();
    calcLeverageMutation.mutate({
      entryPrice: parseFloat(entryPrice),
      stopLossPrice: parseFloat(stopLossPrice),
      riskPercent: parseFloat(riskPercent),
    });
  };

  const config = configData?.data;
  const registry = registryData?.data || [];
  const leverageResult = calcLeverageMutation.data?.data;
  const settings = settingsData?.data;
  const deltaHealth = settings?.deltaHealth;

  const isConfigured = Boolean(settings?.deltaApiKey && settings?.hasDeltaApiSecret);
  const isConnected = deltaHealth?.status === 'CONNECTED';

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="space-y-6 max-w-7xl mx-auto pb-10 font-mono select-none"
    >
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-[#1E293B] pb-4 gap-3">
        <div>
          <h1 className="text-xl font-bold text-[#F8FAFC] flex items-center gap-2">
            <Sliders className="w-5 h-5 text-[#3B82F6]" />
            Application Settings & Exchange Credentials
          </h1>
          <p className="text-xs text-[#94A3B8] mt-0.5">
            Configure your Delta Exchange India API credentials, trading execution rules, leverage bounds, and system parameters.
          </p>
        </div>
        <div className="flex items-center gap-2 bg-[#3B82F6]/10 border border-[#3B82F6]/30 px-3 py-1.5 rounded-lg text-xs text-[#3B82F6] self-start sm:self-auto">
          <ShieldCheck className="w-4 h-4 text-[#00C896]" />
          <span>RULE ENGINE: {config?.ruleVersion ?? 'v2.0.0'}</span>
        </div>
      </div>

      {/* ========================================================================= */}
      {/* SECTION 1: DELTA EXCHANGE INDIA API CREDENTIALS CONFIGURATION */}
      {/* ========================================================================= */}
      <div className="bg-[#121824] border border-[#1E293B] rounded-xl overflow-hidden shadow-lg">
        {/* Card Header & Live Status */}
        <div className="p-4 bg-[#161D2A] border-b border-[#1E293B] flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-[#3B82F6]/15 rounded-lg text-[#3B82F6] border border-[#3B82F6]/30">
              <Key className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-[#F8FAFC] flex items-center gap-2">
                Delta Exchange India API Configuration
                <span className="text-[10px] font-normal px-2 py-0.5 rounded bg-[#00C896]/15 text-[#00C896] border border-[#00C896]/30">
                  DELTAIN
                </span>
              </h2>
              <p className="text-[11px] text-[#94A3B8]">
                Connect your Delta Exchange India account to enable live perpetual contract execution and real-time balance synchronization.
              </p>
            </div>
          </div>

          {/* Connection Status Badge */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-semibold bg-[#0B0E14] border-[#1E293B]">
              <span className="text-[10px] text-[#94A3B8] uppercase">Daemon:</span>
              {isConnected ? (
                <span className="flex items-center gap-1.5 text-[#00C896]">
                  <span className="w-2 h-2 rounded-full bg-[#00C896] animate-pulse" />
                  CONNECTED
                </span>
              ) : isConfigured ? (
                <span className="flex items-center gap-1.5 text-[#F59E0B]">
                  <span className="w-2 h-2 rounded-full bg-[#F59E0B]" />
                  {deltaHealth?.status || 'DISCONNECTED'}
                </span>
              ) : (
                <span className="flex items-center gap-1.5 text-[#EF4444]">
                  <span className="w-2 h-2 rounded-full bg-[#EF4444]" />
                  UNCONFIGURED
                </span>
              )}
            </div>

            <button
              onClick={() => refetchSettings()}
              className="p-1.5 hover:bg-[#1E293B] text-[#94A3B8] hover:text-[#F8FAFC] rounded-lg transition-colors"
              title="Refresh status"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Credentials Form & Telemetry */}
        <div className="p-5 grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left 2 Cols: Form */}
          <form onSubmit={handleSaveCredentials} className="lg:col-span-2 space-y-4">
            {/* Environment Toggle */}
            <div className="space-y-1.5">
              <label className="text-[11px] text-[#94A3B8] uppercase font-bold flex items-center gap-1.5">
                <Server className="w-3.5 h-3.5 text-[#3B82F6]" />
                Trading Environment Target
              </label>
              <div className="p-3 rounded-lg border bg-[#3B82F6]/10 border-[#3B82F6] text-[#F8FAFC] transition-all flex items-start gap-2.5">
                <Radio className="w-4 h-4 mt-0.5 text-[#3B82F6]" />
                <div>
                  <div className="text-xs font-bold flex items-center gap-1.5">
                    <span>🇮🇳 Delta Exchange India (Live)</span>
                    <span className="text-[9px] bg-[#3B82F6] text-white px-1.5 py-0.2 rounded font-bold">ACTIVE</span>
                  </div>
                  <div className="text-[10px] text-[#64748B] mt-0.5">api.india.delta.exchange (Real INR/USDT Capital)</div>
                </div>
              </div>
            </div>

            {/* API Key Input */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-[11px] text-[#94A3B8] uppercase font-bold flex items-center gap-1.5">
                  <Key className="w-3.5 h-3.5 text-[#00C896]" />
                  Delta API Key
                </label>
                <button
                  type="button"
                  onClick={handlePasteApiKey}
                  className="text-[10px] text-[#3B82F6] hover:underline"
                >
                  Paste from Clipboard
                </button>
              </div>
              <div className="relative">
                <input
                  type="text"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="e.g. 8f6b4d3a2c1e0f9b8a7c6d5e4f3a2b1c"
                  className="w-full bg-[#0B0E14] border border-[#334155] focus:border-[#3B82F6] rounded-lg px-3.5 py-2.5 text-xs text-[#F8FAFC] font-mono outline-none transition-colors"
                  required
                />
              </div>
            </div>

            {/* API Secret Input */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-[11px] text-[#94A3B8] uppercase font-bold flex items-center gap-1.5">
                  <Shield className="w-3.5 h-3.5 text-[#F59E0B]" />
                  Delta API Secret
                </label>
                <div className="flex items-center gap-3">
                  {settings?.hasDeltaApiSecret && !apiSecret && (
                    <span className="text-[10px] text-[#00C896] bg-[#00C896]/10 px-2 py-0.5 rounded border border-[#00C896]/20">
                      ✓ Stored in Database
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={handlePasteApiSecret}
                    className="text-[10px] text-[#3B82F6] hover:underline"
                  >
                    Paste from Clipboard
                  </button>
                </div>
              </div>
              <div className="relative">
                <input
                  type={showSecret ? 'text' : 'password'}
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                  placeholder={settings?.hasDeltaApiSecret ? '••••••••••••••••••••••••••••••••' : 'Enter your Delta API Secret HMAC key'}
                  className="w-full bg-[#0B0E14] border border-[#334155] focus:border-[#3B82F6] rounded-lg pl-3.5 pr-10 py-2.5 text-xs text-[#F8FAFC] font-mono outline-none transition-colors"
                  required={!settings?.hasDeltaApiSecret}
                />
                <button
                  type="button"
                  onClick={() => setShowSecret(!showSecret)}
                  className="absolute right-3 top-2.5 text-[#64748B] hover:text-[#F8FAFC] transition-colors"
                  title={showSecret ? 'Hide secret' : 'Show secret'}
                >
                  {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-[10px] text-[#64748B]">
                Your API Secret is strictly stored locally inside your SQLite database (`algoapp.db`) on this machine.
              </p>
            </div>

            {/* Live Verification Result Banner */}
            <AnimatePresence>
              {testResult && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className={`p-3 rounded-lg border text-xs flex items-start gap-2.5 ${
                    testResult.success
                      ? 'bg-[#00C896]/10 border-[#00C896]/30 text-[#00C896]'
                      : 'bg-[#EF4444]/10 border-[#EF4444]/30 text-[#EF4444]'
                  }`}
                >
                  {testResult.success ? (
                    <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
                  ) : (
                    <XCircle className="w-4 h-4 mt-0.5 shrink-0" />
                  )}
                  <div className="space-y-0.5">
                    <div className="font-bold">
                      {testResult.success
                        ? `Delta Exchange Connection Verified (${testResult.latencyMs}ms)`
                        : 'Connection Verification Failed'}
                    </div>
                    <div className="text-[11px] opacity-90">{testResult.message}</div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Action Buttons */}
            <div className="pt-2 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={handleTestConnection}
                disabled={testConnectionMutation.isPending || !apiKey || !apiSecret}
                className="px-4 py-2.5 bg-[#1E293B] hover:bg-[#334155] disabled:opacity-50 text-[#F8FAFC] rounded-lg font-bold text-xs transition-colors flex items-center space-x-2 border border-[#334155]"
              >
                <Activity className={`w-4 h-4 text-[#3B82F6] ${testConnectionMutation.isPending ? 'animate-spin' : ''}`} />
                <span>{testConnectionMutation.isPending ? 'TESTING CONNECTIVITY...' : 'TEST CONNECTION'}</span>
              </button>

              <button
                type="submit"
                disabled={saveCredentialsMutation.isPending || !apiKey || (!apiSecret && !settings?.hasDeltaApiSecret)}
                className="px-5 py-2.5 bg-[#00C896] hover:bg-[#00B080] disabled:opacity-50 text-black font-bold text-xs rounded-lg transition-colors flex items-center space-x-2 shadow-md shadow-[#00C896]/20"
              >
                <CheckCircle2 className="w-4 h-4" />
                <span>{saveCredentialsMutation.isPending ? 'SAVING & CONNECTING...' : 'SAVE & CONNECT DELTA'}</span>
              </button>

              {isConfigured && (
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm('Are you sure you want to remove your Delta Exchange API credentials?')) {
                      deleteCredentialsMutation.mutate();
                    }
                  }}
                  disabled={deleteCredentialsMutation.isPending}
                  className="px-4 py-2.5 bg-[#EF4444]/10 hover:bg-[#EF4444]/20 text-[#EF4444] rounded-lg font-bold text-xs transition-colors flex items-center space-x-2 border border-[#EF4444]/30 ml-auto"
                >
                  <Trash2 className="w-4 h-4" />
                  <span>REMOVE CREDENTIALS</span>
                </button>
              )}
            </div>
          </form>

          {/* Right Col: Instructions & Status Overview */}
          <div className="space-y-4 bg-[#0B0E14] border border-[#1E293B] p-4 rounded-xl text-xs">
            <div className="flex items-center space-x-2 text-[#3B82F6] font-bold pb-2 border-b border-[#1E293B]">
              <ShieldCheck className="w-4 h-4" />
              <span>How to Generate Delta API Key</span>
            </div>

            <ol className="space-y-2.5 text-[11px] text-[#94A3B8] list-decimal list-inside">
              <li>
                Log into{' '}
                <a
                  href="https://india.delta.exchange"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[#3B82F6] hover:underline inline-flex items-center gap-0.5"
                >
                  Delta Exchange India <ExternalLink className="w-3 h-3" />
                </a>
              </li>
              <li>
                Go to <strong>Settings</strong> → <strong>API Management</strong>
              </li>
              <li>
                Click <strong>Create New API Key</strong>
              </li>
              <li>
                Grant permissions:{' '}
                <span className="text-[#00C896] font-semibold">✓ Read</span> and{' '}
                <span className="text-[#00C896] font-semibold">✓ Trade</span>
              </li>
              <li className="text-[#F59E0B]">
                <strong>DO NOT</strong> enable <span className="underline">Withdrawal</span> permission (Never needed for QuantEdge AI).
              </li>
              <li>Copy and paste your API Key & Secret into the form on the left.</li>
            </ol>

            <div className="pt-2 border-t border-[#1E293B] space-y-2">
              <span className="text-[10px] text-[#64748B] uppercase font-bold block">Engine Telemetry</span>
              <div className="grid grid-cols-2 gap-2 text-[10px]">
                <div className="bg-[#161D2A] p-2 rounded border border-[#1E293B]">
                  <span className="text-[#64748B] block">REST Status</span>
                  <span className="font-bold text-[#F8FAFC]">{deltaHealth?.restStatus || 'UNCONFIGURED'}</span>
                </div>
                <div className="bg-[#161D2A] p-2 rounded border border-[#1E293B]">
                  <span className="text-[#64748B] block">WebSocket</span>
                  <span className="font-bold text-[#F8FAFC]">{deltaHealth?.wsStatus || 'DISCONNECTED'}</span>
                </div>
                <div className="bg-[#161D2A] p-2 rounded border border-[#1E293B]">
                  <span className="text-[#64748B] block">Reconcile Cycles</span>
                  <span className="font-bold text-[#3B82F6]">{deltaHealth?.reconcileCount || 0}</span>
                </div>
                <div className="bg-[#161D2A] p-2 rounded border border-[#1E293B]">
                  <span className="text-[#64748B] block">Database</span>
                  <span className="font-bold text-[#00C896]">SQLite (Local)</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ========================================================================= */}
      {/* SECTION 2: RULES OVERVIEW GRID */}
      {/* ========================================================================= */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div className="bg-[#161D2A] border border-[#1E293B] p-3.5 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Rule Engine Version</span>
          <div className="text-sm font-bold text-[#3B82F6] mt-0.5">{config?.ruleVersion ?? 'v2.0.0'}</div>
          <span className="text-[9px] text-[#64748B] block mt-1">Config: {config?.configVersion ?? 'cfg-2026.08.02'}</span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3.5 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Supported Timeframe</span>
          <div className="text-sm font-bold text-[#00C896] mt-0.5">{config?.supportedTimeframe ?? '15M & 1H'}</div>
          <span className="text-[9px] text-[#64748B] block mt-1">15M and 1H active timeframes</span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3.5 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Supported Perpetual Pairs</span>
          <div className="text-sm font-bold text-[#F8FAFC] mt-0.5">4 Pairs Active</div>
          <span className="text-[9px] text-[#64748B] block mt-1">BTC, ETH, SOL, XRP</span>
        </div>

        <div className="bg-[#161D2A] border border-[#1E293B] p-3.5 rounded-xl">
          <span className="text-[10px] text-[#94A3B8] uppercase block">Min Execution Confidence</span>
          <div className="text-sm font-bold text-[#F59E0B] mt-0.5">{config?.riskRules.minConfidence ?? 0}%</div>
          <span className="text-[9px] text-[#64748B] block mt-1">Required for EXECUTE state</span>
        </div>
      </div>

      {/* ========================================================================= */}
      {/* SECTION 3: DYNAMIC LEVERAGE CALCULATOR & RULES REGISTRY */}
      {/* ========================================================================= */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Dynamic Leverage Calculator Widget */}
        <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
          <div className="flex items-center space-x-2 border-b border-[#1E293B] pb-2">
            <Calculator className="w-4 h-4 text-[#3B82F6]" />
            <h3 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
              Deterministic Dynamic Leverage Calculator
            </h3>
          </div>

          <form onSubmit={handleCalcLeverage} className="space-y-3 text-xs">
            <div className="space-y-1">
              <label className="text-[#94A3B8] block text-[11px]">Entry Price ($)</label>
              <input
                type="number"
                step="any"
                value={entryPrice}
                onChange={(e) => setEntryPrice(e.target.value)}
                className="w-full bg-[#0B0E14] border border-[#334155] rounded px-3 py-1.5 text-[#F8FAFC] font-mono outline-none"
                required
              />
            </div>

            <div className="space-y-1">
              <label className="text-[#94A3B8] block text-[11px]">Stop Loss Price ($)</label>
              <input
                type="number"
                step="any"
                value={stopLossPrice}
                onChange={(e) => setStopLossPrice(e.target.value)}
                className="w-full bg-[#0B0E14] border border-[#334155] rounded px-3 py-1.5 text-[#F8FAFC] font-mono outline-none"
                required
              />
            </div>

            <div className="space-y-1">
              <label className="text-[#94A3B8] block text-[11px]">Risk Per Trade (%)</label>
              <input
                type="number"
                step="any"
                value={riskPercent}
                onChange={(e) => setRiskPercent(e.target.value)}
                className="w-full bg-[#0B0E14] border border-[#334155] rounded px-3 py-1.5 text-[#F8FAFC] font-mono outline-none"
                required
              />
            </div>

            <button
              type="submit"
              disabled={calcLeverageMutation.isPending}
              className="w-full py-2 bg-[#3B82F6] hover:bg-[#2563EB] text-white rounded-lg font-bold text-xs transition-colors flex items-center justify-center space-x-2 shadow-md"
            >
              <Zap className="w-4 h-4" />
              <span>{calcLeverageMutation.isPending ? 'CALCULATING...' : 'COMPUTE DYNAMIC LEVERAGE'}</span>
            </button>
          </form>

          {leverageResult && (
            <div className="bg-[#0B0E14] border border-[#00C896]/30 p-3 rounded-lg text-xs space-y-1 mt-2">
              <span className="text-[10px] text-[#94A3B8] uppercase block">Result</span>
              <div className="text-base font-bold text-[#00C896]">
                Recommended Leverage: {leverageResult.recommendedLeverage}x
              </div>
              <span className="text-[11px] text-[#94A3B8] block">
                SL Distance: {leverageResult.stopLossDistancePercent}% | Risk: {leverageResult.riskPercent}%
              </span>
            </div>
          )}
        </div>

        {/* Rule Registry Viewer */}
        <div className="lg:col-span-2 bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
          <div className="flex items-center justify-between border-b border-[#1E293B] pb-2">
            <div className="flex items-center space-x-2">
              <BookOpen className="w-4 h-4 text-[#00C896]" />
              <h3 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider">
                Trading Rules Explanation Registry ({registry.length})
              </h3>
            </div>
            <span className="text-[10px] text-[#94A3B8]">METADATA REGISTRY</span>
          </div>

          <div className="space-y-2.5 max-h-96 overflow-y-auto pr-1">
            {registry.map((r) => (
              <div key={r.ruleId} className="bg-[#0B0E14] border border-[#1E293B] p-3 rounded-lg space-y-1.5 text-xs">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-[#F8FAFC]">{r.name}</span>
                  <span className="text-[10px] bg-[#3B82F6]/15 text-[#3B82F6] px-2 py-0.5 rounded font-bold">
                    {String(r.currentValue)}
                  </span>
                </div>
                <p className="text-[11px] text-[#94A3B8]">{r.description}</p>
                <div className="text-[10px] text-[#64748B] border-t border-[#1E293B] pt-1 mt-1">
                  Purpose: {r.purpose}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </motion.div>
  );
};
