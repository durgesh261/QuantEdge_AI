import { NewsService } from './services/NewsService.js';
import { EconomicCalendarService } from './services/EconomicCalendarService.js';
import { NewsFilterEngine } from './services/NewsFilterEngine.js';
import { newsRouter } from './news.routes.js';

export { NewsService, EconomicCalendarService, NewsFilterEngine, newsRouter };

// Initialize on import
NewsService.start();
EconomicCalendarService.start();
NewsFilterEngine.initialize();