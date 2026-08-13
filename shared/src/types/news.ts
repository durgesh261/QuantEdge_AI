export type NewsCategory = 'CRYPTO' | 'BITCOIN' | 'ETHEREUM' | 'SOLANA' | 'XRP' | 'MACRO_ECONOMY' | 'REGULATORY' | 'MARKETS' | 'TECH' | 'EXCHANGE' | 'DELTA_EXCHANGE';
export type NewsImportance = 'HIGH' | 'MEDIUM' | 'LOW';

export interface NewsArticleDto {
  id: string;
  headline: string;
  summary: string;
  url: string;
  source: string;
  category: NewsCategory;
  importance: NewsImportance;
  sentiment: 'BULLISH' | 'BEARISH' | 'NEUTRAL' | undefined;
  impactScore: number;
  symbols: string[];
  publishedAt: string;
  receivedAt: string;
  imageUrl: string | undefined;
}

export interface LiveNewsItemDto {
  id: string;
  headline: string;
  summary: string;
  url: string;
  source: string;
  category: NewsCategory;
  importance: NewsImportance;
  sentiment: 'BULLISH' | 'BEARISH' | 'NEUTRAL' | undefined;
  impactScore: number;
  symbols: string[];
  publishedAt: string;
  receivedAt: string;
  imageUrl: string | undefined;
}

export interface NewsFilterQueryDto {
  category: NewsCategory | undefined;
  importance: NewsImportance | undefined;
  symbol: string | undefined;
  limit: number | undefined;
  offset: number | undefined;
  since: string | undefined;
}

export interface NewsProviderStatus {
  provider: string;
  connected: boolean;
  lastFetch: string | null;
  articlesFetched: number;
  error: string | undefined;
}

export type EconomicEventCategory = 'INFLATION' | 'EMPLOYMENT' | 'GDP' | 'RATES' | 'PMI' | 'RETAIL' | 'TRADE' | 'HOUSING' | 'CONSUMER' | 'MANUFACTURING' | 'CENTRAL_BANK' | 'OTHER';
export type EconomicEventImportance = 'HIGH' | 'MEDIUM' | 'LOW';
export type EconomicEventStatus = 'UPCOMING' | 'RELEASED';

export interface EconomicEventDto {
  id: string;
  eventId: string;
  title: string;
  country: string;
  currency: string;
  category: EconomicEventCategory;
  importance: EconomicEventImportance;
  scheduledAt: string;
  timezone: string;
  previousValue: string | undefined;
  forecastValue: string | undefined;
  actualValue: string | undefined;
  unit: string | undefined;
  status: EconomicEventStatus;
  source: string;
  sourceUrl: string | undefined;
  releasedAt: string | undefined;
  scheduledAtIST: string;
  releasedAtIST: string | undefined;
}

export interface EconomicCalendarQueryDto {
  country: string | undefined;
  currency: string | undefined;
  category: EconomicEventCategory | undefined;
  importance: EconomicEventImportance | undefined;
  status: EconomicEventStatus | undefined;
  from: string | undefined;
  to: string | undefined;
  limit: number | undefined;
}

export interface NewsFilterEventDto {
  id: string;
  eventId: string;
  title: string;
  category: string;
  impactLevel: 'HIGH' | 'MEDIUM' | 'LOW';
  isBlocking: boolean;
  publishedAt: string;
  blockStart: string;
  blockEnd: string;
  source: string;
  sourceUrl: string | undefined;
}

export interface NewsFilterStatusDto {
  isBlocking: boolean;
  activeEvents: NewsFilterEventDto[];
  nextBlockingEvent: NewsFilterEventDto | undefined;
  newsProviderStatus: NewsProviderStatus[];
  economicCalendarProviderStatus: NewsProviderStatus;
}