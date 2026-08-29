"""
`DeltaPosition.from_dict` must never invent a position's identity.

The parse used to read `data.get("product_symbol", "BTCUSD")` and
`int(data.get("product_id", 0))`, so a `/v2/positions` entry missing its symbol
came back as a BTCUSD position and one missing its product id came back as
product `0`. `DeltaIndiaClient.get_positions` feeds this parse directly, and the
account synchronizer, reconciliation and the flatten path all act on whatever
instrument it names.

Symbol resolution goes through the instrument registry -- the single source of
verified symbols -- `product_id` must be an exact positive integer, and the two
identity fields must agree: `product_id` has to equal the registry's verified id
for the parsed symbol. Missing, `None`, `0`, negative, bool, non-numeric and
fractional ids fail closed with `UnknownInstrumentError` instead of becoming `0`.

This is the same contract `DeltaOrderResponse.from_dict` enforces. The
cross-check was deferred one round while three `/v2/positions` fixtures carrying
ids fabricated by incrementing 27 were corrected; see
`TestAContradictoryPairFailsClosed` for the exact lines.

Zero network access: every payload here is a literal dict.
"""

from decimal import Decimal
import pytest

from quantedge.execution.models import DeltaPosition, PositionSide
from quantedge.instruments import UnknownInstrumentError, delta_india_registry

#: Verified native symbols with their pinned product ids.
NATIVE = (("BTCUSD", 27), ("ETHUSD", 3136), ("SOLUSD", 14823),
          ("XRPUSD", 14969))


def _payload(**overrides) -> dict:
    """A well-formed `/v2/positions` entry; overrides drive each case."""
    payload = {
        "product_id": 27,
        "product_symbol": "BTCUSD",
        "size": "3",
        "entry_price": "77000.0",
        "mark_price": "77500.5",
        "liquidation_price": "70000.0",
        "unrealised_pnl": "12.5",
        "realised_pnl": "4.25",
        "leverage": "10",
        "margin": "231.0",
        "adl_level": 2,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Every verified native identity parses unchanged.
# ---------------------------------------------------------------------------
class TestVerifiedIdentitiesAreParsedExactly:
    @pytest.mark.parametrize("symbol,product_id", NATIVE)
    def test_a_native_symbol_and_its_verified_id_succeed(self, symbol,
                                                         product_id):
        pos = DeltaPosition.from_dict(
            _payload(product_symbol=symbol, product_id=product_id))
        assert pos.product_symbol == symbol
        assert pos.product_id == product_id

    @pytest.mark.parametrize("symbol,product_id", NATIVE)
    def test_the_parsed_symbol_is_the_registered_one(self, symbol, product_id):
        pos = DeltaPosition.from_dict(
            _payload(product_symbol=symbol, product_id=product_id))
        assert pos.product_symbol == delta_india_registry().get(symbol).symbol

    def test_the_legacy_symbol_key_is_still_honoured(self):
        """
        The repository already relies on the `symbol` fallback key (the
        multi-user mock exchange and Delta's own fallback shape both use it).
        """
        payload = _payload(product_id=3136)
        del payload["product_symbol"]
        payload["symbol"] = "ETHUSD"
        assert DeltaPosition.from_dict(payload).product_symbol == "ETHUSD"

    def test_product_symbol_wins_over_the_legacy_key(self):
        pos = DeltaPosition.from_dict(
            _payload(product_symbol="BTCUSD", symbol="ETHUSD"))
        assert pos.product_symbol == "BTCUSD"

    @pytest.mark.parametrize("raw", (27, "27", " 27 ", 27.0, Decimal("27"),
                                     "\t27\n"))
    def test_an_exact_integral_product_id_is_accepted(self, raw):
        """JSON serialisers vary; an exactly integral id is not ambiguous."""
        assert DeltaPosition.from_dict(
            _payload(product_id=raw)).product_id == 27


# ---------------------------------------------------------------------------
# Unusable symbols fail closed instead of becoming BTCUSD.
# ---------------------------------------------------------------------------
class TestAnUnusableSymbolFailsClosed:
    def test_a_missing_symbol_fails_closed(self):
        payload = _payload()
        del payload["product_symbol"]
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(payload)

    def test_a_none_symbol_fails_closed(self):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=None))

    @pytest.mark.parametrize("blank", ("", " ", "   ", "\t", "\n", " \t\n "))
    def test_a_blank_symbol_fails_closed(self, blank):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=blank))

    @pytest.mark.parametrize("folded", ("btcusd", "BtcUsd", " BTCUSD ",
                                        "\tBTCUSD\n", "BTCUSD "))
    def test_a_case_or_whitespace_variant_is_not_folded_in(self, folded):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=folded))

    @pytest.mark.parametrize("suffixed", ("BTCUSD.P", "ETHUSD.P", "SOLUSD.P",
                                          "XRPUSD.P"))
    def test_a_display_suffix_symbol_fails_closed(self, suffixed):
        """`.P` is display/persistence only and is not a tradable alias."""
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=suffixed))

    @pytest.mark.parametrize("separated", ("BTC-USD", "BTC_USD", "BTC/USD",
                                           "BTC:USD", "BTC USD"))
    def test_a_separator_variant_fails_closed(self, separated):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=separated))

    @pytest.mark.parametrize("unknown", ("FOOUSD", "BTCUSDT", "DOGEUSD",
                                         "LTCUSD", "BTC", "USD"))
    def test_an_unknown_symbol_fails_closed(self, unknown):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=unknown))

    @pytest.mark.parametrize("bad", (27, 0.001, True, b"BTCUSD", ("BTCUSD",),
                                     ["BTCUSD"], {"symbol": "BTCUSD"}))
    def test_a_non_string_symbol_fails_closed(self, bad):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=bad))

    def test_an_empty_payload_fails_closed(self):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict({})

    @pytest.mark.parametrize("bad", (None, "", "  ", "btcusd", "BTCUSD.P",
                                     "BTC-USD", "FOOUSD", 27, ["BTCUSD"]))
    def test_nothing_silently_becomes_btcusd(self, bad):
        """The removed default was literally `"BTCUSD"`."""
        try:
            pos = DeltaPosition.from_dict(_payload(product_symbol=bad))
        except UnknownInstrumentError:
            return
        pytest.fail(f"{bad!r} parsed into {pos.product_symbol!r} "
                    f"instead of failing closed")


# ---------------------------------------------------------------------------
# An unusable product id fails closed instead of becoming 0.
# ---------------------------------------------------------------------------
class TestAnUnusableProductIdFailsClosed:
    def test_a_missing_product_id_fails_closed(self):
        payload = _payload()
        del payload["product_id"]
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(payload)

    def test_a_none_product_id_fails_closed(self):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_id=None))

    @pytest.mark.parametrize("zero", (0, "0", " 0 ", 0.0, Decimal("0"),
                                      "-0", "0.0"))
    def test_a_zero_product_id_fails_closed(self, zero):
        """`0` was the old default and identifies no product."""
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_id=zero))

    @pytest.mark.parametrize("neg", (-1, "-1", -27, "-27", Decimal("-27"),
                                     -3136))
    def test_a_negative_product_id_fails_closed(self, neg):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_id=neg))

    @pytest.mark.parametrize("bad", ("", "  ", "abc", "27abc", "2 7", "0x1b",
                                     "twenty-seven", "NaN", "Infinity"))
    def test_a_non_numeric_product_id_fails_closed(self, bad):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_id=bad))

    @pytest.mark.parametrize("bad", (27.5, "27.5", Decimal("27.0001"), 26.999))
    def test_a_fractional_product_id_fails_closed(self, bad):
        """Truncating 27.5 to 27 would silently name a different product."""
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_id=bad))

    @pytest.mark.parametrize("bad", (True, False, [27], (27,), {"id": 27},
                                     b"27"))
    def test_a_non_numeric_type_product_id_fails_closed(self, bad):
        """`True` would otherwise become product 1."""
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_id=bad))

    @pytest.mark.parametrize("bad", (None, "", "abc", 0, "0", -27, 27.5, True,
                                     [27]))
    def test_no_unusable_product_id_becomes_zero(self, bad):
        try:
            pos = DeltaPosition.from_dict(_payload(product_id=bad))
        except UnknownInstrumentError:
            return
        assert pos.product_id != 0, f"{bad!r} silently became product 0"
        pytest.fail(f"{bad!r} parsed into product {pos.product_id} "
                    f"instead of failing closed")

    def test_the_parse_no_longer_contains_an_identity_default(self):
        """
        Structural: neither identity field may carry a literal default. The
        nested `data.get("product_symbol", data.get("symbol"))` fallback is
        allowed -- it reads a second real key rather than inventing a value.
        """
        import ast
        import inspect
        import textwrap

        src = textwrap.dedent(
            inspect.getsource(DeltaPosition.from_dict.__func__))
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"):
                continue
            keys = [a.value for a in node.args
                    if isinstance(a, ast.Constant)]
            if "product_id" not in keys and "product_symbol" not in keys:
                continue
            for default in node.args[1:]:
                assert not isinstance(default, ast.Constant), ast.dump(node)


def _cross_check_source() -> str:
    """Source of the parse, for the structural assertions below."""
    import inspect
    return inspect.getsource(DeltaPosition.from_dict.__func__)


# ---------------------------------------------------------------------------
# The two identity fields must agree.
# ---------------------------------------------------------------------------
class TestAContradictoryPairFailsClosed:
    """
    `DeltaPosition.from_dict` now enforces the same cross-check
    `DeltaOrderResponse.from_dict` does: `product_id` must equal the registry's
    verified id for the parsed symbol.

    Enabling it required correcting three `/v2/positions` fixtures whose ids had
    been fabricated by incrementing 27 --

      * `test_phase5_1_delta_client.py:349`  ETHUSD  28 -> 3136
      * `test_phase5_1_delta_client.py:362`  SOLUSD  29 -> 14823
      * `test_phase5_2_account_sync.py:135`  ETHUSD  28 -> 3136

    Neither 28 nor 29 was the verified id of any registered symbol. Those tests
    assert on long/short parsing and zero-size filtering, not on product ids.
    """

    def test_the_previously_recorded_gap_is_closed(self):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(
                _payload(product_symbol="ETHUSD", product_id=27))
        assert delta_india_registry().get("ETHUSD").product_id == 3136

    def test_no_fabricated_id_belongs_to_any_registered_symbol(self):
        verified = {delta_india_registry().get(s).product_id
                    for s, _pid in NATIVE}
        assert 28 not in verified
        assert 29 not in verified

    @pytest.mark.parametrize("fabricated", (28, 29))
    def test_the_corrected_fixture_ids_now_fail_closed(self, fabricated):
        """The exact ids the three stale fixtures used to carry."""
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(
                _payload(product_symbol="ETHUSD", product_id=fabricated))

    @pytest.mark.parametrize("symbol,product_id", NATIVE)
    def test_each_verified_pair_is_accepted(self, symbol, product_id):
        pos = DeltaPosition.from_dict(
            _payload(product_symbol=symbol, product_id=product_id))
        assert (pos.product_symbol, pos.product_id) == (symbol, product_id)

    @pytest.mark.parametrize("symbol,wrong_id", (
        ("BTCUSD", 3136),
        ("ETHUSD", 27),
        ("SOLUSD", 14969),
        ("XRPUSD", 14823),
    ))
    def test_a_cross_paired_identity_fails_closed(self, symbol, wrong_id):
        """Each symbol paired with another verified symbol's id."""
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(
                _payload(product_symbol=symbol, product_id=wrong_id))

    @pytest.mark.parametrize("symbol,_pid", NATIVE)
    @pytest.mark.parametrize("_other,other_id", NATIVE)
    def test_the_full_pairing_matrix_holds(self, symbol, _pid, _other,
                                           other_id):
        """
        Exhaustive 4x4: a pair is accepted exactly when the id is the symbol's
        own verified id, and refused otherwise.
        """
        payload = _payload(product_symbol=symbol, product_id=other_id)
        if other_id == delta_india_registry().get(symbol).product_id:
            assert DeltaPosition.from_dict(payload).product_id == other_id
        else:
            with pytest.raises(UnknownInstrumentError):
                DeltaPosition.from_dict(payload)

    @pytest.mark.parametrize("unrelated", (1, 26, 28, 30, 3135, 3137, 14822,
                                           14824, 14968, 14970, 999999))
    def test_an_unrelated_or_off_by_one_product_id_fails_closed(self,
                                                                unrelated):
        """Off-by-one and unknown ids are refused, not silently trusted."""
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_id=unrelated))

    def test_the_legacy_symbol_key_is_cross_checked_too(self):
        payload = _payload(product_id=27)
        del payload["product_symbol"]
        payload["symbol"] = "ETHUSD"
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(payload)

    @pytest.mark.parametrize("symbol,wrong_id", (
        ("BTCUSD", 3136),
        ("ETHUSD", 27),
        ("SOLUSD", 14969),
        ("XRPUSD", 14823),
    ))
    def test_a_contradiction_cannot_materialize_a_position(self, symbol,
                                                           wrong_id):
        """
        No `DeltaPosition` object may come back at all -- neither field may win.
        """
        try:
            pos = DeltaPosition.from_dict(
                _payload(product_symbol=symbol, product_id=wrong_id))
        except UnknownInstrumentError:
            return
        pytest.fail(f"{symbol} + {wrong_id} materialized "
                    f"{pos.product_symbol} + {pos.product_id}")

    def test_the_cross_check_compares_against_the_registry_spec(self):
        """The comparison must read the spec, not a literal."""
        assert "spec.product_id" in _cross_check_source()

    def test_no_verified_product_id_is_hardcoded_in_the_parse(self):
        """
        Structural, on the AST so an explanatory comment naming 27 or 3136
        cannot trip it: identity is resolved from the registry, so no verified
        product id may appear as a literal in the module.
        """
        import ast
        import inspect
        from quantedge.execution import models as mod

        verified = {pid for _symbol, pid in NATIVE}
        literals = {
            node.value
            for node in ast.walk(ast.parse(inspect.getsource(mod)))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
        }
        assert not (literals & verified), literals & verified


# ---------------------------------------------------------------------------
# Every other position field is parsed exactly as before.
# ---------------------------------------------------------------------------
class TestUnrelatedBehaviourIsUnchanged:
    def test_a_full_position_parses_identically(self):
        pos = DeltaPosition.from_dict(_payload())
        assert pos.side is PositionSide.LONG
        assert pos.size == Decimal("3")
        assert pos.entry_price == Decimal("77000.0")
        assert pos.mark_price == Decimal("77500.5")
        assert pos.liquidation_price == Decimal("70000.0")
        assert pos.unrealized_pnl == Decimal("12.5")
        assert pos.realized_pnl == Decimal("4.25")
        assert pos.leverage == Decimal("10")
        assert pos.margin == Decimal("231.0")
        assert pos.adl_level == 2

    @pytest.mark.parametrize("size,side,abs_size", (
        ("3", PositionSide.LONG, Decimal("3")),
        ("-3", PositionSide.SHORT, Decimal("3")),
        ("0", PositionSide.LONG, Decimal("0")),
        ("-0.5", PositionSide.SHORT, Decimal("0.5")),
        (7, PositionSide.LONG, Decimal("7")),
        (-7, PositionSide.SHORT, Decimal("7")),
    ))
    def test_side_and_magnitude_still_derive_from_the_signed_size(
            self, size, side, abs_size):
        pos = DeltaPosition.from_dict(_payload(size=size))
        assert pos.side is side
        assert pos.size == abs_size

    def test_a_missing_size_is_still_a_flat_long(self):
        payload = _payload()
        del payload["size"]
        pos = DeltaPosition.from_dict(payload)
        assert pos.size == Decimal("0")
        assert pos.side is PositionSide.LONG

    @pytest.mark.parametrize("raw", (None, "", "   "))
    def test_a_missing_liquidation_price_is_still_none(self, raw):
        assert DeltaPosition.from_dict(
            _payload(liquidation_price=raw)).liquidation_price is None

    def test_an_absent_liquidation_key_is_still_none(self):
        payload = _payload()
        del payload["liquidation_price"]
        assert DeltaPosition.from_dict(payload).liquidation_price is None

    def test_both_pnl_spellings_are_still_accepted(self):
        payload = _payload()
        del payload["unrealised_pnl"]
        del payload["realised_pnl"]
        payload["unrealized_pnl"] = "9.75"
        payload["realized_pnl"] = "1.25"
        pos = DeltaPosition.from_dict(payload)
        assert pos.unrealized_pnl == Decimal("9.75")
        assert pos.realized_pnl == Decimal("1.25")

    def test_the_british_spelling_still_wins_when_both_are_present(self):
        pos = DeltaPosition.from_dict(
            _payload(unrealised_pnl="12.5", unrealized_pnl="99.0",
                     realised_pnl="4.25", realized_pnl="88.0"))
        assert pos.unrealized_pnl == Decimal("12.5")
        assert pos.realized_pnl == Decimal("4.25")

    def test_leverage_and_margin_still_have_their_defaults(self):
        payload = _payload()
        del payload["leverage"]
        del payload["margin"]
        pos = DeltaPosition.from_dict(payload)
        assert pos.leverage == Decimal("1")
        assert pos.margin == Decimal("0")

    def test_an_absent_adl_level_is_still_none(self):
        payload = _payload()
        del payload["adl_level"]
        assert DeltaPosition.from_dict(payload).adl_level is None

    def test_updated_at_is_still_timezone_aware_utc(self):
        from datetime import timezone
        pos = DeltaPosition.from_dict(_payload())
        assert pos.updated_at.tzinfo is not None
        assert pos.updated_at.utcoffset() == timezone.utc.utcoffset(None)

    def test_prices_are_still_exact_decimals(self):
        pos = DeltaPosition.from_dict(
            _payload(entry_price="77000.123456789",
                     mark_price="77500.987654321"))
        assert pos.entry_price == Decimal("77000.123456789")
        assert pos.mark_price == Decimal("77500.987654321")

    def test_absent_prices_still_default_to_zero(self):
        payload = _payload()
        del payload["entry_price"]
        del payload["mark_price"]
        pos = DeltaPosition.from_dict(payload)
        assert pos.entry_price == Decimal("0")
        assert pos.mark_price == Decimal("0")
