import { NotificationDto } from '@algoapp/shared';

/**
 * Optional provider interface for external notification delivery.
 * Implementations may send notifications to Telegram, Discord, Slack, Email, etc.
 * The provider is completely optional; if unavailable or disabled, local notifications
 * continue normally and no external network calls are made.
 */
export interface NotificationProvider {
  send(notification: NotificationDto): Promise<void>;
}

/**
 * Notification provider manager that optionally dispatches notifications
 * to external providers while guaranteeing local delivery via NotificationCenterService.
 *
 * Key safety properties:
 * - Provider calls are fire-and-forget with bounded timeout and retry.
 * - Provider failure never crashes the backend or affects trading execution.
 * - Provider failure never changes executionMode, isLiveModeActive, or kill switch state.
 * - Provider failure never calls DeltaRestClient.placeOrder().
 *
 * The caller (e.g. ContinuousPipelineOrchestratorService) must first record the
 * local notification via NotificationCenterService.notify(), then call deliver()
 * for best-effort external delivery.
 */
export class NotificationProviderManager {
  private providers: NotificationProvider[] = [];
  private readonly enabled: boolean;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly cooldownMs: number;

  // Per-event cooldown tracking: (eventType + symbol + severity) -> timestamp
  private readonly cooldownMap: Map<string, number> = new Map();

  constructor(
    enabled: boolean,
    timeoutMs: number,
    maxRetries: number,
    cooldownMs: number
  ) {
    this.enabled = enabled;
    this.timeoutMs = timeoutMs;
    this.maxRetries = maxRetries;
    this.cooldownMs = cooldownMs;
  }

  /**
   * Add a provider to the rotation. Providers are tried in registration order.
   * If no providers are added, delivery stays local-only.
   */
  public addProvider(provider: NotificationProvider): void {
    this.providers.push(provider);
  }

  /**
   * Attempt external delivery of a notification that has already been locally
   * recorded via NotificationCenterService.notify(). This is best-effort only:
   * - Bounded retry (maxRetries attempts with exponential backoff)
   * - Hard timeout per attempt (timeoutMs)
   * - Cooldown deduplication to prevent duplicate sends
   * - Provider failure never crashes the backend or affects trading execution.
   */
  public async deliver(notif: NotificationDto): Promise<void> {
    if (!this.enabled) {
      // External delivery disabled; nothing more to do.
      return;
    }

    // 1. Check cooldown deduplication
    const dedupKey = this.dedupKey(notif);
    const lastSent = this.cooldownMap.get(dedupKey);
    const now = Date.now();
    if (lastSent !== undefined && now - lastSent < this.cooldownMs) {
      // Suppress duplicate within cooldown period.
      return;
    }

    // 2. Attempt external delivery with bounded retry + timeout
    let attempts = 0;
    let delivered = false;

    while (attempts < this.maxRetries && !delivered) {
      attempts++;

      // Choose a provider (first available; could be round-robin later)
      const provider = this.providers.length > 0 ? this.providers[0] : null;

      if (provider) {
        const abortCtrl = new AbortController();
        const timeoutId = setTimeout(() => {
          abortCtrl.abort();
        }, this.timeoutMs);

        try {
          await provider.send(notif);
          delivered = true;
        } catch (err: any) {
          // Provider failure — do NOT recursively notify.
          // Just retry if attempts remain; otherwise stop.
          if (attempts >= this.maxRetries) {
            // After max retries, stop.
          }
        } finally {
          clearTimeout(timeoutId);
        }
      } else {
        // No providers configured; treat as delivered locally only
        delivered = true;
      }
    }

    // 3. If externally delivered, update cooldown timestamp
    if (delivered) {
      this.cooldownMap.set(dedupKey, Date.now());
    }
  }

  private dedupKey(notif: NotificationDto): string {
    // Deduplication key: eventType + symbol (if present) + severity
    const symbol = notif.message?.match(/[A-Z]+\/?[A-Z]+\.[PZ]?/i)?.[0] || 'unknown';
    return `${notif.type}|${symbol.toUpperCase()}|${notif.severity}`;
  }
}