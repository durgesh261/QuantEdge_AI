import { NotificationDto, NotificationSeverity } from '@algoapp/shared';
import { NotificationProviderManager } from './notificationProvider.js';
import { AppEventBus } from './appEventBus.service.js';

let notificationsStore: NotificationDto[] = [
  {
    id: 'NOTIF-1',
    type: 'SYSTEM_STARTUP',
    title: 'Delta Exchange Event Bus Online',
    message: 'AlgoApp Terminal continuous live trading event bus active.',
    severity: 'SUCCESS',
    read: false,
    timestamp: new Date().toISOString(),
  },
];

export class NotificationCenterService {
  private static providerManager: NotificationProviderManager | null = null;

  public static configureProviderManager(
    manager: NotificationProviderManager
  ): void {
    NotificationCenterService.providerManager = manager;
  }

  public static async notify(
    type: string,
    title: string,
    message: string,
    severity: NotificationSeverity = 'INFO'
  ): Promise<NotificationDto> {
    const notif: NotificationDto = {
      id: `NOTIF-${Date.now()}`,
      type,
      title,
      message,
      severity,
      read: false,
      timestamp: new Date().toISOString(),
    };

    notificationsStore.unshift(notif);
    if (notificationsStore.length > 200) {
      notificationsStore = notificationsStore.slice(0, 200);
    }

    // Always publish the event on the bus
    AppEventBus.publish('NOTIFICATION_GENERATED', notif);

    // If a provider manager is configured, attempt external delivery
    // (local notification already stored above; external is best-effort)
    if (NotificationCenterService.providerManager) {
      void NotificationCenterService.providerManager.deliver(notif);
    }

    return notif;
  }

  public async getNotifications(severityFilter?: NotificationSeverity): Promise<NotificationDto[]> {
    if (!severityFilter) return notificationsStore;
    return notificationsStore.filter((n) => n.severity === severityFilter);
  }

  public async markAsRead(id: string): Promise<boolean> {
    const target = notificationsStore.find((n) => n.id === id);
    if (target) {
      target.read = true;
      return true;
    }
    return false;
  }

  public async markAllAsRead(): Promise<boolean> {
    notificationsStore.forEach((n) => {
      n.read = true;
    });
    return true;
  }

  public async clearAll(): Promise<boolean> {
    notificationsStore = [];
    return true;
  }

  public static getProviderManager(): NotificationProviderManager | null {
    return NotificationCenterService.providerManager;
  }
}