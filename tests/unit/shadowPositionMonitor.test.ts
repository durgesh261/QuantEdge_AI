import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ShadowPositionMonitor } from '../../backend/src/modules/shadow-trading/services/ShadowPositionMonitor.js';
import { OutcomeValidatorService } from '../../backend/src/modules/shadow-trading/services/outcomeValidator.service.js';
import { deltaSyncService } from '../../backend/src/modules/delta-exchange/index.js';
import { prisma } from '../../backend/src/db.js';
import { eventBus } from '../../backend/src/services/EventBus.js';

// Mock all external dependencies
vi.mock('../../backend/src/modules/delta-exchange/index.js', () => ({
  deltaSyncService: {
    onPriceTick: vi.fn(),
    getMarkPrice: vi.fn(),
    closePosition: vi.fn(),
  },
}));

vi.mock('../../backend/src/db.js', () => ({
  prisma: {
    shadowPosition: {
      findMany: vi.fn(),
      update: vi.fn(),
    },
    marketOutcomeValidation: {
      upsert: vi.fn(),
    },
  },
}));

vi.mock('../../backend/src/services/EventBus.js', () => ({
  eventBus: {
    emit: vi.fn(),
  },
}));

vi.mock('../../../logger/index.js', () => ({
  logger: {
    info: vi.fn(),
    error: vi.fn(),
    warn: vi.fn(),
  },
}));

describe('ShadowPositionMonitor - SL/TP Monitoring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    ShadowPositionMonitor.stop();
  });

  it('should detect LONG TP hit when price reaches takeProfitPrice', async () => {
    const mockPosition = {
      id: 'shadow-pos-1',
      decisionId: 'dec-1',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      side: 'LONG',
      entryPrice: 64000,
      stopLossPrice: 63500,
      takeProfitPrice: 65000,
      quantity: 0.5,
      leverage: 10,
      riskPercent: 35,
      confidenceScore: 92,
      reasonCodesJson: '["FRESH_ZONE_CONFIRMED"]',
      status: 'OPEN',
      tpHitAt: null,
      slHitAt: null,
      holdDurationMinutes: null,
      mfe: null,
      mae: null,
      createdAt: new Date(Date.now() - 3600000), // 1 hour ago
      updatedAt: new Date(),
    };

    prisma.shadowPosition.findMany.mockResolvedValue([mockPosition]);
    deltaSyncService.getMarkPrice.mockResolvedValue(65100); // Above TP

    await ShadowPositionMonitor.start();
    await ShadowPositionMonitor.checkAllPositions();

    expect(prisma.shadowPosition.update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { id: 'shadow-pos-1' },
        data: expect.objectContaining({ status: 'TP_HIT' }),
      })
    );
  });

  it('should detect LONG SL hit when price falls to stopLossPrice', async () => {
    const mockPosition = {
      id: 'shadow-pos-2',
      decisionId: 'dec-2',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      side: 'LONG',
      entryPrice: 64000,
      stopLossPrice: 63500,
      takeProfitPrice: 65000,
      quantity: 0.5,
      leverage: 10,
      riskPercent: 35,
      confidenceScore: 92,
      reasonCodesJson: '["FRESH_ZONE_CONFIRMED"]',
      status: 'OPEN',
      tpHitAt: null,
      slHitAt: null,
      holdDurationMinutes: null,
      mfe: null,
      mae: null,
      createdAt: new Date(Date.now() - 3600000),
      updatedAt: new Date(),
    };

    prisma.shadowPosition.findMany.mockResolvedValue([mockPosition]);
    deltaSyncService.getMarkPrice.mockResolvedValue(63400); // Below SL

    await ShadowPositionMonitor.start();
    await ShadowPositionMonitor.checkAllPositions();

    expect(prisma.shadowPosition.update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { id: 'shadow-pos-2' },
        data: expect.objectContaining({ status: 'SL_HIT' }),
      })
    );
  });

  it('should detect SHORT TP hit when price falls to takeProfitPrice', async () => {
    const mockPosition = {
      id: 'shadow-pos-3',
      decisionId: 'dec-3',
      symbol: 'ETHUSD.P',
      timeframe: '1H',
      side: 'SHORT',
      entryPrice: 3400,
      stopLossPrice: 3450,
      takeProfitPrice: 3300,
      quantity: 2.0,
      leverage: 10,
      riskPercent: 35,
      confidenceScore: 88,
      reasonCodesJson: '["SMC_BOS_BREAKOUT"]',
      status: 'OPEN',
      tpHitAt: null,
      slHitAt: null,
      holdDurationMinutes: null,
      mfe: null,
      mae: null,
      createdAt: new Date(Date.now() - 7200000),
      updatedAt: new Date(),
    };

    prisma.shadowPosition.findMany.mockResolvedValue([mockPosition]);
    deltaSyncService.getMarkPrice.mockResolvedValue(3290); // Below TP

    await ShadowPositionMonitor.start();
    await ShadowPositionMonitor.checkAllPositions();

    expect(prisma.shadowPosition.update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { id: 'shadow-pos-3' },
        data: expect.objectContaining({ status: 'TP_HIT' }),
      })
    );
  });

  it('should detect SHORT SL hit when price rises to stopLossPrice', async () => {
    const mockPosition = {
      id: 'shadow-pos-4',
      decisionId: 'dec-4',
      symbol: 'ETHUSD.P',
      timeframe: '1H',
      side: 'SHORT',
      entryPrice: 3400,
      stopLossPrice: 3450,
      takeProfitPrice: 3300,
      quantity: 2.0,
      leverage: 10,
      riskPercent: 35,
      confidenceScore: 88,
      reasonCodesJson: '["SMC_BOS_BREAKOUT"]',
      status: 'OPEN',
      tpHitAt: null,
      slHitAt: null,
      holdDurationMinutes: null,
      mfe: null,
      mae: null,
      createdAt: new Date(Date.now() - 7200000),
      updatedAt: new Date(),
    };

    prisma.shadowPosition.findMany.mockResolvedValue([mockPosition]);
    deltaSyncService.getMarkPrice.mockResolvedValue(3460); // Above SL

    await ShadowPositionMonitor.start();
    await ShadowPositionMonitor.checkAllPositions();

    expect(prisma.shadowPosition.update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { id: 'shadow-pos-4' },
        data: expect.objectContaining({ status: 'SL_HIT' }),
      })
    );
  });

  it('should not close position when price is between SL and TP', async () => {
    const mockPosition = {
      id: 'shadow-pos-5',
      decisionId: 'dec-5',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      side: 'LONG',
      entryPrice: 64000,
      stopLossPrice: 63500,
      takeProfitPrice: 65000,
      quantity: 0.5,
      leverage: 10,
      riskPercent: 35,
      confidenceScore: 92,
      reasonCodesJson: '["FRESH_ZONE_CONFIRMED"]',
      status: 'OPEN',
      tpHitAt: null,
      slHitAt: null,
      holdDurationMinutes: null,
      mfe: null,
      mae: null,
      createdAt: new Date(Date.now() - 3600000),
      updatedAt: new Date(),
    };

    prisma.shadowPosition.findMany.mockResolvedValue([mockPosition]);
    deltaSyncService.getMarkPrice.mockResolvedValue(64200); // Between SL and TP

    await ShadowPositionMonitor.start();
    await ShadowPositionMonitor.checkAllPositions();

    expect(prisma.shadowPosition.update).not.toHaveBeenCalled();
  });

  it('should not process positions with non-OPEN status', async () => {
    const mockPosition = {
      id: 'shadow-pos-6',
      decisionId: 'dec-6',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      side: 'LONG',
      entryPrice: 64000,
      stopLossPrice: 63500,
      takeProfitPrice: 65000,
      quantity: 0.5,
      leverage: 10,
      riskPercent: 35,
      confidenceScore: 92,
      reasonCodesJson: '["FRESH_ZONE_CONFIRMED"]',
      status: 'TP_HIT', // Already closed
      tpHitAt: new Date(),
      slHitAt: null,
      holdDurationMinutes: 60,
      mfe: 1.5,
      mae: 0.5,
      createdAt: new Date(Date.now() - 3600000),
      updatedAt: new Date(),
    };

    prisma.shadowPosition.findMany.mockResolvedValue([mockPosition]);

    await ShadowPositionMonitor.start();
    await ShadowPositionMonitor.checkAllPositions();

    expect(prisma.shadowPosition.update).not.toHaveBeenCalled();
  });

  it('should persist MarketOutcomeValidation when position closes', async () => {
    const mockPosition = {
      id: 'shadow-pos-7',
      decisionId: 'dec-7',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      side: 'LONG',
      entryPrice: 64000,
      stopLossPrice: 63500,
      takeProfitPrice: 65000,
      quantity: 0.5,
      leverage: 10,
      riskPercent: 35,
      confidenceScore: 92,
      reasonCodesJson: '["FRESH_ZONE_CONFIRMED"]',
      status: 'OPEN',
      tpHitAt: null,
      slHitAt: null,
      holdDurationMinutes: null,
      mfe: null,
      mae: null,
      createdAt: new Date(Date.now() - 3600000),
      updatedAt: new Date(),
    };

    prisma.shadowPosition.findMany.mockResolvedValue([mockPosition]);
    deltaSyncService.getMarkPrice.mockResolvedValue(65100);

    await ShadowPositionMonitor.start();
    await ShadowPositionMonitor.checkAllPositions();

    expect(prisma.marketOutcomeValidation.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { decisionId: 'dec-7' },
        create: expect.objectContaining({ tpHit: true }),
      })
    );
  });

  it('should emit shadow:outcome event when position closes', async () => {
    const mockPosition = {
      id: 'shadow-pos-8',
      decisionId: 'dec-8',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      side: 'LONG',
      entryPrice: 64000,
      stopLossPrice: 63500,
      takeProfitPrice: 65000,
      quantity: 0.5,
      leverage: 10,
      riskPercent: 35,
      confidenceScore: 92,
      reasonCodesJson: '["FRESH_ZONE_CONFIRMED"]',
      status: 'OPEN',
      tpHitAt: null,
      slHitAt: null,
      holdDurationMinutes: null,
      mfe: null,
      mae: null,
      createdAt: new Date(Date.now() - 3600000),
      updatedAt: new Date(),
    };

    prisma.shadowPosition.findMany.mockResolvedValue([mockPosition]);
    deltaSyncService.getMarkPrice.mockResolvedValue(65100);

    await ShadowPositionMonitor.start();
    await ShadowPositionMonitor.checkAllPositions();

    expect(eventBus.emit).toHaveBeenCalledWith(
      'shadow:outcome',
      expect.objectContaining({
        decisionId: 'dec-8',
        symbol: 'BTCUSD.P',
        side: 'LONG',
        reason: 'TP_HIT',
      })
    );
  });

  it('should subscribe to deltaSyncService.onPriceTick', async () => {
    await ShadowPositionMonitor.start();
    expect(deltaSyncService.onPriceTick).toHaveBeenCalled();
  });

  it('should not call Delta closePosition (no real execution)', async () => {
    const mockPosition = {
      id: 'shadow-pos-9',
      decisionId: 'dec-9',
      symbol: 'BTCUSD.P',
      timeframe: '1H',
      side: 'LONG',
      entryPrice: 64000,
      stopLossPrice: 63500,
      takeProfitPrice: 65000,
      quantity: 0.5,
      leverage: 10,
      riskPercent: 35,
      confidenceScore: 92,
      reasonCodesJson: '["FRESH_ZONE_CONFIRMED"]',
      status: 'OPEN',
      tpHitAt: null,
      slHitAt: null,
      holdDurationMinutes: null,
      mfe: null,
      mae: null,
      createdAt: new Date(Date.now() - 3600000),
      updatedAt: new Date(),
    };

    prisma.shadowPosition.findMany.mockResolvedValue([mockPosition]);
    deltaSyncService.getMarkPrice.mockResolvedValue(65100);

    await ShadowPositionMonitor.start();
    await ShadowPositionMonitor.checkAllPositions();

    // Verify deltaSyncService.closePosition was NOT called
    expect(deltaSyncService.closePosition).not.toHaveBeenCalled();
    expect(deltaSyncService.onPriceTick).toHaveBeenCalled();
  });
});