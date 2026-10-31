"""
Manual SMC — Causal BOS Scanner (Phase 1 Step 1 extraction).
===========================================================

VERBATIM EXTRACTION from the frozen research oracle:
    engine/src/quantedge/ai/research/displacement_gated_retest_engine.py

Extracted symbol (oracle line ref at extraction time):
    ManualSpecBOSScanner   (oracle L1379)

EXTRACTION CONTRACT
-------------------
Preserved exactly:
  * `deque(maxlen=lookback + 1)` history sizing;
  * the `len(self._history) < 2` early return;
  * backward scan `range(len(self._history) - 2, -1, -1)` — which excludes
    the current bar as origin, so the BOS candle can never be its own
    origin;
  * SHORT evaluated BEFORE LONG, so when a single close satisfies both a
    SHORT and a LONG BOS the returned list order is [SHORT, LONG];
  * `(asset, origin_bar_idx)` dedup keys — one origin, one setup, forever;
  * `width >= self.min_width` gating;
  * break+1 admission semantics (documented below; enforced by the caller,
    which must add returned OBs to the live pool only AFTER the current bar
    is fully processed).

No production wiring. No execution wiring. Nothing imports this module yet.
"""

from __future__ import annotations

import collections
from datetime import datetime
from typing import Dict, List, Optional, Union

from quantedge.strategy.manual_smc.geometry import _make_manual_ob
from quantedge.strategy.manual_smc.luxalgo_structure import (
    LuxAlgoInternalStructure,
    LuxAlgoOrderBlock,
)
from quantedge.strategy.manual_smc.models import ManualOBRecord, ManualSpecConfig


class ManualSpecBOSScanner:
    """
    Streaming, causal BOS scanner implementing the proven manual TradingView SMC rule.
    One instance per asset; call scan() bar-by-bar in chronological order.

    SHORT setup rules (bearish OB):
        origin  = most recent bullish candle (close > open) within last N bars
        ob_top  = origin.close   ← CRITICAL: NOT origin.high
        ob_bot  = origin.low
        BOS     = current_close < ob_bottom  (strict; close-only)

    LONG setup rules (bullish OB):
        origin  = most recent bearish candle (close < open) within last N bars
        ob_top  = origin.high
        ob_bot  = origin.close   ← CRITICAL: NOT origin.low
        BOS     = current_close > ob_top  (strict; close-only)

    Deduplication:
        consumed_origins prevents the same origin bar from generating
        multiple BOS events (e.g. as price continues past the same boundary).
        One origin → one setup, ever.

    Admission timing:
        OBs returned by scan() at bar B are added to the live pool AFTER
        bar B is fully processed, so displacement monitoring starts at B+1.
        This correctly implements break+1 (not break+2) admission.
    """

    def __init__(self, lookback: int = 10, min_width: float = 1e-6) -> None:
        self.lookback  = lookback
        self.min_width = min_width
        # Circular history: (bar_idx, open, high, low, close, timestamp)
        self._history: collections.deque = collections.deque(maxlen=lookback + 1)
        # Consumed origin keys: (asset, origin_bar_idx) → prevents duplicates
        self._consumed: set = set()

    def reset(self) -> None:
        """Reset scanner state. Call when switching assets or re-running."""
        self._history.clear()
        self._consumed.clear()

    def scan(
        self,
        asset: str,
        bar_idx: int,
        ts: datetime,
        o: float,
        h: float,
        l: float,
        c: float,
        cfg: ManualSpecConfig,
    ) -> List[ManualOBRecord]:
        """
        Process one candle. Returns newly created ManualOBRecords (may be empty).

        The BOS candle's CLOSE is the causal trigger. The OB origin and BOS
        are identified simultaneously at the BOS candle close — no future
        information is required.

        Invariant: the BOS candle itself is never the OB origin (origin must
        be a candle BEFORE the current bar).
        """
        self._history.append((bar_idx, o, h, l, c, ts))

        # Need at least 2 bars: one origin candidate + one BOS candle
        if len(self._history) < 2:
            return []

        new_obs: List[ManualOBRecord] = []

        # ── SHORT setup ─────────────────────────────────────────────────────
        # Scan backward through history[:-1] (exclude current bar as origin)
        bull_origin = None
        for i in range(len(self._history) - 2, -1, -1):
            bi, eo, eh, el, ec, ets = self._history[i]
            if ec > eo:                   # bullish: close > open
                bull_origin = self._history[i]
                break

        if bull_origin is not None:
            bi, eo, eh, el, ec, ets = bull_origin
            ob_top_s = ec              # CLOSE (critical — not HIGH)
            ob_bot_s = el              # LOW
            width_s  = ob_top_s - ob_bot_s
            # BOS: strict close below origin low
            if width_s >= self.min_width and c < ob_bot_s:
                key = (asset, bi)
                if key not in self._consumed:
                    self._consumed.add(key)
                    new_obs.append(_make_manual_ob(
                        asset=asset, bos_bar_idx=bar_idx, bos_dt=ts,
                        origin_bar_idx=bi, origin_dt=ets,
                        direction="SHORT",
                        ob_top=ob_top_s, ob_bottom=ob_bot_s,
                        cfg=cfg,
                    ))

        # ── LONG setup ──────────────────────────────────────────────────────
        bear_origin = None
        for i in range(len(self._history) - 2, -1, -1):
            bi, eo, eh, el, ec, ets = self._history[i]
            if ec < eo:                   # bearish: close < open
                bear_origin = self._history[i]
                break

        if bear_origin is not None:
            bi, eo, eh, el, ec, ets = bear_origin
            ob_top_l = eh              # HIGH
            ob_bot_l = ec              # CLOSE (critical — not LOW)
            width_l  = ob_top_l - ob_bot_l
            # BOS: strict close above origin high
            if width_l >= self.min_width and c > ob_top_l:
                key = (asset, bi)
                if key not in self._consumed:
                    self._consumed.add(key)
                    new_obs.append(_make_manual_ob(
                        asset=asset, bos_bar_idx=bar_idx, bos_dt=ts,
                        origin_bar_idx=bi, origin_dt=ets,
                        direction="LONG",
                        ob_top=ob_top_l, ob_bottom=ob_bot_l,
                        cfg=cfg,
                    ))

        return new_obs


# Production-facing alias. Provably behaviour-neutral: identical class object.
ManualSMCBOSScanner = ManualSpecBOSScanner


# ---------------------------------------------------------------------------
# LuxAlgo parity detector -- NOW THE PRODUCTION DEFAULT
# ---------------------------------------------------------------------------
# Promoted from opt-in reference to production default by an explicit authorised
# decision: this is the detector the operator's own TradingView chart runs, and
# `ManualSpecBOSScanner` was ruled non-representative of that setup. The
# demotion changed WHICH zones exist and WHERE their edges are; it changed no
# rule about how a zone is traded.
#
# Reasons this is a SEPARATE class and not a mode flag on ManualSpecBOSScanner:
#   * ManualSpecBOSScanner is a verbatim extraction from the frozen oracle and
#     is covered by an equivalence test against it. A branch inside it would
#     put oracle-extracted code on a non-oracle path.
#   * the two detectors disagree about what an order block IS, not merely about
#     thresholds: different origin selection, different break definition,
#     different zone edges, different break bar.
# ---------------------------------------------------------------------------

# Named rejection reasons. A block can be a perfectly valid LuxAlgo DRAWING and
# still be impossible to trade; when that happens the block is refused by name
# and counted, never repaired into a plausible-looking zone.
REJECT_NON_POSITIVE_WIDTH: str = "NON_POSITIVE_WIDTH"
REJECT_BELOW_MIN_WIDTH: str = "BELOW_MIN_WIDTH"


class LuxAlgoInternalOBScanner:
    """LuxAlgo internal-structure order blocks, in `ManualSpecBOSScanner` shape.

    Same constructor signature and same `scan(...)` signature as the production
    scanner, returning the same `ManualOBRecord` objects, so `lifecycle.py` can
    swap one for the other without knowing which it holds. Everything
    downstream -- 25% entry, fixed 0.60% TP, opposite-edge SL, first touch,
    3-candle entry window, one trade at a time, operator invalidation -- is
    untouched and unaware.

    WHAT DIFFERS FROM THE PRODUCTION DETECTOR
      * the zone is the FULL origin candle `[low, high]` (LuxAlgo's
        `[parsedLow, parsedHigh]`), not the half-candle `[low, close]` /
        `[close, high]`. Since SL is always the distal edge, this roughly
        DOUBLES the stop distance and therefore roughly HALVES the leverage the
        existing sizing rule derives. Measured medians: applied leverage falls
        from 100x to 45-76x, and NO block falls below the authorised 1x floor.
      * the break bar is later (a swing-anchored crossover, not a single-candle
        close displacement), so `bos_bar_idx` differs.
      * `bos_or_choch` is populated. `ManualSpecBOSScanner` leaves it blank
        because it has no trend-bias concept to classify with.
      * roughly a fifth as many blocks are produced (4,136 vs 20,775 over the
        four-symbol history).

    `lookback` is accepted for interface compatibility and is DELIBERATELY
    UNUSED: LuxAlgo's origin window is `[pivot.barIndex, break_bar)`, whose
    length is set by where the pivot fell, not by a fixed bar count. Silently
    honouring `cfg.lookback` here would truncate the origin search and break
    parity. `min_width` IS honoured, because it is the existing production
    guard against degenerate zones.
    """

    def __init__(self, lookback: int = 10, min_width: float = 1e-6) -> None:
        self.lookback = lookback          # accepted, deliberately unused
        self.min_width = min_width
        self._structures: Dict[str, LuxAlgoInternalStructure] = {}
        # Per-asset ACCEPTED candle log, in `ManualSpecBOSScanner._history`
        # tuple order `(bar_idx, o, h, l, c, ts)`.
        #
        # THIS IS NO LONGER "THE STATE". `state.py` now serialises the Pine
        # reconstruction field-by-field through
        # `LuxAlgoInternalStructure.capture_state()` / `restore_state()`, whose
        # completeness is enforced mechanically by `_assert_snapshot_contract`
        # rather than by replay, so a restore installs the structure instead of
        # re-deriving it from these candles. What the log still supplies is
        # narrower and specific: the per-bar `open`, which the structure
        # validates and discards and therefore does not retain, and the bar-index
        # list `state.py`'s torn-capture check reads through
        # `_scanner_bar_indices`.
        #
        # It stays a plain unbounded list IN MEMORY on purpose. The origin search
        # slices `parsed_highs[pivot.barIndex:break_bar]` and an uncrossed pivot
        # can be arbitrarily old, so this process keeps every candle it accepted;
        # only the PAYLOAD is pruned, and only down to
        # `LuxAlgoInternalStructure.causal_watermark()`, the position proved
        # there to be unreachable by any future read. Truncating the live log
        # here would be a different and unproven change (safety rule #15).
        self._bars: Dict[str, List[tuple]] = {}
        # Audit trails. Rejections are counted, not hidden.
        self.rejected: List[tuple] = []   # (reason, asset, LuxAlgoOrderBlock)
        self.rejected_non_positive_width: int = 0
        self.rejected_below_min_width: int = 0

    def reset(self) -> None:
        """Drop all structure state. Mirrors `ManualSpecBOSScanner.reset()`.

        This discards the unbounded parsed-high/parsed-low history, which is the
        only thing the origin search can look back through, so a scanner that
        has been reset cannot reproduce blocks anchored before the reset. That
        is the same "start of history" semantics the production scanner has when
        its deque is cleared. The candle log is dropped with it, because a log
        that outlived its structure would describe candles that structure never
        saw, and `state.py` cross-checks the two on every capture.
        """
        self._structures.clear()
        self._bars.clear()
        self.rejected.clear()
        self.rejected_non_positive_width = 0
        self.rejected_below_min_width = 0

    def structure_for(self, asset: str) -> LuxAlgoInternalStructure:
        """The per-asset indicator instance. Exposed for parity inspection."""
        structure = self._structures.get(asset)
        if structure is None:
            structure = LuxAlgoInternalStructure(asset)
            self._structures[asset] = structure
        return structure

    def bars_for(self, asset: str) -> List[tuple]:
        """The accepted candles for one asset, oldest first.

        Read-only by convention: `state.py` persists the newest slice of this
        list — the window the Pine structure still retains — alongside the
        structure's own bounded snapshot, and cross-checks the two against each
        other on restore. The returned list is the live one, so callers must not
        mutate it.
        """
        return self._bars.setdefault(asset, [])

    def scan(self, asset: str, bar_idx: int, ts: datetime, o: float, h: float,
             l: float, c: float, cfg: ManualSpecConfig) -> List[ManualOBRecord]:
        """Process one closed candle; return admissible OB records.

        Emission order is LuxAlgo's own (bullish break before bearish), which is
        the opposite of `ManualSpecBOSScanner`'s SHORT-before-LONG. Neither is
        normalised to the other: the single trade slot downstream is awarded
        oldest-first, so re-ordering emissions would change which block wins it.
        """
        structure = self.structure_for(asset)
        blocks = structure.step(bar_idx, ts, o, h, l, c)
        # Logged only once `step` has ACCEPTED the candle. `step` validates the
        # geometry and the stream continuity BEFORE it mutates anything, so a
        # refused candle raises above this line and never enters the log: the
        # log and the structure advance together or not at all.
        self._bars.setdefault(asset, []).append((bar_idx, o, h, l, c, ts))
        records: List[ManualOBRecord] = []
        for block in blocks:
            record = self._to_record(block, cfg)
            if record is not None:
                records.append(record)
        return records

    def _to_record(self, block: LuxAlgoOrderBlock,
                   cfg: ManualSpecConfig) -> Optional[ManualOBRecord]:
        """Convert one LuxAlgo box into a trade record, or refuse it by name.

        PARITY-SAFE REJECTION. LuxAlgo happily DRAWS a box that cannot be
        traded, so two explicit rules stand between the drawing and an order:

          1. NON_POSITIVE_WIDTH -- `barHigh <= barLow`. Either the origin was a
             high-volatility candle, whose parsedHigh/parsedLow are swapped, so
             the box is upside down; or the origin was a doji (`high == low`),
             so the box has no thickness. In the inverted case `proximal` and
             `distal` would silently trade places, putting the stop on the
             WRONG SIDE of the entry. Sorting the edges would manufacture a
             plausible-looking zone that LuxAlgo never drew, so the block is
             refused instead.
          2. BELOW_MIN_WIDTH -- the same `width >= min_width` guard the
             production scanner applies, kept identical rather than re-invented.

        The block STAYS in the structure's own store either way, so the 100-block
        cap, the eviction order and mitigation bookkeeping remain bit-identical
        to the indicator. Only the trade record is withheld.
        """
        width = block.barHigh - block.barLow
        if width <= 0.0:
            self.rejected_non_positive_width += 1
            self.rejected.append((REJECT_NON_POSITIVE_WIDTH, block.asset, block))
            return None
        if width < self.min_width:
            self.rejected_below_min_width += 1
            self.rejected.append((REJECT_BELOW_MIN_WIDTH, block.asset, block))
            return None

        assert block.origin_dt is not None and block.created_dt is not None
        record = _make_manual_ob(
            asset=block.asset,
            bos_bar_idx=block.created_bar_idx,
            bos_dt=block.created_dt,
            origin_bar_idx=block.origin_bar_idx,
            origin_dt=block.origin_dt,
            direction=block.direction,
            ob_top=block.barHigh,
            ob_bottom=block.barLow,
            cfg=cfg,
        )
        # Instance attribute shadowing the ClassVar default; see models.py.
        record.bos_or_choch = block.tag
        return record


# Either detector, wherever one is held without caring which.
ManualOBScanner = Union[ManualSpecBOSScanner, LuxAlgoInternalOBScanner]

__all__ = [
    "ManualSpecBOSScanner",
    "ManualSMCBOSScanner",
    "LuxAlgoInternalOBScanner",
    "ManualOBScanner",
    "REJECT_NON_POSITIVE_WIDTH",
    "REJECT_BELOW_MIN_WIDTH",
]
