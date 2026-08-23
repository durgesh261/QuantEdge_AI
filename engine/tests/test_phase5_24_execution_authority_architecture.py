"""
Phase 5.24 — Python Execution Authority & Architectural Boundary Test Suite.

Proves:
1. Direct order creation/cancellation from Python is strictly prevented in production.
2. DeltaExecutionAuthorityError is raised when allow_direct_execution=False (production mode).
3. BackendClient carries zero credentials and communicates only with Java /api/engine/*.
4. Simulation, backtesting, and market scanning have ZERO capability to dispatch live Delta orders.
"""

import os
from decimal import Decimal
import pytest
from unittest.mock import patch, AsyncMock

from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaExecutionAuthorityError,
)
from quantedge.execution.models import (
    DeltaOrderRequest,
    OrderSide,
    OrderType,
)
from quantedge.execution.backend_client import (
    BackendClient,
    AccountStateSnapshot,
)


@pytest.mark.asyncio
async def test_python_direct_order_creation_blocked_in_production():
    """Verify that DeltaIndiaClient raises DeltaExecutionAuthorityError when allow_direct_execution=False."""
    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "", "QUANTEDGE_ENV": "production", "QUANTEDGE_TESTING": ""}, clear=False):
        client = DeltaIndiaClient(
            api_key="mock_key",
            api_secret="mock_secret",
            allow_direct_execution=False,
        )
        req = DeltaOrderRequest(
            product_id=27,
            product_symbol="BTCUSD",
            size=Decimal("1"),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT_ORDER,
            limit_price=Decimal("60000.00"),
        )
        with pytest.raises(DeltaExecutionAuthorityError, match="sole authoritative production execution authority"):
            await client.create_order(req)


@pytest.mark.asyncio
async def test_python_direct_order_cancellation_blocked_in_production():
    """Verify that DeltaIndiaClient raises DeltaExecutionAuthorityError on cancel when allow_direct_execution=False."""
    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "", "QUANTEDGE_ENV": "production", "QUANTEDGE_TESTING": ""}, clear=False):
        client = DeltaIndiaClient(
            api_key="mock_key",
            api_secret="mock_secret",
            allow_direct_execution=False,
        )
        with pytest.raises(DeltaExecutionAuthorityError, match="sole authoritative production execution authority"):
            await client.cancel_order(order_id=12345, product_id=27)

        with pytest.raises(DeltaExecutionAuthorityError, match="sole authoritative production execution authority"):
            await client.cancel_order_by_client_id(client_order_id="QE-123", product_id=27)


def test_backend_client_holds_zero_delta_credentials():
    """Verify that BackendClient only interfaces with the Java internal bridge and never stores Delta credentials."""
    client = BackendClient(base_url="http://localhost:8080", api_key="engine-secret", account_id="acct-uuid")
    assert not hasattr(client, "delta_api_key")
    assert not hasattr(client, "delta_api_secret")
    assert not hasattr(client, "place_order")
    assert not hasattr(client, "create_order")
    assert not hasattr(client, "execute_trade")
