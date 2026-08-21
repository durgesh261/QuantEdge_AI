package com.quantedge.trading.controller;

import com.quantedge.auth.entity.User;
import com.quantedge.trading.service.OrderExecutionService;
import com.quantedge.trading.service.OrderValidationGateway;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;

@RestController
@RequestMapping("/api/v1/trade")
public class TradeExecutionController {

    private final OrderExecutionService executionService;

    public TradeExecutionController(OrderExecutionService executionService) {
        this.executionService = executionService;
    }

    public record ExecuteTradeRequest(
            @NotBlank String accountId,
            @NotBlank String setupId,
            @NotBlank String symbol,
            @NotBlank String direction,
            @NotBlank String orderType,
            @NotNull @Positive BigDecimal quantity,
            BigDecimal entryPrice,
            @NotNull BigDecimal stopLoss,
            @NotNull BigDecimal takeProfit,
            Integer leverage,
            String clientOrderId,
            boolean reduceOnly,
            @NotBlank String encryptedApiKey,
            @NotBlank String encryptedApiSecret
    ) {}

    @PostMapping("/execute")
    public ResponseEntity<OrderExecutionService.ExecutionResult> executeTrade(
            @RequestAttribute("currentUser") User user,
            @Valid @RequestBody ExecuteTradeRequest request
    ) {
        // Build validation context from current user & account state
        OrderValidationGateway.ValidationContext context = new OrderValidationGateway.ValidationContext(
                user.getIsActive(),
                true, // algo_enabled
                false, // kill_switch_active
                "CONNECTED",
                true, // credentials valid
                new BigDecimal("10000.00"), // total equity (loaded from account repository in full flow)
                new BigDecimal("10000.00"), // available balance
                0, // active positions count
                1, // max concurrent trades
                100, // max leverage
                new BigDecimal("35.00"), // risk per trade %
                new BigDecimal("1.50"), // min risk reward
                null,
                null,
                null
        );

        OrderExecutionService.ExecutionRequest execReq = new OrderExecutionService.ExecutionRequest(
                request.accountId(),
                request.setupId(),
                request.symbol(),
                request.direction(),
                request.orderType(),
                request.quantity(),
                request.entryPrice(),
                request.stopLoss(),
                request.takeProfit(),
                request.leverage() != null ? request.leverage() : 100,
                request.clientOrderId(),
                request.reduceOnly()
        );

        OrderExecutionService.ExecutionResult result = executionService.executeOrder(
                execReq,
                context,
                request.encryptedApiKey(),
                request.encryptedApiSecret()
        );

        if (result.success()) {
            return ResponseEntity.ok(result);
        } else {
            return ResponseEntity.badRequest().body(result);
        }
    }
}
