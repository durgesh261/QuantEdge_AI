package com.quantedge.portfolio.repository;

import com.quantedge.portfolio.entity.Position;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface PositionRepository extends JpaRepository<Position, String> {

    List<Position> findByTradingAccountIdAndStatus(String tradingAccountId, String status);

    List<Position> findByTradingAccountId(String tradingAccountId);

    Optional<Position> findByTradingAccountIdAndSymbolAndStatus(String tradingAccountId, String symbol, String status);
}
