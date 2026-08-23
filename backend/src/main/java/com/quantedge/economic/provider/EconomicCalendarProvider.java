package com.quantedge.economic.provider;

import com.quantedge.economic.entity.EconomicEvent;

import java.time.Instant;
import java.util.List;

/**
 * Interface for Macroeconomic Calendar event providers.
 */
public interface EconomicCalendarProvider {

    /**
     * Provider identification name.
     */
    String getProviderName();

    /**
     * Fetches macroeconomic events within the specified time window.
     */
    List<EconomicEvent> fetchUpcomingEvents(Instant from, Instant to);
}
