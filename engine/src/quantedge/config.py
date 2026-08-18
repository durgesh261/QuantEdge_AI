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

    # Database
    database_url: str = "postgresql://quantedge:quantedge_dev@localhost:5432/quantedge"

    # Delta Exchange
    delta_api_key: Optional[str] = None
    delta_api_secret: Optional[str] = None
    delta_testnet_api_key: Optional[str] = None
    delta_testnet_api_secret: Optional[str] = None
    delta_base_url: str = "https://api.delta.exchange"
    delta_testnet_base_url: str = "https://api-testnet.delta.exchange"

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