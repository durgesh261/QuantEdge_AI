package com.quantedge.account.repository;

import com.quantedge.account.entity.TradingAccount;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface TradingAccountRepository extends JpaRepository<TradingAccount, String> {

    List<TradingAccount> findByUserId(String userId);

    Optional<TradingAccount> findByIdAndUserId(String id, String userId);
}
