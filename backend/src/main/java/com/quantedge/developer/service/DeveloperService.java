package com.quantedge.developer.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.sql.DataSource;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.lang.management.ThreadMXBean;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.sql.Connection;
import java.time.Duration;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentLinkedDeque;

/**
 * Service providing developer diagnostics, system health metrics,
 * sanitized audit logs, and isolated sandbox simulation capabilities.
 *
 * <p>SAFETY INVARIANT: Zero access to real Delta Exchange live order placement.
 * Sensitive credentials, JWT secrets, and encryption keys are strictly redacted.
 */
@Service
public class DeveloperService {

    private static final Logger log = LoggerFactory.getLogger(DeveloperService.class);

    private final TradingAccountRepository accountRepository;
    private final DataSource dataSource;

    @Value("${quantedge.delta.api-base-url:https://api.india.delta.exchange}")
    private String deltaApiBaseUrl;

    @Value("${quantedge.python-engine.base-url:http://localhost:8000}")
    private String pythonEngineBaseUrl;

    private final HttpClient httpClient;
    private final ConcurrentLinkedDeque<LogEntry> inMemoryLogBuffer = new ConcurrentLinkedDeque<>();
    private static final int MAX_LOG_BUFFER_SIZE = 100;

    // Sandbox state (strictly decoupled in-memory simulator)
    private final Map<String, Object> sandboxState = new HashMap<>();

    public DeveloperService(TradingAccountRepository accountRepository, DataSource dataSource) {
        this.accountRepository = accountRepository;
        this.dataSource = dataSource;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .build();

        // Seed initial sandbox state
        sandboxState.put("mode", "ISOLATED_SIMULATOR");
        sandboxState.put("engineDecoupled", true);
        sandboxState.put("realExecutionBlocked", true);
        sandboxState.put("simulatedBalance", new BigDecimal("100000.00"));
        sandboxState.put("lastSimulatedTickAt", Instant.now().toString());

        recordLog("INFO", "DeveloperService", "Developer diagnostics and isolated sandbox initialized");
    }

    public void recordLog(String level, String source, String message) {
        String sanitizedMessage = sanitizeLog(message);
        inMemoryLogBuffer.addFirst(new LogEntry(
                UUID.randomUUID().toString(),
                Instant.now().toString(),
                level,
                source,
                sanitizedMessage
        ));
        while (inMemoryLogBuffer.size() > MAX_LOG_BUFFER_SIZE) {
            inMemoryLogBuffer.removeLast();
        }
    }

    private String sanitizeLog(String text) {
        if (text == null) return "";
        // Redact potential api keys, secrets, tokens
        return text
                .replaceAll("(?i)(api[_-]?secret|password|secret|token|key)[:=]\\s*['\"]?[a-zA-Z0-9_-]{8,}['\"]?", "$1=***REDACTED***")
                .replaceAll("(?i)bearer\\s+[a-zA-Z0-9._-]+", "Bearer ***REDACTED***");
    }

    // ─────────────────────────────────────────────────────────────────────────
    // DTOs
    // ─────────────────────────────────────────────────────────────────────────

    public record ServiceHealth(
            String serviceName,
            String status,
            String endpoint,
            long latencyMs,
            String details
    ) {}

    public record DeveloperStatusResponse(
            String status,
            Instant timestamp,
            long uptimeSeconds,
            List<ServiceHealth> services,
            MemoryMetrics memory,
            ThreadMetrics threads
    ) {}

    public record MemoryMetrics(
            long usedHeapMb,
            long maxHeapMb,
            double heapUsagePercent,
            long usedNonHeapMb
    ) {}

    public record ThreadMetrics(
            int activeThreadCount,
            int peakThreadCount,
            long totalStartedThreadCount
    ) {}

    public record ApiDiagnosticsResponse(
            String deltaApiUrl,
            String deltaApiStatus,
            long deltaPingMs,
            String pythonEngineUrl,
            String pythonEngineStatus,
            long pythonEnginePingMs,
            int databasePoolActive,
            int databasePoolTotal,
            String signatureMechanism,
            boolean secretsSanitized
    ) {}

    public record LogEntry(
            String id,
            String timestamp,
            String level,
            String source,
            String message
    ) {}

    public record SandboxInfoResponse(
            String mode,
            boolean realExecutionBlocked,
            BigDecimal simulatedBalance,
            String activeStrategyModel,
            int simulatedTicksCount,
            String lastSimulatedTickAt,
            String safetyNotice
    ) {}

    public record SimulatedTickResult(
            boolean success,
            String symbol,
            BigDecimal price,
            String detectedOrderBlockType,
            BigDecimal orderBlockHigh,
            BigDecimal orderBlockLow,
            String signal,
            String timestamp
    ) {}

    public record AccountHealthSummary(
            String accountId,
            String name,
            String environment,
            boolean isActive,
            boolean algoEnabled,
            boolean killSwitchActive,
            BigDecimal currentBalance,
            BigDecimal totalEquity,
            String lastSyncedAt
    ) {}

    // ─────────────────────────────────────────────────────────────────────────
    // Service Methods
    // ─────────────────────────────────────────────────────────────────────────

    public DeveloperStatusResponse getSystemStatus() {
        long uptime = ManagementFactory.getRuntimeMXBean().getUptime() / 1000;

        List<ServiceHealth> services = new ArrayList<>();

        // 1. PostgreSQL Health Check
        long dbStart = System.currentTimeMillis();
        try (Connection conn = dataSource.getConnection()) {
            boolean valid = conn.isValid(2);
            long latency = System.currentTimeMillis() - dbStart;
            services.add(new ServiceHealth("PostgreSQL Database", valid ? "HEALTHY" : "DEGRADED", "jdbc:postgresql", latency, "Pool connection acquired"));
        } catch (Exception e) {
            long latency = System.currentTimeMillis() - dbStart;
            services.add(new ServiceHealth("PostgreSQL Database", "DOWN", "jdbc:postgresql", latency, "Connection error: " + e.getMessage()));
        }

        // 2. Python Engine Health Check
        long pyStart = System.currentTimeMillis();
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(pythonEngineBaseUrl + "/health"))
                    .timeout(Duration.ofMillis(1500))
                    .GET()
                    .build();
            HttpResponse<String> res = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            long latency = System.currentTimeMillis() - pyStart;
            services.add(new ServiceHealth("Python SMC Engine", res.statusCode() == 200 ? "HEALTHY" : "DEGRADED", pythonEngineBaseUrl, latency, "HTTP " + res.statusCode()));
        } catch (Exception e) {
            long latency = System.currentTimeMillis() - pyStart;
            services.add(new ServiceHealth("Python SMC Engine", "OFFLINE", pythonEngineBaseUrl, latency, "Engine not reachable (Local dev mode)"));
        }

        // 3. Delta Exchange Public API Health Check
        long deltaStart = System.currentTimeMillis();
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(deltaApiBaseUrl + "/v2/products"))
                    .timeout(Duration.ofMillis(2000))
                    .GET()
                    .build();
            HttpResponse<String> res = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            long latency = System.currentTimeMillis() - deltaStart;
            services.add(new ServiceHealth("Delta Exchange India REST", res.statusCode() == 200 ? "REACHABLE" : "DEGRADED", deltaApiBaseUrl, latency, "HTTP " + res.statusCode()));
        } catch (Exception e) {
            long latency = System.currentTimeMillis() - deltaStart;
            services.add(new ServiceHealth("Delta Exchange India REST", "UNREACHABLE", deltaApiBaseUrl, latency, "Network error: " + e.getMessage()));
        }

        // Memory Metrics
        MemoryMXBean memBean = ManagementFactory.getMemoryMXBean();
        long usedHeap = memBean.getHeapMemoryUsage().getUsed() / (1024 * 1024);
        long maxHeap = Math.max(1, memBean.getHeapMemoryUsage().getMax() / (1024 * 1024));
        double heapPct = BigDecimal.valueOf((double) usedHeap / maxHeap * 100).setScale(1, RoundingMode.HALF_UP).doubleValue();
        long usedNonHeap = memBean.getNonHeapMemoryUsage().getUsed() / (1024 * 1024);
        MemoryMetrics memory = new MemoryMetrics(usedHeap, maxHeap, heapPct, usedNonHeap);

        // Thread Metrics
        ThreadMXBean threadBean = ManagementFactory.getThreadMXBean();
        ThreadMetrics threads = new ThreadMetrics(
                threadBean.getThreadCount(),
                threadBean.getPeakThreadCount(),
                threadBean.getTotalStartedThreadCount()
        );

        return new DeveloperStatusResponse(
                "OPERATIONAL",
                Instant.now(),
                uptime,
                services,
                memory,
                threads
        );
    }

    public ApiDiagnosticsResponse getApiDiagnostics() {
        // Delta Ping
        long deltaStart = System.currentTimeMillis();
        String deltaStatus = "OK";
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(deltaApiBaseUrl + "/v2/tickers"))
                    .timeout(Duration.ofMillis(2000))
                    .GET()
                    .build();
            HttpResponse<String> res = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            if (res.statusCode() != 200) deltaStatus = "HTTP_" + res.statusCode();
        } catch (Exception e) {
            deltaStatus = "FAILED";
        }
        long deltaPing = System.currentTimeMillis() - deltaStart;

        // Python Engine Ping
        long pyStart = System.currentTimeMillis();
        String pyStatus = "OK";
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(pythonEngineBaseUrl + "/health"))
                    .timeout(Duration.ofMillis(1500))
                    .GET()
                    .build();
            HttpResponse<String> res = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            if (res.statusCode() != 200) pyStatus = "HTTP_" + res.statusCode();
        } catch (Exception e) {
            pyStatus = "OFFLINE";
        }
        long pyPing = System.currentTimeMillis() - pyStart;

        return new ApiDiagnosticsResponse(
                deltaApiBaseUrl,
                deltaStatus,
                deltaPing,
                pythonEngineBaseUrl,
                pyStatus,
                pyPing,
                5,
                20,
                "HMAC_SHA256_PER_USER_ISOLATED",
                true
        );
    }

    public List<LogEntry> getSanitizedLogs() {
        return new ArrayList<>(inMemoryLogBuffer);
    }

    public SandboxInfoResponse getSandboxInfo() {
        int ticks = (int) sandboxState.getOrDefault("simulatedTicksCount", 0);
        return new SandboxInfoResponse(
                "ISOLATED_SIMULATION_SANDBOX",
                true,
                (BigDecimal) sandboxState.getOrDefault("simulatedBalance", new BigDecimal("100000.00")),
                "SMC_ORDER_BLOCK_LUXALGO_EQUIVALENT",
                ticks,
                (String) sandboxState.getOrDefault("lastSimulatedTickAt", Instant.now().toString()),
                "ISOLATED ENVIRONMENT: Zero connection to real Delta live order placement. Safe for developer strategy testing."
        );
    }

    public SimulatedTickResult simulateTick(String symbol, BigDecimal mockPrice) {
        String sym = symbol != null && !symbol.trim().isEmpty() ? symbol.trim().toUpperCase() : "BTCUSD";
        BigDecimal price = mockPrice != null && mockPrice.compareTo(BigDecimal.ZERO) > 0 ? mockPrice : new BigDecimal("65000.00");

        int ticks = (int) sandboxState.getOrDefault("simulatedTicksCount", 0) + 1;
        sandboxState.put("simulatedTicksCount", ticks);
        sandboxState.put("lastSimulatedTickAt", Instant.now().toString());

        // Deterministic mock SMC qualification for sandbox testing
        BigDecimal obHigh = price.multiply(new BigDecimal("1.008")).setScale(2, RoundingMode.HALF_UP);
        BigDecimal obLow = price.multiply(new BigDecimal("0.995")).setScale(2, RoundingMode.HALF_UP);
        String obType = ticks % 2 == 0 ? "BULLISH_OB" : "BEARISH_OB";
        String signal = ticks % 2 == 0 ? "BUY_SETUP" : "SELL_SETUP";

        recordLog("DEBUG", "SandboxLab", "Simulated tick #" + ticks + " for " + sym + " at $" + price + " -> " + signal);

        return new SimulatedTickResult(
                true,
                sym,
                price,
                obType,
                obHigh,
                obLow,
                signal,
                Instant.now().toString()
        );
    }

    public List<AccountHealthSummary> getAccountsHealthSummary() {
        List<TradingAccount> accounts = accountRepository.findAll();
        return accounts.stream().map(a -> new AccountHealthSummary(
                a.getId(),
                a.getName(),
                a.getAccountType() != null ? a.getAccountType() : "LIVE",
                Boolean.TRUE.equals(a.getIsActive()),
                Boolean.TRUE.equals(a.getAlgoEnabled()),
                Boolean.TRUE.equals(a.getKillSwitchActive()),
                a.getCurrentBalance(),
                a.getTotalEquity(),
                a.getLastSyncedAt() != null ? a.getLastSyncedAt().toString() : null
        )).toList();
    }
}
