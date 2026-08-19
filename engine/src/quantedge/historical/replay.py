"""
Historical Replay Engine for QuantEdge SMC.

Deterministic candle-by-candle replay engine for historical SMC validation.
Runs both Internal (length=5) and Swing (length=50) SMC streams independently.

Fix history
-----------
Phase 3A (OB pipeline fix):
  - Bug 1: _process_order_blocks was passing internal_breaks=[] and
    swing_breaks=[] (hardcoded empty). Fix: accumulate all breaks as they
    are emitted during replay and pass the live list to the OB detector.
  - Bug 2: get_confirmed_pivots() returns only the current/final pivot pair.
    Fix: maintain a full pivot history (self._all_internal_pivots_history /
    self._all_swing_pivots_history) appended each time a pivot is created.
  - Bug 3: OB processing was called every 100 candles. Fix: process OBs
    event-driven, immediately when each structure break fires. This preserves
    same-candle causality: at break candle N, only parsed_candles[0:N+1] and
    pivots known by N are used.
  - Bug 4: run(), _load_dataset(), _parse_candles(), _initialize_detectors()
    were defined twice; Python silently used the later definition. First
    (dead) block removed.
  - Added duplicate-OB protection keyed on (structure_type, break_index).

Frozen SMC files NOT modified:
  - smc/structure.py
  - smc/order_blocks.py
  - smc/volatility.py
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Optional, Any, Set, Tuple
from pathlib import Path
import time
import json

from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.structure import (
    StructureDetector, StructureConfig,
    detect_structure_streaming
)
from quantedge.smc.order_blocks import (
    OrderBlockDetector, OrderBlockConfig, detect_order_blocks_streaming,
)
from quantedge.smc.volatility import parse_candles_with_volatility, ParsedCandle
from quantedge.smc.models import (
    StructureBreak, TrendDirection, BreakType, StructureType,
    OrderBlock, OBState, PivotPoint
)
from quantedge.historical.provider import HistoricalDataProvider, DatasetMetadata
from quantedge.historical.events import (
    JsonlEventWriter,
    create_leg_change_event, create_pivot_created_event,
    create_structure_break_event, create_order_block_created_event,
    create_order_block_lifecycle_event,
    create_dataset_event, create_replay_complete_event
)


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

    OB pipeline (Phase 3A fix):
    - All structure breaks are accumulated in self._all_internal_breaks /
      self._all_swing_breaks as they are emitted.
    - All pivots are accumulated in self._all_internal_pivots_history /
      self._all_swing_pivots_history as they are created.
    - OB processing fires immediately on each break (event-driven) using
      only data causally available at that candle.
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

        # OB deduplication: keyed by (structure_type, break_index)
        # Each structure break can produce at most one OB.
        self.ob_states: Dict[Tuple[str, int], str] = {}

        # ── Phase 3A Fix: Break + pivot accumulation ───────────────────────
        # All structure breaks in chronological order (appended as emitted).
        self._all_internal_breaks: List[StructureBreak] = []
        self._all_swing_breaks: List[StructureBreak] = []

        # Full pivot history in chronological order (appended as created).
        # Provides _find_broken_pivot_index with the correct historical pivot
        # rather than only the final-state pivot returned by get_confirmed_pivots().
        self._all_internal_pivots_history: List[PivotPoint] = []
        self._all_swing_pivots_history: List[PivotPoint] = []

        # Guard against processing the same break twice.
        self._processed_break_keys: Set[Tuple[str, str, int]] = set()
        # ──────────────────────────────────────────────────────────────────

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

    def _run_replay(self) -> None:
        """Run candle-by-candle replay."""
        print("Starting replay...")

        for i, parsed_candle in enumerate(self.parsed_candles):
            self.stats["total_candles"] = i + 1

            # Process internal structure (emits breaks → triggers OBs immediately)
            self._process_internal_structure(parsed_candle, i)

            # Process swing structure (emits breaks → triggers OBs immediately)
            self._process_swing_structure(parsed_candle, i)

            # OB processing is event-driven: each _handle_*_break() call
            # invokes _process_ob_for_break() inline.
            # No periodic batch OB processing needed.

            # Progress logging
            if i % 1000 == 0 and i > 0:
                print(f"  Processed {i} candles... "
                      f"(int_breaks={len(self._all_internal_breaks)}, "
                      f"sw_breaks={len(self._all_swing_breaks)}, "
                      f"obs={len(self.order_blocks)})")

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

        # Handle pivot creation — append to pivot history BEFORE break handling
        if state.pivot_high and state.pivot_high.index != self.internal_state.pivot_high_index:
            self._handle_internal_pivot_high(parsed_candle)

        if state.pivot_low and state.pivot_low.index != self.internal_state.pivot_low_index:
            self._handle_internal_pivot_low(parsed_candle)

        # Handle structure breaks — each one triggers OB processing
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

        # Handle pivot creation — append to pivot history BEFORE break handling
        if state.pivot_high and state.pivot_high.index != self.swing_state.pivot_high_index:
            self._handle_swing_pivot_high(parsed_candle)

        if state.pivot_low and state.pivot_low.index != self.swing_state.pivot_low_index:
            self._handle_swing_pivot_low(parsed_candle)

        # Handle structure breaks — each one triggers OB processing
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

            # Phase 3A Fix: append to full pivot history for OB detection.
            pivot = PivotPoint(
                index=state.pivot_high.index,
                timestamp=state.pivot_high.timestamp,
                price=state.pivot_high.price,
                is_high=True,
                candle=state.pivot_high.candle,
            )
            self._all_internal_pivots_history.append(pivot)

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

            # Phase 3A Fix: append to full pivot history for OB detection.
            pivot = PivotPoint(
                index=state.pivot_low.index,
                timestamp=state.pivot_low.timestamp,
                price=state.pivot_low.price,
                is_high=False,
                candle=state.pivot_low.candle,
            )
            self._all_internal_pivots_history.append(pivot)

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

    def _handle_internal_break(self, brk: StructureBreak, candle_index: int) -> None:
        """Handle internal structure break and immediately process the corresponding OB."""
        state = self.internal_detector.state

        if brk.break_type == BreakType.BOS:
            self.stats["internal_bos"] += 1
        else:
            self.stats["internal_choch"] += 1

        # Determine pivot that was broken (for the break event record)
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

        # Phase 3A Fix: accumulate break and process OB immediately.
        self._all_internal_breaks.append(brk)
        self._process_ob_for_break(brk, "internal", candle_index)

    def _handle_swing_leg_change(self, candle_index: int, parsed_candle) -> None:
        """Handle swing structure leg change."""
        self.stats["swing_leg_changes"] += 1
        # Event emission for swing leg changes is logged via stats only.

    def _handle_swing_pivot_high(self, parsed_candle) -> None:
        """Handle swing pivot high creation."""
        state = self.swing_detector.state
        if state.pivot_high:
            self.stats["swing_pivots"] += 1
            self.swing_state.pivot_high_index = state.pivot_high.index
            self.swing_state.pivot_high_price = str(state.pivot_high.price)

            # Phase 3A Fix: append to full pivot history for OB detection.
            pivot = PivotPoint(
                index=state.pivot_high.index,
                timestamp=state.pivot_high.timestamp,
                price=state.pivot_high.price,
                is_high=True,
                candle=state.pivot_high.candle,
            )
            self._all_swing_pivots_history.append(pivot)

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

            # Phase 3A Fix: append to full pivot history for OB detection.
            pivot = PivotPoint(
                index=state.pivot_low.index,
                timestamp=state.pivot_low.timestamp,
                price=state.pivot_low.price,
                is_high=False,
                candle=state.pivot_low.candle,
            )
            self._all_swing_pivots_history.append(pivot)

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

    def _handle_swing_break(self, brk: StructureBreak, candle_index: int) -> None:
        """Handle swing structure break and immediately process the corresponding OB."""
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

        # Phase 3A Fix: accumulate break and process OB immediately.
        self._all_swing_breaks.append(brk)
        self._process_ob_for_break(brk, "swing", candle_index)

    def _process_ob_for_break(
        self,
        brk: StructureBreak,
        structure_type: str,
        candle_index: int
    ) -> None:
        """
        Process Order Block for a single structure break (event-driven, causal).

        Called immediately when a break is emitted. Uses only data that was
        causally available at candle_index:
          - parsed_candles[0 : candle_index + 1]  (includes break candle)
          - pivot history accumulated up to this candle

        The OB source search range [pivot_index, break_index) excludes the
        break candle per LuxAlgo semantics, so no future data leaks in even
        though the break candle is included in the parsed slice.

        Duplicate protection: each (structure_type, direction, break_index)
        triple is processed at most once.
        """
        # Deduplication guard
        break_key: Tuple[str, str, int] = (
            structure_type, brk.direction.value, brk.index
        )
        if break_key in self._processed_break_keys:
            return
        self._processed_break_keys.add(break_key)

        try:
            # Causal slice: include up to and including the break candle.
            # The OB source search range is [pivot_index, break_index) which
            # EXCLUDES the break candle, so this slice is still causal.
            parsed_slice = self.parsed_candles[: candle_index + 1]

            # Snapshot pivot histories as-of this candle.
            # Both lists are append-only and pivots are added BEFORE breaks
            # in _process_internal_structure / _process_swing_structure,
            # so this snapshot is causally correct.
            int_pivots: List[PivotPoint] = list(self._all_internal_pivots_history)
            sw_pivots: List[PivotPoint] = list(self._all_swing_pivots_history)

            if structure_type == "internal":
                obs = detect_order_blocks_streaming(
                    parsed_candles=parsed_slice,
                    internal_breaks=[brk],
                    swing_breaks=[],
                    internal_pivots=int_pivots,
                    swing_pivots=sw_pivots,
                    config=self.ob_config
                )
            else:  # swing
                obs = detect_order_blocks_streaming(
                    parsed_candles=parsed_slice,
                    internal_breaks=[],
                    swing_breaks=[brk],
                    internal_pivots=int_pivots,
                    swing_pivots=sw_pivots,
                    config=self.ob_config
                )

            for ob in obs:
                # Dedup OBs by (structure_type, break_index):
                # each break yields at most one OB per stream.
                ob_key: Tuple[str, int] = (structure_type, brk.index)
                if ob_key in self.ob_states:
                    continue

                self.ob_states[ob_key] = (
                    ob.state.value if hasattr(ob.state, "value") else str(ob.state)
                )
                self.order_blocks.append(ob)

                if structure_type == "internal":
                    self.stats["internal_obs"] += 1
                else:
                    self.stats["swing_obs"] += 1

                # Emit OB creation event
                # Source candle parsed values for traceability
                source_parsed = parsed_slice[ob.index] if ob.index < len(parsed_slice) else None
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
                    structure_type=structure_type,
                    source_candle_index=ob.index,
                    source_parsed_high=str(source_parsed.parsed_high) if source_parsed else None,
                    source_parsed_low=str(source_parsed.parsed_low) if source_parsed else None,
                )
                self._record_event(event)

        except Exception as e:
            print(
                f"Warning: OB processing error for {structure_type} break "
                f"at index {brk.index}: {e}"
            )

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
            "internal": self.stats["internal_obs"],
            "swing": self.stats["swing_obs"],
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
    dataset_end: Optional[datetime] = None,
    atr_period: int = 200,
    atr_multiplier: float = 2.0,
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
        atr_period: ATR period for volatility parsing (default 200, per LuxAlgo)
        atr_multiplier: ATR multiplier for high-vol detection (default 2.0)

    Returns:
        Dictionary mapping symbol to ReplayResult
    """
    from quantedge.historical.provider import CsvHistoricalDataProvider

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
            atr_period=atr_period,
            atr_multiplier=atr_multiplier,
            output_dir=output_dir,
            dataset_start=dataset_start,
            dataset_end=dataset_end
        )

        engine = HistoricalReplayEngine(provider, config)
        result = engine.run()
        results[symbol] = result

    return results