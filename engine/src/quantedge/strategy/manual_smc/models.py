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

RESTING-ORDER EXPIRY POLICY (approved)
--------------------------------------
There is NO arbitrary time-based expiry while an entry limit is resting.
A LIMIT_RESTING setup remains valid until exactly one of:
    A. the entry is filled;
    B. the OB is invalidated by a distal wick breach;
    C. the account/global trade lock prevents admission;
    D. an explicit operational cancellation / kill-switch / reconciliation
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
    tp_price:                   float          # entry × (1 ∓ 0.006)
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

__all__ = [
    "MANUAL_SMC_STRATEGY_NAME",
    "MANUAL_SMC_STRATEGY_VERSION",
    "ManualOBState",
    "ManualOBRecord",
    "ManualSpecConfig",
    "ManualSMCConfig",
]
