/**
 * Position Monitor SL/TP Tests — Real-time position monitoring
 * Run: npx jest tests/positionMonitorSLTP.test.ts
 */

// Mock DeltaSyncService
jest.mock('../src/modules/delta-exchange/index', () => {
  const mockGetPositions = jest.fn();
  const mockGetMarkPrice = jest.fn();
  const mockClosePosition = jest.fn();
  const mockOnWsPositionUpdate = jest.fn();
  const mockGetConnectionStatus = jest.fn().mockReturnValue('CONNECTED');
  return {
    deltaSyncService: {
      getPositions: mockGetPositions,
      getMarkPrice: mockGetMarkPrice,
      closePosition: mockClosePosition,
      onWsPositionUpdate: mockOnWsPositionUpdate,
      getConnectionStatus: mockGetConnectionStatus,
    },
    __mocks: {
      mockGetPositions,
      mockGetMarkPrice,
      mockClosePosition,
      mockOnWsPositionUpdate,
      mockGetConnectionStatus,
    },
  };
});

// Mock Prisma
jest.mock('../src/db', () => {
  const mockTradeLedgerUpdate = jest.fn();
  const mockTradeLedgerCreate = jest.fn();
  const mockTradeLedgerFindMany = jest.fn();
  const mockScannerStateUpdateMany = jest.fn();
  const mockScannerPairUpdate = jest.fn();
  return {
    prisma: {
      tradeLedger: {
        update: mockTradeLedgerUpdate,
        create: mockTradeLedgerCreate,
        findMany: mockTradeLedgerFindMany,
      },
      scannerState: {
        updateMany: mockScannerStateUpdateMany,
      },
      scannerPair: {
        update: mockScannerPairUpdate,
      },
    },
    __mocks: {
      mockTradeLedgerUpdate,
      mockTradeLedgerCreate,
      mockTradeLedgerFindMany,
      mockScannerStateUpdateMany,
      mockScannerPairUpdate,
    },
  };
});

// Mock WalletEngineService
jest.mock('../src/modules/trade-accounting/services/walletEngine.service', () => {
  const mockApplyTradeResult = jest.fn();
  return {
    WalletEngineService: jest.fn().mockImplementation(() => ({
      applyTradeResult: mockApplyTradeResult,
    })),
    __mocks: {
      mockApplyTradeResult,
    },
  };
});

// Mock CanonicalOBRegistry
jest.mock('../src/modules/indicator-engine/services/canonicalOBRegistry', () => {
  const mockMarkTraded = jest.fn();
  return {
    CanonicalOBRegistry: {
      markTraded: mockMarkTraded,
    },
    __mocks: {
      mockMarkTraded,
    },
  };
});

// Mock logger
jest.mock('../src/logger/index', () => ({
  logger: {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  },
}));

// Mock eventBus
jest.mock('../src/services/EventBus', () => ({
  eventBus: {
    emit: jest.fn(),
  },
}));

// Now import the service under test and the mocks
import { PositionMonitorService } from '../src/modules/position-monitor/services/PositionMonitorService';
import { PositionRecoveryService } from '../src/modules/position-monitor/services/PositionRecoveryService';
import { TradeAccountingService } from '../src/modules/trade-accounting/services/tradeAccounting.service';

// Access the mocks from the jest.mock modules
const deltaSyncMocks = jest.requireMock('../src/modules/delta-exchange/index').__mocks;
const prismaMocks = jest.requireMock('../src/db').__mocks;
const walletMocks = jest.requireMock('../src/modules/trade-accounting/services/walletEngine.service').__mocks;
const canonicalOBRegistryMocks = jest.requireMock('../src/modules/indicator-engine/services/canonicalOBRegistry').__mocks;

describe('PositionMonitorService — SL/TP Monitoring', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();
    
    // Reset PositionMonitorService state
    (PositionMonitorService as any).monitoredPositions.clear();
    (PositionMonitorService as any).isMonitoring = false;
    (PositionMonitorService as any).monitorTimer = null;
    
    // Reset trade ledger findMany to return empty by default
    prismaMocks.mockTradeLedgerFindMany.mockResolvedValue([]);
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  // Helper to create a monitored position
  function addMonitoredPosition(overrides: Partial<{
    symbol: string;
    side: 'LONG' | 'SHORT';
    entryPrice: number;
    stopLossPrice: number;
    takeProfitPrice: number;
    quantity: number;
    leverage: number;
    tradeId: string;
    orderBlockId: string;
  }> = {}) {
    const defaultPos = {
      symbol: 'BTCUSD.P',
      side: 'LONG' as const,
      entryPrice: 100000,
      stopLossPrice: 99000,
      takeProfitPrice: 102000,
      quantity: 0.01,
      leverage: 10,
      tradeId: 'TEST-TRADE-1',
      orderBlockId: 'OB-TEST-1',
      entryTime: new Date().toISOString(),
      deltaProductSymbol: 'BTCUSD.P',
      deltaPositionId: 123,
    };
    PositionMonitorService.addPosition({ ...defaultPos, ...overrides });
  }

  describe('Long Position SL/TP', () => {
    test('A. Long position reaches TP — position closed, trade recorded', async () => {
      addMonitoredPosition({
        side: 'LONG',
        entryPrice: 100000,
        stopLossPrice: 99000,
        takeProfitPrice: 102000,
        quantity: 0.01,
        leverage: 10,
      });

      // Simulate position still open on Delta
      deltaSyncMocks.mockGetPositions.mockReturnValue([
        { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.01, entry_price: '100000', mark_price: '100000' },
      ]);
      
      // Mark price hits TP
      deltaSyncMocks.mockGetMarkPrice.mockResolvedValue(102000);
      deltaSyncMocks.mockClosePosition.mockResolvedValue({ success: true });
      prismaMocks.mockTradeLedgerUpdate.mockResolvedValue({});
      walletMocks.mockApplyTradeResult.mockResolvedValue({});
      canonicalOBRegistryMocks.mockMarkTraded.mockResolvedValue(undefined);

      // Start monitoring
      PositionMonitorService.start();
      
      // Advance timers to trigger checkAllPositions
      await jest.advanceTimersByTimeAsync(5000);

      // Verify position was closed
      expect(deltaSyncMocks.mockClosePosition).toHaveBeenCalledWith('BTCUSD.P');
      expect(prismaMocks.mockTradeLedgerUpdate).toHaveBeenCalled();
      expect(walletMocks.mockApplyTradeResult).toHaveBeenCalled();
      expect(canonicalOBRegistryMocks.mockMarkTraded).toHaveBeenCalledWith('OB-TEST-1');
    });

    test('B. Long position reaches SL — position closed, trade recorded', async () => {
      addMonitoredPosition({
        side: 'LONG',
        entryPrice: 100000,
        stopLossPrice: 99000,
        takeProfitPrice: 102000,
        quantity: 0.01,
        leverage: 10,
      });

      deltaSyncMocks.mockGetPositions.mockReturnValue([
        { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.01, entry_price: '100000', mark_price: '100000' },
      ]);
      
      // Mark price hits SL
      deltaSyncMocks.mockGetMarkPrice.mockResolvedValue(99000);
      deltaSyncMocks.mockClosePosition.mockResolvedValue({ success: true });
      prismaMocks.mockTradeLedgerUpdate.mockResolvedValue({});
      walletMocks.mockApplyTradeResult.mockResolvedValue({});
      canonicalOBRegistryMocks.mockMarkTraded.mockResolvedValue(undefined);

      PositionMonitorService.start();
      await jest.advanceTimersByTimeAsync(5000);

      expect(deltaSyncMocks.mockClosePosition).toHaveBeenCalledWith('BTCUSD.P');
      expect(prismaMocks.mockTradeLedgerUpdate).toHaveBeenCalled();
      expect(walletMocks.mockApplyTradeResult).toHaveBeenCalled();
    });
  });

  describe('Short Position SL/TP', () => {
    test('C. Short position reaches TP — position closed, trade recorded', async () => {
      addMonitoredPosition({
        side: 'SHORT',
        entryPrice: 100000,
        stopLossPrice: 101000,
        takeProfitPrice: 98000,
        quantity: 0.01,
        leverage: 10,
      });

      deltaSyncMocks.mockGetPositions.mockReturnValue([
        { product_symbol: 'BTCUSD.P', side: 'sell', size: -0.01, entry_price: '100000', mark_price: '100000' },
      ]);
      
      // Mark price hits TP (goes down for short)
      deltaSyncMocks.mockGetMarkPrice.mockResolvedValue(98000);
      deltaSyncMocks.mockClosePosition.mockResolvedValue({ success: true });
      prismaMocks.mockTradeLedgerUpdate.mockResolvedValue({});
      walletMocks.mockApplyTradeResult.mockResolvedValue({});
      canonicalOBRegistryMocks.mockMarkTraded.mockResolvedValue(undefined);

      PositionMonitorService.start();
      await jest.advanceTimersByTimeAsync(5000);

      expect(deltaSyncMocks.mockClosePosition).toHaveBeenCalledWith('BTCUSD.P');
      expect(prismaMocks.mockTradeLedgerUpdate).toHaveBeenCalled();
    });

    test('D. Short position reaches SL — position closed, trade recorded', async () => {
      addMonitoredPosition({
        side: 'SHORT',
        entryPrice: 100000,
        stopLossPrice: 101000,
        takeProfitPrice: 98000,
        quantity: 0.01,
        leverage: 10,
      });

      deltaSyncMocks.mockGetPositions.mockReturnValue([
        { product_symbol: 'BTCUSD.P', side: 'sell', size: -0.01, entry_price: '100000', mark_price: '100000' },
      ]);
      
      // Mark price hits SL (goes up for short)
      deltaSyncMocks.mockGetMarkPrice.mockResolvedValue(101000);
      deltaSyncMocks.mockClosePosition.mockResolvedValue({ success: true });
      prismaMocks.mockTradeLedgerUpdate.mockResolvedValue({});
      walletMocks.mockApplyTradeResult.mockResolvedValue({});
      canonicalOBRegistryMocks.mockMarkTraded.mockResolvedValue(undefined);

      PositionMonitorService.start();
      await jest.advanceTimersByTimeAsync(5000);

      expect(deltaSyncMocks.mockClosePosition).toHaveBeenCalledWith('BTCUSD.P');
      expect(prismaMocks.mockTradeLedgerUpdate).toHaveBeenCalled();
    });
  });

  describe('Idempotency', () => {
    test('E. Duplicate price events do not create duplicate close orders', async () => {
      addMonitoredPosition({
        side: 'LONG',
        entryPrice: 100000,
        stopLossPrice: 99000,
        takeProfitPrice: 102000,
        quantity: 0.01,
        leverage: 10,
      });

      deltaSyncMocks.mockGetPositions.mockReturnValue([
        { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.01, entry_price: '100000', mark_price: '100000' },
      ]);
      
      deltaSyncMocks.mockGetMarkPrice.mockResolvedValue(102000);
      deltaSyncMocks.mockClosePosition.mockResolvedValue({ success: true });
      prismaMocks.mockTradeLedgerUpdate.mockResolvedValue({});
      walletMocks.mockApplyTradeResult.mockResolvedValue({});
      canonicalOBRegistryMocks.mockMarkTraded.mockResolvedValue(undefined);

      PositionMonitorService.start();
      
      // Trigger multiple checks in quick succession
      await jest.advanceTimersByTimeAsync(5000);
      await jest.advanceTimersByTimeAsync(5000);
      await jest.advanceTimersByTimeAsync(5000);

      // closePosition should only be called once
      expect(deltaSyncMocks.mockClosePosition).toHaveBeenCalledTimes(1);
    });
  });

  describe('Position Already Closed on Delta', () => {
    test('F. Position already closed on Delta — reconcile local state, no duplicate close order', async () => {
      addMonitoredPosition({
        side: 'LONG',
        entryPrice: 100000,
        stopLossPrice: 99000,
        takeProfitPrice: 102000,
        quantity: 0.01,
        leverage: 10,
      });

      // Delta reports no position
      deltaSyncMocks.mockGetPositions.mockReturnValue([]);
      prismaMocks.mockTradeLedgerUpdate.mockResolvedValue({});
      walletMocks.mockApplyTradeResult.mockResolvedValue({});
      canonicalOBRegistryMocks.mockMarkTraded.mockResolvedValue(undefined);

      PositionMonitorService.start();
      await jest.advanceTimersByTimeAsync(5000);

      // closePosition should NOT be called since position already closed
      expect(deltaSyncMocks.mockClosePosition).not.toHaveBeenCalled();
      // But trade should be recorded as closed
      expect(prismaMocks.mockTradeLedgerUpdate).toHaveBeenCalled();
      expect(walletMocks.mockApplyTradeResult).toHaveBeenCalled();
    });
  });

describe('Backend Restart Recovery', () => {
    test('G. Backend restart with open position — position reloaded and monitored', async () => {
      // Mock trade ledger with open trade
      prismaMocks.mockTradeLedgerFindMany.mockResolvedValue([
        {
          tradeId: 'RESTART-TRADE-1',
          symbol: 'BTCUSD.P',
          side: 'LONG',
          entryPrice: 100000,
          stopLoss: 99000,
          takeProfit: 102000,
          quantity: 0.01,
          leverage: 10,
          executedAt: new Date(),
          activeZoneId: 'OB-RESTART',
          syncStatus: 'SYNCED',
        },
      ]);

      deltaSyncMocks.mockGetPositions.mockReturnValue([
        { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.01, entry_price: '100000', product_id: 123 },
      ]);
      
      deltaSyncMocks.mockGetMarkPrice.mockResolvedValue(100000);
      deltaSyncMocks.mockClosePosition.mockResolvedValue({ success: true });
      deltaSyncMocks.mockGetConnectionStatus.mockReturnValue('CONNECTED');
      prismaMocks.mockTradeLedgerUpdate.mockResolvedValue({});
      walletMocks.mockApplyTradeResult.mockResolvedValue({});
      canonicalOBRegistryMocks.mockMarkTraded.mockResolvedValue(undefined);

      // Start monitoring (which calls recovery)
      try {
        const recoveryResult = await PositionRecoveryService.recoverPositions();
        console.log('Recovery result:', JSON.stringify(recoveryResult, null, 2));
      } catch (err) {
        console.error('Recovery error:', err);
        throw err;
      }
      
      await PositionMonitorService.start();
      
      // Position should be in monitoring
      const monitored = PositionMonitorService.getMonitoredPositions();
      console.log('Monitored positions:', monitored.length, monitored);
      expect(monitored.length).toBe(1);
      expect(monitored[0].tradeId).toBe('RESTART-TRADE-1');
      expect(monitored[0].symbol).toBe('BTCUSD.P');
    });
  });

  describe('Close Order Failure', () => {
    test('H. Close order failure — position re-added to monitoring for retry', async () => {
      addMonitoredPosition({
        side: 'LONG',
        entryPrice: 100000,
        stopLossPrice: 99000,
        takeProfitPrice: 102000,
        quantity: 0.01,
        leverage: 10,
      });

      deltaSyncMocks.mockGetPositions.mockReturnValue([
        { product_symbol: 'BTCUSD.P', side: 'buy', size: 0.01, entry_price: '100000', mark_price: '100000' },
      ]);
      
      deltaSyncMocks.mockGetMarkPrice.mockResolvedValue(102000);
      deltaSyncMocks.mockClosePosition.mockResolvedValue({ success: false, error: 'Network error' });
      prismaMocks.mockTradeLedgerUpdate.mockResolvedValue({});
      walletMocks.mockApplyTradeResult.mockResolvedValue({});
      canonicalOBRegistryMocks.mockMarkTraded.mockResolvedValue(undefined);

      PositionMonitorService.start();
      await jest.advanceTimersByTimeAsync(5000);

      // Position should be re-added to monitoring
      const monitored = PositionMonitorService.getMonitoredPositions();
      expect(monitored.length).toBe(1);
      expect(monitored[0].tradeId).toBe('TEST-TRADE-1');
    });
  });

  describe('Accounting Calculation', () => {
    test('Calculates correct gross PnL, fees, tax, net PnL for Long TP hit', () => {
      const result = TradeAccountingService.calculateAccounting({
        tradeId: 'TEST-1',
        symbol: 'BTCUSD.P',
        side: 'LONG',
        entryPrice: 100000,
        exitPrice: 102000,
        quantity: 0.01,
        leverage: 10,
        isEntryMaker: false,
        isExitMaker: false,
        stopLoss: 99000,
        takeProfit: 102000,
      });

      // Gross PnL = (exit - entry) * qty = (102000 - 100000) * 0.01 = 20
      expect(result.grossPnL).toBe(20);
      
      // Net PnL should be positive for TP hit
      expect(result.netPnL).toBeGreaterThan(0);
      expect(result.resultStatus).toBe('WIN');
    });

    test('Calculates correct gross PnL, fees, tax, net PnL for Long SL hit', () => {
      const result = TradeAccountingService.calculateAccounting({
        tradeId: 'TEST-2',
        symbol: 'BTCUSD.P',
        side: 'LONG',
        entryPrice: 100000,
        exitPrice: 99000,
        quantity: 0.01,
        leverage: 10,
        isEntryMaker: false,
        isExitMaker: false,
        stopLoss: 99000,
        takeProfit: 102000,
      });

      // Gross PnL = (exit - entry) * qty = (99000 - 100000) * 0.01 = -10
      expect(result.grossPnL).toBe(-10);
      
      // Net PnL should be negative for SL hit
      expect(result.netPnL).toBeLessThan(0);
      expect(result.resultStatus).toBe('LOSS');
    });

    test('Calculates correct gross PnL for Short TP hit', () => {
      const result = TradeAccountingService.calculateAccounting({
        tradeId: 'TEST-3',
        symbol: 'BTCUSD.P',
        side: 'SHORT',
        entryPrice: 100000,
        exitPrice: 98000,
        quantity: 0.01,
        leverage: 10,
        isEntryMaker: false,
        isExitMaker: false,
        stopLoss: 101000,
        takeProfit: 98000,
      });

      // Gross PnL = (entry - exit) * qty = (100000 - 98000) * 0.01 = 20
      expect(result.grossPnL).toBe(20);
      expect(result.netPnL).toBeGreaterThan(0);
      expect(result.resultStatus).toBe('WIN');
    });

    test('Calculates correct gross PnL for Short SL hit', () => {
      const result = TradeAccountingService.calculateAccounting({
        tradeId: 'TEST-4',
        symbol: 'BTCUSD.P',
        side: 'SHORT',
        entryPrice: 100000,
        exitPrice: 101000,
        quantity: 0.01,
        leverage: 10,
        isEntryMaker: false,
        isExitMaker: false,
        stopLoss: 101000,
        takeProfit: 98000,
      });

      // Gross PnL = (entry - exit) * qty = (100000 - 101000) * 0.01 = -10
      expect(result.grossPnL).toBe(-10);
      expect(result.netPnL).toBeLessThan(0);
      expect(result.resultStatus).toBe('LOSS');
    });
  });
});