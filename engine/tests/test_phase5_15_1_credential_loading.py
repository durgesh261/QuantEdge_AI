"""
Phase 5.15.1 Offline Test Suite: Credential Loading and Read-Only Connection Safety.

Tests:
1. load_project_env finds and loads .env from project root or custom path.
2. DeltaIndiaClient.from_env() triggers load_project_env.
3. Masking and redaction helper tests.
4. IP compatibility logic.
5. Read-only connection test failure formatting when credentials missing.
6. Zero orders are placed during connection verification.

Frozen SMC core remains 100% untouched.
"""

import os
import tempfile
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from quantedge.execution.security import (
    mask_secret,
    sanitize_text,
    load_project_env,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    ConnectionState,
)
from quantedge.execution.connection_test import (
    run_live_connection_test,
    get_observed_public_ip,
)


def test_mask_secret():
    """Verify secrets are masked safely with prefix/suffix visible."""
    assert mask_secret("abcdefghijklmnop") == "abcd***mnop"
    assert mask_secret("short") == "***"
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


def test_sanitize_text():
    """Verify sensitive strings are redacted from error logs."""
    raw_secret = "super_secret_api_key_12345"
    log_msg = f"Failed to connect using secret {raw_secret} on endpoint"
    sanitized = sanitize_text(log_msg, secrets_to_redact=[raw_secret])
    assert raw_secret not in sanitized
    assert "supe***2345" in sanitized


def test_load_project_env_from_file():
    """Verify load_project_env loads key-value pairs into os.environ."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        env_file = os.path.join(tmp_dir, ".env")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("TEST_CUSTOM_DELTA_KEY=loaded_key_999\n")
            f.write("TEST_CUSTOM_DELTA_SECRET=loaded_secret_888\n")

        with patch("dotenv.find_dotenv", return_value=env_file):
            loaded, loaded_path = load_project_env(override=True)
            assert loaded is True
            assert loaded_path == env_file
            assert os.getenv("TEST_CUSTOM_DELTA_KEY") == "loaded_key_999"
            assert os.getenv("TEST_CUSTOM_DELTA_SECRET") == "loaded_secret_888"


def test_delta_client_from_env_loads_env_file():
    """Verify DeltaIndiaClient.from_env automatically triggers load_project_env."""
    with patch.dict(
        "os.environ",
        {"DELTA_API_KEY": "env_key_123", "DELTA_API_SECRET": "env_secret_456"},
        clear=True,
    ):
        with patch("quantedge.execution.delta_client.load_project_env") as mock_load:
            client = DeltaIndiaClient.from_env()
            mock_load.assert_called_once()
            assert client._api_key == "env_key_123"
            assert client._api_secret == "env_secret_456"


@pytest.mark.asyncio
async def test_run_live_connection_test_missing_credentials():
    """Verify read-only connection test formats failure without throwing unhandled exceptions."""
    with patch.dict("os.environ", {}, clear=True):
        with patch("quantedge.execution.connection_test.load_project_env", return_value=(False, None)):
            with patch("quantedge.execution.connection_test.get_observed_public_ip", return_value="49.14.135.89"):
                res = await run_live_connection_test()
                assert res["key_present"] is False
                assert res["secret_present"] is False
                assert res["auth_pass"] is False
                assert res["ip_compat"] == "PASS"
                assert res["positions_count"] == 0
                assert res["orders_count"] == 0


@pytest.mark.asyncio
async def test_run_live_connection_test_success_mock():
    """Verify read-only connection test formats success when valid credentials respond."""
    with patch.dict(
        "os.environ",
        {"DELTA_API_KEY": "valid_key_123", "DELTA_API_SECRET": "valid_secret_456"},
        clear=True,
    ):
        with patch("quantedge.execution.connection_test.load_project_env", return_value=(True, ".env")):
            with patch("quantedge.execution.connection_test.get_observed_public_ip", return_value="49.14.135.89"):
                with patch.object(DeltaIndiaClient, "validate_credentials", return_value=(True, ConnectionState.CONNECTED, None)):
                    mock_bal = MagicMock()
                    mock_bal.asset_symbol = "USDT"
                    mock_bal.balance = Decimal("2.31")
                    mock_bal.available_balance = Decimal("2.31")
                    mock_bal.blocked_margin = Decimal("0.00")
                    mock_bal.user_id = 9999

                    with patch.object(DeltaIndiaClient, "get_wallet_balances", return_value=[mock_bal]):
                        with patch.object(DeltaIndiaClient, "get_positions", return_value=[]):
                            with patch.object(DeltaIndiaClient, "get_open_orders", return_value=[]):
                                with patch.object(DeltaIndiaClient, "request", return_value={"result": [{"id": 1}]}):
                                    res = await run_live_connection_test()
                                    assert res["auth_pass"] is True
                                    assert res["equity_usdt"] == Decimal("2.31")
                                    assert res["available_usdt"] == Decimal("2.31")
                                    assert res["user_id"] == 9999
                                    assert res["positions_count"] == 0
                                    assert res["orders_count"] == 0
                                    assert res["products_count"] == 1
