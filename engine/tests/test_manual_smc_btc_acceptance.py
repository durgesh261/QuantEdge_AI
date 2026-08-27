"""
test_manual_smc_btc_acceptance.py
==================================
BTC Reference Acceptance Test for the Manual-Spec SMC Engine.

NON-NEGOTIABLE ACCEPTANCE CRITERIA (from forensic investigation + TradingView screenshot):

    Bar 19577  OB origin (bullish):  O=79129.0  H=79239.0  L=78725.5  C=79210.5
               -> ob_top = 79210.5 (CLOSE)   ob_bottom = 78725.5 (LOW)
               -> width  = 484.0
               -> entry  = 78725.5 + 0.25*484 = 78846.75
               -> distal = 79210.5  (SL, = origin.CLOSE — NOT 79239.0 HIGH)
               -> tp     = 78846.75 * 0.994 ≈ 78373.67

    Bar 19580  BOS confirmed (close=78175.5 < ob_bottom=78725.5)
               -> ManualOBRecord created and added to live_obs
               -> state = AWAITING_DISPLACEMENT,  probe_confirmed = False

    Bar 19581  close=78544.0 < proximal=78725.5  -> NOT a probe  -> no state change
               probe_confirmed remains False

    Bar 19582  H=78984.0 >= entry=78846.75  -> pre_displacement_touches = 1  (no fill!)
               close=78858.5 > proximal=78725.5  -> probe_confirmed = True
               state still AWAITING_DISPLACEMENT (not yet displaced)

    Bar 19583  H=78945.5 >= entry=78846.75  -> pre_displacement_touches = 2  (still no fill!)
               close=78512.5 < proximal=78725.5  -> pullback AFTER probe
               -> DISPLACEMENT CONFIRMED
               state = LIMIT_RESTING,  limit_active_from_bar = 19584
               (displacement bar itself cannot be entry bar — invariant §4)

    Bar 19584  LIMIT_RESTING, bar_local_idx=19584 >= limit_active_from_bar=19584
               H=78690.0 < entry=78846.75  -> NOT filled
               no invalidation (H=78690 < distal=79210.5)

    Bar 19585  LIMIT_RESTING, H=78933.0 >= entry=78846.75  -> ENTRY FILLED
               state = TRADE_ACTIVE
               no pre-exit on entry bar (TP/SL checked from NEXT bar)

    Bar 19586–19592  Trade active; SL=79210.5 not reached (max H=79208 < 79210.5)

    Bar 19593  SHORT TP: L=78215.5 <= tp≈78373.67  -> FILLED_TP
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure src on path (matches conftest.py convention)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from quantedge.ai.research.displacement_gated_retest_engine import (
    ManualOBState,
    ManualOBRecord,
    ManualSpecConfig,
    ManualSpecBOSScanner,
    _make_manual_ob,
    _manual_distal_breached,
    _manual_entry_touched,
    _manual_sl_hit,
    _manual_tp_hit,
)

# ---------------------------------------------------------------------------
# Embedded BTC 1H candle data — bars 19567 to 19595 (inclusive)
# ---------------------------------------------------------------------------
BTC_CANDLES_REFERENCE = [
    (19567, 80130.0, 80234.5, 79823.5, 79993.5),
    (19568, 79993.5, 80168.0, 79779.5, 79826.0),
    (19569, 79826.0, 80074.5, 79681.5, 79997.0),
    (19570, 79997.0, 80200.5, 79767.0, 79907.0),
    (19571, 79907.0, 80016.5, 79532.5, 79650.0),
    (19572, 79650.0, 79760.5, 79250.5, 79333.0),
    (19573, 79333.0, 79561.0, 79205.5, 79484.0),
    (19574, 79484.0, 79604.5, 79102.0, 79189.0),
    (19575, 79189.0, 79401.5, 79046.5, 79285.0),
    (19576, 79285.0, 79361.5, 79056.5, 79128.5),
    # Bar 19577: BULLISH candle — the OB origin
    (19577, 79129.0, 79239.0, 78725.5, 79210.5),
    (19578, 79210.5, 79310.0, 79040.5, 79098.0),
    (19579, 79098.0, 79195.0, 78860.0, 78894.5),
    # Bar 19580: BOS candle — close=78175.5 < ob_bottom=78725.5
    (19580, 78894.5, 78977.0, 78046.5, 78175.5),
    # Bar 19581: post-BOS, close below proximal -> no probe
    (19581, 78175.5, 78643.5, 77963.5, 78544.0),
    # Bar 19582: probe bar — close above proximal
    (19582, 78544.0, 78984.0, 78520.5, 78858.5),
    # Bar 19583: displacement confirmation — pullback close < proximal
    (19583, 78858.5, 78945.5, 78425.0, 78512.5),
    # Bar 19584: first resting bar — H < entry -> NOT filled
    (19584, 78512.5, 78690.0, 78318.5, 78571.0),
    # Bar 19585: ENTRY FILL — H=78933 >= entry=78846.75
    (19585, 78571.0, 78933.0, 78497.5, 78569.0),
    # Bars 19586–19592: trade active; SL not reached
    (19586, 78569.0, 79208.0, 78486.5, 78808.0),
    (19587, 78808.0, 79157.5, 78785.5, 79044.5),
    (19588, 79044.5, 79157.5, 78887.5, 79028.5),
    (19589, 79028.5, 79095.5, 78779.0, 78853.0),
    (19590, 78853.0, 79013.5, 78721.0, 78859.0),
    (19591, 78859.0, 79026.0, 78659.5, 78748.0),
    (19592, 78748.0, 78882.5, 78534.5, 78630.0),
    # Bar 19593: TP hit — L=78215.5 <= tp≈78373.67
    (19593, 78630.0, 78858.0, 78215.5, 78336.0),
    (19594, 78336.0, 78571.0, 78265.5, 78450.0),
    (19595, 78450.0, 78682.5, 78354.0, 78541.0),
]

EXPECTED_OB_TOP = 79210.5   # origin.CLOSE — NOT 79239.0 HIGH
EXPECTED_OB_BOTTOM = 78725.5
EXPECTED_OB_WIDTH = EXPECTED_OB_TOP - EXPECTED_OB_BOTTOM  # 485.0
EXPECTED_ENTRY = EXPECTED_OB_BOTTOM + 0.25 * EXPECTED_OB_WIDTH   # = 78846.75
EXPECTED_DISTAL = 79210.5                   # = ob_top = origin.CLOSE
EXPECTED_TP = EXPECTED_ENTRY * (1.0 - 0.006)  # ≈ 78373.6695
TOLS = 0.01

def _make_ts(bar_idx: int) -> datetime:
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(hours=bar_idx)

def _build_scanner_state_at_bos() -> tuple:
    cfg = ManualSpecConfig()
    scanner = ManualSpecBOSScanner(lookback=cfg.lookback, min_width=cfg.min_ob_width)
    new_obs_all = []
    for (bar_idx, o, h, l, c) in BTC_CANDLES_REFERENCE:
        if bar_idx >= 19580:
            break
        ts = _make_ts(bar_idx)
        obs = scanner.scan("BTCUSD", bar_idx, ts, o, h, l, c, cfg)
        new_obs_all.extend(obs)
    return scanner, cfg, new_obs_all

class TestOBBoundaryRules:
    def test_short_ob_top_is_close_not_high(self):
        cfg = ManualSpecConfig()
        ob = _make_manual_ob(
            asset="BTCUSD",
            bos_bar_idx=19580,
            bos_dt=_make_ts(19580),
            origin_bar_idx=19577,
            origin_dt=_make_ts(19577),
            direction="SHORT",
            ob_top=79210.5,
            ob_bottom=78725.5,
            cfg=cfg,
        )
        assert abs(ob.ob_top - EXPECTED_OB_TOP) < TOLS
        assert abs(ob.ob_bottom - EXPECTED_OB_BOTTOM) < TOLS

    def test_sl_equals_ob_top_not_high(self):
        cfg = ManualSpecConfig()
        ob = _make_manual_ob(
            asset="BTCUSD",
            bos_bar_idx=19580,
            bos_dt=_make_ts(19580),
            origin_bar_idx=19577,
            origin_dt=_make_ts(19577),
            direction="SHORT",
            ob_top=79210.5, ob_bottom=78725.5, cfg=cfg,
        )
        assert abs(ob.sl_price - EXPECTED_DISTAL) < TOLS
        assert abs(ob.distal - EXPECTED_DISTAL) < TOLS

    def test_entry_25pct_from_proximal(self):
        cfg = ManualSpecConfig()
        ob = _make_manual_ob(
            asset="BTCUSD",
            bos_bar_idx=19580,
            bos_dt=_make_ts(19580),
            origin_bar_idx=19577,
            origin_dt=_make_ts(19577),
            direction="SHORT",
            ob_top=79210.5, ob_bottom=78725.5, cfg=cfg,
        )
        assert abs(ob.entry_price - EXPECTED_ENTRY) < TOLS

    def test_tp_is_0_6_pct_below_entry(self):
        cfg = ManualSpecConfig()
        ob = _make_manual_ob(
            asset="BTCUSD",
            bos_bar_idx=19580,
            bos_dt=_make_ts(19580),
            origin_bar_idx=19577,
            origin_dt=_make_ts(19577),
            direction="SHORT",
            ob_top=79210.5, ob_bottom=78725.5, cfg=cfg,
        )
        assert abs(ob.tp_price - EXPECTED_TP) < TOLS

    def test_long_ob_bottom_is_close_not_low(self):
        cfg = ManualSpecConfig()
        ob = _make_manual_ob(
            asset="TEST", bos_bar_idx=10, bos_dt=_make_ts(10),
            origin_bar_idx=8, origin_dt=_make_ts(8),
            direction="LONG",
            ob_top=105.0,
            ob_bottom=97.0,
            cfg=cfg,
        )
        assert ob.ob_top == 105.0
        assert ob.ob_bottom == 97.0
        assert ob.distal == 97.0
        assert ob.proximal == 105.0

class TestBOSDetection:
    def test_scanner_detects_bos_at_bar_19580(self):
        scanner, cfg, _ = _build_scanner_state_at_bos()
        bos_bar = BTC_CANDLES_REFERENCE[13]
        assert bos_bar[0] == 19580
        bar_idx, o, h, l, c = bos_bar
        ts = _make_ts(bar_idx)
        new_obs = scanner.scan("BTCUSD", bar_idx, ts, o, h, l, c, cfg)
        short_obs = [ob for ob in new_obs if ob.direction == "SHORT" and ob.origin_bar_idx == 19577]
        assert len(short_obs) == 1

    def test_ob_origin_is_bar_19577(self):
        scanner, cfg, _ = _build_scanner_state_at_bos()
        bar_idx, o, h, l, c = BTC_CANDLES_REFERENCE[13]
        ts = _make_ts(bar_idx)
        new_obs = scanner.scan("BTCUSD", bar_idx, ts, o, h, l, c, cfg)
        ob = next(x for x in new_obs if x.direction == "SHORT")
        assert ob.origin_bar_idx == 19577

    def test_ob_boundaries_from_bos_scanner(self):
        scanner, cfg, _ = _build_scanner_state_at_bos()
        bar_idx, o, h, l, c = BTC_CANDLES_REFERENCE[13]
        ts = _make_ts(bar_idx)
        new_obs = scanner.scan("BTCUSD", bar_idx, ts, o, h, l, c, cfg)
        ob = next(x for x in new_obs if x.direction == "SHORT")
        assert abs(ob.ob_top - EXPECTED_OB_TOP) < TOLS
        assert abs(ob.ob_bottom - EXPECTED_OB_BOTTOM) < TOLS
        assert abs(ob.entry_price - EXPECTED_ENTRY) < TOLS
        assert abs(ob.sl_price - EXPECTED_DISTAL) < TOLS
        assert abs(ob.tp_price - EXPECTED_TP) < TOLS

    def test_bos_candle_not_used_as_origin(self):
        scanner, cfg, _ = _build_scanner_state_at_bos()
        bar_idx, o, h, l, c = BTC_CANDLES_REFERENCE[13]
        ts = _make_ts(bar_idx)
        new_obs = scanner.scan("BTCUSD", bar_idx, ts, o, h, l, c, cfg)
        for ob in new_obs:
            assert ob.origin_bar_idx != bar_idx

    def test_deduplication_prevents_second_bos_from_same_origin(self):
        scanner, cfg, _ = _build_scanner_state_at_bos()
        bar_idx, o, h, l, c = BTC_CANDLES_REFERENCE[13]
        ts = _make_ts(bar_idx)
        first = scanner.scan("BTCUSD", bar_idx, ts, o, h, l, c, cfg)
        assert any(ob.origin_bar_idx == 19577 for ob in first)

        bar_idx2, o2, h2, l2, c2 = BTC_CANDLES_REFERENCE[14]
        ts2 = _make_ts(bar_idx2)
        second = scanner.scan("BTCUSD", bar_idx2, ts2, o2, h2, l2, c2, cfg)
        assert not any(ob.origin_bar_idx == 19577 for ob in second)

class TestModeC:
    def _run_lifecycle_up_to(self, target_bar_idx: int):
        cfg = ManualSpecConfig()
        scanner = ManualSpecBOSScanner(lookback=cfg.lookback)
        live_obs = {}
        states = {}
        ref_ob = None

        for entry in BTC_CANDLES_REFERENCE:
            bar_idx, o, h, l, c = entry
            if bar_idx > target_bar_idx:
                break
            ts = _make_ts(bar_idx)

            for ob in list(live_obs.values()):
                if ob.state in (ManualOBState.TRADE_CLOSED, ManualOBState.INVALIDATED):
                    continue
                if ob.state == ManualOBState.TRADE_ACTIVE:
                    continue

                if ob.state == ManualOBState.AWAITING_DISPLACEMENT:
                    if _manual_distal_breached(ob, h, l):
                        ob.state = ManualOBState.INVALIDATED
                        continue
                    if _manual_entry_touched(ob, h, l):
                        ob.pre_displacement_touches += 1

                    if not ob.probe_confirmed:
                        if ob.direction == "SHORT" and c > ob.proximal:
                            ob.probe_confirmed = True
                    else:
                        if ob.direction == "SHORT" and c < ob.proximal:
                            ob.state = ManualOBState.LIMIT_RESTING
                            ob.displacement_confirmed_dt = ts
                            ob.displacement_confirmed_bar = bar_idx
                            ob.limit_active_from_bar = bar_idx + 1
                            continue

                elif ob.state == ManualOBState.LIMIT_RESTING:
                    if _manual_distal_breached(ob, h, l):
                        ob.state = ManualOBState.INVALIDATED
                        continue
                    if (ob.limit_active_from_bar is not None
                            and bar_idx >= ob.limit_active_from_bar
                            and _manual_entry_touched(ob, h, l)):
                        ob.state = ManualOBState.TRADE_ACTIVE
                        ob.retest_number += 1
                        ob.entry_bar_from_bos = bar_idx - ob.bos_bar_idx

            new_obs = scanner.scan("BTCUSD", bar_idx, ts, o, h, l, c, cfg)
            for ob in new_obs:
                live_obs[ob.ob_id] = ob

            ref_candidates = [ob for ob in live_obs.values() if ob.origin_bar_idx == 19577]
            if ref_candidates:
                ref_ob = ref_candidates[0]
                states[bar_idx] = {
                    "state": ref_ob.state,
                    "probe": ref_ob.probe_confirmed,
                    "pre_touches": ref_ob.pre_displacement_touches,
                    "limit_from": ref_ob.limit_active_from_bar,
                    "disp_bar": ref_ob.displacement_confirmed_bar,
                    "entry_bar_from_bos": ref_ob.entry_bar_from_bos,
                }
            elif bar_idx >= 19580:
                states[bar_idx] = states.get(bar_idx - 1, {})

        return ref_ob, states

    def test_no_probe_at_bar_19581(self):
        _, states = self._run_lifecycle_up_to(19581)
        s = states.get(19581, {})
        assert s.get("probe") is False
        assert s.get("state") == ManualOBState.AWAITING_DISPLACEMENT

    def test_probe_confirmed_at_bar_19582(self):
        _, states = self._run_lifecycle_up_to(19582)
        s = states.get(19582, {})
        assert s.get("probe") is True
        assert s.get("state") == ManualOBState.AWAITING_DISPLACEMENT

    def test_pre_displacement_touch_counted_at_19582(self):
        _, states = self._run_lifecycle_up_to(19582)
        s = states.get(19582, {})
        assert s.get("pre_touches", 0) >= 1
        assert s.get("state") != ManualOBState.TRADE_ACTIVE

    def test_displacement_confirmed_at_bar_19583(self):
        _, states = self._run_lifecycle_up_to(19583)
        s = states.get(19583, {})
        assert s.get("state") == ManualOBState.LIMIT_RESTING
        assert s.get("disp_bar") == 19583

    def test_limit_active_from_bar_is_19584(self):
        _, states = self._run_lifecycle_up_to(19583)
        s = states.get(19583, {})
        assert s.get("limit_from") == 19584

    def test_no_entry_on_displacement_bar(self):
        _, states = self._run_lifecycle_up_to(19583)
        s = states.get(19583, {})
        assert s.get("state") == ManualOBState.LIMIT_RESTING

class TestEntryAndOutcome:
    def _run_full_sim(self):
        cfg = ManualSpecConfig()
        scanner = ManualSpecBOSScanner(lookback=cfg.lookback)
        live_obs = {}
        states = {}
        exits = []
        active_trade = None

        for entry in BTC_CANDLES_REFERENCE:
            bar_idx, o, h, l, c = entry
            ts = _make_ts(bar_idx)

            if active_trade is not None:
                at = active_trade
                hit_tp = _manual_tp_hit(at["direction"], h, l, at["tp_price"])
                hit_sl = _manual_sl_hit(at["direction"], h, l, at["sl_price"])
                if hit_tp or hit_sl:
                    outcome = "FILLED_SL" if (hit_tp and hit_sl) else ("FILLED_TP" if hit_tp else "FILLED_SL")
                    at["ob"].state = ManualOBState.TRADE_CLOSED
                    exits.append({"bar": bar_idx, "outcome": outcome, "ob": at["ob"]})
                    live_obs.pop(at["ob"].ob_id, None)
                    active_trade = None

            for ob in list(live_obs.values()):
                if ob.state in (ManualOBState.TRADE_CLOSED, ManualOBState.INVALIDATED):
                    continue
                if ob.state == ManualOBState.TRADE_ACTIVE:
                    continue

                if ob.state == ManualOBState.AWAITING_DISPLACEMENT:
                    if _manual_distal_breached(ob, h, l):
                        ob.state = ManualOBState.INVALIDATED
                        continue
                    if _manual_entry_touched(ob, h, l):
                        ob.pre_displacement_touches += 1
                    if not ob.probe_confirmed:
                        if ob.direction == "SHORT" and c > ob.proximal:
                            ob.probe_confirmed = True
                    else:
                        if ob.direction == "SHORT" and c < ob.proximal:
                            ob.state = ManualOBState.LIMIT_RESTING
                            ob.displacement_confirmed_bar = bar_idx
                            ob.limit_active_from_bar = bar_idx + 1
                            continue

                elif ob.state == ManualOBState.LIMIT_RESTING:
                    if _manual_distal_breached(ob, h, l):
                        ob.state = ManualOBState.INVALIDATED
                        continue
                    if (ob.limit_active_from_bar is not None
                            and bar_idx >= ob.limit_active_from_bar
                            and _manual_entry_touched(ob, h, l)):
                        ob.state = ManualOBState.TRADE_ACTIVE
                        ob.retest_number += 1
                        ob.entry_bar_from_bos = bar_idx - ob.bos_bar_idx
                        active_trade = {
                            "ob": ob,
                            "direction": ob.direction,
                            "entry_price": ob.entry_price,
                            "sl_price": ob.sl_price,
                            "tp_price": ob.tp_price,
                            "fill_bar": bar_idx,
                        }
                        continue

            new_obs = scanner.scan("BTCUSD", bar_idx, ts, o, h, l, c, cfg)
            for ob in new_obs:
                live_obs[ob.ob_id] = ob

            ref_obs = [ob for ob in live_obs.values() if ob.origin_bar_idx == 19577]
            if ref_obs:
                ob = ref_obs[0]
                states[bar_idx] = {
                    "state": ob.state,
                    "entry_bfb": ob.entry_bar_from_bos,
                }

        return states, exits

    def test_not_filled_at_19584(self):
        states, _ = self._run_full_sim()
        s = states.get(19584, {})
        assert s.get("state") == ManualOBState.LIMIT_RESTING

    def test_entry_filled_at_19585(self):
        states, _ = self._run_full_sim()
        s = states.get(19585, {})
        assert s.get("state") == ManualOBState.TRADE_ACTIVE

    def test_entry_bar_from_bos_is_5(self):
        states, _ = self._run_full_sim()
        s = states.get(19585, {})
        assert s.get("entry_bfb") == 5

    def test_tp_hit_at_19593(self):
        _, exits = self._run_full_sim()
        btc_exit = next((e for e in exits if e["ob"].origin_bar_idx == 19577), None)
        assert btc_exit is not None
        assert btc_exit["outcome"] == "FILLED_TP"
        assert btc_exit["bar"] == 19593

    def test_wick_invalidation(self):
        cfg = ManualSpecConfig()
        ob = _make_manual_ob("BTC", 19580, _make_ts(19580), 19577, _make_ts(19577), "SHORT", 79210.5, 78725.5, cfg)
        assert _manual_distal_breached(ob, 79211.0, 78900.0)
        assert not _manual_distal_breached(ob, 79209.0, 78900.0)
