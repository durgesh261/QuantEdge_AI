package com.quantedge.ai.repository;

import com.quantedge.ai.entity.AiSignalEnrichment;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface AiSignalEnrichmentRepository extends JpaRepository<AiSignalEnrichment, String> {

    @Query("SELECT e FROM AiSignalEnrichment e WHERE e.setupId = :setupId AND e.tradingAccount.id = :accountId ORDER BY e.generatedAt DESC")
    List<AiSignalEnrichment> findBySetupIdAndTradingAccountId(@Param("setupId") String setupId, @Param("accountId") String accountId);

    @Query("SELECT e FROM AiSignalEnrichment e WHERE e.setupId = :setupId ORDER BY e.generatedAt DESC")
    List<AiSignalEnrichment> findBySetupId(@Param("setupId") String setupId);

    @Query("SELECT e FROM AiSignalEnrichment e WHERE e.tradingAccount.id = :accountId ORDER BY e.generatedAt DESC")
    List<AiSignalEnrichment> findByTradingAccountIdOrderByGeneratedAtDesc(@Param("accountId") String accountId);

    @Query("SELECT e FROM AiSignalEnrichment e WHERE e.tradingAccount.id = :accountId AND e.symbol = :symbol ORDER BY e.generatedAt DESC")
    List<AiSignalEnrichment> findByTradingAccountIdAndSymbolOrderByGeneratedAtDesc(@Param("accountId") String accountId, @Param("symbol") String symbol);

    @Query("SELECT e FROM AiSignalEnrichment e WHERE e.setupId IN :setupIds AND e.tradingAccount.id = :accountId ORDER BY e.generatedAt DESC")
    List<AiSignalEnrichment> findBySetupIdInAndTradingAccountId(@Param("setupIds") List<String> setupIds, @Param("accountId") String accountId);
}
