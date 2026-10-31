"""
The compounding backtest runner — behavioural pins.
===================================================

Scope. This file tests `engine/scripts/run_manual_smc_compounding_backtests.py`
and NOTHING about the strategy itself. The strategy's own rules (first touch,
25% entry, 0.60% TP, opposite-edge SL, the 3-candle window, overlap handling)
are pinned by the pre-existing Manual SMC suites and are not re-litigated here.
What is pinned here is what the runner ADDS on top of the driver:

    * the compounding chain is a real chain, and its arithmetic is exact;
    * each pair is its own $10 account, and no pair can see another's trades;
    * the two periods are two independent runs — the recent one does NOT
      inherit the historical one's balance;
    * the runner's integrity gates actually RAISE when their property breaks,
      because a check that cannot fail proves nothing;
    * NO reward:risk rule exists on this path. R:R is computed and reported as a
      diagnostic and can never refuse a setup, so a sub-1.5 setup FILLS. The
      class that used to pin a 1.5 admission threshold here has been removed
      along with the rule, and is replaced by pins in the opposite direction:
      the ratio appears in the ledger, the summary and the report as an
      observation, and `verify_no_risk_reward_refusals` refuses to publish a run
      in which anything was refused for its ratio.

Loading. The runner is a script, not a package module, so it is loaded through
`importlib.util.spec_from_file_location`. Every other test in this repository
references scripts by path or AST only; importing one is new, and it is done
here because the runner's verification helpers are the thing under test.

Fixtures. Every number asserted below was MEASURED from the real driver first
and then written down. Nothing is a hand-computed expectation, except where a
comment shows the arithmetic explicitly as a cross-check of the driver's own
result. No network, no credentials, no exchange: the runner reads local CSVs
and these tests do not even do that — they feed synthetic candles.

Detector. Every fixture here is a six-candle hand-built pattern whose numbers
were measured under `DETECTOR_MANUAL_SPEC` — the half-candle zone geometry
(`[low, close]` for a bullish origin bar) that the entry, SL and whole
compounding chain below are arithmetic consequences of. The SHIPPED default is
now `DETECTOR_LUXALGO_INTERNAL`, which needs an ATR(200) warm-up and a 50-bar
swing window before it will admit anything at all and would return zero trades
on a six-bar pattern. So this file NAMES the research detector at its driver
call sites and, where it drives the runner, passes it through the runner's own
`detector` parameter. That pin fixes each fixture to the algorithm it was drawn
for; it asserts nothing about what production defaults to. The default itself is
pinned in the opposite direction by `TestTheRunnerDefaultsToTheShippedDetector`
below and by `test_manual_smc_luxalgo_parity.py`.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import inspect
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quantedge.strategy.manual_smc.backtest import (
    BacktestResult,
    BacktestTrade,
    EntryBlock,
    candles_from_ohlc,
    run_manual_smc_backtest_from_candles,
)
from quantedge.strategy.manual_smc.lifecycle import (
    DETECTOR_LUXALGO_INTERNAL,
    DETECTOR_MANUAL_SPEC,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_FIXED_TP_PCT,
    ManualSpecConfig,
    manual_smc_production_config,
)

# ---------------------------------------------------------------------------
# Load the script under test
# ---------------------------------------------------------------------------
RUNNER_PATH = (Path(__file__).resolve().parents[1] / "scripts"
               / "run_manual_smc_compounding_backtests.py")


def _load_runner():
    """Import the script by path. It is not importable as a package module."""
    assert RUNNER_PATH.is_file(), f"runner script missing at {RUNNER_PATH}"
    spec = importlib.util.spec_from_file_location(
        "manual_smc_compounding_runner_under_test", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()


def _runner_source() -> str:
    return RUNNER_PATH.read_text(encoding="utf-8")


def _imported_modules() -> set:
    """Every module the runner actually imports, read from its syntax tree."""
    modules = set()
    for node in ast.walk(ast.parse(_runner_source())):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _assigned_names() -> set:
    """
    Every name the runner ASSIGNS TO, anywhere, at any nesting depth.

    Reads are excluded by construction, which is what makes this usable as a
    "the harness computes nothing of its own" check: a transcription harness
    reads strategy quantities constantly and must never write them.
    """
    names = set()
    for node in ast.walk(ast.parse(_runner_source())):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            for inner in ast.walk(target):
                if isinstance(inner, ast.Name):
                    names.add(inner.id)
                elif isinstance(inner, ast.Attribute):
                    names.add(inner.attr)
    return names


# ---------------------------------------------------------------------------
# Synthetic fixtures, measured against the real driver
# ---------------------------------------------------------------------------
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ts(i: int) -> datetime:
    return BASE + timedelta(hours=i)


#: One complete SHORT cycle under the PRODUCTION default mode
#: (FIRST_TOUCH_WINDOW, 3 candles). Bar 0 is the bullish origin candle, so
#: ob_top = close = 105.0 and ob_bottom = low = 99.0 (width 6.0); bar 1 sweeps
#: below ob_bottom and confirms displacement; bar 2's high of 101.0 reaches the
#: 25% entry at 99.0 + 0.25*6.0 = 100.5 and fills; bar 3's low of 98.0 reaches
#: the 0.60% TP at 99.897. Bars 4-5 return price to the start so the pattern is
#: repeatable on a single continuous clock.
WIN_PATTERN = (
    (100.0, 106.0, 99.0, 105.0),
    (104.0, 104.5, 97.0, 98.0),
    (98.0, 101.0, 97.5, 100.0),
    (100.0, 100.2, 98.0, 98.5),
    (99.6, 101.0, 99.5, 100.0),
    (100.0, 100.5, 99.5, 99.8),
)

#: The same setup, resolved the other way: bar 3 trades up through the SL at
#: 105.0 (the OB's distal edge) without ever reaching 99.897.
LOSS_PATTERN = (
    (100.0, 106.0, 99.0, 105.0),
    (104.0, 104.5, 97.0, 98.0),
    (98.0, 101.0, 97.5, 100.0),
    (100.0, 105.5, 100.0, 105.2),
    (105.2, 105.4, 105.0, 105.1),
    (105.1, 105.3, 105.0, 105.2),
)

#: MEASURED chain for WIN_PATTERN repeated. Each value is the driver's own
#: `ending_capital`, transcribed at full float precision.
WIN_CHAIN = (10.0,
             10.406466666666667,
             10.829454848444444,
             11.269636089850882,
             11.727709231449689)
#: MEASURED single-trade outcome for LOSS_PATTERN.
LOSS_END = 6.4374666666666664

#: The same SHORT cycle over a NARROW order block, so its reward:risk lands well
#: ABOVE 1.5 instead of well below it. Nothing admits or refuses either fixture
#: on that number — both trade — which is precisely what the pair of them is for:
#: `WIN_PATTERN` at R:R 0.134 and this one at R:R 1.998 must behave identically
#: with respect to admission.
#:
#: The geometry is closed-form: the entry sits 25% into the zone and the stop on
#: the far edge, so risk = 0.75 * width; the target is a flat 0.60% of entry, so
#: reward = 0.006 * entry. Therefore
#:
#:     R:R = 0.008 * entry / width
#:
#: and a setup lands above 1.5 exactly when width <= entry / 187.5. `WIN_PATTERN`'s
#: block is 6.0 wide at an entry near 100 (R:R 0.134); this one is 0.4 wide at an
#: entry of 99.9, giving 0.008 * 99.9 / 0.4 = 1.998. MEASURED from the driver:
#: entry 99.9, SL 100.2, TP 99.3006, R:R 1.9980000000000284.
NARROW_OB_PATTERN = (
    (99.9, 100.60, 99.80, 100.20),
    (100.1, 100.15, 99.40, 99.50),
    (99.5, 100.00, 99.45, 99.70),
    (99.7, 99.75, 99.20, 99.25),
    (99.4, 100.70, 99.30, 99.90),
    (99.9, 100.00, 99.85, 99.90),
)


def _candles(pattern, reps: int = 1, first_bar: int = 0):
    """`pattern` repeated `reps` times on the shared hourly clock."""
    rows = []
    for _ in range(reps):
        for o, h, l, c in pattern:
            rows.append((first_bar + len(rows), o, h, l, c))
    return candles_from_ohlc(rows, _ts)


def _scaled(pattern, factor: float):
    """The same shape at a different price level — percentages are invariant."""
    return tuple(tuple(v * factor for v in row) for row in pattern)


#: The detector the six-candle fixtures above were measured under. See the
#: module docstring: it is the half-candle geometry every number in this file is
#: derived from, and it is named at the call site rather than inherited so that
#: the fixtures cannot silently follow a change to the library default.
FIXTURE_DETECTOR = DETECTOR_MANUAL_SPEC


def _drive(candles, symbols, **kw) -> BacktestResult:
    """
    The real driver, with the fixture detector named and nothing else changed.

    Every other keyword — including `start_date`/`end_date` and
    `starting_capital` — is passed through untouched, so what is under test is
    still the shipped entry point.
    """
    kw.setdefault("detector", FIXTURE_DETECTOR)
    return run_manual_smc_backtest_from_candles(candles, symbols=symbols, **kw)


def _run(symbol: str, pattern=WIN_PATTERN, reps: int = 3,
         **kw) -> BacktestResult:
    """One pair, one account, through the EXISTING driver the runner uses."""
    return _drive({symbol: _candles(pattern, reps)}, symbols=[symbol], **kw)


def _period(name, start, end, dataset, out, **kw):
    """
    `RUNNER.run_period`, driven on the fixture detector.

    The runner's own default is the shipped production detector and is NOT
    changed here — it is overridden per call, through the runner's own public
    parameter, because the four-pair datasets these tests feed it are the same
    six-candle patterns measured above.
    """
    kw.setdefault("detector", FIXTURE_DETECTOR)
    return RUNNER.run_period(name, start, end, dataset, out, **kw)


@pytest.fixture(scope="module")
def three_wins() -> BacktestResult:
    return _run("BTCUSD", WIN_PATTERN, reps=3)


@pytest.fixture(scope="module")
def one_loss() -> BacktestResult:
    return _run("ETHUSD", LOSS_PATTERN, reps=1)


def _splice(result: BacktestResult, index: int, **changes) -> BacktestResult:
    """A result with one trade field altered. Both dataclasses are frozen."""
    trades = list(result.trades)
    trades[index] = dataclasses.replace(trades[index], **changes)
    return dataclasses.replace(result, trades=tuple(trades))


# ---------------------------------------------------------------------------
class TestTheCompoundingArithmetic:
    """
    Requirement B: after every completed trade the resulting balance becomes
    the next trade's starting balance — no reset to $10, no fixed dollar size.
    """

    def test_the_first_trade_opens_at_exactly_ten_dollars(self, three_wins):
        assert three_wins.starting_capital == 10.0
        assert three_wins.trades[0].starting_capital == 10.0

    def test_each_trade_opens_where_the_previous_one_closed(self, three_wins):
        for earlier, later in zip(three_wins.trades, three_wins.trades[1:]):
            assert later.starting_capital == earlier.ending_capital

    def test_no_trade_is_reset_to_the_starting_capital(self, three_wins):
        assert [t.starting_capital for t in three_wins.trades] != [10.0] * 3

    def test_the_measured_chain_is_reproduced_exactly(self, three_wins):
        assert [t.starting_capital for t in three_wins.trades] == \
            list(WIN_CHAIN[:3])
        assert [t.ending_capital for t in three_wins.trades] == \
            list(WIN_CHAIN[1:4])
        assert three_wins.ending_capital == WIN_CHAIN[3]

    def test_the_chain_is_deterministic_across_repeated_runs(self):
        first = _run("BTCUSD", WIN_PATTERN, reps=3)
        second = _run("BTCUSD", WIN_PATTERN, reps=3)
        assert [t.ending_capital for t in first.trades] == \
            [t.ending_capital for t in second.trades]
        assert first.ending_capital == second.ending_capital

    def test_a_longer_run_extends_the_same_chain_rather_than_changing_it(self):
        """
        Compounding must be path-dependent only on the trades BEFORE it, so
        appending a fourth cycle may not disturb the first three balances.
        """
        four = _run("BTCUSD", WIN_PATTERN, reps=4)
        assert [t.ending_capital for t in four.trades] == list(WIN_CHAIN[1:])

    def test_position_size_is_the_whole_balance_at_the_applied_leverage(
            self, three_wins):
        """
        Requirement B asks how $10 becomes a trade size. It is the WHOLE
        balance as margin, multiplied by the applied leverage.
        """
        for t in three_wins.trades:
            assert t.position_notional == pytest.approx(
                t.starting_capital * t.applied_leverage, rel=1e-12)

    def test_the_notional_grows_with_the_balance(self, three_wins):
        sizes = [t.position_notional for t in three_wins.trades]
        assert sizes == sorted(sizes) and sizes[0] < sizes[-1]

    def test_the_fee_is_the_notional_times_the_config_fee_rate(
            self, three_wins):
        rate = three_wins.config.fee_rate
        for t in three_wins.trades:
            assert t.fees_usd == pytest.approx(
                t.position_notional * rate, rel=1e-12)

    def test_net_pnl_is_gross_less_the_fee_and_moves_the_balance(
            self, three_wins):
        for t in three_wins.trades:
            gross = t.starting_capital * t.return_pct / 100.0
            assert t.gross_pnl_usd == pytest.approx(gross, rel=1e-12)
            assert t.net_pnl_usd == pytest.approx(
                t.gross_pnl_usd - t.fees_usd, rel=1e-12)
            assert t.ending_capital == pytest.approx(
                t.starting_capital + t.net_pnl_usd, rel=1e-12)

    def test_a_loss_costs_the_configured_account_risk_plus_the_fee(
            self, one_loss):
        """
        The SL is the OB's opposite edge and sizing targets
        `max_sl_account_risk_pct` of the balance, so an SL exit returns exactly
        -35% of balance BEFORE fees and -1.0 R.
        """
        trade, = one_loss.trades
        assert trade.outcome == "FILLED_SL"
        assert trade.return_pct == -35.0
        assert trade.return_pct == -one_loss.config.max_sl_account_risk_pct
        assert trade.realized_r == -1.0
        assert one_loss.ending_capital == LOSS_END
        assert one_loss.ending_capital < 10.0 - 3.5, "the fee is charged too"


# ---------------------------------------------------------------------------
class TestOneTradeAtATime:
    """
    Requirement F: one-trade-at-a-time must be enforced by the ACTUAL runner,
    not assumed. It is, by the strategy's `PortfolioLock`, and the refusal is
    observable as an `EntryBlock` carrying `ACTIVE_TRADE_OPEN`.
    """

    def test_no_two_trades_hold_the_slot_at_the_same_time(self, three_wins):
        for earlier, later in zip(three_wins.trades, three_wins.trades[1:]):
            assert later.fill_dt >= earlier.exit_dt

    def test_a_fill_cannot_exit_on_its_own_candle(self, three_wins):
        """
        The intra-candle step order resolves the active trade BEFORE fills, so
        the minimum holding is a full bar. This is also the no-lookahead proof.
        """
        for t in three_wins.trades:
            assert t.exit_dt > t.fill_dt
            assert t.holding_bars >= 1
            assert t.exit_bar_idx > t.fill_bar_idx

    def test_a_touch_while_a_trade_is_open_is_refused_and_recorded(self):
        """
        Driven through the engine's SHARED-slot configuration precisely because
        that is where the refusal is reachable: two assets competing for one
        slot. The runner does NOT use this configuration — see the isolation
        tests below for why — but it is the mechanism's own evidence.
        """
        coupled = _drive(
            {"BTCUSD": _candles(WIN_PATTERN, 3),
             "ETHUSD": _candles(_scaled(WIN_PATTERN, 0.5), 3)},
            symbols=["BTCUSD", "ETHUSD"])
        blocks = [b for b in coupled.entry_blocks
                  if b.rejection_code == "ACTIVE_TRADE_OPEN"]
        assert blocks, "an entry touch during an open trade must be recorded"
        for b in blocks:
            assert b.holder_asset and b.holder_asset != b.asset
            assert b.holder_acquired_at is not None
            assert "active trade already open" in b.detail

    def test_the_shared_slot_serialises_the_two_assets(self):
        coupled = _drive(
            {"BTCUSD": _candles(WIN_PATTERN, 3),
             "ETHUSD": _candles(_scaled(WIN_PATTERN, 0.5), 3)},
            symbols=["BTCUSD", "ETHUSD"])
        for earlier, later in zip(coupled.trades, coupled.trades[1:]):
            assert later.fill_dt >= earlier.exit_dt, (
                "one slot means one trade, even across assets")

    def test_a_block_is_per_touch_and_not_a_permanent_ban(self):
        """
        A refused touch must not poison the pair: the same asset trades again
        once the slot frees. Otherwise the shared-slot evidence above would be
        indistinguishable from an asset being disabled.
        """
        coupled = _drive(
            {"BTCUSD": _candles(WIN_PATTERN, 3),
             "ETHUSD": _candles(_scaled(WIN_PATTERN, 0.5), 3)},
            symbols=["BTCUSD", "ETHUSD"])
        blocked = {b.asset for b in coupled.entry_blocks}
        traded = {t.asset for t in coupled.trades}
        assert blocked and blocked <= traded

    def test_the_runner_documents_where_the_rule_is_enforced(self):
        note = RUNNER.assumptions(manual_smc_production_config())
        assert "PortfolioLock" in note["one_trade_at_a_time"]
        assert "own strategy, lifecycle and lock" in note["one_trade_at_a_time"]


# ---------------------------------------------------------------------------
class TestEachPairIsItsOwnAccount:
    """
    Requirement B: each pair starts independently at exactly $10.00 and capital
    is NOT combined between pairs. The runner achieves that by giving each pair
    its own `run_manual_smc_backtest_from_candles` call — one strategy, one
    lifecycle, one lock, one balance. These tests measure the difference that
    choice makes rather than asserting the intention.
    """

    def test_every_pair_the_runner_drives_starts_at_ten_dollars(self):
        assert RUNNER.STARTING_CAPITAL == 10.0
        assert RUNNER.SYMBOLS == ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
        for symbol in RUNNER.SYMBOLS:
            result = _run(symbol, WIN_PATTERN, reps=1)
            assert result.starting_capital == 10.0
            RUNNER.verify_chain(result, RUNNER.STARTING_CAPITAL)
            RUNNER.verify_isolation(result, symbol)

    def test_a_pair_account_contains_only_that_pair(self):
        for symbol in ("BTCUSD", "SOLUSD"):
            result = _run(symbol, WIN_PATTERN, reps=2)
            assert {t.asset for t in result.trades} == {symbol}
            assert tuple(result.symbols) == (symbol,)

    def test_two_pairs_run_separately_do_not_see_each_others_trades(self):
        btc = _run("BTCUSD", WIN_PATTERN, reps=3)
        eth = _run("ETHUSD", _scaled(WIN_PATTERN, 0.5), reps=3)
        assert len(btc.trades) == len(eth.trades) == 3
        assert {t.asset for t in btc.trades}.isdisjoint(
            {t.asset for t in eth.trades})
        for result, symbol in ((btc, "BTCUSD"), (eth, "ETHUSD")):
            RUNNER.verify_isolation(result, symbol)
            RUNNER.verify_chain(result, 10.0)

    def test_neither_pair_is_blocked_by_the_other_when_run_separately(self):
        btc = _run("BTCUSD", WIN_PATTERN, reps=3)
        eth = _run("ETHUSD", _scaled(WIN_PATTERN, 0.5), reps=3)
        assert btc.entry_blocks == () and eth.entry_blocks == ()

    def test_the_pooled_alternative_produces_a_materially_different_result(self):
        """
        The measured cost of getting this wrong. Driving the same two candle
        series through ONE shared-slot backtest pools the $10, serialises the
        assets and refuses three ETHUSD entries; separate runs give each pair
        its own three trades and its own balance. 11.2696... != 12.7004...
        """
        btc = _run("BTCUSD", WIN_PATTERN, reps=3)
        eth = _run("ETHUSD", _scaled(WIN_PATTERN, 0.5), reps=3)
        pooled = _drive(
            {"BTCUSD": _candles(WIN_PATTERN, 3),
             "ETHUSD": _candles(_scaled(WIN_PATTERN, 0.5), 3)},
            symbols=["BTCUSD", "ETHUSD"])
        assert pooled.starting_capital == 10.0, "ONE $10 account, not two"
        assert len(pooled.trades) == 6 and len(pooled.entry_blocks) == 3
        assert pooled.ending_capital == 12.70046975976695
        assert btc.ending_capital == eth.ending_capital == WIN_CHAIN[3]
        assert pooled.ending_capital != btc.ending_capital + eth.ending_capital
        with pytest.raises(RUNNER.BacktestIntegrityError):
            RUNNER.verify_isolation(pooled, "BTCUSD")

    def test_the_runner_records_that_the_accounts_are_independent(self):
        source = RUNNER_PATH.read_text(encoding="utf-8")
        assert "accounts_are_independent" in source
        assert "run_manual_smc_backtest_from_candles" in source


# ---------------------------------------------------------------------------
def _four_pair_dataset(reps: int = 6):
    """
    The four production pairs, same shape at four price scales.

    `WIN_PATTERN`, whose blocks carry an R:R of 0.134. That is deliberate: these
    setups are far below the ratio that used to be demanded here, and they all
    still trade, so every `run_period` assertion below is made against a
    non-vacuous run. R:R is scale-invariant (risk is 75% of the width and reward
    0.60% of the entry, so the ratio has no units), so all four scales share it.
    """
    return {"BTCUSD": _candles(WIN_PATTERN, reps),
            "ETHUSD": _candles(_scaled(WIN_PATTERN, 0.5), reps),
            "SOLUSD": _candles(_scaled(WIN_PATTERN, 0.1), reps),
            "XRPUSD": _candles(_scaled(WIN_PATTERN, 0.01), reps)}


class TestTheTwoPeriodsAreIndependentRuns:
    """
    Requirement C: the recent-period backtest must NOT carry the historical
    run's balance. Each period is its own run and each pair inside it opens at
    exactly $10.00 again.
    """

    def test_the_two_configured_periods_are_distinct_windows(self):
        names = [p[0] for p in RUNNER.PERIODS]
        assert names == ["historical", "recent"]
        (_, h_start, h_end), (_, r_start, r_end) = RUNNER.PERIODS
        assert h_start == datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert r_start == datetime(2026, 8, 1, tzinfo=timezone.utc)
        assert h_end == r_end == datetime(2026, 9, 2, tzinfo=timezone.utc)
        assert h_start < r_start, "the recent window starts inside the other"

    def test_a_later_window_restarts_at_ten_dollars(self):
        """
        The same pair, the same candles, two windows. The second window's first
        trade opens at $10.00 — not at the first window's $11.2696...
        """
        candles = _candles(WIN_PATTERN, 6)
        first = _drive(
            {"BTCUSD": candles}, symbols=["BTCUSD"],
            start_date=_ts(0), end_date=_ts(17))
        second = _drive(
            {"BTCUSD": candles}, symbols=["BTCUSD"],
            start_date=_ts(18), end_date=_ts(35))
        assert first.starting_capital == second.starting_capital == 10.0
        assert first.trades[0].starting_capital == 10.0
        assert second.trades[0].starting_capital == 10.0
        assert second.trades[0].starting_capital != first.ending_capital
        assert first.ending_capital == second.ending_capital == WIN_CHAIN[3]

    def test_a_windowed_run_is_not_the_continuous_run(self):
        """
        Six cycles read continuously compound into one chain; split into two
        windows they compound into two. The difference is the point.
        """
        candles = _candles(WIN_PATTERN, 6)
        whole = _drive({"BTCUSD": candles}, symbols=["BTCUSD"])
        assert len(whole.trades) == 6
        assert whole.ending_capital == 12.70046975976695
        assert whole.ending_capital > WIN_CHAIN[3]

    def test_a_window_only_reads_candles_inside_it(self):
        candles = _candles(WIN_PATTERN, 6)
        second = _drive(
            {"BTCUSD": candles}, symbols=["BTCUSD"],
            start_date=_ts(18), end_date=_ts(35))
        assert second.candles_processed == 18
        assert second.first_ts == _ts(18) and second.last_ts == _ts(35)
        for t in second.trades:
            assert _ts(18) <= t.fill_dt <= _ts(35)

    def test_run_period_opens_every_pair_at_ten_dollars(self, tmp_path):
        payload, config = _period(
            "windowA", _ts(0), _ts(17), _four_pair_dataset(), tmp_path)
        assert payload["starting_balance_per_pair_usd"] == 10.0
        assert payload["capital_shared_between_pairs"] is False
        assert [s["pair"] for s in payload["summaries"]] == list(RUNNER.SYMBOLS)
        for s in payload["summaries"]:
            assert s["starting_balance_usd"] == 10.0
            assert s["final_balance_usd"] == pytest.approx(
                WIN_CHAIN[3], rel=1e-12)
        assert payload["total_trades_all_pairs"] == 12
        assert payload["entry_level_disagreements"] == 0
        assert config.fixed_tp_market_pct == MANUAL_SMC_FIXED_TP_PCT
        #: Every one of those 12 trades has an R:R of 0.134. They filled anyway,
        #: which is the whole point: no ratio rule exists to refuse them.
        assert payload["risk_reward_admission_rule_exists"] is False
        assert payload["total_setups_rejected_by_risk_reward_all_pairs"] == 0
        assert payload["traded_below_observational_rr_reference_all_pairs"] == 12

    def test_two_run_period_calls_do_not_share_a_balance(self, tmp_path):
        dataset = _four_pair_dataset()
        early, _ = _period(
            "early", _ts(0), _ts(17), dataset, tmp_path)
        late, _ = _period(
            "late", _ts(18), _ts(35), dataset, tmp_path)
        assert early["period"] != late["period"]
        for a, b in zip(early["summaries"], late["summaries"]):
            assert a["pair"] == b["pair"]
            assert a["starting_balance_usd"] == b["starting_balance_usd"] == 10.0
            assert b["starting_balance_usd"] != a["final_balance_usd"]

    def test_each_period_writes_its_own_ledger_files(self, tmp_path):
        dataset = _four_pair_dataset()
        early, _ = _period("early", _ts(0), _ts(17), dataset, tmp_path)
        late, _ = _period("late", _ts(18), _ts(35), dataset, tmp_path)
        assert early["trade_ledger_csv"] != late["trade_ledger_csv"]
        for payload in (early, late):
            for key in ("trade_ledger_csv", "blocked_entries_csv"):
                assert Path(payload[key]).is_file()

    def test_the_requested_and_actual_boundaries_are_both_reported(
            self, tmp_path):
        """
        Requirement B/C: the requested window is kept verbatim and the actual
        data boundary reported separately, so a shortfall cannot be hidden.
        """
        payload, _ = _period(
            "clamped", _ts(0) - timedelta(days=30), _ts(35) + timedelta(days=30),
            _four_pair_dataset(), tmp_path)
        assert payload["requested_start"] == (_ts(0) - timedelta(days=30)).isoformat()
        assert payload["actual_first_candle"] == _ts(0).isoformat()
        assert payload["actual_last_candle"] == _ts(35).isoformat()


# ---------------------------------------------------------------------------
class TestTheRunnerDefaultsToTheShippedDetector:
    """
    The other half of the fixture pin above.

    Every measured number in this file is driven on `DETECTOR_MANUAL_SPEC`, so
    something has to assert what the runner does when nobody names a detector —
    otherwise a regression that flipped the production default back would leave
    this whole file green. These tests never assert a trade count: they assert
    the DEFAULT, the fact that it is published in the artifact, and that an
    unknown value still fails closed on the way through.
    """

    def test_the_runner_default_is_the_luxalgo_detector(self):
        default = inspect.signature(RUNNER.run_period).parameters["detector"]
        assert default.default == DETECTOR_LUXALGO_INTERNAL
        assert default.default == "LUXALGO_INTERNAL"
        assert default.default != DETECTOR_MANUAL_SPEC

    def test_the_runner_hands_the_detector_to_the_driver(self):
        """
        Named at the call site, not inherited: a reader of the runner can see
        which algorithm the artifact came from without opening the library.
        """
        source = inspect.getsource(RUNNER.run_period)
        assert "detector=detector" in source
        assert "DETECTOR_MANUAL_SPEC" not in source

    def test_the_period_payload_publishes_the_detector(self, tmp_path):
        """
        A ledger from one detector must not be silently comparable with a
        ledger from the other, so the choice is recorded in the artifact.
        """
        default_payload, _ = RUNNER.run_period(
            "shipped", _ts(0), _ts(35), _four_pair_dataset(), tmp_path)
        assert default_payload["detector"] == DETECTOR_LUXALGO_INTERNAL
        pinned, _ = _period("pinned", _ts(0), _ts(35),
                            _four_pair_dataset(), tmp_path)
        assert pinned["detector"] == DETECTOR_MANUAL_SPEC

    def test_the_shipped_default_sees_nothing_in_a_six_bar_fixture(self,
                                                                  tmp_path):
        """
        WHY the pin exists, measured rather than asserted from prose. The
        LuxAlgo reconstruction needs an ATR(200) warm-up and a 50-bar swing
        window, so on 36 hand-built bars it admits no order block at all and
        every account ends where it opened. The integrity gates still pass on
        that run — an empty run is a valid run, not a broken one.
        """
        payload, _ = RUNNER.run_period(
            "warmup", _ts(0), _ts(35), _four_pair_dataset(), tmp_path)
        assert payload["total_trades_all_pairs"] == 0
        assert payload["total_setups_considered_all_pairs"] == 0
        for summary in payload["summaries"]:
            assert summary["final_balance_usd"] == 10.0
        #: and the same dataset on the fixture detector is not empty, so the
        #: comparison above is a statement about the detector, not the candles.
        pinned, _ = _period("measured", _ts(0), _ts(35),
                            _four_pair_dataset(), tmp_path)
        assert pinned["total_trades_all_pairs"] == 24

    def test_an_unknown_detector_fails_closed_through_the_runner(self,
                                                                tmp_path):
        """No alias, no fallback, no nearest match — the run does not happen."""
        with pytest.raises(ValueError, match="detector"):
            _period("bogus", _ts(0), _ts(17), _four_pair_dataset(), tmp_path,
                    detector="LUXALGO")
        with pytest.raises(ValueError, match="detector"):
            _drive({"BTCUSD": _candles(WIN_PATTERN, 3)}, symbols=["BTCUSD"],
                   detector="luxalgo_internal")


# ---------------------------------------------------------------------------
class TestTheIntegrityGatesActuallyFail:
    """
    A check that cannot fail proves nothing. Every property the runner claims
    to verify is broken here deliberately, and the gate must raise. The eight
    real runs passed these same gates, which is only meaningful because of the
    tests in this class.
    """

    def test_a_clean_run_passes_both_gates(self, three_wins):
        RUNNER.verify_chain(three_wins, 10.0)
        RUNNER.verify_isolation(three_wins, "BTCUSD")

    def test_the_gate_is_a_named_error_type(self):
        assert issubclass(RUNNER.BacktestIntegrityError, Exception)

    def test_a_wrong_requested_opening_balance_is_caught(self, three_wins):
        with pytest.raises(RUNNER.BacktestIntegrityError, match="driver started"):
            RUNNER.verify_chain(three_wins, 25.0)

    def test_a_broken_link_in_the_chain_is_caught(self, three_wins):
        spliced = _splice(three_wins, 1, starting_capital=99.0)
        with pytest.raises(RUNNER.BacktestIntegrityError, match="opened at"):
            RUNNER.verify_chain(spliced, 10.0)

    def test_a_reset_to_ten_dollars_mid_run_is_caught(self, three_wins):
        """The exact failure requirement B forbids: resetting to $10."""
        spliced = _splice(three_wins, 1, starting_capital=10.0)
        with pytest.raises(RUNNER.BacktestIntegrityError, match="opened at"):
            RUNNER.verify_chain(spliced, 10.0)

    def test_overlapping_trades_are_caught(self, three_wins):
        spliced = _splice(three_wins, 1,
                          fill_dt=three_wins.trades[0].exit_dt - timedelta(hours=1))
        with pytest.raises(RUNNER.BacktestIntegrityError, match="trades overlap"):
            RUNNER.verify_chain(spliced, 10.0)

    def test_a_gap_in_the_trade_numbering_is_caught(self, three_wins):
        spliced = _splice(three_wins, 1, trade_id=7)
        with pytest.raises(RUNNER.BacktestIntegrityError, match="not 1..n"):
            RUNNER.verify_chain(spliced, 10.0)

    def test_a_final_balance_that_no_trade_produced_is_caught(self, three_wins):
        drifted = dataclasses.replace(three_wins, ending_capital=12.0)
        with pytest.raises(RUNNER.BacktestIntegrityError, match="result reports"):
            RUNNER.verify_chain(drifted, 10.0)

    def test_a_balance_that_moved_without_any_trade_is_caught(self, three_wins):
        empty = dataclasses.replace(three_wins, trades=(), ending_capital=12.0)
        with pytest.raises(RUNNER.BacktestIntegrityError, match="no trades"):
            RUNNER.verify_chain(empty, 10.0)

    def test_a_flat_run_with_no_trades_is_accepted(self, three_wins):
        empty = dataclasses.replace(three_wins, trades=(), ending_capital=10.0)
        RUNNER.verify_chain(empty, 10.0)

    def test_a_foreign_pair_inside_an_account_is_caught(self, three_wins):
        spliced = _splice(three_wins, 0, asset="ETHUSD")
        with pytest.raises(RUNNER.BacktestIntegrityError,
                           match="contains trades from"):
            RUNNER.verify_isolation(spliced, "BTCUSD")

    def test_a_multi_symbol_configuration_is_caught(self, three_wins):
        pooled = dataclasses.replace(three_wins, symbols=("BTCUSD", "ETHUSD"))
        with pytest.raises(RUNNER.BacktestIntegrityError,
                           match="was configured with"):
            RUNNER.verify_isolation(pooled, "BTCUSD")

    def test_the_wrong_pair_name_is_caught(self, three_wins):
        with pytest.raises(RUNNER.BacktestIntegrityError):
            RUNNER.verify_isolation(three_wins, "XRPUSD")

    def test_run_period_calls_both_gates_on_every_pair(self):
        """
        The gates would be decoration if the runner did not invoke them. This
        reads the runner's own source rather than trusting the report.
        """
        source = inspect.getsource(RUNNER.run_period)
        assert "verify_chain(" in source and "verify_isolation(" in source


# ---------------------------------------------------------------------------
class TestNoRewardRiskRuleExistsOnThisPath:
    """
    THE REVERSAL, pinned from both ends.

    This class previously enforced a 1.5 minimum reward:risk. That rule is not
    part of the authorised Manual SMC specification, so it has been removed
    entirely — from the strategy, the lifecycle, the backtest driver, this
    runner, the Python order-validation gateway, the Python execution engine and
    the Java `OrderValidationGateway`. A ratio is now a REPORTED CONSEQUENCE of
    the entry, stop and target the structural rules produced, never an input to
    whether the setup trades.

    What is pinned here, in the opposite direction to before:

      * no threshold parameter exists on any Manual SMC constructor or entry
        point, and the module that owned the rule is gone;
      * a setup at R:R 0.134 and a setup at R:R 1.998 trade on IDENTICAL terms;
      * no entry block can carry a reward:risk rejection code, and the runner
        refuses to publish a run in which one appears;
      * the ratio still reaches the ledger, the summary and the report — labelled
        informational — because Task 4 asks for it as an observation;
      * the runner still does not import `quantedge.execution`.
    """

    def test_the_live_gateway_has_no_minimum_risk_reward_setting(self):
        """The field is gone, not defaulted: there is nothing left to compare."""
        from quantedge.execution.validation import RiskConfiguration
        assert not hasattr(RiskConfiguration(), "minimum_risk_reward")
        names = {f.name for f in dataclasses.fields(RiskConfiguration)}
        assert not any("risk_reward" in n or n.endswith("_rr") for n in names)

    def test_the_admission_module_no_longer_exists(self):
        """
        The whole module existed to hold the 1.5 rule, so its absence is the
        cleanest possible proof that no Manual SMC stage can consult one.
        """
        with pytest.raises(ModuleNotFoundError):
            __import__("quantedge.strategy.manual_smc.admission")

    def test_the_backtest_config_has_no_risk_reward_field(self):
        """
        Unchanged and still deliberate. It was never a `ManualSpecConfig` field,
        and now there is no threshold anywhere for it to have been.
        """
        names = {f.name for f in dataclasses.fields(ManualSpecConfig)}
        assert not any("risk_reward" in n or n.endswith("_rr") for n in names)
        assert names == {
            "lookback", "entry_depth_pct", "fixed_tp_market_pct",
            "max_sl_account_risk_pct", "applied_leverage_cap", "fee_rate",
            "max_holding_bars", "starting_capital", "min_ob_width",
            "data_timeframe"}

    def test_neither_the_lifecycle_nor_the_scanner_can_rate_a_setup(self):
        """
        The rule lived in the lifecycle, because that is the stage that opens
        trades. Its removal is checked there, and the scanner is checked to have
        stayed clean throughout.

        The scanner is checked on its IMPORTS and on `risk_reward`, not on the
        bare word "admission": the scanner's docstring uses that word for its own
        "break+1 admission" ordering rule, and a substring scan would trip on
        that prose.
        """
        from quantedge.strategy.manual_smc import lifecycle, scanner
        scanner_source = inspect.getsource(scanner)
        assert "risk_reward" not in scanner_source
        assert "manual_smc.admission" not in scanner_source
        assert "assess_risk_reward" not in scanner_source

        source = inspect.getsource(lifecycle)
        assert "manual_smc.admission" not in source
        assert "assess_risk_reward" not in source
        assert "ENTRY_REJECTED_BY_RISK_REWARD" not in source
        assert "_risk_reward_assessment" not in source
        assert not hasattr(lifecycle.ManualSMCLifecycle,
                           "_risk_reward_assessment")

    def test_the_lifecycle_event_vocabulary_holds_no_rr_refusal(self):
        """
        A refusal that cannot be NAMED cannot be emitted. The event type is gone,
        so no downstream consumer can even branch on one.
        """
        from quantedge.strategy.manual_smc.lifecycle import (
            ManualLifecycleEventType)
        members = {m.name for m in ManualLifecycleEventType}
        assert not any("RISK_REWARD" in m or m.endswith("_RR")
                       for m in members)
        assert "ENTRY_BLOCKED_BY_ACTIVE_TRADE" in members, "the ONE gate stays"

    def test_no_manual_smc_entry_point_accepts_a_threshold(self):
        """
        Every constructor and entry point on the path, checked by signature. A
        parameter that does not exist cannot be passed by a future caller.
        """
        from quantedge.strategy.manual_smc.backtest import (
            ManualSMCBacktest, run_manual_smc_backtest)
        from quantedge.strategy.manual_smc.lifecycle import ManualSMCLifecycle
        from quantedge.strategy.manual_smc.strategy import ManualSMCStrategy
        for target in (run_manual_smc_backtest_from_candles,
                       run_manual_smc_backtest,
                       ManualSMCBacktest.__init__,
                       ManualSMCStrategy.__init__,
                       ManualSMCStrategy.from_state,
                       ManualSMCLifecycle.__init__):
            params = set(inspect.signature(target).parameters)
            assert not any("risk_reward" in p or p.endswith("_rr")
                           for p in params), target
        #: And it is not settable after construction either.
        assert not hasattr(ManualSMCStrategy, "minimum_risk_reward")

    def test_the_runner_reports_the_ratio_instead_of_enforcing_it(self):
        assert RUNNER.OBSERVATIONAL_RR_REFERENCE_DECIMAL == Decimal("1.5")
        assert RUNNER.OBSERVATIONAL_RR_REFERENCE == 1.5
        note = RUNNER.assumptions(manual_smc_production_config())
        #: The enforcing keys are GONE, not set falsy: a reader grepping the
        #: previous report's vocabulary must find nothing to misread.
        assert "minimum_risk_reward_enforced_here" not in note
        assert "live_gateway_min_rr_not_enforced_here" not in note
        assert note["minimum_risk_reward_enforced_anywhere"] is False
        assert "NO THRESHOLD IS APPLIED" in note["risk_reward_semantics"]
        assert note["observational_rr_reference_semantics"]

    def test_run_period_passes_no_threshold_to_the_driver(self):
        """
        The one line the whole reversal rests on. If the runner still injected a
        threshold, every artifact it produced would be gated whatever the
        strategy package said.
        """
        source = inspect.getsource(RUNNER.run_period)
        assert "minimum_risk_reward" not in source.replace(
            "NO `minimum_risk_reward` ARGUMENT EXISTS", "")
        assert "verify_no_risk_reward_refusals(" in source

    def test_the_runner_does_not_import_the_live_gateway(self):
        """
        A backtest must not drag the execution validator into its import graph.
        Checked structurally rather than by substring: the runner's comments cite
        `execution/validation.py` by name to record where the rule USED to live,
        and a text scan would trip over that citation.
        """
        imported = _imported_modules()
        assert not any(m.startswith("quantedge.execution") for m in imported)
        assert imported == {
            "__future__", "argparse", "csv", "datetime", "decimal", "json",
            "pathlib", "quantedge.strategy.manual_smc.backtest", "sys",
            "typing"}
        names = {a.asname or a.name
                 for node in ast.walk(ast.parse(_runner_source()))
                 if isinstance(node, (ast.Import, ast.ImportFrom))
                 for a in node.names}
        assert "RiskConfiguration" not in names

    def test_a_setup_far_below_one_point_five_still_trades(self, three_wins):
        """
        THE REQUIREMENT, at driver level. `WIN_PATTERN`'s R:R is 0.134 — an order
        of magnitude under the ratio that used to be demanded — and all three
        cycles fill, with nothing refused and the compounding chain intact.
        """
        assert len(three_wins.trades) == 3
        assert three_wins.entry_blocks == ()
        assert three_wins.ending_capital == WIN_CHAIN[3]
        for t in three_wins.trades:
            rr = t.reward_dist / t.risk_dist
            assert rr == pytest.approx(0.134, abs=1e-9)
            assert rr < RUNNER.OBSERVATIONAL_RR_REFERENCE

    def test_a_setup_above_one_point_five_also_trades(self):
        """
        The other half: nothing about the reversal favours low ratios either. The
        narrow-OB pattern rates 1.998 and fills on the same terms.

        This fixture also produces slot refusals — its cycles overlap — and those
        MUST survive: `ACTIVE_TRADE_OPEN` is the authorised one-trade-at-a-time
        rule, not the rule being removed. So the assertion is on the REASON, not
        on the block list being empty.
        """
        run = _run("BTCUSD", NARROW_OB_PATTERN, reps=3)
        assert len(run.trades) == 3
        assert {b.rejection_code for b in run.entry_blocks} <= \
            {"ACTIVE_TRADE_OPEN"}
        first = run.trades[0]
        assert first.reward_dist / first.risk_dist == pytest.approx(
            1.998, abs=1e-9)
        assert (first.reward_dist / first.risk_dist
                > RUNNER.OBSERVATIONAL_RR_REFERENCE)

    def test_the_two_ratios_are_treated_identically(self):
        """
        The comparison that makes the point. Fifteen-fold apart in reward:risk,
        the two fixtures produce the same trade count from the same cycle count,
        and neither is refused for its ratio. Admission is blind to the number.
        """
        low = _run("BTCUSD", WIN_PATTERN, reps=4)
        high = _run("BTCUSD", NARROW_OB_PATTERN, reps=4)
        assert len(low.trades) == len(high.trades) == 4
        for run in (low, high):
            assert {b.rejection_code for b in run.entry_blocks} <= \
                {"ACTIVE_TRADE_OPEN"}
        low_rr = low.trades[0].reward_dist / low.trades[0].risk_dist
        high_rr = high.trades[0].reward_dist / high.trades[0].risk_dist
        assert low_rr < 1.5 < high_rr, "the fixtures must straddle the old rule"

    def test_no_entry_block_can_carry_a_risk_reward_code(self):
        """
        `INVALID_RISK_REWARD` is not a code this path can produce. Checked over
        both fixtures and over a pooled run that DOES refuse entries, so the
        assertion is made against a non-empty block list too.
        """
        pooled = _drive(
            {"BTCUSD": _candles(WIN_PATTERN, 3),
             "ETHUSD": _candles(_scaled(WIN_PATTERN, 0.5), 3)},
            symbols=["BTCUSD", "ETHUSD"])
        assert pooled.entry_blocks, "need real refusals for this to mean anything"
        for run in (_run("BTCUSD", WIN_PATTERN, reps=3),
                    _run("BTCUSD", NARROW_OB_PATTERN, reps=3),
                    pooled):
            codes = {b.rejection_code for b in run.entry_blocks}
            assert codes <= {"ACTIVE_TRADE_OPEN"}, codes
            for row in RUNNER.block_rows(run):
                assert row["rejection_code"] != "INVALID_RISK_REWARD"

    def test_the_runners_verifier_catches_a_reintroduced_rule(self,
                                                             three_wins):
        """
        `verify_no_risk_reward_refusals` must be able to FAIL, or its silence in
        the real run means nothing. A synthetic block carrying the retired code is
        spliced in, and the runner has to refuse the whole result.
        """
        planted = dataclasses.replace(
            three_wins,
            entry_blocks=(EntryBlock(
                asset="BTCUSD", ob_id="OB-1", direction="SHORT", bar_idx=4,
                ts=_ts(4), detail="rr 0.134 < 1.5",
                rejection_code="INVALID_RISK_REWARD",
                holder_asset=None, holder_ob_id=None,
                holder_acquired_at=None),))
        with pytest.raises(RUNNER.BacktestIntegrityError,
                           match="reward:risk admission rule has been"):
            RUNNER.verify_no_risk_reward_refusals(planted, "BTCUSD")

    def test_the_verifier_passes_a_clean_run(self, three_wins):
        RUNNER.verify_no_risk_reward_refusals(three_wins, "BTCUSD")

    def test_the_summary_reports_the_ratio_as_an_observation(self, three_wins):
        """
        Task 4's statistic: how many trades that WERE taken sat below 1.5. All
        three did, and all three are in the ledger — which is exactly the shape a
        filter would have made impossible.
        """
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        assert summary["total_trades"] == 3
        assert summary["trades_below_observational_rr_reference"] == 3
        assert summary["trades_below_observational_rr_reference_pct"] == 100.0
        assert summary["observational_rr_reference"] == "1.5"
        assert summary["risk_reward_is_informational_only"] is True
        assert summary["risk_reward_admission_rule_exists"] is False
        assert summary["entry_touches_rejected_by_risk_reward"] == 0
        assert summary["setups_rejected_by_risk_reward"] == 0
        assert summary["min_rr_informational"] == pytest.approx(0.134, abs=1e-9)
        assert summary["max_rr_informational"] == pytest.approx(0.134, abs=1e-9)
        #: The enforcing keys are gone from the summary as well.
        assert "trades_below_live_gateway_min_rr" not in summary
        assert "risk_reward_admission_threshold" not in summary

    def test_the_summary_counts_the_setup_population(self, three_wins):
        """
        Task 4's "total setups considered": the order blocks the scanner created,
        which `total_trades` is a subset of. Counted from the lifecycle's own
        OB_CREATED events, so it includes blocks that were never touched.
        """
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        assert summary["total_setups_considered"] == \
            three_wins.order_blocks_created
        assert summary["total_setups_considered"] >= summary["total_trades"] > 0
        assert 0 < summary["setups_traded_pct"] <= 100.0

    def test_the_reported_ratios_are_exact_decimal_not_float(self, three_wins):
        """
        The informational column is still computed in `Decimal`: `0.5994 / 0.3`
        is 1.9979999999999998 in binary, and a report should not print a ratio
        the prices did not produce.
        """
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        assert Decimal(summary["min_rr_informational_exact"]) == Decimal("0.134")
        assert isinstance(summary["min_rr_informational"], float)

    def test_the_ledger_row_carries_the_informational_ratio(self, three_wins):
        row = RUNNER.ledger_row(three_wins.trades[0], 10.0)
        assert row["rr_informational_exact"] == "0.134"
        assert row["rr_below_observational_reference"] is True
        #: The column that asserted admissibility is gone from the row.
        assert "rr_admitted_by_live_rule" not in row
        assert "rr_at_admission" not in row

    def test_a_high_ratio_row_reports_the_reference_flag_as_false(self):
        run = _run("BTCUSD", NARROW_OB_PATTERN, reps=3)
        row = RUNNER.ledger_row(run.trades[0], 10.0)
        assert row["rr_below_observational_reference"] is False
        assert Decimal(row["rr_informational_exact"]) > Decimal("1.5")


# ---------------------------------------------------------------------------
class TestTheLedgerIsFaithful:
    """
    Requirement D: every listed field is present, and the OB zone information
    in particular is NOT omitted. The ledger is a transcription of the driver's
    own trade rows; the few derived columns are marked as such.
    """

    #: The requirement's own list, mapped onto the runner's column names.
    REQUIRED = {
        "trade number": "trade_number",
        "pair": "pair",
        "direction": "direction",
        "OB creation timestamp": "ob_creation_ts",
        "break context": "structure_context",
        "OB zone upper": "ob_zone_upper",
        "OB zone lower": "ob_zone_lower",
        "OB width": "ob_width",
        "entry level": "entry_level",
        "actual entry timestamp": "entry_ts",
        "actual entry price": "entry_price",
        "SL": "sl_price",
        "TP": "tp_price",
        "exit timestamp": "exit_ts",
        "exit price": "exit_price",
        "outcome": "outcome",
        "holding duration": "holding_hours",
        "R:R (informational only)": "rr_informational",
        "P/L %": "pnl_pct_of_balance",
        "P/L $": "net_pnl_usd",
        "starting balance": "starting_balance_usd",
        "ending balance": "ending_balance_usd",
        "cumulative return": "cumulative_return_pct",
        "OB/setup identifier": "ob_setup_id",
        "exit reason": "reason_for_exit",
    }

    def test_every_required_field_has_a_column(self):
        missing = {label: col for label, col in self.REQUIRED.items()
                   if col not in RUNNER.LEDGER_COLUMNS}
        assert missing == {}

    def test_the_row_keys_are_exactly_the_declared_columns(self, three_wins):
        row = RUNNER.ledger_row(three_wins.trades[0], 10.0)
        assert tuple(row) == RUNNER.LEDGER_COLUMNS
        #: 50 before the R:R columns were widened. The three R:R numbers sit
        #: together rather than at the far end of the row, so they are read as
        #: one group; every consumer in this repository reads the ledger by
        #: column NAME (`csv.DictReader`), so grouping them costs nothing.
        assert len(RUNNER.LEDGER_COLUMNS) == 52
        columns = list(RUNNER.LEDGER_COLUMNS)
        at = columns.index("rr_informational")
        assert columns[at:at + 3] == [
            "rr_informational", "rr_informational_exact",
            "rr_below_observational_reference"]
        #: The names that asserted admissibility are gone from the schema.
        assert "rr_at_admission" not in columns
        assert "rr_admitted_by_live_rule" not in columns

    def test_the_ob_zone_is_present_and_is_the_real_zone(self, three_wins):
        trade = three_wins.trades[0]
        row = RUNNER.ledger_row(trade, 10.0)
        assert float(row["ob_zone_upper"]) == trade.ob_top == 105.0
        assert float(row["ob_zone_lower"]) == trade.ob_bottom == 99.0
        assert float(row["ob_width"]) == trade.ob_width == 6.0
        assert float(row["ob_proximal_edge"]) == trade.proximal == 99.0
        assert float(row["ob_distal_edge"]) == trade.distal == 105.0

    def test_the_stop_is_the_opposite_ob_edge(self, three_wins):
        for t in three_wins.trades:
            assert t.sl_price == t.distal
            row = RUNNER.ledger_row(t, t.starting_capital)
            assert float(row["sl_price"]) == float(row["ob_distal_edge"])

    def test_the_entry_is_a_quarter_of_the_width_into_the_zone(self, three_wins):
        for t in three_wins.trades:
            assert RUNNER.entry_level_from_zone(t) == pytest.approx(
                t.entry_price, rel=1e-12)
            row = RUNNER.ledger_row(t, t.starting_capital)
            assert row["entry_level_matches_25pct_rule"] is True

    def test_the_take_profit_is_the_configured_fixed_percentage(
            self, three_wins):
        pct = three_wins.config.fixed_tp_market_pct
        assert pct == MANUAL_SMC_FIXED_TP_PCT == 0.60
        for t in three_wins.trades:
            expected = t.entry_price * (1 - pct / 100.0)   # SHORT
            assert t.tp_price == pytest.approx(expected, rel=1e-12)

    def test_the_derived_entry_check_can_disagree(self, three_wins):
        """
        The cross-check column would be worthless if it were hard-coded True.
        A trade whose entry price is moved off the 25% level must flip it.
        """
        tampered = dataclasses.replace(three_wins.trades[0], entry_price=101.0)
        row = RUNNER.ledger_row(tampered, 10.0)
        assert row["entry_level_matches_25pct_rule"] is False

    def test_floats_are_written_without_losing_precision(self, three_wins):
        row = RUNNER.ledger_row(three_wins.trades[1], 10.406466666666667)
        assert row["starting_balance_usd"] == repr(10.406466666666667)
        assert float(row["ending_balance_usd"]) == \
            three_wins.trades[1].ending_capital

    def test_the_structure_context_invents_no_classification(self, three_wins):
        """
        The engine records only a break bar; it does not label BOS versus
        CHOCH. The ledger must therefore not claim one.
        """
        context = RUNNER.structure_context(three_wins.trades[0])
        assert "CHOCH" not in context.upper()
        assert "bar" in context and "origin@" in context
        source = inspect.getsource(RUNNER.structure_context)
        assert "CHOCH" in source, "the absence must be documented in place"

    def test_a_blocked_entry_row_names_the_holder(self):
        coupled = _drive(
            {"BTCUSD": _candles(WIN_PATTERN, 3),
             "ETHUSD": _candles(_scaled(WIN_PATTERN, 0.5), 3)},
            symbols=["BTCUSD", "ETHUSD"])
        rows = RUNNER.block_rows(coupled)
        assert rows and tuple(rows[0]) == RUNNER.BLOCK_COLUMNS
        assert rows[0]["rejection_code"] == "ACTIVE_TRADE_OPEN"
        assert rows[0]["holder_pair"] and rows[0]["holder_ob_id"]

    def test_a_ledger_written_to_disk_round_trips(self, tmp_path, three_wins):
        import csv as _csv
        rows = [RUNNER.ledger_row(t, t.starting_capital)
                for t in three_wins.trades]
        path = RUNNER.write_csv(tmp_path / "ledger.csv",
                               RUNNER.LEDGER_COLUMNS, rows)
        with path.open(newline="", encoding="utf-8") as fh:
            back = list(_csv.DictReader(fh))
        assert len(back) == 3
        assert list(back[0]) == list(RUNNER.LEDGER_COLUMNS)
        for original, restored in zip(three_wins.trades, back):
            assert float(restored["ending_balance_usd"]) == \
                original.ending_capital


# ---------------------------------------------------------------------------
class TestTheSummaryCoversRequirementE:
    """Every statistic section E asks for, per period and per pair."""

    REQUIRED = ("starting_balance_usd", "final_balance_usd", "total_trades",
                "tp_count", "sl_count", "win_rate_pct", "loss_rate_pct",
                "net_pnl_usd", "return_pct", "average_trade_pnl_usd",
                "expectancy_r", "max_drawdown_pct", "largest_win_usd",
                "largest_loss_usd", "average_holding_hours")

    def test_every_required_statistic_is_present(self, three_wins):
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        assert set(self.REQUIRED) <= set(summary)

    def test_the_counts_reconcile_with_the_trade_rows(self, three_wins):
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        assert summary["total_trades"] == len(three_wins.trades) == 3
        assert summary["tp_count"] == 3 and summary["sl_count"] == 0
        assert summary["win_rate_pct"] == 100.0
        assert summary["loss_rate_pct"] == 0.0

    def test_the_win_and_loss_rates_sum_with_the_unclassified(self, one_loss):
        summary = RUNNER.summarise(one_loss, "ETHUSD", 10.0)
        assert summary["tp_count"] == 0 and summary["sl_count"] == 1
        assert summary["win_rate_pct"] == 0.0
        assert summary["loss_rate_pct"] == 100.0

    def test_the_net_change_matches_the_balances(self, three_wins):
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        assert summary["net_pnl_usd"] == pytest.approx(
            summary["final_balance_usd"] - summary["starting_balance_usd"],
            rel=1e-12)

    def test_the_labels_stay_true_when_a_pair_never_loses(self, three_wins):
        """
        A bare `min(net_pnl)` fold would report the SMALLEST WIN as the largest
        loss. The clamp keeps the label honest in the degenerate case.
        """
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        assert summary["largest_win_usd"] > 0
        assert summary["largest_loss_usd"] == 0.0

    def test_the_labels_stay_true_when_a_pair_never_wins(self, one_loss):
        summary = RUNNER.summarise(one_loss, "ETHUSD", 10.0)
        assert summary["largest_win_usd"] == 0.0
        assert summary["largest_loss_usd"] < 0

    def test_a_mixed_pair_reports_both_signs(self):
        mixed = _drive(
            {"SOLUSD": _candles(WIN_PATTERN + LOSS_PATTERN, 1)},
            symbols=["SOLUSD"])
        summary = RUNNER.summarise(mixed, "SOLUSD", 10.0)
        assert summary["tp_count"] >= 1 and summary["sl_count"] >= 1
        assert summary["largest_win_usd"] > 0 > summary["largest_loss_usd"]

    def test_the_peak_balance_is_reported_next_to_the_final_one(
            self, three_wins):
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        assert summary["peak_balance_usd"] >= summary["final_balance_usd"]
        assert summary["peak_after_trade_number"] == 3

    def test_a_peak_that_is_never_revisited_is_still_reported(self, one_loss):
        """The opening balance counts as part of the equity curve."""
        summary = RUNNER.summarise(one_loss, "ETHUSD", 10.0)
        assert summary["peak_balance_usd"] == 10.0
        assert summary["peak_after_trade_number"] == 0
        assert summary["final_balance_usd"] < summary["peak_balance_usd"]

    def test_a_zero_final_balance_is_flagged_rather_than_implied(
            self, three_wins, one_loss):
        for result in (three_wins, one_loss):
            summary = RUNNER.summarise(result, result.symbols[0], 10.0)
            assert summary["final_balance_is_exactly_zero"] is False
            assert summary["zero_notional_trades"] == 0

    def test_both_views_reference_only_existing_keys(self, three_wins):
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        for view in (RUNNER.SUMMARY_VIEW, RUNNER.DIAGNOSTIC_VIEW):
            for key, _label, _fmt in view:
                assert key in summary, key

    def test_a_table_renders_for_all_four_pairs(self):
        rows = [RUNNER.summarise(_run(s, WIN_PATTERN, 1), s, 10.0)
                for s in RUNNER.SYMBOLS]
        table = RUNNER.markdown_table(rows, RUNNER.SUMMARY_VIEW)
        lines = table.strip().splitlines()
        assert len(lines) == 2 + len(RUNNER.SYMBOLS)
        for symbol in RUNNER.SYMBOLS:
            assert symbol in table
        assert "Largest loss $" in lines[0]


# ---------------------------------------------------------------------------
class TestTheCoverageInspectorCanActuallyFindFaults:
    """
    Requirement F: coverage, gaps, timezone and OHLC sanity are MEASURED before
    anything runs. An inspector that cannot see a gap would make the reported
    "0 missing hours" meaningless, so each fault is injected here.
    """

    @staticmethod
    def _clean(n: int = 10):
        return candles_from_ohlc(
            [(i, 100.0, 101.0, 99.0, 100.5) for i in range(n)], _ts)

    def test_a_clean_hourly_series_reports_no_faults(self):
        info = RUNNER.inspect_coverage(self._clean())
        assert info["rows"] == 10
        assert info["step_seconds_observed"] == [3600.0]
        assert info["strictly_increasing"] is True
        assert info["duplicate_timestamps"] == 0
        assert info["missing_hours"] == 0
        assert info["utc_offsets"] == [0.0]
        assert info["ohlc_violations"] == 0
        assert info["non_positive_prices"] == 0

    def test_a_missing_hour_is_found(self):
        clean = self._clean()
        info = RUNNER.inspect_coverage([c for i, c in enumerate(clean) if i != 4])
        assert info["missing_hours"] == 1
        assert 7200.0 in info["step_seconds_observed"]

    def test_a_duplicate_timestamp_is_found(self):
        clean = self._clean()
        info = RUNNER.inspect_coverage(list(clean) + [clean[3]])
        assert info["duplicate_timestamps"] == 1
        assert info["strictly_increasing"] is False

    def test_the_hour_arithmetic_is_withheld_when_the_series_is_out_of_order(
            self):
        """
        `missing_hours` reads the first and last rows, so out of order it would
        be a nonsense number. It is reported as None instead, leaving the two
        flags that ARE valid as the signal.
        """
        clean = self._clean()
        info = RUNNER.inspect_coverage(list(clean) + [clean[3]])
        assert info["missing_hours"] is None
        assert info["expected_rows_if_no_gap"] is None

    def test_an_impossible_candle_is_found(self):
        clean = list(self._clean())
        clean[2] = dataclasses.replace(clean[2], high=98.0)
        assert RUNNER.inspect_coverage(clean)["ohlc_violations"] == 1

    def test_a_non_positive_price_is_found(self):
        clean = list(self._clean())
        clean[5] = dataclasses.replace(clean[5], low=0.0)
        assert RUNNER.inspect_coverage(clean)["non_positive_prices"] >= 1

    def test_an_empty_series_reports_zero_rows_rather_than_crashing(self):
        assert RUNNER.inspect_coverage([]) == {"rows": 0}

    def test_the_window_row_count_is_inclusive_of_both_bounds(self):
        clean = self._clean()
        assert RUNNER.rows_in_window(clean, _ts(2), _ts(5)) == 4
        assert RUNNER.rows_in_window(clean, _ts(0), _ts(9)) == 10
        assert RUNNER.rows_in_window(clean, _ts(20), _ts(30)) == 0

    def test_no_candle_is_fabricated_to_fill_a_gap(self):
        """
        Requirement B forbids fabricating candles. The inspector reports a gap;
        it must not return more rows than it was given.
        """
        clean = self._clean()
        gapped = [c for i, c in enumerate(clean) if i != 4]
        assert RUNNER.inspect_coverage(gapped)["rows"] == len(gapped) == 9
        source = inspect.getsource(RUNNER.inspect_coverage)
        assert "No repair, no fill" in source


# ---------------------------------------------------------------------------
class TestTheRunnerOwnsNoStrategyRule:
    """
    Requirement B: extend the EXISTING backtest infrastructure; do not create a
    simplified strategy that duplicates Manual SMC logic. The runner is a
    harness — it calls the driver and formats the result.
    """

    def test_the_runner_drives_the_existing_engine_entry_point(self):
        assert RUNNER.run_manual_smc_backtest_from_candles is \
            run_manual_smc_backtest_from_candles

    def test_the_runner_imports_nothing_but_that_engine_module(self):
        """
        ONE engine module: the driver it calls. The admission module that once
        owned the reward:risk rule is gone with the rule, and the runner did not
        inherit a replacement — it supplies no threshold, no comparison against
        one, and no strategy arithmetic of its own.
        """
        engine = {m for m in _imported_modules() if m.startswith("quantedge")}
        assert engine == {"quantedge.strategy.manual_smc.backtest"}

    def test_the_runner_defines_no_dataclass_of_its_own(self):
        """
        A private trade or OB record would be the first sign of a parallel
        strategy. The only class the runner defines is its error type.
        """
        classes = [node.name for node in ast.walk(ast.parse(_runner_source()))
                   if isinstance(node, ast.ClassDef)]
        assert classes == ["BacktestIntegrityError"]

    def test_the_runner_computes_no_stop_or_target_of_its_own(self):
        """
        The only price arithmetic allowed is the DERIVED 25% cross-check, which
        is confined to `entry_level_from_zone` and acted on by nothing.

        Checked on assignment TARGETS from the syntax tree, not by substring: a
        text scan for `position_notional =` matches the comparison
        `t.position_notional == 0.0`, which is a read, and reads are the whole
        point of a transcription harness.
        """
        source = _runner_source()
        assert "fixed_tp_market_pct" in source, "the TP is read, never computed"
        assigned = _assigned_names()
        assert assigned.isdisjoint({
            "sl_price", "tp_price", "entry_price", "distal", "proximal",
            "ob_top", "ob_bottom", "applied_leverage", "theoretical_leverage",
            "position_notional", "fees_usd", "net_pnl_usd", "ending_capital",
            "starting_capital", "realized_r", "outcome"})
        #: The single derived quantity, and the local it lives in.
        assert "quarter" in assigned and "level" in assigned

    def test_the_derived_cross_check_is_the_only_recomputation(self):
        source = inspect.getsource(RUNNER.entry_level_from_zone)
        assert "DERIVED" in source
        assert "nothing acts on it" in source

    def test_the_runner_uses_the_production_config(self, tmp_path):
        _payload, config = _period(
            "cfg", _ts(0), _ts(17), _four_pair_dataset(), tmp_path)
        assert dataclasses.asdict(config) == \
            dataclasses.asdict(manual_smc_production_config())
        assert config.fixed_tp_market_pct == MANUAL_SMC_FIXED_TP_PCT
        assert config.applied_leverage_cap == 100.0
        assert config.max_sl_account_risk_pct == 35.0
        assert config.fee_rate == 0.0008

    def test_every_pair_in_a_period_ran_under_the_same_config(self, tmp_path):
        source = inspect.getsource(RUNNER.run_period)
        assert "config" in source
        payload, _ = _period(
            "same", _ts(0), _ts(17), _four_pair_dataset(), tmp_path)
        assert len({s["pair"] for s in payload["summaries"]}) == 4

    def test_the_runner_touches_no_exchange_and_no_credential(self):
        """
        Safety scope: local data only. No HTTP client, no credential name, no
        environment read, and no reference to either live-order script.
        """
        source = _runner_source().lower()
        for forbidden in ("requests", "httpx", "aiohttp", "urllib",
                          "delta_api_key", "delta_api_secret", "api_secret",
                          "os.environ", "getenv", "dotenv", "--allow-env-file",
                          "place_order", "run_o12_single_order_live_test",
                          "run_o14_full_size_taker_close_test"):
            assert forbidden not in source, forbidden
        assert "os" not in _imported_modules()

    def test_the_runner_reads_only_local_canonical_candles(self):
        assert RUNNER.load_canonical_dataset is not None
        base = RUNNER.default_canonical_base()
        assert base.name == "delta_exchange_india"
        assert "canonical" in base.parts


# ---------------------------------------------------------------------------
class TestTheAssumptionsAreReadOffTheConfig:
    """
    Requirement B/F: report the existing engine's assumptions EXACTLY; do not
    silently introduce fees, slippage, leverage or sizing that differ from it.
    Every numeric assumption is therefore read off the config that actually
    ran, so an engine default change moves the report with it.
    """

    def test_the_numbers_come_from_the_config_not_from_the_script(self):
        odd = manual_smc_production_config(
            fee_rate=0.0031, applied_leverage_cap=42.0,
            max_sl_account_risk_pct=7.5, data_timeframe="4h")
        note = RUNNER.assumptions(odd)
        assert note["fee_rate_of_notional"] == 0.0031
        assert note["applied_leverage_cap"] == 42.0
        assert note["max_sl_account_risk_pct"] == 7.5
        assert note["timeframe"] == "4h"

    def test_the_production_defaults_are_what_the_report_states(self):
        note = RUNNER.assumptions(manual_smc_production_config())
        assert note["timeframe"] == "1h"
        assert note["fee_rate_of_notional"] == 0.0008
        assert note["applied_leverage_cap"] == 100.0
        assert note["max_sl_account_risk_pct"] == 35.0
        assert note["entry_depth_pct_into_ob"] == 0.25
        assert note["fixed_tp_pct_from_entry"] == 0.60
        assert note["max_holding_bars_after_fill"] == 72
        assert note["lookback_bars_for_ob_origin"] == 10
        assert note["starting_capital_used"] == 10.0

    def test_slippage_and_funding_are_declared_absent_not_omitted(self):
        note = RUNNER.assumptions(manual_smc_production_config())
        assert note["slippage_model"].startswith("none")
        assert note["funding_model"] == "none"

    def test_the_sizing_arithmetic_is_reproduced_in_full(self):
        """
        Requirement B: explicitly document how the $10 balance becomes a trade
        size. Each line of the sizing chain must be present verbatim.
        """
        text = inspect.getdoc(RUNNER.assumptions)
        for line in ("sl_dist_pct  = |entry - sl| / entry * 100",
                     "theo_lev     = max_sl_account_risk_pct / sl_dist_pct",
                     "applied_lev  = min(applied_leverage_cap, theo_lev)",
                     "margin_usd   = account_balance",
                     "notional_usd = account_balance * applied_lev",
                     "fee_usd      = notional_usd * fee_rate",
                     "net_pnl      = gross_pnl - fee_usd",
                     "next_balance = max(0.0, account_balance + net_pnl)"):
            assert line in text, line

    def test_the_documented_sizing_matches_what_the_engine_does(
            self, three_wins):
        """The docstring would be worthless if the engine disagreed with it."""
        config = three_wins.config
        for t in three_wins.trades:
            sl_dist_pct = abs(t.entry_price - t.sl_price) / t.entry_price * 100
            theo = config.max_sl_account_risk_pct / sl_dist_pct
            applied = min(config.applied_leverage_cap, theo)
            assert t.sl_dist_pct == pytest.approx(sl_dist_pct, rel=1e-12)
            assert t.theoretical_leverage == pytest.approx(theo, rel=1e-12)
            assert t.applied_leverage == pytest.approx(applied, rel=1e-12)
            assert t.position_notional == pytest.approx(
                t.starting_capital * applied, rel=1e-12)
            assert t.fees_usd == pytest.approx(
                t.position_notional * config.fee_rate, rel=1e-12)

    def test_the_dual_touch_rule_is_stated_with_its_source(self):
        note = RUNNER.assumptions(manual_smc_production_config())
        rule = note["same_candle_tp_and_sl"]
        assert "SL-first" in rule and "FILLED_SL" in rule
        assert "is_ambiguous=True" in rule and "lifecycle.py" in rule

    def test_the_no_lookahead_claim_is_stated_with_its_mechanism(self):
        note = RUNNER.assumptions(manual_smc_production_config())
        assert "no later candle is read" in note["information_available"]
        order = note["intra_candle_step_order"]
        assert "resolve active trade" in order and "scan and admit" in order
        assert "cannot also exit on its own candle" in order

    def test_the_timezone_is_stated(self):
        note = RUNNER.assumptions(manual_smc_production_config())
        assert "UTC" in note["timezone"]

    def test_the_profit_factor_convention_is_disclosed(self):
        """
        The driver substitutes loss_r=1.0 for a lossless pair, so a lossless
        `profit_factor` is a total gain R, not a ratio. Stated, not hidden.
        """
        note = RUNNER.assumptions(manual_smc_production_config())
        convention = note["profit_factor_convention"]
        assert "loss_r=1.0" in convention and "zero" in convention

    def test_the_lossless_convention_is_what_the_driver_actually_does(
            self, three_wins):
        summary = RUNNER.summarise(three_wins, "BTCUSD", 10.0)
        assert summary["sl_count"] == 0
        assert summary["profit_factor"] == summary["total_realized_r"]


# ---------------------------------------------------------------------------
ARTEFACTS = RUNNER.DEFAULT_OUT
REPORT_JSON = ARTEFACTS / "compounding_backtest_report.json"


def _report():
    if not REPORT_JSON.is_file():
        pytest.skip(f"no run artefacts at {REPORT_JSON}; run the script first")
    import json as _json
    return _json.loads(REPORT_JSON.read_text(encoding="utf-8"))


def _ledger(path_str: str):
    import csv as _csv
    path = Path(path_str)
    if not path.is_file():
        pytest.skip(f"ledger missing: {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        return list(_csv.DictReader(fh))


class TestTheReportedNumbersMatchTheLedgersOnDisk:
    """
    The report and the ledgers are two renderings of one run, and the final
    write-up quotes the report. These tests reconcile them, so a number in the
    write-up cannot disagree with the trade rows behind it.

    They SKIP rather than fail when the artefacts are absent: the CSVs are
    generated output, not committed fixtures, and a fresh clone has none.
    """

    def test_the_report_exists_with_both_periods(self):
        report = _report()
        assert [p["period"] for p in report["periods"]] == \
            ["historical", "recent"]
        assert report["pairs"] == list(RUNNER.SYMBOLS)
        assert report["accounts_are_independent"] is True
        assert report["starting_balance_per_pair_usd"] == 10.0

    def test_every_period_opened_every_pair_at_ten_dollars(self):
        for period in _report()["periods"]:
            assert period["capital_shared_between_pairs"] is False
            assert [s["pair"] for s in period["summaries"]] == \
                list(RUNNER.SYMBOLS)
            for s in period["summaries"]:
                assert s["starting_balance_usd"] == 10.0

    def test_the_recent_period_did_not_inherit_the_historical_balance(self):
        historical, recent = _report()["periods"]
        for old, new in zip(historical["summaries"], recent["summaries"]):
            assert old["pair"] == new["pair"]
            assert new["starting_balance_usd"] == 10.0
            assert new["starting_balance_usd"] != old["final_balance_usd"]

    def test_the_trade_counts_match_the_ledger_rows(self):
        for period in _report()["periods"]:
            rows = _ledger(period["trade_ledger_csv"])
            assert len(rows) == period["total_trades_all_pairs"]
            for s in period["summaries"]:
                mine = [r for r in rows if r["pair"] == s["pair"]]
                assert len(mine) == s["total_trades"]

    def test_the_outcome_counts_match_the_ledger_rows(self):
        for period in _report()["periods"]:
            rows = _ledger(period["trade_ledger_csv"])
            for s in period["summaries"]:
                mine = [r for r in rows if r["pair"] == s["pair"]]
                assert sum(1 for r in mine
                           if r["outcome"] == "FILLED_TP") == s["tp_count"]
                assert sum(1 for r in mine
                           if r["outcome"] == "FILLED_SL") == s["sl_count"]
                assert s["tp_count"] + s["sl_count"] + s["timeout_count"] == \
                    s["total_trades"], "an unclassified outcome would hide here"

    def test_the_chain_in_the_csv_is_contiguous_for_every_pair(self):
        for period in _report()["periods"]:
            rows = _ledger(period["trade_ledger_csv"])
            for s in period["summaries"]:
                mine = [r for r in rows if r["pair"] == s["pair"]]
                assert float(mine[0]["starting_balance_usd"]) == 10.0
                for earlier, later in zip(mine, mine[1:]):
                    assert earlier["ending_balance_usd"] == \
                        later["starting_balance_usd"]
                assert float(mine[-1]["ending_balance_usd"]) == \
                    s["final_balance_usd"]

    def test_the_trade_numbers_in_the_csv_run_one_to_n_per_pair(self):
        for period in _report()["periods"]:
            rows = _ledger(period["trade_ledger_csv"])
            for s in period["summaries"]:
                mine = [r for r in rows if r["pair"] == s["pair"]]
                assert [int(r["trade_number"]) for r in mine] == \
                    list(range(1, len(mine) + 1))

    def test_no_two_trades_in_a_pair_overlap_in_the_csv(self):
        for period in _report()["periods"]:
            rows = _ledger(period["trade_ledger_csv"])
            for s in period["summaries"]:
                mine = [r for r in rows if r["pair"] == s["pair"]]
                for earlier, later in zip(mine, mine[1:]):
                    assert later["entry_ts"] >= earlier["exit_ts"], (
                        f"{s['pair']} trade {later['trade_number']} overlaps")

    def test_every_ledger_row_agrees_with_the_25_percent_rule(self):
        for period in _report()["periods"]:
            rows = _ledger(period["trade_ledger_csv"])
            assert period["entry_level_disagreements"] == 0
            bad = [r for r in rows
                   if r["entry_level_matches_25pct_rule"] != "True"]
            assert bad == []

    def test_the_observational_counts_match_the_ledger_rows(self):
        """
        The observation has to be arithmetically true, not merely stated — and
        it is an observation: every one of these rows IS in the ledger, which is
        what distinguishes a count from a filter.
        """
        for period in _report()["periods"]:
            rows = _ledger(period["trade_ledger_csv"])
            for s in period["summaries"]:
                mine = [r for r in rows if r["pair"] == s["pair"]]
                below = sum(1 for r in mine
                            if Decimal(r["rr_informational_exact"])
                            < RUNNER.OBSERVATIONAL_RR_REFERENCE_DECIMAL)
                assert below == s["trades_below_observational_rr_reference"]
                assert s["risk_reward_admission_rule_exists"] is False
                assert s["setups_rejected_by_risk_reward"] == 0

    def test_no_pair_reports_a_zero_balance_it_did_not_reach(self):
        """
        Four historical balances render as 0.000000 in fixed point but are not
        zero (3.1e-44 and smaller). The flag is the only zero claim allowed.
        """
        for period in _report()["periods"]:
            for s in period["summaries"]:
                zero = s["final_balance_is_exactly_zero"]
                assert zero == (s["final_balance_usd"] == 0.0)
                if not zero:
                    assert s["final_balance_usd"] > 0.0

    def test_the_reported_data_window_is_the_data_and_not_the_request(self):
        report = _report()
        for period in report["periods"]:
            assert period["requested_start"] != period["actual_first_candle"] \
                or period["requested_end"] != period["actual_last_candle"], (
                    "at least one boundary is clamped by the data; both keys "
                    "must be present so the shortfall is visible")
        for symbol in RUNNER.SYMBOLS:
            coverage = report["data_coverage"][symbol]
            assert coverage["missing_hours"] == 0
            assert coverage["duplicate_timestamps"] == 0
            assert coverage["ohlc_violations"] == 0
            assert coverage["strictly_increasing"] is True
            assert coverage["utc_offsets"] == [0.0]
            assert coverage["step_seconds_observed"] == [3600.0]

    def test_the_recorded_assumptions_are_the_production_ones(self):
        recorded = _report()["assumptions"]
        expected = RUNNER.assumptions(manual_smc_production_config())
        assert recorded == expected
