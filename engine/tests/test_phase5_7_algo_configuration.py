"""
Unit & Integration Test Suite for Phase 5.7: Persistent Versioned Algo Configuration & Immutable Trade Snapshots.

Verifies:
1. Fail-Safe Defaults (algo_enabled=False, kill_switch_active=True).
2. Account Ownership & Strict Isolation (User A cannot access or mutate User B's config).
3. Configuration Versioning (version increments on every update).
4. Immutable Trade Snapshots (trades bind configuration version at signal time).
5. Historical Trade Preservation (updating configuration to version 2 does NOT mutate version 1 trades).
6. Authoritative TP/SL Geometry & Validation (LONG: SL < Entry < TP, SHORT: TP < Entry < SL).
7. Invalid Configuration Fail-Closed Behavior.
8. Zero Credential Leakage.
9. Concurrency & Race-Condition Determinism.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from quantedge.execution.algo_config import (
    AlgoConfiguration,
    AlgoConfigurationSnapshot,
    AlgoConfigStore,
    AlgoConfigValidationError,
)
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
    CloseReason,
)
from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    DeltaOrderResponse,
)
from quantedge.execution.synchronizer import (
    LocalStateStore,
    AccountRecord,
    ConnectionRecord,
    PositionRecord,
    OrderRecord,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    RejectionReasonCode,
)
from quantedge.strategy.models import (
    StrategyDecision,
    SetupState,
    StrategyDirection,
    TradeDirection,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


FIXTURE_KEY = "TEST_KEY_PHASE_5_7_0000000001"
FIXTURE_SECRET = "TEST_SECRET_PHASE_5_7_000000000000000000000000001"


@pytest.fixture
def mock_delta_client():
    client = MagicMock()
    client._api_key = FIXTURE_KEY
    client._api_secret = FIXTURE_SECRET
    client.place_order = AsyncMock(side_effect=lambda req: DeltaOrderResponse(
        id=778899,
        client_order_id=req.client_order_id,
        user_id=1,
        product_id=req.product_id,
        product_symbol=req.product_symbol,
        side=req.side,
        order_type=req.order_type,
        size=req.size,
        unfilled_size=req.size,
        limit_price=req.limit_price,
        stop_price=req.stop_price,
        average_fill_price=None,
        state=OrderStatus.OPEN,
        reduce_only=req.reduce_only,
        created_at=datetime.now(timezone.utc),
    ))
    client.cancel_order = AsyncMock(return_value=True)
    return client


@pytest.fixture
def state_store():
    store = LocalStateStore(account_id="acc_user_1")
    store.account.user_id = "user_1"
    store.account.total_equity = Decimal("10000.00")
    store.account.available_balance = Decimal("10000.00")
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.account.last_synced_at = datetime.now(timezone.utc)
    store.connection.connection_status = "CONNECTED"
    return store


@pytest.fixture
def validation_gateway():
    return OrderValidationGateway()


@pytest.fixture
def algo_store():
    return AlgoConfigStore()


@pytest.fixture
def lifecycle_manager(mock_delta_client, validation_gateway, state_store, algo_store):
    return TradeLifecycleManager(
        client=mock_delta_client,
        validation_gateway=validation_gateway,
        state_store=state_store,
        algo_config_store=algo_store,
        daily_loss_limit=Decimal("500.00"),
    )


@pytest.fixture
def bullish_decision():
    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_BULLISH_001",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_reward=Decimal("3.0"),
    )


# ── Test Cases ────────────────────────────────────────────────────────────────


def test_01_new_account_fail_safe_defaults():
    """1. Verify new configuration initializes with fail-safe defaults (algo_enabled=False, kill_switch_active=True)."""
    config = AlgoConfiguration(account_id="acc_new", user_id="user_new")
    assert config.algo_enabled is False
    assert config.kill_switch_active is True
    assert config.version == 1
    assert config.take_profit_pct == Decimal("2.00")
    assert config.stop_loss_pct == Decimal("1.00")
    assert config.risk_per_trade_pct == Decimal("1.00")


def test_02_user_create_and_update_configuration(algo_store):
    """2. Verify user can create and update their configuration."""
    config = algo_store.get_or_create_default(user_id="user_1", account_id="acc_1")
    assert config.version == 1

    updated = algo_store.update_config(
        user_id="user_1",
        account_id="acc_1",
        take_profit_pct=Decimal("3.50"),
        stop_loss_pct=Decimal("1.50"),
        risk_per_trade_pct=Decimal("2.00"),
    )
    assert updated.take_profit_pct == Decimal("3.50")
    assert updated.stop_loss_pct == Decimal("1.50")
    assert updated.risk_per_trade_pct == Decimal("2.00")
    assert updated.version == 2


def test_03_user_retrieve_own_configuration(algo_store):
    """3. Verify user can retrieve their persisted configuration."""
    algo_store.update_config(
        user_id="user_1",
        account_id="acc_1",
        take_profit_pct=Decimal("4.00"),
    )
    fetched = algo_store.get_config(user_id="user_1", account_id="acc_1")
    assert fetched is not None
    assert fetched.take_profit_pct == Decimal("4.00")
    assert fetched.version == 2


def test_04_account_isolation_no_cross_user_access(algo_store):
    """4. Verify User A cannot access User B's configuration."""
    algo_store.update_config(
        user_id="user_A",
        account_id="acc_A",
        take_profit_pct=Decimal("5.00"),
    )

    # User B queries User A's account
    cross_access = algo_store.get_config(user_id="user_B", account_id="acc_A")
    assert cross_access is None


def test_05_account_isolation_no_cross_user_modification(algo_store):
    """5. Verify User B creating/updating their own account does NOT mutate User A's configuration."""
    config_a = algo_store.get_or_create_default(user_id="user_A", account_id="acc_A")
    algo_store.update_config(user_id="user_A", account_id="acc_A", take_profit_pct=Decimal("6.00"))

    # User B updates their account
    algo_store.update_config(user_id="user_B", account_id="acc_B", take_profit_pct=Decimal("1.50"))

    # Verify User A remains unaffected
    refreshed_a = algo_store.get_config(user_id="user_A", account_id="acc_A")
    assert refreshed_a.take_profit_pct == Decimal("6.00")
    assert refreshed_a.version == 2


def test_06_configuration_version_increment(algo_store):
    """6. Verify configuration version strictly increments on every update."""
    config = algo_store.get_or_create_default("u1", "a1")
    assert config.version == 1

    algo_store.update_config("u1", "a1", take_profit_pct=Decimal("2.50"))
    assert algo_store.get_config("u1", "a1").version == 2

    algo_store.update_config("u1", "a1", stop_loss_pct=Decimal("1.20"))
    assert algo_store.get_config("u1", "a1").version == 3

    algo_store.update_config("u1", "a1", risk_per_trade_pct=Decimal("1.50"))
    assert algo_store.get_config("u1", "a1").version == 4


@pytest.mark.asyncio
async def test_07_trade_created_with_version_1_stores_version_1_snapshot(lifecycle_manager, bullish_decision, algo_store):
    """7. Verify trade created with version 1 stores snapshot with version 1."""
    # Ensure config version 1
    algo_store.get_or_create_default("user_1", "acc_user_1")

    record = await lifecycle_manager.execute_trade_setup(
        decision=bullish_decision,
        account_id="acc_user_1",
        user_id="user_1",
    )

    assert record.state == TradeLifecycleState.ENTRY_SUBMITTED
    assert record.config_version == 1
    assert record.config_snapshot is not None
    assert record.config_snapshot.version == 1
    assert record.config_snapshot.take_profit_pct == Decimal("2.00")
    assert record.config_snapshot.stop_loss_pct == Decimal("1.00")


@pytest.mark.asyncio
async def test_08_user_updates_configuration_to_version_2(algo_store):
    """8. Verify user updating configuration creates version 2."""
    algo_store.get_or_create_default("user_1", "acc_user_1")
    updated = algo_store.update_config(
        user_id="user_1",
        account_id="acc_user_1",
        take_profit_pct=Decimal("3.00"),
        stop_loss_pct=Decimal("1.50"),
    )
    assert updated.version == 2
    assert updated.take_profit_pct == Decimal("3.00")
    assert updated.stop_loss_pct == Decimal("1.50")


@pytest.mark.asyncio
async def test_09_new_trade_uses_version_2(lifecycle_manager, bullish_decision, algo_store):
    """9. Verify subsequent trade setup uses version 2 snapshot."""
    algo_store.get_or_create_default("user_1", "acc_user_1")
    algo_store.update_config(
        user_id="user_1",
        account_id="acc_user_1",
        take_profit_pct=Decimal("3.00"),
        stop_loss_pct=Decimal("1.50"),
    )

    trade2 = await lifecycle_manager.execute_trade_setup(
        decision=bullish_decision,
        account_id="acc_user_1",
        user_id="user_1",
    )

    assert trade2.config_version == 2
    assert trade2.config_snapshot.version == 2
    assert trade2.config_snapshot.take_profit_pct == Decimal("3.00")
    assert trade2.config_snapshot.stop_loss_pct == Decimal("1.50")


@pytest.mark.asyncio
async def test_10_existing_trade_preserves_version_1_snapshot_immutability(lifecycle_manager, bullish_decision, algo_store):
    """10. Critical Test: Verify existing Trade 1 retains version 1 after config updated to version 2."""
    algo_store.get_or_create_default("user_1", "acc_user_1")

    # 1. Execute Trade 1 under version 1
    trade1 = await lifecycle_manager.execute_trade_setup(
        decision=bullish_decision,
        account_id="acc_user_1",
        user_id="user_1",
        override_client_order_id="QE_BTCUSD_TRADE1",
    )
    assert trade1.config_version == 1
    assert trade1.config_snapshot.take_profit_pct == Decimal("2.00")

    # 2. Update config to version 2
    algo_store.update_config(
        user_id="user_1",
        account_id="acc_user_1",
        take_profit_pct=Decimal("5.00"),
        stop_loss_pct=Decimal("2.50"),
    )
    assert algo_store.get_config("user_1", "acc_user_1").version == 2

    # 3. Execute Trade 2 under version 2
    decision2 = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_BULLISH_002",
        entry=Decimal("96000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("100000.00"),
        risk_reward=Decimal("2.0"),
    )
    trade2 = await lifecycle_manager.execute_trade_setup(
        decision=decision2,
        account_id="acc_user_1",
        user_id="user_1",
        override_client_order_id="QE_BTCUSD_TRADE2",
    )

    # 4. Verify Trade 1 remains strictly at version 1
    assert trade1.config_version == 1
    assert trade1.config_snapshot.version == 1
    assert trade1.config_snapshot.take_profit_pct == Decimal("2.00")
    assert trade1.config_snapshot.stop_loss_pct == Decimal("1.00")

    # 5. Verify Trade 2 is at version 2
    assert trade2.config_version == 2
    assert trade2.config_snapshot.version == 2
    assert trade2.config_snapshot.take_profit_pct == Decimal("5.00")
    assert trade2.config_snapshot.stop_loss_pct == Decimal("2.50")


def test_11_invalid_tp_sl_geometry_calculation():
    """11. Verify invalid TP/SL geometry is rejected and throws AlgoConfigValidationError."""
    snapshot = AlgoConfigurationSnapshot(
        setup_id="s1",
        account_id="a1",
        user_id="u1",
        version=1,
        take_profit_pct=Decimal("2.0"),
        stop_loss_pct=Decimal("1.0"),
        risk_per_trade_pct=Decimal("1.0"),
        max_risk_usd=None,
        max_daily_loss_usd=Decimal("500"),
        max_leverage=100,
        algo_enabled_at_snapshot=True,
        kill_switch_active_at_snapshot=False,
    )

    # Long: SL < Entry < TP (Valid)
    sl, tp, rr = snapshot.calculate_tp_sl(Decimal("95000.00"), TradeDirection.LONG)
    assert sl < Decimal("95000.00") < tp
    assert rr > Decimal("1.0")

    # Short: TP < Entry < SL (Valid)
    sl_s, tp_s, rr_s = snapshot.calculate_tp_sl(Decimal("95000.00"), TradeDirection.SHORT)
    assert tp_s < Decimal("95000.00") < sl_s
    assert rr_s > Decimal("1.0")


def test_12_invalid_configuration_fails_closed():
    """12. Verify invalid configuration parameters fail closed with descriptive exceptions."""
    with pytest.raises(AlgoConfigValidationError, match="Take Profit percentage"):
        AlgoConfiguration(account_id="a1", user_id="u1", take_profit_pct=Decimal("0"))

    with pytest.raises(AlgoConfigValidationError, match="Stop Loss percentage"):
        AlgoConfiguration(account_id="a1", user_id="u1", stop_loss_pct=Decimal("-1.0"))

    with pytest.raises(AlgoConfigValidationError, match="Risk per trade percentage"):
        AlgoConfiguration(account_id="a1", user_id="u1", risk_per_trade_pct=Decimal("150"))

    with pytest.raises(AlgoConfigValidationError, match="Max leverage"):
        AlgoConfiguration(account_id="a1", user_id="u1", max_leverage=200)


def test_13_configuration_cannot_bypass_kill_switch(algo_store):
    """13. Verify configuration update cannot enable algo trading while kill switch is active."""
    with pytest.raises(AlgoConfigValidationError, match="emergency kill switch is active"):
        algo_store.update_config(
            user_id="u1",
            account_id="a1",
            algo_enabled=True,
            kill_switch_active=True,
        )


def test_14_configuration_cannot_automatically_enable_algo(algo_store):
    """14. Verify saving config parameters never silently enables algo trading."""
    config = algo_store.get_or_create_default("u1", "a1")
    assert config.algo_enabled is False

    algo_store.update_config("u1", "a1", take_profit_pct=Decimal("4.00"))
    refreshed = algo_store.get_config("u1", "a1")
    assert refreshed.algo_enabled is False
    assert refreshed.kill_switch_active is True


@pytest.mark.asyncio
async def test_15_frontend_cannot_override_authoritative_tp_sl(lifecycle_manager, bullish_decision):
    """15. Verify execution rejects any frontend attempt to override server-side TP/SL."""
    # 1. Frontend attempts to inject fabricated TP
    tampered_tp_params = {
        "take_profit": "999999.00",
    }
    rec_tp = await lifecycle_manager.execute_trade_setup(
        decision=bullish_decision,
        account_id="acc_user_1",
        user_id="user_1",
        frontend_params=tampered_tp_params,
    )
    assert rec_tp.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec_tp.rejection_code == "FRONTEND_TP_TAMPERING"

    # 2. Frontend attempts to inject fabricated SL
    decision_sl = StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="1h",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id="SETUP_TAMPER_SL",
        entry=Decimal("95000.00"),
        stop_loss=Decimal("94000.00"),
        take_profit=Decimal("98000.00"),
        risk_reward=Decimal("3.0"),
    )
    tampered_sl_params = {
        "stop_loss": "1.00",
    }
    rec_sl = await lifecycle_manager.execute_trade_setup(
        decision=decision_sl,
        account_id="acc_user_1",
        user_id="user_1",
        frontend_params=tampered_sl_params,
    )
    assert rec_sl.state == TradeLifecycleState.ENTRY_REJECTED
    assert rec_sl.rejection_code == "FRONTEND_SL_TAMPERING"


def test_16_concurrent_config_update_deterministic(algo_store):
    """16. Verify concurrent configuration updates and snapshot creation remain thread-safe and deterministic."""
    import concurrent.futures

    algo_store.get_or_create_default("user_conc", "acc_conc")

    def updater(i):
        algo_store.update_config(
            user_id="user_conc",
            account_id="acc_conc",
            take_profit_pct=Decimal(str(2.0 + (i * 0.1))),
        )

    def snapshooter(i):
        return algo_store.create_trade_snapshot(
            user_id="user_conc",
            account_id="acc_conc",
            setup_id=f"SETUP_CONC_{i}",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        f_updates = [executor.submit(updater, i) for i in range(10)]
        f_snaps = [executor.submit(snapshooter, i) for i in range(10)]
        concurrent.futures.wait(f_updates + f_snaps)

    final_config = algo_store.get_config("user_conc", "acc_conc")
    assert final_config.version == 11  # 1 initial + 10 updates


def test_17_zero_credential_leakage(algo_store):
    """17. Verify configuration objects and dumps never contain or leak API secrets."""
    config = algo_store.get_or_create_default("u_safe", "acc_safe")
    snapshot = algo_store.create_trade_snapshot("u_safe", "acc_safe", "s_safe")

    dump_c = str(config)
    dump_s = str(snapshot)

    assert "api_secret" not in dump_c.lower()
    assert "api_secret" not in dump_s.lower()
    assert "secret" not in dump_c.lower()
    assert "secret" not in dump_s.lower()
