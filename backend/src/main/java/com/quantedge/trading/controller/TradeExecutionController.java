package com.quantedge.trading.controller;

import com.quantedge.auth.entity.User;
import com.quantedge.trading.dto.*;
import com.quantedge.trading.service.OrderExecutionService;
import com.quantedge.trading.service.TradingQueryService;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * REST API Controller for Phase 7 Backend Trading API.
 *
 * <h3>Security & Architecture Contract:</h3>
 * <ul>
 *   <li>All endpoints require authenticated user via JWT.</li>
 *   <li>Multi-tenant isolation is enforced on every query and mutation.</li>
 *   <li>Zero exposure of credentials, API secrets, or encryption keys in responses.</li>
 *   <li>OrderExecutionService remains the sole authority for execution operations.</li>
 * </ul>
 */
@RestController
@RequestMapping("/v1/trade")
public class TradeExecutionController {

    private final OrderExecutionService executionService;
    private final TradingQueryService queryService;

    public TradeExecutionController(
            OrderExecutionService executionService,
            TradingQueryService queryService
    ) {
        this.executionService = executionService;
        this.queryService = queryService;
    }

    public record KillSwitchRequest(
            String accountId,
            String reason
    ) {}

    public record AlgoToggleRequest(
            String accountId,
            boolean enabled
    ) {}

    // ─────────────────────────────────────────────────────────────────────────
    // 1. Trading System Status API
    // ─────────────────────────────────────────────────────────────────────────

    @GetMapping("/status")
    public ResponseEntity<TradingSystemStatusDto> getTradingStatus(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        TradingSystemStatusDto status = queryService.getTradingSystemStatus(effectiveUser, accountId);
        return ResponseEntity.ok(status);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2. Orders API
    // ─────────────────────────────────────────────────────────────────────────

    @GetMapping("/orders")
    public ResponseEntity<List<OrderDto>> getOrders(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId,
            @RequestParam(value = "symbol", required = false) String symbol,
            @RequestParam(value = "status", required = false) String status,
            @RequestParam(value = "limit", required = false, defaultValue = "100") Integer limit
    ) {
        User effectiveUser = user != null ? user : requestUser;
        List<OrderDto> orders = queryService.getOrders(effectiveUser, accountId, symbol, status, limit);
        return ResponseEntity.ok(orders);
    }

    @GetMapping("/orders/{orderId}")
    public ResponseEntity<OrderDto> getOrderById(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @PathVariable("orderId") String orderId,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        OrderDto order = queryService.getOrderById(effectiveUser, accountId, orderId);
        return ResponseEntity.ok(order);
    }

    @GetMapping("/active")
    public ResponseEntity<List<OrderDto>> getActiveOrders(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        List<OrderDto> orders = queryService.getOrders(effectiveUser, accountId, null, "OPEN", 100);
        return ResponseEntity.ok(orders);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 3. Positions API
    // ─────────────────────────────────────────────────────────────────────────

    @GetMapping("/positions")
    public ResponseEntity<List<PositionDto>> getPositions(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId,
            @RequestParam(value = "status", required = false, defaultValue = "OPEN") String status
    ) {
        User effectiveUser = user != null ? user : requestUser;
        List<PositionDto> positions = queryService.getPositions(effectiveUser, accountId, status);
        return ResponseEntity.ok(positions);
    }

    @GetMapping("/positions/{positionId}")
    public ResponseEntity<PositionDto> getPositionById(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @PathVariable("positionId") String positionId,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        PositionDto position = queryService.getPositionById(effectiveUser, accountId, positionId);
        return ResponseEntity.ok(position);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 4. Fills / Executions API
    // ─────────────────────────────────────────────────────────────────────────

    @GetMapping("/fills")
    public ResponseEntity<List<OrderFillDto>> getFills(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId,
            @RequestParam(value = "orderId", required = false) String orderId,
            @RequestParam(value = "symbol", required = false) String symbol,
            @RequestParam(value = "limit", required = false, defaultValue = "100") Integer limit
    ) {
        User effectiveUser = user != null ? user : requestUser;
        List<OrderFillDto> fills = queryService.getFills(effectiveUser, accountId, orderId, symbol, limit);
        return ResponseEntity.ok(fills);
    }

    @GetMapping("/fills/{fillId}")
    public ResponseEntity<OrderFillDto> getFillById(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @PathVariable("fillId") String fillId,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        OrderFillDto fill = queryService.getFillById(effectiveUser, accountId, fillId);
        return ResponseEntity.ok(fill);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 5. Trade History API
    // ─────────────────────────────────────────────────────────────────────────

    @GetMapping("/history")
    public ResponseEntity<List<TradeHistoryDto>> getTradeHistory(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId,
            @RequestParam(value = "limit", required = false, defaultValue = "100") Integer limit
    ) {
        User effectiveUser = user != null ? user : requestUser;
        List<TradeHistoryDto> history = queryService.getTradeHistory(effectiveUser, accountId, limit);
        return ResponseEntity.ok(history);
    }

    @GetMapping("/history/{tradeId}")
    public ResponseEntity<TradeHistoryDto> getTradeRecordById(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @PathVariable("tradeId") String tradeId,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        TradeHistoryDto trade = queryService.getTradeRecordById(effectiveUser, accountId, tradeId);
        return ResponseEntity.ok(trade);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 6. Strategy Setups / Signals API
    // ─────────────────────────────────────────────────────────────────────────

    @GetMapping("/signals")
    public ResponseEntity<List<SignalSetupDto>> getSignals(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId,
            @RequestParam(value = "state", required = false) String state,
            @RequestParam(value = "symbol", required = false) String symbol,
            @RequestParam(value = "limit", required = false, defaultValue = "100") Integer limit
    ) {
        User effectiveUser = user != null ? user : requestUser;
        List<SignalSetupDto> signals = queryService.getSignals(effectiveUser, accountId, state, symbol, limit);
        return ResponseEntity.ok(signals);
    }

    @GetMapping("/signals/{setupId}")
    public ResponseEntity<SignalSetupDto> getSignalBySetupId(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @PathVariable("setupId") String setupId,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        SignalSetupDto signal = queryService.getSignalBySetupId(effectiveUser, accountId, setupId);
        return ResponseEntity.ok(signal);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 7. Trading Operational Controls (Kill Switch & Algo Toggle)
    // ─────────────────────────────────────────────────────────────────────────

    @PostMapping("/kill-switch")
    public ResponseEntity<OrderExecutionService.ControlResponse> activateKillSwitch(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestBody(required = false) KillSwitchRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        String userId = effectiveUser != null ? effectiveUser.getId() : null;
        String accountId = request != null ? request.accountId() : null;
        String reason = request != null ? request.reason() : "Operator trigger";

        OrderExecutionService.ControlResponse response = executionService.activateKillSwitch(userId, accountId, reason);
        return ResponseEntity.ok(response);
    }

    @PostMapping("/kill-switch/reset")
    public ResponseEntity<OrderExecutionService.ControlResponse> resetKillSwitch(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestBody(required = false) KillSwitchRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        String userId = effectiveUser != null ? effectiveUser.getId() : null;
        String accountId = request != null ? request.accountId() : null;

        OrderExecutionService.ControlResponse response = executionService.resetKillSwitch(userId, accountId);
        return ResponseEntity.ok(response);
    }

    @PostMapping("/algo/toggle")
    public ResponseEntity<OrderExecutionService.ControlResponse> toggleAlgoTrading(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestBody AlgoToggleRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        String userId = effectiveUser != null ? effectiveUser.getId() : null;
        String accountId = request != null ? request.accountId() : null;

        OrderExecutionService.ControlResponse response = executionService.setAlgoEnabled(userId, accountId, request.enabled());
        if (response.success()) {
            return ResponseEntity.ok(response);
        } else {
            return ResponseEntity.badRequest().body(response);
        }
    }
}
