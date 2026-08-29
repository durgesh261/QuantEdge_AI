"""
Focused tests — Manual SMC price quantization (Phase 1 Step 4).
==============================================================

Mandated coverage -> class:

    one canonical tick-size quantizer   TestCanonicalQuantizerIsTheOnlyRounder
    tick size from ProductSpecification TestTickSizeComesFromTheProductSpec
    no hardcoded per-symbol rounding    TestNoPerSymbolRoundingRules
    rounding direction, explicit        TestRoundingDirection
    half-tick behaviour, explicit       TestHalfTickBehaviour
    exact Decimal, no float rounding    TestDecimalExactness
    invalid tick sizes fail closed      TestInvalidTickSizeFailsClosed
    invalid prices fail closed          TestInvalidPriceFailsClosed
    no quantity / contract value here   TestNoQuantityOrContractValue
    module independence                 TestModuleIndependence

Plus two consequence checks that make the rounding choice load-bearing rather
than cosmetic: `TestConservativeRoundingProtectsTheRiskBudget` (the quantized
SL distance can never widen, so the 35% budget survives) and
`TestOBBracketBoundary` (the float lifecycle is never mutated by quantizing).
"""

from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import get_type_hints

import re
import pytest

from quantedge.execution.validation import (
    DEFAULT_DELTA_INDIA_PRODUCTS,
    ProductSpecification,
    get_product_specification,
)
from quantedge.strategy.manual_smc.geometry import _make_manual_ob
from quantedge.strategy.manual_smc.models import ManualSpecConfig
from quantedge.strategy.manual_smc.quantization import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    BracketGeometryError,
    InvalidPriceError,
    InvalidTickSizeError,
    PriceRole,
    QuantizationError,
    QuantizedBracket,
    SubTickPriceError,
    TickRounding,
    TickSizeSpec,
    conservative_rounding,
    is_on_tick_grid,
    price_from_strategy_float,
    quantize_bracket,
    quantize_ob_bracket,
    quantize_price,
    tick_size_of,
    validate_direction,
    validate_price,
    validate_tick_size,
)

CFG = ManualSpecConfig()

MODULE_PATH = (Path(__file__).parent.parent / "src" / "quantedge" / "strategy"
               / "manual_smc" / "quantization.py")
MODULE_SRC = MODULE_PATH.read_text(encoding="utf-8")


def _code_lines():
    """Module source with docstring/comment lines removed."""
    out, in_doc = [], False
    for line in MODULE_SRC.splitlines():
        stripped = line.strip()
        if stripped.count('"""') == 1:
            in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#") or stripped.startswith('"""'):
            continue
        out.append(line)
    return out


def _code_without_strings():
    """
    Executable source with comments, docstrings AND string literals removed.

    Used for the "no hardcoded rule" scans: a hardcoded tick or symbol rule
    would be a literal in an EXPRESSION, whereas an error message legitimately
    names Delta's real tick sizes when explaining a refusal.
    """
    code = "\n".join(_code_lines())
    code = re.sub(r'"[^"\n]*"', '""', code)
    code = re.sub(r"'[^'\n]*'", "''", code)
    return code


#: The four real Delta India tick sizes, read from the real product specs.
REAL_TICKS = {s: get_product_specification(s).tick_size
              for s in ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")}


class TestCanonicalQuantizerIsTheOnlyRounder:
    """`quantize_price` must be the single place a price is rounded."""

    def test_exactly_one_grid_division_exists_in_the_module(self):
        code = _code_without_strings()
        assert code.count("divmod(") == 1

    def test_no_other_rounding_construct_is_used(self):
        code = _code_without_strings()
        for banned in ("ROUND_", ".quantize(", "//", "round(",
                       "to_integral", "__round__"):
            assert banned not in code, f"{banned!r} found in quantization.py"

    def test_every_public_entry_point_delegates_to_quantize_price(self):
        code = _code_without_strings()
        # bracket + OB helpers must call the canonical function, never re-derive
        assert code.count("quantize_price(") >= 2
        for fn in ("def quantize_bracket", "def quantize_ob_bracket"):
            assert fn in code


class TestTickSizeComesFromTheProductSpec:
    """The tick size is read from `ProductSpecification.tick_size`."""

    def test_real_product_specification_satisfies_the_protocol(self):
        spec = get_product_specification("BTCUSD")
        assert isinstance(spec, ProductSpecification)
        assert isinstance(spec, TickSizeSpec)
        assert tick_size_of(spec) == Decimal("0.5")

    @pytest.mark.parametrize("symbol", sorted(DEFAULT_DELTA_INDIA_PRODUCTS))
    def test_every_shipped_product_spec_is_accepted(self, symbol):
        spec = DEFAULT_DELTA_INDIA_PRODUCTS[symbol]
        tick = tick_size_of(spec)
        assert tick == spec.tick_size
        assert tick > 0

    @pytest.mark.parametrize("symbol,expected", [
        ("BTCUSD", Decimal("0.5")),
        ("ETHUSD", Decimal("0.05")),
        # Authoritative Delta India tick; the pre-registry table said 0.01.
        ("SOLUSD", Decimal("0.0001")),
        ("XRPUSD", Decimal("0.0001")),
    ])
    def test_the_four_manual_smc_ticks_are_read_not_invented(
            self, symbol, expected):
        assert tick_size_of(get_product_specification(symbol)) == expected
        assert REAL_TICKS[symbol] == expected

    def test_protocol_declares_a_decimal_tick_size(self):
        assert get_type_hints(TickSizeSpec)["tick_size"] is Decimal

    def test_object_without_tick_size_raises_instead_of_defaulting(self):
        class NoTick:
            pass
        with pytest.raises(InvalidTickSizeError, match="no tick_size"):
            tick_size_of(NoTick())

    def test_spec_carrying_a_float_tick_is_refused(self):
        class FloatTick:
            tick_size = 0.5
        with pytest.raises(InvalidTickSizeError, match="must be a Decimal"):
            tick_size_of(FloatTick())

    def test_spec_carrying_a_zero_tick_is_refused(self):
        bad = ProductSpecification(
            symbol="ZZZUSD", product_id=1, min_size=Decimal("1"),
            size_step=Decimal("1"), tick_size=Decimal("0"))
        with pytest.raises(InvalidTickSizeError, match="strictly positive"):
            tick_size_of(bad)


class TestNoPerSymbolRoundingRules:
    """Behaviour depends on the tick size alone — never on the symbol."""

    def test_module_source_names_no_symbol_and_no_default_tick(self):
        code = _code_without_strings()
        for token in ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD",
                      "0.5", "0.05", "0.01", "0.0001", "0.50"):
            assert token not in code, f"{token!r} hardcoded in quantization.py"

    def test_same_price_and_tick_give_the_same_result_for_any_symbol(self):
        price, tick = Decimal("60123.37"), Decimal("0.5")
        results = {
            asset: quantize_bracket(
                asset, DIRECTION_LONG, price, Decimal("60000.13"),
                Decimal("60484.11"), tick).entry_price
            for asset in ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "WHATEVER")
        }
        assert len(set(results.values())) == 1
        assert set(results.values()) == {Decimal("60123.0")}

    def test_a_symbol_the_strategy_never_trades_is_not_special_cased(self):
        """No symbol allow-list here: the tick governs, and only the tick."""
        assert (quantize_price(Decimal("1.234"), Decimal("0.01"),
                               TickRounding.DOWN)
                == quantize_price(Decimal("1.234"), Decimal("0.01"),
                                  TickRounding.DOWN))
        assert quantize_price(Decimal("1.234"), Decimal("0.01"),
                              TickRounding.DOWN) == Decimal("1.23")


class TestRoundingDirection:
    """Direction is explicit, required, and never guessed."""

    def test_rounding_argument_is_required(self):
        with pytest.raises(TypeError):
            quantize_price(Decimal("100.3"), Decimal("0.5"))   # type: ignore

    def test_down_floors_onto_the_grid(self):
        assert quantize_price(Decimal("100.3"), Decimal("0.5"),
                              TickRounding.DOWN) == Decimal("100.0")

    def test_up_ceils_onto_the_grid(self):
        assert quantize_price(Decimal("100.3"), Decimal("0.5"),
                              TickRounding.UP) == Decimal("100.5")

    @pytest.mark.parametrize("rounding", list(TickRounding))
    def test_an_on_grid_price_is_never_moved_by_any_rounding(self, rounding):
        for tick, price in ((Decimal("0.5"), Decimal("60123.5")),
                            (Decimal("0.05"), Decimal("2345.65")),
                            (Decimal("0.01"), Decimal("142.37")),
                            (Decimal("0.0001"), Decimal("0.5123"))):
            assert quantize_price(price, tick, rounding) == price

    @pytest.mark.parametrize("rounding", list(TickRounding))
    def test_quantizing_is_idempotent(self, rounding):
        tick = Decimal("0.05")
        once = quantize_price(Decimal("2345.6789"), tick, rounding)
        assert quantize_price(once, tick, rounding) == once

    @pytest.mark.parametrize("rounding", list(TickRounding))
    def test_result_is_always_on_grid_and_within_one_tick(self, rounding):
        for symbol, tick in REAL_TICKS.items():
            for raw in ("60123.376", "2345.6789", "142.3751", "0.51239",
                        "1.000000001", "99999.99999"):
                price = Decimal(raw)
                out = quantize_price(price, tick, rounding)
                assert is_on_tick_grid(out, tick), (symbol, raw, out)
                assert abs(out - price) < tick

    def test_down_never_exceeds_and_up_never_undercuts_the_input(self):
        tick = Decimal("0.0001")
        for raw in ("0.51239", "0.5", "1.99995", "3.00001"):
            price = Decimal(raw)
            assert quantize_price(price, tick, TickRounding.DOWN) <= price
            assert quantize_price(price, tick, TickRounding.UP) >= price

    def test_result_is_expressed_at_the_tick_scale(self):
        """Deterministic string form for exchange payload construction."""
        assert str(quantize_price(Decimal("100.3"), Decimal("0.05"),
                                 TickRounding.UP)) == "100.30"
        assert str(quantize_price(Decimal("0.51239"), Decimal("0.0001"),
                                 TickRounding.DOWN)) == "0.5123"
        assert str(quantize_price(Decimal("100"), Decimal("0.5"),
                                 TickRounding.DOWN)) == "100.0"

    def test_a_non_tickrounding_argument_is_refused(self):
        for bad in ("DOWN", 0, None, TickRounding):
            with pytest.raises(QuantizationError, match="TickRounding"):
                quantize_price(Decimal("100.3"), Decimal("0.5"), bad)


class TestHalfTickBehaviour:
    """The half-tick tie is defined by the enum member, never implicitly."""

    def test_there_is_no_ambiguous_nearest_member(self):
        names = {m.name for m in TickRounding}
        assert names == {"DOWN", "UP", "NEAREST_HALF_UP", "NEAREST_HALF_DOWN"}
        assert "NEAREST" not in names

    @pytest.mark.parametrize("tick,half,down,up", [
        (Decimal("0.5"), Decimal("100.25"), Decimal("100.0"), Decimal("100.5")),
        (Decimal("0.05"), Decimal("100.025"), Decimal("100.00"),
         Decimal("100.05")),
        (Decimal("0.01"), Decimal("142.375"), Decimal("142.37"),
         Decimal("142.38")),
        (Decimal("0.0001"), Decimal("0.51235"), Decimal("0.5123"),
         Decimal("0.5124")),
    ])
    def test_exact_half_tick_resolves_per_the_named_tie_rule(
            self, tick, half, down, up):
        assert quantize_price(half, tick, TickRounding.NEAREST_HALF_DOWN) == down
        assert quantize_price(half, tick, TickRounding.NEAREST_HALF_UP) == up
        # and the unconditional directions are unaffected by the tie rule
        assert quantize_price(half, tick, TickRounding.DOWN) == down
        assert quantize_price(half, tick, TickRounding.UP) == up

    @pytest.mark.parametrize("rounding", [TickRounding.NEAREST_HALF_UP,
                                          TickRounding.NEAREST_HALF_DOWN])
    def test_just_below_half_always_goes_down(self, rounding):
        tick = Decimal("0.5")
        assert quantize_price(Decimal("100.2499999999"), tick,
                              rounding) == Decimal("100.0")

    @pytest.mark.parametrize("rounding", [TickRounding.NEAREST_HALF_UP,
                                          TickRounding.NEAREST_HALF_DOWN])
    def test_just_above_half_always_goes_up(self, rounding):
        tick = Decimal("0.5")
        assert quantize_price(Decimal("100.2500000001"), tick,
                              rounding) == Decimal("100.5")

    def test_half_tick_of_an_odd_grid_is_still_exact(self):
        """A tick with no exact binary representation still ties exactly."""
        tick = Decimal("0.03")
        assert quantize_price(Decimal("1.005"), tick,
                              TickRounding.NEAREST_HALF_UP) == Decimal("1.02")
        assert quantize_price(Decimal("1.005"), tick,
                              TickRounding.NEAREST_HALF_DOWN) == Decimal("0.99")


class TestDecimalExactness:
    """Exchange prices stay exact Decimals; no float rounding is introduced."""

    def test_a_float_price_is_refused(self):
        with pytest.raises(InvalidPriceError, match="must be a Decimal"):
            quantize_price(100.3, Decimal("0.5"), TickRounding.DOWN)

    def test_a_string_price_is_refused(self):
        with pytest.raises(InvalidPriceError, match="must be a Decimal"):
            quantize_price("100.3", Decimal("0.5"), TickRounding.DOWN)

    def test_a_float_tick_is_refused(self):
        with pytest.raises(InvalidTickSizeError, match="must be a Decimal"):
            quantize_price(Decimal("100.3"), 0.5, TickRounding.DOWN)

    def test_no_float_conversion_happens_inside_the_quantizer(self):
        # The sanctioned boundary helper's NAME ends in `_float`, so the bare
        # substring "float(" matches `price_from_strategy_float(...)` even
        # though no conversion happens there. Neutralise the identifier first:
        # what is being policed is a `float(...)` CALL in the arithmetic.
        code_lines = [re.sub(r'"[^"\n]*"', '""', ln)
                      .replace("price_from_strategy_float", "_PRICE_BOUNDARY")
                      for ln in _code_lines()]
        assert not [ln for ln in code_lines if "float(" in ln]
        # `float` may appear ONLY in the explicit boundary helper's signature
        # and type guard — never in the grid arithmetic.
        offenders = [ln for ln in code_lines
                     if "float" in ln
                     and "Union[float, int]" not in ln
                     and "(int, float)" not in ln]
        assert offenders == [], offenders

    def test_the_returned_value_is_always_a_decimal(self):
        out = quantize_price(Decimal("60123.37"), Decimal("0.5"),
                             TickRounding.DOWN)
        assert isinstance(out, Decimal)
        assert not isinstance(out, float)

    def test_grids_a_float_cannot_represent_are_handled_exactly(self):
        """0.05 and 0.0001 are not binary-exact; the grid must still be."""
        assert quantize_price(Decimal("2345.67"), Decimal("0.05"),
                              TickRounding.UP) == Decimal("2345.70")
        assert quantize_price(Decimal("0.30000"), Decimal("0.0001"),
                              TickRounding.DOWN) == Decimal("0.3000")
        # the classic float hazard, computed exactly
        assert (Decimal("0.1") + Decimal("0.2")) == Decimal("0.3")
        assert quantize_price(Decimal("0.1") + Decimal("0.2"),
                              Decimal("0.0001"),
                              TickRounding.NEAREST_HALF_UP) == Decimal("0.3000")

    def test_strategy_float_boundary_converts_without_binary_noise(self):
        assert price_from_strategy_float(0.1) == Decimal("0.1")
        assert Decimal(0.1) != Decimal("0.1")          # what we avoid
        assert price_from_strategy_float(60123.37) == Decimal("60123.37")
        assert price_from_strategy_float(100) == Decimal("100")

    def test_strategy_float_boundary_does_not_round_to_any_grid(self):
        crossed = price_from_strategy_float(100.37)
        assert crossed == Decimal("100.37")
        assert not is_on_tick_grid(crossed, Decimal("0.5"))


class TestInvalidTickSizeFailsClosed:
    """Every invalid tick size raises; none falls back to a default."""

    @pytest.mark.parametrize("bad", [
        Decimal("0"), Decimal("-0.5"), Decimal("-0.0001"),
    ])
    def test_non_positive_tick_is_refused(self, bad):
        with pytest.raises(InvalidTickSizeError, match="strictly positive"):
            validate_tick_size(bad)

    @pytest.mark.parametrize("bad", [
        Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"),
    ])
    def test_non_finite_tick_is_refused(self, bad):
        with pytest.raises(InvalidTickSizeError, match="not finite"):
            validate_tick_size(bad)

    @pytest.mark.parametrize("bad", [0.5, "0.5", None, 1, True, [Decimal("1")]])
    def test_non_decimal_tick_is_refused(self, bad):
        with pytest.raises(InvalidTickSizeError, match="must be a Decimal"):
            validate_tick_size(bad)

    def test_a_valid_tick_is_returned_unchanged(self):
        tick = Decimal("0.05")
        assert validate_tick_size(tick) is tick

    def test_quantizing_with_a_bad_tick_never_returns_a_price(self):
        for bad in (Decimal("0"), Decimal("-1"), 0.5, None):
            with pytest.raises(InvalidTickSizeError):
                quantize_price(Decimal("100.3"), bad, TickRounding.DOWN)


class TestInvalidPriceFailsClosed:
    """Invalid prices raise rather than producing an exchange-bound number."""

    @pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1"),
                                     Decimal("-0.0001")])
    def test_non_positive_price_is_refused(self, bad):
        with pytest.raises(InvalidPriceError, match="strictly positive"):
            validate_price(bad)

    @pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("sNaN"),
                                     Decimal("Infinity"),
                                     Decimal("-Infinity")])
    def test_non_finite_price_is_refused(self, bad):
        with pytest.raises(InvalidPriceError, match="not finite"):
            validate_price(bad)

    @pytest.mark.parametrize("bad", [100.3, "100.3", None, 100, True])
    def test_non_decimal_price_is_refused(self, bad):
        with pytest.raises(InvalidPriceError, match="must be a Decimal"):
            validate_price(bad)

    def test_the_error_points_at_the_explicit_float_boundary(self):
        with pytest.raises(InvalidPriceError,
                           match="price_from_strategy_float"):
            validate_price(100.3)

    def test_sub_tick_price_raises_instead_of_quantizing_to_zero(self):
        with pytest.raises(SubTickPriceError, match="below one tick"):
            quantize_price(Decimal("0.00005"), Decimal("0.0001"),
                           TickRounding.DOWN)

    def test_sub_tick_price_may_still_round_up_onto_the_first_tick(self):
        assert quantize_price(Decimal("0.00005"), Decimal("0.0001"),
                              TickRounding.UP) == Decimal("0.0001")

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf"),
                                     "1.0", None, True, Decimal("1")])
    def test_strategy_float_boundary_fails_closed_too(self, bad):
        with pytest.raises(InvalidPriceError):
            price_from_strategy_float(bad)

    def test_bad_direction_is_refused(self):
        for bad in ("long", "Long", "BUY", "", None, 1, "LONG "):
            with pytest.raises(QuantizationError, match="direction must be"):
                validate_direction(bad)

    def test_valid_directions_pass_through(self):
        assert validate_direction("LONG") == DIRECTION_LONG
        assert validate_direction("SHORT") == DIRECTION_SHORT


class TestConservativeRoundingProtectsTheRiskBudget:
    """
    The whole rounding table exists for one reason: `sizing.py` computed
    leverage from the UNQUANTIZED SL distance under a 35% risk budget, so the
    quantized SL distance must never be wider than the raw one.
    """

    @pytest.mark.parametrize("role,direction,expected", [
        (PriceRole.ENTRY, "LONG", TickRounding.DOWN),
        (PriceRole.ENTRY, "SHORT", TickRounding.UP),
        (PriceRole.STOP_LOSS, "LONG", TickRounding.UP),
        (PriceRole.STOP_LOSS, "SHORT", TickRounding.DOWN),
        (PriceRole.TAKE_PROFIT, "LONG", TickRounding.DOWN),
        (PriceRole.TAKE_PROFIT, "SHORT", TickRounding.UP),
    ])
    def test_the_table_is_exactly_this(self, role, direction, expected):
        assert conservative_rounding(role, direction) is expected

    def test_the_table_covers_every_role_and_direction(self):
        for role in PriceRole:
            for direction in (DIRECTION_LONG, DIRECTION_SHORT):
                assert isinstance(conservative_rounding(role, direction),
                                  TickRounding)

    def test_bad_role_or_direction_is_refused(self):
        with pytest.raises(QuantizationError, match="PriceRole"):
            conservative_rounding("ENTRY", "LONG")
        with pytest.raises(QuantizationError, match="direction must be"):
            conservative_rounding(PriceRole.ENTRY, "long")

    @pytest.mark.parametrize("tick", sorted(set(REAL_TICKS.values())))
    def test_quantized_risk_distance_never_widens(self, tick):
        """Swept across every real tick and a full grid of sub-tick offsets."""
        checked = 0
        step = tick / 7                       # deliberately off-grid offsets
        for k in range(40):
            offset = step * k
            entry_l = Decimal("1000") + offset
            sl_l = Decimal("990") + offset * 2
            tp_l = Decimal("1006") + offset * 3
            for direction, e, s, t in (
                ("LONG", entry_l, sl_l, tp_l),
                ("SHORT", entry_l, Decimal("1010") + offset * 2,
                 Decimal("994") + offset * 3),
            ):
                bracket = quantize_bracket("ANY", direction, e, s, t, tick)
                assert bracket.risk_dist <= abs(e - s)
                assert abs(bracket.reward_dist - abs(t - e)) < tick
                checked += 1
        assert checked == 80


class TestQuantizeBracket:
    """All three legs on-grid, or a refusal — never a half-quantized bracket."""

    def test_short_bracket_legs_use_the_conservative_directions(self):
        b = quantize_bracket("BTCUSD", "SHORT", Decimal("60123.37"),
                             Decimal("60250.11"), Decimal("59762.63"),
                             Decimal("0.5"))
        assert b.entry_price == Decimal("60123.5")     # UP
        assert b.sl_price == Decimal("60250.0")        # DOWN
        assert b.tp_price == Decimal("59763.0")        # UP
        assert (b.entry_rounding, b.sl_rounding, b.tp_rounding) == (
            TickRounding.UP, TickRounding.DOWN, TickRounding.UP)
        assert b.tp_price < b.entry_price < b.sl_price

    def test_long_bracket_legs_use_the_conservative_directions(self):
        b = quantize_bracket("BTCUSD", "LONG", Decimal("60123.37"),
                             Decimal("59990.11"), Decimal("60484.11"),
                             Decimal("0.5"))
        assert b.entry_price == Decimal("60123.0")     # DOWN
        assert b.sl_price == Decimal("59990.5")        # UP
        assert b.tp_price == Decimal("60484.0")        # DOWN
        assert (b.entry_rounding, b.sl_rounding, b.tp_rounding) == (
            TickRounding.DOWN, TickRounding.UP, TickRounding.DOWN)
        assert b.tp_price > b.entry_price > b.sl_price

    def test_raw_prices_are_retained_for_audit(self):
        b = quantize_bracket("ETHUSD", "LONG", Decimal("2345.678"),
                             Decimal("2300.123"), Decimal("2359.75"),
                             Decimal("0.05"))
        assert b.raw_entry_price == Decimal("2345.678")
        assert b.raw_sl_price == Decimal("2300.123")
        assert b.raw_tp_price == Decimal("2359.75")
        assert b.tick_size == Decimal("0.05")
        assert b.asset == "ETHUSD" and b.direction == "LONG"
        assert isinstance(b, QuantizedBracket)

    @pytest.mark.parametrize("symbol", ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"])
    def test_every_leg_is_on_grid_for_every_real_tick(self, symbol):
        tick = REAL_TICKS[symbol]
        b = quantize_bracket(symbol, "SHORT", Decimal("1000.123456"),
                             Decimal("1010.987654"), Decimal("994.001234"),
                             tick)
        for leg in (b.entry_price, b.sl_price, b.tp_price):
            assert is_on_tick_grid(leg, tick)

    def test_a_bracket_narrower_than_the_grid_is_refused(self):
        with pytest.raises(BracketGeometryError, match="too narrow"):
            quantize_bracket("BTCUSD", "LONG", Decimal("100.2"),
                             Decimal("100.1"), Decimal("100.3"),
                             Decimal("0.5"))

    def test_a_short_bracket_narrower_than_the_grid_is_refused(self):
        with pytest.raises(BracketGeometryError, match="destroyed"):
            quantize_bracket("BTCUSD", "SHORT", Decimal("100.2"),
                             Decimal("100.3"), Decimal("100.1"),
                             Decimal("0.5"))

    def test_the_same_bracket_survives_on_a_finer_grid(self):
        """Proof the refusal is about the grid, not about the geometry."""
        b = quantize_bracket("BTCUSD", "LONG", Decimal("100.2"),
                             Decimal("100.1"), Decimal("100.3"),
                             Decimal("0.01"))
        assert (b.sl_price, b.entry_price, b.tp_price) == (
            Decimal("100.10"), Decimal("100.20"), Decimal("100.30"))

    def test_bracket_refuses_bad_direction_and_bad_tick(self):
        with pytest.raises(QuantizationError, match="direction must be"):
            quantize_bracket("BTCUSD", "buy", Decimal("100"), Decimal("99"),
                             Decimal("101"), Decimal("0.5"))
        with pytest.raises(InvalidTickSizeError):
            quantize_bracket("BTCUSD", "LONG", Decimal("100"), Decimal("99"),
                             Decimal("101"), Decimal("0"))

    def test_bracket_refuses_a_non_positive_leg(self):
        with pytest.raises(InvalidPriceError):
            quantize_bracket("BTCUSD", "LONG", Decimal("100"), Decimal("0"),
                             Decimal("101"), Decimal("0.5"))


class TestOBBracketBoundary:
    """
    Quantizing a `ManualOBRecord` must not disturb the float lifecycle that
    the oracle-equivalence tests depend on.
    """

    def _ob(self, direction="SHORT", top=60250.37, bottom=60100.11):
        return _make_manual_ob(
            asset="BTCUSD", bos_bar_idx=1,
            bos_dt=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            origin_bar_idx=0,
            origin_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
            direction=direction, ob_top=top, ob_bottom=bottom, cfg=CFG)

    def test_short_ob_is_quantized_against_the_real_btc_spec(self):
        ob = self._ob("SHORT")
        spec = get_product_specification("BTCUSD")
        b = quantize_ob_bracket(ob, spec)
        assert b.tick_size == Decimal("0.5")
        for leg in (b.entry_price, b.sl_price, b.tp_price):
            assert is_on_tick_grid(leg, Decimal("0.5"))
        assert b.tp_price < b.entry_price < b.sl_price
        assert b.raw_entry_price == price_from_strategy_float(ob.entry_price)

    def test_long_ob_is_quantized_against_the_real_btc_spec(self):
        ob = self._ob("LONG")
        b = quantize_ob_bracket(ob, get_product_specification("BTCUSD"))
        assert b.tp_price > b.entry_price > b.sl_price
        assert b.direction == "LONG"

    def test_the_ob_record_is_never_mutated(self):
        ob = self._ob("SHORT")
        before = (ob.entry_price, ob.sl_price, ob.tp_price, ob.sl_dist_pct,
                  ob.applied_leverage, ob.state)
        quantize_ob_bracket(ob, get_product_specification("BTCUSD"))
        after = (ob.entry_price, ob.sl_price, ob.tp_price, ob.sl_dist_pct,
                 ob.applied_leverage, ob.state)
        assert before == after
        assert all(isinstance(v, float) for v in before[:5])

    def test_quantized_sl_distance_does_not_widen_for_a_real_ob(self):
        for direction in ("SHORT", "LONG"):
            ob = self._ob(direction)
            b = quantize_ob_bracket(ob, get_product_specification("BTCUSD"))
            raw_risk = abs(price_from_strategy_float(ob.entry_price)
                           - price_from_strategy_float(ob.sl_price))
            assert b.risk_dist <= raw_risk


class TestNoQuantityOrContractValue:
    """Order size and contract semantics stay out of this module."""

    def test_module_source_mentions_no_sizing_concept_in_code(self):
        code = _code_without_strings()
        for banned in ("quantity", "contract_value", "size_step", "min_size",
                       "notional", "leverage", "margin", "fee"):
            assert banned not in code, f"{banned!r} appears in quantization.py"

    def test_the_public_surface_exposes_no_sizing_function(self):
        from quantedge.strategy.manual_smc import quantization as q
        for name in q.__all__:
            lowered = name.lower()
            for banned in ("quantity", "qty", "contract", "notional",
                           "leverage", "margin", "order_size", "position"):
                assert banned not in lowered, name
        # `size` is permitted only as part of the tick size vocabulary
        assert all("size" not in n.lower() or "tick" in n.lower()
                   for n in q.__all__)

    def test_quantized_bracket_carries_prices_only(self):
        from dataclasses import fields
        names = {f.name for f in fields(QuantizedBracket)}
        assert names == {
            "asset", "direction", "tick_size", "entry_price", "sl_price",
            "tp_price", "raw_entry_price", "raw_sl_price", "raw_tp_price",
            "entry_rounding", "sl_rounding", "tp_rounding",
        }

    def test_contract_value_on_a_spec_is_ignored_entirely(self):
        """A spec's contract_value must not influence any price."""
        base = get_product_specification("BTCUSD")
        odd = ProductSpecification(
            symbol=base.symbol, product_id=base.product_id,
            min_size=base.min_size, size_step=base.size_step,
            tick_size=base.tick_size, max_leverage=base.max_leverage,
            contract_value=Decimal("0.001"))
        ob = _make_manual_ob(
            asset="BTCUSD", bos_bar_idx=1,
            bos_dt=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            origin_bar_idx=0,
            origin_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
            direction="SHORT", ob_top=60250.37, ob_bottom=60100.11, cfg=CFG)
        assert quantize_ob_bracket(ob, base) == quantize_ob_bracket(ob, odd)


class TestModuleIndependence:
    """No exchange, DB, execution, runtime, Java or live wiring."""

    def test_imports_are_stdlib_plus_one_sibling_only(self):
        imports = [ln.strip() for ln in _code_lines()
                   if ln.startswith(("import ", "from "))]
        assert imports == [
            "from __future__ import annotations",
            "from dataclasses import dataclass",
            "from decimal import Decimal, InvalidOperation, localcontext",
            "from enum import Enum",
            "from typing import Protocol, Union, runtime_checkable",
            "from quantedge.strategy.manual_smc.models import ManualOBRecord",
        ]

    def test_it_does_not_import_the_execution_package(self):
        code = _code_without_strings()
        for banned in ("quantedge.execution", "delta_client", "httpx",
                       "requests", "psycopg", "sqlalchemy", "asyncio",
                       "ProductSpecification"):
            assert banned not in code, f"{banned!r} imported/referenced"

    def test_no_execution_or_persistence_verbs_appear(self):
        code = _code_without_strings()
        for banned in ("place_order", "submit", "cancel", "api_key", "secret",
                       "INSERT", "SELECT", "commit(", "session.", "client.",
                       "kill_switch", "expiresAt", "expires_at", "live_"):
            assert banned not in code, f"{banned!r} appears in quantization.py"

    def test_the_modules_own_dependency_closure_has_no_exchange_transport(self):
        """
        Measure `quantization.py`'s OWN closure.

        Run in a subprocess so an httpx already loaded by another test module
        cannot mask the result, and with STUB parent packages pre-seeded into
        `sys.modules` so the pre-existing `quantedge/__init__.py` (which does
        `from . import execution`) does not execute. What is left is exactly
        what `quantization.py` and its one sibling actually need.
        """
        import subprocess
        import sys
        code = (
            "import sys, types, pathlib;"
            "root=pathlib.Path('src').resolve();"
            "sys.path.insert(0,str(root));"
            "[sys.modules.setdefault(n, _m) for n, _m in "
            "[(n, types.ModuleType(n)) for n in ("
            "'quantedge','quantedge.strategy','quantedge.strategy.manual_smc')]];"
            "sys.modules['quantedge'].__path__=[str(root/'quantedge')];"
            "sys.modules['quantedge.strategy'].__path__="
            "[str(root/'quantedge'/'strategy')];"
            "sys.modules['quantedge.strategy.manual_smc'].__path__="
            "[str(root/'quantedge'/'strategy'/'manual_smc')];"
            "import quantedge.strategy.manual_smc.quantization as q;"
            "q.quantize_price(q.Decimal('100.3'),q.Decimal('0.5'),"
            "q.TickRounding.UP);"
            "third=sorted(n for n in sys.modules "
            "if getattr(sys.modules[n],'__file__',None) "
            "and 'site-packages' in str(sys.modules[n].__file__));"
            "bad=[m for m in ('httpx','cryptography','quantedge.execution',"
            "'quantedge.execution.delta_client',"
            "'quantedge.execution.validation') if m in sys.modules];"
            "print('LOADED:'+','.join(bad+third))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        assert "LOADED:" in out.stdout
        assert out.stdout.strip().split("LOADED:")[1] == ""

    def test_it_adds_no_transport_beyond_the_pre_existing_package_import(self):
        """
        PRE-EXISTING FINDING, pinned rather than hidden.

        `quantedge/__init__.py` ends with `from . import execution`, so merely
        importing the top-level `quantedge` package already loads httpx, the
        signed Delta REST client and the AESGCM credential crypto. That is not
        caused by this module and fixing it is a separate change; what this
        test proves is that `quantization.py` adds NOTHING to that baseline.
        """
        import subprocess
        import sys
        probe = (
            "import sys;"
            "{stmt};"
            "print('SET:'+','.join(sorted(m for m in sys.modules "
            "if m.startswith(('httpx','cryptography','quantedge.execution')))))"
        )

        def _snapshot(stmt: str) -> set:
            out = subprocess.run(
                [sys.executable, "-c", probe.format(stmt=stmt)],
                cwd=str(Path(__file__).parent.parent),
                capture_output=True, text=True, timeout=120)
            assert out.returncode == 0, out.stderr
            body = out.stdout.strip().split("SET:")[1]
            return set(filter(None, body.split(",")))

        baseline = _snapshot("import quantedge")
        with_module = _snapshot(
            "import quantedge.strategy.manual_smc.quantization")
        assert with_module == baseline
        # And the baseline is non-empty, i.e. the coupling really is the parent
        # package's. If a later change makes `quantedge/__init__.py` lazy this
        # assertion flips and both sets become empty — tighten it then.
        assert "httpx" in baseline
