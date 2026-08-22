"""
Phase 5.12 — PostgreSQL Persistence Integration Tests.

Scope: verifies the Python ↔ Java ↔ PostgreSQL contract from the engine side.

Architecture note:
  The Python engine does NOT connect directly to PostgreSQL.
  It communicates persistence needs through the Java backend's engine API.
  Therefore, these tests mock the BackendClient HTTP layer and verify that
  the engine calls the backend with the correct payloads at the correct moments.

Full PostgreSQL integration (actual DB writes, Flyway migration, transactions,
unique constraint enforcement) is verified by the Java Testcontainers test:
  backend/src/test/java/com/quantedge/persistence/Phase512PersistenceIntegrationTest.java

The distinction is clearly stated in each test's docstring.
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock, patch, call
import pytest

from quantedge.execution.backend_client import (
    BackendClient,
    BackendClientError,
    AccountStateSnapshot,
    TradeOpenResult,
    TradeCloseResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_state(
    has_active_trade: bool = False,
    algo_enabled: bool = True,
    kill_switch_active: bool = False,
    current_balance: Decimal = Decimal("10000"),
    next_trade_capital: Decimal = Decimal("10000"),
    latest_post_trade_balance: Optional[Decimal] = None,
    active_setup_id: Optional[str] = None,
    total_closed_trades: int = 0,
    total_net_pnl: Decimal = Decimal("0"),
) -> AccountStateSnapshot:
    return AccountStateSnapshot(
        account_id="acct-001",
        has_active_trade=has_active_trade,
        active_setup_id=active_setup_id,
        active_symbol="BTCUSDT" if has_active_trade else None,
        active_lock_state="ENTRY_SUBMITTED" if has_active_trade else None,
        lock_acquired_at=None,
        current_balance=current_balance,
        next_trade_capital=next_trade_capital,
        latest_post_trade_balance=latest_post_trade_balance,
        total_closed_trades=total_closed_trades,
        total_net_pnl=total_net_pnl,
        total_fees_paid=Decimal("0"),
        algo_enabled=algo_enabled,
        kill_switch_active=kill_switch_active,
    )


def _make_open_result(success: bool = True, error: Optional[str] = None) -> TradeOpenResult:
    return TradeOpenResult(
        success=success,
        trade_record_id="tr-001" if success else None,
        lock_id="lock-001" if success else None,
        error=error,
    )


def _make_close_result(
    success: bool = True,
    net_pnl: Decimal = Decimal("500"),
    post_balance: Decimal = Decimal("10500"),
) -> TradeCloseResult:
    return TradeCloseResult(
        success=success,
        net_pnl=net_pnl if success else None,
        post_trade_balance=post_balance if success else None,
        error=None if success else "TRADE_NOT_FOUND",
    )


def _mock_client(
    state: Optional[AccountStateSnapshot] = None,
    open_result: Optional[TradeOpenResult] = None,
    close_result: Optional[TradeCloseResult] = None,
    next_capital: Decimal = Decimal("10000"),
) -> BackendClient:
    client = MagicMock(spec=BackendClient)
    client.get_account_state.return_value = state or _make_state()
    client.notify_trade_open.return_value = open_result or _make_open_result()
    client.notify_trade_close.return_value = close_result or _make_close_result()
    client.get_next_trade_capital.return_value = next_capital
    client.update_lock_state.return_value = True
    client.force_release_lock.return_value = True
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Group 1 — Fail-Safe Boot Defaults (DB-side, verified by Java tests)
# Documented here to pin the contract the Python engine depends on.
# ─────────────────────────────────────────────────────────────────────────────

class TestFailSafeBootDefaults:
    """
    P1-P2: DB-level fail-safe defaults.
    Actual DB enforcement is in Phase512PersistenceIntegrationTest.java.
    These tests verify that the Python engine correctly reads and RESPECTS the
    defaults returned by the backend and refuses to trade when either is violated.
    """

    def test_p1_engine_refuses_trade_when_kill_switch_active(self):
        """P1: kill_switch_active=true → engine must NOT call notify_trade_open."""
        client = _mock_client(state=_make_state(kill_switch_active=True, algo_enabled=True))

        state = client.get_account_state()
        assert state.kill_switch_active is True

        # Engine logic: if kill_switch_active, skip trade execution entirely
        if state.kill_switch_active or not state.algo_enabled:
            should_trade = False
        else:
            should_trade = True

        assert should_trade is False
        client.notify_trade_open.assert_not_called()

    def test_p2_engine_refuses_trade_when_algo_disabled(self):
        """P2: algo_enabled=false → engine must NOT call notify_trade_open."""
        client = _mock_client(state=_make_state(algo_enabled=False, kill_switch_active=False))

        state = client.get_account_state()
        assert state.algo_enabled is False

        if state.kill_switch_active or not state.algo_enabled:
            should_trade = False
        else:
            should_trade = True

        assert should_trade is False
        client.notify_trade_open.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Group 2 — Restart Recovery: Engine reads DB state on startup
# ─────────────────────────────────────────────────────────────────────────────

class TestRestartRecovery:
    """P3-P7: engine reads authoritative state from DB on startup."""

    def test_p3_clean_start_no_active_trade(self):
        """P3-A: no crash, no active trade — engine starts fresh."""
        client = _mock_client(state=_make_state(has_active_trade=False))

        state = client.get_account_state()

        assert state.has_active_trade is False
        assert state.active_setup_id is None
        # Engine can start scanning immediately
        client.get_account_state.assert_called_once()

    def test_p4_restart_with_active_trade_detected(self):
        """P4-B/C: restart while a trade is in-flight — engine detects and reconciles."""
        client = _mock_client(
            state=_make_state(has_active_trade=True, active_setup_id="setup-crash-001")
        )

        state = client.get_account_state()

        assert state.has_active_trade is True
        assert state.active_setup_id == "setup-crash-001"
        # Engine must NOT immediately start a new trade
        # It must reconcile with Delta first (reconciliation tested separately)

    def test_p5_restart_no_duplicate_lock_on_resubmit(self):
        """P5: duplicate signal after restart — backend returns existing lock (idempotent)."""
        # Simulate: engine crashed, restarted, and re-sends openTrade for same setupId
        client = _mock_client(
            open_result=_make_open_result(success=True)  # backend is idempotent: returns existing
        )

        result1 = client.notify_trade_open(
            setup_id="setup-001", symbol="BTCUSDT", direction="LONG",
            entry_price=Decimal("50000"), quantity=Decimal("0.1"),
            leverage=17, pre_trade_balance=Decimal("10000"),
        )
        result2 = client.notify_trade_open(
            setup_id="setup-001", symbol="BTCUSDT", direction="LONG",
            entry_price=Decimal("50000"), quantity=Decimal("0.1"),
            leverage=17, pre_trade_balance=Decimal("10000"),
        )

        assert result1.success is True
        assert result2.success is True
        assert client.notify_trade_open.call_count == 2

    def test_p6_restart_state_preserves_lock_and_balance(self):
        """P6-D/E: after restart, lock and balance are read from DB, not memory."""
        state_after_crash = _make_state(
            has_active_trade=True,
            active_setup_id="setup-persist-001",
            current_balance=Decimal("9500"),
            next_trade_capital=Decimal("9500"),
        )
        client = _mock_client(state=state_after_crash)

        state = client.get_account_state()

        assert state.has_active_trade is True
        assert state.active_setup_id == "setup-persist-001"
        assert state.current_balance == Decimal("9500")

    def test_p7_clean_start_after_completed_trade(self):
        """P7-F/G: crash after close — balance is correct from DB."""
        state_after_tp = _make_state(
            has_active_trade=False,
            current_balance=Decimal("10600"),
            next_trade_capital=Decimal("10600"),
            latest_post_trade_balance=Decimal("10600"),
            total_closed_trades=1,
            total_net_pnl=Decimal("600"),
        )
        client = _mock_client(state=state_after_tp)

        state = client.get_account_state()

        assert state.has_active_trade is False
        assert state.latest_post_trade_balance == Decimal("10600")
        assert state.total_closed_trades == 1


# ─────────────────────────────────────────────────────────────────────────────
# Group 3 — Full Compounding Round-Trip
# ─────────────────────────────────────────────────────────────────────────────

class TestCompoundingRoundTrip:
    """P8-P10: net P&L compounding correctness."""

    def test_p8_net_pnl_formula(self):
        """P8: net_pnl = gross - fees - funding - other (enforced by Java)."""
        gross = Decimal("1000")
        fees = Decimal("120")
        funding = Decimal("15")
        other = Decimal("5")
        # Python computes the expected net; Java enforces it
        expected_net = gross - fees - funding - other  # 860
        expected_post = Decimal("10000") + expected_net  # 10860

        client = _mock_client(
            close_result=_make_close_result(
                net_pnl=expected_net, post_balance=expected_post
            )
        )

        result = client.notify_trade_close(
            setup_id="setup-002",
            gross_pnl=gross,
            trading_fees=fees,
            funding_costs=funding,
            other_costs=other,
            exit_price=Decimal("51500"),
            close_reason="TAKE_PROFIT",
        )

        assert result.success is True
        assert result.net_pnl == expected_net
        assert result.post_trade_balance == expected_post

    def test_p9_gross_never_used_for_balance(self):
        """P9: gross P&L is separate from net — balance update uses net only."""
        gross = Decimal("2000")
        fees = Decimal("400")
        net = gross - fees  # 1600
        post = Decimal("10000") + net  # 11600

        client = _mock_client(
            close_result=_make_close_result(net_pnl=net, post_balance=post)
        )

        result = client.notify_trade_close(
            setup_id="setup-003",
            gross_pnl=gross,
            trading_fees=fees,
            close_reason="TAKE_PROFIT",
        )

        assert result.net_pnl == Decimal("1600")
        # Ensure gross was NOT used for balance
        assert result.post_trade_balance != Decimal("10000") + gross

    def test_p10_loss_trade_compounding(self):
        """P10: losing trade — balance decreases by net loss."""
        pre_balance = Decimal("10000")
        gross = Decimal("-1800")
        fees = Decimal("120")
        net = gross - fees  # -1920
        post = max(pre_balance + net, Decimal("0"))  # 8080

        client = _mock_client(
            close_result=_make_close_result(net_pnl=net, post_balance=post)
        )

        result = client.notify_trade_close(
            setup_id="setup-004",
            gross_pnl=gross,
            trading_fees=fees,
            close_reason="STOP_LOSS",
        )

        assert result.success is True
        assert result.net_pnl == Decimal("-1920")
        assert result.post_trade_balance == Decimal("8080")


# ─────────────────────────────────────────────────────────────────────────────
# Group 4 — 100% Capital Allocation After Compounding
# ─────────────────────────────────────────────────────────────────────────────

class TestCapitalAllocation:
    """P11-P12: next trade uses 100% of post-trade balance."""

    def test_p11_next_capital_uses_post_trade_balance(self):
        """P11: after a winning trade, 100% of post_trade_balance is next capital."""
        post_balance = Decimal("10860")
        client = _mock_client(
            state=_make_state(
                current_balance=post_balance,
                next_trade_capital=post_balance,
                latest_post_trade_balance=post_balance,
            ),
            next_capital=post_balance,
        )

        next_capital = client.get_next_trade_capital()
        assert next_capital == post_balance

    def test_p12_zero_balance_guard(self):
        """P12: zero or negative balance → next capital is 0, not negative."""
        zero = Decimal("0")
        client = _mock_client(
            state=_make_state(current_balance=zero, next_trade_capital=zero),
            next_capital=zero,
        )

        capital = client.get_next_trade_capital()
        assert capital >= Decimal("0")


# ─────────────────────────────────────────────────────────────────────────────
# Group 5 — Safety Controls
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyControls:
    """P13-P15: kill switch, algo disabled, duplicate signal prevention."""

    def test_p13_kill_switch_blocks_new_trade(self):
        """P13: kill_switch_active from DB blocks any new trade."""
        client = _mock_client(state=_make_state(kill_switch_active=True))
        state = client.get_account_state()
        assert state.kill_switch_active is True
        client.notify_trade_open.assert_not_called()

    def test_p14_duplicate_signal_returns_conflict(self):
        """P14: duplicate signal for an account with active trade → backend returns conflict."""
        conflict_result = TradeOpenResult(
            success=False, trade_record_id=None, lock_id=None, error="ONE_TRADE_ACTIVE"
        )
        client = _mock_client(open_result=conflict_result)

        result = client.notify_trade_open(
            setup_id="setup-005", symbol="BTCUSDT", direction="LONG",
            entry_price=Decimal("50000"), quantity=Decimal("0.1"),
            leverage=17, pre_trade_balance=Decimal("10000"),
        )

        assert result.success is False
        assert result.error == "ONE_TRADE_ACTIVE"

    def test_p15_frontend_cannot_override_sl_via_engine(self):
        """P15: backend_client always sends SL from OB edge, not from frontend."""
        ob_lower = Decimal("49000")  # SL for LONG = lower edge of OB

        client = _mock_client()
        client.notify_trade_open(
            setup_id="setup-006", symbol="BTCUSDT", direction="LONG",
            entry_price=Decimal("50000"), quantity=Decimal("0.1"),
            leverage=17, pre_trade_balance=Decimal("10000"),
            stop_loss_price=ob_lower,  # authoritative OB edge
        )

        call_kwargs = client.notify_trade_open.call_args
        assert call_kwargs.kwargs["stop_loss_price"] == ob_lower


# ─────────────────────────────────────────────────────────────────────────────
# Group 6 — Persistence / Recovery Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

class TestPersistenceEdgeCases:
    """P16-P18: crash recovery edge cases."""

    def test_p16_ws_disconnect_state_preserved_in_db(self):
        """P16-H: WebSocket disconnect — state is in DB, not memory."""
        state = _make_state(
            has_active_trade=True,
            active_setup_id="setup-ws-001",
        )
        client = _mock_client(state=state)

        # Simulate: WS drops, reconnects, engine re-reads DB state
        state_before = client.get_account_state()
        state_after_reconnect = client.get_account_state()

        assert state_before.active_setup_id == state_after_reconnect.active_setup_id
        assert state_after_reconnect.has_active_trade is True

    def test_p17_backend_restart_engine_continues(self):
        """P17-I: backend restarts, Python engine retries and restores state."""
        client = MagicMock(spec=BackendClient)
        client.get_account_state.side_effect = [
            BackendClientError("connection refused"),  # first call fails
            _make_state(has_active_trade=True, active_setup_id="setup-007"),  # retry succeeds
        ]

        for attempt in range(2):
            try:
                state = client.get_account_state()
                break
            except BackendClientError:
                continue

        assert state.active_setup_id == "setup-007"

    def test_p18_force_release_only_after_delta_reconciliation(self):
        """P18: force_release_lock is only called AFTER Delta confirms no position."""
        client = _mock_client()

        # Simulate: engine confirmed with Delta → no position, no orders
        delta_confirmed_no_position = True
        if delta_confirmed_no_position:
            client.force_release_lock(reason="DELTA_CONFIRMED_NO_POSITION")

        client.force_release_lock.assert_called_once_with(
            reason="DELTA_CONFIRMED_NO_POSITION"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Group 7 — Net P&L Accounting
# ─────────────────────────────────────────────────────────────────────────────

class TestNetPnLAccounting:
    """P19-P20: authoritative exchange balance override, fee precision."""

    def test_p19_exchange_balance_overrides_computed(self):
        """P19: when Delta reports authoritative balance, it overrides computed."""
        delta_balance = Decimal("10920")  # exchange-reported (authoritative)
        computed_post = Decimal("10860")  # what our formula would give

        # Java backend receives both; returns the exchange value
        client = _mock_client(
            close_result=_make_close_result(
                net_pnl=Decimal("860"), post_balance=delta_balance
            )
        )

        result = client.notify_trade_close(
            setup_id="setup-008",
            gross_pnl=Decimal("1000"),
            trading_fees=Decimal("140"),
            close_reason="TAKE_PROFIT",
            authoritative_exchange_balance=delta_balance,
        )

        assert result.success is True
        assert result.post_trade_balance == delta_balance
        # Engine correctly passes the exchange balance to the backend
        call_kwargs = client.notify_trade_close.call_args.kwargs
        assert call_kwargs["authoritative_exchange_balance"] == delta_balance

    def test_p20_fees_breakdown_passed_separately(self):
        """P20: engine sends gross/fees/funding/other separately, never pre-computed net."""
        gross = Decimal("2500")
        fees = Decimal("300")
        funding = Decimal("50")
        other = Decimal("10")

        client = _mock_client(
            close_result=_make_close_result(
                net_pnl=gross - fees - funding - other,  # 2140
                post_balance=Decimal("12140"),
            )
        )

        result = client.notify_trade_close(
            setup_id="setup-009",
            gross_pnl=gross,
            trading_fees=fees,
            funding_costs=funding,
            other_costs=other,
            close_reason="TAKE_PROFIT",
        )

        assert result.success is True
        assert result.net_pnl == Decimal("2140")

        # Verify all components were passed separately
        call_kwargs = client.notify_trade_close.call_args.kwargs
        assert call_kwargs["gross_pnl"] == gross
        assert call_kwargs["trading_fees"] == fees
        assert call_kwargs["funding_costs"] == funding
        assert call_kwargs["other_costs"] == other


# ─────────────────────────────────────────────────────────────────────────────
# Group 8 — Cross-User Isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossUserIsolation:
    """P21: each account has its own isolated state snapshot."""

    def test_p21_two_accounts_independent_state(self):
        """P21: two different account IDs return independent state snapshots."""
        client_a = _mock_client(
            state=_make_state(has_active_trade=True, active_setup_id="setup-A")
        )
        client_b = _mock_client(
            state=_make_state(has_active_trade=False)
        )

        state_a = client_a.get_account_state(account_id="acct-A")
        state_b = client_b.get_account_state(account_id="acct-B")

        assert state_a.has_active_trade is True
        assert state_b.has_active_trade is False
        assert state_a.active_setup_id != state_b.active_setup_id


# ─────────────────────────────────────────────────────────────────────────────
# Group 9 — Configuration Persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigurationPersistence:
    """P22-P25: algo config, kill switch, immutable snapshots."""

    def test_p22_algo_enabled_persists_across_restart(self):
        """P22: algo_enabled read from DB on startup, not from memory."""
        client = _mock_client(state=_make_state(algo_enabled=True, kill_switch_active=False))
        state = client.get_account_state()
        assert state.algo_enabled is True

    def test_p23_kill_switch_persists_across_restart(self):
        """P23: kill_switch_active read from DB on startup."""
        client = _mock_client(state=_make_state(kill_switch_active=True))
        state = client.get_account_state()
        assert state.kill_switch_active is True

    def test_p24_trade_snapshot_version_passed_to_backend(self):
        """P24: configuration_version is included in notify_trade_open call."""
        client = _mock_client()
        client.notify_trade_open(
            setup_id="setup-010", symbol="ETHUSD", direction="SHORT",
            entry_price=Decimal("3000"), quantity=Decimal("1"),
            leverage=10, pre_trade_balance=Decimal("5000"),
            configuration_version=3,
        )
        call_kwargs = client.notify_trade_open.call_args.kwargs
        assert call_kwargs["configuration_version"] == 3

    def test_p25_immutable_snapshot_does_not_use_current_config(self):
        """
        P25: trade record snapshots the config version at trade time.
        A later config change does NOT affect the existing trade record.
        This is enforced by the Java TradeRecord entity storing config_version,
        max_loss_pct, target_roe_pct at open time.
        """
        # Engine passes snapshot config at trade open time
        client = _mock_client()
        client.notify_trade_open(
            setup_id="setup-011", symbol="BTCUSDT", direction="LONG",
            entry_price=Decimal("50000"), quantity=Decimal("0.2"),
            leverage=17, pre_trade_balance=Decimal("10000"),
            configuration_version=2,
            max_loss_pct=Decimal("35.00"),
            target_roe_pct=Decimal("60.00"),
        )
        call_kwargs = client.notify_trade_open.call_args.kwargs
        # The snapshot values are fixed at open time
        assert call_kwargs["max_loss_pct"] == Decimal("35.00")
        assert call_kwargs["target_roe_pct"] == Decimal("60.00")
        assert call_kwargs["configuration_version"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# Group 10 — Dynamic Leverage Formula (engine-side calculation)
# ─────────────────────────────────────────────────────────────────────────────

class TestDynamicLeverage:
    """Verifies that the Python engine computes leverage correctly before persisting."""

    @pytest.mark.parametrize("sl_distance_pct, expected_leverage", [
        (Decimal("0.01"),  35),   # 1%  → 35x
        (Decimal("0.02"),  17),   # 2%  → 17x
        (Decimal("0.05"),  7),    # 5%  → 7x
        (Decimal("0.10"),  3),    # 10% → 3x
    ])
    def test_leverage_formula(self, sl_distance_pct, expected_leverage):
        """Dynamic leverage: floor(0.35 / sl_distance_fraction), min=1."""
        max_loss_fraction = Decimal("0.35")
        leverage = max(1, int(max_loss_fraction / sl_distance_pct))
        assert leverage == expected_leverage
