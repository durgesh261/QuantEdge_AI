package com.quantedge.trading.controller;

import com.quantedge.auth.entity.User;
import com.quantedge.trading.entity.Order;
import com.quantedge.trading.service.OrderExecutionService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v1/trade")
public class TradeExecutionController {

    private final OrderExecutionService executionService;

    public TradeExecutionController(OrderExecutionService executionService) {
        this.executionService = executionService;
    }

    /**
     * Frontend request to execute a live trade setup.
     * Note: Frontend never supplies API credentials, fake balances, or fabricated TP/SL.
     * All authoritative state and credentials are resolved server-side.
     */
    public record ExecuteTradeRequest(
            @NotBlank String accountId,
            @NotBlank String setupId,
            String clientOrderId,
            Boolean reduceOnly
    ) {}

    public record KillSwitchRequest(
            String accountId,
            String reason
    ) {}

    public record AlgoToggleRequest(
            String accountId,
            boolean enabled
    ) {}

    @PostMapping("/execute")
    public ResponseEntity<OrderExecutionService.ExecutionResult> executeTrade(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @Valid @RequestBody ExecuteTradeRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        String userId = effectiveUser != null ? effectiveUser.getId() : null;

        OrderExecutionService.ExecutionCommand command = new OrderExecutionService.ExecutionCommand(
                userId,
                request.accountId(),
                request.setupId(),
                request.clientOrderId(),
                request.reduceOnly()
        );

        OrderExecutionService.ExecutionResult result = executionService.executeAuthoritativeOrder(command);

        if (result.success()) {
            return ResponseEntity.ok(result);
        } else {
            return ResponseEntity.badRequest().body(result);
        }
    }

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

    @GetMapping("/active")
    public ResponseEntity<List<Order>> getActiveOrders(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        String userId = effectiveUser != null ? effectiveUser.getId() : null;

        List<Order> orders = executionService.getActiveOrders(userId, accountId);
        return ResponseEntity.ok(orders);
    }

    @GetMapping("/history")
    public ResponseEntity<List<Order>> getOrderHistory(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        String userId = effectiveUser != null ? effectiveUser.getId() : null;

        List<Order> orders = executionService.getOrderHistory(userId, accountId);
        return ResponseEntity.ok(orders);
    }
}
