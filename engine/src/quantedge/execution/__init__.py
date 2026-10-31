"""
QuantEdge AI Execution & Delta Exchange India Client Package.
"""

from quantedge.execution.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    TimeInForce,
    PositionSide,
    DeltaWalletBalance,
    DeltaAccountSummary,
    DeltaPosition,
    DeltaOrderRequest,
    DeltaOrderResponse,
    ConnectionState,
    ExecutionMode,
    ReconciliationDiscrepancyType,
    ReconciliationDiscrepancy,
    ReconciliationReport,
    TradeCostBreakdown,
    OrderSizeContractError,
)
from quantedge.execution.reconciliation import (
    DeltaReconciliationService,
)
from quantedge.execution.security import (
    mask_secret,
    encrypt_credential,
    decrypt_credential,
    sanitize_text,
)
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DeltaClientError,
    DeltaAuthError,
    DeltaRateLimitError,
    DeltaOrderRejectedError,
    DeltaConnectionError,
    DeltaResponseError,
    DeltaExecutionAuthorityError,
    DeltaListingIncompleteError,
    generate_signature,
    generate_client_order_id,
    DELTA_INDIA_PRODUCTION_URL,
    DELTA_INDIA_TESTNET_URL,
)
from quantedge.execution.synchronizer import (
    PositionStatus,
    PositionRecord,
    OrderRecord,
    AccountRecord,
    ConnectionRecord,
    LocalStateStore,
    SyncResult,
    LiveAccountSyncService,
)
from quantedge.execution.leverage import (
    MIN_LEVERAGE,
    MAX_LEVERAGE,
    LeverageBandError,
    is_within_band,
    normalize_requested_leverage,
    validate_leverage,
)
from quantedge.execution.validation import (
    RejectionReasonCode,
    ProductSpecification,
    DEFAULT_DELTA_INDIA_PRODUCTS,
    UnknownProductError,
    get_product_specification,
    product_specification_from_instrument,
    RiskConfiguration,
    ValidationContext,
    OrderValidationRequest,
    OrderValidationResult,
    OrderValidationGateway,
)
# ── QUARANTINE: `execution_engine` is NOT the canonical execution path ────────
#
# `MarketScannerOrchestrator -> TradeLifecycleManager` is the canonical
# production-facing execution implementation. `LiveOrderExecutionService` in
# `quantedge.execution.execution_engine` is an orphan: nothing in
# `quantedge/` constructs or calls it, and it does not implement the lifecycle
# semantics the canonical path enforces (no protective bracket, no
# single-trade account lock, no reconciliation-alert entry block, no durable
# lifecycle record). See the module banner in `execution_engine.py`.
#
# It is deliberately NOT re-exported here. A package-root export is how an
# orphan becomes production wiring by accident: `from quantedge.execution
# import LiveOrderExecutionService` reads like the blessed entry point. The
# module is intact and importable by its full path, which is how every test
# that exercises it already imports it, so no functionality and no test is
# lost.

from quantedge.execution.algo_config import (
    AlgoConfiguration,
    AlgoConfigurationSnapshot,
    AlgoConfigStore,
    AlgoConfigValidationError,
)
from quantedge.execution.single_trade_lock import (
    SingleTradeLockManager,
    SingleTradeLockError,
    AccountTradeLockState,
)
from quantedge.execution.capital_allocator import (
    CapitalAllocator,
    CapitalAllocationError,
    PositionSizingResult,
)
from quantedge.execution.market_orchestrator import (
    MarketScannerOrchestrator,
    MarketScanResult,
)
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleRecord,
    TradeLifecycleState,
    CloseReason,
)


__all__ = [
    "MIN_LEVERAGE",
    "MAX_LEVERAGE",
    "LeverageBandError",
    "is_within_band",
    "normalize_requested_leverage",
    "validate_leverage",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "TimeInForce",
    "PositionSide",
    "DeltaWalletBalance",
    "DeltaAccountSummary",
    "DeltaPosition",
    "DeltaOrderRequest",
    "DeltaOrderResponse",
    "OrderSizeContractError",
    "mask_secret",
    "encrypt_credential",
    "decrypt_credential",
    "sanitize_text",
    "DeltaIndiaClient",
    "DeltaClientError",
    "DeltaAuthError",
    "DeltaRateLimitError",
    "DeltaOrderRejectedError",
    "DeltaConnectionError",
    "DeltaResponseError",
    "DeltaExecutionAuthorityError",
    "DeltaListingIncompleteError",
    "generate_signature",
    "generate_client_order_id",
    "DELTA_INDIA_PRODUCTION_URL",
    "DELTA_INDIA_TESTNET_URL",
    "PositionStatus",
    "PositionRecord",
    "OrderRecord",
    "AccountRecord",
    "ConnectionRecord",
    "LocalStateStore",
    "SyncResult",
    "LiveAccountSyncService",
    "RejectionReasonCode",
    "ProductSpecification",
    "DEFAULT_DELTA_INDIA_PRODUCTS",
    "UnknownProductError",
    "get_product_specification",
    "product_specification_from_instrument",
    "RiskConfiguration",
    "ValidationContext",
    "OrderValidationRequest",
    "OrderValidationResult",
    "OrderValidationGateway",
    # `ExecutionState`, `OrderExecutionRequest`, `OrderExecutionResult`,
    # `LiveOrderExecutionService` and `StrategyDecisionStore` are intentionally
    # absent: see the QUARANTINE note above. Import them from
    # `quantedge.execution.execution_engine` if a test genuinely needs them.
    "AlgoConfiguration",
    "AlgoConfigurationSnapshot",
    "AlgoConfigStore",
    "AlgoConfigValidationError",
    "SingleTradeLockManager",
    "SingleTradeLockError",
    "AccountTradeLockState",
    "CapitalAllocator",
    "CapitalAllocationError",
    "PositionSizingResult",
    "MarketScannerOrchestrator",
    "MarketScanResult",
    "TradeLifecycleManager",
    "TradeLifecycleRecord",
    "TradeLifecycleState",
    "CloseReason",
    "ConnectionState",
    "ExecutionMode",
    "ReconciliationDiscrepancyType",
    "ReconciliationDiscrepancy",
    "ReconciliationReport",
    "TradeCostBreakdown",
    "DeltaReconciliationService",
    # `UserAccountConfig`, `UserExecutionResult`, `MultiUserDispatchSummary`,
    # `UserExecutionSession` and `MultiUserExecutionOrchestrator` are
    # intentionally absent: see the QUARANTINE note below. Import them from
    # `quantedge.execution.multi_user_orchestrator` if a test genuinely needs
    # them.
    "EntryAdmissionContract",
    "EntryAdmissionError",
    "CANONICAL_ENTRY_SUBMISSION_PATHS",
    "QUARANTINED_SUBMISSION_PATHS",
    "install_entry_admission",
    "assert_entry_admission_contract_intact",
]

# ── QUARANTINE: `multi_user_orchestrator` is NOT a canonical submission path ───
#
# P1-6. This repository contains THREE places that can POST an order:
#
#   1. `TradeLifecycleManager.execute_trade_setup` -- CANONICAL. Deterministic
#      client order ids, protective bracket, single-trade account lock,
#      reconciliation-alert entry block, durable lifecycle record, and the
#      entry-admission contract below.
#   2. `LiveOrderExecutionService` in `execution_engine` -- already quarantined
#      above.
#   3. `UserExecutionSession.execute_trade` in `multi_user_orchestrator` -- this
#      one. It mints `generate_client_order_id` (RANDOM) for entry, SL and TP,
#      so two runs of the same setup are two unrelated names on the exchange and
#      a retry cannot be recognised as a duplicate. It also does not consult
#      `_entry_admission_reason`, so "no clean reconciliation = no entry" does
#      not apply to it.
#
# What made it safe until now was purely that nothing in `quantedge/` constructs
# it (asserted structurally by test F5). That is an accident of wiring, not a
# contract -- and the package root was quietly undoing it, because
# `from quantedge.execution import MultiUserExecutionOrchestrator` reads exactly
# like the blessed multi-account entry point.
#
# So it is no longer re-exported here. The module is UNCHANGED and importable by
# its full path -- every test that exercises it already imports it that way, and
# its random-id behaviour is deliberately left alone (test F7 pins that gap as a
# stated known limitation rather than a silent one). Converting its identity
# would be work on a path that must not become production; the safe move is to
# make it harder to reach, not to make it look production-ready.
#
# There is exactly ONE canonical production submission identity path, and this
# is not it.

from quantedge.execution.entry_admission import (
    CANONICAL_ENTRY_SUBMISSION_PATHS,
    QUARANTINED_SUBMISSION_PATHS,
    EntryAdmissionContract,
    EntryAdmissionError,
    assert_entry_admission_contract_intact,
    install_entry_admission,
)
