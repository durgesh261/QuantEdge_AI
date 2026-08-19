"""
Historical Validation Package for QuantEdge SMC.

Provides historical data providers, replay engine, and event models
for deterministic SMC validation against historical market data.
"""

from .provider import (
    HistoricalDataProvider,
    CsvHistoricalDataProvider,
    DatasetMetadata
)

from .events import (
    JsonlEventWriter,
    load_events_from_jsonl,
    create_leg_change_event,
    create_pivot_created_event,
    create_structure_break_event,
    create_order_block_created_event,
    create_order_block_lifecycle_event,
    create_dataset_event,
    create_replay_complete_event
)

from .replay import (
    ReplayConfig,
    ReplayState,
    ReplayResult,
    HistoricalReplayEngine,
    run_historical_validation
)

__all__ = [
    # Provider
    "HistoricalDataProvider",
    "CsvHistoricalDataProvider",
    "DatasetMetadata",
    # Events
    "JsonlEventWriter",
    "load_events_from_jsonl",
    "create_leg_change_event",
    "create_pivot_created_event",
    "create_structure_break_event",
    "create_order_block_created_event",
    "create_order_block_lifecycle_event",
    "create_dataset_event",
    "create_replay_complete_event",
    # Replay
    "ReplayConfig",
    "ReplayState",
    "ReplayResult",
    "HistoricalReplayEngine",
    "run_historical_validation"
]