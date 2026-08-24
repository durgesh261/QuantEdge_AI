import React, { useEffect, useState, useCallback } from 'react'
import { intelligenceService } from '../../services/intelligenceService'
import { NewsArticleDto } from '../../types/news'
import { EconomicEventDto } from '../../types/economic'
import {
  Newspaper,
  Calendar,
  Filter,
  RefreshCw,
  ExternalLink,
  Flame,
  Globe,
  TrendingUp,
  TrendingDown,
  Minus,
  AlertCircle,
  ShieldCheck,
} from 'lucide-react'

export const MarketIntelligence: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'news' | 'calendar'>('news')

  // News State
  const [news, setNews] = useState<NewsArticleDto[]>([])
  const [newsLoading, setNewsLoading] = useState(true)
  const [newsError, setNewsError] = useState<string | null>(null)
  const [newsCategory, setNewsCategory] = useState<string>('ALL')
  const [newsImportance, setNewsImportance] = useState<string>('ALL')

  // Calendar State
  const [events, setEvents] = useState<EconomicEventDto[]>([])
  const [calendarLoading, setCalendarLoading] = useState(true)
  const [calendarError, setCalendarError] = useState<string | null>(null)
  const [countryFilter, setCountryFilter] = useState<string>('ALL')
  const [importanceFilter, setImportanceFilter] = useState<string>('ALL')

  // Fetch News
  const fetchNews = useCallback(async () => {
    try {
      setNewsLoading(true)
      setNewsError(null)
      const data = await intelligenceService.getNews(
        newsCategory !== 'ALL' ? newsCategory : undefined,
        newsImportance !== 'ALL' ? newsImportance : undefined,
        undefined,
        100
      )
      setNews(data)
    } catch (err: any) {
      console.warn('Failed to fetch news', err)
      setNewsError(err.response?.data?.message || 'Unable to connect to financial news feed')
    } finally {
      setNewsLoading(false)
    }
  }, [newsCategory, newsImportance])

  // Fetch Economic Calendar
  const fetchCalendar = useCallback(async () => {
    try {
      setCalendarLoading(true)
      setCalendarError(null)
      const data = await intelligenceService.getEconomicEvents(
        countryFilter !== 'ALL' ? countryFilter : undefined,
        undefined,
        importanceFilter !== 'ALL' ? importanceFilter : undefined,
        undefined,
        undefined,
        100
      )
      setEvents(data)
    } catch (err: any) {
      console.warn('Failed to fetch economic events', err)
      setCalendarError(err.response?.data?.message || 'Unable to connect to macroeconomic calendar service')
    } finally {
      setCalendarLoading(false)
    }
  }, [countryFilter, importanceFilter])

  useEffect(() => {
    if (activeTab === 'news') {
      fetchNews()
    } else {
      fetchCalendar()
    }
  }, [activeTab, fetchNews, fetchCalendar])

  // Helper to calculate countdown
  const getEventCountdown = (scheduledAt: string) => {
    const diff = new Date(scheduledAt).getTime() - Date.now()
    if (diff < 0) return 'Released'
    const hours = Math.floor(diff / (1000 * 60 * 60))
    const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60))
    if (hours > 24) {
      const days = Math.floor(hours / 24)
      return `in ${days}d ${hours % 24}h`
    }
    return `in ${hours}h ${minutes}m`
  }

  // Periodic timer for live countdown updates
  const [, setTick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setTick((t) => t + 1), 30000)
    return () => clearInterval(timer)
  }, [])

  // Country Flags
  const countryFlags: Record<string, string> = {
    US: '🇺🇸',
    IN: '🇮🇳',
    EU: '🇪🇺',
    GB: '🇬🇧',
    JP: '🇯🇵',
    CN: '🇨🇳',
    CA: '🇨🇦',
    AU: '🇦🇺',
  }

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-terminal-border">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <Newspaper className="w-5 h-5 text-warning" />
            <span>Live Market Intelligence</span>
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-0.5">
            7-Day Categorized Financial & Crypto News • 15-Day Global Macroeconomic Calendar
          </p>
        </div>

        {/* Tab Switcher */}
        <div className="flex items-center p-1 rounded-lg bg-background-surface border border-terminal-border">
          <button
            onClick={() => setActiveTab('news')}
            className={`flex items-center gap-2 px-4 py-1.5 rounded-md text-xs font-mono font-bold transition-all ${
              activeTab === 'news'
                ? 'bg-warning text-background shadow-md shadow-warning/20'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <Newspaper className="w-3.5 h-3.5" />
            <span>Financial News ({news.length})</span>
          </button>
          <button
            onClick={() => setActiveTab('calendar')}
            className={`flex items-center gap-2 px-4 py-1.5 rounded-md text-xs font-mono font-bold transition-all ${
              activeTab === 'calendar'
                ? 'bg-brand-cyan text-background shadow-md shadow-brand-cyan/20'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <Calendar className="w-3.5 h-3.5" />
            <span>15-Day Macro Calendar ({events.length})</span>
          </button>
        </div>
      </div>

      {/* ─────────────────────────────────────────────────────────────
          TAB 1: FINANCIAL & CRYPTO NEWS FEED
      ────────────────────────────────────────────────────────────── */}
      {activeTab === 'news' && (
        <div className="space-y-4">
          {/* News Filter Bar */}
          <div className="glass-panel p-3 rounded-lg flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-1.5 text-slate-400 font-semibold">
                <Filter className="w-3.5 h-3.5" />
                <span>Category:</span>
              </div>
              <div className="flex flex-wrap items-center p-0.5 rounded bg-background/80 border border-terminal-border">
                {['ALL', 'CRYPTO', 'FINANCE', 'MARKETS', 'CENTRAL_BANKS', 'REGULATION', 'ECONOMY', 'COMMODITIES', 'MACRO'].map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setNewsCategory(cat)}
                    className={`px-2.5 py-1 rounded text-[11px] transition-all ${
                      newsCategory === cat
                        ? 'bg-warning text-background font-bold shadow-sm'
                        : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>

              {/* Importance Filter */}
              <div className="flex items-center gap-1.5 text-slate-400 font-semibold ml-2">
                <span>Impact:</span>
              </div>
              <div className="flex items-center p-0.5 rounded bg-background/80 border border-terminal-border">
                {['ALL', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((imp) => (
                  <button
                    key={imp}
                    onClick={() => setNewsImportance(imp)}
                    className={`px-2 py-1 rounded text-[11px] transition-all ${
                      newsImportance === imp
                        ? 'bg-background-elevated text-white font-bold border border-slate-600'
                        : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    {imp}
                  </button>
                ))}
              </div>
            </div>

            <button
              onClick={fetchNews}
              disabled={newsLoading}
              className="p-1.5 rounded-md hover:bg-background-elevated text-slate-400 hover:text-warning transition-colors disabled:opacity-50"
              title="Refresh News Feed"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${newsLoading ? 'animate-spin text-warning' : ''}`} />
            </button>
          </div>

          {/* Error Notice */}
          {newsError && (
            <div className="p-3 rounded-lg bg-bearish/10 border border-bearish/20 text-xs text-bearish flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{newsError}</span>
              </div>
              <button
                onClick={fetchNews}
                className="px-2.5 py-1 rounded bg-bearish/20 text-white font-bold font-mono"
              >
                Retry
              </button>
            </div>
          )}

          {/* News Feed Grid */}
          {newsLoading && news.length === 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-pulse">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="h-44 bg-background-surface rounded-lg"></div>
              ))}
            </div>
          ) : news.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {news.map((item) => {
                const isCritical = item.importance === 'CRITICAL'
                const isHigh = item.importance === 'HIGH'
                const isBullish = item.sentiment === 'BULLISH'
                const isBearish = item.sentiment === 'BEARISH'

                return (
                  <div
                    key={item.id}
                    className="glass-panel p-4 rounded-lg flex flex-col justify-between space-y-3 hover:border-warning/40 transition-all group"
                  >
                    {/* Top Row: Category, Importance & Sentiment Badges */}
                    <div className="flex items-center justify-between gap-2 pb-2 border-b border-terminal-border/80">
                      <div className="flex items-center gap-2">
                        <span className="px-2 py-0.5 rounded bg-background border border-terminal-border text-[10px] font-mono font-bold text-slate-300">
                          {item.category}
                        </span>

                        {/* Importance Pill */}
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold flex items-center gap-1 ${
                            isCritical
                              ? 'bg-bearish/20 text-bearish border border-bearish/30 animate-pulse'
                              : isHigh
                              ? 'bg-warning/20 text-warning border border-warning/30'
                              : 'bg-slate-800 text-slate-400'
                          }`}
                        >
                          {isCritical && <Flame className="w-3 h-3 text-bearish" />}
                          {item.importance}
                        </span>
                      </div>

                      {/* Sentiment Badge */}
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold flex items-center gap-1 ${
                          isBullish
                            ? 'bg-bullish/15 text-bullish'
                            : isBearish
                            ? 'bg-bearish/15 text-bearish'
                            : 'bg-slate-800 text-slate-400'
                        }`}
                      >
                        {isBullish && <TrendingUp className="w-3 h-3" />}
                        {isBearish && <TrendingDown className="w-3 h-3" />}
                        {!isBullish && !isBearish && <Minus className="w-3 h-3" />}
                        {item.sentiment}
                      </span>
                    </div>

                    {/* Headline Title */}
                    <h3 className="text-sm font-bold text-white group-hover:text-warning transition-colors leading-snug">
                      {item.title}
                    </h3>

                    {/* Summary Excerpt */}
                    <p className="text-xs text-slate-300 font-sans leading-relaxed line-clamp-3">
                      {item.summary}
                    </p>

                    {/* Footer: Source Attribution, 7-Day TTL Badge & Canonical Link */}
                    <div className="pt-2 border-t border-terminal-border/60 flex items-center justify-between text-[11px] font-mono text-slate-400">
                      <div className="flex items-center gap-3">
                        <span className="text-slate-300 font-medium">{item.source}</span>
                        <span>•</span>
                        <span>{new Date(item.publishedAt).toLocaleDateString()}</span>
                        <span>•</span>
                        <span className="text-slate-500">7-Day Retention</span>
                      </div>

                      {item.sourceUrl && (
                        <a
                          href={item.sourceUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 text-warning hover:underline font-semibold"
                        >
                          <span>Source</span>
                          <ExternalLink className="w-3 h-3" />
                        </a>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="glass-panel p-12 text-center rounded-lg space-y-3">
              {newsError ? (
                <>
                  <AlertCircle className="w-10 h-10 text-bearish mx-auto" />
                  <h3 className="text-sm font-bold text-white font-mono">News Service Unavailable</h3>
                  <p className="text-xs text-slate-400 max-w-md mx-auto">
                    Unable to connect to the financial news ingestion service. Please try again later.
                  </p>
                  <button
                    onClick={fetchNews}
                    className="mt-4 px-4 py-2 rounded bg-brand-cyan text-background font-mono text-xs font-bold hover:bg-brand-cyan/90 transition-all"
                  >
                    Retry News Feed
                  </button>
                </>
              ) : newsLoading ? (
                <>
                  <Newspaper className="w-10 h-10 text-slate-600 mx-auto animate-pulse" />
                  <h3 className="text-sm font-bold text-white font-mono">Loading News Feed...</h3>
                  <p className="text-xs text-slate-400 max-w-md mx-auto">
                    Ingesting latest financial articles from multiple sources.
                  </p>
                </>
              ) : (newsCategory !== 'ALL' || newsImportance !== 'ALL') ? (
                <>
                  <Filter className="w-10 h-10 text-slate-600 mx-auto" />
                  <h3 className="text-sm font-bold text-white font-mono">No Articles Match Filters</h3>
                  <p className="text-xs text-slate-400 max-w-md mx-auto">
                    No financial articles match your selected category ({newsCategory}) and impact ({newsImportance}) filters.
                  </p>
                  <button
                    onClick={() => { setNewsCategory('ALL'); setNewsImportance('ALL'); }}
                    className="mt-4 px-4 py-2 rounded bg-brand-cyan text-background font-mono text-xs font-bold hover:bg-brand-cyan/90 transition-all"
                  >
                    Reset Filters
                  </button>
                </>
              ) : (
                <>
                  <Newspaper className="w-10 h-10 text-slate-600 mx-auto" />
                  <h3 className="text-sm font-bold text-white font-mono">No News Articles Available</h3>
                  <p className="text-xs text-slate-400 max-w-md mx-auto">
                    No financial articles currently available. Ingestion may be delayed or no new articles published in the last 7 days.
                  </p>
                  <button
                    onClick={fetchNews}
                    className="mt-4 px-4 py-2 rounded bg-brand-cyan text-background font-mono text-xs font-bold hover:bg-brand-cyan/90 transition-all"
                  >
                    Refresh Feed
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────
          TAB 2: 15-DAY MACROECONOMIC CALENDAR
      ────────────────────────────────────────────────────────────── */}
      {activeTab === 'calendar' && (
        <div className="space-y-4">
          {/* Calendar Filter Bar */}
          <div className="glass-panel p-3 rounded-lg flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-1.5 text-slate-400 font-semibold">
                <Globe className="w-3.5 h-3.5" />
                <span>Country:</span>
              </div>
              <div className="flex flex-wrap items-center p-0.5 rounded bg-background/80 border border-terminal-border">
                {['ALL', 'US', 'IN', 'EU', 'GB', 'JP', 'CN', 'CA', 'AU'].map((c) => (
                  <button
                    key={c}
                    onClick={() => setCountryFilter(c)}
                    className={`px-2 py-1 rounded text-[11px] transition-all flex items-center gap-1 ${
                      countryFilter === c
                        ? 'bg-brand-cyan text-background font-bold shadow-sm'
                        : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    {countryFlags[c] && <span>{countryFlags[c]}</span>}
                    <span>{c}</span>
                  </button>
                ))}
              </div>

              {/* Impact Filter */}
              <div className="flex items-center gap-1.5 text-slate-400 font-semibold ml-2">
                <span>Impact:</span>
              </div>
              <div className="flex items-center p-0.5 rounded bg-background/80 border border-terminal-border">
                {['ALL', 'HIGH', 'MEDIUM', 'LOW'].map((imp) => (
                  <button
                    key={imp}
                    onClick={() => setImportanceFilter(imp)}
                    className={`px-2 py-1 rounded text-[11px] transition-all ${
                      importanceFilter === imp
                        ? 'bg-background-elevated text-white font-bold border border-slate-600'
                        : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    {imp}
                  </button>
                ))}
              </div>
            </div>

            <button
              onClick={fetchCalendar}
              disabled={calendarLoading}
              className="p-1.5 rounded-md hover:bg-background-elevated text-slate-400 hover:text-brand-cyan transition-colors disabled:opacity-50"
              title="Refresh Macroeconomic Calendar"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${calendarLoading ? 'animate-spin text-brand-cyan' : ''}`} />
            </button>
          </div>

          {/* Calendar Error Notice */}
          {calendarError && (
            <div className="p-3 rounded-lg bg-bearish/10 border border-bearish/20 text-xs text-bearish flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{calendarError}</span>
              </div>
              <button
                onClick={fetchCalendar}
                className="px-2.5 py-1 rounded bg-bearish/20 text-white font-bold font-mono"
              >
                Retry
              </button>
            </div>
          )}

          {/* Calendar Table Container */}
          <div className="glass-panel rounded-lg overflow-hidden">
            {calendarLoading && events.length === 0 ? (
              <div className="p-12 text-center text-slate-400 font-mono text-xs animate-pulse">
                Loading 15-day rolling macroeconomic calendar...
              </div>
            ) : events.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono">
                  <thead>
                    <tr className="border-b border-terminal-border bg-background-surface/80 text-slate-400 text-[11px]">
                      <th className="py-3 px-4">Date & Time</th>
                      <th className="py-3 px-4">Country</th>
                      <th className="py-3 px-4">Impact</th>
                      <th className="py-3 px-4">Event Name</th>
                      <th className="py-3 px-4">Actual</th>
                      <th className="py-3 px-4">Forecast</th>
                      <th className="py-3 px-4">Previous</th>
                      <th className="py-3 px-4">Countdown</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-terminal-border/50 text-slate-200">
                    {events.map((ev) => {
                      const isHigh = ev.importance === 'HIGH'
                      const isMedium = ev.importance === 'MEDIUM'
                      const countdown = getEventCountdown(ev.scheduledAt)
                      const isReleased = countdown === 'Released'

                      return (
                        <tr key={ev.id} className="hover:bg-background-elevated/40 transition-colors">
                          {/* Date & Time */}
                          <td className="py-3 px-4 whitespace-nowrap text-slate-300">
                            <div>{new Date(ev.scheduledAt).toLocaleDateString()}</div>
                            <div className="text-[10px] text-slate-500">{new Date(ev.scheduledAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
                          </td>

                          {/* Country Flag & Currency */}
                          <td className="py-3 px-4 whitespace-nowrap font-bold text-white">
                            <span className="mr-1.5">{countryFlags[ev.country] || '🌐'}</span>
                            <span>{ev.country} ({ev.currency})</span>
                          </td>

                          {/* Impact Pill */}
                          <td className="py-3 px-4 whitespace-nowrap">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                isHigh
                                  ? 'bg-bearish/20 text-bearish border border-bearish/30'
                                  : isMedium
                                  ? 'bg-warning/20 text-warning border border-warning/30'
                                  : 'bg-slate-800 text-slate-400'
                              }`}
                            >
                              {ev.importance}
                            </span>
                          </td>

                          {/* Event Name & Category */}
                          <td className="py-3 px-4">
                            <div className="font-semibold text-white">{ev.eventName}</div>
                            <div className="text-[10px] text-slate-400">{ev.category}</div>
                          </td>

                          {/* Actual Value */}
                          <td className="py-3 px-4 font-bold text-brand-cyan">
                            {ev.actualValue || '—'}
                          </td>

                          {/* Forecast Value */}
                          <td className="py-3 px-4 text-slate-300">
                            {ev.forecastValue || '—'}
                          </td>

                          {/* Previous Value */}
                          <td className="py-3 px-4 text-slate-400">
                            {ev.previousValue || '—'}
                          </td>

                          {/* Live Countdown & Status */}
                          <td className="py-3 px-4 whitespace-nowrap">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                isReleased
                                  ? 'bg-background border border-terminal-border text-slate-400'
                                  : 'bg-brand-cyan/15 text-brand-cyan border border-brand-cyan/30'
                              }`}
                            >
                              {countdown}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-12 text-center text-slate-500 font-mono text-xs">
                {calendarError ? (
                  <>
                    <AlertCircle className="w-8 h-8 text-bearish mx-auto mb-2" />
                    <div className="text-white font-bold mb-1">Calendar Service Unavailable</div>
                    <div>Unable to connect to macroeconomic calendar service.</div>
                    <button
                      onClick={fetchCalendar}
                      className="mt-3 px-4 py-2 rounded bg-brand-cyan text-background font-mono text-xs font-bold hover:bg-brand-cyan/90 transition-all"
                    >
                      Retry Calendar
                    </button>
                  </>
                ) : (countryFilter !== 'ALL' || importanceFilter !== 'ALL') ? (
                  <>
                    <Filter className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                    <div className="text-white font-bold mb-1">No Events Match Filters</div>
                    <div>No economic events match your selected country ({countryFilter}) and impact ({importanceFilter}) filters.</div>
                    <button
                      onClick={() => { setCountryFilter('ALL'); setImportanceFilter('ALL'); }}
                      className="mt-3 px-4 py-2 rounded bg-brand-cyan text-background font-mono text-xs font-bold hover:bg-brand-cyan/90 transition-all"
                    >
                      Reset Filters
                    </button>
                  </>
                ) : (
                  <>
                    <Calendar className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                    <div className="text-white font-bold mb-1">No Economic Events Found</div>
                    <div>No macroeconomic events available for the 15-day window. Calendar ingestion may be delayed.</div>
                    <button
                      onClick={fetchCalendar}
                      className="mt-3 px-4 py-2 rounded bg-brand-cyan text-background font-mono text-xs font-bold hover:bg-brand-cyan/90 transition-all"
                    >
                      Refresh Calendar
                    </button>
                  </>
                )}
              </div>
            )}
          </div>

          <div className="p-3 rounded-lg bg-background/50 border border-terminal-border text-[11px] font-mono text-slate-400 flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 text-bullish" />
              <span>Retention Invariant: Completed events are retained for 24 hours post-release before scheduled purge.</span>
            </span>
            <span className="text-slate-500">15-Day Rolling Window</span>
          </div>
        </div>
      )}
    </div>
  )
}
