"""
LuxAlgo Canonical Test Fixtures.

These fixtures implement the exact causal sequences required by the
LuxAlgo SMC state machine as defined in the supplied Pine Script.

Each fixture is a deterministic sequence of OHLC candles that produces
a specific, verifiable LuxAlgo structure event.

FIXTURE NAMING:
- bearish_leg_then_bullish_leg_then_bearish_break
- bullish_leg_then_bearish_leg_then_bullish_break
- etc.

Each fixture returns a tuple of (candles, expected_events) where
expected_events is a dict with expected structure events.
"""

from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
from quantedge.market_data.models import Candle, Timeframe


def create_bearish_leg_then_bullish_leg_then_bearish_break() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: Bearish leg -> Bullish leg -> Bearish break (BOS)

    Sequence:
    1. Short bullish leg (indices 0-2): sets up for bearish transition
    2. Bearish leg (indices 3-12): creates pivot_high at index 3 (high[3])
    3. Bullish leg (indices 13-22): creates pivot_low at index 10 (low[10])
    4. Bearish leg (indices 23-32): breaks below pivot_low -> BOS

    size = 2 (internal)

    Expected events:
    - Candle 3: bearish leg starts, pivot_high at index 3 (high[3])
    - Candle 13: bullish leg transition -> pivot_low at index 10 (low[10])
    - Candle 25: bearish crossunder of pivot_low -> BOS (prev trend = RANGING)
    """
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)

    # Leg 1: Short bullish (indices 0-2) to set up for bearish transition
    for i in range(3):
        open_p = Decimal('100') + Decimal(str(i * 3))
        close_p = open_p + Decimal('2')
        high_p = max(open_p, close_p) + Decimal('2')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 2: Bearish (indices 3-12) - creates pivot_high at index 3 (high[3])
    for i in range(3, 13):
        open_p = Decimal('120') - Decimal(str((i-3) * 2))
        close_p = open_p - Decimal('1.5')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 3: Bullish (indices 13-22) - creates pivot_low at index 10 (low[10])
    for i in range(13, 23):
        open_p = Decimal('100') + Decimal(str((i-13) * 3))
        close_p = open_p + Decimal('2')
        high_p = max(open_p, close_p) + Decimal('2')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 4: Bearish (indices 23-32) - breaks below pivot_low (99) -> BOS
    # Need to go BELOW pivot_low price of 99
    for i in range(23, 33):
        open_p = Decimal('130') - Decimal(str((i-23) * 5))  # Much faster decline
        close_p = open_p - Decimal('4')  # Bigger drops
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('2')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    expected = {
        'pivot_low_index': 10,
        'pivot_low_price': Decimal('99'),
        'break_candle': 25,
        'break_type': 'BOS',
        'prev_trend': 'RANGING',
        'new_trend': 'BEARISH',
    }
    return candles, expected


def create_bullish_leg_then_bearish_leg_then_bullish_break() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: Bullish leg -> Bearish leg -> Bullish break (CHOCH)

    Sequence:
    1. Short bearish leg (indices 0-2): sets up for bullish transition
    2. Bullish leg (indices 3-12): creates pivot_low at index 3 (low[3] = 99)
    3. Bearish leg (indices 13-22): creates pivot_high at index 10 (high[10] = 131)
    4. Bullish leg (indices 23-32): breaks above pivot_high (131) -> CHOCH

    size = 2 (internal)

    Expected events:
    - Candle 3: bullish leg starts, pivot_low at index 3 (low[3] = 99)
    - Candle 13: bearish leg transition -> pivot_high at index 10 (high[10] = 131)
    - Candle 25: bullish crossover of pivot_high (131) -> CHOCH (prev trend = BEARISH)
    """
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)

    # Leg 1: Short bearish (indices 0-2) to set up for bullish transition
    for i in range(3):
        open_p = Decimal('120') - Decimal(str(i * 2))
        close_p = open_p - Decimal('1.5')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 2: Bullish (indices 3-12) - creates pivot_low at index 3 (low[3] = 99)
    for i in range(3, 13):
        open_p = Decimal('100') + Decimal(str((i-3) * 3))
        close_p = open_p + Decimal('2')
        high_p = max(open_p, close_p) + Decimal('2')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 3: Bearish (indices 13-22) - creates pivot_high at index 10 (high[10] = 131)
    for i in range(13, 23):
        open_p = Decimal('130') - Decimal(str((i-13) * 3))
        close_p = open_p - Decimal('2')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('2')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 4: Bullish (indices 23-32) - breaks ABOVE pivot_high (131) -> CHOCH
    # Need to go ABOVE 131
    for i in range(23, 33):
        open_p = Decimal('130') + Decimal(str((i-23) * 4))  # Faster rise
        close_p = open_p + Decimal('3')
        high_p = max(open_p, close_p) + Decimal('2')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    expected = {
        'pivot_high_index': 10,
        'pivot_high_price': Decimal('131'),
        'break_candle': 25,
        'break_type': 'CHOCH',
        'prev_trend': 'BEARISH',
        'new_trend': 'BULLISH',
    }
    return candles, expected


def create_bearish_leg_then_bullish_break_choch() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: Bearish leg -> Bullish leg -> Bearish break (BOS) -> Bullish break (CHOCH)

    Full 4-leg sequence:
    1. Short bullish (indices 0-2) -> sets up for bearish transition
    2. Bearish leg (indices 3-12): creates pivot_high at index 3
    3. Bullish leg (indices 13-22): creates pivot_low at index 10, crosses pivot_high -> CHOCH
    4. Bearish leg (indices 23-32): creates pivot_high at index 18, crosses pivot_low -> BOS
    5. Bullish leg (indices 33-42): crosses pivot_high -> CHOCH

    size = 2 (internal)

    Expected events:
    - Candle 3: bearish leg starts, pivot_high at index 3
    - Candle 13: bullish leg transition, pivot_low at index 10
    - Candle 18: bullish crossover of pivot_high -> CHOCH (prev trend=BEARISH)
    - Candle 23: bearish leg transition, pivot_high at index 18
    - Candle 28: bearish crossunder of pivot_low -> BOS (prev trend=BULLISH)
    - Candle 33: bullish crossover of pivot_high -> CHOCH (prev trend=BEARISH)
    """
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)

    # Leg 1: Short bullish (0-2) -> sets up for bearish transition
    for i in range(3):
        open_p = Decimal('100') + Decimal(str(i * 3))
        close_p = open_p + Decimal('2')
        high_p = max(open_p, close_p) + Decimal('2')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 2: Bearish (3-12) - creates pivot_high at index 3
    for i in range(3, 13):
        open_p = Decimal('120') - Decimal(str((i-3) * 2))
        close_p = open_p - Decimal('1.5')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 3: Bullish (13-22) - creates pivot_low at index 10, crosses pivot_high -> CHOCH
    for i in range(13, 23):
        open_p = Decimal('100') + Decimal(str((i-13) * 3))
        close_p = open_p + Decimal('2')
        high_p = max(open_p, close_p) + Decimal('2')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 4: Bearish (23-32) - creates pivot_high at index 18, crosses pivot_low -> BOS
    for i in range(23, 33):
        open_p = Decimal('130') - Decimal(str((i-23) * 3))
        close_p = open_p - Decimal('2')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('2')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Leg 5: Bullish (33-42) - crosses pivot_high -> CHOCH
    for i in range(33, 43):
        open_p = Decimal('90') + Decimal(str((i-33) * 3))
        close_p = open_p + Decimal('2')
        high_p = max(open_p, close_p) + Decimal('2')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    expected = {
        'first_choch_candle': 18,
        'first_bos_candle': 25,
        'second_choch_candle': 33,
    }
    return candles, expected


def create_raw_vs_parsed_high_volatility_inversion() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: High-volatility candle where raw and parsed OHLC differ.

    Creates a high-volatility candle where:
    - Raw: high=120, low=80 (wide range = high volatility)
    - Parsed: inverted (high=80, low=120) per LuxAlgo volatility logic

    The structure detector must use RAW OHLC for leg detection,
    not the inverted parsed values.

    This is a regression test for the raw-vs-parsed bug fix.
    """
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)

    # Normal bearish leg first
    for i in range(5):
        open_p = Decimal('120') - Decimal(str(i * 2))
        close_p = open_p - Decimal('1.5')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # High-volatility candle (index 5): raw H=120, L=80
    # This will be parsed as inverted: parsed_high=80, parsed_low=120
    candles.append(Candle(
        symbol='TEST', timeframe=Timeframe.H1,
        timestamp=base_time + timedelta(hours=5),
        open=Decimal('100'), high=Decimal('120'), low=Decimal('80'), close=Decimal('90'),
        volume=Decimal('5000')  # High volume = high volatility
    ))

    # Continue bearish
    for i in range(6, 10):
        open_p = Decimal('100') - Decimal(str((i-5) * 2))
        close_p = open_p - Decimal('1.5')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Bullish leg
    for i in range(10, 15):
        open_p = Decimal('80') + Decimal(str((i-10) * 3))
        close_p = open_p + Decimal('2')
        high_p = max(open_p, close_p) + Decimal('2')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    expected = {
        'high_vol_candle_index': 5,
        'raw_high': Decimal('120'),
        'raw_low': Decimal('80'),
        'parsed_high': Decimal('80'),
        'parsed_low': Decimal('120'),
        'leg_transition_expected': 12,
        'pivot_low_expected_index': 10,
    }
    return candles, expected


def create_ta_highest_lowest_semantic_test() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: Explicit test for ta.highest/ta.lowest index mapping.

    Verifies that for candle i:
    - high[size] = high[i - size]
    - ta.highest(size) = max(high[i-size+1] ... high[i])

    size = 2
    """
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)

    # Create explicit known high/low values
    # Index 0: H=100, L=90
    # Index 1: H=105, L=95
    # Index 2: H=103, L=93
    # Index 3: H=108, L=98
    # Index 4: H=106, L=96
    # Index 5: H=110, L=100
    # Index 6: H=108, L=98
    # Index 7: H=112, L=102
    
    values = [
        (100, 90), (105, 95), (103, 93), (108, 98),
        (106, 96), (110, 100), (108, 98), (112, 102)
    ]
    
    for i, (h, l) in enumerate(values):
        open_p = Decimal(str((h + l) // 2))
        close_p = Decimal(str((h + l) // 2))
        high_p = Decimal(str(h))
        low_p = Decimal(str(l))
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    expected = {
        'size': 2,
        'checks': [
            # At candle 3: size_idx = 3-2 = 1, high[1]=105, highest[2:4]=max(103,108)=108
            {'candle': 3, 'size_idx': 1, 'high_size': 105, 'highest': 108, 'new_leg_high': False},
            {'candle': 4, 'size_idx': 2, 'high_size': 103, 'highest': 108, 'new_leg_high': False},
            {'candle': 5, 'size_idx': 3, 'high_size': 108, 'highest': 110, 'new_leg_high': False},
            {'candle': 6, 'size_idx': 4, 'high_size': 106, 'highest': 110, 'new_leg_high': False},
            {'candle': 7, 'size_idx': 5, 'high_size': 110, 'highest': 112, 'new_leg_high': False},
        ]
    }
    return candles, expected


def create_crossed_state_test() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: crossed state prevents duplicate breaks.

    Creates a pivot, price breaks it once, then continues.
    Verify exactly ONE break event is emitted.
    """
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)

    # Leg 1: Bearish (creates pivot_high)
    for i in range(6):
        open_p = Decimal('120') - Decimal(str(i * 3))
        close_p = open_p - Decimal('1.5')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Bullish leg - creates pivot_low
    for i in range(6, 12):
        open_p = Decimal('100') + Decimal(str((i-6) * 3))
        close_p = open_p + Decimal('2')
        high_p = max(open_p, close_p) + Decimal('2')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # Bearish break of pivot_low (BOS)
    for i in range(12, 18):
        open_p = Decimal('130') - Decimal(str((i-12) * 3))
        close_p = open_p - Decimal('2')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('2')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    # More bearish - should NOT create another break
    for i in range(18, 24):
        open_p = Decimal('100') - Decimal(str((i-18) * 2))
        close_p = open_p - Decimal('1.5')
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    expected = {
        'break_candles': [17],  # Only one break event
        'break_type': 'BOS',
    }
    return candles, expected


def create_internal_vs_swing_independence() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: Internal and Swing structures are independent.

    Uses a sequence that creates different structures at different lengths.
    """
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)

    # Long bearish then bullish then bearish
    for i in range(60):
        if i < 20:
            # Bearish
            open_p = Decimal('200') - Decimal(str(i * 2))
            close_p = open_p - Decimal('1.5')
        elif i < 40:
            # Bullish
            open_p = Decimal('100') + Decimal(str((i-20) * 3))
            close_p = open_p + Decimal('2')
        else:
            # Bearish
            open_p = Decimal('180') - Decimal(str((i-40) * 2))
            close_p = open_p - Decimal('1.5')
        
        high_p = max(open_p, close_p) + Decimal('1')
        low_p = min(open_p, close_p) - Decimal('1')
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=base_time + timedelta(hours=i),
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=Decimal('1000')
        ))

    expected = {
        'internal_structure_events': True,
        'swing_structure_events': True,
    }
    return candles, expected


if __name__ == "__main__":
    # Quick test of fixtures
    print("Testing fixtures...")
    
    # Test bearish -> bullish -> bearish break
    candles, expected = create_bearish_leg_then_bullish_leg_then_bearish_break()
    print(f"Fixture 1: {len(candles)} candles")
    
    candles2, expected2 = create_bullish_leg_then_bearish_leg_then_bullish_break()
    print(f"Fixture 2: {len(candles2)} candles")
    
    candles3, expected3 = create_bearish_leg_then_bullish_break_choch()
    print(f"Fixture 3: {len(candles3)} candles")
    
    candles4, expected4 = create_raw_vs_parsed_high_volatility_inversion()
    print(f"Fixture 4: {len(candles4)} candles")
    
    print("All fixtures created successfully!")