"""
Authoritative Signal-to-Execution Bridge & Controlled Trade Lifecycle Manager for QuantEdge AI.

Phase 5.7 Implementation:
1. Authoritative Signal-to-Execution Bridge:
   - Evaluates StrategyDecision (TRADE_SETUP_READY).
   - Strictly enforces server-side parameters (direction, entry, SL, TP, R:R).
   - Rejects any frontend tampering or fabricated values.
2. Complete Trade Lifecycle State Machine:
   - ENTRY_PENDING -> ENTRY_SUBMITTED -> ENTRY_PARTIALLY_FILLED -> ENTRY_FILLED
   - -> PROTECTION_PENDING -> SL_TP_SUBMITTED -> PROTECTED_POSITION -> POSITION_CLOSED
   - Handles ENTRY_REJECTED, ENTRY_CANCELLED, ENTRY_TIMEOUT, PROTECTION_FAILED, KILL_SWITCH_TRIGGERED.
3. Authoritative TP/SL Bracket Protection:
   - LONG: SL < Entry < TP
   - SHORT: TP < Entry < SL
   - Dynamically scales protection to exact filled quantity (partial fills 40 -> 70 -> 100).
   - Cancels stale bracket orders upon position closure.
4. Server-Side Daily Loss Guard:
   - Tracks realized losses for current trading day.
   - Rejects new entries if daily_loss >= configured_daily_loss_limit.
5. Emergency Kill Switch Controls:
   - One-click trigger cancels pending entries, halts new orders, preserves protective brackets.
   - Authenticated reset workflow.
6. Dual-Layer WebSocket & REST Reconciliation:
   - Processes real-time WebSocket events with authoritative REST fallback.
   - Zero real orders placed during tests.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
from enum import Enum
import inspect
import logging
import threading
from typing import Optional, Dict, Any, List, Sequence, Set, Union, Callable, Tuple

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    StopOrderType,
    StopTriggerMethod,
    TimeInForce,
    DeltaOrderRequest,
    DeltaOrderResponse,
    ConnectionState,
    ExecutionMode,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaClientError,
    DeltaAuthError,
    DeltaRateLimitError,
    DeltaOrderRejectedError,
    DeltaConnectionError,
    generate_deterministic_client_order_id,
)
from quantedge.execution.security import mask_secret, sanitize_text
from quantedge.execution.synchronizer import (
    LocalStateStore,
    OrderRecord,
    PositionRecord,
    PositionStatus,
    LiveAccountSyncService,
)
from quantedge.execution.validation import (
    OrderValidationGateway,
    OrderValidationRequest,
    OrderValidationResult,
    ProductSpecification,
    UnknownProductError,
    get_product_specification,
    RiskConfiguration,
    ValidationContext,
    RejectionReasonCode,
)
from quantedge.execution.private_websocket import (
    DeltaOrderEvent,
    DeltaPositionEvent,
    DeltaFillEvent,
    DeltaStreamIntegrityEvent,
    StreamHealth,
)
from quantedge.execution.algo_config import (
    AlgoConfigStore,
    AlgoConfiguration,
    AlgoConfigurationSnapshot,
)
from quantedge.execution.single_trade_lock import (
    SingleTradeLockManager,
    SingleTradeLockError,
)
from quantedge.execution.capital_allocator import (
    CapitalAllocator,
    CapitalAllocationError,
)
from quantedge.strategy.models import StrategyDecision, SetupState, StrategyDirection, TradeDirection

logger = logging.getLogger("trade_lifecycle")


# ── Lifecycle States & Enums ──────────────────────────────────────────────────


class TradeLifecycleState(str, Enum):
    ENTRY_PENDING = "ENTRY_PENDING"
    ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
    ENTRY_PARTIALLY_FILLED = "ENTRY_PARTIALLY_FILLED"
    ENTRY_FILLED = "ENTRY_FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    SL_TP_SUBMITTED = "SL_TP_SUBMITTED"
    PROTECTED_POSITION = "PROTECTED_POSITION"
    POSITION_CLOSED = "POSITION_CLOSED"
    ENTRY_REJECTED = "ENTRY_REJECTED"
    ENTRY_CANCELLED = "ENTRY_CANCELLED"
    ENTRY_TIMEOUT = "ENTRY_TIMEOUT"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    KILL_SWITCH_TRIGGERED = "KILL_SWITCH_TRIGGERED"


class CloseReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    KILL_SWITCH = "KILL_SWITCH"
    RISK_LIMIT = "RISK_LIMIT"
    EXCHANGE_LIQUIDATION = "EXCHANGE_LIQUIDATION"
    UNKNOWN_RECONCILIATION = "UNKNOWN_RECONCILIATION"


# ── Task O §O10: verdicts on a cancellation the exchange refused ──────────────
#
# A cancel that raises says nothing about what the order is now, so these four
# names are the only outcomes a caller may act on, and exactly one of them --
# GONE -- lets a caller proceed as if the cancellation had succeeded. There is
# deliberately no fifth value meaning "probably gone": an exception, an HTTP 400
# `order_not_found`, a missing field and a state this engine does not model all
# land in UNKNOWN and fail closed (safety rules #11, #13, #14, #15).
CANCEL_OUTCOME_GONE = "GONE"
CANCEL_OUTCOME_FILLED = "FILLED"
CANCEL_OUTCOME_LIVE = "LIVE"
CANCEL_OUTCOME_UNKNOWN = "UNKNOWN"


# ── Task O §O13: adopting the legs of an exchange-attached bracket ────────────
#
# Delta creates the protective legs of an attached bracket itself, and it does
# so when the entry fills -- which is the same moment `_ensure_bracket_protection`
# runs. So the legs may not be queryable on the first attempt. These bound how
# long the adoption path waits for them before falling back to placing the
# engine's own pair.
#
# Waiting here cannot open an unprotected window: the wait only happens after the
# exchange has confirmed, on the entry order itself, that it is holding a bracket
# at this record's levels, so the position is covered for the whole wait. The
# bound is small because the fallback is safe, not because the wait is risky.
BRACKET_ADOPTION_ATTEMPTS = 3
BRACKET_ADOPTION_DELAY_SECONDS = 1.0


# ── Deterministic order identity ──────────────────────────────────────────────
#
# Every order this manager submits is named by
# `generate_deterministic_client_order_id(account_id, setup_id, role)`. The
# random `generate_client_order_id` is deliberately NOT imported here any more:
# a restarted process must be able to recompute the identity of an order it
# already sent, and a timestamp-plus-uuid name makes that impossible. With the
# deterministic name, `get_order_by_client_id(record.entry_client_order_id)`
# resolves the same order after a restart as before it, and a resubmission of
# the same setup carries the same name rather than creating a second position.
#
# The role vocabulary is closed at three: one entry, one stop-loss, one
# take-profit per setup.
ORDER_ROLE_ENTRY = "ENTRY"
ORDER_ROLE_STOP_LOSS = "SL"
ORDER_ROLE_TAKE_PROFIT = "TP"

#: A replacement bracket may not reuse the client_order_id of a bracket that
#: was just cancelled.
#:
#: `_ensure_bracket_protection` resizes protection by cancelling the resting
#: SL/TP pair and placing a correctly sized pair for the SAME setup (§M2). If
#: both pairs were named `(account_id, setup_id, "SL")` the second placement
#: would replay a client_order_id the exchange has already seen. Whether Delta
#: accepts a replayed client_order_id after a cancel is UNVERIFIED -- it is not
#: something this repository has observed, and safety rule #16 forbids assuming
#: it. The cost of guessing wrong is not cosmetic: `place_order` would raise,
#: the `except` tail would mark PROTECTION_FAILED, and a live position would sit
#: unprotected.
#:
#: So each replacement generation gets its own role token -- SL/TP, then
#: SL2/TP2, SL3/TP3 -- produced by the SAME generator from a counter that is
#: part of the durable record. That keeps the name deterministic across a
#: restart (the counter is persisted, so generation N recomputes to the same
#: id) without depending on unverified replay semantics, and it does not
#: introduce a second identity scheme.
#:
#: `generate_deterministic_client_order_id` hashes the COMPLETE (account, setup,
#: role) triple, so no role token can truncate onto an earlier generation's name
#: -- "SL1000" and "SL100" are now distinct ids. The cap is retained unchanged
#: anyway, and deliberately: a setup that has resized its protection a thousand
#: times is a runaway loop rather than a trade, and failing closed on it costs
#: nothing. Its numeric value is unchanged so no persisted `protection_generation`
#: changes meaning.
MAX_PROTECTION_GENERATION = 998


class ProtectionGenerationExhaustedError(RuntimeError):
    """Raised when a setup has exhausted its distinct bracket role tokens."""


def protection_role(base_role: str, generation: int) -> str:
    """Return the role token for the `generation`-th placement of a bracket leg.

    Generation 0 -- the normal case, a position protected once -- returns the
    base role unchanged, so an ordinary trade's stop-loss really is named
    `(account_id, setup_id, "SL")`. Generation 1 returns "SL2": the human
    reading of "the second SL for this setup".

    Fails closed past `MAX_PROTECTION_GENERATION` rather than minting an
    unbounded number of role tokens for one setup.
    """
    if generation < 0:
        raise ProtectionGenerationExhaustedError(
            f"Protection generation cannot be negative (got {generation})"
        )
    if generation > MAX_PROTECTION_GENERATION:
        raise ProtectionGenerationExhaustedError(
            f"Protection generation {generation} exceeds the "
            f"{MAX_PROTECTION_GENERATION} distinct role tokens available for "
            f"role '{base_role}'; refusing to keep re-placing a bracket for one "
            f"setup without bound"
        )
    if generation == 0:
        return base_role
    return f"{base_role}{generation + 1}"


# ── Protection provenance (P2-2) ──────────────────────────────────────────────
#
# Two mechanisms can be holding a position's protective legs, and the engine
# behaves differently depending on which:
#
#   * the reduce-only STOP_MARKET + LIMIT pair this module places itself, whose
#     client_order_ids it minted deterministically and whose generation it
#     advances on every resize, and
#   * the legs Delta itself built from `bracket_stop_loss_price` /
#     `bracket_take_profit_price` on the entry, which this module ADOPTS (Task O
#     §O13) and whose client_order_ids belong to the exchange.
#
# Both end with real exchange order ids in `sl_order_id`/`tp_order_id`, which is
# why every consumer works on either -- but a record restored from disk that has
# forgotten WHICH it holds cannot answer "would placing a pair now duplicate
# protection?". Persisting the answer is cheaper and safer than re-deriving it,
# and re-deriving it is not always possible: an adopted leg's client_order_id
# does not match the name this engine would have minted.
PROTECTION_SOURCE_ENGINE = "ENGINE_PLACED"
PROTECTION_SOURCE_EXCHANGE_ADOPTED = "EXCHANGE_ADOPTED_BRACKET"

#: The complete vocabulary. Anything else in a persisted record is corrupt.
PROTECTION_SOURCES = (PROTECTION_SOURCE_ENGINE, PROTECTION_SOURCE_EXCHANGE_ADOPTED)


# ── Absent authoritative inputs fail closed ───────────────────────────────────
#
# `execute_trade_setup` used to substitute a number when the decision did not
# carry one: quantity became 1 contract, leverage became 100x, and the entry
# price became whatever `None` did downstream. None of those is a strategy
# output; each is this module answering a question only the strategy can answer,
# and each substitution sizes or prices a REAL order. A missing authoritative
# value is now a refusal, and no replacement value is chosen.
#
# These are plain string codes because `RejectionReasonCode` has no member for
# an absent input and this module already passes bare strings for conditions
# outside that vocabulary ("RECONCILIATION_REQUIRED", "SETUP_NOT_READY",
# "EXCHANGE_ERROR", "FRONTEND_*_TAMPERING").
REFUSAL_MISSING_ENTRY_PRICE = "MISSING_ENTRY_PRICE"
REFUSAL_MISSING_QUANTITY = "MISSING_QUANTITY"
REFUSAL_MISSING_LEVERAGE = "MISSING_CALCULATED_LEVERAGE"

#: An entry refused because the setup or the account cannot be NAMED. Plain
#: strings for the same reason as the three above: `RejectionReasonCode` has no
#: member for an absent input (`SETUP_NOT_FOUND` means a setup that was looked
#: up and is not there, which is a different fact).
REFUSAL_MISSING_SETUP_IDENTITY = "MISSING_SETUP_IDENTITY"
REFUSAL_MISSING_ACCOUNT_IDENTITY = "MISSING_ACCOUNT_IDENTITY"

#: An entry refused because the startup reconciliation boundary has not
#: reported clean. Set by an installed admission gate (see
#: `quantedge.execution.startup_gate`); with no gate installed this manager
#: behaves exactly as before, which keeps it usable as a library.
REFUSAL_STARTUP_GATE = "STARTUP_RECONCILIATION_REQUIRED"


def _is_authoritative_positive(value: Any) -> bool:
    """Is `value` a present, numeric, strictly positive quantity or price?

    Deliberately unanimous, like `_is_adoptable_bracket_leg`: `None`, a bool
    (`True` would otherwise pass as 1), a non-numeric string and a
    non-positive number all answer False, and the caller refuses. Nothing is
    coerced or repaired -- the answer is only ever "usable as given" or "not".
    """
    if value is None or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)) > Decimal("0")
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return False


def _is_authoritative_identity(value: Any) -> bool:
    """Is `value` an identity that can NAME a real order?

    `generate_deterministic_client_order_id` stringifies whatever it is handed,
    so `None` becomes the literal `"None"`: every setup arriving without an id
    would submit under ONE shared client order id, and an exchange that treats
    that name as idempotent would tie two unrelated entries together. That is the
    duplicate-identity class the deterministic naming exists to remove, re-entered
    through an unguarded input instead of through a random suffix. A blank or
    whitespace-only id is refused for the same reason, and a bool for the reason
    `_is_authoritative_positive` refuses it: `True` stringifies to a
    stable-looking name that no strategy chose. Nothing is coerced or repaired.
    """
    if value is None or isinstance(value, bool):
        return False
    try:
        return bool(str(value).strip())
    except Exception:                       # a __str__ that raises names nothing
        return False


def _is_comparable_price(value: Any) -> bool:
    """Can `value` take part in the TP/SL geometry comparison at all?

    Only the CRASH class is answered here. `None` -- and any non-numeric value --
    made `stop_loss < entry_price < take_profit` raise TypeError outside every
    handler, which returned no record to the caller and stranded the single-trade
    lock acquired at step 0, so the account could not trade again until the
    process restarted.

    Every NUMERIC value still passes through to the established geometry and
    risk-distance rules (`INVALID_TP_SL_GEOMETRY`,
    `ZERO_OR_NEGATIVE_RISK_DISTANCE`), including zero and negative ones: those
    are strategy outputs judged by a closed policy this check must not duplicate
    or tighten.
    """
    if value is None or isinstance(value, bool):
        return False
    try:
        Decimal(str(value))
        return True
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return False


# ── P2-1: acting on an EXISTING order uses the product it was submitted on ─────
#
# Submission captures `record.product_id` (line ~958), so the record knows which
# exchange product holds its position. Every LATER action on that same
# position -- resizing the bracket, cancelling an expired entry, closing out,
# tripping the kill switch -- re-derived the product id from the CURRENT registry
# instead of reading it back, and a registry that changed in between silently
# repointed the cancel at a different instrument.
#
# The registry can change: `get_product_specification` resolves through
# `DEFAULT_DELTA_INDIA_PRODUCTS` and `product_specification_from_instrument`, so a
# re-listed contract, a corrected id, or a symbol pointed at a different
# instrument all move the answer for an unchanged symbol string. Cancelling order
# 12345 "on product 14969" when the position lives on the old product does not
# fail loudly -- it can succeed against an unrelated product, or report a
# not-found that reads like "already terminal".
#
# So a disagreement raises. Deliberately an exception rather than a return code:
# all four call sites already treat a raise from product resolution as a
# fail-closed condition (unverified cancel -> trade retained -> reconciliation
# alert), which is exactly the required behaviour, and no new control flow has to
# be invented to get it. Behaviour is byte-identical whenever `product_id` is
# `None` (every record written before P2-1) or the registry agrees -- the guard
# can only ever turn a silent wrong-product action into a refusal.
#
# Reconciliation keeps its own, richer version of this check (a persisted alert
# plus RECONCILIATION_REQUIRED) because it is a sweep over every record and must
# report all of them rather than abort on the first.


class ProductIdentityMismatchError(RuntimeError):
    """A record's persisted product id disagrees with the current registry.

    Raised INSTEAD of acting on the record's existing exchange orders. Never
    raised to mean "unknown symbol" -- that is `UnknownProductError`, and the
    difference matters: one says the product cannot be resolved, this one says it
    resolves to something OTHER than the product the position is on.
    """


def _not_yet_unresolved(
    summary: Dict[str, Any], setup_ids: Sequence[str]
) -> List[str]:
    """The setup ids from `setup_ids` that `summary["unresolved"]` does not list.

    A setup can be reached by two independent findings in one run -- P1-5's
    "this record's exchange state is still ambiguous" and step 4's "a position
    exists that this zero-fill record cannot own" -- and both are correct. What
    must not happen is the same setup id appearing twice in `unresolved`, because
    an operator reading the summary would count two problems where there is one
    record with one unanswered question. Nothing is dropped: the alerts for both
    findings are still raised separately.
    """
    listed = set(summary["unresolved"])
    return [setup_id for setup_id in setup_ids if setup_id not in listed]


def _authoritative_product_specification(
    record: "TradeLifecycleRecord",
) -> ProductSpecification:
    """The product specification for `record`'s EXISTING exchange orders.

    Returns the registry's specification, having proved that its product id is
    the one this record was actually submitted against. The persisted id is the
    authority; the registry supplies the rest of the specification (symbol, tick
    size, contract value) that only it can provide.
    """
    spec = get_product_specification(record.symbol)
    if record.product_id is not None and record.product_id != spec.product_id:
        raise ProductIdentityMismatchError(
            f"setup {record.setup_id} was submitted against product "
            f"{record.product_id} but the product registry now resolves "
            f"{record.symbol} to product {spec.product_id}; refusing to act on "
            f"this record's orders under a different exchange product"
        )
    return spec


# ── Authoritative Lifecycle Record ────────────────────────────────────────────


@dataclass
class TradeLifecycleRecord:
    """Authoritative record tracking the complete lifecycle of a trade setup."""
    setup_id: str
    account_id: str
    user_id: Optional[str]
    symbol: str
    direction: TradeDirection
    requested_quantity: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    risk_reward_ratio: Decimal
    risk_amount: Decimal
    reward_amount: Decimal
    
    # Order tracking
    entry_order_id: Optional[str] = None
    entry_client_order_id: Optional[str] = None
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Optional[Decimal] = None
    #: P2-1: the exchange product this record's orders were actually submitted
    #: against, captured from `get_product_specification` at submission time and
    #: persisted as part of the execution identity. `None` means nothing was ever
    #: submitted for this record (a refusal, or a record still being built).
    #:
    #: Without this, a restart re-derived the product id from the CURRENT product
    #: registry, so a registry edit -- a re-listed contract, a corrected id, a
    #: symbol pointed at a different instrument -- would silently repoint a
    #: persisted record's cancels, bracket placements and reconciliation lookups
    #: at a DIFFERENT exchange product than the one holding the position. The id
    #: is now compared, and a mismatch blocks instead of rebinding.
    product_id: Optional[int] = None

    # Protective bracket orders
    sl_order_id: Optional[str] = None
    sl_client_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    tp_client_order_id: Optional[str] = None
    protected_quantity: Decimal = Decimal("0")
    #: P2-2: WHERE the live protection came from --
    #: `PROTECTION_SOURCE_ENGINE` for the reduce-only pair this engine placed,
    #: `PROTECTION_SOURCE_EXCHANGE_ADOPTED` for legs Delta built from the entry's
    #: attached bracket and this engine adopted, `None` for "no protection is
    #: recorded". Persisted, because a restart that forgets which of the two it
    #: is holding cannot tell whether placing a pair would ADD protection or
    #: DUPLICATE it -- and two live reduce-only stops on one position is exactly
    #: the duplicate protection §M2 forbids.
    protection_source: Optional[str] = None
    #: How many times this setup's protective bracket has been REPLACED. 0 for
    #: a position protected once, which is the normal case. Incremented only
    #: when a resize successfully cancels the resting pair, and used to derive
    #: the role token of the replacement legs so a replacement never replays a
    #: cancelled client_order_id (see `protection_role`). Durable: a restart
    #: must recompute the same names, so this field is part of the persisted
    #: execution state.
    protection_generation: int = 0
    
    # State & Outcome
    state: TradeLifecycleState = TradeLifecycleState.ENTRY_PENDING
    close_reason: Optional[CloseReason] = None
    realized_pnl: Optional[Decimal] = None
    gross_pnl: Optional[Decimal] = None
    # Task O §O2: `None` means the execution commission was never observed, which
    # is NOT the same as a zero-fee execution. `trading_fees_source` records
    # which of the two the number is, so no reader has to infer it from the
    # value, and `net_pnl_is_cost_complete` says outright whether `net_pnl`
    # includes every cost leg.
    trading_fees: Optional[Decimal] = Decimal("0")
    trading_fees_source: Optional[str] = None
    funding_costs: Decimal = Decimal("0")
    net_pnl: Optional[Decimal] = None
    net_pnl_is_cost_complete: bool = True
    gross_pnl_source: Optional[str] = None
    pre_trade_balance: Decimal = Decimal("0")
    post_trade_balance: Optional[Decimal] = None
    daily_loss_at_entry: Decimal = Decimal("0")
    rejection_code: Optional[str] = None
    error_message: Optional[str] = None
    
    # Configuration & Strategy Snapshot (Phase 5.7)
    config_version: Optional[int] = None
    config_snapshot: Optional[Any] = None
    strategy_name: str = "SMC"
    strategy_version: str = "2.1"
    
    # Audit log history
    history: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None

    def record_transition(self, new_state: TradeLifecycleState, reason: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Record an immutable state transition event in history."""
        now = datetime.now(timezone.utc)
        event = {
            "from_state": self.state.value,
            "to_state": new_state.value,
            "reason": reason,
            "metadata": metadata or {},
            "timestamp": now.isoformat(),
        }
        self.history.append(event)
        self.state = new_state
        self.updated_at = now
        if new_state == TradeLifecycleState.POSITION_CLOSED:
            self.closed_at = now


# ── Trade Lifecycle Manager ───────────────────────────────────────────────────


class TradeLifecycleManager:
    """Coordinates signal qualification, pre-trade risk/stale checks, order execution,

    bracket protection, partial fill scaling, position closure, and kill-switch safety.
    """

    def __init__(
        self,
        client: DeltaIndiaClient,
        validation_gateway: OrderValidationGateway,
        state_store: LocalStateStore,
        sync_service: Optional[LiveAccountSyncService] = None,
        algo_config_store: Optional[AlgoConfigStore] = None,
        single_trade_lock: Optional[SingleTradeLockManager] = None,
        capital_allocator: Optional[CapitalAllocator] = None,
        daily_loss_limit: Decimal = Decimal("500.00"),
        max_stale_seconds: int = 120,
        execution_mode: ExecutionMode = ExecutionMode.LIVE,
    ):
        self.client = client
        self.validation_gateway = validation_gateway
        self.state_store = state_store
        self.sync_service = sync_service
        self.algo_config_store = algo_config_store or AlgoConfigStore()
        self.single_trade_lock = single_trade_lock or SingleTradeLockManager()
        self.capital_allocator = capital_allocator or CapitalAllocator()
        self.daily_loss_limit = daily_loss_limit
        self.max_stale_seconds = max_stale_seconds
        self.execution_mode = execution_mode

        self._active_trades: Dict[str, TradeLifecycleRecord] = {}  # setup_id -> record
        self._trade_history: List[TradeLifecycleRecord] = []
        # P1-4: RE-ENTRANT, deliberately. The entry admission critical section
        # asks an installed safety gate whether it still admits, and a gate that
        # proves its verdict is still current (see
        # `ExecutionStartupGate.admission_reason`) does so by serializing this
        # manager's own execution state -- which takes this same lock. A plain
        # `threading.Lock` deadlocks on that nesting. Re-entrancy changes nothing
        # about mutual exclusion BETWEEN threads; it only permits the one thread
        # already inside a section to enter a nested one.
        self._lock = threading.RLock()
        # P1-4: how many admitted entries are between "admitted" and "returned".
        # Non-durable: it describes THIS process's in-flight work, and a restart
        # has no in-flight work by definition. Read by reconciliation, which
        # refuses to call a world clean while a submission it cannot see is in
        # flight.
        self._submissions_in_flight: int = 0

        # Realized daily loss cache
        self._realized_daily_losses: Dict[date, Decimal] = {}

        # Task M: authoritative exchange execution facts observed for the trades
        # this manager owns, keyed by setup_id. Fees are accumulated from the
        # private `user_trades` stream (and from nothing else), so a closure
        # never has to invent a fee figure. Every accumulator is keyed off the
        # exchange trade_id in `_processed_fill_trade_ids`, which makes them
        # idempotent when the same execution is seen twice (WebSocket replay
        # after a reconnect, or WebSocket plus REST reconciliation).
        #
        # Task O §O2 -- this accumulator has THREE distinguishable states, and
        # the difference between them is the whole point:
        #
        #   key absent   -> no execution was ever observed for the trade
        #   value None   -> at least one observed execution carried no
        #                   `commission`, so the total is unknowable (sticky:
        #                   a later priced leg cannot repair it)
        #   value Decimal-> every observed leg carried a commission; the exact
        #                   signed sum, maker rebates included
        self._observed_fill_fees: Dict[str, Optional[Decimal]] = {}
        self._processed_fill_trade_ids: Set[str] = set()
        self._entry_fill_sizes: Dict[str, Decimal] = {}
        self._exit_fill_sizes: Dict[str, Decimal] = {}
        self._exit_fill_notional: Dict[str, Decimal] = {}
        self._exit_fill_roles: Dict[str, Set[str]] = {}
        # Conditions that must block further entries until an operator or a
        # successful reconciliation clears them (§M11 / §M15).
        self._reconciliation_alerts: List[Dict[str, Any]] = []
        # The private order/position stream this manager observes, once bound.
        self._private_stream: Optional[Any] = None
        # Set by the orchestrator so an exchange-side closure can run the
        # existing closure/rescan flow without this module importing it.
        self._closure_handler: Optional[Callable[..., Any]] = None
        # The startup reconciliation boundary, once installed. A zero-argument
        # callable returning `None` to admit a new entry, or a reason string to
        # refuse it. `None` here means NO gate is installed, which is this
        # manager's long-standing library behaviour and what the ~100 existing
        # `execute_trade_setup` tests exercise; a production composition is
        # expected to install `ExecutionStartupGate`, which starts BLOCKED.
        self._entry_admission_gate: Optional[Callable[[], Optional[str]]] = None

    def get_realized_daily_loss(self, query_date: Optional[date] = None) -> Decimal:
        """Calculate total realized trading loss for the specified day."""
        target_date = query_date or datetime.now(timezone.utc).date()
        total_loss = Decimal("0")
        for trade in self._trade_history:
            if trade.closed_at and trade.closed_at.date() == target_date:
                if trade.realized_pnl and trade.realized_pnl < Decimal("0"):
                    total_loss += abs(trade.realized_pnl)
        return total_loss

    def get_active_trade(self, setup_id: str) -> Optional[TradeLifecycleRecord]:
        return self._active_trades.get(setup_id)

    def get_all_active_trades(self) -> List[TradeLifecycleRecord]:
        return list(self._active_trades.values())

    def get_trade_history(self) -> List[TradeLifecycleRecord]:
        return list(self._trade_history)

    def _entry_admission_reason(self) -> Optional[str]:
        """Why a new entry is refused right now, or `None` if it is admitted.

        Fail-closed in both directions: with no gate installed there is nothing
        to refuse (library behaviour, unchanged), and with a gate installed an
        EXCEPTION from the gate is a refusal rather than an admission -- a
        broken boundary must not open the door.
        """
        gate = self._entry_admission_gate
        if gate is None:
            return None
        try:
            reason = gate()
        except Exception as exc:                                   # noqa: BLE001
            logger.critical("ENTRY_ADMISSION_GATE_RAISED error=%s", exc)
            return f"entry admission gate failed: {exc}"
        if reason is None:
            return None
        return str(reason)

    # ── Signal-to-Execution Bridge Entry Point ────────────────────────────────

    async def execute_trade_setup(
        self,
        decision: StrategyDecision,
        account_id: str,
        user_id: Optional[str] = None,
        override_client_order_id: Optional[str] = None,
        frontend_params: Optional[Dict[str, Any]] = None,
    ) -> TradeLifecycleRecord:
        """Execute a qualified strategy decision through the authoritative validation and execution bridge.

        P1-4/P2-6: this method is the ONE canonical entry admission point, and
        the four questions that decide admission -- is this setup already live,
        does the installed safety gate admit it, are there unresolved
        reconciliation alerts, and can the account's single-trade lock be taken
        -- are answered inside a SINGLE critical section holding `self._lock`.
        They used to be answered one after another with the lock released
        between them, which is the TOCTOU the audit found: reconciliation could
        see a clean world, an entry could be admitted against it, and the entry
        could then proceed on a verdict that no longer described the account.
        The section takes no network round trip; the exchange is only contacted
        after it has been left.

        `_submissions_in_flight` is raised inside the same section and lowered in
        `finally`, so `reconcile_active_trades_with_exchange` can refuse to
        declare a world clean while a submission it cannot see is in flight.
        """
        setup_id = decision.setup_id
        symbol = decision.symbol

        # P2-5: the two identities that NAME this order and SCOPE its lock are
        # checked before the critical section, so an unusable one takes no lock
        # and occupies no duplicate-setup slot. Both are hashed into the
        # deterministic client order id, and `str(None)` is a perfectly valid
        # string: an absent setup id used to produce one shared order name for
        # every such setup, and a blank account id would take the account lock
        # under a blank key that every other blank-account caller also matches.
        # `_create_rejected_record` is safe to use here even though no lock is
        # held: `release_lock` clears state only when that exact setup holds it,
        # so it is a no-op rather than a lock this call could steal.
        if not _is_authoritative_identity(setup_id):
            logger.critical(
                "ENTRY_BLOCKED_MISSING_SETUP_IDENTITY symbol=%s value=%r",
                symbol, setup_id,
            )
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction,
                Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                REFUSAL_MISSING_SETUP_IDENTITY,
                f"Strategy decision carried no usable setup id (got {setup_id!r}); "
                f"refusing to name a real order from a substituted identity",
            )

        if not _is_authoritative_identity(account_id):
            logger.critical(
                "ENTRY_BLOCKED_MISSING_ACCOUNT_IDENTITY setup=%s symbol=%s value=%r",
                setup_id, symbol, account_id,
            )
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction,
                Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                REFUSAL_MISSING_ACCOUNT_IDENTITY,
                f"No usable account id was supplied (got {account_id!r}); refusing "
                f"to submit and lock an order against a substituted account",
            )

        # ── Admission: one critical section, no I/O ───────────────────────────
        refusal: Optional[Tuple[str, str]] = None
        with self._lock:
            if setup_id in self._active_trades:
                rec = self._active_trades[setup_id]
                rec.rejection_code = RejectionReasonCode.DUPLICATE_SETUP_ID.value
                rec.error_message = f"Setup {setup_id} is already in active execution"
                return rec

            # The startup reconciliation boundary. Persisted execution state must
            # be loaded and reconciliation must have completed clean before ANY
            # new entry is admitted; an installed gate that has not armed, or
            # whose clean verdict no longer describes current execution state,
            # refuses here. Deliberately checked AFTER the duplicate-setup guard
            # so an already-live trade is still returned as itself (never
            # re-reported as rejected, and never at risk of having its lock
            # released), and BEFORE the alert check so a manager whose state was
            # never loaded cannot be trusted to have meaningful alerts yet.
            gate_reason = self._entry_admission_reason()
            if gate_reason is not None:
                logger.critical(
                    "ENTRY_BLOCKED_STARTUP_GATE setup=%s symbol=%s reason=%s",
                    setup_id, symbol, gate_reason,
                )
                refusal = (REFUSAL_STARTUP_GATE, gate_reason)

            # Task M §M11/§M15: an unresolved reconciliation condition means the
            # exchange and local state provably disagree somewhere -- a possibly
            # live order, a possibly unprotected position, or accounting that
            # could not be verified. Trading on top of that is exactly what
            # fail-closed forbids, so no new entry is admitted until it is
            # cleared.
            elif self._reconciliation_alerts:
                codes = sorted({a["code"] for a in self._reconciliation_alerts})
                logger.critical(
                    "ENTRY_BLOCKED_RECONCILIATION_REQUIRED setup=%s symbol=%s alerts=%s",
                    setup_id, symbol, codes,
                )
                refusal = (
                    "RECONCILIATION_REQUIRED",
                    "Unresolved reconciliation conditions block new entries: "
                    f"{', '.join(codes)}",
                )

            else:
                # Step 0: Single-Trade Account Lock. Inside the same section as
                # the checks above, so nothing can be admitted between passing
                # them and owning the account.
                effective_user_id = (
                    user_id or self.state_store.account.user_id or "default_user"
                )
                try:
                    self.single_trade_lock.acquire_lock(
                        user_id=effective_user_id,
                        account_id=account_id,
                        setup_id=setup_id,
                        symbol=symbol,
                    )
                except SingleTradeLockError as e:
                    refusal = (
                        RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value, str(e)
                    )
                else:
                    self._submissions_in_flight += 1

        if refusal is not None:
            code, message = refusal
            # P2-5: a refused entry fabricates NO execution-critical value. The
            # previous `Decimal("1.0")` quantity and `Decimal("2.0")` R:R here
            # were this module answering questions only the strategy can answer,
            # and `_create_rejected_record` multiplies the quantity into the
            # persisted `risk_amount`/`reward_amount` -- so a refusal invented
            # the risk figures of a trade that never existed. Absent values are
            # reported as absent, exactly as the MISSING_* refusals below do.
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction,
                Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                code, message,
            )

        try:
            return await self._execute_admitted_trade_setup(
                decision,
                account_id,
                user_id=user_id,
                override_client_order_id=override_client_order_id,
                frontend_params=frontend_params,
            )
        finally:
            with self._lock:
                if self._submissions_in_flight > 0:
                    self._submissions_in_flight -= 1

    async def _execute_admitted_trade_setup(
        self,
        decision: StrategyDecision,
        account_id: str,
        user_id: Optional[str] = None,
        override_client_order_id: Optional[str] = None,
        frontend_params: Optional[Dict[str, Any]] = None,
    ) -> TradeLifecycleRecord:
        """Validate and submit an entry that `execute_trade_setup` has ADMITTED.

        Never call this directly: admission (duplicate guard, safety gate,
        reconciliation alerts, single-trade lock) happens in
        `execute_trade_setup` and this half assumes it has already succeeded.
        Split out only so the admission checks can share one critical section;
        the body below is otherwise unchanged.
        """
        setup_id = decision.setup_id
        symbol = decision.symbol

        # Extract authoritative attributes. Read exactly as before; what changed
        # is that an absent or unusable value is no longer replaced with a
        # number of this module's choosing.
        setup_state = getattr(decision, "setup_state", None) or getattr(decision, "status", None)
        entry_price = getattr(decision, "entry", None) or getattr(decision, "entry_price", None)
        stop_loss = decision.stop_loss
        take_profit = decision.take_profit
        risk_reward = getattr(decision, "risk_reward", None) or getattr(decision, "risk_reward_ratio", Decimal("2.0"))
        quantity = getattr(decision, "quantity", None)

        # The entry price is the price a real order rests at and the basis of
        # every sizing and margin figure computed from this record. There is no
        # safe substitute for it.
        #
        # This also removes a latent crash: with `entry_price = None` the step-4
        # geometry comparison `stop_loss < entry_price < take_profit` raised
        # TypeError outside every handler, so the single-trade lock acquired at
        # step 0 above was stranded and the account could not trade again until
        # the process restarted. `_create_rejected_record` releases it.
        if not _is_authoritative_positive(entry_price):
            logger.critical(
                "ENTRY_BLOCKED_MISSING_ENTRY_PRICE setup=%s symbol=%s value=%r",
                setup_id, symbol, entry_price,
            )
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction,
                Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                REFUSAL_MISSING_ENTRY_PRICE,
                f"Strategy decision carried no usable entry price (got {entry_price!r}); "
                f"refusing to price an order on a substituted value",
            )

        # The quantity is the size of a real position. The previous default of
        # one contract was not a strategy output.
        if not _is_authoritative_positive(quantity):
            logger.critical(
                "ENTRY_BLOCKED_MISSING_QUANTITY setup=%s symbol=%s value=%r",
                setup_id, symbol, quantity,
            )
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction,
                Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                REFUSAL_MISSING_QUANTITY,
                f"Strategy decision carried no usable quantity (got {quantity!r}); "
                f"refusing to size an order on a substituted value",
            )

        # The stop is the only thing bounding this position's loss and the target
        # is the only thing closing it in profit; step 4 below compares both
        # against the entry price. An absent or non-numeric value made that
        # comparison raise TypeError outside every handler -- no record was
        # returned to the caller and the step-0 single-trade lock was stranded,
        # so the account could not trade again until the process restarted. This
        # refuses ONLY that class; every numeric value still reaches the
        # established geometry and risk-distance rules unchanged.
        #
        # The codes are the repository's existing vocabulary for this exact
        # condition -- `OrderValidationGateway` already emits MISSING_STOP_LOSS /
        # MISSING_TAKE_PROFIT from `CHECK_MISSING_SL` / `CHECK_MISSING_TP` -- so
        # an operator sees one code whichever layer refuses first, and no new
        # constant is minted for a condition that already had one.
        if not _is_comparable_price(stop_loss):
            logger.critical(
                "ENTRY_BLOCKED_MISSING_STOP_LOSS setup=%s symbol=%s value=%r",
                setup_id, symbol, stop_loss,
            )
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction,
                Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                RejectionReasonCode.MISSING_STOP_LOSS.value,
                f"Strategy decision carried no usable stop loss (got {stop_loss!r}); "
                f"refusing to submit an entry whose loss is unbounded",
            )

        if not _is_comparable_price(take_profit):
            logger.critical(
                "ENTRY_BLOCKED_MISSING_TAKE_PROFIT setup=%s symbol=%s value=%r",
                setup_id, symbol, take_profit,
            )
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction,
                Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                RejectionReasonCode.MISSING_TAKE_PROFIT.value,
                f"Strategy decision carried no usable take profit (got {take_profit!r}); "
                f"refusing to submit an entry with no target",
            )

        raw_risk = getattr(decision, "risk_amount", None)
        risk_amount = raw_risk if raw_risk is not None else (abs(entry_price - stop_loss) * quantity if entry_price and stop_loss else Decimal("0"))
        raw_reward = getattr(decision, "reward_amount", None)
        reward_amount = raw_reward if raw_reward is not None else (abs(take_profit - entry_price) * quantity if entry_price and take_profit else Decimal("0"))

        # 1. Authoritative Parameter Enforcement & Anti-Tampering Check
        if frontend_params:
            # Check if frontend attempted to alter direction, prices, leverage, quantity, or R:R
            if "direction" in frontend_params and frontend_params["direction"] != decision.direction.value:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "FRONTEND_DIRECTION_TAMPERING", "Frontend cannot alter authoritative trade direction"
                )
            if "entry_price" in frontend_params and Decimal(str(frontend_params["entry_price"])) != entry_price:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "FRONTEND_ENTRY_TAMPERING", "Frontend cannot alter authoritative entry price"
                )
            if "stop_loss" in frontend_params and Decimal(str(frontend_params["stop_loss"])) != stop_loss:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "FRONTEND_SL_TAMPERING", "Frontend cannot alter authoritative stop loss"
                )
            if "take_profit" in frontend_params and Decimal(str(frontend_params["take_profit"])) != take_profit:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "FRONTEND_TP_TAMPERING", "Frontend cannot alter authoritative take profit"
                )
            if "leverage" in frontend_params:
                fe_lev = int(frontend_params["leverage"])
                expected_lev = getattr(decision, "calculated_leverage", None)
                if expected_lev is not None and fe_lev != expected_lev:
                    return self._create_rejected_record(
                        setup_id, account_id, user_id, symbol, decision.direction, quantity,
                        entry_price, stop_loss, take_profit, risk_reward,
                        "FRONTEND_LEVERAGE_TAMPERING", "Frontend cannot alter authoritative leverage"
                    )
            if "quantity" in frontend_params or "position_size" in frontend_params:
                fe_qty = Decimal(str(frontend_params.get("quantity") or frontend_params.get("position_size")))
                if fe_qty != quantity:
                    return self._create_rejected_record(
                        setup_id, account_id, user_id, symbol, decision.direction, quantity,
                        entry_price, stop_loss, take_profit, risk_reward,
                        "FRONTEND_QUANTITY_TAMPERING", "Frontend cannot alter authoritative position size"
                    )

        # 2. Verify Strategy Status
        if setup_state != SetupState.TRADE_SETUP_READY:
            state_val = setup_state.value if hasattr(setup_state, 'value') else str(setup_state)
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction, quantity,
                entry_price, stop_loss, take_profit, risk_reward,
                "SETUP_NOT_READY", f"Strategy decision status is {state_val}, expected TRADE_SETUP_READY"
            )

        # 3. Verify Account Fail-Safe Flags (Kill-Switch & Algo-Enabled)
        if self.state_store.account.kill_switch_active:
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction, quantity,
                entry_price, stop_loss, take_profit, risk_reward,
                RejectionReasonCode.KILL_SWITCH_ACTIVE.value, "Emergency kill switch is active — all trade execution blocked"
            )

        if not self.state_store.account.algo_enabled:
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction, quantity,
                entry_price, stop_loss, take_profit, risk_reward,
                RejectionReasonCode.ALGO_DISABLED.value, "Algorithmic trading is disabled on this account"
            )

        # 3b. Verify Connection State in LIVE mode
        if self.execution_mode == ExecutionMode.LIVE:
            conn_state = getattr(self.client, "connection_state", ConnectionState.UNKNOWN)
            if conn_state == ConnectionState.AUTH_FAILED:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "AUTH_FAILED", "Delta Exchange authentication failed — check API credentials"
                )
            elif conn_state == ConnectionState.RATE_LIMITED:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "RATE_LIMITED", "Delta Exchange rate limit active — order submission blocked"
                )
            elif conn_state in (ConnectionState.EXCHANGE_ERROR, ConnectionState.DISCONNECTED):
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    "EXCHANGE_ERROR", f"Delta Exchange connection state is {conn_state.value}"
                )

        # 4. Verify TP/SL Geometry
        if decision.direction in (TradeDirection.LONG, StrategyDirection.LONG):
            if not (stop_loss < entry_price < take_profit):
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    RejectionReasonCode.INVALID_TP_SL_GEOMETRY.value,
                    f"Invalid LONG geometry: SL ({stop_loss}) must be < Entry ({entry_price}) < TP ({take_profit})"
                )
        else:  # SHORT
            if not (take_profit < entry_price < stop_loss):
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    RejectionReasonCode.INVALID_TP_SL_GEOMETRY.value,
                    f"Invalid SHORT geometry: TP ({take_profit}) must be < Entry ({entry_price}) < SL ({stop_loss})"
                )

        # 5. Server-Side Daily Loss Guard
        today_loss = self.get_realized_daily_loss()
        if today_loss >= self.daily_loss_limit:
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction, quantity,
                entry_price, stop_loss, take_profit, risk_reward,
                RejectionReasonCode.DAILY_LOSS_LIMIT.value,
                f"Daily realized loss (${today_loss:.2f}) meets/exceeds limit (${self.daily_loss_limit:.2f})"
            )

        # 6. Stale Account State Check
        if self.state_store.account.last_synced_at:
            age_seconds = (datetime.now(timezone.utc) - self.state_store.account.last_synced_at).total_seconds()
            if age_seconds > self.max_stale_seconds:
                return self._create_rejected_record(
                    setup_id, account_id, user_id, symbol, decision.direction, quantity,
                    entry_price, stop_loss, take_profit, risk_reward,
                    RejectionReasonCode.ACCOUNT_STATE_STALE.value,
                    f"Account state is stale ({age_seconds:.0f}s old, max allowed {self.max_stale_seconds}s)"
                )

        # 7. Create authoritative TradeLifecycleRecord & Bind Immutable Config Snapshot
        effective_user_id = user_id or self.state_store.account.user_id or "default_user"
        snapshot = self.algo_config_store.create_trade_snapshot(
            user_id=effective_user_id,
            account_id=account_id,
            setup_id=setup_id,
        )

        # The leverage this order is validated against must come from the
        # strategy's own sizing. The previous `or 100` silently substituted the
        # TOP of the authorised band -- the one substitution guaranteed to pass
        # the band check downstream while multiplying the position's real margin
        # exposure by up to 100.
        #
        # Only ABSENCE is refused here. A present-but-out-of-band value is left
        # to the authorised band enforcement in `OrderValidationGateway`
        # (`CHECK_LEVERAGE_CAP`: 0x and 101x are rejected, never clamped), which
        # is a closed policy this change does not touch or duplicate. No
        # leverage field is added to any Delta payload.
        effective_leverage = getattr(decision, "calculated_leverage", None)
        if effective_leverage is None:
            logger.critical(
                "ENTRY_BLOCKED_MISSING_CALCULATED_LEVERAGE setup=%s symbol=%s",
                setup_id, symbol,
            )
            return self._create_rejected_record(
                setup_id, account_id, user_id, symbol, decision.direction,
                Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                REFUSAL_MISSING_LEVERAGE,
                "Strategy decision carried no calculated leverage; refusing to "
                "validate an order against a substituted leverage",
            )

        record = TradeLifecycleRecord(
            setup_id=setup_id,
            account_id=account_id,
            user_id=user_id,
            symbol=symbol,
            direction=decision.direction if isinstance(decision.direction, TradeDirection) else (TradeDirection.LONG if decision.direction == StrategyDirection.LONG else TradeDirection.SHORT),
            requested_quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            risk_reward_ratio=risk_reward,
            risk_amount=risk_amount,
            reward_amount=reward_amount,
            pre_trade_balance=self.state_store.account.available_balance,
            daily_loss_at_entry=today_loss,
            config_version=snapshot.version,
            config_snapshot=snapshot,
            strategy_name=getattr(decision, "strategy_name", "SMC"),
            strategy_version=getattr(decision, "strategy_version", "2.1"),
        )

        with self._lock:
            self._active_trades[setup_id] = record

        # 8. Submit Entry Order via OrderValidationGateway
        try:
            product_spec = get_product_specification(symbol)
        except UnknownProductError as e:
            # An unregistered symbol is the same fail-closed condition the
            # gateway reports as UNSUPPORTED_SYMBOL, so it is returned as a
            # rejected record rather than raised out of a function whose
            # callers read the outcome from the returned record. Nothing has
            # been sent to the exchange at this point, so the single-trade lock
            # this call acquired must be released instead of stranded.
            record.record_transition(
                TradeLifecycleState.ENTRY_REJECTED,
                f"Order validation rejected: "
                f"{RejectionReasonCode.UNSUPPORTED_SYMBOL.value} - {e}"
            )
            record.rejection_code = RejectionReasonCode.UNSUPPORTED_SYMBOL.value
            record.error_message = str(e)
            self._release_setup_lock(user_id, account_id, setup_id)
            return record

        # P2-1: the product this record's orders are about to be submitted
        # against becomes part of its persisted identity, BEFORE anything is
        # sent. A later run compares the persisted id against what the registry
        # resolves then, and blocks on disagreement rather than silently acting
        # on a different exchange product than the one holding the position.
        record.product_id = product_spec.product_id
        # Deterministic identity. The same (account_id, setup_id, ENTRY) triple
        # always names the same order, so a process that restarts between
        # submitting this entry and observing its fill can recover the order by
        # recomputing its name (`_find_entry_order` already looks it up with
        # `get_order_by_client_id`) instead of inferring which of the account's
        # resting orders was its own. It also means a replayed setup carries the
        # name the exchange has already seen rather than opening a second
        # position under a fresh random name. The `override_client_order_id`
        # escape is preserved unchanged for callers that supply their own.
        client_order_id = override_client_order_id or generate_deterministic_client_order_id(
            account_id, setup_id, ORDER_ROLE_ENTRY
        )
        record.entry_client_order_id = client_order_id

        side = OrderSide.BUY if record.direction == TradeDirection.LONG else OrderSide.SELL

        order_req = DeltaOrderRequest(
            product_id=product_spec.product_id,
            # Both identity fields come from the spec resolved above. `symbol`
            # is necessarily equal to it -- `get_product_specification` is an
            # exact lookup and every record in that table is keyed by its own
            # `spec.symbol` -- so this is the same value, sourced structurally
            # from the authority rather than from `StrategyDecision.symbol`.
            product_symbol=product_spec.symbol,
            side=side,
            order_type=OrderType.LIMIT_ORDER,
            size=record.requested_quantity,
            limit_price=record.entry_price,
            client_order_id=client_order_id,
            stop_loss_price=record.stop_loss_price,
            take_profit_price=record.take_profit_price,
        )

        val_request = OrderValidationRequest(
            account_id=account_id,
            setup_id=setup_id,
            symbol=symbol,
            direction=record.direction,
            order_type=OrderType.LIMIT_ORDER,
            quantity=record.requested_quantity,
            entry_price=record.entry_price,
            stop_loss=record.stop_loss_price,
            take_profit=record.take_profit_price,
            leverage=effective_leverage,
            client_order_id=client_order_id,
        )

        eff_risk_pct = snapshot.risk_per_trade_pct if (snapshot.risk_per_trade_pct and snapshot.risk_per_trade_pct > Decimal("1.00")) else Decimal("35.0")
        risk_cfg = RiskConfiguration(
            # P2-5: the ceiling this order is validated against is read, never
            # substituted. The previous `or 100` named the TOP of the authorised
            # band as a fallback -- the one value certain to pass the band check
            # below it. `AlgoConfigurationSnapshot.max_leverage` is a non-optional
            # int already band-checked in `__post_init__`, so there is nothing for
            # a fallback to answer; the authorised 1x-100x policy itself is
            # unchanged and still enforced by `CHECK_LEVERAGE_CAP`.
            max_leverage=snapshot.max_leverage,
            risk_per_trade_pct=eff_risk_pct,
        )

        context = ValidationContext(
            account=self.state_store.account,
            risk_config=risk_cfg,
            algo_enabled=self.state_store.account.algo_enabled,
            kill_switch_active=self.state_store.account.kill_switch_active,
            connection=self.state_store.connection,
            # A client that exposes no credential attribute is a client that
            # cannot prove it is authenticated, and the honest way to say so is
            # `None` -- which `ValidationContext` already declares as the field's
            # own default and which `CHECK_API_CREDENTIALS` refuses before the
            # exchange is ever contacted.
            #
            # The previous fallbacks were the literals "MOCKED_API_KEY" /
            # "MOCKED_API_SECRET". At 15 and 18 characters they cleared that
            # check's length floor, so a client with no credential at all
            # validated as credentialled: a default answering a safety question
            # (rule #13). Nothing legitimate depended on them -- the real
            # production and test clients both set `_api_key` / `_api_secret` --
            # so removing them narrows the accepted world without substituting a
            # new value for a missing one.
            api_key=getattr(self.client, "_api_key", None),
            api_secret=getattr(self.client, "_api_secret", None),
            open_positions=self.state_store.get_open_positions(),
            open_orders=self.state_store.get_open_orders(),
            active_setup_ids=set(k for k in self._active_trades.keys() if k != setup_id),
        )

        val_result = self.validation_gateway.validate(val_request, context)
        if not val_result.is_valid:
            record.record_transition(
                TradeLifecycleState.ENTRY_REJECTED,
                f"Order validation rejected: {val_result.rejection_code.value} - {val_result.rejection_reason}"
            )
            record.rejection_code = val_result.rejection_code.value
            record.error_message = val_result.rejection_reason
            # Rejected before submission: no order and no position exist, so
            # the lock acquired by this call is released here.
            self._release_setup_lock(user_id, account_id, setup_id)
            return record

        # 9. Submit Order to Exchange
        record.record_transition(TradeLifecycleState.ENTRY_SUBMITTED, f"Submitted limit entry order {client_order_id}")

        try:
            order_resp = await self.client.place_order(order_req)
            resp_id = getattr(order_resp, "id", None) or (order_resp.get("order_id") or order_resp.get("id") if isinstance(order_resp, dict) else str(order_resp))
            resp_state = getattr(order_resp, "state", None) or (order_resp.get("state") if isinstance(order_resp, dict) else OrderStatus.OPEN)
            resp_filled = getattr(order_resp, "filled_size", None) or (Decimal(str(order_resp.get("filled_size", 0))) if isinstance(order_resp, dict) else Decimal("0"))
            resp_size = getattr(order_resp, "size", None) or (Decimal(str(order_resp.get("size", record.requested_quantity))) if isinstance(order_resp, dict) else record.requested_quantity)
            resp_avg_price = getattr(order_resp, "average_fill_price", None) or (Decimal(str(order_resp["average_fill_price"])) if isinstance(order_resp, dict) and order_resp.get("average_fill_price") else None)

            record.entry_order_id = str(resp_id)
            
            # Record entry order in state store
            self.state_store.orders[str(resp_id)] = OrderRecord(
                delta_order_id=str(resp_id),
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                order_type=OrderType.LIMIT_ORDER,
                quantity=record.requested_quantity,
                filled_quantity=resp_filled,
                status=resp_state if isinstance(resp_state, OrderStatus) else OrderStatus.OPEN,
                price=record.entry_price,
            )

            # Check if immediately filled or partially filled
            if resp_state == OrderStatus.FILLED or str(resp_state).lower() == "filled":
                await self.on_entry_fill(setup_id, resp_size, resp_avg_price or record.entry_price)
            elif resp_filled > Decimal("0"):
                await self.on_entry_partial_fill(setup_id, resp_filled, resp_avg_price or record.entry_price)

            return record

        except DeltaOrderRejectedError as e:
            record.record_transition(TradeLifecycleState.ENTRY_REJECTED, f"Exchange rejected order: {str(e)}")
            record.error_message = str(e)
            # An explicit exchange rejection means the order does not exist, so
            # no position can exist and the lock is released.
            self._release_setup_lock(user_id, account_id, setup_id)
            return record
        except (DeltaConnectionError, asyncio.TimeoutError) as e:
            record.record_transition(TradeLifecycleState.RECONCILIATION_REQUIRED, f"Network timeout during entry submission: {str(e)}")
            record.error_message = str(e)
            # LOCK INTENTIONALLY RETAINED. The order may have reached Delta, so
            # a position may exist. Releasing here would allow a second trade
            # alongside a possibly-live, possibly-unprotected one (safety rules
            # #11, #14). The lock is released by close_position/reconciliation.
            #
            # P1-5: the retained lock and the record state were the ONLY traces
            # of this condition, and neither is a first-class alert -- so nothing
            # blocked on it explicitly, nothing survived into the operator-facing
            # alert list, and a later reconciliation run could quietly move on.
            # The ambiguity is now recorded through the existing alert model, with
            # the identity needed to go and ask the exchange what happened: the
            # setup, the deterministic client order id of the attempt, and the
            # requested size. It is NOT assumed to have failed, it is NOT retried
            # here, and it can only be cleared by an authoritative reconciliation
            # (which the post-loop sweep in
            # `reconcile_active_trades_with_exchange` now prevents from being a
            # coincidentally-empty listing).
            self._raise_reconciliation_alert(
                "AMBIGUOUS_ENTRY_SUBMISSION", symbol,
                f"setup {setup_id}: entry submission of {record.requested_quantity} "
                f"({record.direction.value}) neither confirmed nor refused by the "
                f"exchange ({type(e).__name__}: {e}); the order may be live. No "
                f"retry is attempted and the portfolio lock is retained until "
                f"authoritative reconciliation resolves it",
                context={
                    "setup_id": setup_id,
                    "symbol": symbol,
                    "direction": record.direction.value,
                    "client_order_id": client_order_id,
                    "requested_quantity": str(record.requested_quantity),
                    "entry_price": str(record.entry_price),
                    "product_id": product_spec.product_id,
                    "account_id": account_id,
                    "error_type": type(e).__name__,
                },
            )
            return record
        except Exception as e:
            record.record_transition(TradeLifecycleState.ENTRY_REJECTED, f"Unexpected error during entry: {str(e)}")
            record.error_message = str(e)
            # LOCK INTENTIONALLY RETAINED. This handler also covers failures
            # after place_order returned (response parsing, fill handling and
            # bracket placement), so exchange state is unknown here. Retaining
            # is the fail-safe direction (safety rules #11, #14).
            return record

    # ── Partial Fill & Fill Lifecycle Handlers ────────────────────────────────

    @staticmethod
    def _fill_notification_to_ignore(
        record: TradeLifecycleRecord, filled_size: Decimal
    ) -> Optional[str]:
        """Why this fill notification must not be applied, or None to apply it.

        Task O §O14. The two handlers below are where every fill observation
        ends up, and they are public: the synchronous immediate-fill branch of
        `execute_trade_setup` calls them directly, and so does any operator loop
        polling for a fill. Only the WebSocket / REST paths arrive through
        `_apply_entry_order_state`, which already refuses terminal records and
        duplicate fills before delegating here (§M10). A caller that does not
        pass through it re-enters an unguarded handler, and because
        `record_transition` records whatever it is given, a replayed fill for a
        position that is ALREADY protected rewinds PROTECTED_POSITION back to
        ENTRY_FILLED -- observed on the §O14 XRPUSD trade. Nothing restores it
        afterwards, because `_ensure_bracket_protection` correctly no-ops on an
        already-protected size, so the record is left claiming an unprotected
        position that is in fact protected, and the one verdict that reads that
        state (`reconcile_active_trades_with_exchange`'s protection-restored
        test) can no longer tell the two apart.

        Both conditions below are the ones this file already treats as "ignore
        this observation" in `_apply_entry_order_state`; they are applied here so
        that every caller gets them rather than only that one. A record that is
        NOT fully protected is never refused, so a protection retry after
        PROTECTION_FAILED still runs exactly as before.
        """
        if record.state in (
            TradeLifecycleState.POSITION_CLOSED,
            TradeLifecycleState.ENTRY_CANCELLED,
            TradeLifecycleState.ENTRY_REJECTED,
        ):
            return f"the record is already terminal ({record.state.value})"

        fully_protected = (
            record.filled_quantity > Decimal("0")
            and record.protected_quantity >= record.filled_quantity
            and bool(record.sl_order_id)
            and bool(record.tp_order_id)
        )
        if fully_protected and filled_size <= record.filled_quantity:
            return (
                f"{filled_size} adds nothing to the {record.filled_quantity} already "
                f"filled and protected by SL {record.sl_order_id} / TP {record.tp_order_id}"
            )
        return None

    async def on_entry_partial_fill(self, setup_id: str, filled_size: Decimal, avg_price: Decimal) -> None:
        """Handle partial entry fill: scale protection to match exact filled quantity."""
        record = self._active_trades.get(setup_id)
        if not record:
            return

        ignore = self._fill_notification_to_ignore(record, filled_size)
        if ignore:
            logger.debug(
                "ENTRY_FILL_NOTIFICATION_IGNORED setup=%s handler=on_entry_partial_fill "
                "filled=%s state=%s: %s",
                setup_id, filled_size, record.state.value, ignore,
            )
            return

        # A fill observation may confirm or grow what is already known; it may
        # never shrink it (§M10 monotonicity, enforced here so direct callers get
        # it too). Protection is then built for what the record actually holds,
        # never for a smaller stale number.
        record.filled_quantity = max(record.filled_quantity, filled_size)
        record.average_fill_price = avg_price
        record.record_transition(
            TradeLifecycleState.ENTRY_PARTIALLY_FILLED,
            f"Partially filled {record.filled_quantity}/{record.requested_quantity} @ {avg_price}"
        )

        # Scale protective bracket orders to matched filled size
        await self._ensure_bracket_protection(record, record.filled_quantity)

    async def on_entry_fill(self, setup_id: str, filled_size: Decimal, avg_price: Decimal) -> None:
        """Handle complete entry fill: ensure 100% position bracket protection is active."""
        record = self._active_trades.get(setup_id)
        if not record:
            return

        ignore = self._fill_notification_to_ignore(record, filled_size)
        if ignore:
            logger.debug(
                "ENTRY_FILL_NOTIFICATION_IGNORED setup=%s handler=on_entry_fill "
                "filled=%s state=%s: %s",
                setup_id, filled_size, record.state.value, ignore,
            )
            return

        record.filled_quantity = max(record.filled_quantity, filled_size)
        record.average_fill_price = avg_price
        record.record_transition(
            TradeLifecycleState.ENTRY_FILLED,
            f"Entry completely filled {record.filled_quantity} @ {avg_price}"
        )

        await self._ensure_bracket_protection(record, record.filled_quantity)

    # ── Bracket Protection (SL / TP) ──────────────────────────────────────────

    async def _ensure_bracket_protection(self, record: TradeLifecycleRecord, target_protected_size: Decimal) -> None:
        """Construct and submit protective SL and TP bracket orders matching the exact filled position size."""
        if target_protected_size <= Decimal("0"):
            return

        # If already fully protected for this size, no action needed
        if record.protected_quantity == target_protected_size and record.sl_order_id and record.tp_order_id:
            return

        record.record_transition(
            TradeLifecycleState.PROTECTION_PENDING,
            f"Configuring bracket protection for size {target_protected_size}"
        )

        close_side = OrderSide.SELL if record.direction == TradeDirection.LONG else OrderSide.BUY
        # P2-1: the product the ENTRY was submitted against, not whatever the
        # registry resolves for the symbol now. A bracket placed on a different
        # product would leave the real position naked.
        product_spec = _authoritative_product_specification(record)

        # Task M §M2: a partial fill that grows (30 -> 70 -> 100) must end with
        # protection for exactly the filled quantity and NOT with one bracket
        # per partial. Delta cannot resize a resting order in place, so the
        # existing pair is cancelled before the correctly-sized pair is placed.
        # If a stale bracket cannot be cancelled we stop here rather than adding
        # a second protective pair: two live reduce-only stops on one position
        # is exactly the duplicate protection §M2 forbids, and leaving the
        # smaller (already live) bracket in place keeps the position protected
        # for at least part of its size while reconciliation resolves it.
        stale_bracket_cancelled = False
        if record.protected_quantity != target_protected_size and (record.sl_order_id or record.tp_order_id):
            try:
                await self._cancel_existing_brackets(record, product_spec.product_id)
                stale_bracket_cancelled = True
                # The cancelled pair's client_order_ids are now spent. Advance
                # the generation so the replacement legs are named
                # deterministically but distinctly (SL -> SL2 -> SL3), rather
                # than replaying a name the exchange has already seen -- whose
                # acceptance is UNVERIFIED and whose refusal would leave this
                # position unprotected.
                record.protection_generation += 1
            except Exception as e:
                logger.critical(
                    "CRITICAL: could not cancel stale bracket orders for setup %s before "
                    "resizing protection %s -> %s: %s",
                    record.setup_id, record.protected_quantity, target_protected_size, e,
                )
                record.record_transition(
                    TradeLifecycleState.PROTECTION_FAILED,
                    f"Stale bracket cancellation failed while resizing protection to {target_protected_size}: {e}",
                )
                self.state_store.record_audit(
                    action="PROTECTION_RESIZE_CANCEL_FAILED",
                    details={
                        "setup_id": record.setup_id, "symbol": record.symbol,
                        "protected_quantity": str(record.protected_quantity),
                        "target_size": str(target_protected_size), "error": str(e),
                    },
                )
                self._raise_reconciliation_alert(
                    "PROTECTION_RESIZE_CANCEL_FAILED", record.symbol,
                    f"setup {record.setup_id}: stale bracket could not be cancelled",
                )
                return

        # Task O §O13: the position may ALREADY be protected on the exchange.
        #
        # §G2 made the entry carry Delta's documented `bracket_stop_loss_price` /
        # `bracket_take_profit_price`, so the exchange builds the protective legs
        # itself from the entry. Placing the pair below as well would leave one
        # position covered by two reduce-only SL legs and two reduce-only TP
        # legs: the duplicate protection §M2 forbids, and a surplus leg can rest
        # on after the position has closed.
        #
        # This ADOPTS rather than suppresses. When the exchange's own legs are
        # found they become `sl_order_id` / `tp_order_id`, so every consumer of
        # protection state keeps working on real, re-verifiable exchange order
        # ids: the resize path above, `close_position`'s cancel loop, the kill
        # switch, and `reconcile_active_trades_with_exchange`'s membership test
        # for "is protection still live". Nothing is ever marked protected on the
        # strength of a field that was echoed once at entry time, so protection
        # that later disappears is still caught by reconciliation and rebuilt.
        #
        # Every failure to confirm returns False and falls through to the
        # placement below -- the pre-§G2 behaviour, unchanged (safety rule #15).
        if await self._adopt_exchange_attached_bracket(
            record, target_protected_size, product_spec.product_id,
            stale_bracket_cancelled=stale_bracket_cancelled,
        ):
            return

        try:
            # 1. Submit Stop Loss Order (stop-market with reduce_only)
            #
            # `order_type` alone does not make this a stop: Delta reads
            # `stop_order_type` for that, and `stop_trigger_method` for the
            # price series that arms it. Without those two fields the payload
            # reaches the exchange as a plain `market_order` carrying an
            # ignored `stop_price` -- an immediate market exit that would close
            # the position this call exists to protect. `DeltaOrderRequest`
            # now refuses that shape outright; the fields are supplied here.
            #
            # `last_traded_price` is the trigger series because Manual SMC's
            # stop level is derived from, and its backtest measured against,
            # traded prices from the 1H candle feed. Triggering against
            # `mark_price` would arm the stop off a different series than the
            # one the level was computed on, which is a change in exit
            # semantics rather than a formatting choice.
            sl_client_id = generate_deterministic_client_order_id(
                record.account_id, record.setup_id,
                protection_role(ORDER_ROLE_STOP_LOSS, record.protection_generation),
            )
            sl_req = DeltaOrderRequest(
                product_id=product_spec.product_id,
                product_symbol=product_spec.symbol,
                side=close_side,
                order_type=OrderType.STOP_MARKET_ORDER,
                size=target_protected_size,
                stop_price=record.stop_loss_price,
                stop_order_type=StopOrderType.STOP_LOSS_ORDER,
                stop_trigger_method=StopTriggerMethod.LAST_TRADED_PRICE,
                reduce_only=True,
                client_order_id=sl_client_id,
            )
            sl_resp = await self.client.place_order(sl_req)
            record.sl_order_id = str(sl_resp.id)
            record.sl_client_order_id = sl_client_id

            # 2. Submit Take Profit Order (Limit with reduce_only)
            tp_client_id = generate_deterministic_client_order_id(
                record.account_id, record.setup_id,
                protection_role(ORDER_ROLE_TAKE_PROFIT, record.protection_generation),
            )
            tp_req = DeltaOrderRequest(
                product_id=product_spec.product_id,
                product_symbol=product_spec.symbol,
                side=close_side,
                order_type=OrderType.LIMIT_ORDER,
                size=target_protected_size,
                limit_price=record.take_profit_price,
                reduce_only=True,
                client_order_id=tp_client_id,
            )
            tp_resp = await self.client.place_order(tp_req)
            record.tp_order_id = str(tp_resp.id)
            record.tp_client_order_id = tp_client_id

            if not record.sl_order_id or not record.tp_order_id:
                raise RuntimeError("Exchange failed to confirm SL/TP bracket order IDs")

            record.protected_quantity = target_protected_size
            record.protection_source = PROTECTION_SOURCE_ENGINE
            record.record_transition(
                TradeLifecycleState.PROTECTED_POSITION,
                f"Bracket protection active: SL={record.stop_loss_price} (ID {record.sl_order_id}), TP={record.take_profit_price} (ID {record.tp_order_id})"
            )

        except Exception as e:
            logger.critical("CRITICAL: Failed to submit bracket protection for setup %s: %s", record.setup_id, str(e))
            record.record_transition(
                TradeLifecycleState.PROTECTION_FAILED,
                f"Bracket protection submission failed: {str(e)}"
            )
            self.state_store.record_audit(
                action="PROTECTION_PLACEMENT_FAILED",
                details={"setup_id": record.setup_id, "error": str(e), "symbol": record.symbol, "target_size": str(target_protected_size)}
            )

    # ── Task O §O13: adopting an exchange-attached bracket ────────────────────

    @staticmethod
    def _is_adoptable_bracket_leg(
        order: DeltaOrderResponse,
        *,
        product_id: int,
        close_side: OrderSide,
        size: Decimal,
        stop_order_type: StopOrderType,
        stop_price: Decimal,
    ) -> bool:
        """Is this resting order the exchange's protective leg for that level?

        Pure, and deliberately unanimous: every clause must be positively stated
        by the exchange. `None` -- what `DeltaOrderResponse` uses for "the
        exchange did not say" -- satisfies no clause, so an order object that
        omits the stop descriptors is simply not adoptable and the caller places
        its own protection instead (safety rules #13, #15).

        `stop_trigger_method` is checked rather than assumed. This engine arms
        stops on `last_traded_price` because Manual SMC's stop level is derived
        from traded prices, while every bracket leg in this account's history was
        armed on `mark_price`, the exchange default. A leg on a different series
        is a different exit contract, so it is refused here instead of being
        adopted quietly -- the same refusal `_assert_stop_contract` makes when a
        standalone stop arrives without a trigger series.

        `bracket_order` is NOT required. A leg matching product, side, size,
        reduce-only, stop type, trigger series and stop price is protection at
        the authorised level whatever created it, and requiring a field whose
        presence on the live-orders endpoint this engine has not observed would
        turn a healthy adoption into a duplicated pair.
        """
        return (
            order.product_id == product_id
            and order.side == close_side
            and order.reduce_only is True
            and order.state in (OrderStatus.OPEN, OrderStatus.PENDING)
            and order.size == size
            and order.stop_order_type == stop_order_type.to_exchange()
            and order.stop_trigger_method == StopTriggerMethod.LAST_TRADED_PRICE.to_exchange()
            and order.stop_price is not None
            and order.stop_price == stop_price
        )

    def _record_duplicate_protection(
        self,
        record: TradeLifecycleRecord,
        target_protected_size: Decimal,
        reason: str,
    ) -> None:
        """State, once, that the exchange's own bracket could not be adopted.

        Only reached when the exchange has CONFIRMED it holds an attached bracket
        for this entry and the engine is nevertheless placing its own pair, so
        exchange-side legs the engine does not track may be resting. Both pairs
        are reduce-only, so neither can flip the position or close more than
        exists, but a surplus leg can rest on after the position closes and an
        operator has to clear it -- hence a blocking reconciliation alert rather
        than a log line (safety rules #11, #14).

        The reason string carries the specific finding, because the two worlds
        this can land in are materially different for whoever reads the alert:
        legs that exist but do not match, and legs that are not there at all.
        """
        logger.critical(
            "PROTECTION_DUPLICATED_ON_EXCHANGE setup=%s symbol=%s size=%s: %s",
            record.setup_id, record.symbol, target_protected_size, reason,
        )
        self.state_store.record_audit(
            action="PROTECTION_DUPLICATED_ON_EXCHANGE",
            details={
                "setup_id": record.setup_id,
                "symbol": record.symbol,
                "entry_order_id": str(record.entry_order_id),
                "target_size": str(target_protected_size),
                "authorised_stop_loss": str(record.stop_loss_price),
                "authorised_take_profit": str(record.take_profit_price),
                "reason": reason,
            },
        )
        self._raise_reconciliation_alert(
            "PROTECTION_DUPLICATED_ON_EXCHANGE", record.symbol,
            f"setup {record.setup_id}: {reason}; the engine places its own "
            f"reduce-only pair as well, so untracked exchange-side protective legs "
            f"may be resting on this position and must be cleared by an operator",
        )

    async def _adopt_exchange_attached_bracket(
        self,
        record: TradeLifecycleRecord,
        target_protected_size: Decimal,
        product_id: int,
        stale_bracket_cancelled: bool = False,
    ) -> bool:
        """Adopt the SL/TP legs Delta built from the entry's attached bracket.

        Returns True only when protection for exactly `target_protected_size` is
        confirmed on the exchange AND its two order ids are now on the record.
        Returns False in every other case, error paths included, so the caller
        places the engine's own pair: protection that exists twice is
        recoverable, protection that does not exist is not.

        Two independent confirmations are required and neither is inferred:

          1. the ENTRY order object echoes `bracket_stop_loss_price` and
             `bracket_take_profit_price` equal to this record's levels. That echo
             is the exchange stating it accepted an attached bracket for this
             order, and it is a verified field of Delta's order object.
             `bracket_stop_trigger_method` is not echoed, so it is not read here
             -- the legs state their own trigger series and step 2 checks it.
          2. exactly one resting reduce-only leg per side matches that level,
             size, product, stop type and trigger series.

        When (1) holds and (2) does not, protection is about to be duplicated
        knowingly, so `_record_duplicate_protection` states it and raises a
        blocking alert. When (1) does not hold there is no exchange-side bracket
        to duplicate and the caller's placement is the only protection -- the
        pre-§G2 path, which raises nothing.

        `stale_bracket_cancelled` is True when the caller has just cancelled the
        legs it was tracking in order to resize protection after a growing
        partial fill. Those legs may well be the exchange's own adopted ones, so
        their absence below is expected and is not evidence of duplication; the
        entry's echo persists for the life of the order and cannot distinguish
        the two cases. Adoption is still attempted -- if Delta rebuilt the legs
        at the new size they are the right ones to hold -- but a failure to find
        them then falls through quietly instead of raising a false alert.
        """
        if not record.entry_order_id:
            return False
        if record.stop_loss_price is None or record.take_profit_price is None:
            return False

        close_side = OrderSide.SELL if record.direction == TradeDirection.LONG else OrderSide.BUY
        gate_confirmed = False
        try:
            # 1. GATE -- did the exchange accept an attached bracket for THIS
            #    entry, at exactly the levels this record authorises? Only a
            #    Decimal counts: `None` is "the exchange did not state it", and
            #    anything else is an answer this engine cannot read, so both fall
            #    through to the caller's own placement rather than being
            #    interpreted (safety rule #15).
            entry = await self.client.get_order(int(record.entry_order_id))
            echoed_sl = getattr(entry, "bracket_stop_loss_price", None)
            echoed_tp = getattr(entry, "bracket_take_profit_price", None)
            if not isinstance(echoed_sl, Decimal) or not isinstance(echoed_tp, Decimal):
                logger.info(
                    "BRACKET_NOT_ATTACHED setup=%s entry=%s states no attached bracket; "
                    "the engine places its own protection",
                    record.setup_id, record.entry_order_id,
                )
                return False
            gate_confirmed = True
            if echoed_sl != record.stop_loss_price or echoed_tp != record.take_profit_price:
                # A live bracket at levels this record never authorised. It is
                # not adoptable, and it is not ignorable either.
                self._record_duplicate_protection(
                    record, target_protected_size,
                    f"the exchange holds an attached bracket at SL={echoed_sl} "
                    f"TP={echoed_tp}, which is not the authorised "
                    f"SL={record.stop_loss_price} TP={record.take_profit_price}",
                )
                return False

            # 2. Which resting orders ARE those legs? Bounded retry, because
            #    Delta builds the legs when the entry fills and this runs on the
            #    fill observation. The wait is safe only because the gate above
            #    already confirmed the exchange is holding protection, so the
            #    position is covered throughout it.
            attempts = max(1, int(BRACKET_ADOPTION_ATTEMPTS))
            delay = max(0.0, float(BRACKET_ADOPTION_DELAY_SECONDS))
            sl_legs: List[DeltaOrderResponse] = []
            tp_legs: List[DeltaOrderResponse] = []
            for attempt in range(attempts):
                if attempt:
                    await asyncio.sleep(delay)
                resting = await self.client.get_open_orders(product_id=product_id)
                candidates = list(resting) if isinstance(resting, (list, tuple)) else []
                sl_legs = [
                    o for o in candidates
                    if self._is_adoptable_bracket_leg(
                        o, product_id=product_id, close_side=close_side,
                        size=target_protected_size,
                        stop_order_type=StopOrderType.STOP_LOSS_ORDER,
                        stop_price=record.stop_loss_price,
                    )
                ]
                tp_legs = [
                    o for o in candidates
                    if self._is_adoptable_bracket_leg(
                        o, product_id=product_id, close_side=close_side,
                        size=target_protected_size,
                        stop_order_type=StopOrderType.TAKE_PROFIT_ORDER,
                        stop_price=record.take_profit_price,
                    )
                ]
                if len(sl_legs) == 1 and len(tp_legs) == 1:
                    break

            if len(sl_legs) != 1 or len(tp_legs) != 1:
                # Ambiguity is refused as hard as absence: adopting one of two
                # matching legs would leave the other resting and untracked.
                reason = (
                    f"the exchange confirmed an attached bracket at "
                    f"SL={record.stop_loss_price} TP={record.take_profit_price}, but "
                    f"{len(sl_legs)} adoptable stop-loss leg(s) and {len(tp_legs)} "
                    f"adoptable take-profit leg(s) for size {target_protected_size} "
                    f"were found after {attempts} attempt(s)"
                )
                if stale_bracket_cancelled:
                    logger.info(
                        "BRACKET_ADOPTION_SKIPPED_AFTER_RESIZE setup=%s: %s; this call "
                        "cancelled the previously adopted legs, so the engine places "
                        "the correctly sized pair itself",
                        record.setup_id, reason,
                    )
                else:
                    self._record_duplicate_protection(record, target_protected_size, reason)
                return False

            # 3. Adopt. From here the record holds two real exchange order ids,
            #    so `close_position`, the kill switch and reconciliation act on
            #    the exchange's own legs exactly as they would on engine-placed
            #    ones, and protection that later vanishes is still rebuilt.
            sl_leg, tp_leg = sl_legs[0], tp_legs[0]
            record.sl_order_id = str(sl_leg.id)
            record.sl_client_order_id = sl_leg.client_order_id
            record.tp_order_id = str(tp_leg.id)
            record.tp_client_order_id = tp_leg.client_order_id
            record.protected_quantity = target_protected_size
            record.protection_source = PROTECTION_SOURCE_EXCHANGE_ADOPTED
            record.record_transition(
                TradeLifecycleState.PROTECTED_POSITION,
                f"Exchange-attached bracket adopted for size {target_protected_size}: "
                f"SL={record.stop_loss_price} (ID {record.sl_order_id}), "
                f"TP={record.take_profit_price} (ID {record.tp_order_id}); no second "
                f"reduce-only pair was placed",
            )
            self.state_store.record_audit(
                action="PROTECTION_ADOPTED_FROM_EXCHANGE",
                details={
                    "setup_id": record.setup_id,
                    "symbol": record.symbol,
                    "entry_order_id": str(record.entry_order_id),
                    "protected_size": str(target_protected_size),
                    "sl_order_id": record.sl_order_id,
                    "tp_order_id": record.tp_order_id,
                    "sl_stop_price": str(sl_leg.stop_price),
                    "tp_stop_price": str(tp_leg.stop_price),
                    "trigger_method": StopTriggerMethod.LAST_TRADED_PRICE.to_exchange(),
                    "sl_flagged_bracket_leg": sl_leg.bracket_order,
                    "tp_flagged_bracket_leg": tp_leg.bracket_order,
                },
            )
            logger.info(
                "PROTECTION_ADOPTED_FROM_EXCHANGE setup=%s symbol=%s size=%s SL=%s(%s) TP=%s(%s)",
                record.setup_id, record.symbol, target_protected_size,
                record.stop_loss_price, record.sl_order_id,
                record.take_profit_price, record.tp_order_id,
            )
            return True

        except Exception as e:
            # Any failure to READ the exchange is a failure to confirm, so the
            # caller places protection. The alert is raised only when the bracket
            # was already confirmed, because that is the only case in which a
            # second pair is knowingly being added.
            if gate_confirmed:
                self._record_duplicate_protection(
                    record, target_protected_size,
                    f"the exchange confirmed an attached bracket but its legs could "
                    f"not be read ({e})",
                )
            else:
                logger.warning(
                    "BRACKET_ADOPTION_UNAVAILABLE setup=%s: %s", record.setup_id, e
                )
        return False

    async def _cancel_existing_brackets(self, record: TradeLifecycleRecord, product_id: int) -> None:
        """Cancel the currently resting SL/TP pair and forget their ids.

        Raises if the exchange refuses a cancellation for an order that is still
        resting, so the caller can fail closed. An order the exchange no longer
        has (already cancelled, already filled) is not an error for this step --
        it is simply gone -- but a FILLED bracket means the position is closing,
        which the caller must not paper over by placing new protection, so that
        case is surfaced as an exception too.
        """
        for label in ("sl", "tp"):
            order_id = getattr(record, f"{label}_order_id")
            if not order_id:
                continue
            try:
                await self.client.cancel_order(int(order_id), product_id)
            except Exception as cancel_error:
                # Ask the exchange what the order actually is before deciding.
                try:
                    confirmed = await self.client.get_order(int(order_id))
                except Exception:
                    raise cancel_error
                state = getattr(confirmed, "state", None)
                if state == OrderStatus.FILLED:
                    raise RuntimeError(
                        f"{label.upper()} order {order_id} is FILLED on the exchange; "
                        f"protection cannot be resized while the position is closing"
                    )
                if state not in (OrderStatus.CANCELLED, OrderStatus.EXPIRED):
                    raise cancel_error
            setattr(record, f"{label}_order_id", None)
            setattr(record, f"{label}_client_order_id", None)
        record.protected_quantity = Decimal("0")
        # P2-2: provenance describes a protection contract that exists. With both
        # legs gone there is no contract, and a stale "ENGINE_PLACED" would let a
        # restored record believe protection it no longer has.
        record.protection_source = None

    async def _classify_refused_cancel(
        self, order_id: Union[str, int]
    ) -> Tuple[str, Optional[str], Optional[str]]:
        """Ask the exchange what an order is, after its cancellation was refused.

        Returns `(outcome, exchange_state, verify_error)`: `outcome` is one of the
        four `CANCEL_OUTCOME_*` verdicts, `exchange_state` is the state the
        exchange actually reported (`None` when it could not be read), and
        `verify_error` is why the verification itself failed (`None` when it
        succeeded -- which is exactly what separates an *_UNCONFIRMED alert from
        an *_UNVERIFIED one).

        This asks the same question `_cancel_existing_brackets` asks at its own
        cancel site, and keys on the same two terminal states, so there is one
        notion of "the exchange no longer has this order" rather than two. Only
        the reaction a caller is permitted differs between the sites, so the
        verdict is returned rather than raised.

        Nothing is inferred. A failed lookup -- including the HTTP 400
        `order_not_found` an order-addressed endpoint can answer with -- is
        UNKNOWN, not GONE: "the exchange did not answer" and "the exchange has no
        such order" are different statements from "the order is not resting", and
        only the last of the three would justify proceeding (safety rule #15).
        """
        try:
            confirmed = await self.client.get_order(int(order_id))
        except Exception as verify_error:
            return CANCEL_OUTCOME_UNKNOWN, None, str(verify_error)

        state = getattr(confirmed, "state", None)
        state_name = state.value if isinstance(state, OrderStatus) else None

        # The terminal pair `_cancel_existing_brackets` accepts as "simply gone",
        # and the pair `_expire_entry_order` finalises an unfilled entry on.
        if state in (OrderStatus.CANCELLED, OrderStatus.EXPIRED):
            return CANCEL_OUTCOME_GONE, state_name, None
        if state == OrderStatus.FILLED:
            return CANCEL_OUTCOME_FILLED, state_name, None
        if state in (OrderStatus.OPEN, OrderStatus.PENDING,
                     OrderStatus.PARTIALLY_FILLED):
            return CANCEL_OUTCOME_LIVE, state_name, None

        # Anything else is left unclassified rather than assumed: REJECTED is not
        # in the terminal pair the bracket precedent accepts, and
        # `OrderStatus.from_exchange` already refuses state names this engine does
        # not model (§O5), so reaching here means the object was not what this
        # code expects. Unclassified fails closed.
        return CANCEL_OUTCOME_UNKNOWN, state_name, None

    def _raise_reconciliation_alert(
        self,
        code: str,
        symbol: str,
        details: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a critical condition that must block further entries (§M11/§M15).

        `context` carries machine-readable identifying data for conditions where
        prose is not enough to act on -- the affected setup id, the submission
        attempt's client order id (P1-5). It is nested under a single "context"
        key so it can never collide with the four fields every alert has, and the
        persisted alert list is free-form by design, so it survives a restart
        with no schema change and no second alert subsystem.
        """
        alert = {
            "code": code,
            "symbol": symbol,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if context:
            alert["context"] = dict(context)
        self._reconciliation_alerts.append(alert)
        logger.critical("RECONCILIATION_ALERT %s symbol=%s %s", code, symbol, details)
        self.state_store.record_audit(action=f"RECONCILIATION_ALERT_{code}", details=alert)

    @property
    def reconciliation_alerts(self) -> List[Dict[str, Any]]:
        return list(self._reconciliation_alerts)

    def clear_reconciliation_alerts(self, authorized_by: str) -> int:
        """Clear blocking reconciliation alerts (explicit operator action only)."""
        count = len(self._reconciliation_alerts)
        self._reconciliation_alerts.clear()
        self.state_store.record_audit(
            action="RECONCILIATION_ALERTS_CLEARED",
            details={"authorized_by": authorized_by, "cleared": count},
        )
        return count

    # ── Task M §M1/§M2/§M10: Private Event Stream Ingestion ───────────────────

    def bind_private_stream(self, ws_client: Any) -> None:
        """Observe the EXISTING private order/position stream.

        This is the only wiring step needed on the transport side: the client
        already parses, normalizes, de-duplicates and applies every private
        event, and only notifies observers for events it actually applied. So
        the duplicate / out-of-order guards inside the applier double as this
        manager's de-duplication guards, and no second event-state model is
        introduced (§M1, §M10).
        """
        ws_client.register_event_observer(self.observe_private_event)
        self._private_stream = ws_client
        logger.info(
            "PRIVATE_STREAM_BOUND private order/position/fill events -> TradeLifecycleManager"
        )

    async def observe_private_event(self, event: Any) -> None:
        """Route one already-applied private event into the existing handlers."""
        try:
            if isinstance(event, DeltaOrderEvent):
                await self.handle_order_event(event)
            elif isinstance(event, DeltaFillEvent):
                await self.handle_fill_event(event)
            elif isinstance(event, DeltaPositionEvent):
                await self.handle_position_event(event)
            elif isinstance(event, DeltaStreamIntegrityEvent):
                self.handle_stream_integrity_event(event)
        except Exception as e:
            # An observer must never take the transport down, but a lifecycle
            # handler that failed leaves execution state unknown, so it becomes
            # a blocking reconciliation condition rather than a warning (§M15).
            symbol = getattr(event, "symbol", "UNKNOWN")
            logger.critical(
                "CRITICAL: private event handling failed for %s (%s): %s",
                type(event).__name__, symbol, e,
            )
            self._raise_reconciliation_alert(
                "PRIVATE_EVENT_HANDLER_FAILED", symbol,
                f"{type(event).__name__} could not be applied to lifecycle state: {e}",
            )

    def handle_stream_integrity_event(self, event: DeltaStreamIntegrityEvent) -> bool:
        """Record a private-stream integrity failure (Task O §O5).

        The transport proved that the private stream can no longer be trusted --
        frames were lost, or a frame named an order state or an `action` this
        engine cannot interpret. This method is the ONLY thing §O5 adds on the
        consumer side, and it deliberately terminates in the EXISTING
        `_raise_reconciliation_alert`, whose alert list already blocks new entries
        at `execute_trade_setup`. No second state model, no new registry and no
        parallel lifecycle path: the signal arrives on the same observer channel
        the typed market events use, and it never touches position, order or
        trade state.

        The blocking decision is exactly the distinction §O5 requires:

        * `resynchronized=True`  -- a gap was detected AND the authoritative REST
          snapshot re-established trust. That is a real diagnostic and is audited,
          but it must NOT leave a standing block on new entries: the state the gap
          endangered has already been re-derived from the exchange.
        * `resynchronized=False` -- trust was NOT re-established. The fail-closed
          state is retained: an alert is raised and new entries stay blocked until
          an operator clears it explicitly. Trading on top of state that provably
          disagrees with the exchange is precisely what fail-closed forbids.

        Returns True when a blocking alert was raised.
        """
        details = {
            "code": event.code,
            "channel": event.channel,
            "reason": event.reason,
            "expected_seq_no": event.expected_seq_no,
            "received_seq_no": event.received_seq_no,
            "resynchronized": event.resynchronized,
        }

        if event.resynchronized:
            logger.warning(
                "PRIVATE_STREAM_INTEGRITY_RECOVERED code=%s channel=%s %s "
                "(REST resynchronization re-established trustworthy state)",
                event.code, event.channel, event.reason,
            )
            self.state_store.record_audit(
                action="PRIVATE_STREAM_INTEGRITY_RECOVERED", details=details,
            )
            return False

        logger.critical(
            "PRIVATE_STREAM_INTEGRITY_UNRESOLVED code=%s channel=%s %s",
            event.code, event.channel, event.reason,
        )
        self._raise_reconciliation_alert(
            event.code, event.symbol,
            f"private stream integrity could not be re-established: {event.reason}",
        )
        return True

    def _find_record_by_order(
        self,
        order_id: Optional[str],
        client_order_id: Optional[str] = None,
    ) -> Optional[tuple]:
        """Resolve an exchange order id to (record, role) for the trades we own.

        `role` is one of "entry", "sl", "tp". Returns None when the order
        belongs to no locally tracked trade -- that is an orphan condition for
        reconciliation (§M11 case A) and deliberately not something this method
        guesses about.
        """
        oid = str(order_id) if order_id is not None else None
        coid = str(client_order_id) if client_order_id else None
        for record in list(self._active_trades.values()):
            for role in ("entry", "sl", "tp"):
                known_id = getattr(record, f"{role}_order_id")
                known_coid = getattr(record, f"{role}_client_order_id")
                if oid and known_id and str(known_id) == oid:
                    return (record, role)
                if coid and known_coid and str(known_coid) == coid:
                    return (record, role)
        return None

    async def handle_order_event(self, event: DeltaOrderEvent) -> bool:
        """Apply an authoritative exchange order state change (§M2)."""
        located = self._find_record_by_order(event.order_id, event.client_order_id)
        if located is None:
            logger.warning(
                "ORPHAN_ORDER_EVENT order=%s client_order_id=%s symbol=%s status=%s "
                "matches no locally tracked trade",
                event.order_id, event.client_order_id, event.symbol, event.status.value,
            )
            self.state_store.record_audit(
                action="ORPHAN_ORDER_EVENT",
                details={
                    "order_id": event.order_id,
                    "client_order_id": event.client_order_id,
                    "symbol": event.symbol,
                    "status": event.status.value,
                    "filled_quantity": str(event.filled_quantity),
                },
            )
            return False

        record, role = located
        if role != "entry":
            # A protective order changing state is exit-side information; the
            # position-close path owns it, not the entry-fill path.
            logger.info(
                "BRACKET_ORDER_STATE setup=%s role=%s order=%s status=%s filled=%s",
                record.setup_id, role, event.order_id, event.status.value,
                event.filled_quantity,
            )
            return False

        # A resting entry order can only be bound to this trade once, so an
        # event that names it is authoritative for the entry order id.
        if not record.entry_order_id:
            record.entry_order_id = str(event.order_id)

        return await self._apply_entry_order_state(
            record,
            status=event.status,
            filled_quantity=event.filled_quantity,
            average_fill_price=event.average_fill_price,
            source="private_ws_order_event",
        )

    def _accumulate_observed_fee(self, setup_id: str, fee: Optional[Decimal]) -> None:
        """Fold one execution's commission into the trade's fee total (§O2).

        Absence is contagious and permanent. If any leg of a trade executed
        without a reported commission, the trade's total commission is not
        knowable from this stream, and a later leg that *does* report one cannot
        repair that -- summing only the priced legs would understate the cost
        while looking like a complete figure. So the total becomes `None` and
        stays `None`, and the closure path reports it as unobserved.

        Nothing is inferred from `role`, from the pinned maker/taker rates, or
        from size x price. A reported commission is added exactly as reported,
        sign included: negative means the maker rebate reduced the cost.
        """
        if setup_id in self._observed_fill_fees and self._observed_fill_fees[setup_id] is None:
            return
        if fee is None:
            self._observed_fill_fees[setup_id] = None
            logger.warning(
                "FILL_COMMISSION_UNOBSERVED setup=%s: an execution carried no "
                "`commission`; the trade's fee total is now unobservable",
                setup_id,
            )
            return
        prior = self._observed_fill_fees.get(setup_id)
        self._observed_fill_fees[setup_id] = (prior if prior is not None else Decimal("0")) + fee

    async def handle_fill_event(self, event: DeltaFillEvent) -> bool:
        """Record an authoritative execution: real commission, real size, real price.

        Commissions observed here are the ONLY source of `trading_fees` at
        closure (§M5): nothing is derived, estimated, or back-computed from a
        rate. A fill whose `commission` the exchange did not report makes the
        trade's fee total unobservable rather than zero (§O2).
        """
        located = self._find_record_by_order(event.order_id)
        if located is None:
            logger.warning(
                "ORPHAN_FILL_EVENT trade=%s order=%s symbol=%s size=%s matches no "
                "locally tracked trade",
                event.trade_id, event.order_id, event.symbol, event.size,
            )
            return False

        record, role = located
        if event.trade_id in self._processed_fill_trade_ids:
            logger.debug(
                "DUPLICATE_FILL_IGNORED trade=%s setup=%s role=%s",
                event.trade_id, record.setup_id, role,
            )
            return False
        self._processed_fill_trade_ids.add(event.trade_id)

        setup_id = record.setup_id
        self._accumulate_observed_fee(setup_id, event.fee)

        if role == "entry":
            cumulative = self._entry_fill_sizes.get(setup_id, Decimal("0")) + event.size
            self._entry_fill_sizes[setup_id] = cumulative
            logger.info(
                "ENTRY_EXECUTION setup=%s trade=%s size=%s price=%s fee=%s cumulative=%s/%s",
                setup_id, event.trade_id, event.size, event.price, event.fee,
                cumulative, record.requested_quantity,
            )
            return await self._apply_entry_order_state(
                record,
                status=None,
                filled_quantity=cumulative,
                average_fill_price=None,
                source="private_ws_user_trade",
            )

        # Exit-side execution: accumulate the authoritative size and notional so
        # the closure path never has to invent an exit price.
        self._exit_fill_sizes[setup_id] = (
            self._exit_fill_sizes.get(setup_id, Decimal("0")) + event.size
        )
        self._exit_fill_notional[setup_id] = (
            self._exit_fill_notional.get(setup_id, Decimal("0")) + (event.size * event.price)
        )
        self._exit_fill_roles.setdefault(setup_id, set()).add(role)
        logger.info(
            "EXIT_EXECUTION setup=%s role=%s trade=%s size=%s price=%s fee=%s",
            setup_id, role, event.trade_id, event.size, event.price, event.fee,
        )
        return True

    async def handle_position_event(self, event: DeltaPositionEvent) -> bool:
        """Observe an authoritative position snapshot for a symbol we trade."""
        matches = [r for r in self._active_trades.values() if r.symbol == event.symbol]
        if not matches:
            # §O5: `is_closure` is the single closure definition (an `action:
            # "delete"` frame OR a reported size of zero). This used to test
            # `event.size != Decimal("0")` directly, so a deletion carrying the
            # last known non-zero size raised a false `ORPHAN_EXCHANGE_POSITION`
            # alert -- and that alert blocks every new entry -- against a
            # position the exchange had already closed.
            if not event.is_closure:
                logger.warning(
                    "ORPHAN_POSITION_EVENT symbol=%s size=%s has no locally tracked trade",
                    event.symbol, event.size,
                )
                self._raise_reconciliation_alert(
                    "ORPHAN_EXCHANGE_POSITION", event.symbol,
                    f"exchange reports an open position of {event.size} with no local trade",
                )
            return False

        record = matches[0]
        if event.is_closure:
            # The exchange says the position is gone -- either explicitly
            # (`action: "delete"`) or by reporting a flat size. Closure runs from
            # authoritative exchange values only (§M5).
            logger.info(
                "EXCHANGE_POSITION_FLAT setup=%s symbol=%s realized_pnl=%s action=%s",
                record.setup_id, event.symbol, event.realized_pnl, event.action,
            )
            self.state_store.record_audit(
                action="EXCHANGE_POSITION_FLAT",
                details={
                    "setup_id": record.setup_id,
                    "symbol": event.symbol,
                    # §O5: how the exchange stated the closure, and the size it
                    # last reported -- preserved as reported, never rewritten to
                    # zero to make the closure look flat.
                    "stream_action": event.action,
                    "reported_size": str(event.size),
                    # §O3: null when the stream did not carry a realized PnL.
                    "realized_pnl": (
                        str(event.realized_pnl) if event.realized_pnl is not None else None
                    ),
                    "realized_pnl_source": (
                        "EXCHANGE_REALIZED_PNL" if event.realized_pnl is not None
                        else "UNOBSERVED"
                    ),
                    "local_state": record.state.value,
                },
            )
            if record.filled_quantity > Decimal("0"):
                await self.handle_exchange_closure(
                    record.setup_id,
                    exchange_realized_pnl=event.realized_pnl,
                    source="private_ws_position_event",
                )
            return True

        logger.debug(
            "EXCHANGE_POSITION setup=%s symbol=%s size=%s entry=%s mark=%s",
            record.setup_id, event.symbol, event.size, event.entry_price, event.mark_price,
        )
        return True

    async def _apply_entry_order_state(
        self,
        record: TradeLifecycleRecord,
        status: Optional[OrderStatus],
        filled_quantity: Decimal,
        average_fill_price: Optional[Decimal],
        source: str,
    ) -> bool:
        """The single convergence point for every entry-order observation.

        WebSocket order events, `user_trades` executions and REST snapshots all
        arrive here, so there is exactly one place that decides whether a fill
        is new, and exactly one place that calls the existing fill handlers.
        The filled quantity is monotonic: a replayed frame or a stale REST
        snapshot can never shrink a fill that was already observed, which is
        what stops `WS PARTIAL + REST FULL` (or the reverse) from double
        counting or regressing (§M10).
        """
        if record.state in (
            TradeLifecycleState.POSITION_CLOSED,
            TradeLifecycleState.ENTRY_CANCELLED,
            TradeLifecycleState.ENTRY_REJECTED,
        ):
            logger.debug(
                "ENTRY_STATE_IGNORED setup=%s already terminal (%s) source=%s",
                record.setup_id, record.state.value, source,
            )
            return False

        prior = record.filled_quantity
        observed_executions = self._entry_fill_sizes.get(record.setup_id, Decimal("0"))
        effective = max(filled_quantity, prior, observed_executions)
        if filled_quantity < prior:
            logger.warning(
                "ENTRY_FILL_REGRESSION_IGNORED setup=%s reported=%s known=%s source=%s",
                record.setup_id, filled_quantity, prior, source,
            )

        if effective <= Decimal("0"):
            if status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED):
                return self._finalize_unfilled_entry(record, status, source)
            return False

        if effective > record.requested_quantity:
            # Protect what the exchange says exists, and surface the divergence.
            self._raise_reconciliation_alert(
                "ENTRY_OVERFILL", record.symbol,
                f"setup {record.setup_id}: exchange filled {effective} against a "
                f"requested {record.requested_quantity} (source={source})",
            )

        fully_filled = status == OrderStatus.FILLED or effective >= record.requested_quantity
        already_protected = (
            effective == prior
            and record.protected_quantity == effective
            and bool(record.sl_order_id)
            and bool(record.tp_order_id)
        )
        if already_protected:
            logger.debug(
                "ENTRY_FILL_DUPLICATE_IGNORED setup=%s filled=%s already protected source=%s",
                record.setup_id, effective, source,
            )
            return False

        effective_avg = average_fill_price or record.average_fill_price
        logger.info(
            "ENTRY_FILL_OBSERVED setup=%s symbol=%s filled=%s/%s full=%s source=%s",
            record.setup_id, record.symbol, effective, record.requested_quantity,
            fully_filled, source,
        )

        if fully_filled:
            await self.on_entry_fill(record.setup_id, effective, effective_avg)
        else:
            await self.on_entry_partial_fill(record.setup_id, effective, effective_avg)

        if record.state == TradeLifecycleState.PROTECTION_FAILED:
            self._raise_reconciliation_alert(
                "POSITION_UNPROTECTED", record.symbol,
                f"setup {record.setup_id}: filled {effective} but bracket protection "
                f"could not be established",
            )
        return True

    def _finalize_unfilled_entry(
        self,
        record: TradeLifecycleRecord,
        status: OrderStatus,
        source: str,
        terminal_state: Optional[TradeLifecycleState] = None,
    ) -> bool:
        """Close out an entry the exchange terminated with zero fill.

        Nothing can exist on the exchange for this setup once the exchange
        itself reports the entry CANCELLED / EXPIRED / REJECTED with no fill, so
        the portfolio slot is released here (see `_release_setup_lock`).
        """
        target = terminal_state or (
            TradeLifecycleState.ENTRY_REJECTED
            if status == OrderStatus.REJECTED
            else TradeLifecycleState.ENTRY_CANCELLED
        )
        record.record_transition(
            target,
            f"Exchange reports entry order {record.entry_order_id} {status.value} "
            f"with zero fill (source={source})",
        )
        self.state_store.record_audit(
            action="ENTRY_TERMINATED_UNFILLED",
            details={
                "setup_id": record.setup_id,
                "symbol": record.symbol,
                "entry_order_id": record.entry_order_id,
                "exchange_status": status.value,
                "source": source,
            },
        )
        logger.info(
            "ENTRY_TERMINATED_UNFILLED setup=%s status=%s source=%s",
            record.setup_id, status.value, source,
        )
        self._archive_and_release(record)
        return True

    def _archive_and_release(self, record: TradeLifecycleRecord) -> None:
        """Move a finished record to history and release its portfolio slot."""
        self._active_trades.pop(record.setup_id, None)
        if record not in self._trade_history:
            self._trade_history.append(record)
        self._release_setup_lock(record.user_id, record.account_id, record.setup_id)

    # ── Task M §M1B: REST-authoritative fallback ──────────────────────────────

    async def _resolve_entry_order(
        self,
        record: TradeLifecycleRecord,
    ) -> Optional[DeltaOrderResponse]:
        """Fetch the authoritative exchange record for this trade's entry order.

        Reuses the existing client methods only: `get_order` when the exchange
        order id is known, `get_order_by_client_id` when submission was
        ambiguous and only the client order id exists (§M4 case C). Returns None
        when the exchange cannot tell us, which callers must treat as ambiguity.
        """
        if record.entry_order_id:
            return await self.client.get_order(int(record.entry_order_id))
        if record.entry_client_order_id:
            found = await self.client.get_order_by_client_id(record.entry_client_order_id)
            if found is not None:
                record.entry_order_id = str(found.id)
            return found
        return None

    async def refresh_entry_from_exchange(
        self,
        setup_id: str,
        alert_on_failure: bool = False,
    ) -> Optional[OrderStatus]:
        """Converge this trade's entry state onto the REST snapshot (§M1B).

        This is the recovery path for private events that were missed, delayed,
        or never delivered: a resting entry can go OPEN -> PARTIAL -> MORE
        PARTIAL -> FILLED purely through repeated calls here, because every
        observation lands on the same convergence point that the WebSocket uses.
        The REST snapshot is authoritative over anything the stream said, bounded
        only by the monotonic fill rule (a snapshot cannot un-fill a fill that
        was already observed and protected).

        Returns the authoritative status, or None when the exchange could not be
        asked -- never a guess.
        """
        record = self._active_trades.get(setup_id)
        if not record:
            return None

        try:
            order = await self._resolve_entry_order(record)
        except Exception as e:
            logger.error(
                "ENTRY_REST_REFRESH_FAILED setup=%s order=%s: %s",
                setup_id, record.entry_order_id, e,
            )
            if alert_on_failure:
                self._raise_reconciliation_alert(
                    "ENTRY_STATE_UNKNOWN", record.symbol,
                    f"setup {setup_id}: exchange could not confirm entry order "
                    f"{record.entry_order_id} ({e})",
                )
            return None

        if order is None:
            logger.error(
                "ENTRY_REST_REFRESH_EMPTY setup=%s: exchange returned no order for "
                "entry_order_id=%s client_order_id=%s",
                setup_id, record.entry_order_id, record.entry_client_order_id,
            )
            if alert_on_failure:
                self._raise_reconciliation_alert(
                    "ENTRY_STATE_UNKNOWN", record.symbol,
                    f"setup {setup_id}: exchange returned no record for the entry order",
                )
            return None

        logger.info(
            "ENTRY_REST_SNAPSHOT setup=%s order=%s status=%s filled=%s/%s avg=%s",
            setup_id, order.id, order.state.value, order.filled_size, order.size,
            order.average_fill_price,
        )
        await self._apply_entry_order_state(
            record,
            status=order.state,
            filled_quantity=order.filled_size,
            average_fill_price=order.average_fill_price,
            source="rest_order_snapshot",
        )
        return order.state

    # ── Task M §M3: Entry-window expiry cancels the REAL exchange order ───────

    async def expire_resting_entry(
        self,
        setup_id: str,
        reason: str = "ENTRY_WINDOW_EXPIRED",
    ) -> Dict[str, Any]:
        """Cancel the real exchange order behind an expired entry window.

        The strategy's 3-candle entry window is computed and detected exactly as
        before; this is only the execution consequence of it. The exchange -- not
        the expiry -- decides the outcome:

        * FILLED             -> the fill won the race. Reconcile it and protect
                                the position. It is NOT cancelled.
        * partially filled   -> protect exactly the filled quantity; the
                                remainder is cancelled.
        * CANCELLED/EXPIRED  -> confirmed dead, release the portfolio slot.
        * still OPEN, or the exchange cannot be asked -> ambiguous, so fail
          closed: RECONCILIATION_REQUIRED, portfolio lock retained, no new
          entry, and a blocking alert, because a stale resting order could still
          create an untracked position.
        """
        record = self._active_trades.get(setup_id)
        if not record:
            return {"setup_id": setup_id, "outcome": "NO_ACTIVE_TRADE", "cancelled": False}

        if not record.entry_order_id and not record.entry_client_order_id:
            return {"setup_id": setup_id, "outcome": "NO_EXCHANGE_ORDER", "cancelled": False}

        if record.state not in (
            TradeLifecycleState.ENTRY_PENDING,
            TradeLifecycleState.ENTRY_SUBMITTED,
            TradeLifecycleState.ENTRY_PARTIALLY_FILLED,
        ):
            # Nothing is resting: the entry already reached a terminal state or
            # the position is filled and protected. Cancelling here would be an
            # action taken on a stale assumption.
            return {
                "setup_id": setup_id, "outcome": "NOT_RESTING",
                "state": record.state.value, "cancelled": False,
            }

        # P2-1: cancel the resting entry on the product it was submitted against.
        product_spec = _authoritative_product_specification(record)
        logger.info(
            "ENTRY_EXPIRY_CANCEL_REQUESTED setup=%s symbol=%s order=%s reason=%s",
            setup_id, record.symbol, record.entry_order_id, reason,
        )
        cancel_error: Optional[str] = None
        if record.entry_order_id:
            try:
                await self.client.cancel_order(int(record.entry_order_id), product_spec.product_id)
            except Exception as e:
                # A rejected cancel is NOT a failure yet: the usual cause is that
                # the order already reached a terminal state. Only the exchange's
                # own order state may decide, so the verification below runs
                # either way.
                cancel_error = str(e)
                logger.warning(
                    "ENTRY_EXPIRY_CANCEL_REJECTED setup=%s order=%s: %s",
                    setup_id, record.entry_order_id, cancel_error,
                )

        try:
            order = await self._resolve_entry_order(record)
        except Exception as e:
            order = None
            verify_error: Optional[str] = str(e)
        else:
            verify_error = None

        if order is None:
            logger.critical(
                "ENTRY_EXPIRY_UNVERIFIED setup=%s order=%s cancel_error=%s verify_error=%s",
                setup_id, record.entry_order_id, cancel_error, verify_error,
            )
            record.record_transition(
                TradeLifecycleState.RECONCILIATION_REQUIRED,
                f"Entry expiry cancellation could not be verified against the exchange "
                f"(cancel_error={cancel_error}, verify_error={verify_error}); portfolio "
                f"lock retained and no new entry permitted",
            )
            self.state_store.record_audit(
                action="ENTRY_EXPIRY_UNVERIFIED",
                details={
                    "setup_id": setup_id, "symbol": record.symbol,
                    "entry_order_id": record.entry_order_id,
                    "cancel_error": cancel_error, "verify_error": verify_error,
                    "reason": reason,
                },
            )
            self._raise_reconciliation_alert(
                "ENTRY_EXPIRY_UNVERIFIED", record.symbol,
                f"setup {setup_id}: entry order {record.entry_order_id} may still be live",
            )
            return {
                "setup_id": setup_id, "outcome": "RECONCILIATION_REQUIRED",
                "cancelled": False, "lock_retained": True,
            }

        status = order.state
        filled = order.filled_size
        logger.info(
            "ENTRY_EXPIRY_EXCHANGE_STATE setup=%s order=%s status=%s filled=%s/%s",
            setup_id, order.id, status.value, filled, order.size,
        )

        # The fill/expiry race is resolved by the exchange, not by the expiry.
        if filled > Decimal("0"):
            await self._apply_entry_order_state(
                record,
                status=status,
                filled_quantity=filled,
                average_fill_price=order.average_fill_price,
                source="entry_expiry_verification",
            )
            outcome = "FILLED_NOT_CANCELLED" if status == OrderStatus.FILLED else "PARTIALLY_FILLED"
            self.state_store.record_audit(
                action="ENTRY_EXPIRY_LOST_RACE_TO_FILL",
                details={
                    "setup_id": setup_id, "symbol": record.symbol,
                    "entry_order_id": str(order.id), "exchange_status": status.value,
                    "filled": str(filled), "requested": str(record.requested_quantity),
                    "reason": reason,
                },
            )
            return {
                "setup_id": setup_id, "outcome": outcome, "cancelled": False,
                "filled_quantity": str(filled), "lock_retained": True,
            }

        if status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED):
            self._finalize_unfilled_entry(
                record, status, source="entry_expiry_verification",
                terminal_state=TradeLifecycleState.ENTRY_TIMEOUT,
            )
            logger.info(
                "ENTRY_EXPIRY_CANCEL_CONFIRMED setup=%s order=%s status=%s",
                setup_id, order.id, status.value,
            )
            return {
                "setup_id": setup_id, "outcome": "CANCELLED", "cancelled": True,
                "lock_retained": False,
            }

        # Zero fill but the exchange still reports the order live: the cancel did
        # not take effect. Nothing may assume it is gone (safety rule #11/#14).
        logger.critical(
            "ENTRY_EXPIRY_CANCEL_UNCONFIRMED setup=%s order=%s still reports %s",
            setup_id, order.id, status.value,
        )
        record.record_transition(
            TradeLifecycleState.RECONCILIATION_REQUIRED,
            f"Entry expiry cancel did not take effect: exchange still reports order "
            f"{order.id} as {status.value}; portfolio lock retained",
        )
        self.state_store.record_audit(
            action="ENTRY_EXPIRY_CANCEL_UNCONFIRMED",
            details={
                "setup_id": setup_id, "symbol": record.symbol,
                "entry_order_id": str(order.id), "exchange_status": status.value,
                "cancel_error": cancel_error, "reason": reason,
            },
        )
        self._raise_reconciliation_alert(
            "ENTRY_EXPIRY_CANCEL_UNCONFIRMED", record.symbol,
            f"setup {setup_id}: entry order {order.id} is still {status.value} after cancel",
        )
        return {
            "setup_id": setup_id, "outcome": "RECONCILIATION_REQUIRED",
            "cancelled": False, "lock_retained": True,
        }

    # ── Task M §M5: Exchange-side closure binding ─────────────────────────────

    def register_closure_handler(self, handler: Callable[..., Any]) -> None:
        """Bind the existing closure/rescan flow (orchestrator-owned).

        The orchestrator registers `handle_trade_closure_and_rescan` here, so an
        exchange-observed closure runs the existing flow instead of a second
        closure implementation. When nothing is registered this manager falls
        back to `close_position` directly, which is the same call the
        orchestrator would make.
        """
        self._closure_handler = handler

    def _infer_close_reason(self, setup_id: str) -> CloseReason:
        """Derive the close reason from which protective order actually executed.

        This reads observed exchange executions only. It never infers the reason
        from strategy OHLC: a strategy may believe the stop was hit while the
        exchange filled the take profit, and only the exchange is authoritative.
        """
        roles = self._exit_fill_roles.get(setup_id, set())
        if "sl" in roles and "tp" not in roles:
            return CloseReason.STOP_LOSS
        if "tp" in roles and "sl" not in roles:
            return CloseReason.TAKE_PROFIT
        return CloseReason.UNKNOWN_RECONCILIATION

    async def _authoritative_exchange_balance(self, symbol: str) -> Optional[Decimal]:
        """Read the settlement-currency balance straight from the exchange."""
        try:
            balances = await self.client.get_wallet_balances()
        except Exception as e:
            logger.error("CLOSURE_BALANCE_UNAVAILABLE symbol=%s: %s", symbol, e)
            return None
        for bal in balances:
            if getattr(bal, "asset_symbol", None) in ("USDT", "USD"):
                return bal.balance
        logger.error("CLOSURE_BALANCE_UNAVAILABLE symbol=%s: no USDT wallet returned", symbol)
        return None

    async def handle_exchange_closure(
        self,
        setup_id: str,
        exchange_realized_pnl: Optional[Decimal] = None,
        reason: Optional[CloseReason] = None,
        source: str = "exchange_observation",
    ) -> Optional[TradeLifecycleRecord]:
        """Close a local trade because the EXCHANGE closed the position (§M5).

        Every realized number comes from exchange data:

        * `gross_pnl`        - the exchange's own realized PnL for the position.
        * `trading_fees`     - the sum of `commission` on the observed
                               `user_trades` executions for this trade, and
                               nothing else; `None` when any leg went unpriced.
        * `final_exchange_balance` - the live wallet balance read back from the
                               exchange after the close.

        Nothing is derived from strategy OHLC, and nothing is estimated from a
        fee rate. Where a value is genuinely unobservable, the closure still
        proceeds (the position really is gone, so holding the slot forever would
        be wrong) but the gap is recorded as a blocking reconciliation alert so
        no further entry is taken on unverified accounting.
        """
        record = self._active_trades.get(setup_id)
        if not record:
            return None
        if record.state == TradeLifecycleState.POSITION_CLOSED:
            return None

        if exchange_realized_pnl is None:
            logger.critical(
                "CLOSURE_PNL_UNOBSERVED setup=%s source=%s: exchange realized PnL unavailable",
                setup_id, source,
            )
            record.record_transition(
                TradeLifecycleState.RECONCILIATION_REQUIRED,
                f"Exchange closed the position but no authoritative realized PnL was "
                f"available (source={source}); refusing to fabricate a result",
            )
            self._raise_reconciliation_alert(
                "CLOSURE_PNL_UNOBSERVED", record.symbol,
                f"setup {setup_id}: position closed on the exchange with no authoritative "
                f"realized PnL; local closure deferred",
            )
            return None

        observed_fees = self._observed_fill_fees.get(setup_id)
        if observed_fees is None:
            # §O2: no commission was observed -- either no execution reached this
            # manager, or one that did carried no `commission`. Either way the
            # fee total is unknown, and it is reported as unknown rather than as
            # a free trade. `fees` stays None all the way into the record.
            self._raise_reconciliation_alert(
                "CLOSURE_FEES_UNOBSERVED", record.symbol,
                f"setup {setup_id}: no execution commission was observed on the private "
                f"trade stream, so the trade's fee total is unobserved and net PnL "
                f"excludes it",
            )
            fees: Optional[Decimal] = None
            fees_source = "UNOBSERVED"
        else:
            fees = observed_fees
            fees_source = "PRIVATE_USER_TRADES"

        final_balance = await self._authoritative_exchange_balance(record.symbol)
        if final_balance is None:
            self._raise_reconciliation_alert(
                "CLOSURE_BALANCE_UNOBSERVED", record.symbol,
                f"setup {setup_id}: wallet balance could not be read from the exchange "
                f"after closure; post-trade balance is locally derived",
            )

        effective_reason = reason or self._infer_close_reason(setup_id)
        self.state_store.record_audit(
            action="EXCHANGE_CLOSURE_OBSERVED",
            details={
                "setup_id": setup_id,
                "symbol": record.symbol,
                "reason": effective_reason.value,
                "gross_pnl": str(exchange_realized_pnl),
                # §O3: provable rather than asserted -- the `is None` guard above
                # returns before this point, so reaching here means the exchange
                # actually reported a realized PnL for the closed position.
                "gross_pnl_source": "EXCHANGE_REALIZED_PNL",
                # §O2: null, not "0", when no commission was observed. A reader
                # of this audit trail can tell a free trade from an unpriced one.
                "trading_fees": str(fees) if fees is not None else None,
                "trading_fees_source": fees_source,
                # Funding is settled by the exchange on a separate schedule and
                # has no verified per-trade source in this client, so it is
                # reported as unobserved rather than guessed.
                "funding_costs_source": "UNOBSERVED",
                "final_exchange_balance": str(final_balance) if final_balance is not None else None,
                "exit_fill_size": str(self._exit_fill_sizes.get(setup_id, Decimal("0"))),
                "source": source,
            },
        )
        logger.info(
            "EXCHANGE_CLOSURE setup=%s symbol=%s reason=%s gross=%s fees=%s(%s) balance=%s source=%s",
            setup_id, record.symbol, effective_reason.value, exchange_realized_pnl,
            fees, fees_source, final_balance, source,
        )

        # §O3: the gross figure IS the exchange's own realized PnL on this path,
        # so the record says so; §O2: the fee provenance travels with the value
        # rather than being re-derived by whoever finalizes the record.
        record.gross_pnl_source = "EXCHANGE_REALIZED_PNL"
        record.trading_fees_source = fees_source

        if self._closure_handler is not None:
            result = self._closure_handler(
                setup_id=setup_id,
                reason=effective_reason,
                gross_pnl=exchange_realized_pnl,
                trading_fees=fees,
                funding_costs=Decimal("0"),
                final_exchange_balance=final_balance,
            )
            if inspect.isawaitable(result):
                result = await result
            return result

        return await self.close_position(
            setup_id=setup_id,
            reason=effective_reason,
            gross_pnl=exchange_realized_pnl,
            trading_fees=fees,
            funding_costs=Decimal("0"),
            final_exchange_balance=final_balance,
            trading_fees_source=fees_source,
        )

    # ── Task M §M4/§M6/§M11: Exchange reconciliation of active trades ─────────

    async def reconcile_active_trades_with_exchange(
        self,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Converge local execution state onto the authoritative exchange state.

        Direction of authority is one-way: EXCHANGE STATE -> local convergence.
        This is the safety net behind the private stream, and the recovery path
        after a restart, so it is written to be callable at startup, on every
        (re)connect, periodically, and after any ambiguous submission.

        Findings that cannot be resolved from authoritative data become blocking
        reconciliation alerts rather than assumptions (§M11, §M15). A run that
        reaches the exchange and finds nothing unresolved clears prior alerts,
        which is the only self-healing route: positive exchange confirmation.
        """
        summary: Dict[str, Any] = {
            "checked": 0,
            "converged": [],
            "protection_restored": [],
            "orphan_positions": [],
            "orphan_orders": [],
            "unresolved": [],
            "exchange_unreachable": False,
        }

        # P1-4: whether an entry submission was already in flight when the
        # authoritative listings below were read. An admitted submission holds no
        # lock while it awaits the exchange, and its record does not exist yet, so
        # a reconciliation run overlapping it can see NEITHER the local record nor
        # the order the exchange may already be holding -- and would then compute a
        # clean world and clear the alerts protecting the account. Read before the
        # listings on purpose: it is the listings' completeness that is in doubt.
        with self._lock:
            submission_in_flight = self._submissions_in_flight > 0

        try:
            exchange_positions = await self.client.get_positions()
            exchange_orders = await self.client.get_open_orders()
        except Exception as e:
            logger.critical("RECONCILIATION_EXCHANGE_UNREACHABLE: %s", e)
            summary["exchange_unreachable"] = True
            summary["unresolved"].append("EXCHANGE_UNREACHABLE")
            self._raise_reconciliation_alert(
                "EXCHANGE_UNREACHABLE", account_id or self.state_store.account_id,
                f"exchange state could not be read during reconciliation: {e}",
            )
            return summary

        logger.info(
            "RECONCILIATION_STARTED account=%s exchange_positions=%s exchange_open_orders=%s "
            "local_active=%s",
            account_id or self.state_store.account_id, len(exchange_positions),
            len(exchange_orders), len(self._active_trades),
        )

        open_order_ids = {str(o.id) for o in exchange_orders}
        positions_by_product: Dict[int, Any] = {
            p.product_id: p for p in exchange_positions if p.size != Decimal("0")
        }
        claimed_products: Set[int] = set()
        claimed_order_ids: Set[str] = set()
        #: product id -> setup ids of active records that do NOT account for an
        #: open position on that product, because nothing of theirs has filled.
        #: This is emphatically NOT a claim. It exists so a position found on
        #: such a product can be reported against the setup that at least names
        #: the product, rather than as a wholly anonymous orphan.
        unfilled_products: Dict[int, List[str]] = {}
        #: product id -> setup ids whose recorded DIRECTION contradicts the open
        #: position on that product (P1-1). Also not a claim: a local LONG cannot
        #: own an exchange SHORT, whatever the sizes are.
        contradicted_products: Dict[int, List[str]] = {}

        for record in list(self._active_trades.values()):
            summary["checked"] += 1
            try:
                spec = get_product_specification(record.symbol)
            except Exception as e:
                # An unresolvable symbol must never be papered over (rule #15).
                summary["unresolved"].append(record.setup_id)
                self._raise_reconciliation_alert(
                    "UNKNOWN_SYMBOL", record.symbol,
                    f"setup {record.setup_id}: product specification unavailable ({e})",
                )
                continue

            # P2-1: the product this record's orders were actually submitted
            # against, versus what the registry resolves for its symbol NOW. A
            # disagreement means the persisted execution identity and the current
            # registry describe two different exchange products, so every
            # subsequent lookup, cancel and bracket placement for this record
            # would act on the wrong instrument. It is reported and blocked; the
            # record is never silently rebound to the new id.
            if record.product_id is not None and record.product_id != spec.product_id:
                logger.critical(
                    "RECONCILIATION_PRODUCT_IDENTITY_MISMATCH setup=%s symbol=%s "
                    "persisted_product_id=%s registry_product_id=%s",
                    record.setup_id, record.symbol, record.product_id, spec.product_id,
                )
                if record.state != TradeLifecycleState.RECONCILIATION_REQUIRED:
                    record.record_transition(
                        TradeLifecycleState.RECONCILIATION_REQUIRED,
                        f"Persisted execution product id {record.product_id} does not "
                        f"match the product registry's current id {spec.product_id} for "
                        f"{record.symbol}; refusing to rebind this record to a "
                        f"different exchange product",
                    )
                summary["unresolved"].append(record.setup_id)
                self._raise_reconciliation_alert(
                    "PRODUCT_IDENTITY_MISMATCH", record.symbol,
                    f"setup {record.setup_id}: submitted against product "
                    f"{record.product_id} but the registry now resolves "
                    f"{record.symbol} to {spec.product_id}",
                )
                continue

            # Order ids are claimed unconditionally: a resting entry order is
            # this setup's order whether or not it has filled yet. A POSITION is
            # deliberately NOT claimed here. It used to be, and that was the
            # masking defect: the claim was recorded before step 1 refreshed the
            # fill state, and regardless of whether the record accounted for any
            # position at all. A record holding `filled_quantity == 0` -- the
            # state an ambiguous submission leaves behind -- therefore hid a real
            # exchange position from the orphan sweep in step 4, and the run
            # reported CLEAN over a live unprotected position. The claim now
            # happens in step 2, from an observed fill, after step 1 has spoken.
            for role in ("entry", "sl", "tp"):
                oid = getattr(record, f"{role}_order_id")
                if oid:
                    claimed_order_ids.add(str(oid))

            # 1. Entry order: the REST snapshot decides OPEN / PARTIAL / FILLED /
            #    terminal, and drives the existing fill + bracket handlers.
            if record.state in (
                TradeLifecycleState.ENTRY_SUBMITTED,
                TradeLifecycleState.ENTRY_PARTIALLY_FILLED,
                TradeLifecycleState.ENTRY_PENDING,
            ):
                status = await self.refresh_entry_from_exchange(record.setup_id)
                if status is None:
                    summary["unresolved"].append(record.setup_id)
                    continue
                summary["converged"].append(record.setup_id)

            # The record may have been archived by the convergence above.
            if record.setup_id not in self._active_trades:
                continue

            position = positions_by_product.get(spec.product_id)

            # 2. Local believes it holds a position; the exchange must agree.
            if record.filled_quantity > Decimal("0"):
                if position is None:
                    logger.critical(
                        "RECONCILIATION_DIVERGENCE setup=%s local filled=%s but exchange "
                        "reports no position for %s",
                        record.setup_id, record.filled_quantity, record.symbol,
                    )
                    if record.state != TradeLifecycleState.RECONCILIATION_REQUIRED:
                        record.record_transition(
                            TradeLifecycleState.RECONCILIATION_REQUIRED,
                            f"Exchange reports no open position for {record.symbol} while "
                            f"local state holds {record.filled_quantity} filled; closure "
                            f"requires authoritative realized values that are not available "
                            f"from a position snapshot",
                        )
                    summary["unresolved"].append(record.setup_id)
                    self._raise_reconciliation_alert(
                        "LOCAL_POSITION_MISSING_ON_EXCHANGE", record.symbol,
                        f"setup {record.setup_id}: local filled {record.filled_quantity} with "
                        f"no exchange position; portfolio lock retained",
                    )
                    continue

                # P1-1: a position's identity is the product AND the direction.
                # Matching on product alone meant a local LONG "found" an exchange
                # SHORT of the same absolute size and reported the pair as
                # reconciled -- the one case where the two sides are maximally
                # wrong and look maximally right, because `DeltaPosition.size` is
                # always non-negative and `side` carries the sign. The engine
                # would then have gone on to "protect" a short with a sell stop
                # below it: an OPENING order that doubles the position.
                #
                # Nothing is reversed, closed, adopted or repaired here. The
                # contradiction is recorded, the product is left UNCLAIMED so
                # step 4 reports the position, and the run cannot be clean.
                expected_side = (
                    PositionSide.LONG if record.direction == TradeDirection.LONG
                    else PositionSide.SHORT
                )
                if position.side != expected_side:
                    logger.critical(
                        "RECONCILIATION_POSITION_SIDE_MISMATCH setup=%s symbol=%s "
                        "local_direction=%s local_filled=%s exchange_side=%s "
                        "exchange_size=%s",
                        record.setup_id, record.symbol, record.direction.value,
                        record.filled_quantity, position.side.value, position.size,
                    )
                    self.state_store.record_audit(
                        action="RECONCILIATION_POSITION_SIDE_MISMATCH",
                        details={
                            "setup_id": record.setup_id, "symbol": record.symbol,
                            "product_id": spec.product_id,
                            "local_direction": record.direction.value,
                            "local_filled_quantity": str(record.filled_quantity),
                            "exchange_side": position.side.value,
                            "exchange_size": str(position.size),
                        },
                    )
                    if record.state != TradeLifecycleState.RECONCILIATION_REQUIRED:
                        record.record_transition(
                            TradeLifecycleState.RECONCILIATION_REQUIRED,
                            f"Exchange holds a {position.side.value} position of "
                            f"{position.size} on {record.symbol} while this record is "
                            f"{record.direction.value} with {record.filled_quantity} "
                            f"filled; the position cannot belong to this setup and no "
                            f"reversal, closure or adoption is attempted",
                        )
                    contradicted_products.setdefault(spec.product_id, []).append(
                        record.setup_id
                    )
                    summary["unresolved"].append(record.setup_id)
                    self._raise_reconciliation_alert(
                        "POSITION_SIDE_MISMATCH", record.symbol,
                        f"setup {record.setup_id} is {record.direction.value} but the "
                        f"exchange holds {position.side.value} {position.size} on "
                        f"{record.symbol}; protective orders for the recorded direction "
                        f"would INCREASE this position, so none are placed",
                    )
                    continue

                # Only now, with a position this record actually accounts for --
                # same product, same direction -- does the product stop being
                # unattributed for step 4's purposes.
                claimed_products.add(spec.product_id)

                # 3. Protection must exist on the exchange for the real size.
                exchange_size = abs(position.size)
                sl_live = bool(record.sl_order_id) and str(record.sl_order_id) in open_order_ids
                tp_live = bool(record.tp_order_id) and str(record.tp_order_id) in open_order_ids
                if not (sl_live and tp_live) or record.protected_quantity != exchange_size:
                    logger.critical(
                        "PROTECTION_MISSING setup=%s symbol=%s position=%s sl_live=%s "
                        "tp_live=%s protected=%s",
                        record.setup_id, record.symbol, exchange_size, sl_live, tp_live,
                        record.protected_quantity,
                    )
                    self.state_store.record_audit(
                        action="RECONCILIATION_PROTECTION_MISSING",
                        details={
                            "setup_id": record.setup_id, "symbol": record.symbol,
                            "exchange_position_size": str(exchange_size),
                            "sl_order_live": sl_live, "tp_order_live": tp_live,
                            "protected_quantity": str(record.protected_quantity),
                        },
                    )
                    # Forget bracket ids the exchange no longer has, so the
                    # existing bracket path rebuilds the pair instead of
                    # believing stale protection is live.
                    if not sl_live:
                        record.sl_order_id = None
                        record.sl_client_order_id = None
                    if not tp_live:
                        record.tp_order_id = None
                        record.tp_client_order_id = None
                    if not sl_live and not tp_live:
                        record.protected_quantity = Decimal("0")
                        record.protection_source = None
                    record.filled_quantity = max(record.filled_quantity, exchange_size)
                    await self._ensure_bracket_protection(record, exchange_size)
                    if record.state == TradeLifecycleState.PROTECTED_POSITION:
                        summary["protection_restored"].append(record.setup_id)
                    else:
                        summary["unresolved"].append(record.setup_id)
                        self._raise_reconciliation_alert(
                            "POSITION_UNPROTECTED", record.symbol,
                            f"setup {record.setup_id}: exchange position of {exchange_size} "
                            f"could not be protected",
                        )
            else:
                # Nothing filled, so this record cannot be the owner of anything
                # open on the product. Remember that it named the product -- so
                # step 4 can say which setup is implicated -- and leave the
                # product unclaimed so step 4 actually looks at it.
                unfilled_products.setdefault(spec.product_id, []).append(record.setup_id)

        # 3b. P1-5: a record still saying "I do not know what the exchange did"
        #     is itself an unresolved finding, evaluated only AFTER every record
        #     has had its chance to converge above.
        #
        #     This closes the masking route that made the ambiguous-submission
        #     alert self-clearing: an ambiguous submission leaves the record in
        #     RECONCILIATION_REQUIRED with `filled_quantity == 0`, so it took the
        #     unfilled branch, and if the exchange listings then happened to look
        #     flat -- because the order had not yet appeared, or appeared and was
        #     cancelled -- nothing landed in `unresolved`, `clean` computed True,
        #     and the run CLEARED the very alert that was protecting the account.
        #
        #     Reading two listings that are momentarily empty does not prove the
        #     submission failed (§M15 / safety rule #15), and the mandate is
        #     explicit that the ambiguity may not be cleared until authoritative
        #     reconciliation proves the state safe. The routes that legitimately
        #     resolve it already exist and all run before this point: protection
        #     restored on a real position transitions the record to
        #     PROTECTED_POSITION, and an authoritative close removes it from
        #     `_active_trades` entirely. Neither is a silent clear.
        #
        #     No new alert is raised here: the alert raised at the point of
        #     ambiguity is persisted and, because this makes the run unresolved,
        #     can no longer be auto-cleared. Re-raising per run would only spam an
        #     unbounded list with a condition already recorded.
        for record in list(self._active_trades.values()):
            if record.state != TradeLifecycleState.RECONCILIATION_REQUIRED:
                continue
            if record.setup_id in summary["unresolved"]:
                continue
            logger.critical(
                "RECONCILIATION_STATE_UNRESOLVED setup=%s symbol=%s state=%s "
                "filled=%s: exchange state for this setup is still ambiguous",
                record.setup_id, record.symbol, record.state.value,
                record.filled_quantity,
            )
            summary["unresolved"].append(record.setup_id)

        # 4. Exchange positions no local record accounts for (§M11 case C).
        #    "Accounts for" means a local fill on the SAME side, not a local
        #    mention: a record that named the product but filled nothing, or
        #    filled the opposite direction, leaves the position unexplained, and
        #    an unexplained position is reported here.
        for product_id, position in positions_by_product.items():
            if product_id in claimed_products:
                continue
            symbol = position.product_symbol
            implicated = sorted(unfilled_products.get(product_id, []))
            contradicted = sorted(contradicted_products.get(product_id, []))
            logger.critical(
                "ORPHAN_EXCHANGE_POSITION product_id=%s symbol=%s side=%s size=%s "
                "entry=%s zero_fill_local_setups=%s opposite_side_local_setups=%s",
                product_id, symbol, position.side.value, position.size,
                position.entry_price, implicated, contradicted,
            )
            details: Dict[str, Any] = {
                "product_id": product_id, "symbol": symbol,
                "side": position.side.value,
                "size": str(position.size), "entry_price": str(position.entry_price),
            }
            if implicated:
                details["zero_fill_local_setups"] = implicated
            if contradicted:
                details["opposite_side_local_setups"] = contradicted
            self.state_store.record_audit(
                action="RECONCILIATION_ORPHAN_POSITION",
                details=details,
            )
            summary["orphan_positions"].append(symbol)
            if contradicted:
                # P1-1: a local record claims the opposite direction on this
                # product. Step 2 already marked those setups unresolved and
                # raised POSITION_SIDE_MISMATCH; what step 4 adds is that the
                # exchange position itself remains UNATTRIBUTED. Reporting both
                # is the point: neither the record nor the position may be
                # quietly reinterpreted into the other.
                self._raise_reconciliation_alert(
                    "ORPHAN_EXCHANGE_POSITION", symbol,
                    f"exchange holds {position.side.value} {position.size} on {symbol} "
                    f"while local setup(s) {contradicted} record the opposite "
                    f"direction; the position is not attributed to them and no "
                    f"reversal, closure or protection is derived",
                )
                if implicated:
                    summary["unresolved"].extend(
                        _not_yet_unresolved(summary, implicated))
                    self._raise_reconciliation_alert(
                        "POSITION_ON_ZERO_FILL_SETUP", symbol,
                        f"exchange holds {position.size} on {symbol} while local setup(s) "
                        f"{implicated} record no fill; the position cannot be attributed "
                        f"to them and no fill is assumed",
                    )
            elif implicated:

                # A local record names this product but has no fill, so it cannot
                # be the owner of what is open. Both facts are reported: the
                # position is unattributed, AND the setup that may have caused it
                # is unresolved. Adopting the position into that record would be
                # inventing a fill, and an invented fill is how an unprotected
                # position becomes an unnoticed one. No repair is attempted.
                summary["unresolved"].extend(
                    _not_yet_unresolved(summary, implicated))
                self._raise_reconciliation_alert(
                    "POSITION_ON_ZERO_FILL_SETUP", symbol,
                    f"exchange holds {position.size} on {symbol} while local setup(s) "
                    f"{implicated} record no fill; the position cannot be attributed "
                    f"to them and no fill is assumed",
                )
            else:
                # Protecting this would require authoritative SL/TP levels, and
                # the strategy intent behind an unknown position is not
                # available, so inventing levels is forbidden. Fail closed
                # instead: no new entries until an operator resolves it.
                self._raise_reconciliation_alert(
                    "ORPHAN_EXCHANGE_POSITION", symbol,
                    f"exchange holds {position.size} on {symbol} with no local trade; "
                    f"protective levels are unknown so no bracket can be derived",
                )

        # 5. Exchange orders belonging to no local trade (§M11 case A).
        for order in exchange_orders:
            if str(order.id) in claimed_order_ids:
                continue
            logger.warning(
                "ORPHAN_EXCHANGE_ORDER order=%s symbol=%s side=%s size=%s state=%s",
                order.id, order.product_symbol, order.side.value, order.size,
                order.state.value,
            )
            self.state_store.record_audit(
                action="RECONCILIATION_ORPHAN_ORDER",
                details={
                    "order_id": str(order.id), "symbol": order.product_symbol,
                    "side": order.side.value, "size": str(order.size),
                    "state": order.state.value, "reduce_only": order.reduce_only,
                },
            )
            summary["orphan_orders"].append(str(order.id))
            self._raise_reconciliation_alert(
                "ORPHAN_EXCHANGE_ORDER", order.product_symbol,
                f"exchange order {order.id} ({order.side.value} {order.size}) is not "
                f"tracked locally and is reported, not cancelled",
            )

        # 6. P1-4: a run that overlapped an entry submission cannot speak for the
        #    account. The counter is re-read here as well as before the listings,
        #    so a submission admitted DURING the run is caught too; either way the
        #    run is unresolved, which is what stops `clean` below from clearing
        #    alerts on evidence that was incomplete when it was gathered.
        #
        #    Deliberately NOT a persisted alert. The condition is transient -- it
        #    ends when the submission returns -- and a blocking alert for it would
        #    have to be cleared by an operator after every ordinary concurrent
        #    entry. Marking the run unresolved is the fail-closed half that
        #    matters: nothing is declared clean, no alert is cleared, and the
        #    startup gate refuses to admit on this verdict. Re-running
        #    reconciliation once the submission has returned resolves it.
        with self._lock:
            submission_in_flight = submission_in_flight or self._submissions_in_flight > 0
        if submission_in_flight:
            logger.critical(
                "RECONCILIATION_SUBMISSION_IN_FLIGHT account=%s: an entry submission "
                "overlapped this run, so the exchange listings it read cannot be "
                "treated as the complete account state",
                account_id or self.state_store.account_id,
            )
            summary["unresolved"].append("SUBMISSION_IN_FLIGHT")

        clean = (
            not summary["unresolved"]
            and not summary["orphan_positions"]
            and not summary["orphan_orders"]
        )
        if clean and self._reconciliation_alerts:
            cleared = self.clear_reconciliation_alerts("RECONCILIATION_CLEAN")
            logger.info(
                "RECONCILIATION_ALERTS_CLEARED count=%s: exchange and local state agree",
                cleared,
            )
            summary["alerts_cleared"] = cleared

        logger.info(
            "RECONCILIATION_COMPLETED checked=%s converged=%s protection_restored=%s "
            "orphan_positions=%s orphan_orders=%s unresolved=%s",
            summary["checked"], len(summary["converged"]),
            len(summary["protection_restored"]), len(summary["orphan_positions"]),
            len(summary["orphan_orders"]), len(summary["unresolved"]),
        )
        return summary

    # ── Position Closure Lifecycle ────────────────────────────────────────────

    async def close_position(
        self,
        setup_id: str,
        reason: CloseReason,
        realized_pnl: Optional[Decimal] = None,
        gross_pnl: Optional[Decimal] = None,
        trading_fees: Optional[Decimal] = Decimal("0.0"),
        funding_costs: Decimal = Decimal("0.0"),
        taxes_and_charges: Decimal = Decimal("0.0"),
        final_exchange_balance: Optional[Decimal] = None,
        trading_fees_source: Optional[str] = None,
    ) -> TradeLifecycleRecord:
        """Close an active position, cancel stale protective orders, reconcile net PnL, and finalize lifecycle.

        `trading_fees=None` means the execution commission was never observed
        (Task O §O2). The closure still completes -- the position really is gone
        -- but the record keeps the fee as `None`, marks `net_pnl` as
        cost-incomplete, and leaves the caller's blocking reconciliation alert
        standing, so nothing downstream reads the result as a verified net
        figure. A zero is never substituted for the missing observation.
        """
        with self._lock:
            record = self._active_trades.get(setup_id)
            if not record:
                raise ValueError(f"No active trade found for setup {setup_id}")

            product_spec = _authoritative_product_specification(record)

            # 1. Cancel remaining open bracket orders to avoid stale executions
            #
            # Task O §O10: a refused cancellation is not a warning, it is an open
            # question about whether a reduce-only order is still resting. Each
            # refusal is put to the exchange and only a positively confirmed
            # terminal state lets this closure complete; every other verdict keeps
            # the trade, the position, the order ids and the portfolio lock, the
            # same way the entry-expiry path does (safety rules #11, #14). Both
            # brackets are still attempted, and each is judged on its own.
            unresolved_brackets: List[Dict[str, Optional[str]]] = []
            for label in ("sl", "tp"):
                bracket_id = getattr(record, f"{label}_order_id")
                if not bracket_id:
                    continue
                cancel_error: Optional[str] = None
                try:
                    await self.client.cancel_order(int(bracket_id), product_spec.product_id)
                except Exception as e:
                    cancel_error = str(e)
                if cancel_error is None:
                    continue
                logger.warning(
                    "Error cancelling %s order %s: %s",
                    label.upper(), bracket_id, cancel_error,
                )
                outcome, exchange_state, verify_error = await self._classify_refused_cancel(bracket_id)
                if outcome == CANCEL_OUTCOME_GONE:
                    logger.info(
                        "CLOSE_BRACKET_CANCEL_TERMINAL_CONFIRMED setup=%s %s order=%s state=%s",
                        setup_id, label.upper(), bracket_id, exchange_state,
                    )
                    continue
                unresolved_brackets.append({
                    "role": label.upper(),
                    "order_id": str(bracket_id),
                    "outcome": outcome,
                    "exchange_state": exchange_state,
                    "cancel_error": cancel_error,
                    "verify_error": verify_error,
                })

            # 2. Net PnL & Fee Calculation
            #
            # §O2/§O3: `gross_pnl is None and realized_pnl is None` means no
            # authoritative result was supplied at all, and `trading_fees is
            # None` means the commission was never observed. Neither is silently
            # renamed "zero": the arithmetic below has to produce a number for
            # the balance ledger, so what is missing is recorded as missing on
            # the record itself (`*_source`, `net_pnl_is_cost_complete`) instead
            # of being laundered into the value.
            pnl_observed = gross_pnl is not None or realized_pnl is not None
            eff_gross = gross_pnl if gross_pnl is not None else (
                realized_pnl if realized_pnl is not None else Decimal("0"))
            fee_observed = trading_fees is not None
            eff_fees = trading_fees if fee_observed else Decimal("0")
            net_pnl = CapitalAllocator.calculate_net_pnl(eff_gross, eff_fees, funding_costs, taxes_and_charges)

            record.gross_pnl = eff_gross
            record.gross_pnl_source = (
                record.gross_pnl_source
                or ("CALLER_SUPPLIED" if pnl_observed else "UNOBSERVED")
            )
            record.trading_fees = trading_fees
            record.trading_fees_source = (
                trading_fees_source
                or record.trading_fees_source
                or ("CALLER_SUPPLIED" if fee_observed else "UNOBSERVED")
            )
            record.net_pnl_is_cost_complete = fee_observed
            record.funding_costs = funding_costs
            record.net_pnl = net_pnl
            record.realized_pnl = net_pnl
            record.close_reason = reason

            if final_exchange_balance is not None:
                record.post_trade_balance = final_exchange_balance
                self.state_store.account.available_balance = final_exchange_balance
                self.state_store.account.total_equity = final_exchange_balance
            else:
                post_bal = CapitalAllocator.calculate_compounded_balance(record.pre_trade_balance, net_pnl)
                record.post_trade_balance = post_bal
                self.state_store.account.available_balance = post_bal
                self.state_store.account.total_equity = post_bal

            fees_text = f"${eff_fees:.2f}" if fee_observed else "UNOBSERVED (excluded)"
            net_label = "Net PnL" if fee_observed else "PnL before unobserved fees"

            # §O10: the accounting above stands -- it is the caller's authoritative
            # exchange data, and discarding it would trade one gap for another --
            # but the closure is NOT completed while a protective order's fate is
            # unresolved. Nothing is archived, the position stays, the bracket ids
            # stay, and the lock stays, so no second entry can be admitted while a
            # reduce-only order may still be resting on the exchange.
            if unresolved_brackets:
                detail = "; ".join(
                    f"{u['role']} order {u['order_id']} {u['outcome']}"
                    f" (state={u['exchange_state']}, cancel_error={u['cancel_error']},"
                    f" verify_error={u['verify_error']})"
                    for u in unresolved_brackets
                )
                record.record_transition(
                    TradeLifecycleState.RECONCILIATION_REQUIRED,
                    f"Position close accounting completed for {reason.value} "
                    f"(Gross: ${eff_gross:.2f}, Fees: {fees_text}, {net_label}: "
                    f"${net_pnl:.2f}, Post Balance: ${record.post_trade_balance:.2f}) "
                    f"but protective order cancellation was not confirmed: {detail}. "
                    f"Trade, position, bracket order ids and portfolio lock retained; "
                    f"no new entry permitted",
                )
                for u in unresolved_brackets:
                    self.state_store.record_audit(
                        action="CLOSE_PROTECTION_CANCEL_UNRESOLVED",
                        details={
                            "setup_id": setup_id, "symbol": record.symbol,
                            "site": "close_position", "role": u["role"],
                            "order_id": u["order_id"], "outcome": u["outcome"],
                            "exchange_state": u["exchange_state"],
                            "cancel_error": u["cancel_error"],
                            "verify_error": u["verify_error"],
                            "close_reason": reason.value,
                            "lock_retained": True, "archived": False,
                        },
                    )
                    # UNVERIFIED = the verification GET itself failed (including an
                    # `order_not_found`); UNCONFIRMED = it answered, and the answer
                    # was not a terminal state.
                    self._raise_reconciliation_alert(
                        "PROTECTION_CANCEL_UNVERIFIED"
                        if u["outcome"] == CANCEL_OUTCOME_UNKNOWN
                        else "PROTECTION_CANCEL_UNCONFIRMED",
                        record.symbol,
                        f"setup {setup_id}: {u['role']} order {u['order_id']} was not "
                        f"confirmed cancelled (outcome={u['outcome']}, "
                        f"exchange_state={u['exchange_state']}); position, bracket "
                        f"order ids and portfolio lock retained",
                    )
                logger.critical(
                    "CLOSE_POSITION_RECONCILIATION_REQUIRED setup=%s symbol=%s unresolved=%s",
                    setup_id, record.symbol, len(unresolved_brackets),
                )
                return record

            record.record_transition(
                TradeLifecycleState.POSITION_CLOSED,
                f"Position closed due to {reason.value}. Gross: ${eff_gross:.2f}, Fees: {fees_text}, {net_label}: ${net_pnl:.2f}, Post Balance: ${record.post_trade_balance:.2f}"
            )

            # 3. Archive to history
            del self._active_trades[setup_id]
            self._trade_history.append(record)

            # Update position in local state store
            if record.symbol in self.state_store.positions:
                pos = self.state_store.positions[record.symbol]
                pos.status = PositionStatus.CLOSED
                pos.closed_at = datetime.now(timezone.utc)
                pos.realized_pnl = net_pnl
                self.state_store.position_history.append(pos)
                del self.state_store.positions[record.symbol]

            # 4. Release Single-Trade Lock
            effective_user_id = record.user_id or self.state_store.account.user_id or "default_user"
            self.single_trade_lock.release_lock(effective_user_id, record.account_id, setup_id)

            return record

    # ── Emergency Kill Switch Workflow ────────────────────────────────────────

    async def activate_kill_switch(self, reason: str = "EMERGENCY_OPERATOR_TRIGGER") -> Dict[str, Any]:
        """Activate the emergency kill switch: blocks all new entries, cancels pending entry orders,

        preserves protective SL/TP brackets.

        The switch itself always engages -- `kill_switch_active` is set before any
        exchange call, so a refused cancellation can never leave entries admissible.

        What a refusal does change is what may be *concluded*. An entry order whose
        cancellation the exchange refused is asked about (`_classify_refused_cancel`)
        and only a positively confirmed terminal state counts as cancelled. Any other
        verdict keeps the trade in `_active_trades` with its `entry_order_id` intact
        -- reconciliation claims order ids from active trades only -- and names the
        order in the returned `unverified_orders`/`unverified_count`. Failure is never
        left to be inferred from an id's absence from `cancelled_orders`.
        """
        self.state_store.account.kill_switch_active = True
        cancelled_orders: List[str] = []
        # §O10: a cancellation this switch could not confirm is now stated
        # explicitly. It is never left to be read off the absence of an id from
        # `cancelled_orders` (safety rule #13), and the trade that owns it is kept
        # rather than archived, so the order keeps an owner for reconciliation.
        unverified_orders: List[str] = []
        unverified_details: List[Dict[str, Optional[str]]] = []

        with self._lock:
            for setup_id, trade in list(self._active_trades.items()):
                if trade.state in (TradeLifecycleState.ENTRY_PENDING, TradeLifecycleState.ENTRY_SUBMITTED):
                    finding: Optional[Dict[str, Optional[str]]] = None
                    if trade.entry_order_id:
                        cancel_error: Optional[str] = None
                        try:
                            spec = _authoritative_product_specification(trade)
                            await self.client.cancel_order(int(trade.entry_order_id), spec.product_id)
                        except Exception as e:
                            cancel_error = str(e)
                        if cancel_error is None:
                            cancelled_orders.append(trade.entry_order_id)
                        else:
                            logger.warning("Failed to cancel entry order %s on kill-switch: %s", trade.entry_order_id, cancel_error)
                            outcome, exchange_state, verify_error = await self._classify_refused_cancel(trade.entry_order_id)
                            if outcome == CANCEL_OUTCOME_GONE:
                                # The exchange itself reports the order terminal, so
                                # the refused cancel changed nothing that matters.
                                cancelled_orders.append(trade.entry_order_id)
                                logger.info(
                                    "KILL_SWITCH_ENTRY_TERMINAL_CONFIRMED setup=%s order=%s state=%s",
                                    setup_id, trade.entry_order_id, exchange_state,
                                )
                            else:
                                finding = {
                                    "setup_id": setup_id, "symbol": trade.symbol,
                                    "site": "activate_kill_switch",
                                    "order_id": str(trade.entry_order_id),
                                    "outcome": outcome,
                                    "exchange_state": exchange_state,
                                    "cancel_error": cancel_error,
                                    "verify_error": verify_error,
                                }

                    if finding is not None:
                        # D4: the trade stays in `_active_trades` so the order keeps
                        # an owner -- reconciliation builds `claimed_order_ids` from
                        # active trades only, so archiving here is what would turn a
                        # possibly-live entry order into an orphan. The kill switch
                        # touches no lock on either path, so nothing is released.
                        unverified_orders.append(finding["order_id"])
                        unverified_details.append(finding)
                        trade.record_transition(
                            TradeLifecycleState.RECONCILIATION_REQUIRED,
                            f"Kill switch activated: {reason}, but entry order "
                            f"{finding['order_id']} cancellation was not confirmed "
                            f"(outcome={finding['outcome']}, state={finding['exchange_state']}, "
                            f"cancel_error={finding['cancel_error']}, "
                            f"verify_error={finding['verify_error']}). Trade and entry "
                            f"order id retained; not archived",
                        )
                        self.state_store.record_audit(
                            action="KILL_SWITCH_ENTRY_CANCEL_UNRESOLVED",
                            details={**finding, "reason": reason,
                                     "archived": False, "lock_retained": True},
                        )
                        # UNVERIFIED = the verification GET itself failed (an
                        # `order_not_found` included); UNCONFIRMED = it answered, and
                        # the answer was not a terminal state.
                        self._raise_reconciliation_alert(
                            "KILL_SWITCH_ENTRY_UNVERIFIED"
                            if finding["outcome"] == CANCEL_OUTCOME_UNKNOWN
                            else "KILL_SWITCH_ENTRY_CANCEL_UNCONFIRMED",
                            trade.symbol,
                            f"setup {setup_id}: entry order {finding['order_id']} was "
                            f"not confirmed cancelled on kill-switch "
                            f"(outcome={finding['outcome']}, "
                            f"exchange_state={finding['exchange_state']}); trade and "
                            f"entry order id retained, not archived",
                        )
                        logger.critical(
                            "KILL_SWITCH_ENTRY_RECONCILIATION_REQUIRED setup=%s symbol=%s order=%s outcome=%s",
                            setup_id, trade.symbol, finding["order_id"], finding["outcome"],
                        )
                        continue

                    trade.record_transition(
                        TradeLifecycleState.KILL_SWITCH_TRIGGERED,
                        f"Kill switch activated: {reason}"
                    )
                    del self._active_trades[setup_id]
                    self._trade_history.append(trade)

        self.state_store.record_audit(
            action="KILL_SWITCH_ACTIVATED",
            details={
                "reason": reason,
                # `cancelled_orders` carries positively confirmed cancellations
                # only. The unresolved ones are named, not implied by omission.
                "cancelled_orders": cancelled_orders,
                "unverified_orders": unverified_orders,
                "unverified_count": len(unverified_orders),
                "unverified_details": unverified_details,
            },
        )

        logger.critical(
            "EMERGENCY KILL SWITCH ACTIVATED: %s. Cancelled %d entry orders, %d unverified.",
            reason, len(cancelled_orders), len(unverified_orders),
        )

        return {
            "kill_switch_active": True,
            "reason": reason,
            "cancelled_orders_count": len(cancelled_orders),
            "cancelled_orders": cancelled_orders,
            "unverified_count": len(unverified_orders),
            "unverified_orders": unverified_orders,
            "unverified_details": unverified_details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def reset_kill_switch(self, authorized_by: str) -> Dict[str, Any]:
        """Reset the emergency kill switch (requires authenticated operator action)."""
        self.state_store.account.kill_switch_active = False
        self.state_store.record_audit(
            action="KILL_SWITCH_RESET",
            details={"authorized_by": authorized_by}
        )
        logger.info("Emergency kill switch reset by %s", authorized_by)
        return {
            "kill_switch_active": False,
            "authorized_by": authorized_by,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _release_setup_lock(
        self,
        user_id: Optional[str],
        account_id: str,
        setup_id: str,
    ) -> None:
        """
        Release the single-trade lock this setup owns.

        Uses the same resolution of `effective_user_id` and the same
        `release_lock` call as `_create_rejected_record`, so there is one
        release mechanism rather than two. `release_lock` is setup-scoped and
        idempotent: it is a no-op when the lock is held by a different setup or
        has already been released, so this can neither steal another setup's
        lock nor double-release its own.

        Call this only on exits where nothing can exist on the exchange. When
        an order may be live, or a position may be open or unprotected, the
        lock is retained on purpose (safety rules #11, #14) and released by
        `close_position` / reconciliation instead.
        """
        effective_user_id = user_id or self.state_store.account.user_id or "default_user"
        self.single_trade_lock.release_lock(effective_user_id, account_id, setup_id)

    def _create_rejected_record(
        self,
        setup_id: str,
        account_id: str,
        user_id: Optional[str],
        symbol: str,
        direction: Union[TradeDirection, StrategyDirection, str],
        quantity: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        risk_reward_ratio: Decimal,
        code: str,
        message: str,
    ) -> TradeLifecycleRecord:
        # Release single trade lock if it was acquired for this rejected attempt.
        # SINGLE_TRADE_LIMIT_EXCEEDED is the one code where acquisition failed
        # because another setup owns the lock, so it must not be released here.
        # REFUSAL_STARTUP_GATE is refused BEFORE this attempt acquires anything,
        # so a release here could only drop a lock that a restored session (or
        # another attempt) still holds -- and retaining a lock is the fail-safe
        # direction. The pre-existing "RECONCILIATION_REQUIRED" refusal has the
        # same pre-lock property; it is left unchanged here deliberately (minimal
        # change), since its own gate already blocks every entry while it stands.
        if code not in (RejectionReasonCode.SINGLE_TRADE_LIMIT_EXCEEDED.value,
                        REFUSAL_STARTUP_GATE):
            self._release_setup_lock(user_id, account_id, setup_id)

        dir_enum = direction if isinstance(direction, TradeDirection) else TradeDirection.LONG
        rec = TradeLifecycleRecord(
            setup_id=setup_id,
            account_id=account_id,
            user_id=user_id,
            symbol=symbol,
            direction=dir_enum,
            requested_quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            risk_reward_ratio=risk_reward_ratio,
            risk_amount=abs(entry_price - stop_loss) * quantity,
            reward_amount=abs(take_profit - entry_price) * quantity,
            pre_trade_balance=self.state_store.account.available_balance,
            state=TradeLifecycleState.ENTRY_REJECTED,
            rejection_code=code,
            error_message=message,
        )
        rec.record_transition(TradeLifecycleState.ENTRY_REJECTED, f"{code}: {message}")
        self._trade_history.append(rec)
        return rec
