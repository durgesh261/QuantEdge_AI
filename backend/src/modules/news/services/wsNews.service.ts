import { Server } from 'socket.io';
import { logger } from '../../../logger/index.js';
import { NewsService } from './NewsService.js';
import { EconomicCalendarService } from './EconomicCalendarService.js';

export function setupNewsWebSocket(io: Server) {
  const newsNs = io.of('/news');
  const calendarNs = io.of('/calendar');

  // News namespace
  newsNs.on('connection', (socket) => {
    logger.info(`[WS:News] Client connected: ${socket.id}`);
    
    socket.on('subscribe-category', (category: string) => {
      socket.join(`category-${category.toUpperCase()}`);
    });
    
    socket.on('subscribe-symbol', (symbol: string) => {
      socket.join(`symbol-${symbol.toUpperCase()}`);
    });

    socket.on('disconnect', () => {
      logger.info(`[WS:News] Client disconnected: ${socket.id}`);
    });
  });

  // Calendar namespace
  calendarNs.on('connection', (socket) => {
    logger.info(`[WS:Calendar] Client connected: ${socket.id}`);
    
    socket.on('subscribe-country', (country: string) => {
      socket.join(`country-${country}`);
    });
    
    socket.on('subscribe-currency', (currency: string) => {
      socket.join(`currency-${currency.toUpperCase()}`);
    });

    socket.on('disconnect', () => {
      logger.info(`[WS:Calendar] Client disconnected: ${socket.id}`);
    });
  });

  // Ensure services are running
  NewsService.start();
  EconomicCalendarService.start();
  
  // We'll use eventBus to emit events from services
  // The services emit 'news:new-article' and 'economic:new-event' / 'economic:event-released'
  // We listen to those and push via WebSocket

  // Import eventBus dynamically to avoid circular dependency
  import('../../../services/EventBus.js').then(({ eventBus }) => {
    eventBus.on('news:new-article', (article: any) => {
      newsNs.emit('new-article', article);
      
      if (article.symbols) {
        for (const symbol of article.symbols) {
          newsNs.to(`symbol-${symbol}`).emit('new-article', article);
        }
      }
      if (article.category) {
        newsNs.to(`category-${article.category}`).emit('new-article', article);
      }
    });

    eventBus.on('economic:new-event', (event: any) => {
      calendarNs.emit('new-event', event);
      
      if (event.country) {
        calendarNs.to(`country-${event.country}`).emit('new-event', event);
      }
      if (event.currency) {
        calendarNs.to(`currency-${event.currency}`).emit('new-event', event);
      }
    });

    eventBus.on('economic:event-released', (event: any) => {
      calendarNs.emit('event-released', event);
      
      if (event.country) {
        calendarNs.to(`country-${event.country}`).emit('event-released', event);
      }
      if (event.currency) {
        calendarNs.to(`currency-${event.currency}`).emit('event-released', event);
      }
    });

    eventBus.on('news:blocking_event', (event: any) => {
      newsNs.emit('filter-blocking', event);
      calendarNs.emit('filter-blocking', event);
    });

    // Filter status updates
    eventBus.on('filter:status-changed', (status: any) => {
      newsNs.emit('filter-status', status);
      calendarNs.emit('filter-status', status);
    });
  });

  // Heartbeat
  setInterval(() => {
    newsNs.emit('ping', { timestamp: Date.now() });
    calendarNs.emit('ping', { timestamp: Date.now() });
  }, 30000);
}