"""
Private-stream event normalization must never invent a product symbol.

All three product-symbol paths in `EventValidator` used to read

    str(data.get("product_symbol") or data.get("symbol", "BTCUSD")).upper()

so a live order, position or fill frame that omitted its symbol became an event
on BTCUSD, and a lower-case or padded symbol was folded into a registered one.
That symbol is not cosmetic: `_apply_order_event` writes it to
`OrderRecord.symbol`, `_apply_position_event` uses it as the
`state_store.positions` key (and deletes that key when size reaches 0), and
`_apply_fill_event` puts it into the reconciliation payload. A fabricated BTCUSD
therefore opens, mutates or closes a BTCUSD position record from a frame the
exchange never attributed to BTCUSD.

Identity now resolves through `delta_india_registry()` -- the same exact,
fail-closed lookup used by `DeltaPosition.from_dict` and
`DeltaOrderResponse.from_dict`. `UnknownInstrumentError` propagates into the
`except Exception` block `parse_and_validate` already wraps every normalizer in,
so an unusable frame is counted malformed, quarantined and dropped, exactly like
the pre-existing `ValueError("Order event missing order_id")` path. No new
recovery policy is introduced.

Zero network access: every frame here is a literal dict.
"""

from decimal import Decimal
import json
import pytest

from quantedge.execution.models import OrderSide, OrderStatus, OrderType, PositionSide
from quantedge.execution.private_websocket import (
    DeltaFillEvent,
    DeltaOrderEvent,
    DeltaPositionEvent,
    EventValidator,
)
from quantedge.instruments import UnknownInstrumentError, delta_india_registry

#: Verified native symbols.
NATIVE = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")

#: Every raw symbol value that must fail closed at all three paths.
UNUSABLE = (
    None, "", " ", "   ", "\t", "\n", " \t\n ",
    "btcusd", "BtcUsd", " BTCUSD ", "\tBTCUSD\n", "BTCUSD ",
    "BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P",
    "BTC-USD", "BTC_USD", "BTC/USD", "BTC:USD", "BTC USD",
    "BTCUSDT", "FOOUSD", "DOGEUSD", "LTCUSD", "BTC", "USD",
    27, 0.001, True, b"BTCUSD", ("BTCUSD",), ["BTCUSD"], {"symbol": "BTCUSD"},
)


def _order(**overrides) -> dict:
    payload = {
        "id": 88001,
        "client_order_id": "qe-ws-1",
        "product_symbol": "BTCUSD",
        "side": "buy",
        "order_type": "limit_order",
        "size": "3",
        "unfilled_size": "1",
        "limit_price": "77000.0",
        "state": "open",
        "reduce_only": False,
    }
    payload.update(overrides)
    return payload


def _position(**overrides) -> dict:
    payload = {
        "product_symbol": "BTCUSD",
        "size": "3",
        "entry_price": "77000.0",
        "mark_price": "77500.0",
        "liquidation_price": "70000.0",
        "unrealised_pnl": "1.50",
        "realised_pnl": "0.00",
        "margin": "23.10",
        "leverage": "10",
    }
    payload.update(overrides)
    return payload


def _fill(**overrides) -> dict:
    payload = {
        "id": 55001,
        "order_id": "88001",
        "product_symbol": "BTCUSD",
        "side": "buy",
        "size": "3",
        "price": "77001.5",
        # Task O §O2: `commission` is Delta's documented fee field on a fill.
        # This fixture previously used `fee`, which the exchange never sends;
        # reading it kept the assertion below green while proving nothing.
        "commission": "0.05",
        "role": "taker",
    }
    payload.update(overrides)
    return payload


#: (builder, normalizer name, channel used by `parse_and_validate`)
PATHS = (
    (_order, "_normalize_order", "orders"),
    (_position, "_normalize_position", "positions"),
    (_fill, "_normalize_fill", "user_trades"),
)
PATH_IDS = ("order", "position", "fill")


def _normalize(builder, method: str, **overrides):
    return getattr(EventValidator(), method)(builder(**overrides))


# ---------------------------------------------------------------------------
# Every verified native symbol survives all three paths byte for byte.
# ---------------------------------------------------------------------------
class TestVerifiedSymbolsArePreservedExactly:
    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    @pytest.mark.parametrize("symbol", NATIVE)
    def test_a_native_symbol_is_unchanged(self, builder, method, _channel,
                                         symbol):
        assert _normalize(builder, method,
                          product_symbol=symbol).symbol == symbol

    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    @pytest.mark.parametrize("symbol", NATIVE)
    def test_the_parsed_symbol_is_the_registered_one(self, builder, method,
                                                    _channel, symbol):
        event = _normalize(builder, method, product_symbol=symbol)
        assert event.symbol == delta_india_registry().get(symbol).symbol

    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    def test_the_legacy_symbol_key_is_still_honoured(self, builder, method,
                                                    _channel):
        """Delta's fallback shape names the field `symbol`."""
        payload = builder()
        del payload["product_symbol"]
        payload["symbol"] = "ETHUSD"
        assert getattr(EventValidator(), method)(payload).symbol == "ETHUSD"

    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    def test_product_symbol_wins_over_the_legacy_key(self, builder, method,
                                                     _channel):
        event = _normalize(builder, method, product_symbol="BTCUSD",
                           symbol="ETHUSD")
        assert event.symbol == "BTCUSD"


# ---------------------------------------------------------------------------
# Everything unusable fails closed at all three paths.
# ---------------------------------------------------------------------------
class TestAnUnusableSymbolFailsClosed:
    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    def test_a_missing_symbol_fails_closed(self, builder, method, _channel):
        payload = builder()
        del payload["product_symbol"]
        with pytest.raises(UnknownInstrumentError):
            getattr(EventValidator(), method)(payload)

    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    @pytest.mark.parametrize("bad", UNUSABLE)
    def test_every_unusable_symbol_fails_closed(self, builder, method,
                                                _channel, bad):
        with pytest.raises(UnknownInstrumentError):
            _normalize(builder, method, product_symbol=bad)

    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    @pytest.mark.parametrize("suffixed", ("BTCUSD.P", "ETHUSD.P", "SOLUSD.P",
                                          "XRPUSD.P"))
    def test_a_display_suffix_symbol_fails_closed(self, builder, method,
                                                  _channel, suffixed):
        """`.P` is display/persistence only and is not a tradable alias."""
        with pytest.raises(UnknownInstrumentError):
            _normalize(builder, method, product_symbol=suffixed)

    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    def test_an_empty_payload_fails_closed(self, builder, method, _channel):
        """
        No event is produced. `_normalize_order` reaches its pre-existing
        `ValueError("Order event missing order_id")` guard before the symbol
        lookup; that ordering is unchanged by this task and both errors are
        quarantined identically by `parse_and_validate`.
        """
        with pytest.raises((UnknownInstrumentError, ValueError)):
            getattr(EventValidator(), method)({})

    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    def test_a_legacy_key_variant_is_not_folded_in(self, builder, method,
                                                   _channel):
        payload = builder()
        del payload["product_symbol"]
        payload["symbol"] = "btcusd"
        with pytest.raises(UnknownInstrumentError):
            getattr(EventValidator(), method)(payload)


# ---------------------------------------------------------------------------
# The specific regression: no event is ever attributed to BTCUSD by accident.
# ---------------------------------------------------------------------------
class TestNothingSilentlyBecomesBtcusd:
    @pytest.mark.parametrize("builder,method,_channel", PATHS, ids=PATH_IDS)
    @pytest.mark.parametrize("bad", UNUSABLE)
    def test_no_unusable_symbol_yields_an_event_at_all(self, builder, method,
                                                       _channel, bad):
        try:
            event = _normalize(builder, method, product_symbol=bad)
        except UnknownInstrumentError:
            return
        pytest.fail(f"{bad!r} produced an event on {event.symbol!r} "
                    f"instead of failing closed")

    @pytest.mark.parametrize("builder,method,channel", PATHS, ids=PATH_IDS)
    @pytest.mark.parametrize("bad", (None, "", "  ", "btcusd", "BTCUSD.P",
                                     "BTC-USD", "BTCUSDT", "FOOUSD", 27,
                                     ["BTCUSD"]))
    def test_the_frame_is_quarantined_not_fabricated(self, builder, method,
                                                     channel, bad):
        """
        End to end through `parse_and_validate`: the existing failure semantics
        (count malformed, quarantine, return None) apply, and no event object
        exists to reach `apply_event`.
        """
        validator = EventValidator()
        frame = json.dumps({"channel": channel,
                            "payload": builder(product_symbol=bad)},
                           default=str)
        assert validator.parse_and_validate(frame) is None
        assert validator.malformed_events_count == 1
        assert validator.valid_events_count == 0
        assert len(validator.quarantined_events) == 1

    @pytest.mark.parametrize("builder,method,channel", PATHS, ids=PATH_IDS)
    def test_a_valid_frame_still_produces_an_event(self, builder, method,
                                                   channel):
        validator = EventValidator()
        frame = json.dumps({"channel": channel,
                            "payload": builder(product_symbol="ETHUSD")})
        event = validator.parse_and_validate(frame)
        assert event is not None
        assert event.symbol == "ETHUSD"
        assert validator.valid_events_count == 1
        assert validator.malformed_events_count == 0
        assert validator.quarantined_events == []

    def test_a_missing_symbol_never_reaches_the_position_store(self):
        """
        The sharpest consequence of the old default: a symbol-less position
        frame keyed `state_store.positions["BTCUSD"]`.
        """
        payload = _position(size="0")
        del payload["product_symbol"]
        with pytest.raises(UnknownInstrumentError) as exc:
            EventValidator()._normalize_position(payload)
        assert "BTCUSD" not in str(exc.value).split("Registered:")[0]


# ---------------------------------------------------------------------------
# Structural: the old fabrication and normalization are gone for good.
# ---------------------------------------------------------------------------
class TestTheOldFabricationIsStructurallyGone:
    def _symbol_functions(self):
        """The three product-symbol normalizers, as dedented source."""
        import inspect
        import textwrap

        return {
            name: textwrap.dedent(inspect.getsource(getattr(EventValidator,
                                                            name)))
            for name in ("_normalize_order", "_normalize_position",
                         "_normalize_fill")
        }

    @pytest.mark.parametrize("name", ("_normalize_order",
                                      "_normalize_position",
                                      "_normalize_fill"))
    def test_no_btcusd_fallback_remains(self, name):
        """AST, so the explanatory comment naming the old default cannot trip it."""
        import ast

        src = self._symbol_functions()[name]
        literals = {
            node.value
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not any(s.endswith("USD") for s in literals), literals

    @pytest.mark.parametrize("name", ("_normalize_order",
                                      "_normalize_position",
                                      "_normalize_fill"))
    def test_no_symbol_normalizer_remains(self, name):
        """
        No `.upper()`, `.strip()`, `.lower()` or `.replace()` is applied to the
        symbol expression. `role` in `_normalize_fill` legitimately keeps its
        own `.lower()`, so the scan is restricted to the symbol assignment.
        """
        import ast

        src = self._symbol_functions()[name]
        tree = ast.parse(src)
        symbol_exprs = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in ("symbol",
                                                         "raw_symbol")
                    for t in node.targets)
        ]
        assert symbol_exprs, name
        for expr in symbol_exprs:
            for node in ast.walk(expr):
                if isinstance(node, ast.Call) and isinstance(node.func,
                                                             ast.Attribute):
                    assert node.func.attr not in ("upper", "lower", "strip",
                                                  "replace"), ast.dump(node)

    @pytest.mark.parametrize("name", ("_normalize_order",
                                      "_normalize_position",
                                      "_normalize_fill"))
    def test_the_registry_is_the_symbol_source(self, name):
        import ast

        src = self._symbol_functions()[name]
        called = {
            node.func.id for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "delta_india_registry" in called
        assert ".symbol" in src

    def test_no_product_identity_is_hardcoded_in_the_module(self):
        """No verified symbol or product id literal anywhere in the module."""
        import ast
        import inspect
        from quantedge.execution import private_websocket as mod

        tree = ast.parse(inspect.getsource(mod))
        strings, ints = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                if isinstance(node.value, str):
                    strings.add(node.value)
                elif isinstance(node.value, int) and not isinstance(node.value,
                                                                   bool):
                    ints.add(node.value)
        assert not (set(NATIVE) & strings), set(NATIVE) & strings
        verified_ids = {delta_india_registry().get(s).product_id
                        for s in NATIVE}
        assert not (verified_ids & ints), verified_ids & ints

    def test_all_three_paths_use_the_identical_expression(self):
        """One lookup shape, so a future edit cannot diverge silently."""
        for name, src in self._symbol_functions().items():
            assert 'data.get("product_symbol", data.get("symbol"))' in src, name
            assert "delta_india_registry().get(raw_symbol).symbol" in src, name


# ---------------------------------------------------------------------------
# Every unrelated field of every event is parsed exactly as before.
# ---------------------------------------------------------------------------
class TestUnrelatedBehaviourIsUnchanged:
    def test_the_order_event_fields_are_unchanged(self):
        event = _normalize(_order, "_normalize_order")
        assert isinstance(event, DeltaOrderEvent)
        assert event.order_id == "88001"
        assert event.client_order_id == "qe-ws-1"
        assert event.side == OrderSide.BUY
        assert event.order_type == OrderType.LIMIT_ORDER
        assert event.quantity == Decimal("3")
        assert event.unfilled_quantity == Decimal("1")
        assert event.filled_quantity == Decimal("2")
        assert event.status == OrderStatus.OPEN
        assert event.price == Decimal("77000.0")
        assert event.stop_price is None
        assert event.average_fill_price is None
        assert event.reduce_only is False
        assert event.cancellation_reason is None
        assert event.timestamp.tzinfo is not None

    def test_the_order_id_guard_still_precedes_nothing_else(self):
        payload = _order()
        del payload["id"]
        with pytest.raises(ValueError):
            EventValidator()._normalize_order(payload)

    def test_a_reduce_only_bracket_order_event_is_unchanged(self):
        event = _normalize(_order, "_normalize_order", reduce_only=True,
                           order_type="stop_market_order", side="sell",
                           limit_price=None, stop_price="76000.0",
                           state="cancelled",
                           cancellation_reason="user_cancelled")
        assert event.reduce_only is True
        assert event.order_type == OrderType.STOP_MARKET_ORDER
        assert event.side == OrderSide.SELL
        assert event.price is None
        assert event.stop_price == Decimal("76000.0")
        assert event.status == OrderStatus.CANCELLED
        assert event.cancellation_reason == "user_cancelled"

    def test_the_unfilled_size_fallback_is_unchanged(self):
        payload = _order()
        del payload["unfilled_size"]
        event = EventValidator()._normalize_order(payload)
        assert event.unfilled_quantity == Decimal("3")
        assert event.filled_quantity == Decimal("0")

    @pytest.mark.parametrize("state,expected", (
        ("open", OrderStatus.OPEN),
        ("closed", OrderStatus.FILLED),
        ("cancelled", OrderStatus.CANCELLED),
        ("partially_filled", OrderStatus.PARTIALLY_FILLED),
    ))
    def test_order_state_semantics_are_unchanged(self, state, expected):
        assert _normalize(_order, "_normalize_order",
                          state=state).status == expected

    def test_the_position_event_fields_are_unchanged(self):
        event = _normalize(_position, "_normalize_position")
        assert isinstance(event, DeltaPositionEvent)
        assert event.side == PositionSide.LONG
        assert event.size == Decimal("3")
        assert event.entry_price == Decimal("77000.0")
        assert event.mark_price == Decimal("77500.0")
        assert event.liquidation_price == Decimal("70000.0")
        assert event.unrealized_pnl == Decimal("1.50")
        assert event.realized_pnl == Decimal("0.00")
        assert event.margin == Decimal("23.10")
        assert event.leverage == Decimal("10")

    def test_a_short_position_event_still_reports_absolute_size(self):
        event = _normalize(_position, "_normalize_position", size="-4")
        assert event.side == PositionSide.SHORT
        assert event.size == Decimal("4")

    def test_the_american_pnl_spelling_is_still_accepted(self):
        payload = _position(unrealized_pnl="9.99", realized_pnl="1.11")
        del payload["unrealised_pnl"]
        del payload["realised_pnl"]
        event = EventValidator()._normalize_position(payload)
        assert event.unrealized_pnl == Decimal("9.99")
        assert event.realized_pnl == Decimal("1.11")

    def test_position_optional_numerics_are_unobserved_not_fabricated(self):
        """
        Amended by Task O §O6 C7 (was `test_position_optional_fields_still_default`).

        Every assertion below that previously pinned a number pinned a
        fabrication instead of an observation: an absent `mark_price` became the
        entry price, so an un-marked position reported zero unrealized drift at
        exactly break-even; an absent `unrealized_pnl` and `margin` became an
        observed zero; an absent `leverage` became an observed 1x. §O3 had
        already removed the same defect from `realized_pnl` alone -- this
        extends that one rule to the remaining four. Absence is `None`.

        The assertions are strengthened, not relaxed: `None` is a strictly
        narrower claim than a number the payload never carried, and the
        companion test below proves supplied values are still exact.
        """
        payload = _position(liquidation_price="")
        for key in ("unrealised_pnl", "realised_pnl", "margin", "leverage",
                    "mark_price"):
            del payload[key]
        event = EventValidator()._normalize_position(payload)
        assert event.liquidation_price is None
        # No fallback to `entry_price`: an un-marked position is un-marked.
        assert event.mark_price is None
        assert event.unrealized_pnl is None
        # Task O §O3: an absent realized PnL is unobserved, not break-even. The
        # previous `== Decimal("0")` here encoded the defect that let a real
        # closure book at exactly zero P&L.
        assert event.realized_pnl is None
        assert event.margin is None
        assert event.leverage is None
        # Unrelated, unchanged: identity and size still resolve normally.
        assert event.symbol == "BTCUSD"
        assert event.entry_price == Decimal("77000.0")
        assert event.size == Decimal("3")

    def test_position_optional_numerics_that_are_supplied_stay_exact(self):
        """
        The other half of §O6 C7. Refusing to fabricate must not blur a value the
        stream actually sent, including an explicitly observed zero, which is a
        real fact and must not collapse to `None`.
        """
        event = EventValidator()._normalize_position(_position(
            mark_price="77500.00000001", unrealised_pnl="0", margin="0",
            leverage="0", liquidation_price="70000.5"))
        assert str(event.mark_price) == "77500.00000001"
        assert event.unrealized_pnl == Decimal("0")
        assert event.margin == Decimal("0")
        assert event.leverage == Decimal("0")
        assert event.liquidation_price == Decimal("70000.5")

    def test_the_fill_event_fields_are_unchanged(self):
        event = _normalize(_fill, "_normalize_fill")
        assert isinstance(event, DeltaFillEvent)
        assert event.trade_id == "55001"
        assert event.order_id == "88001"
        assert event.side == OrderSide.BUY
        assert event.size == Decimal("3")
        assert event.price == Decimal("77001.5")
        assert event.fee == Decimal("0.05")
        assert event.role == "taker"

    @pytest.mark.parametrize("key", ("trade_id", "fill_id"))
    def test_the_fill_id_aliases_are_unchanged(self, key):
        payload = _fill()
        del payload["id"]
        payload[key] = 55002
        assert EventValidator()._normalize_fill(payload).trade_id == "55002"

    def test_the_maker_role_is_unchanged(self):
        assert _normalize(_fill, "_normalize_fill", role="MAKER").role == \
            "maker"

    def test_margin_event_routing_is_untouched(self):
        """
        `_normalize_margin` carries an asset symbol, not a product symbol, and
        is deliberately outside this change.
        """
        validator = EventValidator()
        event = validator.parse_and_validate(json.dumps({
            "channel": "margins",
            "payload": {"asset_symbol": "USDT", "balance": "1000.00",
                        "available_balance": "900.00",
                        "position_margin": "100.00", "order_margin": "0"}}))
        assert event.asset_symbol == "USDT"
        assert event.balance == Decimal("1000.00")
        assert validator.valid_events_count == 1

    @pytest.mark.parametrize("frame,counter", (
        ("not json at all", "malformed_events_count"),
        ("[1, 2, 3]", "malformed_events_count"),
    ))
    def test_frame_level_error_handling_is_untouched(self, frame, counter):
        validator = EventValidator()
        assert validator.parse_and_validate(frame) is None
        assert getattr(validator, counter) == 1

    @pytest.mark.parametrize("msg_type", ("pong", "ping", "subscriptions",
                                          "key-auth"))
    def test_system_frames_are_still_passed_through(self, msg_type):
        validator = EventValidator()
        assert validator.parse_and_validate(
            json.dumps({"type": msg_type})) == {"type": msg_type}

    def test_an_unknown_channel_is_still_counted_unknown(self):
        validator = EventValidator()
        assert validator.parse_and_validate(
            json.dumps({"channel": "candlesticks", "payload": {}})) is None
        assert validator.unknown_events_count == 1
        assert validator.malformed_events_count == 0

