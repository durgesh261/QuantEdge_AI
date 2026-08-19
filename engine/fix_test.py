with open('tests/test_structure_luxalgo.py', 'r') as f:
    content = f.read()

old = """    def test_bearish_crossunder_of_pivot_low_emits_choch_when_trend_bullish(self):
        \"\"\"Bearish crossunder of pivot_low with prev trend BULLISH -> CHOCH.\"\"\"
        # Create a fixture that produces: Bearish -> Bullish -> Bearish break (CHOCH)
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
    
        # Leg 1: Bearish (indices 0-4) - sets up for bullish transition
        for i in range(5):
            open_p = Decimal('120') - Decimal(str(i * 4))
            close_p = open_p - Decimal('2')
            high_p = max(open_p, close_p) + Decimal('1')
            low_p = min(open_p, close_p) - Decimal('1')
            candles.append(Candle(
                symbol='TEST', timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_p, high=high_p, low=low_p, close=close_p,
                volume=Decimal('1000')
            ))
    
        # Leg 2: Bullish (indices 5-14) - creates pivot_low at transition from bearish
        for i in range(5, 15):
            open_p = Decimal('100') + Decimal(str((i-5) * 3))
            close_p = open_p + Decimal('2')
            high_p = max(open_p, close_p) + Decimal('2')
            low_p = min(open_p, close_p) - Decimal('1')
            candles.append(Candle(
                symbol='TEST', timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_p, high=high_p, low=low_p, close=close_p,
                volume=Decimal('1000')
            ))
    
        # Leg 3: Bearish (indices 15-24) - breaks below pivot_low -> CHOCH
        for i in range(15, 25):
            if i == 15:
                open_p = Decimal('100')
                close_p = Decimal('98')  # Below pivot_low
                high_p = max(open_p, close_p) + Decimal('1')
                low_p = min(open_p, close_p) - Decimal('1')
            else:
                open_p = Decimal('100') - Decimal(str((i-15) * 3))
                close_p = open_p - Decimal('2')
                high_p = max(open_p, close_p) + Decimal('1')
                low_p = min(open_p, close_p) - Decimal('1')
            candles.append(Candle(
                symbol='TEST', timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_p, high=high_p, low=low_p, close=close_p,
                volume=Decimal('1000')
            ))
    
        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)
    
        detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))
    
        breaks_found = []
        for i, pc in enumerate(parsed):
            breaks = detector.process_candle(pc, i)
            breaks_found.extend(breaks)
    
        # Find the bearish break (CHOCH because prev_trend=BULLISH)
        bearish_breaks = [b for b in breaks_found if b.direction == TrendDirection.BEARISH]
        assert len(bearish_breaks) >= 1
        choch_breaks = [b for b in bearish_breaks if b.break_type == BreakType.CHOCH]
        assert len(choch_breaks) >= 1
"""

new = """    def test_bearish_crossunder_of_pivot_low_emits_choch_when_trend_bullish(self):
        \"\"\"Bearish crossunder of pivot_low with prev trend BULLISH -> CHOCH.\"\"\"
        # Need 3 legs: Bearish -> Bullish (creates pivot_low, sets trend=BULLISH) -> Bearish (crosses pivot_low -> CHOCH)
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)

        # Leg 1: Bearish (indices 0-4) - creates pivot_high at transition
        for i in range(5):
            open_p = Decimal('120') - Decimal(str(i * 4))
            close_p = open_p - Decimal('2')
            high_p = max(open_p, close_p) + Decimal('1')
            low_p = min(open_p, close_p) - Decimal('1')
            candles.append(Candle(
                symbol='TEST', timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_p, high=high_p, low=low_p, close=close_p,
                volume=Decimal('1000')
            ))

        # Leg 2: Bullish (indices 5-14) - creates pivot_low at transition from bearish
        for i in range(5, 15):
            open_p = Decimal('100') + Decimal(str((i-5) * 3))
            close_p = open_p + Decimal('2')
            high_p = max(open_p, close_p) + Decimal('2')
            low_p = min(open_p, close_p) - Decimal('1')
            candles.append(Candle(
                symbol='TEST', timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_p, high=high_p, low=low_p, close=close_p,
                volume=Decimal('1000')
            ))

        # Leg 3: Bearish (indices 15-24) - breaks below pivot_low -> CHOCH
        for i in range(15, 25):
            if i == 15:
                open_p = Decimal('100')
                close_p = Decimal('98')  # Below pivot_low
                high_p = max(open_p, close_p) + Decimal('1')
                low_p = min(open_p, close_p) - Decimal('1')
            else:
                open_p = Decimal('100') - Decimal(str((i-15) * 3))
                close_p = open_p - Decimal('2')
                high_p = max(open_p, close_p) + Decimal('1')
                low_p = min(open_p, close_p) - Decimal('1')
            candles.append(Candle(
                symbol='TEST', timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_p, high=high_p, low=low_p, close=close_p,
                volume=Decimal('1000')
            ))

        parsed = parse_candles_with_volatility(candles, atr_period=10, atr_multiplier=2.0)

        detector = StructureDetector(StructureConfig(length=2, structure_type=StructureType.INTERNAL))

        breaks_found = []
        for i, pc in enumerate(parsed):
            breaks = detector.process_candle(pc, i)
            breaks_found.extend(breaks)

        # Find the bearish break (CHOCH because prev_trend=BULLISH)
        bearish_breaks = [b for b in breaks_found if b.direction == TrendDirection.BEARISH]
        assert len(bearish_breaks) >= 1
        choch_breaks = [b for b in bearish_breaks if b.break_type == BreakType.CHOCH]
        assert len(choch_breaks) >= 1
"""

with open('tests/test_structure_luxalgo.py', 'r') as f:
    content = f.read()

content = content.replace(old, new)

with open('tests/test_structure_luxalgo.py', 'w') as f:
    f.write(content)
print('Done')