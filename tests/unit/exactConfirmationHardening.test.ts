import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { setExecutionMode } from '../../backend/src/modules/production/production.controller.js';
import { LiveTradingGuard } from '../../backend/src/modules/production/services/liveTradingGuard.js';
import { EmergencyKillSwitch } from '../../backend/src/modules/execution/adapters/delta/emergencyKillSwitch.js';
import { ExecutionMode } from '@algoapp/shared';

describe('Phase C.4.1: Exact Confirmation Phrase & Safety Guard Hardening Tests', () => {
  const originalEnv = { ...process.env };

  const createMockReqRes = (body: any) => {
    const req = { body, correlationId: 'test-req' } as any;
    let responseData: any = null;
    let statusCode: number = 200;

    const res = {
      status: (code: number) => {
        statusCode = code;
        return res;
      },
      json: (data: any) => {
        responseData = data;
        return res;
      },
    } as any;

    return { req, res, getResponse: () => ({ statusCode, responseData }) };
  };

  beforeEach(() => {
    process.env.NODE_ENV = 'development';
    process.env.DELTA_API_KEY = 'mock_key';
    process.env.DELTA_API_SECRET = 'mock_secret';
    process.env.ALLOW_LIVE_TRADING = 'true';

    LiveTradingGuard.setLiveModeActive(false);
    LiveTradingGuard.setExplicitUserConfirmed(false);
    EmergencyKillSwitch.setKillSwitch(false);
  });

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  describe('1. Controller Confirmation Phrase Hardening', () => {
    it('1. rejects when userConfirmed is missing', async () => {
      const { req, res, getResponse } = createMockReqRes({ mode: ExecutionMode.LIVE });
      await setExecutionMode(req, res);
      const { statusCode, responseData } = getResponse();

      expect(statusCode).toBe(400);
      expect(responseData.success).toBe(false);
      expect(responseData.error).toContain('CONFIRM_LIVE_TRADING');
    });

    it('2. rejects when userConfirmed is boolean false', async () => {
      const { req, res, getResponse } = createMockReqRes({ mode: ExecutionMode.LIVE, userConfirmed: false });
      await setExecutionMode(req, res);
      const { statusCode, responseData } = getResponse();

      expect(statusCode).toBe(400);
      expect(responseData.success).toBe(false);
      expect(responseData.error).toContain('CONFIRM_LIVE_TRADING');
    });

    it('3. rejects when userConfirmed is boolean true (bypassing exact phrase)', async () => {
      const { req, res, getResponse } = createMockReqRes({ mode: ExecutionMode.LIVE, userConfirmed: true });
      await setExecutionMode(req, res);
      const { statusCode, responseData } = getResponse();

      expect(statusCode).toBe(400);
      expect(responseData.success).toBe(false);
      expect(responseData.error).toContain('CONFIRM_LIVE_TRADING');
    });

    it('4. rejects when userConfirmed is arbitrary string "yes"', async () => {
      const { req, res, getResponse } = createMockReqRes({ mode: ExecutionMode.LIVE, userConfirmed: 'yes' });
      await setExecutionMode(req, res);
      const { statusCode, responseData } = getResponse();

      expect(statusCode).toBe(400);
      expect(responseData.success).toBe(false);
      expect(responseData.error).toContain('CONFIRM_LIVE_TRADING');
    });

    it('5. rejects when userConfirmed is partial string "CONFIRM_LIVE"', async () => {
      const { req, res, getResponse } = createMockReqRes({ mode: ExecutionMode.LIVE, userConfirmed: 'CONFIRM_LIVE' });
      await setExecutionMode(req, res);
      const { statusCode, responseData } = getResponse();

      expect(statusCode).toBe(400);
      expect(responseData.success).toBe(false);
      expect(responseData.error).toContain('CONFIRM_LIVE_TRADING');
    });

    it('6. succeeds only when userConfirmed === "CONFIRM_LIVE_TRADING"', async () => {
      const { req, res, getResponse } = createMockReqRes({ mode: ExecutionMode.LIVE, userConfirmed: 'CONFIRM_LIVE_TRADING' });
      await setExecutionMode(req, res);
      const { statusCode, responseData } = getResponse();

      expect(statusCode).toBe(200);
      expect(responseData.success).toBe(true);
      expect(responseData.data.activeExecutionMode).toBe(ExecutionMode.LIVE);
      expect(responseData.data.userConfirmed).toBe(true);
    });
  });

  describe('2. LiveTradingGuard Environment & Safety Evaluator Hardening', () => {
    it('7. rejects when LIVE mode is disabled regardless of confirmation', async () => {
      LiveTradingGuard.setExplicitUserConfirmed(true);
      LiveTradingGuard.setLiveModeActive(false);

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.isAllowed).toBe(false);
      expect(safety.rejectionReasons).toContain('Live Mode has not been activated by user.');
    });

    it('8. rejects when ALLOW_LIVE_TRADING environment variable is not "true"', async () => {
      delete process.env.ALLOW_LIVE_TRADING; // Or 'false'
      LiveTradingGuard.setExplicitUserConfirmed(true);
      LiveTradingGuard.setLiveModeActive(true);

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.isAllowed).toBe(false);
      expect(safety.rejectionReasons).toContain('ALLOW_LIVE_TRADING environment variable is not set to true.');
    });

    it('9. rejects when Emergency Kill Switch is active', async () => {
      EmergencyKillSwitch.setKillSwitch(true);
      LiveTradingGuard.setExplicitUserConfirmed(true);
      LiveTradingGuard.setLiveModeActive(true);

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.isAllowed).toBe(false);
      expect(safety.rejectionReasons).toContain('Platform Emergency Kill Switch is ACTIVE.');
    });

    it('10. permits LIVE mode ONLY when valid confirmation + ALLOW_LIVE_TRADING=true + kill switch inactive', async () => {
      process.env.ALLOW_LIVE_TRADING = 'true';
      LiveTradingGuard.setExplicitUserConfirmed(true);
      LiveTradingGuard.setLiveModeActive(true);
      EmergencyKillSwitch.setKillSwitch(false);

      const safety = await LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE);
      expect(safety.isAllowed).toBe(true);
      expect(safety.rejectionReasons.length).toBe(0);
    });
  });
});
