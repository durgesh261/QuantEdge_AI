import { LiveTradingSafetyCheckDto, ExecutionMode } from '@algoapp/shared';
import { EmergencyKillSwitch } from '../../execution/adapters/delta/emergencyKillSwitch.js';
import { TradingViewHealthMonitor } from '../../tradingview-adapter/services/tradingViewHealthMonitor.js';

let isExplicitUserConfirmed = false;
let isLiveModeActive = false;

export class LiveTradingGuard {
  public static setExplicitUserConfirmed(confirmed: boolean): void {
    isExplicitUserConfirmed = confirmed;
  }

  public static setLiveModeActive(active: boolean): void {
    isLiveModeActive = active;
  }

  public static async evaluateSafety(requestedMode: ExecutionMode = ExecutionMode.PAPER): Promise<LiveTradingSafetyCheckDto> {
    const tvHealth = await TradingViewHealthMonitor.getHealth();
    const isKillSwitchActive = EmergencyKillSwitch.isKillSwitchActive();

    const env = process.env.NODE_ENV || 'development';
    const hasDeltaKeys = Boolean(process.env.DELTA_API_KEY && process.env.DELTA_API_SECRET);
    const isAllowLiveTradingEnvSet = process.env.ALLOW_LIVE_TRADING === 'true';

    const checks = {
      explicitUserConfirmed: isExplicitUserConfirmed,
      validEnvironment: env === 'production' || env === 'sandbox' || env === 'development',
      productionApiKeysPresent: hasDeltaKeys || env !== 'production',
      allowLiveTradingEnvSet: requestedMode === ExecutionMode.LIVE ? isAllowLiveTradingEnvSet : true,
      killSwitchInactive: !isKillSwitchActive,
      challengeGuardEnabled: true,
      liveModeActive: requestedMode === ExecutionMode.LIVE ? isLiveModeActive : true,
      deltaConnectionHealthy: true,
      tradingViewConnectionHealthy: tvHealth.status !== 'DISCONNECTED',
    };

    const rejectionReasons: string[] = [];
    if (!checks.explicitUserConfirmed && requestedMode === ExecutionMode.LIVE) {
      rejectionReasons.push('Explicit user confirmation is missing for Live Trading.');
    }
    if (!checks.allowLiveTradingEnvSet && requestedMode === ExecutionMode.LIVE) {
      rejectionReasons.push('ALLOW_LIVE_TRADING environment variable is not set to true.');
    }
    if (!checks.killSwitchInactive) {
      rejectionReasons.push('Platform Emergency Kill Switch is ACTIVE.');
    }
    if (!checks.liveModeActive && requestedMode === ExecutionMode.LIVE) {
      rejectionReasons.push('Live Mode has not been activated by user.');
    }
    if (!checks.tradingViewConnectionHealthy) {
      rejectionReasons.push('TradingView webhook data connection is DISCONNECTED.');
    }

    const isAllowed = requestedMode !== ExecutionMode.LIVE || (Object.values(checks).every(Boolean) && rejectionReasons.length === 0);

    return {
      isAllowed,
      checks,
      rejectionReasons,
      timestamp: new Date().toISOString(),
    };
  }
}
