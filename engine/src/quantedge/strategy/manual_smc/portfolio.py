"""
Manual SMC — Portfolio Lock (Phase 1 Step 3).
=============================================

The single globally exclusive trade slot, expressed as an explicit,
auditable lock instead of an implicit variable assignment.

WHY THIS EXISTS
---------------
The frozen research oracle gated entry on a timestamp watermark
(`c_ts <= global_lock_until_dt`), which only refused a fill on the SAME
candle. On a strictly later candle a second fill simply overwrote
`active_trade`; the first trade's OB was stranded in TRADE_ACTIVE and was
never closed or recorded. `lifecycle.py` corrected that with
`active_trade is not None`. This module makes the same rule a first-class
object so a later phase can back it with the Postgres advisory/row lock
without re-deriving the semantics.

CONTRACT
--------
  * At most ONE holder exists at any time, across ALL assets and ALL
    directions. `active_trade is not None` rejects — unconditionally, and
    regardless of whether the holder was acquired on this candle or an
    earlier one.
  * The lock is released ONLY on evidence that the trade actually closed:
    the caller must present the holder's token AND a terminal outcome.
    A stale token, a foreign token, or a non-terminal outcome raises.
  * On release the close timestamp is retained as a watermark, reproducing
    the oracle's secondary conservative guard: a new acquisition at or
    before that timestamp is refused because intra-candle ordering is not
    determinable from OHLC alone.

DELIBERATELY ABSENT
-------------------
No execution-layer behaviour whatsoever: no order placement, no order
cancellation, no bracket management, no exchange calls, no database, no
kill-switch handling, no reconciliation transport. This module decides
whether a trade slot is available and records that decision. Acting on the
decision belongs to later phases.

Safety rules honoured here: #13 (never two active trades for the same
account) and #14 (never release the global lock before the position is
actually closed/reconciled).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple, Union

from quantedge.strategy.manual_smc.lifecycle import (
    OUTCOME_SL,
    OUTCOME_TIMEOUT,
    OUTCOME_TP,
)

# ---------------------------------------------------------------------------
# Terminal outcomes accepted as proof that the position is really closed.
# ---------------------------------------------------------------------------
#: Produced by reconciliation when a position is confirmed flat by a source
#: other than the strategy's own exit logic (operator flatten, kill switch,
#: exchange-side liquidation discovered on resync). Defined HERE and not in
#: lifecycle.py precisely because the strategy never emits it.
OUTCOME_RECONCILED_CLOSED: str = "CLOSED_RECONCILED"

TERMINAL_OUTCOMES: frozenset = frozenset({
    OUTCOME_TP,
    OUTCOME_SL,
    OUTCOME_TIMEOUT,
    OUTCOME_RECONCILED_CLOSED,
})

class LockRejectionCode(Enum):
    """Why an acquisition was refused. Both codes mean 'no slot available'."""
    #: A trade is open right now. The corrected, load-bearing rule.
    ACTIVE_TRADE_OPEN = "ACTIVE_TRADE_OPEN"
    #: A trade closed at or after this candle's timestamp. Retained oracle
    #: watermark: intra-candle ordering is not determinable from OHLC.
    INTRA_CANDLE_AMBIGUITY = "INTRA_CANDLE_AMBIGUITY"


class PortfolioLockError(RuntimeError):
    """Base class for portfolio-lock misuse."""


class PortfolioLockUnavailableError(PortfolioLockError):
    """Raised by `acquire()` when no trade slot is available."""

    def __init__(self, rejection: "LockRejection") -> None:
        super().__init__(f"{rejection.code.value}: {rejection.detail}")
        self.rejection = rejection


class PortfolioLockViolationError(PortfolioLockError):
    """Raised on an illegitimate release attempt (safety rule #14)."""


@dataclass(frozen=True)
class LockHolder:
    """The one trade currently occupying the global slot."""
    token: str
    account_id: str
    asset: str
    ob_id: str
    direction: str
    acquired_at: datetime
    acquired_bar_idx: int

    @property
    def granted(self) -> bool:
        return True


@dataclass(frozen=True)
class LockRejection:
    """A refusal. Carries the holder so callers can log *why* concretely."""
    code: LockRejectionCode
    detail: str
    held_by: Optional[LockHolder] = None

    @property
    def granted(self) -> bool:
        return False


@dataclass(frozen=True)
class LockEvent:
    """Audit trail entry. Decisions only — never an exchange action."""
    event: str                      # ACQUIRED | REJECTED | RELEASED
    ts: datetime
    asset: str
    ob_id: str
    detail: str


LockDecision = Union[LockHolder, LockRejection]

class PortfolioLock:
    """
    Exactly one globally active trade per account.

    One instance per account. Not thread-safe by design: the authoritative
    cross-process lock is the Postgres lock in a later phase, and adding a
    local mutex here would create a false impression of distributed safety.
    """

    def __init__(self, account_id: str = "DEFAULT") -> None:
        self.account_id = account_id
        self._holder: Optional[LockHolder] = None
        self._last_closed_dt: Optional[datetime] = None
        self._token_seq: int = 0
        self._events: List[LockEvent] = []

    # -- introspection ----------------------------------------------------
    @property
    def active_trade(self) -> Optional[LockHolder]:
        """The current holder, or None. `is not None` IS the rejection rule."""
        return self._holder

    def is_held(self) -> bool:
        return self._holder is not None

    @property
    def last_closed_dt(self) -> Optional[datetime]:
        return self._last_closed_dt

    @property
    def events(self) -> Tuple[LockEvent, ...]:
        return tuple(self._events)

    def reset(self) -> None:
        """Drop all state. Test/replay helper — never an operational action."""
        self._holder = None
        self._last_closed_dt = None
        self._token_seq = 0
        self._events.clear()

    # -- acquisition ------------------------------------------------------
    def evaluate(self, ts: datetime) -> Optional[LockRejection]:
        """
        Would an acquisition at `ts` be refused? Pure — mutates nothing.

        Mirrors `ManualSMCLifecycle._entry_blocked` exactly, in the same
        order, so the two can never disagree about admissibility.
        """
        if self._holder is not None:
            return LockRejection(
                code=LockRejectionCode.ACTIVE_TRADE_OPEN,
                detail=(
                    f"active trade already open on {self._holder.asset} "
                    f"(filled {self._holder.acquired_at.isoformat()})"
                ),
                held_by=self._holder,
            )
        if self._last_closed_dt is not None and ts <= self._last_closed_dt:
            return LockRejection(
                code=LockRejectionCode.INTRA_CANDLE_AMBIGUITY,
                detail=(
                    "a trade closed at or after this candle timestamp; "
                    "intra-candle re-entry ordering is not determinable"
                ),
                held_by=None,
            )
        return None

    def try_acquire(
        self,
        asset: str,
        ob_id: str,
        direction: str,
        ts: datetime,
        bar_idx: int,
    ) -> LockDecision:
        """Non-raising acquisition. Returns a LockHolder or a LockRejection."""
        rejection = self.evaluate(ts)
        if rejection is not None:
            self._events.append(LockEvent(
                event="REJECTED", ts=ts, asset=asset, ob_id=ob_id,
                detail=f"{rejection.code.value}: {rejection.detail}",
            ))
            return rejection

        self._token_seq += 1
        holder = LockHolder(
            token=f"{self.account_id}#{self._token_seq}",
            account_id=self.account_id,
            asset=asset,
            ob_id=ob_id,
            direction=direction,
            acquired_at=ts,
            acquired_bar_idx=bar_idx,
        )
        self._holder = holder
        self._events.append(LockEvent(
            event="ACQUIRED", ts=ts, asset=asset, ob_id=ob_id,
            detail=f"token {holder.token} direction {direction}",
        ))
        return holder

    def acquire(
        self,
        asset: str,
        ob_id: str,
        direction: str,
        ts: datetime,
        bar_idx: int,
    ) -> LockHolder:
        """Raising acquisition, for callers that treat refusal as exceptional."""
        decision = self.try_acquire(asset, ob_id, direction, ts, bar_idx)
        if isinstance(decision, LockRejection):
            raise PortfolioLockUnavailableError(decision)
        return decision

    # -- release ----------------------------------------------------------
    def release(self, token: str, closed_at: datetime, outcome: str) -> None:
        """
        Release the slot. Safety rule #14: only on proof of an actual close.

        Refuses — loudly — when the lock is not held, when the token does not
        match the current holder (stale or foreign), or when the outcome is
        not terminal. A caller cannot free the slot merely by asking.
        """
        if self._holder is None:
            raise PortfolioLockViolationError(
                "release attempted while no trade is active")
        if token != self._holder.token:
            raise PortfolioLockViolationError(
                f"token mismatch: {token!r} does not hold the lock "
                f"(held by {self._holder.token!r} on {self._holder.asset})")
        if outcome not in TERMINAL_OUTCOMES:
            raise PortfolioLockViolationError(
                f"outcome {outcome!r} is not terminal; the position is not "
                f"proven closed. Accepted: {sorted(TERMINAL_OUTCOMES)}")

        released = self._holder
        self._holder = None
        self._last_closed_dt = closed_at
        self._events.append(LockEvent(
            event="RELEASED", ts=closed_at, asset=released.asset,
            ob_id=released.ob_id,
            detail=f"token {released.token} outcome {outcome}",
        ))


__all__ = [
    "OUTCOME_RECONCILED_CLOSED",
    "TERMINAL_OUTCOMES",
    "LockRejectionCode",
    "PortfolioLockError",
    "PortfolioLockUnavailableError",
    "PortfolioLockViolationError",
    "LockHolder",
    "LockRejection",
    "LockEvent",
    "LockDecision",
    "PortfolioLock",
]




