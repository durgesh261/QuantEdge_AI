"""
Single Active Position Lock & Account Mutex for QuantEdge AI.

Phase 5.8 Implementation:
1. Single Active Trade Rule:
   - Exactly ONE active trade per trading account at any given time.
   - Rejects simultaneous multi-pair signals, double-clicks, and duplicate events.
2. Cross-Restart Persistence:
   - Preserves active lock across engine/server restarts.
3. Strict Release Protocol:
   - Lock is released ONLY when the trade is confirmed POSITION_CLOSED by Delta exchange.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import threading
from typing import Optional, Dict, Tuple, Any

logger = logging.getLogger("single_trade_lock")


class SingleTradeLockError(Exception):
    """Raised when an attempt is made to open multiple concurrent trades on a single account."""
    pass


@dataclass
class AccountTradeLockState:
    """State of an account's single-trade lock."""
    account_id: str
    user_id: str
    is_locked: bool = False
    active_setup_id: Optional[str] = None
    active_symbol: Optional[str] = None
    acquired_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "user_id": self.user_id,
            "is_locked": self.is_locked,
            "active_setup_id": self.active_setup_id,
            "active_symbol": self.active_symbol,
            "acquired_at": self.acquired_at.isoformat() if self.acquired_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountTradeLockState":
        return cls(
            account_id=data["account_id"],
            user_id=data["user_id"],
            is_locked=bool(data.get("is_locked", False)),
            active_setup_id=data.get("active_setup_id"),
            active_symbol=data.get("active_symbol"),
            acquired_at=datetime.fromisoformat(data["acquired_at"]) if data.get("acquired_at") else None,
        )


class SingleTradeLockManager:
    """Thread-safe, account-isolated single active position lock manager."""

    def __init__(self):
        # Key: (user_id, account_id) -> AccountTradeLockState
        self._locks: Dict[Tuple[str, str], AccountTradeLockState] = {}
        self._lock = threading.RLock()

    def acquire_lock(
        self,
        user_id: str,
        account_id: str,
        setup_id: str,
        symbol: str,
    ) -> bool:
        """Attempt to acquire exclusive trade lock for an account.

        Raises:
            SingleTradeLockError if an active trade already exists for this account.
        """
        with self._lock:
            key = (user_id, account_id)
            state = self._locks.get(key)

            if state is None:
                state = AccountTradeLockState(account_id=account_id, user_id=user_id)
                self._locks[key] = state

            # Idempotent replay: already locked by this exact setup_id
            if state.is_locked and state.active_setup_id == setup_id:
                return True

            # Rejection: already locked by a different active trade
            if state.is_locked and state.active_setup_id is not None:
                raise SingleTradeLockError(
                    f"Account {account_id} already has an active trade in progress: "
                    f"Setup '{state.active_setup_id}' on {state.active_symbol}. "
                    f"QuantEdge allows exactly ONE active trade per account."
                )

            # Successfully acquire lock
            state.is_locked = True
            state.active_setup_id = setup_id
            state.active_symbol = symbol
            state.acquired_at = datetime.now(timezone.utc)
            logger.info(
                "Acquired single-trade lock for account %s: setup_id=%s, symbol=%s",
                account_id, setup_id, symbol
            )
            return True

    def release_lock(
        self,
        user_id: str,
        account_id: str,
        setup_id: str,
    ) -> bool:
        """Release exclusive trade lock when a trade is confirmed fully closed."""
        with self._lock:
            key = (user_id, account_id)
            state = self._locks.get(key)

            if state is None:
                return False

            if state.is_locked and state.active_setup_id == setup_id:
                state.is_locked = False
                state.active_setup_id = None
                state.active_symbol = None
                state.acquired_at = None
                logger.info(
                    "Released single-trade lock for account %s from setup_id=%s",
                    account_id, setup_id
                )
                return True

            return False

    def force_release(self, user_id: str, account_id: str) -> bool:
        """Force release lock (e.g. during emergency kill-switch or admin override)."""
        with self._lock:
            key = (user_id, account_id)
            state = self._locks.get(key)
            if state is not None:
                state.is_locked = False
                state.active_setup_id = None
                state.active_symbol = None
                state.acquired_at = None
                return True
            return False

    def is_locked(self, user_id: str, account_id: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Check if account currently has an active trade lock.

        Returns:
            (is_locked, active_setup_id, active_symbol)
        """
        with self._lock:
            key = (user_id, account_id)
            state = self._locks.get(key)
            if state is not None and state.is_locked:
                return True, state.active_setup_id, state.active_symbol
            return False, None, None

    def export_state(self) -> Dict[str, Any]:
        """Export state for persistence across restarts."""
        with self._lock:
            return {
                f"{u}:{a}": s.to_dict()
                for (u, a), s in self._locks.items()
            }

    def load_state(self, state_dict: Dict[str, Any]) -> None:
        """Load state from persistent storage."""
        with self._lock:
            for key_str, s_dict in state_dict.items():
                state = AccountTradeLockState.from_dict(s_dict)
                self._locks[(state.user_id, state.account_id)] = state
