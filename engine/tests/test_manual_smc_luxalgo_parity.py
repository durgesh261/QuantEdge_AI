"""LuxAlgo internal-structure detector -- parity, Pine quirks, default lock.

The detector added in `luxalgo_structure.py` / `LuxAlgoInternalOBScanner` is a
bar-by-bar reconstruction of the published LuxAlgo "Smart Money Concepts"
Pine v5 script. It began life as an OPT-IN parity reference; it is now the
PRODUCTION DEFAULT by an explicit authorised decision, and `ManualSpecBOSScanner`
is retained as the explicitly-selectable research/oracle extraction. This module
is still the guard that keeps the default from moving by accident -- the
direction the guard points has been inverted, not removed (see
`TestDefaultDetectorRegression`, whose own docstring records the inversion).

    requirement                                   proved by
    -------------------------------------------   ------------------------------
    A  the four screenshot order blocks are        TestScreenshotReconstruction
       reproduced exactly (origin bar, zone,
       creation bar, BOS/CHoCH)
    B  a non-high-volatility zone is exactly       TestZoneIsTheWholeOriginCandle
       the origin candle's [low, high]
    C  the ATR-200 warm-up and the                 TestHighVolatilitySwap
       parsedHigh/parsedLow swap
    D  the 50 / 5 / 3 leg states are               TestIndependentLegStates
       independent, as Pine `var` per call site
    E  a pivot is recorded `size` bars in          TestPivotTiming
       arrears, not on the detection bar
    F  BOS vs CHoCH follows the trend-bias         TestBosChochClassification
       state, and a virgin bias tags BOS
    G  extraCondition reads the swing-50 layer     TestExtraConditionUsesSwing
    H  the origin search excludes the break bar    TestOriginSearchWindow
       and resolves a tie to the earliest bar
    I  Pine's mutate-during-iteration mitigation   TestPineMitigationIteration
       loop, and the 100-block cap eviction
    J  omitting `detector` selects                 TestDefaultDetectorRegression
       DETECTOR_LUXALGO_INTERNAL, everywhere,
       and MANUAL_SPEC stays selectable
    K  `ManualOBRecord`'s persisted schema is      TestRecordSchemaIsUntouched
       unchanged by the BOS/CHoCH carrier

Two further classes pin behaviour that is deliberately NOT Pine:
`TestStreamFailsClosed` (a gap, a repeat or an impossible candle raises rather
than silently mis-selecting an origin) and `TestScannerInterfaceParity` (the
two scanners are interchangeable at the call site, and BOTH are now
snapshottable -- the LuxAlgo block is a different shape, not a relaxed one,
and a scanner that disagrees with the declared detector is still refused at
capture time).

Mitigation is a LuxAlgo DRAWING concept. It is reproduced here because the
100-block cap, the eviction order and the reported mitigation bar all depend on
it -- it is never forwarded to the trading lifecycle as an invalidation.

Every fixture below was verified empirically against the engine before a single
assertion was written; the named numbers are measured, not predicted.
"""
from __future__ import annotations

import dataclasses
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from quantedge.strategy.manual_smc import luxalgo_structure as LS
from quantedge.strategy.manual_smc import state as ST
from quantedge.strategy.manual_smc.backtest import ManualSMCBacktest
from quantedge.strategy.manual_smc.lifecycle import (
    DETECTOR_LUXALGO_INTERNAL,
    DETECTOR_MANUAL_SPEC,
    DETECTORS,
    ManualSMCLifecycle,
)
from quantedge.strategy.manual_smc.luxalgo_structure import (
    ATR_HIGH_VOL_MULT,
    ATR_LENGTH,
    BEARISH,
    BULLISH,
    EQUAL_LENGTH,
    INTERNAL_LENGTH,
    SWINGS_LENGTH,
    TAG_BOS,
    TAG_CHOCH,
    LuxAlgoInternalStructure,
    LuxAlgoOrderBlock,
    LuxAlgoPivot,
    LuxAlgoStructureError,
    _LegState,
    _leg_step,
)
from quantedge.strategy.manual_smc.models import (
    ManualOBRecord,
    manual_smc_production_config,
)
from quantedge.strategy.manual_smc.scanner import (
    REJECT_BELOW_MIN_WIDTH,
    REJECT_NON_POSITIVE_WIDTH,
    LuxAlgoInternalOBScanner,
    ManualSpecBOSScanner,
)
from quantedge.strategy.manual_smc.strategy import ManualSMCStrategy

CFG = manual_smc_production_config()
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ts(i: int) -> datetime:
    """One synthetic hourly candle timestamp per bar index."""
    return T0 + timedelta(hours=i)


def _ramp(start: float, step: float, n: int,
          spread: float = 2.0) -> List[Tuple[float, float, float, float]]:
    """`n` candles whose open/close march by `step` and whose wicks add `spread`.

    Deliberately monotonic: a one-sided LuxAlgo leg only flips when the bar
    `size` back beats every bar after it, so a clean ramp puts the pivot on a
    bar this test can name by hand.
    """
    out: List[Tuple[float, float, float, float]] = []
    mid = start
    for _ in range(n):
        o, c = mid, mid + step
        out.append((o, max(o, c) + spread, min(o, c) - spread, c))
        mid += step
    return out


# The workhorse series: six alternating ramps, 81 bars, no ATR (the 200-bar
# warm-up never completes) and therefore no high-volatility bar anywhere.
# Measured: 6 internal pivots, 0 swing pivots, 4 internal breaks tagged
# BOS, CHoCH, CHoCH, BOS, and 4 order blocks.
S1: List[Tuple[float, float, float, float]] = (
    _ramp(100.0, +2.0, 15)        # bars  0..14  rising
    + _ramp(130.0, -3.0, 13)      # bars 15..27  falling
    + _ramp(91.0, +4.0, 13)       # bars 28..40  rising
    + _ramp(143.0, -5.0, 13)      # bars 41..53  falling
    + _ramp(78.0, +3.0, 13)       # bars 54..66  rising
    + _ramp(117.0, -4.0, 14)      # bars 67..80  falling
)
S1_LAST = len(S1) - 1             # 80

# The ATR series: 199 one-dollar-range bars with a single 500-wide bar at 100
# (inside the warm-up, so it CANNOT be high-volatility), then a 1000-wide bar
# at 199 where `ta.atr(200)` first has a value. Measured ATR[199] == 8.49.
S2: List[Tuple[float, float, float, float]] = [
    (100.0, 350.0, -150.0, 100.0) if i == 100 else (100.0, 100.5, 99.5, 100.0)
    for i in range(199)
] + [(100.0, 600.0, -400.0, 100.0)]


def _wave(n: int) -> List[Tuple[float, float, float, float]]:
    """A choppy but fully deterministic series -- no RNG, no clock, no floats.

    The modular arithmetic gives a mean-reverting sawtooth (measured range
    981..1010 over 400 bars) choppy enough for the size-3 and size-5 legs to
    fire often and the size-50 leg to fire almost never, which is what makes
    the three leg states distinguishable from each other.
    """
    bars: List[Tuple[float, float, float, float]] = []
    v = 1000.0
    for i in range(n):
        o = v
        c = v + float(((i * 7919) % 23) - 11)
        bars.append((o, max(o, c) + float((i * 104729) % 5),
                     min(o, c) - float((i * 15485863) % 5), c))
        v = c
    return bars


WAVE = _wave(400)


def _drive(structure: LuxAlgoInternalStructure,
           bars: List[Tuple[float, float, float, float]],
           first_bar: int = 0) -> List[LuxAlgoOrderBlock]:
    """Stream `bars` through `structure` and return every block it created."""
    created: List[LuxAlgoOrderBlock] = []
    for k, (o, h, l, c) in enumerate(bars):
        created.extend(structure.step(first_bar + k, _ts(first_bar + k),
                                      o, h, l, c))
    return created


def _driven(bars: List[Tuple[float, float, float, float]], asset: str = "SYN"
            ) -> Tuple[LuxAlgoInternalStructure, List[LuxAlgoOrderBlock]]:
    """A fresh structure driven over `bars`. Cheap enough to rebuild per test."""
    structure = LuxAlgoInternalStructure(asset)
    return structure, _drive(structure, bars)


def _lifecycle_events(bars: List[Tuple[float, float, float, float]],
                      **kwargs: object) -> List[object]:
    """Every event a lifecycle emits over `bars`, for detector A/B comparison."""
    lifecycle = ManualSMCLifecycle(assets=["SYN"], **kwargs)  # type: ignore[arg-type]
    events: List[object] = []
    for i, (o, h, l, c) in enumerate(bars):
        events.extend(lifecycle.process_candle("SYN", i, _ts(i), o, h, l, c))
    return events


# ---------------------------------------------------------------------------
# Real candles. The four screenshot order blocks live at bars 19,609..19,703,
# which is PAST the end of the 19,597-bar canonical tree, so this module reads
# the extended tree the five-day parity diagnostic downloaded. That tree is
# gitignored, so every test that needs it is skipped when it is absent -- the
# same pattern as `test_manual_smc_lifecycle.py` and
# `test_manual_smc_portfolio_sizing.py`.
#
# Only the first BARS candles are driven, so appending more history later
# cannot move a single number pinned below.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent.parent
EXTENDED = (REPO_ROOT / "engine" / "data" / "research" / "manual_smc_5day_parity"
            / "canonical_extended" / "delta_exchange_india")
SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
BARS = 19762
HAVE_EXTENDED = all((EXTENDED / s / "1h" / "full_history.csv").exists()
                    for s in SYMBOLS)
needs_extended = pytest.mark.skipif(
    not HAVE_EXTENDED,
    reason="extended canonical 1h history (19,762 bars) not present")

_CACHE: Dict[str, Tuple[LuxAlgoInternalStructure, List[LuxAlgoOrderBlock],
                        LuxAlgoInternalOBScanner, List[ManualOBRecord],
                        List[dict]]] = {}


def _run(symbol: str):
    """Drive the structure AND the production scanner over one symbol once.

    Measured cost: ~0.35 s per symbol, so the four-symbol sweep is ~1.4 s. The
    result is cached because six test methods below re-use the same drive.
    """
    if symbol not in _CACHE:
        from quantedge.ai.research.displacement_gated_retest_engine import (
            _load_canonical_candles,
        )

        rows = list(_load_canonical_candles(EXTENDED, symbol))[:BARS]
        assert len(rows) == BARS, (
            f"{symbol}: expected at least {BARS} candles, got {len(rows)}")
        structure = LuxAlgoInternalStructure(symbol)
        scanner = LuxAlgoInternalOBScanner(lookback=CFG.lookback,
                                           min_width=CFG.min_ob_width)
        blocks: List[LuxAlgoOrderBlock] = []
        records: List[ManualOBRecord] = []
        for i, row in enumerate(rows):
            args = (i, row["timestamp"], row["open"], row["high"], row["low"],
                    row["close"])
            blocks.extend(structure.step(*args))
            records.extend(scanner.scan(symbol, *args, CFG))
        _CACHE[symbol] = (structure, blocks, scanner, records, rows)
    return _CACHE[symbol]
# The four order blocks that are MEASURABLE on the operator's TradingView
# screenshots (BTC shot 2, ETH shot 4, SOL shot 6, XRP shot 7): the ones whose
# box edges land on labelled price gridlines and whose origin candle is
# unambiguous. Every value here was read off the chart, not produced by the
# engine, which is what makes this an external check rather than a tautology.
#   symbol, origin bar, direction, zone low, zone high, creation bar, tag
SCREENSHOTS: Tuple[Tuple[str, int, str, float, float, int, str], ...] = (
    ("BTCUSD", 19696, "SHORT", 78715.0, 79386.5, 19703, TAG_CHOCH),
    ("ETHUSD", 19633, "SHORT", 2506.70, 2534.25, 19634, TAG_CHOCH),
    ("SOLUSD", 19609, "LONG", 100.560, 101.963, 19616, TAG_BOS),
    ("XRPUSD", 19613, "LONG", 1.3939, 1.4115, 19616, TAG_CHOCH),
)

# Measured once through `LuxAlgoInternalOBScanner` + the SHARED production
# geometry helper: sl_dist_pct, theoretical leverage, applied leverage.
SCREENSHOT_RISK: Dict[str, Tuple[float, float, float]] = {
    "BTCUSD": (0.638446557633707, 54.82056341523951, 54.82056341523951),
    "ETHUSD": (0.8220322546957433, 42.577404718692534, 42.577404718692534),
    "SOLUSD": (1.0355542761822385, 33.79832501781939, 33.79832501781939),
    "XRPUSD": (0.93809963755242, 37.30946969696941, 37.30946969696941),
}


@needs_extended
class TestScreenshotReconstruction:
    """
    A -- the four measurable screenshot order blocks, reproduced exactly.

    This is the only test in the module that compares the reconstruction against
    something OUTSIDE the repository. If a future edit changes a pivot length, a
    tie-break, the slice bound or the swap, one of these four boxes moves and
    this class fails.
    """

    @pytest.mark.parametrize("case", SCREENSHOTS,
                             ids=[c[0] for c in SCREENSHOTS])
    def test_block_matches_the_chart(self, case) -> None:
        """Origin bar, direction, both zone edges, creation bar and the tag."""
        symbol, origin, direction, zone_low, zone_high, created, tag = case
        _structure, blocks, _scanner, _records, _rows = _run(symbol)
        hits = [b for b in blocks if b.origin_bar_idx == origin
                and b.created_bar_idx == created]
        assert len(hits) == 1, (
            f"{symbol}: expected exactly one block with origin {origin} and "
            f"creation {created}, got {len(hits)}")
        block = hits[0]
        assert block.direction == direction
        assert block.barLow == zone_low
        assert block.barHigh == zone_high
        assert block.tag == tag
        assert block.bias == (BEARISH if direction == "SHORT" else BULLISH)
    @pytest.mark.parametrize("case", SCREENSHOTS,
                             ids=[c[0] for c in SCREENSHOTS])
    def test_zone_is_the_origin_candle(self, case) -> None:
        """The chart box is the WHOLE origin candle, wick to wick."""
        symbol, origin, _direction, zone_low, zone_high, _created, _tag = case
        _structure, _blocks, _scanner, _records, rows = _run(symbol)
        assert rows[origin]["low"] == zone_low
        assert rows[origin]["high"] == zone_high

    @pytest.mark.parametrize("case", SCREENSHOTS,
                             ids=[c[0] for c in SCREENSHOTS])
    def test_record_carries_the_zone_and_the_tag(self, case) -> None:
        """One `ManualOBRecord` per block, built by the SHARED geometry helper.

        The entry/SL/TP arithmetic is deliberately NOT re-implemented here: the
        point is that the LuxAlgo scanner hands its zone to the same
        `_make_manual_ob` the production scanner uses, so the numbers below are
        the production rules (25% entry from the proximal edge, fixed 0.60% TP,
        opposite-edge SL) applied to a LuxAlgo zone.
        """
        symbol, origin, direction, zone_low, zone_high, created, tag = case
        _structure, _blocks, _scanner, records, _rows = _run(symbol)
        hits = [r for r in records if r.origin_bar_idx == origin
                and r.bos_bar_idx == created]
        assert len(hits) == 1
        record = hits[0]
        assert record.ob_id == f"MANUAL_{symbol}_{direction}_{origin}_{created}"
        assert record.direction == direction
        assert (record.ob_bottom, record.ob_top) == (zone_low, zone_high)
        assert record.bos_or_choch == tag

        width = zone_high - zone_low
        if direction == "SHORT":
            assert record.proximal == zone_low and record.distal == zone_high
            assert record.entry_price == zone_low + 0.25 * width
            assert record.tp_price == record.entry_price * (1.0 - 0.006)
        else:
            assert record.proximal == zone_high and record.distal == zone_low
            assert record.entry_price == zone_high - 0.25 * width
            assert record.tp_price == record.entry_price * (1.0 + 0.006)
        assert record.sl_price == record.distal

        sl_pct, theo, applied = SCREENSHOT_RISK[symbol]
        assert record.sl_dist_pct == pytest.approx(sl_pct, rel=1e-12)
        assert record.theoretical_leverage == pytest.approx(theo, rel=1e-12)
        assert record.applied_leverage == pytest.approx(applied, rel=1e-12)
        # The authorised band is 1x..100x inclusive. A full-candle LuxAlgo zone
        # is roughly twice as wide as the production half-candle zone, so the
        # derived leverage is roughly half -- it moves DOWN into the band, never
        # out of it. Nothing here clamps: 100x is simply never reached.
        assert 1.0 <= record.applied_leverage <= 100.0
# Full-history counters, measured over the first 19,762 candles of each symbol.
# blocks, BOS, CHoCH, internal pivots, swing pivots, breaks, high-volatility
# bars, mitigated, still-live, inverted, Pine loop skips, records, rejections.
COUNTERS: Dict[str, Dict[str, int]] = {
    "BTCUSD": dict(blocks=1057, bos=472, choch=585, internal=2848, swing=306,
                   breaks=1057, hivol=1496, mitigated=1011, live=46,
                   inverted=2, skips=1006, records=1055, rejected=2),
    "ETHUSD": dict(blocks=1032, bos=455, choch=577, internal=2835, swing=312,
                   breaks=1032, hivol=1425, mitigated=990, live=42,
                   inverted=1, skips=983, records=1031, rejected=1),
    "SOLUSD": dict(blocks=1037, bos=495, choch=542, internal=2630, swing=294,
                   breaks=1037, hivol=1219, mitigated=997, live=40,
                   inverted=3, skips=990, records=1034, rejected=3),
    "XRPUSD": dict(blocks=1022, bos=484, choch=538, internal=2780, swing=344,
                   breaks=1022, hivol=1436, mitigated=975, live=47,
                   inverted=5, skips=970, records=1016, rejected=6),
}


@needs_extended
@pytest.mark.parametrize("symbol", SYMBOLS)
class TestZoneIsTheWholeOriginCandle:
    """
    B -- every non-high-volatility zone is exactly the origin candle.

    LuxAlgo stores `parsedHigh`/`parsedLow` of the winning candle, and off a
    high-volatility bar those ARE the raw high and low. So the zone law is an
    equality, not an approximation, and it is checked on all ~4,100 blocks
    rather than on a sample.
    """

    def test_zone_equals_origin_candle_high_low(self, symbol: str) -> None:
        """No tolerance: the stored edges are the candle's own floats."""
        _structure, blocks, _scanner, _records, rows = _run(symbol)
        checked = 0
        for block in blocks:
            if block.origin_high_volatility:
                continue
            row = rows[block.origin_bar_idx]
            assert block.barLow == row["low"], block
            assert block.barHigh == row["high"], block
            assert block.barTime == row["timestamp"]
            checked += 1
        want = COUNTERS[symbol]
        assert checked == want["blocks"] - want["inverted"]
        assert checked > 1000
    def test_high_volatility_origin_gives_an_inverted_box(self,
                                                          symbol: str) -> None:
        """The swap is the ATR filter, and when a volatile bar wins anyway the
        stored box is upside down. Pine draws it; the scanner refuses it."""
        _structure, blocks, _scanner, _records, _rows = _run(symbol)
        volatile = [b for b in blocks if b.origin_high_volatility]
        assert len(volatile) == COUNTERS[symbol]["inverted"]
        for block in volatile:
            assert block.is_inverted, block
            assert not block.has_tradeable_width
            assert block.width < 0.0

    def test_structure_counters(self, symbol: str) -> None:
        """The whole audit table in one assertion set, as a regression lock."""
        structure, blocks, _scanner, _records, _rows = _run(symbol)
        want = COUNTERS[symbol]
        assert len(blocks) == want["blocks"]
        assert len(structure.all_order_blocks) == want["blocks"]
        assert sum(1 for b in blocks if b.tag == TAG_BOS) == want["bos"]
        assert sum(1 for b in blocks if b.tag == TAG_CHOCH) == want["choch"]
        assert want["bos"] + want["choch"] == want["blocks"]
        assert len(structure.internal_pivots) == want["internal"]
        assert len(structure.swing_pivots) == want["swing"]
        assert len(structure.internal_events) == want["breaks"]
        assert sum(structure.high_volatility) == want["hivol"]
        assert len(structure.mitigated_order_blocks) == want["mitigated"]
        assert len(structure.live_order_blocks) == want["live"]
        assert len(structure.pine_loop_skips) == want["skips"]
        assert len(structure.evicted_at_cap) == 0
        # Every block is either still live or was mitigated -- nothing leaks.
        assert want["mitigated"] + want["live"] == want["blocks"]
        # `extraCondition` suppressed NOTHING on this data. It is implemented
        # anyway; a filter that happens to be inert here is not absent.
        assert structure.suppressed_by_extra_condition == []
        assert len(structure.display_slice()) == min(20, want["live"])

    def test_scanner_rejects_exactly_the_untradeable_boxes(self,
                                                          symbol: str) -> None:
        """An inverted or zero-width box is refused by an explicit named rule.

        XRPUSD is the interesting one: it has 5 inverted boxes and 6 rejections,
        because bar 335 is a doji whose high == low. That box is NOT inverted,
        it is zero-width -- equally untradeable, and caught by the same rule
        rather than by a second implicit one.
        """
        _structure, blocks, scanner, records, _rows = _run(symbol)
        want = COUNTERS[symbol]
        assert len(records) == want["records"]
        assert len(records) == want["blocks"] - want["rejected"]
        assert scanner.rejected_non_positive_width == want["rejected"]
        assert scanner.rejected_below_min_width == 0
        assert [r[0] for r in scanner.rejected] == (
            [REJECT_NON_POSITIVE_WIDTH] * want["rejected"])
        for _rule, _asset, block in scanner.rejected:
            assert not block.has_tradeable_width
        zero_width = [b for _r, _a, b in scanner.rejected
                      if b.barHigh == b.barLow]
        assert len(zero_width) == want["rejected"] - want["inverted"]
        assert all(r.ob_width > 0.0 for r in records)
        # `applied_leverage` is `min(100, 35 / sl_dist_pct)` from the SHARED
        # production helper. Measured minima: BTC 22.2x, ETH 15.8x, SOL 10.3x,
        # XRP 8.6x -- a full-candle zone lowers leverage but never below 1x, so
        # the authorised 1x..100x band holds without a new clamp.
        assert all(1.0 <= r.applied_leverage <= 100.0 for r in records)
        tradeable = [b for b in blocks if b.has_tradeable_width]
        assert len(tradeable) == len(records)
class TestHighVolatilitySwap:
    """
    C -- the ATR-200 warm-up, and parsedHigh/parsedLow swapped when volatile.

    Pine:
        atr             = ta.atr(200)
        highVolatility  = (high - low) >= (2 * atr)
        parsedHigh      = highVolatility ? low  : high
        parsedLow       = highVolatility ? high : low

    The swap IS the volatility filter -- it hands a volatile candle a tiny
    parsedHigh and a huge parsedLow so it loses the argmax/argmin. There is no
    separate rejection step in Pine, which is why the engine needs one.
    """

    def test_warm_up_is_na_and_immune(self) -> None:
        """`ta.atr(200)` is na for 199 bars, and `na >= x` is false in Pine.

        Bar 100 has a 500-wide range in a series of 1-wide bars. On any later
        bar that would be high-volatility several times over. Inside the warm-up
        it cannot be, because the comparison is against na.
        """
        structure, _blocks = _driven(S2)
        assert ATR_LENGTH == 200
        assert all(v is None for v in structure.atr_values[:ATR_LENGTH - 1])
        assert structure.atr_values[ATR_LENGTH - 2] is None
        assert structure.high_volatility[100] is False
        assert structure.parsed_highs[100] == 350.0     # NOT swapped
        assert structure.parsed_lows[100] == -150.0
        assert sum(structure.high_volatility[:ATR_LENGTH - 1]) == 0

    def test_first_atr_is_the_simple_mean_of_200_true_ranges(self) -> None:
        """`ta.rma` seeds with an SMA at index length-1, not with na or with tr.

        198 bars of true range 1, one bar of 500, one bar of 1000: the mean is
        1698 / 200 == 8.49 exactly.
        """
        structure, _blocks = _driven(S2)
        assert structure.atr_values[199] == pytest.approx((198.0 + 500.0
                                                           + 1000.0) / 200.0)
        assert structure.atr_values[199] == pytest.approx(8.49)
        assert ATR_HIGH_VOL_MULT == 2.0
        span = structure.highs[199] - structure.lows[199]
        assert span == 1000.0
        assert span >= ATR_HIGH_VOL_MULT * structure.atr_values[199]
    def test_volatile_bar_has_its_high_and_low_swapped(self) -> None:
        """Bar 199 is the only high-volatility bar in the series, and it swaps."""
        structure, _blocks = _driven(S2)
        assert structure.high_volatility[199] is True
        assert sum(structure.high_volatility) == 1
        assert structure.highs[199] == 600.0
        assert structure.lows[199] == -400.0
        assert structure.parsed_highs[199] == -400.0     # low, deliberately
        assert structure.parsed_lows[199] == 600.0       # high, deliberately
        assert structure.parsed_highs[199] < structure.parsed_lows[199]

    def test_a_swapped_origin_produces_an_inverted_box(self) -> None:
        """An inverted box is what the swap yields when a volatile bar wins.

        Built by hand rather than searched for, so the refusal is pinned even on
        a machine with no market data present.
        """
        block = LuxAlgoOrderBlock(barHigh=20.0, barLow=50.0, barTime=T0,
                                  bias=BEARISH, asset="SYN",
                                  origin_bar_idx=7, origin_dt=T0,
                                  created_bar_idx=9, created_dt=T0,
                                  tag=TAG_BOS, origin_high_volatility=True)
        assert block.is_inverted
        assert not block.has_tradeable_width
        assert block.width == -30.0
        scanner = LuxAlgoInternalOBScanner(lookback=CFG.lookback,
                                          min_width=CFG.min_ob_width)
        assert scanner._to_record(block, CFG) is None
        assert scanner.rejected_non_positive_width == 1
        assert scanner.rejected_below_min_width == 0
        assert scanner.rejected == [(REJECT_NON_POSITIVE_WIDTH, "SYN", block)]

    def test_a_too_narrow_box_is_refused_by_its_own_named_rule(self) -> None:
        """Positive but sub-`min_width` is a DIFFERENT rejection, counted apart."""
        block = LuxAlgoOrderBlock(barHigh=100.5, barLow=100.0, barTime=T0,
                                  bias=BULLISH, asset="SYN",
                                  origin_bar_idx=7, origin_dt=T0,
                                  created_bar_idx=9, created_dt=T0, tag=TAG_BOS)
        assert block.has_tradeable_width
        scanner = LuxAlgoInternalOBScanner(lookback=CFG.lookback, min_width=10.0)
        assert scanner._to_record(block, CFG) is None
        assert scanner.rejected_non_positive_width == 0
        assert scanner.rejected_below_min_width == 1
        assert scanner.rejected == [(REJECT_BELOW_MIN_WIDTH, "SYN", block)]
        # ...and the SAME block passes under the production min width.
        ok = LuxAlgoInternalOBScanner(lookback=CFG.lookback,
                                      min_width=CFG.min_ob_width)
        record = ok._to_record(block, CFG)
        assert record is not None and record.ob_width == 0.5
def _leg_stream(size: int, bars) -> List[Tuple[str, int, int]]:
    """Every pivot one INDEPENDENT `leg(size)` state fires: (kind, bar, origin)."""
    highs = [b[1] for b in bars]
    lows = [b[2] for b in bars]
    state = _LegState()
    out: List[Tuple[str, int, int]] = []
    for pos in range(len(bars)):
        new_pivot, pivot_low, _pivot_high = _leg_step(state, pos, size,
                                                      highs, lows)
        if new_pivot:
            out.append(("LOW" if pivot_low else "HIGH", pos, pos - size))
    return out


class TestIndependentLegStates:
    """
    D -- Pine gives every CALL SITE its own `var leg`, so 50 / 5 / 3 are three
    independent detectors. Fusing them into one shared counter is the single
    easiest way to destroy parity while still producing plausible output.
    """

    def test_the_three_sizes_produce_three_different_pivot_streams(self) -> None:
        """Measured over the 400-bar wave: 1, 69 and 69 pivots, all distinct."""
        s50 = _leg_stream(SWINGS_LENGTH, WAVE)
        s5 = _leg_stream(INTERNAL_LENGTH, WAVE)
        s3 = _leg_stream(EQUAL_LENGTH, WAVE)
        assert (SWINGS_LENGTH, INTERNAL_LENGTH, EQUAL_LENGTH) == (50, 5, 3)
        assert len(s50) == 1 and s50 == [("LOW", 153, 103)]
        assert len(s5) == 69
        assert len(s3) == 69
        # 5 and 3 happen to fire the same NUMBER of times on this series; they
        # are still different streams, because a pivot is detected `size` bars
        # after the bar it anchors to.
        assert s5 != s3
        assert s5[:4] == [("LOW", 6, 1), ("HIGH", 11, 6),
                          ("LOW", 16, 11), ("HIGH", 21, 16)]
        assert s3[:4] == [("LOW", 4, 1), ("HIGH", 9, 6),
                          ("LOW", 14, 11), ("HIGH", 19, 16)]
        assert s50 != s5 and s50 != s3

    def test_two_states_of_the_same_size_agree(self) -> None:
        """The control: separate state, same size, same input -> same stream."""
        assert _leg_stream(INTERNAL_LENGTH, WAVE) == _leg_stream(
            INTERNAL_LENGTH, WAVE)
    def test_sharing_one_state_between_two_sizes_corrupts_both(self) -> None:
        """The failure mode this design avoids, demonstrated.

        Interleaving a size-5 and a size-3 call through ONE `_LegState` collapses
        the size-5 pivot stream from 69 firings to 6, because each call now sees
        a `leg` the other call just moved.
        """
        highs = [b[1] for b in WAVE]
        lows = [b[2] for b in WAVE]
        shared = _LegState()
        fused: List[int] = []
        for pos in range(len(WAVE)):
            if _leg_step(shared, pos, INTERNAL_LENGTH, highs, lows)[0]:
                fused.append(pos)
            _leg_step(shared, pos, EQUAL_LENGTH, highs, lows)
        clean = [bar for _kind, bar, _origin in _leg_stream(INTERNAL_LENGTH,
                                                            WAVE)]
        assert len(clean) == 69
        assert len(fused) == 6
        assert fused != clean

    def test_the_structure_keeps_three_separate_states(self) -> None:
        """Three distinct objects, and three different pivot populations."""
        structure = LuxAlgoInternalStructure("WAVE")
        _drive(structure, WAVE)
        assert len({id(structure._leg_swing), id(structure._leg_internal),
                    id(structure._leg_equal)}) == 3
        assert len(structure.internal_pivots) == 69
        assert len(structure.swing_pivots) == 1
        assert len(structure.equal_events) == 9
        assert len(structure.internal_pivots) == len(
            _leg_stream(INTERNAL_LENGTH, WAVE))
        assert len(structure.swing_pivots) == len(
            _leg_stream(SWINGS_LENGTH, WAVE))
        assert len(structure.all_order_blocks) == 17
        # The ATR warm-up DOES complete here (400 bars > 200), but the wave's
        # ranges are near-uniform, so nothing reaches 2x ATR and no bar swaps.
        assert structure.atr_values[199] is not None
        assert sum(structure.high_volatility) == 0
# Measured over S1. A pivot tuple is
# (kind, detection bar, detection ts, origin bar, origin ts, level).
S1_INTERNAL_PIVOTS: Tuple[Tuple[str, int, int, float], ...] = (
    ("LOW", 5, 0, 98.0),
    ("HIGH", 20, 15, 132.0),
    ("LOW", 33, 28, 89.0),
    ("HIGH", 46, 41, 145.0),
    ("LOW", 59, 54, 76.0),
    ("HIGH", 72, 67, 119.0),
)


class TestPivotTiming:
    """
    E -- `barIndex := bar_index - size`: a pivot anchors `size` bars BACK.

    This is the whole reason a LuxAlgo order block sits further left than the
    engine's: the origin search starts at the anchored bar, not at the bar the
    leg flip was noticed on. Off-by-one here moves every zone.
    """

    def test_pivots_are_recorded_five_bars_in_arrears(self) -> None:
        """Six internal pivots, each detected exactly INTERNAL_LENGTH bars late."""
        structure, _blocks = _driven(S1)
        got = tuple((p[0], p[1], p[3], p[5])
                    for p in structure.internal_pivots)
        assert got == S1_INTERNAL_PIVOTS
        for _kind, detect, origin, _level in S1_INTERNAL_PIVOTS:
            assert detect - origin == INTERNAL_LENGTH == 5

    def test_the_level_is_the_anchored_candle_not_the_detection_candle(self
                                                                      ) -> None:
        """A pivot LOW carries the anchored bar's low, a HIGH its high."""
        structure, _blocks = _driven(S1)
        for pivot in structure.internal_pivots:
            kind, detect, detect_ts, origin, origin_ts, level = pivot
            assert detect_ts == _ts(detect)
            assert origin_ts == _ts(origin)
            expect = (structure.lows if kind == "LOW" else structure.highs)
            assert level == expect[origin]
            assert level != expect[detect]

    def test_bar_zero_can_never_produce_a_pivot(self) -> None:
        """`ta.change(leg)` is na on the first bar, so nothing fires there."""
        structure, blocks = _driven(S1)
        assert all(p[1] > 0 for p in structure.internal_pivots)
        assert blocks == structure.all_order_blocks
        first = _driven(S1[:1])[0]
        assert first.internal_pivots == [] and first.swing_pivots == []
    def test_the_swing_layer_needs_fifty_bars_and_stays_silent_here(self) -> None:
        """81 monotonic-ramp bars never flip the size-50 leg. Measured: zero.

        This matters for G: `extraCondition` compares against a swing level that
        is still `na` throughout this series, and `_pine_ne(x, None)` is True, so
        the filter passes rather than blocking every break.
        """
        structure, _blocks = _driven(S1)
        assert structure.swing_pivots == []
        assert structure.swing_high.currentLevel is None
        assert structure.swing_low.currentLevel is None
        assert structure.swing_trend.bias == 0
        assert len(S1) == 81 and S1_LAST == 80


# Measured over S1. An event tuple is
# (bar, ts, direction, tag, level, pivot bar, pivot ts, block).
S1_BREAKS: Tuple[Tuple[int, str, str, float, int], ...] = (
    (25, "BEARISH", TAG_BOS, 98.0, 0),
    (38, "BULLISH", TAG_CHOCH, 132.0, 15),
    (51, "BEARISH", TAG_CHOCH, 89.0, 28),
    (77, "BEARISH", TAG_BOS, 76.0, 54),
)
# (seq, direction, origin bar, zone low, zone high, creation bar, tag,
#  mitigation bar)
S1_BLOCKS: Tuple[Tuple[int, str, int, float, float, int, str, object], ...] = (
    (0, "SHORT", 14, 126.0, 132.0, 25, TAG_BOS, 37),
    (1, "LONG", 27, 89.0, 96.0, 38, TAG_CHOCH, 51),
    (2, "SHORT", 40, 137.0, 145.0, 51, TAG_CHOCH, None),
    (3, "SHORT", 66, 112.0, 119.0, 77, TAG_BOS, None),
)


class TestBosChochClassification:
    """
    F -- BOS continues the bias, CHoCH flips it, and a virgin bias tags BOS.

    Pine:
        tag = bias == BEARISH ? 'CHoCH' : 'BOS'     (bullish break)
        tag = bias == BULLISH ? 'CHoCH' : 'BOS'     (bearish break)

    `var trend` starts at 0, which is NEITHER, so the first break in a symbol's
    history is always BOS. S1 walks the full cycle: BOS, CHoCH, CHoCH, BOS.
    """

    def test_the_break_chain_is_bos_choch_choch_bos(self) -> None:
        """Four breaks, four tags, in order, with the level and pivot they used."""
        structure, _blocks = _driven(S1)
        got = tuple((e[0], e[2], e[3], e[4], e[5])
                    for e in structure.internal_events)
        assert got == S1_BREAKS
        assert structure.swing_events == []
    def test_the_first_break_in_history_is_bos_because_bias_is_zero(self) -> None:
        """Not BULLISH, not BEARISH: zero. Both CHoCH comparisons are false."""
        fresh = LuxAlgoInternalStructure("SYN")
        assert fresh.internal_trend.bias == 0
        assert fresh.internal_trend.bias not in (BULLISH, BEARISH)
        structure, _blocks = _driven(S1)
        assert structure.internal_events[0][3] == TAG_BOS

    def test_each_break_leaves_the_bias_pointing_at_its_own_direction(self
                                                                     ) -> None:
        """Replaying the chain bar by bar pins the bias after every break."""
        structure = LuxAlgoInternalStructure("SYN")
        seen = 0
        expect_bias = {25: BEARISH, 38: BULLISH, 51: BEARISH, 77: BEARISH}
        for i, (o, h, l, c) in enumerate(S1):
            structure.step(i, _ts(i), o, h, l, c)
            if i in expect_bias:
                seen += 1
                assert structure.internal_trend.bias == expect_bias[i], i
                assert len(structure.internal_events) == seen
        assert seen == 4
        assert structure.internal_trend.bias == BEARISH

    def test_a_crossed_pivot_cannot_fire_twice(self) -> None:
        """`pivot.crossed` latches, so one pivot yields at most one break."""
        structure, _blocks = _driven(S1)
        used = [e[5] for e in structure.internal_events]
        assert used == sorted(used)
        assert len(set(used)) == len(used) == 4
        assert structure.internal_low.crossed is True

    def test_blocks_carry_the_tag_of_the_break_that_created_them(self) -> None:
        """One block per break, and the block's tag is the break's tag."""
        structure, blocks = _driven(S1)
        got = tuple((b.seq, b.direction, b.origin_bar_idx, b.barLow, b.barHigh,
                     b.created_bar_idx, b.tag, b.mitigated_bar_idx)
                    for b in blocks)
        assert got == S1_BLOCKS
        assert len(blocks) == len(structure.internal_events) == 4
        for event, block in zip(structure.internal_events, blocks):
            assert event[7] is block
            assert block.tag == event[3]
            assert block.created_bar_idx == event[0]
            assert block.pivot_bar_idx == event[5]

    def test_every_synthetic_zone_is_its_origin_candle(self) -> None:
        """B again, on data that needs no CSV: no ATR, so no swap, so equality."""
        structure, blocks = _driven(S1)
        assert not any(structure.high_volatility)
        for block in blocks:
            assert block.barLow == structure.lows[block.origin_bar_idx]
            assert block.barHigh == structure.highs[block.origin_bar_idx]
def _armed_for_extra_condition(swing_level) -> LuxAlgoInternalStructure:
    """A driven structure with a bullish internal crossover armed on bar 53.

    White-box on purpose. `extraCondition` compares the INTERNAL level against
    the SWING level, and on organic data the two are equal only by coincidence
    -- measured across 19,762 candles per symbol it never happened. Waiting for
    a coincidence is not a test, so the coincidence is constructed.
    """
    structure, _blocks = _driven(S1)
    structure.internal_high.currentLevel = 200.0
    structure.internal_high.crossed = False
    structure.internal_high.barIndex = 10
    structure.internal_high.barTime = structure.times[10]
    structure.internal_low.currentLevel = None      # keep the bearish branch out
    structure.swing_high.currentLevel = swing_level
    structure._prev_internal_high = 200.0
    structure._prev_close = 199.0
    structure.closes[53] = 201.0                    # closes ABOVE the level
    return structure


class TestExtraConditionUsesSwing:
    """
    G -- `extraCondition` reads the swing-50 layer, which is why the swing layer
    is modelled at all even though Swing Order Blocks are switched OFF.

    Pine:
        extraCondition = internal
            ? internalHigh.currentLevel != swingHigh.currentLevel and bullishBar
            : true

    Confluence Filter is OFF, so `bullishBar` is stuck at `true` and cannot
    filter. The level comparison survives and CAN.
    """

    def test_an_equal_swing_level_suppresses_the_break(self) -> None:
        """No event, no block, no bias change, and the pivot stays uncrossed."""
        structure = _armed_for_extra_condition(200.0)
        before = (len(structure.internal_events),
                  len(structure.all_order_blocks),
                  structure.internal_trend.bias)
        structure._display_structure(53, internal=True)
        assert len(structure.internal_events) == before[0]
        assert len(structure.all_order_blocks) == before[1]
        assert structure.internal_trend.bias == before[2] == BEARISH
        assert structure.internal_high.crossed is False
        assert len(structure.suppressed_by_extra_condition) == 1
        assert structure.suppressed_by_extra_condition[0] == (
            53, _ts(53), "BULLISH", 200.0, 10)
    def test_a_different_swing_level_lets_the_same_break_through(self) -> None:
        """Identical bar, identical crossover: only the swing level changed."""
        structure = _armed_for_extra_condition(999.0)
        before = len(structure.all_order_blocks)
        structure._display_structure(53, internal=True)
        assert structure.suppressed_by_extra_condition == []
        assert len(structure.internal_events) == 5
        assert len(structure.all_order_blocks) == before + 1
        assert structure.internal_high.crossed is True
        assert structure.internal_trend.bias == BULLISH
        block = structure.all_order_blocks[-1]
        assert block.tag == TAG_CHOCH        # the bias was BEARISH going in
        assert block.direction == "LONG"
        assert block.origin_bar_idx == 52
        assert (block.barLow, block.barHigh) == (81.0, 90.0)

    def test_a_missing_swing_level_does_not_suppress(self) -> None:
        """`na != x` is TRUE in Pine, so a virgin swing layer blocks nothing.

        Without this convention the first ~50 bars of every symbol would be
        silently filtered out, and the block counts in `COUNTERS` would drop.
        """
        structure = _armed_for_extra_condition(None)
        assert structure.swing_high.currentLevel is None
        structure._display_structure(53, internal=True)
        assert structure.suppressed_by_extra_condition == []
        assert structure.internal_high.crossed is True
        assert LS._pine_ne(None, None) is False
        assert LS._pine_ne(None, 1.0) is True
        assert LS._pine_ne(1.0, None) is True
        assert LS._pine_ne(1.0, 1.0) is False
        assert LS._pine_ne(1.0, 2.0) is True

    def test_the_swing_layer_never_consults_extra_condition(self) -> None:
        """For the swing branch Pine's ternary yields a literal `true`."""
        structure, _blocks = _driven(S1)
        structure.swing_high.currentLevel = 200.0
        structure.swing_high.crossed = False
        structure.swing_high.barIndex = 10
        structure.swing_low.currentLevel = None
        structure._prev_swing_high = 200.0
        structure._prev_close = 199.0
        structure.closes[53] = 201.0
        structure._display_structure(53, internal=False)
        assert len(structure.swing_events) == 1
        assert structure.swing_events[0][2] == "BULLISH"
        assert structure.swing_trend.bias == BULLISH
        assert structure.suppressed_by_extra_condition == []
        # Swing Order Blocks are OFF, so a swing break stores NO block.
        assert structure.swing_events[0][7] is None
        assert len(structure.all_order_blocks) == 4
def _pivot_at(structure: LuxAlgoInternalStructure, bar: int) -> LuxAlgoPivot:
    """A pivot object anchored to `bar`, for driving the origin search directly."""
    pivot = LuxAlgoPivot()
    pivot.barIndex = bar
    pivot.currentLevel = 50.0
    pivot.barTime = structure.times[bar]
    return pivot


class TestOriginSearchWindow:
    """
    H -- `slice(pivot.barIndex, bar_index)` excludes the break bar, and
    `indexof(max())` resolves a tie to the EARLIEST bar.

    Pine:
        a_rray      := parsedHighs.slice(p.barIndex, bar_index)
        parsedIndex := p.barIndex + a_rray.indexof(a_rray.max())

    Both details are tested with a planted window rather than by hunting for a
    natural tie: an extreme value is placed ON the break bar (which must be
    ignored) and the winning value is duplicated (the first copy must win).
    """

    def test_bearish_window_excludes_the_break_bar_and_ties_go_earliest(self
                                                                       ) -> None:
        """Two 9s at bars 12 and 14, and a 1000 on the break bar 20."""
        structure, _blocks = _driven(S1)
        structure.parsed_highs[10:20] = [1.0, 2.0, 9.0, 4.0, 9.0,
                                         6.0, 7.0, 8.0, 3.0, 5.0]
        structure.parsed_highs[20] = 1000.0
        block = structure._store_order_block(20, _pivot_at(structure, 10),
                                            BEARISH, TAG_BOS)
        assert block.origin_bar_idx == 12          # NOT 14, NOT 20
        assert block.barHigh == 9.0
        assert block.barHigh == structure.parsed_highs[12]
        assert block.barLow == structure.parsed_lows[12]
        assert block.pivot_bar_idx == 10
        assert block.created_bar_idx == 20
        assert block.tag == TAG_BOS and block.bias == BEARISH

    def test_bullish_window_searches_lows_with_the_same_two_rules(self) -> None:
        """Mirrored: argmin, two 1s at bars 12 and 14, -1000 on the break bar."""
        structure, _blocks = _driven(S1)
        structure.parsed_lows[10:20] = [9.0, 8.0, 1.0, 7.0, 1.0,
                                        6.0, 5.0, 4.0, 3.0, 2.0]
        structure.parsed_lows[20] = -1000.0
        block = structure._store_order_block(20, _pivot_at(structure, 10),
                                            BULLISH, TAG_CHOCH)
        assert block.origin_bar_idx == 12
        assert block.barLow == 1.0
        assert block.barHigh == structure.parsed_highs[12]
        assert block.tag == TAG_CHOCH and block.bias == BULLISH
    def test_the_search_reads_parsed_arrays_not_raw_highs(self) -> None:
        """The swap must reach the origin choice, or the ATR filter does nothing.

        Bar 16 is given a huge RAW high and an ordinary parsedHigh, which is
        exactly the shape a high-volatility candle has. It must lose.
        """
        structure, _blocks = _driven(S1)
        structure.parsed_highs[10:20] = [1.0] * 10
        structure.parsed_highs[13] = 7.0
        structure.highs[16] = 10_000.0
        structure.parsed_highs[16] = 0.5
        block = structure._store_order_block(20, _pivot_at(structure, 10),
                                            BEARISH, TAG_BOS)
        assert block.origin_bar_idx == 13
        assert block.barHigh == 7.0

    @pytest.mark.parametrize("pivot_bar,break_bar", [(20, 20), (25, 20)])
    def test_an_empty_or_backwards_window_refuses_to_guess(self, pivot_bar: int,
                                                           break_bar: int
                                                           ) -> None:
        """`slice` of an empty range has no max. Fail closed, do not invent."""
        structure, _blocks = _driven(S1)
        with pytest.raises(LuxAlgoStructureError, match="empty origin window"):
            structure._store_order_block(break_bar,
                                         _pivot_at(structure, pivot_bar),
                                         BEARISH, TAG_BOS)

    def test_a_pivot_with_no_bar_index_refuses_to_guess(self) -> None:
        """`pivot.barIndex` is na until the first leg flip; a break cannot use it."""
        structure, _blocks = _driven(S1)
        naked = LuxAlgoPivot()
        assert naked.barIndex is None
        with pytest.raises(LuxAlgoStructureError, match="no bar index"):
            structure._store_order_block(20, naked, BEARISH, TAG_BOS)

    def test_the_real_windows_never_include_their_break_bar(self) -> None:
        """Swept over S1's four blocks: origin is strictly inside [pivot, break)."""
        structure, blocks = _driven(S1)
        for block in blocks:
            assert block.pivot_bar_idx <= block.origin_bar_idx
            assert block.origin_bar_idx < block.created_bar_idx
def _box(name: str, bias: int = BEARISH) -> LuxAlgoOrderBlock:
    """A hand-built block whose zone bar 53 of S1 (high 85, low 76) mitigates."""
    return LuxAlgoOrderBlock(barHigh=10.0, barLow=5.0, barTime=T0, bias=bias,
                             asset=name, origin_bar_idx=1, created_bar_idx=2,
                             tag=TAG_BOS)


class TestPineMitigationIteration:
    """
    I -- `deleteOrderBlocks()` mutates the array it is iterating, and Pine's
    `for [index, x] in array` keeps incrementing the index anyway, so the element
    that shifts into the freed slot is SKIPPED on that bar.

    Measured on the real history this quirk fires 1,006 / 983 / 990 / 970 times
    per symbol, so it is not an edge case -- it is most of the mitigation
    behaviour. Reproducing it is why the block counts match the operator's chart.

    MITIGATION IS NOT INVALIDATION. Nothing here reaches the trading lifecycle;
    it decides which BOXES exist, and therefore which blocks the 100 cap evicts.
    """

    def test_three_mitigable_blocks_leave_the_middle_one_alive(self) -> None:
        """A is removed, B is skipped, C is removed. B survives the bar."""
        structure, _blocks = _driven(S1)
        assert structure.highs[53] == 85.0 and structure.lows[53] == 76.0
        a, b, c = _box("A"), _box("B"), _box("C")
        structure.live_order_blocks = [a, b, c]
        structure.mitigated_order_blocks = []
        structure.pine_loop_skips = []
        structure._delete_order_blocks(53)
        assert structure.live_order_blocks == [b]
        assert structure.mitigated_order_blocks == [a, c]
        assert structure.pine_loop_skips == [(53, b)]
        assert a.mitigated_bar_idx == 53 and a.mitigated_dt == _ts(53)
        assert c.mitigated_bar_idx == 53
        assert b.mitigated_bar_idx is None and b.mitigated_dt is None

    def test_the_quirk_is_the_only_reason_the_middle_block_survives(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flip CLEAN_MITIGATION and the identical input clears the whole array.

        This is the control that proves the assertion above is measuring the
        skip and not some property of the three fixtures.
        """
        monkeypatch.setattr(LS, "CLEAN_MITIGATION", True)
        structure, _blocks = _driven(S1)
        a, b, c = _box("A"), _box("B"), _box("C")
        structure.live_order_blocks = [a, b, c]
        structure.mitigated_order_blocks = []
        structure._delete_order_blocks(53)
        assert structure.live_order_blocks == []
        assert structure.mitigated_order_blocks == [a, b, c]
        assert LS.CLEAN_MITIGATION is True
    def test_the_module_default_keeps_the_quirk(self) -> None:
        """`CLEAN_MITIGATION` is a module constant, and it is False.

        It is not a constructor argument on purpose: turning it on would tidy up
        the loop and stop reproducing the operator's chart, which is not a
        per-instance choice.
        """
        assert LS.CLEAN_MITIGATION is False
        assert LS.OB_MITIGATION_HIGH_LOW is True

    def test_mitigation_triggers_on_the_wick_not_the_close(self) -> None:
        """High/Low mitigation: a bearish box dies to `high`, a bullish to `low`."""
        structure, _blocks = _driven(S1)
        bear = LuxAlgoOrderBlock(barHigh=84.9, barLow=80.0, barTime=T0,
                                 bias=BEARISH, asset="BEAR")
        bull = LuxAlgoOrderBlock(barHigh=90.0, barLow=76.1, barTime=T0,
                                 bias=BULLISH, asset="BULL")
        safe_bear = LuxAlgoOrderBlock(barHigh=85.0, barLow=80.0, barTime=T0,
                                      bias=BEARISH, asset="SAFEBEAR")
        safe_bull = LuxAlgoOrderBlock(barHigh=90.0, barLow=76.0, barTime=T0,
                                      bias=BULLISH, asset="SAFEBULL")
        structure.live_order_blocks = [bear, safe_bear, bull, safe_bull]
        structure.mitigated_order_blocks = []
        structure.pine_loop_skips = []
        structure._delete_order_blocks(53)
        # bear dies (85 > 84.9) and bull dies (76 < 76.1). The two "safe" boxes
        # sit exactly ON the wick, and the comparison is strict, so they survive
        # on their own merits -- they are also the two the loop skipped, which is
        # why this fixture cannot tell the two effects apart and does not try.
        assert [b.asset for b in structure.mitigated_order_blocks] == [
            "BEAR", "BULL"]
        assert [b.asset for b in structure.live_order_blocks] == [
            "SAFEBEAR", "SAFEBULL"]
        assert [b.asset for _bar, b in structure.pine_loop_skips] == [
            "SAFEBEAR", "SAFEBULL"]
        # Neither survivor would have been mitigated even if it had been tested.
        assert structure.highs[53] == safe_bear.barHigh
        assert structure.lows[53] == safe_bull.barLow

    def test_the_hundred_block_cap_evicts_the_oldest(self) -> None:
        """`array.pop()` takes the LAST element, and `unshift` puts the new
        block at index 0, so the cap discards the OLDEST live block."""
        structure, _blocks = _driven(S1)
        dummies = [_box(f"D{i}") for i in range(LS.ALGO_OB_CAP)]
        structure.live_order_blocks = list(dummies)
        structure.evicted_at_cap = []
        mitigated_before = list(structure.mitigated_order_blocks)
        block = structure._store_order_block(53, _pivot_at(structure, 40),
                                            BEARISH, TAG_BOS)
        assert LS.ALGO_OB_CAP == 100
        assert len(structure.live_order_blocks) == 100
        assert structure.live_order_blocks[0] is block
        assert structure.live_order_blocks[1] is dummies[0]
        assert structure.evicted_at_cap == [dummies[-1]]
        assert dummies[-1] not in structure.live_order_blocks
        # An evicted block is NOT a mitigated block; it left the chart because
        # the array is full, not because price took it out. S1 mitigated two
        # real blocks earlier (bars 37 and 51) and the eviction adds neither.
        assert len(mitigated_before) == 2
        assert structure.mitigated_order_blocks == mitigated_before
        assert dummies[-1] not in structure.mitigated_order_blocks
        assert dummies[-1].mitigated_bar_idx is None
        assert block.origin_bar_idx == 40      # tie between bars 40 and 41: 40
        assert (block.barLow, block.barHigh) == (137.0, 145.0)
def _kinds(events: List[object]) -> Dict[str, int]:
    """Count a lifecycle event stream by event type, for A/B comparison."""
    out: Dict[str, int] = {}
    for event in events:
        key = event.event_type.value          # type: ignore[attr-defined]
        out[key] = out.get(key, 0) + 1
    return out


class TestDefaultDetectorRegression:
    """J. The selector is real, it is fail-closed, and LuxAlgo is now the default.

    This class was written when the LuxAlgo reconstruction was a parity
    REFERENCE and the manual-spec extraction was production. That has been
    reversed by authorisation: `DETECTOR_LUXALGO_INTERNAL` is the canonical
    production detector, and `DETECTOR_MANUAL_SPEC` is retained as the
    explicitly-selectable research/oracle extraction. So the three
    "bare construction" tests below still do exactly the job they were written
    for -- they are the single class that would catch the production detector
    changing by accident -- with the expected answer inverted, and the direction
    of the inversion recorded here rather than silently edited in.

    Everything else in the class is unchanged: the selector still admits exactly
    two names, still refuses to guess, still has no `else` fallback, and still
    demonstrably changes what is detected.
    """

    def test_a_bare_lifecycle_is_the_luxalgo_detector(self) -> None:
        lifecycle = ManualSMCLifecycle(assets=["SYN"])
        assert lifecycle.detector == DETECTOR_LUXALGO_INTERNAL
        assert DETECTOR_LUXALGO_INTERNAL == "LUXALGO_INTERNAL"
        scanner = lifecycle._scanner_for("SYN")
        assert type(scanner) is LuxAlgoInternalOBScanner
        assert not isinstance(scanner, ManualSpecBOSScanner)

    def test_a_bare_strategy_is_the_luxalgo_detector(self) -> None:
        strategy = ManualSMCStrategy(assets=["SYN"])
        assert strategy.lifecycle.detector == DETECTOR_LUXALGO_INTERNAL
        assert type(strategy.lifecycle._scanner_for("SYN")) is (
            LuxAlgoInternalOBScanner)

    def test_a_bare_backtest_is_the_luxalgo_detector(self) -> None:
        backtest = ManualSMCBacktest(symbols=["SYN"])
        lifecycle = backtest.strategy.lifecycle
        assert lifecycle.detector == DETECTOR_LUXALGO_INTERNAL
        assert type(lifecycle._scanner_for("SYN")) is LuxAlgoInternalOBScanner

    def test_the_research_detector_is_still_explicitly_selectable(self) -> None:
        """The other half: nothing was removed, only the default moved."""
        for lifecycle in (
                ManualSMCLifecycle(assets=["SYN"],
                                   detector=DETECTOR_MANUAL_SPEC),
                ManualSMCStrategy(assets=["SYN"],
                                  detector=DETECTOR_MANUAL_SPEC).lifecycle,
                ManualSMCBacktest(symbols=["SYN"],
                                  detector=DETECTOR_MANUAL_SPEC)
                .strategy.lifecycle):
            assert lifecycle.detector == DETECTOR_MANUAL_SPEC
            assert type(lifecycle._scanner_for("SYN")) is ManualSpecBOSScanner
        assert DETECTOR_MANUAL_SPEC == "MANUAL_SPEC_BOS"

    def test_the_selector_admits_exactly_two_names(self) -> None:
        assert DETECTORS == frozenset(
            {"MANUAL_SPEC_BOS", "LUXALGO_INTERNAL"})
        assert DETECTOR_LUXALGO_INTERNAL == "LUXALGO_INTERNAL"
        # Either name may be given explicitly, and each means itself. Naming the
        # DEFAULT is a no-op; naming the other one selects the research
        # extraction. Neither is a guess.
        assert ManualSMCLifecycle(
            assets=["SYN"], detector=DETECTOR_MANUAL_SPEC).detector == (
            DETECTOR_MANUAL_SPEC)
        assert ManualSMCLifecycle(
            assets=["SYN"], detector=DETECTOR_LUXALGO_INTERNAL).detector == (
            DETECTOR_LUXALGO_INTERNAL)

    @pytest.mark.parametrize("bad", [
        "LUXALGO",                     # nearly right
        "luxalgo_internal",            # right name, wrong case
        "MANUAL_SPEC",                 # the old informal spelling
        "",                            # empty
        None,                          # missing
    ])
    def test_an_unknown_detector_refuses_to_guess(self, bad) -> None:
        """Safety rule #13: a default must never answer a safety question.

        There is no `else` fallback anywhere on this path, so an unrecognised
        name cannot quietly become the production detector.
        """
        for build in (
                lambda: ManualSMCLifecycle(assets=["SYN"], detector=bad),
                lambda: ManualSMCStrategy(assets=["SYN"], detector=bad),
                lambda: ManualSMCBacktest(symbols=["SYN"], detector=bad)):
            with pytest.raises(ValueError, match="unknown detector"):
                build()

    def test_a_lifecycle_cannot_be_talked_into_a_third_detector(self) -> None:
        """`_scanner_for()` keeps its own guard instead of an `else` fallback.

        Scanners are cached per asset and the constructor pre-builds one for
        every named asset, so a mutated attribute cannot retroactively change
        an existing scanner -- but the first UNSEEN asset still refuses to
        resolve rather than falling back to the manual spec.
        """
        lifecycle = ManualSMCLifecycle(assets=["SYN"])
        built = lifecycle._scanner_for("SYN")
        lifecycle.detector = "SOMETHING_ELSE"
        assert lifecycle._scanner_for("SYN") is built
        with pytest.raises(ValueError, match="unknown detector"):
            lifecycle._scanner_for("NEVER_SEEN")
        assert "NEVER_SEEN" not in lifecycle._scanners

    def test_naming_the_default_is_indistinguishable_from_not_naming_it(
            self) -> None:
        """
        A bare construction and an explicit `DETECTOR_LUXALGO_INTERNAL` must
        produce the same stream, event for event -- otherwise the default is
        reaching a different code path than the named value.

        The counts are LuxAlgo's own over S1, measured: four blocks rather than
        the manual spec's five. The manual-spec stream is not deleted, it is
        asserted immediately below in
        `test_the_selector_actually_changes_what_is_detected`, which is what
        keeps both numbers on the record.
        """
        implicit = _lifecycle_events(S1)
        explicit = _lifecycle_events(S1, detector=DETECTOR_LUXALGO_INTERNAL)
        assert _kinds(implicit) == _kinds(explicit)
        assert _kinds(implicit) == {
            "OB_CREATED": 4, "INVALIDATED": 2,
            "PRE_DISPLACEMENT_TOUCH": 2, "PROBE_CONFIRMED": 2}
        assert len(implicit) == len(explicit) == 10
        assert [(e.event_type, e.ob_id, e.bar_idx) for e in implicit] == [
            (e.event_type, e.ob_id, e.bar_idx) for e in explicit]

    def test_the_selector_actually_changes_what_is_detected(self) -> None:
        """The proof that the switch is wired to something.

        Both streams are non-empty and they DIFFER: the manual spec finds 5
        blocks over these 81 candles, the LuxAlgo reconstruction finds 4, and
        the two agree on neither the count nor the break bars. A selector that
        silently ignored its argument could not produce this.
        """
        native = _kinds(_lifecycle_events(S1, detector=DETECTOR_MANUAL_SPEC))
        lux = _kinds(_lifecycle_events(S1, detector=DETECTOR_LUXALGO_INTERNAL))
        assert lux == {"OB_CREATED": 4, "INVALIDATED": 2,
                       "PRE_DISPLACEMENT_TOUCH": 2, "PROBE_CONFIRMED": 2}
        assert native != lux
        assert native["OB_CREATED"] == 5 and lux["OB_CREATED"] == 4

    def test_the_two_detectors_disagree_about_the_break_bar(self) -> None:
        """Same two surviving origins, different creation bars.

        The trailing OB ids end in `<origin>_<break>`: the manual spec calls the
        break two bars after the origin, LuxAlgo calls it when the close crosses
        the pivot -- eleven bars later. Different zones, same downstream rules.
        """
        native = ManualSMCLifecycle(assets=["SYN"],
                                    detector=DETECTOR_MANUAL_SPEC)
        lux = ManualSMCLifecycle(assets=["SYN"],
                                 detector=DETECTOR_LUXALGO_INTERNAL)
        for lifecycle in (native, lux):
            for i, (o, h, l, c) in enumerate(S1):
                lifecycle.process_candle("SYN", i, _ts(i), o, h, l, c)
        assert sorted(native.live_obs) == [
            "MANUAL_SYN_SHORT_40_42", "MANUAL_SYN_SHORT_66_68"]
        assert sorted(lux.live_obs) == [
            "MANUAL_SYN_SHORT_40_51", "MANUAL_SYN_SHORT_66_77"]
        assert native.exits == [] and lux.exits == []


def _records(scanner_cls, bars: List[Tuple[float, float, float, float]],
             asset: str = "SYN") -> List[ManualOBRecord]:
    """Every record either detector emits over `bars`, production config."""
    scanner = scanner_cls(lookback=CFG.lookback, min_width=CFG.min_ob_width)
    out: List[ManualOBRecord] = []
    for i, (o, h, l, c) in enumerate(bars):
        out.extend(scanner.scan(asset, i, _ts(i), o, h, l, c, CFG))
    return out


class TestRecordSchemaIsUntouched:
    """K-guard. Carrying BOS/CHoCH must not add a field to the record.

    Two independent gates make a real dataclass field impossible: the oracle
    provenance test compares `ManualOBRecord`'s field names/order/types against
    the frozen research engine, and `state.expected_keys()` drives a snapshot
    codec that refuses BOTH missing and unknown keys. `bos_or_choch` is
    therefore a `ClassVar` assigned per instance -- invisible to
    `dataclasses.fields()` and to the codec, so neither gate moves.
    """

    def test_the_dataclass_still_has_exactly_twenty_nine_fields(self) -> None:
        names = tuple(f.name for f in dataclasses.fields(ManualOBRecord))
        assert len(names) == 29
        assert "bos_or_choch" not in names
        assert names[0] == "ob_id"
        assert names[-1] == "mfe_from_proximal"   # nothing appended

    def test_the_snapshot_codec_does_not_know_the_tag_exists(self) -> None:
        keys = ST.expected_keys(ManualOBRecord)
        assert len(keys) == 29
        assert "bos_or_choch" not in keys
        assert keys == tuple(f.name for f in dataclasses.fields(ManualOBRecord))

    def test_the_tag_is_a_class_variable_with_an_empty_default(self) -> None:
        assert ManualOBRecord.bos_or_choch == ""
        assert "ClassVar" in str(
            ManualOBRecord.__annotations__["bos_or_choch"])

    def test_setting_the_tag_on_one_record_touches_nothing_else(self) -> None:
        """A per-instance write must not become a class-wide default."""
        lux = _records(LuxAlgoInternalOBScanner, S1)
        native = _records(ManualSpecBOSScanner, S1)
        assert [r.bos_or_choch for r in lux] == [
            TAG_BOS, TAG_CHOCH, TAG_CHOCH, TAG_BOS]
        assert all("bos_or_choch" in r.__dict__ for r in lux)
        # The production detector does not classify breaks, so its records keep
        # the empty class default and no instance attribute at all.
        assert [r.bos_or_choch for r in native] == ["", "", "", "", ""]
        assert not any("bos_or_choch" in r.__dict__ for r in native)
        assert ManualOBRecord.bos_or_choch == ""
        assert len(dataclasses.fields(lux[0])) == 29

    def test_a_tagged_record_still_round_trips_through_the_codec(self) -> None:
        """The PER-RECORD codec still does not know the tag exists.

        This is the narrow claim, and it is still true after BOS/CHoCH
        persistence landed: `encode_dataclass` / `decode_dataclass` see only the
        29 dataclass fields, so a record pushed through them alone comes back
        unlabelled. The label is restored one level up, by
        `state.decode_ob_structure_tags`, from the payload's `ob_structure_tags`
        side map — which is exactly why the record schema above did not have to
        move. Full-payload survival is asserted in
        `test_manual_smc_state_roundtrip.py`.
        """
        record = _records(LuxAlgoInternalOBScanner, S1)[1]
        assert record.bos_or_choch == TAG_CHOCH
        payload = ST.encode_dataclass(record)
        assert tuple(payload) == ST.expected_keys(ManualOBRecord)
        assert "bos_or_choch" not in payload
        ST.require_exact_keys(payload, ST.expected_keys(ManualOBRecord),
                              "live_obs['SYN']")
        restored = ST.decode_dataclass(ManualOBRecord, payload,
                                       "live_obs['SYN']")
        assert restored == record            # every persisted field survives
        assert restored.bos_or_choch == ""   # this codec alone cannot carry it
        # ...and the side-map codec is what puts it back.
        pool = {restored.ob_id: restored}
        ST.decode_ob_structure_tags([[restored.ob_id, TAG_CHOCH]], pool,
                                    DETECTOR_LUXALGO_INTERNAL)
        assert restored.bos_or_choch == TAG_CHOCH


def _warm(bars: int = 5, asset: str = "SYN") -> LuxAlgoInternalStructure:
    """A structure that has consumed `bars` flat, valid candles (0..bars-1)."""
    structure = LuxAlgoInternalStructure(asset)
    for i in range(bars):
        structure.step(i, _ts(i), 100.0, 101.0, 99.0, 100.5)
    return structure


class TestStreamFailsClosed:
    """The origin search slices an unbounded array by absolute bar index.

    A gap, a repeat or a reordered bar would not raise on its own -- it would
    quietly return a DIFFERENT candle's high and low as a trade zone. These are
    deliberate additions to the Pine source (Pine cannot have a gap) and are
    disclosed as such in the parity report.
    """

    @pytest.mark.parametrize("bad_idx,expected", [
        (7, "expected bar_idx 5, got 7"),      # gap
        (4, "expected bar_idx 5, got 4"),      # repeat
        (2, "expected bar_idx 5, got 2"),      # out of order
        (0, "expected bar_idx 5, got 0"),      # restart
    ])
    def test_a_broken_bar_index_refuses_rather_than_guessing(
            self, bad_idx: int, expected: str) -> None:
        structure = _warm()
        with pytest.raises(LuxAlgoStructureError, match="non-contiguous"):
            structure.step(bad_idx, _ts(bad_idx), 100.0, 101.0, 99.0, 100.5)
        with pytest.raises(LuxAlgoStructureError, match=expected):
            structure.step(bad_idx, _ts(bad_idx), 100.0, 101.0, 99.0, 100.5)
        # The refusal did not consume the bar, so the correct one still fits.
        assert len(structure) == 5
        structure.step(5, _ts(5), 100.0, 101.0, 99.0, 100.5)
        assert len(structure) == 6

    def test_the_stream_may_start_anywhere_but_not_skip(self) -> None:
        """Real drives start at 0; `_abs()` exists so a slice can start later."""
        structure = LuxAlgoInternalStructure("SYN")
        with pytest.raises(LuxAlgoStructureError, match="no candle"):
            structure._abs(0)
        structure.step(500, _ts(500), 100.0, 101.0, 99.0, 100.5)
        assert structure._abs(0) == 500
        structure.step(501, _ts(501), 100.0, 101.0, 99.0, 100.5)
        assert structure._abs(1) == 501
        with pytest.raises(LuxAlgoStructureError, match="got 503"):
            structure.step(503, _ts(503), 100.0, 101.0, 99.0, 100.5)

    @pytest.mark.parametrize("field,candle", [
        ("open", (float("nan"), 101.0, 99.0, 100.5)),
        ("open", (float("inf"), 101.0, 99.0, 100.5)),
        ("high", (100.0, float("nan"), 99.0, 100.5)),
        ("high", (100.0, float("inf"), 99.0, 100.5)),
        ("low", (100.0, 101.0, float("nan"), 100.5)),
        ("low", (100.0, 101.0, float("-inf"), 100.5)),
        ("close", (100.0, 101.0, 99.0, float("nan"))),
        ("close", (100.0, 101.0, 99.0, float("-inf"))),
    ])
    def test_a_non_finite_price_is_refused(self, field: str, candle) -> None:
        """`open` is never consumed, but a non-finite open means the feed is
        corrupt and its high/low cannot be trusted either."""
        structure = _warm()
        with pytest.raises(LuxAlgoStructureError,
                           match=f"{field} is not a finite number"):
            structure.step(5, _ts(5), *candle)
        assert len(structure) == 5           # nothing was appended

    def test_an_inverted_candle_is_refused_not_repaired(self) -> None:
        """A high below its low would invert every zone derived from it."""
        structure = _warm()
        with pytest.raises(LuxAlgoStructureError, match=r"high 99\.0 < low"):
            structure.step(5, _ts(5), 100.0, 99.0, 101.0, 100.0)
        assert len(structure) == 5
        # high == low is NOT inverted: it is a doji, and it is accepted here.
        # The scanner is what refuses it, by its own named width rule.
        structure.step(5, _ts(5), 100.0, 100.0, 100.0, 100.0)
        assert len(structure) == 6

    def test_the_scanner_surfaces_the_same_refusal(self) -> None:
        """The guard lives in the structure, so the scanner cannot bypass it."""
        scanner = LuxAlgoInternalOBScanner(lookback=CFG.lookback,
                                           min_width=CFG.min_ob_width)
        for i in range(3):
            scanner.scan("SYN", i, _ts(i), 100.0, 101.0, 99.0, 100.5, CFG)
        with pytest.raises(LuxAlgoStructureError,
                           match="expected bar_idx 3, got 9"):
            scanner.scan("SYN", 9, _ts(9), 100.0, 101.0, 99.0, 100.5, CFG)


def _scan_wave(**kwargs) -> Tuple[LuxAlgoInternalOBScanner,
                                  List[ManualOBRecord]]:
    """Drive WAVE through a LuxAlgo scanner built with `kwargs`."""
    scanner = LuxAlgoInternalOBScanner(**kwargs)
    out: List[ManualOBRecord] = []
    for i, (o, h, l, c) in enumerate(WAVE):
        out.extend(scanner.scan("WAVE", i, _ts(i), o, h, l, c, CFG))
    return scanner, out


class TestScannerInterfaceParity:
    """The new detector is a DROP-IN for the old one, and says so honestly.

    Same `scan()` signature, same record type, same rejection accounting -- and
    the one place where the two are NOT interchangeable (snapshot capture) is
    asserted as a fail-closed refusal rather than quietly relaxed.
    """

    def test_both_detectors_expose_the_same_scan_signature(self) -> None:
        native = inspect.signature(ManualSpecBOSScanner.scan)
        lux = inspect.signature(LuxAlgoInternalOBScanner.scan)
        assert native == lux
        assert tuple(lux.parameters) == (
            "self", "asset", "bar_idx", "ts", "o", "h", "l", "c", "cfg")

    def test_lookback_is_accepted_and_deliberately_unused(self) -> None:
        """A disclosed deviation: Pine has no lookback, the interface does.

        `ManualSpecBOSScanner` trims its candle history to `lookback`; the
        LuxAlgo origin search slices an UNBOUNDED array by absolute bar index,
        exactly as a Pine array does, so honouring a lookback would break
        parity. The parameter is accepted for interface symmetry and ignored.
        """
        tight, a = _scan_wave(lookback=10, min_width=CFG.min_ob_width)
        loose, b = _scan_wave(lookback=999, min_width=CFG.min_ob_width)
        assert tight.lookback == 10 and loose.lookback == 999
        assert len(a) == len(b) == 17
        assert [r.ob_id for r in a] == [r.ob_id for r in b]
        assert [(r.ob_bottom, r.ob_top) for r in a] == [
            (r.ob_bottom, r.ob_top) for r in b]

    def test_min_width_is_the_one_setting_that_does_bite(self) -> None:
        """Proof the ignored parameter is not hiding an ignored constructor."""
        _default, keep = _scan_wave(lookback=10, min_width=CFG.min_ob_width)
        wide, few = _scan_wave(lookback=10, min_width=10.0)
        assert len(keep) == 17 and len(few) == 10
        assert wide.rejected_below_min_width == 7
        assert wide.rejected_non_positive_width == 0
        assert [r[0] for r in wide.rejected] == [REJECT_BELOW_MIN_WIDTH] * 7

    def test_reset_forgets_every_symbol_and_every_rejection(self) -> None:
        scanner, records = _scan_wave(lookback=10, min_width=10.0)
        assert scanner._structures and scanner.rejected and records
        scanner.reset()
        assert scanner._structures == {}
        assert scanner.rejected == []
        assert scanner.rejected_below_min_width == 0
        assert scanner.rejected_non_positive_width == 0
        # A reset scanner is a fresh scanner: same records, second time round.
        again: List[ManualOBRecord] = []
        for i, (o, h, l, c) in enumerate(WAVE):
            again.extend(scanner.scan("WAVE", i, _ts(i), o, h, l, c, CFG))
        assert [r.ob_id for r in again] == [r.ob_id for r in records]

    def test_both_detectors_can_now_be_snapshotted(self) -> None:
        """
        A REPLACED ASSERTION, recorded as such.

        This test previously asserted the opposite: that `encode_scanner()` was
        typed to the manual-spec scanner's `history`/`consumed` fields and
        refused a LuxAlgo scanner outright, so a LuxAlgo lifecycle could not be
        snapshotted at all. That was correct while LuxAlgo was a parity
        reference. Now that it is the authorised production detector, refusing to
        persist it would mean production silently losing recoverability, so the
        codec grew an explicit LuxAlgo encoding (schema 4) and this assertion is
        inverted rather than deleted.

        The fail-closed direction is unchanged and is asserted here: the two
        block shapes are DISJOINT, a scanner that disagrees with the declared
        detector is still refused at capture time, and the error type is still
        the same one. The full LuxAlgo persistence contract -- the bounded
        structure block, the 13-field digest, the two checksums, the
        cross-agreement check between the payload's two copies of the candle
        window, tamper refusal and detector-identity round trip -- lives in
        `test_manual_smc_state_roundtrip.py`.
        """
        native = ManualSMCLifecycle(assets=["BTCUSD"],
                                    detector=DETECTOR_MANUAL_SPEC)
        lux = ManualSMCLifecycle(assets=["BTCUSD"],
                                 detector=DETECTOR_LUXALGO_INTERNAL)
        for lifecycle, detector in ((native, DETECTOR_MANUAL_SPEC),
                                    (lux, DETECTOR_LUXALGO_INTERNAL)):
            snapshot = ST.capture_state(lifecycle)
            assert snapshot["scanners"]
            assert snapshot["detector"] == detector
            assert isinstance(ST.capture_state_json(lifecycle), str)
            assert ST.restore_state(snapshot).lifecycle.detector == detector

        # The two encodings are different shapes, not one relaxed shape.
        manual_block = ST.encode_scanner(
            "BTCUSD", native._scanner_for("BTCUSD"))
        lux_block = ST.encode_scanner("BTCUSD", lux._scanner_for("BTCUSD"))
        assert sorted(manual_block) == [
            "asset", "consumed", "history", "lookback", "min_width"]
        assert sorted(lux_block) == [
            "asset", "bars", "digest", "lookback", "min_width", "rejections",
            "structure"]
        assert set(manual_block) & set(lux_block) == {
            "asset", "lookback", "min_width"}

        # And a scanner that disagrees with the declared detector is refused,
        # in both directions, at capture time rather than at restore time.
        for lifecycle, wrong in ((native, DETECTOR_LUXALGO_INTERNAL),
                                 (lux, DETECTOR_MANUAL_SPEC)):
            lifecycle.detector = wrong
            with pytest.raises(ST.StateError):
                ST.capture_state(lifecycle)
        assert issubclass(ST.MalformedStateError, ST.StateError)
        assert issubclass(ST.StateError, RuntimeError)


# The four records the LuxAlgo detector emits over S1, measured. Zone edges come
# from the reconstruction; EVERYTHING ELSE is the untouched production rule:
# proximal is the near edge, entry sits `entry_depth_pct` into the zone, the
# stop is the far edge and the target is the fixed `fixed_tp_market_pct`.
S1_RECORDS = (
    # ob_id, direction, tag, bottom, top, prox, dist, entry, sl, tp
    ("MANUAL_SYN_SHORT_14_25", "SHORT", TAG_BOS, 126.0, 132.0,
     126.0, 132.0, 127.5, 132.0, 126.735),
    ("MANUAL_SYN_LONG_27_38", "LONG", TAG_CHOCH, 89.0, 96.0,
     96.0, 89.0, 94.25, 89.0, 94.8155),
    ("MANUAL_SYN_SHORT_40_51", "SHORT", TAG_CHOCH, 137.0, 145.0,
     137.0, 145.0, 139.0, 145.0, 138.166),
    ("MANUAL_SYN_SHORT_66_77", "SHORT", TAG_BOS, 112.0, 119.0,
     112.0, 119.0, 113.75, 119.0, 113.0675),
)


class TestDownstreamRulesAreUnchanged:
    """A LuxAlgo zone enters the SAME production pricing rules as a native one.

    The detector decides WHERE the zone is. It decides nothing else: no R:R
    admission exists on this path, the entry depth, the stop edge, the fixed
    take profit and the leverage cap are all read from the production config.
    """

    def test_every_record_prices_by_the_production_rules(self) -> None:
        records = _records(LuxAlgoInternalOBScanner, S1)
        assert len(records) == len(S1_RECORDS)
        for record, want in zip(records, S1_RECORDS):
            (ob_id, direction, tag, bottom, top,
             prox, dist, entry, sl, tp) = want
            assert record.ob_id == ob_id
            assert (record.direction, record.bos_or_choch) == (direction, tag)
            assert (record.ob_bottom, record.ob_top) == (bottom, top)
            assert (record.proximal, record.distal) == (prox, dist)
            assert record.ob_width == top - bottom
            assert record.entry_price == pytest.approx(entry, rel=1e-12)
            assert record.sl_price == sl               # always the far edge
            assert record.tp_price == pytest.approx(tp, rel=1e-12)

    def test_the_entry_depth_and_target_come_from_the_config(self) -> None:
        """Recompute both from the config alone -- no constant is hard-coded.

        `entry_depth_pct` is a FRACTION of the zone width (0.25 == 25% in, the
        authorised rule) while `fixed_tp_market_pct` is a true percentage of the
        entry price (0.60%). Both are read, neither is re-derived here.
        """
        assert CFG.entry_depth_pct == 0.25
        assert CFG.fixed_tp_market_pct == 0.60
        for record in _records(LuxAlgoInternalOBScanner, S1):
            depth = record.ob_width * CFG.entry_depth_pct
            sign = -1.0 if record.direction == "LONG" else +1.0
            assert record.entry_price == pytest.approx(
                record.proximal + sign * depth, rel=1e-12)
            assert record.tp_price == pytest.approx(
                record.entry_price * (1.0 - sign * CFG.fixed_tp_market_pct
                                      / 100.0), rel=1e-12)

    def test_leverage_is_the_authorised_band_and_nothing_else(self) -> None:
        """`min(cap, max_sl_account_risk_pct / sl_dist_pct)`, 1x..100x.

        Measured on these four synthetic zones: 9.92x, 6.28x, 8.11x, 7.58x --
        all inside the authorised band, none clamped, none coerced.
        """
        assert CFG.max_sl_account_risk_pct == 35.0
        assert CFG.applied_leverage_cap == 100.0
        for record in _records(LuxAlgoInternalOBScanner, S1):
            span = abs(record.entry_price - record.sl_price)
            assert record.sl_dist_pct == pytest.approx(
                span / record.entry_price * 100.0, rel=1e-12)
            theoretical = CFG.max_sl_account_risk_pct / record.sl_dist_pct
            assert record.theoretical_leverage == pytest.approx(
                theoretical, rel=1e-12)
            assert record.applied_leverage == pytest.approx(
                min(CFG.applied_leverage_cap, theoretical), rel=1e-12)
            assert 1.0 <= record.applied_leverage <= 100.0

    def test_no_record_is_refused_for_its_reward_to_risk_ratio(self) -> None:
        """There is no R:R admission on this path, for either detector.

        The 0.60% fixed target against a full-candle stop makes every one of
        these four setups worse than 1:1 -- and all four are still admitted,
        because reward:risk is a reported consequence, never an input.
        """
        for scanner_cls in (LuxAlgoInternalOBScanner, ManualSpecBOSScanner):
            records = _records(scanner_cls, S1)
            assert records
            for record in records:
                reward = abs(record.tp_price - record.entry_price)
                risk = abs(record.sl_price - record.entry_price)
                assert reward < risk          # all four are sub-1:1
                assert record.state is not None

