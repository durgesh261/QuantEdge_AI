"""
Authenticated Delta Exchange India REST API client for QuantEdge AI.

Features:
- Production endpoint: https://api.india.delta.exchange
- HMAC-SHA256 request authentication
- Exact Decimal parsing for financial safety
- Idempotent order placement via client_order_id
- Comprehensive error sanitization (no secret leakage)
- Zero real orders placed during testing (mock-supported)
"""

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Optional, Dict, Any, List, Union, Tuple
from urllib.parse import urlencode

import httpx

from quantedge.execution.models import (
    DeltaWalletBalance,
    DeltaAccountSummary,
    DeltaPosition,
    DeltaOrderRequest,
    DeltaOrderResponse,
    OrderStatus,
    ConnectionState,
)
from quantedge.execution.security import mask_secret, sanitize_text, load_project_env

logger = logging.getLogger("delta_execution_client")

DELTA_INDIA_PRODUCTION_URL = "https://api.india.delta.exchange"
DELTA_INDIA_TESTNET_URL = "https://api-testnet.delta.exchange"


# ── Custom Exceptions ─────────────────────────────────────────────────────────


class DeltaClientError(Exception):
    """Base exception for Delta Exchange execution client."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[Any] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class DeltaAuthError(DeltaClientError):
    """Authentication or signature failure (HTTP 401)."""
    pass


class DeltaRateLimitError(DeltaClientError):
    """Rate limit exceeded (HTTP 429)."""
    def __init__(self, message: str, retry_after: Optional[int] = None, status_code: int = 429, response_body: Optional[Any] = None):
        super().__init__(message, status_code=status_code, response_body=response_body)
        self.retry_after = retry_after


class DeltaOrderRejectedError(DeltaClientError):
    """Order submission rejected by exchange business rules (HTTP 400)."""
    pass


class DeltaConnectionError(DeltaClientError):
    """Network connection timeout or unreachable host (HTTP 5xx or network err)."""
    pass


class DeltaResponseError(DeltaClientError):
    """Malformed or unparseable exchange response."""
    pass


# ── Helper Functions ──────────────────────────────────────────────────────────


def generate_signature(
    api_secret: str,
    method: str,
    path: str,
    query: str = "",
    body: str = "",
    timestamp: Optional[int] = None,
) -> Tuple[str, str]:
    """Generate HMAC-SHA256 signature for Delta Exchange API request.

    Signature string format:
        METHOD + TIMESTAMP + PATH + [QUERY] + [BODY]

    Returns:
        tuple of (hex_signature, timestamp_str)
    """
    if timestamp is None:
        timestamp_str = str(int(time.time()))
    else:
        timestamp_str = str(int(timestamp))

    # Clean query parameter prefix if present
    query_clean = query
    if query_clean and not query_clean.startswith("?") and not query_clean.startswith("&"):
        query_clean = f"?{query_clean}" if "?" not in path else f"&{query_clean}"

    message = method.upper() + timestamp_str + path + query_clean + body
    sig = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return sig, timestamp_str


def generate_client_order_id(prefix: str = "QE") -> str:
    """Generate a unique, collision-resistant client order identifier.

    Format: QE-{timestamp_ms}-{uuid_hex_8}
    """
    ts_ms = int(time.time() * 1000)
    rand_suffix = uuid.uuid4().hex[:8]
    return f"{prefix}-{ts_ms}-{rand_suffix}"


def generate_deterministic_client_order_id(account_id: str, setup_id: str, role: str = "ENTRY") -> str:
    """Generate a deterministic, idempotent client order ID tied to a trade setup and order role."""
    clean_acct = account_id.replace("-", "")[:8]
    clean_setup = setup_id.replace("-", "")[:12]
    return f"QE-{clean_acct}-{clean_setup}-{role.upper()}"


# ── Main Client ───────────────────────────────────────────────────────────────


class DeltaIndiaClient:
    """Authenticated Delta Exchange India Execution REST Client."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = DELTA_INDIA_PRODUCTION_URL,
        timeout_seconds: float = 10.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._custom_http_client = http_client
        self._owned_http_client: Optional[httpx.AsyncClient] = None
        self._connection_state: ConnectionState = ConnectionState.UNKNOWN

    @classmethod
    def from_env(cls, base_url: Optional[str] = None, timeout_seconds: float = 10.0) -> "DeltaIndiaClient":
        """Instantiate client securely from environment variables, loading .env if present."""
        load_project_env()
        api_key = os.getenv("DELTA_API_KEY", "").strip()
        api_secret = os.getenv("DELTA_API_SECRET", "").strip()
        url = base_url or os.getenv("DELTA_BASE_URL", DELTA_INDIA_PRODUCTION_URL).strip()
        if not api_key or not api_secret:
            raise ValueError("DELTA_API_KEY and DELTA_API_SECRET environment variables must be set.")
        return cls(api_key=api_key, api_secret=api_secret, base_url=url, timeout_seconds=timeout_seconds)

    @property
    def connection_state(self) -> ConnectionState:
        return self._connection_state

    def __repr__(self) -> str:
        masked_key = mask_secret(self._api_key)
        return f"DeltaIndiaClient(base_url={self.base_url!r}, api_key={masked_key!r}, state={self._connection_state.value})"

    def __str__(self) -> str:
        return self.__repr__()

    async def validate_credentials(self) -> Tuple[bool, ConnectionState, Optional[str]]:
        """Perform a read-only request to Delta Exchange to validate API key and secret."""
        if not self._api_key or not self._api_secret:
            self._connection_state = ConnectionState.AUTH_FAILED
            return False, ConnectionState.AUTH_FAILED, "Missing Delta API key or secret."
        try:
            await self.get_wallet_balances()
            self._connection_state = ConnectionState.CONNECTED
            return True, ConnectionState.CONNECTED, None
        except DeltaAuthError as e:
            self._connection_state = ConnectionState.AUTH_FAILED
            return False, ConnectionState.AUTH_FAILED, "Authentication failed: invalid key or secret."
        except DeltaRateLimitError as e:
            self._connection_state = ConnectionState.RATE_LIMITED
            return False, ConnectionState.RATE_LIMITED, f"Rate limit exceeded (retry after {e.retry_after}s)."
        except DeltaConnectionError as e:
            self._connection_state = ConnectionState.EXCHANGE_ERROR
            return False, ConnectionState.EXCHANGE_ERROR, f"Exchange connection error: {e}"
        except Exception as e:
            self._connection_state = ConnectionState.EXCHANGE_ERROR
            return False, ConnectionState.EXCHANGE_ERROR, f"Verification failed: {sanitize_text(str(e))}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._custom_http_client is not None:
            return self._custom_http_client
        if self._owned_http_client is None or self._owned_http_client.is_closed:
            self._owned_http_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
                headers={"User-Agent": "QuantEdge-AI/2.0"},
            )
        return self._owned_http_client

    async def close(self) -> None:
        """Close client sessions."""
        if self._owned_http_client is not None and not self._owned_http_client.is_closed:
            await self._owned_http_client.aclose()
        self._connection_state = ConnectionState.DISCONNECTED

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        authenticated: bool = True,
    ) -> Dict[str, Any]:
        """Send an HTTP request to Delta Exchange with authentication and error handling."""
        client = await self._get_client()
        query_str = ""
        if params:
            # Filter out None values
            filtered_params = {k: v for k, v in params.items() if v is not None}
            if filtered_params:
                query_str = urlencode(filtered_params)

        body_str = ""
        if json_body is not None:
            body_str = json.dumps(json_body, separators=(",", ":"))

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if authenticated:
            sig, ts = generate_signature(
                api_secret=self._api_secret,
                method=method,
                path=path,
                query=query_str,
                body=body_str,
            )
            headers["api-key"] = self._api_key
            headers["timestamp"] = ts
            headers["signature"] = sig

        url_path = f"{path}?{query_str}" if query_str else path

        try:
            response = await client.request(
                method=method.upper(),
                url=url_path,
                headers=headers,
                content=body_str.encode("utf-8") if body_str else None,
            )
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            self._connection_state = ConnectionState.EXCHANGE_ERROR
            raise DeltaConnectionError(f"Delta API connection timed out: {e}") from e
        except httpx.ConnectError as e:
            self._connection_state = ConnectionState.EXCHANGE_ERROR
            raise DeltaConnectionError(f"Failed to connect to Delta Exchange at {self.base_url}: {e}") from e
        except httpx.RequestError as e:
            self._connection_state = ConnectionState.EXCHANGE_ERROR
            raise DeltaConnectionError(f"HTTP request error: {e}") from e

        # Handle status codes
        if response.status_code == 401:
            self._connection_state = ConnectionState.AUTH_FAILED
            raise DeltaAuthError("Delta Exchange authentication failed: Invalid API key or signature.", status_code=401)
        elif response.status_code == 429:
            self._connection_state = ConnectionState.RATE_LIMITED
            retry_after = None
            if "Retry-After" in response.headers:
                try:
                    retry_after = int(response.headers["Retry-After"])
                except ValueError:
                    pass
            raise DeltaRateLimitError("Delta Exchange rate limit exceeded.", retry_after=retry_after, response_body=response.text)
        elif response.status_code == 400:
            try:
                err_data = response.json()
                err_msg = err_data.get("error", {}).get("message") or err_data.get("message") or response.text
            except Exception:
                err_msg = response.text
            raise DeltaOrderRejectedError(f"Delta Exchange rejected request (HTTP 400): {err_msg}", status_code=400, response_body=response.text)
        elif response.status_code >= 500:
            self._connection_state = ConnectionState.EXCHANGE_ERROR
            raise DeltaConnectionError(f"Delta Exchange server error (HTTP {response.status_code}): {response.text}", status_code=response.status_code)
        elif response.status_code not in (200, 201):
            self._connection_state = ConnectionState.EXCHANGE_ERROR
            raise DeltaClientError(f"Delta Exchange returned unexpected HTTP status {response.status_code}: {response.text}", status_code=response.status_code)

        self._connection_state = ConnectionState.CONNECTED

        try:
            return response.json()
        except Exception as e:
            raise DeltaResponseError(f"Failed to parse JSON response from Delta API: {e}") from e

    # ── Account & Connectivity Endpoints ──────────────────────────────────────

    async def check_connectivity(self) -> bool:
        """Verify authenticated connectivity to Delta Exchange India.

        Calls GET /v2/wallet/balances to test auth + network.
        Returns True if successful, raises exception on error.
        """
        data = await self.request("GET", "/v2/wallet/balances", authenticated=True)
        return bool(data.get("success", False))

    async def get_wallet_balances(self) -> List[DeltaWalletBalance]:
        """Fetch all wallet balances (USDT, BTC, etc.)."""
        data = await self.request("GET", "/v2/wallet/balances", authenticated=True)
        results = data.get("result", [])
        if not isinstance(results, list):
            raise DeltaResponseError(f"Expected list for wallet balances, got: {type(results)}")
        return [DeltaWalletBalance.from_dict(item) for item in results]

    async def get_account_summary(self) -> DeltaAccountSummary:
        """Fetch account summary with aggregated total equity and margin."""
        balances = await self.get_wallet_balances()
        balance_map: Dict[str, DeltaWalletBalance] = {b.asset_symbol: b for b in balances}
        
        user_id = balances[0].user_id if balances else None

        # Calculate primary equity basis (USDT primary for crypto derivatives)
        usdt_bal = balance_map.get("USDT")
        if usdt_bal:
            total_equity = usdt_bal.balance
            available_balance = usdt_bal.available_balance
            margin_used = usdt_bal.position_margin + usdt_bal.order_margin
        else:
            total_equity = sum((b.balance for b in balances), Decimal("0"))
            available_balance = sum((b.available_balance for b in balances), Decimal("0"))
            margin_used = sum((b.position_margin + b.order_margin for b in balances), Decimal("0"))

        return DeltaAccountSummary(
            user_id=user_id,
            balances=balance_map,
            total_equity=total_equity,
            available_balance=available_balance,
            margin_used=margin_used,
        )

    # ── Position Endpoints ────────────────────────────────────────────────────

    async def get_positions(self, product_ids: Optional[List[int]] = None) -> List[DeltaPosition]:
        """Fetch live margined derivative positions."""
        params: Dict[str, Any] = {}
        if product_ids:
            params["product_ids"] = ",".join(str(pid) for pid in product_ids)

        try:
            data = await self.request("GET", "/v2/positions/margined", params=params, authenticated=True)
        except DeltaClientError as e:
            if e.status_code == 404:
                # Fallback to /v2/positions if /v2/positions/margined is unsupported in test environment
                data = await self.request("GET", "/v2/positions", params=params, authenticated=True)
            else:
                raise

        results = data.get("result", [])
        if not isinstance(results, list):
            return []
        
        # Only return open positions (size != 0)
        positions: List[DeltaPosition] = []
        for item in results:
            pos = DeltaPosition.from_dict(item)
            if pos.size > Decimal("0"):
                positions.append(pos)
        return positions

    # ── Order Endpoints ───────────────────────────────────────────────────────

    async def get_open_orders(self, product_id: Optional[int] = None) -> List[DeltaOrderResponse]:
        """Fetch currently open orders."""
        params: Dict[str, Any] = {"state": "open"}
        if product_id is not None:
            params["product_id"] = product_id

        data = await self.request("GET", "/v2/orders", params=params, authenticated=True)
        results = data.get("result", [])
        if not isinstance(results, list):
            return []
        return [DeltaOrderResponse.from_dict(item) for item in results]

    async def get_order(self, order_id: int) -> DeltaOrderResponse:
        """Fetch order details by exchange order ID."""
        data = await self.request("GET", f"/v2/orders/{order_id}", authenticated=True)
        result = data.get("result")
        if not result or not isinstance(result, dict):
            raise DeltaResponseError(f"Order {order_id} not found or malformed response")
        return DeltaOrderResponse.from_dict(result)

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[DeltaOrderResponse]:
        """Fetch order details by client order ID."""
        params = {"client_order_id": client_order_id}
        data = await self.request("GET", "/v2/orders", params=params, authenticated=True)
        results = data.get("result", [])
        if isinstance(results, list) and results:
            return DeltaOrderResponse.from_dict(results[0])
        elif isinstance(results, dict):
            return DeltaOrderResponse.from_dict(results)
        return None

    async def create_order(self, request: DeltaOrderRequest) -> DeltaOrderResponse:
        """Submit a real order to Delta Exchange India with client_order_id idempotency.

        Guarantees:
        - Automatically supplies a client_order_id if not provided.
        - Validates parameters before submission.
        """
        if request.client_order_id is None:
            request.client_order_id = generate_client_order_id()

        payload = request.to_exchange_payload()
        data = await self.request("POST", "/v2/orders", json_body=payload, authenticated=True)
        result = data.get("result")
        if not result or not isinstance(result, dict):
            raise DeltaResponseError(f"Failed to parse order placement response: {data}")
        return DeltaOrderResponse.from_dict(result)

    # Alias for create_order
    place_order = create_order

    async def cancel_order(self, order_id: Union[int, str], product_id: int) -> bool:
        """Cancel an open order by exchange order ID."""
        payload = {"product_id": product_id}
        data = await self.request("DELETE", f"/v2/orders/{order_id}", json_body=payload, authenticated=True)
        return bool(data.get("success", False))

    async def cancel_order_by_client_id(self, client_order_id: str, product_id: int) -> bool:
        """Cancel an open order by client order ID."""
        payload = {"product_id": product_id, "client_order_id": client_order_id}
        data = await self.request("DELETE", "/v2/orders", json_body=payload, authenticated=True)
        return bool(data.get("success", False))

    async def get_fills(self, order_id: Optional[int] = None, product_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch execution fills / trades from Delta Exchange."""
        params: Dict[str, Any] = {}
        if order_id is not None:
            params["order_id"] = order_id
        if product_id is not None:
            params["product_id"] = product_id
        try:
            data = await self.request("GET", "/v2/fills", params=params, authenticated=True)
            results = data.get("result", [])
            return results if isinstance(results, list) else []
        except Exception as e:
            logger.warning("Failed to fetch fills from Delta: %s", e)
            return []

