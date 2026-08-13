import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { eventBus } from '../../../services/EventBus.js';
import type { NewsArticleDto, NewsFilterQueryDto, NewsProviderStatus } from '@algoapp/shared';

const NEWS_API_KEY = process.env['NEWS_API_KEY'] || '';
const CRYPTO_PANIC_KEY = process.env['CRYPTO_PANIC_KEY'] || '';
const POLL_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes
const MAX_AGE_DAYS = 7;

interface NewsAPIArticle {
  title: string;
  description: string;
  url: string;
  source: { name: string };
  publishedAt: string;
  urlToImage?: string;
}

interface CryptoPanicPost {
  id: number;
  title: string;
  url: string;
  source: { title: string; domain: string };
  published_at: string;
  currencies: Array<{ code: string }>;
  kind: string;
  sentiment?: string;
}

export class NewsService {
  private static isRunning = false;
  private static intervalId: NodeJS.Timeout | null = null;
  private static providerStatuses = new Map<string, NewsProviderStatus>();

  static start() {
    if (this.isRunning) return;
    this.isRunning = true;
    logger.info('[NewsService] Starting real-time news aggregation...');
    this.pollAll();
    this.intervalId = setInterval(() => this.pollAll(), POLL_INTERVAL_MS);
    this.scheduleCleanup();
  }

  static stop() {
    if (this.intervalId) clearInterval(this.intervalId);
    this.isRunning = false;
    logger.info('[NewsService] Stopped.');
  }

  static getProviderStatuses(): NewsProviderStatus[] {
    return Array.from(this.providerStatuses.values());
  }

  private static scheduleCleanup() {
    // Run cleanup every 6 hours
    setInterval(() => this.cleanupOldArticles(), 6 * 60 * 60 * 1000);
  }

  private static async pollAll() {
    const allArticles: NewsArticleDto[] = [];

    // Source 1: NewsAPI.org - Business/Finance headlines
    if (NEWS_API_KEY) {
      const articles = await this.fetchNewsAPI();
      allArticles.push(...articles);
    } else {
      this.updateProviderStatus('newsapi', { connected: false, error: 'NEWS_API_KEY not configured' });
    }

    // Source 2: CryptoPanic - Crypto-specific news
    if (CRYPTO_PANIC_KEY) {
      const articles = await this.fetchCryptoPanic();
      allArticles.push(...articles);
    } else {
      this.updateProviderStatus('cryptopanic', { connected: false, error: 'CRYPTO_PANIC_KEY not configured' });
    }

    // Source 3: CoinDesk RSS (fallback, no auth needed)
    const coindeskArticles = await this.fetchCoinDeskRSS();
    allArticles.push(...coindeskArticles);

    // Source 4: Cointelegraph RSS (fallback, no auth needed)
    const cointelegraphArticles = await this.fetchCoinTelegraphRSS();
    allArticles.push(...cointelegraphArticles);

    // Deduplicate by URL
    const uniqueMap = new Map<string, NewsArticleDto>();
    for (const article of allArticles) {
      if (!uniqueMap.has(article.url)) {
        uniqueMap.set(article.url, article);
      }
    }
    const uniqueArticles = Array.from(uniqueMap.values())
      .sort((a, b) => new Date(b.publishedAt).getTime() - new Date(a.publishedAt).getTime());

    // Filter to last 7 days
    const cutoff = new Date(Date.now() - MAX_AGE_DAYS * 24 * 60 * 60 * 1000);
    const recentArticles = uniqueArticles.filter(a => new Date(a.publishedAt) >= cutoff);

    // Detect and store new articles
    const newArticles = await this.detectAndStoreNewArticles(recentArticles);
    
    // Emit new articles for WebSocket push
    if (newArticles.length > 0) {
      for (const article of newArticles.slice(0, 10)) {
        eventBus.emit('news:new-article', article);
      }
      logger.info(`[NewsService] ${newArticles.length} new articles ingested.`);
    }

    // Cleanup old articles
    await this.cleanupOldArticles();
  }

  private static async fetchNewsAPI(): Promise<NewsArticleDto[]> {
    const articles: NewsArticleDto[] = [];
    try {
      const endpoints = [
        'https://newsapi.org/v2/top-headlines?category=business&language=en&pageSize=50',
        'https://newsapi.org/v2/top-headlines?category=technology&language=en&pageSize=30',
        'https://newsapi.org/v2/everything?q=crypto OR bitcoin OR ethereum OR blockchain&language=en&pageSize=30&sortBy=publishedAt',
      ];

      for (const endpoint of endpoints) {
        try {
          const res = await fetch(`${endpoint}&apiKey=${NEWS_API_KEY}`, {
            headers: { 'User-Agent': 'QuantEdge-AI/1.0' },
            signal: AbortSignal.timeout(15000),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();
          if (data.status === 'ok' && Array.isArray(data.articles)) {
            for (const item of data.articles) {
              const article = this.normalizeNewsAPIArticle(item);
              if (article) articles.push(article);
            }
          }
        } catch (err) {
          logger.warn(`[NewsService] NewsAPI endpoint failed: ${(err as Error).message}`);
        }
      }
      this.updateProviderStatus('newsapi', { connected: true, lastFetch: new Date().toISOString(), articlesFetched: articles.length });
    } catch (err) {
      this.updateProviderStatus('newsapi', { connected: false, error: (err as Error).message });
    }
    return articles;
  }

  private static normalizeNewsAPIArticle(item: NewsAPIArticle): NewsArticleDto | null {
    if (!item.title || !item.url) return null;
    const fullText = `${item.title} ${item.description || ''}`.toLowerCase();
    
    let category: NewsArticleDto['category'] = 'MARKETS';
    if (fullText.includes('bitcoin') || fullText.includes('btc')) category = 'BITCOIN';
    else if (fullText.includes('ethereum') || fullText.includes('eth ')) category = 'ETHEREUM';
    else if (fullText.includes('solana') || fullText.includes('sol ')) category = 'SOLANA';
    else if (fullText.includes('xrp') || fullText.includes('ripple')) category = 'XRP';
    else if (fullText.includes('crypto') || fullText.includes('blockchain') || fullText.includes('defi')) category = 'CRYPTO';
    else if (fullText.includes('fed') || fullText.includes('fomc') || fullText.includes('cpi') || fullText.includes('inflation') || fullText.includes('gdp') || fullText.includes('unemployment') || fullText.includes('interest rate')) category = 'MACRO_ECONOMY';
    else if (fullText.includes('sec') || fullText.includes('etf') || fullText.includes('regulation') || fullText.includes('regulatory') || fullText.includes('lawsuit')) category = 'REGULATORY';
    else if (fullText.includes('exchange') || fullText.includes('binance') || fullText.includes('coinbase') || fullText.includes('delta')) category = 'EXCHANGE';
    else if (fullText.includes('tech') || fullText.includes('ai ') || fullText.includes('technology')) category = 'TECH';

    let importance: NewsArticleDto['importance'] = 'LOW';
    if (category === 'MACRO_ECONOMY' || fullText.includes('etf') || fullText.includes('billion') || fullText.includes('emergency') || fullText.includes('rate cut') || fullText.includes('crash')) importance = 'HIGH';
    else if (fullText.includes('million') || fullText.includes('upgrade') || fullText.includes('partnership') || category === 'REGULATORY' || category === 'EXCHANGE') importance = 'MEDIUM';

    let sentiment: NewsArticleDto['sentiment'] = 'NEUTRAL';
    if (fullText.includes('surge') || fullText.includes('bull') || fullText.includes('rally') || fullText.includes('breakout') || fullText.includes('approved') || fullText.includes('record high') || fullText.includes('adoption')) sentiment = 'BULLISH';
    else if (fullText.includes('crash') || fullText.includes('bear') || fullText.includes('plunge') || fullText.includes('dump') || fullText.includes('ban') || fullText.includes('lawsuit') || fullText.includes('hack') || fullText.includes('exploit')) sentiment = 'BEARISH';

    const symbols: string[] = [];
    if (fullText.includes('btc') || fullText.includes('bitcoin')) symbols.push('BTC');
    if (fullText.includes('eth') || fullText.includes('ethereum')) symbols.push('ETH');
    if (fullText.includes('sol') || fullText.includes('solana')) symbols.push('SOL');
    if (fullText.includes('xrp') || fullText.includes('ripple')) symbols.push('XRP');
    if (symbols.length === 0) symbols.push('MARKET');

    const impactScore = importance === 'HIGH' ? 9 : importance === 'MEDIUM' ? 6 : 3;

    return {
      id: `newsapi-${Buffer.from(item.url).toString('base64').slice(0, 16)}`,
      headline: item.title.slice(0, 200),
      summary: (item.description || '').slice(0, 500),
      url: item.url,
      source: item.source.name,
      category,
      importance,
      sentiment: (sentiment ?? 'NEUTRAL') as NewsArticleDto['sentiment'],
      impactScore,
      symbols,
      publishedAt: item.publishedAt,
      receivedAt: new Date().toISOString(),
      imageUrl: (item.urlToImage ?? undefined) as NewsArticleDto['imageUrl'],
    };
  }

  private static async fetchCryptoPanic(): Promise<NewsArticleDto[]> {
    const articles: NewsArticleDto[] = [];
    try {
      const res = await fetch(`https://cryptopanic.com/api/v1/posts/?auth_token=${CRYPTO_PANIC_KEY}&kind=news&public=true&filter=hot`, {
        headers: { 'User-Agent': 'QuantEdge-AI/1.0' },
        signal: AbortSignal.timeout(15000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (Array.isArray(data.results)) {
        for (const item of data.results.slice(0, 50)) {
          const article = this.normalizeCryptoPanicPost(item);
          if (article) articles.push(article);
        }
      }
      this.updateProviderStatus('cryptopanic', { connected: true, lastFetch: new Date().toISOString(), articlesFetched: articles.length });
    } catch (err) {
      this.updateProviderStatus('cryptopanic', { connected: false, error: (err as Error).message });
    }
    return articles;
  }

  private static normalizeCryptoPanicPost(item: CryptoPanicPost): NewsArticleDto | null {
    if (!item.title || !item.url) return null;
    const fullText = item.title.toLowerCase();
    const currencies = item.currencies?.map(c => c.code) || [];
    
    let category: NewsArticleDto['category'] = 'CRYPTO';
    if (currencies.includes('BTC')) category = 'BITCOIN';
    else if (currencies.includes('ETH')) category = 'ETHEREUM';
    else if (currencies.includes('SOL')) category = 'SOLANA';
    else if (currencies.includes('XRP')) category = 'XRP';
    else if (fullText.includes('macro') || fullText.includes('fed') || fullText.includes('cpi') || fullText.includes('inflation')) category = 'MACRO_ECONOMY';
    else if (fullText.includes('sec') || fullText.includes('etf') || fullText.includes('regulation') || fullText.includes('lawsuit')) category = 'REGULATORY';
    else if (fullText.includes('exchange') || fullText.includes('binance') || fullText.includes('coinbase')) category = 'EXCHANGE';

    let importance: NewsArticleDto['importance'] = 'MEDIUM';
    if (item.kind === 'news' && (fullText.includes('etf') || fullText.includes('billion') || fullText.includes('etf approval'))) {
      importance = 'HIGH';
    } else if (fullText.includes('million') || fullText.includes('upgrade') || fullText.includes('partnership')) {
      importance = 'MEDIUM';
    }

    let sentiment: NewsArticleDto['sentiment'] = 'NEUTRAL';
    if (item.sentiment === 'bullish' || fullText.includes('surge') || fullText.includes('bull') || fullText.includes('rally') || fullText.includes('breakout') || fullText.includes('approved') || fullText.includes('record high')) sentiment = 'BULLISH';
    else if (item.sentiment === 'bearish' || fullText.includes('crash') || fullText.includes('bear') || fullText.includes('plunge') || fullText.includes('dump') || fullText.includes('ban') || fullText.includes('lawsuit') || fullText.includes('hack')) sentiment = 'BEARISH';

    const symbols = currencies.length > 0 ? currencies : ['MARKET'];
    const impactScore = importance === 'HIGH' ? 9 : importance === 'MEDIUM' ? 6 : 3;

    return {
      id: `cryptopanic-${item.id}`,
      headline: item.title.slice(0, 200),
      summary: '',
      url: item.url,
      source: item.source.title || item.source.domain || 'CryptoPanic',
      category,
      importance,
      sentiment: (sentiment ?? 'NEUTRAL') as NewsArticleDto['sentiment'],
      impactScore,
      symbols,
      publishedAt: item.published_at,
      receivedAt: new Date().toISOString(),
      imageUrl: undefined as NewsArticleDto['imageUrl'],
    };
  }

  private static async fetchCoinDeskRSS(): Promise<NewsArticleDto[]> {
    const articles: NewsArticleDto[] = [];
    try {
      const { default: Parser } = await import('rss-parser');
      const parser = new Parser({ timeout: 15000 });
      const feed = await parser.parseURL('https://www.coindesk.com/arc/outboundfeeds/rss/');
      if (feed.items) {
        for (const item of feed.items.slice(0, 30)) {
          const article = this.normalizeRSSItem(item, 'CoinDesk', 'CRYPTO');
          if (article) articles.push(article);
        }
      }
      this.updateProviderStatus('coindesk_rss', { connected: true, lastFetch: new Date().toISOString(), articlesFetched: articles.length });
    } catch (err) {
      this.updateProviderStatus('coindesk_rss', { connected: false, error: (err as Error).message });
    }
    return articles;
  }

  private static async fetchCoinTelegraphRSS(): Promise<NewsArticleDto[]> {
    const articles: NewsArticleDto[] = [];
    try {
      const { default: Parser } = await import('rss-parser');
      const parser = new Parser({ timeout: 15000 });
      const feed = await parser.parseURL('https://cointelegraph.com/rss');
      if (feed.items) {
        for (const item of feed.items.slice(0, 30)) {
          const article = this.normalizeRSSItem(item, 'Cointelegraph', 'CRYPTO');
          if (article) articles.push(article);
        }
      }
      this.updateProviderStatus('cointelegraph_rss', { connected: true, lastFetch: new Date().toISOString(), articlesFetched: articles.length });
    } catch (err) {
      this.updateProviderStatus('cointelegraph_rss', { connected: false, error: (err as Error).message });
    }
    return articles;
  }

  private static normalizeRSSItem(item: any, sourceName: string, defaultCategory: NewsArticleDto['category']): NewsArticleDto | null {
    if (!item.title || !item.link) return null;
    const fullText = `${item.title} ${item.contentSnippet || ''}`.toLowerCase();
    
    let category: NewsArticleDto['category'] = defaultCategory;
    if (fullText.includes('bitcoin') || fullText.includes('btc')) category = 'BITCOIN';
    else if (fullText.includes('ethereum') || fullText.includes('eth ')) category = 'ETHEREUM';
    else if (fullText.includes('solana') || fullText.includes('sol ')) category = 'SOLANA';
    else if (fullText.includes('xrp') || fullText.includes('ripple')) category = 'XRP';
    else if (fullText.includes('macro') || fullText.includes('fed') || fullText.includes('cpi') || fullText.includes('inflation')) category = 'MACRO_ECONOMY';
    else if (fullText.includes('sec') || fullText.includes('etf') || fullText.includes('regulation') || fullText.includes('lawsuit')) category = 'REGULATORY';
    else if (fullText.includes('exchange') || fullText.includes('binance') || fullText.includes('coinbase')) category = 'EXCHANGE';

    let importance: NewsArticleDto['importance'] = 'LOW';
    if (category === 'MACRO_ECONOMY' || fullText.includes('etf') || fullText.includes('billion') || fullText.includes('emergency') || fullText.includes('rate cut')) importance = 'HIGH';
    else if (fullText.includes('million') || fullText.includes('upgrade') || fullText.includes('partnership') || category === 'REGULATORY') importance = 'MEDIUM';

    let sentiment: NewsArticleDto['sentiment'] = 'NEUTRAL';
    if (fullText.includes('surge') || fullText.includes('bull') || fullText.includes('rally') || fullText.includes('breakout') || fullText.includes('approved') || fullText.includes('record high')) sentiment = 'BULLISH';
    else if (fullText.includes('crash') || fullText.includes('bear') || fullText.includes('plunge') || fullText.includes('dump') || fullText.includes('ban') || fullText.includes('lawsuit') || fullText.includes('hack')) sentiment = 'BEARISH';

    const symbols: string[] = [];
    if (fullText.includes('btc') || fullText.includes('bitcoin')) symbols.push('BTC');
    if (fullText.includes('eth') || fullText.includes('ethereum')) symbols.push('ETH');
    if (fullText.includes('sol') || fullText.includes('solana')) symbols.push('SOL');
    if (fullText.includes('xrp') || fullText.includes('ripple')) symbols.push('XRP');
    if (symbols.length === 0) symbols.push('MARKET');

    const impactScore = importance === 'HIGH' ? 9 : importance === 'MEDIUM' ? 6 : 3;

    return {
      id: `rss-${Buffer.from(item.link).toString('base64').slice(0, 16)}`,
      headline: item.title!.slice(0, 200),
      summary: (item.contentSnippet || item.summary || '').slice(0, 500),
      url: item.link!,
      source: sourceName,
      category,
      importance,
      sentiment: (sentiment ?? 'NEUTRAL') as NewsArticleDto['sentiment'],
      impactScore,
      symbols,
      publishedAt: item.pubDate ? new Date(item.pubDate).toISOString() : new Date().toISOString(),
      receivedAt: new Date().toISOString(),
      imageUrl: (item.enclosure?.url ?? undefined) as NewsArticleDto['imageUrl'],
    };
  }

  private static async detectAndStoreNewArticles(articles: NewsArticleDto[]): Promise<NewsArticleDto[]> {
    const urls = articles.map(a => a.url);
    const existing = await prisma.newsArticle.findMany({
      where: { url: { in: urls } },
      select: { url: true },
    });
    const existingSet = new Set(existing.map(e => e.url));
    const newArticles = articles.filter(a => !existingSet.has(a.url));

    if (newArticles.length > 0) {
      const data = newArticles.map(a => ({
        id: a.id,
        articleId: a.id,
        headline: a.headline,
        summary: a.summary || null,
        url: a.url,
        source: a.source,
        category: a.category,
        importance: a.importance,
        sentiment: a.sentiment || null,
        impactScore: a.impactScore || null,
        symbols: JSON.stringify(a.symbols),
        publishedAt: new Date(a.publishedAt),
        receivedAt: new Date(a.receivedAt),
        imageUrl: a.imageUrl || null,
      }));

      try {
        await prisma.newsArticle.createMany({ data });
      } catch (err: any) {
        // Ignore unique constraint violations (P2002) - articles already exist
        if (err?.code !== 'P2002') {
          logger.warn('[NewsService] createMany failed:', err?.message || err);
        }
      }
    }

    return newArticles;
  }

  private static async cleanupOldArticles() {
    const cutoff = new Date(Date.now() - MAX_AGE_DAYS * 24 * 60 * 60 * 1000);
    const deleted = await prisma.newsArticle.deleteMany({
      where: { publishedAt: { lt: cutoff } },
    });
    if (deleted.count > 0) {
      logger.info(`[NewsService] Cleaned up ${deleted.count} articles older than ${MAX_AGE_DAYS} days.`);
    }
  }

  private static updateProviderStatus(provider: string, partial: Partial<NewsProviderStatus>) {
    const existing = this.providerStatuses.get(provider) || {
      provider,
      connected: false,
      lastFetch: null,
      articlesFetched: 0,
      error: undefined,
    };
    this.providerStatuses.set(provider, { ...existing, ...partial });
  }

  // ═══════════════════════════════════════════════════════════════
  // PUBLIC API
  // ════════════════════════════════════════════════════════════════

  static async getRecentArticles(filters: NewsFilterQueryDto = {
    category: undefined,
    importance: undefined,
    symbol: undefined,
    limit: undefined,
    offset: undefined,
    since: undefined,
  }): Promise<NewsArticleDto[]> {
    const { category, importance, symbol, limit = 50, offset = 0, since } = filters;
    const where: any = {};
    if (category) where.category = category;
    if (importance) where.importance = importance;
    if (symbol && symbol !== 'ALL') {
      const cleanSym = symbol.replace('USD.P', '').replace('.P', '').toUpperCase();
      where.symbols = { contains: cleanSym };
    }
    if (since) where.publishedAt = { gte: new Date(since) };

    const articles = await prisma.newsArticle.findMany({
      where,
      orderBy: { publishedAt: 'desc' },
      take: limit,
      skip: offset,
    });

    return articles.map(a => ({
      id: a.id,
      headline: a.headline,
      summary: a.summary || '',
      url: a.url,
      source: a.source,
      category: a.category as NewsArticleDto['category'],
      importance: a.importance as NewsArticleDto['importance'],
      sentiment: (a.sentiment as NewsArticleDto['sentiment'] | undefined) ?? undefined,
      impactScore: a.impactScore || (a.importance === 'HIGH' ? 9 : a.importance === 'MEDIUM' ? 6 : 3),
      symbols: a.symbols ? JSON.parse(a.symbols) : [],
      publishedAt: a.publishedAt.toISOString(),
      receivedAt: a.receivedAt.toISOString(),
      imageUrl: a.imageUrl || undefined,
    }));
  }

  static async getArticlesBySymbol(symbol: string, limit: number = 20): Promise<NewsArticleDto[]> {
    const cleanSym = symbol.replace('USD.P', '').replace('.P', '').toUpperCase();
    return this.getRecentArticles({ 
      symbol: cleanSym, 
      limit,
      category: undefined,
      importance: undefined,
      offset: 0,
      since: undefined,
    });
  }

  static async searchArticles(query: string, limit: number = 20): Promise<NewsArticleDto[]> {
    const articles = await prisma.newsArticle.findMany({
      where: {
        OR: [
          { headline: { contains: query } },
          { summary: { contains: query } },
        ],
      },
      orderBy: { publishedAt: 'desc' },
      take: limit,
    });
    return articles.map(a => ({
      id: a.id,
      headline: a.headline,
      summary: a.summary || '',
      url: a.url,
      source: a.source,
      category: a.category as NewsArticleDto['category'],
      importance: a.importance as NewsArticleDto['importance'],
      sentiment: (a.sentiment as NewsArticleDto['sentiment'] | undefined) ?? undefined,
      impactScore: a.impactScore || 5,
      symbols: a.symbols ? JSON.parse(a.symbols) : [],
      publishedAt: a.publishedAt.toISOString(),
      receivedAt: a.receivedAt.toISOString(),
      imageUrl: a.imageUrl || undefined,
    }));
  }
}