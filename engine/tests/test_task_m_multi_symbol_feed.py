"""
Task M §M7 — the 1H feed must reach every configured pair.

Before Task M the subscription payload only ever carried BTCUSD, so a runtime
wired for four pairs advanced exactly one pair's strategy state: the other three
received no candles at all and could never produce a decision. Worse, every
frame was labelled `BTCUSD.P` regardless of which product sent it, so one pair's
prices could have been fed into another pair's strategy.

These tests pin the fix and its safety boundary:

  * the subscription carries every configured exchange symbol, spelled exactly
    as the provenance-verified registry spells it;
  * each candle is labelled with the symbol its own frame carried;
  * an unregistered symbol, a `.P` label the exchange never uses, and a frame
    for a pair this client did not subscribe to are all refused (no fuzzy
    matching, no case folding, no fallback to BTCUSD);
  * watermarks, dedup sets and persistence targets are partitioned per pair, so
    one pair can never mark a timestamp processed on another's behalf;
  * BTCUSD keeps the canonical 2026 partition it was measured on.

Strategy semantics are untouched: only CLOSED 1H candles reach the engine here,
exactly as before.
"""

import time
from decimal import Decimal

import pytest

from quantedge.instruments import delta_india_registry
from quantedge.market_data.delta_websocket import (
    CANONICAL_CSV,
    SUBSCRIPTION_CHANNEL,
    DeltaWebSocketClient,
    UnsupportedFeedSymbolError,
    _parse_candle_from_ws,
    canonical_paths,
    exchange_symbol,
    local_symbol,
)

ALL_SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")


def _closed_hour(hours_ago: int = 2) -> int:
    """The start of a 1H candle that is definitely closed."""
    now = int(time.time())
    return (now - (now % 3600)) - hours_ago * 3600


def _frame(symbol: str, candle_ts: int, close: float = 100.0) -> dict:
    """A `candlestick_1h` frame in the real Delta India flat shape."""
    return {
        "type": SUBSCRIPTION_CHANNEL,
        "symbol": symbol,
        "resolution": "1h",
        "open": close - 1,
        "high": close + 2,
        "low": close - 3,
        "close": close,
        "volume": 1234.0,
        "candle_start_time": candle_ts * 1_000_000,
        "timestamp": candle_ts * 1_000_000,
    }


class _RecordingSocket:
    """Captures what the client would have sent, and sends nothing."""

    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


@pytest.fixture
def four_pair_client(tmp_path):
    """A client configured for all four registered pairs, persistence off."""
    seen = []
    client = DeltaWebSocketClient(
        symbols=list(ALL_SYMBOLS),
        persist=False,
        on_candle_closed=seen.append,
        csv_path=tmp_path / "feed.csv",
        meta_path=tmp_path / "feed_metadata.json",
    )
    client.ws = _RecordingSocket()
    return client, seen


# ── 1. Subscription reaches every configured pair ─────────────────────────────


@pytest.mark.asyncio
async def test_the_subscription_carries_every_configured_pair(four_pair_client):
    """§M7: the defect itself. One channel entry, four symbols."""
    import json

    client, _ = four_pair_client
    await client.subscribe()

    assert len(client.ws.sent) == 1
    msg = json.loads(client.ws.sent[0])
    assert msg["type"] == "subscribe"
    channels = msg["payload"]["channels"]
    assert len(channels) == 1
    assert channels[0]["name"] == SUBSCRIPTION_CHANNEL
    assert channels[0]["symbols"] == list(ALL_SYMBOLS)


@pytest.mark.asyncio
async def test_the_subscription_spells_symbols_exactly_as_the_registry_does(
    four_pair_client,
):
    """Exchange identity is the registry's, not a local label."""
    import json

    client, _ = four_pair_client
    await client.subscribe()

    sent = json.loads(client.ws.sent[0])["payload"]["channels"][0]["symbols"]
    assert set(sent) == set(delta_india_registry().symbols)
    assert not any(s.endswith(".P") for s in sent)


@pytest.mark.asyncio
async def test_local_dot_p_configuration_still_subscribes_to_exchange_symbols(
    tmp_path,
):
    """A runtime may hold `.P` labels; only the suffix is removed, nothing else."""
    import json

    client = DeltaWebSocketClient(
        symbols=["BTCUSD.P", "ETHUSD.P"], persist=False,
        csv_path=tmp_path / "f.csv", meta_path=tmp_path / "f.json",
    )
    client.ws = _RecordingSocket()
    await client.subscribe()

    assert json.loads(client.ws.sent[0])["payload"]["channels"][0]["symbols"] == [
        "BTCUSD", "ETHUSD",
    ]
    assert client.symbols == ("BTCUSD.P", "ETHUSD.P")


@pytest.mark.asyncio
async def test_a_single_symbol_client_is_unchanged(tmp_path):
    """The pre-Task-M single-pair client must behave exactly as it did."""
    import json

    client = DeltaWebSocketClient(
        persist=False, csv_path=tmp_path / "f.csv", meta_path=tmp_path / "f.json",
    )
    client.ws = _RecordingSocket()
    await client.subscribe()

    assert json.loads(client.ws.sent[0])["payload"]["channels"][0]["symbols"] == [
        "BTCUSD",
    ]


def test_an_empty_pair_list_is_refused(tmp_path):
    """An empty subscription would silently starve every strategy."""
    with pytest.raises(UnsupportedFeedSymbolError):
        DeltaWebSocketClient(symbols=[], persist=False)


def test_an_unregistered_pair_cannot_be_configured(tmp_path):
    with pytest.raises(UnsupportedFeedSymbolError):
        DeltaWebSocketClient(symbols=["BTCUSD", "DOGEUSD"], persist=False)


# ── 2. Symbol identity: no fuzzy matching, no BTCUSD fallback ─────────────────


def test_symbol_resolution_is_exact():
    assert exchange_symbol("BTCUSD") == "BTCUSD"
    assert exchange_symbol("BTCUSD.P") == "BTCUSD"
    assert local_symbol("ETHUSD") == "ETHUSD.P"

    for bad in ("btcusd", " BTCUSD", "BTCUSD ", "BTC-USD", "DOGEUSD", "", None):
        with pytest.raises(UnsupportedFeedSymbolError):
            exchange_symbol(bad)


@pytest.mark.parametrize("symbol", ALL_SYMBOLS)
def test_each_pairs_frame_is_labelled_with_its_own_symbol(symbol):
    """The old code labelled every frame BTCUSD.P."""
    candle = _parse_candle_from_ws(_frame(symbol, _closed_hour()))

    assert candle is not None
    assert candle["exchange_symbol"] == symbol
    assert candle["symbol"] == f"{symbol}.P"


def test_a_frame_for_an_unsubscribed_pair_is_refused():
    """Cross-pair contamination: this client never asked for ETHUSD."""
    candle = _parse_candle_from_ws(
        _frame("ETHUSD", _closed_hour()), accepted={"BTCUSD"}
    )
    assert candle is None


def test_an_unregistered_frame_symbol_is_refused_not_defaulted():
    for bad in ("DOGEUSD", "btcusd", "BTC-USD"):
        assert _parse_candle_from_ws(_frame(bad, _closed_hour())) is None


def test_a_frame_without_a_symbol_is_refused():
    frame = _frame("BTCUSD", _closed_hour())
    frame.pop("symbol")
    assert _parse_candle_from_ws(frame) is None


# ── 3. Per-pair isolation of watermarks, dedup and persistence ────────────────


@pytest.mark.asyncio
async def test_all_four_pairs_receive_their_candles(four_pair_client):
    """§M18: all four symbols receive the 1H feed."""
    client, seen = four_pair_client
    ts = _closed_hour()

    for i, symbol in enumerate(ALL_SYMBOLS):
        await client._handle_message(_frame(symbol, ts, close=100.0 + i))

    assert [c["exchange_symbol"] for c in seen] == list(ALL_SYMBOLS)
    for symbol in ALL_SYMBOLS:
        assert client.last_closed_for(symbol) == ts
        assert client.processed_for(symbol) == {ts}


@pytest.mark.asyncio
async def test_one_pairs_candle_never_advances_another_pair(four_pair_client):
    """Per-symbol watermarks: BTCUSD moving must not move ETHUSD."""
    client, _ = four_pair_client
    ts = _closed_hour()

    await client._handle_message(_frame("BTCUSD", ts))

    assert client.last_closed_for("BTCUSD") == ts
    for other in ("ETHUSD", "SOLUSD", "XRPUSD"):
        assert client.last_closed_for(other) is None
        assert client.processed_for(other) == set()


@pytest.mark.asyncio
async def test_the_same_timestamp_is_accepted_once_per_pair(four_pair_client):
    """Dedup is per pair: four pairs share an hour boundary legitimately, but a
    replay of one pair's own candle is still refused."""
    client, seen = four_pair_client
    ts = _closed_hour()

    await client._handle_message(_frame("BTCUSD", ts))
    await client._handle_message(_frame("ETHUSD", ts))
    await client._handle_message(_frame("BTCUSD", ts))  # duplicate

    assert [c["exchange_symbol"] for c in seen] == ["BTCUSD", "ETHUSD"]


@pytest.mark.asyncio
async def test_a_forming_candle_never_reaches_the_strategy_on_any_pair(
    four_pair_client,
):
    """§M9: the strategy consumes CLOSED 1H candles only, on every pair."""
    client, seen = four_pair_client
    now = int(time.time())
    current_hour = now - (now % 3600)

    for symbol in ALL_SYMBOLS:
        await client._handle_message(_frame(symbol, current_hour))

    assert seen == []
    for symbol in ALL_SYMBOLS:
        assert client.last_closed_for(symbol) is None


@pytest.mark.asyncio
async def test_each_pair_persists_to_its_own_partition(four_pair_client):
    """One file can never hold two products."""
    client, _ = four_pair_client
    paths = {s: client.paths_for(s) for s in ALL_SYMBOLS}

    csvs = [p[0] for p in paths.values()]
    metas = [p[1] for p in paths.values()]
    assert len(set(csvs)) == 4
    assert len(set(metas)) == 4
    for symbol in ("ETHUSD", "SOLUSD", "XRPUSD"):
        assert symbol in paths[symbol][0].name


def test_btcusd_keeps_the_canonical_partition_byte_for_byte():
    """The frozen 4,641-trade baseline was measured on this exact dataset."""
    csv_path, _ = canonical_paths("BTCUSD")
    assert str(csv_path) == str(CANONICAL_CSV)

    eth_csv, _ = canonical_paths("ETHUSD")
    assert eth_csv != csv_path
    assert "ETHUSD" in eth_csv.parts


def test_the_primary_pair_keeps_the_legacy_attributes(tmp_path):
    """Legacy `processed_timestamps` / `last_closed_ts` still address the
    primary pair, by mutation and by rebinding."""
    client = DeltaWebSocketClient(
        symbols=list(ALL_SYMBOLS), persist=False,
        csv_path=tmp_path / "f.csv", meta_path=tmp_path / "f.json",
    )
    ts = _closed_hour()

    client.processed_timestamps.add(ts)
    assert client.processed_for("BTCUSD") == {ts}
    assert client.processed_for("ETHUSD") == set()

    client.processed_timestamps = {ts, ts - 3600}
    assert client.processed_for("BTCUSD") == {ts, ts - 3600}

    client.last_closed_ts = ts
    assert client.last_closed_for("BTCUSD") == ts
    assert client.last_closed_for("XRPUSD") is None


def test_watermark_lookups_fail_closed_on_an_unknown_pair(tmp_path):
    client = DeltaWebSocketClient(
        symbols=["BTCUSD"], persist=False,
        csv_path=tmp_path / "f.csv", meta_path=tmp_path / "f.json",
    )
    with pytest.raises(UnsupportedFeedSymbolError):
        client.processed_for("DOGEUSD")
    with pytest.raises(KeyError):
        client.paths_for("ETHUSD")  # registered, but not subscribed here


@pytest.mark.asyncio
async def test_the_candle_payload_still_carries_decimal_prices(four_pair_client):
    """Float round-tripping a price is exactly what the strategy must not see."""
    client, seen = four_pair_client
    await client._handle_message(_frame("SOLUSD", _closed_hour(), close=203.4567))

    candle = seen[0]
    assert isinstance(candle["close"], Decimal)
    assert candle["close"] == Decimal("203.4567")
