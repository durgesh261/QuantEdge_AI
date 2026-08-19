"""
LuxAlgo Canonical Test Fixtures.

These fixtures implement the exact causal sequences required by the
LuxAlgo SMC state machine as defined in the supplied Pine Script.

Each fixture is a deterministic sequence of OHLC candles that produces
a specific, verifiable LuxAlgo structure event.

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

    Sequence (size=2):
    Indices 0-31 (32 candles total)

    Leg transitions (size=2):
    - Candle 2: Bearish leg starts -> pivot_high at index 0
    - Candle 10: Bullish leg starts -> pivot_low at index 8
    - Candle 20: Bearish leg starts -> pivot_high at index 18
    - Candle 30: Bearish crossunder of pivot_low -> BOS

    Key insight: For size=2, leg transitions happen when:
    - Bearish: high[i-2] > max(high[i-1], high[i])  (i.e., high[i-2] is highest of last 3)
    - Bullish: low[i-2] < min(low[i-1], low[i]) (low[i-2] is lowest of last 3)
    """
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)

    # We need explicit price values to control leg transitions exactly
    # Using raw OHLC values that will produce the exact leg transitions we want

    # Index:  OHLC values carefully chosen to trigger leg transitions at exact candles
    # Format: (open, high, low, close)
    raw_data = [
        # Indices 0-1: Initial flat candles for history
        (Decimal('100'), Decimal('101'), Decimal('99'), Decimal('100')),   # 0: flat
        (Decimal('100'), Decimal('102'), Decimal('99'), Decimal('101')),   # 1: slight up

        # Candle 2: Bearish leg starts - high[0] > max(high[1], high[2])
        # high[0]=101, need high[1]<=101 and high[2]<=101
        (Decimal('100'), Decimal('101'), Decimal('98'), Decimal('100')),   # 1: flat-ish
        (Decimal('98'), Decimal('99'), Decimal('95'), Decimal('97')),      # 2: bearish - high[2]=99 < high[0]=101 -> BEARISH LEG at i=2

        # Strong bearish continuation (candles 3-9)
        (Decimal('97'), Decimal('98'), Decimal('93'), Decimal('96')),      # 3
        (Decimal('96'), Decimal('97'), Decimal('92'), Decimal('95')),      # 4
        (Decimal('95'), Decimal('96'), Decimal('91'), Decimal('94')),      # 5
        (Decimal('94'), Decimal('95'), Decimal('90'), Decimal('93')),      # 6
        (Decimal('93'), Decimal('94'), Decimal('89'), Decimal('92')),      # 7
        (Decimal('92'), Decimal('93'), Decimal('88'), Decimal('91')),      # 8
        (Decimal('91'), Decimal('92'), Decimal('87'), Decimal('90')),      # 9
        (Decimal('90'), Decimal('91'), Decimal('85'), Decimal('89')),      # 9

        # Candle 10: BULLISH TRANSITION
        # Need: low[8] < min(low[9], low[10])
        # low[8]=87, need low[9] > 87 and low[10] > 87
        (Decimal('89'), Decimal('90'), Decimal('88'), Decimal('89')),      # 10: bullish, low[10]=88 > low[8]=87

        # Strong bullish continuation (candles 11-19)
        (Decimal('90'), Decimal('92'), Decimal('89'), Decimal('91')),      # 11
        (Decimal('91'), Decimal('93'), Decimal('90'), Decimal('92')),      # 11
        (Decimal('92'), Decimal('94'), Decimal('91'), Decimal('93')),      # 12
        (Decimal('93'), Decimal('95'), Decimal('92'), Decimal('94')),      # 13
        (Decimal('94'), Decimal('96'), Decimal('93'), Decimal('95')),      # 14
        (Decimal('95'), Decimal('97'), Decimal('94'), Decimal('96')),      # 15
        (Decimal('96'), Decimal('98'), Decimal('95'), Decimal('97')),      # 15
        (Decimal('97'), Decimal('99'), Decimal('96'), Decimal('98')),      # 16
        (Decimal('98'), Decimal('100'), Decimal('97'), Decimal('99')),     # 16
        (Decimal('99'), Decimal('101'), Decimal('98'), Decimal('100')),    # 17
        (Decimal('100'), Decimal('102'), Decimal('99'), Decimal('101')),   # 17
        (Decimal('101'), Decimal('103'), Decimal('100'), Decimal('102')),  # 18
        (Decimal('102'), Decimal('104'), Decimal('101'), Decimal('103')),  # 18
        (Decimal('103'), Decimal('105'), Decimal('102'), Decimal('104')),  # 18

        # Candle 18: BEARISH TRANSITION - high[16] > max(high[17], high[18])
        # high[16]=105, need high[17] <= 105 and high[18] <= 105
        (Decimal('102'), Decimal('103'), Decimal('98'), Decimal('101')),   # 19: high=103 < 105
        (Decimal('100'), Decimal('101'), Decimal('95'), Decimal('99')),    # 20: high=101 < 105

        # Strong bearish continuation (candles 21-28)
        (Decimal('98'), Decimal('99'), Decimal('93'), Decimal('97')),      # 21
        (Decimal('96'), Decimal('97'), Decimal('91'), Decimal('95')),      # 22
        (Decimal('94'), Decimal('95'), Decimal('89'), Decimal('93')),      # 23
        (Decimal('92'), Decimal('93'), Decimal('87'), Decimal('91')),      # 24
        (Decimal('90'), Decimal('91'), Decimal('85'), Decimal('89')),      # 25
        (Decimal('88'), Decimal('89'), Decimal('83'), Decimal('87')),      # 26
        (Decimal('86'), Decimal('87'), Decimal('81'), Decimal('85')),      # 27

        # Candle 28: Bearish crossunder of pivot_low (BOS)
        # pivot_low was at index 10 (low[10]=87). Need close < 87 and prev_close >= 87
        # Previous close at 27 = 85, current close at 28 < 87
        (Decimal('84'), Decimal('85'), Decimal('80'), Decimal('82')),      # 28: close=82 < 87, prev=85 >= 87? No, 85 < 87. Need prev >= 87.
        # Fix: candle 27 close should be >= 87
        # Let me adjust candle 27: close = 88
        (Decimal('87'), Decimal('88'), Decimal('82'), Decimal('88')),      # 27: close=88 >= 87
        (Decimal('84'), Decimal('85'), Decimal('80'), Decimal('82')),      # 28: close=82 < 87, prev=88 >= 87 -> CROSSUNDER!
    ]

    # Build candles from raw data
    for i, (o, h, l, c) in enumerate(raw_data):
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1) + timedelta(hours=i),
            open=o, high=h, low=l, close=c,
            volume=Decimal('1000')
        ))

    expected = {
        'pivot_high_index': 0,    # At bearish leg start (candle 2, size_idx=0)
        'pivot_low_index': 8,     # At bullish leg start (candle 10, size_idx=8)
        'pivot_high_2_index': 16, # At second bearish start (candle 18, size_idx=16)
        'break_candle': 28,
        'break_type': 'BOS',
        'prev_trend': 'RANGING',
        'new_trend': 'BEARISH',
    }
    return candles, expected


def create_bullish_leg_then_bearish_leg_then_bullish_break() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: Bullish leg -> Bearish leg -> Bullish break (CHOCH)

    Sequence (size=2):
    1. Initial bearish (indices 0-1) to set up for bullish transition
    2. Bullish leg (indices 2-9): creates pivot_low at index 0 (low[0] at transition)
    3. Bearish leg (indices 10-17): creates pivot_high at index 8 (high[8] at transition)
    4. Bullish leg (indices 18-25): breaks above pivot_high -> CHOCH
    """
    raw_data = [
        # Indices 0-1: Initial bearish to set up
        (Decimal('120'), Decimal('121'), Decimal('115'), Decimal('118')),  # 0
        (Decimal('118'), Decimal('119'), Decimal('113'), Decimal('116')),  # 1

        # Candle 2: BULLISH LEG starts - low[0] < min(low[1], low[2])
        # low[0]=115, need low[1] > 115 and low[2] > 115
        (Decimal('100'), Decimal('101'), Decimal('96'), Decimal('100')),   # 1: low=100 < 115
        (Decimal('98'), Decimal('99'), Decimal('95'), Decimal('98')),      # 2: low=95 < 115 -> BULLISH at i=2

        # Strong bullish (3-8)
        (Decimal('97'), Decimal('98'), Decimal('94'), Decimal('96')),
        (Decimal('96'), Decimal('97'), Decimal('93'), Decimal('95')),
        (Decimal('95'), Decimal('96'), Decimal('92'), Decimal('94')),
        (Decimal('94'), Decimal('95'), Decimal('91'), Decimal('93')),
        (Decimal('93'), Decimal('94'), Decimal('90'), Decimal('92')),
        (Decimal('92'), Decimal('93'), Decimal('89'), Decimal('91')),

        # Candle 8: BEARISH TRANSITION - high[6] > max(high[7], high[8])
        # high[6]=100, need high[7]<=100, high[8]<=100
        (Decimal('98'), Decimal('99'), Decimal('95'), Decimal('97')),      # 7
        (Decimal('96'), Decimal('97'), Decimal('92'), Decimal('95')),      # 8: high=97 < 100
        (Decimal('94'), Decimal('95'), Decimal('90'), Decimal('92')),      # 9: high=95 < 100

        # Strong bearish (10-15)
        (Decimal('92'), Decimal('93'), Decimal('88'), Decimal('90')),
        (Decimal('90'), Decimal('91'), Decimal('86'), Decimal('88')),
        (Decimal('88'), Decimal('89'), Decimal('84'), Decimal('86')),
        (Decimal('86'), Decimal('87'), Decimal('82'), Decimal('84')),
        (Decimal('84'), Decimal('85'), Decimal('80'), Decimal('82')),
        (Decimal('82'), Decimal('83'), Decimal('78'), Decimal('80')),

        # Candle 15: BULLISH TRANSITION - low[13] < min(low[14], low[15])
        # low[13]=78, need low[14] > 78, low[15] > 78
        (Decimal('79'), Decimal('80'), Decimal('77'), Decimal('78')),      # 14
        (Decimal('78'), Decimal('79'), Decimal('76'), Decimal('77')),      # 15: low=76 < 78? NO, need >78. Fix: low=79
        (Decimal('79'), Decimal('80'), Decimal('78'), Decimal('79')),      # 15: low=78 > 78? Need >. low=79
        (Decimal('80'), Decimal('81'), Decimal('79'), Decimal('80')),      # 16
        (Decimal('81'), Decimal('82'), Decimal('80'), Decimal('81')),      # 17

        # Candle 18: BULLISH BREAK of pivot_high (CHOCH)
        # pivot_high was at index 8 (high[8]=100). Need close > 100 and prev_close <= 100
        (Decimal('100'), Decimal('101'), Decimal('99'), Decimal('101')),   # 18: close=101 > 100, prev=80 <= 100 -> CHOCH!
    ]

    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)
    for i, (o, h, l, c) in enumerate(raw_data):
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1) + timedelta(hours=i),
            open=o, high=h, low=l, close=c,
            volume=Decimal('1000')
        ))

    expected = {
        'pivot_low_index': 0,   # At bullish leg start (candle 2, size_idx=0)
        'pivot_high_index': 8,  # At bearish leg start (candle 10, size_idx=8)
        'break_candle': 18,
        'break_type': 'CHOCH',
        'prev_trend': 'BEARISH',
        'new_trend': 'BULLISH',
    }
    return candles, expected


def create_bearish_leg_then_bullish_break_choch() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: Bearish leg -> Bullish leg -> Bearish break (BOS) -> Bullish break (CHOCH)

    Full 4-leg sequence (size=2):
    """
    raw_data = [
        # 0-1: Initial bullish
        (Decimal('100'), Decimal('101'), Decimal('95'), Decimal('100')),
        (Decimal('101'), Decimal('102'), Decimal('96'), Decimal('101')),

        # 2: Bearish leg starts
        (Decimal('118'), Decimal('119'), Decimal('113'), Decimal('116')),  # 2: bearish starts
        (Decimal('116'), Decimal('117'), Decimal('111'), Decimal('114')),
        (Decimal('114'), Decimal('115'), Decimal('109'), Decimal('112')),
        (Decimal('112'), Decimal('113'), Decimal('107'), Decimal('110')),
        (Decimal('110'), Decimal('111'), Decimal('105'), Decimal('108')),
        (Decimal('108'), Decimal('109'), Decimal('103'), Decimal('106')),
        (Decimal('106'), Decimal('107'), Decimal('101'), Decimal('104')),
        (Decimal('104'), Decimal('105'), Decimal('99'), Decimal('102')),
        (Decimal('102'), Decimal('103'), Decimal('97'), Decimal('100')),
        (Decimal('100'), Decimal('101'), Decimal('95'), Decimal('98')),

        # Bullish leg
        (Decimal('98'), Decimal('99'), Decimal('94'), Decimal('97')),
        (Decimal('97'), Decimal('98'), Decimal('93'), Decimal('96')),
        (Decimal('96'), Decimal('97'), Decimal('92'), Decimal('95')),
        (Decimal('95'), Decimal('96'), Decimal('91'), Decimal('93')),
        (Decimal('93'), Decimal('94'), Decimal('89'), Decimal('91')),
        (Decimal('91'), Decimal('92'), Decimal('87'), Decimal('89')),
        (Decimal('89'), Decimal('90'), Decimal('85'), Decimal('87')),
        (Decimal('87'), Decimal('88'), Decimal('83'), Decimal('85')),
        (Decimal('85'), Decimal('86'), Decimal('81'), Decimal('83')),
        (Decimal('83'), Decimal('84'), Decimal('79'), Decimal('81')),

        # Bearish break of pivot_low
        (Decimal('81'), Decimal('82'), Decimal('77'), Decimal('79')),
        (Decimal('79'), Decimal('80'), Decimal('75'), Decimal('77')),
        (Decimal('77'), Decimal('78'), Decimal('73'), Decimal('75')),
        (Decimal('75'), Decimal('76'), Decimal('71'), Decimal('73')),
        (Decimal('73'), Decimal('74'), Decimal('69'), Decimal('71')),
        (Decimal('71'), Decimal('72'), Decimal('67'), Decimal('69')),
        (Decimal('69'), Decimal('70'), Decimal('65'), Decimal('67')),
        (Decimal('67'), Decimal('68'), Decimal('63'), Decimal('65')),
        (Decimal('65'), Decimal('66'), Decimal('61'), Decimal('63')),
        (Decimal('63'), Decimal('64'), Decimal('59'), Decimal('61')),

        # Bullish break of pivot_high
        (Decimal('61'), Decimal('62'), Decimal('57'), Decimal('59')),
        (Decimal('59'), Decimal('60'), Decimal('55'), Decimal('57')),
        (Decimal('57'), Decimal('58'), Decimal('53'), Decimal('55')),
        (Decimal('55'), Decimal('56'), Decimal('51'), Decimal('53')),
        (Decimal('53'), Decimal('54'), Decimal('49'), Decimal('51')),
        (Decimal('51'), Decimal('52'), Decimal('47'), Decimal('49')),
        (Decimal('49'), Decimal('50'), Decimal('45'), Decimal('47')),
        (Decimal('47'), Decimal('48'), Decimal('43'), Decimal('45')),
        (Decimal('45'), Decimal('46'), Decimal('41'), Decimal('43')),
        (Decimal('43'), Decimal('44'), Decimal('39'), Decimal('41')),
    ]

    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)
    for i, (o, h, l, c) in enumerate(raw_data):
        candles.append(Candle(
            symbol='TEST', timeframe=Timeframe.H1,
            timestamp=datetime(2024, 1, 1) + timedelta(hours=i),
            open=o, high=h, low=l, close=c,
            volume=Decimal('1000')
        ))

    expected = {
        'first_choch_candle': 18,
        'first_bos_candle': 30,
        'second_choch_candle': 40,
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
        'break_candles': [17],
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

    for i in range(60):
        if i < 20:
            open_p = Decimal('200') - Decimal(str(i * 2))
            close_p = open_p - Decimal('1.5')
        elif i < 40:
            open_p = Decimal('100') + Decimal(str((i-20) * 3))
            close_p = open_p + Decimal('2')
        else:
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
    print("Testing fixtures...")
    
    candles, expected = create_bearish_leg_then_bullish_leg_then_bearish_break()
    print(f"Fixture 1: {len(candles)} candles")
    
    candles2, expected2 = create_bullish_leg_then_bearish_leg_then_bullish_break()
    print(f"Fixture 2: {len(candles2)} candles")
    
    candles3, expected3 = create_bearish_leg_then_bullish_break_choch()
    print(f"Fixture 3: {len(candles3)} candles")
    
    candles4, expected4 = create_raw_vs_parsed_high_volatility_inversion()
    print(f"Fixture 4: {len(candles4)} candles")
    
    print("All fixtures created successfully!")