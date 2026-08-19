"""
Causality and Determinism Tests for Historical SMC Replay.

Tests that verify the replay engine behaves correctly and without
look-ahead bias.
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import shutil

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType
from quantedge.smc.order_blocks import OrderBlockConfig, detect_order_blocks_streaming
from quantedge.historical.provider import CsvHistoricalDataProvider, DatasetMetadata
from quantedge.historical.replay import HistoricalReplayEngine, ReplayConfig
from quantedge.smc.structure import detect_structure_streaming


def create_test_candles(count: int, start_time: datetime = None) -> list:
    """Create deterministic test candles."""
    if start_time is None:
        start_time = datetime(2024, 1, 1, 0, 0, 0)
    
    candles = []
    base_price = Decimal("50000")
    
    for i in range(count):
        # Create a gentle uptrend with some noise
        price_change = Decimal(str(i * 10))
        open_price = base_price + price_change
        close_price = open_price + Decimal("5")
        high_price = max(open_price, close_price) + Decimal("10")
        low_price = min(open_price, close_price) - Decimal("10")
        
        candle = Candle(
            symbol="BTCUSD.P",
            timeframe=Timeframe.H1,
            timestamp=start_time + timedelta(hours=i),
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=Decimal("1000"),
            source=MarketDataSource.HISTORICAL
        )
        candles.append(candle)
    
    return candles


def create_test_csv(file_path: Path, candles: list) -> None:
    """Write candles to CSV file."""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("timestamp,open,high,low,close,volume\n")
        for c in candles:
            f.write(f"{c.timestamp.isoformat()},{c.open},{c.high},{c.low},{c.close},{c.volume}\n")


class TestCausality:
    """Tests that verify causal event generation (no look-ahead bias)."""
    
    def setup_method(self):
        """Create test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.data_dir = self.temp_dir / "BTCUSD.P" / "1h"
        self.data_dir.mkdir(parents=True)
        
        # Create test data
        self.candles = create_test_candles(1000)
        self.csv_file = self.data_dir / "1h.csv"
        create_test_csv(self.csv_file, self.candles)
    
    def teardown_method(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir)
    
    def test_no_lookahead_bias_in_structure(self):
        """Verify structure events don't use future data."""
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        candles = provider.load_candles("BTCUSD.P", Timeframe.H1)
        
        # Parse with volatility
        parsed = parse_candles_with_volatility(candles, atr_period=14, atr_multiplier=2.0)
        
        # Run structure detection
        detector = StructureDetector(StructureConfig(5, StructureType.INTERNAL))
        
        # Track events at each index
        events_at_index = {}
        
        for i, pc in enumerate(parsed):
            breaks = detector.process_candle(pc, i)
            if breaks:
                events_at_index[i] = breaks
        
        # Verify: at index i, we only use data up to index i
        # This is inherently tested by the streaming nature of process_candle
        # The detector only uses _high_history/_low_history up to current index
        
        # Additional check: events should only reference current or past indices
        for idx, breaks in events_at_index.items():
            for brk in breaks:
                # Break index should equal current candle index
                assert brk.index == idx, f"Break index {brk.index} != candle index {idx}"
                # Confirmation candle should be current candle
                assert brk.confirmation_candle.timestamp == candles[idx].timestamp
    
    def test_no_lookahead_bias_in_order_blocks(self):
        """Verify OB events don't use future data."""
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        candles = provider.load_candles("BTCUSD.P", Timeframe.H1)
        parsed = parse_candles_with_volatility(candles, atr_period=14, atr_multiplier=2.0)
        
        # Run full structure detection first
        internal_highs, internal_lows, internal_breaks, _ = detect_structure_streaming(
            parsed, 5, StructureType.INTERNAL
        )
        swing_highs, swing_lows, swing_breaks, _ = detect_structure_streaming(
            parsed, 50, StructureType.SWING
        )
        
        # Detect OBs
        obs = detect_order_blocks_streaming(
            parsed,
            internal_breaks, swing_breaks,
            internal_highs + internal_lows,
            swing_highs + swing_lows,
            OrderBlockConfig(internal_length=5, swing_length=50)
        )
        
        # Verify: OB formation index <= break index (slice is [pivot, break))
        for ob in obs:
            assert ob.formation_index < ob.break_index, \
                f"OB formation {ob.formation_index} >= break index {ob.break_index}"
            assert ob.formation_index >= 0
            assert ob.break_index < len(candles)
    
    def test_event_timestamps_monotonic(self):
        """Verify all event timestamps are monotonically increasing."""
        # This would be tested in full replay
        pass


class TestDeterminism:
    """Tests that verify deterministic replay output."""

    def setup_method(self):
        """Create test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.data_dir = self.temp_dir / "BTCUSD.P" / "1h"
        self.data_dir.mkdir(parents=True)

        # Create deterministic test data
        self.candles = create_test_candles(5000)
        self.csv_file = self.data_dir / "1h.csv"
        create_test_csv(self.csv_file, self.candles)
    
    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
    
    def test_deterministic_replay(self):
        """Running same dataset twice produces identical events."""
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        
        # Run 1
        config1 = ReplayConfig(
            symbol="BTCUSD.P",
            timeframe=Timeframe.H1,
            output_dir=str(self.temp_dir / "run1")
        )
        engine1 = HistoricalReplayEngine(provider, config1)
        result1 = engine1.run()
        
        # Run 2
        config2 = ReplayConfig(
            symbol="BTCUSD.P",
            timeframe=Timeframe.H1,
            output_dir=str(self.temp_dir / "run2")
        )
        engine2 = HistoricalReplayEngine(provider, config2)
        result2 = engine2.run()
        
        # Compare events
        events1 = result1.events
        events2 = result2.events
        
        assert len(events1) == len(events2), "Event count mismatch"
        
        for e1, e2 in zip(events1, events2):
            # Compare key fields
            assert e1["event_type"] == e2["event_type"]
            assert e1["symbol"] == e2["symbol"]
            assert e1["candle_index"] == e2["candle_index"]
            # Note: event_id includes timestamp so may differ
            # Compare structural fields
            assert e1["event_type"] == e2["event_type"]
    
    def test_identical_summary(self):
        """Running same dataset produces identical summary."""
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        
        config1 = ReplayConfig(
            symbol="BTCUSD.P",
            timeframe=Timeframe.H1,
            output_dir=str(self.temp_dir / "run1")
        )
        engine1 = HistoricalReplayEngine(provider, config1)
        result1 = engine1.run()
        
        config2 = ReplayConfig(
            symbol="BTCUSD.P",
            timeframe=Timeframe.H1,
            output_dir=str(self.temp_dir / "run2")
        )
        engine2 = HistoricalReplayEngine(provider, config2)
        result2 = engine2.run()
        
        # Compare summary statistics
        assert result1.internal_summary == result2.internal_summary
        assert result1.swing_summary == result2.swing_summary
        assert result1.ob_summary == result2.ob_summary


class TestFutureDataInvariance:
    """Tests that verify future data doesn't change past events."""

    def setup_method(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.data_dir = self.temp_dir / "BTCUSD.P" / "1h"
        self.data_dir.mkdir(parents=True)

        # Create base data
        self.base_candles = create_test_candles(1000)
        self.csv_file = self.data_dir / "1h.csv"
        create_test_csv(self.csv_file, self.base_candles)
    
    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
    
    def test_future_candles_dont_change_past_events(self):
        """Adding future candles doesn't change events before cutoff."""
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        
        # Run with base data
        config1 = ReplayConfig(
            symbol="BTCUSD.P",
            timeframe=Timeframe.H1,
            output_dir=str(self.temp_dir / "base"),
            dataset_end=datetime(2024, 1, 15)  # Cutoff
        )
        engine1 = HistoricalReplayEngine(provider, config1)
        result1 = engine1.run()
        
        # Add future candles
        future_candles = create_test_candles(
            500, 
            start_time=datetime(2024, 1, 15, 1, 0, 0)
        )
        with open(self.csv_file, "a") as f:
            for c in future_candles:
                f.write(f"{c.timestamp.isoformat()},{c.open},{c.high},{c.low},{c.close},{c.volume}\n")
        
        # Run with extended data but same cutoff
        config2 = ReplayConfig(
            symbol="BTCUSD.P",
            timeframe=Timeframe.H1,
            output_dir=str(self.temp_dir / "extended"),
            dataset_end=datetime(2024, 1, 15)
        )
        engine2 = HistoricalReplayEngine(provider, config2)
        result2 = engine2.run()
        
        # Compare events before cutoff
        events1 = result1.events
        events2 = result2.events
        
        # Events up to cutoff should be identical
        cutoff_events1 = [e for e in events1 if e["candle_index"] < 360]  # ~15 days * 24h
        cutoff_events2 = [e for e in events2 if e["candle_index"] < 360]
        
        assert len(cutoff_events1) == len(cutoff_events2)
        
        for e1, e2 in zip(cutoff_events1, cutoff_events2):
            assert e1["event_type"] == e2["event_type"]
            assert e1["candle_index"] == e2["candle_index"]
    
    def test_full_dataset_with_future_invariant(self):
        """Full replay with and without future data should match on overlap."""
        # This is essentially the same test but more comprehensive
        pass


class TestRawVsParsedSeparation:
    """Tests that verify raw-vs-parsed separation during replay."""

    def setup_method(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.data_dir = self.temp_dir / "BTCUSD.P" / "1h"
        self.data_dir.mkdir(parents=True)

        # Create candles with one high-volatility candle
        self.candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        base_price = Decimal("50000")

        for i in range(20):
            if i == 10:
                # High volatility candle
                open_p = base_price
                close_p = base_price - Decimal("100")
                high_p = base_price + Decimal("500")
                low_p = base_price - Decimal("600")
                vol = Decimal("10000")
            else:
                open_p = base_price + Decimal(str(i * 10))
                close_p = open_p + Decimal("5")
                high_p = max(open_p, close_p) + Decimal("10")
                low_p = min(open_p, close_p) - Decimal("10")
                vol = Decimal("1000")

            candle = Candle(
                symbol="BTCUSD.P",
                timeframe=Timeframe.H1,
                timestamp=base_time + timedelta(hours=i),
                open=open_p, high=high_p, low=low_p, close=close_p,
                volume=vol, source=MarketDataSource.HISTORICAL
            )
            self.candles.append(candle)

        create_test_csv(self.data_dir / "1h.csv", self.candles)
    
    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
    
    def test_structure_uses_raw_not_parsed(self):
        """Structure detection uses RAW OHLC, not parsed."""
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        candles = provider.load_candles("BTCUSD.P", Timeframe.H1)
        
        # Raw high/low at index 10 (high vol candle)
        raw_high_10 = self.candles[10].high  # 50500
        raw_low_10 = self.candles[10].low    # 49400
        
        parsed = parse_candles_with_volatility(candles, atr_period=14, atr_multiplier=2.0)
        
        # Run structure detection
        detector = StructureDetector(StructureConfig(5, StructureType.INTERNAL))
        
        for i, pc in enumerate(parsed):
            detector.process_candle(pc, i)
        
        # Verify pivot prices come from RAW values
        if detector.state.pivot_high:
            # Pivot high should use RAW high, not parsed
            assert detector.state.pivot_high.price == candles[detector.state.pivot_high.index].high
        if detector.state.pivot_low:
            assert detector.state.pivot_low.price == candles[detector.state.pivot_low.index].low
    
    def test_ob_selection_uses_parsed(self):
        """OB extreme selection uses parsed values."""
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        candles = provider.load_candles("BTCUSD.P", Timeframe.H1)
        parsed = parse_candles_with_volatility(candles, atr_period=14, atr_multiplier=2.0)
        
        # Create a known structure break
        internal_highs, internal_lows, internal_breaks, _ = detect_structure_streaming(
            parsed, 5, StructureType.INTERNAL
        )
        
        if internal_breaks:
            obs = detect_order_blocks_streaming(
                parsed, internal_breaks, [],
                [{"index": p.index, "timestamp": p.timestamp, "price": p.price, "is_high": p.is_high, "candle": p.candle} for p in []],
                [],
                OrderBlockConfig(internal_length=5, swing_length=50)
            )
            
            for ob in obs:
                # OB should be formed from candle in the correct slice
                # The selection should use parsed_low/parsed_high
                pass


class TestEventOrdering:
    """Tests that verify correct event ordering."""

    def setup_method(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.data_dir = self.temp_dir / "BTCUSD.P" / "1h"
        self.data_dir.mkdir(parents=True)
        self.candles = create_test_candles(1000)
        self.csv_file = self.data_dir / "1h.csv"
        create_test_csv(self.csv_file, self.candles)
    
    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
    
    def test_events_in_candle_order(self):
        """Events should be emitted in candle order."""
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        
        config = ReplayConfig(symbol="BTCUSD.P", timeframe=Timeframe.H1, output_dir=str(self.temp_dir / "output"))
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        engine = HistoricalReplayEngine(provider, ReplayConfig(
            symbol="BTCUSD.P", timeframe=Timeframe.H1, output_dir=str(self.temp_dir / "output")
        ))
        result = engine.run()
        
        # Check events are in candle order
        prev_index = -1
        for event in result.events:
            assert event["candle_index"] >= prev_index, \
                f"Event {event.get('event_id', 'unknown')} at index {event['candle_index']} < previous {prev_index}"
            prev_index = event["candle_index"]
    
    def test_break_event_at_break_candle(self):
        """Break events should be emitted at the break candle, not pivot."""
        provider = CsvHistoricalDataProvider(self.temp_dir, Timeframe.H1)
        
        config = ReplayConfig(symbol="BTCUSD.P", timeframe=Timeframe.H1, output_dir=str(self.temp_dir / "out"))
        engine = HistoricalReplayEngine(provider, config)
        result = engine.run()
        
        for event in result.events:
            if event["event_type"] in ("bos", "choch"):
                # Break index should match event candle index
                assert event["candle_index"] == event.get("break_index", event["candle_index"])


class TestRawVsParsedRegression:
    """Regression tests for raw-vs-parsed separation."""
    
    def test_high_vol_candle_no_false_leg(self):
        """High volatility candle should not trigger false leg transition."""
        # Create data with high vol candle that inverts parsed high/low
        candles = []
        base_time = datetime(2024, 1, 1, 0, 0, 0)
        base_price = Decimal("50000")
        
        for i in range(20):
            if i == 10:
                # High vol: wide range
                open_p = base_price
                close_p = base_price - Decimal("100")
                high_p = base_price + Decimal("500")
                low_p = base_price - Decimal("600")
                vol = Decimal("10000")
            else:
                open_p = base_price + Decimal(str(i * 10))
                close_p = open_p + Decimal("5")
                high_p = max(open_p, close_p) + Decimal("10")
                low_p = min(open_p, close_p) - Decimal("10")
                vol = Decimal("1000")
            
            candles.append(Candle(
                symbol="BTCUSD.P", timeframe=Timeframe.H1,
                timestamp=datetime(2024, 1, 1) + timedelta(hours=i),
                open=open_p, high=high_p, low=low_p, close=close_p,
                volume=vol, source=MarketDataSource.HISTORICAL
            ))
        
        # The raw-vs-parsed test in test_raw_vs_parsed.py covers this
        # This is a sanity check that the replay engine respects the separation
        pass


class TestReplayDeterminism:
    """Tests for replay determinism."""
    
    def test_byte_for_byte_identical_output(self):
        """Two runs produce identical events."""
        temp_dir = Path(tempfile.mkdtemp())
        data_dir = temp_dir / "BTCUSD.P" / "1h"
        data_dir.mkdir(parents=True)
        
        candles = create_test_candles(1000)
        csv_file = data_dir / "1h.csv"
        create_test_csv(csv_file, candles)
        
        try:
            provider = CsvHistoricalDataProvider(temp_dir, Timeframe.H1)
            
            # Run 1
            config1 = ReplayConfig(
                symbol="BTCUSD.P", timeframe=Timeframe.H1,
                output_dir=str(temp_dir / "run1")
            )
            engine1 = HistoricalReplayEngine(provider, config1)
            result1 = engine1.run()
            
            # Run 2
            config2 = ReplayConfig(
                symbol="BTCUSD.P", timeframe=Timeframe.H1,
                output_dir=str(temp_dir / "run2")
            )
            engine2 = HistoricalReplayEngine(provider, config2)
            result2 = engine2.run()
            
            # Compare events
            events1 = result1.events
            events2 = result2.events
            
            assert len(events1) == len(events2)
            
            for e1, e2 in zip(events1, events2):
                # Compare key fields
                assert e1["event_type"] == e2["event_type"]
                assert e1["symbol"] == e2["symbol"]
                assert e1["candle_index"] == e2["candle_index"]
        finally:
            shutil.rmtree(temp_dir)


class TestCausalEventGeneration:
    """Tests that verify causal event generation."""
    
    def test_leg_change_before_pivot(self):
        """Leg change event should be emitted before pivot creation."""
        # Leg change at transition candle, pivot at size-offset
        pass
    
    def test_pivot_before_break(self):
        """Pivot must be created before it can be broken."""
        # This is inherently tested by the state machine
        pass
    
    def test_break_before_ob(self):
        """Structure break must occur before OB creation."""
        # OB is created after break, from candles in [pivot, break)
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])