package com.quantedge.ai.controller;

import com.quantedge.ai.dto.AiEnrichmentDto;
import com.quantedge.ai.service.AiEnrichmentService;
import com.quantedge.ai.service.CombinedDecisionEngine;
import com.quantedge.auth.entity.User;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * REST API Controller for AI Intelligence & Signal Enrichment.
 */
@RestController
@RequestMapping
public class AiIntelligenceController {

    private final AiEnrichmentService enrichmentService;

    public AiIntelligenceController(AiEnrichmentService enrichmentService) {
        this.enrichmentService = enrichmentService;
    }

    /**
     * Retrieves AI intelligence enrichment for a specific setup ID.
     */
    @GetMapping({"/v1/trade/signals/{setupId}/intelligence", "/v1/ai/enrichments/{setupId}"})
    public ResponseEntity<AiEnrichmentDto> getSetupIntelligence(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @PathVariable("setupId") String setupId,
            @RequestParam(value = "accountId", required = false) String accountId
    ) {
        User effectiveUser = user != null ? user : requestUser;
        AiEnrichmentDto dto = enrichmentService.getEnrichmentBySetupId(effectiveUser, setupId, accountId);
        return ResponseEntity.ok(dto);
    }

    /**
     * Retrieves recent AI signal enrichments for an account.
     */
    @GetMapping("/v1/ai/enrichments")
    public ResponseEntity<List<AiEnrichmentDto>> getEnrichments(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId,
            @RequestParam(value = "symbol", required = false) String symbol,
            @RequestParam(value = "limit", required = false, defaultValue = "50") Integer limit
    ) {
        User effectiveUser = user != null ? user : requestUser;
        List<AiEnrichmentDto> list = enrichmentService.getEnrichmentsByAccount(effectiveUser, accountId, symbol, limit);
        return ResponseEntity.ok(list);
    }

    public record BulkIntelligenceRequest(
            List<String> setupIds,
            String accountId
    ) {}

    /**
     * Retrieves AI signal enrichments in bulk for a list of setup IDs.
     */
    @PostMapping({"/v1/ai/enrichments/bulk", "/v1/trade/signals/intelligence/bulk"})
    public ResponseEntity<Map<String, AiEnrichmentDto>> getBulkSetupIntelligence(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestBody BulkIntelligenceRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        List<String> ids = request != null && request.setupIds() != null ? request.setupIds() : List.of();
        String accountId = request != null ? request.accountId() : null;
        Map<String, AiEnrichmentDto> map = enrichmentService.getBulkEnrichments(effectiveUser, ids, accountId);
        return ResponseEntity.ok(map);
    }

    public record DecisionEvaluationRequest(
            String setupId,
            String accountId,
            boolean killSwitchActive,
            boolean algoEnabled
    ) {}

    /**
     * Evaluates a setup through the complete SMC + AI + Risk decision pipeline.
     * Returns the combined decision with full audit trail.
     */
    @PostMapping({"/v1/ai/decisions/evaluate", "/v1/trade/signals/{setupId}/decision"})
    public ResponseEntity<CombinedDecisionEngine.DecisionResult> evaluateDecision(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @PathVariable(value = "setupId", required = false) String pathSetupId,
            @RequestBody DecisionEvaluationRequest request
    ) {
        User effectiveUser = user != null ? user : requestUser;
        String setupId = request.setupId() != null ? request.setupId() : pathSetupId;
        String accountId = request.accountId();
        boolean killSwitch = request.killSwitchActive();
        boolean algoEnabled = request.algoEnabled();

        CombinedDecisionEngine.DecisionResult result = enrichmentService.evaluateSetupDecision(
                effectiveUser, setupId, accountId, killSwitch, algoEnabled
        );
        return ResponseEntity.ok(result);
    }

    /**
     * Retrieves AI decision audit history for an account.
     */
    @GetMapping("/v1/ai/decisions/audit")
    public ResponseEntity<List<com.quantedge.ai.entity.AiDecisionAudit>> getDecisionAudit(
            @AuthenticationPrincipal User user,
            @RequestAttribute(value = "currentUser", required = false) User requestUser,
            @RequestParam(value = "accountId", required = false) String accountId,
            @RequestParam(value = "limit", required = false, defaultValue = "100") Integer limit
    ) {
        User effectiveUser = user != null ? user : requestUser;
        // TODO: Implement audit service call
        return ResponseEntity.ok(List.of());
    }
}
