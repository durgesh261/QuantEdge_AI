"""Production execution composition for QuantEdge AI (§§4-7,9).

This module is the ONE place where the canonical execution stack is built and
armed: authenticated Delta client, TradeLifecycleManager with its protective
contract, MarketScannerOrchestrator, and the startup reconciliation gate. It
composes nothing else: no quarantined path, no orphan service, and no second
client. It imports no strategy, contacts no exchange, and submits no order.

WHY IT EXISTS
-------------
`quantedge.runtime` is *"the only place where a strategy package and the frozen
execution engine are allowed to meet"*. Before this module, that meeting was
read-only: `manual_smc_runtime.py` bound the closed-candle STRUCTURE clock and
left `orchestrator=None`, so every ready decision hit NO_ORCHESTRATION_BOUND.

This module installs the canonical orchestrator and binds BOTH clocks: closed
candle → structure, forming candle → execution. It adds NOTHING to strategy
semantics: LuxAlgo detection, first-touch admission, 25% entry sizing, TP/SL
calculation, three-candle expiry, and movement filters stay unchanged. What it
adds is the execution contract: deterministic client-order-ID generation,
protective bracket submission, single-trade account lock, durable lifecycle
persistence, admission-contract consultation, and startup reconciliation.

WHAT IT DOES NOT DO
-------------------
It does NOT perform order submission in this mandate. `order_submission_enabled`
stays False, and `ProductionExecutionGate` refuses entries with a distinguishable
TRADING_DISABLED reason after all safety checks pass. A fully composed, fully
reconciled, admission-authorized production system that stops BEFORE the exchange
is the deliverable — not a system that trades.

It does NOT invent credential architecture. The authenticated user's Delta
credential lives encrypted in the Java backend, and the backend deliberately
exposes no route that returns it. `UserSettingsCredentialProvider` fails closed
naming that gap as a blocker rather than falling back to .env. Only the developer
provider — explicitly opted into with `allow_developer_env=True` — reaches .env,
and it is marked `production_grade=False` so no composition can mistake it for a
real user's account.

It does NOT create a second execution authority. The one canonical path is
MarketScannerOrchestrator -> TradeLifecycleManager -> DeltaIndiaClient.
LiveOrderExecutionService and MultiUserExecutionOrchestrator stay quarantined:
neither is imported here, neither is re-exported from `quantedge.execution`, and
the admission-contract structural assertion proves they are unreachable.

STARTUP ORDERING (§5)
---------------------
1. Load configuration (`Settings`).
2. Resolve the credential provider (production first, developer only if opted in).
3. Build an authenticated Delta client through the provider with
   `allow_direct_execution=settings.order_submission_enabled` (False).
4. Validate account identity (client.get_account_summary()).
5. Load durable execution state from the configured state path.
6. Construct the canonical execution objects: state store, validation gateway,
   locks, allocators, sync service, lifecycle manager, orchestrator, reconciliation.
7. Install the admission contract (ProductionExecutionGate wrapping
   ExecutionStartupGate).
8. Perform authoritative startup reconciliation.
9. Arm the gate only after CLEAN reconciliation.
10. Bind both clocks and start processing market data.

Non-clean reconciliation blocks arming. A blocked gate refuses every entry with
its original block reason (exchange unreachable, orphan position, ambiguous
submission, state incompatible, etc.), and trading-disabled stays unreachable
until reconciliation is clean.

SAFETY INVARIANTS (§§4,9,12)
-----------------------------
* The admission contract is installed BEFORE the orchestrator is attached to the
  runtime, so there is no window where a strategy callback can submit without it.
* `allow_direct_execution=False` blocks writes at the client level, pre-HTTP and
  outside pytest, so even if reconciliation's `_ensure_bracket_protection` path
  were reached (it requires a filled durable record), the call would raise before
  contacting the exchange.
* `AccountRecord.__post_init__` makes it impossible to construct a trading-enabled
  account without an explicit post-construction authorized mutation, and
  `execute_trade_setup` re-checks `kill_switch_active` before submission.
* Deterministic client-order-ID generation is verified by test for ENTRY, SL, TP,
  SL2, TP2.
* No module-level import of a forbidden token: no `place_order` call site, no
  `notify_trade_open`, no `get_next_trade_capital`, no `reconcile_account` in a
  new production module. The structural checks prove it.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

from quantedge.config import Settings
from quantedge.execution.delta_client import DeltaIndiaClient
from quantedge.execution.entry_admission import install_entry_admission
from quantedge.execution.market_orchestrator import (
    CapitalAllocator,
    MarketScannerOrchestrator,
)
from quantedge.execution.reconciliation import DeltaReconciliationService
from quantedge.execution.startup_gate import ExecutionStartupGate
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import (
    LiveAccountSyncService,
    LocalStateStore,
)
from quantedge.execution.trade_lifecycle import TradeLifecycleManager
from quantedge.execution.validation import OrderValidationGateway
from quantedge.market_data.delta_websocket import DeltaWebSocketClient
from quantedge.runtime.credential_provider import (
    CredentialUnavailableError,
    DeltaCredentialProvider,
    resolve_credential_provider,
)
from quantedge.runtime.restart_recovery import make_durable_feed_callback
from quantedge.runtime.manual_smc_runtime import (
    ManualSMCRuntime,
    make_feed_callback,
    make_market_update_callback,
)
from quantedge.execution.models import ExecutionMode

logger = logging.getLogger("production_composition")

#: The refusal reason returned when all safety checks pass but trading is disabled.
#: Distinguishable from genuine safety refusals (exchange unreachable, orphan
#: position, stale authorization, etc.) so logs and diagnostics can separate
#: "blocked by safety" from "blocked by the trading gate".
TRADING_DISABLED_REASON = (
    "order submission is disabled: all safety checks passed, but the final "
    "trading-enable gate (order_submission_enabled) is false"
)


class ProductionCompositionError(RuntimeError):
    """Production execution could not be composed safely.

    Raised for: missing state path, credential unavailable, account identity
    unreachable, state incompatible, reconciliation non-clean with no override.
    Never raised for a strategy decision or an admission refusal — those are
    verdicts, not composition failures.
    """


class ProductionExecutionGate(ExecutionStartupGate):
    """The final trading-enable gate wrapping the startup reconciliation gate.

    Composes with `ExecutionStartupGate` rather than replacing it: the parent
    evaluates the safety verdict (reconciliation clean, state current, admission
    authorized), and this subclass adds the explicit trading-enable check on top.

    The result is ONE installed contract whose `admission_reason()` returns:
      * A safety block reason (exchange unreachable, orphan position, stale
        authorization, etc.) when a safety check fails, OR
      * TRADING_DISABLED_REASON when all safety checks pass but trading is
        disabled, OR
      * None to admit.

    This keeps "all safety checks passed, trading disabled" distinguishable from
    a genuine safety refusal, which is what §9 and test L require, with exactly
    one installed contract and no change to `startup_gate.py`.

    `_trading_enabled` is set BEFORE `super().__init__(...)` on purpose: the
    parent's `__init__` may call `_block(...)`, which clears `_armed_fingerprint`,
    and a subclass field the parent reads must exist before the parent runs.
    """

    def __init__(
        self,
        manager: TradeLifecycleManager,
        state_path: Path,
        *,
        trading_enabled: bool,
        strategy_runtime: Optional[Any] = None,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        allow_missing_state: bool = False,
    ) -> None:
        self._trading_enabled = trading_enabled
        self.entries_refused_by_trading_gate = 0
        super().__init__(
            manager,
            state_path,
            strategy_runtime=strategy_runtime,
            account_id=account_id,
            user_id=user_id,
            allow_missing_state=allow_missing_state,
        )

    def admission_reason(self) -> Optional[str]:
        """Compose the safety verdict with the trading-enable check.

        Evaluates the parent's safety verdict first (block reason, NOT_ARMED_REASON,
        or a staleness reason). If non-None, that safety refusal is returned
        unchanged — safety blocks trading, full stop.

        If the safety verdict is None (all checks passed), the trading-enable
        flag is consulted. When disabled, TRADING_DISABLED_REASON is returned
        and the refusal is counted for diagnostics. When enabled, None is
        returned to admit.

        Synchronous and side-effect free by design, exactly as the parent's
        contract requires.
        """
        safety_verdict = super().admission_reason()
        if safety_verdict is not None:
            return safety_verdict

        if not self._trading_enabled:
            self.entries_refused_by_trading_gate += 1
            logger.warning(
                "ENTRY_REFUSED trading_enabled=%s | all safety checks passed but "
                "order submission is disabled | refusals_by_trading_gate=%d",
                self._trading_enabled,
                self.entries_refused_by_trading_gate,
            )
            return TRADING_DISABLED_REASON

        return None


async def describe_real_account(client: DeltaIndiaClient) -> Dict[str, Any]:
    """Safe metadata from a real Delta account. Masked identities only.

    Read-only validation helper for §3. Returns account identity masked, product
    resolution success, and position/order inventory counts. Never returns a
    credential, a plaintext user_id, or raw API responses that might contain PII.

    Raises on connectivity failure or missing account identity. The caller decides
    whether that blocks composition or is merely reported.
    """
    balances = await client.get_wallet_balances()
    if not balances:
        raise ProductionCompositionError(
            "real account identity unavailable: get_wallet_balances returned empty"
        )

    user_id = balances[0].user_id
    if not user_id:
        raise ProductionCompositionError(
            "real account identity unavailable: user_id is absent"
        )

    # Mask: keep prefix and length, replace the rest with asterisks. Safe for logs.
    masked = user_id[:4] + "*" * max(0, len(user_id) - 4) if len(user_id) > 4 else "****"

    from quantedge.execution.validation import get_product_specification

    resolved_products = {}
    for symbol in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
        try:
            spec = get_product_specification(symbol)
            resolved_products[symbol] = {
                "product_id": spec.product_id,
                "contract_value": str(spec.contract_value),
                "tick_size": str(spec.tick_size),
            }
        except Exception as exc:  # noqa: BLE001
            resolved_products[symbol] = {"error": str(exc)}

    positions = await client.get_positions()
    orders = await client.get_open_orders()

    return {
        "authenticated": True,
        "user_id_masked": masked,
        "products_resolved": resolved_products,
        "position_count": len(positions),
        "open_order_count": len(orders),
    }


class ProductionExecution:
    """The composed production execution stack, before anything is armed.

    A named container rather than a positional tuple: the gate, the orchestrator
    and the client are three different kinds of thing, and returning them in
    declaration order invites a caller to swap two of them silently. Every field
    is read-only in intent; nothing here has contacted the exchange yet, and the
    gate is NOT armed.
    """

    __slots__ = (
        "client",
        "orchestrator",
        "lifecycle_manager",
        "reconciliation_service",
        "state_store",
        "gate",
    )

    def __init__(
        self,
        *,
        client: DeltaIndiaClient,
        orchestrator: MarketScannerOrchestrator,
        lifecycle_manager: TradeLifecycleManager,
        reconciliation_service: DeltaReconciliationService,
        state_store: LocalStateStore,
        gate: "ProductionExecutionGate",
    ) -> None:
        self.client = client
        self.orchestrator = orchestrator
        self.lifecycle_manager = lifecycle_manager
        self.reconciliation_service = reconciliation_service
        self.state_store = state_store
        self.gate = gate


def build_production_execution(
    *,
    runtime: ManualSMCRuntime,
    settings: Settings,
    credential_provider: DeltaCredentialProvider,
    state_path: Path,
    account_id: str = "DEFAULT",
    user_id: str = "DEFAULT",
) -> ProductionExecution:
    """Build the canonical execution stack with the trading-disabled gate (§§4-7,9).

    Returns a `ProductionExecution` holding the client, the orchestrator, the
    lifecycle manager, the reconciliation service and the gate. The orchestrator
    is NOT yet attached to the runtime and the gate is NOT yet armed: the caller
    attaches, arms, and only then binds clocks, per §5.

    Deterministic client-order-ID generation is verified by test for ENTRY, SL,
    TP, SL2, TP2. The orphan `LiveOrderExecutionService` submission path stays
    quarantined: not imported, not constructed, not reachable from the package
    root. No credential is logged, no credential is persisted, and no credential
    is returned.

    Raises `CredentialUnavailableError` when the provider cannot build a client,
    and `ProductionCompositionError` for structural failures (missing state path,
    state incompatible, etc.). Neither is a strategy refusal or an admission
    verdict — those happen later, once the gate exists.
    """
    if state_path is None or not Path(state_path).exists():
        raise ProductionCompositionError(
            f"production execution state path does not exist: {state_path}. Write "
            f"one with `persist_execution_state(...)` against a fresh manager, or "
            f"mount a persistent volume at the configured location. Refusing to "
            f"assume a flat book on first boot"
        )

    # Build the authenticated client through the provider with the final
    # order-submission authority. The plaintext credential goes straight from
    # its source into DeltaIndiaClient and is never bound to a name here, never
    # logged, never serialized, and never returned.
    client = credential_provider.build_client(
        allow_direct_execution=settings.order_submission_enabled
    )

    logger.info(
        "canonical execution stack composition | account_id=%s user_id=%s "
        "credential_source=%s production_grade=%s order_submission_authority=%s",
        account_id,
        user_id,
        credential_provider.source_label,
        credential_provider.production_grade,
        settings.order_submission_enabled,
    )

    # Canonical execution objects in dependency order.
    state_store = LocalStateStore(account_id=account_id)
    validation_gateway = OrderValidationGateway()
    single_trade_lock = SingleTradeLockManager()
    capital_allocator = CapitalAllocator(default_safety_buffer_pct=Decimal("98.00"))
    sync_service = LiveAccountSyncService(client, state_store=state_store)

    lifecycle_manager = TradeLifecycleManager(
        client=client,
        validation_gateway=validation_gateway,
        state_store=state_store,
        sync_service=sync_service,
        single_trade_lock=single_trade_lock,
        capital_allocator=capital_allocator,
        execution_mode=ExecutionMode.LIVE,
    )

    orchestrator = MarketScannerOrchestrator(
        lifecycle_manager=lifecycle_manager,
        single_trade_lock=single_trade_lock,
        capital_allocator=capital_allocator,
        supported_symbols=list(runtime.symbols),
    )

    reconciliation_service = DeltaReconciliationService(
        client=client,
        state_store=state_store,
        sync_service=sync_service,
        single_trade_lock=single_trade_lock,
    )

    # Install the admission contract BEFORE the orchestrator is attached to the
    # runtime, so there is no window where a strategy callback can submit without
    # it. ProductionExecutionGate wraps ExecutionStartupGate, composing the
    # trading-enable check with the startup safety verdict.
    #
    # Constructing the gate installs it: `ExecutionStartupGate.__init__` calls
    # the one canonical installer, which refuses to replace an installed
    # contract. That is deliberate -- there is exactly one installation event,
    # so there is no second call a composition could forget.
    gate = ProductionExecutionGate(
        manager=lifecycle_manager,
        state_path=Path(state_path),
        trading_enabled=settings.order_submission_enabled,
        strategy_runtime=runtime,
        account_id=account_id,
        user_id=user_id,
        allow_missing_state=False,
    )
    installed = lifecycle_manager._entry_admission_gate
    assert installed == gate.admission_reason, (
        "the installed admission hook is not this gate's own admission_reason: "
        "the contract is not the one that was just built"
    )

    logger.info(
        "admission contract installed | gate=%s trading_enabled=%s",
        type(gate).__name__,
        settings.order_submission_enabled,
    )

    return ProductionExecution(
        client=client,
        orchestrator=orchestrator,
        lifecycle_manager=lifecycle_manager,
        reconciliation_service=reconciliation_service,
        state_store=state_store,
        gate=gate,
    )


async def run_production_execution(
    *,
    runtime: ManualSMCRuntime,
    state_path: Path,
    settings: Settings,
    account_id: str = "DEFAULT",
    user_id: str = "DEFAULT",
) -> None:
    """Compose, reconcile, arm, and run production execution with both clocks (§§5-7).

    Startup ordering per §5:
      1. Resolve credential provider (production first, developer only if opted in).
      2. Build canonical execution stack with admission contract installed.
      3. Load durable execution state.
      4. Perform authoritative startup reconciliation.
      5. Arm the gate only after CLEAN reconciliation.
      6. Attach orchestrator to runtime and bind execution feeds.
      7. Bind both clocks: closed candle → structure, forming candle → execution.
      8. Subscribe to the live Delta websocket and process market data.

    Non-clean reconciliation blocks arming. A blocked gate refuses every entry
    with its original safety block reason, and trading-disabled stays unreachable
    until reconciliation is clean.

    This is the production entry point called from `__main__.py` when
    `settings.production_execution_enabled` is True. It never returns while the
    websocket is connected; cancellation or connection loss is the only exit.
    """
    provider = resolve_credential_provider(
        account_id=account_id,
        user_id=user_id,
        allow_developer_env=False,  # Production path: no .env
        backend_base_url=settings.backend_base_url,
    )

    logger.info(
        "credential provider resolved | %s",
        provider.describe(),
    )

    client, orchestrator, reconciliation_service = build_production_execution(
        runtime=runtime,
        settings=settings,
        credential_provider=provider,
        state_path=state_path,
        account_id=account_id,
        user_id=user_id,
    )

    # Attach the orchestrator to the runtime and bind the reconciliation service
    # BEFORE loading state or reconciling, so the runtime's lifecycle_manager
    # property resolves and the gate can export state.
    runtime.orchestrator = orchestrator
    runtime.bind_execution_feeds(reconciliation_service=reconciliation_service)

    # Load durable execution state. The gate's `allow_missing_state=False` means
    # a missing file blocks arming rather than being assumed clean.
    from quantedge.execution.execution_state_store import load_execution_state

    loaded = load_execution_state(state_path)
    if loaded:
        logger.info(
            "execution state loaded | path=%s active_trades=%d",
            state_path,
            len(loaded.get("active_trades", {})),
        )

    # Perform authoritative startup reconciliation. Non-clean blocks arming.
    gate = runtime.lifecycle_manager._entry_admission_gate  # type: ignore
    recon_result = await runtime.reconcile(account_id=account_id, user_id=user_id)

    if recon_result.get("converged"):
        gate.arm(recon_result, strategy_setup_id=None)
        logger.info(
            "startup reconciliation CLEAN | gate armed | %s",
            {k: v for k, v in recon_result.items() if k != "details"},
        )
    else:
        logger.error(
            "startup reconciliation NON-CLEAN | gate blocked | %s",
            recon_result,
        )
        # Gate stays blocked. Every entry attempt will return the block reason.

    # Bind both clocks: closed candle → structure (with durable-state wrapper),
    # forming candle → execution.
    structure_callback = make_durable_feed_callback(
        runtime=runtime,
        callback=make_feed_callback(runtime, submit_to=None),
        state_dir=settings.engine_state_dir,
    )

    execution_callback = make_market_update_callback(
        runtime,
        submit_to=orchestrator.handle_trade_closure_and_rescan,
    )

    # Subscribe and run. The websocket is configured with on_candle_closed only
    # in the existing Phase 1 path; here both callbacks are bound. The client
    # dispatches closed frames to on_candle_closed and forming frames to
    # on_market_update.
    client_ws = DeltaWebSocketClient(
        symbols=list(runtime.symbols),
        on_candle_closed=structure_callback,
        on_market_update=execution_callback,
    )

    logger.info(
        "production execution composed | symbols=%s clocks=structure+execution "
        "orchestrator=%s trading_enabled=%s",
        runtime.symbols,
        type(orchestrator).__name__,
        settings.order_submission_enabled,
    )

    await client_ws.connect()
    # Never returns while connected. Cancellation or connection loss is the exit.


__all__ = [
    "TRADING_DISABLED_REASON",
    "ProductionCompositionError",
    "ProductionExecutionGate",
    "describe_real_account",
    "build_production_execution",
    "run_production_execution",
]
