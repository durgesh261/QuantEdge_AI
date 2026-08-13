import {
  ExecutionSessionDto,
  ExecutionRequestDto,
  ExecutionResultDto,
  ExecutionJournalDto,
  ExecutionMode,
  ExecutionStatus,
  SubmitExecutionInput,
} from '@algoapp/shared';

import { PaperAdapter } from '../adapters/paperAdapter.js';
import { DeltaAdapter } from '../adapters/deltaAdapter.js';
import { IExecutionAdapter } from '../adapters/executionAdapter.interface.js';
import { ExecutionValidator } from './executionValidator.js';
import { IdempotencyManager } from './idempotencyManager.js';
import { ExecutionStateMachine } from '../state-machine/executionStateMachine.js';
import { TradingRulesService } from '../../rules/services/tradingRules.service.js';
import { deltaSyncService } from '../../delta-exchange/index.js';

let sessionStore: ExecutionSessionDto[] = [];
let requestStore: ExecutionRequestDto[] = [];
let resultStore: ExecutionResultDto[] = [];
let journalStore: ExecutionJournalDto[] = [];
let processedIdempotencyKeys = new Set<string>();

const paperAdapter = new PaperAdapter();
// DeltaAdapter will be initialized lazily with restClient
let deltaAdapter: DeltaAdapter | null = null;

function getDeltaAdapter(): DeltaAdapter {
  if (!deltaAdapter) {
    deltaAdapter = new DeltaAdapter(deltaSyncService.getRestClient());
  }
  return deltaAdapter;
}

export class ExecutionEngineService {
  public static async submitExecution(input: SubmitExecutionInput): Promise<{
    session: ExecutionSessionDto;
    request: ExecutionRequestDto;
    result: ExecutionResultDto;
    journal: ExecutionJournalDto[];
  }> {
    const totalStart = Date.now();
    const idempotencyKey = IdempotencyManager.generateKey(input);

    // 1. Idempotency Check for Duplicate Request
    const existingReq = requestStore.find((r) => r.idempotencyKey === idempotencyKey);
    if (existingReq) {
      const existingRes = resultStore.find((r) => r.requestId === existingReq.id)!;
      const existingSession = sessionStore.find((s) => s.id === existingReq.sessionId)!;
      const existingJournals = journalStore.filter((j) => j.requestId === existingReq.id);

      return {
        session: existingSession,
        request: existingReq,
        result: existingRes,
        journal: existingJournals,
      };
    }

    const ruleConfig = await TradingRulesService.getRuleConfig();
    const mode = input.mode || ExecutionMode.PAPER;
    const adapter: IExecutionAdapter = ExecutionEngineService.getAdapter(mode);

    // 2. Create Execution Session
    const session: ExecutionSessionDto = {
      id: `EX-SES-${Date.now()}`,
      mode,
      pair: input.symbol,
      timeframe: '1H',
      decisionId: input.decisionId,
      ruleVersion: ruleConfig.ruleVersion,
      configVersion: ruleConfig.configVersion,
      adapter: adapter.name,
      createdAt: new Date().toISOString(),
    };
    sessionStore.unshift(session);

    // 3. Create Execution Request (State: QUEUED)
    const requestId = `EX-REQ-${Date.now()}`;
    const request: ExecutionRequestDto = {
      id: requestId,
      sessionId: session.id,
      idempotencyKey,
      decisionId: input.decisionId,
      symbol: input.symbol,
      side: input.side,
      mode,
      ruleVersion: ruleConfig.ruleVersion,
      configVersion: ruleConfig.configVersion,
      orderType: input.price ? 'LIMIT' : 'MARKET',
      quantity: input.quantity,
      price: input.price,
      stopLoss: input.stopLoss,
      takeProfit: input.takeProfit,
      timestamp: new Date().toISOString(),
    };
    requestStore.unshift(request);

    const sessionJournals: ExecutionJournalDto[] = [];
    let currentState = ExecutionStatus.QUEUED;

    // Log QUEUED state
    const queuedJournal: ExecutionJournalDto = {
      id: `EX-JRN-${Date.now()}-1`,
      sessionId: session.id,
      requestId: request.id,
      resultId: '',
      adapter: adapter.name,
      fromState: ExecutionStatus.QUEUED,
      toState: ExecutionStatus.QUEUED,
      action: `QUEUE_${input.side}_${input.symbol}`,
      details: `Request ${requestId} queued into execution pipeline.`,
      latencyMs: 1,
      timestamp: new Date().toISOString(),
    };
    journalStore.unshift(queuedJournal);
    sessionJournals.push(queuedJournal);

    // 4. Execution Validation Phase
    const valStart = Date.now();
    const validation = ExecutionValidator.validateExecutionRequest(input, processedIdempotencyKeys);
    const validationLatencyMs = Date.now() - valStart;

    if (!validation.valid) {
      const nextState = ExecutionStatus.REJECTED;
      ExecutionStateMachine.validateTransition(currentState, nextState);

      const result: ExecutionResultDto = {
        id: `RES-${Date.now()}`,
        requestId: request.id,
        sessionId: session.id,
        adapter: adapter.name,
        status: nextState,
        filledQuantity: 0,
        observability: {
          queueTimeMs: 2,
          validationLatencyMs,
          adapterLatencyMs: 0,
          totalLifecycleTimeMs: Date.now() - totalStart,
        },
        message: validation.reason || 'Validation failed.',
        timestamp: new Date().toISOString(),
      };
      resultStore.unshift(result);

      const valFailedJournal: ExecutionJournalDto = {
        id: `EX-JRN-${Date.now()}-2`,
        sessionId: session.id,
        requestId: request.id,
        resultId: result.id,
        adapter: adapter.name,
        fromState: currentState,
        toState: nextState,
        action: 'VALIDATION_FAILED',
        details: validation.reason || 'Execution request failed validator checks.',
        latencyMs: validationLatencyMs,
        timestamp: new Date().toISOString(),
      };
      journalStore.unshift(valFailedJournal);
      sessionJournals.push(valFailedJournal);

      return { session, request, result, journal: sessionJournals };
    }

    processedIdempotencyKeys.add(idempotencyKey);

    // Transition QUEUED -> VALIDATED
    const validatedState = ExecutionStatus.VALIDATED;
    ExecutionStateMachine.validateTransition(currentState, validatedState);
    currentState = validatedState;

    const validatedJournal: ExecutionJournalDto = {
      id: `EX-JRN-${Date.now()}-2`,
      sessionId: session.id,
      requestId: request.id,
      resultId: '',
      adapter: adapter.name,
      fromState: ExecutionStatus.QUEUED,
      toState: ExecutionStatus.VALIDATED,
      action: 'VALIDATION_PASSED',
      details: 'All execution rules & risk checks passed.',
      latencyMs: validationLatencyMs,
      timestamp: new Date().toISOString(),
    };
    journalStore.unshift(validatedJournal);
    sessionJournals.push(validatedJournal);

    // 5. Adapter Submission Phase
    const adapterStart = Date.now();
    const adapterResult = await adapter.submit(request);
    const adapterLatencyMs = Date.now() - adapterStart;

    // Transition VALIDATED -> SUBMITTED (or FILLED / REJECTED)
    const finalState = adapterResult.status;
    if (ExecutionStateMachine.isValidTransition(currentState, ExecutionStatus.SUBMITTED)) {
      ExecutionStateMachine.validateTransition(currentState, ExecutionStatus.SUBMITTED);
      currentState = ExecutionStatus.SUBMITTED;
    }
    if (currentState !== finalState && ExecutionStateMachine.isValidTransition(currentState, finalState)) {
      ExecutionStateMachine.validateTransition(currentState, finalState);
      currentState = finalState;
    }

    const totalLifecycleTimeMs = Date.now() - totalStart;
    const finalResult: ExecutionResultDto = {
      ...adapterResult,
      observability: {
        queueTimeMs: 2,
        validationLatencyMs,
        adapterLatencyMs,
        totalLifecycleTimeMs,
      },
    };
    resultStore.unshift(finalResult);

    const submissionJournal: ExecutionJournalDto = {
      id: `EX-JRN-${Date.now()}-3`,
      sessionId: session.id,
      requestId: request.id,
      resultId: finalResult.id,
      adapter: adapter.name,
      fromState: ExecutionStatus.SUBMITTED,
      toState: finalState,
      action: `ADAPTER_RESPONSE_${finalState}`,
      details: finalResult.message || `Adapter ${adapter.name} returned ${finalState}`,
      latencyMs: adapterLatencyMs,
      timestamp: new Date().toISOString(),
    };
    journalStore.unshift(submissionJournal);
    sessionJournals.push(submissionJournal);

    return { session, request, result: finalResult, journal: sessionJournals };
  }

  public static async getSessions(): Promise<ExecutionSessionDto[]> {
    return sessionStore;
  }

  public static async getRequests(): Promise<ExecutionRequestDto[]> {
    return requestStore;
  }

  public static async getResults(): Promise<ExecutionResultDto[]> {
    return resultStore;
  }

  public static async getJournal(): Promise<ExecutionJournalDto[]> {
    return journalStore;
  }

  public static getAdapter(mode: ExecutionMode): IExecutionAdapter {
    if (mode === ExecutionMode.LIVE) {
      return getDeltaAdapter();
    }
    return paperAdapter;
  }
}
