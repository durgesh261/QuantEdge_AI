"""
Historical Event Models for QuantEdge SMC Replay.

Normalized event models for historical SMC replay output.
Uses plain dicts instead of dataclasses to avoid inheritance issues.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
import json


class HistoricalEventType(str):
    """Types of historical events emitted during replay."""
    LEG_CHANGE = "leg_change"
    PIVOT_CREATED = "pivot_created"
    BOS = "bos"
    CHOCH = "choch"
    ORDER_BLOCK_CREATED = "order_block_created"
    ORDER_BLOCK_INVALIDATED = "order_block_invalidated"
    ORDER_BLOCK_TOUCHED = "order_block_touched"
    ORDER_BLOCK_USED = "order_block_used"
    DATASET_START = "dataset_start"
    DATASET_END = "dataset_end"
    REPLAY_COMPLETE = "replay_complete"


def create_leg_change_event(
    symbol: str,
    timeframe: str,
    candle_index: int,
    timestamp: datetime,
    previous_leg: int,
    new_leg: int,
    pivot_high_price: Optional[str] = None,
    pivot_high_index: Optional[int] = None,
    pivot_low_price: Optional[str] = None,
    pivot_low_index: Optional[int] = None
) -> Dict[str, Any]:
    return {
        "event_id": f"leg_change_{symbol}_{candle_index}",
        "event_type": "leg_change",
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
        "candle_index": candle_index,
        "previous_leg": previous_leg,
        "new_leg": new_leg,
        "pivot_high_price": pivot_high_price,
        "pivot_high_index": pivot_high_index,
        "pivot_low_price": pivot_low_price,
        "pivot_low_index": pivot_low_index
    }


def create_pivot_created_event(
    symbol: str,
    timeframe: str,
    candle_index: int,
    timestamp: datetime,
    pivot_type: str,
    pivot_price: str,
    pivot_index: int,
    pivot_timestamp: datetime,
    structure_type: str
) -> Dict[str, Any]:
    return {
        "event_id": f"pivot_{pivot_type}_{symbol}_{candle_index}",
        "event_type": "pivot_created",
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
        "candle_index": candle_index,
        "pivot_type": pivot_type,
        "pivot_price": pivot_price,
        "pivot_index": pivot_index,
        "pivot_timestamp": pivot_timestamp.isoformat() if isinstance(pivot_timestamp, datetime) else str(pivot_timestamp),
        "structure_type": structure_type
    }


def create_structure_break_event(
    symbol: str,
    timeframe: str,
    candle_index: int,
    timestamp: datetime,
    break_type: str,
    direction: str,
    previous_trend: str,
    trend_after: str,
    pivot_price: str,
    pivot_index: int,
    break_price: str,
    structure_type: str
) -> Dict[str, Any]:
    return {
        "event_id": f"{break_type}_{symbol}_{candle_index}",
        "event_type": "bos" if break_type == "bos" else "choch",
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
        "candle_index": candle_index,
        "break_type": break_type,
        "direction": direction,
        "previous_trend": previous_trend,
        "trend_after": trend_after,
        "pivot_price": pivot_price,
        "pivot_index": pivot_index,
        "break_price": break_price,
        "structure_type": structure_type
    }


def create_order_block_created_event(
    symbol: str,
    timeframe: str,
    candle_index: int,
    timestamp: datetime,
    ob_type: str,
    top_price: str,
    bottom_price: str,
    formation_candle_index: int,
    formation_timestamp: datetime,
    break_index: int,
    break_type: str,
    trend_before_break: str,
    pivot_index: int,
    break_index_: int,
    structure_type: str,
    source_candle_index: int,
    source_parsed_high: Optional[str] = None,
    source_parsed_low: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "event_id": f"ob_{ob_type}_{symbol}_{candle_index}",
        "event_type": "order_block_created",
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
        "candle_index": candle_index,
        "ob_type": ob_type,
        "top_price": top_price,
        "bottom_price": bottom_price,
        "formation_candle_index": formation_candle_index,
        "formation_timestamp": formation_timestamp.isoformat() if isinstance(formation_timestamp, datetime) else str(formation_timestamp),
        "break_index": break_index,
        "break_type": break_type,
        "trend_before_break": trend_before_break,
        "pivot_index": pivot_index,
        "break_index_": break_index_,
        "structure_type": structure_type,
        "source_candle_index": source_candle_index,
        "source_parsed_high": source_parsed_high,
        "source_parsed_low": source_parsed_low
    }


def create_order_block_lifecycle_event(
    symbol: str,
    timeframe: str,
    candle_index: int,
    timestamp: datetime,
    event_type: str,
    ob_index: int,
    ob_type: str,
    previous_state: str,
    new_state: str,
    touch_price: Optional[str] = None,
    touch_timestamp: Optional[datetime] = None
) -> Dict[str, Any]:
    return {
        "event_id": f"ob_lifecycle_{event_type}_{symbol}_{candle_index}",
        "event_type": event_type,
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
        "candle_index": candle_index,
        "ob_index": ob_index,
        "ob_type": ob_type,
        "previous_state": previous_state,
        "new_state": new_state,
        "touch_price": touch_price,
        "touch_timestamp": touch_timestamp.isoformat() if isinstance(touch_timestamp, datetime) else str(touch_timestamp) if touch_timestamp else None
    }


def create_dataset_event(
    event_type: str,
    symbol: str,
    timeframe: str,
    dataset_id: str,
    candle_count: int,
    date_range_start: Optional[datetime] = None,
    date_range_end: Optional[datetime] = None
) -> Dict[str, Any]:
    timestamp = date_range_start if date_range_start else datetime.now()
    return {
        "event_id": f"dataset_{event_type}_{symbol}",
        "event_type": event_type,
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
        "candle_index": 0,
        "dataset_id": dataset_id,
        "candle_count": candle_count,
        "date_range_start": date_range_start.isoformat() if isinstance(date_range_start, datetime) else str(date_range_start) if date_range_start else None,
        "date_range_end": date_range_end.isoformat() if isinstance(date_range_end, datetime) else str(date_range_end) if date_range_end else None
    }


def create_replay_complete_event(
    symbol: str,
    timeframe: str,
    total_candles: int,
    events_generated: int,
    duration_seconds: float,
    summary: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        "event_id": f"replay_complete_{symbol}",
        "event_type": "replay_complete",
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": datetime.now().isoformat(),
        "candle_index": total_candles,
        "total_candles": total_candles,
        "events_generated": events_generated,
        "duration_seconds": duration_seconds,
        "summary": summary
    }


# JSONL writer for streaming output

class JsonlEventWriter:
    """Writes historical events to JSONL file."""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self._file = None
        self._count = 0
    
    def __enter__(self):
        self._file = open(self.file_path, "w", encoding="utf-8")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file:
            self._file.close()
    
    def write(self, event: Dict[str, Any]) -> None:
        """Write a single event to JSONL."""
        if self._file:
            self._file.write(json.dumps(event, default=str) + "\n")
            self._count += 1
    
    def write_batch(self, events: List[Dict[str, Any]]) -> None:
        for event in events:
            self.write(event)
    
    @property
    def count(self) -> int:
        return self._count


def load_events_from_jsonl(file_path: str) -> List[Dict[str, Any]]:
    """Load events from JSONL file (for testing/verification)."""
    events = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            events.append(data)
    return events