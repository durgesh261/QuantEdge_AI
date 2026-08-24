package com.quantedge.ai.repository;

import com.quantedge.ai.entity.AiDecisionAudit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

@Repository
public interface AiDecisionAuditRepository extends JpaRepository<AiDecisionAudit, String> {

    @Query("SELECT a FROM AiDecisionAudit a WHERE a.setupId = :setupId ORDER BY a.decisionTimestamp DESC")
    List<AiDecisionAudit> findBySetupIdOrderByDecisionTimestampDesc(@Param("setupId") String setupId);

    @Query("SELECT a FROM AiDecisionAudit a WHERE a.tradingAccount.id = :accountId ORDER BY a.decisionTimestamp DESC")
    List<AiDecisionAudit> findByTradingAccountIdOrderByDecisionTimestampDesc(@Param("accountId") String accountId);

    @Query("SELECT a FROM AiDecisionAudit a WHERE a.tradingAccount.id = :accountId AND a.decisionTimestamp BETWEEN :from AND :to ORDER BY a.decisionTimestamp DESC")
    List<AiDecisionAudit> findByTradingAccountIdAndDecisionTimestampBetween(
            @Param("accountId") String accountId,
            @Param("from") Instant from,
            @Param("to") Instant to
    );

    @Query("SELECT a FROM AiDecisionAudit a WHERE a.combinedDecision = :decision ORDER BY a.decisionTimestamp DESC")
    List<AiDecisionAudit> findByCombinedDecision(@Param("decision") String decision);

    @Query("SELECT a FROM AiDecisionAudit a WHERE a.symbol = :symbol ORDER BY a.decisionTimestamp DESC")
    List<AiDecisionAudit> findBySymbolOrderByDecisionTimestampDesc(@Param("symbol") String symbol);

    Optional<AiDecisionAudit> findBySetupIdAndDecisionTimestamp(String setupId, Instant timestamp);
}