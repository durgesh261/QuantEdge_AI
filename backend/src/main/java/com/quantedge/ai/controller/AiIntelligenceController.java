package com.quantedge.ai.controller;

import com.quantedge.ai.dto.AiEnrichmentDto;
import com.quantedge.ai.service.AiEnrichmentService;
import com.quantedge.auth.entity.User;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;

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
}
