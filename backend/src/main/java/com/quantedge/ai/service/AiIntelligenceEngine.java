package com.quantedge.ai.service;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.ai.entity.AiSignalEnrichment;
import com.quantedge.strategy.entity.StrategySetupRecord;

/**
 * Interface for AI Intelligence Evaluation Engines.
 */
public interface AiIntelligenceEngine {

    /**
     * Version identifier of this intelligence engine.
     */
    String getVersion();

    /**
     * Evaluates a deterministic SMC strategy setup and produces an enriched AI record.
     * Guaranteed not to modify the input StrategySetupRecord.
     */
    AiSignalEnrichment evaluate(TradingAccount account, StrategySetupRecord setup);
}
