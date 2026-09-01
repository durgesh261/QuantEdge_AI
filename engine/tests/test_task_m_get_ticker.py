"""
Task M §M8 — Path B `get_ticker` defect closure.

Before Task M, `MultiUserExecutionOrchestrator.execute_trade_for_user` (Path B)
called `client.get_ticker(symbol)` on a `DeltaIndiaClient` that had no such
method: every non-mocked invocation of that supposedly production path raised
`AttributeError` after the single-trade lock had already been acquired.

§M8 offered two options. Path B is exported from `quantedge.execution` and
pinned by seven audit suites, so it is not obsolete: Option A (implement the
method correctly against the real exchange API) is the decision.

These tests pin the endpoint contract that was verified against Delta Exchange
India's published REST reference -- `GET /v2/tickers/{symbol}`, public (no
authentication), `{"success": ..., "result": {single object}}`, `mark_price`
quoted as a string -- and pin the fail-closed validation. No live network
access: every response is served by an `httpx.MockTransport`.
"""

import httpx
import pytest
from decimal import Decimal

from quantedge.execution.delta_client import (
    DELTA_INDIA_PRODUCTION_URL,
    DeltaIndiaClient,
    DeltaResponseError,
)
from quantedge.instruments import UnknownInstrumentError, delta_india_registry


TEST_API_KEY = "test_delta_api_key_123456789"
TEST_API_SECRET = "test_delta_api_secret_987654321_abcdef"


def _ticker_result(symbol: str = "BTCUSD", **overrides):
    """A ticker payload shaped like the documented response.

    `mark_price`/`spot_price` are quoted strings; `close`/`open`/`high`/`low`/
    `volume`/`product_id` are unquoted numbers. Both shapes are reproduced here
    deliberately so the parser is exercised against the real mixture.
    """
    result = {
        "symbol": symbol,
        "product_id": 27,
        "mark_price": "104123.5",
        "spot_price": "104120.0",
        "close": 104125.0,
        "open": 103000.0,
        "high": 104900.0,
        "low": 102500.0,
        "volume": 1523.75,
        "timestamp": 1756512000000000,
    }
    result.update(overrides)
    return result


@pytest.fixture
def ticker_client_factory():
    """Build a `DeltaIndiaClient` whose transport is a recording mock."""
    created = []

    def _factory(handler):
        requests = []

        def _recording_handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        async_client = httpx.AsyncClient(
            transport=httpx.MockTransport(_recording_handler),
            base_url=DELTA_INDIA_PRODUCTION_URL,
        )
        client = DeltaIndiaClient(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            base_url=DELTA_INDIA_PRODUCTION_URL,
            http_client=async_client,
        )
        created.append(client)
        return client, requests

    return _factory


def _ok(payload):
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return _handler


def _explode(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    raise AssertionError(
        "get_ticker must not reach the network for an unregistered symbol"
    )


# ── 1. The defect itself ──────────────────────────────────────────────────────


def test_get_ticker_exists_on_the_real_client():
    """§M8/§M18: the real production client must not raise AttributeError."""
    import inspect

    assert hasattr(DeltaIndiaClient, "get_ticker")
    assert inspect.iscoroutinefunction(DeltaIndiaClient.get_ticker)


@pytest.mark.asyncio
async def test_get_ticker_hits_the_documented_public_endpoint(ticker_client_factory):
    """`GET /v2/tickers/{symbol}`, unauthenticated, single-symbol path param."""
    client, requests = ticker_client_factory(
        _ok({"success": True, "result": _ticker_result()})
    )
    result = await client.get_ticker("BTCUSD")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v2/tickers/BTCUSD"
    assert request.url.query == b""
    # Public route: no credential material is attached, so nothing can leak.
    assert "api-key" not in request.headers
    assert "signature" not in request.headers
    assert "timestamp" not in request.headers
    assert result["symbol"] == "BTCUSD"


@pytest.mark.asyncio
async def test_get_ticker_returns_the_quoted_mark_price_verbatim(ticker_client_factory):
    """The string must survive untouched: no float round-trip on a price."""
    client, _ = ticker_client_factory(
        _ok({"success": True, "result": _ticker_result(mark_price="104123.55555555")})
    )
    result = await client.get_ticker("BTCUSD")

    assert result["mark_price"] == "104123.55555555"
    assert Decimal(str(result["mark_price"])) == Decimal("104123.55555555")


@pytest.mark.asyncio
async def test_get_ticker_accepts_every_registered_symbol(ticker_client_factory):
    """All four supported pairs resolve; the path carries each pair's own id."""
    for symbol in delta_india_registry().symbols:
        client, requests = ticker_client_factory(
            _ok({"success": True, "result": _ticker_result(symbol=symbol)})
        )
        result = await client.get_ticker(symbol)
        assert requests[0].url.path == f"/v2/tickers/{symbol}"
        assert result["symbol"] == symbol


# ── 2. Symbol identity fails closed before any request ────────────────────────


@pytest.mark.asyncio
async def test_unregistered_symbol_never_reaches_the_exchange(ticker_client_factory):
    """Safety rule #15: an unknown product fails closed, it is not probed."""
    client, requests = ticker_client_factory(_explode)
    with pytest.raises(UnknownInstrumentError):
        await client.get_ticker("DOGEUSD")
    assert requests == []


@pytest.mark.asyncio
async def test_local_dot_p_symbol_fails_closed(ticker_client_factory):
    """`BTCUSD.P` is the local feed label, not an exchange product id."""
    client, requests = ticker_client_factory(_explode)
    with pytest.raises(UnknownInstrumentError):
        await client.get_ticker("BTCUSD.P")
    assert requests == []


@pytest.mark.asyncio
async def test_case_folded_symbol_fails_closed(ticker_client_factory):
    """No case-folding hack: `btcusd` is not silently promoted to BTCUSD."""
    client, requests = ticker_client_factory(_explode)
    with pytest.raises(UnknownInstrumentError):
        await client.get_ticker("btcusd")
    assert requests == []


# ── 3. Response validation fails closed ───────────────────────────────────────


@pytest.mark.asyncio
async def test_unsuccessful_envelope_raises(ticker_client_factory):
    client, _ = ticker_client_factory(
        _ok({"success": False, "error": {"code": "unavailable"}})
    )
    with pytest.raises(DeltaResponseError, match="unsuccessful"):
        await client.get_ticker("BTCUSD")


@pytest.mark.asyncio
async def test_missing_result_raises(ticker_client_factory):
    client, _ = ticker_client_factory(_ok({"success": True}))
    with pytest.raises(DeltaResponseError, match="single ticker object"):
        await client.get_ticker("BTCUSD")


@pytest.mark.asyncio
async def test_list_result_raises(ticker_client_factory):
    """The multi-symbol form of this endpoint returns a list; one symbol must
    not be silently priced from an array whose ordering we never verified."""
    client, _ = ticker_client_factory(
        _ok({"success": True, "result": [_ticker_result()]})
    )
    with pytest.raises(DeltaResponseError, match="single ticker object"):
        await client.get_ticker("BTCUSD")


@pytest.mark.asyncio
async def test_symbol_mismatch_raises(ticker_client_factory):
    """One product can never be priced from another product's ticker."""
    client, _ = ticker_client_factory(
        _ok({"success": True, "result": _ticker_result(symbol="ETHUSD")})
    )
    with pytest.raises(DeltaResponseError, match="identity mismatch"):
        await client.get_ticker("BTCUSD")


@pytest.mark.asyncio
async def test_absent_symbol_field_raises(ticker_client_factory):
    payload = _ticker_result()
    payload.pop("symbol")
    client, _ = ticker_client_factory(_ok({"success": True, "result": payload}))
    with pytest.raises(DeltaResponseError, match="identity mismatch"):
        await client.get_ticker("BTCUSD")


@pytest.mark.parametrize(
    "bad_mark_price, expected",
    [
        (None, "no mark_price"),
        ("", "no mark_price"),
        ("not-a-number", "unparseable mark_price"),
        ("0", "non-positive mark_price"),
        (0, "non-positive mark_price"),
        ("-104123.5", "non-positive mark_price"),
        ("NaN", "non-finite mark_price"),
        ("Infinity", "non-finite mark_price"),
    ],
)
@pytest.mark.asyncio
async def test_unusable_mark_price_raises(
    ticker_client_factory, bad_mark_price, expected
):
    """A mark price the caller would size real capital from must be usable or
    the call must fail -- never degrade to a plausible-looking number."""
    client, _ = ticker_client_factory(
        _ok({"success": True, "result": _ticker_result(mark_price=bad_mark_price)})
    )
    with pytest.raises(DeltaResponseError, match=expected):
        await client.get_ticker("BTCUSD")


@pytest.mark.asyncio
async def test_missing_mark_price_key_raises(ticker_client_factory):
    payload = _ticker_result()
    payload.pop("mark_price")
    client, _ = ticker_client_factory(_ok({"success": True, "result": payload}))
    with pytest.raises(DeltaResponseError, match="no mark_price"):
        await client.get_ticker("BTCUSD")


# ── 4. The Path B call site no longer defaults around the ticker ──────────────


def test_path_b_does_not_default_the_mark_price_to_the_planned_entry():
    """Safety rule #13: a default must never answer a safety question.

    The old call site was
    `Decimal(str(ticker.get("mark_price", planned_entry_price)))` -- a silent
    fall-back that would have sized real capital off the strategy's theoretical
    entry price whenever the exchange's answer was unusable. `get_ticker` now
    raises instead, so the fall-back is both dead and wrong; this pins its
    removal.
    """
    from pathlib import Path

    import quantedge.execution.multi_user_orchestrator as orchestrator_module

    source = Path(orchestrator_module.__file__).read_text(encoding="utf-8")
    assert 'ticker.get("mark_price", planned_entry_price)' not in source
    assert 'await client.get_ticker(symbol)' in source


def test_path_b_rejects_a_ticker_without_a_mark_price():
    """Even a collaborator that hands Path B a mark-price-less dict fails closed
    rather than sizing a position."""
    import inspect

    import quantedge.execution.multi_user_orchestrator as orchestrator_module

    source = inspect.getsource(
        orchestrator_module.UserExecutionSession.execute_trade
    )
    # The guard is the only path from an absent mark price to an outcome, and it
    # raises the module's own allocation error rather than continuing.
    assert 'raw_mark_price = ticker.get("mark_price")' in source
    assert "if raw_mark_price is None:" in source
    assert "CapitalAllocationError" in source
