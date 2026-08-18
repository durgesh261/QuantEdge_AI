"""
ATR-based volatility parsing per LuxAlgo specification.

Reference: LuxAlgo SMC uses ATR(200) as default volatility measure.
High volatility condition: high - low >= 2 * ATR
Then parsedHigh = low, parsedLow = high for high volatility candles.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
import numpy as np
import pandas as pd

from quantedge.market_data.models import Candle


@dataclass(frozen=True)
class ParsedCandle:
    """Candle with volatility-parsed high/low values."""
    original: Candle
    parsed_high: Decimal
    parsed_low: Decimal
    is_high_volatility: bool
    atr_value: Decimal


def calculate_atr(candles: list[Candle], period: int = 200) -> list[Decimal]:
    """
    Calculate Average True Range (ATR) using Wilder's smoothing.

    Args:
        candles: List of candles (chronological order)
        period: ATR period (default 200 per LuxAlgo)

    Returns:
        List of ATR values aligned with candles (first period-1 values are None)
    """
    if len(candles) < period + 1:
        raise ValueError(f"Need at least {period + 1} candles for ATR({period})")

    # Calculate True Range for each candle
    true_ranges = []
    for i, candle in enumerate(candles):
        if i == 0:
            tr = candle.high - candle.low
        else:
            prev_close = candles[i - 1].close
            tr = max(
                candle.high - candle.low,
                abs(candle.high - prev_close),
                abs(candle.low - prev_close)
            )
        true_ranges.append(tr)

    # Convert to numpy for efficient calculation
    tr_array = np.array([float(tr) for tr in true_ranges])

    # Wilder's smoothing (RMA)
    atr_values = np.full(len(tr_array), np.nan)

    # First ATR is simple average of first 'period' true ranges
    first_atr = np.mean(tr_array[:period])
    atr_values[period - 1] = first_atr

    # Subsequent ATRs use Wilder's smoothing: ATR = (prev_ATR * (period - 1) + TR) / period
    for i in range(period, len(tr_array)):
        atr_values[i] = (atr_values[i - 1] * (period - 1) + tr_array[i]) / period

    return [Decimal(str(v)) if not np.isnan(v) else None for v in atr_values]


def parse_candles_with_volatility(
    candles: list[Candle],
    atr_period: int = 200,
    atr_multiplier: float = 2.0
) -> list[ParsedCandle]:
    """
    Apply LuxAlgo volatility parsing to candles.

    High volatility condition: high - low >= multiplier * ATR
    If high volatility: parsed_high = low, parsed_low = high (inverted)
    If normal: parsed_high = high, parsed_low = low

    Args:
        candles: List of candles in chronological order
        atr_period: ATR period (default 200)
        atr_multiplier: Volatility multiplier (default 2.0)

    Returns:
        List of ParsedCandle objects
    """
    if len(candles) < atr_period + 1:
        raise ValueError(f"Need at least {atr_period + 1} candles")

    atr_values = calculate_atr(candles, atr_period)
    multiplier = Decimal(str(atr_multiplier))

    parsed = []
    for i, (candle, atr) in enumerate(zip(candles, atr_values)):
        if atr is None:
            # Not enough data for ATR yet
            parsed.append(ParsedCandle(
                original=candle,
                parsed_high=candle.high,
                parsed_low=candle.low,
                is_high_volatility=False,
                atr_value=Decimal("0")
            ))
            continue

        candle_range = candle.high - candle.low
        volatility_threshold = atr * multiplier

        is_high_vol = candle_range >= volatility_threshold

        if is_high_vol:
            # Invert for high volatility per LuxAlgo
            parsed_high = candle.low
            parsed_low = candle.high
        else:
            parsed_high = candle.high
            parsed_low = candle.low

        parsed.append(ParsedCandle(
            original=candle,
            parsed_high=parsed_high,
            parsed_low=parsed_low,
            is_high_volatility=is_high_vol,
            atr_value=atr
        ))

    return parsed