import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ShadowTriggerService } from '../../backend/src/modules/shadow-trading/services/ShadowTriggerService.js';
import { ShadowTradingEngineService } from '../../backend/src/modules/shadow-trading/services/shadowTradingEngine.service.js';
import { eventBus } from '../../backend/src/services/EventBus.js';
import { prisma } from '../../backend/src/db.js';

vi.mock('../../backend/src/modules/shadow-trading/services/shadowTradingEngine.service.js', () => ({
  ShadowTradingEngineService: {
    runShadowCycle: vi.fn().mockResolvedValue({ status: 'SHADOW_CYCLE_EXECUTED', record: { id: 'test' } }),
  },
}));

vi.mock('../../backend/src/db.js', () => ({
  prisma: {
    scannerPair: {
      findUnique: vi.fn(),
    },
  },
}));

vi.mock('../../backend/src/services/EventBus.js', () => ({
  eventBus: {
    on: vi.fn(),
    emit: vi.fn(),
  },
}));

describe('ShadowTriggerService - Automatic Shadow Pipeline Trigger', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    ShadowTriggerService.clearProcessedCandlesForTesting();
  });

  afterEach(() => {
    vi.useRealTimers();
    ShadowTriggerService.stop();
  });

  it('should start and register event listener for candle:1H:update', async () => {
    await ShadowTriggerService.start();
    
    expect(eventBus.on).toHaveBeenCalledWith('candle:1H:update', expect.any(Function));
  });

  it('should stop and clear running flag', async () => {
    await ShadowTriggerService.start();
    ShadowTriggerService.stop();
    
    // The stop method just sets isRunning = false
    // We can't easily test the internal flag, but we can verify it doesn't throw
  });

  it('should NOT trigger Shadow pipeline for non-new candle (isNew === false)', async () => {
    await ShadowTriggerService.start();
    
    // Get the handler that was registered
    const handler = (eventBus.on as vi.Mock).mock.calls.find(
      (call: any[]) => call[0] === 'candle:1H:update'
    )?.[1];
    
    expect(handler).toBeDefined();
    
    // Call handler with isNew: false
    await handler({
      symbol: 'BTCUSD.P',
      candle: {
        id: 'candle-1',
        symbol: 'BTCUSD.P',
        timeframe: '1H',
        open: 65000,
        high: 65500,
        low: 64500,
        close: 65200,
        volume: 100,
        timestamp: '2026-08-12T12:00:00Z',
      },
      isNew: false,
    });
    
    // Should NOT call runShadowCycle
    expect(ShadowTradingEngineService.runShadowCycle).not.toHaveBeenCalled();
  });

  it('should trigger Shadow pipeline for new 1H candle (isNew === true) on active pair', async () => {
    await ShadowTriggerService.start();
    
    (prisma.scannerPair.findUnique as vi.Mock).mockResolvedValue({
      isActive: true,
      isPaused: false,
      status: 'ENGINE',
    });
    
    const handler = (eventBus.on as vi.Mock).mock.calls.find(
      (call: any[]) => call[0] === 'candle:1H:update'
    )?.[1];
    
    await handler({
      symbol: 'BTCUSD.P',
      candle: {
        id: 'candle-1',
        symbol: 'BTCUSD.P',
        timeframe: '1H',
        open: 65000,
        high: 65500,
        low: 64500,
        close: 65200,
        volume: 100,
        timestamp: '2026-08-12T12:00:00Z',
      },
      isNew: true,
    });
    
    expect(ShadowTradingEngineService.runShadowCycle).toHaveBeenCalledWith('BTCUSD.P');
  });

  it('should NOT trigger Shadow pipeline for inactive/paused pair', async () => {
    await ShadowTriggerService.start();
    
    (prisma.scannerPair.findUnique as vi.Mock).mockResolvedValue({
      isActive: false,
      isPaused: false,
      status: 'ENGINE',
    });
    
    const handler = (eventBus.on as vi.Mock).mock.calls.find(
      (call: any[]) => call[0] === 'candle:1H:update'
    )?.[1];
    
    await handler({
      symbol: 'INACTIVE.P',
      candle: {
        id: 'candle-1',
        symbol: 'INACTIVE.P',
        timeframe: '1H',
        open: 65000,
        high: 65500,
        low: 64500,
        close: 65200,
        volume: 100,
        timestamp: '2026-08-12T12:00:00Z',
      },
      isNew: true,
    });
    
    expect(ShadowTradingEngineService.runShadowCycle).not.toHaveBeenCalled();
  });

  it('should prevent duplicate processing of the same candle', async () => {
    await ShadowTriggerService.start();
    
    (prisma.scannerPair.findUnique as vi.Mock).mockResolvedValue({
      isActive: true,
      isPaused: false,
      status: 'ENGINE',
    });
    
    const handler = (eventBus.on as vi.Mock).mock.calls.find(
      (call: any[]) => call[0] === 'candle:1H:update'
    )?.[1];
    
    const candleData = {
      symbol: 'BTCUSD.P',
      candle: {
        id: 'candle-1',
        symbol: 'BTCUSD.P',
        timeframe: '1H',
        open: 65000,
        high: 65500,
        low: 64500,
        close: 65200,
        volume: 100,
        timestamp: '2026-08-12T12:00:00Z',
      },
      isNew: true,
    };
    
    // First call - should trigger
    await handler(candleData);
    expect(ShadowTradingEngineService.runShadowCycle).toHaveBeenCalledTimes(1);
    
    // Second call with same candle - should NOT trigger again
    await handler(candleData);
    expect(ShadowTradingEngineService.runShadowCycle).toHaveBeenCalledTimes(1);
  });

  it('should process different candles for the same symbol', async () => {
    await ShadowTriggerService.start();
    
    (prisma.scannerPair.findUnique as vi.Mock).mockResolvedValue({
      isActive: true,
      isPaused: false,
      status: 'ENGINE',
    });
    
    const handler = (eventBus.on as vi.Mock).mock.calls.find(
      (call: any[]) => call[0] === 'candle:1H:update'
    )?.[1];
    
    // First candle
    await handler({
      symbol: 'BTCUSD.P',
      candle: {
        id: 'candle-1',
        symbol: 'BTCUSD.P',
        timeframe: '1H',
        open: 65000,
        high: 65500,
        low: 64500,
        close: 65200,
        volume: 100,
        timestamp: '2026-08-12T12:00:00Z',
      },
      isNew: true,
    });
    
    // Second candle (different timestamp)
    await handler({
      symbol: 'BTCUSD.P',
      candle: {
        id: 'candle-2',
        symbol: 'BTCUSD.P',
        timeframe: '1H',
        open: 65200,
        high: 65700,
        low: 65100,
        close: 65400,
        volume: 120,
        timestamp: '2026-08-12T13:00:00Z',
      },
      isNew: true,
    });
    
    expect(ShadowTradingEngineService.runShadowCycle).toHaveBeenCalledTimes(2);
  });

  it('should NOT trigger when stopped', async () => {
    await ShadowTriggerService.start();
    ShadowTriggerService.stop();
    
    (prisma.scannerPair.findUnique as vi.Mock).mockResolvedValue({
      isActive: true,
      isPaused: false,
      status: 'ENGINE',
    });
    
    const handler = (eventBus.on as vi.Mock).mock.calls.find(
      (call: any[]) => call[0] === 'candle:1H:update'
    )?.[1];
    
    await handler({
      symbol: 'BTCUSD.P',
      candle: {
        id: 'candle-1',
        symbol: 'BTCUSD.P',
        timeframe: '1H',
        open: 65000,
        high: 65500,
        low: 64500,
        close: 65200,
        volume: 100,
        timestamp: '2026-08-12T12:00:00Z',
      },
      isNew: true,
    });
    
    expect(ShadowTradingEngineService.runShadowCycle).not.toHaveBeenCalled();
  });
});