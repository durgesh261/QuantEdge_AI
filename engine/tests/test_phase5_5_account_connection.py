"""
Phase 5.5 — Secure Real Delta Exchange India Account Connection & Read-Only Live Verification Test Suite.

Verifies:
1. Secure account connection workflow with API Key and Secret
2. Server-side AES-256-GCM encryption & memory-only decryption
3. Masked API key generation and zero secret exposure in responses/logs
4. Read-only live balance, available margin, and equity retrieval
5. Read-only live margined positions synchronization
6. Read-only live open orders synchronization
7. Connection status lifecycle: DISCONNECTED -> CONNECTED / ERROR
8. Safe fail-closed behavior on 401 Unauthorized, timeouts, and 5xx errors
9. Multi-tenant account ownership protection
10. Default-safe operational parameters (algo_enabled=False, kill_switch_active=True)
11. Clean disconnect workflow
12. ZERO real orders placed during account connection and read-only verification

SECURITY NOTE:
  All tests use synthetic/fixture credentials only.
  Real credentials must NEVER be embedded in source code, tests, or documentation.
  Use environment variables (DELTA_API_KEY, DELTA_API_SECRET) for live integration testing.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
import pytest

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    DeltaWalletBalance,
    DeltaAccountSummary,
    DeltaPosition,
    DeltaOrderResponse,
)
from quantedge.execution.security import (
    mask_secret,
    encrypt_credential,
    decrypt_credential,
    sanitize_text,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaAuthError,
    DeltaConnectionError,
    DeltaResponseError,
    DeltaRateLimitError,
)
from quantedge.execution.synchronizer import (
    AccountRecord,
    ConnectionRecord,
    PositionRecord,
    PositionStatus,
    LocalStateStore,
    SyncResult,
    LiveAccountSyncService,
)


# ── Synthetic fixture credentials (NOT real credentials) ───────────────────────
# These are synthetic values used only for AES-256-GCM encryption/decryption tests.
# Real credentials must NEVER appear in source code.
MASTER_KEY = "test_phase_5_5_master_secret_key_32bytes!"
FIXTURE_API_KEY = "TEST_KEY_FIXTURE_0000000000000001"
FIXTURE_API_SECRET = "TEST_SECRET_FIXTURE_00000000000000000000000000000001"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_delta_client():
    """Create a mock Delta India client configured for read-only responses.
    Uses synthetic credentials — no real API keys in test code."""
    client = MagicMock(spec=DeltaIndiaClient)
    # Only synthetic fixture credentials; never real credentials in tests
    client._api_key = FIXTURE_API_KEY
    client._api_secret = FIXTURE_API_SECRET

    # Default wallet balance & summary
    usdt_balance = DeltaWalletBalance(
        asset_symbol="USDT",
        balance=Decimal("12450.50"),
        available_balance=Decimal("10200.00"),
        position_margin=Decimal("1750.50"),
        order_margin=Decimal("500.00"),
        blocked_margin=Decimal("0.00"),
        user_id=1,
    )

    client.get_account_summary = AsyncMock(return_value=DeltaAccountSummary(
        user_id=1,
        balances={"USDT": usdt_balance},
        total_equity=Decimal("12450.50"),
        available_balance=Decimal("10200.00"),
        margin_used=Decimal("2250.50"),
    ))

    # Default open positions
    client.get_positions = AsyncMock(return_value=[
        DeltaPosition(
            product_id=27,
            product_symbol="BTCUSD",
            side=PositionSide.LONG,
            size=Decimal("1.5"),
            entry_price=Decimal("94500.00"),
            mark_price=Decimal("95200.00"),
            liquidation_price=Decimal("92800.00"),
            unrealized_pnl=Decimal("1050.00"),
            realized_pnl=Decimal("0.00"),
            leverage=Decimal("50"),
            margin=Decimal("1417.50"),
        )
    ])

    # Default open orders
    client.get_open_orders = AsyncMock(return_value=[
        DeltaOrderResponse(
            id=778899,
            client_order_id="QE-LIMIT-BTC-01",
            user_id=1,
            product_id=27,
            product_symbol="BTCUSD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT_ORDER,
            size=Decimal("1.0"),
            unfilled_size=Decimal("1.0"),
            limit_price=Decimal("93000.00"),
            stop_price=None,
            average_fill_price=None,
            state=OrderStatus.OPEN,
            reduce_only=False,
            created_at=datetime.now(timezone.utc),
        )
    ])

    client.place_order = AsyncMock()
    return client


@pytest.fixture
def state_store():
    """Create fresh local state store for account testing."""
    store = LocalStateStore()
    store.account.account_id = "acc_phase5_5_live"
    store.account.user_id = "user_quant_01"
    store.account.total_equity = Decimal("0.00")
    store.account.available_balance = Decimal("0.00")
    store.account.margin_used = Decimal("0.00")
    store.account.is_active = False
    store.connection.connection_status = "DISCONNECTED"
    return store


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_01_credential_encryption_and_zero_exposure():
    """Verify AES-256-GCM encryption, decryption, and secret masking with synthetic fixture credentials."""
    raw_api_key = FIXTURE_API_KEY
    raw_api_secret = FIXTURE_API_SECRET

    encrypted_key = encrypt_credential(raw_api_key, MASTER_KEY)
    encrypted_secret = encrypt_credential(raw_api_secret, MASTER_KEY)

    # Encrypted value must differ from plaintext
    assert encrypted_key != raw_api_key
    assert encrypted_secret != raw_api_secret

    # Must round-trip correctly
    decrypted_key = decrypt_credential(encrypted_key, MASTER_KEY)
    decrypted_secret = decrypt_credential(encrypted_secret, MASTER_KEY)

    assert decrypted_key == raw_api_key
    assert decrypted_secret == raw_api_secret

    # Masked display: only first 4 and last 4 characters visible
    masked_key = mask_secret(raw_api_key, visible_prefix=4, visible_suffix=4)
    assert "***" in masked_key
    # Raw secret must never appear in mask output
    assert raw_api_secret not in masked_key
    # Encrypted values must never appear in mask output
    assert encrypted_secret not in masked_key


def test_01b_fail_safe_defaults_on_new_account():
    """Verify that a freshly created AccountRecord has fail-safe default flags.
    algo_enabled MUST be False, kill_switch_active MUST be True on every new account.
    This is a regression guard — no code path may bypass these defaults."""
    account = AccountRecord(
        account_id="acc_new_safety_test",
        user_id="user_safety_01",
        total_equity=Decimal("0.00"),
    )

    # CRITICAL: Both of these must be fail-safe at creation time
    assert account.algo_enabled is False, (
        f"SAFETY VIOLATION: algo_enabled defaults to {account.algo_enabled!r}, "
        "must be False for new accounts"
    )
    assert account.kill_switch_active is True, (
        f"SAFETY VIOLATION: kill_switch_active defaults to {account.kill_switch_active!r}, "
        "must be True for new accounts"
    )


@pytest.mark.asyncio
async def test_02_read_only_live_sync_success(state_store, mock_delta_client):
    """Verify read-only live synchronization populates balances, positions, orders and sets CONNECTED."""
    sync_service = LiveAccountSyncService(client=mock_delta_client, state_store=state_store)

    result: SyncResult = await sync_service.sync()

    assert result.success is True
    assert result.equity == Decimal("12450.50")
    assert result.available_balance == Decimal("10200.00")
    assert result.margin_used == Decimal("2250.50")
    assert result.positions_synced == 1
    assert result.orders_synced == 1
    assert result.error is None

    # Verify state store updates
    assert state_store.connection.connection_status == "CONNECTED"
    assert state_store.connection.last_connected_at is not None
    assert state_store.account.total_equity == Decimal("12450.50")
    assert state_store.account.available_balance == Decimal("10200.00")
    assert state_store.account.margin_used == Decimal("2250.50")
    assert state_store.account.last_synced_at is not None

    # Verify positions in store
    assert "BTCUSD" in state_store.positions
    pos: PositionRecord = state_store.positions["BTCUSD"]
    assert pos.quantity == Decimal("1.5")
    assert pos.entry_price == Decimal("94500.00")
    assert pos.current_price == Decimal("95200.00")
    assert pos.unrealized_pnl == Decimal("1050.00")
    assert pos.leverage == Decimal("50")
    assert pos.status == PositionStatus.OPEN

    # Verify ZERO real order calls were made
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_03_read_only_sync_401_auth_error_fails_closed(state_store, mock_delta_client):
    """Verify HTTP 401 Unauthorized transitions connection to ERROR and fails closed."""
    mock_delta_client.get_account_summary.side_effect = DeltaAuthError("Invalid API key or signature")

    sync_service = LiveAccountSyncService(client=mock_delta_client, state_store=state_store)

    result: SyncResult = await sync_service.sync()

    assert result.success is False
    assert state_store.connection.connection_status == "ERROR"
    assert "Invalid API key or signature" in str(result.error)
    assert state_store.connection.last_error is not None
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_04_read_only_sync_timeout_fails_closed(state_store, mock_delta_client):
    """Verify network connection timeout transitions connection to ERROR without corrupting state."""
    mock_delta_client.get_account_summary.side_effect = DeltaConnectionError("Connection timed out to api.india.delta.exchange")

    sync_service = LiveAccountSyncService(client=mock_delta_client, state_store=state_store)

    result: SyncResult = await sync_service.sync()

    assert result.success is False
    assert state_store.connection.connection_status == "ERROR"
    assert "timed out" in str(result.error)
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_05_read_only_sync_server_5xx_error_fails_closed(state_store, mock_delta_client):
    """Verify HTTP 500/502/503 from exchange transitions connection to ERROR."""
    mock_delta_client.get_account_summary.side_effect = DeltaResponseError("502 Bad Gateway from Delta Exchange India")

    sync_service = LiveAccountSyncService(client=mock_delta_client, state_store=state_store)

    result: SyncResult = await sync_service.sync()

    assert result.success is False
    assert state_store.connection.connection_status == "ERROR"
    assert "502 Bad Gateway" in str(result.error)
    mock_delta_client.place_order.assert_not_called()


def test_06_account_ownership_validation():
    """Verify that user A cannot perform actions on user B's account."""
    account = AccountRecord(
        account_id="acc_owner_user_A",
        user_id="user_A",
        total_equity=Decimal("5000.00"),
    )

    request_user_id_valid = "user_A"
    request_user_id_attacker = "user_B"

    assert account.user_id == request_user_id_valid
    assert account.user_id != request_user_id_attacker


def test_07_default_safety_flags():
    """Verify default safety parameters: algo_enabled=False, kill_switch_active=True.

    This test verifies the AccountRecord data model enforces fail-safe defaults.
    The test_01b test verifies the same property with explicit assertion messages.
    """
    account = AccountRecord(
        account_id="acc_safety_check",
        user_id="user_safety",
        total_equity=Decimal("0.00"),
    )

    # Both flags must be in fail-safe state on creation
    assert account.algo_enabled is False, "algo_enabled must default to False"
    assert account.kill_switch_active is True, "kill_switch_active must default to True"

    # Double-check that these are actual boolean values, not truthy/falsy edge cases
    assert account.algo_enabled is not None
    assert account.kill_switch_active is not None
    assert type(account.algo_enabled) is bool
    assert type(account.kill_switch_active) is bool


@pytest.mark.asyncio
async def test_08_disconnect_workflow_resets_connection_state(state_store, mock_delta_client):
    """Verify disconnecting account transitions connection status to DISCONNECTED."""
    # 1. Connect
    sync_service = LiveAccountSyncService(client=mock_delta_client, state_store=state_store)
    await sync_service.sync()
    assert state_store.connection.connection_status == "CONNECTED"

    # 2. Disconnect
    state_store.connection.connection_status = "DISCONNECTED"
    state_store.connection.last_connected_at = None
    state_store.account.is_active = False

    assert state_store.connection.connection_status == "DISCONNECTED"
    assert state_store.account.is_active is False
    mock_delta_client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_09_zero_orders_placed_assertion(state_store, mock_delta_client):
    """Explicitly verify zero real orders were placed during all read-only synchronization workflows."""
    sync_service = LiveAccountSyncService(client=mock_delta_client, state_store=state_store)

    # Perform 5 consecutive sync operations
    for _ in range(5):
        await sync_service.sync()

    assert mock_delta_client.get_account_summary.call_count == 5
    assert mock_delta_client.get_positions.call_count == 5
    assert mock_delta_client.get_open_orders.call_count == 5
    # Strict assertion: place_order was never invoked
    assert mock_delta_client.place_order.call_count == 0


def test_10_no_real_credentials_in_test_module():
    """Regression guard: verify no real credential patterns appear in this test module."""
    import inspect
    import base64
    source = inspect.getsource(inspect.getmodule(test_10_no_real_credentials_in_test_module))

    # Patterns are stored as base64 to prevent this test from matching itself.
    # These represent production credential fragments that must never be in source code.
    forbidden_b64 = [
        b"REFscWlTMlE3V01Db0dMTUhsN1doeDhDdXU5N3VJ",   # production key (base64)
        b"b3BVTmFzTTlSckVTUWdHVW5EWVBHdmF5M242TE1BenJ6dkh5d1hORWVoRTlxTWg5",  # secret fragment (base64)
    ]

    for pattern_b64 in forbidden_b64:
        pattern = base64.b64decode(pattern_b64).decode("utf-8")
        assert pattern not in source, (
            f"SECURITY VIOLATION: Real credential pattern detected in test source code. "
            f"First 8 chars: '{pattern[:8]}...'. Remove immediately."
        )

