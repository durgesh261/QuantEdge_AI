"""
Script to design OHLC values that produce exact LuxAlgo leg transitions.
"""
from decimal import Decimal
from datetime import datetime, timedelta
from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType

def trace_detector(candles, length=2):
    """Trace detector state through all candles."""
    parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)
    detector = StructureDetector(StructureConfig(length=length, structure_type=StructureType.INTERNAL))
    
    events = []
    for i, pc in enumerate(parsed):
        breaks = detector.process_candle(pc, i)
        leg = detector.state.current_leg
        prev_leg = detector.state.previous_leg
        trend = detector.state.trend
        ph = detector.state.pivot_high
        pl = detector.state.pivot_low
        
        if breaks:
            events.append(('break', i, breaks[0].break_type, breaks[0].direction, trend))
        if leg != prev_leg and prev_leg != 0:
            events.append(('leg_change', i, prev_leg, leg, 
                          ph.index if ph else None, pl.index if pl else None))
    
    return events, detector

def test_sequence():
    """Test a sequence and print events."""
    # Build a proper sequence
    candles = []
    base_time = datetime(2024, 1, 1, 0, 0, 0)
    
    # We need: bearish leg at candle 2, bullish at 10, bearish at 20, break at 28
    # Let me design this carefully
    
    # For bearish leg at candle 2 (i=2):
    # Need high[0] > max(high[1], high[2])
    # size_idx = 0, pivot_high at index 0
    
    # For bullish leg at candle 10 (i=10):
    # Need low[8] < min(low[9], low[10])
    # size_idx = 8, pivot_low at index 8
    
    # For bearish leg at candle 20 (i=20):
    # Need high[18] > max(high[19], high[20])
    # size_idx = 18, pivot_high at index 18
    
    # For bearish break at candle 28:
    # pivot_low at index 8 (price = low[8])
    # Need prev_close >= pivot_low AND curr_close < pivot_low at candle 28
    
    # Let's design
    
    # Index 0: flat base
    # Index 1: flat base
    # Index 2: bearish transition -> high[0] > max(high[1], high[2])
    # Index 3-9: bearish continuation
    # Index 10: bullish transition -> low[8] < min(low[9], low[10])
    # Index 11-19: bullish continuation
    # Index 20: bearish transition -> high[18] > max(high[19], high[20])
    # Index 21-27: bearish continuation
    # Index 28: bearish crossunder of pivot_low
    
    # Design values
    raw_data = [
        # 0: base
        (Decimal('100'), Decimal('101'), Decimal('99'), Decimal('100')),
        # 1: base
        (Decimal('100'), Decimal('101'), Decimal('99'), Decimal('100')),
        # 2: bearish transition - high[0]=101 > max(high[1]=101, high[2]=?)
        # Need high[2] <= 101, so max is 101. high[0]=101 > 101? No, need strictly greater.
        # So high[0] must be > max(high[1], high[2])
        # Let's set high[0] = 105, high[1] = 100, high[2] = 98
        # high[0]=105 > max(100, 98)=100 -> bearish at i=2
        (Decimal('104'), Decimal('105'), Decimal('99'), Decimal('102')),  # 0: high=105
        (Decimal('100'), Decimal('100'), Decimal('98'), Decimal('99')),   # 1: high=100
        (Decimal('98'), Decimal('98'), Decimal('95'), Decimal('96')),     # 2: high=98 < 105 -> BEARISH at i=2
        
        # 3-9: bearish continuation (need to keep leg bearish)
        (Decimal('96'), Decimal('97'), Decimal('93'), Decimal('95')),     # 3
        (Decimal('94'), Decimal('95'), Decimal('91'), Decimal('93')),     # 4
        (Decimal('92'), Decimal('93'), Decimal('89'), Decimal('91')),     # 5
        (Decimal('90'), Decimal('91'), Decimal('87'), Decimal('89')),     # 6
        (Decimal('88'), Decimal('89'), Decimal('85'), Decimal('87')),     # 7
        (Decimal('86'), Decimal('87'), Decimal('83'), Decimal('85')),     # 8: low[8]=83 - this will be pivot_low
        (Decimal('84'), Decimal('85'), Decimal('81'), Decimal('83')),     # 9
        
        # 10: bullish transition - need low[8]=83 < min(low[9]=81, low[10]=?)
        # Wait, low[9]=81 which is < 83, so min would be 81, not > 83.
        # Need low[9] > 83 and low[10] > 83
        # Let's fix: low[8]=83, low[9]=85, low[10]=84
        # 83 < min(85, 84)=84 -> bullish at i=10
        (Decimal('84'), Decimal('85'), Decimal('85'), Decimal('85')),     # 9 fixed: low=85
        (Decimal('85'), Decimal('86'), Decimal('84'), Decimal('85')),     # 10: low=84 > 83 -> BULLISH at i=10
        
        # 11-19: bullish continuation
        (Decimal('86'), Decimal('87'), Decimal('85'), Decimal('86')),
        (Decimal('87'), Decimal('88'), Decimal('86'), Decimal('87')),
        (Decimal('88'), Decimal('89'), Decimal('87'), Decimal('88')),
        (Decimal('89'), Decimal('90'), Decimal('88'), Decimal('89')),
        (Decimal('90'), Decimal('91'), Decimal('89'), Decimal('90')),
        (Decimal('91'), Decimal('92'), Decimal('90'), Decimal('91')),
        (Decimal('92'), Decimal('93'), Decimal('91'), Decimal('92')),
        (Decimal('93'), Decimal('94'), Decimal('92'), Decimal('93')),
        (Decimal('94'), Decimal('95'), Decimal('93'), Decimal('94')),
        
        # 20: bearish transition - need high[18] > max(high[19], high[20])
        # high[18]=95, need high[19] <= 95 and high[20] <= 95
        (Decimal('95'), Decimal('95'), Decimal('94'), Decimal('94')),     # 19: high=95
        (Decimal('94'), Decimal('94'), Decimal('92'), Decimal('93')),     # 20: high=94 < 95 -> BEARISH at i=20
        
        # 21-27: bearish continuation
        (Decimal('92'), Decimal('93'), Decimal('89'), Decimal('91')),
        (Decimal('90'), Decimal('91'), Decimal('87'), Decimal('89')),
        (Decimal('88'), Decimal('89'), Decimal('85'), Decimal('87')),
        (Decimal('86'), Decimal('87'), Decimal('83'), Decimal('85')),
        (Decimal('84'), Decimal('85'), Decimal('81'), Decimal('83')),
        (Decimal('82'), Decimal('83'), Decimal('79'), Decimal('81')),
        (Decimal('80'), Decimal('81'), Decimal('77'), Decimal('79')),     # 27: close=79
        
        # 28: bearish crossunder of pivot_low (pivot_low = low[8] = 83)
        # Need prev_close >= 83 AND curr_close < 83
        # prev_close at 27 = 79 < 83. Not good.
        # Need to fix candle 27 to have close >= 83
        # But candle 27 is bearish continuation. Let's make it close at 84.
        # Actually, let's redo from candle 20.
    ]
    
    return raw_data

if __name__ == "__main__":
    raw_data = test_sequence()
    print(f"Raw data length: {len(raw_data)}")
    for i, (o, h, l, c) in enumerate(raw_data):
        print(f"{i}: O={o} H={h} L={l} C={c}")