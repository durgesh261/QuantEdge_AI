package com.quantedge.economic.controller;

import com.quantedge.economic.dto.EconomicEventDto;
import com.quantedge.economic.service.EconomicCalendarService;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * REST API Controller for Macroeconomic Calendar & Events.
 */
@RestController
@RequestMapping("/api/v1/economic-events")
public class EconomicCalendarController {

    private final EconomicCalendarService calendarService;

    public EconomicCalendarController(EconomicCalendarService calendarService) {
        this.calendarService = calendarService;
    }

    @GetMapping("/upcoming")
    public ResponseEntity<List<EconomicEventDto>> getUpcomingEvents(
            @RequestParam(value = "limit", required = false, defaultValue = "50") Integer limit
    ) {
        List<EconomicEventDto> list = calendarService.getUpcomingEvents(limit);
        return ResponseEntity.ok(list);
    }

    @GetMapping
    public ResponseEntity<List<EconomicEventDto>> getEvents(
            @RequestParam(value = "country", required = false) String country,
            @RequestParam(value = "currency", required = false) String currency,
            @RequestParam(value = "importance", required = false) String importance,
            @RequestParam(value = "from", required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant from,
            @RequestParam(value = "to", required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant to,
            @RequestParam(value = "limit", required = false, defaultValue = "50") Integer limit
    ) {
        List<EconomicEventDto> list = calendarService.getEvents(country, currency, importance, from, to, limit);
        return ResponseEntity.ok(list);
    }

    @GetMapping("/{id}")
    public ResponseEntity<EconomicEventDto> getEventById(@PathVariable("id") String id) {
        EconomicEventDto dto = calendarService.getEventById(id);
        return ResponseEntity.ok(dto);
    }

    @PostMapping("/refresh")
    public ResponseEntity<Map<String, Object>> refreshEconomicCalendar() {
        int count = calendarService.syncEconomicCalendar();
        return ResponseEntity.ok(Map.of("success", true, "synchronizedEventsCount", count));
    }
}
