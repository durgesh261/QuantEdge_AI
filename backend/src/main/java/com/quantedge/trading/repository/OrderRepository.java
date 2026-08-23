package com.quantedge.trading.repository;

import com.quantedge.trading.entity.Order;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

@Repository
public interface OrderRepository extends JpaRepository<Order, String> {

    Optional<Order> findByClientOrderId(String clientOrderId);

    boolean existsByClientOrderId(String clientOrderId);

    Optional<Order> findByIdAndTradingAccountId(String id, String tradingAccountId);

    Optional<Order> findByClientOrderIdAndTradingAccountId(String clientOrderId, String tradingAccountId);

    boolean existsBySetupIdAndStatusIn(String setupId, Collection<String> statuses);

    List<Order> findByTradingAccountIdAndStatusIn(String tradingAccountId, Collection<String> statuses);

    List<Order> findByTradingAccountIdOrderByPlacedAtDesc(String tradingAccountId);

    List<Order> findByTradingAccountIdAndSymbolOrderByPlacedAtDesc(String tradingAccountId, String symbol);

    List<Order> findByTradingAccountIdAndStatusOrderByPlacedAtDesc(String tradingAccountId, String status);

    List<Order> findByTradingAccountIdAndSymbolAndStatusOrderByPlacedAtDesc(String tradingAccountId, String symbol, String status);

    int countByTradingAccountIdAndStatusIn(String tradingAccountId, Collection<String> statuses);
}
