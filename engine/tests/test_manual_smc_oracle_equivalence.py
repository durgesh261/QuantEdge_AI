"""
test_manual_smc_oracle_equivalence.py
=====================================
Phase 1 Step 1 acceptance gate: the extracted production Manual SMC modules
under `quantedge.strategy.manual_smc` must be BEHAVIOURALLY IDENTICAL to the
frozen research oracle `quantedge.ai.research.displacement_gated_retest_engine`.

The research module is the ORACLE. It is never modified. This test does not
recompute expected values with a reimplementation of the algorithm — every
assertion compares extracted output against oracle output, or against the
already-published golden constants in test_manual_smc_btc_acceptance.py.

Reference data is REUSED from the existing golden acceptance test
(`BTC_CANDLES_REFERENCE`, bars 19567–19595). No new arbitrary fixture is
introduced. Over that window the oracle emits 6 OBs — 5 SHORT and 1 LONG —
so both direction paths are exercised by real reference data.
"""

import sys
from dataclasses import asdict, fields
from pathlib import Path

# Ensure src on path (matches conftest.py convention)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ---------------------------------------------------------------------------
# ORACLE (frozen research reference — never modified)
# ---------------------------------------------------------------------------
from quantedge.ai.research.displacement_gated_retest_engine import (  # noqa: E402
    ManualOBRecord as OracleManualOBRecord,
)
from quantedge.ai.research.displacement_gated_retest_engine import (  # noqa: E402
    ManualOBState as OracleManualOBState,
)
from quantedge.ai.research.displacement_gated_retest_engine import (  # noqa: E402
    ManualSpecBOSScanner as OracleScanner,
)
from quantedge.ai.research.displacement_gated_retest_engine import (  # noqa: E402
    ManualSpecConfig as OracleConfig,
)
from quantedge.ai.research.displacement_gated_retest_engine import (  # noqa: E402
    _make_manual_ob as oracle_make_manual_ob,
)
from quantedge.ai.research.displacement_gated_retest_engine import (  # noqa: E402
    _manual_distal_breached as oracle_distal_breached,
)
from quantedge.ai.research.displacement_gated_retest_engine import (  # noqa: E402
    _manual_entry_touched as oracle_entry_touched,
)
from quantedge.ai.research.displacement_gated_retest_engine import (  # noqa: E402
    _manual_sl_hit as oracle_sl_hit,
)
from quantedge.ai.research.displacement_gated_retest_engine import (  # noqa: E402
    _manual_tp_hit as oracle_tp_hit,
)

# ---------------------------------------------------------------------------
# EXTRACTED PRODUCTION IMPLEMENTATION (under test)
# ---------------------------------------------------------------------------
from quantedge.strategy.manual_smc import (  # noqa: E402
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
)
from quantedge.strategy.manual_smc.geometry import (  # noqa: E402
    _make_manual_ob as new_make_manual_ob,
)
from quantedge.strategy.manual_smc.geometry import (  # noqa: E402
    _manual_distal_breached as new_distal_breached,
)
from quantedge.strategy.manual_smc.geometry import (  # noqa: E402
    _manual_entry_touched as new_entry_touched,
)
from quantedge.strategy.manual_smc.geometry import (  # noqa: E402
    _manual_sl_hit as new_sl_hit,
)
from quantedge.strategy.manual_smc.geometry import (  # noqa: E402
    _manual_tp_hit as new_tp_hit,
)
from quantedge.strategy.manual_smc.models import (  # noqa: E402
    ManualOBRecord as NewManualOBRecord,
)
from quantedge.strategy.manual_smc.models import (  # noqa: E402
    ManualOBState as NewManualOBState,
)
from quantedge.strategy.manual_smc.models import (  # noqa: E402
    ManualSpecConfig as NewConfig,
)
from quantedge.strategy.manual_smc.scanner import (  # noqa: E402
    ManualSpecBOSScanner as NewScanner,
)

# ---------------------------------------------------------------------------
# Reference data reused from the existing golden acceptance test
# ---------------------------------------------------------------------------
from test_manual_smc_btc_acceptance import (  # noqa: E402
    BTC_CANDLES_REFERENCE,
    EXPECTED_ENTRY,
    EXPECTED_OB_BOTTOM,
    EXPECTED_OB_TOP,
    EXPECTED_TP,
    _make_ts,
)

# The 14 fields the Phase 1 Step 1 gate mandates comparing.
MANDATED_FIELDS = (
    "ob_top",
    "ob_bottom",
    "proximal",
    "distal",
    "entry_price",
    "sl_price",
    "tp_price",
    "sl_dist_pct",
    "theoretical_leverage",
    "applied_leverage",
    "ob_id",
    "direction",
    "origin_bar_idx",
    "bos_bar_idx",
)

def _run_oracle_scan():
    """Stream the reference window through the ORACLE scanner."""
    cfg = OracleConfig()
    scanner = OracleScanner(lookback=cfg.lookback, min_width=cfg.min_ob_width)
    obs = []
    for (bar_idx, o, h, l, c) in BTC_CANDLES_REFERENCE:
        obs.extend(scanner.scan("BTCUSD", bar_idx, _make_ts(bar_idx), o, h, l, c, cfg))
    return scanner, obs


def _run_new_scan():
    """Stream the reference window through the EXTRACTED scanner."""
    cfg = NewConfig()
    scanner = NewScanner(lookback=cfg.lookback, min_width=cfg.min_ob_width)
    obs = []
    for (bar_idx, o, h, l, c) in BTC_CANDLES_REFERENCE:
        obs.extend(scanner.scan("BTCUSD", bar_idx, _make_ts(bar_idx), o, h, l, c, cfg))
    return scanner, obs


def _pick(obs, origin_bar_idx):
    """Select the single OB produced by a given origin bar."""
    matches = [ob for ob in obs if ob.origin_bar_idx == origin_bar_idx]
    assert len(matches) == 1, f"expected exactly 1 OB from origin {origin_bar_idx}"
    return matches[0]


class TestStructuralEquivalence:
    """The extracted dataclasses must mirror the oracle field-for-field."""

    def test_ob_record_field_names_and_order_identical(self):
        oracle_names = [f.name for f in fields(OracleManualOBRecord)]
        new_names = [f.name for f in fields(NewManualOBRecord)]
        assert new_names == oracle_names

    def test_ob_record_field_types_identical(self):
        oracle_types = {f.name: str(f.type) for f in fields(OracleManualOBRecord)}
        new_types = {f.name: str(f.type) for f in fields(NewManualOBRecord)}
        assert new_types == oracle_types

    def test_ob_state_members_identical(self):
        assert [m.name for m in NewManualOBState] == [m.name for m in OracleManualOBState]
        assert [m.value for m in NewManualOBState] == [m.value for m in OracleManualOBState]

    def test_config_defaults_identical(self):
        assert asdict(NewConfig()) == asdict(OracleConfig())

    def test_config_field_names_and_order_identical(self):
        assert [f.name for f in fields(NewConfig)] == [f.name for f in fields(OracleConfig)]

    def test_no_resting_expiry_field_introduced(self):
        """
        Approved policy: Manual SMC has NO time-based expiry while an entry
        limit is resting. Guard against a future step smuggling one into the
        record under an expiry-like name.
        """
        names = [f.name for f in fields(NewManualOBRecord)]
        assert not [n for n in names if "expir" in n or "ttl" in n or "deadline" in n]


class TestStrategyIdentity:
    """Manual SMC must be distinguishable from the LuxAlgo 'SMC' / '2.1'."""

    def test_strategy_name_and_version(self):
        assert MANUAL_SMC_STRATEGY_NAME == "MANUAL_SMC"
        assert MANUAL_SMC_STRATEGY_VERSION == "1.0.0"

    def test_identity_is_not_luxalgo(self):
        assert MANUAL_SMC_STRATEGY_NAME != "SMC"
        assert MANUAL_SMC_STRATEGY_VERSION != "2.1"


class TestScannerOracleEquivalence:
    """Full-stream equivalence over the reference window."""

    def test_same_number_of_obs(self):
        _, oracle_obs = _run_oracle_scan()
        _, new_obs = _run_new_scan()
        assert len(new_obs) == len(oracle_obs)
        # Reference window is expected to yield real OBs in both directions.
        assert len(oracle_obs) > 0

    def test_emission_order_identical(self):
        _, oracle_obs = _run_oracle_scan()
        _, new_obs = _run_new_scan()
        oracle_seq = [(ob.direction, ob.origin_bar_idx, ob.bos_bar_idx) for ob in oracle_obs]
        new_seq = [(ob.direction, ob.origin_bar_idx, ob.bos_bar_idx) for ob in new_obs]
        assert new_seq == oracle_seq

    def test_mandated_fields_bit_identical(self):
        """Exact float equality — identical expressions must give identical bits."""
        _, oracle_obs = _run_oracle_scan()
        _, new_obs = _run_new_scan()
        for oracle_ob, new_ob in zip(oracle_obs, new_obs):
            for name in MANDATED_FIELDS:
                o_val = getattr(oracle_ob, name)
                n_val = getattr(new_ob, name)
                assert n_val == o_val, f"{new_ob.ob_id}.{name}: {n_val!r} != oracle {o_val!r}"

    def test_every_field_identical(self):
        """Stronger than the mandated subset: the whole record, incl. timestamps."""
        _, oracle_obs = _run_oracle_scan()
        _, new_obs = _run_new_scan()
        for oracle_ob, new_ob in zip(oracle_obs, new_obs):
            o_dict = asdict(oracle_ob)
            n_dict = asdict(new_ob)
            # Enum members belong to different classes; compare by value.
            o_dict["state"] = oracle_ob.state.value
            n_dict["state"] = new_ob.state.value
            assert n_dict == o_dict

    def test_both_directions_covered_by_reference_data(self):
        _, new_obs = _run_new_scan()
        directions = {ob.direction for ob in new_obs}
        assert directions == {"SHORT", "LONG"}

    def test_initial_state_is_awaiting_displacement(self):
        _, new_obs = _run_new_scan()
        for ob in new_obs:
            assert ob.state is NewManualOBState.AWAITING_DISPLACEMENT
            assert ob.probe_confirmed is False
            assert ob.limit_active_from_bar is None
            assert ob.displacement_confirmed_bar is None


class TestGoldenShortGeometry:
    """Bar 19577 origin → bar 19580 BOS, against the published golden values."""

    def test_bos_at_bar_19580_from_origin_19577(self):
        _, new_obs = _run_new_scan()
        ob = _pick(new_obs, 19577)
        assert ob.direction == "SHORT"
        assert ob.bos_bar_idx == 19580
        assert ob.origin_bar_idx == 19577

    def test_bos_candle_is_never_its_own_origin(self):
        _, new_obs = _run_new_scan()
        for ob in new_obs:
            assert ob.origin_bar_idx < ob.bos_bar_idx

    def test_golden_boundaries_entry_sl_tp(self):
        _, new_obs = _run_new_scan()
        ob = _pick(new_obs, 19577)
        assert ob.ob_top == EXPECTED_OB_TOP          # origin.CLOSE, not HIGH
        assert ob.ob_bottom == EXPECTED_OB_BOTTOM    # origin.LOW
        assert ob.distal == EXPECTED_OB_TOP
        assert ob.proximal == EXPECTED_OB_BOTTOM
        assert abs(ob.entry_price - EXPECTED_ENTRY) < 0.01
        assert ob.sl_price == ob.distal
        assert abs(ob.tp_price - EXPECTED_TP) < 0.01

    def test_ob_id_format_matches_oracle(self):
        _, oracle_obs = _run_oracle_scan()
        _, new_obs = _run_new_scan()
        assert _pick(new_obs, 19577).ob_id == _pick(oracle_obs, 19577).ob_id
        assert _pick(new_obs, 19577).ob_id == "MANUAL_BTCUSD_SHORT_19577_19580"

    def test_short_leverage_clamped_not_raised(self):
        """`min(cap, theoretical)` must clamp; exceeding the cap is not an error."""
        _, new_obs = _run_new_scan()
        for ob in (o for o in new_obs if o.direction == "SHORT"):
            assert ob.applied_leverage == min(100.0, ob.theoretical_leverage)
            assert ob.applied_leverage <= 100.0


class TestLongGeometry:
    """The reference window emits a real LONG OB (origin 19585, BOS 19587)."""

    def test_long_ob_matches_oracle_field_for_field(self):
        _, oracle_obs = _run_oracle_scan()
        _, new_obs = _run_new_scan()
        oracle_long = [ob for ob in oracle_obs if ob.direction == "LONG"]
        new_long = [ob for ob in new_obs if ob.direction == "LONG"]
        assert len(new_long) == len(oracle_long) == 1
        for name in MANDATED_FIELDS:
            assert getattr(new_long[0], name) == getattr(oracle_long[0], name)

    def test_long_distal_is_bottom_and_proximal_is_top(self):
        _, new_obs = _run_new_scan()
        ob = next(o for o in new_obs if o.direction == "LONG")
        assert ob.distal == ob.ob_bottom     # origin.CLOSE
        assert ob.proximal == ob.ob_top      # origin.HIGH
        assert ob.sl_price == ob.ob_bottom

    def test_long_entry_is_25pct_below_proximal_and_tp_above(self):
        _, new_obs = _run_new_scan()
        ob = next(o for o in new_obs if o.direction == "LONG")
        assert ob.entry_price == ob.ob_top - 0.25 * ob.ob_width
        assert ob.tp_price == ob.entry_price * 1.006
        assert ob.tp_price > ob.entry_price

    def test_long_synthetic_case_matches_oracle(self):
        """
        Reuses the LONG boundary case already asserted by the golden
        acceptance test (ob_top=105.0, ob_bottom=97.0).
        """
        args = dict(
            asset="TEST", bos_bar_idx=10, bos_dt=_make_ts(10),
            origin_bar_idx=8, origin_dt=_make_ts(8),
            direction="LONG", ob_top=105.0, ob_bottom=97.0,
        )
        oracle_ob = oracle_make_manual_ob(cfg=OracleConfig(), **args)
        new_ob = new_make_manual_ob(cfg=NewConfig(), **args)
        o_dict, n_dict = asdict(oracle_ob), asdict(new_ob)
        o_dict["state"] = oracle_ob.state.value
        n_dict["state"] = new_ob.state.value
        assert n_dict == o_dict


class TestDeduplication:
    """One origin candle → one setup, forever."""

    def test_consumed_sets_identical(self):
        oracle_scanner, _ = _run_oracle_scan()
        new_scanner, _ = _run_new_scan()
        assert new_scanner._consumed == oracle_scanner._consumed

    def test_no_origin_appears_twice(self):
        _, new_obs = _run_new_scan()
        keys = [(ob.direction, ob.origin_bar_idx) for ob in new_obs]
        origins = [ob.origin_bar_idx for ob in new_obs]
        assert len(set(keys)) == len(keys)
        assert len(set(origins)) == len(origins)

    def test_origin_19577_not_respawned_by_later_closes(self):
        """
        Bars 19581+ also close below 78725.5 at times, but 19577 is consumed.
        """
        _, new_obs = _run_new_scan()
        assert len([ob for ob in new_obs if ob.origin_bar_idx == 19577]) == 1

    def test_reset_clears_history_and_consumed(self):
        new_scanner, _ = _run_new_scan()
        assert len(new_scanner._consumed) > 0
        new_scanner.reset()
        assert len(new_scanner._consumed) == 0
        assert len(new_scanner._history) == 0

    def test_history_maxlen_matches_oracle(self):
        oracle_scanner, _ = _run_oracle_scan()
        new_scanner, _ = _run_new_scan()
        assert new_scanner._history.maxlen == oracle_scanner._history.maxlen
        assert new_scanner._history.maxlen == OracleConfig().lookback + 1


class TestPredicateEquivalence:
    """All four wick predicates must agree with the oracle on every bar."""

    def test_all_predicates_agree_on_every_reference_bar(self):
        _, oracle_obs = _run_oracle_scan()
        _, new_obs = _run_new_scan()
        checks = 0
        for oracle_ob, new_ob in zip(oracle_obs, new_obs):
            for (_bar_idx, _o, h, l, _c) in BTC_CANDLES_REFERENCE:
                assert new_distal_breached(new_ob, h, l) == oracle_distal_breached(
                    oracle_ob, h, l)
                assert new_entry_touched(new_ob, h, l) == oracle_entry_touched(
                    oracle_ob, h, l)
                assert new_sl_hit(new_ob.direction, h, l, new_ob.sl_price) == oracle_sl_hit(
                    oracle_ob.direction, h, l, oracle_ob.sl_price)
                assert new_tp_hit(new_ob.direction, h, l, new_ob.tp_price) == oracle_tp_hit(
                    oracle_ob.direction, h, l, oracle_ob.tp_price)
                checks += 4
        assert checks > 0

    def test_wick_invalidation_boundary_is_inclusive(self):
        """Reuses the golden acceptance test's boundary case."""
        _, new_obs = _run_new_scan()
        ob = _pick(new_obs, 19577)
        assert new_distal_breached(ob, 79211.0, 78900.0) is True
        assert new_distal_breached(ob, ob.distal, 78900.0) is True
        assert new_distal_breached(ob, 79209.0, 78900.0) is False



