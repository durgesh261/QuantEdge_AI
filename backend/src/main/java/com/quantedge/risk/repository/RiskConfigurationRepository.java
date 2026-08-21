package com.quantedge.risk.repository;

import com.quantedge.risk.entity.RiskConfiguration;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface RiskConfigurationRepository extends JpaRepository<RiskConfiguration, String> {

    Optional<RiskConfiguration> findByTradingAccountId(String tradingAccountId);
}
