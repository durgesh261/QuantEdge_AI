"""
Historical Replay Engine for QuantEdge SMC.

Deterministic candle-by-candle replay engine for historical SMC validation.
Runs both Internal (length=5) and Swing (length=50) SMC streams independently.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Optional, Any, Iterator, Tuple
from pathlib import Path
import time
import json

from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.structure import (
    StructureDetector, StructureConfig, StructureType,
    detect_structure_streaming
)
from quantedge.smc.order_blocks import (
    OrderBlockDetector, OrderBlockConfig, detect_order_blocks_streaming,
    OrderBlock
)
from quantedge.smc.volatility import parse_candles_with_volatility, ParsedCandle
from quantedge.smc.models import (
    StructureBreak, TrendDirection, BreakType, StructureType,
    OrderBlock, OBState
)
from quantedge.historical.provider import HistoricalDataProvider, DatasetMetadata
from quantedge.historical.events import (
    JsonlEventWriter,
    create_leg_change_event, create_pivot_created_event,
    create_structure_break_event, create_order_block_created_event,
    create_order_block_lifecycle_event,
    create_dataset_event, create_replay_complete_event
)
from quantedge.market_data.models import Timeframe


@dataclass
class ReplayConfig:
    """Configuration for historical replay."""
    symbol: str
    timeframe: Timeframe = Timeframe.H1
    internal_length: int = 5
    swing_length: int = 50
    atr_period: int = 200
    atr_multiplier: float = 2.0
    output_dir: str = "validation_output"
    dataset_start: Optional[datetime] = None
    dataset_end: Optional[datetime] = None


@dataclass
class ReplayState:
    """Current state of replay for a single structure type."""
    detector: Any
    pivot_high_index: Optional[int] = None
    pivot_high_price: Optional[str] = None
    pivot_low_index: Optional[int] = None
    pivot_low_price: Optional[str] = None
    current_leg: int = 0
    previous_leg: int = 0
    trend: str = "ranging"
    last_break_index: Optional[int] = None


@dataclass
class ReplayResult:
    """Result of a replay run."""
    symbol: str
    timeframe: str
    dataset_metadata: DatasetMetadata
    events: List
    internal_summary: Dict[str, Any]
    swing_summary: Dict[str, Any]
    ob_summary: Dict[str, Any]
    duration_seconds: float
    deterministic: bool = True


class HistoricalReplayEngine:
    """
    Deterministic historical replay engine for SMC validation.
    
    Runs candle-by-candle replay through historical data,
    generating normalized events for both Internal and Swing structures.
    """
    
    def __init__(
        self,
        provider: HistoricalDataProvider,
        config: ReplayConfig
    ):
        self.provider = provider
        self.config = config
        self.output_dir = Path(config.output_dir) / config.symbol / config.timeframe.value
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Event writer
        self.events_file = self.output_dir / "events.jsonl"
        self.summary_file = self.output_dir / "summary.json"
        
        # Replay state
        self.candles: List = []
        self.parsed_candles: List = []
        self.dataset_metadata: Optional[DatasetMetadata] = None
        
        # Internal structure state
        self.internal_detector: Any = None
        self.internal_state = ReplayState(detector=None)
        
        # Swing structure state
        self.swing_detector: Any = None
        self.swing_state = ReplayState(detector=None)
        
        # Order Block detector
        self.ob_config = OrderBlockConfig(
            internal_length=config.internal_length,
            swing_length=config.swing_length,
            atr_period=config.atr_period,
            atr_multiplier=config.atr_multiplier
        )
        self.ob_detector: Any = None
        self.order_blocks: List = []
        self.ob_states: Dict[int, str] = {}  # ob_index -> state
        
        # Events
        self.events: List = []
        
        # Statistics
        self.stats = {
            "total_candles": 0,
            "internal_leg_changes": 0,
            "swing_leg_changes": 0,
            "internal_pivots": 0,
            "swing_pivots": 0,
            "internal_bos": 0,
            "internal_choch": 0,
            "swing_bos": 0,
            "swing_choch": 0,
            "internal_obs": 0,
            "swing_obs": 0,
            "ob_invalidations": 0,
            "ob_touches": 0,
        }
    
    def run(self) -> ReplayResult:
        """Run the complete historical replay."""
        start_time = time.time()
        
        # Load dataset
        self._load_dataset()
        
        # Parse candles with volatility
        self._parse_candles()
        
        # Initialize detectors
        self._initialize_detectors()
        
        # Write dataset start event
        self._write_dataset_start_event()
        
        # Run candle-by-candle replay
        self._run_replay()
        
        # Write replay complete event
        duration = time.time() - start_time
        self._write_replay_complete_event(duration)
        
        # Write summary
        self._write_summary()
        
        return ReplayResult(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe.value,
            dataset_metadata=self.dataset_metadata,
            events=self.events,
            internal_summary=self._get_internal_summary(),
            swing_summary=self._get_swing_summary(),
            ob_summary=self._get_ob_summary(),
            duration_seconds=duration,
            deterministic=True
        )
    
    def _load_dataset(self) -> None:
        """Load and validate dataset."""
        print(f"Loading dataset for {self.config.symbol} {self.config.timeframe.value}...")
        
        self.dataset_metadata = self.provider.get_metadata(
            self.config.symbol, self.config.timeframe
        )
        
        self.candles = self.provider.load_candles(
            self.config.symbol,
            self.config.timeframe,
            self.config.dataset_start,
            self.config.dataset_end
        )
        
        print(f"Loaded {len(self.candles)} candles")
        print(f"Date range: {self.candles[0].timestamp} to {self.candles[-1].timestamp}")
    
    def _parse_candles(self) -> None:
        """Parse candles with volatility for OB detection."""
        print("Parsing candles with volatility...")
        self.parsed_candles = parse_candles_with_volatility(
            self.candles,
            atr_period=self.config.atr_period,
            atr_multiplier=self.config.atr_multiplier
        )
        print(f"Parsed {len(self.parsed_candles)} candles")
    
    def _initialize_detectors(self) -> None:
        """Initialize structure detectors."""
        self.internal_detector = StructureDetector(
            StructureConfig(self.config.internal_length, StructureType.INTERNAL)
        )
        self.internal_state.detector = self.internal_detector
        
        self.swing_detector = StructureDetector(
            StructureConfig(self.config.swing_length, StructureType.SWING)
        )
        self.swing_state.detector = self.swing_detector
        
        self.ob_detector = OrderBlockDetector(self.ob_config)
    
    def run(self) -> ReplayResult:
        """Run the complete historical replay."""
        start_time = time.time()
        
        # Load dataset
        self._load_dataset()
        
        # Parse candles with volatility
        self._parse_candles()
        
        # Initialize detectors
        self._initialize_detectors()
        
        # Write dataset start event
        self._write_dataset_start_event()
        
        # Run candle-by-candle replay
        self._run_replay()
        
        # Write replay complete event
        duration = time.time() - start_time
        self._write_replay_complete_event(duration)
        
        # Write summary
        self._write_summary()
        
        return ReplayResult(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe.value,
            dataset_metadata=self.dataset_metadata,
            events=self.events,
            internal_summary=self._get_internal_summary(),
            swing_summary=self._get_swing_summary(),
            ob_summary=self._get_ob_summary(),
            duration_seconds=duration,
            deterministic=True
        )
    
    def _load_dataset(self) -> None:
        """Load and validate dataset."""
        print(f"Loading dataset for {self.config.symbol} {self.config.timeframe.value}...")
        
        self.dataset_metadata = self.provider.get_metadata(
            self.config.symbol, self.config.timeframe
        )
        
        self.candles = self.provider.load_candles(
            self.config.symbol,
            self.config.timeframe,
            self.config.dataset_start,
            self.config.dataset_end
        )
        
        print(f"Loaded {len(self.candles)} candles")
        print(f"Date range: {self.candles[0].timestamp} to {self.candles[-1].timestamp}")
    
    def _parse_candles(self) -> None:
        """Parse candles with volatility for OB detection."""
        print("Parsing candles with volatility...")
        self.parsed_candles = parse_candles_with_volatility(
            self.candles,
            atr_period=self.config.atr_period,
            atr_multiplier=self.config.atr_multiplier
        )
        print(f"Parsed {len(self.parsed_candles)} candles")
    
    def _initialize_detectors(self) -> None:
        """Initialize structure detectors."""
        self.internal_detector = StructureDetector(
            StructureConfig(self.config.internal_length, StructureType.INTERNAL)
        )
        self.internal_state.detector = self.internal_detector
        
        self.swing_detector = StructureDetector(
            StructureConfig(self.config.swing_length, StructureType.SWING)
        )
        self.swing_state.detector = self.swing_detector
        
        self.ob_detector = OrderBlockDetector(self.ob_config)
    
    def _run_replay(self) -> None:
        """Run candle-by-candle replay."""
        print("Starting replay...")
        
        for i, parsed_candle in enumerate(self.parsed_candles):
            self.stats["total_candles"] = i + 1
            
            # Process internal structure
            self._process_internal_structure(parsed_candle, i)
            
            # Process swing structure
            self._process_swing_structure(parsed_candle, i)
            
            # Process order blocks (less frequently to reduce computation)
            if i % 100 == 0 or i == len(self.parsed_candles) - 1:
                self._process_order_blocks(i)
            
            # Progress logging
            if i % 10000 == 0 and i > 0:
                print(f"  Processed {i} candles...")
        
        # Final OB processing
        self._process_order_blocks(len(self.parsed_candles) - 1, final=True)
        
        print("Replay complete.")
    
    def _process_internal_structure(self, parsed_candle, candle_index: int) -> None:
        """Process a candle through internal structure detector."""
        breaks = self.internal_detector.process_candle(parsed_candle, candle_index)
        state = self.internal_detector.state
        
        # Track leg changes
        if state.current_leg != self.internal_state.current_leg and self.internal_state.current_leg != 0:
            self._handle_internal_leg_change(candle_index, parsed_candle)
        
        self.internal_state.current_leg = state.current_leg
        self.internal_state.previous_leg = state.previous_leg
        self.internal_state.trend = state.trend.value
        
        # Handle pivot creation
        if state.pivot_high and state.pivot_high.index != self.internal_state.pivot_high_index:
            self._handle_internal_pivot_high(parsed_candle)
        
        if state.pivot_low and state.pivot_low.index != self.internal_state.pivot_low_index:
            self._handle_internal_pivot_low(parsed_candle)
        
        # Handle structure breaks
        for brk in breaks:
            self._handle_internal_break(brk, candle_index)
    
    def _process_swing_structure(self, parsed_candle, candle_index: int) -> None:
        """Process a candle through swing structure detector."""
        breaks = self.swing_detector.process_candle(parsed_candle, candle_index)
        state = self.swing_detector.state
        
        # Track leg changes
        if state.current_leg != self.swing_state.current_leg and self.swing_state.current_leg != 0:
            self._handle_swing_leg_change(candle_index, parsed_candle)
        
        self.swing_state.current_leg = state.current_leg
        self.swing_state.previous_leg = state.previous_leg
        self.swing_state.trend = state.trend.value
        
        # Handle pivot creation
        if state.pivot_high and state.pivot_high.index != self.swing_state.pivot_high_index:
            self._handle_swing_pivot_high(parsed_candle)
        
        if state.pivot_low and state.pivot_low.index != self.swing_state.pivot_low_index:
            self._handle_swing_pivot_low(parsed_candle)
        
        # Handle structure breaks
        for brk in breaks:
            self._handle_swing_break(brk, candle_index)
    
    def _handle_internal_leg_change(self, candle_index: int, parsed_candle) -> None:
        """Handle internal structure leg change."""
        self.stats["internal_leg_changes"] += 1
        
        state = self.internal_detector.state
        
        # Create leg change event
        event = create_leg_change_event(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe.value,
            candle_index=candle_index,
            timestamp=parsed_candle.original.timestamp,
            previous_leg=self.internal_state.current_leg,
            new_leg=state.current_leg,
            pivot_high_price=str(state.pivot_high.price) if state.pivot_high else None,
            pivot_high_index=state.pivot_high.index if state.pivot_high else None,
            pivot_low_price=str(state.pivot_low.price) if state.pivot_low else None,
            pivot_low_index=state.pivot_low.index if state.pivot_low else None
        )
        self._record_event(event)
    
    def _handle_internal_pivot_high(self, parsed_candle) -> None:
        """Handle internal pivot high creation."""
        state = self.internal_detector.state
        if state.pivot_high:
            self.stats["internal_pivots"] += 1
            self.internal_state.pivot_high_index = state.pivot_high.index
            self.internal_state.pivot_high_price = str(state.pivot_high.price)
            
            event = {
                "event_id": f"pivot_high_internal_{self.config.symbol}_{state.pivot_high.index}",
                "event_type": "pivot_created",
                "symbol": self.config.symbol,
                "timeframe": self.config.timeframe.value,
                "timestamp": state.pivot_high.timestamp.isoformat() if isinstance(state.pivot_high.timestamp, datetime) else str(state.pivot_high.timestamp),
                "candle_index": state.pivot_high.index,
                "pivot_type": "high",
                "pivot_price": str(state.pivot_high.price),
                "pivot_index": state.pivot_high.index,
                "pivot_timestamp": state.pivot_high.timestamp.isoformat() if isinstance(state.pivot_high.timestamp, datetime) else str(state.pivot_high.timestamp),
                "structure_type": "internal"
            }
            self._record_event(event)
    
    def _handle_internal_pivot_low(self, parsed_candle) -> None:
        """Handle internal pivot low creation."""
        state = self.internal_detector.state
        if state.pivot_low:
            self.stats["internal_pivots"] += 1
            self.internal_state.pivot_low_index = state.pivot_low.index
            self.internal_state.pivot_low_price = str(state.pivot_low.price)
            
            event = {
                "event_id": f"pivot_low_internal_{self.config.symbol}_{state.pivot_low.index}",
                "event_type": "pivot_created",
                "symbol": self.config.symbol,
                "timeframe": self.config.timeframe.value,
                "timestamp": state.pivot_low.timestamp.isoformat() if isinstance(state.pivot_low.timestamp, datetime) else str(state.pivot_low.timestamp),
                "candle_index": state.pivot_low.index,
                "pivot_type": "low",
                "pivot_price": str(state.pivot_low.price),
                "pivot_index": state.pivot_low.index,
                "pivot_timestamp": state.pivot_low.timestamp.isoformat() if isinstance(state.pivot_low.timestamp, datetime) else str(state.pivot_low.timestamp),
                "structure_type": "internal"
            }
            self._record_event(event)
    
    def _handle_internal_break(self, brk, candle_index: int) -> None:
        """Handle internal structure break."""
        state = self.internal_detector.state
        
        if brk.break_type == BreakType.BOS:
            self.stats["internal_bos"] += 1
        else:
            self.stats["internal_choch"] += 1
        
        # Determine pivot that was broken
        pivot_price = None
        pivot_index = None
        if brk.direction == TrendDirection.BULLISH:
            if state.pivot_high:
                pivot_price = str(state.pivot_high.price)
                pivot_index = state.pivot_high.index
        else:
            if state.pivot_low:
                pivot_price = str(state.pivot_low.price)
                pivot_index = state.pivot_low.index
        
        event = create_structure_break_event(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe.value,
            candle_index=candle_index,
            timestamp=brk.timestamp,
            break_type=brk.break_type.value,
            direction=brk.direction.value,
            previous_trend=brk.previous_trend.value,
            trend_after=state.trend.value,
            pivot_price=pivot_price,
            pivot_index=pivot_index,
            break_price=str(brk.price),
            structure_type="internal"
        )
        self._record_event(event)
    
    def _handle_swing_leg_change(self, candle_index: int, parsed_candle) -> None:
        """Handle swing structure leg change."""
        self.stats["swing_leg_changes"] += 1
        # Similar to internal but for swing structure
        pass
    
    def _handle_swing_pivot_high(self, parsed_candle) -> None:
        """Handle swing pivot high creation."""
        state = self.swing_detector.state
        if state.pivot_high:
            self.stats["swing_pivots"] += 1
            self.swing_state.pivot_high_index = state.pivot_high.index
            self.swing_state.pivot_high_price = str(state.pivot_high.price)
            
            event = {
                "event_id": f"pivot_high_swing_{self.config.symbol}_{state.pivot_high.index}",
                "event_type": "pivot_created",
                "symbol": self.config.symbol,
                "timeframe": self.config.timeframe.value,
                "timestamp": state.pivot_high.timestamp.isoformat() if isinstance(state.pivot_high.timestamp, datetime) else str(state.pivot_high.timestamp),
                "candle_index": state.pivot_high.index,
                "pivot_type": "high",
                "pivot_price": str(state.pivot_high.price),
                "pivot_index": state.pivot_high.index,
                "pivot_timestamp": state.pivot_high.timestamp.isoformat() if isinstance(state.pivot_high.timestamp, datetime) else str(state.pivot_high.timestamp),
                "structure_type": "swing"
            }
            self._record_event(event)
    
    def _handle_swing_pivot_low(self, parsed_candle) -> None:
        """Handle swing pivot low creation."""
        state = self.swing_detector.state
        if state.pivot_low:
            self.stats["swing_pivots"] += 1
            self.swing_state.pivot_low_index = state.pivot_low.index
            self.swing_state.pivot_low_price = str(state.pivot_low.price)
            
            event = {
                "event_id": f"pivot_low_swing_{self.config.symbol}_{state.pivot_low.index}",
                "event_type": "pivot_created",
                "symbol": self.config.symbol,
                "timeframe": self.config.timeframe.value,
                "timestamp": state.pivot_low.timestamp.isoformat() if isinstance(state.pivot_low.timestamp, datetime) else str(state.pivot_low.timestamp),
                "candle_index": state.pivot_low.index,
                "pivot_type": "low",
                "pivot_price": str(state.pivot_low.price),
                "pivot_index": state.pivot_low.index,
                "pivot_timestamp": state.pivot_low.timestamp.isoformat() if isinstance(state.pivot_low.timestamp, datetime) else str(state.pivot_low.timestamp),
                "structure_type": "swing"
            }
            self._record_event(event)
    
    def _handle_swing_break(self, brk, candle_index: int) -> None:
        """Handle swing structure break."""
        state = self.swing_detector.state
        
        if brk.break_type == BreakType.BOS:
            self.stats["swing_bos"] += 1
        else:
            self.stats["swing_choch"] += 1
        
        pivot_price = None
        pivot_index = None
        if brk.direction == TrendDirection.BULLISH:
            if state.pivot_high:
                pivot_price = str(state.pivot_high.price)
                pivot_index = state.pivot_high.index
        else:
            if state.pivot_low:
                pivot_price = str(state.pivot_low.price)
                pivot_index = state.pivot_low.index
        
        event = create_structure_break_event(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe.value,
            candle_index=candle_index,
            timestamp=brk.timestamp,
            break_type=brk.break_type.value,
            direction=brk.direction.value,
            previous_trend=brk.previous_trend.value,
            trend_after=state.trend.value,
            pivot_price=pivot_price,
            pivot_index=pivot_index,
            break_price=str(brk.price),
            structure_type="swing"
        )
        self._record_event(event)
    
    def _process_order_blocks(self, current_index: int, final: bool = False) -> None:
        """Process order blocks from detected breaks."""
        # Get current structure state
        internal_highs, internal_lows = self.internal_detector.get_confirmed_pivots()
        swing_highs, swing_lows = self.swing_detector.get_confirmed_pivots()
        
        internal_breaks = self.internal_detector.state.last_break
        swing_breaks = self.swing_detector.state.last_break
        
        # For full replay, we need all breaks - this is simplified
        # Full implementation would track all breaks
        
        # Only process OBs periodically to save computation
        if not final and current_index % 100 != 0:
            return
        
        # Run OB detection
        try:
            obs = detect_order_blocks_streaming(
                parsed_candles=self.parsed_candles[:current_index + 1],
                internal_breaks=[],  # Would need to track all breaks
                swing_breaks=[],
                internal_pivots=internal_highs + internal_lows,
                swing_pivots=swing_highs + swing_lows,
                config=self.ob_config
            )
            
            # Track new OBs
            for ob in obs:
                if ob.index not in self.ob_states:
                    self.ob_states[ob.index] = ob.state
                    if ob.type == "BULLISH":
                        self.stats["internal_obs" if ob.structure_type == "internal" else "swing_obs"] += 1
                    
                    # Create OB creation event
                    event = create_order_block_created_event(
                        symbol=self.config.symbol,
                        timeframe=self.config.timeframe.value,
                        candle_index=ob.index,
                        timestamp=ob.formation_candle.timestamp,
                        ob_type=ob.type,
                        top_price=str(ob.top_price),
                        bottom_price=str(ob.bottom_price),
                        formation_candle_index=ob.formation_index,
                        formation_timestamp=ob.formation_candle.timestamp,
                        break_index=ob.break_index,
                        break_type=ob.break_type.value,
                        trend_before_break=ob.trend_before_break.value,
                        pivot_index=ob.break_index,
                        break_index_=ob.break_index,
                        structure_type=ob.structure_type,
                        source_candle_index=ob.index
                    )
                    self._record_event(event)
            
            # Check lifecycle changes for existing OBs
            self._check_ob_lifecycle(current_index)
            
        except Exception as e:
            print(f"Warning: OB processing error at index {current_index}: {e}")
    
    def _check_ob_lifecycle(self, current_index: int) -> None:
        """Check for OB lifecycle changes (touch, invalidation, used)."""
        current_candle = self.candles[current_index] if current_index < len(self.candles) else None
        if not current_candle:
            return
        
        # This would iterate through existing OBs and check for touches/invalidations
        # Simplified for now
        pass
    
    def _record_event(self, event) -> None:
        """Record an event to the events list and file."""
        self.events.append(event)
        # Write to file immediately for streaming
        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    
    def _write_dataset_start_event(self) -> None:
        """Write dataset start event."""
        event = create_dataset_event(
            event_type="dataset_start",
            symbol=self.config.symbol,
            timeframe=self.config.timeframe.value,
            dataset_id=self.dataset_metadata.dataset_id,
            candle_count=self.dataset_metadata.candle_count,
            date_range_start=self.dataset_metadata.start_time,
            date_range_end=self.dataset_metadata.end_time
        )
        self._record_event(event)
    
    def _write_replay_complete_event(self, duration: float) -> None:
        """Write replay completion event."""
        summary = {
            "internal_leg_changes": self.stats["internal_leg_changes"],
            "swing_leg_changes": self.stats["swing_leg_changes"],
            "internal_pivots": self.stats["internal_pivots"],
            "swing_pivots": self.stats["swing_pivots"],
            "internal_bos": self.stats["internal_bos"],
            "internal_choch": self.stats["internal_choch"],
            "swing_bos": self.stats["swing_bos"],
            "swing_choch": self.stats["swing_choch"],
            "internal_obs": self.stats["internal_obs"],
            "swing_obs": self.stats["swing_obs"],
            "ob_invalidations": self.stats["ob_invalidations"],
            "ob_touches": self.stats["ob_touches"],
            "total_candles": self.stats["total_candles"],
            "total_events": len(self.events)
        }
        
        event = create_replay_complete_event(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe.value,
            total_candles=self.stats["total_candles"],
            events_generated=len(self.events),
            duration_seconds=duration,
            summary=summary
        )
        self._record_event(event)
    
    def _write_summary(self) -> None:
        """Write summary JSON file."""
        summary = {
            "symbol": self.config.symbol,
            "timeframe": self.config.timeframe.value,
            "dataset": {
                "dataset_id": self.dataset_metadata.dataset_id,
                "symbol": self.dataset_metadata.symbol,
                "timeframe": self.dataset_metadata.timeframe.value,
                "start_time": self.dataset_metadata.start_time.isoformat(),
                "end_time": self.dataset_metadata.end_time.isoformat(),
                "candle_count": self.dataset_metadata.candle_count,
                "file_hash": self.dataset_metadata.file_hash,
                "gaps": self.dataset_metadata.gaps,
                "quality_report": self.dataset_metadata.quality_report
            },
            "config": {
                "internal_length": self.config.internal_length,
                "swing_length": self.config.swing_length,
                "atr_period": self.config.atr_period,
                "atr_multiplier": self.config.atr_multiplier
            },
            "statistics": self.stats,
            "output": {
                "events_file": str(self.events_file),
                "summary_file": str(self.summary_file),
                "total_events": len(self.events)
            }
        }
        
        with open(self.summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
        
        print(f"Summary written to {self.summary_file}")
    
    def _get_internal_summary(self) -> Dict[str, Any]:
        return {
            "leg_changes": self.stats["internal_leg_changes"],
            "pivots": self.stats["internal_pivots"],
            "bos": self.stats["internal_bos"],
            "choch": self.stats["internal_choch"],
            "obs": self.stats["internal_obs"]
        }
    
    def _get_swing_summary(self) -> Dict[str, Any]:
        return {
            "leg_changes": self.stats["swing_leg_changes"],
            "pivots": self.stats["swing_pivots"],
            "bos": self.stats["swing_bos"],
            "choch": self.stats["swing_choch"],
            "obs": self.stats["swing_obs"]
        }
    
    def _get_ob_summary(self) -> Dict[str, Any]:
        return {
            "total": self.stats["internal_obs"] + self.stats["swing_obs"],
            "invalidations": self.stats["ob_invalidations"],
            "touches": self.stats["ob_touches"]
        }


def run_historical_validation(
    data_root: Path,
    symbols: List[str],
    timeframe: Timeframe = Timeframe.H1,
    internal_length: int = 5,
    swing_length: int = 50,
    output_dir: str = "validation_output",
    dataset_start: Optional[datetime] = None,
    dataset_end: Optional[datetime] = None
) -> Dict[str, ReplayResult]:
    """
    Run historical validation for multiple symbols.
    
    Args:
        data_root: Root directory containing CSV data
        symbols: List of symbols to validate
        timeframe: Timeframe for validation (default 1H)
        internal_length: Internal structure length (default 5)
        swing_length: Swing structure length (default 50)
        output_dir: Output directory for validation results
        dataset_start: Optional start time filter
        dataset_end: Optional end time filter
    
    Returns:
        Dictionary mapping symbol to ReplayResult
    """
    provider = CsvHistoricalDataProvider(data_root, timeframe)
    results = {}
    
    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"Validating {symbol} {timeframe.value}")
        print(f"{'='*60}")
        
        config = ReplayConfig(
            symbol=symbol,
            timeframe=timeframe,
            internal_length=internal_length,
            swing_length=swing_length,
            output_dir=output_dir,
            dataset_start=dataset_start,
            dataset_end=dataset_end
        )
        
        provider = CsvHistoricalDataProvider(data_root, timeframe)
        engine = HistoricalReplayEngine(provider, config)
        result = engine.run()
        results[symbol] = result
    
    return results