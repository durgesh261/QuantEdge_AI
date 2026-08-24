package com.quantedge.strategy.repository;

import com.quantedge.strategy.entity.StrategySetupRecord;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

@Repository
public interface StrategySetupRepository extends JpaRepository<StrategySetupRecord, String> {

    Optional<StrategySetupRecord> findBySetupId(String setupId);

    Optional<StrategySetupRecord> findBySetupIdAndTradingAccountId(String setupId, String tradingAccountId);

    @Query("SELECT s FROM StrategySetupRecord s WHERE s.setupId IN :setupIds")
    List<StrategySetupRecord> findBySetupIdIn(@Param("setupIds") List<String> setupIds);

    @Query("SELECT s FROM StrategySetupRecord s WHERE s.createdAt BETWEEN :from AND :to")
    List<StrategySetupRecord> findByCreatedAtBetween(@Param("from") Instant from, @Param("to") Instant to);

    java.util.List<StrategySetupRecord> findByTradingAccountIdOrderByCreatedAtDesc(String tradingAccountId);

    java.util.List<StrategySetupRecord> findByTradingAccountIdAndSetupStateOrderByCreatedAtDesc(String tradingAccountId, String setupState);

    java.util.List<StrategySetupRecord> findByTradingAccountIdAndSymbolOrderByCreatedAtDesc(String tradingAccountId, String symbol);

    java.util.List<StrategySetupRecord> findByTradingAccountIdAndSetupStateAndSymbolOrderByCreatedAtDesc(String tradingAccountId, String setupState, String symbol);
}
