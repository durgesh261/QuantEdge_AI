"""
Manual SMC — Data Models (Phase 1 Step 1 extraction).
=====================================================

VERBATIM EXTRACTION from the frozen research oracle:
    engine/src/quantedge/ai/research/displacement_gated_retest_engine.py

Extracted symbols (oracle line refs at extraction time):
    ManualOBState      (oracle L1199)
    ManualOBRecord     (oracle L1212)
    ManualSpecConfig   (oracle L1273)

EXTRACTION CONTRACT
-------------------
This module is an EXTRACTION, not a redesign. Field names, field order,
default values, types and docstrings are preserved exactly so that
`dataclasses.asdict()` over an oracle record and over an extracted record
yield structurally identical dictionaries.

Arithmetic is float, exactly as in the oracle. This module deliberately
does NOT introduce Decimal: doing so would change rounding behaviour and
break oracle equivalence. Decimal quantization is a separate, later concern
(quantization.py) applied at the execution boundary only.

STRATEGY IDENTITY POLICY (approved)
-----------------------------------
Manual SMC is identified as MANUAL_SMC / 1.0.0 and must remain
distinguishable from the pre-existing LuxAlgo "SMC" / "2.1" strategy in
database records, logs, execution decisions, reconciliation and tests.

RESTING-ORDER EXPIRY POLICY (superseded — see below)
----------------------------------------------------
The ORIGINAL approved policy was: no time-based expiry at all while an entry
limit is resting. The manual specification supersedes it with a two-phase rule,
implemented in `lifecycle.py`:

  PHASE 1 — before the first touch (state AWAITING_DISPLACEMENT):
      NO expiry whatsoever. An untouched OB stays active indefinitely, across
      days and across a backtest warm-up boundary. OB age alone never
      invalidates it.

  PHASE 2 — after the first touch (state LIMIT_RESTING):
      the 25% limit is live for exactly `MANUAL_SMC_ENTRY_WINDOW_CANDLES` (3)
      candles INCLUSIVE of the first-touch candle. If the entry is not reached
      inside that window the order is cancelled and the OB is PERMANENTLY
      invalidated — it can never become active again.

A LIMIT_RESTING setup therefore ends in exactly one of:
    A. the entry is filled;
    B. the OB is invalidated by a distal wick breach;
    C. the 3-candle entry window expires;
    D. the account/global trade lock prevents admission and the window then
       expires;
    E. an explicit operational cancellation / kill-switch / reconciliation
       event cancels it.
`ManualSpecConfig.max_holding_bars` (72) applies ONLY to an ACTIVE TRADE
after entry fill. It is NOT a resting-order lifetime.

No production wiring. No execution wiring. Nothing imports this module yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Strategy identity (approved policy — see module docstring)
# ---------------------------------------------------------------------------
MANUAL_SMC_STRATEGY_NAME: str = "MANUAL_SMC"
MANUAL_SMC_STRATEGY_VERSION: str = "1.0.0"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class ManualOBState(Enum):
    """State machine for the manual-spec OB lifecycle."""
    AWAITING_DISPLACEMENT = "AWAITING_DISPLACEMENT"
    LIMIT_RESTING         = "LIMIT_RESTING"
    TRADE_ACTIVE          = "TRADE_ACTIVE"
    TRADE_CLOSED          = "TRADE_CLOSED"
    INVALIDATED           = "INVALIDATED"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ManualOBRecord:
    """
    Live OB record for the manual-spec strategy engine.

    OB boundary rules (direction-specific, proved by TradingView screenshot):
        SHORT (bearish OB from bullish origin candle):
            ob_top    = origin.CLOSE  (CRITICAL — NOT origin.high)
            ob_bottom = origin.LOW
            distal    = ob_top        SL = ob_top = origin.CLOSE
            proximal  = ob_bottom

        LONG (bullish OB from bearish origin candle):
            ob_top    = origin.HIGH
            ob_bottom = origin.CLOSE  (CRITICAL — NOT origin.low)
            distal    = ob_bottom     SL = ob_bottom = origin.CLOSE
            proximal  = ob_top
    """
    # Identity
    ob_id:                      str
    asset:                      str
    direction:                  str            # "SHORT" | "LONG"

    # Bar references (absolute index in asset DataFrame)
    origin_bar_idx:             int
    bos_bar_idx:                int
    bos_dt:                     datetime
    formation_dt:               datetime       # timestamp of origin candle

    # OB geometry (manual spec)
    ob_top:                     float
    ob_bottom:                  float
    ob_width:                   float
    proximal:                   float          # SHORT: ob_bottom  LONG: ob_top
    distal:                     float          # SHORT: ob_top     LONG: ob_bottom

    # Trade parameters
    entry_price:                float          # 25% from proximal
    sl_price:                   float          # = distal
    tp_price:                   float          # entry × (1 ∓ cfg.fixed_tp_market_pct/100)
    sl_dist_pct:                float
    theoretical_leverage:       float
    applied_leverage:           float

    # Mode C displacement state
    state:                      ManualOBState  = field(default=ManualOBState.AWAITING_DISPLACEMENT)
    probe_confirmed:            bool           = False
    displacement_confirmed_dt:  Optional[datetime] = None
    displacement_confirmed_bar: Optional[int]  = None
    # Limit is active starting at this bar index (= displacement_bar + 1)
    limit_active_from_bar:      Optional[int]  = None

    # Diagnostics
    pre_displacement_touches:   int            = 0
    first_touch_dt:             Optional[datetime] = None
    entry_bar_from_bos:         int            = 0
    ob_age_at_entry_hours:      float          = 0.0
    retest_number:              int            = 0
    mfe_from_proximal:          float          = 0.0


@dataclass
class ManualSpecConfig:
    """Configuration for the manual-spec backtest engine."""
    lookback:                   int   = 10      # bars to scan backward for origin
    entry_depth_pct:            float = 0.25    # 25% from proximal into OB
    fixed_tp_market_pct:        float = 0.60    # 0.60% from entry
    max_sl_account_risk_pct:    float = 35.0    # SL risk % of account
    applied_leverage_cap:       float = 100.0   # exchange cap
    fee_rate:                   float = 0.0008  # 0.08% round-trip
    max_holding_bars:           int   = 72      # timeout horizon (POST-FILL ONLY)
    starting_capital:           float = 10.0
    min_ob_width:               float = 1e-6    # reject zero-width OBs
    data_timeframe:             str   = "1h"


# Production-facing alias. Provably behaviour-neutral: identical object.
ManualSMCConfig = ManualSpecConfig


# ---------------------------------------------------------------------------
# PRODUCTION STRATEGY CONSTANTS (manual specification)
# ---------------------------------------------------------------------------
# `ManualSpecConfig`'s DEFAULTS are frozen against the research oracle so that
# `asdict(ManualSpecConfig()) == asdict(OracleManualSpecConfig())` keeps holding
# — that equality is the extraction-provenance gate in
# test_manual_smc_oracle_equivalence.py, and weakening it would destroy the only
# proof that the extracted geometry is bit-identical to the frozen research
# engine.
#
# AUTHORIZED VALUE: 0.60%. The production take profit is the SAME 0.60% the
# oracle uses; an earlier proposal to raise it to 0.65% was explicitly withdrawn.
# The constant is kept as the single injection point rather than being folded
# back into `ManualSpecConfig` for two reasons: mutating the oracle-pinned
# default is what would break the provenance proof above, and every production
# entry point already reads this one symbol, so a future AUTHORIZED change has
# exactly one place to go. There is exactly ONE take-profit number in production
# and this is it; `_make_manual_ob` still reads it from the config it is handed,
# so no second TP computation exists anywhere.
#
# Consequence of 0.60% that must not be hidden: against a stop at the far OB
# edge the R:R the Path A gateway sees is `0.0060 * entry / (0.75 * width)`, so
# the widest Path-A-executable OB is ~0.533% of price (it was ~0.578% at the
# withdrawn 0.65%). See `TestFrozenRiskRewardGate` in
# test_manual_smc_first_touch_window.py — the gateway is frozen and the TP is a
# flat percentage, so neither side of that arithmetic may be adjusted here.
MANUAL_SMC_FIXED_TP_PCT: float = 0.60

# The manual specification's entry window: once the first touch has armed the
# 25% limit, the limit is live for exactly three candles INCLUSIVE of the
# first-touch candle itself. Deliberately NOT a `ManualSpecConfig` field: adding
# one would change `fields(ManualSpecConfig)` and break the same provenance
# gate. `ManualSMCLifecycle` owns it (see `lifecycle.ENTRY_WINDOW_CANDLES`).
MANUAL_SMC_ENTRY_WINDOW_CANDLES: int = 3


def manual_smc_production_config(**overrides: object) -> ManualSpecConfig:
    """
    The config every PRODUCTION Manual SMC entry point must use.

    Value-identical to `ManualSpecConfig()` today: the authorized production take
    profit is the oracle's 0.60%, so `asdict()` of the two is equal. That equality
    is a CONSEQUENCE of the current authorization, not a guarantee — the seam
    exists so an authorized change to `MANUAL_SMC_FIXED_TP_PCT` reaches every
    production entry point without touching the oracle-pinned default. Callers may
    override any field; an unknown field raises `TypeError` from the dataclass
    constructor rather than being silently dropped.

    A bare `ManualSpecConfig()` remains the RESEARCH config and still reproduces
    the oracle exactly. Production code must not construct one directly.
    """
    params: dict = {"fixed_tp_market_pct": MANUAL_SMC_FIXED_TP_PCT}
    params.update(overrides)
    return ManualSpecConfig(**params)   # type: ignore[arg-type]


__all__ = [
    "MANUAL_SMC_STRATEGY_NAME",
    "MANUAL_SMC_STRATEGY_VERSION",
    "MANUAL_SMC_FIXED_TP_PCT",
    "MANUAL_SMC_ENTRY_WINDOW_CANDLES",
    "ManualOBState",
    "ManualOBRecord",
    "ManualSpecConfig",
    "ManualSMCConfig",
    "manual_smc_production_config",
]
