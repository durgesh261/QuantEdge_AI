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
    Indices 0-28 (29 candles total)

    Leg transitions (size=2):
    - Candle 2: Bearish leg starts (first leg, no pivot)
    - Candle 12: Bullish leg starts -> pivot_low at index 10 (low[10])
    - Candle 18: Bearish leg starts -> pivot_high at index 16 (high[16])
    - Candle 28: Bearish crossunder of pivot_low -> BOS (prev_trend=RANGING)

    Key insight: For size=2, leg transitions happen when:
    - Bearish: high[i-2] > max(high[i-1], high[i])
    - Bullish: low[i-2] < min(low[i-1], low[i])
    """
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)

    # Raw OHLC values that produce exact leg transitions at specified candles
    # Format: (open, high, low, close)
    raw_data = [
        # 0: base high=110
        (Decimal('108'), Decimal('110'), Decimal('105'), Decimal('108')),
        # 1: base high=100
        (Decimal('98'), Decimal('100'), Decimal('95'), Decimal('98')),
        # 2: bearish start - high[0]=110 > max(high[1]=100, high[2]=95)=100
        (Decimal('93'), Decimal('95'), Decimal('90'), Decimal('93')),
        # 3: bearish continuation
        (Decimal('91'), Decimal('93'), Decimal('88'), Decimal('91')),
        # 4:
        (Decimal('89'), Decimal('91'), Decimal('86'), Decimal('89')),
        # 5:
        (Decimal('87'), Decimal('89'), Decimal('84'), Decimal('87')),
        # 6:
        (Decimal('85'), Decimal('87'), Decimal('82'), Decimal('85')),
        # 7:
        (Decimal('83'), Decimal('85'), Decimal('80'), Decimal('83')),
        # 8:
        (Decimal('81'), Decimal('83'), Decimal('78'), Decimal('81')),
        # 9: low=78 (raised from 76 to prevent early bullish transition at candle 11)
        (Decimal('79'), Decimal('81'), Decimal('78'), Decimal('79')),
        # 10: pivot_low candidate - low=78
        (Decimal('79'), Decimal('81'), Decimal('78'), Decimal('80')),
        # 11:
        (Decimal('80'), Decimal('82'), Decimal('79'), Decimal('81')),
        # 12: bullish start - low[10]=78 < min(low[11]=79, low[12]=80)=79
        (Decimal('81'), Decimal('83'), Decimal('80'), Decimal('82')),
        # 13: bullish continuation
        (Decimal('82'), Decimal('84'), Decimal('81'), Decimal('83')),
        # 14:
        (Decimal('83'), Decimal('85'), Decimal('82'), Decimal('84')),
        # 15:
        (Decimal('84'), Decimal('86'), Decimal('83'), Decimal('85')),
        # 16: pivot_high candidate - high=110
        (Decimal('108'), Decimal('110'), Decimal('88'), Decimal('109')),
        # 17:
        (Decimal('98'), Decimal('100'), Decimal('90'), Decimal('98')),
        # 18: bearish start - high[16]=110 > max(high[17]=100, high[18]=95)=100
        (Decimal('93'), Decimal('95'), Decimal('88'), Decimal('93')),
        # 19: bearish continuation
        (Decimal('91'), Decimal('93'), Decimal('86'), Decimal('91')),
        # 20:
        (Decimal('89'), Decimal('91'), Decimal('84'), Decimal('89')),
        # 21:
        (Decimal('87'), Decimal('89'), Decimal('82'), Decimal('87')),
        # 22:
        (Decimal('85'), Decimal('87'), Decimal('80'), Decimal('85')),
# 23:
        (Decimal('83'), Decimal('85'), Decimal('78'), Decimal('83')),
        # 24:
        (Decimal('81'), Decimal('83'), Decimal('76'), Decimal('81')),
        # 25: low=78, close=79 >= 78
        (Decimal('79'), Decimal('81'), Decimal('78'), Decimal('79')),
        # 26: low=78, close=80 >= 78
        (Decimal('80'), Decimal('82'), Decimal('78'), Decimal('80')),
        # 27: low=78, close=81 >= 78
        (Decimal('81'), Decimal('83'), Decimal('78'), Decimal('81')),
        # 28: crossunder - close=76 < 78, prev=81 >= 78, low=74
        (Decimal('76'), Decimal('78'), Decimal('74'), Decimal('76')),
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
        'pivot_low_index': 10,      # At bullish leg start (candle 12, size_idx=10)
        'pivot_low_price': Decimal('78'),  # raw low[10]
        'pivot_high_index': 16,     # At second bearish start (candle 18, size_idx=16)
        'pivot_high_price': Decimal('110'), # raw high[16]
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
    4. Bullish leg (indices 18-25): breaks above pivot_high -> CHOCH (prev_trend=BEARISH)
    """
    raw_data = [
        # 0-1: Initial bearish to set up
        (Decimal('120'), Decimal('122'), Decimal('115'), Decimal('118')),  # 0: high=122
        (Decimal('118'), Decimal('120'), Decimal('113'), Decimal('116')),  # 1: high=120

        # 2: BULLISH LEG starts - low[0] < min(low[1], low[2])
        # low[0]=115, need low[1] > 115 and low[2] > 115
        (Decimal('116'), Decimal('118'), Decimal('116'), Decimal('117')),  # 1: low=116 > 115
        (Decimal('117'), Decimal('119'), Decimal('116'), Decimal('118')),  # 2: low=116 > 115 -> BULLISH at i=2

        # Strong bullish (3-8)
        (Decimal('116'), Decimal('118'), Decimal('114'), Decimal('116')),
        (Decimal('115'), Decimal('117'), Decimal('113'), Decimal('115')),
        (Decimal('114'), Decimal('116'), Decimal('112'), Decimal('114')),
        (Decimal('113'), Decimal('115'), Decimal('111'), Decimal('113')),
        (Decimal('112'), Decimal('114'), Decimal('110'), Decimal('112')),
        (Decimal('111'), Decimal('113'), Decimal('109'), Decimal('111')),

        # 10: BEARISH TRANSITION - high[8] > max(high[9], high[10])
        # high[8]=113, need high[9]<=113, high[10]<=113
        (Decimal('113'), Decimal('113'), Decimal('109'), Decimal('111')),  # 9: high=113
        (Decimal('111'), Decimal('112'), Decimal('107'), Decimal('110')),  # 10: high=112 <= 113
        (Decimal('109'), Decimal('110'), Decimal('105'), Decimal('108')),  # 11: high=110 <= 113

        # Strong bearish (12-17)
        (Decimal('109'), Decimal('110'), Decimal('103'), Decimal('108')),
        (Decimal('107'), Decimal('108'), Decimal('101'), Decimal('106')),
        (Decimal('105'), Decimal('106'), Decimal('99'), Decimal('104')),
        (Decimal('103'), Decimal('104'), Decimal('97'), Decimal('102')),
        (Decimal('101'), Decimal('102'), Decimal('95'), Decimal('100')),
        (Decimal('99'), Decimal('100'), Decimal('93'), Decimal('98')),

        # 18: BULLISH TRANSITION - low[16] < min(low[17], low[18])
        # low[16]=93, need low[17] > 93, low[18] > 93
        (Decimal('98'), Decimal('100'), Decimal('94'), Decimal('99')),      # 17: low=94
        (Decimal('97'), Decimal('99'), Decimal('94'), Decimal('98')),      # 18: low=94 > 93 -> BULLISH at i=18

        # 19-25: bullish continuation
        (Decimal('98'), Decimal('100'), Decimal('94'), Decimal('99')),
        (Decimal('99'), Decimal('101'), Decimal('95'), Decimal('100')),
        (Decimal('100'), Decimal('102'), Decimal('96'), Decimal('101')),
        (Decimal('101'), Decimal('103'), Decimal('97'), Decimal('102')),
        (Decimal('102'), Decimal('104'), Decimal('98'), Decimal('103')),
        (Decimal('103'), Decimal('105'), Decimal('99'), Decimal('104')),
        (Decimal('104'), Decimal('106'), Decimal('100'), Decimal('105')),

        # 26: BULLISH BREAK of pivot_high (CHOCH)
        # pivot_high was at index 8 (high[8]=113). Need close > 113 and prev_close <= 113
        (Decimal('112'), Decimal('113'), Decimal('111'), Decimal('112')),  # 25: close=112 <= 113
        (Decimal('114'), Decimal('115'), Decimal('113'), Decimal('114')),  # 26: close=114 > 113, prev=112 <= 113
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
        'break_candle': 26,
        'break_type': 'CHOCH',
        'prev_trend': 'BEARISH',
        'new_trend': 'BULLISH',
    }
    return candles, expected


def create_bearish_leg_then_bullish_break_choch() -> Tuple[List[Candle], Dict[str, Any]]:
    """
    Fixture: Bearish leg -> Bullish leg -> Bearish break (BOS) -> Bullish break (CHOCH)

    Full 4-leg sequence (size=2):
    1. Bearish leg (indices 2-11): creates pivot_high at index 0
    2. Bullish leg (indices 12-19): creates pivot_low at index 10, breaks pivot_high -> CHOCH (trend=BULLISH)
    3. Bearish leg (indices 20-27): creates pivot_high at index 18, breaks pivot_low -> BOS (trend=BEARISH)
    4. Bullish leg (indices 28-35): breaks pivot_high -> CHOCH (prev_trend=BEARISH)
    """
    raw_data = [
        # 0-1: Initial flat
        (Decimal('100'), Decimal('102'), Decimal('98'), Decimal('100')),
        (Decimal('100'), Decimal('102'), Decimal('98'), Decimal('100')),

        # 2: Bearish leg starts - high[0]=102 > max(high[1]=102, high[2]=95)
        (Decimal('93'), Decimal('95'), Decimal('90'), Decimal('93')),

        # 3-11: bearish continuation
        (Decimal('91'), Decimal('93'), Decimal('88'), Decimal('91')),
        (Decimal('89'), Decimal('91'), Decimal('86'), Decimal('89')),
        (Decimal('87'), Decimal('89'), Decimal('84'), Decimal('87')),
        (Decimal('85'), Decimal('87'), Decimal('82'), Decimal('85')),
        (Decimal('83'), Decimal('85'), Decimal('80'), Decimal('83')),
        (Decimal('81'), Decimal('83'), Decimal('78'), Decimal('81')),
        (Decimal('79'), Decimal('81'), Decimal('76'), Decimal('79')),
        (Decimal('79'), Decimal('81'), Decimal('78'), Decimal('80')),  # 10: low=78 (pivot_low)
        (Decimal('80'), Decimal('82'), Decimal('79'), Decimal('81')),  # 11

        # 12: Bullish start - low[10]=78 < min(low[11]=79, low[12]=80)=79
        (Decimal('81'), Decimal('83'), Decimal('80'), Decimal('82')),

        # 13-17: bullish continuation
        (Decimal('82'), Decimal('84'), Decimal('81'), Decimal('83')),
        (Decimal('83'), Decimal('85'), Decimal('82'), Decimal('84')),
        (Decimal('84'), Decimal('86'), Decimal('83'), Decimal('85')),
        (Decimal('85'), Decimal('87'), Decimal('84'), Decimal('86')),
        (Decimal('86'), Decimal('88'), Decimal('85'), Decimal('87')),

        # 18: pivot_high - high=110
        (Decimal('108'), Decimal('110'), Decimal('88'), Decimal('109')),

        # 19: high=100
        (Decimal('98'), Decimal('100'), Decimal('90'), Decimal('98')),

        # 20: Bearish break of pivot_high -> CHOCH (trend=BULLISH)
        # pivot_high at index 0 (high[0]=102). Need close > 102, prev_close <= 102
        (Decimal('102'), Decimal('103'), Decimal('101'), Decimal('103')),  # 20: close=103 > 102, prev=87 <= 102 -> CHOCH

        # 21-27: bearish continuation (trend=BULLISH, leg=BEARISH)
        (Decimal('100'), Decimal('102'), Decimal('95'), Decimal('100')),
        (Decimal('98'), Decimal('100'), Decimal('93'), Decimal('98')),
        (Decimal('96'), Decimal('98'), Decimal('91'), Decimal('96')),
        (Decimal('94'), Decimal('96'), Decimal('89'), Decimal('94')),
        (Decimal('92'), Decimal('94'), Decimal('87'), Decimal('92')),
        (Decimal('90'), Decimal('92'), Decimal('85'), Decimal('90')),
        (Decimal('88'), Decimal('90'), Decimal('83'), Decimal('88')),

        # 28: Bearish start (new leg) - high[26] > max(high[27], high[28])
        # high[26]=102, need high[27]<=102, high[28]<=102
        (Decimal('98'), Decimal('100'), Decimal('93'), Decimal('98')),   # 27
        (Decimal('95'), Decimal('97'), Decimal('90'), Decimal('95')),   # 28: high=97 <= 102

        # 29-35: bearish continuation
        (Decimal('93'), Decimal('95'), Decimal('88'), Decimal('93')),
        (Decimal('91'), Decimal('93'), Decimal('86'), Decimal('91')),
        (Decimal('89'), Decimal('91'), Decimal('84'), Decimal('89')),
        (Decimal('87'), Decimal('89'), Decimal('82'), Decimal('87')),
        (Decimal('85'), Decimal('87'), Decimal('80'), Decimal('85')),
        (Decimal('83'), Decimal('85'), Decimal('78'), Decimal('83')),
        (Decimal('81'), Decimal('83'), Decimal('76'), Decimal('81')),

        # 36: Bullish break of pivot_high -> CHOCH (prev_trend=BEARISH)
        # pivot_high at index 26 (high[26]=102). Need close > 102, prev_close <= 102
        (Decimal('102'), Decimal('103'), Decimal('101'), Decimal('103')), # 36: close=103 > 102
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
        'first_choch_candle': 20,
        'first_bos_candle': 28,  # This will be a BOS from bearish break of pivot_low
        'second_choch_candle': 36,
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