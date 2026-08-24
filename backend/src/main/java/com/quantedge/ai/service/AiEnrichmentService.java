package com.quantedge.ai.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.ai.dto.AiEnrichmentDto;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.ai.repository.AiSignalEnrichmentRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.common.exception.ResourceNotFoundException;
import com.quantedge.risk.entity.RiskConfiguration;
import com.quantedge.risk.repository.RiskConfigurationRepository;
import com.quantedge.strategy.entity.StrategySetupRecord;
import com.quantedge.strategy.repository.StrategySetupRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * Service managing AI Signal Enrichments with strict tenant isolation.
 */
@Service
public class AiEnrichmentService {

    private static final Logger log = LoggerFactory.getLogger(AiEnrichmentService.class);

    private final AiSignalEnrichmentRepository enrichmentRepository;
    private final TradingAccountRepository accountRepository;
    private final StrategySetupRepository setupRepository;
    private final RiskConfigurationRepository riskConfigRepository;
    private final AiIntelligenceEngine intelligenceEngine;
    private final AiDecisionAuditService auditService;
    private final CombinedDecisionEngine decisionEngine;

    public AiEnrichmentService(
            AiSignalEnrichmentRepository enrichmentRepository,
            TradingAccountRepository accountRepository,
            StrategySetupRepository setupRepository,
            RiskConfigurationRepository riskConfigRepository,
            AiIntelligenceEngine intelligenceEngine,
            AiDecisionAuditService auditService,
            CombinedDecisionEngine decisionEngine
    ) {
        this.enrichmentRepository = enrichmentRepository;
        this.accountRepository = accountRepository;
        this.setupRepository = setupRepository;
        this.riskConfigRepository = riskConfigRepository;
        this.intelligenceEngine = intelligenceEngine;
        this.auditService = auditService;
        this.decisionEngine = decisionEngine;
    }

    /**
     * Resolves trading account optionally without throwing if not configured.
     */
    public java.util.Optional<TradingAccount> findAuthorizedAccount(User user, String accountId) {
        if (user == null || user.getId() == null) {
            return java.util.Optional.empty();
        }

        if (accountId != null && !accountId.isBlank()) {
            TradingAccount account = accountRepository.findById(accountId).orElse(null);
            if (account != null && account.getUser() != null && user.getId().equals(account.getUser().getId())) {
                return java.util.Optional.of(account);
            }
            return java.util.Optional.empty();
        }

        List<TradingAccount> accounts = accountRepository.findByUserId(user.getId());
        if (accounts.isEmpty()) {
            return java.util.Optional.empty();
        }

        return accounts.stream()
                .filter(a -> Boolean.TRUE.equals(a.getIsActive()))
                .findFirst()
                .or(() -> java.util.Optional.of(accounts.getFirst()));
    }

    /**
     * Resolves trading account and verifies authenticated user ownership.
     */
    private TradingAccount resolveAndVerifyAccount(User user, String accountId) {
        if (user == null) {
            throw new AccessDeniedException("Unauthenticated: User context missing");
        }

        if (accountId != null && !accountId.isBlank()) {
            TradingAccount account = accountRepository.findById(accountId)
                    .orElseThrow(() -> new ResourceNotFoundException("Trading account not found: " + accountId));

            if (account.getUser() == null || !user.getId().equals(account.getUser().getId())) {
                log.warn("IDOR attempt detected in AI Enrichment: User {} tried to access Account {} owned by {}",
                        user.getId(), account.getId(), account.getUser() != null ? account.getUser().getId() : "null");
                throw new AccessDeniedException("Access denied: You do not own trading account " + account.getId());
            }
            return account;
        }

        List<TradingAccount> accounts = accountRepository.findByUserId(user.getId());
        TradingAccount account = accounts.stream()
                .filter(a -> Boolean.TRUE.equals(a.getIsActive()))
                .findFirst()
                .orElseGet(() -> accounts.isEmpty() ? null : accounts.getFirst());

        if (account == null) {
            throw new ResourceNotFoundException("No trading account found for user " + user.getId());
        }

        if (account.getUser() == null || !user.getId().equals(account.getUser().getId())) {
            log.warn("IDOR attempt detected in AI Enrichment: User {} tried to access Account {} owned by {}",
                    user.getId(), account.getId(), account.getUser() != null ? account.getUser().getId() : "null");
            throw new AccessDeniedException("Access denied: You do not own trading account " + account.getId());
        }

        return account;
    }

    /**
     * Generates and persists AI enrichment for a deterministic strategy setup.
     */
    @Transactional
    public AiEnrichmentDto enrichAndSave(TradingAccount account, StrategySetupRecord setup) {
        if (setup == null) {
            throw new IllegalArgumentException("Setup record cannot be null");
        }

        AiSignalEnrichment enrichment = intelligenceEngine.evaluate(account, setup);
        AiSignalEnrichment saved = enrichmentRepository.save(enrichment);
        return AiEnrichmentDto.fromEntity(saved);
    }

    /**
     * Retrieves AI intelligence for a setup ID ensuring tenant ownership.
     */
    @Transactional(readOnly = true)
    public AiEnrichmentDto getEnrichmentBySetupId(User user, String setupId, String accountId) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);

        List<AiSignalEnrichment> enrichments = enrichmentRepository.findBySetupIdAndTradingAccountId(setupId, account.getId());
        if (enrichments.isEmpty()) {
            // If not yet enriched in database, attempt on-the-fly evaluation if setup exists for this account
            StrategySetupRecord setup = setupRepository.findBySetupId(setupId)
                    .orElseThrow(() -> new ResourceNotFoundException("Strategy setup not found: " + setupId));

            if (setup.getTradingAccount() == null || !account.getId().equals(setup.getTradingAccount().getId())) {
                throw new AccessDeniedException("Access denied: Setup does not belong to your account");
            }

            AiSignalEnrichment enrichment = intelligenceEngine.evaluate(account, setup);
            return AiEnrichmentDto.fromEntity(enrichment);
        }

        return AiEnrichmentDto.fromEntity(enrichments.getFirst());
    }

    /**
     * Retrieves all AI enrichments for the user's account.
     */
    @Transactional(readOnly = true)
    public List<AiEnrichmentDto> getEnrichmentsByAccount(User user, String accountId, String symbol, int limit) {
        java.util.Optional<TradingAccount> accountOpt = findAuthorizedAccount(user, accountId);
        if (accountOpt.isEmpty()) {
            return java.util.Collections.emptyList();
        }
        TradingAccount account = accountOpt.get();

        List<AiSignalEnrichment> list;
        if (symbol != null && !symbol.isBlank()) {
            list = enrichmentRepository.findByTradingAccountIdAndSymbolOrderByGeneratedAtDesc(account.getId(), symbol.trim().toUpperCase());
        } else {
            list = enrichmentRepository.findByTradingAccountIdOrderByGeneratedAtDesc(account.getId());
        }

        return list.stream()
                .limit(limit > 0 ? limit : 100)
                .map(AiEnrichmentDto::fromEntity)
                .toList();
    }

    /**
     * Retrieves AI intelligence in bulk for a list of setup IDs.
     * Uses a single database query to fetch all enrichments, then evaluates missing ones on-the-fly.
     */
    @Transactional(readOnly = true)
    public java.util.Map<String, AiEnrichmentDto> getBulkEnrichments(User user, List<String> setupIds, String accountId) {
        if (setupIds == null || setupIds.isEmpty()) {
            return java.util.Collections.emptyMap();
        }
        java.util.Optional<TradingAccount> accountOpt = findAuthorizedAccount(user, accountId);
        if (accountOpt.isEmpty()) {
            return java.util.Collections.emptyMap();
        }
        TradingAccount account = accountOpt.get();
        java.util.Map<String, AiEnrichmentDto> resultMap = new java.util.HashMap<>();

        // Single bulk query for all setup IDs
        List<AiSignalEnrichment> existingEnrichments = enrichmentRepository.findBySetupIdInAndTradingAccountId(setupIds, account.getId());
        
        // Map existing enrichments by setupId (take the latest for each)
        java.util.Map<String, AiSignalEnrichment> existingMap = new java.util.LinkedHashMap<>();
        for (AiSignalEnrichment e : existingEnrichments) {
            if (!existingMap.containsKey(e.getSetupId())) {
                existingMap.put(e.getSetupId(), e);
            }
        }

        // Find which setup IDs need on-the-fly evaluation
        List<String> missingSetupIds = setupIds.stream()
                .filter(id -> !existingMap.containsKey(id))
                .toList();

        // Add existing enrichments to result
        for (String setupId : setupIds) {
            if (existingMap.containsKey(setupId)) {
                resultMap.put(setupId, AiEnrichmentDto.fromEntity(existingMap.get(setupId)));
            }
        }

        // Evaluate missing setups on-the-fly (still in bulk to avoid N+1 for setups)
        if (!missingSetupIds.isEmpty()) {
            List<StrategySetupRecord> missingSetups = setupRepository.findBySetupIdIn(missingSetupIds);
            for (StrategySetupRecord setup : missingSetups) {
                if (setup.getTradingAccount() != null && account.getId().equals(setup.getTradingAccount().getId())) {
                    try {
                        AiSignalEnrichment enrichment = intelligenceEngine.evaluate(account, setup);
                        resultMap.put(setup.getSetupId(), AiEnrichmentDto.fromEntity(enrichment));
                    } catch (Exception e) {
                        log.warn("Could not enrich setup {}: {}", setup.getSetupId(), e.getMessage());
                    }
                }
            }
        }

        return resultMap;
    }

    /**
     * Evaluates a setup through the complete SMC + AI + Risk decision pipeline.
     * Returns the combined decision with full audit trail.
     */
    @Transactional
    public CombinedDecisionEngine.DecisionResult evaluateSetupDecision(
            User user,
            String setupId,
            String accountId,
            boolean killSwitchActive,
            boolean algoEnabled
    ) {
        TradingAccount account = resolveAndVerifyAccount(user, accountId);
        
        StrategySetupRecord setup = setupRepository.findBySetupId(setupId)
                .orElseThrow(() -> new ResourceNotFoundException("Strategy setup not found: " + setupId));

        if (setup.getTradingAccount() == null || !account.getId().equals(setup.getTradingAccount().getId())) {
            throw new AccessDeniedException("Access denied: Setup does not belong to your account");
        }

        // Get or generate AI enrichment
        AiSignalEnrichment enrichment;
        List<AiSignalEnrichment> existing = enrichmentRepository.findBySetupIdAndTradingAccountId(setupId, account.getId());
        if (existing.isEmpty()) {
            enrichment = intelligenceEngine.evaluate(account, setup);
        } else {
            enrichment = existing.getFirst();
        }

        // Get risk configuration
        RiskConfiguration riskConfig = riskConfigRepository.findByTradingAccountId(account.getId())
                .orElseThrow(() -> new ResourceNotFoundException("Risk configuration not found for account"));

        // Run through combined decision engine
        return decisionEngine.evaluate(account, setup, enrichment, riskConfig, killSwitchActive, algoEnabled);
    }
}
