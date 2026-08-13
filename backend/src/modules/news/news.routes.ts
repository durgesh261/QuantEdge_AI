import { Router } from 'express';
import { NewsService } from './services/NewsService.js';
import { EconomicCalendarService } from './services/EconomicCalendarService.js';
import { NewsFilterEngine } from './services/NewsFilterEngine.js';

const router = Router();

// Initialize services
NewsService.start();
EconomicCalendarService.start();
NewsFilterEngine.initialize();

// ════════════════════════════════════════════════════════════════
// NEWS ENDPOINTS
// ════════════════════════════════════════════════════════════════

router.get('/news', async (req, res) => {
  try {
    const categoryRaw = req.query.category as string | undefined;
    const importanceRaw = req.query.importance as string | undefined;
    const category = categoryRaw === 'ALL' ? undefined : (categoryRaw as import('@algoapp/shared').NewsCategory | undefined);
    const importance = importanceRaw === 'ALL' ? undefined : (importanceRaw as import('@algoapp/shared').NewsImportance | undefined);
    const symbol = req.query.symbol as string | undefined;
    const limit = Math.min(parseInt(req.query.limit as string) || 50, 100);
    const offset = parseInt(req.query.offset as string) || 0;
    const since = req.query.since as string | undefined;

    const articles = await NewsService.getRecentArticles({
      category,
      importance,
      symbol,
      limit,
      offset,
      since,
    });
    res.json({ success: true, data: articles });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/news/symbol/:symbol', async (req, res) => {
  try {
    const articles = await NewsService.getArticlesBySymbol(req.params.symbol.toUpperCase(), 20);
    res.json({ success: true, data: articles });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/news/search', async (req, res) => {
  try {
    const q = req.query.q as string;
    if (!q) {
      res.status(400).json({ success: false, error: 'Query required' });
      return;
    }
    const articles = await NewsService.searchArticles(q, 20);
    res.json({ success: true, data: articles });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/news/providers/status', async (_req, res) => {
  try {
    const statuses = NewsService.getProviderStatuses();
    res.json({ success: true, data: statuses });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ════════════════════════════════════════════════════════════════
// ECONOMIC CALENDAR ENDPOINTS
// ════════════════════════════════════════════════════════════════

router.get('/calendar', async (req, res) => {
  try {
    const country = req.query.country as string | undefined;
    const currency = req.query.currency as string | undefined;
    const category = (req.query.category as string | undefined) as import('@algoapp/shared').EconomicEventCategory | undefined;
    const importance = (req.query.importance as string | undefined) as import('@algoapp/shared').EconomicEventImportance | undefined;
    const status = (req.query.status as string | undefined) as import('@algoapp/shared').EconomicEventStatus | undefined;
    const from = req.query.from as string | undefined;
    const to = req.query.to as string | undefined;
    const limit = Math.min(parseInt(req.query.limit as string) || 100, 200);

    const events = await EconomicCalendarService.getCalendar({
      country,
      currency,
      category,
      importance,
      status,
      from,
      to,
      limit,
    });
    res.json({ success: true, data: events });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/calendar/upcoming', async (req, res) => {
  try {
    const limit = parseInt(req.query.limit as string) || 50;
    const events = await EconomicCalendarService.getUpcomingEvents(limit);
    res.json({ success: true, data: events });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/calendar/recent', async (req, res) => {
  try {
    const limit = parseInt(req.query.limit as string) || 20;
    const events = await EconomicCalendarService.getRecentReleases(limit);
    res.json({ success: true, data: events });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/calendar/event/:eventId', async (req, res) => {
  try {
    const event = await EconomicCalendarService.getEventById(req.params.eventId);
    if (!event) {
      res.status(404).json({ success: false, error: 'Event not found' });
      return;
    }
    res.json({ success: true, data: event });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/calendar/providers/status', async (_req, res) => {
  try {
    const status = EconomicCalendarService.getProviderStatus();
    res.json({ success: true, data: status });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ════════════════════════════════════════════════════════════════
// NEWS FILTER (TRADING) ENDPOINTS
// ════════════════════════════════════════════════════════════════

router.get('/filter/status', async (_req, res) => {
  try {
    const status = NewsFilterEngine.getStatus();
    // Add provider statuses
    status.newsProviderStatus = NewsService.getProviderStatuses();
    status.economicCalendarProviderStatus = EconomicCalendarService.getProviderStatus();
    res.json({ success: true, data: status });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/filter/is-blocking', async (_req, res) => {
  try {
    const isBlocking = NewsFilterEngine.isBlocking();
    res.json({ success: true, data: { isBlocking } });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

export const newsRouter = router;