"""
The authoritative leverage band: 1x <= leverage <= 100x, fail-closed.
=====================================================================

The owner authorised one band for every symbol: **1x minimum, 100x maximum,
both inclusive**. Anything below 1 and anything above 100 is REFUSED — never
clamped into range, never substituted, never silently defaulted.

Before this file existed the band was four drifted literals. The live
consequence was a hidden 50x ceiling: `UNVERIFIED_MAX_LEVERAGE` carried
`SOLUSD: 50, XRPUSD: 50` (retained from the pre-registry gateway), gateway
check 14 folded it into `min(spec.max_leverage, risk_config.max_leverage)`, and
both dispatch paths reached it. A requested 100x was therefore accepted on
BTCUSD/ETHUSD and rejected on SOLUSD/XRPUSD. Two further violations rode along:
`request.leverage or 1` turned an explicit `leverage=0` into an ACCEPTED 1x, and
`AlgoConfiguration.update()` stored 0 / -1 / 101 / 1000 entirely unchecked.

This file pins the corrected contract through the REAL production entry points —
the gateway, the config object, its store, the frozen snapshot, the allocator —
not through a re-declared constant.

Not asserted here, because it would be invention: any exchange-side leverage
ceiling. Delta India publishes none, `max_leverage` stays in
`quantedge.instruments.PERMANENTLY_UNVERIFIED`, and the engine transmits no
leverage field at all (pinned in section J). The band is LOCAL POLICY.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pytest

from quantedge.execution.algo_config import (
    AlgoConfigStore,
    AlgoConfiguration,
    AlgoConfigurationSnapshot,
    AlgoConfigValidationError,
)
from quantedge.execution.capital_allocator import (
    CapitalAllocationError,
    CapitalAllocator,
)
from quantedge.execution.leverage import (
    MAX_LEVERAGE,
    MIN_LEVERAGE,
    LeverageBandError,
    is_within_band,
    normalize_requested_leverage,
    validate_leverage,
)
from quantedge.execution.models import DeltaOrderRequest
from quantedge.execution.synchronizer import AccountRecord, ConnectionRecord
from quantedge.execution.validation import (
    DEFAULT_DELTA_INDIA_PRODUCTS,
    UNVERIFIED_MAX_LEVERAGE,
    UNVERIFIED_MAX_LEVERAGE_FALLBACK,
    OrderValidationGateway,
    OrderValidationRequest,
    ProductSpecification,
    RejectionReasonCode,
    RiskConfiguration,
    ValidationContext,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENGINE_SRC = REPO_ROOT / "engine" / "src" / "quantedge"
LEVERAGE_PY = ENGINE_SRC / "execution" / "leverage.py"
JAVA_REGISTRY = (REPO_ROOT / "backend" / "src" / "main" / "java" / "com"
                 / "quantedge" / "market" / "service" / "InstrumentRegistry.java")
FRONTEND_INSTRUMENTS = (REPO_ROOT / "user-app" / "src" / "constants"
                        / "instruments.ts")

USER = "usr-band"
ACCOUNT = "acct-band"

#: symbol -> (entry, stop_loss, take_profit). Every price is tick-aligned for
#: its symbol and every geometry clears the minimum 1.5 R:R, so the ONLY check
#: these requests can fail is the leverage band.
GEOMETRY = {
    "BTCUSD": (Decimal("60000.0"), Decimal("59000.0"), Decimal("62000.0")),
    "ETHUSD": (Decimal("3000.00"), Decimal("2950.00"), Decimal("3100.00")),
    "SOLUSD": (Decimal("180.0000"), Decimal("175.0000"), Decimal("195.0000")),
    "XRPUSD": (Decimal("2.5000"), Decimal("2.4000"), Decimal("2.7000")),
}
SYMBOLS = sorted(GEOMETRY)

#: Accepted verbatim. 50 and 51 are here on purpose: they straddle the ceiling
#: that used to exist, so a returning 50x cap fails this file loudly.
IN_BAND = (1, 2, 25, 50, 51, 99, 100)

#: Refused. Nothing in this tuple may be rounded, clamped or defaulted into the
#: band — `0 -> 1` and `101 -> 100` are both violations.
OUT_OF_BAND = (0, -1, -50, -100, 101, 150, 1000, 100000)


def _account(available: Decimal = Decimal("100000.00"),
             equity: Decimal = Decimal("100000.00")) -> AccountRecord:
    return AccountRecord(
        account_id=ACCOUNT,
        base_currency="USDT",
        current_balance=equity,
        available_balance=available,
        margin_used=equity - available,
        total_equity=equity,
        is_active=True,
    )


def _context(risk_config: RiskConfiguration | None = None,
             account: AccountRecord | None = None) -> ValidationContext:
    return ValidationContext(
        account=account if account is not None else _account(),
        algo_enabled=True,
        kill_switch_active=False,
        connection=ConnectionRecord(
            connection_status="CONNECTED",
            last_connected_at=datetime.now(timezone.utc),
        ),
        api_key="band_api_key_1234567890",
        api_secret="band_api_secret_0987654321",
        risk_config=risk_config if risk_config is not None else RiskConfiguration(),
        open_positions=[],
        open_orders=[],
        active_client_order_ids=set(),
        active_setup_ids=set(),
    )


def _request(symbol: str, leverage, quantity: Decimal = Decimal("1")
             ) -> OrderValidationRequest:
    """Valid in every respect except, possibly, its leverage."""
    entry, stop, target = GEOMETRY[symbol]
    return OrderValidationRequest(
        account_id=ACCOUNT,
        symbol=symbol,
        direction="BUY",
        order_type="LIMIT_ORDER",
        quantity=quantity,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        leverage=leverage,
        setup_id=f"band-{symbol}-{leverage}",
    )


def _validate(symbol: str, leverage, **kwargs):
    return OrderValidationGateway().validate(
        _request(symbol, leverage, quantity=kwargs.pop("quantity", Decimal("1"))),
        _context(**kwargs),
    )


def _config(**overrides) -> AlgoConfiguration:
    params = {"account_id": ACCOUNT, "user_id": USER,
              "algo_enabled": True, "kill_switch_active": False}
    params.update(overrides)
    return AlgoConfiguration(**params)


def _snapshot(max_leverage: int) -> AlgoConfigurationSnapshot:
    return AlgoConfigurationSnapshot(
        setup_id="band-snap",
        account_id=ACCOUNT,
        user_id=USER,
        version=1,
        take_profit_pct=Decimal("2.00"),
        stop_loss_pct=Decimal("1.00"),
        risk_per_trade_pct=Decimal("1.00"),
        max_risk_usd=None,
        max_daily_loss_usd=Decimal("500.00"),
        max_leverage=max_leverage,
        algo_enabled_at_snapshot=True,
        kill_switch_active_at_snapshot=False,
    )


def _source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# A. THE BAND MODULE — one definition, no dependencies
# ═════════════════════════════════════════════════════════════════════════════
def test_the_band_is_one_through_one_hundred_inclusive():
    assert MIN_LEVERAGE == 1
    assert MAX_LEVERAGE == 100


@pytest.mark.parametrize("leverage", IN_BAND)
def test_an_in_band_integer_is_returned_verbatim(leverage):
    assert validate_leverage(leverage) == leverage
    assert is_within_band(leverage) is True


@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_an_out_of_band_integer_is_refused_not_adjusted(leverage):
    assert is_within_band(leverage) is False
    with pytest.raises(LeverageBandError) as excinfo:
        validate_leverage(leverage)
    assert str(leverage) in str(excinfo.value)


def test_below_the_band_is_not_promoted_to_one():
    """`0` and `-1` were both silently readable as 1x before this band."""
    for leverage in (0, -1):
        with pytest.raises(LeverageBandError):
            validate_leverage(leverage)


def test_above_the_band_is_not_clamped_to_the_maximum():
    """`101 -> 100` would hide a rejected request behind an executed one."""
    for leverage in (101, 1000):
        with pytest.raises(LeverageBandError):
            validate_leverage(leverage)


@pytest.mark.parametrize("leverage", [
    None, "50", "", 50.0, 1.5, Decimal("50"), [50], (50,), {"leverage": 50},
    object(),
])
def test_a_non_integer_is_refused(leverage):
    assert is_within_band(leverage) is False
    with pytest.raises(LeverageBandError):
        validate_leverage(leverage)


def test_the_refusal_names_the_field_it_was_given():
    with pytest.raises(LeverageBandError) as excinfo:
        validate_leverage(101, field_name="RiskConfiguration.max_leverage")
    assert "RiskConfiguration.max_leverage" in str(excinfo.value)


def test_the_refusal_is_a_value_error():
    """Callers that already guard `ValueError` keep working unchanged."""
    assert issubclass(LeverageBandError, ValueError)


def test_the_band_module_imports_nothing_from_the_package():
    """
    Deliberate: `execution.validation` loads the product snapshot from disk at
    import time. If the band lived there, `algo_config` — which needs only two
    integers — would inherit a snapshot-on-disk requirement.
    """
    imported = set()
    for node in ast.walk(ast.parse(_source(LEVERAGE_PY))):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not {n for n in imported if n.startswith("quantedge")}, imported


# ═════════════════════════════════════════════════════════════════════════════
# B. THE GATEWAY — the real production path, every symbol
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("leverage", IN_BAND)
def test_the_gateway_accepts_the_whole_band_on_every_symbol(symbol, leverage):
    result = _validate(symbol, leverage)
    assert result.is_valid is True, result.rejection_reason
    assert result.rejection_code is None


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_one_x_is_accepted_on_every_symbol(symbol):
    assert _validate(symbol, MIN_LEVERAGE).is_valid is True


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_one_hundred_x_is_accepted_on_every_symbol(symbol):
    """SOLUSD and XRPUSD refused this before the correction."""
    assert _validate(symbol, MAX_LEVERAGE).is_valid is True


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_the_gateway_refuses_out_of_band_on_every_symbol(symbol, leverage):
    result = _validate(symbol, leverage)
    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE
    assert result.failed_check == "CHECK_LEVERAGE_CAP"


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_zero_leverage_is_refused_not_read_as_one(symbol):
    """
    `request.leverage or 1` ACCEPTED an explicit `leverage=0` as a 1x order.
    The Java twin (`OrderValidationGateway.java`) tests `!= null` and rejects
    0, which is what proved the Python `or` a defect rather than parity.
    """
    result = _validate(symbol, 0)
    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE
    assert "below the minimum" in result.rejection_reason


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_one_hundred_and_one_is_refused_though_one_hundred_passes(symbol):
    assert _validate(symbol, 100).is_valid is True
    refused = _validate(symbol, 101)
    assert refused.is_valid is False
    assert "101" in refused.rejection_reason


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_only_none_means_unset(symbol):
    """`None` is 'no leverage supplied' and defaults to the minimum, 1x."""
    assert _validate(symbol, None).is_valid is True


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_no_symbol_carries_a_cap_below_the_band(symbol):
    assert UNVERIFIED_MAX_LEVERAGE[symbol] == MAX_LEVERAGE
    assert DEFAULT_DELTA_INDIA_PRODUCTS[symbol].max_leverage == MAX_LEVERAGE


def test_the_unlisted_symbol_fallback_is_the_strictest_named_cap():
    assert UNVERIFIED_MAX_LEVERAGE_FALLBACK == min(UNVERIFIED_MAX_LEVERAGE.values())
    assert UNVERIFIED_MAX_LEVERAGE_FALLBACK == MAX_LEVERAGE


# ═════════════════════════════════════════════════════════════════════════════
# C. NO SILENT CLAMP — proven by the margin the gateway actually charges
# ═════════════════════════════════════════════════════════════════════════════
#
# `validate()` divides notional by the *effective* leverage
# (`required_margin = notional / leverage`) and rejects when that exceeds the
# available balance. Sizing the balance to sit between notional/100 and
# notional/50 turns the divisor into an observable: if anything clamped 100x
# down to 50x, the 100x request would fail INSUFFICIENT_BALANCE instead of
# passing. This is the strongest available evidence, because the engine sends
# no leverage field to the exchange for a payload assertion to read.

#: SOLUSD @ 180, contract_value 1, 1000 contracts -> 180,000 USDT notional.
#: margin at 100x = 1,800 ; at 50x = 3,600 ; at 1x = 180,000.
_CLAMP_QTY = Decimal("1000")
_CLAMP_NOTIONAL = Decimal("180000")


def test_a_hundred_x_request_is_charged_hundred_x_margin():
    result = _validate(
        "SOLUSD", 100, quantity=_CLAMP_QTY,
        account=_account(available=Decimal("2000"), equity=Decimal("100000")),
    )
    assert result.is_valid is True, result.rejection_reason
    assert _CLAMP_NOTIONAL / Decimal("100") < Decimal("2000")


def test_the_same_notional_is_unaffordable_at_fifty_x():
    """
    The control that isolates the divisor. Narrow the effective band to 50 and
    request exactly 50x, so check 14 passes and the margin check is what runs:
    180,000 / 50 = 3,600 > 2,000, and the order is refused for money.

    The previous test sent 100x against the same notional and the same 2,000
    balance and was ACCEPTED, which is only possible if the divisor was the
    requested 100. Together the two prove the requested leverage reaches the
    margin arithmetic unclamped.
    """
    result = _validate(
        "SOLUSD", 50, quantity=_CLAMP_QTY,
        risk_config=RiskConfiguration(max_leverage=50),
        account=_account(available=Decimal("2000"), equity=Decimal("100000")),
    )
    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.INSUFFICIENT_BALANCE
    assert result.failed_check == "CHECK_AVAILABLE_MARGIN"


def test_an_over_band_request_is_refused_before_the_margin_check():
    """Ordering: the band is enforced ahead of affordability, not after it."""
    result = _validate(
        "SOLUSD", 101, quantity=_CLAMP_QTY,
        account=_account(available=Decimal("1"), equity=Decimal("100000")),
    )
    assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE
    assert result.failed_check == "CHECK_LEVERAGE_CAP"


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_an_accepted_request_keeps_the_leverage_it_asked_for(symbol):
    """The request object is not rewritten on the way through validation."""
    request = _request(symbol, 100)
    assert OrderValidationGateway().validate(request, _context()).is_valid is True
    assert request.leverage == 100


# ═════════════════════════════════════════════════════════════════════════════
# D. A STRICTER RISK CONFIG STILL WINS — the band is a ceiling, not a floor
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_a_narrower_risk_config_narrows_the_accepted_range(symbol):
    context_kwargs = {"risk_config": RiskConfiguration(max_leverage=10)}
    assert _validate(symbol, 10, **context_kwargs).is_valid is True
    refused = _validate(symbol, 11, **context_kwargs)
    assert refused.is_valid is False
    assert "10x" in refused.rejection_reason


def test_the_minimum_is_not_configurable_away():
    """No risk config can authorise a sub-1x order."""
    result = _validate("BTCUSD", 0,
                       risk_config=RiskConfiguration(max_leverage=1))
    assert result.is_valid is False


# ═════════════════════════════════════════════════════════════════════════════
# E. THE ALGO CONFIG — construction, update, and the store
# ═════════════════════════════════════════════════════════════════════════════
def test_the_algo_config_default_is_the_band_maximum():
    assert _config().max_leverage == MAX_LEVERAGE


@pytest.mark.parametrize("leverage", IN_BAND)
def test_the_algo_config_accepts_the_whole_band(leverage):
    assert _config(max_leverage=leverage).max_leverage == leverage


@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_the_algo_config_refuses_out_of_band_at_construction(leverage):
    with pytest.raises(AlgoConfigValidationError):
        _config(max_leverage=leverage)


@pytest.mark.parametrize("leverage", IN_BAND)
def test_update_accepts_the_whole_band(leverage):
    config = _config()
    config.update(max_leverage=leverage)
    assert config.max_leverage == leverage
    assert config.version == 2


@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_a_refused_update_changes_neither_value_nor_version(leverage):
    """
    `update()` re-validated nothing, so 0 / -1 / 101 / 1000 were all stored
    even though the constructor refused them. The check now runs BEFORE any
    assignment, so a refused update leaves the object exactly as it was — the
    version does not advance, and no trade snapshot can bind a bad band.
    """
    config = _config(max_leverage=25)
    with pytest.raises(AlgoConfigValidationError):
        config.update(max_leverage=leverage)
    assert config.max_leverage == 25
    assert config.version == 1


def test_an_unrelated_update_leaves_the_leverage_alone():
    config = _config(max_leverage=40)
    config.update(take_profit_pct=Decimal("3.00"))
    assert config.max_leverage == 40
    assert config.version == 2


@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_the_store_refuses_out_of_band(leverage):
    store = AlgoConfigStore()
    store.get_or_create_default(USER, ACCOUNT)
    with pytest.raises(AlgoConfigValidationError):
        store.update_config(USER, ACCOUNT, max_leverage=leverage)
    assert store.get_config(USER, ACCOUNT).max_leverage == MAX_LEVERAGE


@pytest.mark.parametrize("leverage", (1, 50, 100))
def test_the_store_accepts_in_band(leverage):
    store = AlgoConfigStore()
    store.get_or_create_default(USER, ACCOUNT)
    assert store.update_config(USER, ACCOUNT,
                               max_leverage=leverage).max_leverage == leverage


def test_a_dict_without_a_leverage_key_defaults_to_the_band_maximum():
    data = _config().to_dict()
    del data["max_leverage"]
    assert AlgoConfiguration.from_dict(data).max_leverage == MAX_LEVERAGE


@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_from_dict_refuses_an_out_of_band_stored_value(leverage):
    """A tampered or legacy export cannot reintroduce a bad band on load."""
    data = _config().to_dict()
    data["max_leverage"] = leverage
    with pytest.raises(AlgoConfigValidationError):
        AlgoConfiguration.from_dict(data)


def test_a_round_trip_preserves_an_in_band_value():
    restored = AlgoConfiguration.from_dict(_config(max_leverage=73).to_dict())
    assert restored.max_leverage == 73


# ═════════════════════════════════════════════════════════════════════════════
# F. THE FROZEN SNAPSHOT — the value `trade_lifecycle` dispatches on
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("leverage", IN_BAND)
def test_the_snapshot_accepts_the_whole_band(leverage):
    assert _snapshot(leverage).max_leverage == leverage


@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_the_snapshot_refuses_out_of_band(leverage):
    with pytest.raises(AlgoConfigValidationError):
        _snapshot(leverage)


@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_the_snapshot_from_dict_refuses_out_of_band(leverage):
    data = _snapshot(50).to_dict()
    data["max_leverage"] = leverage
    with pytest.raises(AlgoConfigValidationError):
        AlgoConfigurationSnapshot.from_dict(data)


def test_a_created_snapshot_carries_the_configs_band():
    config = _config(max_leverage=88)
    assert config.create_snapshot(setup_id="s-1").max_leverage == 88


@pytest.mark.parametrize("leverage", IN_BAND)
def test_no_constructible_snapshot_can_break_the_lifecycle_risk_config(leverage):
    """
    WHY the snapshot is validated at construction and not only at dispatch:
    `TradeLifecycleManager` builds `RiskConfiguration(max_leverage=...)` from
    `snapshot.max_leverage` with NO surrounding try/except. Validating the
    snapshot is what makes that call provably unable to raise; validating only
    `RiskConfiguration` would have moved the failure into the middle of a
    dispatch, with the single-trade lock still held.
    """
    snapshot = _snapshot(leverage)
    assert RiskConfiguration(
        max_leverage=snapshot.max_leverage or MAX_LEVERAGE
    ).max_leverage == leverage


# ═════════════════════════════════════════════════════════════════════════════
# G. RISK CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════
def test_the_risk_config_default_is_the_band_maximum():
    assert RiskConfiguration().max_leverage == MAX_LEVERAGE


@pytest.mark.parametrize("leverage", IN_BAND)
def test_the_risk_config_accepts_the_whole_band(leverage):
    assert RiskConfiguration(max_leverage=leverage).max_leverage == leverage


@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_the_risk_config_refuses_out_of_band(leverage):
    """
    It validated nothing at all. A stored 0 or -1 made `min(spec, risk_config)`
    zero and silently blocked every order; a stored 101+ was absorbed only for
    as long as the per-symbol table happened to be the stricter of the two.
    """
    with pytest.raises(LeverageBandError):
        RiskConfiguration(max_leverage=leverage)


def test_the_product_specification_default_is_the_band_maximum():
    assert ProductSpecification.max_leverage == MAX_LEVERAGE


# ═════════════════════════════════════════════════════════════════════════════
# H. THE CAPITAL ALLOCATOR
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("leverage", IN_BAND)
def test_the_allocator_accepts_the_band_and_reports_it_verbatim(leverage):
    result = CapitalAllocator().calculate_100_percent_allocation(
        symbol="BTCUSD",
        entry_price=Decimal("60000.0"),
        available_balance=Decimal("100000"),
        leverage=leverage,
        contract_unit=Decimal("0.001"),
    )
    assert result.leverage == leverage
    assert result.position_quantity > 0


@pytest.mark.parametrize("leverage", OUT_OF_BAND)
def test_the_allocator_refuses_out_of_band(leverage):
    with pytest.raises(CapitalAllocationError):
        CapitalAllocator().calculate_100_percent_allocation(
            symbol="BTCUSD",
            entry_price=Decimal("60000.0"),
            available_balance=Decimal("100000"),
            leverage=leverage,
            contract_unit=Decimal("0.001"),
        )


def test_the_roe_take_profit_still_refuses_sub_one_leverage():
    with pytest.raises(CapitalAllocationError):
        CapitalAllocator.calculate_roe_take_profit(
            entry_price=Decimal("60000.0"), direction="LONG", leverage=0)


def test_the_derived_leverage_cap_defaults_to_the_band_maximum():
    """
    `calculate_leverage_from_stop_distance` CLAMPS a *computed* leverage — that
    is the documented sizing model and is left as it is. Only its default cap
    is pinned here, so the derived path cannot ceiling below the band.
    """
    assert CapitalAllocator.calculate_leverage_from_stop_distance(
        entry_price=Decimal("60000"), stop_loss_price=Decimal("59700"),
    ) <= MAX_LEVERAGE


# ═════════════════════════════════════════════════════════════════════════════
# I. NO DRIFT — every declaring site agrees, in all three languages
# ═════════════════════════════════════════════════════════════════════════════
def test_the_python_config_defaults_agree_with_the_band():
    """
    `Settings` and `StrategyConfig` declare `100` as a plain literal. Neither
    imports from `execution` — that would invert the dependency direction — so
    this guard is what keeps them from drifting apart again.
    """
    from quantedge.config import Settings
    from quantedge.strategy.models import StrategyConfig

    assert StrategyConfig().max_leverage == MAX_LEVERAGE
    assert Settings.model_fields["max_leverage"].default == MAX_LEVERAGE


def test_the_java_registry_declares_the_same_band():
    """A source-level guard, because pytest cannot compile Java.

    The Java side is separately compiled and tested offline with
    `backend/mvnw -o test -Dtest=InstrumentRegistryTest`, which is the
    authoritative check on its behaviour. This test only stops the two
    declarations from drifting apart without anyone noticing, so that a
    Python-only change can never silently leave the backend on 50x.
    """
    source = _source(JAVA_REGISTRY)
    assert f"public static final int MAX_LEVERAGE = {MAX_LEVERAGE};" in source
    assert source.count("MAX_LEVERAGE,") == 4, "one row per instrument"
    # The four rows are positional, so a reintroduced cap would appear as a
    # bare `50,` on its own line in the constructor call.
    bare = [n for n, line in enumerate(source.splitlines(), 1)
            if line.strip() in ("50", "50,")]
    assert bare == [], f"a positional 50x leverage literal returned: {bare}"


def test_the_java_test_pins_the_uniform_band():
    java_test = (REPO_ROOT / "backend" / "src" / "test" / "java" / "com"
                 / "quantedge" / "market" / "InstrumentRegistryTest.java")
    source = _source(java_test)
    assert f"MAX_LEVERAGE).isEqualTo({MAX_LEVERAGE})" in source
    assert "isEqualTo(50)" not in source


def test_the_frontend_declares_the_same_band_on_every_instrument():
    """
    `OrderTicketCard` binds the leverage slider's `max` to this field and
    clamps the held value down to it, so a 50 here made 100x unreachable in the
    UI even once the gateway accepted it.
    """
    source = _source(FRONTEND_INSTRUMENTS)
    assert source.count(f"maxLeverage: {MAX_LEVERAGE}") == 4
    assert "maxLeverage: 50" not in source


def _leverage_named_literal_assignments(root: pathlib.Path, value: int,
                                        suffix_only: bool = False) -> dict:
    """`file:line -> target name` for `<something_leverage> = <value>`.

    Covers plain assignments AND annotated dataclass fields (`max_leverage:
    int = 50`), which is the form every one of these bounds actually took.
    """
    offenders = {}
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(_source(path))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if not (isinstance(node.value, ast.Constant)
                    and node.value.value == value
                    and not isinstance(node.value.value, bool)):
                continue
            for target in targets:
                name = getattr(target, "attr", getattr(target, "id", ""))
                matched = (name.lower().endswith("max_leverage") if suffix_only
                           else "leverage" in name.lower())
                if matched:
                    offenders[f"{path.name}:{node.lineno}"] = name
    return offenders


def test_no_production_module_still_names_a_fifty_x_leverage_cap():
    """
    Structural sweep of every module that DECLARES a leverage bound. A literal
    50 bound to a leverage name is the exact defect this change removed.
    """
    offenders = _leverage_named_literal_assignments(ENGINE_SRC, 50)
    assert offenders == {}, f"a 50x leverage cap survives: {offenders}"


def test_the_band_is_declared_exactly_once_in_python():
    """
    Every other execution module must reference `MAX_LEVERAGE`, not repeat the
    integer. `leverage.py` itself is the definition and is exempt; `config.py`
    and `strategy/models.py` are outside `execution/` and are guarded by value
    above instead, because importing from `execution` would invert the
    dependency direction.
    """
    offenders = _leverage_named_literal_assignments(
        ENGINE_SRC / "execution", MAX_LEVERAGE, suffix_only=True)
    offenders = {k: v for k, v in offenders.items()
                 if not k.startswith(LEVERAGE_PY.name + ":")}
    assert offenders == {}, f"the band is re-declared in {offenders}"


# ═════════════════════════════════════════════════════════════════════════════
# J. WHAT THIS CHANGE DELIBERATELY DID NOT TOUCH
# ═════════════════════════════════════════════════════════════════════════════
def test_no_leverage_field_reaches_the_exchange():
    """
    The band is LOCAL policy. `DeltaOrderRequest` has no leverage field and the
    serialized payload has no leverage key, so nothing in this correction
    changed what is transmitted to Delta.
    """
    assert "leverage" not in {f.name for f in fields(DeltaOrderRequest)}

    from quantedge.execution.models import OrderSide, OrderType

    payload = DeltaOrderRequest(
        product_id=14823, product_symbol="SOLUSD", side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER, size=Decimal("1"),
        limit_price=Decimal("180.0000"),
    ).to_exchange_payload()
    assert not [k for k in payload if "leverage" in k.lower()]


def test_the_derived_leverage_clamp_is_still_a_clamp():
    """
    `strategy/risk.py` narrows a COMPUTED leverage with `min(...)`. That is the
    documented sizing model, distinct from the requested-leverage contract, and
    was intentionally left alone.
    """
    source = _source(ENGINE_SRC / "strategy" / "risk.py")
    assert "min(int(required_leverage), self.config.max_leverage)" in source


def test_manual_smc_take_profit_is_untouched():
    """Guards the 0.60% invariant against collateral edits."""
    from quantedge.strategy.manual_smc.models import (
        MANUAL_SMC_FIXED_TP_PCT,
        ManualSpecConfig,
    )
    assert MANUAL_SMC_FIXED_TP_PCT == 0.60
    assert ManualSpecConfig().fixed_tp_market_pct == 0.60


def test_the_other_risk_limits_are_untouched():
    risk = RiskConfiguration()
    assert risk.risk_per_trade_pct == Decimal("35.0")
    assert risk.target_reward_pct == Decimal("60.0")
    assert risk.minimum_risk_reward == Decimal("1.5")
    assert risk.max_concurrent_trades == 1
    assert risk.max_daily_loss_pct is None


# ═════════════════════════════════════════════════════════════════════════════
# L. `bool` IS NOT A LEVERAGE
# ═════════════════════════════════════════════════════════════════════════════
# `isinstance(True, int)` is True in Python. Without an explicit guard `True`
# reads as a valid 1x and `False` as an out-of-band 0 -- an ambiguous value
# answering a safety question, which rule 13 forbids.
@pytest.mark.parametrize("leverage", [True, False])
def test_a_bool_is_not_a_leverage(leverage):
    assert is_within_band(leverage) is False
    with pytest.raises(LeverageBandError):
        validate_leverage(leverage)
    with pytest.raises(LeverageBandError):
        normalize_requested_leverage(leverage)


def test_true_is_not_read_as_one_x():
    """The failure mode this guards: `True` passing as an in-band 1x."""
    assert is_within_band(1) is True
    assert is_within_band(True) is False


def test_false_is_not_read_as_zero_x():
    """`False == 0`, so an unguarded `False` would be refused for the WRONG
    reason -- as an out-of-band number rather than as a wrong type."""
    with pytest.raises(LeverageBandError) as excinfo:
        validate_leverage(False)
    assert "bool" in str(excinfo.value)


# ═════════════════════════════════════════════════════════════════════════════
# M. `normalize_requested_leverage` — the SUBMITTED-REQUEST entry point
# ═════════════════════════════════════════════════════════════════════════════
# A stored ceiling and a submitted request are not the same question. A request
# has crossed a JSON or UI boundary, so `100.0` and `Decimal("100")` are the
# same request as `100`; a stored ceiling that arrives as a float means
# something upstream lost the type. Hence two entry points, not one.
@pytest.mark.parametrize("leverage", [1, 2, 25, 50, 51, 99, 100])
def test_an_int_request_is_returned_verbatim(leverage):
    assert normalize_requested_leverage(leverage) == leverage


@pytest.mark.parametrize("leverage,expected", [
    (1.0, 1), (2.0, 2), (50.0, 50), (100.0, 100),
    (Decimal("1"), 1), (Decimal("50"), 50), (Decimal("100"), 100),
    (Decimal("100.00"), 100), (Decimal("1E+2"), 100),
])
def test_an_integral_float_or_decimal_normalises_to_its_int(leverage, expected):
    result = normalize_requested_leverage(leverage)
    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize("leverage", [
    0.5, 1.5, 2.5, 50.5, 99.5, 99.9999, 100.5, 100.0000001,
    Decimal("0.5"), Decimal("1.5"), Decimal("50.5"), Decimal("99.99"),
    Decimal("100.0000000001"),
])
def test_a_fractional_request_is_refused_not_rounded(leverage):
    """`OrderValidationRequest.leverage` is `Optional[int]` and the column is
    INTEGER, so 1.5x is not a leverage this repository can express. Refusing is
    the only answer that does not invent one."""
    with pytest.raises(LeverageBandError) as excinfo:
        normalize_requested_leverage(leverage)
    assert "whole number" in str(excinfo.value)


@pytest.mark.parametrize("leverage", [
    float("nan"), float("inf"), float("-inf"),
    Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"),
])
def test_nan_and_infinity_are_refused(leverage):
    with pytest.raises(LeverageBandError):
        normalize_requested_leverage(leverage)


def test_why_nan_must_be_refused_before_the_band_comparison():
    """A band check ALONE lets NaN through: `nan < 1` is False and
    `nan > 100` is False, so neither bound reports it as out of range. This is
    the mechanism by which `leverage=float("nan")` used to reach the margin
    arithmetic and raise `decimal.InvalidOperation` out of gateway check 16 --
    an unhandled exception, which is not a rejection."""
    nan = float("nan")
    assert (nan < MIN_LEVERAGE) is False
    assert (nan > MAX_LEVERAGE) is False
    with pytest.raises(LeverageBandError):
        normalize_requested_leverage(nan)


def test_decimal_nan_cannot_even_be_compared():
    """A second, different hazard: `decimal` raises on an ordering comparison
    with NaN rather than answering False, so a band check would not merely
    mis-answer -- it would throw from inside the validator."""
    with pytest.raises(InvalidOperation):
        Decimal("NaN") < MIN_LEVERAGE  # noqa: B015
    with pytest.raises(LeverageBandError):
        normalize_requested_leverage(Decimal("NaN"))


@pytest.mark.parametrize("leverage", [
    None, "100", "50", "", "abc", [100], (100,), {"leverage": 100},
    object(), bytes(b"100"),
])
def test_a_non_numeric_request_is_refused(leverage):
    with pytest.raises(LeverageBandError):
        normalize_requested_leverage(leverage)


def test_normalisation_deliberately_does_not_range_check():
    """Range-checking stays with gateway check 14, which composes
    `min(spec.max_leverage, risk_config.max_leverage)` and must report WHICH
    ceiling was hit. Widening this function would duplicate or lose that."""
    assert normalize_requested_leverage(101) == 101
    assert normalize_requested_leverage(0) == 0
    assert normalize_requested_leverage(-5) == -5


def test_the_normaliser_is_exported_from_the_package():
    import quantedge.execution as pkg
    assert pkg.normalize_requested_leverage is normalize_requested_leverage
    assert "normalize_requested_leverage" in pkg.__all__


# ═════════════════════════════════════════════════════════════════════════════
# N. THE AUTHORISED BOUNDARY TABLE, ROW BY ROW, ON THE REAL GATEWAY
# ═════════════════════════════════════════════════════════════════════════════
# Every row the owner specified, asserted through `OrderValidationGateway.
# validate()` on every symbol. `ACCEPT` means `is_valid is True` -- an order
# that would be submitted -- not merely "did not raise".
BOUNDARY_TABLE = [
    # (requested leverage, accepted?, why this row exists)
    (0, False, "explicit 0 must be rejected, never coerced to 1x"),
    (-1, False, "negative leverage is not a position"),
    (0.5, False, "fractional leverage is not representable"),
    (1, True, "the band minimum"),
    (1.0, True, "an integral float is the same request as its int"),
    (50, True, "the value the withdrawn SOL/XRP cap used to allow"),
    (51, True, "the first value the withdrawn cap used to refuse"),
    (99, True, "one below the maximum"),
    (100, True, "the band maximum"),
    (100.0, True, "an integral float at the maximum"),
    (100.0000001, False, "above 100 by any margin is above 100"),
    (101, False, "one above the maximum"),
    (1000, False, "far above the maximum"),
]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("leverage,accepted,why", BOUNDARY_TABLE)
def test_the_authorised_boundary_table(symbol, leverage, accepted, why):
    result = _validate(symbol, leverage)
    assert result.is_valid is accepted, (
        f"{symbol} at {leverage!r}: expected "
        f"{'ACCEPT' if accepted else 'REJECT'} because {why}, got "
        f"{result.rejection_code} {result.rejection_reason}")
    if not accepted:
        assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE
        assert result.failed_check == "CHECK_LEVERAGE_CAP"


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_an_integral_decimal_request_is_accepted(symbol):
    """Separate from the table because `Decimal` is unhashable as a parametrize
    id alongside the floats -- `1` and `1.0` collide as dict keys."""
    assert _validate(symbol, Decimal("100")).is_valid is True
    assert _validate(symbol, Decimal("1")).is_valid is True
    assert _validate(symbol, Decimal("50")).is_valid is True


# ═════════════════════════════════════════════════════════════════════════════
# O. A MALFORMED LEVERAGE IS REJECTED, NOT RAISED
# ═════════════════════════════════════════════════════════════════════════════
# An unhandled exception is not a rejection. Before the request path was routed
# through `normalize_requested_leverage`, check 14's two plain numeric
# comparisons let these values past the band and the failure surfaced later as
# `decimal.InvalidOperation` from the margin arithmetic (check 16) or as
# `TypeError` from the comparison itself -- neither of which produces a
# `RejectionReasonCode`, an audit record, or a message naming leverage.
MALFORMED = [
    True, False, "100", "50", "", "abc", [100], (100,), {"leverage": 100},
    float("nan"), float("inf"), float("-inf"), object(),
]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("leverage", MALFORMED)
def test_a_malformed_leverage_is_rejected_without_raising(symbol, leverage):
    result = _validate(symbol, leverage)
    assert result.is_valid is False
    assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE
    assert result.failed_check == "CHECK_LEVERAGE_CAP"
    assert result.rejection_reason


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_a_decimal_nan_or_infinity_is_rejected_without_raising(symbol):
    for leverage in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        result = _validate(symbol, leverage)
        assert result.is_valid is False
        assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE
        assert result.failed_check == "CHECK_LEVERAGE_CAP"


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_a_true_leverage_is_not_executed_as_one_x(symbol):
    """The specific fail-open this closes: `True == 1`, so an unguarded `True`
    was ACCEPTED and would have been charged 1x margin on a real order."""
    assert _validate(symbol, 1).is_valid is True
    assert _validate(symbol, True).is_valid is False


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_the_rejection_survives_the_whole_gateway(symbol):
    """Check 14 runs before the margin arithmetic, so a malformed value is
    reported as a leverage problem rather than as an insufficient-margin one."""
    for leverage in (float("nan"), True, "100", 50.5):
        result = _validate(symbol, leverage)
        assert result.rejection_code != RejectionReasonCode.INSUFFICIENT_BALANCE
        assert result.rejection_code == RejectionReasonCode.EXCESSIVE_LEVERAGE


# ═════════════════════════════════════════════════════════════════════════════
# P. NO PRODUCER EMITS A ZERO, WHICH IS WHAT KEEPS THE `or` DEFAULTS HONEST
# ═════════════════════════════════════════════════════════════════════════════
# Two downstream readers default with `or`, not with `is None`:
#
#   market_orchestrator.py  effective_lev = qualified_decision.calculated_leverage or 10
#   trade_lifecycle.py      effective_leverage = getattr(decision, "calculated_leverage", None) or 100
#
# `0 or 100` is 100, so a `calculated_leverage` of 0 would be substituted rather
# than refused -- the same shape as the `request.leverage or 1` defect this band
# removed from gateway check 14. Those two lines are NOT changed here: they are
# sizing semantics, and every production producer of `calculated_leverage`
# yields `None` or a value at or above the band minimum, so the `or` can only
# ever fire on `None`. That is an invariant of the producers, not of the
# readers, so it is pinned here. If a producer ever emits 0, this fails and the
# two `or` defaults must become explicit `is None` checks.
def test_manual_smc_never_represents_a_zero_leverage():
    from quantedge.strategy.manual_smc.adapter import represent_leverage
    for applied in (0.0, 0.01, 0.5, 0.9, 0.999):
        leverage, note = represent_leverage(applied)
        assert leverage is None, f"{applied} must be refused, not floored to 0"
        assert note, "a refusal must always be reported"
    for applied in (1.0, 1.4, 50.6, 99.9, 100.0):
        leverage, _ = represent_leverage(applied)
        assert leverage is not None
        assert leverage >= MIN_LEVERAGE
        assert leverage != 0


def test_the_risk_calculator_never_derives_a_zero_leverage():
    """`risk.py` floors its derived leverage at 1 before returning it. That
    floor is the intentionally separate derived-leverage clamp -- it bounds a
    COMPUTED leverage, never a REQUESTED one -- and it is what stops a tiny
    notional from producing a 0 that the `or` defaults would then inflate."""
    source = _source(ENGINE_SRC / "strategy" / "risk.py")
    assert "if max_leverage < 1:" in source
    assert "max_leverage = 1" in source


def test_the_zero_sentinel_never_reaches_order_admission():
    """`strategy/engine.py` builds rejection `TradeSetup`s with `leverage=0`.

    Every such site must be an all-zero setup -- zero entry, zero stop, zero
    target, zero size -- because that is what makes it a refusal record rather
    than a request. A `leverage=0` appearing next to real prices would be a
    setup asking to be traded at no leverage, which nothing downstream can
    represent: the `or` defaults would inflate it to 10x or 100x.
    """
    tree = ast.parse(_source(ENGINE_SRC / "strategy" / "engine.py"))
    zero_sites = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        leverage = keywords.get("leverage")
        if not (isinstance(leverage, ast.Constant) and leverage.value == 0):
            continue
        zero_sites += 1
        for companion in ("entry_price", "stop_loss", "take_profit", "position_size"):
            value = keywords.get(companion)
            assert value is not None, (
                f"line {node.lineno}: leverage=0 without {companion}")
            rendered = ast.unparse(value)
            assert rendered in ('Decimal("0")', "Decimal('0')"), (
                f"line {node.lineno}: leverage=0 alongside a real "
                f"{companion} ({rendered}) is a tradable setup, not a refusal")
    assert zero_sites >= 1, "the sentinel sites vanished; retire this test"
