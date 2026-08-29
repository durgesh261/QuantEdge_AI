"""
Manual SMC — Position Sizing (Phase 1 Step 3).
==============================================

Capital mathematics for the Manual SMC strategy, extracted from the frozen
research oracle `displacement_gated_retest_engine.run_manual_spec_backtest`
and kept expression-for-expression identical to it.

THE MATHEMATICS (oracle-faithful; do not "clean up")
----------------------------------------------------
    sl_dist_pct    = |entry - sl| / entry * 100        (0.0 if entry <= 1e-9)
    theo_leverage  = 35.0 / sl_dist_pct                (1.0 if sl_dist <= 1e-9)
    applied_lev    = min(100.0, theo_leverage)         CLAMPS — never raises
    margin_usd     = account_balance                   whole balance is margin
    notional_usd   = account_balance * applied_lev
    fee_usd        = notional_usd * 0.0008             round-trip, charged once
    gross_sl_pct   = applied_lev * sl_dist_pct
    gross_tp_pct   = 0.60 * applied_lev
    ret_pct        = +gross_tp_pct           (TP)
                     -gross_sl_pct           (SL)
                     realized_r * gross_sl_pct  (TIMEOUT)
    gross_pnl      = account_balance * ret_pct / 100
    net_pnl        = gross_pnl - fee_usd
    ending_balance = max(0.0, account_balance + net_pnl)   compounding input

float throughout — no Decimal. Decimal quantization belongs at the execution
boundary (quantization.py, a later phase), not here.

The ONE exception is `ContractSpec.contract_value`, which may be an exact
`Decimal` because it is an exchange-published constant, not a capital
expression: the shared `quantedge.instruments` registry reads it as a Decimal
and a caller injects it here without rounding. It participates in no
expression above. `require_verified()` still returns a float for existing
callers; `require_verified_exact()` returns the Decimal.

QUANTITY IS NOT COMPUTED HERE — BY DESIGN
-----------------------------------------
Delta's contract-value and order-quantity semantics are NOT yet verified, so
this module refuses to invent them (safety rules #8 and #16). Consequences:

  * `PositionSizing` has NO quantity field. Sizing output is denominated in
    USD notional and leverage only.
  * A symbol's contract value lives in `ContractSpec`, whose default state is
    the `UNVERIFIED` sentinel — not a number, not None, not 1.0. Arithmetic
    on it is impossible.
  * `resolve_order_quantity()` requires BOTH a verified `ContractSpec` AND an
    explicitly injected converter encoding the verified exchange semantics.
    Omit either and it raises. There is no default converter.
  * Unknown symbols fail closed with `UnknownSymbolError` (safety rule #15).

DELIBERATELY ABSENT
-------------------
No execution-layer behaviour: no order placement, no exchange calls, no
bracket logic, no persistence, no rounding to exchange tick/lot sizes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Dict, Optional, Union

from quantedge.strategy.manual_smc.lifecycle import (
    OUTCOME_SL,
    OUTCOME_TIMEOUT,
    OUTCOME_TP,
)
from quantedge.strategy.manual_smc.models import ManualOBRecord, ManualSpecConfig

#: Guard threshold used by the oracle. Preserved exactly.
EPS: float = 1e-9


class SizingError(RuntimeError):
    """Base class for sizing refusals."""


class UnknownSymbolError(SizingError):
    """Symbol not registered. Fail closed — safety rule #15."""


class ContractValueUnverifiedError(SizingError):
    """
    The symbol's contract value has not been established against Delta.
    Safety rule #8: never guess exchange product IDs or contract values.
    """


class QuantitySemanticsUnverifiedError(SizingError):
    """
    No verified notional→contracts conversion was supplied. Safety rule #16:
    ambiguity about exchange semantics must be verified, not guessed.
    """


class DegenerateRiskError(SizingError):
    """SL distance is zero or near-zero; leverage is meaningless."""


class _UnverifiedContractValue:
    """
    Sentinel for 'not yet established by Delta verification'.

    Deliberately NOT a number and NOT None: every arithmetic operation on it
    raises TypeError, so it cannot silently propagate into an order quantity.
    It is also falsey, so `if contract_value:` guards behave conservatively.
    """

    __slots__ = ()

    def __repr__(self) -> str:                     # pragma: no cover - trivial
        return "UNVERIFIED"

    def __bool__(self) -> bool:
        return False


#: The single sentinel instance. Compare with `is`.
UNVERIFIED = _UnverifiedContractValue()

ContractValue = Union[float, Decimal, _UnverifiedContractValue]

@dataclass(frozen=True)
class ContractSpec:
    """
    Per-symbol exchange contract metadata.

    `contract_value` starts as `UNVERIFIED` and may only become a number
    together with a non-empty `verification_source` naming where the value
    came from. A number without provenance is rejected at construction.
    """
    symbol: str
    contract_value: ContractValue = UNVERIFIED
    verification_source: Optional[str] = None
    verified_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.contract_value is UNVERIFIED:
            if self.verification_source is not None:
                raise ContractValueUnverifiedError(
                    f"{self.symbol}: verification_source given but "
                    f"contract_value is still UNVERIFIED")
            return
        if isinstance(self.contract_value, bool) or not isinstance(
                self.contract_value, (int, float, Decimal)):
            raise ContractValueUnverifiedError(
                f"{self.symbol}: contract_value must be UNVERIFIED or a "
                f"positive number, got {self.contract_value!r}")
        if isinstance(self.contract_value, Decimal) and \
                not self.contract_value.is_finite():
            raise ContractValueUnverifiedError(
                f"{self.symbol}: contract_value must be finite, got "
                f"{self.contract_value!r}")
        if float(self.contract_value) <= 0.0:
            raise ContractValueUnverifiedError(
                f"{self.symbol}: contract_value must be positive, got "
                f"{self.contract_value!r}")
        if not self.verification_source or not str(
                self.verification_source).strip():
            raise ContractValueUnverifiedError(
                f"{self.symbol}: a numeric contract_value requires a "
                f"verification_source naming how it was established")

    @property
    def is_verified(self) -> bool:
        return self.contract_value is not UNVERIFIED

    def require_verified(self) -> float:
        """Return the contract value, or raise. The only legitimate accessor."""
        if not self.is_verified:
            raise ContractValueUnverifiedError(
                f"{self.symbol}: contract value is UNVERIFIED — it must be "
                f"established against Delta before any order quantity exists")
        return float(self.contract_value)          # type: ignore[arg-type]

    def require_verified_exact(self) -> Decimal:
        """
        The same value without a float crossing.

        Use this wherever the exchange's exact constant matters (order
        quantity, once its semantics are verified). A float or int that was
        injected as-is is widened via `str` so no binary artefact is created.
        """
        if not self.is_verified:
            raise ContractValueUnverifiedError(
                f"{self.symbol}: contract value is UNVERIFIED — it must be "
                f"established against Delta before any order quantity exists")
        raw = self.contract_value
        return raw if isinstance(raw, Decimal) else Decimal(str(raw))


#: The four Manual SMC assets, registered with UNVERIFIED contract values.
#: They are listed so that a KNOWN symbol is distinguishable from a TYPO,
#: while still being unusable for quantity until Delta verification lands.
MANUAL_SMC_SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")


class ContractSpecRegistry:
    """Symbol → ContractSpec. Unknown symbols fail closed (safety rule #15)."""

    def __init__(self, specs: Optional[Dict[str, ContractSpec]] = None) -> None:
        self._specs: Dict[str, ContractSpec] = dict(specs or {})

    @classmethod
    def default(cls) -> "ContractSpecRegistry":
        """The four assets, every contract value UNVERIFIED. No guesses."""
        return cls({s: ContractSpec(symbol=s) for s in MANUAL_SMC_SYMBOLS})

    def register(self, spec: ContractSpec) -> None:
        self._specs[spec.symbol] = spec

    def get(self, symbol: str) -> ContractSpec:
        spec = self._specs.get(symbol)
        if spec is None:
            raise UnknownSymbolError(
                f"{symbol!r} is not a registered contract; refusing to size it")
        return spec

    def is_verified(self, symbol: str) -> bool:
        return self.get(symbol).is_verified

    @property
    def symbols(self) -> tuple:
        return tuple(self._specs)


@dataclass(frozen=True)
class PositionSizing:
    """
    Sizing decision for one trade. USD/leverage only — NO quantity field.

    `margin_usd` equals `account_balance`: the oracle commits the whole
    balance as margin, which is what makes `notional = balance * leverage`.
    """
    asset: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    risk_dist: float
    reward_dist: float
    sl_dist_pct: float
    theoretical_leverage: float
    applied_leverage: float
    account_balance: float
    margin_usd: float
    notional_usd: float
    fee_usd: float
    gross_sl_return_pct: float
    gross_tp_return_pct: float
    leverage_clamped: bool
    degenerate_sl_distance: bool

@dataclass(frozen=True)
class TradeSettlement:
    """Realised capital outcome of one closed trade. Compounding input."""
    outcome: str
    realized_r: float
    return_pct: float
    starting_balance: float
    notional_usd: float
    fee_usd: float
    gross_pnl_usd: float
    net_pnl_usd: float
    ending_balance: float


# ---------------------------------------------------------------------------
# Leverage — oracle expressions, guards and fallbacks preserved exactly.
# ---------------------------------------------------------------------------
def compute_sl_dist_pct(entry_price: float, sl_price: float) -> float:
    """|entry - sl| / entry * 100, or 0.0 when entry is non-positive."""
    risk_dist = abs(entry_price - sl_price)
    return (risk_dist / entry_price) * 100.0 if entry_price > EPS else 0.0


def compute_leverage(sl_dist_pct: float, cfg: ManualSpecConfig) -> tuple:
    """
    Returns (theoretical_leverage, applied_leverage).

    `min(cap, theoretical)` CLAMPS. Exceeding the cap is NOT an error: the
    oracle clamps silently and changing that would change trade history.
    A degenerate SL distance yields the oracle's 1.0 fallback, not a raise —
    callers that must refuse such a setup call `assert_executable()`.
    """
    theo = (cfg.max_sl_account_risk_pct / sl_dist_pct
            if sl_dist_pct > EPS else 1.0)
    return theo, min(cfg.applied_leverage_cap, theo)

def size_position(
    ob: ManualOBRecord,
    account_balance: float,
    cfg: ManualSpecConfig,
    registry: Optional[ContractSpecRegistry] = None,
) -> PositionSizing:
    """
    Size one trade from its OB and the balance at fill time.

    Leverage is taken from the OB, where `_make_manual_ob` already computed it
    with these exact expressions; recomputing it here could only introduce
    drift. `registry`, when supplied, is consulted solely to fail closed on an
    unknown symbol — a verified contract value is NOT required to size in USD.
    """
    if registry is not None:
        registry.get(ob.asset)                    # raises on unknown symbol

    applied_lev = ob.applied_leverage
    notional = account_balance * applied_lev
    return PositionSizing(
        asset=ob.asset,
        direction=ob.direction,
        entry_price=ob.entry_price,
        sl_price=ob.sl_price,
        tp_price=ob.tp_price,
        risk_dist=abs(ob.entry_price - ob.sl_price),
        reward_dist=abs(ob.tp_price - ob.entry_price),
        sl_dist_pct=ob.sl_dist_pct,
        theoretical_leverage=ob.theoretical_leverage,
        applied_leverage=applied_lev,
        account_balance=account_balance,
        margin_usd=account_balance,
        notional_usd=notional,
        fee_usd=notional * cfg.fee_rate,
        gross_sl_return_pct=applied_lev * ob.sl_dist_pct,
        gross_tp_return_pct=cfg.fixed_tp_market_pct * applied_lev,
        leverage_clamped=ob.theoretical_leverage > cfg.applied_leverage_cap,
        degenerate_sl_distance=ob.sl_dist_pct <= EPS,
    )

# ---------------------------------------------------------------------------
# Outcome → capital. Oracle expressions preserved exactly.
# ---------------------------------------------------------------------------
def realized_r_for_outcome(
    outcome: str,
    sizing: PositionSizing,
    exit_price: Optional[float] = None,
) -> float:
    """
    TP  → reward/risk (0.0 when risk is degenerate)
    SL  → exactly -1.0
    TIMEOUT → signed price move / risk, so `exit_price` is required
    """
    if outcome == OUTCOME_TP:
        return (sizing.reward_dist / sizing.risk_dist
                if sizing.risk_dist > EPS else 0.0)
    if outcome == OUTCOME_SL:
        return -1.0
    if outcome == OUTCOME_TIMEOUT:
        if exit_price is None:
            raise SizingError("timeout settlement requires an exit price")
        p_diff = ((exit_price - sizing.entry_price)
                  if sizing.direction == "LONG"
                  else (sizing.entry_price - exit_price))
        return p_diff / sizing.risk_dist if sizing.risk_dist > EPS else 0.0
    raise SizingError(f"unknown outcome {outcome!r}")


def return_pct_for_outcome(
    outcome: str, sizing: PositionSizing, realized_r: float
) -> float:
    """Leveraged gross account return, before fees."""
    if outcome == OUTCOME_TP:
        return sizing.gross_tp_return_pct
    if outcome == OUTCOME_SL:
        return -sizing.gross_sl_return_pct
    if outcome == OUTCOME_TIMEOUT:
        return realized_r * sizing.gross_sl_return_pct
    raise SizingError(f"unknown outcome {outcome!r}")

def settle_trade(
    sizing: PositionSizing,
    outcome: str,
    exit_price: Optional[float] = None,
) -> TradeSettlement:
    """
    Full capital settlement: fees on notional, PnL on balance, floor at 0.

    The 0.08% fee rate is round-trip and is therefore charged ONCE, on close,
    against the whole notional — exactly as the oracle does.
    """
    realized_r = realized_r_for_outcome(outcome, sizing, exit_price)
    ret_pct = return_pct_for_outcome(outcome, sizing, realized_r)
    gross_pnl = sizing.account_balance * (ret_pct / 100.0)
    net_pnl = gross_pnl - sizing.fee_usd
    return TradeSettlement(
        outcome=outcome,
        realized_r=realized_r,
        return_pct=ret_pct,
        starting_balance=sizing.account_balance,
        notional_usd=sizing.notional_usd,
        fee_usd=sizing.fee_usd,
        gross_pnl_usd=gross_pnl,
        net_pnl_usd=net_pnl,
        ending_balance=max(0.0, sizing.account_balance + net_pnl),
    )


# ---------------------------------------------------------------------------
# Execution readiness. Nothing below computes a quantity without proof.
# ---------------------------------------------------------------------------
#: (notional_usd, contract_value) -> contract count. Must encode VERIFIED
#: Delta semantics. No implementation is provided in this phase on purpose.
QuantityConverter = Callable[[float, float], float]


def assert_executable(sizing: PositionSizing, spec: ContractSpec) -> None:
    """
    Refuse a setup that must never reach an exchange.

    Raises on a degenerate SL distance (leverage would be the meaningless 1.0
    fallback) and on an unverified contract value. Called explicitly — never
    implicitly from `size_position`, so backtest arithmetic is unaffected.
    """
    if sizing.degenerate_sl_distance:
        raise DegenerateRiskError(
            f"{sizing.asset}: sl_dist_pct={sizing.sl_dist_pct!r} is zero or "
            f"near-zero; leverage fell back to the 1.0 sentinel")
    if spec.symbol != sizing.asset:
        raise UnknownSymbolError(
            f"contract spec {spec.symbol!r} does not match sized asset "
            f"{sizing.asset!r}")
    spec.require_verified()

def resolve_order_quantity(
    sizing: PositionSizing,
    spec: ContractSpec,
    converter: Optional[QuantityConverter] = None,
) -> float:
    """
    The ONLY path from sizing to a contract count — and it is closed today.

    Requires all three of: a non-degenerate setup, a verified contract value,
    and an explicitly injected converter encoding verified Delta semantics.
    The default `converter=None` raises, so no caller can obtain a quantity
    by accident while the semantics remain unverified.
    """
    assert_executable(sizing, spec)
    if converter is None:
        raise QuantitySemanticsUnverifiedError(
            f"{sizing.asset}: notional→contracts conversion is not verified; "
            f"a converter must be supplied explicitly. Refusing to guess.")
    contract_value = spec.require_verified()
    qty = float(converter(sizing.notional_usd, contract_value))
    if qty <= 0.0:
        raise SizingError(
            f"{sizing.asset}: converter produced non-positive quantity {qty!r}")
    return qty


__all__ = [
    "EPS",
    "MANUAL_SMC_SYMBOLS",
    "UNVERIFIED",
    "ContractValue",
    "ContractSpec",
    "ContractSpecRegistry",
    "PositionSizing",
    "TradeSettlement",
    "QuantityConverter",
    "SizingError",
    "UnknownSymbolError",
    "ContractValueUnverifiedError",
    "QuantitySemanticsUnverifiedError",
    "DegenerateRiskError",
    "compute_sl_dist_pct",
    "compute_leverage",
    "size_position",
    "realized_r_for_outcome",
    "return_pct_for_outcome",
    "settle_trade",
    "assert_executable",
    "resolve_order_quantity",
]








