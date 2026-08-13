import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { eventBus } from '../../../services/EventBus.js';
import type { EconomicEventDto, EconomicCalendarQueryDto, NewsProviderStatus } from '@algoapp/shared';

const TRADING_ECONOMICS_KEY = process.env['TRADING_ECONOMICS_KEY'] || '';
const POLL_INTERVAL_MS = 60 * 60 * 1000; // 1 hour
const UPCOMING_DAYS = 10;
const COMPLETED_RETENTION_HOURS = 24;

const IST_TIMEZONE = 'Asia/Kolkata';

interface TradingEconomicsEvent {
  CalendarId: string;
  Date: string;
  Event: string;
  Country: string;
  Currency: string;
  Category: string;
  Importance: number;
  Forecast?: string;
  Previous?: string;
  Actual?: string;
  Unit?: string;
  Source?: string;
  Url?: string;
}

const MAJOR_COUNTRIES = ['United States', 'Eurozone', 'United Kingdom', 'China', 'Japan', 'Canada', 'Australia', 'India', 'Switzerland', 'Germany', 'France'];
const HIGH_IMPACT_KEYWORDS = [
  'CPI', 'Inflation', 'Core CPI', 'PCE', 'PPI',
  'Non-Farm Payroll', 'NFP', 'Unemployment', 'Jobless Claims',
  'Fed Funds Rate', 'Interest Rate Decision', 'FOMC', 'Federal Reserve',
  'ECB', 'European Central Bank', 'Deposit Rate', 'Refinancing Rate',
  'BOE', 'Bank of England', 'Bank Rate',
  'BOJ', 'Bank of Japan',
  'RBI', 'Reserve Bank of India', 'Repo Rate',
  'GDP', 'Gross Domestic Product',
  'PMI', 'ISM Manufacturing', 'ISM Services',
  'Retail Sales',
];

export class EconomicCalendarService {
  private static isRunning = false;
  private static intervalId: NodeJS.Timeout | null = null;
  private static lastFetchTime = 0;
  private static providerStatus: NewsProviderStatus = {
    provider: 'trading_economics',
    connected: false,
    lastFetch: null,
    articlesFetched: 0,
    error: undefined,
  };

  static start() {
    if (this.isRunning) return;
    this.isRunning = true;
    logger.info('[EconomicCalendarService] Starting economic calendar aggregation...');
    this.fetchAndStoreCalendar();
    this.intervalId = setInterval(() => this.fetchAndStoreCalendar(), POLL_INTERVAL_MS);
    this.cleanupJob();
  }

  static stop() {
    if (this.intervalId) clearInterval(this.intervalId);
    this.isRunning = false;
    logger.info('[EconomicCalendarService] Stopped.');
  }

  static getProviderStatus(): NewsProviderStatus {
    return { ...this.providerStatus };
  }

  private static async fetchAndStoreCalendar() {
    const now = Date.now();
    if (now - this.lastFetchTime < 5 * 60 * 1000) return; // Min 5 min between fetches

    let events: Partial<EconomicEventDto>[] = [];

    // Try Trading Economics API
    if (TRADING_ECONOMICS_KEY) {
      try {
        const from = new Date().toISOString().split('T')[0];
        const to = new Date(Date.now() + UPCOMING_DAYS * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
        const countries = MAJOR_COUNTRIES.join(',');
        
        const url = `https://api.tradingeconomics.com/calendar?c=${TRADING_ECONOMICS_KEY}&country=${countries}&from=${from}&to=${to}&importance=1,2,3&format=json`;
        const res = await fetch(url, { 
          headers: { 'Accept': 'application/json' },
          signal: AbortSignal.timeout(30000),
        });
        
        if (res.ok) {
          const data: TradingEconomicsEvent[] = await res.json();
          events = data.map(e => this.normalizeTradingEconomicsEvent(e));
          this.providerStatus = { 
            provider: 'trading_economics', 
            connected: true, 
            lastFetch: new Date().toISOString(), 
            articlesFetched: events.length,
            error: undefined,
          };
          logger.info(`[EconomicCalendarService] Fetched ${events.length} events from Trading Economics`);
        } else {
          throw new Error(`HTTP ${res.status}`);
        }
      } catch (err) {
        logger.warn(`[EconomicCalendarService] Trading Economics API failed: ${(err as Error).message}`);
        this.providerStatus = { 
          provider: 'trading_economics', 
          connected: false, 
          lastFetch: new Date().toISOString(), 
          articlesFetched: 0,
          error: (err as Error).message 
        };
      }
    }

    // Fallback: ForexFactory free JSON
    if (events.length === 0) {
      try {
        const res = await fetch('https://nfs.faireconomy.media/ff_calendar_thisweek.json', {
          headers: { 'Accept': 'application/json', 'User-Agent': 'QuantEdge-AI/1.0' },
          signal: AbortSignal.timeout(15000),
        });
        if (res.ok) {
          const data = await res.json();
          events = data
            .filter((item: any) => this.isMajorEvent(item.title, item.country))
            .map((item: any) => this.normalizeForexFactoryEvent(item));
          this.providerStatus = { 
            provider: 'forex_factory', 
            connected: true, 
            lastFetch: new Date().toISOString(), 
            articlesFetched: events.length,
            error: undefined,
          };
          logger.info(`[EconomicCalendarService] Fetched ${events.length} events from ForexFactory`);
        }
      } catch (err) {
        logger.warn(`[EconomicCalendarService] ForexFactory failed: ${(err as Error).message}`);
        this.providerStatus = { 
          provider: 'forex_factory', 
          connected: false, 
          lastFetch: new Date().toISOString(), 
          articlesFetched: 0,
          error: (err as Error).message 
        };
      }
    }

    if (events.length === 0) {
      logger.warn('[EconomicCalendarService] All providers failed - no economic events fetched');
      this.providerStatus = { 
        provider: 'none', 
        connected: false, 
        lastFetch: new Date().toISOString(), 
        articlesFetched: 0,
        error: 'All economic calendar providers unavailable' 
      };
      return;
    }

    // Store/Update events
    let newCount = 0, updatedCount = 0, releasedCount = 0;
    
    for (const event of events) {
      if (!event.eventId || !event.scheduledAt) continue;
      
      const scheduledAt = new Date(event.scheduledAt);
      const now = new Date();
      const isUpcoming = scheduledAt > now;
      const status = isUpcoming ? 'UPCOMING' : 'RELEASED';
      
      const existing = await prisma.economicEvent.findUnique({
        where: { eventId: event.eventId },
      });

      if (existing) {
        // Update if new actual value available
        if (event.actualValue && event.actualValue !== existing.actualValue) {
          await prisma.economicEvent.update({
            where: { eventId: event.eventId },
            data: {
              actualValue: event.actualValue,
              status: 'RELEASED',
              releasedAt: new Date(),
              updatedAt: new Date(),
            },
          });
          updatedCount++;
          releasedCount++;
          eventBus.emit('economic:event-released', event);
        }
        // Update forecast/previous if changed
        else if (event.forecastValue && event.forecastValue !== existing.forecastValue) {
          await prisma.economicEvent.update({
            where: { eventId: event.eventId },
            data: {
              forecastValue: event.forecastValue,
              previousValue: event.previousValue ?? existing.previousValue,
              updatedAt: new Date(),
            },
          });
          updatedCount++;
        }
      } else {
        // New event
        await prisma.economicEvent.create({
          data: {
            eventId: event.eventId!,
            title: event.title!,
            country: event.country!,
            currency: event.currency!,
            category: event.category!,
            importance: event.importance!,
            scheduledAt,
            timezone: event.timezone || 'UTC',
            previousValue: event.previousValue || null,
            forecastValue: event.forecastValue || null,
            actualValue: event.actualValue || null,
            unit: event.unit || null,
            status,
            source: event.source || 'unknown',
            sourceUrl: event.sourceUrl || null,
            releasedAt: event.releasedAt ? new Date(event.releasedAt) : null,
            expiresAt: this.calculateExpiry(scheduledAt, status),
          },
        });
        newCount++;
        eventBus.emit('economic:new-event', event);
      }
    }

    // Cleanup
    await this.cleanupOldEvents();

    this.lastFetchTime = Date.now();
    logger.info(`[EconomicCalendarService] Sync complete: ${newCount} new, ${updatedCount} updated, ${releasedCount} released`);
  }

  private static isMajorEvent(title: string, country: string): boolean {
    if (!title) return false;
    const upperTitle = title.toUpperCase();
    const isMajor = HIGH_IMPACT_KEYWORDS.some(k => upperTitle.includes(k.toUpperCase()));
    const isMajorCountry = MAJOR_COUNTRIES.includes(country);
    return isMajor && isMajorCountry;
  }

  private static normalizeTradingEconomicsEvent(item: TradingEconomicsEvent): Partial<EconomicEventDto> {
    const eventDate = new Date(item.Date);
    const category = this.categorizeEvent(item.Event, item.Category);
    const importance = this.mapImportance(item.Importance);
    
    return {
      eventId: `TE-${item.CalendarId}`,
      title: item.Event.slice(0, 200),
      country: item.Country,
      currency: item.Currency,
      category,
      importance,
      scheduledAt: eventDate.toISOString(),
      timezone: 'UTC',
      previousValue: (item.Previous || undefined) as string | undefined,
      forecastValue: (item.Forecast || undefined) as string | undefined,
      actualValue: (item.Actual || undefined) as string | undefined,
      unit: (item.Unit || undefined) as string | undefined,
      status: eventDate > new Date() ? 'UPCOMING' : 'RELEASED',
      source: 'trading_economics',
      sourceUrl: (item.Url || undefined) as string | undefined,
      releasedAt: (item.Actual ? new Date().toISOString() : undefined) as string | undefined,
    };
  }

  private static normalizeForexFactoryEvent(item: any): Partial<EconomicEventDto> {
    const eventDate = new Date(`${item.date} ${item.time}`);
    if (isNaN(eventDate.getTime())) {
      return null as any;
    }
    
    const category = this.categorizeEvent(item.title, '');
    const importance = this.mapForexFactoryImpact(item.impact);
    
    return {
      eventId: `FF-${item.title?.substring(0, 20).replace(/\s/g, '')}-${item.date}`,
      title: item.title?.slice(0, 200) || 'Unknown Event',
      country: item.country || 'Unknown',
      currency: item.currency || '',
      category,
      importance,
      scheduledAt: eventDate.toISOString(),
      timezone: 'UTC',
      previousValue: (item.previous || undefined) as string | undefined,
      forecastValue: (item.forecast || undefined) as string | undefined,
      actualValue: (item.actual || undefined) as string | undefined,
      unit: undefined as string | undefined,
      status: eventDate > new Date() ? 'UPCOMING' : 'RELEASED',
      source: 'forex_factory',
      sourceUrl: undefined as string | undefined,
      releasedAt: (item.actual ? new Date().toISOString() : undefined) as string | undefined,
    };
  }

  private static categorizeEvent(title: string, apiCategory: string): import('@algoapp/shared').EconomicEventCategory {
    const upper = (title + ' ' + apiCategory).toUpperCase();
    if (upper.includes('CPI') || upper.includes('INFLATION') || upper.includes('PCE') || upper.includes('PPI')) return 'INFLATION';
    if (upper.includes('NON-FARM') || upper.includes('NFP') || upper.includes('UNEMPLOYMENT') || upper.includes('JOBLESS')) return 'EMPLOYMENT';
    if (upper.includes('GDP') || upper.includes('GROSS DOMESTIC')) return 'GDP';
    if (upper.includes('FED') || upper.includes('FOMC') || upper.includes('INTEREST RATE') || upper.includes('ECB') || upper.includes('BOE') || upper.includes('BOJ') || upper.includes('RBI') || upper.includes('CENTRAL BANK') || upper.includes('REPO RATE') || upper.includes('DEPOSIT RATE')) return 'RATES';
    if (upper.includes('PMI') || upper.includes('ISM') || upper.includes('MANUFACTURING') || upper.includes('SERVICES')) return 'PMI';
    if (upper.includes('RETAIL')) return 'RETAIL';
    if (upper.includes('TRADE') || upper.includes('CURRENT ACCOUNT') || upper.includes('EXPORT') || upper.includes('IMPORT')) return 'TRADE';
    if (upper.includes('HOUSING') || upper.includes('HOME SALES') || upper.includes('BUILDING PERMITS')) return 'HOUSING';
    if (upper.includes('CONSUMER') || upper.includes('CONFIDENCE') || upper.includes('SENTIMENT')) return 'CONSUMER';
    return 'OTHER';
  }

  private static mapImportance(importance: number): 'HIGH' | 'MEDIUM' | 'LOW' {
    if (importance >= 3) return 'HIGH';
    if (importance >= 2) return 'MEDIUM';
    return 'LOW';
  }

  private static mapForexFactoryImpact(impact: string): 'HIGH' | 'MEDIUM' | 'LOW' {
    const upper = (impact || '').toUpperCase();
    if (upper.includes('HIGH') || upper === '3') return 'HIGH';
    if (upper.includes('MEDIUM') || upper === '2') return 'MEDIUM';
    return 'LOW';
  }

  private static calculateExpiry(scheduledAt: Date, status: string): Date {
    const expiry = new Date(scheduledAt);
    if (status === 'RELEASED') {
      // Released events: expire after 24 hours
      expiry.setHours(expiry.getHours() + COMPLETED_RETENTION_HOURS);
    } else {
      // Upcoming events: expire after scheduled time + 1 day buffer
      expiry.setHours(expiry.getHours() + 24);
    }
    return expiry;
  }

  // ═══════════════════════════════════════════════════════════════
  // TIMEZONE CONVERSION (IST)
  // ═══════════════════════════════════════════════════════════════

  private static toIST(date: Date): string {
    return date.toLocaleString('en-IN', {
      timeZone: IST_TIMEZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    });
  }

  // ══════════════════════════════════════════════════════════════
  // CLEANUP
  // ═══════════════════════════════════════════════════════════════

  private static async cleanupOldEvents() {
    const now = new Date();
    
    // Delete expired events (expiresAt < now)
    const deletedExpired = await prisma.economicEvent.deleteMany({
      where: { expiresAt: { lt: now } },
    });
    
    // Also delete events older than 30 days as safety net
    const cutoff = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    const deletedOld = await prisma.economicEvent.deleteMany({
      where: { scheduledAt: { lt: cutoff } },
    });

    if (deletedExpired.count > 0 || deletedOld.count > 0) {
      logger.info(`[EconomicCalendarService] Cleanup: ${deletedExpired.count} expired, ${deletedOld.count} old events removed`);
    }
  }

  private static cleanupJob() {
    // Run cleanup every 6 hours
    setInterval(() => this.cleanupOldEvents(), 6 * 60 * 60 * 1000);
  }

  // ══════════════════════════════════════════════════════════════
  // PUBLIC API
  // ═══════════════════════════════════════════════════════════════

  static async getCalendar(filters: EconomicCalendarQueryDto = {
    country: undefined,
    currency: undefined,
    category: undefined,
    importance: undefined,
    status: undefined,
    from: undefined,
    to: undefined,
    limit: undefined,
  }): Promise<EconomicEventDto[]> {
    const { country, currency, category, importance, status, from, to, limit = 100 } = filters;
    const where: any = {};

    if (country) where.country = country;
    if (currency) where.currency = currency;
    if (category) where.category = category;
    if (importance) where.importance = importance;
    if (status) where.status = status;
    if (from || to) {
      where.scheduledAt = {};
      if (from) where.scheduledAt.gte = new Date(from);
      if (to) where.scheduledAt.lte = new Date(to);
    }

    const events = await prisma.economicEvent.findMany({
      where,
      orderBy: { scheduledAt: 'asc' },
      take: limit,
    });

    return events.map(this.toDto);
  }

  static async getUpcomingEvents(limit: number = 50): Promise<EconomicEventDto[]> {
    const now = new Date();
    const to = new Date(now.getTime() + UPCOMING_DAYS * 24 * 60 * 60 * 1000);
    
    const events = await prisma.economicEvent.findMany({
      where: {
        status: 'UPCOMING',
        scheduledAt: { gte: now, lte: to },
      },
      orderBy: { scheduledAt: 'asc' },
      take: limit,
    });

    return events.map(this.toDto);
  }

  static async getRecentReleases(limit: number = 20): Promise<EconomicEventDto[]> {
    const now = new Date();
    const from = new Date(now.getTime() - COMPLETED_RETENTION_HOURS * 60 * 60 * 1000);
    
    const events = await prisma.economicEvent.findMany({
      where: {
        status: 'RELEASED',
        releasedAt: { gte: from },
      },
      orderBy: { releasedAt: 'desc' },
      take: limit,
    });

    return events.map(this.toDto);
  }

  static async getEventById(eventId: string): Promise<EconomicEventDto | null> {
    const event = await prisma.economicEvent.findUnique({ where: { eventId } });
    return event ? this.toDto(event) : null;
  }

  private static toDto(event: any): EconomicEventDto {
    const scheduledAt = new Date(event.scheduledAt);
    const releasedAt = event.releasedAt ? new Date(event.releasedAt) : null;
    
    return {
      id: event.id,
      eventId: event.eventId,
      title: event.title,
      country: event.country,
      currency: event.currency,
      category: event.category as any,
      importance: event.importance as any,
      scheduledAt: scheduledAt.toISOString(),
      timezone: event.timezone,
      previousValue: event.previousValue ?? undefined,
      forecastValue: event.forecastValue ?? undefined,
      actualValue: event.actualValue ?? undefined,
      unit: event.unit ?? undefined,
      status: event.status as any,
      source: event.source,
      sourceUrl: event.sourceUrl ?? undefined,
      releasedAt: releasedAt?.toISOString() ?? undefined,
      scheduledAtIST: this.toIST(scheduledAt),
      releasedAtIST: releasedAt ? this.toIST(releasedAt) : undefined,
    };
  }
}