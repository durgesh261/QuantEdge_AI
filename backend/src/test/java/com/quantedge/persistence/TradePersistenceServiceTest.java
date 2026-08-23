package com.quantedge.persistence;

import com.quantedge.account.entity.TradingAccount;
import com.quantedge.account.repository.TradingAccountRepository;
import com.quantedge.audit.entity.AuditLog;
import com.quantedge.audit.repository.AuditLogRepository;
import com.quantedge.auth.entity.User;
import com.quantedge.trading.entity.ActiveTradeLock;
import com.quantedge.trading.entity.TradeRecord;
import com.quantedge.trading.repository.ActiveTradeLockRepository;
import com.quantedge.trading.repository.TradeRecordRepository;
import com.quantedge.trading.service.TradePersistenceService;
import com.quantedge.trading.service.TradePersistenceService.TradeCloseRequest;
import com.quantedge.trading.service.TradePersistenceService.TradeCloseResult;
import com.quantedge.trading.service.TradePersistenceService.TradeLockException;
import com.quantedge.trading.service.TradePersistenceService.TradeNotFoundException;
import com.quantedge.trading.service.TradePersistenceService.TradeOpenRequest;
import com.quantedge.trading.service.TradePersistenceService.TradeOpenResult;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class TradePersistenceServiceTest {

    @Mock private ActiveTradeLockRepository lockRepository;
    @Mock private TradeRecordRepository tradeRecordRepository;
    @Mock private TradingAccountRepository accountRepository;
    @Mock private com.quantedge.trading.position.PositionRepository positionRepository;
    @Mock private AuditLogRepository auditLogRepository;

    private TradePersistenceService service;
    private User testUser;
    private TradingAccount testAccount;

    @BeforeEach
    void setUp() {
        service = new TradePersistenceService(
                lockRepository,
                tradeRecordRepository,
                accountRepository,
                positionRepository,
                auditLogRepository
        );

        testUser = new User();
        testUser.setId("usr-1");
        testUser.setEmail("trader@quantedge.io");
        testUser.setName("Trader");

        testAccount = new TradingAccount(testUser, "Test Live Account", "LIVE", "USDT");
        testAccount.setId("acct-1");
        testAccount.setCurrentBalance(new BigDecimal("10000.00"));
        testAccount.setAlgoEnabled(true);
        testAccount.setKillSwitchActive(false);
    }

    @Test
    @DisplayName("Fail-safe: Refuses trade open when kill switch is active")
    void testOpenTradeRefusesWhenKillSwitchActive() {
        testAccount.setKillSwitchActive(true);
        when(accountRepository.findById("acct-1")).thenReturn(Optional.of(testAccount));

        TradeOpenRequest req = new TradeOpenRequest(
                "acct-1", "setup-1", "BTCUSDT", "LONG",
                new BigDecimal("50000"), new BigDecimal("0.1"), 17,
                new BigDecimal("10000"), null, null, null, null, 1,
                new BigDecimal("35.00"), new BigDecimal("60.00")
        );

        TradeOpenResult result = service.openTrade(req);
        assertThat(result.success()).isFalse();
        assertThat(result.error()).isEqualTo("KILL_SWITCH_ACTIVE");
        verify(lockRepository, never()).save(any());
        verify(tradeRecordRepository, never()).save(any());
    }

    @Test
    @DisplayName("Fail-safe: Refuses trade open when algo is disabled")
    void testOpenTradeRefusesWhenAlgoDisabled() {
        testAccount.setAlgoEnabled(false);
        when(accountRepository.findById("acct-1")).thenReturn(Optional.of(testAccount));

        TradeOpenRequest req = new TradeOpenRequest(
                "acct-1", "setup-2", "BTCUSDT", "LONG",
                new BigDecimal("50000"), new BigDecimal("0.1"), 17,
                new BigDecimal("10000"), null, null, null, null, 1,
                new BigDecimal("35.00"), new BigDecimal("60.00")
        );

        TradeOpenResult result = service.openTrade(req);
        assertThat(result.success()).isFalse();
        assertThat(result.error()).isEqualTo("ALGO_DISABLED");
        verify(lockRepository, never()).save(any());
    }

    @Test
    @DisplayName("One-trade rule: Throws TradeLockException when active lock already exists")
    void testOpenTradeThrowsWhenLockExists() {
        when(accountRepository.findById("acct-1")).thenReturn(Optional.of(testAccount));

        ActiveTradeLock existingLock = new ActiveTradeLock(testAccount, "setup-old", "BTCUSDT");
        when(lockRepository.findActiveLockByAccountId("acct-1")).thenReturn(Optional.of(existingLock));

        TradeOpenRequest req = new TradeOpenRequest(
                "acct-1", "setup-new", "BTCUSDT", "LONG",
                new BigDecimal("50000"), new BigDecimal("0.1"), 17,
                new BigDecimal("10000"), null, null, null, null, 1,
                new BigDecimal("35.00"), new BigDecimal("60.00")
        );

        assertThatThrownBy(() -> service.openTrade(req))
                .isInstanceOf(TradeLockException.class)
                .hasMessageContaining("One-trade-at-a-time rule violated");
    }

    @Test
    @DisplayName("Idempotent open: Re-submitting same setupId returns existing trade record")
    void testOpenTradeIdempotentWhenSameSetupId() {
        when(accountRepository.findById("acct-1")).thenReturn(Optional.of(testAccount));

        TradeRecord existingRecord = new TradeRecord(
                testAccount, "setup-same", "BTCUSDT", "LONG",
                new BigDecimal("50000"), new BigDecimal("0.1"), 17,
                new BigDecimal("10000")
        );
        existingRecord.setId("tr-existing");
        when(tradeRecordRepository.findBySetupId("setup-same")).thenReturn(Optional.of(existingRecord));

        TradeOpenRequest req = new TradeOpenRequest(
                "acct-1", "setup-same", "BTCUSDT", "LONG",
                new BigDecimal("50000"), new BigDecimal("0.1"), 17,
                new BigDecimal("10000"), null, null, null, null, 1,
                new BigDecimal("35.00"), new BigDecimal("60.00")
        );

        TradeOpenResult result = service.openTrade(req);
        assertThat(result.success()).isTrue();
        assertThat(result.tradeRecordId()).isEqualTo("tr-existing");
    }

    @Test
    @DisplayName("Trade open persists TradeRecord and ActiveTradeLock atomically")
    void testOpenTradeSuccess() {
        when(accountRepository.findById("acct-1")).thenReturn(Optional.of(testAccount));
        when(lockRepository.findActiveLockByAccountId("acct-1")).thenReturn(Optional.empty());
        when(tradeRecordRepository.findBySetupId("setup-success")).thenReturn(Optional.empty());

        ActiveTradeLock savedLock = new ActiveTradeLock(testAccount, "setup-success", "BTCUSDT");
        savedLock.setId("lock-100");
        when(lockRepository.saveAndFlush(any(ActiveTradeLock.class))).thenReturn(savedLock);

        TradeRecord savedRecord = new TradeRecord(
                testAccount, "setup-success", "BTCUSDT", "LONG",
                new BigDecimal("50000"), new BigDecimal("0.1"), 17,
                new BigDecimal("10000")
        );
        savedRecord.setId("tr-100");
        when(tradeRecordRepository.saveAndFlush(any(TradeRecord.class))).thenReturn(savedRecord);

        TradeOpenRequest req = new TradeOpenRequest(
                "acct-1", "setup-success", "BTCUSDT", "LONG",
                new BigDecimal("50000"), new BigDecimal("0.1"), 17,
                new BigDecimal("10000"), null, null, null, null, 1,
                new BigDecimal("35.00"), new BigDecimal("60.00")
        );

        TradeOpenResult result = service.openTrade(req);
        assertThat(result.success()).isTrue();
        assertThat(result.tradeRecordId()).isEqualTo("tr-100");
        assertThat(result.lockId()).isEqualTo("lock-100");

        verify(lockRepository).saveAndFlush(any(ActiveTradeLock.class));
        verify(tradeRecordRepository).saveAndFlush(any(TradeRecord.class));
    }

    @Test
    @DisplayName("Trade close: Net P&L formula and balance compounding")
    void testCloseTradeNetPnlAndCompounding() {
        when(accountRepository.findById("acct-1")).thenReturn(Optional.of(testAccount));

        TradeRecord openRecord = new TradeRecord(
                testAccount, "setup-close", "BTCUSDT", "LONG",
                new BigDecimal("50000"), new BigDecimal("0.1"), 17,
                new BigDecimal("10000")
        );
        when(tradeRecordRepository.findBySetupId("setup-close")).thenReturn(Optional.of(openRecord));

        ActiveTradeLock activeLock = new ActiveTradeLock(testAccount, "setup-close", "BTCUSDT");
        when(lockRepository.findActiveLockByAccountId("acct-1")).thenReturn(Optional.of(activeLock));

        // gross=600, fees=72, funding=8, other=0 -> net = 520 -> post_balance = 10520
        TradeCloseRequest closeReq = new TradeCloseRequest(
                "acct-1", "setup-close",
                new BigDecimal("600"), new BigDecimal("72"),
                new BigDecimal("8"), BigDecimal.ZERO,
                new BigDecimal("56000"), "TAKE_PROFIT", null, "tp-ord"
        );

        TradeCloseResult result = service.closeTrade(closeReq);
        assertThat(result.success()).isTrue();
        assertThat(result.netPnl()).isEqualByComparingTo("520");
        assertThat(result.postTradeBalance()).isEqualByComparingTo("10520");

        // Verify lock is released
        assertThat(activeLock.getReleasedAt()).isNotNull();
        assertThat(activeLock.getReleaseReason()).isEqualTo("TAKE_PROFIT");

        // Verify account current balance updated
        assertThat(testAccount.getCurrentBalance()).isEqualByComparingTo("10520");
        verify(accountRepository).saveAndFlush(testAccount);
    }

    @Test
    @DisplayName("Trade close: Authoritative exchange balance override")
    void testCloseTradeExchangeBalanceOverride() {
        when(accountRepository.findById("acct-1")).thenReturn(Optional.of(testAccount));

        TradeRecord openRecord = new TradeRecord(
                testAccount, "setup-override", "BTCUSDT", "LONG",
                new BigDecimal("50000"), new BigDecimal("0.1"), 17,
                new BigDecimal("10000")
        );
        when(tradeRecordRepository.findBySetupId("setup-override")).thenReturn(Optional.of(openRecord));
        when(lockRepository.findActiveLockByAccountId("acct-1")).thenReturn(Optional.empty());

        BigDecimal authoritativeBalance = new BigDecimal("10955.50");

        TradeCloseRequest closeReq = new TradeCloseRequest(
                "acct-1", "setup-override",
                new BigDecimal("1000"), new BigDecimal("50"),
                BigDecimal.ZERO, BigDecimal.ZERO,
                new BigDecimal("58000"), "TAKE_PROFIT",
                authoritativeBalance, "tp-ord"
        );

        TradeCloseResult result = service.closeTrade(closeReq);
        assertThat(result.postTradeBalance()).isEqualByComparingTo(authoritativeBalance);
        assertThat(testAccount.getCurrentBalance()).isEqualByComparingTo(authoritativeBalance);
    }

    @Test
    @DisplayName("Trade close: Throws TradeNotFoundException when setupId not found")
    void testCloseTradeThrowsWhenNotFound() {
        when(tradeRecordRepository.findBySetupId("setup-missing")).thenReturn(Optional.empty());

        TradeCloseRequest closeReq = new TradeCloseRequest(
                "acct-1", "setup-missing",
                new BigDecimal("600"), new BigDecimal("72"),
                BigDecimal.ZERO, BigDecimal.ZERO,
                new BigDecimal("56000"), "TAKE_PROFIT", null, null
        );

        assertThatThrownBy(() -> service.closeTrade(closeReq))
                .isInstanceOf(TradeNotFoundException.class);
    }

    @Test
    @DisplayName("Capital allocation: 100% of post_trade_balance returned for next trade")
    void testGetNextTradeCapital() {
        when(tradeRecordRepository.findLatestPostTradeBalance("acct-1"))
                .thenReturn(Optional.of(new BigDecimal("10528.00")));

        BigDecimal capital = service.getNextTradeCapital("acct-1");
        assertThat(capital).isEqualByComparingTo("10528.00");
    }

    @Test
    @DisplayName("Force release lock creates audit log and marks lock released")
    void testForceReleaseLock() {
        when(accountRepository.findById("acct-1")).thenReturn(Optional.of(testAccount));

        ActiveTradeLock activeLock = new ActiveTradeLock(testAccount, "setup-stuck", "BTCUSDT");
        when(lockRepository.findActiveLockByAccountId("acct-1")).thenReturn(Optional.of(activeLock));

        service.forceReleaseLock("acct-1", "DELTA_RECONCILED");

        assertThat(activeLock.getReleasedAt()).isNotNull();
        assertThat(activeLock.getForceReleased()).isTrue();
        assertThat(activeLock.getReleaseReason()).isEqualTo("DELTA_RECONCILED");
        verify(lockRepository).saveAndFlush(activeLock);
        verify(auditLogRepository).save(any(AuditLog.class));
    }
}
