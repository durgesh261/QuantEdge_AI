"""
Manual SMC — Authoritative OB Lifecycle State Machine (Phase 1 Step 2).
======================================================================

THE single source of truth for Manual SMC order-block lifecycle transitions.

Extracted from the frozen research oracle
`quantedge.ai.research.displacement_gated_retest_engine.run_manual_spec_backtest`
steps 1–3 (oracle L1598–L1967). Backtest and live must both drive THIS module.
No second lifecycle implementation may exist — not in `backtest.py`, not in
tests.

PER-CANDLE ORDERING (preserved from the oracle, load-bearing)
-------------------------------------------------------------
  1. Resolve the active trade's exit (TP / SL / dual-touch / 72-bar timeout).
  2. Update every live OB for this asset (invalidation, first touch / probe,
     entry-window expiry, entry fill).
  3. Run the BOS scanner and admit new OBs AFTER the bar is fully processed
     — this is what makes admission break+1 rather than break+2.

Step 3 must stay last: admitting a new OB before step 2 would let the BOS
candle act as its own displacement candle, and would let the OB-creation candle
trade itself — which the manual specification forbids outright.

ACTIVATION MODES (the manual-specification change)
--------------------------------------------------
`activation_mode` selects how a live OB's 25% entry limit becomes executable:

  * `ACTIVATION_MODE_FIRST_TOUCH` — THE PRODUCTION POLICY, i.e. the manual
    specification. The OB is active from creation and waits indefinitely. The
    first touch of the zone (proximal edge, wick or body, edge inclusive) arms
    the limit on that same candle for exactly `entry_window_candles` candles
    inclusive. Reached inside the window -> filled. Window expires -> cancelled
    and PERMANENTLY invalidated.

  * `ACTIVATION_MODE_ORACLE_C` — the oracle's Mode C probe -> pullback
    displacement gate, arming from `displacement_bar + 1` with no expiry.
    Retained unchanged so research parity stays provable.

`ACTIVATION_MODE_ORACLE_C` is this class's CONSTRUCTOR DEFAULT and
`ACTIVATION_MODE_FIRST_TOUCH` is the PRODUCTION default — the split is
deliberate. It is an ACTIVATION split only: the take profit is an authorized
0.60% under both `ManualSpecConfig()` (research) and
`manual_smc_production_config()` (production). A bare
`ManualSMCLifecycle()` must keep reproducing the frozen oracle's ACTIVATION
path, so production policy is injected at the production entry point
(`ManualSMCStrategy`) and nowhere else. The DETECTOR default is a separate
question and points the other way -- see the DETECTORS section below.

Both modes share every other rule: OB geometry, the 25% entry depth, the
opposite-edge stop, wick-based distal invalidation, the single-active-trade
gate, and the post-fill exit resolution.

THE ONE INTENTIONAL DEVIATION FROM THE ORACLE
---------------------------------------------
The oracle gated entry on a timestamp watermark::

    if global_lock_until_dt is not None and c_ts <= global_lock_until_dt:
        continue                                   # oracle L1920–1922

That only blocks entries at the SAME timestamp. On any strictly-later bar a
second OB filled and OVERWROTE `active_trade` (oracle L1937), while the first
trade's OB stayed `TRADE_ACTIVE` in the pool and was skipped forever by
``if ob.state == TRADE_ACTIVE: continue`` (oracle L1851) — so the first trade
was never closed and never recorded. The published multi-asset baseline was
produced under those semantics.

This module enforces the actual rule instead::

    if self.active_trade is not None:  -> reject, regardless of candle

The oracle's timestamp watermark is ALSO retained (`_last_trade_closed_dt`) as
a secondary conservative guard: a trade that closes on candle T does not allow
a new entry at timestamp <= T, because intra-candle ordering is unknowable from
OHLC alone. The combined gate is strictly stronger than the oracle's — nothing
was weakened. A rejected OB stays `LIMIT_RESTING` and remains eligible later.

EVERYTHING ELSE IS ORACLE-FAITHFUL
----------------------------------
Wick predicates, the oracle's close-based Mode C probe→pullback (still selectable
via `ACTIVATION_MODE_ORACLE_C`, including `limit_active_from_bar =
displacement_bar + 1`), wick distal invalidation, dual-touch SL-first with
`DUAL_TOUCH_CONSERVATIVE_SL` / `is_ambiguous=True`, the 72-bar post-fill
timeout and its `int(seconds/3600)` arithmetic, `max(1.0, hours)` holding
normalisation, MFE tracking, and SHORT-before-LONG scanner emission order are
all copied expression-for-expression. float throughout; no Decimal.

RESTING-ORDER EXPIRY (manual specification — supersedes the original policy)
---------------------------------------------------------------------------
Before the first touch there is NO expiry of any kind: an untouched OB stays
active indefinitely, across days and across a backtest warm-up boundary, and OB
age alone never invalidates it.

After the first touch, in `ACTIVATION_MODE_FIRST_TOUCH`, the resting limit lives
for exactly `entry_window_candles` candles inclusive of the first-touch candle.
A `LIMIT_RESTING` OB is then cancelled by: entry fill, distal wick breach,
entry-window expiry, or an explicit external operational cancellation. An OB
invalidated by any of those can never become active again. In
`ACTIVATION_MODE_ORACLE_C` there is no expiry, exactly as the oracle had it.
`max_holding_bars` (72) applies strictly AFTER fill in both modes.

OUT OF SCOPE FOR THIS STEP (deliberately absent)
------------------------------------------------
Capital, PnL, fees, notional and compounding (sizing.py); the crash-safe
Postgres lock (portfolio.py); tick quantization; persistence; the orchestrator
adapter; execution. `realized_r` IS computed here because it is dimensionless
exit resolution, not position sizing.

No production wiring. No execution wiring. Nothing imports this module yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from quantedge.strategy.manual_smc.geometry import (
    _manual_distal_breached,
    _manual_entry_touched,
    _manual_sl_hit,
    _manual_tp_hit,
    _manual_zone_touched,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_ENTRY_WINDOW_CANDLES,
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBRecord,
    ManualOBState,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.scanner import (
    LuxAlgoInternalOBScanner,
    ManualOBScanner,
    ManualSpecBOSScanner,
)
from quantedge.strategy.manual_smc.invalidation import (
    DEFAULT_REASON,
    DEFAULT_REQUESTED_BY,
    ManualOBInvalidation,
    ManualOBInvalidationLedger,
    build_record,
    fingerprint_for_ob,
    record_for_ob,
)

# Outcome / exit-reason string constants (oracle-identical values).
OUTCOME_TP = "FILLED_TP"
OUTCOME_SL = "FILLED_SL"
OUTCOME_TIMEOUT = "FILLED_TIMEOUT"

REASON_TP_HIT = "TP_HIT"
REASON_SL_HIT = "SL_HIT"
REASON_DUAL_TOUCH = "DUAL_TOUCH_CONSERVATIVE_SL"
REASON_TIMEOUT = "TIMEOUT"

DISPLACEMENT_MODE = "C_PROBE_PULLBACK"


class LifecycleClockError(RuntimeError):
    """A live market update was routed into a clock that cannot accept it."""


# ---------------------------------------------------------------------------
# ACTIVATION MODES — how an OB's 25% entry limit becomes live
# ---------------------------------------------------------------------------
#: The manual specification, and the policy every PRODUCTION entry point selects
#: (see `ManualSMCStrategy`). An OB is active from creation and waits, untouched,
#: indefinitely. The FIRST TOUCH of the OB zone (the proximal edge, wick or body,
#: edge inclusive) arms the 25% limit for exactly `entry_window_candles` candles
#: INCLUSIVE of the first-touch candle. Entry reached inside the window ->
#: filled. Window expires -> the order is cancelled and the OB is PERMANENTLY
#: invalidated.
ACTIVATION_MODE_FIRST_TOUCH: str = "FIRST_TOUCH_WINDOW"

#: The frozen research oracle's Mode C displacement gate, retained verbatim so
#: research parity remains provable and reproducible. A close-based probe beyond
#: the proximal, then a pullback close back through it, arms the limit from
#: `displacement_bar + 1` with NO expiry. This is `ManualSMCLifecycle`'s own
#: constructor default — a bare lifecycle is the research engine — but it is
#: never what production runs.
ACTIVATION_MODE_ORACLE_C: str = DISPLACEMENT_MODE

ACTIVATION_MODES: frozenset = frozenset(
    {ACTIVATION_MODE_FIRST_TOUCH, ACTIVATION_MODE_ORACLE_C})

#: Candle count for the first-touch entry window, INCLUSIVE of the first-touch
#: candle: bars [limit_active_from_bar, limit_active_from_bar + 2].
ENTRY_WINDOW_CANDLES: int = MANUAL_SMC_ENTRY_WINDOW_CANDLES

# ---------------------------------------------------------------------------
# DETECTORS -- which algorithm decides what an order block IS
# ---------------------------------------------------------------------------
# This selects the STRUCTURE DETECTOR only. Everything after detection is
# shared and unchanged: 25% entry, fixed 0.60% TP, opposite-edge SL, first
# touch, the 3-candle entry window, one trade at a time, and operator
# invalidation. Changing the detector changes WHICH zones exist and WHERE their
# edges are; it changes no rule about how a zone is traded.

#: The manual specification, extracted verbatim from the frozen research
#: oracle: a single-candle close displacement past the most recent
#: opposite-colour candle within `cfg.lookback`, with a HALF-candle zone
#: (`[low, close]` for SHORT, `[close, high]` for LONG).
#:
#: NO LONGER THE PRODUCTION DEFAULT. The operator has ruled that this detector
#: is NOT representative of the TradingView/LuxAlgo setup being traded, so it
#: was demoted by an explicit authorised decision. It remains fully supported
#: and EXPLICITLY SELECTABLE -- it is still the verbatim oracle extraction, and
#: the oracle-equivalence and Phase-1 lifecycle acceptance gates select it by
#: name -- but nothing reaches it any more without asking for it.
DETECTOR_MANUAL_SPEC: str = "MANUAL_SPEC_BOS"

#: The LuxAlgo "Smart Money Concepts" internal-structure reconstruction:
#: swing-anchored BOS/CHoCH crossovers with an ATR-filtered FULL-candle zone
#: (`[parsedLow, parsedHigh]`).
#:
#: THIS IS THE PRODUCTION DEFAULT. It is the detector the operator's own
#: TradingView chart runs, and the authorised decision is that production must
#: reproduce that chart's structure. The consequence is disclosed, not hidden:
#: full-candle zones roughly double the stop distance and therefore roughly
#: halve the leverage the existing sizing rule derives (measured medians 45-76x,
#: never below the authorised 1x floor), and roughly a fifth as many blocks are
#: produced. No downstream rule -- 25% entry, opposite-edge stop, fixed 0.60%
#: take profit, the 3-candle entry window, one trade at a time, operator
#: invalidation, the 1x-100x leverage band -- was changed to accommodate it.
DETECTOR_LUXALGO_INTERNAL: str = "LUXALGO_INTERNAL"

DETECTORS: frozenset = frozenset(
    {DETECTOR_MANUAL_SPEC, DETECTOR_LUXALGO_INTERNAL})


class ManualLifecycleEventType(Enum):
    """Observable lifecycle transitions, emitted in the order they occur."""
    OB_CREATED = "OB_CREATED"
    PRE_DISPLACEMENT_TOUCH = "PRE_DISPLACEMENT_TOUCH"
    PROBE_CONFIRMED = "PROBE_CONFIRMED"
    DISPLACEMENT_CONFIRMED = "DISPLACEMENT_CONFIRMED"
    FIRST_TOUCH_LIMIT_ACTIVATED = "FIRST_TOUCH_LIMIT_ACTIVATED"
    INVALIDATED = "INVALIDATED"
    ENTRY_FILLED = "ENTRY_FILLED"
    ENTRY_BLOCKED_BY_ACTIVE_TRADE = "ENTRY_BLOCKED_BY_ACTIVE_TRADE"
    TRADE_CLOSED = "TRADE_CLOSED"
    #: A BOS scan produced an order block the operator has invalidated, so it
    #: was NOT admitted to the live pool. Distinct from INVALIDATED on purpose:
    #: nothing died on this candle, a creation was refused — and the runtime's
    #: `expired_setup_ids` must not try to cancel an exchange order for a setup
    #: that was never submitted.
    OB_CREATION_SUPPRESSED = "OB_CREATION_SUPPRESSED"


@dataclass
class ManualLifecycleEvent:
    """One observable transition. Diagnostics only — never a control signal."""
    event_type: ManualLifecycleEventType
    asset: str
    bar_idx: int
    ts: datetime
    ob_id: str
    direction: str
    state: ManualOBState
    detail: str = ""
    strategy_name: str = MANUAL_SMC_STRATEGY_NAME
    strategy_version: str = MANUAL_SMC_STRATEGY_VERSION


@dataclass
class ManualActiveTrade:
    """
    The single globally-active trade.

    Carries only what exit resolution needs, plus the leverage/geometry values
    captured at fill so a later sizing layer can compute PnL without
    re-deriving them. No capital, notional, fee or balance field lives here —
    that is sizing.py's responsibility.
    """
    ob: ManualOBRecord
    asset: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    applied_leverage: float
    theoretical_leverage: float
    risk_dist: float
    reward_dist: float
    fill_dt: datetime
    retest_dt: datetime
    fill_bar_idx: int


@dataclass
class ManualTradeExit:
    """A resolved, closed trade. Exit resolution only — no PnL."""
    asset: str
    direction: str
    ob_id: str
    origin_bar_idx: int
    bos_bar_idx: int
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    fill_dt: datetime
    exit_dt: datetime
    fill_bar_idx: int
    exit_bar_idx: int
    outcome: str
    reason_for_exit: str
    is_ambiguous: bool
    realized_r: float
    risk_dist: float
    reward_dist: float
    holding_bars: int
    holding_time_hours: float
    applied_leverage: float
    theoretical_leverage: float
    sl_dist_pct: float
    entry_bar_from_bos: int
    ob_age_at_entry_hours: float
    pre_displacement_touches: int
    retest_number: int
    mfe_from_proximal: float
    displacement_mode: str = DISPLACEMENT_MODE
    data_timeframe: str = "1h"
    strategy_name: str = MANUAL_SMC_STRATEGY_NAME
    strategy_version: str = MANUAL_SMC_STRATEGY_VERSION
    narrative: str = ""


@dataclass(frozen=True)
class ManualOBInvalidationOutcome:
    """
    What one `invalidate_ob()` call actually did.

    Every field exists so the caller cannot mistake one situation for another:

      * `created is False` — the order block was ALREADY invalidated and the
        stored record was kept. A success, and the idempotent path.
      * `evicted_ob_id is None` and `live_trade_untouched is False` — nothing
        matching was in the live pool. Also a success: the decision is recorded
        and any future rescan is blocked (requirement A9).
      * `live_trade_untouched is True` — the block is TRADE_ACTIVE right now.
        The decision was recorded but the OPEN POSITION WAS DELIBERATELY LEFT
        ALONE. Reported, never hidden.
    """
    record: ManualOBInvalidation
    created: bool
    evicted_ob_id: Optional[str]
    live_trade_untouched: bool

    @property
    def fingerprint(self) -> str:
        return self.record.fingerprint


class ManualSMCLifecycle:
    """
    Authoritative Manual SMC lifecycle state machine.

    One instance owns the multi-asset candidate pool and the single active
    trade. Drive it by calling `process_candle()` once per CLOSED candle, in
    global chronological order across assets.

    The candidate pool is unbounded: many OBs may sit concurrently in
    AWAITING_DISPLACEMENT / LIMIT_RESTING across all assets. Only one may ever
    be TRADE_ACTIVE.
    """

    def __init__(
        self,
        config: Optional[ManualSpecConfig] = None,
        assets: Optional[List[str]] = None,
        activation_mode: str = ACTIVATION_MODE_ORACLE_C,
        entry_window_candles: int = ENTRY_WINDOW_CANDLES,
        invalidations: Optional[ManualOBInvalidationLedger] = None,
        detector: str = DETECTOR_LUXALGO_INTERNAL,
    ) -> None:
        """
        A BARE `ManualSMCLifecycle()` IS NOT PRODUCTION AND IS NO LONGER THE
        ORACLE EITHER.

        Its ACTIVATION default is still the oracle's `C_PROBE_PULLBACK` and its
        config default is still `ManualSpecConfig()`'s 0.60% take profit, for the
        same reason `manual_smc_production_config()` exists separately: the
        extraction stays provable against the oracle. But the DETECTOR default is
        now `DETECTOR_LUXALGO_INTERNAL` by an explicit authorised decision, so
        oracle reproduction requires naming `detector=DETECTOR_MANUAL_SPEC`. That
        is stated rather than papered over: the oracle-equivalence and Phase-1
        acceptance gates name it, and a bare object no longer reproduces the
        oracle's order blocks.

        PRODUCTION never constructs one directly. `ManualSMCStrategy` injects
        `FIRST_TOUCH_WINDOW`, the authorized production config and the production
        detector, so there is exactly one place where the production policy is
        chosen and it is the production entry point. The authorized production TP
        is the same 0.60%, so today the two configs differ in no field; the
        injection point is what keeps that a verifiable fact rather than an
        assumption.

        NO RISK/REWARD ADMISSION EXISTS ON THIS PATH. The authorised Manual SMC
        specification admits an order block on its own structural rules — first
        touch, the 25% entry, the opposite-edge stop, the fixed 0.60% target, the
        3-candle window and one trade at a time. A reward:risk ratio is a
        REPORTED CONSEQUENCE of those rules, never an input to them, so no
        threshold is accepted here and no setup can be refused for its ratio.

        `detector` selects the STRUCTURE DETECTOR and nothing else. It defaults
        to `DETECTOR_LUXALGO_INTERNAL`, the LuxAlgo internal-structure
        reconstruction, because that is the authorised production detector: it
        is what the operator's own TradingView chart runs. `DETECTOR_MANUAL_SPEC`
        -- the verbatim oracle extraction -- is still fully supported but must
        now be NAMED, which is exactly how the oracle-equivalence and Phase-1
        lifecycle acceptance gates select it. An unrecognised value raises rather
        than falling back, because a default must never be allowed to answer a
        safety question (safety rule #13).
        """
        if activation_mode not in ACTIVATION_MODES:
            raise ValueError(
                f"unknown activation_mode {activation_mode!r}; expected one of "
                f"{sorted(ACTIVATION_MODES)}. Refusing to guess how an entry "
                f"limit becomes live (safety rule #13)")
        if detector not in DETECTORS:
            raise ValueError(
                f"unknown detector {detector!r}; expected one of "
                f"{sorted(DETECTORS)}. Refusing to guess which algorithm "
                f"decides what an order block is (safety rule #13)")
        if not isinstance(entry_window_candles, int) or isinstance(
                entry_window_candles, bool) or entry_window_candles < 1:
            raise ValueError(
                f"entry_window_candles must be an int >= 1, got "
                f"{entry_window_candles!r}; a zero or negative window would "
                f"cancel every order before it could fill")
        self.cfg: ManualSpecConfig = config or ManualSpecConfig()
        self.activation_mode: str = activation_mode
        self.entry_window_candles: int = entry_window_candles
        self.detector: str = detector
        self.live_obs: Dict[str, ManualOBRecord] = {}
        self.active_trade: Optional[ManualActiveTrade] = None
        self.exits: List[ManualTradeExit] = []
        self._scanners: Dict[str, ManualOBScanner] = {}
        # Operator invalidations (Task N). Empty by default, so a lifecycle
        # built without one behaves EXACTLY as before: every consultation of
        # the ledger is short-circuited on `if not self.invalidations`.
        if invalidations is not None and not isinstance(
                invalidations, ManualOBInvalidationLedger):
            raise TypeError(
                f"invalidations must be a ManualOBInvalidationLedger, got "
                f"{type(invalidations).__name__}; refusing to treat an "
                f"arbitrary container as the authoritative list of order "
                f"blocks that must never trade")
        self.invalidations: ManualOBInvalidationLedger = (
            invalidations if invalidations is not None
            else ManualOBInvalidationLedger())
        # Oracle's original timestamp watermark, retained as a secondary
        # conservative guard (see module docstring). NOT load-bearing for the
        # single-active-trade invariant.
        self._last_trade_closed_dt: Optional[datetime] = None
        for asset in assets or []:
            self._scanner_for(asset)

    # -- introspection ----------------------------------------------------
    def _scanner_for(self, asset: str) -> ManualOBScanner:
        """The per-asset detector instance, built lazily on first use.

        The selected detector is resolved HERE and only here, so both detectors
        receive identical construction arguments from identical config fields.
        An unrecognised `self.detector` cannot reach this point -- the
        constructor rejects it -- but the final `raise` stands rather than an
        `else` fallback, so adding a third detector without wiring it up fails
        closed instead of silently running the manual spec (safety rule #15).
        """
        scanner = self._scanners.get(asset)
        if scanner is None:
            if self.detector == DETECTOR_MANUAL_SPEC:
                scanner = ManualSpecBOSScanner(
                    lookback=self.cfg.lookback, min_width=self.cfg.min_ob_width
                )
            elif self.detector == DETECTOR_LUXALGO_INTERNAL:
                scanner = LuxAlgoInternalOBScanner(
                    lookback=self.cfg.lookback, min_width=self.cfg.min_ob_width
                )
            else:
                raise ValueError(
                    f"unknown detector {self.detector!r}; expected one of "
                    f"{sorted(DETECTORS)}. Refusing to fall back to a default "
                    f"detector (safety rule #13)")
            self._scanners[asset] = scanner
        return scanner

    def candidate_obs(self, asset: Optional[str] = None) -> List[ManualOBRecord]:
        """
        Live OBs not yet filled and not yet dead, in insertion order.

        Operator-invalidated blocks are excluded. `invalidate_ob` already evicts
        the matching OB from `live_obs`, so this filter is normally a no-op; it
        is here because this projection feeds `ManualSMCEvaluation.setups` — the
        list an execution path acts on — and that list must not be able to
        contain a deleted order block under any restore or ordering.
        """
        ledger_active = bool(self.invalidations)
        return [
            ob for ob in self.live_obs.values()
            if (asset is None or ob.asset == asset)
            and ob.state in (ManualOBState.AWAITING_DISPLACEMENT,
                             ManualOBState.LIMIT_RESTING)
            and not (ledger_active and self.invalidations.covers(ob))
        ]

    def has_active_trade(self) -> bool:
        """The corrected global gate, stated positively."""
        return self.active_trade is not None

    def reset(self) -> None:
        """
        Drop all lifecycle state. Test/replay helper — never an operational act.

        The operator invalidation ledger is DELIBERATELY NOT cleared. Clearing
        it would make a replay or a rescan re-create every deleted order block
        as a fresh tradable setup, which is precisely the failure this feature
        exists to prevent. A caller that genuinely wants a virgin lifecycle
        constructs a new one.
        """
        self.live_obs.clear()
        self.active_trade = None
        self.exits.clear()
        self._last_trade_closed_dt = None
        for scanner in self._scanners.values():
            scanner.reset()

    # -- operator invalidation (Task N) ------------------------------------
    def is_operator_invalidated(self, ob: ManualOBRecord) -> bool:
        """Has the operator permanently invalidated this order block?"""
        return self.invalidations.covers(ob)

    def invalidate_ob(
        self,
        ob: Optional[ManualOBRecord] = None,
        *,
        fingerprint: Optional[str] = None,
        asset: Optional[str] = None,
        direction: Optional[str] = None,
        origin_dt: Optional[datetime] = None,
        bos_dt: Optional[datetime] = None,
        requested_at: Optional[datetime] = None,
        requested_by: str = DEFAULT_REQUESTED_BY,
        reason: str = DEFAULT_REASON,
        bar_idx: int = -1,
        events: Optional[List[ManualLifecycleEvent]] = None,
    ) -> "ManualOBInvalidationOutcome":
        """
        Make ONE order block permanently untradable. Idempotent.

        Pass either a live `ManualOBRecord` (what a dashboard row resolves to)
        or the four identity characteristics — `asset`, `direction`,
        `origin_dt`, `bos_dt` — so an order block that has already expired,
        been invalidated automatically, or been swept out of the pool can still
        be recorded (requirement A9). A `fingerprint` alone is NOT accepted for
        a NEW record: the characteristics are what make the key verifiable, and
        a bare key could not be checked against the block it claims to name.

        Effects, in order:
          1. the decision is recorded in the ledger (idempotently);
          2. a matching AWAITING_DISPLACEMENT / LIMIT_RESTING OB is moved to
             INVALIDATED and evicted from the live pool immediately, so it is
             untradable from this instant rather than from the next candle;
          3. an INVALIDATED event is emitted when a live OB was evicted, which
             is the SAME event the runtime's `cancel_expired_entries` already
             keys on — so a real resting exchange order behind that setup is
             cancelled through the existing path, with no new wiring.

        A TRADE_ACTIVE order block is NOT touched. Its position is in the
        market and abandoning it here would strand it without a stop
        (safety rule #14 — never release the slot before the position is
        proven closed). The decision is still recorded, so the block can never
        be entered again after that trade settles, and the outcome reports
        `live_trade_untouched=True` rather than pretending the position was
        dealt with.
        """
        if ob is None and fingerprint is not None and asset is None:
            existing = self.invalidations.get(fingerprint)
            if existing is None:
                raise ValueError(
                    f"fingerprint {fingerprint!r} is not already in the ledger "
                    f"and a new record cannot be built from a key alone; pass "
                    f"the order block, or its asset/direction/origin_dt/bos_dt")
            return self._apply_invalidation(existing, False, bar_idx, events)

        if ob is not None:
            record = record_for_ob(
                ob,
                requested_at=requested_at or ob.bos_dt,
                requested_by=requested_by,
                reason=reason)
        else:
            if requested_at is None:
                raise ValueError(
                    "requested_at is required when invalidating by "
                    "characteristics; refusing to stamp an operator decision "
                    "with a guessed time")
            record = build_record(
                asset=asset, direction=direction, origin_dt=origin_dt,
                bos_dt=bos_dt, requested_at=requested_at,
                requested_by=requested_by, reason=reason)

        stored, created = self.invalidations.add(record)
        return self._apply_invalidation(stored, created, bar_idx, events)

    def _apply_invalidation(
        self,
        record: ManualOBInvalidation,
        created: bool,
        bar_idx: int,
        events: Optional[List[ManualLifecycleEvent]],
    ) -> "ManualOBInvalidationOutcome":
        """Evict the matching live OB, if there is one. See `invalidate_ob`."""
        sink: List[ManualLifecycleEvent] = [] if events is None else events
        live_ob: Optional[ManualOBRecord] = None
        for candidate in self.live_obs.values():
            if fingerprint_for_ob(candidate) == record.fingerprint:
                live_ob = candidate
                break

        if live_ob is None:
            return ManualOBInvalidationOutcome(
                record=record, created=created, evicted_ob_id=None,
                live_trade_untouched=False)

        if live_ob.state == ManualOBState.TRADE_ACTIVE:
            return ManualOBInvalidationOutcome(
                record=record, created=created, evicted_ob_id=None,
                live_trade_untouched=True)

        live_ob.state = ManualOBState.INVALIDATED
        self.live_obs.pop(live_ob.ob_id, None)
        self._emit(sink, ManualLifecycleEventType.INVALIDATED, live_ob,
                   bar_idx, record.requested_at,
                   f"operator invalidation by {record.requested_by} "
                   f"({record.reason}); fingerprint {record.fingerprint}; "
                   f"this order block is permanently untradable")
        return ManualOBInvalidationOutcome(
            record=record, created=created, evicted_ob_id=live_ob.ob_id,
            live_trade_untouched=False)

    # -- the single admission gate ----------------------------------------
    def _entry_blocked(self, ts: datetime) -> Optional[str]:
        """
        Return a rejection reason, or None if a new entry may be admitted.

        (a) is the CORRECTED rule the oracle lacked; (b) is the oracle's own
        same-timestamp watermark, retained.
        """
        if self.active_trade is not None:
            return (
                f"active trade already open on {self.active_trade.asset} "
                f"(filled {self.active_trade.fill_dt.isoformat()})"
            )
        if self._last_trade_closed_dt is not None and ts <= self._last_trade_closed_dt:
            return (
                "a trade closed at or after this candle timestamp; "
                "intra-candle re-entry ordering is not determinable"
            )
        return None

    # -- main entry point -------------------------------------------------
    def process_candle(
        self,
        asset: str,
        bar_idx: int,
        ts: datetime,
        o: float,
        h: float,
        l: float,
        c: float,
    ) -> List[ManualLifecycleEvent]:
        """
        Process ONE closed candle. `bar_idx` is the asset's own monotonically
        increasing bar index (comparisons against `limit_active_from_bar` are
        always within a single asset).

        Steps run in oracle order and must not be reordered.
        """
        events: List[ManualLifecycleEvent] = []
        self._step1_resolve_active_trade(asset, bar_idx, ts, c, h, l, events)
        self._step2_update_obs(asset, bar_idx, ts, c, h, l, events)
        self._step3_scan_and_admit(asset, bar_idx, ts, o, h, l, c, events)
        return events

    # -- the EXECUTION CLOCK: one live market update -----------------------
    def process_market_update(
        self,
        asset: str,
        bar_idx: int,
        ts: datetime,
        price: float,
        high: Optional[float] = None,
        low: Optional[float] = None,
    ) -> List[ManualLifecycleEvent]:
        """Process ONE live market update inside the currently-forming candle.

        THE TWO CLOCKS. `process_candle` is the STRUCTURE clock: it runs steps
        1-3, and step 3 is the only step that creates order blocks, so BOS /
        CHoCH / OB geometry stay on closed 1h candles exactly as before. This
        method is the EXECUTION clock: it runs steps 1 and 2 ONLY. Those two
        steps read nothing but `(bar_idx, ts, close, high, low)` and the live OB
        pool, and every predicate they use -- `_manual_zone_touched`,
        `_manual_entry_touched`, `_manual_distal_breached`, `_manual_tp_hit`,
        `_manual_sl_hit` -- is a pure function of a (high, low) pair. A single
        live price IS the degenerate pair `high == low == price`, so the
        specification's own predicates apply to a tick without being restated,
        reinterpreted or relaxed. NOTHING here creates an order block.

        `high`/`low` default to `price`. A feed that pushes the forming candle's
        running extremes (Delta India's `candlestick_1h` frames do) may supply
        them, which covers the price path BETWEEN two pushed updates instead of
        only the instants the feed happened to publish. They are extremes of the
        interval that ENDS at `ts`, never a forecast.

        THE RANGE IS FOR ORDER BLOCKS, NEVER FOR EXITS. A forming frame's high
        and low are the extremes of the WHOLE hour so far, so an extreme it
        reports may have occurred BEFORE an entry that filled later in the same
        hour. Resolving a stop from it would invent an exit that never happened
        after the fill. Step 1 therefore sees the instantaneous price ONLY -- the
        degenerate `(price, price)` pair -- while step 2, whose predicates are
        monotone in the extremes and whose windows are counted in bars, sees the
        reported range. The consequence is stated plainly: a stop or target that
        is visible only in a frame's range and never in a published price is
        resolved by the CLOSED candle at the end of the hour, exactly as it is
        today. The execution clock can bring an exit FORWARD; it can never
        manufacture one.

        WHAT THIS CLOCK DELIBERATELY DOES NOT DO, and why:

          * it never creates or admits an order block (step 3 is not called), so
            structure remains a closed-candle-only concept;
          * it never resolves the `max_holding_bars` horizon -- that exit is
            triggered by TIME, not price, and firing it mid-hour would change
            the exit price of an authorised rule. Time-triggered exits stay on
            the candle clock;
          * it never expires the entry window and never invalidates an OB for
            expiry. It only refuses to FILL outside the window. Removing the OB
            here would race the closed candle that is still entitled to fill it,
            and a race must not decide whether a trade exists (rule #15).

        Everything it DOES do is price-triggered and therefore belongs on the
        clock the price actually arrives on: first touch of the zone, the 25%
        entry fill, the distal-breach invalidation, and TP/SL on an open trade.
        """
        events: List[ManualLifecycleEvent] = []
        if self.activation_mode != ACTIVATION_MODE_FIRST_TOUCH:
            raise LifecycleClockError(
                f"the execution clock is defined only for activation mode "
                f"{ACTIVATION_MODE_FIRST_TOUCH!r}; this lifecycle runs "
                f"{self.activation_mode!r}, whose displacement gate is decided "
                f"by candle CLOSES. Refusing to reinterpret a live price as a "
                f"close (safety rule #15).")
        h = price if high is None else high
        l = price if low is None else low
        if h < l:
            raise LifecycleClockError(
                f"{asset}: live update at {ts.isoformat()} reports high {h} "
                f"below low {l}")
        if not (l <= price <= h):
            raise LifecycleClockError(
                f"{asset}: live update at {ts.isoformat()} reports price "
                f"{price} outside its own range [{l}, {h}]")
        # Step 1 gets the INSTANT only; step 2 gets the reported range. See the
        # docstring: a frame's extremes can predate a fill made later in the
        # same hour, and an exit must never be resolved from such a price.
        self._step1_resolve_active_trade(asset, bar_idx, ts, price, price, price,
                                         events, intrabar=True)
        self._step2_update_obs(asset, bar_idx, ts, price, h, l, events,
                               intrabar=True)
        return events

    # -- step 1: active-trade exit resolution -----------------------------
    @staticmethod
    def _exit_predates_fill(
        at: ManualActiveTrade, bar_idx: int, ts: datetime, intrabar: bool
    ) -> bool:
        """True when this price information is not allowed to resolve the exit.

        THE INVARIANT, stated once: an exit may only be resolved from price
        information that is strictly LATER than the fill. The pure closed-candle
        engine gets this for free from step ordering -- a fill happens in step 2
        of bar F, so step 1 first sees the trade on bar F+1 -- and the two
        clocks together must not lose it.

        On the CANDLE clock the unit of price information is a whole bar, whose
        high and low span `[B*3600, (B+1)*3600)`. That interval contains or
        precedes the fill iff `B <= F`, so those bars are refused. THIS CANNOT
        FIRE IN AN OFFLINE REPLAY: `process_candle` runs step 1 before step 2,
        so no trade with `fill_bar_idx == bar_idx` exists when step 1 runs, and
        bar indices strictly increase, so `bar_idx < fill_bar_idx` cannot occur
        either. The guard is therefore provably behaviour-neutral for every
        backtest and every historical replay -- it exists solely so that a
        closed candle arriving AFTER a live intrabar fill inside that same hour
        cannot resolve the exit from pre-fill prices.

        On the EXECUTION clock the unit is a single instant, so the test is the
        timestamp itself: `ts <= fill_dt` is refused, everything after it
        resolves normally. This is what keeps TP/SL monitoring live for the rest
        of the fill's own hour instead of blacking it out.
        """
        if intrabar:
            return ts <= at.fill_dt
        return bar_idx <= at.fill_bar_idx

    def _step1_resolve_active_trade(
        self,
        asset: str,
        bar_idx: int,
        ts: datetime,
        c_c: float,
        c_h: float,
        c_l: float,
        events: List[ManualLifecycleEvent],
        intrabar: bool = False,
    ) -> None:
        at = self.active_trade
        if at is None or at.asset != asset:
            return
        if self._exit_predates_fill(at, bar_idx, ts, intrabar):
            return

        hit_tp = _manual_tp_hit(at.direction, c_h, c_l, at.tp_price)
        hit_sl = _manual_sl_hit(at.direction, c_h, c_l, at.sl_price)

        if hit_tp or hit_sl:
            if hit_tp and hit_sl:
                # Conservative: SL first (same-candle ambiguity)
                outcome, reason, exit_p, ambiguous = (
                    OUTCOME_SL, REASON_DUAL_TOUCH, at.sl_price, True)
                narrative = ("Dual-touch: both TP and SL hit same candle. "
                             "Conservative SL-first applied.")
            elif hit_tp:
                outcome, reason, exit_p, ambiguous = (
                    OUTCOME_TP, REASON_TP_HIT, at.tp_price, False)
                narrative = (f"Fixed +{self.cfg.fixed_tp_market_pct:.2f}% TP "
                             f"reached at {at.tp_price:.6f}.")
            else:
                outcome, reason, exit_p, ambiguous = (
                    OUTCOME_SL, REASON_SL_HIT, at.sl_price, False)
                narrative = f"SL breached at {at.sl_price:.6f}."

            if outcome == OUTCOME_TP:
                realized_r = (at.reward_dist / at.risk_dist
                              if at.risk_dist > 1e-9 else 0.0)
            else:
                realized_r = -1.0

            self._close_trade(at, bar_idx, ts, exit_p, outcome, reason,
                              ambiguous, realized_r, narrative, events)
            return

        # The holding horizon is triggered by TIME, not by price, so it belongs
        # to the candle clock alone. Resolving it on a live update would close
        # the trade at a mid-hour price instead of the candle close the
        # authorised rule names, which would be a rule change, not a latency
        # improvement. See `process_market_update`.
        if intrabar:
            return

        # No TP/SL this candle — post-fill holding-horizon check only.
        bars_held = int((ts - at.fill_dt).total_seconds() / 3600)
        if bars_held >= self.cfg.max_holding_bars:
            p_diff = ((c_c - at.entry_price) if at.direction == "LONG"
                      else (at.entry_price - c_c))
            realized_r = p_diff / at.risk_dist if at.risk_dist > 1e-9 else 0.0
            narrative = (f"{self.cfg.max_holding_bars}h horizon expired. "
                         f"Closed at {c_c:.6f}.")
            self._close_trade(at, bar_idx, ts, c_c, OUTCOME_TIMEOUT,
                              REASON_TIMEOUT, False, realized_r, narrative,
                              events)

    def _close_trade(
        self,
        at: ManualActiveTrade,
        bar_idx: int,
        ts: datetime,
        exit_price: float,
        outcome: str,
        reason: str,
        is_ambiguous: bool,
        realized_r: float,
        narrative: str,
        events: List[ManualLifecycleEvent],
    ) -> None:
        holding_hrs = max(1.0, (ts - at.fill_dt).total_seconds() / 3600.0)
        ob = at.ob
        ob.state = ManualOBState.TRADE_CLOSED

        self.exits.append(ManualTradeExit(
            asset=at.asset, direction=at.direction, ob_id=ob.ob_id,
            origin_bar_idx=ob.origin_bar_idx, bos_bar_idx=ob.bos_bar_idx,
            entry_price=at.entry_price, exit_price=exit_price,
            sl_price=at.sl_price, tp_price=at.tp_price,
            fill_dt=at.fill_dt, exit_dt=ts,
            fill_bar_idx=at.fill_bar_idx, exit_bar_idx=bar_idx,
            outcome=outcome, reason_for_exit=reason, is_ambiguous=is_ambiguous,
            realized_r=realized_r,
            risk_dist=at.risk_dist, reward_dist=at.reward_dist,
            holding_bars=int(holding_hrs), holding_time_hours=holding_hrs,
            applied_leverage=at.applied_leverage,
            theoretical_leverage=at.theoretical_leverage,
            sl_dist_pct=ob.sl_dist_pct,
            entry_bar_from_bos=ob.entry_bar_from_bos,
            ob_age_at_entry_hours=ob.ob_age_at_entry_hours,
            pre_displacement_touches=ob.pre_displacement_touches,
            retest_number=ob.retest_number,
            mfe_from_proximal=ob.mfe_from_proximal,
            displacement_mode=self.activation_mode,
            data_timeframe=self.cfg.data_timeframe,
            narrative=narrative,
        ))
        events.append(ManualLifecycleEvent(
            event_type=ManualLifecycleEventType.TRADE_CLOSED,
            asset=at.asset, bar_idx=bar_idx, ts=ts, ob_id=ob.ob_id,
            direction=at.direction, state=ob.state,
            detail=f"{outcome} ({reason}) @ {exit_price:.6f} r={realized_r:.4f}",
        ))
        self._last_trade_closed_dt = ts
        self.active_trade = None
        self.live_obs.pop(ob.ob_id, None)


    # -- step 2: live-OB update -------------------------------------------
    def _emit(
        self,
        events: List[ManualLifecycleEvent],
        event_type: ManualLifecycleEventType,
        ob: ManualOBRecord,
        bar_idx: int,
        ts: datetime,
        detail: str = "",
    ) -> None:
        events.append(ManualLifecycleEvent(
            event_type=event_type, asset=ob.asset, bar_idx=bar_idx, ts=ts,
            ob_id=ob.ob_id, direction=ob.direction, state=ob.state,
            detail=detail,
        ))

    def _step2_update_obs(
        self,
        asset: str,
        bar_idx: int,
        ts: datetime,
        c_c: float,
        c_h: float,
        c_l: float,
        events: List[ManualLifecycleEvent],
        intrabar: bool = False,
    ) -> None:
        """
        Update every live OB for `asset` (oracle L1841–1957).

        Iteration is over a snapshot of the pool; dead OBs are collected and
        popped only after the sweep, exactly as the oracle does.

        The operator-invalidation check runs FIRST for each OB, before the MFE
        update and before any state branch, so a deleted order block can never
        take another step of its lifecycle. `invalidate_ob` already evicts a
        matching live OB the instant the decision is made; this is the guard
        that also holds for a ledger restored from disk beside a pool captured
        before it, and it makes "an invalidated OB never advances" a property
        of the sweep itself rather than of the eviction's timing.
        """
        obs_to_remove: List[str] = []
        ledger_active = bool(self.invalidations)

        for ob_id, ob in list(self.live_obs.items()):
            if ob.asset != asset:
                continue
            if ob.state in (ManualOBState.TRADE_CLOSED,
                            ManualOBState.INVALIDATED):
                obs_to_remove.append(ob_id)
                continue
            if ob.state == ManualOBState.TRADE_ACTIVE:
                continue                      # resolved by step 1, never here

            if ledger_active and self.invalidations.covers(ob):
                ob.state = ManualOBState.INVALIDATED
                self._emit(events, ManualLifecycleEventType.INVALIDATED, ob,
                           bar_idx, ts,
                           "operator-invalidated order block; permanently "
                           "untradable")
                obs_to_remove.append(ob_id)
                continue

            # MFE from proximal, tracked pre-entry for both live states.
            mfe_this = (max(0.0, ob.proximal - c_l) if ob.direction == "SHORT"
                        else max(0.0, c_h - ob.proximal))
            if mfe_this > ob.mfe_from_proximal:
                ob.mfe_from_proximal = mfe_this

            if ob.state == ManualOBState.AWAITING_DISPLACEMENT:
                dead = self._update_awaiting(ob, bar_idx, ts, c_c, c_h, c_l,
                                             events, intrabar=intrabar)
            elif ob.state == ManualOBState.LIMIT_RESTING:
                dead = self._update_resting(ob, bar_idx, ts, c_h, c_l, events,
                                            intrabar=intrabar)
            else:
                dead = False

            if dead:
                obs_to_remove.append(ob_id)

        for ob_id in obs_to_remove:
            self.live_obs.pop(ob_id, None)


    def _update_awaiting(
        self,
        ob: ManualOBRecord,
        bar_idx: int,
        ts: datetime,
        c_c: float,
        c_h: float,
        c_l: float,
        events: List[ManualLifecycleEvent],
        intrabar: bool = False,
    ) -> bool:
        """
        AWAITING_DISPLACEMENT branch. Returns True if the OB died this candle.

        The state name is the oracle's and is preserved for record-structure
        equivalence. Under `ACTIVATION_MODE_FIRST_TOUCH` it means simply
        "ACTIVE, waiting for the first touch" — and it waits indefinitely:
        nothing in this branch can invalidate an OB because time passed.
        """
        # (a) wick distal breach kills the setup before any entry. Checked first
        #     in both modes: a candle that reaches the far edge has crossed the
        #     whole block, and refusing the trade is the conservative direction.
        if _manual_distal_breached(ob, c_h, c_l):
            ob.state = ManualOBState.INVALIDATED
            self._emit(events, ManualLifecycleEventType.INVALIDATED, ob,
                       bar_idx, ts, "distal wick breach before displacement")
            return True

        if self.activation_mode == ACTIVATION_MODE_FIRST_TOUCH:
            return self._awaiting_first_touch(ob, bar_idx, ts, c_h, c_l, events,
                                              intrabar=intrabar)
        # Mode C is CLOSE-based over two distinct candles, so it has no meaning
        # for a single live price. `process_market_update` refuses the mode
        # outright; this assertion is the second lock on the same door.
        if intrabar:
            raise LifecycleClockError(
                f"{ob.asset}: the execution clock is defined only for "
                f"{ACTIVATION_MODE_FIRST_TOUCH!r}; {self.activation_mode!r} "
                f"resolves displacement from candle CLOSES and a live update "
                f"is not a close")
        return self._awaiting_oracle_mode_c(
            ob, bar_idx, ts, c_c, c_h, c_l, events)

    def _awaiting_first_touch(
        self,
        ob: ManualOBRecord,
        bar_idx: int,
        ts: datetime,
        c_h: float,
        c_l: float,
        events: List[ManualLifecycleEvent],
        intrabar: bool = False,
    ) -> bool:
        """
        Manual specification: the FIRST TOUCH of the zone arms the 25% limit.

        A touch is `high >= proximal` (SHORT) / `low <= proximal` (LONG) — wick
        or body, edge inclusive, no penetration-depth requirement. The limit
        becomes live on the first-touch candle ITSELF (`limit_active_from_bar =
        bar_idx`, not `bar_idx + 1`), so an entry reached on that same candle
        fills normally. That is why this method ends by delegating to
        `_update_resting` for the very same candle.
        """
        if not _manual_zone_touched(ob, c_h, c_l):
            return False            # untouched: still ACTIVE, indefinitely

        ob.pre_displacement_touches += 1
        if ob.first_touch_dt is None:
            ob.first_touch_dt = ts
        ob.state = ManualOBState.LIMIT_RESTING
        ob.displacement_confirmed_dt = ts
        ob.displacement_confirmed_bar = bar_idx
        ob.limit_active_from_bar = bar_idx
        last_bar = bar_idx + self.entry_window_candles - 1
        self._emit(events,
                   ManualLifecycleEventType.FIRST_TOUCH_LIMIT_ACTIVATED, ob,
                   bar_idx, ts,
                   f"first zone touch at proximal {ob.proximal:.6f}; "
                   f"{ob.entry_price:.6f} limit live for bars "
                   f"{bar_idx}..{last_bar} "
                   f"({self.entry_window_candles} candles, inclusive)")
        # The activating candle may itself reach the entry.
        return self._update_resting(ob, bar_idx, ts, c_h, c_l, events,
                                    intrabar=intrabar)

    def _awaiting_oracle_mode_c(
        self,
        ob: ManualOBRecord,
        bar_idx: int,
        ts: datetime,
        c_c: float,
        c_h: float,
        c_l: float,
        events: List[ManualLifecycleEvent],
    ) -> bool:
        """
        The oracle's Mode C gate, unchanged.

        Mode C displacement is CLOSE-based and needs two distinct candles: a
        probe close beyond the proximal, then a pullback close back through it.
        A single candle can therefore never both confirm the probe and confirm
        displacement — the `if not probe_confirmed / else` split is the oracle's
        and is load-bearing.
        """
        # (b) pre-displacement touches are counted but never fill.
        if _manual_entry_touched(ob, c_h, c_l):
            ob.pre_displacement_touches += 1
            if ob.first_touch_dt is None:
                ob.first_touch_dt = ts
            self._emit(events, ManualLifecycleEventType.PRE_DISPLACEMENT_TOUCH,
                       ob, bar_idx, ts,
                       f"touch #{ob.pre_displacement_touches} "
                       f"(no limit active yet)")

        # (c) Mode C probe -> pullback, close-based.
        if not ob.probe_confirmed:
            if ob.direction == "SHORT" and c_c > ob.proximal:
                ob.probe_confirmed = True
            elif ob.direction == "LONG" and c_c < ob.proximal:
                ob.probe_confirmed = True
            if ob.probe_confirmed:
                self._emit(events, ManualLifecycleEventType.PROBE_CONFIRMED,
                           ob, bar_idx, ts, f"probe close {c_c:.6f} beyond "
                           f"proximal {ob.proximal:.6f}")
        else:
            displaced = ((ob.direction == "SHORT" and c_c < ob.proximal)
                         or (ob.direction == "LONG" and c_c > ob.proximal))
            if displaced:
                ob.state = ManualOBState.LIMIT_RESTING
                ob.displacement_confirmed_dt = ts
                ob.displacement_confirmed_bar = bar_idx
                ob.limit_active_from_bar = bar_idx + 1
                self._emit(events,
                           ManualLifecycleEventType.DISPLACEMENT_CONFIRMED, ob,
                           bar_idx, ts,
                           f"limit active from bar {ob.limit_active_from_bar} "
                           f"(displacement candle cannot fill)")
        return False

    def _window_last_bar(self, ob: ManualOBRecord) -> Optional[int]:
        """
        Last bar index on which this OB's resting limit may still fill.

        `None` means "no window applies": either the oracle activation mode is
        in force (no expiry at all) or the limit has no activation bar yet.

        The convention, stated once and relied on everywhere: the window is the
        `entry_window_candles` bars
        `[limit_active_from_bar, limit_active_from_bar + entry_window_candles - 1]`
        INCLUSIVE, so with the specification's 3 it is the first-touch candle
        plus the next two.
        """
        if self.activation_mode != ACTIVATION_MODE_FIRST_TOUCH:
            return None
        if ob.limit_active_from_bar is None:
            return None
        return ob.limit_active_from_bar + self.entry_window_candles - 1

    def _update_resting(
        self,
        ob: ManualOBRecord,
        bar_idx: int,
        ts: datetime,
        c_h: float,
        c_l: float,
        events: List[ManualLifecycleEvent],
        intrabar: bool = False,
    ) -> bool:
        """
        LIMIT_RESTING branch. Returns True if the OB died this candle.

        Exits: entry fill, distal wick breach, entry-window expiry (first-touch
        mode only), or external operational cancellation. A distal breach is
        checked before expiry, so on a candle that does both the reported reason
        is the breach; the outcome — INVALIDATED and out of the pool — is the
        same either way.

        Expiry is checked BEFORE the entry touch, so a limit can never fill on a
        candle after its window closed, and never expires on a candle where it
        could still legitimately fill.

        The expiry message says "without an admitted fill" rather than "without
        the entry being reached" because the two are not the same: an entry
        reached inside the window but refused by the single-trade slot leaves the
        OB resting, and its window still closes on schedule. That is the
        fail-closed direction (one fewer trade, never an unslotted one), and the
        specification does not cover the case — see the report accompanying this
        change.
        """
        if _manual_distal_breached(ob, c_h, c_l):
            ob.state = ManualOBState.INVALIDATED
            self._emit(events, ManualLifecycleEventType.INVALIDATED, ob,
                       bar_idx, ts, "distal wick breach while limit resting")
            return True

        last_bar = self._window_last_bar(ob)
        if last_bar is not None and bar_idx > last_bar:
            # THE EXECUTION CLOCK REFUSES THE FILL BUT NOT THE ORDER BLOCK.
            # Expiry is a bar-count decision, and the closed candle for
            # `last_bar` may still be in flight behind this live update. Killing
            # the OB here would let transport timing decide whether a legitimate
            # closed-candle fill ever happens, so the live clock only enforces
            # the half of the rule that is unambiguously safe: no fill after the
            # window. The candle clock below still expires it, exactly once, at
            # exactly the same bar it always did.
            if intrabar:
                return False
            ob.state = ManualOBState.INVALIDATED
            self._emit(events, ManualLifecycleEventType.INVALIDATED, ob,
                       bar_idx, ts,
                       f"{self.entry_window_candles}-candle entry window "
                       f"[{ob.limit_active_from_bar}..{last_bar}] expired "
                       f"without an admitted fill at {ob.entry_price:.6f}; "
                       f"order cancelled and this OB is permanently invalid")
            return True

        if ob.limit_active_from_bar is None or bar_idx < ob.limit_active_from_bar:
            return False
        if not _manual_entry_touched(ob, c_h, c_l):
            return False

        # --- operator invalidation: the last gate before a trade exists ----
        # Structurally unreachable today (`_step2_update_obs` evicts a covered
        # OB before this branch can run, and `invalidate_ob` evicts it the
        # instant the decision is taken). Kept anyway so "an operator-deleted
        # order block can never become a trade" is provable HERE, at the single
        # line that creates the trade, and does not depend on the ordering of
        # two other functions staying correct.
        if self.invalidations and self.invalidations.covers(ob):
            ob.state = ManualOBState.INVALIDATED
            self._emit(events, ManualLifecycleEventType.INVALIDATED, ob,
                       bar_idx, ts,
                       "operator-invalidated order block reached its entry; "
                       "fill refused and the order block is permanently "
                       "untradable")
            return True

        # --- the single admission gate (corrected; see module docstring) ---
        rejection = self._entry_blocked(ts)
        if rejection is not None:
            self._emit(events,
                       ManualLifecycleEventType.ENTRY_BLOCKED_BY_ACTIVE_TRADE,
                       ob, bar_idx, ts, rejection)
            return False                       # stays LIMIT_RESTING, still live

        # NOTHING ELSE MAY REFUSE THIS FILL. The slot check above is the single
        # admission gate. In particular there is NO reward:risk test here: the
        # authorised specification does not contain one, so a setup whose ratio
        # is 0.11 fills on exactly the same terms as one whose ratio is 3.0. The
        # two distances computed below are recorded for reporting only.
        ob.state = ManualOBState.TRADE_ACTIVE
        ob.retest_number += 1
        ob.entry_bar_from_bos = bar_idx - ob.bos_bar_idx
        ob.ob_age_at_entry_hours = (ts - ob.bos_dt).total_seconds() / 3600

        risk_dist = abs(ob.entry_price - ob.sl_price)
        reward_dist = abs(ob.tp_price - ob.entry_price)
        self.active_trade = ManualActiveTrade(
            ob=ob, asset=ob.asset, direction=ob.direction,
            entry_price=ob.entry_price, sl_price=ob.sl_price,
            tp_price=ob.tp_price,
            applied_leverage=ob.applied_leverage,
            theoretical_leverage=ob.theoretical_leverage,
            risk_dist=risk_dist, reward_dist=reward_dist,
            fill_dt=ts, retest_dt=ts, fill_bar_idx=bar_idx,
        )
        self._emit(events, ManualLifecycleEventType.ENTRY_FILLED, ob, bar_idx,
                   ts, f"limit filled @ {ob.entry_price:.6f} "
                       f"sl={ob.sl_price:.6f} tp={ob.tp_price:.6f} "
                       f"retest #{ob.retest_number}")
        return False

    # -- step 3: BOS scan and admission (MUST stay last) ------------------
    def _step3_scan_and_admit(
        self,
        asset: str,
        bar_idx: int,
        ts: datetime,
        o: float,
        h: float,
        l: float,
        c: float,
        events: List[ManualLifecycleEvent],
    ) -> None:
        """
        Run the BOS scanner and admit new OBs (oracle L1959–1967).

        Runs AFTER step 2 so a new OB cannot use its own BOS candle as a
        displacement candle — this is what makes admission break+1. The
        scanner's SHORT-before-LONG emission order is preserved verbatim.

        An order block the operator has invalidated is NOT admitted. This is
        the gate that makes the decision survive a RESCAN: `ManualSpecBOSScanner`
        dedups on `(asset, origin_bar_idx)` in an in-memory set, so a fresh
        process replaying the same candles re-creates the identical order block
        with no memory that it was ever deleted. The ledger's fingerprint is
        built from timestamps rather than bar indices precisely so it still
        matches here (requirement A6).
        """
        new_obs = self._scanner_for(asset).scan(
            asset, bar_idx, ts, o, h, l, c, self.cfg)
        ledger_active = bool(self.invalidations)
        for ob in new_obs:
            if ledger_active and self.invalidations.covers(ob):
                ob.state = ManualOBState.INVALIDATED
                self._emit(events,
                           ManualLifecycleEventType.OB_CREATION_SUPPRESSED, ob,
                           bar_idx, ts,
                           f"BOS bar {ob.bos_bar_idx} origin "
                           f"{ob.origin_bar_idx} matches an operator "
                           f"invalidation; not admitted and never tradable")
                continue
            self.live_obs[ob.ob_id] = ob
            self._emit(events, ManualLifecycleEventType.OB_CREATED, ob,
                       bar_idx, ts,
                       f"BOS bar {ob.bos_bar_idx} origin {ob.origin_bar_idx} "
                       f"proximal={ob.proximal:.6f} distal={ob.distal:.6f}")


__all__ = [
    "OUTCOME_TP",
    "OUTCOME_SL",
    "OUTCOME_TIMEOUT",
    "REASON_TP_HIT",
    "REASON_SL_HIT",
    "REASON_DUAL_TOUCH",
    "REASON_TIMEOUT",
    "DISPLACEMENT_MODE",
    "ACTIVATION_MODE_FIRST_TOUCH",
    "ACTIVATION_MODE_ORACLE_C",
    "ACTIVATION_MODES",
    "ENTRY_WINDOW_CANDLES",
    "DETECTOR_MANUAL_SPEC",
    "DETECTOR_LUXALGO_INTERNAL",
    "DETECTORS",
    "LifecycleClockError",
    "ManualLifecycleEventType",
    "ManualLifecycleEvent",
    "ManualActiveTrade",
    "ManualTradeExit",
    "ManualOBInvalidationOutcome",
    "ManualSMCLifecycle",
]






