"""
Configuration management for QuantEdge Engine.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment
    environment: str = "development"
    log_level: str = "INFO"

    # Process-level live market-data gate (Phase 1).
    #
    # False - the default and the only fail-safe value - keeps
    # `python -m quantedge` completely inert: the health endpoint serves, the
    # Manual SMC runtime is registered, and no socket is opened.
    #
    # True binds the PUBLIC Delta India candlestick feed to the closed-candle
    # STRUCTURE clock only. It is deliberately NOT an execution switch: the
    # forming-frame EXECUTION clock stays unbound, no orchestrator is built and
    # no credential is read, so no order can be placed by turning this on. The
    # per-account `algo_enabled` / `kill_switch_active` governance flags are
    # untouched by it and remain the only authority over trading.
    #
    # An absent env var leaves this False. A value pydantic cannot coerce to a
    # bool raises while `quantedge.config` is imported, before the health
    # server, the runtime or any socket exists - fail closed.
    live_feed_enabled: bool = False

    # Durable structure state (Phase 2).
    #
    # Directory that holds the per-process Manual SMC runtime snapshot written by
    # the EXISTING `ManualSMCRuntime.save_state()` / `load_state()` pair. There is
    # deliberately no default path: LuxAlgo structure is path-dependent from bar
    # 0, so a snapshot written into an ephemeral container filesystem would be
    # silently discarded on `docker compose up --force-recreate` and the engine
    # would then have to invent a warmup. None therefore means "no durable state
    # location was configured", which the startup sequence treats as a refusal to
    # subscribe rather than as permission to cold-start - fail closed.
    #
    # Point it at a MOUNTED persistent location (compose mounts the dedicated
    # `engine_state` volume at /var/lib/quantedge/state). It holds market
    # structure only; no credential is ever written here.
    engine_state_dir: Optional[str] = None

    # Directory holding the full canonical 1h history used ONLY when no durable
    # snapshot exists yet (first ever boot, or a deliberately cleared volume).
    # Layout is the repository's existing canonical one, read through
    # `strategy.manual_smc.backtest.load_canonical_candles`:
    #
    #     <engine_genesis_history_dir>/<SYMBOL>/1h/full_history.csv
    #
    # `full_history.csv` is required; the `2026.csv` fallback is NOT accepted for
    # genesis because starting at 2026-01-01 is a truncated origin dressed up as
    # a complete one. None means "no genesis source configured": the engine stays
    # inert instead of guessing an origin.
    engine_genesis_history_dir: Optional[str] = None

    # Process-level PRODUCTION EXECUTION COMPOSITION gate.
    #
    # False - the default and the only fail-safe value - keeps the process on the
    # data-only path it has always taken: the closed-candle STRUCTURE clock is
    # bound, the forming-frame EXECUTION clock is not, no credential is read, no
    # orchestrator is built, and `ManualSMCRuntime.submit()` refuses every ready
    # decision with NO_ORCHESTRATION_BOUND.
    #
    # True asks `quantedge.runtime.production_composition` to build the canonical
    # execution stack (authenticated Delta client -> TradeLifecycleManager ->
    # MarketScannerOrchestrator), install the entry-admission contract, load
    # durable execution state and run the authoritative startup reconciliation
    # before either clock is subscribed. It is deliberately NOT an order-
    # submission switch: `order_submission_enabled` below is, and it is separate.
    #
    # Nested under LIVE_FEED_ENABLED on purpose - production execution consumes
    # the live feed, so it cannot be reached without the feed gate being open
    # first.
    production_execution_enabled: bool = False

    # THE FINAL ORDER-SUBMISSION GATE. The last flag between a fully composed,
    # fully reconciled, admission-authorized production system and a real order
    # on a real account.
    #
    # False - the default and the only fail-safe value - means a strategy signal
    # travels the entire canonical path (detected -> admission consulted -> every
    # safety check evaluated) and then STOPS, explicitly and observably, before
    # `TradeLifecycleManager.execute_trade_setup` reaches the exchange. The
    # refusal is recorded as a refusal; nothing is reported as executed.
    #
    # True is NOT sufficient on its own. Order submission additionally requires:
    # a connected real account, a CLEAN startup reconciliation, an installed
    # admission contract whose authorization is still current, and the existing
    # per-account `algo_enabled` / `kill_switch_active` governance flags - which
    # live in the account record, not here, and are untouched by this flag.
    order_submission_enabled: bool = False

    # Database
    database_url: str = "postgresql://quantedge:quantedge_dev@localhost:5432/quantedge"

    # Delta Exchange India (Live Production Endpoint)
    delta_api_key: Optional[str] = None
    delta_api_secret: Optional[str] = None
    delta_base_url: str = "https://api.india.delta.exchange"

    # Spring Boot Backend
    backend_base_url: str = "http://localhost:8080"
    backend_api_key: Optional[str] = None

    # Market Data
    default_timeframe: str = "1h"
    default_symbols: list[str] = ["BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P"]
    max_candles_per_request: int = 1000

    # SMC Parameters (LuxAlgo defaults)
    internal_structure_length: int = 5
    swing_structure_length: int = 50
    atr_period: int = 200
    atr_multiplier: float = 2.0

    # Strategy Parameters
    confidence_threshold: int = 85
    ob_width_threshold_pct: float = 0.6
    opposing_zone_threshold_pct: float = 0.5

    # Risk Parameters
    risk_per_trade_pct: float = 35.0
    target_reward_pct: float = 60.0
    max_leverage: int = 100

    # Backtesting
    backtest_start_date: Optional[str] = None
    backtest_end_date: Optional[str] = None
    initial_capital: float = 10000.0
    commission_pct: float = 0.02
    slippage_pct: float = 0.01


settings = Settings()