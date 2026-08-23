package com.quantedge.market.controller;

import com.quantedge.market.dto.*;
import com.quantedge.market.service.MarketDataService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * REST API Controller for Real Delta Exchange India (DELTAIN) Market Data & Charts.
 */
@RestController
@RequestMapping("/v1/market")
public class MarketDataController {

    private final MarketDataService marketDataService;

    public MarketDataController(MarketDataService marketDataService) {
        this.marketDataService = marketDataService;
    }

    @GetMapping("/products")
    public ResponseEntity<List<ProductDto>> getProducts() {
        List<ProductDto> products = marketDataService.getProducts();
        return ResponseEntity.ok(products);
    }

    @GetMapping("/ticker/{symbol}")
    public ResponseEntity<TickerDto> getTicker(@PathVariable("symbol") String symbol) {
        TickerDto ticker = marketDataService.getTicker(symbol);
        return ResponseEntity.ok(ticker);
    }

    @GetMapping("/candles/{symbol}")
    public ResponseEntity<ChartCandlesResponseDto> getCandlesByPath(
            @PathVariable("symbol") String symbol,
            @RequestParam(value = "interval", required = false, defaultValue = "1h") String interval,
            @RequestParam(value = "start", required = false) Long start,
            @RequestParam(value = "end", required = false) Long end,
            @RequestParam(value = "limit", required = false, defaultValue = "500") Integer limit
    ) {
        ChartCandlesResponseDto response = marketDataService.getCandles(symbol, interval, start, end, limit);
        return ResponseEntity.ok(response);
    }

    @GetMapping("/candles")
    public ResponseEntity<ChartCandlesResponseDto> getCandlesByQuery(
            @RequestParam(value = "symbol", required = false, defaultValue = "BTCUSD") String symbol,
            @RequestParam(value = "interval", required = false, defaultValue = "1h") String interval,
            @RequestParam(value = "start", required = false) Long start,
            @RequestParam(value = "end", required = false) Long end,
            @RequestParam(value = "limit", required = false, defaultValue = "500") Integer limit
    ) {
        ChartCandlesResponseDto response = marketDataService.getCandles(symbol, interval, start, end, limit);
        return ResponseEntity.ok(response);
    }

    @GetMapping("/status")
    public ResponseEntity<MarketStatusDto> getMarketStatus(
            @RequestParam(value = "symbol", required = false, defaultValue = "BTCUSD") String symbol
    ) {
        MarketStatusDto status = marketDataService.getMarketStatus(symbol);
        return ResponseEntity.ok(status);
    }
}
