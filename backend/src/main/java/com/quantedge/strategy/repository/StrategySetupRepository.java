package com.quantedge.strategy.repository;

import com.quantedge.strategy.entity.StrategySetupRecord;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface StrategySetupRepository extends JpaRepository<StrategySetupRecord, String> {

    Optional<StrategySetupRecord> findBySetupId(String setupId);

    Optional<StrategySetupRecord> findBySetupIdAndTradingAccountId(String setupId, String tradingAccountId);
}
