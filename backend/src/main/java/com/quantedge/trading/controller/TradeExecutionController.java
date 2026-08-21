package com.quantedge.trading.controller;

import com.quantedge.auth.entity.User;
import com.quantedge.trading.service.OrderExecutionService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

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
}
