"""
Execution sizing / quantity-conversion semantics — audit pin.
=============================================================

Companion to `test_execution_identity_audit.py`. That file closed the *identity*
question (which product an order names). This one pins the *quantity* question
(how many of it), and the answer is deliberately partial.

What the repository establishes, and what it refuses to:

  * VERIFIED, from the pinned snapshot: `product_id`, `tick_size`,
    `contract_value`, `contract_unit_currency`, `notional_type`. One BTCUSD
    contract is 0.001 BTC. That is a fact about the contract's *size*.
  * NOT VERIFIED, by the snapshot's own declaration: `minimum_order_size`,
    `size_step`, `max_leverage`, and `notional_to_contracts_formula`. The
    snapshot states of the last one: "Delta's public documentation never states
    how order size relates to contract_value; a converter must still be
    supplied explicitly."

A verified `contract_value` does NOT by itself establish
`contracts = notional / contract_value`, and the snapshot is right to refuse:
that formula is absent from the `/v2/products` payload the snapshot is scoped
to. `InstrumentSpec.notional_to_contracts` still refuses, and
`manual_smc.sizing.resolve_order_quantity` still refuses without an injected
converter — and has zero production callers. §G pins both refusals.

The conversion is nevertheless established by *other* official Delta material,
which is outside the snapshot's scope and is therefore recorded here rather than
in the snapshot (§H carries the citations):

  * Delta's MCP tool reference types `place_order`'s `size` as `int`, described
    as "order size in contracts (positive)", with examples in "contracts".
  * The REST reference's POST /v2/orders example sends `"size": 10` unquoted,
    and Types->Numbers states "Integer numbers (like contract size, product_id
    and impact size) are unquoted".
  * Delta's fee schedule states notional as "No. of contracts x Lot size x
    Index Price", worked as "1000 x 0.001 x $100000 = $100000", and gives
    BTCUSD's lot as 0.001 BTC — the snapshot's verified `contract_value`.
  * Delta India's user guide defines a limit order as "an order to buy or sell a
    specified number of futures contracts at a specified price", and quotes
    quantities as whole contracts throughout ("A buy order for 50 contracts",
    "Quantity = 50 contracts"). No product-specific lot increment is stated
    anywhere, and none exists in the `/v2/products` payload.

So `size` is a positive integer contract count and notional = size x
contract_value x price, hence contracts = notional / (price x contract_value).
That is exactly the allocator's `raw_quantity` when `contract_unit` is the
verified `contract_value`. `multi_user_orchestrator` already did this;
`market_orchestrator` passed nothing and inherited the allocator's 1.0 policy
default, sizing in BASE-ASSET units and submitting that number as a contract
count — 1000x low for BTCUSD, 100x for ETHUSD. It now passes the verified value.
Because `contract_unit` cancels out of the notional and margin arithmetic (§D),
no internal check could have caught it and the correction changes no
risk/margin assumption.

Because the count is an integer, two further things follow and are pinned here:
the allocator's quantity grid defaults are one whole contract (§C/§E), and a
non-integral or non-positive `size` is REFUSED at serialization rather than
truncated (§J) — flooring 98.492 to 98 would change the exposure a validated
order was approved for.

What this file pins:

  §A  The sizing-relevant policy/authority boundary in `validation.py`.
  §B  The retained `max_leverage` policy is never more permissive than the
      snapshot's recorded `default_leverage` — a direction check, not a
      derivation of a cap from recorded fields.
  §C  `contract_unit` is a caller-supplied multiplier whose default is local
      policy 1.0, never the verified `contract_value`; and the quantity grid
      defaults are the documented one-whole-contract rule.
  §D  The current sizing arithmetic, pinned exactly, so any future change to
      quantity behavior is visible in a diff — including the proof that
      `contract_unit` is invisible to every margin check.
  §E  Both production callers pass the verified contract value and now agree on
      a whole-contract grid, structurally.
  §F  A non-positive `contract_unit` fails closed with `CapitalAllocationError`
      rather than raising `decimal.DivisionByZero` out of the allocator.
  §G  The closed doors stay closed and un-called from production.
  §H  The documented `size` contract, and the structural bans that keep a
      second conversion or product authority from appearing.
  §I  The recorded `position_size_limit` is unenforced -- pinned as a gap,
      because wiring a cap to a recorded (non-verified) field is new policy.
  §J  The serialization choke point refuses a non-integral or non-positive
      contract count, and never truncates one onto the grid.
  §K  Both execution paths turn that refusal into a structured outcome instead
      of crashing the loop.

This file adds no product table, duplicates no verified value (§B reads the
snapshot; §A/§C/§D/§E read the registry), and asserts nothing about the
conversion beyond what the cited official documentation states.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from quantedge.execution.algo_config import AlgoConfigStore
from quantedge.execution.capital_allocator import (
    CapitalAllocationError,
    CapitalAllocator,
    PositionSizingResult,
)
from quantedge.execution.delta_client import DeltaIndiaClient
from quantedge.execution.models import (
    ConnectionState,
    DeltaOrderRequest,
    DeltaOrderResponse,
    DeltaWalletBalance,
    ExecutionMode,
    OrderSide,
    OrderSizeContractError,
    OrderStatus,
    OrderType,
    StopOrderType,
    StopTriggerMethod,
)
from quantedge.execution.multi_user_orchestrator import (
    TradeDirection,
    UserAccountConfig,
    UserExecutionSession,
)
from quantedge.execution.single_trade_lock import SingleTradeLockManager
from quantedge.execution.synchronizer import LocalStateStore
from quantedge.execution.trade_lifecycle import (
    TradeLifecycleManager,
    TradeLifecycleState,
)
from quantedge.execution.leverage import MAX_LEVERAGE, MIN_LEVERAGE
from quantedge.execution.validation import (
    DEFAULT_DELTA_INDIA_PRODUCTS,
    UNVERIFIED_MAX_LEVERAGE,
    UNVERIFIED_MAX_LEVERAGE_FALLBACK,
    UNVERIFIED_MIN_SIZE,
    UNVERIFIED_SIZE_STEP,
    OrderValidationGateway,
    RejectionReasonCode,
    get_product_specification,
)
from quantedge.instruments import (
    FieldUnverifiedError,
    PERMANENTLY_UNVERIFIED,
    delta_india_registry,
)
from quantedge.strategy.models import (
    SetupState,
    StrategyDecision,
    StrategyDirection,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = (REPO_ROOT / "data" / "reference" / "delta_exchange_india"
                 / "product_specs_snapshot.json")
EXECUTION_DIR = (Path(__file__).resolve().parents[1] / "src" / "quantedge"
                 / "execution")
SRC_DIR = Path(__file__).resolve().parents[1] / "src"

#: The three names the gateway needs a bound for and the exchange does not
#: publish. Kept here so a policy change has to be made deliberately.
POLICY_TRIO = ("min_size", "size_step", "max_leverage")


@pytest.fixture(scope="module")
def registry():
    return delta_india_registry()


@pytest.fixture(scope="module")
def snapshot():
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def allocator():
    return CapitalAllocator()


def _module_tree(name: str) -> ast.Module:
    path = EXECUTION_DIR / name
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _allocation_call_kwargs(module: str) -> list:
    """Keyword names passed to `calculate_100_percent_allocation` in `module`."""
    found = []
    for node in ast.walk(_module_tree(module)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and \
                func.attr == "calculate_100_percent_allocation":
            found.append({kw.arg: kw.value for kw in node.keywords})
    return found


# ---------------------------------------------------------------------------
# §A  The sizing-relevant policy / authority boundary.
# ---------------------------------------------------------------------------
class TestTheGatewayBoundsAreLabelledPolicyNotExchangeMetadata:
    """
    The gateway's quantity and leverage checks need a bound; the exchange
    publishes none. The three bounds are therefore local policy, and the
    authoritative schema must keep refusing the same three names.
    """

    def test_the_policy_names_are_exactly_the_unpublished_names(self):
        assert set(POLICY_TRIO) | {"notional_to_contracts_formula"} == {
            "min_size" if n == "minimum_order_size" else n
            for n in PERMANENTLY_UNVERIFIED
        }

    @pytest.mark.parametrize("symbol", sorted(DEFAULT_DELTA_INDIA_PRODUCTS))
    def test_every_gateway_spec_names_the_trio_as_unverified(self, symbol):
        spec = get_product_specification(symbol)
        assert spec.unverified_fields == POLICY_TRIO
        for name in POLICY_TRIO:
            assert getattr(spec, name) is not None

    @pytest.mark.parametrize("symbol", sorted(DEFAULT_DELTA_INDIA_PRODUCTS))
    def test_the_gateway_bounds_equal_the_named_policy_constants(self, symbol):
        spec = get_product_specification(symbol)
        assert spec.min_size == UNVERIFIED_MIN_SIZE
        assert spec.size_step == UNVERIFIED_SIZE_STEP
        assert spec.max_leverage == UNVERIFIED_MAX_LEVERAGE.get(
            symbol, UNVERIFIED_MAX_LEVERAGE_FALLBACK)

    @pytest.mark.parametrize("symbol", sorted(DEFAULT_DELTA_INDIA_PRODUCTS))
    def test_the_authority_still_refuses_the_same_three_names(
            self, registry, symbol):
        """
        Policy values existing in the gateway must not leak back into the
        authoritative schema. Reading them off an `InstrumentSpec` still raises.
        """
        spec = registry.get(symbol)
        for name in ("minimum_order_size", "size_step", "max_leverage"):
            with pytest.raises(FieldUnverifiedError):
                getattr(spec, name)

    @pytest.mark.parametrize("symbol", sorted(DEFAULT_DELTA_INDIA_PRODUCTS))
    def test_verification_source_describes_provenance_not_the_bounds(
            self, registry, symbol):
        """
        `verification_source` is set from the snapshot provenance because the
        *verified* trio was copied from it. It is not a claim that the policy
        bounds are verified -- `unverified_fields` names those.
        """
        spec = get_product_specification(symbol)
        assert spec.verification_source == \
            registry.get(symbol).provenance.as_source_string()
        assert spec.unverified_fields == POLICY_TRIO


# ---------------------------------------------------------------------------
# §B  The retained leverage cap is conservative with respect to the snapshot.
# ---------------------------------------------------------------------------
class TestTheLeverageCapIsNeverMorePermissiveThanTheSnapshot:
    """
    `max_leverage` is not a Delta field. The snapshot records `initial_margin`,
    `default_leverage` and `max_leverage_notional` raw and explicitly declines
    to derive a cap from them:

        "not a Delta field; initial_margin / default_leverage /
         max_leverage_notional are recorded raw instead of deriving a cap"

    So this section does NOT compute the policy cap from the recorded numbers.
    It only checks the *direction*: the retained policy must never permit more
    leverage than the exchange itself records as its default. That is a
    one-sided safety check which stays valid whatever the true cap is, and it
    would fail loudly if the policy were ever raised.
    """

    @pytest.mark.parametrize("symbol", sorted(DEFAULT_DELTA_INDIA_PRODUCTS))
    def test_the_policy_cap_does_not_exceed_recorded_default_leverage(
            self, snapshot, symbol):
        recorded = Decimal(
            snapshot["products"][symbol]["margin_and_limits"]["default_leverage"])
        policy = Decimal(get_product_specification(symbol).max_leverage)
        assert policy <= recorded, (
            f"{symbol}: policy cap {policy}x exceeds the exchange's recorded "
            f"default leverage {recorded}x -- raising a cap weakens a safety "
            f"check")

    def test_the_fallback_is_the_strictest_retained_cap(self):
        assert UNVERIFIED_MAX_LEVERAGE_FALLBACK == \
            min(UNVERIFIED_MAX_LEVERAGE.values())
        assert UNVERIFIED_MAX_LEVERAGE_FALLBACK == MAX_LEVERAGE

    def test_the_policy_table_is_uniform_at_the_authorised_band(self):
        """The table used to read BTC/ETH 100, SOL/XRP 50.

        The two 50s were retained from the pre-registry gateway and made a
        requested 100x unreachable on those symbols. The owner authorised a
        uniform 1x..100x band, so every entry is `MAX_LEVERAGE` now and the
        fallback above coincides with it. The `policy <= recorded` check in
        this class still holds -- SOLUSD and XRPUSD record a
        `default_leverage` of exactly 100 -- so the one-sided safety direction
        is preserved, not bypassed.
        """
        assert set(UNVERIFIED_MAX_LEVERAGE.values()) == {MAX_LEVERAGE}
        assert MIN_LEVERAGE == 1 and MAX_LEVERAGE == 100

    def test_recorded_margin_fields_are_not_hashed_as_verified(self, snapshot):
        """
        The recorded leverage numbers move with Delta's risk configuration, so
        they are outside the pinned hash. Anything derived from them would be
        derived from an unpinned value.
        """
        recorded = snapshot["products"]["BTCUSD"]["recorded_not_hashed"]
        assert "default_leverage" in recorded
        assert "initial_margin" in recorded
        verified = snapshot["products"]["BTCUSD"]["verified_fields"]
        assert not {"default_leverage", "initial_margin", "max_leverage"} \
            & set(verified)

    @pytest.mark.parametrize("symbol", sorted(DEFAULT_DELTA_INDIA_PRODUCTS))
    def test_the_size_side_fields_are_absent_from_the_payload(
            self, snapshot, symbol):
        absent = set(snapshot["products"][symbol]["absent_from_payload"])
        assert "max_leverage" in absent
        assert "size_step" in absent
        assert {"minimum_order_size", "min_size"} & absent


# ---------------------------------------------------------------------------
# §C  `contract_unit` is a caller-supplied multiplier, not verified metadata.
# ---------------------------------------------------------------------------
class TestContractUnitIsCallerPolicyNotTheVerifiedContractValue:

    def test_the_allocator_defaults_are_policy_constants(self):
        """
        None of these three defaults is verified product metadata -- they are
        the allocator's own policy, and every production caller overrides
        `contract_unit` with the verified contract value (§E).

        The quantity grid defaults are `1` because Delta's documented order
        `size` is a positive integer contract count (§H): the REST reference
        sends `"size": 10` unquoted and states that "Integer numbers (like
        contract size, product_id and impact size) are unquoted", the order-tool
        reference types `size` as `int` ("order size in contracts (positive)"),
        and the India user guide defines an order as one "to buy or sell a
        specified number of futures contracts". Delta publishes no size-side
        increment field for any product, so the grid is one contract for every
        product and the smallest order is one contract.

        These were `0.001`, a base-asset-shaped grid that only made sense while
        `contract_unit` defaulted to `1.0` and `raw_quantity` was therefore a
        base-asset amount. With `contract_unit` set to the verified contract
        value, `raw_quantity` is a contract count and a 0.001 grid yields
        fractional contracts Delta cannot accept.
        """
        import inspect
        sig = inspect.signature(
            CapitalAllocator.calculate_100_percent_allocation)
        assert sig.parameters["contract_unit"].default == Decimal("1.0")
        assert sig.parameters["lot_size_step"].default == Decimal("1")
        assert sig.parameters["min_quantity"].default == Decimal("1")

    @pytest.mark.parametrize("symbol,contract_value", [
        ("BTCUSD", "0.001"), ("ETHUSD", "0.01"),
        ("SOLUSD", "1"), ("XRPUSD", "1"),
    ])
    def test_the_verified_contract_value_differs_from_that_default(
            self, registry, symbol, contract_value):
        """
        Two of the four verified contract values are not 1, so the allocator's
        `contract_unit` default coincides with SOL/XRP and disagrees with
        BTC/ETH by 1000x and 100x. That default is why every production caller
        must pass the verified contract value explicitly (§E): leaving the
        default in place computes a base-asset amount and then submits it as a
        contract count.
        """
        assert registry.get(symbol).contract_value == Decimal(contract_value)
        assert get_product_specification(symbol).contract_value == \
            Decimal(contract_value)

    def test_the_allocator_holds_no_product_specific_knowledge(self):
        """
        The allocator must stay symbol-agnostic: every product-specific number
        arrives as an argument. A contract value or symbol literal appearing
        here would be a fifth product table.
        """
        source = (EXECUTION_DIR / "capital_allocator.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        strings = {n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert not {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"} & strings
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "delta_india_registry" not in names
        assert "contract_value" not in names | attrs


# ---------------------------------------------------------------------------
# §D  The current sizing arithmetic, pinned exactly.
# ---------------------------------------------------------------------------
class TestTheSizingArithmeticIsPinned:
    """
    Units, as the code actually computes them:

        available_balance   USD (quote/settle currency, verified as USD)
        entry_price         USD per one unit of `contract_unit`
        usable_margin       USD              = balance * buffer%
        max_notional        USD              = usable_margin * leverage
        raw_quantity        `contract_unit`s = max_notional / (price * unit)
        stepped_quantity    `contract_unit`s = floor(raw / step) * step
        actual_notional     USD              = qty * unit * price
        required_margin     USD              = notional / leverage

    `stepped_quantity` is what reaches `DeltaOrderRequest.size`. Its unit is
    therefore whatever the caller's `contract_unit` meant -- base asset when
    the caller passes 1.0, contracts when the caller passes `contract_value`.
    """

    def test_the_documented_hundred_percent_allocation_case(self, allocator):
        res = allocator.calculate_100_percent_allocation(
            symbol="BTCUSD",
            entry_price=Decimal("100000"),
            available_balance=Decimal("1000"),
            leverage=10,
            lot_size_step=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
        )
        # 1000 * 98% * 10 = 9800 USD of buying power at 100000 USD/unit.
        assert res.position_quantity == Decimal("0.098")
        assert res.notional_value == Decimal("9800")
        assert res.allocated_margin == Decimal("980")
        assert res.effective_leverage == Decimal("9.80")
        assert res.safety_buffer_pct == Decimal("98.00")

    def test_stepping_always_rounds_down_never_up(self, allocator):
        res = allocator.calculate_100_percent_allocation(
            symbol="BTCUSD",
            entry_price=Decimal("100000"),
            available_balance=Decimal("100"),
            leverage=10,
            lot_size_step=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
        )
        # raw 0.0098 -> 9 whole steps, not 10.
        assert res.position_quantity == Decimal("0.009")
        assert res.allocated_margin <= Decimal("100")

    def test_margin_never_exceeds_the_balance_that_was_queried(self, allocator):
        for balance in ("50", "137.44", "1000", "25000"):
            res = allocator.calculate_100_percent_allocation(
                symbol="BTCUSD",
                entry_price=Decimal("99500"),
                available_balance=Decimal(balance),
                leverage=17,
                lot_size_step=Decimal("0.001"),
                min_quantity=Decimal("0.001"),
            )
            assert res.allocated_margin <= Decimal(balance)

    @pytest.mark.parametrize("symbol,price,unit,expect_one,expect_unit", [
        ("BTCUSD", "100000", "0.001", "0.098", "98.000"),
        ("ETHUSD", "4000", "0.01", "2.450", "245.000"),
        ("SOLUSD", "200", "1", "49.000", "49.000"),
        ("XRPUSD", "2", "1", "4900.000", "4900.000"),
    ])
    def test_the_margin_math_cannot_detect_the_wrong_contract_unit(
            self, allocator, symbol, price, unit, expect_one, expect_unit):
        """
        The load-bearing finding of this audit.

        `contract_unit` divides in step 3 and multiplies back in step 6, so it
        CANCELS: notional, required margin and effective leverage come out
        identical whichever value the caller passes. Only `position_quantity`
        -- the number written into `DeltaOrderRequest.size` and sent to the
        exchange -- changes, by 1000x for BTCUSD and 100x for ETHUSD.

        No internal consistency check inside the allocator can therefore catch
        a wrong `contract_unit`; the allocator's own fail-closed margin
        assertion passes in both cases. Only Delta's definition of `size`
        settles it -- absent from the snapshot's `/v2/products` scope, but
        stated in the other official material cited in §H.
        """
        kwargs = dict(symbol=symbol, entry_price=Decimal(price),
                      available_balance=Decimal("1000"), leverage=10,
                      lot_size_step=Decimal("0.001"),
                      min_quantity=Decimal("0.001"))
        as_base = allocator.calculate_100_percent_allocation(**kwargs)
        as_contracts = allocator.calculate_100_percent_allocation(
            contract_unit=Decimal(unit), **kwargs)

        assert as_base.position_quantity == Decimal(expect_one)
        assert as_contracts.position_quantity == Decimal(expect_unit)

        assert as_base.notional_value == as_contracts.notional_value
        assert as_base.allocated_margin == as_contracts.allocated_margin
        assert as_base.effective_leverage == as_contracts.effective_leverage


# ---------------------------------------------------------------------------
# §E  Both production callers now convert notional the same way.
# ---------------------------------------------------------------------------
class TestBothProductionSizingCallersConvertTheSameWay:
    """
    There are exactly two production calls into the allocator. They used to
    disagree: `multi_user_orchestrator` passed the verified `contract_value`,
    `market_orchestrator` passed nothing and inherited the allocator's
    `Decimal("1.0")` default, which is a BASE-ASSET quantity.

    Delta's own documentation settles which is right (see §H). Both callers now
    pass the verified contract value. This section pins that agreement so the
    divergence cannot come back, and §E-last pins the quantity grid the two now
    share -- one explicitly, one by allocator default.
    """

    def test_there_are_exactly_two_production_call_sites(self):
        sites = {
            path.name: len(_allocation_call_kwargs(path.name))
            for path in EXECUTION_DIR.glob("*.py")
            if _allocation_call_kwargs(path.name)
        }
        assert sites == {"market_orchestrator.py": 1,
                         "multi_user_orchestrator.py": 1}

    def test_the_multi_user_path_passes_the_verified_contract_value(self):
        (kwargs,) = _allocation_call_kwargs("multi_user_orchestrator.py")
        assert "contract_unit" in kwargs
        assert isinstance(kwargs["contract_unit"], ast.Name)
        assert kwargs["contract_unit"].id == "contract_value"
        # ...which step 6 of that method binds from the registry spec.
        source = (EXECUTION_DIR / "multi_user_orchestrator.py").read_text(
            encoding="utf-8")
        assert "contract_value = spec.contract_value" in source

    def test_the_market_scan_path_passes_the_verified_contract_value(self):
        (kwargs,) = _allocation_call_kwargs("market_orchestrator.py")
        assert "contract_unit" in kwargs, (
            "market_orchestrator no longer passes contract_unit -- it would be "
            "back to sizing in base-asset units and submitting that number as "
            "a contract count (1000x low for BTCUSD)")
        # `spec.contract_value if spec else Decimal("1.0")` -- the value comes
        # from the gateway spec, which copies it verbatim from the registry.
        expr = kwargs["contract_unit"]
        assert isinstance(expr, ast.IfExp)
        assert isinstance(expr.body, ast.Attribute)
        assert expr.body.attr == "contract_value"

    def test_neither_caller_writes_a_contract_value_literal(self):
        """
        Both must read the value from the authority. A number written at the
        call site would be a second product table.
        """
        for module in ("market_orchestrator.py", "multi_user_orchestrator.py"):
            (kwargs,) = _allocation_call_kwargs(module)
            for node in ast.walk(kwargs["contract_unit"]):
                if isinstance(node, ast.Constant):
                    # The only literal permitted is the `else` fallback for an
                    # unregistered symbol, which is the allocator's own default.
                    assert node.value == "1.0", (module, node.value)

    def test_the_two_paths_now_agree_on_a_whole_contract_grid(self):
        """
        The step divergence Task E reported is closed, without either module
        naming a size-side field.

        `market_orchestrator` passes the gateway's policy constants explicitly.
        `multi_user_orchestrator` passes neither argument and inherits the
        allocator's defaults -- which are now `1`/`1`, the documented integer
        contract rule (§H), instead of the base-asset-shaped `0.001` that let it
        compute e.g. 98.492 contracts and serialize a non-integer `size`.

        It deliberately is NOT fixed by passing `lot_size_step=` there:
        `test_multi_user_product_identity.test_no_unverified_field_is_read`
        asserts the substring "size_step" is absent from that module's source,
        and "lot_size_step" contains it. That assertion encodes a real
        requirement -- the module must not read registry fields that refuse to
        be read -- so the grid moved into the allocator default and the existing
        test stays untouched and passing.

        The explicit constants and the implicit default are pinned equal here so
        the two paths cannot silently drift back apart.
        """
        (market,) = _allocation_call_kwargs("market_orchestrator.py")
        (multi,) = _allocation_call_kwargs("multi_user_orchestrator.py")
        assert {"lot_size_step", "min_quantity"} <= set(market)
        assert not {"lot_size_step", "min_quantity"} & set(multi)
        assert UNVERIFIED_MIN_SIZE == UNVERIFIED_SIZE_STEP == Decimal("1")
        assert "size_step" not in (
            EXECUTION_DIR / "multi_user_orchestrator.py").read_text(
                encoding="utf-8")

        import inspect
        sig = inspect.signature(
            CapitalAllocator.calculate_100_percent_allocation)
        assert sig.parameters["lot_size_step"].default == UNVERIFIED_SIZE_STEP
        assert sig.parameters["min_quantity"].default == UNVERIFIED_MIN_SIZE

    def test_the_quantity_the_lifecycle_submits_comes_from_the_decision(self):
        """
        `market_orchestrator` writes `sizing.position_quantity` onto the
        decision, and `trade_lifecycle` reads `decision.quantity` straight into
        `DeltaOrderRequest.size`. No second conversion happens in between --
        which is why the `contract_unit` choice reaches the exchange verbatim.
        """
        market = (EXECUTION_DIR / "market_orchestrator.py").read_text(
            encoding="utf-8")
        assert "qualified_decision.quantity = sizing.position_quantity" in market
        lifecycle = (EXECUTION_DIR / "trade_lifecycle.py").read_text(
            encoding="utf-8")
        assert "size=record.requested_quantity," in lifecycle


# ---------------------------------------------------------------------------
# §F  A non-positive `contract_unit` fails closed.
# ---------------------------------------------------------------------------
class TestANonPositiveContractUnitFailsClosed:
    """
    `contract_unit` is a divisor. Zero raised `decimal.DivisionByZero` out of
    the allocator, and a negative value produced a negative `raw_quantity` that
    surfaced as the unrelated "below exchange minimum" message.

    `market_orchestrator` catches only `CapitalAllocationError`, so the zero
    case escaped its scan loop entirely while the same input was absorbed by
    `multi_user_orchestrator`'s broad handler -- the same bad input failed
    closed in one caller and crashed the other. Raising the allocator's own
    error class makes both fail closed identically. No accepted sizing result
    changes: every currently-valid call passes a positive unit.
    """

    @pytest.mark.parametrize("unit", ["0", "-1", "-0.001"])
    def test_it_raises_the_allocators_own_error(self, allocator, unit):
        with pytest.raises(CapitalAllocationError) as excinfo:
            allocator.calculate_100_percent_allocation(
                symbol="BTCUSD",
                entry_price=Decimal("100000"),
                available_balance=Decimal("1000"),
                leverage=10,
                contract_unit=Decimal(unit),
            )
        assert "contract unit" in str(excinfo.value).lower()

    def test_a_positive_unit_is_unaffected(self, allocator):
        res = allocator.calculate_100_percent_allocation(
            symbol="BTCUSD",
            entry_price=Decimal("100000"),
            available_balance=Decimal("1000"),
            leverage=10,
            contract_unit=Decimal("0.001"),
            lot_size_step=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
        )
        assert res.position_quantity == Decimal("98.000")


# ---------------------------------------------------------------------------
# §G  The closed doors stay closed, and stay un-called from production.
# ---------------------------------------------------------------------------
class TestTheUnverifiedConversionStaysClosed:

    def test_the_snapshot_still_declares_the_formula_unverified(self, snapshot):
        reason = snapshot["unverified"]["notional_to_contracts_formula"]
        assert "never states how order size relates to contract_value" in reason
        assert "explicitly" in reason

    @pytest.mark.parametrize("symbol", sorted(DEFAULT_DELTA_INDIA_PRODUCTS))
    def test_the_authority_never_hands_out_a_quantity(self, registry, symbol):
        with pytest.raises(FieldUnverifiedError):
            registry.get(symbol).notional_to_contracts(10_000.0)

    def test_the_manual_smc_converter_has_no_production_caller(self):
        """
        `resolve_order_quantity` is the only sanctioned notional->contracts
        path and it refuses without an injected converter. It must stay
        unreferenced outside tests: a production caller would mean somebody
        supplied a converter, i.e. invented the formula.
        """
        callers = []
        for path in SRC_DIR.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "resolve_order_quantity" not in source:
                continue
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and \
                        isinstance(node.func, ast.Name) and \
                        node.func.id == "resolve_order_quantity":
                    callers.append(f"{path.name}:{node.lineno}")
        assert callers == []

    def test_no_execution_module_defines_a_notional_to_contracts_conversion(
            self):
        for path in EXECUTION_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))
            names = {n.name for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert not [n for n in names if "notional_to_contract" in n], path.name


# ---------------------------------------------------------------------------
# §H  What official Delta material *does* establish about `size`.
# ---------------------------------------------------------------------------
class TestTheDocumentedOrderSizeContract:
    """
    Cited, not inferred. The three official Delta sources, verbatim:

      1. Delta's MCP tool reference (mcp.delta.exchange, `place_order`):
         `size` is typed `int`, required, "order size in contracts
         (positive)"; `edit_order`'s size is "total size after the edit";
         examples read "10 BTCUSD contracts" and "2 contracts each". Only
         prices are tick-rounded there -- never size.
      2. The REST reference (docs.delta.exchange, POST /v2/orders): the example
         body sends `"size": 10` unquoted, and Types->Numbers states "Integer
         numbers (like contract size, product_id and impact size) are
         unquoted". The product example carries `"contract_value": "0.001"`
         with `"contract_unit_currency": "BTC"`.
      3. Delta's fee schedule (www.delta.exchange/fees/): "1 lot = 0.001 BTC"
         for BTCUSD, notional stated as "No. of contracts x Lot size x Index
         Price of BTC", worked as "1000 x 0.001 x $100000 = $100000".

    Together: `size` is a contract count, and
    notional = size x contract_value x price. Nothing here asserts the two
    things Delta still does not publish -- a minimum order size and a size
    increment. Those stay in the policy trio (§A/§B) and stay declared
    unverified by the snapshot and the registry (§G).

    The identity-side structural bans (no product-id literals, no symbol
    literals, no product table outside the registry, no symbol normalisation)
    live in `test_execution_identity_audit.py` §E/§F. This section adds only
    the sizing-side ones: one conversion authority, and call sites that read
    the contract value from that authority rather than restating it.
    """

    def test_the_registry_describes_one_contract_in_base_asset_units(
            self, registry):
        assert registry.get("BTCUSD").one_contract_description == \
            "1 BTCUSD contract = 0.001 BTC"
        assert registry.get("ETHUSD").one_contract_description == \
            "1 ETHUSD contract = 0.01 ETH"

    def test_deltas_worked_notional_example_reproduces_from_the_registry(
            self, registry):
        """Delta's fee page: 1000 x 0.001 x $100000 = $100000."""
        contracts = Decimal("1000")
        price = Decimal("100000")
        contract_value = registry.get("BTCUSD").contract_value
        assert contract_value == Decimal("0.001")
        assert contracts * contract_value * price == Decimal("100000.000")

    def test_the_allocator_inverts_deltas_worked_example(self, allocator,
                                                        registry):
        """
        The same arithmetic run backwards is exactly the allocator's step 3
        (`raw_quantity = max_notional / (entry_price * contract_unit)`) when
        `contract_unit` is the verified contract value. $100,000 of notional at
        $100,000 must be 1000 contracts, not 1 BTC.
        """
        res = allocator.calculate_100_percent_allocation(
            symbol="BTCUSD",
            entry_price=Decimal("100000"),
            available_balance=Decimal("10000"),
            leverage=10,
            contract_unit=registry.get("BTCUSD").contract_value,
            lot_size_step=UNVERIFIED_SIZE_STEP,
            min_quantity=UNVERIFIED_MIN_SIZE,
            custom_safety_buffer=Decimal("100.00"),
        )
        assert res.position_quantity == Decimal("1000")
        assert res.notional_value == Decimal("100000.000")
        assert res.allocated_margin == Decimal("10000.00000000")

    def test_the_gateway_risk_math_already_treats_quantity_as_contracts(self):
        """
        Internal corroboration that predates this audit: the validation
        gateway's own risk and margin checks multiply `quantity` by
        `contract_value` before a price. That is dimensionally correct only if
        `quantity` is a contract count.
        """
        source = (EXECUTION_DIR / "validation.py").read_text(encoding="utf-8")
        assert "request.quantity * contract_val * risk_dist" in source
        assert "notional_value = request.quantity * contract_val * entry" in source

    def test_the_snapshot_fetch_script_records_the_same_reading(self):
        script = " ".join(
            (REPO_ROOT / "engine" / "scripts"
             / "fetch_delta_product_specs.py").read_text(
                 encoding="utf-8").split())
        assert "base-asset meaning of ONE contract (BTCUSD -> 0.001 BTC " \
            "per contract)" in script
        assert "`size` is an integer contract count" in script
        # ...while still recording that the formula itself is unpublished, which
        # is why the snapshot keeps it in `unverified` (§G).
        assert "no published formula ties it to `contract_value`" in script

    def test_the_allocator_is_the_only_sizing_authority_in_the_tree(self):
        defs = []
        for path in SRC_DIR.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node.name == "calculate_100_percent_allocation":
                    defs.append(path.name)
        assert defs == ["capital_allocator.py"]

    def test_no_other_execution_module_divides_by_a_contract_value(self):
        """
        A second `/ contract_value` anywhere in the execution layer would be a
        duplicated conversion authority -- the failure mode that let the two
        callers disagree in the first place.
        """
        offenders = []
        for path in EXECUTION_DIR.glob("*.py"):
            if path.name == "capital_allocator.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.BinOp) or \
                        not isinstance(node.op, ast.Div):
                    continue
                for inner in ast.walk(node.right):
                    name = getattr(inner, "id", None) or \
                        getattr(inner, "attr", None)
                    if name and "contract" in name:
                        offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == []

    def test_both_call_sites_read_the_contract_value_from_the_authority(self):
        market = (EXECUTION_DIR / "market_orchestrator.py").read_text(
            encoding="utf-8")
        assert "DEFAULT_DELTA_INDIA_PRODUCTS.get(qualified_decision.symbol)" \
            in market
        multi = (EXECUTION_DIR / "multi_user_orchestrator.py").read_text(
            encoding="utf-8")
        assert "spec = delta_india_registry().get(symbol)" in multi


# ---------------------------------------------------------------------------
# §I  The upper bound on size is unenforced. Pinned as a gap, not fixed.
# ---------------------------------------------------------------------------
class TestTheRecordedPositionSizeLimitIsNotEnforced:
    """
    The allocator accepts a `max_quantity` and clamps to it, and the snapshot
    records a `position_size_limit` per product. Nothing connects the two:
    neither caller passes `max_quantity`, and no production module reads
    `position_size_limit`.

    This matters more now that sizes are contract counts: at a $100,000 balance
    and 10x, XRPUSD already sizes to 490,000 contracts against a recorded limit
    of 300,000. That predates this audit (XRPUSD's contract value is 1, so the
    `contract_unit` correction did not change it), and `position_size_limit`
    sits in `recorded_not_hashed` -- it moves with Delta's risk configuration
    and is not a verified contract field. Wiring a cap to it would be new
    policy, so the gap is pinned here rather than closed.
    """

    def test_neither_caller_passes_a_max_quantity(self):
        for module in ("market_orchestrator.py", "multi_user_orchestrator.py"):
            (kwargs,) = _allocation_call_kwargs(module)
            assert "max_quantity" not in kwargs, module

    def test_no_production_module_reads_the_recorded_limit(self):
        readers = [path.name for path in SRC_DIR.rglob("*.py")
                   if "position_size_limit" in path.read_text(encoding="utf-8")]
        assert readers == []

    def test_the_limit_is_recorded_not_verified(self, snapshot):
        for symbol, entry in snapshot["products"].items():
            assert "position_size_limit" in entry["recorded_not_hashed"], symbol
            assert "position_size_limit" not in entry["contract_spec"], symbol

    def test_the_clamp_itself_still_works_when_a_bound_is_supplied(
            self, allocator):
        res = allocator.calculate_100_percent_allocation(
            symbol="XRPUSD",
            entry_price=Decimal("2"),
            available_balance=Decimal("100000"),
            leverage=10,
            contract_unit=Decimal("1"),
            lot_size_step=UNVERIFIED_SIZE_STEP,
            min_quantity=UNVERIFIED_MIN_SIZE,
            max_quantity=Decimal("300000"),
        )
        assert res.position_quantity == Decimal("300000")

    def test_the_limits_provenance_is_the_products_payload_and_nothing_more(
            self, snapshot):
        """
        Provenance audit, recorded so the field cannot quietly become policy.

        WHERE IT COMES FROM: it is a genuine `/v2/products` response field. The
        fetch script captures it verbatim at `result.position_size_limit` into
        `margin_and_limits`, alongside the margin rates and leverage numbers,
        and the registry re-exposes it read-only as `InstrumentSpec.recorded`.

        WHAT IT MEANS: not established. Delta's REST reference shows it only
        inside an example product body (`"position_size_limit": 229167`) with no
        description, no unit and no schema-table entry; the MCP market-data
        reference never mentions it; Delta's own support article for the Products
        API lists a generic "Position limits" bullet and no definition. Whether
        it bounds an order, a position, a contract count or a notional is
        therefore unknown from documentation.

        The recorded numbers are consistent with a per-product CONTRACT-COUNT
        cap rather than a notional one -- BTCUSD 125000 contracts is ~$12.5M at
        $100k while XRPUSD 300000 contracts is ~$0.6M at $2 -- but that is
        inference from four data points, not a published definition, so it is
        not acted on. The field stays recorded-and-unenforced.
        """
        paths = snapshot["field_paths"]["margin_and_limits"]
        assert paths["position_size_limit"] == "result.position_size_limit"
        for symbol, entry in snapshot["products"].items():
            limit = entry["margin_and_limits"]["position_size_limit"]
            assert isinstance(limit, int), (symbol, type(limit).__name__)
            assert limit > 0, symbol

    def test_the_registry_exposes_the_limit_only_as_recorded_metadata(
            self, registry):
        spec = registry.get("BTCUSD")
        assert "position_size_limit" in spec.recorded
        assert not hasattr(spec, "position_size_limit")
        with pytest.raises(TypeError):
            spec.recorded["position_size_limit"] = 1


# ---------------------------------------------------------------------------
# §J  The serialization choke point refuses a non-integer contract count.
# ---------------------------------------------------------------------------
def _request(size, symbol: str = "BTCUSD") -> DeltaOrderRequest:
    """An otherwise-valid entry order carrying `size` verbatim."""
    return DeltaOrderRequest(
        product_id=delta_india_registry().get(symbol).product_id,
        product_symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        size=size,
        limit_price=Decimal("77000.0"),
    )


class TestFractionalOrderSizesAreRefusedNotTruncated:
    """
    `DeltaOrderRequest.to_exchange_payload` is the single serialization choke
    point: `DeltaIndiaClient.create_order` (aliased `place_order`) is its only
    production caller, and it is invoked strictly before the POST and is not
    caught there -- so a raise here means nothing was sent.

    It used to fall back to a float for a non-integral size, so
    `Decimal("127.272")` serialized as `127.272`. Delta types `size` as an
    integer contract count (§H), so that request could only be rejected by the
    exchange -- or, worse, accepted with a coerced size nobody chose.

    The refusal is deliberately NOT a truncation. Flooring 127.272 to 127 here
    would change the exposure of an order that was sized and (on the market-scan
    path) gateway-validated at a different number, silently, at the last
    possible moment. Rounding up would exceed the margin the allocator sized
    against. The grid belongs upstream in the allocator, where the number is
    still visible to every downstream check; this layer only refuses what cannot
    be expressed.
    """

    @pytest.mark.parametrize("size,expected", [
        (Decimal("1"), 1),
        (Decimal("127"), 127),
        (Decimal("3500.000"), 3500),
        (Decimal("1E+3"), 1000),
        (1, 1),
        (392, 392),
        (54.0, 54),
    ])
    def test_an_integral_size_serializes_as_that_integer(self, size, expected):
        payload = _request(size).to_exchange_payload()
        assert payload["size"] == expected
        assert type(payload["size"]) is int

    @pytest.mark.parametrize("size", [
        Decimal("127.272"), Decimal("98.492"), Decimal("0.098"),
        Decimal("1.5"), Decimal("0.001"), 54.444, 1.5,
    ])
    def test_a_fractional_size_is_refused(self, size):
        with pytest.raises(OrderSizeContractError) as excinfo:
            _request(size).to_exchange_payload()
        assert "whole number of contracts" in str(excinfo.value)

    @pytest.mark.parametrize("size", [
        Decimal("0"), Decimal("-1"), Decimal("-127"), 0, -5, -1.0,
    ])
    def test_a_non_positive_size_is_refused(self, size):
        with pytest.raises(OrderSizeContractError):
            _request(size).to_exchange_payload()

    @pytest.mark.parametrize("size", [
        None, "10", "", Decimal("NaN"), Decimal("Infinity"),
        Decimal("-Infinity"), float("nan"), float("inf"), True,
    ])
    def test_a_malformed_size_is_refused(self, size):
        with pytest.raises(OrderSizeContractError):
            _request(size).to_exchange_payload()

    def test_the_refusal_happens_before_anything_is_sent(self):
        """
        Structural proof that the check precedes the POST: `create_order` builds
        the payload on an earlier line than the request call, and does not wrap
        it in a `try`.
        """
        source = (EXECUTION_DIR / "delta_client.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        (create,) = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == "create_order"]
        serialize = [n.lineno for n in ast.walk(create)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "to_exchange_payload"]
        post = [n.lineno for n in ast.walk(create)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "request"]
        assert len(serialize) == 1 and len(post) == 1
        assert serialize[0] < post[0]
        assert not [n for n in ast.walk(create) if isinstance(n, ast.Try)]

    def test_unrelated_payload_behaviour_is_unchanged(self):
        """
        Only `size` changed. Identity still comes from the registry and every
        price is still a string.

        Task O §O1 added the two documented stop companions to the *input*: a
        `stop_price` without `stop_order_type` / `stop_trigger_method` is now
        refused locally, so this stop-loss request carries them. The sizing and
        identity assertions the case exists for are untouched.

        §G2 renamed the two attached-bracket keys on the *output* to Delta's
        documented `bracket_stop_loss_price` / `bracket_take_profit_price` and
        added `bracket_stop_trigger_method`; the old spelling was not a
        parameter of POST /v2/orders and created no protection. Values are
        unchanged and still strings.
        """
        req = DeltaOrderRequest(
            product_id=delta_india_registry().get("ETHUSD").product_id,
            product_symbol="ETHUSD",
            side=OrderSide.SELL,
            order_type=OrderType.STOP_MARKET_ORDER,
            size=Decimal("392"),
            stop_price=Decimal("2400.05"),
            stop_order_type=StopOrderType.STOP_LOSS_ORDER,
            stop_trigger_method=StopTriggerMethod.LAST_TRADED_PRICE,
            reduce_only=True,
            client_order_id="SL-abc",
            stop_loss_price=Decimal("2400.05"),
            take_profit_price=Decimal("2700.10"),
        )
        payload = req.to_exchange_payload()
        assert payload["product_id"] == 3136
        assert payload["product_symbol"] == "ETHUSD"
        assert payload["side"] == "sell"
        assert payload["reduce_only"] is True
        assert payload["stop_price"] == "2400.05"
        assert payload["stop_order_type"] == "stop_loss_order"
        assert payload["stop_trigger_method"] == "last_traded_price"
        assert payload["client_order_id"] == "SL-abc"
        assert payload["bracket_stop_loss_price"] == "2400.05"
        assert payload["bracket_take_profit_price"] == "2700.10"
        assert payload["bracket_stop_trigger_method"] == "last_traded_price"

    def test_nothing_in_the_execution_layer_floors_an_order_size(self):
        """
        The only downward quantization of a quantity in the execution layer is
        the allocator's explicit `lot_size_step` stepping, where the number is
        still visible to every downstream check. `models.py` uses `ROUND_DOWN`
        and `math.floor` only to NAME the value it refuses in the error message
        -- proven by the fact that every fractional input above raises.
        """
        rounders = {}
        for path in EXECUTION_DIR.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            hits = [token for token in
                    ("ROUND_DOWN", "ROUND_FLOOR", "math.floor", "math.trunc")
                    if token in source]
            if hits:
                rounders[path.name] = sorted(hits)
        assert set(rounders) == {"capital_allocator.py", "models.py"}

    def test_the_serializer_no_longer_coerces_a_size_to_float(self):
        """
        The removed fallback was `float(self.size)` for a non-integral size --
        the one place a fractional contract count became a wire value. Neither
        the serializer nor the contract-count check may call `float` at all.
        """
        tree = ast.parse((EXECUTION_DIR / "models.py").read_text(
            encoding="utf-8"))
        methods = [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name in ("to_exchange_payload",
                                  "exchange_contract_count")]
        assert {m.name for m in methods} == {"to_exchange_payload",
                                            "exchange_contract_count"}
        for method in methods:
            floats = [ast.unparse(n) for n in ast.walk(method)
                      if isinstance(n, ast.Call)
                      and getattr(n.func, "id", None) == "float"]
            assert floats == [], (method.name, floats)


# ---------------------------------------------------------------------------
# §K  Both execution paths turn the refusal into an outcome, not a crash.
# ---------------------------------------------------------------------------
USER = "usr-sizing-audit"
ACCOUNT = "acct-sizing-audit"


def _lifecycle_store(balance: str = "500.00") -> LocalStateStore:
    store = LocalStateStore()
    store.account.account_id = ACCOUNT
    store.account.user_id = USER
    store.account.total_equity = Decimal(balance)
    store.account.available_balance = Decimal(balance)
    store.account.current_balance = Decimal(balance)
    store.account.last_synced_at = datetime.now(timezone.utc)
    store.account.algo_enabled = True
    store.account.kill_switch_active = False
    store.connection.connection_status = "CONNECTED"
    return store


def _serializing_client() -> MagicMock:
    """A mock whose `place_order` serializes for real, as the live client does."""
    client = MagicMock(spec=DeltaIndiaClient)
    client._api_key = "SIZING_KEY"
    client._api_secret = "SIZING_SECRET"
    client.connection_state = ConnectionState.CONNECTED
    client.serialized: list = []

    async def _place(req: DeltaOrderRequest) -> DeltaOrderResponse:
        # The real `create_order` does exactly this before its POST.
        client.serialized.append(req.to_exchange_payload())
        return DeltaOrderResponse(
            id=990001,
            client_order_id=req.client_order_id,
            user_id=1,
            product_id=req.product_id,
            product_symbol=req.product_symbol,
            side=req.side,
            order_type=req.order_type,
            size=req.size,
            unfilled_size=Decimal("0"),
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            average_fill_price=req.limit_price or Decimal("77000.0"),
            state=OrderStatus.FILLED,
            reduce_only=req.reduce_only,
            created_at=datetime.now(timezone.utc),
        )

    client.place_order = AsyncMock(side_effect=_place)
    client.cancel_order = AsyncMock(return_value=True)
    client.get_positions = AsyncMock(return_value=[])
    client.get_open_orders = AsyncMock(return_value=[])
    client.get_wallet_balances = AsyncMock(return_value=[
        DeltaWalletBalance(
            asset_symbol="USDT",
            balance=Decimal("1000.00"),
            available_balance=Decimal("1000.00"),
            position_margin=Decimal("0"),
            order_margin=Decimal("0"),
            blocked_margin=Decimal("0"),
        )
    ])
    client.get_ticker = AsyncMock(return_value={"mark_price": "77000.0"})

    async def _get_order(order_id: int) -> DeltaOrderResponse:
        return DeltaOrderResponse(
            id=order_id,
            client_order_id=None,
            user_id=1,
            product_id=27,
            product_symbol="BTCUSD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT_ORDER,
            size=Decimal("1"),
            unfilled_size=Decimal("0"),
            limit_price=Decimal("77077.0"),
            stop_price=None,
            average_fill_price=Decimal("77077.0"),
            state=OrderStatus.FILLED,
            reduce_only=False,
            created_at=datetime.now(timezone.utc),
        )

    client.get_order = AsyncMock(side_effect=_get_order)
    client.close = AsyncMock()
    return client


def _decision(quantity: Decimal, setup_id: str) -> StrategyDecision:
    return StrategyDecision(
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSD",
        timeframe="15m",
        direction=StrategyDirection.LONG,
        setup_state=SetupState.TRADE_SETUP_READY,
        setup_id=setup_id,
        entry=Decimal("77000.0"),
        stop_loss=Decimal("76000.0"),
        take_profit=Decimal("79000.0"),
        risk_reward=Decimal("2.0"),
        quantity=quantity,
    )


class _FractionalAllocator(CapitalAllocator):
    """Returns a deliberately fractional contract count.

    The production allocator cannot do this any more (its grid defaults are one
    whole contract), so the only way to prove the multi-user path survives a
    refusal is to inject one.
    """

    def calculate_100_percent_allocation(self, symbol, entry_price,
                                         available_balance, **kwargs):
        return PositionSizingResult(
            symbol=symbol,
            entry_price=entry_price,
            available_balance=available_balance,
            leverage=10,
            contract_unit=Decimal("0.001"),
            allocated_margin=Decimal("980"),
            position_quantity=Decimal("127.272"),
            notional_value=Decimal("9800"),
            effective_leverage=Decimal("9.80"),
            safety_buffer_pct=Decimal("98.00"),
        )


class TestNeitherPathCrashesOnARefusedSize:

    @pytest.mark.asyncio
    async def test_the_market_scan_path_rejects_before_serialization(self):
        """
        Path A never reaches the choke point: the validation gateway's own step
        rule refuses a fractional quantity first ((1.5 - 1) % 1 != 0), so the
        lifecycle returns a structured ENTRY_REJECTED record and releases the
        lock. Two independent refusals, the outer one earlier.
        """
        lock = SingleTradeLockManager()
        client = _serializing_client()
        manager = TradeLifecycleManager(
            client=client,
            validation_gateway=OrderValidationGateway(),
            state_store=_lifecycle_store(),
            sync_service=None,
            algo_config_store=AlgoConfigStore(),
            single_trade_lock=lock,
            capital_allocator=CapitalAllocator(),
            execution_mode=ExecutionMode.LIVE,
        )

        record = await manager.execute_trade_setup(
            _decision(Decimal("1.5"), "setup-fractional"), ACCOUNT, USER)

        assert record.state == TradeLifecycleState.ENTRY_REJECTED
        assert record.rejection_code == \
            RejectionReasonCode.INVALID_QUANTITY_STEP.value
        client.place_order.assert_not_awaited()
        assert client.serialized == []
        assert lock.is_locked(USER, ACCOUNT)[0] is False

    @pytest.mark.asyncio
    async def test_the_multi_user_path_returns_an_error_and_frees_the_lock(self):
        """
        Path B does not use the validation gateway, so for it the choke point IS
        the guard. An injected fractional size raises `OrderSizeContractError`
        out of `place_order`; the session's broad handler converts it into
        `status="ERROR"`, releases the lock and closes the client. The dispatch
        loop keeps running and no order was sent.
        """
        lock = SingleTradeLockManager()
        client = _serializing_client()
        config = UserAccountConfig(
            user_id=USER,
            account_id=ACCOUNT,
            is_active=True,
            algo_enabled=True,
            kill_switch_active=False,
            api_key="KEY",
            api_secret="SECRET",
            client_factory=lambda k, s: client,
        )
        session = UserExecutionSession(config, lock, _FractionalAllocator())

        result = await session.execute_trade(
            setup_id="setup-mu-fractional",
            symbol="BTCUSD",
            direction=TradeDirection.LONG,
            planned_entry_price=Decimal("77000"),
            stop_loss_price=Decimal("76000"),
            take_profit_price=Decimal("79000"),
        )

        assert result.status == "ERROR"
        assert "whole number of contracts" in result.error
        assert client.serialized == []
        assert lock.is_locked(USER, ACCOUNT)[0] is False
        client.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_the_multi_user_path_still_executes_a_whole_contract_size(
            self):
        """The control: the real allocator's size serializes as an integer."""
        lock = SingleTradeLockManager()
        client = _serializing_client()
        config = UserAccountConfig(
            user_id=USER,
            account_id=ACCOUNT,
            is_active=True,
            algo_enabled=True,
            kill_switch_active=False,
            api_key="KEY",
            api_secret="SECRET",
            client_factory=lambda k, s: client,
        )
        session = UserExecutionSession(config, lock, CapitalAllocator())

        result = await session.execute_trade(
            setup_id="setup-mu-whole",
            symbol="BTCUSD",
            direction=TradeDirection.LONG,
            planned_entry_price=Decimal("77000"),
            stop_loss_price=Decimal("76000"),
            take_profit_price=Decimal("79000"),
        )

        assert result.status == "EXECUTED"
        assert result.allocated_quantity == \
            result.allocated_quantity.to_integral_value()
        assert len(client.serialized) == 3
        for payload in client.serialized:
            assert type(payload["size"]) is int
            assert payload["size"] == int(result.allocated_quantity)


