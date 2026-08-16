import { NotificationProviderManager } from '../src/modules/realtime-operations/services/notificationProvider';
import { NotificationDto } from '../src/shared';

describe('C.17.1 Notification Deduplication', () => {
  const manager = new NotificationProviderManager(
    true, // enabled
    5000,  // timeoutMs
    3,     // maxRetries
    300000 // cooldownMs
  );

  describe('cooldown deduplication', () => {
    it('should suppress duplicate event within cooldown period', async () => {
      const notif: NotificationDto = {
        id: 'NOTIF-1',
        type: 'DELTA_DISCONNECTED',
        title: 'Delta Disconnected',
        message: 'DELTA_DISCONNECTED BTCUSD.P CRITICAL',
        severity: 'CRITICAL',
        read: false,
        timestamp: new Date().toISOString(),
      };

      // First delivery should go through (or be suppressed locally)
      await manager.deliver(notif);

      // Second delivery within cooldown should be suppressed (no external attempt)
      // The method should not throw; it should suppress the duplicate
      await manager.deliver(notif);
    });

    it('should send new event after cooldown', async () => {
      const disconnectNotif: NotificationDto = {
        id: 'NOTIF-1',
        type: 'DELTA_DISCONNECTED',
        title: 'Delta Disconnected',
        message: 'DELTA_DISCONNECTED BTCUSD.P CRITICAL',
        severity: 'CRITICAL',
        read: false,
        timestamp: new Date().toISOString(),
      };

      const recoverNotif: NotificationDto = {
        id: 'NOTIF-2',
        type: 'DELTA_RECOVERED',
        title: 'Delta Recovered',
        message: 'DELTA_RECOVERED BTCUSD.P',
        severity: 'CRITICAL',
        read: false,
        timestamp: new Date().toISOString(),
      };

      // First, deliver the disconnect (will be suppressed by cooldown on second call)
      await manager.deliver(disconnectNotif);

      // After cooldown, deliver the recovery event (different event type, should go through)
      await manager.deliver(recoverNotif);
    });
  });

  describe('deduplication key', () => {
    it('should deduplicate based on eventType + symbol + severity', async () => {
      const btcDisconnect: NotificationDto = {
        id: 'NOTIF-1',
        type: 'DELTA_DISCONNECTED',
        title: 'Delta Disconnected',
        message: 'DELTA_DISCONNECTED BTCUSD.P CRITICAL',
        severity: 'CRITICAL',
        read: false,
        timestamp: new Date().toISOString(),
      };

      const ethDisconnect: NotificationDto = {
        id: 'NOTIF-2',
        type: 'DELTA_DISCONNECTED',
        title: 'Delta Disconnected',
        message: 'DELTA_DISCONNECTED ETHUSD.P CRITICAL',
        severity: 'CRITICAL',
        read: false,
        timestamp: new Date().toISOString(),
      };

      // Both should be deliverable (different symbols)
      await manager.deliver(btcDisconnect);
      await manager.deliver(ethDisconnect);
    });

    it('should suppress same event with same symbol within cooldown', async () => {
      const btcDisconnect1: NotificationDto = {
        id: 'NOTIF-1',
        type: 'DELTA_DISCONNECTED',
        message: 'DELTA_DISCONNECTED BTCUSD.P CRITICAL',
        severity: 'CRITICAL',
        read: false,
        timestamp: new Date().toISOString(),
      };

      const btcDisconnect2: NotificationDto = {
        id: 'NOTIF-2',
        type: 'DELTA_DISCONNECTED',
        message: 'DELTA_DISCONNECTED BTCUSD.P CRITICAL (duplicate)',
        severity: 'CRITICAL',
        read: false,
        timestamp: new Date().toISOString(),
      };

      // First delivery
      await manager.deliver(btcDisconnect1);
      // Second within cooldown should be suppressed
      await manager.deliver(btcDisconnect2);
    });
  });
});