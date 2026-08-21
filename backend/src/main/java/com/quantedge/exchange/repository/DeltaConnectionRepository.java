package com.quantedge.exchange.repository;

import com.quantedge.exchange.entity.DeltaConnection;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface DeltaConnectionRepository extends JpaRepository<DeltaConnection, String> {

    Optional<DeltaConnection> findByTradingAccountIdAndEnvironment(String tradingAccountId, String environment);

    Optional<DeltaConnection> findByTradingAccountId(String tradingAccountId);
}
