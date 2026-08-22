"""
Backend Client — Python Engine → Java Backend HTTP Bridge.

Replaces the in-memory export_state / load_state pattern with real
PostgreSQL persistence through the Java backend's internal engine API.

Architecture contract:
  Python engine (logic) → BackendClient → Java backend → PostgreSQL

The Python engine NEVER holds credentials.
The Python engine NEVER writes directly to PostgreSQL.
All authoritative state (balance, lock, P&L) is owned by Java/PostgreSQL.

Environment variables:
    BACKEND_BASE_URL          - Java backend URL (default: http://localhost:8080)
    BACKEND_API_KEY           - Internal engine API key (from PYTHON_ENGINE_API_KEY)
    BACKEND_ACCOUNT_ID        - Trading account UUID to operate on
    BACKEND_REQUEST_TIMEOUT   - HTTP timeout in seconds (default: 10)
    BACKEND_MAX_RETRIES       - Max retry count for transient failures (default: 3)
"""

import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import urllib.request
import urllib.error
import json

logger = logging.getLogger("backend_client")

# ── Environment Configuration ─────────────────────────────────────────────────

BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8080")
BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY", "")
BACKEND_ACCOUNT_ID = os.environ.get("BACKEND_ACCOUNT_ID", "")
REQUEST_TIMEOUT = int(os.environ.get("BACKEND_REQUEST_TIMEOUT", "10"))
MAX_RETRIES = int(os.environ.get("BACKEND_MAX_RETRIES", "3"))


# ── DTOs ──────────────────────────────────────────────────────────────────────

@dataclass
class AccountStateSnapshot:
    """Mirrors TradePersistenceService.AccountStateSnapshot from Java."""
    account_id: str
    has_active_trade: bool
    active_setup_id: Optional[str]
    active_symbol: Optional[str]
    active_lock_state: Optional[str]
    lock_acquired_at: Optional[str]
    current_balance: Decimal
    next_trade_capital: Decimal
    latest_post_trade_balance: Optional[Decimal]
    total_closed_trades: int
    total_net_pnl: Decimal
    total_fees_paid: Decimal
    algo_enabled: bool
    kill_switch_active: bool


@dataclass
class TradeOpenResult:
    success: bool
    trade_record_id: Optional[str]
    lock_id: Optional[str]
    error: Optional[str]


@dataclass
class TradeCloseResult:
    success: bool
    net_pnl: Optional[Decimal]
    post_trade_balance: Optional[Decimal]
    error: Optional[str]


# ── Client ────────────────────────────────────────────────────────────────────

class BackendClient:
    """
    HTTP client for the Java backend's internal engine API.

    All methods are synchronous (engine calls are sequential by design).
    Retries are applied only to transient network errors, NOT to business
    logic errors (4xx responses are returned immediately).
    """

    def __init__(
        self,
        base_url: str = BACKEND_BASE_URL,
        api_key: str = BACKEND_API_KEY,
        account_id: str = BACKEND_ACCOUNT_ID,
        timeout: int = REQUEST_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._account_id = account_id
        self._timeout = timeout
        self._max_retries = max_retries

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            h["X-Engine-Api-Key"] = self._api_key
        return h

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = f"{self._base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None

        last_error: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8") if e.fp else ""
                # 4xx errors are business failures — do not retry
                if 400 <= e.code < 500:
                    logger.warning("%s %s → HTTP %d: %s", method, url, e.code, raw)
                    try:
                        return json.loads(raw)
                    except Exception:
                        return {"success": False, "error": f"HTTP {e.code}", "data": None}
                # 5xx — retry
                logger.warning("%s %s → HTTP %d (attempt %d/%d): %s",
                               method, url, e.code, attempt, self._max_retries, raw)
                last_error = e
            except (urllib.error.URLError, OSError) as e:
                logger.warning("%s %s → network error (attempt %d/%d): %s",
                               method, url, attempt, self._max_retries, e)
                last_error = e

            if attempt < self._max_retries:
                time.sleep(min(2 ** attempt, 8))  # exponential backoff, max 8s

        raise BackendClientError(
            f"Backend request failed after {self._max_retries} attempts: {last_error}"
        ) from last_error

    # ── State Queries ─────────────────────────────────────────────────────────

    def get_account_state(self, account_id: Optional[str] = None) -> AccountStateSnapshot:
        """
        Fetches the authoritative account state from PostgreSQL via the Java backend.
        Called on engine startup to replace in-memory state loading.

        Returns an AccountStateSnapshot. If has_active_trade=True the engine
        must reconcile before allowing a new signal.
        """
        aid = account_id or self._account_id
        if not aid:
            raise BackendClientError("account_id is required")

        resp = self._request("GET", f"/api/engine/state/{aid}")
        return AccountStateSnapshot(
            account_id=resp.get("accountId", aid),
            has_active_trade=resp.get("hasActiveTrade", False),
            active_setup_id=resp.get("activeSetupId"),
            active_symbol=resp.get("activeSymbol"),
            active_lock_state=resp.get("activeLockState"),
            lock_acquired_at=resp.get("lockAcquiredAt"),
            current_balance=Decimal(str(resp.get("currentBalance", 0))),
            next_trade_capital=Decimal(str(resp.get("nextTradeCapital", 0))),
            latest_post_trade_balance=(
                Decimal(str(resp["latestPostTradeBalance"]))
                if resp.get("latestPostTradeBalance") is not None else None
            ),
            total_closed_trades=int(resp.get("totalClosedTrades", 0)),
            total_net_pnl=Decimal(str(resp.get("totalNetPnl", 0))),
            total_fees_paid=Decimal(str(resp.get("totalFeesPaid", 0))),
            algo_enabled=bool(resp.get("algoEnabled", False)),
            kill_switch_active=bool(resp.get("killSwitchActive", True)),
        )

    def get_next_trade_capital(self, account_id: Optional[str] = None) -> Decimal:
        """
        Returns the authoritative capital for the next trade (100% allocation).
        Priority: last post_trade_balance → account.current_balance.
        """
        aid = account_id or self._account_id
        resp = self._request("GET", f"/api/engine/capital/{aid}")
        capital = resp.get("data")
        return Decimal(str(capital)) if capital is not None else Decimal("0")

    # ── Trade Lifecycle ───────────────────────────────────────────────────────

    def notify_trade_open(
        self,
        setup_id: str,
        symbol: str,
        direction: str,
        entry_price: Decimal,
        quantity: Decimal,
        leverage: int,
        pre_trade_balance: Decimal,
        order_block_upper: Optional[Decimal] = None,
        order_block_lower: Optional[Decimal] = None,
        stop_loss_price: Optional[Decimal] = None,
        take_profit_price: Optional[Decimal] = None,
        configuration_version: int = 1,
        max_loss_pct: Decimal = Decimal("35.00"),
        target_roe_pct: Decimal = Decimal("60.00"),
        account_id: Optional[str] = None,
    ) -> TradeOpenResult:
        """
        Atomically acquires the DB trade lock and persists the trade open record.
        Returns TradeOpenResult with success=False and error='ONE_TRADE_ACTIVE'
        if an active trade already exists (HTTP 409 from backend).

        Call this BEFORE submitting the entry order to Delta Exchange.
        If this fails, do NOT submit the order.
        """
        aid = account_id or self._account_id

        def _dec(v: Optional[Decimal]) -> Optional[str]:
            return str(v) if v is not None else None

        body = {
            "setupId": setup_id,
            "symbol": symbol,
            "direction": direction,
            "entryPrice": str(entry_price),
            "quantity": str(quantity),
            "leverage": leverage,
            "preTradeBalance": str(pre_trade_balance),
            "orderBlockUpper": _dec(order_block_upper),
            "orderBlockLower": _dec(order_block_lower),
            "stopLossPrice": _dec(stop_loss_price),
            "takeProfitPrice": _dec(take_profit_price),
            "configurationVersion": configuration_version,
            "maxLossPct": str(max_loss_pct),
            "targetRoePct": str(target_roe_pct),
        }

        resp = self._request("POST", f"/api/engine/trade/open/{aid}", body)

        # HTTP 409 is returned by backend for active trade conflicts
        if not resp.get("success", False):
            return TradeOpenResult(
                success=False,
                trade_record_id=None,
                lock_id=None,
                error=resp.get("error", "UNKNOWN_ERROR"),
            )

        data = resp.get("data", {}) or {}
        return TradeOpenResult(
            success=True,
            trade_record_id=data.get("tradeRecordId"),
            lock_id=data.get("lockId"),
            error=None,
        )

    def notify_trade_close(
        self,
        setup_id: str,
        gross_pnl: Decimal,
        trading_fees: Decimal,
        funding_costs: Decimal = Decimal("0"),
        other_costs: Decimal = Decimal("0"),
        exit_price: Optional[Decimal] = None,
        close_reason: str = "POSITION_CLOSED",
        authoritative_exchange_balance: Optional[Decimal] = None,
        exit_order_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> TradeCloseResult:
        """
        Atomically records the final P&L, updates the account balance,
        and releases the DB trade lock.

        All three operations happen in one transaction.
        Call this AFTER Delta confirms the position is closed and fees are known.

        net_pnl = gross_pnl - trading_fees - funding_costs - other_costs
        This formula is enforced by the Java backend — the Python engine
        must NOT compute net_pnl itself.
        """
        aid = account_id or self._account_id

        body = {
            "setupId": setup_id,
            "grossPnl": str(gross_pnl),
            "tradingFees": str(trading_fees),
            "fundingCosts": str(funding_costs),
            "otherCosts": str(other_costs),
            "exitPrice": str(exit_price) if exit_price is not None else None,
            "closeReason": close_reason,
            "authoritativeExchangeBalance": (
                str(authoritative_exchange_balance)
                if authoritative_exchange_balance is not None else None
            ),
            "exitOrderId": exit_order_id,
        }

        resp = self._request("POST", f"/api/engine/trade/close/{aid}", body)

        if not resp.get("success", False):
            return TradeCloseResult(
                success=False,
                net_pnl=None,
                post_trade_balance=None,
                error=resp.get("error", "UNKNOWN_ERROR"),
            )

        data = resp.get("data", {}) or {}
        return TradeCloseResult(
            success=True,
            net_pnl=Decimal(str(data["netPnl"])) if data.get("netPnl") is not None else None,
            post_trade_balance=(
                Decimal(str(data["postTradeBalance"]))
                if data.get("postTradeBalance") is not None else None
            ),
            error=None,
        )

    def update_lock_state(
        self,
        state: str,
        account_id: Optional[str] = None,
    ) -> bool:
        """Updates the active trade lock's lifecycle state in the DB."""
        aid = account_id or self._account_id
        resp = self._request("POST", f"/api/engine/trade/lock-state/{aid}?state={state}")
        return resp.get("success", False)

    def force_release_lock(
        self,
        reason: str,
        account_id: Optional[str] = None,
    ) -> bool:
        """
        Force-releases a stuck lock after confirmed Delta reconciliation.
        ONLY call when Delta confirms: no open position + no pending orders.
        """
        aid = account_id or self._account_id
        resp = self._request("POST", f"/api/engine/trade/force-release/{aid}?reason={reason}")
        return resp.get("success", False)


# ── Exceptions ────────────────────────────────────────────────────────────────

class BackendClientError(Exception):
    """Raised when the backend API is unreachable or returns an unexpected error."""
    pass


# ── Module-level singleton (lazy-initialized) ─────────────────────────────────

_client: Optional[BackendClient] = None


def get_client() -> BackendClient:
    """Returns the module-level BackendClient singleton."""
    global _client
    if _client is None:
        _client = BackendClient()
    return _client
