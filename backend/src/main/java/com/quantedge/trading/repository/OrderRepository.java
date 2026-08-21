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

    boolean existsBySetupIdAndStatusIn(String setupId, Collection<String> statuses);

    List<Order> findByTradingAccountIdAndStatusIn(String tradingAccountId, Collection<String> statuses);

    int countByTradingAccountIdAndStatusIn(String tradingAccountId, Collection<String> statuses);
}
