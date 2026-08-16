import { prisma } from '../../../db.js';
import { NotificationSeverity } from '@algoapp/shared';
import { NotificationCenterService } from './notificationCenter.service.js';
import { NotificationProviderManager } from './notificationProvider.js';

/**
 * Status of an incident in the persistent history.
 */
export enum IncidentStatus {
  OPEN = 'OPEN',
  RESOLVED = 'RESOLVED',
}

/**
 * Delivery status of an incident external delivery attempt.
 */
export enum IncidentDeliveryStatus {
  LOCAL_ONLY = 'LOCAL_ONLY',
  PENDING = 'PENDING',
  SENT = 'SENT',
  FAILED = 'FAILED',
}

/**
 * Incident types that should be tracked persistently.
 * These correspond to the required incident types from C.17 §9.
 */
export enum IncidentType {
  SCANNER_HEARTBEAT_STALE = 'SCANNER_HEARTBEAT_STALE',
  MARKET_DATA_STALE = 'MARKET_DATA_STALE',
  DELTA_REST_DISCONNECTED = 'DELTA_REST_DISCONNECTED',
  DELTA_WS_DISCONNECTED = 'DELTA_WS_DISCONNECTED',
  PRODUCT_METADATA_STALE = 'PRODUCT_METADATA_STALE',
  PRODUCT_METADATA_REFRESH_FAILED = 'PRODUCT_METADATA_REFRESH_FAILED',
  LIVE_AUTHORIZATION_REJECTED = 'LIVE_AUTHORIZATION_REJECTED',
  KILL_SWITCH_ACTIVATED = 'KILL_SWITCH_ACTIVATED',
  RISK_LIMIT_BREACHED = 'RISK_LIMIT_BREACHED',
  ORDER_VALIDATION_REJECTED = 'ORDER_VALIDATION_REJECTED',
  PROTECTIVE_CLOSE_BLOCKED = 'PROTECTIVE_CLOSE_BLOCKED',
  DATABASE_UNAVAILABLE = 'DATABASE_UNAVAILABLE',
  UNHANDLED_EXCEPTION = 'UNHANDLED_EXCEPTION',
  UNCAUGHT_EXCEPTION = 'UNCAUGHT_EXCEPTION',
  GRACEFUL_SHUTDOWN = 'GRACEFUL_SHUTDOWN',
  STARTUP_FAILURE = 'STARTUP_FAILURE',
}

/**
 * Service for managing persistent incident history via Prisma IncidentHistory model.
 * Responsibilities:
 * - Create incidents when significant operational events occur
 * - Mark incidents as resolved on recovery
 * - Prevent duplicate OPEN incidents for the same event type+symbol
 * - Provide lookup by event type and symbol
 * - All operations are observational only; never affect executionMode, LIVE state, or order submission
 */
export class IncidentHistoryService {
  private static incidentLockMap: Map<string, NodeJS.Timeout> = new Map();

  /**
   * Create a new incident in the persistent history.
   * If an incident with the same eventType+symbol is already OPEN,
   * the new request is suppressed (no duplicate OPEN incident).
   *
   * The local notification via NotificationCenterService is always sent.
   * External delivery via NotificationProviderManager is best-effort.
   */
  public static async createIncident(
    eventType: IncidentType,
    severity: NotificationSeverity,
    message: string,
    symbol: string | null,
    providerManager?: NotificationProviderManager
  ): Promise<{ incidentId: string; alreadyExists: boolean }> {
    // 1. Check for existing OPEN incident with same eventType+symbol
    const where: any = {
      eventType: eventType,
      status: IncidentStatus.OPEN,
    };
    if (symbol) {
      where.symbol = symbol;
    }

    const existingOpen = await prisma.incidentHistory.findFirst({
      where,
    });

    // Prevent rapid duplicate creation attempts
    IncidentHistoryService.preventDuplicateCreation(eventType, symbol);

    if (existingOpen) {
      // Already have an OPEN incident for this event+symbol; suppress duplicate
      // Still send a local notification so the operator sees it
      await NotificationCenterService.notify(
        eventType,
        'Incident Already Active: ' + eventType,
        message,
        severity
      );
      return { incidentId: existingOpen.id, alreadyExists: true };
    }

    // 2. Create the new incident
    const incident = await prisma.incidentHistory.create({
      data: {
        eventType: eventType,
        severity: severity,
        message: message,
        symbol: symbol,
        executionMode: 'PAPER',
        status: IncidentStatus.OPEN,
        deliveryStatus: IncidentDeliveryStatus.LOCAL_ONLY,
      },
    });

    // 3. Send local notification
    await NotificationCenterService.notify(
      eventType,
      'Incident: ' + eventType,
      message,
      severity
    );

    // 4. Attempt external delivery if provider manager is configured
    if (providerManager) {
      void providerManager.deliver({
        id: 'NOTIF-' + Date.now(),
        type: eventType,
        title: 'Incident: ' + eventType,
        message: message,
        severity: severity,
        read: false,
        timestamp: new Date().toISOString(),
      });
    }

    return { incidentId: incident.id, alreadyExists: false };
  }

  /**
   * Mark an existing incident as resolved.
   * Only one OPEN incident per (eventType, symbol) pair is allowed.
   * Sets resolvedAt and status to RESOLVED.
   *
   * After resolution, a new incident of the same type can be created.
   */
  public static async resolveIncident(
    eventType: IncidentType,
    symbol?: string | null
  ): Promise<boolean> {
    // Find the OPEN incident for this eventType+symbol
    const where: any = {
      eventType: eventType,
      status: IncidentStatus.OPEN,
    };
    if (symbol) {
      where.symbol = symbol;
    }

    const existingOpen = await prisma.incidentHistory.findFirst({
      where,
    });

    if (!existingOpen) {
      // No OPEN incident found; nothing to resolve
      return false;
    }

    // Mark as resolved
    await prisma.incidentHistory.update({
      where: { id: existingOpen.id },
      data: {
        status: IncidentStatus.RESOLVED,
        resolvedAt: new Date(),
      },
    });

    // Send recovery notification
    await NotificationCenterService.notify(
      eventType,
      'Recovery: ' + eventType,
      'Incident resolved: ' + (existingOpen.message || 'Operational issue resolved'),
      existingOpen.severity as NotificationSeverity
    );

    return true;
  }

  /**
   * Check if there is an OPEN incident for the given event type and symbol.
   */
  public static async isIncidentOpen(
    eventType: IncidentType,
    symbol?: string | null
  ): Promise<boolean> {
    const where: any = {
      eventType: eventType,
      status: IncidentStatus.OPEN,
    };
    if (symbol) {
      where.symbol = symbol;
    }

    const existing = await prisma.incidentHistory.findFirst({
      where,
    });
    return !!existing;
  }

  /**
   * Get all OPEN incidents, optionally filtered by event type.
   */
  public static async getOpenIncidents(
    eventType?: IncidentType
  ): Promise<{ id: string; eventType: IncidentType; severity: NotificationSeverity; message: string; symbol: string | null; createdAt: Date; }[]> {
    const where: any = eventType
      ? { eventType: eventType, status: IncidentStatus.OPEN }
      : { status: IncidentStatus.OPEN };

    const incidents = await prisma.incidentHistory.findMany({
      where,
      orderBy: { createdAt: 'desc' },
    });

    return incidents.map((i) => ({
      id: i.id,
      eventType: i.eventType as IncidentType,
      severity: i.severity as NotificationSeverity,
      message: i.message,
      symbol: i.symbol,
      createdAt: i.createdAt,
    }));
  }

  /**
   * Prevent rapid re-creation of the same OPEN incident.
   * Uses a per-event lock to suppress duplicate creation attempts
   * within a short window (prevents incident spam from rapid event loops).
   */
  private static preventDuplicateCreation(
    eventType: IncidentType,
    symbol?: string | null
  ): boolean {
    const dedupKey = eventType + '|' + (symbol || 'unknown');
    const existingTimeout = IncidentHistoryService.incidentLockMap.get(dedupKey);

    if (existingTimeout) {
      // Already have a lock; suppress this creation attempt.
      return true;
    }

    // Set a short-lived lock (2 seconds) to suppress rapid duplicates
    const timeout = setTimeout(() => {
      IncidentHistoryService.incidentLockMap.delete(dedupKey);
    }, 2000);
    IncidentHistoryService.incidentLockMap.set(dedupKey, timeout);

    return false;
  }
}