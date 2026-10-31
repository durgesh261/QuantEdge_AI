"""
Execution startup reconciliation boundary for QuantEdge AI.

This module is the ONE place where "may this process open a new trade?" is
answered after a restart. It is a boundary, not a composition: constructing an
`ExecutionStartupGate` wires nothing, opens no socket, places no order, and
does not build the production execution stack. It installs a single refusal
hook on an existing `TradeLifecycleManager` and starts BLOCKED.

The sequence a gate must complete before it admits anything:

  1. Persisted execution state is LOADED (identities, order ids, roles, the
     lifecycle records and the single-trade lock) -- see
     `quantedge.execution.execution_state_store`. Corrupt or incompatible
     state keeps the gate blocked AND raises.
  2. `TradeLifecycleManager.reconcile_active_trades_with_exchange` runs and
     returns CLEAN. Unreachable exchange, unresolved findings, orphan
     positions or orphan orders keep the gate blocked.
  3. `_reconciliation_alerts` is empty (the manager's own long-standing
     blocking semantics, re-checked here rather than replaced).
  4. Execution-side internal state agrees with itself: every retained
     single-trade lock names a live record and every live record is locked.
  5. Strategy-side open-trade state agrees with execution-side state.

Ambiguity is never repaired. This module invents no order id, no fill, no
quantity, no protection leg and no position; it only reports and blocks.
Nothing here is persisted either: a restart must re-arm from scratch, because
a remembered "admitted" verdict would defeat the whole boundary.

Nor is an admitted verdict permanent within one process (P1-3). `arm()` records
a fingerprint of the execution state it reconciled, and `admission_reason()`
re-checks it on every entry attempt: once that state moves -- a fill, a close, an
alert, a lock change, a restore -- the authorization is stale and entries are
refused until a fresh `arm()`. Freshness is proved structurally against state the
repository already owns, not by a clock or a TTL.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from quantedge.execution.entry_admission import (
    EntryAdmissionContract,
    EntryAdmissionError,
    install_entry_admission,
)
from quantedge.execution.execution_state_store import (
    ExecutionStateError,
    execution_state_exists,
    export_execution_state,
    restore_execution_state_from_file,
    serialize_execution_state_body,
)
from quantedge.execution.trade_lifecycle import (
    REFUSAL_STARTUP_GATE,
    TradeLifecycleManager,
)

logger = logging.getLogger("execution_startup_gate")

#: The rejection code `TradeLifecycleManager` stamps on an entry this gate
#: refuses. Re-exported so callers and tests name it in one place.
GATE_REFUSAL_CODE = REFUSAL_STARTUP_GATE

#: The refusal a freshly constructed gate gives. A gate is blocked from the
#: moment it is installed, so a composition that forgets to `await arm()`
#: cannot trade -- it fails closed, loudly, on the first entry attempt.
NOT_ARMED_REASON = (
    "startup reconciliation has not completed: execution state has not been "
    "loaded and reconciled against the exchange"
)

#: The refusal an ARMED gate gives once the execution state it was armed against
#: is no longer the execution state in memory (P1-3). A clean reconciliation is
#: a statement about a specific book at a specific moment, not a permanent
#: licence, so the verdict expires the moment its subject changes.
STALE_AUTHORIZATION_REASON = (
    "startup reconciliation is no longer current: execution state has changed "
    "since the gate was armed, so a fresh reconciliation is required before a "
    "new entry can be admitted"
)


class ExecutionStartupGateError(RuntimeError):
    """Arming could not even be attempted honestly (missing/corrupt state).

    Distinct from a block: a block is a verdict the gate reached and recorded,
    while this is raised when the inputs to the verdict are unusable. Either
    way the gate remains blocked.
    """


@dataclass(frozen=True)
class StartupGateResult:
    """The verdict of one `arm()` attempt. Immutable and fully self-describing."""

    admitted: bool
    block_reason: Optional[str]
    reconciliation: Optional[Dict[str, Any]]
    state_loaded: bool
    active_setup_ids: Tuple[str, ...]
    strategy_setup_id: Optional[str]


class ExecutionStartupGate(EntryAdmissionContract):
    """The startup reconciliation boundary for one `TradeLifecycleManager`.

    Constructing this installs `manager._entry_admission_gate` -- through the one
    canonical installer, `install_entry_admission` (P2-6) -- and immediately
    blocks every new entry. It does NOT compose the production execution stack:
    no orchestrator is built, no market-data or private stream is bound, and no
    live execution flag is touched.

    Args:
        manager: the lifecycle manager whose entries this gate governs.
        state_path: the durable execution-state file written by
            `quantedge.execution.execution_state_store`.
        strategy_runtime: the strategy-side runtime (`ManualSMCRuntime`-shaped:
            `.strategy.has_active_trade()`, `.strategy.lifecycle.active_trade`
            and `.setup_id_for(asset, ob_id, direction)`). Required to arm --
            without it the strategy/execution disagreement check cannot be
            performed, and an uncheckable invariant blocks.
        account_id / user_id: passed through to reconciliation. When omitted
            they are read from the manager's own state store rather than
            guessed.
        allow_missing_state: permit arming when no state file exists yet. This
            is for a genuinely first-ever start; it is NOT a way to skip a file
            that should have been there, which is why it is opt-in per gate.
    """

    def __init__(
        self,
        manager: TradeLifecycleManager,
        state_path: Union[str, Path],
        strategy_runtime: Optional[Any] = None,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        allow_missing_state: bool = False,
    ) -> None:
        if manager._entry_admission_gate is not None:
            # Silently replacing an installed gate could replace a BLOCK with a
            # fresh gate that a caller then arms against different inputs.
            raise ExecutionStartupGateError(
                "an entry admission gate is already installed on this "
                "TradeLifecycleManager; refusing to replace it"
            )
        self.manager = manager
        self.state_path = Path(state_path)
        self.strategy_runtime = strategy_runtime
        self.account_id = account_id
        self.user_id = user_id
        self.allow_missing_state = bool(allow_missing_state)

        self._result: StartupGateResult = StartupGateResult(
            admitted=False,
            block_reason=NOT_ARMED_REASON,
            reconciliation=None,
            state_loaded=False,
            active_setup_ids=(),
            strategy_setup_id=None,
        )
        self._armed_fingerprint: Optional[bytes] = None

        # P2-6: installed through the one canonical installer rather than by
        # assigning the private attribute here, so "who may fill this and with
        # what" lives in exactly one place. The pre-check above is kept because
        # it carries this module's own error type and message, which callers and
        # tests already depend on.
        try:
            install_entry_admission(manager, self)
        except EntryAdmissionError as exc:                      # pragma: no cover
            raise ExecutionStartupGateError(str(exc)) from exc
        logger.info(
            "EXECUTION_STARTUP_GATE_INSTALLED state_path=%s blocked=True", self.state_path
        )

    # ── Read-only state ───────────────────────────────────────────────────────

    @property
    def armed(self) -> bool:
        """True only when the last `arm()` admitted. Never inferred elsewhere.

        This reports the VERDICT, not permission to trade right now: an armed
        gate still refuses entries once the execution state it was armed against
        has moved (P1-3). `admission_reason()` is the authority on the latter,
        and `authorization_current` is the freshness half of it on its own.
        """
        return self._result.admitted

    @property
    def result(self) -> StartupGateResult:
        """The last verdict. Present from construction, so it is never `None`."""
        return self._result

    @property
    def authorization_current(self) -> bool:
        """Whether the armed verdict still describes the execution state in memory.

        False for a gate that never armed, for a gate whose state has changed
        since arming, and for a gate whose state can no longer be serialized --
        an unverifiable freshness claim is not a fresh one.
        """
        return self._staleness_reason() is None

    def _staleness_reason(self) -> Optional[str]:
        """Why the armed verdict is no longer usable, or `None` if it still is.

        P1-3. `arm()` used to be a one-way switch: it recorded `admitted=True`
        and `admission_reason()` returned `None` from then on, forever, without
        ever consulting the fingerprint it had carefully computed. A clean
        reconciliation at 09:00 therefore authorized an entry at 17:00 across
        every fill, close, alert and lock change in between.

        Freshness is proved STRUCTURALLY, not by a clock: the fingerprint is the
        deterministic serialization of the durable execution body -- active
        records, history, alerts, fill accounting and the single-trade lock --
        so any change the exchange, the private stream, a close or a restore made
        to the account's execution state changes it. That is a real invalidation
        signal the repository already owns, which is why no TTL, no
        "last_checked_at" and no synthetic "still fresh" timestamp is introduced
        here (the mandate forbids inventing one, and a wall-clock TTL would
        answer a safety question with an assumption).

        Consequence, deliberately: an entry after ANY execution-state change
        requires a fresh `arm()`. Re-arming is idempotent and cheap when the
        exchange agrees, so the cost of the strict reading is a reconciliation
        round-trip; the cost of the permissive reading is trading on a book
        nobody has looked at since startup.
        """
        if not self._result.admitted:
            return self._result.block_reason or NOT_ARMED_REASON
        if self._armed_fingerprint is None:
            # Admitted with nothing to compare against: freshness cannot be
            # proved, so it is not assumed (safety rule #15).
            return NOT_ARMED_REASON
        try:
            current = self._fingerprint()
        except Exception as exc:                                    # noqa: BLE001
            return (
                "execution state could not be serialized, so the startup "
                f"reconciliation's freshness cannot be verified: {exc}"
            )
        if current != self._armed_fingerprint:
            return STALE_AUTHORIZATION_REASON
        return None

    def admission_reason(self) -> Optional[str]:
        """The hook installed on the manager: `None` admits, a string refuses.

        Synchronous and side-effect free by design -- it is called on the entry
        path, so it must not perform I/O, contact the exchange, or re-run
        reconciliation. It reports the verdict `arm()` already reached, AND
        whether that verdict still describes the current execution state (P1-3);
        it deliberately does not mutate `_result`, so the recorded verdict stays
        the verdict and staleness is recomputed from live state every time.
        """
        return self._staleness_reason()

    # ── Arming ────────────────────────────────────────────────────────────────

    def _fingerprint(self) -> bytes:
        """Deterministic bytes for the manager's current execution state."""
        return serialize_execution_state_body(export_execution_state(self.manager))

    def _block(
        self,
        reason: str,
        *,
        reconciliation: Optional[Dict[str, Any]] = None,
        state_loaded: bool = False,
        strategy_setup_id: Optional[str] = None,
    ) -> StartupGateResult:
        """Record a blocking verdict. Always leaves the gate refusing entries."""
        self._armed_fingerprint = None
        self._result = StartupGateResult(
            admitted=False,
            block_reason=reason,
            reconciliation=reconciliation,
            state_loaded=state_loaded,
            active_setup_ids=tuple(sorted(self.manager._active_trades)),
            strategy_setup_id=strategy_setup_id,
        )
        logger.critical("EXECUTION_STARTUP_GATE_BLOCKED reason=%s", reason)
        return self._result

    def _load_state(self) -> bool:
        """Restore persisted execution state, or fail closed trying.

        Returns True when a file was loaded, False when none existed and the
        gate was explicitly configured to allow that. Never returns after a
        corrupt/incompatible file: it blocks and raises, so a caller that
        swallows the exception still cannot trade.
        """
        if not execution_state_exists(self.state_path):
            if not self.allow_missing_state:
                self._block(
                    f"no persisted execution state at {self.state_path}: refusing "
                    "to assume a flat book"
                )
                raise ExecutionStartupGateError(
                    f"no persisted execution state at {self.state_path}. Pass "
                    "allow_missing_state=True only for a genuine first-ever start."
                )
            logger.warning(
                "EXECUTION_STARTUP_GATE_NO_STATE_FILE path=%s (allowed explicitly)",
                self.state_path,
            )
            return False

        try:
            restore_execution_state_from_file(self.manager, self.state_path)
        except ExecutionStateError as exc:
            self._block(
                f"persisted execution state at {self.state_path} could not be "
                f"loaded: {exc}"
            )
            raise ExecutionStartupGateError(
                f"persisted execution state at {self.state_path} is unusable: {exc}"
            ) from exc

        logger.info(
            "EXECUTION_STARTUP_GATE_STATE_LOADED path=%s active_trades=%s history=%s",
            self.state_path, len(self.manager._active_trades),
            len(self.manager._trade_history),
        )
        return True

    def _lock_disagreement(self) -> Optional[str]:
        """Execution-side internal consistency: retained locks vs live records.

        A retained lock naming no live record means some earlier attempt may
        have left an order or a position behind -- safety rules #11/#14 retain
        the lock in exactly that situation. A live record holding no lock means
        the two persisted stores disagree. Both are ambiguous, so both block;
        neither is repaired here.
        """
        try:
            lock_state = self.manager.single_trade_lock.export_state()
        except Exception as exc:                                    # noqa: BLE001
            return f"single-trade lock state could not be read: {exc}"

        active = set(self.manager._active_trades)
        locked_setups = set()
        for key, entry in sorted(lock_state.items()):
            if not isinstance(entry, dict):
                return f"single-trade lock entry {key!r} is not a mapping"
            if not entry.get("is_locked"):
                continue
            setup_id = entry.get("active_setup_id")
            if not setup_id:
                return (
                    f"single-trade lock {key!r} is held but names no setup, so what "
                    "it is protecting cannot be determined"
                )
            locked_setups.add(setup_id)
            if setup_id not in active:
                return (
                    f"single-trade lock {key!r} is held for setup {setup_id!r}, which "
                    "has no live execution record; something may still be open or "
                    "unprotected on the exchange"
                )

        unlocked = sorted(active - locked_setups)
        if unlocked:
            return (
                f"live execution record(s) {unlocked} hold no single-trade lock: "
                "execution state and lock state disagree"
            )
        return None

    def _strategy_disagreement(self) -> Tuple[Optional[str], Optional[str]]:
        """`(block reason, strategy setup id)` for the strategy/execution check.

        Compares the strategy's own open-trade state against execution-side
        active records through the runtime's single `setup_id_for` formula, so
        the two sides are compared on identical ids rather than on a
        reconstruction of one of them.
        """
        runtime = self.strategy_runtime
        if runtime is None:
            return (
                "no strategy runtime is bound to this gate, so strategy-side and "
                "execution-side open-trade state cannot be compared",
                None,
            )

        try:
            strategy_active = bool(runtime.strategy.has_active_trade())
        except Exception as exc:                                    # noqa: BLE001
            return (f"strategy open-trade state could not be read: {exc}", None)

        strategy_setup_id: Optional[str] = None
        if strategy_active:
            try:
                trade = runtime.strategy.lifecycle.active_trade
                strategy_setup_id = runtime.setup_id_for(
                    trade.asset, trade.ob.ob_id, trade.direction
                )
            except Exception as exc:                                # noqa: BLE001
                return (
                    "strategy reports an active trade whose setup id could not be "
                    f"derived: {exc}",
                    None,
                )
            if not strategy_setup_id:
                return ("strategy reports an active trade with an empty setup id", None)

        execution_setups = sorted(self.manager._active_trades)
        if strategy_active and not execution_setups:
            return (
                f"strategy holds active trade {strategy_setup_id!r} but execution "
                "state holds none",
                strategy_setup_id,
            )
        if execution_setups and not strategy_active:
            return (
                f"execution state holds active trade(s) {execution_setups} but the "
                "strategy holds none",
                strategy_setup_id,
            )
        if strategy_active and strategy_setup_id not in execution_setups:
            return (
                f"strategy active trade {strategy_setup_id!r} does not match "
                f"execution active trade(s) {execution_setups}",
                strategy_setup_id,
            )
        return (None, strategy_setup_id)

    async def arm(self) -> StartupGateResult:
        """Run the whole boundary and admit entries only if every step is clean.

        Idempotent: re-arming a gate that is already armed and whose execution
        state has not changed returns the cached verdict WITHOUT contacting the
        exchange again. Anything else -- a prior block, or state that moved --
        re-runs the full sequence from a blocked starting point.

        Raises `ExecutionStartupGateError` only when the inputs are unusable
        (missing or corrupt state file, or reconciliation itself failing in an
        unexpected way). A dirty-but-readable world is reported as a block, not
        an exception, because that is a verdict rather than a malfunction.

        This method does NOT write state back. Reconciliation is idempotent, so
        re-running it after a restart is safe; persisting is the composing
        caller's decision, through `persist_execution_state`.
        """
        if self._result.admitted and self._armed_fingerprint is not None:
            try:
                unchanged = self._fingerprint() == self._armed_fingerprint
            except ExecutionStateError as exc:
                return self._block(
                    f"execution state is no longer serializable: {exc}", state_loaded=True
                )
            if unchanged:
                logger.info(
                    "EXECUTION_STARTUP_GATE_ALREADY_ARMED active_trades=%s "
                    "(execution state unchanged; exchange not re-queried)",
                    self._result.active_setup_ids,
                )
                return self._result

        # The previous verdict is stale from here on, and reconciliation is
        # about to await. Demote to BLOCKED first so nothing can be admitted on
        # a stale verdict while the sequence is in flight.
        self._armed_fingerprint = None
        self._result = StartupGateResult(
            admitted=False,
            block_reason="startup reconciliation is in progress",
            reconciliation=None,
            state_loaded=False,
            active_setup_ids=tuple(sorted(self.manager._active_trades)),
            strategy_setup_id=None,
        )

        state_loaded = self._load_state()

        account_id = self.account_id or self.manager.state_store.account_id
        user_id = self.user_id or self.manager.state_store.account.user_id
        try:
            summary = await self.manager.reconcile_active_trades_with_exchange(
                account_id=account_id, user_id=user_id
            )
        except Exception as exc:                                    # noqa: BLE001
            self._block(
                f"startup reconciliation failed: {exc}", state_loaded=state_loaded
            )
            raise ExecutionStartupGateError(
                f"startup reconciliation failed and entries remain blocked: {exc}"
            ) from exc

        dirty = []
        if summary.get("exchange_unreachable"):
            dirty.append("exchange_unreachable=True")
        for key in ("unresolved", "orphan_positions", "orphan_orders"):
            values = summary.get(key) or []
            if values:
                dirty.append(f"{key}={sorted(str(v) for v in values)}")
        if dirty:
            return self._block(
                "startup reconciliation did not return clean: " + "; ".join(dirty),
                reconciliation=summary,
                state_loaded=state_loaded,
            )

        # The manager's own long-standing blocking semantics, re-checked rather
        # than replaced. A clean reconciliation clears alerts itself, so anything
        # still standing here was not resolvable from authoritative data.
        alerts = self.manager._reconciliation_alerts
        if alerts:
            codes = sorted({str(a.get("code")) for a in alerts})
            return self._block(
                f"unresolved reconciliation alerts block new entries: {codes}",
                reconciliation=summary,
                state_loaded=state_loaded,
            )

        lock_reason = self._lock_disagreement()
        if lock_reason is not None:
            return self._block(
                lock_reason, reconciliation=summary, state_loaded=state_loaded
            )

        strategy_reason, strategy_setup_id = self._strategy_disagreement()
        if strategy_reason is not None:
            return self._block(
                strategy_reason,
                reconciliation=summary,
                state_loaded=state_loaded,
                strategy_setup_id=strategy_setup_id,
            )

        try:
            fingerprint = self._fingerprint()
        except ExecutionStateError as exc:
            return self._block(
                "reconciled execution state could not be serialized, so it could "
                f"not be persisted or re-verified: {exc}",
                reconciliation=summary,
                state_loaded=state_loaded,
                strategy_setup_id=strategy_setup_id,
            )

        self._result = StartupGateResult(
            admitted=True,
            block_reason=None,
            reconciliation=summary,
            state_loaded=state_loaded,
            active_setup_ids=tuple(sorted(self.manager._active_trades)),
            strategy_setup_id=strategy_setup_id,
        )
        self._armed_fingerprint = fingerprint
        logger.info(
            "EXECUTION_STARTUP_GATE_ARMED state_loaded=%s active_trades=%s strategy_setup=%s",
            state_loaded, self._result.active_setup_ids, strategy_setup_id,
        )
        return self._result


__all__ = [
    "GATE_REFUSAL_CODE",
    "NOT_ARMED_REASON",
    "STALE_AUTHORIZATION_REASON",
    "ExecutionStartupGateError",
    "StartupGateResult",
    "ExecutionStartupGate",
]
