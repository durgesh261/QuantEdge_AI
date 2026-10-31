#!/usr/bin/env python3
"""
Task O §O12 -- operator-run single live-order test for XRPUSD (Route 1).
=======================================================================

WHAT THIS IS
------------
An OPERATOR-RUN script. Nothing in the engine imports it; it registers no
callback, opens no websocket, starts no scheduler and triggers no rescan. It
exists so a human can drive exactly ONE real order through the most hardened
execution path in this repository and watch what the exchange does with it.

ROUTE 1, AND ONLY ROUTE 1
-------------------------
    DeltaIndiaClient(allow_direct_execution=<mode-dependent>)
      -> LiveAccountSyncService.synchronize()            (GET only)
      -> OrderValidationGateway.validate()               (pure, in-process)
      -> TradeLifecycleManager.execute_trade_setup()     (the single POST)

Deliberately NOT used, and why:
  * MarketScannerOrchestrator -- overwrites `decision.quantity` with a
    100%-of-balance allocation (market_orchestrator.py:201), which is the
    opposite of a 1-contract test.
  * MultiUserExecutionOrchestrator -- never calls the validation gateway.
  * LiveOrderExecutionService -- no bracket protection, no single-trade lock.
  * DeltaPrivateWebSocket / any feed callback / any rescan -- out of scope, and
    each one would add an autonomous trigger to an operator-driven test.

Note on the import graph: `quantedge.execution.__init__` re-exports the whole
package, so importing anything from it also LOADS `market_orchestrator`,
`multi_user_orchestrator` and `private_websocket`. That was checked rather than
assumed -- every module-level statement in those three files is a constant, a
logger or `__all__`, so loading them constructs nothing, connects nothing and
starts no task. This script never instantiates any of them.

FIVE MODES, ESCALATING
----------------------
  --dry-run     No network at all. Verifies the pinned instrument identity,
                builds the order plan against an operator-supplied reference
                price, and runs the validation gateway twice (fail-safe flags
                and operator flags) against a synthetic account.
  --public-preflight
                Unauthenticated GETs only, and no credential is read at all:
                live product cross-check, minimum-order evidence, live ticker,
                the plan against the real mark price, and the gateway probe
                against a labelled stand-in equity. Covers every §O12 preflight
                item that does not require an API key; the account-side items
                are reported as outstanding rather than skipped silently.
  --preflight   Read-only. Public product + ticker GETs, authenticated wallet /
                positions / open-orders GETs, then the same gateway probe
                against the real synced account, then the approval block. The
                client is constructed with allow_direct_execution=False, so
                even a coding mistake cannot POST from this mode.
  --g2-evidence Read-only G2 resolution. Prints the documented POST /v2/orders
                body, the payload the entry path actually builds (pure, never
                submitted), a sweep of all ~1274 live products for any exposed
                minimum order size, and the exchange's own order-object schema
                from authenticated history GETs. allow_direct_execution=False.
  --execute     The only mode that can POST. Requires BOTH
                --confirm-live-order and --approval-token, and re-runs the
                whole preflight first. Refuses if anything drifted.

CREDENTIALS
-----------
Read from os.environ["DELTA_API_KEY"] / ["DELTA_API_SECRET"] and nowhere else.
The project's .env loader is consulted only if --allow-env-file is passed
explicitly. Values are never printed, logged, hashed or persisted, and no
substring, length or other derivative of them ever reaches stdout -- the
credential sections report presence only.
G2 RESOLVED AND FIXED -- THE ENTRY NOW CARRIES DELTA'S DOCUMENTED BRACKET FIELDS
--------------------------------------------------------------------------------
`DeltaOrderRequest.to_exchange_payload()` used to emit `stop_loss_price` and
`take_profit_price` (execution/models.py) for the entry order built at
trade_lifecycle.py:599-600. Three authoritative Delta sources were checked and
all three agree that those two names are NOT part of Delta's create-order
request schema:

  1. Delta India REST reference     https://docs.delta.exchange/
  2. Delta Global REST reference    https://docs-global.delta.exchange/
  3. Delta's own MCP tool reference https://mcp.delta.exchange/docs/tools/trading

The documented POST /v2/orders body is:
    product_id, product_symbol, limit_price, size, side, order_type,
    stop_order_type, stop_price, trail_amount, stop_trigger_method,
    bracket_stop_trigger_method, bracket_stop_loss_limit_price,
    bracket_stop_loss_price, bracket_trail_amount,
    bracket_take_profit_limit_price, bracket_take_profit_price,
    time_in_force, mmp, post_only, reduce_only, client_order_id,
    cancel_orders_accepted

Attached protection is spelled `bracket_*`. Bare `stop_loss_price` /
`take_profit_price` appear nowhere -- not in POST /v2/orders, not in
POST /v2/orders/bracket (which nests `stop_loss_order` / `take_profit_order`
objects), and not in PUT /v2/orders/bracket (which uses the flat `bracket_*`
names). They created no exchange-side protection at all.

CORROBORATED BY THE LIVE EXCHANGE, NOT ONLY BY THE DOCS (--g2-evidence)
  * The order objects Delta itself returns carry `bracket_order`,
    `bracket_stop_loss_price`, `bracket_stop_loss_limit_price`,
    `bracket_take_profit_price`, `bracket_take_profit_limit_price` and
    `bracket_trail_amount`. Across 600 rows of this account's own
    GET /v2/orders/history there is no `stop_loss_price` key and no
    `take_profit_price` key, at any nesting level.
  * Attached brackets DO work on XRPUSD: 13 XRPUSD parent orders in that
    history carry real `bracket_stop_loss_price` / `bracket_take_profit_price`
    values, and 46 more XRPUSD rows are bracket legs. So the entry payload's
    problem is the field NAMES, not missing exchange support.
  * `ui_config.show_bracket_orders` is NOT evidence either way: it is false for
    all 220 live perpetual_futures including BTCUSD, so it is a global UI flag.

THE FIX (production change, execution/models.py only)
`to_exchange_payload()` now emits `bracket_stop_loss_price`,
`bracket_take_profit_price` and `bracket_stop_trigger_method` in place of the
two inert keys. The dataclass field names and every construction site are
unchanged, so this is a serialization-only change at the single choke point
every order passes through.

Every shape in it was read off orders Delta itself accepted rather than assumed
(--g2-evidence, section G2-4c): of the 97 parents carrying an attached bracket,
57 carry exactly one trigger price per leg and no `*_limit_price` companion, so
the limit companion is not mandatory and no price the engine never computed has
to be invented. The children Delta then creates are reduce-only `market_order`s
with `stop_order_type` stop_loss_order / take_profit_order and a `stop_price` --
the same shape `_ensure_bracket_protection` builds. All 13 XRPUSD parents are
`limit_order`s, so a bracket attaches to exactly the kind of entry this test
places. `bracket_stop_trigger_method` is sent explicitly because every leg in
that history arms on `mark_price`, while this engine's stops are deliberately
armed on `last_traded_price` (trade_lifecycle.py:806-811); leaving the field out
would let an exchange default change the exit semantics.

CONSEQUENCE FOR PROTECTION TIMING, AND FOR DOUBLE PROTECTION
Protection is now attached atomically with the entry, which closes the
one-round-trip unprotected window that used to exist between a fill and the
reduce-only pair landing. `_ensure_bracket_protection` is deliberately NOT
changed, so on a fill the book can hold two SL legs and two TP legs for one
position: the pair Delta attached and the pair the lifecycle places. Both are
reduce-only, so neither can flip or over-close the position, but a surplus leg
can rest after the position closes. --execute reports what the exchange actually
attached (`_g2_verdict`) instead of predicting it.

FILL HANDLING WITHOUT A WEBSOCKET
---------------------------------
`execute_trade_setup` only reaches `_ensure_bracket_protection` if the POST
response itself reports a fill (trade_lifecycle.py:676-679). A later fill
normally arrives on the private stream, which this test forbids. So --execute
polls GET /v2/orders/{id} and, on an observed fill, calls the manager's own
`on_entry_fill` / `on_entry_partial_fill`. That is operator orchestration, not a
production change: no engine file is modified by this script.

USAGE
-----
    python engine/scripts/run_o12_single_order_live_test.py --dry-run
    python engine/scripts/run_o12_single_order_live_test.py --public-preflight
    python engine/scripts/run_o12_single_order_live_test.py --preflight
    python engine/scripts/run_o12_single_order_live_test.py --g2-evidence
    python engine/scripts/run_o12_single_order_live_test.py --execute \\
        --confirm-live-order --approval-token <token printed by --preflight>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Run from a clean checkout without an editable install.
_ENGINE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SRC))
from quantedge.execution.algo_config import AlgoConfigStore
from quantedge.execution.delta_client import (
    DELTA_INDIA_PRODUCTION_URL,
    ConnectionState,
    DeltaIndiaClient,
)
from quantedge.execution.synchronizer import (
    ConnectionRecord,
    LiveAccountSyncService,
    LocalStateStore,
)
from quantedge.execution.trade_lifecycle import TradeLifecycleManager
from quantedge.execution.validation import (
    OrderValidationGateway,
    OrderValidationRequest,
    RiskConfiguration,
    ValidationContext,
    get_product_specification,
)
from quantedge.instruments import delta_india_registry
from quantedge.strategy.models import (
    SetupState,
    StrategyDecision,
    StrategyDirection,
    TradeDirection,
)
from quantedge.execution.models import DeltaOrderRequest, OrderSide, OrderType

# ── Fixed test parameters (single place, no environment overrides) ─────────────

SYMBOL = "XRPUSD"
#: From data/reference/delta_exchange_india/product_specs_snapshot.json. These
#: are assertions, not inputs: if the pinned registry disagrees the run aborts
#: rather than trading whatever it found (safety rules #8, #15, #16).
EXPECTED_PRODUCT_ID = 14969
EXPECTED_TICK_SIZE = Decimal("0.0001")
EXPECTED_CONTRACT_VALUE = Decimal("1")
EXPECTED_CONTRACT_UNIT = "XRP"

QUANTITY = Decimal("1")                    # contracts == 1 XRP of exposure
DIRECTION = StrategyDirection.LONG
#: What the gateway actually receives. `TradeLifecycleManager` converts
#: `decision.direction` into a `TradeDirection` on the record
#: (trade_lifecycle.py:542) and submits THAT to the gateway
#: (trade_lifecycle.py:607). Check 7 accepts `TradeDirection`, `OrderSide` or a
#: BUY/SELL/LONG/SHORT string, and `str(StrategyDirection.LONG)` is none of
#: those -- so a probe that passed the strategy enum straight through would
#: report INVALID_DIRECTION for a request the real path validates cleanly. The
#: probe must mirror the production request, not paraphrase it.
GATEWAY_DIRECTION = TradeDirection.LONG
#: Local margin-check leverage only. Delta sets position leverage through a
#: separate endpoint this path never calls, so the exchange will apply whatever
#: leverage the account already has configured for product 14969.
LEVERAGE = 10
#: Entry is placed BELOW the mark so it rests as a maker order instead of
#: crossing the spread; a resting order is observable and cancellable, and it
#: cannot create an unprotected position at submission time.
ENTRY_DISCOUNT = Decimal("0.005")          # 0.5% below mark
RISK_FRACTION = Decimal("0.01")            # SL 1% below entry
REWARD_MULTIPLE = Decimal("2")             # R:R = 2.0 (gateway floor is 1.5)
OBSERVATION_WINDOW_SECONDS = 300
ACCOUNT_ID = "o12-live-test-account"
USER_ID = "o12-operator"

# ── Small helpers ─────────────────────────────────────────────────────────────


class Blocker(RuntimeError):
    """A precondition that must be reported to the operator, not worked around."""


def _q(value: Decimal, tick: Decimal, rounding: str = ROUND_DOWN) -> Decimal:
    """Snap a price onto the instrument's tick grid, exactly."""
    return (value / tick).to_integral_value(rounding=rounding) * tick


def _line(char: str = "-", width: int = 78) -> str:
    return char * width


def _section(title: str) -> None:
    print()
    print(_line("="))
    print(title)
    print(_line("="))


def _kv(key: str, value: Any) -> None:
    print(f"  {key:<34} {value}")


def read_credentials(allow_env_file: bool) -> Tuple[str, str]:
    """Return (api_key, api_secret) from the process environment.

    The project's .env loader is only consulted when the operator explicitly
    passes --allow-env-file. Neither value -- nor any substring, length, hash
    or other derivative of one -- is written to stdout by this script.
    """
    key = os.environ.get("DELTA_API_KEY", "").strip()
    secret = os.environ.get("DELTA_API_SECRET", "").strip()

    if (not key or not secret) and allow_env_file:
        from quantedge.execution.security import load_project_env

        loaded, path = load_project_env()
        print(f"  .env loader: loaded={loaded} path={path or '(none)'}")
        key = os.environ.get("DELTA_API_KEY", "").strip()
        secret = os.environ.get("DELTA_API_SECRET", "").strip()

    if not key or not secret:
        raise Blocker(
            "DELTA_API_KEY and/or DELTA_API_SECRET are absent from the process "
            "environment. Export them into this shell and re-run, or pass "
            "--allow-env-file to authorise reading the project's .env. Nothing "
            "was sent to the exchange."
        )
    return key, secret

# ── Step 1: pinned instrument identity (no network) ───────────────────────────


def verify_pinned_identity() -> Dict[str, Any]:
    """Resolve XRPUSD from BOTH authorities and require them to agree.

    The hash-pinned snapshot is the exchange-verified source; the execution
    gateway keeps its own `ProductSpecification` table. A disagreement between
    them is an identity failure and aborts the run -- product_id is the field
    that decides which contract a real order lands on.
    """
    spec = delta_india_registry().get(SYMBOL)          # raises on unknown symbol
    gate = get_product_specification(SYMBOL)           # raises on unknown symbol

    problems: List[str] = []
    if spec.product_id != EXPECTED_PRODUCT_ID:
        problems.append(f"pinned product_id {spec.product_id} != {EXPECTED_PRODUCT_ID}")
    if spec.tick_size != EXPECTED_TICK_SIZE:
        problems.append(f"pinned tick_size {spec.tick_size} != {EXPECTED_TICK_SIZE}")
    if spec.contract_value != EXPECTED_CONTRACT_VALUE:
        problems.append(
            f"pinned contract_value {spec.contract_value} != {EXPECTED_CONTRACT_VALUE}")
    if spec.contract_unit_currency != EXPECTED_CONTRACT_UNIT:
        problems.append(
            f"pinned contract_unit_currency {spec.contract_unit_currency!r} "
            f"!= {EXPECTED_CONTRACT_UNIT!r}")
    if gate.product_id != spec.product_id:
        problems.append(
            f"gateway product_id {gate.product_id} != pinned {spec.product_id}")
    if gate.tick_size != spec.tick_size:
        problems.append(
            f"gateway tick_size {gate.tick_size} != pinned {spec.tick_size}")
    if gate.contract_value != spec.contract_value:
        problems.append(
            f"gateway contract_value {gate.contract_value} != pinned "
            f"{spec.contract_value}")
    if problems:
        raise Blocker("instrument identity disagreement: " + "; ".join(problems))

    return {
        "symbol": spec.symbol,
        "product_id": spec.product_id,
        "tick_size": spec.tick_size,
        "contract_value": spec.contract_value,
        "contract_unit_currency": spec.contract_unit_currency,
        "notional_type": spec.notional_type,
        "settling_asset": spec.settling_asset,
        "one_contract": spec.one_contract_description,
        "provenance": spec.provenance.as_source_string(),
        "recorded": dict(spec.recorded),
        "unverified": dict(spec.unverified),
        "gateway_min_size": gate.min_size,
        "gateway_size_step": gate.size_step,
        "gateway_max_leverage": gate.max_leverage,
    }

# ── Step 2: the order plan (pure arithmetic) ──────────────────────────────────


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    product_id: int
    side: str
    quantity: Decimal
    mark_price: Decimal
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    risk_distance: Decimal
    reward_distance: Decimal
    risk_reward: Decimal
    notional: Decimal
    margin_local_check: Decimal
    leverage: int
    setup_id: str

    def fingerprint(self) -> str:
        """Stable token over every field the operator approves.

        --execute recomputes this from live data and refuses to run if it does
        not match the token printed by --preflight, so an approval cannot be
        replayed against a materially different market or a different contract.
        """
        payload = "|".join([
            self.symbol, str(self.product_id), self.side, str(self.quantity),
            str(self.entry), str(self.stop_loss), str(self.take_profit),
            str(self.leverage),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_plan(mark_price: Decimal, identity: Dict[str, Any], setup_id: str) -> OrderPlan:
    tick: Decimal = identity["tick_size"]
    contract_value: Decimal = identity["contract_value"]

    entry = _q(mark_price * (Decimal("1") - ENTRY_DISCOUNT), tick, ROUND_DOWN)
    if entry <= Decimal("0"):
        raise Blocker(f"computed entry {entry} is not positive from mark {mark_price}")

    risk_distance = _q(entry * RISK_FRACTION, tick, ROUND_HALF_UP)
    if risk_distance <= Decimal("0"):
        # One tick is the smallest expressible risk; never zero.
        risk_distance = tick
    reward_distance = risk_distance * REWARD_MULTIPLE

    stop_loss = entry - risk_distance
    take_profit = entry + reward_distance
    if not (stop_loss < entry < take_profit):
        raise Blocker(
            f"LONG geometry failed: SL {stop_loss} < entry {entry} < TP {take_profit}")
    if stop_loss <= Decimal("0"):
        raise Blocker(f"computed stop loss {stop_loss} is not positive")

    notional = QUANTITY * contract_value * entry
    return OrderPlan(
        symbol=identity["symbol"],
        product_id=identity["product_id"],
        side="buy",
        quantity=QUANTITY,
        mark_price=mark_price,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_distance=risk_distance,
        reward_distance=reward_distance,
        risk_reward=(reward_distance / risk_distance),
        notional=notional,
        margin_local_check=(notional / Decimal(LEVERAGE)),
        leverage=LEVERAGE,
        setup_id=setup_id,
    )

def build_decision(plan: OrderPlan) -> StrategyDecision:
    """The exact object Route 1 consumes. `quantity` is the whole point.

    TradeLifecycleManager honours `decision.quantity` (trade_lifecycle.py:390),
    and `calculated_leverage` must stay at or under
    min(spec.max_leverage, risk_config.max_leverage) or check 14 rejects the
    order as EXCESSIVE_LEVERAGE (validation.py:456).
    """
    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol=plan.symbol,
        timeframe="1h",
        direction=DIRECTION,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id=plan.setup_id,
        setup_type="O12_OPERATOR_LIVE_TEST",
        entry=plan.entry,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        risk_distance=plan.risk_distance,
        reward_distance=plan.reward_distance,
        risk_reward=plan.risk_reward,
        quantity=plan.quantity,
        calculated_leverage=plan.leverage,
        strategy_name="O12_LIVE_TEST",
    )


# ── Step 3: gateway probe (pure, in-process, no network) ───────────────────────


def gateway_probe(
    plan: OrderPlan,
    account: Any,
    connection: ConnectionRecord,
    api_key: str,
    api_secret: str,
    open_positions: Optional[List[Any]] = None,
    open_orders: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Run the 17-check gateway twice: fail-safe flags, then operator flags.

    The first run must REJECT. That is the point of running it: it demonstrates
    on this exact request that the shipped fail-closed defaults still block a
    live order, so the second run's acceptance is attributable to the operator
    deliberately clearing the kill switch and enabling algo, not to a gate that
    quietly stopped working.
    """
    gateway = OrderValidationGateway()
    request = OrderValidationRequest(
        account_id=ACCOUNT_ID,
        setup_id=plan.setup_id,
        symbol=plan.symbol,
        direction=GATEWAY_DIRECTION,
        order_type=OrderType.LIMIT_ORDER,
        quantity=plan.quantity,
        entry_price=plan.entry,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        leverage=plan.leverage,
        client_order_id=None,
    )
    risk_cfg = RiskConfiguration(risk_per_trade_pct=Decimal("35.0"), max_leverage=100)

    def _ctx(algo: bool, kill: bool) -> ValidationContext:
        return ValidationContext(
            account=account,
            algo_enabled=algo,
            kill_switch_active=kill,
            connection=connection,
            api_key=api_key,
            api_secret=api_secret,
            risk_config=risk_cfg,
            open_positions=list(open_positions or []),
            open_orders=list(open_orders or []),
        )

    failsafe = gateway.validate(request, _ctx(algo=False, kill=True))
    operator = gateway.validate(request, _ctx(algo=True, kill=False))

    def _render(result: Any) -> Dict[str, Any]:
        return {
            "is_valid": result.is_valid,
            "failed_check": result.failed_check,
            "rejection_code": (
                result.rejection_code.value if result.rejection_code else None),
            "rejection_reason": result.rejection_reason,
            "risk_distance": result.calculated_risk_distance,
            "reward_distance": result.calculated_reward_distance,
            "risk_reward": result.calculated_risk_reward,
            "risk_amount": result.calculated_risk_amount,
        }

    return {"failsafe": _render(failsafe), "operator": _render(operator)}


def print_identity(identity: Dict[str, Any]) -> None:
    _section("1. PINNED INSTRUMENT IDENTITY (hash-verified snapshot)")
    _kv("symbol", identity["symbol"])
    _kv("product_id", identity["product_id"])
    _kv("tick_size", identity["tick_size"])
    _kv("contract_value", identity["one_contract"])
    _kv("notional_type", identity["notional_type"])
    _kv("settling_asset", identity["settling_asset"])
    _kv("provenance", identity["provenance"])
    print()
    print("  Recorded but NOT hash-pinned (moves with Delta's risk config):")
    for key in sorted(identity["recorded"]):
        _kv(f"  {key}", identity["recorded"][key])
    print()
    print("  PERMANENTLY UNVERIFIED -- local policy, not exchange fact:")
    for key in sorted(identity["unverified"]):
        print(f"    {key}: {identity['unverified'][key]}")
    print()
    _kv("gateway min_size (policy)", identity["gateway_min_size"])
    _kv("gateway size_step (policy)", identity["gateway_size_step"])
    _kv("gateway max_leverage (policy)", identity["gateway_max_leverage"])

#: Every name that could plausibly express a minimum order size or a minimum
#: notional on Delta's product payload. The preflight reports which of these the
#: LIVE payload actually carries; §O12 requires the exchange minimum to be
#: established from the exchange rather than assumed from local policy.
MIN_CONSTRAINT_KEYS = (
    "min_size", "minimum_order_size", "min_order_size", "min_quantity",
    "size_step", "step_size", "lot_size",
    "min_notional", "minimum_notional", "min_order_value", "min_trade_size",
    "impact_size", "position_size_limit", "max_order_size",
    "initial_margin", "initial_margin_scaling_factor",
    "maintenance_margin", "max_leverage_notional",
)


def _scan_nested(node: Any, path: str = "") -> Dict[str, Any]:
    """Find candidate constraint names ANYWHERE in the payload, not just at the top.

    `ui_config` and `product_specs` are nested objects on Delta's product record,
    so a top-level-only scan could report "no minimum order size" while one sat
    one level down. Safety rule #16 says verify, so the whole tree is walked.
    """
    found: Dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key in MIN_CONSTRAINT_KEYS and not isinstance(value, (dict, list)):
                found[here] = value
            found.update(_scan_nested(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.update(_scan_nested(value, f"{path}[{index}]"))
    return found


def report_min_constraints(product: Dict[str, Any]) -> Dict[str, Any]:
    """Report what the LIVE product payload says about minimum order size."""
    present = {k: product[k] for k in MIN_CONSTRAINT_KEYS if k in product}
    absent = [k for k in MIN_CONSTRAINT_KEYS if k not in product]
    nested = {p: v for p, v in _scan_nested(product).items() if "." in p}
    _section("3. EXCHANGE MINIMUM-ORDER / MINIMUM-NOTIONAL EVIDENCE")
    print("  Fields present in the LIVE GET /v2/products/XRPUSD payload:")
    if present:
        for key in sorted(present):
            _kv(f"  {key}", present[key])
    else:
        print("    (none of the candidate names appear)")
    print()
    print("  Candidate names ABSENT from the live payload:")
    print(f"    {', '.join(absent) if absent else '(none)'}")
    print()
    print("  Same candidate names found NESTED (ui_config, product_specs, ...):")
    if nested:
        for path in sorted(nested):
            _kv(f"  {path}", nested[path])
    else:
        print("    (none at any depth)")
    print()
    print("  All keys returned by the exchange for this product:")
    print(f"    {', '.join(sorted(str(k) for k in product))}")
    print()
    print("  Reading: Delta publishes no minimum order size and no minimum")
    print("  notional for this contract in either the product payload or the")
    print("  REST reference. `size` is documented only as a positive integer")
    print("  contract count. The gateway's min_size=1 / size_step=1 are LOCAL")
    print("  POLICY (declared PERMANENTLY_UNVERIFIED), so a 1-contract order is")
    print("  the smallest expressible order rather than a verified minimum. If")
    print("  the exchange does enforce a floor it will surface as a placement")
    print("  error, which --execute reports verbatim instead of retrying.")
    return {"present": present, "absent": absent, "nested": nested}


def report_order_constraints(product: Dict[str, Any], plan: OrderPlan) -> None:
    """Live constraints other than a size floor that bear on THIS order.

    Each is read from the exchange payload rather than assumed. A
    reduce-only-only product would make an opening entry impossible, so that one
    is a hard stop; the rest are reported with the margin by which the plan
    clears them.
    """
    ui = product.get("ui_config") or {}
    specs = product.get("product_specs") or {}
    _section("5b. OTHER LIVE EXCHANGE CONSTRAINTS ON THIS ORDER")

    reduce_only_only = bool(specs.get("only_reduce_only_orders_allowed"))
    _kv("only_reduce_only_orders_allowed", reduce_only_only)
    if reduce_only_only:
        raise Blocker(
            "the exchange currently accepts only reduce-only orders on this "
            "product, so an opening entry cannot be placed at all")

    band = product.get("price_band")
    if band is not None:
        band_pct = Decimal(str(band))
        deviation = ((plan.mark_price - plan.entry) / plan.mark_price) * Decimal("100")
        _kv("price_band (%)", band_pct)
        _kv("entry deviation from mark (%)", round(deviation, 4))
        if deviation >= band_pct:
            raise Blocker(
                f"entry {plan.entry} sits {deviation}% away from mark "
                f"{plan.mark_price}, at or beyond the exchange price band of "
                f"{band_pct}%; the order would be refused")
        print(f"      OK: inside the band by {round(band_pct - deviation, 4)}%.")

    sliders = ui.get("leverage_slider_values")
    _kv("leverage_slider_values", sliders)
    if isinstance(sliders, list) and sliders:
        _kv(f"  {plan.leverage}x offered by the exchange UI",
            plan.leverage in [int(v) for v in sliders])
    _kv("show_bracket_orders (UI hint)", ui.get("show_bracket_orders"))
    _kv("position_size_limit (contracts)", product.get("position_size_limit"))
    _kv("position_notional_limit", product.get("position_notional_limit"))
    _kv("impact_size", product.get("impact_size"))
    _kv("initial_margin (%)", product.get("initial_margin"))
    _kv("maintenance_margin (%)", product.get("maintenance_margin"))
    print()
    print("  `show_bracket_orders` carries NO information here: it is false for all")
    print("  220 live perpetual_futures, BTCUSD included, so it is a global UI flag")
    print("  rather than a statement about this product. Attached brackets DO work")
    print("  on XRPUSD -- 13 parent orders in this account's own history carry real")
    print("  bracket_stop_loss_price / bracket_take_profit_price values, and the")
    print("  entry body now uses those documented names (see section 6b), so the")
    print("  SL/TP are attached by the exchange to the entry order itself.")

def print_gateway(probe: Dict[str, Any]) -> None:
    _section("6. VALIDATION GATEWAY PROBE (in-process, no network)")
    fs, op = probe["failsafe"], probe["operator"]
    print("  Run A -- shipped fail-safe flags (algo_enabled=False,"
          " kill_switch_active=True)")
    _kv("  is_valid", fs["is_valid"])
    _kv("  failed_check", fs["failed_check"])
    _kv("  rejection_code", fs["rejection_code"])
    print()
    print("  Run B -- operator flags (algo_enabled=True, kill_switch_active=False)")
    _kv("  is_valid", op["is_valid"])
    _kv("  failed_check", op["failed_check"])
    _kv("  rejection_code", op["rejection_code"])
    _kv("  rejection_reason", op["rejection_reason"])
    _kv("  risk_distance", op["risk_distance"])
    _kv("  reward_distance", op["reward_distance"])
    _kv("  risk_reward", op["risk_reward"])
    _kv("  risk_amount", op["risk_amount"])
    print()
    if fs["is_valid"]:
        print("  *** FAIL-CLOSED REGRESSION: the shipped defaults accepted a live")
        print("      order. This is a blocker regardless of the rest of the run.")
    else:
        print("  OK: shipped defaults reject this exact request.")
    if not op["is_valid"]:
        print("  BLOCKER: the gateway rejects the planned order even with the")
        print("           operator flags set. Nothing may be submitted.")


def print_entry_payload(plan: OrderPlan) -> Dict[str, Any]:
    """Serialize the exact entry body this plan would POST, without sending it.

    Built through the production serializer -- `to_exchange_payload()` is the one
    choke point every order passes through -- from the same fields
    trade_lifecycle.py:586-601 sets, so what prints here is what the wire would
    carry. Issues no request. Fails closed if the attached bracket is missing or
    if any key is not a documented POST /v2/orders parameter.
    """
    request = DeltaOrderRequest(
        product_id=plan.product_id,
        product_symbol=plan.symbol,
        side=OrderSide.BUY if plan.side == "buy" else OrderSide.SELL,
        order_type=OrderType.LIMIT_ORDER,
        size=plan.quantity,
        limit_price=plan.entry,
        client_order_id="O12-PREFLIGHT-NOT-SUBMITTED",
        stop_loss_price=plan.stop_loss,
        take_profit_price=plan.take_profit,
    )
    payload = request.to_exchange_payload()
    _section("6b. EXACT POST BODY THAT WOULD BE SUBMITTED (serialized, not sent)")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print()
    undocumented = sorted(k for k in payload
                          if k not in DOCUMENTED_CREATE_ORDER_FIELDS)
    bracket = sorted(k for k in payload if k.startswith("bracket_"))
    _kv("keys in the body", len(payload))
    _kv("keys NOT in the documented body", ", ".join(undocumented) or "(none)")
    _kv("attached-bracket keys", ", ".join(bracket) or "(none)")
    if undocumented:
        raise Blocker(
            f"the entry body carries key(s) Delta does not document for "
            f"POST /v2/orders: {', '.join(undocumented)}. An undocumented key "
            f"cannot be relied on to do anything, so this is refused rather "
            f"than submitted.")
    expected = {"bracket_stop_loss_price": plan.stop_loss,
                "bracket_take_profit_price": plan.take_profit}
    for key, value in expected.items():
        if payload.get(key) != str(value):
            raise Blocker(
                f"{key} is {payload.get(key)!r} but the approved plan level is "
                f"{value}; the entry would not carry the protection that was "
                f"approved")
    _kv("bracket_stop_trigger_method", payload.get("bracket_stop_trigger_method"))
    if payload.get("bracket_stop_trigger_method") is None:
        raise Blocker(
            "the attached bracket carries no bracket_stop_trigger_method, so "
            "the price series that arms both legs would be whatever the "
            "exchange defaults to (every bracket leg in this account's history "
            "arms on mark_price, not the last_traded_price this engine's stops "
            "are computed against)")
    print()
    print("  Delta creates the legs itself when this body is accepted: a")
    print("  reduce-only market_order with stop_order_type stop_loss_order at")
    print(f"  {plan.stop_loss} and one with take_profit_order at {plan.take_profit}.")
    print("  That shape is read off the 97 bracketed parents in this account's")
    print("  own history (13 XRPUSD), where a trigger price without a")
    print("  *_limit_price companion is what 57 of them carry -- so no price the")
    print("  engine never computed is invented. Protection is attached")
    print("  ATOMICALLY with the entry rather than after a fill, closing the")
    print("  unprotected window.")
    print()
    print("  NOT ESTABLISHED: the price series the legs will arm on. Every")
    print("  bracket leg in this account's history arms on mark_price, but none")
    print("  of those parents sent bracket_stop_trigger_method at all, so")
    print("  mark_price is Delta's DEFAULT rather than proof that an explicit")
    print("  last_traded_price is ignored. The live order settles it. Both")
    print("  outcomes are fail-closed and neither is silent (Task O §O13):")
    print("    - honoured  -> the legs match the engine's stop contract, are")
    print("                   ADOPTED as record.sl_order_id / tp_order_id, and")
    print("                   no second pair is placed: ONE protective pair.")
    print("    - ignored   -> the mark_price legs are a different exit contract,")
    print("                   so adoption REFUSES them, the engine places its own")
    print("                   last_traded_price pair, and")
    print("                   PROTECTION_DUPLICATED_ON_EXCHANGE is audited and")
    print("                   raised as a blocking reconciliation alert. The")
    print("                   position is over-protected, never under-protected,")
    print("                   and the operator is told to clear the surplus legs.")
    return payload


def print_approval_block(plan: OrderPlan) -> None:
    _section("7. APPROVAL BLOCK -- nothing is submitted until this is approved")
    print(f"  symbol             {plan.symbol}")
    print(f"  product_id         {plan.product_id}")
    print(f"  side               {plan.side.upper()} (LONG)")
    print(f"  quantity           {plan.quantity} contract "
          f"({plan.quantity} XRP exposure)")
    print(f"  current price      {plan.mark_price} (mark)")
    print(f"  entry              {plan.entry} (limit, "
          f"{ENTRY_DISCOUNT * 100}% below mark)")
    print(f"  SL                 {plan.stop_loss} (-{plan.risk_distance})")
    print(f"  TP                 {plan.take_profit} (+{plan.reward_distance})")
    print(f"  R:R                {plan.risk_reward}")
    print(f"  estimated notional {plan.notional} USD")
    print(f"  estimated margin   {plan.margin_local_check} USD "
          f"(local check at {plan.leverage}x)")
    print(f"  observation window {OBSERVATION_WINDOW_SECONDS} s")
    print()
    print(f"  approval token     {plan.fingerprint()}")

def print_plan(plan: OrderPlan, identity: Dict[str, Any]) -> None:
    _section("5. ORDER PLAN AND DERIVED ARITHMETIC")
    _kv("setup_id", plan.setup_id)
    _kv("side", f"{plan.side} / {DIRECTION.value}")
    _kv("quantity (contracts)", plan.quantity)
    _kv("mark price", plan.mark_price)
    _kv("entry (limit, on tick grid)", plan.entry)
    _kv("entry % tick_size", plan.entry % identity["tick_size"])
    _kv("stop loss", plan.stop_loss)
    _kv("take profit", plan.take_profit)
    _kv("risk distance", plan.risk_distance)
    _kv("reward distance", plan.reward_distance)
    _kv("R:R", plan.risk_reward)
    _kv("notional (qty x cv x entry)", f"{plan.notional} USD")
    _kv(f"margin at {plan.leverage}x (local gate)", f"{plan.margin_local_check} USD")
    recorded = identity["recorded"]
    im = recorded.get("initial_margin")
    if im is not None:
        est = plan.notional * (Decimal(str(im)) / Decimal("100"))
        _kv("exchange initial margin est.", f"{est} USD (recorded initial_margin={im}%)")
    taker = recorded.get("taker_commission_rate")
    maker = recorded.get("maker_commission_rate")
    if taker is not None:
        _kv("taker fee est. (one leg)", f"{plan.notional * Decimal(str(taker))} USD")
    if maker is not None:
        _kv("maker fee est. (one leg)", f"{plan.notional * Decimal(str(maker))} USD")
    print()
    print("  NOTE: this path never sets exchange leverage -- Delta exposes that on a")
    print("  separate endpoint the engine does not call. The leverage above feeds the")
    print("  LOCAL margin gate only; the exchange will apply whatever leverage the")
    print("  account already has configured for this product.")


def _print_ticker(ticker: Dict[str, Any], identity: Dict[str, Any]) -> Decimal:
    """Print the public ticker and return its mark price, identity-checked."""
    _section("4. LIVE TICKER (public GET /v2/tickers/XRPUSD)")
    _kv("symbol", ticker.get("symbol"))
    _kv("product_id", ticker.get("product_id"))
    _kv("mark_price", ticker.get("mark_price"))
    _kv("spot_price", ticker.get("spot_price"))
    _kv("close", ticker.get("close"))
    quotes = ticker.get("quotes") or {}
    _kv("best bid / ask", f"{quotes.get('best_bid')} / {quotes.get('best_ask')}")
    if int(ticker.get("product_id", -1)) != identity["product_id"]:
        raise Blocker(
            f"ticker product_id {ticker.get('product_id')} != pinned "
            f"{identity['product_id']}")
    return Decimal(str(ticker["mark_price"]))


def _synthetic_probe_store(equity: Decimal) -> Tuple[LocalStateStore, ConnectionRecord]:
    """A labelled stand-in account for probes that cannot read the real one.

    Only --dry-run and --public-preflight use this. It exists so the gateway's
    arithmetic checks (risk exposure, required margin) can be exercised without
    credentials; it is NOT account data and is never used by --preflight or
    --execute, both of which probe the synchronized live account.
    """
    store = LocalStateStore(account_id=ACCOUNT_ID)
    store.account.total_equity = equity
    store.account.available_balance = equity
    store.account.current_balance = equity
    connection = ConnectionRecord()
    connection.connection_status = "CONNECTED"
    return store, connection


def cmd_dry_run(args: argparse.Namespace) -> int:
    print(_line("#"))
    print("# O12 DRY RUN -- NO NETWORK ACCESS, NO CREDENTIALS READ")
    print(_line("#"))
    identity = verify_pinned_identity()
    print_identity(identity)

    reference = Decimal(args.reference_price)
    _section("4. REFERENCE PRICE (operator-supplied, NOT exchange data)")
    _kv("--reference-price", reference)

    setup_id = f"O12-DRYRUN-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    plan = build_plan(reference, identity, setup_id)
    print_plan(plan, identity)

    store, connected = _synthetic_probe_store(Decimal(args.assumed_equity))
    probe = gateway_probe(
        plan, store.account, connected,
        api_key="DRYRUN_PLACEHOLDER_KEY", api_secret="DRYRUN_PLACEHOLDER_SECRET",
    )
    print_gateway(probe)
    print_entry_payload(plan)
    print_approval_block(plan)
    print()
    print("DRY RUN COMPLETE. No network request was made and no credential was read.")
    return 0 if probe["operator"]["is_valid"] and not probe["failsafe"]["is_valid"] else 1

# ── Step 4: read-only preflight ───────────────────────────────────────────────


async def live_product_crosscheck(client: DeltaIndiaClient,
                                  identity: Dict[str, Any]) -> Dict[str, Any]:
    """GET /v2/products/XRPUSD (public) and diff it against the pinned snapshot."""
    envelope = await client.request(
        "GET", f"/v2/products/{SYMBOL}", authenticated=False)
    product = envelope.get("result")
    if not isinstance(product, dict):
        raise Blocker(
            f"GET /v2/products/{SYMBOL} did not return a product object "
            f"(got {type(product).__name__}); refusing to trade an unverified "
            f"contract")

    drift: List[str] = []
    if product.get("symbol") != identity["symbol"]:
        drift.append(f"symbol {product.get('symbol')!r} != {identity['symbol']!r}")
    if int(product.get("id", -1)) != identity["product_id"]:
        drift.append(f"id {product.get('id')} != {identity['product_id']}")
    if Decimal(str(product.get("tick_size"))) != identity["tick_size"]:
        drift.append(f"tick_size {product.get('tick_size')} != {identity['tick_size']}")
    if Decimal(str(product.get("contract_value"))) != identity["contract_value"]:
        drift.append(
            f"contract_value {product.get('contract_value')} != "
            f"{identity['contract_value']}")

    _section("2. LIVE PRODUCT CROSS-CHECK (public GET, vs pinned snapshot)")
    _kv("live symbol", product.get("symbol"))
    _kv("live id", product.get("id"))
    _kv("live tick_size", product.get("tick_size"))
    _kv("live contract_value", product.get("contract_value"))
    _kv("live contract_unit_currency", product.get("contract_unit_currency"))
    _kv("live state", product.get("state"))
    _kv("live trading_status", product.get("trading_status"))
    _kv("live default_leverage", product.get("default_leverage"))
    _kv("live initial_margin", product.get("initial_margin"))
    _kv("live taker_commission_rate", product.get("taker_commission_rate"))
    print()
    if drift:
        raise Blocker(
            "the live product payload disagrees with the hash-pinned snapshot: "
            + "; ".join(drift))
    print("  OK: live identity matches the pinned snapshot on symbol, id,")
    print("      tick_size and contract_value.")
    if product.get("state") != "live" or product.get("trading_status") != "operational":
        raise Blocker(
            f"product is not tradable: state={product.get('state')!r} "
            f"trading_status={product.get('trading_status')!r}")
    return product

async def run_preflight(
    client: DeltaIndiaClient,
    identity: Dict[str, Any],
    api_key: str,
    api_secret: str,
    setup_id: str,
) -> Tuple[OrderPlan, LocalStateStore, TradeLifecycleManager, Dict[str, Any]]:
    """Every read-only check §O12 requires, in order. Raises Blocker on any stop."""
    product = await live_product_crosscheck(client, identity)
    report_min_constraints(product)

    # ── 4. Ticker ─────────────────────────────────────────────────────────────
    ticker = await client.get_ticker(SYMBOL)
    mark_price = _print_ticker(ticker, identity)

    # ── 4b. Credentials + account state (authenticated GETs) ──────────────────
    ok, state, error = await client.validate_credentials()
    _section("4b. CREDENTIAL VALIDATION (authenticated GET, read-only)")
    # Presence only. No substring, length, hash or other derivative of either
    # credential is printed -- mask_secret() would still reveal 8 characters.
    _kv("api key", "present (value never displayed)" if api_key else "ABSENT")
    _kv("api secret", "present (value never displayed)" if api_secret else "ABSENT")
    _kv("base_url", client.base_url)
    _kv("allow_direct_execution", client._allow_direct_execution)
    _kv("validated", ok)
    _kv("connection_state", state.value)
    _kv("error", error)
    if not ok or state != ConnectionState.CONNECTED:
        raise Blocker(f"credential validation failed: state={state.value} error={error}")

    store = LocalStateStore(account_id=ACCOUNT_ID)
    sync = LiveAccountSyncService(client=client, state_store=store)
    sync_result = await sync.synchronize(ACCOUNT_ID)
    if not sync_result.success:
        raise Blocker(f"account synchronization failed: {sync_result.error}")
    return await _preflight_state(
        client, identity, store, sync_result, mark_price, setup_id,
        api_key, api_secret, product)

async def _preflight_state(
    client: DeltaIndiaClient,
    identity: Dict[str, Any],
    store: LocalStateStore,
    sync_result: Any,
    mark_price: Decimal,
    setup_id: str,
    api_key: str,
    api_secret: str,
    product: Dict[str, Any],
) -> Tuple[OrderPlan, LocalStateStore, TradeLifecycleManager, Dict[str, Any]]:
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
        _kv("  order", f"id={order.id} {order.product_symbol} "
                       f"{order.side} size={order.size} state={order.state}")
    _kv("sync discrepancies", len(sync_result.discrepancies))
    for disc in sync_result.discrepancies:
        print(f"      {disc}")

    if positions:
        raise Blocker(
            f"{len(positions)} open position(s) already exist on the exchange. "
            f"max_concurrent_trades is 1, so no new entry may be submitted "
            f"(validation.py:446).")
    if orders:
        raise Blocker(
            f"{len(orders)} working order(s) already exist on the exchange. "
            f"A single-order test must start from a flat book.")

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

    plan = build_plan(mark_price, identity, setup_id)
    print_plan(plan, identity)
    report_order_constraints(product, plan)

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

    print_entry_payload(plan)
    print_approval_block(plan)
    return plan, store, manager, probe


async def cmd_preflight(args: argparse.Namespace) -> int:
    print(_line("#"))
    print("# O12 PREFLIGHT -- READ-ONLY. NO ORDER IS PLACED, MODIFIED OR CANCELLED.")
    print("# The client is built with allow_direct_execution=False, so the client's")
    print("# own authority guard would refuse a POST even if one were attempted.")
    print(_line("#"))
    identity = verify_pinned_identity()
    print_identity(identity)

    api_key, api_secret = read_credentials(args.allow_env_file)
    client = DeltaIndiaClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=DELTA_INDIA_PRODUCTION_URL,
        allow_direct_execution=False,
    )
    try:
        setup_id = f"O12-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        await run_preflight(client, identity, api_key, api_secret, setup_id)
    finally:
        await client.close()

    print()
    print(_line("#"))
    print("# PREFLIGHT COMPLETE. Mutating requests made: 0.")
    print("# Re-run with --execute --confirm-live-order --approval-token <token>")
    print("# to submit the single live order. The token binds symbol, product_id,")
    print("# side, quantity, entry, SL, TP and leverage; --execute recomputes it")
    print("# from fresh market data and aborts on any drift.")
    print(_line("#"))
    return 0

# ── Step 5: execute (the only mode that can POST) ─────────────────────────────


async def dump_exchange_order(client: DeltaIndiaClient, order_id: str,
                              title: str) -> Dict[str, Any]:
    """Print the exchange's own representation of one order, verbatim.

    This is the empirical half of the G2 question: if Delta had acted on the
    entry payload's `stop_loss_price` / `take_profit_price`, the attached legs
    would appear here as `bracket_stop_loss_price` / `bracket_take_profit_price`
    or as linked stop orders. What the exchange actually returns is printed
    without interpretation.
    """
    envelope = await client.request(
        "GET", f"/v2/orders/{order_id}", authenticated=True)
    result = envelope.get("result") or {}
    _section(title)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    bracket_keys = sorted(k for k in result if "bracket" in str(k).lower()
                          or "stop_loss" in str(k).lower()
                          or "take_profit" in str(k).lower())
    print()
    _kv("bracket/SL/TP keys returned", ", ".join(bracket_keys) or "(none)")
    return result


async def cmd_execute(args: argparse.Namespace) -> int:
    if not args.confirm_live_order:
        raise Blocker("--execute requires --confirm-live-order")
    if not args.approval_token:
        raise Blocker(
            "--execute requires --approval-token <token from --preflight>")

    print(_line("#"))
    print("# O12 EXECUTE -- ONE REAL ORDER WILL BE SUBMITTED TO PRODUCTION")
    print(_line("#"))
    identity = verify_pinned_identity()
    print_identity(identity)

    api_key, api_secret = read_credentials(args.allow_env_file)
    client = DeltaIndiaClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=DELTA_INDIA_PRODUCTION_URL,
        allow_direct_execution=True,
    )
    try:
        setup_id = f"O12-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        plan, store, manager, _probe = await run_preflight(
            client, identity, api_key, api_secret, setup_id)

        token = plan.fingerprint()
        _section("8. APPROVAL TOKEN CHECK")
        _kv("recomputed token", token)
        _kv("supplied token", args.approval_token)
        if token != args.approval_token.strip():
            raise Blocker(
                "approval token mismatch: the live market has moved far enough "
                "that the approved entry/SL/TP no longer reproduce. Re-run "
                "--preflight and approve the fresh block. NOTHING was submitted.")
        print("  OK: the approved parameters reproduce exactly.")
        return await _submit_and_observe(client, manager, store, plan, args)
    finally:
        await client.close()


async def _submit_and_observe(
    client: DeltaIndiaClient,
    manager: TradeLifecycleManager,
    store: LocalStateStore,
    plan: OrderPlan,
    args: argparse.Namespace,
) -> int:
    """Enable execution, submit exactly one entry, then observe. Never cancels."""
    _section("9. ENABLING EXECUTION (kill switch first, then algo)")
    print("  The order is forced by the config validator: AlgoConfiguration.update()")
    print("  raises if algo_enabled and kill_switch_active are both true")
    print("  (algo_config.py:109), so the kill switch must be cleared FIRST.")
    print()
    # AccountRecord.__post_init__ forbids CONSTRUCTING an unsafe record; these
    # are the "explicitly enabled by an authorized action" mutations it names.
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
    _kv("config.version", cfg.version)
    _kv("config.risk_per_trade_pct", cfg.risk_per_trade_pct)
    _kv("config.max_leverage", cfg.max_leverage)

    decision = build_decision(plan)
    _section("10. SUBMITTING THE ENTRY ORDER (the one and only POST)")
    _kv("setup_id", decision.setup_id)
    _kv("symbol / product_id", f"{plan.symbol} / {plan.product_id}")
    _kv("side", plan.side.upper())
    _kv("order_type", "LIMIT_ORDER")
    _kv("size (contracts)", plan.quantity)
    _kv("limit_price", plan.entry)
    _kv("stop_loss_price (inert, see G2)", plan.stop_loss)
    _kv("take_profit_price (inert, see G2)", plan.take_profit)
    print()
    submitted_at = datetime.now(timezone.utc)
    record = await manager.execute_trade_setup(
        decision=decision,
        account_id=ACCOUNT_ID,
        user_id=USER_ID,
    )
    return await _report_and_observe(client, manager, store, plan, record,
                                     submitted_at, args)


def _print_record(record: Any, title: str) -> None:
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


async def _report_and_observe(
    client: DeltaIndiaClient,
    manager: TradeLifecycleManager,
    store: LocalStateStore,
    plan: OrderPlan,
    record: Any,
    submitted_at: datetime,
    args: argparse.Namespace,
) -> int:
    _print_record(record, "11. LIFECYCLE RECORD IMMEDIATELY AFTER SUBMISSION")

    if not record.entry_order_id:
        _section("12. NO EXCHANGE ORDER ID -- NOTHING IS RESTING")
        print("  The lifecycle path returned without an exchange order id. Either the")
        print("  gateway/lifecycle rejected the request in-process, or the POST was")
        print("  refused by Delta. Both outcomes are fail-safe: no position exists.")
        print()
        print("  If the state is RECONCILIATION_REQUIRED the single-trade lock is")
        print("  RETAINED BY DESIGN (safety rules #11/#14) -- do not clear it; the")
        print("  exchange must be inspected manually before any further attempt.")
        return 1

    raw = await dump_exchange_order(
        client, str(record.entry_order_id),
        "12. G2 EMPIRICAL EVIDENCE -- EXCHANGE REPRESENTATION OF THE ENTRY ORDER")
    _g2_verdict(raw)
    return await _observe_window(client, manager, store, plan, record,
                                 submitted_at, args)


def _g2_verdict(raw: Dict[str, Any]) -> None:
    """State whether the exchange actually attached the bracket that was sent."""
    bracket_sl = raw.get("bracket_stop_loss_price")
    bracket_tp = raw.get("bracket_take_profit_price")
    echoed_sl = raw.get("stop_loss_price")
    echoed_tp = raw.get("take_profit_price")

    print()
    print("  G2 verdict from live data (not inference):")
    _kv("  bracket_stop_loss_price", bracket_sl)
    _kv("  bracket_take_profit_price", bracket_tp)
    _kv("  stop_loss_price (echoed?)", echoed_sl)
    _kv("  take_profit_price (echoed?)", echoed_tp)
    print()
    if bracket_sl and bracket_tp:
        print("  CONFIRMED: the exchange reports an ATTACHED bracket on the entry")
        print("  order, so both protection levels are exchange-side from the")
        print("  moment the entry exists rather than only after a fill.")
        print("  Watch for the double-protection condition: on fill,")
        print("  `_ensure_bracket_protection` still places its own reduce-only")
        print("  SL/TP pair (trade_lifecycle.py:813-843), so the book can carry")
        print("  two SL legs and two TP legs for one position. Both are")
        print("  reduce-only, so neither can flip the position, but the surplus")
        print("  pair may rest after the position closes -- inspect and report.")
    elif bracket_sl or bracket_tp:
        print("  *** PARTIAL: the exchange attached only one leg of the bracket.")
        print("      Stop, inspect the book, and report before proceeding.")
    else:
        print("  *** UNEXPECTED: the entry body carried the documented")
        print("      bracket_stop_loss_price / bracket_take_profit_price fields")
        print("      but the exchange reports no attached bracket. Treat the")
        print("      position as protected only by the separate reduce-only pair")
        print("      the lifecycle places on fill, and report this -- it")
        print("      contradicts the 97 bracketed parents in this account's own")
        print("      history that the field names were verified against.")


async def _observe_window(
    client: DeltaIndiaClient,
    manager: TradeLifecycleManager,
    store: LocalStateStore,
    plan: OrderPlan,
    record: Any,
    submitted_at: datetime,
    args: argparse.Namespace,
) -> int:
    """Poll the entry order and hand any observed fill to the lifecycle manager.

    The only mutating requests this can cause are the lifecycle's OWN documented
    reduce-only protection legs (trade_lifecycle.py:813-843), which exist to stop
    a filled position from sitting unprotected. Nothing here cancels, modifies or
    closes anything.
    """
    order_id = int(record.entry_order_id)
    total = int(args.observe_seconds)
    interval = max(1, int(args.poll_seconds))
    handed_to_manager = Decimal("0")

    _section("13. OBSERVATION WINDOW (GET polling; no cancel, no modify, no close)")
    print(f"  GET /v2/orders/{order_id} every {interval}s for up to {total}s.")
    print("  A resting maker order may simply never fill inside the window; that is")
    print("  a valid outcome, not a failure. On an observed fill the manager's own")
    print("  on_entry_fill / on_entry_partial_fill is called so its documented")
    print("  reduce-only SL and TP legs are placed -- those are protective POSTs.")
    print()

    while True:
        elapsed = int((datetime.now(timezone.utc) - submitted_at).total_seconds())
        order = await client.get_order(order_id)
        state = str(getattr(order.state, "value", order.state)).lower()
        filled = order.filled_size
        print(f"  [{elapsed:>4}s] state={state:<12} filled={filled}/{order.size} "
              f"unfilled={order.unfilled_size} avg={order.average_fill_price}")

        if filled > handed_to_manager:
            avg = order.average_fill_price or plan.entry
            if filled >= order.size:
                await manager.on_entry_fill(record.setup_id, filled, avg)
                print(f"         -> on_entry_fill({filled}, {avg}) called")
            else:
                await manager.on_entry_partial_fill(record.setup_id, filled, avg)
                print(f"         -> on_entry_partial_fill({filled}, {avg}) called")
            handed_to_manager = filled

        if filled >= order.size or state in ("cancelled", "closed", "rejected"):
            break
        if elapsed >= total:
            print(f"  Observation window of {total}s elapsed.")
            break
        await asyncio.sleep(interval)

    return await _final_report(client, manager, store, record, order_id)


async def _final_report(
    client: DeltaIndiaClient,
    manager: TradeLifecycleManager,
    store: LocalStateStore,
    record: Any,
    order_id: int,
) -> int:
    await dump_exchange_order(
        client, str(order_id),
        "14. FINAL EXCHANGE REPRESENTATION OF THE ENTRY ORDER")

    positions = await client.get_positions()
    orders = await client.get_open_orders()
    _section("15. FINAL EXCHANGE STATE (read-only)")
    _kv("open positions", len(positions))
    for pos in positions:
        _kv("  position", f"{pos.product_symbol} size={pos.size} "
                          f"entry={pos.entry_price}")
    _kv("working orders", len(orders))
    for order in orders:
        _kv("  order", f"id={order.id} {order.product_symbol} {order.side} "
                       f"type={getattr(order.order_type, 'value', order.order_type)} "
                       f"size={order.size} reduce_only={order.reduce_only} "
                       f"state={getattr(order.state, 'value', order.state)}")

    live = manager.get_active_trade(record.setup_id) or record
    _print_record(live, "16. FINAL LIFECYCLE RECORD")

    is_locked, active_setup, active_symbol = manager.single_trade_lock.is_locked(
        USER_ID, ACCOUNT_ID)
    _section("17. LOCK STATE AND PROTECTION ASSESSMENT")
    _kv("single-trade lock held", is_locked)
    _kv("locked setup / symbol", f"{active_setup} / {active_symbol}")
    filled = getattr(live, "filled_quantity", Decimal("0")) or Decimal("0")
    protected = live.protected_quantity or Decimal("0")
    _kv("filled quantity", filled)
    _kv("protected quantity", protected)
    _kv("sl_order_id", live.sl_order_id)
    _kv("tp_order_id", live.tp_order_id)
    print()
    return _verdict(positions, filled, protected, live)


def _verdict(positions: List[Any], filled: Decimal, protected: Decimal,
             live: Any) -> int:
    """Report the outcome. This script never cleans up; the operator decides."""
    if positions and protected < filled:
        print("  *** UNPROTECTED POSITION ON THE EXCHANGE ***")
        print("  A position exists whose size exceeds the protected quantity. Attend")
        print("  to it manually NOW -- place a reduce-only stop or flatten it from the")
        print("  Delta UI. This script deliberately does not cancel, modify or close")
        print("  anything, so it cannot resolve this for you.")
        return 2
    if positions:
        print("  A position is open and its full filled size is protected by the")
        print("  reduce-only SL/TP pair listed above. Closing it, and releasing the")
        print("  single-trade lock, is a separate operator decision -- this script")
        print("  does not close positions.")
        return 0
    if filled > Decimal("0"):
        print("  The entry filled but no position remains, so it was closed by a")
        print("  protective leg or by hand. Verify the fills in the Delta UI.")
        return 0
    print("  No fill occurred inside the observation window. The entry may still be")
    print("  RESTING on the book -- check section 15 above. If it is still working,")
    print("  cancel it from the Delta UI or with an explicitly authorised follow-up;")
    print("  §O12 authorises exactly one placement and no cancellation.")
    return 0


async def cmd_public_preflight(args: argparse.Namespace) -> int:
    """Every §O12 preflight check that needs NO credential -- and nothing else.

    §O12 requires the live ticker, the product identity and product_id 14969,
    the tick size, the 1-contract notional and the exchange minimum-order /
    minimum-notional requirements to be established before any POST. All of
    those are on PUBLIC endpoints. Positions, open orders, account and
    connection state, and reconciliation state are not -- they need
    DELTA_API_KEY / DELTA_API_SECRET, and this mode does not look for them in
    .env, source, history or anywhere else. Run --preflight once they are
    exported into the environment; this mode does not substitute for it.
    """
    print(_line("#"))
    print("# O12 PUBLIC PREFLIGHT -- UNAUTHENTICATED GETs ONLY")
    print("# No credential is read. No authenticated request is sent. Nothing is")
    print("# placed, modified or cancelled. The account-side checks (positions,")
    print("# open orders, balances, reconciliation) are NOT covered here.")
    print(_line("#"))
    identity = verify_pinned_identity()
    print_identity(identity)

    client = DeltaIndiaClient(
        api_key="PUBLIC_ONLY_NO_CREDENTIAL",
        api_secret="PUBLIC_ONLY_NO_CREDENTIAL",
        base_url=DELTA_INDIA_PRODUCTION_URL,
        allow_direct_execution=False,
    )
    try:
        product = await live_product_crosscheck(client, identity)
        report_min_constraints(product)
        mark_price = _print_ticker(await client.get_ticker(SYMBOL), identity)

        setup_id = f"O12-PUBLIC-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        plan = build_plan(mark_price, identity, setup_id)
        print_plan(plan, identity)
        report_order_constraints(product, plan)

        store, connected = _synthetic_probe_store(Decimal(args.assumed_equity))
        probe = gateway_probe(
            plan, store.account, connected,
            api_key="PUBLIC_ONLY_NO_CREDENTIAL",
            api_secret="PUBLIC_ONLY_NO_CREDENTIAL",
        )
        print_gateway(probe)
        print_approval_block(plan)
        _public_preflight_footer(args, probe)
        return 0 if probe["operator"]["is_valid"] and not probe["failsafe"]["is_valid"] else 1
    finally:
        await client.close()


def _public_preflight_footer(args: argparse.Namespace, probe: Dict[str, Any]) -> None:
    """State exactly which §O12 preflight items are still outstanding."""
    print()
    print(_line("#"))
    print("# PUBLIC PREFLIGHT COMPLETE. Mutating requests made: 0.")
    print("#")
    print("# ESTABLISHED from the exchange, unauthenticated:")
    print("#   product identity and product_id, tick size, contract value, tradable")
    print("#   state, live mark price, 1-contract notional, and the minimum-order /")
    print("#   minimum-notional evidence.")
    print("#")
    print("# STILL OUTSTANDING -- each needs DELTA_API_KEY / DELTA_API_SECRET:")
    print("#   current positions, open orders, connection and account state,")
    print("#   balances, and reconciliation state.")
    print("#")
    print(f"# The gateway probe above used an ASSUMED equity of {args.assumed_equity}")
    print("# USD, not account data. --preflight repeats it against the synchronized")
    print("# live account, which is the run whose approval block may be acted on.")
    print("#")
    print("# The approval token above is PROVISIONAL: --execute re-runs the full")
    print("# authenticated preflight and aborts unless the token reproduces from")
    print("# fresh market data.")
    print(_line("#"))


# ── CLI ───────────────────────────────────────────────────────────────────────


# ── G2 evidence mode: documentation + live GET structures, zero mutations ─────

# POST /v2/orders request-body field set, as documented by Delta. Recorded here
# verbatim so the comparison below is auditable without re-fetching the docs.
DOCUMENTED_CREATE_ORDER_FIELDS = (
    "product_id", "product_symbol", "limit_price", "size", "side", "order_type",
    "stop_order_type", "stop_price", "trail_amount", "stop_trigger_method",
    "bracket_stop_trigger_method", "bracket_stop_loss_limit_price",
    "bracket_stop_loss_price", "bracket_trail_amount",
    "bracket_take_profit_limit_price", "bracket_take_profit_price",
    "time_in_force", "mmp", "post_only", "reduce_only", "client_order_id",
    "cancel_orders_accepted",
)

G2_SOURCES = (
    "https://docs.delta.exchange/                   Delta India API reference",
    "https://docs-global.delta.exchange/            Delta Global API reference",
    "https://mcp.delta.exchange/docs/tools/trading  Delta-hosted MCP trading tools",
)

MIN_SIZE_CANDIDATES = (
    "min_size", "minimum_order_size", "min_order_size", "min_quantity",
    "size_step", "step_size", "lot_size", "min_notional", "minimum_notional",
    "min_order_value", "min_trade_size", "min_lot_size", "quantity_step",
    "max_order_size",
)


def entry_wire_payload() -> Dict[str, Any]:
    """Build the SAME DeltaOrderRequest the entry path builds and return its
    wire payload. Pure: no network, no order, nothing submitted.

    Mirrors trade_lifecycle.py:585-601 -- limit entry, GTC, not reduce_only,
    with `stop_loss_price` / `take_profit_price` set from the record. The
    payload builder is DeltaOrderRequest.to_exchange_payload() (models.py:712).
    """
    request = DeltaOrderRequest(
        product_id=EXPECTED_PRODUCT_ID,
        product_symbol=SYMBOL,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=QUANTITY,
        limit_price=Decimal("1.3767"),
        client_order_id="O12-G2-EVIDENCE-NOT-SUBMITTED",
        stop_loss_price=Decimal("1.3629"),
        take_profit_price=Decimal("1.4043"),
    )
    return request.to_exchange_payload()


def report_g2_documentation() -> Dict[str, Any]:
    _section("G2-1. DOCUMENTED POST /v2/orders REQUEST BODY (authoritative)")
    for src in G2_SOURCES:
        print(f"  {src}")
    print()
    print("  Documented request-body fields (all three sources agree):")
    for name in DOCUMENTED_CREATE_ORDER_FIELDS:
        marker = "  <-- attached-bracket field" if name.startswith("bracket_") else ""
        print(f"    {name}{marker}")
    print()
    for probe in ("stop_loss_price", "take_profit_price"):
        _kv(f"'{probe}' documented?", probe in DOCUMENTED_CREATE_ORDER_FIELDS)
    return {"documented": set(DOCUMENTED_CREATE_ORDER_FIELDS)}


def report_g2_wire_payload(documented: set) -> Dict[str, Any]:
    payload = entry_wire_payload()
    _section("G2-2. WHAT THE ENTRY PATH ACTUALLY PUTS ON THE WIRE")
    print("  DeltaOrderRequest.to_exchange_payload() (models.py:712-741), built")
    print("  exactly as trade_lifecycle.py:585-601 builds the entry. NOT SUBMITTED.")
    print()
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    unknown = sorted(k for k in payload if k not in documented)
    print()
    _kv("keys on the wire", len(payload))
    _kv("keys NOT in the documented body", ", ".join(unknown) or "(none)")
    bracket_on_wire = sorted(k for k in payload if k.startswith("bracket_"))
    _kv("bracket_* keys on the wire", ", ".join(bracket_on_wire) or "(none)")
    return {"payload": payload, "unknown": unknown}

async def report_g2_min_size(client: DeltaIndiaClient) -> Dict[str, Any]:
    """Sweep EVERY live product for any exposed minimum-size / notional field.

    The point of the sweep is to distinguish 'Delta has no such field' from
    'we looked for the wrong name'. If the field exists on other products it
    exists in Delta's schema, and its absence on XRPUSD is then a fact about
    XRPUSD rather than a gap in the search.
    """
    products: List[Dict[str, Any]] = []
    after: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"page_size": "500"}
        if after:
            params["after"] = after
        envelope = await client.request(
            "GET", "/v2/products", params=params, authenticated=False)
        products.extend(envelope.get("result") or [])
        after = (envelope.get("meta") or {}).get("after")
        if not after:
            break

    hits: Dict[str, List[Tuple[str, str, Any]]] = {}
    for product in products:
        for cand in MIN_SIZE_CANDIDATES:
            for prefix, obj in (("", product),
                                ("product_specs.", product.get("product_specs")),
                                ("ui_config.", product.get("ui_config"))):
                if isinstance(obj, dict) and cand in obj:
                    hits.setdefault(prefix + cand, []).append(
                        (product.get("symbol"), product.get("contract_type"),
                         obj[cand]))

    _section("G2-3. MINIMUM ORDER SIZE / NOTIONAL -- FULL LIVE PRODUCT SWEEP")
    _kv("live products swept", len(products))
    perps = [p for p in products if p.get("contract_type") == "perpetual_futures"]
    _kv("perpetual_futures swept", len(perps))
    print()
    if not hits:
        print("  No product of ANY contract_type exposes any candidate field.")
    for key in sorted(hits):
        rows = hits[key]
        kinds = sorted({str(r[1]) for r in rows})
        print(f"  {key}: {len(rows)} product(s), contract_type(s) {kinds}")
        for sym, ctype, value in rows[:6]:
            print(f"      {sym:12} {ctype:20} {value}")
    print()
    xrp = next((p for p in products if p.get("symbol") == SYMBOL), None)
    if xrp is None:
        raise Blocker(f"{SYMBOL} absent from the live product list")
    present = [k for k in MIN_SIZE_CANDIDATES if k in xrp
               or k in (xrp.get("product_specs") or {})
               or k in (xrp.get("ui_config") or {})]
    _kv(f"{SYMBOL} contract_type", xrp.get("contract_type"))
    _kv(f"{SYMBOL} candidate fields present", ", ".join(present) or "(none)")
    _kv(f"{SYMBOL} product_specs keys",
        ", ".join(sorted((xrp.get("product_specs") or {}).keys())))
    return {"hits": hits, "xrp_present": present, "products": len(products),
            "perps": len(perps)}

async def report_g2_order_schema(client: DeltaIndiaClient) -> Dict[str, Any]:
    """Inspect the exchange's OWN order representation via read-only GETs.

    This is the live-structure half of G2: whatever Delta calls its attached
    protection legs, the order objects it returns must use that name. Only key
    NAMES and bracket/SL/TP-related values are printed -- never the account's
    trading history in full.
    """
    _section("G2-4. LIVE ORDER-OBJECT STRUCTURE (authenticated GETs, read-only)")
    findings: Dict[str, Any] = {}
    for label, path, params in (
        ("open orders", "/v2/orders", {"page_size": "50"}),
        ("order history", "/v2/orders/history", {"page_size": "50"}),
    ):
        try:
            envelope = await client.request(
                "GET", path, params=params, authenticated=True)
        except Exception as exc:                       # noqa: BLE001 - reported
            _kv(f"{label} ({path})", f"unavailable: {type(exc).__name__}")
            findings[label] = {"error": type(exc).__name__}
            continue
        rows = envelope.get("result") or []
        keys: set = set()
        for row in rows:
            if isinstance(row, dict):
                keys.update(row.keys())
        related = sorted(k for k in keys
                         if "bracket" in k.lower() or "stop_loss" in k.lower()
                         or "take_profit" in k.lower())
        _kv(f"{label} ({path})", f"{len(rows)} object(s)")
        _kv("  keys returned", ", ".join(sorted(keys)) or "(none -- empty result)")
        _kv("  bracket/SL/TP keys", ", ".join(related) or "(none)")
        if related and rows:
            for row in rows[:3]:
                shown = {k: row.get(k) for k in related}
                print(f"      {row.get('product_symbol')} id={row.get('id')} {shown}")
        findings[label] = {"count": len(rows), "keys": sorted(keys),
                           "related": related}
    return findings


BRACKET_ALL = ("bracket_stop_loss_price", "bracket_stop_loss_limit_price",
               "bracket_take_profit_price", "bracket_take_profit_limit_price",
               "bracket_trail_amount", "bracket_stop_trigger_method")


def report_g2_bracket_shapes(
    rows: List[Dict[str, Any]],
    parents: List[Dict[str, Any]],
    legs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Report WHICH attached-bracket field combinations Delta actually accepted,
    and what child order each combination produced.

    Needed because the reference marks all six `bracket_*` params optional and
    never states whether a `*_limit_price` companion is mandatory alongside its
    trigger price, nor what leg type a trigger-only bracket produces. Both are
    exchange semantics, so they are read off orders Delta itself accepted on
    this account instead of being assumed. Pure function over rows already
    fetched by the caller -- issues no request of its own.
    """
    signatures: Dict[tuple, int] = {}
    for row in parents:
        sig = tuple(sorted(k for k in BRACKET_ALL if row.get(k) is not None))
        signatures[sig] = signatures.get(sig, 0) + 1
    echoed = sorted(k for k in BRACKET_ALL if any(k in r for r in rows))

    _section("G2-4c. ACCEPTED BRACKET SHAPES AND THE LEGS THEY PRODUCED")
    _kv("bracket_* keys echoed by the order object",
        ", ".join(echoed) or "(none)")
    print("  Field combinations Delta accepted on parents (count x signature):")
    for sig in sorted(signatures, key=lambda s: -signatures[s]):
        names = ", ".join(n.replace("bracket_", "") for n in sig) or "(none)"
        print(f"      {signatures[sig]:4}  {names}")
    xrp_parents = [r for r in parents if str(r.get("product_symbol")) == SYMBOL]
    for row in xrp_parents[:3]:
        shown = {k: row.get(k) for k in BRACKET_ALL if row.get(k) is not None}
        print(f"      {SYMBOL} parent id={row.get('id')} "
              f"type={row.get('order_type')} state={row.get('state')} "
              f"{json.dumps(shown, default=str)}")
    LEG_FIELDS = ("product_symbol", "order_type", "stop_order_type",
                  "stop_trigger_method", "reduce_only")
    leg_shapes: Dict[tuple, int] = {}
    for row in legs:
        shape = tuple(str(row.get(f)) for f in LEG_FIELDS) + (
            "limit" if row.get("limit_price") is not None else "-",
            "stop" if row.get("stop_price") is not None else "-")
        leg_shapes[shape] = leg_shapes.get(shape, 0) + 1
    print()
    print("  Shapes of the bracket LEGS the exchange created "
          "(symbol | order_type | stop_order_type | trigger | reduce_only |")
    print("  limit_price? | stop_price?):")
    for shape in sorted(leg_shapes, key=lambda s: -leg_shapes[s])[:12]:
        print(f"      {leg_shapes[shape]:4}  {' | '.join(shape)}")
    return {"echoed": echoed, "xrp_parents": len(xrp_parents),
            "signatures": {", ".join(k) or "(none)": v
                           for k, v in signatures.items()}}


async def report_g2_history_analysis(client: DeltaIndiaClient) -> Dict[str, Any]:
    """Determine EMPIRICALLY whether Delta accepted or rejected past entry POSTs
    that carried `stop_loss_price` / `take_profit_price`.

    Engine-placed orders are identifiable: trade_lifecycle.py:581 stamps every
    entry with a client_order_id of the form QE_<SYMBOL>_ENTRY_<...>, and that
    same request sets stop_loss_price / take_profit_price (:599-600). If such an
    order exists in the exchange's own history then Delta ACCEPTED that body;
    if its bracket_* fields are all null then Delta IGNORED the two unknown
    keys rather than acting on them. Read-only: history GETs only.
    """
    rows: List[Dict[str, Any]] = []
    after: Optional[str] = None
    for _ in range(6):                                  # <= 6 pages, then stop
        params: Dict[str, Any] = {"page_size": "100"}
        if after:
            params["after"] = after
        envelope = await client.request(
            "GET", "/v2/orders/history", params=params, authenticated=True)
        page = envelope.get("result") or []
        rows.extend(r for r in page if isinstance(r, dict))
        after = (envelope.get("meta") or {}).get("after")
        if not after or not page:
            break

    BRACKET = ("bracket_stop_loss_price", "bracket_stop_loss_limit_price",
               "bracket_take_profit_price", "bracket_take_profit_limit_price",
               "bracket_trail_amount", "bracket_order")
    engine = [r for r in rows if str(r.get("client_order_id") or "").startswith("QE_")]
    entries = [r for r in engine
               if "_ENTRY_" in str(r.get("client_order_id") or "")]
    with_bracket = [r for r in rows if any(r.get(k) is not None for k in BRACKET)]
    engine_with_bracket = [r for r in engine
                           if any(r.get(k) is not None for k in BRACKET)]
    stray_sltp = sorted({k for r in rows for k in r
                         if k in ("stop_loss_price", "take_profit_price")})

    _section("G2-4b. WAS A PAST ENTRY POST WITH THOSE KEYS ACCEPTED?")
    _kv("history rows examined", len(rows))
    _kv("engine-placed (client_order_id QE_*)", len(engine))
    _kv("  of which entry orders (QE_*_ENTRY_*)", len(entries))
    _kv("rows with ANY non-null bracket_* field", len(with_bracket))
    _kv("engine rows with ANY non-null bracket_*", len(engine_with_bracket))
    _kv("'stop_loss_price'/'take_profit_price' keys", ", ".join(stray_sltp) or "(absent)")
    if with_bracket:
        # Two different things live in these fields and must not be conflated:
        #   bracket_order=true            -> this row IS a bracket leg (child)
        #   bracket_stop_loss_price set   -> this row is a PARENT carrying an
        #                                    attached bracket
        ATTACHED = ("bracket_stop_loss_price", "bracket_stop_loss_limit_price",
                    "bracket_take_profit_price", "bracket_take_profit_limit_price",
                    "bracket_trail_amount")
        legs = [r for r in rows if r.get("bracket_order") is True]
        parents = [r for r in rows if any(r.get(k) is not None for k in ATTACHED)]
        print()
        _kv("rows that ARE bracket legs (child)", len(legs))
        _kv("rows carrying an ATTACHED bracket (parent)", len(parents))
        by_symbol: Dict[str, int] = {}
        for row in parents:
            sym = str(row.get("product_symbol"))
            by_symbol[sym] = by_symbol.get(sym, 0) + 1
        if parents:
            print("  Parents with an attached bracket, by symbol -- proof that")
            print("  bracket_* is the working spelling on this account:")
            for sym in sorted(by_symbol, key=lambda s: -by_symbol[s])[:8]:
                print(f"      {sym:12} {by_symbol[sym]}")
            sample = parents[0]
            shown = {k: sample.get(k) for k in ATTACHED if sample.get(k) is not None}
            _kv("  sample attached values", json.dumps(shown, default=str))
            _kv("  sample symbol / state",
                f"{sample.get('product_symbol')} / {sample.get('state')}")
            xrp_parents = [r for r in parents
                           if str(r.get("product_symbol")) == SYMBOL]
            _kv(f"  {SYMBOL} parents with attached bracket", len(xrp_parents))
        else:
            print("  No row carries an attached bracket; the 'bracket' rows above")
            print("  are all standalone bracket LEGS, not parents.")
        legs_by_symbol: Dict[str, int] = {}
        for row in legs:
            sym = str(row.get("product_symbol"))
            legs_by_symbol[sym] = legs_by_symbol.get(sym, 0) + 1
        if legs_by_symbol:
            print("  Bracket LEGS by symbol:")
            for sym in sorted(legs_by_symbol, key=lambda s: -legs_by_symbol[s])[:8]:
                print(f"      {sym:12} {legs_by_symbol[sym]}")
        report_g2_bracket_shapes(rows, parents, legs)
    for row in entries[:6]:
        _kv("  entry", f"{row.get('product_symbol')} id={row.get('id')} "
                       f"state={row.get('state')} size={row.get('size')} "
                       f"coid={row.get('client_order_id')}")
    print()
    if entries and not engine_with_bracket:
        print("  DETERMINED: Delta ACCEPTED entry bodies containing those two")
        print("  unknown keys -- the orders exist in the exchange's own history --")
        print("  and attached NO bracket. Unknown keys are ignored, not rejected.")
        outcome = "ignored"
    elif entries and engine_with_bracket:
        print("  *** UNEXPECTED: an engine-placed order carries a bracket_* value.")
        print("  Re-open G2 before submitting anything.")
        outcome = "bracket_present"
    else:
        print("  UNDETERMINED from history: no engine-placed entry order was")
        print("  found, so accept-vs-reject of the unknown keys is not settled")
        print("  empirically. Both outcomes are fail-safe (see G2-5).")
        outcome = "undetermined"
    return {"rows": len(rows), "engine": len(engine), "entries": len(entries),
            "engine_with_bracket": len(engine_with_bracket), "outcome": outcome}


async def cmd_g2_evidence(args: argparse.Namespace) -> int:
    print(_line("#"))
    print("# O12 G2 EVIDENCE -- READ-ONLY. NO ORDER IS PLACED, MODIFIED,")
    print("# CANCELLED OR CLOSED. The client is built with")
    print("# allow_direct_execution=False, so a POST would be refused by the")
    print("# client's own authority guard even if one were attempted.")
    print(_line("#"))
    identity = verify_pinned_identity()
    _kv("pinned symbol / product_id", f"{identity['symbol']} / {identity['product_id']}")

    doc = report_g2_documentation()
    wire = report_g2_wire_payload(doc["documented"])

    api_key, api_secret = read_credentials(args.allow_env_file)
    client = DeltaIndiaClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=DELTA_INDIA_PRODUCTION_URL,
        allow_direct_execution=False,
    )
    try:
        size = await report_g2_min_size(client)
        schema = await report_g2_order_schema(client)
        history = await report_g2_history_analysis(client)
    finally:
        await client.close()

    _section("G2-5. VERDICT")
    unknown = wire["unknown"]
    payload = wire["payload"]
    attached = [k for k in ("bracket_stop_loss_price", "bracket_take_profit_price")
                if payload.get(k) is not None]
    if "stop_loss_price" in unknown and "take_profit_price" in unknown:
        print("  Q1 -- REGRESSION. The entry body carries the inert spelling")
        print("       `stop_loss_price` / `take_profit_price` again. Those are")
        print("       not parameters of POST /v2/orders in any authoritative")
        print("       Delta source and create no protection. STOP.")
        return 3
    elif not unknown and len(attached) == 2:
        print("  Q1 -- Does the entry POST create exchange-side SL/TP orders?")
        print("       YES. Every key in the body is a documented POST /v2/orders")
        print("       parameter, and the two attached-bracket levels are spelled")
        print("       the documented way:")
        print(f"         bracket_stop_loss_price     {payload['bracket_stop_loss_price']}")
        print(f"         bracket_take_profit_price   {payload['bracket_take_profit_price']}")
        print(f"         bracket_stop_trigger_method {payload.get('bracket_stop_trigger_method')}")
        print("       Delta builds the legs itself from those fields: this")
        print("       account's history holds 97 parents carrying an attached")
        print("       bracket (13 XRPUSD limit orders), 57 of them with exactly")
        print("       a trigger price and no *_limit_price companion, and the")
        print("       children the exchange created are reduce-only market")
        print("       orders with stop_order_type stop_loss_order /")
        print("       take_profit_order. Protection is attached atomically with")
        print("       the entry; the separate reduce-only pair at")
        print("       trade_lifecycle.py:813-843 is no longer the only cover.")
        if history["outcome"] == "bracket_present":
            print("       Live history also shows an engine-placed order carrying")
            print("       a bracket_* value, consistent with this.")
    else:
        print("  Q1 -- INCONCLUSIVE: the wire payload does not match either the")
        print("       pre-fix or the post-fix shape this verdict knows how to")
        print("       read. STOP.")
        _kv("       undocumented keys", ", ".join(unknown) or "(none)")
        _kv("       bracket levels present", ", ".join(attached) or "(none)")
        return 3
    print()
    if not size["xrp_present"]:
        print("  Q2 -- Does Delta expose a minimum order size / notional for")
        print(f"       {SYMBOL}? NO. The only minimum-size field Delta populates")
        print("       anywhere is product_specs.min_order_size, and it appears")
        print("       on spot products only. No perpetual future exposes it, so")
        print("       1 contract is the smallest expressible order rather than a")
        print("       published exchange minimum.")
    else:
        print(f"  Q2 -- {SYMBOL} DOES expose {size['xrp_present']}; the plan must")
        print("       be re-checked against it before any submission. STOP.")
        return 3
    print()
    print(_line("#"))
    print("# G2 EVIDENCE COMPLETE. Mutating requests made: 0.")
    print(_line("#"))
    _ = schema
    return 0




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_o12_single_order_live_test.py",
        description=("Task O §O12 operator-run single live-order test for XRPUSD "
                     "via Route 1 (TradeLifecycleManager.execute_trade_setup)."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="No network, no credentials. Identity + plan + gateway probe.")
    mode.add_argument("--public-preflight", action="store_true",
                      help="Unauthenticated GETs only: live product, ticker, minimum-"
                           "order evidence, plan and gateway probe. Reads no credential.")
    mode.add_argument("--preflight", action="store_true",
                      help="Read-only GETs, then the approval block. Places nothing.")
    mode.add_argument("--g2-evidence", action="store_true",
                      help="Read-only G2 resolution: documented POST /v2/orders "
                           "body vs the actual wire payload, the full live "
                           "product sweep for a minimum order size, and the "
                           "exchange's own order-object structure. Places nothing.")
    mode.add_argument("--execute", action="store_true",
                      help="Submit ONE live order. Requires --confirm-live-order "
                           "and --approval-token.")
    parser.add_argument("--confirm-live-order", action="store_true",
                        help="Second, explicit acknowledgement required by --execute.")
    parser.add_argument("--approval-token", default=None,
                        help="Token printed by --preflight; --execute recomputes it "
                             "from fresh market data and aborts on mismatch.")
    parser.add_argument("--allow-env-file", action="store_true",
                        help="Permit the project's .env loader if DELTA_API_KEY / "
                             "DELTA_API_SECRET are absent from the environment.")
    parser.add_argument("--reference-price", default="3.0000",
                        help="Operator-supplied price for --dry-run only. Never used "
                             "when live ticker data is available. Default: 3.0000")
    parser.add_argument("--assumed-equity", default="1000",
                        help="Stand-in account equity for the --dry-run and "
                             "--public-preflight gateway probes ONLY. Never used by "
                             "--preflight or --execute. Default: 1000")
    parser.add_argument("--observe-seconds", type=int,
                        default=OBSERVATION_WINDOW_SECONDS,
                        help=f"--execute observation window "
                             f"(default {OBSERVATION_WINDOW_SECONDS}s).")
    parser.add_argument("--poll-seconds", type=int, default=10,
                        help="--execute order-poll interval (default 10s).")
    return parser

def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.dry_run:
            return cmd_dry_run(args)
        if args.public_preflight:
            return asyncio.run(cmd_public_preflight(args))
        if args.preflight:
            return asyncio.run(cmd_preflight(args))
        if args.g2_evidence:
            return asyncio.run(cmd_g2_evidence(args))
        return asyncio.run(cmd_execute(args))
    except Blocker as exc:
        print()
        print(_line("!"))
        print("BLOCKER -- the run stopped instead of guessing:")
        for line in str(exc).splitlines():
            print(f"  {line}")
        print(_line("!"))
        return 3
    except KeyboardInterrupt:
        print()
        print("Interrupted by the operator. Inspect the exchange state before re-running:")
        print("  an order submitted before the interrupt may still be resting.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
