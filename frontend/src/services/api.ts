import axios from 'axios';
import {
  ApiResponse,
  IndicatorEngineOutput,
  SystemHealthStatus,
  SystemSettingsDto,
  PaperWalletDto,
  PaperOrderDto,
  PaperPositionDto,
  PaperRiskConfigDto,
  PaperTradeJournalDto,
  PaperAnalyticsDto,
  CreatePaperOrderInput,
  ZoneDto,
  StrategySignalDto,
  EvaluateStrategySignalInput,
  DecisionDto,
  EvaluateDecisionInput,
  DecisionExplanationDto,
  ExplainDecisionInput,
  TradingRuleConfigDto,
  RuleMetadataDto,
  CalculateLeverageInput,
  LeverageOutputDto,
  UpdateTradingRuleConfigInput,
  CandleDto,
  MarketSnapshotDto,
  MarketEventDto,
  IngestCandleInput,
  ReplaySessionDto,
  ReplayControlAction,
  ReplayEventDto,
  BacktestSessionDto,
  RunBacktestInput,
  ExecutionSessionDto,
  ExecutionRequestDto,
  ExecutionResultDto,
  ExecutionJournalDto,
  SubmitExecutionInput,
  TradingViewHealthDto,
  TradingViewWebhookResult,
  WebhookEventDto,
  WebhookErrorDto,
  TradingViewWebhookPayload,
  PipelineTraceDto,
  SystemMonitorOverviewDto,
  RunPipelineInput,
  DeltaHealthDto,
  DeltaEnvironment,
  DeltaSyncStatusDto,
  DeltaStateReconciliationDto,
  DeltaRecoveryTestDto,
  ProductionOverviewDto,
  ExecutionMode,
  BackupStatusDto,
  ValidationReportDto,
  RunValidationInput,
  StrategyProfileDto,
  CreateStrategyProfileInput,
  WalletStateDto,
  ChallengeStateDto,
  ResetChallengeInput,
  TradeLedgerEntryDto,
  TradeLedgerFilterDto,
  TradeAccountingSummaryDto,
  NotificationDto,
  SubsystemHealthDto,
  ReconciliationReportDto,
  TradeAuditTimelineDto,
  ParameterSweepInput,
  OptimizationRunResult,
  NocServiceHealthDto,
  SystemMetricsDto,
  ErrorLogEntryDto,
  DatabaseDiagnosticsDto,
  BackupInfoDto,
  DiagnosticsReportDto,
  TradeReviewDetailDto,
  TradeJournalNoteDto,
  PerformanceReviewSummaryDto,
  ShadowDecisionRecordDto,
  ChallengeSimulationDto,
  StabilityMatrixItemDto,
  ProductionReadinessScoreDto,
  OrderBlockDto,
} from '@algoapp/shared';

const API_BASE_URL = (import.meta as any).env?.VITE_API_URL || 'http://localhost:4000/api/v1';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

let reqCounter = 0;
apiClient.interceptors.request.use((config) => {
  reqCounter = (reqCounter + 1) % 100000;
  config.headers['X-Request-Id'] = `req-${Date.now()}-${reqCounter}`;
  return config;
});

// Debug interceptor
apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.code === 'ERR_NETWORK') {
      console.error('[API] Backend unreachable. Is it running on', API_BASE_URL, '?');
    }
    return Promise.reject(err);
  }
);

export const systemApi = {
  getLiveness: async (): Promise<ApiResponse<{ status: string }>> => {
    const res = await apiClient.get('/system/liveness');
    return res.data;
  },
  getReadiness: async (): Promise<ApiResponse<SystemHealthStatus>> => {
    const res = await apiClient.get('/system/readiness');
    return res.data;
  },
  getSettings: async (): Promise<ApiResponse<SystemSettingsDto>> => {
    const res = await apiClient.get('/settings');
    return res.data;
  },
  hardReset: async (): Promise<ApiResponse<{message: string}>> => {
    const res = await apiClient.post('/system/hard-reset');
    return res.data;
  },
};

export const settingsApi = {
  getSettings: async (): Promise<ApiResponse<SystemSettingsDto>> => {
    const res = await apiClient.get('/settings');
    return res.data;
  },
  saveDeltaCredentials: async (payload: {
    apiKey: string;
    apiSecret: string;
    environment: 'PRODUCTION' | 'SANDBOX';
  }): Promise<ApiResponse<{ success: boolean; message: string; settings: SystemSettingsDto }>> => {
    const res = await apiClient.post('/settings/delta-credentials', payload);
    return res.data;
  },
  testDeltaCredentials: async (payload: {
    apiKey: string;
    apiSecret: string;
    environment: 'PRODUCTION' | 'SANDBOX';
  }): Promise<ApiResponse<{ success: boolean; latencyMs: number; message: string; data?: any }>> => {
    const res = await apiClient.post('/settings/delta-credentials/test', payload);
    return res.data;
  },
  deleteDeltaCredentials: async (): Promise<ApiResponse<{ success: boolean; message: string }>> => {
    const res = await apiClient.delete('/settings/delta-credentials');
    return res.data;
  },
};

export const paperTradingApi = {
  getWallet: async (): Promise<ApiResponse<PaperWalletDto>> => {
    const res = await apiClient.get('/paper-trading/wallet');
    return res.data;
  },
  getOrders: async (): Promise<ApiResponse<PaperOrderDto[]>> => {
    const res = await apiClient.get('/paper-trading/orders');
    return res.data;
  },
  createOrder: async (order: CreatePaperOrderInput): Promise<ApiResponse<PaperOrderDto>> => {
    const res = await apiClient.post('/paper-trading/orders', order);
    return res.data;
  },
  cancelOrder: async (id: string): Promise<ApiResponse<PaperOrderDto>> => {
    const res = await apiClient.delete(`/paper-trading/orders/${id}`);
    return res.data;
  },
  getPositions: async (): Promise<ApiResponse<PaperPositionDto[]>> => {
    const res = await apiClient.get('/paper-trading/positions');
    return res.data;
  },
  getClosedPositions: async (): Promise<ApiResponse<PaperPositionDto[]>> => {
    const res = await apiClient.get('/paper-trading/positions/closed');
    return res.data;
  },
  closePosition: async (id: string, exitPrice: number): Promise<ApiResponse<PaperPositionDto>> => {
    const res = await apiClient.post(`/paper-trading/positions/${id}/close`, { exitPrice });
    return res.data;
  },
  getRiskConfig: async (): Promise<ApiResponse<PaperRiskConfigDto>> => {
    const res = await apiClient.get('/paper-trading/risk');
    return res.data;
  },
  getJournal: async (): Promise<ApiResponse<PaperTradeJournalDto[]>> => {
    const res = await apiClient.get('/paper-trading/journal');
    return res.data;
  },
  getAnalytics: async (): Promise<ApiResponse<PaperAnalyticsDto>> => {
    const res = await apiClient.get('/paper-trading/analytics');
    return res.data;
  },
};

export const strategyApi = {
  getZones: async (symbol?: string): Promise<ApiResponse<ZoneDto[]>> => {
    const res = await apiClient.get('/strategy/zones', { params: { symbol } });
    return res.data;
  },
  getSignals: async (): Promise<ApiResponse<StrategySignalDto[]>> => {
    const res = await apiClient.get('/strategy/signals');
    return res.data;
  },
  evaluateSignal: async (input: EvaluateStrategySignalInput): Promise<ApiResponse<StrategySignalDto>> => {
    const res = await apiClient.post('/strategy/evaluate', input);
    return res.data;
  },
};

export const indicatorApi = {
  evaluate: async (symbol?: string, timeframe?: string): Promise<ApiResponse<IndicatorEngineOutput>> => {
    const res = await apiClient.get('/indicators/evaluate', { params: { symbol, timeframe } });
    return res.data;
  },
  getOrderBlocks: async (symbol: string): Promise<ApiResponse<OrderBlockDto[]>> => {
    const res = await apiClient.get('/indicators/order-blocks', { params: { symbol } });
    return res.data;
  },
};

export const decisionApi = {
  getLogs: async (): Promise<ApiResponse<DecisionDto[]>> => {
    const res = await apiClient.get('/decision/logs');
    return res.data;
  },
  evaluateDecision: async (input: EvaluateDecisionInput): Promise<ApiResponse<DecisionDto>> => {
    const res = await apiClient.post('/decision/evaluate', input);
    return res.data;
  },
};

export const aiDecisionApi = {
  explainDecision: async (input: ExplainDecisionInput): Promise<ApiResponse<DecisionExplanationDto>> => {
    const res = await apiClient.post('/ai-decision/explain', input);
    return res.data;
  },
};

export const tradingRulesApi = {
  getConfig: async (): Promise<ApiResponse<TradingRuleConfigDto>> => {
    const res = await apiClient.get('/rules/config');
    return res.data;
  },
  updateConfig: async (input: UpdateTradingRuleConfigInput): Promise<ApiResponse<TradingRuleConfigDto>> => {
    const res = await apiClient.patch('/rules/config', input);
    return res.data;
  },
  calculateLeverage: async (input: CalculateLeverageInput): Promise<ApiResponse<LeverageOutputDto>> => {
    const res = await apiClient.post('/rules/calculate-leverage', input);
    return res.data;
  },
  getRegistry: async (): Promise<ApiResponse<RuleMetadataDto[]>> => {
    const res = await apiClient.get('/rules/registry');
    return res.data;
  },
};

export const marketDataApi = {
  getSnapshot: async (symbol?: string): Promise<ApiResponse<MarketSnapshotDto>> => {
    const res = await apiClient.get('/market-data/snapshot', { params: { symbol } });
    return res.data;
  },
  getCandles: async (
    params: { symbol: string; timeframe?: string; limit?: number } | string,
    timeframe?: string,
    limit: number = 50
  ): Promise<ApiResponse<CandleDto[]>> => {
    if (typeof params === 'object') {
      const res = await apiClient.get('/market-data/candles', { params });
      return res.data;
    }
    const res = await apiClient.get('/market-data/candles', { params: { symbol: params, timeframe, limit } });
    return res.data;
  },
  ingestCandle: async (input: IngestCandleInput): Promise<ApiResponse<CandleDto>> => {
    const res = await apiClient.post('/market-data/candles', input);
    return res.data;
  },
  getEvents: async (): Promise<ApiResponse<MarketEventDto[]>> => {
    const res = await apiClient.get('/market-data/events');
    return res.data;
  },
};

export const replayApi = {
  getSession: async (symbol?: string): Promise<ApiResponse<ReplaySessionDto>> => {
    const res = await apiClient.get('/replay/session', { params: { symbol } });
    return res.data;
  },
  control: async (
    action: ReplayControlAction,
    payload?: { speedMultiplier?: number; targetIndex?: number }
  ): Promise<ApiResponse<ReplaySessionDto>> => {
    const res = await apiClient.post('/replay/control', { action, ...payload });
    return res.data;
  },
  getEvents: async (): Promise<ApiResponse<ReplayEventDto[]>> => {
    const res = await apiClient.get('/replay/events');
    return res.data;
  },
};

export const backtestApi = {
  getSessions: async (): Promise<ApiResponse<BacktestSessionDto[]>> => {
    const res = await apiClient.get('/replay/backtest/sessions');
    return res.data;
  },
  runBacktest: async (input: RunBacktestInput): Promise<ApiResponse<BacktestSessionDto>> => {
    const res = await apiClient.post('/replay/backtest/run', input);
    return res.data;
  },
};

export interface OrderExecutionDto {
  symbol: string;
  side: 'buy' | 'sell';
  orderType: 'market' | 'limit' | 'stop_market' | 'stop_limit';
  size: number;
  price?: number | undefined;
  stopPrice?: number | undefined;
  leverage?: number | undefined;
  reduceOnly?: boolean | undefined;
  postOnly?: boolean | undefined;
  stopLossPrice?: number | undefined;
  takeProfitPrice?: number | undefined;
  clientOrderId?: string | undefined;
}

export const executionApi = {
  placeOrder: async (order: OrderExecutionDto): Promise<ApiResponse<any>> => {
    const res = await apiClient.post('/execution/orders', order);
    return res.data;
  },
  validateOrder: async (order: OrderExecutionDto): Promise<ApiResponse<any>> => {
    const res = await apiClient.post('/execution/validate', order);
    return res.data;
  },
  cancelOrder: async (orderId: string | number): Promise<ApiResponse<any>> => {
    const res = await apiClient.post(`/execution/orders/${orderId}/cancel`);
    return res.data;
  },
  cancelAllOrders: async (): Promise<ApiResponse<any>> => {
    const res = await apiClient.post('/execution/orders/cancel-all');
    return res.data;
  },
  closePosition: async (symbol: string): Promise<ApiResponse<any>> => {
    const res = await apiClient.post(`/execution/positions/${symbol}/close`);
    return res.data;
  },
  modifyOrder: async (orderId: string | number, updates: { price?: number; size?: number }): Promise<ApiResponse<any>> => {
    const res = await apiClient.post(`/execution/orders/${orderId}/modify`, updates);
    return res.data;
  },
  getActiveOrders: async (): Promise<ApiResponse<any[]>> => {
    const res = await apiClient.get('/execution/active');
    return res.data;
  },
  getHistory: async (): Promise<ApiResponse<any[]>> => {
    const res = await apiClient.get('/execution/history');
    return res.data;
  },
  toggleKillSwitch: async (active: boolean): Promise<ApiResponse<any>> => {
    const res = await apiClient.post('/execution/kill-switch', { active });
    return res.data;
  },
  submitExecution: async (input: SubmitExecutionInput): Promise<ApiResponse<{
    session: ExecutionSessionDto;
    request: ExecutionRequestDto;
    result: ExecutionResultDto;
    journal: ExecutionJournalDto[];
  }>> => {
    const res = await apiClient.post('/execution/submit', input);
    return res.data;
  },
  getSessions: async (): Promise<ApiResponse<ExecutionSessionDto[]>> => {
    const res = await apiClient.get('/execution/sessions');
    return res.data;
  },
  getRequests: async (): Promise<ApiResponse<ExecutionRequestDto[]>> => {
    const res = await apiClient.get('/execution/requests');
    return res.data;
  },
  getResults: async (): Promise<ApiResponse<ExecutionResultDto[]>> => {
    const res = await apiClient.get('/execution/results');
    return res.data;
  },
  getJournal: async (): Promise<ApiResponse<ExecutionJournalDto[]>> => {
    const res = await apiClient.get('/execution/journal');
    return res.data;
  },
};

export const tradingViewApi = {
  sendWebhook: async (payload: TradingViewWebhookPayload): Promise<ApiResponse<TradingViewWebhookResult>> => {
    const res = await apiClient.post('/tradingview/webhook', payload);
    return res.data;
  },
  getHealth: async (): Promise<ApiResponse<TradingViewHealthDto>> => {
    const res = await apiClient.get('/tradingview/health');
    return res.data;
  },
  getEvents: async (): Promise<ApiResponse<WebhookEventDto[]>> => {
    const res = await apiClient.get('/tradingview/events');
    return res.data;
  },
  getErrors: async (): Promise<ApiResponse<WebhookErrorDto[]>> => {
    const res = await apiClient.get('/tradingview/errors');
    return res.data;
  },
};

export const systemIntegrationApi = {
  runPipeline: async (input: RunPipelineInput): Promise<ApiResponse<PipelineTraceDto>> => {
    const res = await apiClient.post('/system-integration/pipeline/run', input);
    return res.data;
  },
  getTraces: async (): Promise<ApiResponse<PipelineTraceDto[]>> => {
    const res = await apiClient.get('/system-integration/traces');
    return res.data;
  },
  getTraceById: async (id: string): Promise<ApiResponse<PipelineTraceDto>> => {
    const res = await apiClient.get(`/system-integration/traces/${id}`);
    return res.data;
  },
  getHealthOverview: async (): Promise<ApiResponse<SystemMonitorOverviewDto>> => {
    const res = await apiClient.get('/system-integration/health-overview');
    return res.data;
  },
};

export const deltaApi = {
  getHealth: async (): Promise<ApiResponse<DeltaHealthDto>> => {
    const res = await apiClient.get('/execution/delta/health');
    return res.data;
  },
  getPortfolio: async (): Promise<ApiResponse<any>> => {
    const res = await apiClient.get('/delta/portfolio');
    return res.data;
  },
  getOrders: async (): Promise<ApiResponse<any[]>> => {
    const res = await apiClient.get('/delta/orders');
    return res.data;
  },
  getPositions: async (): Promise<ApiResponse<any[]>> => {
    const res = await apiClient.get('/delta/positions');
    return res.data;
  },
  getHistory: async (): Promise<ApiResponse<any[]>> => {
    const res = await apiClient.get('/delta/history');
    return res.data;
  },
  placeOrder: async (payload: {
    symbol: string;
    side: 'buy' | 'sell';
    orderType: 'market' | 'limit' | 'stop_market' | 'stop_limit';
    size: number;
    price?: number | undefined;
    stopPrice?: number | undefined;
    stopLoss?: number | undefined;
    takeProfit?: number | undefined;
    clientOrderId?: string | undefined;
    reduceOnly?: boolean | undefined;
  }): Promise<ApiResponse<any>> => {
    const res = await apiClient.post('/delta/orders', payload);
    return res.data;
  },
  cancelOrder: async (orderId: number | string, productId?: number | undefined): Promise<ApiResponse<any>> => {
    const res = await apiClient.delete(`/delta/orders/${orderId}`, { data: { productId } });
    return res.data;
  },
  connect: async (environment: DeltaEnvironment): Promise<ApiResponse<DeltaHealthDto>> => {
    const res = await apiClient.post('/execution/delta/connect', { environment });
    return res.data;
  },
  disconnect: async (): Promise<ApiResponse<DeltaHealthDto>> => {
    const res = await apiClient.post('/execution/delta/disconnect');
    return res.data;
  },
  toggleKillSwitch: async (active: boolean): Promise<ApiResponse<{ isKillSwitchActive: boolean }>> => {
    const res = await apiClient.post('/execution/delta/kill-switch', { active });
    return res.data;
  },
  getSyncStatus: async (): Promise<ApiResponse<DeltaSyncStatusDto>> => {
    const res = await apiClient.get('/execution/delta/sync');
    return res.data;
  },
  reconcileState: async (): Promise<ApiResponse<DeltaStateReconciliationDto>> => {
    const res = await apiClient.post('/execution/delta/reconcile');
    return res.data;
  },
  simulateRecovery: async (scenario: string): Promise<ApiResponse<DeltaRecoveryTestDto>> => {
    const res = await apiClient.post('/execution/delta/simulate-recovery', { scenario });
    return res.data;
  },
};

export const productionApi = {
  getOverview: async (): Promise<ApiResponse<ProductionOverviewDto>> => {
    const res = await apiClient.get('/production/overview');
    return res.data;
  },
  setMode: async (mode: ExecutionMode, userConfirmed: boolean = false): Promise<ApiResponse<{ activeExecutionMode: ExecutionMode }>> => {
    const res = await apiClient.post('/production/mode', { mode, userConfirmed });
    return res.data;
  },
  triggerBackup: async (): Promise<ApiResponse<BackupStatusDto>> => {
    const res = await apiClient.post('/production/backup');
    return res.data;
  },
};

export const indicatorValidationApi = {
  runValidation: async (input: RunValidationInput = {}): Promise<ApiResponse<ValidationReportDto>> => {
    const res = await apiClient.post('/indicator-validation/run', input);
    return res.data;
  },
  getHistory: async (): Promise<ApiResponse<ValidationReportDto[]>> => {
    const res = await apiClient.get('/indicator-validation/history');
    return res.data;
  },
  getReportById: async (id: string): Promise<ApiResponse<ValidationReportDto>> => {
    const res = await apiClient.get(`/indicator-validation/report/${id}`);
    return res.data;
  },
};

export const strategyProfileApi = {
  getProfiles: async (): Promise<ApiResponse<StrategyProfileDto[]>> => {
    const res = await apiClient.get('/strategy-profile');
    return res.data;
  },
  getProfileById: async (id: string): Promise<ApiResponse<StrategyProfileDto>> => {
    const res = await apiClient.get(`/strategy-profile/${id}`);
    return res.data;
  },
  createProfile: async (input: CreateStrategyProfileInput): Promise<ApiResponse<StrategyProfileDto>> => {
    const res = await apiClient.post('/strategy-profile', input);
    return res.data;
  },
  updateProfile: async (id: string, updates: Partial<StrategyProfileDto>): Promise<ApiResponse<StrategyProfileDto>> => {
    const res = await apiClient.put(`/strategy-profile/${id}`, updates);
    return res.data;
  },
};

export const tradeAccountingApi = {
  getWallet: async (): Promise<ApiResponse<WalletStateDto>> => {
    const res = await apiClient.get('/trade-accounting/wallet');
    return res.data;
  },
  getChallenge: async (): Promise<ApiResponse<ChallengeStateDto>> => {
    const res = await apiClient.get('/trade-accounting/challenge');
    return res.data;
  },
  resetChallenge: async (input?: ResetChallengeInput): Promise<ApiResponse<ChallengeStateDto>> => {
    const res = await apiClient.post('/trade-accounting/challenge/reset', input || {});
    return res.data;
  },
  getLedger: async (params?: Partial<TradeLedgerFilterDto>): Promise<ApiResponse<TradeLedgerEntryDto[]>> => {
    const res = await apiClient.get('/trade-accounting/ledger', { params });
    return res.data;
  },
  getSummary: async (params?: Partial<TradeLedgerFilterDto>): Promise<ApiResponse<TradeAccountingSummaryDto>> => {
    const res = await apiClient.get('/trade-accounting/summary', { params });
    return res.data;
  },
  syncTrade: async (tradeData: any): Promise<ApiResponse<TradeLedgerEntryDto>> => {
    const res = await apiClient.post('/trade-accounting/sync-trade', tradeData);
    return res.data;
  },
  reconcile: async (): Promise<ApiResponse<ReconciliationReportDto>> => {
    const res = await apiClient.post('/trade-accounting/reconcile');
    return res.data;
  },
};

export const realtimeOperationsApi = {
  getNotifications: async (severity?: string): Promise<ApiResponse<NotificationDto[]>> => {
    const query = severity ? `?severity=${severity}` : '';
    const res = await apiClient.get(`/realtime-operations/notifications${query}`);
    return res.data;
  },
  markNotificationRead: async (id: string): Promise<ApiResponse<{ success: boolean }>> => {
    const res = await apiClient.post(`/realtime-operations/notifications/${id}/read`);
    return res.data;
  },
  markAllRead: async (): Promise<ApiResponse<{ success: boolean }>> => {
    const res = await apiClient.post('/realtime-operations/notifications/read-all');
    return res.data;
  },
  clearAll: async (): Promise<ApiResponse<{ success: boolean }>> => {
    const res = await apiClient.post('/realtime-operations/notifications/clear');
    return res.data;
  },
  getAuditTimeline: async (tradeId: string): Promise<ApiResponse<TradeAuditTimelineDto>> => {
    const res = await apiClient.get(`/realtime-operations/audit-timeline/${tradeId}`);
    return res.data;
  },
  runReconciliation: async (): Promise<ApiResponse<ReconciliationReportDto>> => {
    const res = await apiClient.post('/realtime-operations/reconcile');
    return res.data;
  },
  getSubsystemHealth: async (): Promise<ApiResponse<SubsystemHealthDto[]>> => {
    const res = await apiClient.get('/realtime-operations/subsystem-health');
    return res.data;
  },
};

export const strategyOptimizationApi = {
  runSweep: async (input: ParameterSweepInput): Promise<ApiResponse<OptimizationRunResult[]>> => {
    const res = await apiClient.post('/strategy-optimization/run', input);
    return res.data;
  },
  getHistory: async (): Promise<ApiResponse<OptimizationRunResult[]>> => {
    const res = await apiClient.get('/strategy-optimization/history');
    return res.data;
  },
};

export const operationsCenterApi = {
  getNocStatus: async (): Promise<ApiResponse<{ services: NocServiceHealthDto[]; metrics: SystemMetricsDto }>> => {
    const res = await apiClient.get('/operations-center/status');
    return res.data;
  },
  getErrors: async (category?: string, severity?: string): Promise<ApiResponse<ErrorLogEntryDto[]>> => {
    const params = new URLSearchParams();
    if (category) params.append('category', category);
    if (severity) params.append('severity', severity);
    const query = params.toString() ? `?${params.toString()}` : '';
    const res = await apiClient.get(`/operations-center/errors${query}`);
    return res.data;
  },
  getDatabaseDiagnostics: async (): Promise<ApiResponse<DatabaseDiagnosticsDto>> => {
    const res = await apiClient.get('/operations-center/database-diagnostics');
    return res.data;
  },
  createBackup: async (): Promise<ApiResponse<BackupInfoDto>> => {
    const res = await apiClient.post('/operations-center/backup');
    return res.data;
  },
  getBackupHistory: async (): Promise<ApiResponse<BackupInfoDto[]>> => {
    const res = await apiClient.get('/operations-center/backup-history');
    return res.data;
  },
  generateDiagnosticsReport: async (): Promise<ApiResponse<DiagnosticsReportDto>> => {
    const res = await apiClient.get('/operations-center/diagnostics-report');
    return res.data;
  },
};

export const tradeReviewApi = {
  getReview: async (tradeId: string): Promise<ApiResponse<TradeReviewDetailDto>> => {
    const res = await apiClient.get(`/trade-review/${tradeId}`);
    return res.data;
  },
  saveJournalNote: async (tradeId: string, note: Partial<TradeJournalNoteDto>): Promise<ApiResponse<TradeJournalNoteDto>> => {
    const res = await apiClient.post(`/trade-review/${tradeId}/journal`, note);
    return res.data;
  },
  getPerformanceSummary: async (): Promise<ApiResponse<PerformanceReviewSummaryDto>> => {
    const res = await apiClient.get('/trade-review/performance-summary');
    return res.data;
  },
};

export const shadowTradingApi = {
  getDashboard: async (): Promise<
    ApiResponse<{
      decisions: ShadowDecisionRecordDto[];
      stability: StabilityMatrixItemDto[];
      readiness: ProductionReadinessScoreDto;
      challengeSim: ChallengeSimulationDto;
    }>
  > => {
    const res = await apiClient.get('/shadow-trading/dashboard');
    return res.data;
  },
  triggerCycle: async (): Promise<ApiResponse<{ status: string; record: ShadowDecisionRecordDto }>> => {
    const res = await apiClient.post('/shadow-trading/cycle');
    return res.data;
  },
};

export const intelligenceApi = {
  getIntelligenceScore: async () => {
    const res = await apiClient.get('/analysis/intelligence-score');
    return res.data;
  },
  getStrategyMetrics: async (profileId?: string) => {
    const res = await apiClient.get('/analytics/strategy-metrics', { params: { profileId } });
    return res.data;
  },
  getMarketRegime: async (symbol?: string, timeframe?: string) => {
    const res = await apiClient.get('/analysis/market-regime', { params: { symbol, timeframe } });
    return res.data;
  },
  getPatterns: async () => {
    const res = await apiClient.get('/analysis/patterns');
    return res.data;
  },
  getTraderAnalytics: async () => {
    const res = await apiClient.get('/analytics/trader-analytics');
    return res.data;
  },
  getRecommendations: async () => {
    const res = await apiClient.get('/analysis/recommendations');
    return res.data;
  },
  getJournalIntelligence: async () => {
    const res = await apiClient.get('/analysis/journal-intelligence');
    return res.data;
  },
  getRiskIntelligence: async () => {
    const res = await apiClient.get('/analysis/risk-intelligence');
    return res.data;
  },
};

export const portfolioApi = {
  getSummary: async () => {
    const res = await apiClient.get('/portfolio/summary');
    return res.data;
  },
  getWallet: async () => {
    const res = await apiClient.get('/portfolio/wallet');
    return res.data;
  },
  getPositions: async () => {
    const res = await apiClient.get('/portfolio/positions');
    return res.data;
  },
  getOrders: async () => {
    const res = await apiClient.get('/portfolio/orders');
    return res.data;
  },
  getPnl: async () => {
    const res = await apiClient.get('/portfolio/pnl');
    return res.data;
  },
  getAnalytics: async () => {
    const res = await apiClient.get('/portfolio/analytics');
    return res.data;
  },
  getFunding: async () => {
    const res = await apiClient.get('/portfolio/funding');
    return res.data;
  },
};

export const scannerApi = {
  getStatus: async () => {
    const res = await apiClient.get('/live-trading/scanner/status');
    return res.data;
  },
  start: async () => {
    const res = await apiClient.post('/live-trading/scanner/start');
    return res.data;
  },
  pause: async () => {
    const res = await apiClient.post('/live-trading/scanner/pause');
    return res.data;
  },
  resume: async () => {
    const res = await apiClient.post('/live-trading/scanner/resume');
    return res.data;
  },
  stop: async () => {
    const res = await apiClient.post('/live-trading/scanner/stop');
    return res.data;
  },
  pausePair: async (symbol: string) => {
    const res = await apiClient.post('/live-trading/scanner/pair/pause', { symbol });
    return res.data;
  },
  resumePair: async (symbol: string) => {
    const res = await apiClient.post('/live-trading/scanner/pair/resume', { symbol });
    return res.data;
  },
  stopPair: async (symbol: string) => {
    const res = await apiClient.post('/live-trading/scanner/pair/stop', { symbol });
    return res.data;
  },
  setPairStatus: async (symbol: string, status: 'RUNNING' | 'PAUSED' | 'STOPPED') => {
    const res = await apiClient.post('/live-trading/scanner/pair/set-status', { symbol, status });
    return res.data;
  },
  calculateRisk: async (input: { accountBalance: number; entryPrice: number; stopLossPrice: number; direction: 'BUY' | 'SELL' }) => {
    const res = await apiClient.post('/live-trading/calculate-risk', input);
    return res.data;
  },
};

export const newsApi = {
  getNews: async (params?: { category?: string | undefined; importance?: string | undefined; symbol?: string | undefined; limit?: number | undefined; forceRefresh?: boolean }) => {
    const res = await apiClient.get('/news/live', { params });
    if (res.data?.success && Array.isArray(res.data.data)) {
      res.data.data = res.data.data.map((item: any) => ({
        ...item,
        headline: item.title,
        summary: item.description,
        symbols: item.tickers || [],
        importance: item.category === 'MACRO' || item.category === 'REGULATION' ? 'HIGH' : 'MEDIUM',
        sentiment: 'NEUTRAL'
      }));
    }
    return res.data;
  },
  getCalendar: async (forceRefresh = false) => {
    const res = await apiClient.get('/news/calendar', { params: { forceRefresh } });
    return res.data;
  },
};



















