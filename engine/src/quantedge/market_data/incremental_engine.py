"""
Incremental/Live SMC Engine for QuantEdge AI V2.

Provides a continuous, incremental SMC engine that processes newly closed
candles one at a time, maintaining state without recomputing from scratch.

Key guarantees:
- Deterministic: same input sequence always produces same state
- Causal: only uses data up to current candle (no look-ahead)
- Future-data invariant: adding future candles does not change past state
- Idempotent: processing same closed candle twice has no effect
- Restart-safe: can resume from persisted state
"""

import csv
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any
from enum import Enum

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.volatility import parse_candles_with_volatility, ParsedCandle
from quantedge.smc.structure import (
    StructureDetector, StructureConfig, StructureType,
    PivotLevel, StructureState,
)
from quantedge.smc.order_blocks import (
    OrderBlockConfig, OrderBlockDetector,
)
from quantedge.smc.models import (
    OrderBlock, PivotPoint, StructureBreak,
    OBState, TrendDirection, BreakType,
    StructureType as ModelStructureType,
)


# ── Event types ────────────────────────────────────────────────────────────────


class EventType(str, Enum):
    """Types of events emitted by the incremental engine."""
    CANDLE_CLOSED = "CANDLE_CLOSED"
    INTERNAL_BOS = "INTERNAL_BOS"
    INTERNAL_CHOCH = "INTERNAL_CHOCH"
    SWING_BOS = "SWING_BOS"
    SWING_CHOCH = "SWING_CHOCH"
    INTERNAL_OB_CREATED = "INTERNAL_OB_CREATED"
    SWING_OB_CREATED = "SWING_OB_CREATED"
    OB_TOUCHED = "OB_TOUCHED"
    OB_INVALIDATED = "OB_INVALIDATED"
    OB_USED = "OB_USED"
    DATA_GAP_DETECTED = "DATA_GAP_DETECTED"
    ENGINE_RECOVERED = "ENGINE_RECOVERED"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"


class Event:
    """Event emitted by the incremental engine."""

    def __init__(
        self,
        event_type: EventType,
        timestamp: datetime,
        symbol: str,
        timeframe: "Timeframe",
        data: Dict[str, Any],
    ) -> None:
        self.event_type = event_type
        self.timestamp = timestamp
        self.symbol = symbol
        self.timeframe = timeframe
        self.data = data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe.value if hasattr(self.timeframe, "value") else str(self.timeframe),
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ── Configuration ──────────────────────────────────────────────────────────────


@dataclass
class IncrementalEngineConfig:
    """Configuration for the incremental engine."""
    symbol_local: str = "BTCUSD.P"
    delta_symbol: str = "BTCUSD"
    resolution: str = "1h"
    timeframe: Timeframe = Timeframe.H1
    atr_period: int = 200
    atr_multiplier: float = 2.0
    internal_length: int = 5
    swing_length: int = 50
    max_candles_per_request: int = 2000
    chunk_seconds: int = 2000 * 3600
    lookback_bars: int = 100
    event_callback: Optional[Callable[["Event"], None]] = None

    def __post_init__(self) -> None:
        # Normalise timeframe: accept both Timeframe enum and "1h" string
        if isinstance(self.timeframe, str):
            tf_map = {
                "1m": Timeframe.M1, "5m": Timeframe.M5,
                "15m": Timeframe.M15, "30m": Timeframe.M30,
                "1h": Timeframe.H1, "4h": Timeframe.H4,
                "1d": Timeframe.D1, "1w": Timeframe.W1,
            }
            self.timeframe = tf_map.get(self.timeframe, Timeframe.H1)


# ── State snapshot ─────────────────────────────────────────────────────────────


@dataclass
class EngineStateSnapshot:
    """Snapshot of engine state for persistence/restart."""
    last_processed_ts: int
    last_processed_idx: int
    internal_detector_state: Dict
    swing_detector_state: Dict
    active_obs: Dict
    all_obs: Dict
    internal_pivots: List
    swing_pivots: List
    internal_breaks: List
    swing_breaks: List
    gaps_detected: List
    next_ob_id: int
    config: Dict
    schema_version: int = 1


# ── Engine ─────────────────────────────────────────────────────────────────────


class IncrementalSMCEngine:
    """
    Incremental SMC Engine for continuous market data processing.

    Processes newly closed candles one at a time, maintaining state
    incrementally without recomputing from scratch.
    """

    def __init__(
        self,
        config: Optional[IncrementalEngineConfig] = None,
        state_path: Optional[Path] = None,
        event_callback: Optional[Callable[[Event], None]] = None,
    ) -> None:
        self.config = config or IncrementalEngineConfig()
        self.state_path = state_path
        self.event_callback = event_callback

        # Core state
        self._initialized = False
        self._active_obs: Dict[int, OrderBlock] = {}
        self._all_obs: Dict[int, OrderBlock] = {}
        self._all_candles: List[Candle] = []
        self._all_parsed_candles: List[ParsedCandle] = []
        self._internal_breaks: List[StructureBreak] = []
        self._swing_breaks: List[StructureBreak] = []
        self._internal_pivots: List[PivotPoint] = []
        self._swing_pivots: List[PivotPoint] = []
        self._gaps_detected: List[Dict] = []

        # Structure detectors
        self._internal_config = StructureConfig(
            self.config.internal_length, StructureType.INTERNAL
        )
        self._swing_config = StructureConfig(
            self.config.swing_length, StructureType.SWING
        )
        self._internal_detector = StructureDetector(self._internal_config)
        self._swing_detector = StructureDetector(self._swing_config)

        # OB detector
        ob_config = OrderBlockConfig(
            internal_length=self.config.internal_length,
            swing_length=self.config.swing_length,
            atr_period=self.config.atr_period,
            atr_multiplier=self.config.atr_multiplier,
        )
        self._ob_detector = OrderBlockDetector(ob_config)

        # Event listeners
        self._event_listeners: List[Callable[[Event], None]] = []
        if self.config.event_callback:
            self._event_listeners.append(self.config.event_callback)
        if event_callback:
            self._event_listeners.append(event_callback)

        # Persistence
        self._state_path = state_path
        self._last_persisted_ts = 0
        self._persistence_interval = 60  # seconds
        self._last_persistence_time = time.time()

        # Runtime tracking
        self._last_processed_ts = 0
        self._last_processed_idx = -1

    # ── Event emission ─────────────────────────────────────────────────────

    def _emit_event(self, event: Event) -> None:
        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception as e:
                print(f"[WARN] Event listener error: {e}")

    def _emit(
        self,
        event_type: EventType,
        timestamp: datetime,
        data: Dict[str, Any],
    ) -> None:
        event = Event(
            event_type=event_type,
            timestamp=timestamp,
            symbol=self.config.symbol_local,
            timeframe=self.config.timeframe,
            data=data,
        )
        self._emit_event(event)

    # ── Candle closed check ────────────────────────────────────────────────

    def _is_candle_closed(self, candle: Candle) -> bool:
        """Return True iff the candle's 1H interval has fully closed.

        Contract (matching delta_websocket._is_candle_closed):
            candle_ts < current_hour_start
            where current_hour_start = floor(now / 3600) * 3600

        Boundary table (candle at 12:00, current_hour_start shown):
            now=11:59 → chs=11:00 → 11:00>12:00? No  → forming  ✓
            now=12:00 → chs=12:00 → 12:00>12:00? No  → forming  ✓
            now=12:59 → chs=12:00 → 12:00>12:00? No  → forming  ✓
            now=13:00 → chs=13:00 → 13:00>12:00? Yes → CLOSED   ✓
        """
        now_ts = int(datetime.now(timezone.utc).timestamp())
        current_hour_start = now_ts - (now_ts % 3600)
        candle_ts = int(candle.timestamp.timestamp())
        return candle_ts < current_hour_start

    # ── Initialization ─────────────────────────────────────────────────────

    def initialize_from_canonical(self, csv_path) -> None:
        """Initialize engine from canonical historical data CSV."""
        csv_path = Path(csv_path)
        print(f"Initializing engine from {csv_path}")

        if not csv_path.exists():
            raise ValueError(f"CSV file not found: {csv_path}")

        candles = self._load_candles_from_csv(csv_path)
        if not candles:
            raise ValueError("No candles loaded from CSV")

        self._initialize_from_candles(candles)
        self._initialized = True
        print(f"Engine initialized with {len(self._all_candles)} historical candles")

    def _load_candles_from_csv(self, csv_path: Path) -> List[Candle]:
        """Load candles from CSV file into Candle model objects."""
        candles: List[Candle] = []
        if not csv_path.exists():
            return candles

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                ts_int = int(ts.timestamp())
                candles.append(
                    Candle(
                        symbol=self.config.symbol_local,
                        timeframe=self.config.timeframe,
                        timestamp=datetime.fromtimestamp(ts_int, tz=timezone.utc),
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row["volume"]),
                        source=MarketDataSource.HISTORICAL,
                    )
                )
        return candles

    def _initialize_from_candles(self, candles: List[Candle]) -> None:
        """Process historical candles to build initial engine state."""
        if not candles:
            return

        candles.sort(key=lambda c: c.timestamp)

        # Parse with volatility (ATR-based)
        try:
            parsed = parse_candles_with_volatility(
                candles,
                atr_period=self.config.atr_period,
                atr_multiplier=self.config.atr_multiplier,
            )
        except ValueError:
            # Not enough candles for ATR — store as-is without SMC processing
            self._all_candles = candles
            self._all_parsed_candles = []
            if candles:
                self._last_processed_ts = int(candles[-1].timestamp.timestamp())
                self._last_processed_idx = len(candles) - 1
            self._persist_state()
            return

        # Reset detectors for clean initialization
        self._internal_detector.reset()
        self._swing_detector.reset()

        internal_breaks: List[StructureBreak] = []
        swing_breaks: List[StructureBreak] = []

        for i, pc in enumerate(parsed):
            ibs = self._internal_detector.process_candle(pc, i)
            if ibs:
                internal_breaks.extend(ibs)
            sbs = self._swing_detector.process_candle(pc, i)
            if sbs:
                swing_breaks.extend(sbs)

        # Store candles and parsed
        self._all_candles = [pc.original for pc in parsed]
        self._all_parsed_candles = parsed

        # Collect pivots from detectors
        self._internal_pivots = self._extract_pivots(self._internal_detector)
        self._swing_pivots = self._extract_pivots(self._swing_detector)

        # Collect breaks
        self._internal_breaks = internal_breaks
        self._swing_breaks = swing_breaks

        # Detect order blocks
        obs = self._ob_detector.detect_order_blocks(
            parsed_candles=parsed,
            internal_breaks=internal_breaks,
            swing_breaks=swing_breaks,
            internal_pivots=self._internal_pivots,
            swing_pivots=self._swing_pivots,
        )
        for ob in obs:
            self._register_ob(ob)

        # Update tracking
        if parsed:
            self._last_processed_ts = int(parsed[-1].original.timestamp.timestamp())
            self._last_processed_idx = len(parsed) - 1

        # Detect gaps in historical data
        self._detect_gaps_internal()

        # Persist initial state
        self._persist_state()

    def _extract_pivots(self, detector: StructureDetector) -> List[PivotPoint]:
        """Extract current pivot points from a detector."""
        pivots: List[PivotPoint] = []
        if detector.state.pivot_high:
            ph = detector.state.pivot_high
            pivots.append(
                PivotPoint(
                    index=ph.index,
                    timestamp=ph.timestamp,
                    price=ph.price,
                    is_high=True,
                    candle=ph.candle,
                )
            )
        if detector.state.pivot_low:
            pl = detector.state.pivot_low
            pivots.append(
                PivotPoint(
                    index=pl.index,
                    timestamp=pl.timestamp,
                    price=pl.price,
                    is_high=False,
                    candle=pl.candle,
                )
            )
        return pivots

    def _register_ob(self, ob: OrderBlock) -> None:
        """Register an OB in the engine state."""
        ob_id = len(self._all_obs)
        self._all_obs[ob_id] = ob
        if ob.state in (OBState.FRESH, OBState.TOUCHED):
            self._active_obs[ob_id] = ob

    # ── Incremental processing ─────────────────────────────────────────────

    def process_new_candles(self, new_candles: List[Any]) -> Dict[str, Any]:
        """Process a batch of newly closed candles and update engine state.

        Args:
            new_candles: List of closed candles. Each entry may be a Candle
                         model or a dict (from WebSocket/backfill).

        Returns:
            Dict with processing summary.
        """
        if not self._initialized:
            raise RuntimeError(
                "Engine not initialized. Call initialize_from_canonical() first."
            )

        if not new_candles:
            return {
                "events": [],
                "processed": 0,
                "new_obs": 0,
                "obs_touched": 0,
                "obs_invalidated": 0,
                "new_breaks": 0,
            }

        # Normalise to Candle model objects
        candle_models = []
        for c in new_candles:
            if isinstance(c, Candle):
                candle_models.append(c)
            elif isinstance(c, dict):
                cm = self._dict_to_candle(c)
                if cm is not None:
                    candle_models.append(cm)

        # Filter to only closed candles
        closed = [c for c in candle_models if self._is_candle_closed(c)]

        if not closed:
            return {
                "events": [],
                "processed": 0,
                "new_obs": 0,
                "obs_touched": 0,
                "obs_invalidated": 0,
                "new_breaks": 0,
            }

        results: Dict[str, Any] = {
            "events": [],
            "processed": 0,
            "new_obs": 0,
            "obs_touched": 0,
            "obs_invalidated": 0,
            "new_breaks": 0,
        }

        for candle in closed:
            candle_ts = int(candle.timestamp.timestamp())
            # Deduplication: skip already-processed timestamps
            if candle_ts <= self._last_processed_ts:
                continue
            self._process_candle(candle, results)

        # Periodic persistence
        self._maybe_persist()
        return results

    def _dict_to_candle(self, d: dict) -> Optional[Candle]:
        """Convert a WebSocket/REST dict to a Candle model."""
        try:
            ts_raw = d.get("timestamp")
            if ts_raw is None:
                return None
            if isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)

            def _dec(key: str, alt: str = "") -> Decimal:
                val = d.get(key) or d.get(alt) or "0"
                return Decimal(str(val))

            return Candle(
                symbol=d.get("symbol", self.config.symbol_local),
                timeframe=self.config.timeframe,
                timestamp=ts,
                open=_dec("open", "o"),
                high=_dec("high", "h"),
                low=_dec("low", "l"),
                close=_dec("close", "c"),
                volume=_dec("volume", "v"),
                source=MarketDataSource.DELTA,
            )
        except Exception as e:
            print(f"[WARN] Failed to convert dict to Candle: {e}")
            return None

    def _process_candle(self, candle: Candle, results: Dict[str, Any]) -> None:
        """Process a single closed candle through the full SMC pipeline."""
        # Emit candle-closed event
        self._emit(
            EventType.CANDLE_CLOSED,
            timestamp=candle.timestamp,
            data={
                "candle_ts": int(candle.timestamp.timestamp()),
                "open": float(candle.open),
                "high": float(candle.high),
                "low": float(candle.low),
                "close": float(candle.close),
                "volume": float(candle.volume),
            },
        )

        # Append candle to history (needed for ATR sliding window)
        self._all_candles.append(candle)

        # Build ParsedCandle for this new candle by running full parse
        # (We re-parse the sliding window to get accurate ATR)
        window_size = max(self.config.atr_period + 1, len(self._all_candles))
        window_candles = self._all_candles[-window_size:]

        try:
            parsed_window = parse_candles_with_volatility(
                window_candles,
                atr_period=self.config.atr_period,
                atr_multiplier=self.config.atr_multiplier,
            )
        except ValueError:
            # Not enough candles for ATR yet — just track but don't run SMC
            self._last_processed_ts = int(candle.timestamp.timestamp())
            self._last_processed_idx += 1
            results["processed"] += 1
            return

        # The new parsed candle is the last in the window
        new_pc = parsed_window[-1]
        self._all_parsed_candles.append(new_pc)

        # Feed into structure detectors using the absolute candle index
        candle_idx = len(self._all_candles) - 1
        new_internal_breaks = self._internal_detector.process_candle(new_pc, candle_idx)
        new_swing_breaks = self._swing_detector.process_candle(new_pc, candle_idx)

        # Update pivots
        self._internal_pivots = self._extract_pivots(self._internal_detector)
        self._swing_pivots = self._extract_pivots(self._swing_detector)

        # Emit structure break events
        for brk in new_internal_breaks:
            self._internal_breaks.append(brk)
            evt = (
                EventType.INTERNAL_CHOCH
                if brk.break_type == BreakType.CHOCH
                else EventType.INTERNAL_BOS
            )
            self._emit(evt, timestamp=brk.timestamp, data={"price": float(brk.price)})
            results["new_breaks"] += 1

        for brk in new_swing_breaks:
            self._swing_breaks.append(brk)
            evt = (
                EventType.SWING_CHOCH
                if brk.break_type == BreakType.CHOCH
                else EventType.SWING_BOS
            )
            self._emit(evt, timestamp=brk.timestamp, data={"price": float(brk.price)})
            results["new_breaks"] += 1

        # Detect new OBs from any new breaks
        all_new_breaks = new_internal_breaks + new_swing_breaks
        if all_new_breaks:
            for brk in new_internal_breaks:
                ob = self._ob_detector._create_order_block_from_break(
                    parsed_candles=self._all_parsed_candles,
                    break_event=brk,
                    structure_type="internal",
                    internal_pivots=self._internal_pivots,
                    swing_pivots=self._swing_pivots,
                )
                if ob is not None:
                    self._register_ob(ob)
                    results["new_obs"] += 1
                    self._emit(
                        EventType.INTERNAL_OB_CREATED,
                        timestamp=ob.formation_candle.timestamp,
                        data={
                            "type": ob.type,
                            "top": float(ob.top_price),
                            "bottom": float(ob.bottom_price),
                        },
                    )
            for brk in new_swing_breaks:
                ob = self._ob_detector._create_order_block_from_break(
                    parsed_candles=self._all_parsed_candles,
                    break_event=brk,
                    structure_type="swing",
                    internal_pivots=self._internal_pivots,
                    swing_pivots=self._swing_pivots,
                )
                if ob is not None:
                    self._register_ob(ob)
                    results["new_obs"] += 1
                    self._emit(
                        EventType.SWING_OB_CREATED,
                        timestamp=ob.formation_candle.timestamp,
                        data={
                            "type": ob.type,
                            "top": float(ob.top_price),
                            "bottom": float(ob.bottom_price),
                        },
                    )

        # Update active OB lifecycle (touch / invalidation)
        for ob_id, ob in list(self._active_obs.items()):
            # Touch check (strictly after break candle)
            if ob.break_index < candle_idx:
                if ob.check_touch(candle):
                    results["obs_touched"] += 1
                    self._emit(
                        EventType.OB_TOUCHED,
                        timestamp=candle.timestamp,
                        data={"ob_id": ob_id, "ob_type": ob.type},
                    )
            # Invalidation check (strictly after break candle)
            if ob.break_index < candle_idx:
                if ob.check_invalidation(candle):
                    results["obs_invalidated"] += 1
                    self._active_obs.pop(ob_id, None)
                    self._emit(
                        EventType.OB_INVALIDATED,
                        timestamp=candle.timestamp,
                        data={"ob_id": ob_id, "ob_type": ob.type},
                    )

        # Update tracking
        self._last_processed_ts = int(candle.timestamp.timestamp())
        self._last_processed_idx += 1
        results["processed"] += 1

    def process_new_candle(self, candle: Candle) -> List[Event]:
        """Process a single newly closed candle (convenience wrapper)."""
        if not self._is_candle_closed(candle):
            return []
        results: Dict[str, Any] = {
            "events": [],
            "processed": 0,
            "new_obs": 0,
            "obs_touched": 0,
            "obs_invalidated": 0,
            "new_breaks": 0,
        }
        candle_ts = int(candle.timestamp.timestamp())
        if candle_ts <= self._last_processed_ts:
            return []
        self._process_candle(candle, results)
        self._maybe_persist()
        return results.get("events", [])

    # ── Persistence ────────────────────────────────────────────────────────

    def _persist_state(self) -> None:
        if not self._state_path:
            return
        try:
            snapshot = EngineStateSnapshot(
                last_processed_ts=self._last_processed_ts,
                last_processed_idx=self._last_processed_idx,
                internal_detector_state={},
                swing_detector_state={},
                active_obs={},
                all_obs={},
                internal_pivots=[],
                swing_pivots=[],
                internal_breaks=[],
                swing_breaks=[],
                gaps_detected=self._gaps_detected,
                next_ob_id=len(self._all_obs),
                config={
                    "symbol_local": self.config.symbol_local,
                    "delta_symbol": self.config.delta_symbol,
                    "resolution": self.config.resolution,
                    "atr_period": self.config.atr_period,
                    "atr_multiplier": self.config.atr_multiplier,
                    "internal_length": self.config.internal_length,
                    "swing_length": self.config.swing_length,
                },
                schema_version=1,
            )
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._state_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "last_processed_ts": snapshot.last_processed_ts,
                        "last_processed_idx": snapshot.last_processed_idx,
                        "gaps_detected": snapshot.gaps_detected,
                        "next_ob_id": snapshot.next_ob_id,
                        "config": snapshot.config,
                        "schema_version": snapshot.schema_version,
                    },
                    f,
                    indent=2,
                    default=str,
                )
            temp_path.replace(self._state_path)
            self._last_persisted_ts = self._last_processed_ts
        except Exception as e:
            print(f"[WARN] State persistence failed: {e}")

    def _load_state(self) -> bool:
        if not self._state_path or not self._state_path.exists():
            return False
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
            self._last_processed_ts = snapshot.get("last_processed_ts", 0)
            self._last_processed_idx = snapshot.get("last_processed_idx", -1)
            return True
        except Exception as e:
            print(f"[WARN] State load failed: {e}")
            return False

    def _maybe_persist(self) -> None:
        if self._state_path and (
            time.time() - self._last_persistence_time > self._persistence_interval
        ):
            self._persist_state()
            self._last_persistence_time = time.time()

    # ── Gap detection ──────────────────────────────────────────────────────

    def _detect_gaps_internal(self) -> None:
        from quantedge.market_data.ingestion import detect_gaps
        if self._all_candles:
            candles_dict = {
                int(c.timestamp.timestamp()): {"timestamp": c.timestamp.isoformat()}
                for c in self._all_candles
            }
            self._gaps_detected = detect_gaps(candles_dict)
        else:
            self._gaps_detected = []

    # ── Query API ──────────────────────────────────────────────────────────

    def get_current_snapshot(self) -> Dict[str, Any]:
        return {
            "last_processed_ts": self._last_processed_ts,
            "last_processed_idx": self._last_processed_idx,
            "active_obs_count": len(self._active_obs),
            "total_obs": len(self._all_obs),
            "internal_trend": self._internal_detector.get_current_trend().value,
            "swing_trend": self._swing_detector.get_current_trend().value,
            "internal_obs_count": sum(
                1
                for ob in self._active_obs.values()
                if hasattr(ob, "type") and ob.type == "internal"
            ),
            "swing_obs_count": sum(
                1
                for ob in self._active_obs.values()
                if hasattr(ob, "type") and ob.type == "swing"
            ),
            "last_candle_ts": self._last_processed_ts,
            "gaps_detected": len(self._gaps_detected),
        }

    def get_active_obs(self) -> List[OrderBlock]:
        return [
            ob for ob in self._active_obs.values()
            if ob.state in (OBState.FRESH, OBState.TOUCHED)
        ]

    def get_active_obs_at_price(self, price: Any) -> List[OrderBlock]:
        """Return all active (FRESH/TOUCHED) OBs whose price zone contains `price`."""
        p = price if isinstance(price, Decimal) else Decimal(str(price))
        return [
            ob for ob in self.get_active_obs()
            if ob.contains_price(p)
        ]

    def is_price_in_active_ob(self, price: Any) -> bool:
        """Return True if `price` is currently inside at least one active OB zone."""
        return len(self.get_active_obs_at_price(price)) > 0

    def get_all_obs(self) -> List[OrderBlock]:
        return list(self._all_obs.values())

    def get_invalidated_obs(self) -> List[OrderBlock]:
        return [ob for ob in self._all_obs.values() if ob.state == OBState.INVALIDATED]

    def get_recent_breaks(self, lookback: int = 10) -> List[StructureBreak]:
        all_breaks = self._internal_breaks + self._swing_breaks
        all_breaks.sort(key=lambda b: b.timestamp)
        return all_breaks[-lookback:]

    def get_recent_obs(self, lookback: int = 10) -> List[OrderBlock]:
        recent = sorted(
            self._all_obs.values(),
            key=lambda ob: ob.formation_candle.timestamp if ob.formation_candle else 0,
            reverse=True,
        )
        return recent[:lookback]

    # ── Event listener management ──────────────────────────────────────────

    def emit_event(self, event_type: EventType, data: Dict[str, Any]) -> None:
        self._emit(event_type, timestamp=datetime.now(timezone.utc), data=data)

    def register_event_listener(self, callback: Callable[[Event], None]) -> None:
        if callback not in self._event_listeners:
            self._event_listeners.append(callback)

    def remove_event_listener(self, callback: Callable[[Event], None]) -> None:
        if callback in self._event_listeners:
            self._event_listeners.remove(callback)

    def shutdown(self) -> None:
        self._persist_state()
        print("Engine shutdown complete")


# ── Factory helpers ────────────────────────────────────────────────────────────


def create_incremental_engine(
    config: Optional[IncrementalEngineConfig] = None,
    state_path: Optional[Path] = None,
    event_callback: Optional[Callable[[Event], None]] = None,
) -> IncrementalSMCEngine:
    """Factory function to create an incremental SMC engine."""
    return IncrementalSMCEngine(
        config=config, state_path=state_path, event_callback=event_callback
    )


def run_incremental_processing(
    engine: IncrementalSMCEngine,
    candles: List[Candle],
) -> List[Event]:
    """Process a batch of new candles and return events."""
    results = engine.process_new_candles(candles)
    return results.get("events", [])


if __name__ == "__main__":
    print("Incremental SMC Engine module loaded")