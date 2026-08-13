import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { eventBus } from '../../../services/EventBus.js';
import type { NewsFilterEventDto, NewsFilterStatusDto } from '@algoapp/shared';

const BLOCK_BEFORE_MINUTES = 30;
const BLOCK_AFTER_MINUTES = 60;

const BLOCKING_CATEGORIES = new Set([
  'CPI', 'PPI', 'NFP', 'FOMC', 'INTEREST_RATE', 'SEC', 'ETF', 'REGULATORY', 'RATES'
]);

export class NewsFilterEngine {
  private static isInitialized = false;
  private static initializationPromise: Promise<void> | null = null;

  static async initialize() {
    if (this.isInitialized) return;
    if (this.initializationPromise) return this.initializationPromise;
    
    this.initializationPromise = this.loadFromDatabase();
    await this.initializationPromise;
    this.isInitialized = true;
  }

  private static async loadFromDatabase() {
    try {
      const now = new Date();
      const cutoff = new Date(now.getTime() - 24 * 60 * 60 * 1000);
      
      const dbEvents = await prisma.newsFilterEvent.findMany({
        where: {
          publishedAt: { gte: cutoff },
          blockEnd: { gte: now }, // Only load events that could still be blocking
        },
        orderBy: { publishedAt: 'desc' },
        take: 200,
      });

      for (const e of dbEvents) {
        this.addEventToMemory({
          id: e.id,
          eventId: e.eventId,
          title: e.title,
          category: e.category,
          impactLevel: e.impactLevel as any,
          isBlocking: e.isBlocking,
          publishedAt: e.publishedAt.toISOString(),
          blockStart: e.blockStart.toISOString(),
          blockEnd: e.blockEnd.toISOString(),
          source: e.source,
          sourceUrl: e.sourceUrl ?? undefined,
        });
      }
      
      logger.info(`[NewsFilterEngine] Loaded ${dbEvents.length} events from database`);
    } catch (err) {
      logger.warn(`[NewsFilterEngine] Failed to load from database: ${(err as Error).message}`);
    }
  }

  private static recentEvents: NewsFilterEventDto[] = [];

  private static addEventToMemory(event: NewsFilterEventDto) {
    this.recentEvents.push(event);
    this.recentEvents.sort((a, b) => new Date(b.publishedAt).getTime() - new Date(a.publishedAt).getTime());
    
    // Keep only last 200 events
    if (this.recentEvents.length > 200) {
      this.recentEvents = this.recentEvents.slice(0, 200);
    }
  }

  /**
   * Check if new entries should be blocked right now.
   * Returns true if any high-impact event is within its blocking window.
   */
  public static isBlocking(): boolean {
    this.cleanupOldEvents();
    
    const now = new Date();
    
    for (const event of this.recentEvents) {
      if (!event.isBlocking) continue;
      
      const blockStart = new Date(event.blockStart);
      const blockEnd = new Date(event.blockEnd);
      
      if (now >= blockStart && now <= blockEnd) {
        logger.info(
          { event: event.title, category: event.category, until: blockEnd },
          'News filter BLOCKING new entries'
        );
        return true;
      }
    }
    
    return false;
  }

  /**
   * Get current blocking status with details.
   */
  public static getStatus(): NewsFilterStatusDto {
    this.cleanupOldEvents();
    
    const now = new Date();
    const activeEvents = this.recentEvents.filter(e => {
      if (!e.isBlocking) return false;
      const blockStart = new Date(e.blockStart);
      const blockEnd = new Date(e.blockEnd);
      return now >= blockStart && now <= blockEnd;
    });

    // Find next upcoming blocking event
    const upcoming = this.recentEvents
      .filter(e => e.isBlocking && new Date(e.publishedAt) > now)
      .sort((a, b) => new Date(a.publishedAt).getTime() - new Date(b.publishedAt).getTime())[0];

    return {
      isBlocking: activeEvents.length > 0,
      activeEvents,
      nextBlockingEvent: upcoming ?? undefined,
      newsProviderStatus: [], // Will be populated by API
      economicCalendarProviderStatus: { provider: 'unknown', connected: false, lastFetch: null, articlesFetched: 0, error: undefined },
    };
  }

  /**
   * Add a news/economic event to the filter engine.
   * Called by NewsService and EconomicCalendarService when new events arrive.
   */
  public static addEvent(event: {
    eventId: string;
    title: string;
    category: string;
    impactLevel: 'HIGH' | 'MEDIUM' | 'LOW';
    publishedAt: string; // ISO
    source: string;
    sourceUrl?: string;
    scheduledAt?: string; // For economic events
  }): void {
    const isBlocking = BLOCKING_CATEGORIES.has(event.category) && event.impactLevel === 'HIGH';
    
    let blockStart: Date;
    let blockEnd: Date;
    const publishedAt = new Date(event.publishedAt);
    
    if (event.scheduledAt) {
      // Economic event: block around scheduled time
      const scheduled = new Date(event.scheduledAt);
      blockStart = new Date(scheduled.getTime() - BLOCK_BEFORE_MINUTES * 60000);
      blockEnd = new Date(scheduled.getTime() + BLOCK_AFTER_MINUTES * 60000);
    } else {
      // News event: block around publication time
      blockStart = new Date(publishedAt.getTime() - BLOCK_BEFORE_MINUTES * 60000);
      blockEnd = new Date(publishedAt.getTime() + BLOCK_AFTER_MINUTES * 60000);
    }

    const filterEvent: NewsFilterEventDto = {
      id: `filter-${event.eventId}`,
      eventId: event.eventId,
      title: event.title,
      category: event.category,
      impactLevel: event.impactLevel,
      isBlocking,
      publishedAt: publishedAt.toISOString(),
      blockStart: blockStart.toISOString(),
      blockEnd: blockEnd.toISOString(),
      source: event.source,
      sourceUrl: event.sourceUrl ?? undefined,
    };

    this.addEventToMemory(filterEvent);

    // Persist to database
    this.persistEvent(filterEvent).catch(err => 
      logger.warn(`[NewsFilterEngine] Failed to persist event: ${err.message}`)
    );

    if (isBlocking) {
      eventBus.emit('news:blocking_event', filterEvent);
    }

    logger.info(
      { event: event.title, category: event.category, blocking: isBlocking, blockStart, blockEnd },
      'News filter event added'
    );
  }

  private static async persistEvent(event: NewsFilterEventDto) {
    try {
      await prisma.newsFilterEvent.upsert({
        where: { eventId: event.eventId },
        create: {
          eventId: event.eventId,
          title: event.title,
          category: event.category,
          impactLevel: event.impactLevel,
          isBlocking: event.isBlocking,
          publishedAt: new Date(event.publishedAt),
          blockStart: new Date(event.blockStart),
          blockEnd: new Date(event.blockEnd),
          source: event.source,
          sourceUrl: event.sourceUrl || null,
          metadataJson: JSON.stringify(event),
        },
        update: {
          isBlocking: event.isBlocking,
          blockStart: new Date(event.blockStart),
          blockEnd: new Date(event.blockEnd),
        },
      });
    } catch (err) {
      logger.warn(`[NewsFilterEngine] Persist error: ${(err as Error).message}`);
    }
  }

  private static cleanupOldEvents(): void {
    const cutoff = new Date(Date.now() - 24 * 60 * 60 * 1000);
    this.recentEvents = this.recentEvents.filter(e => new Date(e.publishedAt) > cutoff);
  }

  /**
   * Called when an economic event is released with actual data.
   * Updates the blocking window if needed.
   */
  public static onEventReleased(eventId: string): void {
    const event = this.recentEvents.find(e => e.eventId === eventId);
    if (event) {
      // Keep blocking for BLOCK_AFTER_MINUTES after release
      const newBlockEnd = new Date(Date.now() + BLOCK_AFTER_MINUTES * 60000);
      event.blockEnd = newBlockEnd.toISOString();
      event.isBlocking = true; // Still blocking after release
      
      this.persistEvent(event).catch(err => 
        logger.warn(`[NewsFilterEngine] Failed to update released event: ${err.message}`)
      );
    }
  }
}