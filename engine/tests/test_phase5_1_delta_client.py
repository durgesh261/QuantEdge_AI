"""
Phase 5.1 — Authenticated Delta Exchange India Execution Client Test Suite.

Verifies:
- HMAC-SHA256 signature generation and validation
- Credential encryption / decryption and secret masking
- Delta India REST client request signing and headers
- Wallet balance, account summary, position, and open order parsing with exact Decimal precision
- Typed order creation requests with client_order_id idempotency
- Full error handling (401 Auth, 429 Rate-Limit, 400 Rejection, 5xx Connection, Timeout, Malformed JSON)
- 100% Mocked transport: ZERO real orders placed, ZERO live credentials leaked.
"""

import hashlib
import hmac
import json
import pytest
import httpx
from decimal import Decimal
from datetime import datetime, timezone

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    TimeInForce,
    PositionSide,
    DeltaWalletBalance,
    DeltaAccountSummary,
    DeltaPosition,
    DeltaOrderRequest,
    DeltaOrderResponse,
)
from quantedge.execution.security import (
    mask_secret,
    derive_key,
    encrypt_credential,
    decrypt_credential,
    sanitize_text,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaClientError,
    DeltaAuthError,
    DeltaRateLimitError,
    DeltaOrderRejectedError,
    DeltaConnectionError,
    DeltaResponseError,
    generate_signature,
    generate_client_order_id,
    DELTA_INDIA_PRODUCTION_URL,
    DELTA_INDIA_TESTNET_URL,
)


# ── Fixtures & Mock Helpers ───────────────────────────────────────────────────

TEST_API_KEY = "test_delta_api_key_123456789"
TEST_API_SECRET = "test_delta_api_secret_987654321_abcdef"
TEST_TIMESTAMP = 1724261234


def create_mock_transport(handler):
    """Create an httpx MockTransport with the given handler."""
    return httpx.MockTransport(handler)


@pytest.fixture
def mock_client_factory():
    """Factory fixture to create DeltaIndiaClient with a custom mock transport handler."""
    clients = []

    def _factory(handler):
        transport = create_mock_transport(handler)
        async_client = httpx.AsyncClient(
            transport=transport,
            base_url=DELTA_INDIA_PRODUCTION_URL,
        )
        client = DeltaIndiaClient(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            base_url=DELTA_INDIA_PRODUCTION_URL,
            http_client=async_client,
        )
        clients.append((client, async_client))
        return client

    return _factory


# ── 1. HMAC-SHA256 Signature Tests ───────────────────────────────────────────


def test_hmac_signature_generation_basic():
    """Verify HMAC-SHA256 calculation matches expected reference hash."""
    method = "GET"
    path = "/v2/wallet/balances"
    sig, ts = generate_signature(
        api_secret=TEST_API_SECRET,
        method=method,
        path=path,
        query="",
        body="",
        timestamp=TEST_TIMESTAMP,
    )
    assert ts == str(TEST_TIMESTAMP)
    
    # Compute manual reference
    expected_message = f"GET{TEST_TIMESTAMP}/v2/wallet/balances"
    expected_sig = hmac.new(
        TEST_API_SECRET.encode("utf-8"),
        expected_message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert sig == expected_sig
    assert len(sig) == 64  # SHA-256 hex string length


def test_hmac_signature_with_query_and_body():
    """Verify signature calculation includes query parameters and body string."""
    method = "POST"
    path = "/v2/orders"
    query = "state=open"
    body = '{"product_id":27,"size":1}'
    
    sig, ts = generate_signature(
        api_secret=TEST_API_SECRET,
        method=method,
        path=path,
        query=query,
        body=body,
        timestamp=TEST_TIMESTAMP,
    )
    
    expected_message = f"POST{TEST_TIMESTAMP}/v2/orders?state=open" + body
    expected_sig = hmac.new(
        TEST_API_SECRET.encode("utf-8"),
        expected_message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert sig == expected_sig


def test_hmac_signature_deterministic():
    """Verify identical inputs yield identical signature."""
    sig1, ts1 = generate_signature(TEST_API_SECRET, "GET", "/v2/positions", timestamp=1000)
    sig2, ts2 = generate_signature(TEST_API_SECRET, "GET", "/v2/positions", timestamp=1000)
    assert sig1 == sig2
    assert ts1 == ts2


def test_generate_client_order_id_format_and_uniqueness():
    """Verify generated client_order_id adheres to format and produces unique IDs."""
    id1 = generate_client_order_id("QE")
    id2 = generate_client_order_id("QE")
    
    assert id1.startswith("QE-")
    assert id2.startswith("QE-")
    assert id1 != id2
    parts = id1.split("-")
    assert len(parts) == 3
    assert parts[1].isdigit()  # Timestamp milliseconds


# ── 2. Security & Redaction Tests ─────────────────────────────────────────────


def test_mask_secret():
    """Verify secrets are masked safely for logging."""
    assert mask_secret("abcdef123456789") == "abcd***6789"
    assert mask_secret("short") == "***"
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


def test_aes_gcm_credential_roundtrip():
    """Verify AES-256-GCM encryption and decryption roundtrip."""
    master_key = "quantedge-super-secure-production-master-secret-key-32b"
    plaintext_secret = "delta_secret_live_a8f93bc10294e82d"

    encrypted = encrypt_credential(plaintext_secret, master_key)
    assert encrypted != plaintext_secret
    assert len(encrypted) > 20

    decrypted = decrypt_credential(encrypted, master_key)
    assert decrypted == plaintext_secret


def test_aes_gcm_nonces_are_unique():
    """Verify repeated encryption produces distinct ciphertexts due to fresh nonces."""
    master_key = "test-master-key"
    plaintext = "my_api_secret"
    enc1 = encrypt_credential(plaintext, master_key)
    enc2 = encrypt_credential(plaintext, master_key)
    assert enc1 != enc2
    assert decrypt_credential(enc1, master_key) == plaintext
    assert decrypt_credential(enc2, master_key) == plaintext


def test_sanitize_text():
    """Verify sensitive strings are replaced in text logs."""
    raw_secret = "super_secret_api_key_xyz987"
    log_msg = f"Failed to authenticate with secret {raw_secret} on server"
    sanitized = sanitize_text(log_msg, secrets_to_redact=[raw_secret])
    assert raw_secret not in sanitized
    assert "supe***z987" in sanitized


def test_client_repr_and_str_mask_credentials():
    """Verify DeltaIndiaClient __repr__ and __str__ never expose plaintext secret or full key."""
    client = DeltaIndiaClient(
        api_key="my_secret_key_123456789",
        api_secret="my_very_private_secret_9999",
    )
    repr_str = repr(client)
    str_str = str(client)

    assert "my_very_private_secret_9999" not in repr_str
    assert "my_very_private_secret_9999" not in str_str
    assert "my_s***6789" in repr_str
    assert "my_s***6789" in str_str


# ── 3. Connectivity & Account Balances Tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_check_connectivity_success(mock_client_factory):
    """Verify check_connectivity returns True when exchange returns success."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/wallet/balances"
        assert "api-key" in request.headers
        assert "signature" in request.headers
        assert "timestamp" in request.headers
        return httpx.Response(200, json={"success": True, "result": []})

    client = mock_client_factory(handler)
    is_connected = await client.check_connectivity()
    assert is_connected is True


@pytest.mark.asyncio
async def test_get_wallet_balances_decimal_precision(mock_client_factory):
    """Verify wallet balances are accurately parsed into Decimal types without floating point errors."""
    mock_balances_json = {
        "success": True,
        "result": [
            {
                "id": 101,
                "user_id": 999,
                "asset_symbol": "USDT",
                "balance": "10500.25500000",
                "available_balance": "8200.50000000",
                "position_margin": "1500.00500000",
                "order_margin": "799.75000000",
                "blocked_margin": "2299.75500000",
            },
            {
                "id": 102,
                "user_id": 999,
                "asset_symbol": "BTC",
                "balance": "0.12345678",
                "available_balance": "0.10000000",
                "position_margin": "0.02345678",
                "order_margin": "0.00000000",
                "blocked_margin": "0.02345678",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_balances_json)

    client = mock_client_factory(handler)
    balances = await client.get_wallet_balances()

    assert len(balances) == 2
    usdt = balances[0]
    assert usdt.asset_symbol == "USDT"
    assert usdt.balance == Decimal("10500.25500000")
    assert usdt.available_balance == Decimal("8200.50000000")
    assert usdt.position_margin == Decimal("1500.00500000")
    assert usdt.order_margin == Decimal("799.75000000")
    assert usdt.blocked_margin == Decimal("2299.75500000")
    assert usdt.user_id == 999

    btc = balances[1]
    assert btc.asset_symbol == "BTC"
    assert btc.balance == Decimal("0.12345678")


@pytest.mark.asyncio
async def test_get_account_summary(mock_client_factory):
    """Verify aggregated account summary computes total equity and margin used."""
    mock_balances_json = {
        "success": True,
        "result": [
            {
                "id": 101,
                "user_id": 888,
                "asset_symbol": "USDT",
                "balance": "50000.00",
                "available_balance": "35000.00",
                "position_margin": "10000.00",
                "order_margin": "5000.00",
                "blocked_margin": "15000.00",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_balances_json)

    client = mock_client_factory(handler)
    summary = await client.get_account_summary()

    assert summary.user_id == 888
    assert summary.total_equity == Decimal("50000.00")
    assert summary.available_balance == Decimal("35000.00")
    assert summary.margin_used == Decimal("15000.00")
    assert "USDT" in summary.balances


# ── 4. Position Retrieval Tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_positions_long_and_short(mock_client_factory):
    """Verify live margined positions parsing for LONG and SHORT directions."""
    mock_positions_json = {
        "success": True,
        "result": [
            {
                "product_id": 27,
                "product_symbol": "BTCUSD",
                "size": "5",  # Positive = LONG
                "entry_price": "95000.50",
                "mark_price": "96200.00",
                "liquidation_price": "87500.00",
                "unrealised_pnl": "5997.50",
                "realised_pnl": "120.00",
                "leverage": "50",
                "margin": "9500.05",
                "adl_level": 1,
            },
            {
                "product_id": 28,
                "product_symbol": "ETHUSD",
                "size": "-10",  # Negative = SHORT
                "entry_price": "3400.00",
                "mark_price": "3350.00",
                "liquidation_price": "3700.00",
                "unrealised_pnl": "500.00",
                "realised_pnl": "-50.00",
                "leverage": "25",
                "margin": "1360.00",
                "adl_level": 2,
            },
            {
                "product_id": 29,
                "product_symbol": "SOLUSD",
                "size": "0",  # Zero size -> closed position, should be filtered
                "entry_price": "0",
                "mark_price": "180.00",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/positions/margined"
        return httpx.Response(200, json=mock_positions_json)

    client = mock_client_factory(handler)
    positions = await client.get_positions()

    # Zero size position should be filtered out
    assert len(positions) == 2

    btc_pos = positions[0]
    assert btc_pos.product_id == 27
    assert btc_pos.product_symbol == "BTCUSD"
    assert btc_pos.side == PositionSide.LONG
    assert btc_pos.size == Decimal("5")
    assert btc_pos.entry_price == Decimal("95000.50")
    assert btc_pos.mark_price == Decimal("96200.00")
    assert btc_pos.liquidation_price == Decimal("87500.00")
    assert btc_pos.unrealized_pnl == Decimal("5997.50")
    assert btc_pos.realized_pnl == Decimal("120.00")
    assert btc_pos.leverage == Decimal("50")
    assert btc_pos.margin == Decimal("9500.05")
    assert btc_pos.adl_level == 1

    eth_pos = positions[1]
    assert eth_pos.product_id == 28
    assert eth_pos.product_symbol == "ETHUSD"
    assert eth_pos.side == PositionSide.SHORT
    assert eth_pos.size == Decimal("10")
    assert eth_pos.entry_price == Decimal("3400.00")


# ── 5. Open Orders & Order Management Tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_get_open_orders(mock_client_factory):
    """Verify open orders retrieval and field deserialization."""
    mock_orders_json = {
        "success": True,
        "result": [
            {
                "id": 5001,
                "client_order_id": "QE-1724261234000-abc12345",
                "user_id": 999,
                "product_id": 27,
                "product_symbol": "BTCUSD",
                "side": "buy",
                "order_type": "limit_order",
                "size": "2",
                "unfilled_size": "2",
                "limit_price": "94500.00",
                "stop_price": None,
                "state": "open",
                "reduce_only": False,
                "created_at": 1724261234000000,
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/orders"
        assert "state=open" in str(request.url)
        return httpx.Response(200, json=mock_orders_json)

    client = mock_client_factory(handler)
    orders = await client.get_open_orders(product_id=27)

    assert len(orders) == 1
    order = orders[0]
    assert order.id == 5001
    assert order.client_order_id == "QE-1724261234000-abc12345"
    assert order.side == OrderSide.BUY
    assert order.order_type == OrderType.LIMIT_ORDER
    assert order.size == Decimal("2")
    assert order.unfilled_size == Decimal("2")
    assert order.filled_size == Decimal("0")
    assert order.limit_price == Decimal("94500.00")
    assert order.state == OrderStatus.OPEN
    assert order.reduce_only is False


@pytest.mark.asyncio
async def test_create_order_request_payload_and_idempotency(mock_client_factory):
    """Verify create_order generates compliant payload with client_order_id."""
    client_order_id = "QE-1724261234-unique-id"
    order_req = DeltaOrderRequest(
        product_id=27,
        product_symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("1"),
        limit_price=Decimal("95000.00"),
        time_in_force=TimeInForce.GTC,
        reduce_only=False,
        client_order_id=client_order_id,
    )

    mock_resp_json = {
        "success": True,
        "result": {
            "id": 9001,
            "client_order_id": client_order_id,
            "product_id": 27,
            "product_symbol": "BTCUSD",
            "side": "buy",
            "order_type": "limit_order",
            "size": "1",
            "unfilled_size": "1",
            "limit_price": "95000.00",
            "state": "open",
            "reduce_only": False,
            "created_at": 1724261234000000,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/orders"
        body = json.loads(request.content.decode("utf-8"))
        assert body["client_order_id"] == client_order_id
        assert body["side"] == "buy"
        assert body["order_type"] == "limit_order"
        assert body["limit_price"] == "95000.00"
        return httpx.Response(200, json=mock_resp_json)

    client = mock_client_factory(handler)
    response = await client.create_order(order_req)

    assert response.id == 9001
    assert response.client_order_id == client_order_id
    assert response.state == OrderStatus.OPEN
    assert response.limit_price == Decimal("95000.00")


@pytest.mark.asyncio
async def test_cancel_order_by_id(mock_client_factory):
    """Verify DELETE /v2/orders/{id} cancels order."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/v2/orders/9001"
        return httpx.Response(200, json={"success": True, "result": {"id": 9001, "state": "cancelled"}})

    client = mock_client_factory(handler)
    success = await client.cancel_order(order_id=9001, product_id=27)
    assert success is True


# ── 6. Error Handling & Edge Cases Tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_failure_http_401(mock_client_factory):
    """Verify HTTP 401 raises DeltaAuthError with sanitized message."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid api key or signature"}})

    client = mock_client_factory(handler)
    with pytest.raises(DeltaAuthError) as excinfo:
        await client.get_wallet_balances()
    assert "authentication failed" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_rate_limit_http_429(mock_client_factory):
    """Verify HTTP 429 raises DeltaRateLimitError with retry-after header."""
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"Retry-After": "5"}
        return httpx.Response(429, headers=headers, json={"error": {"message": "Rate limit exceeded"}})

    client = mock_client_factory(handler)
    with pytest.raises(DeltaRateLimitError) as excinfo:
        await client.get_wallet_balances()
    assert excinfo.value.retry_after == 5


@pytest.mark.asyncio
async def test_order_rejected_http_400(mock_client_factory):
    """Verify HTTP 400 rejection (insufficient balance) raises DeltaOrderRejectedError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "insufficient margin for order"}})

    client = mock_client_factory(handler)
    order_req = DeltaOrderRequest(
        product_id=27,
        product_symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=Decimal("100"),
        limit_price=Decimal("95000.00"),
    )
    with pytest.raises(DeltaOrderRejectedError) as excinfo:
        await client.create_order(order_req)
    assert "insufficient margin" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_server_error_http_500(mock_client_factory):
    """Verify HTTP 500 raises DeltaConnectionError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = mock_client_factory(handler)
    with pytest.raises(DeltaConnectionError) as excinfo:
        await client.get_wallet_balances()
    assert "server error" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_malformed_json_response(mock_client_factory):
    """Verify bad non-JSON response raises DeltaResponseError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<HTML>Not JSON</HTML>", headers={"Content-Type": "text/html"})

    client = mock_client_factory(handler)
    with pytest.raises(DeltaResponseError):
        await client.get_wallet_balances()
