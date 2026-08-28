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
from typing import List

from quantedge.strategy.manual_smc.geometry import _make_manual_ob
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

__all__ = ["ManualSpecBOSScanner", "ManualSMCBOSScanner"]
