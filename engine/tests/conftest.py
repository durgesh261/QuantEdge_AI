"""
Pytest configuration for QuantEdge Engine tests.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture
def sample_candles():
    """Provide sample candles for testing."""
    from datetime import datetime, timedelta
    from decimal import Decimal
    from quantedge.market_data.models import Candle, Timeframe

    base_time = datetime(2024, 1, 1, 0, 0, 0)
    candles = []

    for i in range(250):
        open_price = Decimal("100") + Decimal(str(i * 0.1))
        high_price = open_price + Decimal("1.0")
        low_price = open_price - Decimal("1.0")
        close_price = open_price + Decimal("0.5")

        candles.append(Candle(
            symbol="BTCUSD.P",
            timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=Decimal("1000"),
        ))

    return candles


@pytest.fixture
def strategy_config():
    """Provide default strategy config."""
    from quantedge.strategy.models import StrategyConfig
    return StrategyConfig()


@pytest.fixture
def account_state():
    """Provide test account state."""
    from decimal import Decimal
    from quantedge.strategy.models import AccountState
    return AccountState(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        free_margin=Decimal("10000"),
        used_margin=Decimal("0"),
    )