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
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional, Dict, Any, List, Union, Tuple
from urllib.parse import quote, urlencode

import httpx

from quantedge.instruments import delta_india_registry
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


class DeltaListingIncompleteError(DeltaResponseError):
    """A listing could not be PROVEN to be the account's complete inventory.

    Raised instead of returning rows whose completeness is unestablished. Every
    consumer of `get_positions` / `get_open_orders` treats the returned list as
    the whole account: the synchronizer closes local positions missing from it,
    reconciliation reports what is missing as an orphan and clears blocking
    alerts when nothing is unresolved, and the pre-trade gate authorises a new
    order when it sees no exposure. A truncated page reaching any of those is
    indistinguishable from a flat account, so an unprovable listing is refused
    rather than reported. A subclass of `DeltaResponseError` so every existing
    fail-closed handler already catches it.
    """
    pass


class DeltaExecutionAuthorityError(DeltaClientError):
    """Raised when an unauthorized direct production order execution or cancellation is attempted from Python."""
    pass


def is_test_or_simulation_mode() -> bool:
    """Return True if executing within an authorized test runner or simulation context."""
    return bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("QUANTEDGE_TESTING") == "1"
        or os.environ.get("QUANTEDGE_ENV") in ("test", "simulation")
    )


# ── Helper Functions ──────────────────────────────────────────────────────────


#: Maximum number of `product_ids` this engine will put in a single query.
#:
#: Task O §O4 established this for `GET /v2/orders`, where it is the documented
#: cap. Task O §O6 applies the SAME number to `GET /v2/positions/margined`, and
#: the distinction matters: the maximum accepted by the positions endpoint is
#: NOT documented in the evidence available to this repository. Ten is used
#: there as an inherited, evidenced-elsewhere bound rather than as a verified
#: `/v2/positions/margined` contract, because a cap that is possibly *stricter*
#: than the exchange's real limit fails closed -- an over-long list is refused
#: locally instead of being silently truncated into an answer about the wrong
#: instruments. The real positions maximum remains an open Phase 2 question.
_MAX_PRODUCT_IDS = 10


def _validated_product_ids(product_ids: List[int], endpoint: str) -> List[int]:
    """Refuse a malformed product filter before any request leaves the process.

    Task O §O6. `get_positions` used to build its CSV straight out of the
    caller's list -- `",".join(str(pid) for pid in product_ids)` -- so `2.5`,
    `True`, `None`, `-1` and `"27; DROP"` all serialized into a live query, and
    an over-long list was truncated by the exchange rather than refused here.
    Either way the answer describes instruments the caller never asked about,
    and every consumer of a positions snapshot treats it as authoritative.

    Only exact positive integers are accepted. `bool` is rejected explicitly
    because it is an `int` subclass, so `True` would otherwise serialize as
    product 1. Values are not coerced: rule #8 forbids guessing a product id,
    and `int(2.5)` is a guess.
    """
    ids: List[int] = []
    for pid in product_ids:
        if isinstance(pid, bool) or not isinstance(pid, int):
            raise DeltaResponseError(
                f"{endpoint} product_ids must be exact integers; "
                f"got {pid!r} ({type(pid).__name__})"
            )
        if pid <= 0:
            raise DeltaResponseError(
                f"{endpoint} product_ids must be positive; got {pid!r}"
            )
        ids.append(pid)

    # Exceeding the cap would silently truncate the filter, i.e. answer about
    # the wrong instruments, so it fails closed instead.
    if len(ids) > _MAX_PRODUCT_IDS:
        raise DeltaResponseError(
            f"{endpoint} accepts at most {_MAX_PRODUCT_IDS} product_ids; "
            f"{len(ids)} were requested"
        )
    return ids


# ── Listing completeness (P1-2) ───────────────────────────────────────────────
#
# `get_positions` and `get_open_orders` answer the question "what does this
# account hold, in full?". Nothing in this repository establishes Delta's
# pagination semantics for either endpoint: no cursor is sent today, no `meta`
# field is read today, and no captured envelope or contract document in the tree
# describes one. Under safety rules #13/#15/#16 that leaves exactly one
# defensible design:
#
#   * A response whose envelope shows NO forward cursor is the complete listing,
#     which is what the code already assumed -- so an account whose whole book
#     fits in one page behaves byte-identically to before this change.
#   * A response that offers a forward page in the ONE self-describing dialect
#     (`meta.after` -> request `after=`) is followed to exhaustion, so a position
#     or order that lives on page 2 is really discovered.
#   * ANY other indication that more rows exist -- an unrecognised pagination
#     key, a non-mapping `meta`, a cursor that does not advance, a repeated row,
#     a page budget exhausted, or (on an unfiltered query) a `total_count`
#     larger than what was actually collected -- raises
#     `DeltaListingIncompleteError`. It is never treated as a complete listing.
#
# The `meta.after` -> `after=` pairing is an INFERENCE from the symmetry of the
# field names, not a verified contract, and it is the only inference here. It is
# also the only one whose failure mode is safe: if Delta does not accept `after`,
# the follow-up page either errors (propagating) or returns the same rows again
# (caught as a non-advancing cursor). Nothing in this module can conclude
# "complete" from a guess.

#: Page budget for one listing. Reaching it is a refusal, never a truncation.
MAX_LISTING_PAGES = 50

#: The one forward-cursor field this module knows how to follow, and the query
#: parameter it is sent back as.
_LISTING_CURSOR_META_KEY = "after"
_LISTING_CURSOR_PARAM = "after"

#: Envelope keys that would mean "there is more", in dialects this module does
#: NOT know how to follow. A truthy value in any of them fails closed.
_UNSUPPORTED_PAGINATION_KEYS = (
    "next", "next_cursor", "next_page", "cursor", "page", "page_token",
    "offset", "has_more", "has_next", "more", "last_evaluated_key",
)


def _forward_listing_cursor(envelope: Dict[str, Any], *, what: str) -> Optional[str]:
    """The cursor for the next page, or `None` when this page is the last one.

    Raises `DeltaListingIncompleteError` when the envelope says more rows exist
    but does not say so in the one dialect this module can follow safely.
    """
    if not isinstance(envelope, dict):
        raise DeltaListingIncompleteError(
            f"{what}: response envelope is {type(envelope).__name__}, not a "
            f"mapping, so listing completeness cannot be established"
        )

    meta = envelope.get("meta")
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise DeltaListingIncompleteError(
            f"{what}: response carried a non-mapping `meta` "
            f"({type(meta).__name__}); refusing to treat this page as the "
            f"complete account inventory"
        )

    unsupported = sorted(k for k in _UNSUPPORTED_PAGINATION_KEYS if meta.get(k))
    if unsupported:
        raise DeltaListingIncompleteError(
            f"{what}: response `meta` uses pagination field(s) {unsupported} "
            f"whose semantics are not established by this repository; refusing "
            f"to treat the rows received as the complete account inventory"
        )

    raw = meta.get(_LISTING_CURSOR_META_KEY)
    # Delta returns the cursor fields as null on a terminal page.
    if raw is None or raw is False or raw == "":
        return None
    if not isinstance(raw, str):
        raise DeltaListingIncompleteError(
            f"{what}: response `meta.{_LISTING_CURSOR_META_KEY}` is "
            f"{type(raw).__name__} ({raw!r}), not the documented opaque string "
            f"cursor; refusing to guess how to request the next page"
        )
    return raw


def _assert_listing_count_consistent(
    envelope: Dict[str, Any], collected: int, *, what: str, unfiltered: bool
) -> None:
    """Fail closed when an unfiltered listing returned fewer rows than it claims.

    `total_count` is only consulted for a query that carried NO `product_ids`
    filter. For an unfiltered account-wide listing, "rows the exchange says
    exist" and "rows received" must agree under any reading of the field. For a
    FILTERED query the scope of `total_count` (filtered vs account-wide) is not
    established by this repository, so it is deliberately not used -- inventing
    a reading of it there could block reconciliation permanently on a value that
    was never about the filtered set.
    """
    if not unfiltered or not isinstance(envelope, dict):
        return
    meta = envelope.get("meta")
    if not isinstance(meta, dict):
        return
    total = meta.get("total_count")
    if isinstance(total, bool) or not isinstance(total, int):
        return
    if total > collected:
        raise DeltaListingIncompleteError(
            f"{what}: the exchange reports total_count={total} but only "
            f"{collected} row(s) were received and no further page was offered; "
            f"refusing to treat a partial listing as the complete account "
            f"inventory"
        )


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
    """Generate a unique, collision-resistant client order identifier (max 32 chars).

    Format: {prefix[:6]}-{timestamp_ms}-{uuid_hex_6}
    """
    clean_prefix = prefix[:6]
    ts_ms = int(time.time() * 1000)
    rand_suffix = uuid.uuid4().hex[:6]
    cid = f"{clean_prefix}-{ts_ms}-{rand_suffix}"
    return cid[:32]


#: Maximum length of a `client_order_id` this engine will submit.
#:
#: Both generators in this module have always self-imposed 32 characters, and no
#: larger exchange-documented maximum is recorded anywhere in this repository, so
#: 32 is treated as the binding constraint rather than relaxed upward (rule #16:
#: an unverified exchange limit is not guessed).
CLIENT_ORDER_ID_MAX_LENGTH = 32

#: Hex characters of the identity digest kept in a deterministic client order id.
#:
#: 20 hex characters is 80 bits. The population being separated is the set of
#: (account, setup, role) triples one deployment will ever submit -- thousands,
#: not billions -- so the birthday bound sits many orders of magnitude below
#: every other failure mode this package models. It also fits: "QE-" + 20 + "-"
#: + "ENTRY" is 29 of the 32 characters available.
DETERMINISTIC_CLIENT_ORDER_ID_DIGEST_CHARS = 20


def _canonical_order_identity(account_id: str, setup_id: str, role: str) -> str:
    """Return the unambiguous identity string naming exactly one order.

    Length-prefixed rather than delimiter-joined. `("ab", "c")` and `("a", "bc")`
    must not produce the same string, and no separator character can be assumed
    absent from an account id or a setup id -- setup ids already contain both
    `_` and `-`. `2:ab|1:c` and `1:a|2:bc` differ whatever the fields contain,
    so the encoding is injective by construction rather than by convention.
    """
    return "|".join(f"{len(part)}:{part}" for part in (account_id, setup_id, role))


def generate_deterministic_client_order_id(account_id: str, setup_id: str, role: str = "ENTRY") -> str:
    """Generate a deterministic, idempotent client order ID for (account, setup, role).

    This is the ONE canonical generator for every order the trade lifecycle
    submits. Determinism is the point: a restarted process must be able to
    recompute the name of an order it already sent, so
    `get_order_by_client_id(record.entry_client_order_id)` resolves the same
    order after a restart as before it.

    The identity is the COMPLETE triple. It used to be a truncated prefix of it
    --- the first 8 non-dash characters of the account id and the first 12 of the
    setup id --- which made the name of a Manual SMC order
    `QE-{acct8}-XRPUSD_1h_MA-ENTRY` for every setup on one symbol and timeframe,
    LONG and SHORT alike, because `direction` sits far beyond character 12. Two
    different setups therefore submitted under one name, and
    `_resolve_entry_order` could bind another setup's exchange order into this
    record. That is now impossible: the full account id, the full setup id and
    the full role are hashed, so two ids are equal only if all three inputs are.

    The readable role suffix is decoration, not identity. It is kept because an
    operator reading an order book should be able to see which leg an order is,
    and it is safe to truncate to fit `CLIENT_ORDER_ID_MAX_LENGTH` precisely
    because the digest ahead of it already separates the full identities --- a
    cut suffix cannot make two different identities agree.

    Normalization matches the previous behaviour exactly where it mattered: the
    role is upper-cased (so a caller cannot mint a second name for one leg by
    spelling it "sl") and surrounding whitespace is stripped. Account and setup
    ids are NOT case-folded and NOT stripped of dashes --- folding would discard
    identity, which is the defect being fixed.

    Character set is a strict subset of what the old scheme emitted: `QE`, upper
    hex, `-`, and the caller's role token. Nothing wider is introduced, so no
    exchange constraint the old ids satisfied can be newly violated.
    """
    account = str(account_id)
    setup = str(setup_id)
    normalized_role = str(role).strip().upper()

    digest = hashlib.sha256(
        _canonical_order_identity(account, setup, normalized_role).encode("utf-8")
    ).hexdigest()[:DETERMINISTIC_CLIENT_ORDER_ID_DIGEST_CHARS].upper()

    cid = f"QE-{digest}-{normalized_role}"
    return cid[:CLIENT_ORDER_ID_MAX_LENGTH]


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
        allow_direct_execution: Optional[bool] = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._custom_http_client = http_client
        self._owned_http_client: Optional[httpx.AsyncClient] = None
        self._connection_state: ConnectionState = ConnectionState.UNKNOWN
        if allow_direct_execution is None:
            self._allow_direct_execution = is_test_or_simulation_mode()
        else:
            self._allow_direct_execution = allow_direct_execution

    @classmethod
    def from_env(
        cls,
        base_url: Optional[str] = None,
        timeout_seconds: float = 10.0,
        allow_direct_execution: Optional[bool] = None,
    ) -> "DeltaIndiaClient":
        """Instantiate client securely from environment variables, loading .env if present."""
        load_project_env()
        api_key = os.getenv("DELTA_API_KEY", "").strip()
        api_secret = os.getenv("DELTA_API_SECRET", "").strip()
        url = base_url or os.getenv("DELTA_BASE_URL", DELTA_INDIA_PRODUCTION_URL).strip()
        if not api_key or not api_secret:
            raise ValueError("DELTA_API_KEY and DELTA_API_SECRET environment variables must be set.")
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            base_url=url,
            timeout_seconds=timeout_seconds,
            allow_direct_execution=allow_direct_execution,
        )

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
            # Bind to IPv4 0.0.0.0 to ensure requests route through whitelisted IPv4 address
            transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
            self._owned_http_client = httpx.AsyncClient(
                base_url=self.base_url,
                transport=transport,
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
            data = response.json()
        except Exception as e:
            raise DeltaResponseError(f"Failed to parse JSON response from Delta API: {e}") from e

        # Task O §O9: the envelope is checked HERE, once, for every endpoint.
        #
        # Delta answers an application-level failure with HTTP 200 and
        # `{"success": false, ...}`. The status ladder above cannot see that, so
        # until now the failure was returned as if it were a valid answer, and
        # each endpoint then applied `data.get("result", [])`. That default
        # turned "the exchange refused to tell us" into an EMPTY COLLECTION:
        # no wallet rows (and therefore, via `get_account_summary`, a fabricated
        # zero-equity account), no positions, no open orders. The §O6/§O7/§O8
        # guards could not help, because they are row-level -- zero rows means
        # there is nothing left for them to refuse. Downstream that empty reads
        # as a flat exchange, which closes local positions, infers resting
        # orders away, and force-releases the single-trade lock.
        #
        # UNKNOWN is not FLAT and a default must never answer a safety
        # question, so an unsuccessful envelope is refused before `result` is
        # ever looked at, and nothing is salvaged from it. `success` is required
        # to be present and truthy: an envelope that never says it worked has
        # not said it worked. This mirrors the existing `get_ticker` check and
        # the ingestion path, which both refuse a falsy-or-absent `success` on
        # this same host. The exception is the existing `DeltaResponseError` --
        # the transport succeeded and the *response* is unusable -- and
        # `_connection_state` stays CONNECTED for exactly that reason. The
        # status ladder keeps running first, so `DeltaAuthError`,
        # `DeltaRateLimitError`, `DeltaOrderRejectedError` and
        # `DeltaConnectionError` keep their own taxonomy.
        if not isinstance(data, dict):
            raise DeltaResponseError(
                f"Delta {method.upper()} {path} returned a {type(data).__name__} "
                f"body, not the documented success/result envelope"
            )

        if not data.get("success", False):
            raise DeltaResponseError(
                f"Delta {method.upper()} {path} was unsuccessful: "
                f"{sanitize_text(str(data.get('error', data)))}"
            )

        return data

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

    # ── Market Data Endpoints ─────────────────────────────────────────────────

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch the live ticker for one registered Delta India instrument.

        Endpoint: ``GET /v2/tickers/{symbol}`` on the configured base URL. Per
        Delta Exchange India's published REST reference this route is PUBLIC (no
        authentication required) and returns the standard
        ``{"success": bool, "result": {...}}`` envelope whose ``result`` is a
        SINGLE object when one symbol is requested. In that object
        ``mark_price``/``spot_price`` are quoted strings while
        ``close``/``open``/``high``/``low``/``volume``/``product_id`` are
        unquoted numbers -- which is why the value is normalised through
        ``str()`` before ``Decimal`` rather than assumed to be either.

        The caller sizes real capital off ``mark_price``, so every ambiguity
        fails closed instead of degrading to a plausible number:
        - the symbol must resolve in the pinned instrument registry, so an
          unregistered or ``.P`` symbol never reaches the exchange;
        - ``result`` must be a dict (a list/None/absent payload raises);
        - ``result["symbol"]`` must equal the requested symbol, so one product
          can never be priced from another product's ticker;
        - ``mark_price`` must be present and parse to a finite, strictly
          positive ``Decimal``.

        Raises:
            UnknownInstrumentError: symbol is not a registered instrument.
            DeltaResponseError: the exchange's answer is missing, malformed, for
                the wrong product, or carries an unusable mark price.
        """
        exchange_symbol = delta_india_registry().get(symbol).symbol

        data = await self.request(
            "GET",
            f"/v2/tickers/{exchange_symbol}",
            authenticated=False,
        )

        if not data.get("success", False):
            raise DeltaResponseError(
                f"Delta ticker request for {exchange_symbol} was unsuccessful: "
                f"{sanitize_text(str(data.get('error', data)))}"
            )

        result = data.get("result")
        if not isinstance(result, dict):
            raise DeltaResponseError(
                f"Expected a single ticker object for {exchange_symbol}, got: "
                f"{type(result).__name__}"
            )

        returned_symbol = result.get("symbol")
        if returned_symbol != exchange_symbol:
            raise DeltaResponseError(
                f"Ticker identity mismatch: requested {exchange_symbol}, "
                f"exchange returned {returned_symbol!r}"
            )

        raw_mark_price = result.get("mark_price")
        if raw_mark_price is None or raw_mark_price == "":
            raise DeltaResponseError(
                f"Ticker for {exchange_symbol} carries no mark_price; refusing "
                f"to price an order without an authoritative mark"
            )
        try:
            mark_price = Decimal(str(raw_mark_price))
        except (InvalidOperation, ValueError) as e:
            raise DeltaResponseError(
                f"Ticker for {exchange_symbol} has an unparseable mark_price "
                f"{raw_mark_price!r}"
            ) from e
        if not mark_price.is_finite():
            raise DeltaResponseError(
                f"Ticker for {exchange_symbol} has a non-finite mark_price "
                f"{raw_mark_price!r}"
            )
        if mark_price <= Decimal("0"):
            raise DeltaResponseError(
                f"Ticker for {exchange_symbol} has a non-positive mark_price "
                f"{raw_mark_price!r}"
            )

        logger.debug(
            "TICKER_FETCHED | symbol=%s mark_price=%s", exchange_symbol, mark_price
        )
        return result

    # ── Complete-inventory listings (P1-2) ────────────────────────────────────

    async def _fetch_complete_listing(
        self,
        path: str,
        *,
        params: Dict[str, Any],
        what: str,
        row_identity: Callable[[Any], Any],
        unfiltered: bool,
    ) -> List[Any]:
        """Return EVERY row of a listing, or refuse to return any of them.

        Follows `meta.after` to exhaustion and fails closed on anything that
        indicates more rows exist without saying so followably. See the
        "Listing completeness" note above `MAX_LISTING_PAGES` for why this is the
        only safe shape available from this repository's evidence.

        Raises:
            DeltaResponseError: `result` is not the documented list.
            DeltaListingIncompleteError: completeness could not be established.
        """
        rows: List[Any] = []
        seen_rows: set = set()
        seen_cursors: set = set()
        page_params = dict(params)

        for page in range(1, MAX_LISTING_PAGES + 1):
            data = await self.request("GET", path, params=page_params, authenticated=True)
            results = data.get("result", [])
            if not isinstance(results, list):
                raise DeltaResponseError(
                    f"Expected list for {what}, got: {type(results)}"
                )

            for item in results:
                key = row_identity(item)
                if key is not None:
                    if key in seen_rows:
                        # The same row on two pages means the cursor did not
                        # advance. Continuing would loop; de-duplicating would
                        # silently accept a listing whose paging is broken.
                        raise DeltaListingIncompleteError(
                            f"{what}: page {page} repeated row {key!r}, so the "
                            f"exchange's paging did not advance; refusing to "
                            f"treat the rows received as complete"
                        )
                    seen_rows.add(key)
                rows.append(item)

            cursor = _forward_listing_cursor(data, what=what)
            if cursor is None:
                _assert_listing_count_consistent(
                    data, len(rows), what=what, unfiltered=unfiltered
                )
                if page > 1:
                    logger.info(
                        "LISTING_PAGINATED path=%s pages=%s rows=%s", path, page, len(rows)
                    )
                return rows

            if cursor in seen_cursors:
                raise DeltaListingIncompleteError(
                    f"{what}: the exchange re-issued cursor {cursor!r} after "
                    f"{page} page(s); refusing to loop or to treat the rows "
                    f"received as complete"
                )
            seen_cursors.add(cursor)
            # Rebuilt from the ORIGINAL params each time so the filter and
            # states stay exactly as the caller specified and only the cursor
            # moves. Ordering is whatever the exchange returned, preserved.
            page_params = dict(params)
            page_params[_LISTING_CURSOR_PARAM] = cursor

        raise DeltaListingIncompleteError(
            f"{what}: still offering further pages after {MAX_LISTING_PAGES} "
            f"page(s) ({len(rows)} rows); refusing to keep paging or to treat "
            f"what was received as the complete account inventory"
        )

    # ── Position Endpoints ────────────────────────────────────────────────────

    async def get_positions(self, product_ids: Optional[List[int]] = None) -> List[DeltaPosition]:
        """Fetch live margined derivative positions.

        Task O §O6. Three fabrications were removed from this path, all of which
        produced the same dangerous answer -- *no positions* -- from something
        that was not an observation of flatness:

        1. The `product_ids` CSV was built with no validation, so a malformed or
           over-long filter asked about the wrong instruments (see
           `_validated_product_ids`).
        2. An HTTP 404 was answered by silently retrying `/v2/positions`, a
           different endpoint with a different response shape whose result was
           then trusted as authoritative position state. That fallback was
           undocumented and untested, so a transport, routing or permission
           failure became a second opinion. A 404 now propagates as
           `DeltaClientError(status_code=404)` and every caller's existing
           fail-closed path handles it.
        3. A non-list `result` returned `[]`, so a malformed envelope was
           reported as an empty exchange. It now raises, matching
           `get_wallet_balances`.

        This matters because four consumers act on the snapshot: the
        synchronizer CLOSES every local position missing from it, the trade
        lifecycle CLEARS blocking reconciliation alerts on a clean run,
        reconciliation force-releases the single-trade lock when the exchange
        looks flat, and the pre-trade gate AUTHORIZES a new order when it sees
        no exposure.

        P1-2: for exactly those four reasons the listing must be the COMPLETE
        inventory. Pages are followed to exhaustion and any listing whose
        completeness cannot be established is refused rather than returned --
        see `_fetch_complete_listing`. A single-page account is unaffected.

        Raises:
            DeltaResponseError: the product filter is malformed, or the
                exchange's answer is not the documented list.
            DeltaListingIncompleteError: the listing could not be proven
                complete.
            DeltaClientError: the endpoint answered non-2xx, 404 included.
        """
        params: Dict[str, Any] = {}
        if product_ids is not None:
            ids = _validated_product_ids(product_ids, "GET /v2/positions/margined")
            if ids:
                params["product_ids"] = ",".join(str(pid) for pid in ids)

        results = await self._fetch_complete_listing(
            "/v2/positions/margined",
            params=params,
            what="positions",
            # `product_id` is the position identity: one product cannot hold two
            # distinct margined positions, so the same product id twice is a
            # repeated row rather than two positions.
            row_identity=lambda item: (
                item.get("product_id") if isinstance(item, dict) else None
            ),
            unfiltered="product_ids" not in params,
        )

        # Only return open positions (size != 0). `DeltaPosition.from_dict`
        # refuses an entry that carries no size at all, so nothing reaches this
        # filter with a fabricated zero.
        positions: List[DeltaPosition] = []
        for item in results:
            pos = DeltaPosition.from_dict(item)
            if pos.size > Decimal("0"):
                positions.append(pos)
        return positions

    # ── Order Endpoints ───────────────────────────────────────────────────────

    async def get_open_orders(
        self,
        product_id: Optional[int] = None,
        product_ids: Optional[List[int]] = None,
    ) -> List[DeltaOrderResponse]:
        """Fetch every order that is still working on the exchange (Task O §O4).

        The documented `GET /v2/orders` query parameters are `states` and
        `product_ids`, both CSV, both capped at 10 values. The previous
        singular `state` / `product_id` were not documented parameters at all,
        so the exchange was free to ignore them and answer with something other
        than this account's open orders.

        `states` is `open,pending` rather than `open` alone, and that is a
        safety requirement rather than a preference: the bracket stop-loss this
        engine places is a `stop_loss_order`, an untriggered stop rests in the
        documented `pending` state, and `reconcile_with_exchange` decides
        `sl_live` from membership of this list. Asking only for `open` would
        report healthy protection as missing, and the reconciliation path
        responds to missing protection by discarding the order id and placing
        the bracket again -- duplicating a live stop. Reporting a resting order
        that exists is the fail-closed direction.

        Task O §O6 removed one further fabrication here. A non-list `result`
        used to return `[]`, so a malformed envelope was reported as *no working
        orders* -- and `reconcile_account` computes
        `exchange_is_flat = not exchange_positions and not exchange_orders`, so
        the orders half carries the same false-flatness blast radius as the
        positions half: up to and including force-releasing the single-trade
        lock. It now raises, matching `get_positions` and
        `get_wallet_balances`.

        Raises:
            DeltaResponseError: the product filter is malformed, or the
                exchange's answer is not the documented list.
            DeltaListingIncompleteError: the listing could not be proven
                complete (P1-2). `reconcile_account` computes
                `exchange_is_flat = not exchange_positions and not
                exchange_orders`, so a truncated page here carries the same
                false-flatness blast radius as a malformed envelope.
        """
        ids: List[int] = list(product_ids or [])
        if product_id is not None:
            ids.append(int(product_id))
        # Task O §O6: the cap and the per-value checks live in
        # `_validated_product_ids`, shared with `get_positions`, so there is one
        # implementation of this rule rather than two that can drift apart.
        ids = _validated_product_ids(ids, "GET /v2/orders")

        params: Dict[str, Any] = {"states": "open,pending"}
        if ids:
            params["product_ids"] = ",".join(str(pid) for pid in ids)

        results = await self._fetch_complete_listing(
            "/v2/orders",
            params=params,
            what="orders",
            row_identity=lambda item: (
                item.get("id") if isinstance(item, dict) else None
            ),
            unfiltered="product_ids" not in params,
        )
        return [DeltaOrderResponse.from_dict(item) for item in results]

    async def get_order(self, order_id: int) -> DeltaOrderResponse:
        """Fetch order details by exchange order ID."""
        data = await self.request("GET", f"/v2/orders/{order_id}", authenticated=True)
        result = data.get("result")
        if not result or not isinstance(result, dict):
            raise DeltaResponseError(f"Order {order_id} not found or malformed response")
        return DeltaOrderResponse.from_dict(result)

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[DeltaOrderResponse]:
        """Fetch one order by client order id, or fail closed (Task O §O4).

        The documented lookup is a PATH, `GET /v2/orders/client_order_id/{id}`,
        which addresses exactly one order. The previous implementation queried
        `GET /v2/orders?client_order_id=...` -- an undocumented filter -- and
        then adopted `results[0]` whatever it happened to be.

        That mattered because the single caller, `_resolve_entry_order`, copies
        the returned `id` into `record.entry_order_id`, and the cancel and
        bracket paths then act on that id. Adopting an unverified row is
        therefore a route to cancelling or tracking somebody else's order. So
        the identity is checked byte-for-byte here: no case-folding, no
        whitespace tolerance, no "close enough". A mismatch raises instead of
        returning a wrong order, and the caller already treats a raise as
        ENTRY_STATE_UNKNOWN.
        """
        if not isinstance(client_order_id, str) or client_order_id.strip() == "":
            # A blank id would address `/v2/orders/client_order_id/`, i.e. the
            # collection, and any order in the answer could then be adopted.
            raise DeltaResponseError(
                f"{client_order_id!r} is not a usable client_order_id for a lookup"
            )

        path = f"/v2/orders/client_order_id/{quote(client_order_id, safe='')}"
        data = await self.request("GET", path, authenticated=True)
        result = data.get("result")
        if result is None:
            return None
        if not isinstance(result, dict):
            # A list here means the endpoint did not behave as a single-order
            # lookup; picking an element would be exactly the old defect.
            raise DeltaResponseError(
                f"client_order_id lookup for {client_order_id!r} returned "
                f"{type(result).__name__}, not a single order object"
            )

        returned = result.get("client_order_id")
        if returned != client_order_id:
            raise DeltaResponseError(
                f"client_order_id lookup returned {returned!r} for a request for "
                f"{client_order_id!r}; refusing to adopt an order that is not ours"
            )
        return DeltaOrderResponse.from_dict(result)

    async def create_order(self, request: DeltaOrderRequest) -> DeltaOrderResponse:
        """Submit an order to Delta Exchange India with client_order_id idempotency.

        Guarantees:
        - Structural Authority Protection: Direct production order execution from Python is prohibited.
        - OrderExecutionService in Java Spring Boot is the sole authoritative production execution authority.
        - Automatically supplies a client_order_id if not provided.
        - Validates parameters before submission.
        """
        if not self._allow_direct_execution and not is_test_or_simulation_mode():
            raise DeltaExecutionAuthorityError(
                "Direct order submission from Python engine is disabled in production. "
                "OrderExecutionService in Java Spring Boot is the sole authoritative production execution authority."
            )

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
        """Cancel an open order by exchange order ID (Task O §O4).

        The documented cancel is `DELETE /v2/orders` carrying the order in the
        BODY as `{id, client_order_id, product_id}`. The previous form put the
        id in the path, `DELETE /v2/orders/{id}`, and sent only `product_id` in
        the body: an undocumented route whose body named no order at all. If the
        exchange were to route that path to the collection endpoint, a cancel
        aimed at one order would be a cancel with no order identified.
        """
        if not self._allow_direct_execution and not is_test_or_simulation_mode():
            raise DeltaExecutionAuthorityError(
                "Direct order cancellation from Python engine is disabled in production. "
                "OrderExecutionService in Java Spring Boot is the sole authoritative production execution authority."
            )
        # `id` is documented as an integer. A value that is not integral cannot
        # be sent as one without inventing an order id, so it fails closed.
        try:
            numeric_id = int(str(order_id).strip())
        except (TypeError, ValueError):
            raise DeltaResponseError(
                f"{order_id!r} is not a usable exchange order id for a cancel"
            ) from None

        payload = {"id": numeric_id, "product_id": product_id}
        data = await self.request("DELETE", "/v2/orders", json_body=payload, authenticated=True)
        return bool(data.get("success", False))

    async def cancel_order_by_client_id(self, client_order_id: str, product_id: int) -> bool:
        """Cancel an open order by client order ID."""
        if not self._allow_direct_execution and not is_test_or_simulation_mode():
            raise DeltaExecutionAuthorityError(
                "Direct order cancellation from Python engine is disabled in production. "
                "OrderExecutionService in Java Spring Boot is the sole authoritative production execution authority."
            )
        payload = {"product_id": product_id, "client_order_id": client_order_id}
        data = await self.request("DELETE", "/v2/orders", json_body=payload, authenticated=True)
        return bool(data.get("success", False))

    async def get_fills(self, order_id: Optional[int] = None, product_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch execution fills / trades from Delta Exchange (Task O §O4).

        `GET /v2/fills` documents no `order_id` filter. Sending one meant the
        exchange was free to ignore it and answer with every recent fill, which
        the caller would then read as "the fills for this order". The filter is
        therefore applied locally, against the `order_id` each returned fill
        carries, and a fill that does not state its order is not attributed to
        one.

        Exceptions are no longer swallowed into an empty list: "no fills" and
        "we could not find out" are different facts, and returning the first for
        the second is how an unfilled entry gets mistaken for a flat account.
        """
        params: Dict[str, Any] = {}
        if product_id is not None:
            params["product_id"] = product_id

        data = await self.request("GET", "/v2/fills", params=params, authenticated=True)
        results = data.get("result", [])
        if not isinstance(results, list):
            return []
        if order_id is None:
            return results

        wanted = str(order_id)
        return [
            fill for fill in results
            if isinstance(fill, dict) and str(fill.get("order_id", "")) == wanted
        ]

