"""
`DeltaOrderResponse.from_dict` must never invent an order's identity.

The parse used to read
`str(data.get("product_symbol", data.get("symbol", ""))).upper()` and
`int(data.get("product_id", 0))`, so an order response missing its symbol came
back as an order on `""`, a case-folded or padded symbol was folded into a
registered one, and an order with no product id came back as product `0`.
Downstream (reconciliation, the synchronizer, bracket placement) then acts on an
order whose instrument the exchange never named.

Identity is now resolved through the instrument registry -- the same exact,
fail-closed lookup `DeltaPosition.from_dict` uses -- a product id that is absent
or not an exact integer fails closed, and the two identity fields must agree:
`product_id` has to equal the registry's verified id for the parsed symbol.

That last cross-check was deferred in an earlier step because
`multi_user_orchestrator` resolved ids from a live product catalogue payload, so
contradictory pairs reached this parser. The orchestrator now resolves identity
from the registry, and the one remaining contradictory fixture (an ETHUSD fill
response carrying `product_id: 27`) has been corrected to the verified 3136, so
the check is enabled. `TestAContradictoryPairFailsClosed` below replaces the
recorded-gap test that pinned the old behaviour.

Zero network access: every payload here is a literal dict.
"""

from decimal import Decimal
import pytest

from quantedge.execution.models import (
    DeltaOrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
)
from quantedge.instruments import UnknownInstrumentError, delta_india_registry

#: Verified native symbols with their pinned product ids.
NATIVE = (("BTCUSD", 27), ("ETHUSD", 3136), ("SOLUSD", 14823),
          ("XRPUSD", 14969))


def _payload(**overrides) -> dict:
    """A well-formed `/v2/orders` response; overrides drive each case."""
    payload = {
        "id": 910001,
        "client_order_id": "qe-setup-1",
        "user_id": 5511,
        "product_id": 27,
        "product_symbol": "BTCUSD",
        "side": "buy",
        "order_type": "limit_order",
        "size": "3",
        "unfilled_size": "1",
        "limit_price": "77000.0",
        "stop_price": None,
        "average_fill_price": "77001.5",
        "state": "open",
        "reduce_only": False,
        "created_at": 1756339200000000,
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
        resp = DeltaOrderResponse.from_dict(
            _payload(product_symbol=symbol, product_id=product_id))
        assert resp.product_symbol == symbol
        assert resp.product_id == product_id

    @pytest.mark.parametrize("symbol,product_id", NATIVE)
    def test_the_parsed_symbol_is_the_registered_one(self, symbol, product_id):
        resp = DeltaOrderResponse.from_dict(
            _payload(product_symbol=symbol, product_id=product_id))
        assert resp.product_symbol == delta_india_registry().get(symbol).symbol

    def test_the_legacy_symbol_key_is_still_honoured(self):
        """
        The repository already relies on the `symbol` fallback key (the
        multi-user mock exchange and Delta's own fallback shape both use it).
        """
        payload = _payload(product_id=3136)
        del payload["product_symbol"]
        payload["symbol"] = "ETHUSD"
        assert DeltaOrderResponse.from_dict(payload).product_symbol == "ETHUSD"

    def test_product_symbol_wins_over_the_legacy_key(self):
        resp = DeltaOrderResponse.from_dict(
            _payload(product_symbol="BTCUSD", symbol="ETHUSD"))
        assert resp.product_symbol == "BTCUSD"

    @pytest.mark.parametrize("raw", (27, "27", " 27 ", 27.0, Decimal("27")))
    def test_an_exact_integral_product_id_is_accepted(self, raw):
        """JSON serialisers vary; an exactly integral id is not ambiguous."""
        assert DeltaOrderResponse.from_dict(
            _payload(product_id=raw)).product_id == 27


# ---------------------------------------------------------------------------
# Unusable symbols fail closed.
# ---------------------------------------------------------------------------
class TestAnUnusableSymbolFailsClosed:
    def test_a_missing_symbol_fails_closed(self):
        payload = _payload()
        del payload["product_symbol"]
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(payload)

    def test_a_none_symbol_fails_closed(self):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_symbol=None))

    @pytest.mark.parametrize("blank", ("", " ", "   ", "\t", "\n", " \t\n "))
    def test_a_blank_symbol_fails_closed(self, blank):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_symbol=blank))

    @pytest.mark.parametrize("folded", ("btcusd", "BtcUsd", " BTCUSD ",
                                        "\tBTCUSD\n", "BTCUSD "))
    def test_a_case_or_whitespace_variant_is_not_folded_in(self, folded):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_symbol=folded))

    @pytest.mark.parametrize("suffixed", ("BTCUSD.P", "ETHUSD.P", "SOLUSD.P",
                                          "XRPUSD.P"))
    def test_a_display_suffix_symbol_fails_closed(self, suffixed):
        """`.P` is display/persistence only and is not a tradable alias."""
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_symbol=suffixed))

    @pytest.mark.parametrize("separated", ("BTC-USD", "BTC_USD", "BTC/USD",
                                           "BTC:USD", "BTC USD"))
    def test_a_separator_variant_fails_closed(self, separated):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_symbol=separated))

    @pytest.mark.parametrize("unknown", ("FOOUSD", "BTCUSDT", "DOGEUSD",
                                         "LTCUSD", "BTC", "USD"))
    def test_an_unknown_symbol_fails_closed(self, unknown):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_symbol=unknown))

    @pytest.mark.parametrize("bad", (27, 0.001, True, b"BTCUSD", ("BTCUSD",),
                                     ["BTCUSD"], {"symbol": "BTCUSD"}))
    def test_a_non_string_symbol_fails_closed(self, bad):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_symbol=bad))

    def test_an_empty_payload_fails_closed(self):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict({})

    @pytest.mark.parametrize("bad", (None, "", "  ", "btcusd", "BTCUSD.P",
                                     "BTC-USD", "FOOUSD", 27, ["BTCUSD"]))
    def test_no_unusable_symbol_yields_an_order_at_all(self, bad):
        try:
            resp = DeltaOrderResponse.from_dict(_payload(product_symbol=bad))
        except UnknownInstrumentError:
            return
        pytest.fail(f"{bad!r} parsed into {resp.product_symbol!r} "
                    f"instead of failing closed")


# ---------------------------------------------------------------------------
# An unusable product id fails closed instead of becoming 0.
# ---------------------------------------------------------------------------
class TestAnUnusableProductIdFailsClosed:
    def test_a_missing_product_id_fails_closed(self):
        payload = _payload()
        del payload["product_id"]
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(payload)

    def test_a_none_product_id_fails_closed(self):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_id=None))

    @pytest.mark.parametrize("bad", ("", "  ", "abc", "27abc", "2 7", "0x1b",
                                     "twenty-seven", "NaN", "Infinity"))
    def test_a_non_numeric_product_id_fails_closed(self, bad):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_id=bad))

    @pytest.mark.parametrize("bad", (27.5, "27.5", Decimal("27.0001")))
    def test_a_fractional_product_id_fails_closed(self, bad):
        """Truncating 27.5 to 27 would silently name a different product."""
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_id=bad))

    @pytest.mark.parametrize("bad", (True, False, [27], (27,), {"id": 27},
                                     b"27"))
    def test_a_non_numeric_type_product_id_fails_closed(self, bad):
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_id=bad))

    @pytest.mark.parametrize("bad", (None, "", "abc", 27.5, True, [27]))
    def test_no_unusable_product_id_becomes_zero(self, bad):
        try:
            resp = DeltaOrderResponse.from_dict(_payload(product_id=bad))
        except UnknownInstrumentError:
            return
        assert resp.product_id != 0, f"{bad!r} silently became product 0"
        pytest.fail(f"{bad!r} parsed into product {resp.product_id} "
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
            inspect.getsource(DeltaOrderResponse.from_dict.__func__))
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


# ---------------------------------------------------------------------------
# The two identity fields must agree.
# ---------------------------------------------------------------------------
class TestAContradictoryPairFailsClosed:
    def test_the_previously_recorded_gap_is_closed(self):
        """
        `product_id=27` with `ETHUSD` is self-contradictory (27 is BTCUSD, and
        ETHUSD's verified id is 3136). It used to parse, because the
        orchestrator sourced ids from a product catalogue payload rather than
        the registry. That bypass is fixed and this now fails closed.
        """
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(
                _payload(product_symbol="ETHUSD", product_id=27))
        assert delta_india_registry().get("ETHUSD").product_id == 3136

    @pytest.mark.parametrize("symbol,product_id", NATIVE)
    def test_each_verified_pair_is_accepted(self, symbol, product_id):
        resp = DeltaOrderResponse.from_dict(
            _payload(product_symbol=symbol, product_id=product_id))
        assert (resp.product_symbol, resp.product_id) == (symbol, product_id)

    @pytest.mark.parametrize("symbol,wrong_id", (
        ("BTCUSD", 3136),
        ("ETHUSD", 27),
        ("SOLUSD", 14969),
        ("XRPUSD", 14823),
    ))
    def test_a_cross_paired_identity_fails_closed(self, symbol, wrong_id):
        """Each symbol paired with another verified symbol's id."""
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(
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
            assert DeltaOrderResponse.from_dict(payload).product_id == other_id
        else:
            with pytest.raises(UnknownInstrumentError):
                DeltaOrderResponse.from_dict(payload)

    @pytest.mark.parametrize("unrelated", (1, 26, 28, 3135, 3137, 14824,
                                           999999))
    def test_an_unrelated_product_id_fails_closed(self, unrelated):
        """Off-by-one and unknown ids are refused, not silently trusted."""
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(_payload(product_id=unrelated))

    def test_the_legacy_symbol_key_is_cross_checked_too(self):
        payload = _payload(product_id=27)
        del payload["product_symbol"]
        payload["symbol"] = "ETHUSD"
        with pytest.raises(UnknownInstrumentError):
            DeltaOrderResponse.from_dict(payload)

    def test_no_verified_product_id_is_hardcoded_in_the_parse(self):
        """
        Structural, on the AST so an explanatory comment cannot trip it: the
        cross-check compares against the registry spec, so no verified product
        id may appear as a literal in the module.
        """
        import ast
        import inspect
        from quantedge.execution import models as mod

        src = inspect.getsource(mod)
        verified = {pid for _symbol, pid in NATIVE}
        literals = {
            node.value
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
        }
        assert not (literals & verified), literals & verified
        assert "spec.product_id" in src


# ---------------------------------------------------------------------------
# Every other response field is parsed exactly as before.
# ---------------------------------------------------------------------------
class TestValidResponseFieldsAreUnchanged:
    def test_a_full_response_parses_identically(self):
        resp = DeltaOrderResponse.from_dict(_payload())
        assert resp.id == 910001
        assert resp.client_order_id == "qe-setup-1"
        assert resp.user_id == 5511
        assert resp.side == OrderSide.BUY
        assert resp.order_type == OrderType.LIMIT_ORDER
        assert resp.size == Decimal("3")
        assert resp.unfilled_size == Decimal("1")
        assert resp.filled_size == Decimal("2")
        assert resp.limit_price == Decimal("77000.0")
        assert resp.stop_price is None
        assert resp.average_fill_price == Decimal("77001.5")
        assert resp.state == OrderStatus.OPEN
        assert resp.status == OrderStatus.OPEN
        assert resp.reduce_only is False
        assert resp.created_at.year == 2025

    def test_optional_price_fields_still_default_to_none(self):
        resp = DeltaOrderResponse.from_dict(
            _payload(limit_price="", stop_price="", average_fill_price=None))
        assert resp.limit_price is None
        assert resp.stop_price is None
        assert resp.average_fill_price is None

    def test_the_avg_fill_price_alias_is_still_accepted(self):
        payload = _payload()
        del payload["average_fill_price"]
        payload["avg_fill_price"] = "76999.5"
        assert DeltaOrderResponse.from_dict(payload).average_fill_price == \
            Decimal("76999.5")

    def test_unfilled_size_still_falls_back_to_size(self):
        payload = _payload()
        del payload["unfilled_size"]
        resp = DeltaOrderResponse.from_dict(payload)
        assert resp.unfilled_size == Decimal("3")
        assert resp.filled_size == Decimal("0")

    @pytest.mark.parametrize("state,expected", (
        ("open", OrderStatus.OPEN),
        ("closed", OrderStatus.FILLED),
        ("cancelled", OrderStatus.CANCELLED),
    ))
    def test_order_state_semantics_are_unchanged(self, state, expected):
        assert DeltaOrderResponse.from_dict(
            _payload(state=state)).state == expected

    @pytest.mark.parametrize("created,year", (
        (1756339200000000, 2025),   # microseconds
        (1756339200000, 2025),      # milliseconds
        (1756339200, 2025),         # seconds
        ("2025-08-28T00:00:00Z", 2025),
    ))
    def test_every_timestamp_shape_still_parses(self, created, year):
        assert DeltaOrderResponse.from_dict(
            _payload(created_at=created)).created_at.year == year

    def test_a_reduce_only_bracket_response_parses(self):
        resp = DeltaOrderResponse.from_dict(
            _payload(reduce_only=True, order_type="stop_market_order",
                     side="sell", limit_price=None, stop_price="76000.0"))
        assert resp.reduce_only is True
        assert resp.order_type == OrderType.STOP_MARKET_ORDER
        assert resp.side == OrderSide.SELL
        assert resp.stop_price == Decimal("76000.0")
