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
`ManualSMCLifecycle()` must keep reproducing the frozen oracle, so production
policy is injected at the production entry point (`ManualSMCStrategy`) and
nowhere else.

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
from quantedge.strategy.manual_smc.scanner import ManualSpecBOSScanner

# Outcome / exit-reason string constants (oracle-identical values).
OUTCOME_TP = "FILLED_TP"
OUTCOME_SL = "FILLED_SL"
OUTCOME_TIMEOUT = "FILLED_TIMEOUT"

REASON_TP_HIT = "TP_HIT"
REASON_SL_HIT = "SL_HIT"
REASON_DUAL_TOUCH = "DUAL_TOUCH_CONSERVATIVE_SL"
REASON_TIMEOUT = "TIMEOUT"

DISPLACEMENT_MODE = "C_PROBE_PULLBACK"

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
    ) -> None:
        """
        A BARE `ManualSMCLifecycle()` IS THE RESEARCH ENGINE, NOT PRODUCTION.

        Its defaults reproduce the frozen oracle exactly — `C_PROBE_PULLBACK`
        activation and `ManualSpecConfig()`'s 0.60% take profit — for the same
        reason `manual_smc_production_config()` exists separately: the extraction
        is only provable against the oracle while the bare object still behaves
        like it.

        PRODUCTION never constructs one directly. `ManualSMCStrategy` injects
        `FIRST_TOUCH_WINDOW` and the authorized production config, so there is
        exactly one place where the production policy is chosen and it is the
        production entry point. The authorized production TP is the same 0.60%,
        so today the two configs differ in no field; the injection point is what
        keeps that a verifiable fact rather than an assumption.
        """
        if activation_mode not in ACTIVATION_MODES:
            raise ValueError(
                f"unknown activation_mode {activation_mode!r}; expected one of "
                f"{sorted(ACTIVATION_MODES)}. Refusing to guess how an entry "
                f"limit becomes live (safety rule #13)")
        if not isinstance(entry_window_candles, int) or isinstance(
                entry_window_candles, bool) or entry_window_candles < 1:
            raise ValueError(
                f"entry_window_candles must be an int >= 1, got "
                f"{entry_window_candles!r}; a zero or negative window would "
                f"cancel every order before it could fill")
        self.cfg: ManualSpecConfig = config or ManualSpecConfig()
        self.activation_mode: str = activation_mode
        self.entry_window_candles: int = entry_window_candles
        self.live_obs: Dict[str, ManualOBRecord] = {}
        self.active_trade: Optional[ManualActiveTrade] = None
        self.exits: List[ManualTradeExit] = []
        self._scanners: Dict[str, ManualSpecBOSScanner] = {}
        # Oracle's original timestamp watermark, retained as a secondary
        # conservative guard (see module docstring). NOT load-bearing for the
        # single-active-trade invariant.
        self._last_trade_closed_dt: Optional[datetime] = None
        for asset in assets or []:
            self._scanner_for(asset)

    # -- introspection ----------------------------------------------------
    def _scanner_for(self, asset: str) -> ManualSpecBOSScanner:
        scanner = self._scanners.get(asset)
        if scanner is None:
            scanner = ManualSpecBOSScanner(
                lookback=self.cfg.lookback, min_width=self.cfg.min_ob_width
            )
            self._scanners[asset] = scanner
        return scanner

    def candidate_obs(self, asset: Optional[str] = None) -> List[ManualOBRecord]:
        """Live OBs not yet filled and not yet dead, in insertion order."""
        return [
            ob for ob in self.live_obs.values()
            if (asset is None or ob.asset == asset)
            and ob.state in (ManualOBState.AWAITING_DISPLACEMENT,
                             ManualOBState.LIMIT_RESTING)
        ]

    def has_active_trade(self) -> bool:
        """The corrected global gate, stated positively."""
        return self.active_trade is not None

    def reset(self) -> None:
        self.live_obs.clear()
        self.active_trade = None
        self.exits.clear()
        self._last_trade_closed_dt = None
        for scanner in self._scanners.values():
            scanner.reset()

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

    # -- step 1: active-trade exit resolution -----------------------------
    def _step1_resolve_active_trade(
        self,
        asset: str,
        bar_idx: int,
        ts: datetime,
        c_c: float,
        c_h: float,
        c_l: float,
        events: List[ManualLifecycleEvent],
    ) -> None:
        at = self.active_trade
        if at is None or at.asset != asset:
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
    ) -> None:
        """
        Update every live OB for `asset` (oracle L1841–1957).

        Iteration is over a snapshot of the pool; dead OBs are collected and
        popped only after the sweep, exactly as the oracle does.
        """
        obs_to_remove: List[str] = []

        for ob_id, ob in list(self.live_obs.items()):
            if ob.asset != asset:
                continue
            if ob.state in (ManualOBState.TRADE_CLOSED,
                            ManualOBState.INVALIDATED):
                obs_to_remove.append(ob_id)
                continue
            if ob.state == ManualOBState.TRADE_ACTIVE:
                continue                      # resolved by step 1, never here

            # MFE from proximal, tracked pre-entry for both live states.
            mfe_this = (max(0.0, ob.proximal - c_l) if ob.direction == "SHORT"
                        else max(0.0, c_h - ob.proximal))
            if mfe_this > ob.mfe_from_proximal:
                ob.mfe_from_proximal = mfe_this

            if ob.state == ManualOBState.AWAITING_DISPLACEMENT:
                dead = self._update_awaiting(ob, bar_idx, ts, c_c, c_h, c_l,
                                             events)
            elif ob.state == ManualOBState.LIMIT_RESTING:
                dead = self._update_resting(ob, bar_idx, ts, c_h, c_l, events)
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
            return self._awaiting_first_touch(ob, bar_idx, ts, c_h, c_l, events)
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
        return self._update_resting(ob, bar_idx, ts, c_h, c_l, events)

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

        # --- the single admission gate (corrected; see module docstring) ---
        rejection = self._entry_blocked(ts)
        if rejection is not None:
            self._emit(events,
                       ManualLifecycleEventType.ENTRY_BLOCKED_BY_ACTIVE_TRADE,
                       ob, bar_idx, ts, rejection)
            return False                       # stays LIMIT_RESTING, still live

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
        """
        new_obs = self._scanner_for(asset).scan(
            asset, bar_idx, ts, o, h, l, c, self.cfg)
        for ob in new_obs:
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
    "ManualLifecycleEventType",
    "ManualLifecycleEvent",
    "ManualActiveTrade",
    "ManualTradeExit",
    "ManualSMCLifecycle",
]






