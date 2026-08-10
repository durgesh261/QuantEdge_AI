import Parser from 'rss-parser';
import { prisma } from '../../../db.js';
import { logger } from '../../../logger/index.js';
import { EventEmitter } from 'events';

const rssParser = new Parser({
  headers: { 'User-Agent': 'QuantEdge-AI/1.0' },
  timeout: 15000,
});

export interface NewsArticle {
  id: string;
  title: string;
  description: string;
  url: string;
  source: string;
  sourceLabel: string;
  category: 'CRYPTO' | 'MACRO' | 'REGULATION' | 'MARKETS' | 'TECH';
  publishedAt: Date;
  imageUrl?: string | undefined;
  tickers?: string[] | undefined;
  isNew?: boolean | undefined;
}

// ═══════════════════════════════════════════════════════════════
// MAJOR NEWS SOURCES — Finance, Global, Crypto
// ═══════════════════════════════════════════════════════════════
const NEWS_SOURCES = [
  // Tier 1: Crypto Native
  { url: 'https://cointelegraph.com/rss', name: 'Cointelegraph', category: 'CRYPTO' as const },
  { url: 'https://www.coindesk.com/arc/outboundfeeds/rss/', name: 'CoinDesk', category: 'CRYPTO' as const },
  { url: 'https://www.theblock.co/rss.xml', name: 'The Block', category: 'CRYPTO' as const },
  { url: 'https://decrypt.co/feed', name: 'Decrypt', category: 'CRYPTO' as const },
  { url: 'https://cryptoslate.com/feed', name: 'CryptoSlate', category: 'CRYPTO' as const },
  { url: 'https://news.bitcoin.com/feed/', name: 'Bitcoin.com', category: 'CRYPTO' as const },
  
  // Tier 2: Crypto Markets & Analysis
  { url: 'https://cryptopotato.com/feed/', name: 'CryptoPotato', category: 'MARKETS' as const },
  { url: 'https://www.newsbtc.com/feed/', name: 'NewsBTC', category: 'MARKETS' as const },
  { url: 'https://thedefiant.io/api/feed/', name: 'The Defiant', category: 'CRYPTO' as const },
  { url: 'https://ambcrypto.com/feed/', name: 'AMBCrypto', category: 'CRYPTO' as const },
  
  // Tier 3: Traditional Finance / Macro
  { url: 'https://search.cnbc.com/rs/search/combinedradios/search.xml?partnerId=2000&keywords=finance', name: 'CNBC Finance', category: 'MACRO' as const },
  { url: 'https://www.forexlive.com/feed/', name: 'ForexLive', category: 'MACRO' as const },
  { url: 'https://www.investing.com/rss/news_301.rss', name: 'Investing.com', category: 'MARKETS' as const },
  
  // Tier 4: Regulation & Policy
  { url: 'https://cointelegraph.com/rss/tag/regulation', name: 'Cointelegraph Regulation', category: 'REGULATION' as const },
];

export const newsEmitter = new EventEmitter();

export class NewsAggregatorService {
  private static isRunning = false;
  private static intervalId: NodeJS.Timeout | null = null;
  private static readonly POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
  private static readonly MAX_AGE_DAYS = 7;

  static start() {
    if (this.isRunning) return;
    this.isRunning = true;
    
    logger.info('[NewsAggregator] Starting RSS aggregation...');
    this.pollAll(); // Immediate first run
    this.intervalId = setInterval(() => this.pollAll(), this.POLL_INTERVAL_MS);
  }

  static stop() {
    if (this.intervalId) clearInterval(this.intervalId);
    this.isRunning = false;
    logger.info('[NewsAggregator] Stopped.');
  }

  private static async pollAll() {
    const allArticles: NewsArticle[] = [];
    
    for (const source of NEWS_SOURCES) {
      try {
        const feed = await rssParser.parseURL(source.url);
        const articles = feed.items
          ?.filter(item => item.title && item.link)
          ?.map(item => this.normalizeArticle(item, source.name, source.category))
          ?.filter(a => this.isWithinLast7Days(a.publishedAt)) || [];
        
        allArticles.push(...articles);
      } catch (err) {
        logger.warn(`[NewsAggregator] Failed to fetch ${source.name}: ${(err as Error).message}`);
      }
    }

    // Deduplicate by URL
    const uniqueMap = new Map<string, NewsArticle>();
    for (const article of allArticles) {
      if (!uniqueMap.has(article.url)) {
        uniqueMap.set(article.url, article);
      }
    }
    const uniqueArticles = Array.from(uniqueMap.values())
      .sort((a, b) => b.publishedAt.getTime() - a.publishedAt.getTime());

    // Detect new articles (not in DB)
    const newArticles = await this.detectNewArticles(uniqueArticles);
    
    if (newArticles.length > 0) {
      // Store in DB
      await this.storeArticles(newArticles);
      // Emit for WebSocket push
      for (const article of newArticles.slice(0, 5)) {
        newsEmitter.emit('new-article', { ...article, isNew: true });
      }
      logger.info(`[NewsAggregator] ${newArticles.length} new articles ingested.`);
    }

    // Cleanup old articles
    await this.cleanupOldArticles();
  }

  private static normalizeArticle(
    item: Parser.Item, 
    sourceName: string, 
    category: NewsArticle['category']
  ): NewsArticle {
    const pubDate = item.pubDate ? new Date(item.pubDate) : new Date();
    
    // Extract tickers from title/description
    const text = `${item.title} ${item.contentSnippet || ''}`;
    const tickerMatches = text.match(/\b(BTC|ETH|SOL|XRP|BNB|ADA|DOT|AVAX|LINK|MATIC|DOGE|SHIB|PEPE|FET|RENDER|TAO|SEI|SUI|APT|ARB|OP|STRK|WLD|ARKM|INJ|RNDR|LDO|UNI|AAVE|COMP|MKR|CRV|SNX|YFI|BAL|1INCH|SUSHI|DYDX|GMX|SNX|PERP|VRTX|DRIFT|JUP|JTO|PYTH|W|BOME|WIF|BONK|FLOKI|MEME|PEOPLE|BLUR|BLAST|ZRO|ZKSYNC|LINEA|SCROLL|TAIKO|MANTA|MANTLE|MODE|BASE|ZORA|FRAME|DEGEN|AERO|VELO|SONNE|EXTRA|GRAIL|RAM|EQUAL|THENAFI|SOLIDLY|SOLIDLIZARD|SOLIDSEX|SNEK|SQUID|SPX|MOG|BITCOIN|HPOS10I|TURBO|NPC|PEPECOIN|APU|BOBO|WOJAK|MILADY|LADYS|POND|KEKEC|BRETT|ANDY|LANDWOLF|WOLF|BOME|SLERF|NOS|IO|GRASS|NODEAI|AIOZ|RNDR|AKT|FIL|AR|STORJ|SC|SIA|BLZ|HNT|MOBILE|HONEY|SHDW|GENE|ATLAS|POLIS|STAR|STARATLAS|SAMO|BONFIDA|ORCA|RAY|MNDE|MSOL|JITOSOL|BSOL|VSOL|INF|LST|BNSOL|HBAR|ALGO|XLM|XTZ|ICP|NEAR|FLOW|IMX|GALA|SAND|MANA|AXS|ENJ|CHZ|OGN|SUPER|ILV|YGG|MC|APE|NFT|LOOKS|X2Y2|BLUR|BLAST|ZRO|ZKSYNC|LINEA|SCROLL|TAIKO|MANTA|MANTLE|MODE|BASE|ZORA|FRAME|DEGEN|AERO|VELO|SONNE|EXTRA|GRAIL|RAM|EQUAL|THENAFI|SOLIDLY|SOLIDLIZARD|SOLIDSEX|SNEK|SQUID|SPX|MOG|BITCOIN|HPOS10I|TURBO|NPC|PEPECOIN|APU|BOBO|WOJAK|MILADY|LADYS|POND|KEKEC|BRETT|ANDY|LANDWOLF|WOLF|BOME|SLERF|NOS|IO|GRASS|NODEAI|AIOZ|RNDR|AKT|FIL|AR|STORJ|SC|SIA|BLZ|HNT|MOBILE|HONEY|SHDW|GENE|ATLAS|POLIS|STAR|STARATLAS|SAMO|BONFIDA|ORCA|RAY|MNDE|MSOL|JITOSOL|BSOL|VSOL|INF|LST|BNSOL|HBAR|ALGO|XLM|XTZ|ICP|NEAR|FLOW|IMX|GALA|SAND|MANA|AXS|ENJ|CHZ|OGN|SUPER|ILV|YGG|MC|APE|NFT|LOOKS|X2Y2)\b/gi);
    const tickers = tickerMatches ? [...new Set(tickerMatches.map(t => t.toUpperCase()))] : undefined;

    // Categorize regulation articles
    let finalCategory = category;
    const lowerText = text.toLowerCase();
    if (lowerText.includes('sec') || lowerText.includes('regulation') || lowerText.includes('regulatory') || 
        lowerText.includes('cbdc') || lowerText.includes('fed') || lowerText.includes('treasury') ||
        lowerText.includes('bill') || lowerText.includes('law') || lowerText.includes('compliance') ||
        lowerText.includes('sanction') || lowerText.includes('etf approval') || lowerText.includes('etf rejection')) {
      finalCategory = 'REGULATION';
    } else if (lowerText.includes('cpi') || lowerText.includes('inflation') || lowerText.includes('gdp') ||
               lowerText.includes('unemployment') || lowerText.includes('nfp') || lowerText.includes('non-farm') ||
               lowerText.includes('fed rate') || lowerText.includes('interest rate') || lowerText.includes('fomc') ||
               lowerText.includes('ecb') || lowerText.includes('boe') || lowerText.includes('rbi') ||
               lowerText.includes('recession') || lowerText.includes('macro')) {
      finalCategory = 'MACRO';
    }

    return {
      id: `news-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`,
      title: item.title!.slice(0, 200),
      description: (item.contentSnippet || item.summary || '').slice(0, 500),
      url: item.link!,
      source: sourceName,
      sourceLabel: sourceName,
      category: finalCategory,
      publishedAt: pubDate,
      imageUrl: item.enclosure?.url || undefined,
      tickers: tickers && tickers.length > 0 ? tickers.slice(0, 5) : undefined,
    };
  }

  private static isWithinLast7Days(date: Date): boolean {
    const sevenDaysAgo = new Date();
    sevenDaysAgo.setDate(sevenDaysAgo.getDate() - this.MAX_AGE_DAYS);
    return date >= sevenDaysAgo;
  }

  private static async detectNewArticles(articles: NewsArticle[]): Promise<NewsArticle[]> {
    const urls = articles.map(a => a.url);
    const existing = await prisma.newsArticle.findMany({
      where: { url: { in: urls } },
      select: { url: true },
    });
    const existingSet = new Set(existing.map(e => e.url));
    return articles.filter(a => !existingSet.has(a.url));
  }

  private static async storeArticles(articles: NewsArticle[]) {
    const data = articles.map(a => ({
      id: a.id,
      title: a.title,
      description: a.description,
      url: a.url,
      source: a.source,
      category: a.category as NewsArticle['category'],
      publishedAt: a.publishedAt,
      imageUrl: a.imageUrl || null,
      tickers: a.tickers ? JSON.stringify(a.tickers) : null,
    }));

    await prisma.newsArticle.createMany({
      data,
    });
  }

  private static async cleanupOldArticles() {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - this.MAX_AGE_DAYS);
    await prisma.newsArticle.deleteMany({
      where: { publishedAt: { lt: cutoff } },
    });
  }

  // ═══════════════════════════════════════════════════════════════
  // PUBLIC API
  // ═══════════════════════════════════════════════════════════════

  static async getRecentArticles(
    category?: string, 
    limit: number = 50,
    offset: number = 0
  ): Promise<NewsArticle[]> {
    const where: any = {};
    if (category && category !== 'ALL') where.category = category;

    const articles = await prisma.newsArticle.findMany({
      where,
      orderBy: { publishedAt: 'desc' },
      take: limit,
      skip: offset,
    });

    return articles.map(a => ({
      ...a,
      category: a.category as NewsArticle['category'],
      sourceLabel: a.source,
      tickers: a.tickers ? JSON.parse(a.tickers) : undefined,
      publishedAt: a.publishedAt,
      imageUrl: a.imageUrl || undefined,
    }));
  }

  static async getArticlesByTicker(ticker: string, limit: number = 20): Promise<NewsArticle[]> {
    const articles = await prisma.newsArticle.findMany({
      where: {
        tickers: { contains: ticker },
      },
      orderBy: { publishedAt: 'desc' },
      take: limit,
    });
    return articles.map(a => ({
      ...a,
      category: a.category as NewsArticle['category'],
      sourceLabel: a.source,
      tickers: a.tickers ? JSON.parse(a.tickers) : undefined,
      imageUrl: a.imageUrl || undefined,
    }));
  }

  static async searchArticles(query: string, limit: number = 20): Promise<NewsArticle[]> {
    const articles = await prisma.newsArticle.findMany({
      where: {
        OR: [
          { title: { contains: query } },
          { description: { contains: query } },
        ],
      },
      orderBy: { publishedAt: 'desc' },
      take: limit,
    });
    return articles.map(a => ({
      ...a,
      category: a.category as NewsArticle['category'],
      sourceLabel: a.source,
      tickers: a.tickers ? JSON.parse(a.tickers) : undefined,
      imageUrl: a.imageUrl || undefined,
    }));
  }
}
