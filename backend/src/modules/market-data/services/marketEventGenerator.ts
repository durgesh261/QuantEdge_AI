import { MarketEventDto, MarketEventType } from '@algoapp/shared';

// Event log - starts empty, only real events are added
let eventLog: MarketEventDto[] = [];

export class MarketEventGenerator {
  public static async getEvents(): Promise<MarketEventDto[]> {
    return eventLog;
  }

  public static async emitEvent(
    symbol: string,
    eventType: MarketEventType,
    payload: Record<string, any>
  ): Promise<MarketEventDto> {
    const event: MarketEventDto = {
      id: `MKT-EVT-${Date.now()}`,
      symbol,
      eventType,
      payloadJson: JSON.stringify(payload),
      timestamp: new Date().toISOString(),
    };
    eventLog.unshift(event);
    
    // Keep only last 1000 events to prevent memory growth
    if (eventLog.length > 1000) {
      eventLog = eventLog.slice(0, 1000);
    }
    
    return event;
  }
}