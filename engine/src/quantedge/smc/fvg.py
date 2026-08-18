"""
Fair Value Gap (FVG) / Imbalance detection.

FVG is a 3-candle pattern where candle 1 and 3 don't overlap,
creating a gap that often gets filled (mitigated).

Bullish FVG: Candle 1 high < Candle 3 low (gap up)
Bearish FVG: Candle 1 low > Candle 3 high (gap down)

Note: FVG is NOT a standalone entry trigger per strategy spec.
It's a confluence factor only.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from quantedge.market_data.models import Candle
from quantedge.smc.models import FairValueGap


@dataclass
class FVGConfig:
    min_gap_size_pct: float = 0.01  # Minimum 0.01% gap


class FVGDetector:
    """Detects Fair Value Gaps in candle sequences."""

    def __init__(self, config: FVGConfig):
        self.config = config

    def detect_fvgs(self, candles: list[Candle]) -> list[FairValueGap]:
        """
        Detect all FVGs in a candle series.

        FVG forms over 3 candles:
        - Candle 0: First candle
        - Candle 1: Middle candle (the "gap" candle)
        - Candle 2: Third candle
        """
        fvgs = []

        for i in range(2, len(candles)):
            c0 = candles[i - 2]
            c1 = candles[i - 1]
            c2 = candles[i]

            # Bullish FVG: c0.high < c2.low
            if c0.high < c2.low:
                gap_size_pct = float((c2.low - c0.high) / c0.high * 100)
                if gap_size_pct >= self.config.min_gap_size_pct:
                    fvgs.append(FairValueGap(
                        index=i,
                        timestamp=c2.timestamp,
                        type="BULLISH",
                        top_price=c2.low,
                        bottom_price=c0.high,
                        forming_candles=(c0, c1, c2),
                    ))

            # Bearish FVG: c0.low > c2.high
            elif c0.low > c2.high:
                gap_size_pct = float((c0.low - c2.high) / c0.low * 100)
                if gap_size_pct >= self.config.min_gap_size_pct:
                    fvgs.append(FairValueGap(
                        index=i,
                        timestamp=c2.timestamp,
                        type="BEARISH",
                        top_price=c0.low,
                        bottom_price=c2.high,
                        forming_candles=(c0, c1, c2),
                    ))

        return fvgs

    def check_fvg_mitigation(
        self,
        candle: Candle,
        fvgs: list[FairValueGap],
    ) -> list[FairValueGap]:
        """
        Check if any FVGs were mitigated (filled) by this candle.

        Returns list of newly mitigated FVGs.
        """
        mitigated = []

        for fvg in fvgs:
            if fvg.is_mitigated:
                continue

            if fvg.type == "BULLISH":
                # Bullish FVG mitigated when price trades down into gap
                if candle.low <= fvg.top_price and candle.low >= fvg.bottom_price:
                    fvg.is_mitigated = True
                    fvg.mitigated_at = candle.timestamp
                    mitigated.append(fvg)
            else:
                # Bearish FVG mitigated when price trades up into gap
                if candle.high >= fvg.bottom_price and candle.high <= fvg.top_price:
                    fvg.is_mitigated = True
                    fvg.mitigated_at = candle.timestamp
                    mitigated.append(fvg)

        return mitigated
