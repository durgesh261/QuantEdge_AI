"""
`DeltaPosition.from_dict` must never invent a product identity.

The parse used to read
`str(data.get("product_symbol", data.get("symbol", "BTCUSD"))).upper()`, so an
exchange payload that omitted the symbol came back as a BTCUSD position, and a
lower-case or padded symbol was folded into a registered one. Both are fabricated
provenance: downstream code (reconciliation, the single-trade lock, position
sizing) then believes the account holds an instrument the exchange never named.

The symbol is now resolved through the instrument registry, which is the single
source of verified Delta India symbols and performs an exact lookup. Anything it
cannot resolve raises `UnknownInstrumentError` at the parse boundary instead of
resolving to another product.

Zero network access: every payload here is a literal dict.
"""

from decimal import Decimal
import pytest

from quantedge.execution.models import DeltaPosition, PositionSide
from quantedge.instruments import UnknownInstrumentError, delta_india_registry

#: Verified native symbols, with their pinned product ids.
NATIVE = (("BTCUSD", 27), ("ETHUSD", 3136), ("SOLUSD", 14823),
          ("XRPUSD", 14969))


def _payload(**overrides) -> dict:
    """A well-formed margined-position payload; overrides drive each case."""
    payload = {
        "product_id": 27,
        "product_symbol": "BTCUSD",
        "size": "3",
        "entry_price": "77000.0",
        "mark_price": "77500.0",
        "liquidation_price": "70000.0",
        "unrealised_pnl": "1.50",
        "realised_pnl": "0.00",
        "leverage": "10",
        "margin": "23.10",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Every verified native symbol survives the parse byte for byte.
# ---------------------------------------------------------------------------
class TestVerifiedSymbolsArePreservedExactly:
    @pytest.mark.parametrize("symbol,product_id", NATIVE)
    def test_a_native_symbol_is_unchanged(self, symbol, product_id):
        pos = DeltaPosition.from_dict(
            _payload(product_symbol=symbol, product_id=product_id))
        assert pos.product_symbol == symbol
        assert pos.product_id == product_id

    def test_btcusd_stays_btcusd(self):
        assert DeltaPosition.from_dict(
            _payload(product_symbol="BTCUSD")).product_symbol == "BTCUSD"

    @pytest.mark.parametrize("symbol,product_id", NATIVE)
    def test_the_parsed_symbol_is_the_registered_one(self, symbol, product_id):
        """The value carried forward is the registry's own symbol object value."""
        pos = DeltaPosition.from_dict(
            _payload(product_symbol=symbol, product_id=product_id))
        assert pos.product_symbol == delta_india_registry().get(symbol).symbol

    def test_the_legacy_symbol_key_is_still_honoured(self):
        """
        Delta's `/v2/positions` fallback names the field `symbol`, and existing
        callers rely on that path. It must keep working.
        """
        payload = _payload(product_id=3136)
        del payload["product_symbol"]
        payload["symbol"] = "ETHUSD"
        assert DeltaPosition.from_dict(payload).product_symbol == "ETHUSD"


# ---------------------------------------------------------------------------
# Everything unusable fails closed at the parse boundary.
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

    @pytest.mark.parametrize("unknown", ("FOOUSD", "BTCUSDT", "BTC-USD",
                                        "DOGEUSD", "LTCUSD", "BTC"))
    def test_an_unknown_symbol_fails_closed(self, unknown):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=unknown))

    @pytest.mark.parametrize("suffixed", ("BTCUSD.P", "ETHUSD.P", "SOLUSD.P",
                                          "XRPUSD.P"))
    def test_a_display_suffix_symbol_fails_closed(self, suffixed):
        """`.P` is display/persistence only and is not a tradable alias."""
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=suffixed))

    @pytest.mark.parametrize("folded", ("btcusd", "BtcUsd", " BTCUSD ",
                                        "\tBTCUSD\n", "BTCUSD "))
    def test_a_case_or_whitespace_variant_is_not_folded_in(self, folded):
        """
        The parse used to `.strip().upper()` its way to a registered symbol.
        Inbound parsing now matches the gateway: exact or nothing.
        """
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=folded))

    @pytest.mark.parametrize("bad", (27, 0.001, True, b"BTCUSD", ("BTCUSD",),
                                     ["BTCUSD"], {"symbol": "BTCUSD"}))
    def test_a_non_string_symbol_fails_closed(self, bad):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict(_payload(product_symbol=bad))

    def test_an_empty_payload_fails_closed(self):
        with pytest.raises(UnknownInstrumentError):
            DeltaPosition.from_dict({})


# ---------------------------------------------------------------------------
# The specific regression: nothing becomes BTCUSD by accident.
# ---------------------------------------------------------------------------
class TestNothingSilentlyBecomesBtcusd:
    @pytest.mark.parametrize("bad", (None, "", "  ", "FOOUSD", "btcusd",
                                    "BTCUSD.P", "BTCUSDT", 27, ["BTCUSD"]))
    def test_no_unusable_symbol_yields_a_btcusd_position(self, bad):
        try:
            pos = DeltaPosition.from_dict(_payload(product_symbol=bad))
        except UnknownInstrumentError:
            return
        pytest.fail(f"{bad!r} parsed into {pos.product_symbol!r} "
                    f"instead of failing closed")

    def test_a_missing_symbol_yields_no_position_at_all(self):
        payload = _payload()
        del payload["product_symbol"]
        with pytest.raises(UnknownInstrumentError) as exc:
            DeltaPosition.from_dict(payload)
        assert "BTCUSD" not in str(exc.value).split("Registered:")[0]

    def test_the_parse_no_longer_contains_a_default_symbol(self):
        """Structural: no string literal symbol default survives in the parse."""
        import ast
        import inspect
        import textwrap

        src = textwrap.dedent(
            inspect.getsource(DeltaPosition.from_dict.__func__))
        literals = {
            node.value
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not any(s.endswith("USD") for s in literals), literals


# ---------------------------------------------------------------------------
# The rest of the payload is parsed exactly as before.
# ---------------------------------------------------------------------------
class TestValidPayloadBehaviourIsUnchanged:
    def test_a_long_position_parses_identically(self):
        pos = DeltaPosition.from_dict(_payload())
        assert pos.side == PositionSide.LONG
        assert pos.size == Decimal("3")
        assert pos.entry_price == Decimal("77000.0")
        assert pos.mark_price == Decimal("77500.0")
        assert pos.liquidation_price == Decimal("70000.0")
        assert pos.unrealized_pnl == Decimal("1.50")
        assert pos.realized_pnl == Decimal("0.00")
        assert pos.leverage == Decimal("10")
        assert pos.margin == Decimal("23.10")

    def test_a_short_position_still_reports_absolute_size(self):
        pos = DeltaPosition.from_dict(_payload(size="-4"))
        assert pos.side == PositionSide.SHORT
        assert pos.size == Decimal("4")

    def test_a_flat_position_still_parses(self):
        pos = DeltaPosition.from_dict(_payload(size="0"))
        assert pos.side == PositionSide.LONG
        assert pos.size == Decimal("0")

    def test_optional_fields_are_unobserved_not_zero(self):
        payload = _payload(liquidation_price="")
        for key in ("unrealised_pnl", "realised_pnl", "leverage", "margin"):
            del payload[key]
        pos = DeltaPosition.from_dict(payload)
        assert pos.liquidation_price is None
        # Task O §O3: strengthened, not weakened. This previously asserted
        # `Decimal("0")` for a realized PnL the payload never carried -- the
        # fabricated zero that made every unreported closure look like an
        # observed break-even. Absence is now `None`.
        assert pos.realized_pnl is None
        # Task O §O6: strengthened again. `unrealized_pnl`, `leverage` and
        # `margin` fabricated an observation too -- an unreported unrealized PnL
        # became an observed zero, an unreported margin became zero margin, and
        # an unreported leverage became 1x. The same rule now covers all of them:
        # absent is `None`, and an observed zero stays a distinct fact.
        assert pos.unrealized_pnl is None
        assert pos.leverage is None
        assert pos.margin is None
        assert pos.adl_level is None

    def test_an_observed_zero_is_still_an_observed_zero(self):
        """
        Task O §O6: the other half of the contract above. The refusal to
        fabricate must not turn a real zero into `None`, or reconciliation would
        lose an observation the exchange actually made.
        """
        pos = DeltaPosition.from_dict(
            _payload(unrealised_pnl="0", leverage="0", margin="0"))
        assert pos.unrealized_pnl == Decimal("0")
        assert pos.leverage == Decimal("0")
        assert pos.margin == Decimal("0")

    def test_the_american_spelling_of_pnl_is_still_accepted(self):
        payload = _payload(unrealized_pnl="9.99", realized_pnl="1.11")
        del payload["unrealised_pnl"]
        del payload["realised_pnl"]
        pos = DeltaPosition.from_dict(payload)
        assert pos.unrealized_pnl == Decimal("9.99")
        assert pos.realized_pnl == Decimal("1.11")
