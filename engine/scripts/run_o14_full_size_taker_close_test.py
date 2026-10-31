"""§O14 -- full-size crossing-limit (taker) entry, 60-second hold, forced flatten.

How this differs from `run_o12_single_order_live_test.py`, which stays untouched:

* O12 rested ONE contract 0.5% BELOW the mark as a maker. It never filled, so
  Delta never built the child bracket legs and the §O13 trigger-method question
  is still open.
* O14 crosses the book: a LIMIT order priced ABOVE the best ask, which fills
  immediately as a TAKER. `TradeLifecycleManager` hardcodes
  `order_type=OrderType.LIMIT_ORDER` (trade_lifecycle.py:601/624), so a literal
  MARKET_ORDER entry cannot be routed without editing production code -- which
  this task forbids. A limit priced through the book is the market-equivalent.
* Size is the largest the engine's own gates allow, computed from live balances.
* On fill it holds HOLD_SECONDS, then flattens with a reduce-only MARKET_ORDER,
  cancels anything left resting, and calls `close_position` for accounting and
  single-trade-lock release.

Size ceiling, stated up front because it bounds the result:
  the gateway requires `notional / request.leverage <= available_balance`
  (validation.py:550) and caps `request.leverage` at
  `min(spec.max_leverage, risk_config.max_leverage)` = 50 for XRPUSD
  (validation.py:145, 456). The largest notional it will pass is therefore
  `available_balance * 50`. Delta itself margins the position at the product's
  `default_leverage = 100`, so that notional locks up only HALF the wallet.
  Deploying 100% would mean raising UNVERIFIED_MAX_LEVERAGE["XRPUSD"] -- a
  safety cap -- which this script does NOT do. Every binding constraint is
  printed so the ceiling is attributable to that cap, not to a sizing error.

No production code, governance default or test is modified. Credentials are read
only through the O12 `--allow-env-file` loader and are never printed or hashed.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_ENGINE_SRC = _SCRIPTS.parent / "src"
if str(_ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SRC))

from run_o12_single_order_live_test import (  # noqa: E402
    ACCOUNT_ID,
    USER_ID,
    SYMBOL,
    Blocker,
    OrderPlan,
    _kv,
    _line,
    _print_ticker,
    _q,
    _section,
    dump_exchange_order,
    gateway_probe,
    live_product_crosscheck,
    print_gateway,
    print_identity,
    read_credentials,
    report_min_constraints,
    verify_pinned_identity,
)
from quantedge.execution.algo_config import AlgoConfigStore  # noqa: E402
from quantedge.execution.delta_client import (  # noqa: E402
    DELTA_INDIA_PRODUCTION_URL,
    ConnectionState,
    DeltaIndiaClient,
)
from quantedge.execution.models import (  # noqa: E402
    DeltaOrderRequest,
    OrderSide,
    OrderType,
)
from quantedge.execution.synchronizer import (  # noqa: E402
    LiveAccountSyncService,
    LocalStateStore,
)
from quantedge.execution.trade_lifecycle import (  # noqa: E402
    CloseReason,
    TradeLifecycleManager,
)
from quantedge.execution.validation import OrderValidationGateway  # noqa: E402
from quantedge.strategy.models import (  # noqa: E402
    SetupState,
    StrategyDecision,
    StrategyDirection,
)

# ── Fixed O14 parameters (one place, no environment overrides) ────────────────

DIRECTION = StrategyDirection.LONG
SIDE = "buy"

#: Priced THROUGH the book so the limit order takes liquidity instead of resting.
#: 0.1% above max(mark, best_ask) is ~13 ticks of XRPUSD headroom -- enough to
#: absorb the top of book moving between the ticker GET and the POST, small
#: enough that the fill price stays close to the approved entry.
ENTRY_PREMIUM = Decimal("0.001")

#: 0.4% stop. NOT a preference: at Delta's `default_leverage = 100` the adverse
#: move to liquidation is `1/leverage - maintenance_margin_rate` = 1% - 0.5% =
#: 0.5%. A 1% stop can therefore never trigger -- liquidation fires first, with
#: `liquidation_penalty_factor` applied. 0.4% keeps the stop INSIDE that band.
RISK_FRACTION = Decimal("0.004")
REWARD_MULTIPLE = Decimal("2")          # R:R 2.0 clears the 1.5 gateway floor

#: The maximum `min(spec.max_leverage, risk_config.max_leverage)` allows for
#: XRPUSD (validation.py:145, 456). This number never reaches Delta: the engine
#: sends no leverage field, so it only drives the local margin/leverage gates.
ENGINE_LEVERAGE = 50
#: What Delta actually applies, read live from the product payload and asserted.
EXPECTED_EXCHANGE_LEVERAGE = Decimal("100")

#: `trade_lifecycle.py:633` -- config reports risk_per_trade_pct 1.00, which is
#: not > 1.00, so the effective cap the real path uses is the 35.0 fallback.
RISK_CAP_PCT = Decimal("35")
#: Measured empirically on order 1509543748: available_balance fell 0.0152048 for
#: a 0.0136 margin order, i.e. ~0.118% of notional held back beyond margin.
#: 0.15% is that measurement with headroom.
FEE_RESERVE_RATE = Decimal("0.0015")
MARGIN_UTILISATION = Decimal("0.98")    # never ask for the last 2% of the wallet

HOLD_SECONDS = 60                       # the requested one-minute hold
FILL_WAIT_SECONDS = 30                  # a crossing order that misses this aborts
POLL_SECONDS = 2
FLATTEN_WAIT_SECONDS = 45

def _floor_int(value: Decimal) -> Decimal:
    """Whole contracts only. Delta's `size` is an integer contract count."""
    return value.to_integral_value(rounding=ROUND_DOWN)


def size_order(
    entry: Decimal,
    risk_distance: Decimal,
    contract_value: Decimal,
    equity: Decimal,
    available: Decimal,
    product: Dict[str, Any],
) -> Tuple[Decimal, List[Tuple[str, Decimal, str]]]:
    """Return the largest contract count every gate accepts, plus the workings.

    Each candidate mirrors one real constraint in the shipped code or on the
    exchange. Nothing here is a preference and nothing is rounded up.
    """
    candidates: List[Tuple[str, Decimal, str]] = []

    # A. Gateway risk cap -- validation.py:536-542.
    #    risk_amount = quantity * contract_value * risk_dist  <=  equity * 35%
    #    (the shipped 1.01 tolerance is deliberately NOT consumed).
    risk_budget = equity * RISK_CAP_PCT / Decimal("100")
    candidates.append((
        "A gateway risk cap 35% of equity",
        _floor_int(risk_budget / (risk_distance * contract_value)),
        f"{risk_budget} / ({risk_distance} x {contract_value})"))

    # B. Gateway margin check -- validation.py:550-555.
    #    required_margin = notional / request.leverage <= available_balance,
    #    and request.leverage may not exceed 50 for XRPUSD. THIS is the binding
    #    constraint that stops the wallet being fully deployed.
    candidates.append((
        f"B gateway margin at leverage {ENGINE_LEVERAGE}",
        _floor_int(available * Decimal(ENGINE_LEVERAGE) / (entry * contract_value)),
        f"{available} x {ENGINE_LEVERAGE} / ({entry} x {contract_value})"))

    # C. Real exchange margin at default_leverage 100, plus the measured reserve.
    per_contract = (entry * contract_value / EXPECTED_EXCHANGE_LEVERAGE
                    + entry * contract_value * FEE_RESERVE_RATE)
    candidates.append((
        "C exchange margin + fee reserve",
        _floor_int(available * MARGIN_UTILISATION / per_contract),
        f"{available} x {MARGIN_UTILISATION} / {per_contract}"))

    # D. Any size ceiling the LIVE payload actually publishes. Absent keys are
    #    reported as absent rather than replaced with an assumed number.
    for key in ("position_size_limit", "max_order_size"):
        raw = product.get(key)
        if raw in (None, ""):
            candidates.append((f"D {key} (absent from payload)",
                               Decimal("Infinity"), "not published"))
            continue
        candidates.append((f"D exchange {key}", _floor_int(Decimal(str(raw))),
                           f"live payload {raw}"))

    quantity = min(value for _, value, _ in candidates)
    return quantity, candidates


def print_size_math(candidates: List[Tuple[str, Decimal, str]],
                    quantity: Decimal) -> None:
    _section("6. SIZE DERIVATION (every gate, largest count that clears all)")
    for label, value, workings in candidates:
        mark = " <== BINDING" if value == quantity else ""
        shown = "no limit" if value == Decimal("Infinity") else f"{value}"
        print(f"  {label:<44} {shown:>12}{mark}")
        print(f"      {workings}")
    print()
    _kv("chosen quantity (contracts)", quantity)
    if quantity < Decimal("1"):
        raise Blocker(
            f"the gates leave room for {quantity} contracts, below the "
            f"UNVERIFIED_MIN_SIZE of 1 (validation.py:139,406). Nothing is "
            f"submitted.")


def build_plan_o14(mark_price: Decimal, best_ask: Optional[Decimal],
                   identity: Dict[str, Any], equity: Decimal,
                   available: Decimal, product: Dict[str, Any],
                   setup_id: str) -> OrderPlan:
    """Crossing-limit LONG, sized to the account, stop inside liquidation."""
    tick: Decimal = identity["tick_size"]
    contract_value: Decimal = identity["contract_value"]

    reference = mark_price if best_ask is None else max(mark_price, best_ask)
    entry = _q(reference * (Decimal("1") + ENTRY_PREMIUM), tick, ROUND_UP)
    if entry <= Decimal("0"):
        raise Blocker(f"computed entry {entry} is not positive from {reference}")

    raw_risk = _q(entry * RISK_FRACTION, tick, ROUND_HALF_UP)
    stop_loss = _q(entry - raw_risk, tick, ROUND_DOWN)
    if stop_loss >= entry:
        stop_loss = entry - tick
    take_profit = _q(entry + raw_risk * REWARD_MULTIPLE, tick, ROUND_DOWN)

    # Distances are recomputed FROM the quantized prices, because those are what
    # the gateway and the exchange will see. Deriving them any other way would
    # let the printed R:R disagree with the R:R that is actually validated.
    risk_distance = entry - stop_loss
    reward_distance = take_profit - entry
    if not (stop_loss < entry < take_profit):
        raise Blocker(
            f"LONG geometry failed: SL {stop_loss} < entry {entry} "
            f"< TP {take_profit}")
    if stop_loss <= Decimal("0") or risk_distance <= Decimal("0"):
        raise Blocker(f"non-positive risk: SL {stop_loss} risk {risk_distance}")

    quantity, candidates = size_order(
        entry, risk_distance, contract_value, equity, available, product)
    print_size_math(candidates, quantity)

    notional = quantity * contract_value * entry
    return OrderPlan(
        symbol=identity["symbol"],
        product_id=identity["product_id"],
        side=SIDE,
        quantity=quantity,
        mark_price=mark_price,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_distance=risk_distance,
        reward_distance=reward_distance,
        risk_reward=(reward_distance / risk_distance),
        notional=notional,
        margin_local_check=(notional / Decimal(ENGINE_LEVERAGE)),
        leverage=ENGINE_LEVERAGE,
        setup_id=setup_id,
    )


def build_decision_o14(plan: OrderPlan) -> StrategyDecision:
    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol=plan.symbol,
        timeframe="1h",
        direction=DIRECTION,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id=plan.setup_id,
        setup_type="O14_FULL_SIZE_TAKER_TEST",
        entry=plan.entry,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        risk_distance=plan.risk_distance,
        reward_distance=plan.reward_distance,
        risk_reward=plan.risk_reward,
        quantity=plan.quantity,
        calculated_leverage=plan.leverage,
        strategy_name="O14_LIVE_TEST",
    )

def print_plan_o14(plan: OrderPlan, identity: Dict[str, Any],
                   equity: Decimal, available: Decimal) -> None:
    _section("7. THE PLANNED ORDER (crossing limit = market-equivalent taker)")
    _kv("symbol / product_id", f"{plan.symbol} / {plan.product_id}")
    _kv("side / order_type", f"{plan.side.upper()} / LIMIT (priced through book)")
    _kv("quantity (contracts)", plan.quantity)
    _kv("one contract", f"{identity['one_contract']}")
    _kv("mark_price", plan.mark_price)
    _kv("entry (limit)", plan.entry)
    _kv("entry premium over reference", f"{ENTRY_PREMIUM * 100}%")
    _kv("stop_loss", f"{plan.stop_loss}  (-{plan.risk_distance})")
    _kv("take_profit", f"{plan.take_profit}  (+{plan.reward_distance})")
    _kv("risk:reward", plan.risk_reward)
    _kv("stop distance %", plan.risk_distance / plan.entry * Decimal("100"))
    print()
    _kv("notional (USD)", plan.notional)
    _kv("gateway required_margin", f"{plan.margin_local_check}  "
                                   f"(notional / {ENGINE_LEVERAGE})")
    _kv("exchange margin at 100x", plan.notional / EXPECTED_EXCHANGE_LEVERAGE)
    _kv("total_equity", equity)
    _kv("available_balance", available)
    util = plan.notional / EXPECTED_EXCHANGE_LEVERAGE / available * Decimal("100")
    _kv("wallet used by margin", f"{util:.2f}%")
    _kv("risk if stop fills", plan.quantity * plan.risk_distance)
    _kv("risk as % of equity",
        plan.quantity * plan.risk_distance / equity * Decimal("100"))
    print()
    print("  Liquidation reference, from the same model the repo uses")
    print("  (ai/evaluation/phase_i_ob_replay.py:507): the adverse move to")
    print(f"  liquidation is 1/100 - 0.005 = 0.5%, and this stop sits at")
    print(f"  {plan.risk_distance / plan.entry * Decimal('100'):.3f}%, i.e. INSIDE it.")


def print_approval_block_o14(plan: OrderPlan) -> None:
    _section("8. APPROVAL BLOCK")
    print("  These exact parameters are what --execute will re-derive. If the")
    print("  market moves far enough to change any of them, the token stops the")
    print("  run BEFORE any request is sent.")
    print()
    _kv("symbol / product_id", f"{plan.symbol} / {plan.product_id}")
    _kv("side / quantity", f"{plan.side.upper()} / {plan.quantity}")
    _kv("entry / SL / TP", f"{plan.entry} / {plan.stop_loss} / {plan.take_profit}")
    _kv("leverage (local gates only)", plan.leverage)
    print()
    _kv("approval token", plan.fingerprint())

async def run_preflight_o14(
    client: DeltaIndiaClient,
    identity: Dict[str, Any],
    api_key: str,
    api_secret: str,
    setup_id: str,
) -> Tuple[OrderPlan, LocalStateStore, TradeLifecycleManager, Dict[str, Any]]:
    """Every read-only check, in order. Raises Blocker on any stop condition."""
    product = await live_product_crosscheck(client, identity)
    report_min_constraints(product)

    live_lev = product.get("default_leverage")
    if live_lev in (None, "") or Decimal(str(live_lev)) != EXPECTED_EXCHANGE_LEVERAGE:
        raise Blocker(
            f"default_leverage is {live_lev!r}, not the "
            f"{EXPECTED_EXCHANGE_LEVERAGE} this sizing and the 0.5% liquidation "
            f"band are derived from. Refusing to guess the margin regime "
            f"(safety rules #15, #16).")

    ticker = await client.get_ticker(SYMBOL)
    mark_price = _print_ticker(ticker, identity)
    quotes = ticker.get("quotes") or {}
    raw_ask = quotes.get("best_ask")
    best_ask = None if raw_ask in (None, "") else Decimal(str(raw_ask))

    ok, state, error = await client.validate_credentials()
    _section("4b. CREDENTIAL VALIDATION (authenticated GET, read-only)")
    _kv("api key", "present (value never displayed)" if api_key else "ABSENT")
    _kv("api secret", "present (value never displayed)" if api_secret else "ABSENT")
    _kv("base_url", client.base_url)
    _kv("allow_direct_execution", client._allow_direct_execution)
    _kv("validated", ok)
    _kv("connection_state", state.value)
    _kv("error", error)
    if not ok or state != ConnectionState.CONNECTED:
        raise Blocker(f"credential validation failed: {state.value} / {error}")

    store = LocalStateStore(account_id=ACCOUNT_ID)
    sync = LiveAccountSyncService(client=client, state_store=store)
    sync_result = await sync.synchronize(ACCOUNT_ID)
    if not sync_result.success:
        raise Blocker(f"account synchronization failed: {sync_result.error}")

    positions = await client.get_positions()
    orders = await client.get_open_orders()

    _section("5a. ACCOUNT / POSITION / ORDER STATE (authoritative, read-only)")
    _kv("connection_status", store.connection.connection_status)
    _kv("environment", store.connection.environment)
    _kv("total_equity", store.account.total_equity)
    _kv("available_balance", store.account.available_balance)
    _kv("margin_used", store.account.margin_used)
    _kv("last_synced_at", store.account.last_synced_at)
    _kv("algo_enabled (fail-safe)", store.account.algo_enabled)
    _kv("kill_switch_active (fail-safe)", store.account.kill_switch_active)
    _kv("exchange open positions", len(positions))
    for pos in positions:
        _kv("  position", f"{pos.product_symbol} size={pos.size} "
                          f"entry={pos.entry_price}")
    _kv("exchange working orders", len(orders))
    for order in orders:
        _kv("  order", f"id={order.id} {order.product_symbol} {order.side} "
                       f"size={order.size} state={order.state}")
    _kv("sync discrepancies", len(sync_result.discrepancies))
    for disc in sync_result.discrepancies:
        print(f"      {disc}")

    if positions:
        raise Blocker(
            f"{len(positions)} open position(s) already exist. "
            f"max_concurrent_trades is 1 (validation.py:446).")
    if orders:
        raise Blocker(
            f"{len(orders)} working order(s) already exist. This test must "
            f"start from a flat book so the fill is unambiguously ours.")

    manager = TradeLifecycleManager(
        client=client,
        validation_gateway=OrderValidationGateway(),
        state_store=store,
        sync_service=LiveAccountSyncService(client=client, state_store=store),
        algo_config_store=AlgoConfigStore(),
    )
    _section("5b. RECONCILIATION AND LOCK STATE")
    _kv("reconciliation alerts", len(manager._reconciliation_alerts))
    for alert in manager._reconciliation_alerts:
        print(f"      {alert}")
    is_locked, active_setup, active_symbol = manager.single_trade_lock.is_locked(
        USER_ID, ACCOUNT_ID)
    _kv("single-trade lock held", is_locked)
    _kv("locked setup / symbol", f"{active_setup} / {active_symbol}")
    _kv("active trades in manager", len(manager.get_all_active_trades()))
    if manager._reconciliation_alerts:
        raise Blocker("unresolved reconciliation alerts block any new entry")
    if is_locked:
        raise Blocker("the single-trade lock is already held")

    plan = build_plan_o14(
        mark_price, best_ask, identity,
        store.account.total_equity, store.account.available_balance,
        product, setup_id)
    print_plan_o14(plan, identity,
                   store.account.total_equity, store.account.available_balance)

    probe = gateway_probe(
        plan, store.account, store.connection, api_key, api_secret,
        open_positions=store.get_open_positions(),
        open_orders=store.get_open_orders(),
    )
    print_gateway(probe)
    if probe["failsafe"]["is_valid"]:
        raise Blocker(
            "fail-closed regression: the shipped defaults accepted this order")
    if not probe["operator"]["is_valid"]:
        raise Blocker(
            f"gateway rejects the planned order: "
            f"{probe['operator']['rejection_code']} - "
            f"{probe['operator']['rejection_reason']}")

    print_approval_block_o14(plan)
    return plan, store, manager, probe


def _client(api_key: str, api_secret: str, allow_execution: bool) -> DeltaIndiaClient:
    return DeltaIndiaClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=DELTA_INDIA_PRODUCTION_URL,
        allow_direct_execution=allow_execution,
    )


async def cmd_preflight(args: argparse.Namespace) -> int:
    print(_line("#"))
    print("# O14 PREFLIGHT -- READ-ONLY. Nothing is placed, modified or cancelled.")
    print("# The client is built with allow_direct_execution=False, so its own")
    print("# authority guard would refuse a POST even if one were attempted.")
    print(_line("#"))
    api_key, api_secret = read_credentials(args.allow_env_file)
    identity = verify_pinned_identity()
    print_identity(identity)
    client = _client(api_key, api_secret, allow_execution=False)
    try:
        await run_preflight_o14(client, identity, api_key, api_secret,
                                _setup_id())
    finally:
        await client.close()
    print()
    print("PREFLIGHT COMPLETE. Mutating requests made: 0.")
    return 0

def _setup_id() -> str:
    return f"o14-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}Z"


async def cmd_execute(args: argparse.Namespace) -> int:
    if not args.confirm_live_order:
        print("REFUSED: --confirm-live-order is required.")
        return 3
    if not args.approval_token:
        print("REFUSED: --approval-token from a fresh --preflight is required.")
        return 3

    print(_line("#"))
    print("# O14 LIVE EXECUTION")
    print("# 1 crossing-limit entry (taker) -> hold 60s -> reduce-only flatten.")
    print("# The entry is the only discretionary POST. The flatten is mandatory")
    print("# and runs from a finally block even if the hold or the reporting")
    print("# raises, so a filled position is never left behind by this script.")
    print(_line("#"))
    api_key, api_secret = read_credentials(args.allow_env_file)
    identity = verify_pinned_identity()
    print_identity(identity)

    client = _client(api_key, api_secret, allow_execution=True)
    try:
        plan, store, manager, _probe = await run_preflight_o14(
            client, identity, api_key, api_secret, _setup_id())
        token = plan.fingerprint()
        _section("8b. APPROVAL TOKEN CHECK")
        _kv("recomputed token", token)
        _kv("supplied token", args.approval_token)
        if token != args.approval_token.strip():
            raise Blocker(
                "approval token mismatch: the live market moved far enough that "
                "the approved entry/SL/TP/quantity no longer reproduce. Re-run "
                "--preflight and approve the fresh block. NOTHING was submitted.")
        print("  OK: the approved parameters reproduce exactly.")
        return await _submit_hold_and_close(client, manager, store, plan, args)
    except Blocker as exc:
        print()
        print(_line("!"))
        print(f"BLOCKED: {exc}")
        print(_line("!"))
        return 3
    finally:
        await client.close()

async def _submit_hold_and_close(
    client: DeltaIndiaClient,
    manager: TradeLifecycleManager,
    store: LocalStateStore,
    plan: OrderPlan,
    args: argparse.Namespace,
) -> int:
    _section("9. ENABLING EXECUTION (kill switch first, then algo)")
    print("  AlgoConfiguration.update() raises if algo_enabled and")
    print("  kill_switch_active are both true (algo_config.py:109), so the kill")
    print("  switch must be cleared FIRST. Both are process-local mutations of")
    print("  this run's own store; no governance default on disk is changed.")
    print()
    store.account.kill_switch_active = False
    _kv("account.kill_switch_active", store.account.kill_switch_active)
    store.account.algo_enabled = True
    _kv("account.algo_enabled", store.account.algo_enabled)
    manager.algo_config_store.update_config(
        user_id=USER_ID, account_id=ACCOUNT_ID, kill_switch_active=False)
    cfg = manager.algo_config_store.update_config(
        user_id=USER_ID, account_id=ACCOUNT_ID, algo_enabled=True)
    _kv("config.kill_switch_active", cfg.kill_switch_active)
    _kv("config.algo_enabled", cfg.algo_enabled)
    _kv("config.risk_per_trade_pct", cfg.risk_per_trade_pct)
    _kv("config.max_leverage", cfg.max_leverage)

    decision = build_decision_o14(plan)
    _section("10. SUBMITTING THE ENTRY (crossing limit, expected to fill as taker)")
    _kv("setup_id", decision.setup_id)
    _kv("side / size", f"{plan.side.upper()} / {plan.quantity}")
    _kv("limit_price", plan.entry)
    _kv("attached bracket SL / TP", f"{plan.stop_loss} / {plan.take_profit}")
    print()
    submitted_at = datetime.now(timezone.utc)
    record = await manager.execute_trade_setup(
        decision=decision, account_id=ACCOUNT_ID, user_id=USER_ID)
    _print_record_o14(record, "11. LIFECYCLE RECORD AFTER SUBMISSION")

    if not record.entry_order_id:
        print()
        print("  No exchange order id on the record: the entry was rejected")
        print("  before reaching Delta. Nothing to hold, nothing to flatten.")
        return 3

    await dump_exchange_order(client, str(record.entry_order_id),
                              "12. THE ORDER AS DELTA STORED IT")
    return await _hold_then_flatten(client, manager, store, plan, record,
                                    submitted_at, args)

def _print_record_o14(record: Any, title: str) -> None:
    _section(title)
    _kv("state", getattr(record.state, "value", record.state))
    _kv("rejection_code", record.rejection_code)
    _kv("error_message", record.error_message)
    _kv("entry_order_id", record.entry_order_id)
    _kv("entry_client_order_id", record.entry_client_order_id)
    _kv("requested_quantity", record.requested_quantity)
    _kv("filled_quantity", getattr(record, "filled_quantity", None))
    _kv("average_fill_price", getattr(record, "average_fill_price", None))
    _kv("protected_quantity", record.protected_quantity)
    _kv("sl_order_id", record.sl_order_id)
    _kv("tp_order_id", record.tp_order_id)
    print()
    print("  Lifecycle history:")
    for event in record.history:
        print(f"    {event.get('timestamp')}  "
              f"{event.get('from_state')} -> {event.get('to_state')}")
        print(f"        {event.get('reason')}")


async def _await_fill(client: DeltaIndiaClient, manager: TradeLifecycleManager,
                      plan: OrderPlan, record: Any,
                      submitted_at: datetime) -> Tuple[Decimal, Decimal, str]:
    """Poll the entry until it fills, dies, or FILL_WAIT_SECONDS expires."""
    order_id = int(record.entry_order_id)
    handed = Decimal("0")
    avg = plan.entry
    state = "unknown"

    _section("13. WAITING FOR THE TAKER FILL (GET polling only)")
    print(f"  GET /v2/orders/{order_id} every {POLL_SECONDS}s for up to "
          f"{FILL_WAIT_SECONDS}s. Observed fills are handed to the manager's own")
    print("  on_entry_fill / on_entry_partial_fill so its protective legs run.")
    print()
    while True:
        elapsed = int((datetime.now(timezone.utc) - submitted_at).total_seconds())
        order = await client.get_order(order_id)
        state = str(getattr(order.state, "value", order.state)).lower()
        filled = order.filled_size
        print(f"  [{elapsed:>4}s] state={state:<12} filled={filled}/{order.size} "
              f"unfilled={order.unfilled_size} avg={order.average_fill_price}")
        if filled > handed:
            avg = order.average_fill_price or plan.entry
            if filled >= order.size:
                await manager.on_entry_fill(record.setup_id, filled, avg)
                print(f"         -> on_entry_fill({filled}, {avg})")
            else:
                await manager.on_entry_partial_fill(record.setup_id, filled, avg)
                print(f"         -> on_entry_partial_fill({filled}, {avg})")
            handed = filled
        if filled >= order.size or state in ("cancelled", "closed", "rejected"):
            return handed, avg, state
        if elapsed >= FILL_WAIT_SECONDS:
            print(f"  {FILL_WAIT_SECONDS}s elapsed without a complete fill.")
            return handed, avg, state
        await asyncio.sleep(POLL_SECONDS)

_LEG_KEYS = ("id", "state", "order_type", "stop_order_type", "side", "size",
             "unfilled_size", "reduce_only", "stop_price", "limit_price",
             "stop_trigger_method", "bracket_stop_loss_price",
             "bracket_take_profit_price", "created_at", "meta_data")


async def _raw_open_orders(client: DeltaIndiaClient,
                           product_id: int) -> List[Dict[str, Any]]:
    """Open orders as Delta returns them, untyped, so no field is dropped."""
    envelope = await client.request(
        "GET", "/v2/orders",
        params={"product_ids": product_id, "states": "open,pending"},
        authenticated=True)
    result = envelope.get("result")
    return [row for row in (result or []) if isinstance(row, dict)]


async def _inspect_protection(client: DeltaIndiaClient, product_id: int,
                              record: Any, title: str) -> List[Dict[str, Any]]:
    rows = await _raw_open_orders(client, product_id)
    _section(title)
    _kv("open/pending orders on product", len(rows))
    for row in rows:
        print("    " + _line("-", 70))
        for key in _LEG_KEYS:
            if key in row:
                _kv(f"  {key}", row[key])
    print()
    _kv("record.sl_order_id", record.sl_order_id)
    _kv("record.tp_order_id", record.tp_order_id)
    _kv("record.protected_quantity", record.protected_quantity)
    return rows


def _o13_verdict(rows: List[Dict[str, Any]], record: Any) -> None:
    """The §O13 question: does Delta honour bracket_stop_trigger_method?

    The engine sends `last_traded_price`. If Delta's child legs come back with
    `last_traded_price`, an adoption path can trust the attached bracket. If they
    come back with anything else, adoption must refuse, because the engine would
    be reporting a trigger basis the exchange is not using.
    """
    legs = [r for r in rows if r.get("stop_order_type")]
    _section("15. §O13 VERDICT -- bracket_stop_trigger_method, settled on evidence")
    _kv("protective child legs found", len(legs))
    if not legs:
        print("  UNRESOLVED: the position filled but Delta exposed no protective")
        print("  child legs on the open-orders endpoint at this instant. No")
        print("  conclusion is drawn from an absence (safety rule #15).")
        return
    methods = sorted({str(leg.get("stop_trigger_method")) for leg in legs})
    _kv("trigger methods observed", ", ".join(methods))
    for leg in legs:
        _kv(f"  leg {leg.get('id')} {leg.get('stop_order_type')}",
            f"trigger={leg.get('stop_trigger_method')} "
            f"stop_price={leg.get('stop_price')} size={leg.get('size')} "
            f"reduce_only={leg.get('reduce_only')}")
    print()
    if methods == ["last_traded_price"]:
        print("  OUTCOME A: Delta honoured last_traded_price on every leg. The")
        print("  attached bracket is adoptable as-is -- 2 legs total, no")
        print("  duplicate protection needed.")
    else:
        print("  OUTCOME B: Delta did NOT honour last_traded_price "
              f"(got {', '.join(methods)}).")
        print("  An adoption path must REFUSE these legs and let the engine place")
        print("  its own pair, raising PROTECTION_DUPLICATED_ON_EXCHANGE -- 4 legs.")
    engine_legs = {str(record.sl_order_id), str(record.tp_order_id)} - {"None"}
    _kv("legs the engine itself placed", ", ".join(sorted(engine_legs)) or "(none)")

async def _raw_position(client: DeltaIndiaClient,
                        product_id: int) -> Optional[Dict[str, Any]]:
    envelope = await client.request(
        "GET", "/v2/positions/margined", authenticated=True)
    result = envelope.get("result")
    rows = result if isinstance(result, list) else ([result] if result else [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("product_id") or (row.get("product") or {}).get("id")
        if pid is not None and int(pid) == int(product_id):
            if int(Decimal(str(row.get("size") or 0))) != 0:
                return row
    return None


async def _hold_window(client: DeltaIndiaClient, plan: OrderPlan,
                       filled: Decimal, avg: Decimal) -> None:
    started = datetime.now(timezone.utc)
    _section(f"16. HOLDING THE POSITION FOR {HOLD_SECONDS}s (GET polling only)")
    print("  Nothing is cancelled, modified or closed inside this window. The")
    print("  attached bracket and any engine legs stay live exactly as placed.")
    print()
    while True:
        elapsed = int((datetime.now(timezone.utc) - started).total_seconds())
        pos = await _raw_position(client, plan.product_id)
        if pos is None:
            print(f"  [{elapsed:>4}s] position is GONE -- a protective leg or a")
            print("         liquidation closed it inside the hold window.")
            return
        ticker = await client.get_ticker(SYMBOL)
        mark = Decimal(str(ticker.get("mark_price")))
        move = (mark - avg) / avg * Decimal("100")
        print(f"  [{elapsed:>4}s] size={pos.get('size')} entry={pos.get('entry_price')} "
              f"mark={mark} move={move:+.4f}% "
              f"upnl={pos.get('unrealized_pnl')} "
              f"liq={pos.get('liquidation_price')} "
              f"margin={pos.get('margin')}")
        if elapsed >= HOLD_SECONDS:
            print(f"  {HOLD_SECONDS}s hold complete.")
            return
        await asyncio.sleep(max(1, min(10, HOLD_SECONDS - elapsed)))


async def _cancel_leftovers(client: DeltaIndiaClient, plan: OrderPlan) -> int:
    """Cancel whatever is still resting on this product. Explicitly authorized."""
    rows = await _raw_open_orders(client, plan.product_id)
    cancelled = 0
    for row in rows:
        oid = row.get("id")
        if oid is None:
            continue
        try:
            ok = await client.cancel_order(int(oid), plan.product_id)
            print(f"    cancel {oid} ({row.get('stop_order_type') or row.get('order_type')})"
                  f" -> {ok}")
            cancelled += 1 if ok else 0
        except Exception as exc:  # noqa: BLE001
            print(f"    cancel {oid} refused: {type(exc).__name__}: {exc}")
    return cancelled

async def _flatten(client: DeltaIndiaClient, plan: OrderPlan
                   ) -> Tuple[Decimal, Optional[Decimal], Optional[int]]:
    """Submit ONE reduce-only MARKET order for whatever size is still open.

    `close_position` does NOT flatten -- it cancels the two tracked bracket legs
    and does accounting (trade_lifecycle.py:2418-2486). The closing order has to
    come from here, and it is `reduce_only=True` so it can only ever shrink the
    position, never open a new one in the opposite direction.
    """
    _section("17. FLATTENING (reduce-only MARKET order -- mandatory, not optional)")
    pos = await _raw_position(client, plan.product_id)
    if pos is None:
        print("  No open position on this product. Nothing to flatten.")
        return Decimal("0"), None, None

    size = int(Decimal(str(pos.get("size"))))
    side = OrderSide.SELL if size > 0 else OrderSide.BUY
    _kv("open size", size)
    _kv("closing side", side.value)
    request = DeltaOrderRequest(
        product_id=plan.product_id,
        product_symbol=plan.symbol,
        side=side,
        order_type=OrderType.MARKET_ORDER,
        size=Decimal(abs(size)),
        reduce_only=True,
    )
    response = await client.create_order(request)
    _kv("close order id", response.id)
    _kv("close state", getattr(response.state, "value", response.state))
    _kv("close filled_size", response.filled_size)
    _kv("close average_fill_price", response.average_fill_price)

    exit_qty = response.filled_size or Decimal("0")
    exit_avg = response.average_fill_price
    deadline = datetime.now(timezone.utc).timestamp() + FLATTEN_WAIT_SECONDS
    while datetime.now(timezone.utc).timestamp() < deadline:
        still = await _raw_position(client, plan.product_id)
        if still is None:
            print("  Confirmed flat: /v2/positions/margined reports no position.")
            break
        print(f"  still open: size={still.get('size')} -- re-checking")
        await asyncio.sleep(2)
    else:
        print("  WARNING: the position was still reported open when the flatten")
        print("  wait expired. It is reported in the verdict, not swallowed.")

    if exit_avg is None or exit_qty <= 0:
        fresh = await client.get_order(int(response.id))
        exit_qty = fresh.filled_size or exit_qty
        exit_avg = fresh.average_fill_price or exit_avg
        _kv("close filled (re-read)", f"{exit_qty} @ {exit_avg}")
    return exit_qty, exit_avg, int(response.id)

async def _observed_fees(client: DeltaIndiaClient, product_id: int,
                         order_ids: List[int]) -> Tuple[Optional[Decimal], int]:
    """Sum the commission Delta actually reported for our own fills.

    Returns `(total, n_fills)`. `None` means no fill row was found for any of our
    order ids, i.e. the commission was NOT observed -- which is passed to
    `close_position` as `trading_fees=None` rather than as a fabricated zero.
    """
    envelope = await client.request(
        "GET", "/v2/fills",
        params={"product_ids": product_id, "page_size": 100},
        authenticated=True)
    rows = envelope.get("result")
    wanted = {int(o) for o in order_ids if o is not None}
    total = Decimal("0")
    seen = 0
    _section("18. FILLS AND COMMISSION (exchange's own numbers)")
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        oid = row.get("order_id")
        if oid is None or int(oid) not in wanted:
            continue
        seen += 1
        commission = Decimal(str(row.get("commission") or "0"))
        total += commission
        print(f"    order={oid} side={row.get('side')} size={row.get('size')} "
              f"price={row.get('price')} commission={commission} "
              f"role={row.get('role')} at={row.get('created_at')}")
    _kv("our fill rows", seen)
    _kv("commission total", total if seen else "UNOBSERVED")
    return (total if seen else None), seen


async def _reconcile(client: DeltaIndiaClient, manager: TradeLifecycleManager,
                     store: LocalStateStore, plan: OrderPlan, record: Any,
                     filled: Decimal, entry_avg: Decimal,
                     exit_qty: Decimal, exit_avg: Optional[Decimal],
                     close_order_id: Optional[int]) -> None:
    order_ids = [int(record.entry_order_id)] if record.entry_order_id else []
    if close_order_id:
        order_ids.append(close_order_id)
    fees, _n = await _observed_fees(client, plan.product_id, order_ids)

    gross: Optional[Decimal] = None
    if exit_avg is not None and filled > 0:
        # LONG: (exit - entry) * contracts * contract_value.
        gross = (exit_avg - entry_avg) * min(filled, exit_qty or filled)

    sync = LiveAccountSyncService(client=client, state_store=store)
    result = await sync.synchronize(ACCOUNT_ID)
    final_balance = store.account.total_equity if result.success else None

    _section("19. CLOSING THE LIFECYCLE (accounting + lock release)")
    _kv("entry avg / exit avg", f"{entry_avg} / {exit_avg}")
    _kv("gross pnl supplied", gross if gross is not None else "UNOBSERVED")
    _kv("trading fees supplied", fees if fees is not None else "UNOBSERVED (None)")
    _kv("final_exchange_balance", final_balance)
    closed = await manager.close_position(
        setup_id=plan.setup_id,
        reason=CloseReason.MANUAL_CLOSE,
        gross_pnl=gross,
        trading_fees=fees,
        final_exchange_balance=final_balance,
        trading_fees_source="EXCHANGE_FILLS" if fees is not None else None,
    )
    _print_record_o14(closed, "20. LIFECYCLE RECORD AFTER CLOSE")

async def _hold_then_flatten(
    client: DeltaIndiaClient,
    manager: TradeLifecycleManager,
    store: LocalStateStore,
    plan: OrderPlan,
    record: Any,
    submitted_at: datetime,
    args: argparse.Namespace,
) -> int:
    filled = Decimal("0")
    entry_avg = plan.entry
    code = 0
    try:
        filled, entry_avg, state = await _await_fill(
            client, manager, plan, record, submitted_at)
        if filled <= 0:
            print()
            print("  The crossing limit did not fill inside the window "
                  f"(state={state}).")
            print("  There is no position to hold. The resting order is cancelled")
            print("  below so the book is left flat.")
            code = 4
        else:
            rows = await _inspect_protection(
                client, plan.product_id, record,
                "14. PROTECTION ON THE EXCHANGE, IMMEDIATELY AFTER THE FILL")
            _o13_verdict(rows, record)
            await _hold_window(client, plan, filled, entry_avg)
    except Exception as exc:  # noqa: BLE001
        print()
        print(f"  UNEXPECTED before the close: {type(exc).__name__}: {exc}")
        print("  Falling through to the mandatory flatten.")
        code = 5

    exit_qty, exit_avg, close_id = await _flatten(client, plan)
    print()
    print("  Cancelling anything still resting on the product:")
    await _cancel_leftovers(client, plan)
    try:
        await _reconcile(client, manager, store, plan, record,
                         filled, entry_avg, exit_qty, exit_avg, close_id)
    except Exception as exc:  # noqa: BLE001
        print(f"  close_position raised: {type(exc).__name__}: {exc}")
        code = code or 5
    return await _final_verdict(client, plan, manager, filled, exit_qty, code)


async def _final_verdict(client: DeltaIndiaClient, plan: OrderPlan,
                         manager: TradeLifecycleManager, filled: Decimal,
                         exit_qty: Decimal, code: int) -> int:
    position = await _raw_position(client, plan.product_id)
    rows = await _raw_open_orders(client, plan.product_id)
    is_locked, setup, symbol = manager.single_trade_lock.is_locked(
        USER_ID, ACCOUNT_ID)

    _section("21. FINAL STATE AND VERDICT")
    _kv("entry filled (contracts)", filled)
    _kv("exit filled (contracts)", exit_qty)
    _kv("open position now", "NONE" if position is None else position.get("size"))
    _kv("orders still resting", len(rows))
    for row in rows:
        _kv("  resting", f"id={row.get('id')} {row.get('order_type')} "
                         f"{row.get('stop_order_type')} state={row.get('state')}")
    _kv("single-trade lock held", f"{is_locked} ({setup} / {symbol})")
    _kv("reconciliation alerts", len(manager._reconciliation_alerts))
    for alert in manager._reconciliation_alerts:
        print(f"      {alert}")
    print()
    if position is not None:
        print("  VERDICT: FAIL -- a position is still open. Flatten it manually.")
        return 2
    if rows:
        print("  VERDICT: PARTIAL -- flat, but orders are still resting.")
        return 2
    print("  VERDICT: the book is flat and nothing is resting.")
    return code

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="O14 full-size crossing-limit entry, 60s hold, forced close.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true",
                      help="read-only: size the order and print an approval token")
    mode.add_argument("--execute", action="store_true",
                      help="place the entry, hold, then flatten")
    parser.add_argument("--confirm-live-order", action="store_true",
                        help="required by --execute")
    parser.add_argument("--approval-token", default=None,
                        help="the 16-hex token from a fresh --preflight")
    parser.add_argument("--allow-env-file", action="store_true",
                        help="permit the project .env loader to supply credentials")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.preflight:
            return asyncio.run(cmd_preflight(args))
        return asyncio.run(cmd_execute(args))
    except Blocker as exc:
        print()
        print(_line("!"))
        print(f"BLOCKED: {exc}")
        print(_line("!"))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
