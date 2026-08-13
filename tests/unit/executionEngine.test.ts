import { describe, it, expect } from 'vitest';
import { ExecutionMode, ExecutionStatus } from '@algoapp/shared';
import { ExecutionEngineService } from '../../backend/src/modules/execution/services/executionEngine.service.js';

describe('Execution Engine Unit Tests', () => {
  it('should get sessions and adapters for paper mode', async () => {
    const adapter = ExecutionEngineService.getAdapter(ExecutionMode.PAPER);
    expect(adapter.name).toBe('PAPER_ADAPTER');

    const sessions = await ExecutionEngineService.getSessions();
    expect(Array.isArray(sessions)).toBe(true);
  });

  it('should get adapter for SHADOW mode - routes to PaperAdapter', async () => {
    const adapter = ExecutionEngineService.getAdapter(ExecutionMode.SHADOW);
    expect(adapter.name).toBe('PAPER_ADAPTER');
  });

  it('should get adapter for LIVE mode - routes to DeltaAdapter', async () => {
    const adapter = ExecutionEngineService.getAdapter(ExecutionMode.LIVE);
    expect(adapter.name).toBe('DELTA_ADAPTER');
  });

  it('should submit execution request to PaperAdapter in paper mode', async () => {
    const outcome = await ExecutionEngineService.submitExecution({
      decisionId: 'DEC-101',
      symbol: 'BTCUSD.P',
      side: 'LONG',
      mode: ExecutionMode.PAPER,
      quantity: 0.001,
      price: 64000.0,
    });

    expect(outcome.session.id).toBeDefined();
    expect(outcome.request.symbol).toBe('BTCUSD.P');
    expect(outcome.request.mode).toBe(ExecutionMode.PAPER);
    expect(outcome.result.adapter).toBe('PAPER_ADAPTER');
    expect(outcome.result.status).toBe(ExecutionStatus.SUBMITTED);
    expect(outcome.journal.length).toBeGreaterThan(0);
  });

  it('should submit execution request to PaperAdapter in SHADOW mode', async () => {
    const outcome = await ExecutionEngineService.submitExecution({
      decisionId: 'DEC-SHADOW-101',
      symbol: 'BTCUSD.P',
      side: 'LONG',
      mode: ExecutionMode.SHADOW,
      quantity: 0.001,
      price: 64000.0,
    });

    expect(outcome.session.id).toBeDefined();
    expect(outcome.request.symbol).toBe('BTCUSD.P');
    expect(outcome.request.mode).toBe(ExecutionMode.SHADOW);
    expect(outcome.result.adapter).toBe('PAPER_ADAPTER');
    expect(outcome.result.status).toBe(ExecutionStatus.SUBMITTED);
    expect(outcome.journal.length).toBeGreaterThan(0);
  });

  it('should reject live mode execution via DeltaAdapter when safety guards are unfulfilled', async () => {
    const outcome = await ExecutionEngineService.submitExecution({
      decisionId: 'DEC-102',
      symbol: 'ETHUSD.P',
      side: 'SHORT',
      mode: ExecutionMode.LIVE,
      quantity: 1.0,
    });

    expect(outcome.result.adapter).toBe('DELTA_ADAPTER');
    expect(outcome.result.status).toBe(ExecutionStatus.REJECTED);
    expect(outcome.result.message).toContain('LIVE_SAFETY_REJECTED');
  });
});
