"""
test_manual_smc_lifecycle.py
============================
Phase 1 Step 2 acceptance gate for
`quantedge.strategy.manual_smc.lifecycle.ManualSMCLifecycle`.

Coverage map (the 12 mandated gate points):
    01  OB creation -> candidate/resting state ....... TestGate01ObCreation
    02  first entry touch -> fill .................... TestGate02EntryFill
    03  active trade blocks another OB ............... TestGate03ActiveTradeBlocks
    04  after close, another OB may enter ............ TestGate04ReentryAfterClose
    05  distal invalidation cancels a resting OB ..... TestGate05DistalInvalidation
    06  SL hit closes the active trade ............... TestGate06StopLossExit
    07  TP hit closes the active trade ............... TestGate07TakeProfitExit
    08  LONG mirrors SHORT ........................... TestGate08LongMirrorsShort
    09  deterministic scanner / pool order ........... TestGate09DeterministicOrder
    10  no time-based expiry ......................... TestGate10NoTimeBasedExpiry
    11  a consumed OB cannot respawn ................. TestGate11NoRespawn
    12  corrected lock vs the oracle's timestamp lock  TestGate12CorrectedGlobalLock

Data sources, in order of preference:
  * `BTC_CANDLES_REFERENCE` — the published golden window, reused from
    `test_manual_smc_btc_acceptance.py` (no new arbitrary fixture);
  * the canonical BTCUSD CSV already used by other tests in this suite, for
    the oracle differential in gate 12 (skipped if absent);
  * small hand-built OHLC sequences ONLY where the reference window does not
    contain the required transition (SL exit, resting-state invalidation,
    LONG fill, 72-bar timeout, cross-asset blocking).

Every synthetic sequence is checked against the module's own documented rules,
never against a reimplementation of the algorithm.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quantedge.strategy.manual_smc.lifecycle import (  # noqa: E402
    DISPLACEMENT_MODE,
    OUTCOME_SL,
    OUTCOME_TIMEOUT,
    OUTCOME_TP,
    REASON_DUAL_TOUCH,
    REASON_SL_HIT,
    REASON_TIMEOUT,
    REASON_TP_HIT,
    ManualLifecycleEventType as ET,
)
from quantedge.strategy.manual_smc.lifecycle import (  # noqa: E402
    ManualSMCLifecycle,
)
from quantedge.strategy.manual_smc.models import (  # noqa: E402
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBState as S,
)

from test_manual_smc_btc_acceptance import (  # noqa: E402
    BTC_CANDLES_REFERENCE,
    _make_ts,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
SYNTH_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ts(bar_idx: int) -> datetime:
    """Synthetic 1h clock, same shape as the reference window's clock."""
    return SYNTH_BASE + timedelta(hours=bar_idx)


def _drive(lc: ManualSMCLifecycle, asset: str, candles):
    """Feed (bar_idx, o, h, l, c) rows for ONE asset; return (bar_idx, event)."""
    out = []
    for (bar_idx, o, h, l, c) in candles:
        for ev in lc.process_candle(asset, bar_idx, _ts(bar_idx), o, h, l, c):
            out.append((bar_idx, ev))
    return out


def _of(events, event_type):
    return [(b, e) for (b, e) in events if e.event_type is event_type]


# A SHORT setup that reaches LIMIT_RESTING with the limit active from bar 4.
#   origin bar 0 (bullish): ob_top = close = 105.0, ob_bottom = low = 99.0
#   width 6.0 -> proximal 99.0, distal 105.0, entry 100.5, sl 105.0,
#   tp = 100.5 * 0.994 = 99.897
SHORT_SETUP = [
    (0, 100.0, 106.0, 99.0, 105.0),    # bullish origin
    (1, 104.0, 104.5, 97.0, 98.0),     # BOS: close 98.0 < ob_bottom 99.0
    (2, 98.0, 101.0, 97.5, 100.0),     # probe close 100.0 > proximal (+ touch)
    (3, 100.0, 100.2, 98.0, 98.5),     # pullback close 98.5 < proximal
]
SHORT_FILL = (4, 99.0, 101.0, 99.5, 100.0)      # high 101.0 >= entry 100.5
SHORT_ENTRY, SHORT_SL, SHORT_TP = 100.5, 105.0, 99.897
SHORT_OB_ID = "MANUAL_AAAUSD_SHORT_0_1"

# A LONG setup, mirrored.
#   origin bar 0 (bearish): ob_top = high = 106.0, ob_bottom = close = 100.0
#   width 6.0 -> proximal 106.0, distal 100.0, entry 104.5, sl 100.0,
#   tp = 104.5 * 1.006 = 105.127
LONG_SETUP = [
    (0, 105.0, 106.0, 99.0, 100.0),    # bearish origin
    (1, 101.0, 107.5, 100.5, 107.0),   # BOS: close 107.0 > ob_top 106.0
    (2, 107.0, 107.2, 104.0, 105.0),   # probe close 105.0 < proximal (+ touch)
    (3, 105.0, 107.0, 104.8, 106.5),   # pullback close 106.5 > proximal
]
LONG_FILL = (4, 105.5, 106.0, 104.0, 105.0)     # low 104.0 <= entry 104.5
LONG_ENTRY, LONG_SL, LONG_TP = 104.5, 100.0, 105.127
LONG_OB_ID = "MANUAL_AAAUSD_LONG_0_1"

# Inert candle: open == close means neither a bullish nor a bearish origin, so
# the scanner cannot admit anything new while one of these is streaming.
def _flat(bar_idx: int, price: float):
    return (bar_idx, price, price, price, price)


def _run_reference(assets=("BTCUSD",)):
    """Replay the published golden window through the lifecycle."""
    lc = ManualSMCLifecycle(assets=list(assets))
    events = []
    for (bar_idx, o, h, l, c) in BTC_CANDLES_REFERENCE:
        for asset in assets:
            for ev in lc.process_candle(asset, bar_idx, _make_ts(bar_idx),
                                        o, h, l, c):
                events.append((bar_idx, ev))
    return lc, events


class TestGate01ObCreation:
    """Gate 1 — a BOS admits an OB as a CANDIDATE, never as a live order."""

    def test_reference_window_creates_six_obs_in_scanner_order(self):
        _, events = _run_reference()
        created = [(b, e.ob_id) for (b, e) in _of(events, ET.OB_CREATED)]
        assert created == [
            (19571, "MANUAL_BTCUSD_SHORT_19569_19571"),
            (19574, "MANUAL_BTCUSD_SHORT_19573_19574"),
            (19580, "MANUAL_BTCUSD_SHORT_19577_19580"),
            (19583, "MANUAL_BTCUSD_SHORT_19582_19583"),
            (19587, "MANUAL_BTCUSD_LONG_19585_19587"),
            (19592, "MANUAL_BTCUSD_SHORT_19590_19592"),
        ]

    def test_new_ob_starts_awaiting_displacement_with_no_live_limit(self):
        lc = ManualSMCLifecycle(assets=["BTCUSD"])
        events = []
        for (bar_idx, o, h, l, c) in BTC_CANDLES_REFERENCE[:5]:
            events += [(bar_idx, e) for e in lc.process_candle(
                "BTCUSD", bar_idx, _make_ts(bar_idx), o, h, l, c)]
        created = _of(events, ET.OB_CREATED)
        assert len(created) == 1
        ob = lc.live_obs[created[0][1].ob_id]
        assert ob.state is S.AWAITING_DISPLACEMENT
        assert ob.probe_confirmed is False
        assert ob.limit_active_from_bar is None       # no resting order yet
        assert ob.displacement_confirmed_bar is None
        assert ob in lc.candidate_obs("BTCUSD")

    def test_created_ob_is_admitted_after_the_bos_bar_is_processed(self):
        """Break+1: the BOS candle can never be its own displacement candle."""
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP[:2])
        assert [e.event_type for (_b, e) in events] == [ET.OB_CREATED]
        assert events[0][0] == 1                      # BOS bar
        ob = lc.live_obs[SHORT_OB_ID]
        assert ob.origin_bar_idx == 0 and ob.bos_bar_idx == 1
        assert ob.state is S.AWAITING_DISPLACEMENT

    def test_identity_is_manual_smc_on_every_event(self):
        _, events = _run_reference()
        assert events
        for _b, e in events:
            assert e.strategy_name == MANUAL_SMC_STRATEGY_NAME == "MANUAL_SMC"
            assert e.strategy_version == MANUAL_SMC_STRATEGY_VERSION == "1.0.0"


class TestGate02EntryFill:
    """Gate 2 — the first entry touch AFTER displacement fills; earlier ones don't."""

    def test_reference_window_fill_prices_and_bar(self):
        _, events = _run_reference()
        fills = _of(events, ET.ENTRY_FILLED)
        assert [(b, e.ob_id) for (b, e) in fills] == [
            (19578, "MANUAL_BTCUSD_SHORT_19573_19574"),
            (19585, "MANUAL_BTCUSD_SHORT_19577_19580"),
        ]

    def test_displacement_sets_limit_active_from_next_bar_only(self):
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP)
        disp = _of(events, ET.DISPLACEMENT_CONFIRMED)
        assert len(disp) == 1 and disp[0][0] == 3
        ob = lc.live_obs[SHORT_OB_ID]
        assert ob.state is S.LIMIT_RESTING
        assert ob.displacement_confirmed_bar == 3
        assert ob.limit_active_from_bar == 4          # displacement_bar + 1
        assert lc.active_trade is None                # nothing filled yet

    def test_pre_displacement_touches_are_counted_but_never_fill(self):
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP)
        touches = _of(events, ET.PRE_DISPLACEMENT_TOUCH)
        assert [b for (b, _e) in touches] == [2]      # bar 2 high 101.0 >= 100.5
        assert not _of(events, ET.ENTRY_FILLED)
        assert lc.live_obs[SHORT_OB_ID].pre_displacement_touches == 1
        assert lc.live_obs[SHORT_OB_ID].first_touch_dt == _ts(2)

    def test_first_touch_after_displacement_fills_at_the_limit_price(self):
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + [SHORT_FILL])
        fills = _of(events, ET.ENTRY_FILLED)
        assert len(fills) == 1 and fills[0][0] == 4
        at = lc.active_trade
        assert at is not None
        assert at.entry_price == SHORT_ENTRY
        assert at.sl_price == SHORT_SL
        assert at.tp_price == SHORT_TP
        assert at.fill_bar_idx == 4 and at.fill_dt == _ts(4)
        assert at.risk_dist == abs(SHORT_ENTRY - SHORT_SL)
        assert at.reward_dist == abs(SHORT_TP - SHORT_ENTRY)
        ob = lc.live_obs[SHORT_OB_ID]
        assert ob.state is S.TRADE_ACTIVE
        assert ob.retest_number == 1
        assert ob.entry_bar_from_bos == 4 - 1
        assert ob.ob_age_at_entry_hours == 3.0        # bos bar 1 -> fill bar 4
        # A TRADE_ACTIVE OB is no longer a candidate.
        assert lc.candidate_obs("AAAUSD") == []
        assert lc.has_active_trade() is True


# ---------------------------------------------------------------------------
# Two-asset scenario used by gates 3, 4 and 11.
#
#   AAAUSD  fills at bar 4 and closes (TP) at bar 8.
#   BBBUSD  runs the same SHORT setup shifted +2 bars, so its limit is active
#           from bar 6 and it touches its entry on bars 6..10 — i.e. on candles
#           STRICTLY LATER than AAAUSD's fill. Under the oracle's timestamp-only
#           lock those touches would have filled and overwritten the active
#           trade; here they must be rejected.
# ---------------------------------------------------------------------------
_AAA = {b: ohlc for b, *ohlc in
        [[0, 100.0, 106.0, 99.0, 105.0], [1, 104.0, 104.5, 97.0, 98.0],
         [2, 98.0, 101.0, 97.5, 100.0], [3, 100.0, 100.2, 98.0, 98.5],
         [4, 99.0, 101.0, 99.5, 100.0],
         [8, 100.0, 100.5, 99.5, 99.8]]}          # bar 8: low 99.5 <= TP 99.897
for _b in (5, 6, 7):
    _AAA[_b] = [100.0, 100.0, 100.0, 100.0]       # inert; no TP/SL, no new OB
for _b in (9, 10):
    _AAA[_b] = [99.8, 99.8, 99.8, 99.8]

_BBB = {b: ohlc for b, *ohlc in
        [[2, 100.0, 106.0, 99.0, 105.0], [3, 104.0, 104.5, 97.0, 98.0],
         [4, 98.0, 101.0, 97.5, 100.0], [5, 100.0, 100.2, 98.0, 98.5]]}
for _b in (6, 7, 8, 9, 10):
    _BBB[_b] = [99.0, 101.0, 99.5, 100.0]         # touches entry every bar

BBB_OB_ID = "MANUAL_BBBUSD_SHORT_2_3"


def _run_two_assets(last_bar: int = 10):
    """Interleave the two assets in global chronological order, AAA then BBB."""
    lc = ManualSMCLifecycle(assets=["AAAUSD", "BBBUSD"])
    events = []
    for bar_idx in range(last_bar + 1):
        for asset, series in (("AAAUSD", _AAA), ("BBBUSD", _BBB)):
            if bar_idx not in series:
                continue
            o, h, l, c = series[bar_idx]
            for ev in lc.process_candle(asset, bar_idx, _ts(bar_idx),
                                        o, h, l, c):
                events.append((bar_idx, ev))
    return lc, events


class TestGate03ActiveTradeBlocks:
    """Gate 3 — one active trade globally, including on strictly later candles."""

    def test_later_candle_entry_is_rejected_while_a_trade_is_open(self):
        lc, events = _run_two_assets(last_bar=7)
        blocked = _of(events, ET.ENTRY_BLOCKED_BY_ACTIVE_TRADE)
        assert [(b, e.ob_id) for (b, e) in blocked] == [
            (6, BBB_OB_ID), (7, BBB_OB_ID)]
        for _b, e in blocked:
            assert "active trade already open on AAAUSD" in e.detail
        # AAAUSD's trade filled on bar 4 — strictly EARLIER than bars 6 and 7.
        assert lc.active_trade is not None
        assert lc.active_trade.asset == "AAAUSD"
        assert lc.active_trade.fill_bar_idx == 4
        assert len(lc.exits) == 0

    def test_a_rejected_ob_stays_resting_and_remains_eligible(self):
        lc, _events = _run_two_assets(last_bar=7)
        ob = lc.live_obs[BBB_OB_ID]
        assert ob.state is S.LIMIT_RESTING           # not invalidated, not filled
        assert ob.retest_number == 0                 # no fill was recorded
        assert ob in lc.candidate_obs("BBBUSD")

    def test_never_two_active_trades(self):
        for last_bar in range(0, 11):
            lc, _ = _run_two_assets(last_bar=last_bar)
            active = [ob for ob in lc.live_obs.values()
                      if ob.state is S.TRADE_ACTIVE]
            assert len(active) <= 1, f"two active trades by bar {last_bar}"
            assert (lc.active_trade is not None) == (len(active) == 1)

    def test_same_candle_reentry_is_also_rejected(self):
        """
        Gate 3's 'same candle' half: AAAUSD closes on bar 8 and BBBUSD touches
        its entry on that same bar. Intra-candle ordering is not determinable
        from OHLC, so the retained watermark rejects it.
        """
        lc, events = _run_two_assets(last_bar=8)
        bar8 = [e for (b, e) in events if b == 8]
        kinds = [e.event_type for e in bar8]
        assert ET.TRADE_CLOSED in kinds
        assert ET.ENTRY_BLOCKED_BY_ACTIVE_TRADE in kinds
        assert ET.ENTRY_FILLED not in kinds
        blocked = [e for e in bar8
                   if e.event_type is ET.ENTRY_BLOCKED_BY_ACTIVE_TRADE][0]
        assert "intra-candle re-entry ordering is not determinable" in blocked.detail
        assert lc.active_trade is None
        assert lc.live_obs[BBB_OB_ID].state is S.LIMIT_RESTING


class TestGate04ReentryAfterClose:
    """Gate 4 — once the active trade closes, another eligible OB may enter."""

    def test_blocked_ob_fills_on_the_first_bar_after_the_lock_clears(self):
        lc, events = _run_two_assets(last_bar=10)
        fills = [(b, e.ob_id) for (b, e) in _of(events, ET.ENTRY_FILLED)]
        assert fills == [(4, SHORT_OB_ID), (9, BBB_OB_ID)]
        closes = [(b, e.ob_id) for (b, e) in _of(events, ET.TRADE_CLOSED)]
        assert closes[0] == (8, "MANUAL_AAAUSD_SHORT_0_1")
        # Filled on bar 9: the first bar strictly after the close bar 8.
        assert lc.exits[0].exit_bar_idx == 8
        assert lc.exits[1].fill_bar_idx == 9

    def test_second_trade_is_recorded_not_silently_dropped(self):
        lc, _events = _run_two_assets(last_bar=10)
        assert len(lc.exits) == 2
        assert [x.asset for x in lc.exits] == ["AAAUSD", "BBBUSD"]
        assert [x.ob_id for x in lc.exits] == [
            "MANUAL_AAAUSD_SHORT_0_1", BBB_OB_ID]
        assert lc.active_trade is None

    def test_reference_window_second_trade_enters_after_the_first_closes(self):
        lc, events = _run_reference()
        closes = [(b, e.ob_id) for (b, e) in _of(events, ET.TRADE_CLOSED)]
        fills = [(b, e.ob_id) for (b, e) in _of(events, ET.ENTRY_FILLED)]
        assert closes[0][0] == 19580 and fills[1][0] == 19585
        assert fills[1][0] > closes[0][0]
        assert len(lc.exits) == 2


class TestGate05DistalInvalidation:
    """Gate 5 — a wick through the distal cancels the setup; there is no fill."""

    def test_resting_ob_is_cancelled_by_a_distal_wick(self):
        breach = (4, 100.0, 105.5, 99.0, 100.0)      # high 105.5 >= distal 105.0
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + [breach])
        inval = _of(events, ET.INVALIDATED)
        assert [(b, e.ob_id) for (b, e) in inval] == [(4, SHORT_OB_ID)]
        assert "while limit resting" in inval[0][1].detail
        # The same candle also touched the entry (high 105.5 >= 100.5), yet
        # invalidation wins and nothing filled.
        assert not _of(events, ET.ENTRY_FILLED)
        assert lc.active_trade is None
        assert lc.exits == []
        assert SHORT_OB_ID not in lc.live_obs        # removed from the pool
        assert lc.candidate_obs("AAAUSD") == []

    def test_invalidation_boundary_is_inclusive(self):
        touch_exactly = (4, 100.0, SHORT_SL, 99.0, 100.0)
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + [touch_exactly])
        assert _of(events, ET.INVALIDATED)

    def test_awaiting_displacement_ob_is_also_cancelled_by_a_distal_wick(self):
        lc, events = _run_reference()
        inval = [(b, e.ob_id) for (b, e) in _of(events, ET.INVALIDATED)]
        assert inval == [(19585, "MANUAL_BTCUSD_SHORT_19582_19583"),
                         (19592, "MANUAL_BTCUSD_LONG_19585_19587")]
        for _b, e in _of(events, ET.INVALIDATED):
            assert "before displacement" in e.detail
        for ob_id in ("MANUAL_BTCUSD_SHORT_19582_19583",
                      "MANUAL_BTCUSD_LONG_19585_19587"):
            assert ob_id not in lc.live_obs


class TestGate06StopLossExit:
    """Gate 6 — an SL wick closes the active trade at the SL price, r = -1."""

    SL_CANDLE = (5, 100.0, 106.0, 100.0, 105.5)   # high >= SL, low > TP

    def _run(self):
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + [SHORT_FILL, self.SL_CANDLE])
        return lc, events

    def test_sl_exit_fields(self):
        lc, events = self._run()
        assert [(b, e.ob_id) for (b, e) in _of(events, ET.TRADE_CLOSED)] == [
            (5, SHORT_OB_ID)]
        x = lc.exits[0]
        assert x.outcome == OUTCOME_SL
        assert x.reason_for_exit == REASON_SL_HIT
        assert x.exit_price == SHORT_SL
        assert x.realized_r == -1.0
        assert x.is_ambiguous is False
        assert x.narrative == f"SL breached at {SHORT_SL:.6f}."
        assert x.exit_bar_idx == 5 and x.exit_dt == _ts(5)
        assert x.holding_bars == 1 and x.holding_time_hours == 1.0
        assert x.displacement_mode == DISPLACEMENT_MODE
        assert x.strategy_name == "MANUAL_SMC"
        assert x.strategy_version == "1.0.0"

    def test_lock_is_released_and_ob_leaves_the_pool(self):
        lc, _events = self._run()
        assert lc.active_trade is None
        assert lc.has_active_trade() is False
        assert SHORT_OB_ID not in lc.live_obs

    def test_dual_touch_resolves_to_sl_and_is_flagged_ambiguous(self):
        dual = (5, 100.0, 106.0, 99.0, 100.0)      # high >= SL AND low <= TP
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        _drive(lc, "AAAUSD", SHORT_SETUP + [SHORT_FILL, dual])
        x = lc.exits[0]
        assert x.outcome == OUTCOME_SL
        assert x.reason_for_exit == REASON_DUAL_TOUCH
        assert x.exit_price == SHORT_SL
        assert x.realized_r == -1.0
        assert x.is_ambiguous is True
        assert "Conservative SL-first applied." in x.narrative


class TestGate07TakeProfitExit:
    """Gate 7 — a TP wick closes the trade at the TP price, r = reward/risk."""

    TP_CANDLE = (5, 100.0, 100.5, 99.5, 99.8)     # low <= TP, high < SL

    def test_tp_exit_fields(self):
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + [SHORT_FILL, self.TP_CANDLE])
        assert [(b, e.ob_id) for (b, e) in _of(events, ET.TRADE_CLOSED)] == [
            (5, SHORT_OB_ID)]
        x = lc.exits[0]
        assert x.outcome == OUTCOME_TP
        assert x.reason_for_exit == REASON_TP_HIT
        assert x.exit_price == SHORT_TP
        assert x.is_ambiguous is False
        assert x.realized_r == x.reward_dist / x.risk_dist
        assert x.narrative == f"Fixed +0.60% TP reached at {SHORT_TP:.6f}."
        assert lc.active_trade is None

    def test_reference_window_tp_exits_match_the_ob_geometry(self):
        lc, _events = _run_reference()
        assert len(lc.exits) == 2
        for x in lc.exits:
            assert x.outcome == OUTCOME_TP
            assert x.reason_for_exit == REASON_TP_HIT
            assert x.exit_price == x.tp_price
            assert x.realized_r == x.reward_dist / x.risk_dist
            assert x.realized_r > 0.0
            assert x.is_ambiguous is False
        assert lc.exits[0].ob_id == "MANUAL_BTCUSD_SHORT_19573_19574"
        assert lc.exits[0].entry_price == 79275.125
        assert lc.exits[0].sl_price == 79484.0
        assert lc.exits[1].ob_id == "MANUAL_BTCUSD_SHORT_19577_19580"

    def test_tp_r_multiple_is_dimensionless_and_capital_free(self):
        """Exit resolution carries no capital, PnL or fee field (sizing.py)."""
        lc, _events = _run_reference()
        x = lc.exits[0]
        for banned in ("capital", "pnl", "fee", "notional", "balance",
                       "position_size", "quantity"):
            assert not [f for f in vars(x) if banned in f], banned


class TestGate08LongMirrorsShort:
    """Gate 8 — the LONG path mirrors SHORT with inverted geometry."""

    def test_long_geometry_at_creation(self):
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        _drive(lc, "AAAUSD", LONG_SETUP[:2])
        ob = lc.live_obs[LONG_OB_ID]
        assert ob.direction == "LONG"
        assert ob.ob_top == 106.0 and ob.ob_bottom == 100.0   # high / close
        assert ob.proximal == ob.ob_top                       # inverted
        assert ob.distal == ob.ob_bottom
        assert ob.entry_price == LONG_ENTRY
        assert ob.sl_price == LONG_SL

    def test_long_probe_pullback_then_fill(self):
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", LONG_SETUP + [LONG_FILL])
        seq = [(b, e.event_type) for (b, e) in events]
        assert seq == [
            (1, ET.OB_CREATED),
            (2, ET.PRE_DISPLACEMENT_TOUCH),
            (2, ET.PROBE_CONFIRMED),
            (3, ET.DISPLACEMENT_CONFIRMED),
            (4, ET.ENTRY_FILLED),
        ]
        at = lc.active_trade
        assert at.direction == "LONG"
        assert at.entry_price == LONG_ENTRY
        assert at.sl_price == LONG_SL
        assert at.tp_price == LONG_TP
        assert lc.live_obs[LONG_OB_ID].limit_active_from_bar == 4

    def test_long_tp_and_sl_exits(self):
        for candle, outcome, reason, price in (
            ((5, 105.0, 105.5, 104.8, 105.3), OUTCOME_TP, REASON_TP_HIT, LONG_TP),
            ((5, 105.0, 105.1, 99.5, 100.0), OUTCOME_SL, REASON_SL_HIT, LONG_SL),
        ):
            lc = ManualSMCLifecycle(assets=["AAAUSD"])
            _drive(lc, "AAAUSD", LONG_SETUP + [LONG_FILL, candle])
            x = lc.exits[0]
            assert (x.outcome, x.reason_for_exit) == (outcome, reason)
            assert x.exit_price == price
            assert x.direction == "LONG"
            assert x.realized_r == (x.reward_dist / x.risk_dist
                                    if outcome == OUTCOME_TP else -1.0)

    def test_long_distal_invalidation_uses_the_low_not_the_high(self):
        breach = (4, 105.0, 106.0, 99.9, 105.0)    # low 99.9 <= distal 100.0
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", LONG_SETUP + [breach])
        assert [(b, e.ob_id) for (b, e) in _of(events, ET.INVALIDATED)] == [
            (4, LONG_OB_ID)]
        assert lc.active_trade is None and lc.exits == []


class TestGate09DeterministicOrder:
    """Gate 9 — multiple concurrent candidates keep a deterministic order."""

    def test_candidate_pool_order_is_creation_order(self):
        lc = ManualSMCLifecycle(assets=["BTCUSD"])
        created = []
        for (bar_idx, o, h, l, c) in BTC_CANDLES_REFERENCE:
            for ev in lc.process_candle("BTCUSD", bar_idx, _make_ts(bar_idx),
                                        o, h, l, c):
                if ev.event_type is ET.OB_CREATED:
                    created.append(ev.ob_id)
            live = [ob.ob_id for ob in lc.candidate_obs("BTCUSD")]
            assert live == [i for i in created if i in set(live)]

    def test_replay_is_bit_deterministic(self):
        def stream():
            lc, events = _run_reference()
            return [(b, e.event_type, e.ob_id, e.detail) for (b, e) in events], \
                   [(x.ob_id, x.outcome, x.realized_r) for x in lc.exits]
        assert stream() == stream()

    def test_scanner_emits_short_before_long_on_a_dual_bos_close(self):
        """
        The oracle evaluates SHORT before LONG, so a close satisfying both
        returns [SHORT, LONG]. Reaching that state by streaming is impossible
        (any candle that closes through one boundary consumes the opposite
        origin first), so the history is seeded directly to assert the ordering
        the extraction preserves.
        """
        lc = ManualSMCLifecycle(assets=["ZZZUSD"])
        scanner = lc._scanner_for("ZZZUSD")
        scanner._history.append((0, 104.5, 104.5, 100.0, 104.0, _ts(0)))
        scanner._history.append((1, 105.2, 108.0, 105.0, 107.0, _ts(1)))
        events = lc.process_candle("ZZZUSD", 2, _ts(2), 104.0, 104.8, 104.6,
                                   104.7)
        assert [(e.event_type, e.direction) for e in events] == [
            (ET.OB_CREATED, "SHORT"), (ET.OB_CREATED, "LONG")]
        assert [ob.ob_id for ob in lc.candidate_obs("ZZZUSD")] == [
            "MANUAL_ZZZUSD_SHORT_1_2", "MANUAL_ZZZUSD_LONG_0_2"]


class TestGate10NoTimeBasedExpiry:
    """
    Gate 10 — approved policy: NO time-based expiry while a limit rests.
    The 72-bar horizon applies strictly AFTER the fill.
    """

    REST_BARS = 120                                # >> max_holding_bars (72)

    def test_resting_limit_survives_far_beyond_the_holding_horizon(self):
        # Inert candles below the OB: no entry touch, no distal breach, and
        # (open == close) no new origin, so nothing else can interfere.
        idle = [_flat(4 + i, 98.0) for i in range(self.REST_BARS)]
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + idle)
        ob = lc.live_obs[SHORT_OB_ID]
        assert self.REST_BARS > lc.cfg.max_holding_bars
        assert ob.state is S.LIMIT_RESTING
        assert ob.limit_active_from_bar == 4
        assert lc.exits == []
        assert lc.active_trade is None
        assert not _of(events, ET.INVALIDATED)
        assert ob in lc.candidate_obs("AAAUSD")

    def test_resting_ob_still_fills_after_a_long_wait(self):
        idle = [_flat(4 + i, 98.0) for i in range(self.REST_BARS)]
        late_touch = (4 + self.REST_BARS, 99.0, 101.0, 99.5, 100.0)
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + idle + [late_touch])
        fills = _of(events, ET.ENTRY_FILLED)
        assert [(b, e.ob_id) for (b, e) in fills] == [
            (4 + self.REST_BARS, SHORT_OB_ID)]

    def test_holding_horizon_applies_only_after_the_fill(self):
        idle = [_flat(5 + i, 100.0) for i in range(80)]   # no TP/SL touch
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + [SHORT_FILL] + idle)
        closed = _of(events, ET.TRADE_CLOSED)
        assert len(closed) == 1
        x = lc.exits[0]
        assert x.outcome == OUTCOME_TIMEOUT
        assert x.reason_for_exit == REASON_TIMEOUT
        assert x.exit_price == 100.0                      # closed at the close
        assert x.holding_bars == lc.cfg.max_holding_bars  # 72
        assert x.exit_bar_idx == 4 + lc.cfg.max_holding_bars
        assert x.realized_r == (SHORT_ENTRY - 100.0) / x.risk_dist
        assert x.narrative == "72h horizon expired. Closed at 100.000000."

    def test_no_expiry_or_deadline_field_exists_anywhere(self):
        lc, _events = _run_reference()
        names = list(vars(lc.exits[0])) + list(vars(lc))
        assert not [n for n in names
                    if "expir" in n or "ttl" in n or "deadline" in n]


class TestGate11NoRespawn:
    """Gate 11 — one origin candle, one setup, forever."""

    def test_no_ob_id_is_created_twice_in_the_reference_window(self):
        _, events = _run_reference()
        ids = [e.ob_id for (_b, e) in _of(events, ET.OB_CREATED)]
        assert len(ids) == len(set(ids)) == 6

    def test_consumed_origin_is_not_recreated_after_its_trade_closes(self):
        # Bar 5 closes below bar 4's low, which would satisfy the SHORT BOS
        # rule for a *new* origin, and also re-crosses origin 0's boundary.
        reclose = (5, 100.0, 100.2, 97.0, 98.0)
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + [SHORT_FILL, reclose])
        created = [e.ob_id for (_b, e) in _of(events, ET.OB_CREATED)]
        assert created == [SHORT_OB_ID, "MANUAL_AAAUSD_SHORT_4_5"]
        assert created.count(SHORT_OB_ID) == 1
        # Origin 0 is consumed for good, in either direction.
        assert ("AAAUSD", 0) in lc._scanner_for("AAAUSD")._consumed
        assert lc.exits[0].ob_id == SHORT_OB_ID     # the closed trade

    def test_invalidated_origin_is_not_recreated(self):
        breach = (4, 100.0, 105.5, 99.0, 100.0)
        reclose = (5, 100.0, 100.2, 97.0, 98.0)
        lc = ManualSMCLifecycle(assets=["AAAUSD"])
        events = _drive(lc, "AAAUSD", SHORT_SETUP + [breach, reclose])
        created = [e.ob_id for (_b, e) in _of(events, ET.OB_CREATED)]
        assert SHORT_OB_ID in created and created.count(SHORT_OB_ID) == 1
        assert SHORT_OB_ID not in lc.live_obs

    def test_reset_clears_pool_trade_and_consumed_origins(self):
        lc, _events = _run_reference()
        assert lc.live_obs and lc.exits
        lc.reset()
        assert lc.live_obs == {} and lc.exits == []
        assert lc.active_trade is None
        assert lc.candidate_obs() == []
        assert lc._scanner_for("BTCUSD")._consumed == set()


# ---------------------------------------------------------------------------
# Gate 12 — differential against the frozen oracle on real canonical candles.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent.parent
CANONICAL = REPO_ROOT / "data" / "canonical" / "delta_exchange_india"
BTC_CSV = CANONICAL / "BTCUSD" / "1h" / "full_history.csv"
WIN_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
WIN_END = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _oracle_and_lifecycle():
    """Same asset, same candles, same window: oracle trades vs lifecycle exits."""
    from quantedge.ai.research.displacement_gated_retest_engine import (
        _load_canonical_candles,
        _to_utc_str,
        run_manual_spec_backtest,
    )

    oracle = run_manual_spec_backtest(
        data_base_dir=CANONICAL, symbols=["BTCUSD"],
        start_date=WIN_START, end_date=WIN_END)["trades_df"]

    lc = ManualSMCLifecycle(assets=["BTCUSD"])
    blocked, max_active = [], 0
    for i, row in enumerate(_load_canonical_candles(CANONICAL, "BTCUSD")):
        ts = row["timestamp"]
        if ts < WIN_START or ts > WIN_END:
            continue
        for ev in lc.process_candle("BTCUSD", i, ts, row["open"], row["high"],
                                    row["low"], row["close"]):
            if ev.event_type is ET.ENTRY_BLOCKED_BY_ACTIVE_TRADE:
                blocked.append((_to_utc_str(ev.ts), ev.ob_id, ev.detail))
        max_active = max(max_active, sum(
            1 for ob in lc.live_obs.values() if ob.state is S.TRADE_ACTIVE))
    return oracle, lc, blocked, max_active, _to_utc_str


@pytest.mark.skipif(not BTC_CSV.exists(),
                    reason="canonical BTCUSD 1h history not present")
class TestGate12CorrectedGlobalLock:
    """
    Gate 12 — the behaviour the oracle's timestamp-only lock failed to enforce.

    The oracle gated entry on `c_ts <= global_lock_until_dt`, which only blocks
    the SAME timestamp. On a strictly later bar a second fill OVERWROTE
    `active_trade`; the first trade's OB stayed TRADE_ACTIVE, was skipped by
    `if ob.state == TRADE_ACTIVE: continue`, and was never closed or recorded.
    This test pins that difference to concrete candles.
    """

    def test_shared_prefix_is_identical(self):
        """
        Up to the divergence point the two implementations agree trade-for-trade.
        This proves the difference below is the LOCK, not the geometry, the
        displacement rule or the exit resolution.
        """
        oracle, lc, _blocked, _mx, utc = _oracle_and_lifecycle()
        assert len(oracle) >= 6 and len(lc.exits) >= 6
        for i in range(6):
            o = oracle.iloc[i]
            n = lc.exits[i]
            assert n.direction == o["direction"]
            assert utc(n.fill_dt) == o["entry_time"]
            assert utc(n.exit_dt) == o["exit_time"]
            assert n.outcome == o["outcome"]
            assert round(n.entry_price, 6) == o["entry_price"]
            assert round(n.sl_price, 6) == o["sl_price"]
            assert round(n.tp_price, 6) == o["tp_price"]
            assert round(n.realized_r, 4) == o["realized_r"]

    def test_oracle_opened_two_trades_inside_an_open_trade(self):
        """The oracle's own output, unmodified: two fills the lock should have refused."""
        oracle, _lc, _blocked, _mx, _utc = _oracle_and_lifecycle()
        entries = set(oracle["entry_time"])
        assert "2026-01-10 17:00:00+00:00" in entries
        assert "2026-01-11 00:00:00+00:00" in entries

    def test_lifecycle_holds_one_trade_across_that_span(self):
        """
        The lifecycle fills 2026-01-10 06:00 and holds it until 2026-01-11 14:00,
        which strictly contains both oracle fills above.
        """
        _oracle, lc, _blocked, _mx, utc = _oracle_and_lifecycle()
        held = [e for e in lc.exits
                if utc(e.fill_dt) == "2026-01-10 06:00:00+00:00"]
        assert len(held) == 1
        assert held[0].direction == "LONG"
        assert utc(held[0].exit_dt) == "2026-01-11 14:00:00+00:00"
        assert held[0].outcome == OUTCOME_TP
        assert held[0].fill_dt < datetime(2026, 1, 10, 17, tzinfo=timezone.utc)
        assert held[0].exit_dt > datetime(2026, 1, 11, 0, tzinfo=timezone.utc)

    def test_oracle_silently_dropped_that_winner(self):
        """The oracle never records the trade it overwrote — it vanished."""
        oracle, _lc, _blocked, _mx, _utc = _oracle_and_lifecycle()
        assert "2026-01-10 06:00:00+00:00" not in set(oracle["entry_time"])

    def test_lifecycle_blocks_exactly_those_two_entries(self):
        """
        The two fills the oracle invented become explicit, auditable
        ENTRY_BLOCKED_BY_ACTIVE_TRADE events — refused, not silently lost.
        """
        _oracle, _lc, blocked, _mx, _utc = _oracle_and_lifecycle()
        assert [(ts, ob_id) for ts, ob_id, _d in blocked] == [
            ("2026-01-10 17:00:00+00:00", "MANUAL_BTCUSD_LONG_14118_14119"),
            ("2026-01-11 00:00:00+00:00", "MANUAL_BTCUSD_SHORT_14128_14129"),
        ]
        for _ts, _ob_id, detail in blocked:
            assert "active trade already open" in detail

    def test_blocked_obs_are_not_recorded_as_trades_by_the_lifecycle(self):
        _oracle, lc, _blocked, _mx, utc = _oracle_and_lifecycle()
        fills = {utc(e.fill_dt) for e in lc.exits}
        assert "2026-01-10 17:00:00+00:00" not in fills
        assert "2026-01-11 00:00:00+00:00" not in fills

    def test_lifecycle_never_holds_two_active_trades_on_real_data(self):
        _oracle, lc, _blocked, max_active, _utc = _oracle_and_lifecycle()
        assert max_active <= 1

    def test_no_lifecycle_ob_is_left_stranded_in_trade_active(self):
        """
        The oracle's defect stranded an OB in TRADE_ACTIVE forever. Every OB the
        lifecycle marks TRADE_ACTIVE is resolved and removed from the pool.
        """
        _oracle, lc, _blocked, _mx, _utc = _oracle_and_lifecycle()
        assert not [ob for ob in lc.live_obs.values()
                    if ob.state is S.TRADE_ACTIVE]
        assert lc.active_trade is None or lc.has_active_trade()


class TestConcurrentCandidatesWithSingleActiveTrade:
    """
    Mandate: the lifecycle must be able to REPRESENT many concurrent candidate
    OBs while still permitting only one active trade. Candidacy is unbounded;
    only ENTRY is exclusive.
    """

    def _four_asset_replay(self):
        assets = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
        lc = ManualSMCLifecycle(assets=assets)
        peak_candidates, peak_active = 0, 0
        for (bar_idx, o, h, l, c) in BTC_CANDLES_REFERENCE:
            for asset in assets:
                lc.process_candle(asset, bar_idx, _make_ts(bar_idx), o, h, l, c)
            peak_candidates = max(peak_candidates, len(lc.candidate_obs()))
            peak_active = max(peak_active, sum(
                1 for ob in lc.live_obs.values() if ob.state is S.TRADE_ACTIVE))
        return lc, peak_candidates, peak_active

    def test_at_least_five_concurrent_candidate_obs(self):
        _lc, peak_candidates, _pa = self._four_asset_replay()
        assert peak_candidates >= 5

    def test_never_two_trade_active_obs(self):
        _lc, _pc, peak_active = self._four_asset_replay()
        assert peak_active <= 1

    def test_candidates_span_multiple_assets_simultaneously(self):
        lc, _pc, _pa = self._four_asset_replay()
        assets_seen = {ob.asset for ob in lc.candidate_obs()}
        assert len(assets_seen) >= 2

    def test_single_asset_pool_holds_multiple_candidates(self):
        lc, events = _run_reference()
        assert len(_of(events, ET.OB_CREATED)) >= 5   # 6 OBs over the window
        assert len(lc.candidate_obs("BTCUSD")) >= 1


