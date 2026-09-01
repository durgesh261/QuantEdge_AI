"""
Manual SMC Strategy Package — Production Extraction.
====================================================

Authoritative production home of the Manual TradingView SMC strategy.

Identity:  MANUAL_SMC / 1.0.0  (distinct from the LuxAlgo "SMC" / "2.1")

Phase 1 Step 1 scope: models, geometry and the causal BOS scanner — extracted
verbatim from the frozen research oracle
`quantedge.ai.research.displacement_gated_retest_engine` and proven equivalent
to it by `engine/tests/test_manual_smc_oracle_equivalence.py`.

Phase 1 Step 2 scope: `lifecycle.py` — the single source of truth for OB
lifecycle transitions (creation → displacement → resting limit → fill → exit),
covered by `engine/tests/test_manual_smc_lifecycle.py`. It carries ONE
intentional deviation from the oracle: the global single-active-trade gate is
`active_trade is not None`, not the oracle's same-timestamp watermark.

Phase 1 Step 3 scope: `portfolio.py` and `sizing.py`, covered by
`engine/tests/test_manual_smc_portfolio_sizing.py`.

  * `portfolio.py` — `PortfolioLock`: the single globally exclusive trade slot
    as an explicit, auditable object. Rejects while `active_trade is not None`;
    releases only on the holder's token PLUS a terminal outcome (safety rules
    #13, #14). Retains the oracle's close-timestamp watermark as a secondary
    conservative guard.
  * `sizing.py` — the oracle's capital mathematics, expression-for-expression:
    `min(100, 35 / sl_dist_pct)` leverage clamp, notional, the 0.08% round-trip
    fee, PnL and compounding. It computes NO order quantity: a symbol's
    contract value defaults to the non-numeric `UNVERIFIED` sentinel, and
    `resolve_order_quantity` requires both a verified value and an explicitly
    injected converter, so an unknown Delta semantic cannot silently become a
    live order size (safety rules #8, #15, #16).

Phase 1 Step 4 scope: `quantization.py`, covered by
`engine/tests/test_manual_smc_quantization.py`.

  * ONE canonical tick-size quantizer, `quantize_price`, and nothing else in
    this package rounds a price. Exact `Decimal` `divmod` on the tick grid at
    precision 60 — no float price rounding anywhere. The tick size is read from
    `ProductSpecification.tick_size` through the structural `TickSizeSpec`
    protocol; there are NO per-symbol rounding rules and NO default tick, so a
    caller without a product specification gets an exception rather than a
    guess (safety rules #15, #16).
  * Rounding direction is a REQUIRED argument and the half-tick tie rule is
    explicit (`NEAREST_HALF_UP` / `NEAREST_HALF_DOWN`); there is no bare
    `NEAREST`. `quantize_bracket` applies the conservative direction per leg so
    the quantized |entry − SL| can only shrink, which keeps the leverage
    `sizing.py` derived from the unquantized distance inside the 35% budget.
  * Order quantity, contract value, `min_size` and `size_step` are absent by
    design — `sizing.py` already refuses to invent Delta's contract semantics
    and this module does not reopen that door.

Phase 1 Step 5 scope: `state.py`, covered by
`engine/tests/test_manual_smc_state_roundtrip.py`.

  * Capture/restore of everything `ManualSMCLifecycle` needs to resume
    deterministically: the live OB pool (insertion order preserved, because
    `candidate_obs()` iterates it), the single active trade (persisted BY
    REFERENCE so the restored trade's `ob` is the IDENTICAL pool object), the
    exit log, `_last_trade_closed_dt`, and each asset's scanner — its history
    deque AND its consumed-origin set, without which a restored scanner would
    re-emit an OB for an origin it already used.
  * An explicit `MANUAL_SMC_STATE` / version-1 schema carrying the
    MANUAL_SMC / 1.0.0 identity, so a snapshot of the LuxAlgo "SMC" / "2.1"
    strategy can never be loaded as Manual SMC state.
  * Fails closed on malformed payloads, missing fields, UNKNOWN fields,
    unknown enum values, an unsupported schema version, a config that changed
    between crash and resume, and any state that is not self-consistent (two
    TRADE_ACTIVE OBs, a dangling active-trade reference, a scanner that
    disagrees with the config) — safety rules #13, #14, #16.
  * The processed-candle watermark (`CandleWatermark`) lives HERE, not in the
    lifecycle: `ManualSMCLifecycle` tracks no such marker, and Step 5 must not
    modify it. See the module docstring's ATOMICITY REQUIREMENT — the
    lifecycle mutation and the watermark advance are two operations, and a
    capture torn between them is DETECTED and refused rather than resumed.
  * No database, no SQL, no ORM, no file I/O: `capture_state` returns a dict
    and `dumps_state` returns a string. Where those bytes are stored is Step 6.

Phase 1 Step 6 scope: `strategy.py`, covered by
`engine/tests/test_manual_smc_strategy.py`.

  * `ManualSMCStrategy.evaluate_closed_candle()` — the orchestration layer over
    the eight modules above. It ADDS NO STRATEGY RULES: no BOS test, no
    displacement test, no entry test, no invalidation test, no leverage formula
    and no lock rule are restated here. The only numeric literals in the whole
    module are `0.0`, `1` and `1e-9`.
  * `ManualSMCLifecycle.process_candle()` is called EXACTLY ONCE per candle, so
    its load-bearing order (resolve the active trade → update the OBs → scan and
    admit last, which is what makes admission break+1) is preserved intact.
    Everything else happens strictly before it (input refusal) or strictly after
    it (lock reconciliation, sizing, quantization, reporting).
  * Duplicate, out-of-order and globally stale candles are REFUSED, not skipped:
    the OB update sweep is not idempotent, so a replayed candle could re-touch
    an entry level and fill twice. The pre-check runs before the lifecycle is
    touched, and `CandleWatermark.advance()` remains the authority afterwards —
    a disagreement surfaces as `TornStateError`, never as a silent replay.
  * Every take profit is the OB's ABSOLUTE `tp_price`. No result type carries a
    percentage-TP field, so the application's `StrategyDecision.
    take_profit_target_pct` (a 60% return ON MARGIN, which coincides with a
    Manual SMC price move — an authorized 0.60% under both the production and
    the research config — only at one particular leverage) can never be mistaken
    for one; `TP_SOURCE` records the provenance.
  * Quantization happens at the OUTPUT boundary only, against injected product
    specifications. No `ManualOBRecord` is mutated, and an asset with no spec
    yields a NON-executable setup rather than a guessed grid (safety rules #15,
    #16). A quantization refusal is REPORTED per setup, not raised, because it
    runs after the lifecycle has already mutated.
  * The lock and the lifecycle are cross-checked every candle (safety rules #13,
    #14), and `PERSISTENCE_IS_ATOMIC` is `False` with
    `unpersisted_strategy_state()` naming what the Step 5 snapshot does not
    carry. `from_state` REFUSES to guess the balance as of an open trade's fill.
  * `StrategyDecision` / `SetupState` are NOT imported (that translation is
    `adapter.py`, Step 7), and nothing here places, amends, cancels or
    authorises an order — invalidations and stale resting orders are reported as
    data (safety rule #9).

Phase 1 Step 7 scope: `adapter.py`, covered by
`engine/tests/test_manual_smc_adapter.py`.

  * The ONLY boundary between `ManualSMCStrategy` and the application's strategy
    types, and the ONLY module in this package permitted to import
    `quantedge.strategy.models` (`SetupState`, `SetupType`, `StrategyDecision`,
    `StrategyDirection`). Every sibling above stays independent of the
    application; the boundary is proven by AST, not by text search, because
    `strategy.py` and this file name those types in PROSE.
  * It TRANSLATES and computes nothing. No lifecycle, portfolio, sizing,
    quantization or geometry rule is restated: prices are the `QuantizedBracket`
    Decimal legs copied verbatim, and the module's only numeric literal is `1`
    (the sub-1x leverage check). `take_profit` descends from the OB's absolute
    `tp_price`, cross-checked leg by leg against the setup's own floats;
    `take_profit_target_pct` never appears in the code.
  * `TRADE_SETUP_READY` is EARNED, not mapped: a resting limit additionally
    requires a quantized bracket, an int-representable leverage, a free global
    trade slot, and no entry refusal on this candle. Both safety facts are READ
    off the evaluation and are REQUIRED keyword arguments with no defaults, so a
    default cannot answer a safety question (rules #13, #14). Everything else
    maps to `WATCHING_OB` or `QUALIFIED_LONG` / `QUALIFIED_SHORT`, and a filled,
    closed or invalidated OB raises rather than being offered as a setup.
  * `calculated_leverage` is an int and Manual SMC's leverage is fractional, so
    the FLOOR is written loudly (reasons + `metadata['applied_leverage']` +
    `leverage_truncated_to_int`) because `None` fails OPEN downstream. A sub-1x
    leverage is REFUSED rather than rounded up to 1x, which is the only
    direction that would raise risk above the 35% budget.
  * It refuses to invent: `quantity`, `risk_amount`, `reward_amount`,
    `order_block`, `candle`, `confidence`, `minimum_risk_reward`,
    `configuration_version` and any exchange symbol stay absent (rules #8, #15).
  * The adapter is DELIBERATELY NOT re-exported below, so importing
    `quantedge.strategy.manual_smc` keeps this package's own import surface
    application-free. Consumers import `quantedge.strategy.manual_smc.adapter`
    explicitly. Nothing in it places, amends, cancels or authorises an order:
    `cancel_ob_ids` is a withdrawal REPORT (safety rule #9).

Phase 1 Step 8 scope: `backtest.py`, covered by
`engine/tests/test_manual_smc_backtest.py`.

  * A THIN chronological driver over `ManualSMCStrategy`, and the reason the
    backtest can no longer disagree with production: it owns the loop, the
    canonical-CSV preparation and the trade ledger, and NOTHING else. No BOS
    scan, no OB geometry, no probe/displacement test, no entry timing, no
    invalidation, no TP/SL resolution, no lock rule and no leverage formula is
    restated — `evaluate_closed_candle` has exactly ONE call site, and the
    driver never assigns to `ob.state`, `active_trade` or the live OB pool.
  * The global single-trade slot is the SHARED `PortfolioLock` of the ONE
    strategy instance the driver creates, so it is portfolio-wide across every
    asset. This is a deliberate BEHAVIOURAL divergence from the frozen oracle's
    `run_manual_spec_backtest`, whose timestamp-only watermark let a later
    setup overwrite `active_trade` and strand the trade it had already opened.
    The corrected lock therefore records MORE trades than the oracle published,
    and `EntryBlock.oracle_would_have_overwritten` names each block where the
    two implementations part company.
  * Two fail-closed divergences in data preparation: a repository root that
    cannot be found by marker RAISES instead of being guessed (a wrong root
    yields an empty dataset and a zero-trade "baseline"), and duplicate
    timestamps RAISE instead of being assigned two bar indices.
  * Quantization is REPORTING ONLY. `quantized_at_fill` and
    `quantization_refusal` are recorded per trade; no strategy price is moved
    onto a tick grid, so the ideal-strategy baseline and the
    exchange-executable baseline stay distinguishable. Order quantity is still
    absent (rules #8, #15).
  * `LIVE_EXECUTION_AUTHORIZED` is `False` and the driver refuses to construct
    if it is ever flipped. No exchange, no database, no Java, no WebSocket, no
    runtime composition root, no CLI on import.

Phase 2 scope (quantization half): `executable.py`, covered by
`engine/tests/test_manual_smc_executable.py`.

  * The MEASUREMENT that Step 8 section 11 promised to keep possible: the
    ideal (unquantized) baseline and the exchange-executable (on-grid)
    baseline reported side by side, per asset and in total, with the
    divergence between them named leg by leg and trade by trade.
  * A pure READER of the Step 8 ledger. It re-runs no candle, re-quantizes no
    price and owns no rule: the tick grid stays in `quantization.py` (the
    on-grid bracket is the one `strategy.py` already computed at fill and
    `backtest.py` now retains verbatim on the trade row), risk percentage and
    leverage come from `sizing.compute_sl_dist_pct` / `compute_leverage`, R
    comes from `sizing.realized_r_for_outcome` called on the trade's OWN
    `PositionSizing` with only its five bracket fields re-expressed, and the
    summaries come from `backtest.aggregate` — `ExecutableTrade` deliberately
    satisfies that one function structurally so the two baselines cannot be
    summarised by two different implementations.
  * A PRICE-LEVEL counterfactual, and it says so in code:
    `TIMING_IS_RESIMULATED` is `False` and is asserted. Order blocks, fills,
    exits, outcomes and bar indices are the ideal run's. An executable-mode
    re-run in which a grid-snapped entry fills on a different bar would need
    either a second lifecycle or changed geometry predicates, both forbidden,
    so it is reported as an OPEN DECISION rather than approximated.
  * Capital is not re-compounded: a different leverage changes every
    subsequent notional, fee and balance, which is a sequential re-run rather
    than a post-hoc measurement. Prices, distances, risk percentages,
    leverage and R are reported; notional, fee, PnL, balance and return
    percentage are not.
  * `APPLIES_TO_STRATEGY_GEOMETRY` and `ORDER_QUANTITY_IS_COMPUTED` are
    `False` and asserted at every entry point. The two conservative-rounding
    guarantees are checked per trade and counted (`risk_grew_count`,
    `budget_breach_count`, both of which must be 0). There is no symbol table,
    no default tick and no quantity field — the tick size arrives inside the
    recorded bracket, from an injected product specification (rules #8, #15,
    #16).
  * Like `adapter.py` and `backtest.py` it is DELIBERATELY NOT re-exported
    below; consumers import `quantedge.strategy.manual_smc.executable`.

THE PRODUCTION ACTIVATION POLICY — FIRST TOUCH, THEN A THREE-CANDLE WINDOW
--------------------------------------------------------------------------
`ManualSMCLifecycle` supports TWO activation modes and they are not
interchangeable. The mode is what decides WHEN a created OB becomes executable;
it changes no geometry, no leverage and no exit rule.

  * `ACTIVATION_MODE_FIRST_TOUCH` ("FIRST_TOUCH_WINDOW") is the PRODUCTION
    policy and the default of both production entry points, `ManualSMCStrategy`
    and `ManualSMCBacktest`, together with `ENTRY_WINDOW_CANDLES` and the
    authorized 0.60% `manual_smc_production_config()`. BOS/CHOCH creates the OB;
    the OB then
    waits, ACTIVE and untouched, for as long as it takes — age alone NEVER
    invalidates it, across days or across a backtest's preloaded history. The
    FIRST re-entry of the zone (body or wick, edge inclusive, at any depth) arms
    the exact 25%-depth limit for the window
    `[touch_bar, touch_bar + ENTRY_WINDOW_CANDLES - 1]` INCLUSIVE — the touch
    candle itself plus the next two, so an entry reached on the touch candle
    fills. If the entry is not reached inside that window the resting order is
    withdrawn (REPORTED, never placed or cancelled here — safety rule #9) and
    the OB is PERMANENTLY invalid: it is dropped from the live pool and can
    never re-arm. Only the first touch arms; later touches do not extend or
    restart the window.
  * `ACTIVATION_MODE_ORACLE_C` ("C_PROBE_PULLBACK") is the RESEARCH policy and
    the bare `ManualSMCLifecycle()` default, preserved verbatim so oracle
    equivalence stays provable: a close-based probe beyond the proximal, then a
    pullback close back through it, arming from `displacement_bar + 1` with NO
    window expiry. It is never the production default.

Neither mode trades on the OB-creation candle. The per-candle order — resolve
the active trade, update every live OB, admit new OBs LAST — is what makes
admission strictly break+1, so a BOS candle whose own range covers the entry
emits `OB_CREATED` and nothing else. The window convention, the permanence of
an expiry and the indefinite survival of an untouched OB are pinned by
`engine/tests/test_manual_smc_first_touch_window.py`, which constructs every
subject with NO policy keywords so the shipped production defaults are the
subject under test.

This package has NO production wiring and NO execution wiring. Importing it
cannot place, cancel or authorise an order.
"""

from quantedge.strategy.manual_smc.geometry import (
    _make_manual_ob,
    _manual_distal_breached,
    _manual_entry_touched,
    _manual_sl_hit,
    _manual_tp_hit,
    make_manual_ob,
    manual_distal_breached,
    manual_entry_touched,
    manual_sl_hit,
    manual_tp_hit,
)
from quantedge.strategy.manual_smc.lifecycle import (
    ACTIVATION_MODE_FIRST_TOUCH,
    ACTIVATION_MODE_ORACLE_C,
    ACTIVATION_MODES,
    DISPLACEMENT_MODE,
    ENTRY_WINDOW_CANDLES,
    OUTCOME_SL,
    OUTCOME_TIMEOUT,
    OUTCOME_TP,
    REASON_DUAL_TOUCH,
    REASON_SL_HIT,
    REASON_TIMEOUT,
    REASON_TP_HIT,
    ManualActiveTrade,
    ManualLifecycleEvent,
    ManualLifecycleEventType,
    ManualSMCLifecycle,
    ManualTradeExit,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBRecord,
    ManualOBState,
    ManualSMCConfig,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.portfolio import (
    OUTCOME_RECONCILED_CLOSED,
    TERMINAL_OUTCOMES,
    LockDecision,
    LockEvent,
    LockHolder,
    LockRejection,
    LockRejectionCode,
    PortfolioLock,
    PortfolioLockError,
    PortfolioLockUnavailableError,
    PortfolioLockViolationError,
)
from quantedge.strategy.manual_smc.quantization import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    QUANTIZE_PRECISION,
    BracketGeometryError,
    InvalidPriceError,
    InvalidTickSizeError,
    PriceRole,
    QuantizationError,
    QuantizedBracket,
    SubTickPriceError,
    TickRounding,
    TickSizeSpec,
    conservative_rounding,
    is_on_tick_grid,
    price_from_strategy_float,
    quantize_bracket,
    quantize_ob_bracket,
    quantize_price,
    tick_size_of,
    validate_direction,
    validate_price,
    validate_tick_size,
)
from quantedge.strategy.manual_smc.scanner import (
    ManualSMCBOSScanner,
    ManualSpecBOSScanner,
)
from quantedge.strategy.manual_smc.state import (
    MANUAL_SMC_STATE_SCHEMA,
    MANUAL_SMC_STATE_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    CandleMark,
    CandleWatermark,
    MalformedStateError,
    MissingFieldError,
    RestoredState,
    StateError,
    StateIntegrityError,
    StateSchemaError,
    UnknownEnumValueError,
    UnknownFieldError,
    UnsupportedSchemaVersionError,
    WatermarkRegressionError,
    assert_config_compatible,
    capture_state,
    capture_state_json,
    decode_dataclass,
    decode_datetime,
    decode_decimal,
    decode_ob_state,
    decode_scanner,
    decode_watermark,
    dumps_state,
    encode_bool,
    encode_dataclass,
    encode_datetime,
    encode_decimal,
    encode_float,
    encode_int,
    encode_ob_state,
    encode_scanner,
    encode_str,
    encode_watermark,
    expected_keys,
    loads_state,
    require_exact_keys,
    require_list,
    require_mapping,
    restore_state,
    restore_state_json,
    validate_header,
)
from quantedge.strategy.manual_smc.sizing import (
    EPS,
    MANUAL_SMC_SYMBOLS,
    UNVERIFIED,
    ContractSpec,
    ContractSpecRegistry,
    ContractValue,
    ContractValueUnverifiedError,
    DegenerateRiskError,
    PositionSizing,
    QuantityConverter,
    QuantitySemanticsUnverifiedError,
    SizingError,
    TradeSettlement,
    UnknownSymbolError,
    assert_executable,
    compute_leverage,
    compute_sl_dist_pct,
    realized_r_for_outcome,
    resolve_order_quantity,
    return_pct_for_outcome,
    settle_trade,
    size_position,
)

from quantedge.strategy.manual_smc.strategy import (
    ATOMICITY_NOTE,
    PERSISTENCE_IS_ATOMIC,
    TP_SOURCE,
    CandleOrderError,
    DuplicateCandleError,
    GlobalOrderError,
    InvalidCandleError,
    ManualSMCBlocked,
    ManualSMCClose,
    ManualSMCEvaluation,
    ManualSMCFill,
    ManualSMCSetup,
    ManualSMCStrategy,
    OutOfOrderCandleError,
    PortfolioLockDesyncError,
    StrategyError,
    StrategyStateError,
    TornStateError,
    validate_candle,
)

__all__ = [
    "MANUAL_SMC_STRATEGY_NAME",
    "MANUAL_SMC_STRATEGY_VERSION",
    "ManualOBState",
    "ManualOBRecord",
    "ManualSpecConfig",
    "ManualSMCConfig",
    "ManualSpecBOSScanner",
    "ManualSMCBOSScanner",
    "DISPLACEMENT_MODE",
    "ACTIVATION_MODE_FIRST_TOUCH",
    "ACTIVATION_MODE_ORACLE_C",
    "ACTIVATION_MODES",
    "ENTRY_WINDOW_CANDLES",
    "OUTCOME_TP",
    "OUTCOME_SL",
    "OUTCOME_TIMEOUT",
    "REASON_TP_HIT",
    "REASON_SL_HIT",
    "REASON_DUAL_TOUCH",
    "REASON_TIMEOUT",
    "ManualLifecycleEventType",
    "ManualLifecycleEvent",
    "ManualActiveTrade",
    "ManualTradeExit",
    "ManualSMCLifecycle",
    "OUTCOME_RECONCILED_CLOSED",
    "TERMINAL_OUTCOMES",
    "LockRejectionCode",
    "LockHolder",
    "LockRejection",
    "LockEvent",
    "LockDecision",
    "PortfolioLock",
    "PortfolioLockError",
    "PortfolioLockUnavailableError",
    "PortfolioLockViolationError",
    "EPS",
    "MANUAL_SMC_SYMBOLS",
    "UNVERIFIED",
    "ContractValue",
    "ContractSpec",
    "ContractSpecRegistry",
    "PositionSizing",
    "TradeSettlement",
    "QuantityConverter",
    "SizingError",
    "UnknownSymbolError",
    "ContractValueUnverifiedError",
    "QuantitySemanticsUnverifiedError",
    "DegenerateRiskError",
    "compute_sl_dist_pct",
    "compute_leverage",
    "size_position",
    "realized_r_for_outcome",
    "return_pct_for_outcome",
    "settle_trade",
    "assert_executable",
    "resolve_order_quantity",
    "QUANTIZE_PRECISION",
    "DIRECTION_LONG",
    "DIRECTION_SHORT",
    "QuantizationError",
    "InvalidTickSizeError",
    "InvalidPriceError",
    "SubTickPriceError",
    "BracketGeometryError",
    "TickRounding",
    "TickSizeSpec",
    "PriceRole",
    "QuantizedBracket",
    "validate_tick_size",
    "validate_price",
    "validate_direction",
    "tick_size_of",
    "price_from_strategy_float",
    "quantize_price",
    "is_on_tick_grid",
    "conservative_rounding",
    "quantize_bracket",
    "quantize_ob_bracket",
    "MANUAL_SMC_STATE_SCHEMA",
    "MANUAL_SMC_STATE_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "StateError",
    "MalformedStateError",
    "MissingFieldError",
    "UnknownFieldError",
    "UnknownEnumValueError",
    "UnsupportedSchemaVersionError",
    "StateIntegrityError",
    "StateSchemaError",
    "WatermarkRegressionError",
    "CandleMark",
    "CandleWatermark",
    "RestoredState",
    "encode_str",
    "encode_int",
    "encode_bool",
    "encode_float",
    "encode_datetime",
    "decode_datetime",
    "encode_decimal",
    "decode_decimal",
    "encode_ob_state",
    "decode_ob_state",
    "expected_keys",
    "encode_dataclass",
    "decode_dataclass",
    "require_mapping",
    "require_exact_keys",
    "require_list",
    "encode_watermark",
    "decode_watermark",
    "encode_scanner",
    "decode_scanner",
    "validate_header",
    "assert_config_compatible",
    "capture_state",
    "restore_state",
    "dumps_state",
    "loads_state",
    "capture_state_json",
    "restore_state_json",
    "_make_manual_ob",
    "_manual_distal_breached",
    "_manual_entry_touched",
    "_manual_sl_hit",
    "_manual_tp_hit",
    "make_manual_ob",
    "manual_distal_breached",
    "manual_entry_touched",
    "manual_sl_hit",
    "manual_tp_hit",
    "TP_SOURCE",
    "PERSISTENCE_IS_ATOMIC",
    "ATOMICITY_NOTE",
    "StrategyError",
    "InvalidCandleError",
    "CandleOrderError",
    "DuplicateCandleError",
    "OutOfOrderCandleError",
    "GlobalOrderError",
    "PortfolioLockDesyncError",
    "TornStateError",
    "StrategyStateError",
    "validate_candle",
    "ManualSMCSetup",
    "ManualSMCFill",
    "ManualSMCClose",
    "ManualSMCBlocked",
    "ManualSMCEvaluation",
    "ManualSMCStrategy",
]
