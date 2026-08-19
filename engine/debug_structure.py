import sys
sys.path.insert(0, '.')

from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.market_data.models import Candle, Timeframe
from datetime import datetime, timedelta
from decimal import Decimal

# Create a simple bullish trend
candles = []
base_time = datetime(2024, 1, 1, 0, 0, 0)
for i in range(10):
    open_p = Decimal('100') + Decimal(str(i * 0.5))
    candles.append(Candle(
        symbol='TEST', timeframe='1h',
        timestamp=base_time + timedelta(hours=i),
        open=open_p, high=open_p + Decimal('1.0'),
        low=open_p - Decimal('0.3'), close=open_p + Decimal('0.5'),
        volume=Decimal('1000')
    ))

from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType

parsed = parse_candles_with_volatility(candles, atr_period=5, atr_multiplier=2.0)

detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))
for i, pc in enumerate(parsed):
    breaks = detector.process_candle(pc, i)
    print(f'Candle {i}: breaks={len(breaks)}, leg={detector.state.current_leg}, pivot_high={detector.state.pivot_high is not None}, pivot_low={detector.state.pivot_low is not None}, pivot_high_price={detector.state.pivot_high.price if detector.state.pivot_high else None}, pivot_low_price={detector.state.pivot_low.price if detector.state.pivot_low else None}')

highs, lows = detector.get_confirmed_pivots()
print(f'Pivots: highs={len(highs)}, lows={len(lows)}')
print(f'First leg established: {detector._first_leg_established}')
print(f'Current leg: {detector.state.current_leg}')
print(f'Pivot high: {detector.state.pivot_high}')
print(f'Pivot low: {detector.state.pivot_low}')
print(f'Leg high: {detector._leg_high}, leg low: {detector._leg_low}')
print(f'First leg established: {detector._first_leg_established}')