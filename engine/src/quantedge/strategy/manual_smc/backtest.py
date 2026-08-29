"""
Manual SMC — Backtest Driver (Phase 1 Step 8).
==============================================

The ONLY backtest entry point for the Manual SMC strategy, and a deliberately
THIN one: it owns chronological iteration, historical data preparation and
result collection, and nothing else.

WHAT THIS MODULE MUST NEVER CONTAIN
-----------------------------------
No probe logic, no pullback logic, no displacement logic, no entry timing, no
invalidation rule, no TP/SL hit test, no BOS detection, no OB geometry, no
global-lock semantics and no PnL formula. Every one of those lives exactly
once, in the module that already owns it:

    BOS + origin selection ........ scanner.ManualSpecBOSScanner
    OB geometry / hit predicates .. geometry (verbatim oracle extraction)
    probe -> displacement -> fill .. lifecycle.ManualSMCLifecycle
    single global trade slot ....... portfolio.PortfolioLock + lifecycle
    leverage / fees / compounding .. sizing
    tick grid ...................... quantization (OUTPUT boundary only)
    per-candle orchestration ....... strategy.ManualSMCStrategy

This driver calls `ManualSMCStrategy.evaluate_closed_candle()` exactly once per
closed candle and reads the report it returns. The frozen research oracle and
the old golden test suite each re-implemented the lifecycle inline, which is
precisely how the stranded-trade lock defect survived unnoticed; a second
implementation here would recreate that entire class of bug.

THE CORRECTED GLOBAL LOCK — the behavioural difference from the oracle
---------------------------------------------------------------------
`run_manual_spec_backtest()` in the frozen oracle gates entry on
`c_ts <= global_lock_until_dt`, a SAME-TIMESTAMP watermark. On any strictly
later candle a second setup simply overwrote `active_trade`: the first trade
was stranded in TRADE_ACTIVE, never closed, never recorded, and its capital
outcome never applied. This driver inherits `lifecycle._entry_blocked`, whose
first clause is `active_trade is not None` — ONE trade at a time across ALL
assets, ALL directions and ALL order blocks, for as long as it stays open. The
oracle's timestamp watermark survives only as a secondary conservative guard.

Consequence, stated plainly: the trade population produced here is NOT the
oracle's published 2,022-trade population, and the difference is caused by that
lock correction, not by any change to a Manual SMC rule.

DATA CONTRACT (the I/O is mirrored from the oracle; none of the logic is)
------------------------------------------------------------------------
    <repo>/data/canonical/delta_exchange_india/<SYMBOL>/1h/full_history.csv
        falling back to  .../1h/2026.csv
    columns: timestamp, open, high, low, close, volume
    a naive timestamp is read as UTC
    `bar_idx` is the row's position in the FULL sorted history and is NOT
    renumbered by a start/end date filter, because OB records carry absolute
    bar indices and `entry_bar_from_bos` must remain comparable.

`pandas` is deliberately NOT imported: the strategy layer stays cheap to
import, and `csv` + `sorted` reproduce the oracle's loader exactly. Two
deliberate divergences, both fail-closed and both I/O-only: a missing
repository root RAISES instead of guessing `parents[5]`, and a CSV with
duplicate timestamps RAISES instead of assigning two bar indices to one candle.

ARITHMETIC OWNED HERE
---------------------
Only ledger aggregation over values the strategy already produced: the running
sum of `settlement.realized_r`, the peak/drawdown walk over `balance_after`,
counts per outcome, and `round()` for presentation. `100.0`, `3600.0` and the
profit-factor sentinel `99.0` are reporting constants reproduced from the
oracle's `_agg` so the two baselines can be compared like for like. No price,
leverage, fee, R or return percentage is computed in this module.

DELIBERATELY ABSENT
-------------------
No live execution, no exchange call, no order placement, no Java/backend call,
no database, no WebSocket, no runtime composition, no CLI side effect on
import. `LIVE_EXECUTION_AUTHORIZED` is False and every public entry point
asserts it, mirroring the oracle's own governance guard. Quantization is
REPORTED, never applied to strategy geometry: the ideal behavioural baseline
and the exchange-executable baseline must stay distinguishable.

The on-grid bracket the strategy produced at fill is RETAINED on the trade row
(`BacktestTrade.quantized_bracket`) so that the executable baseline can be
measured from this ledger without any module re-quantizing a price. Measuring
it is `executable.py`'s job, not this driver's: nothing here reads that field,
compares it to the ideal legs, or lets it influence a single recorded number.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

from quantedge.strategy.manual_smc.lifecycle import (
    DISPLACEMENT_MODE,
    OUTCOME_SL,
    OUTCOME_TIMEOUT,
    OUTCOME_TP,
)
from quantedge.strategy.manual_smc.models import (
    MANUAL_SMC_STRATEGY_NAME,
    MANUAL_SMC_STRATEGY_VERSION,
    ManualOBRecord,
    ManualSpecConfig,
)
from quantedge.strategy.manual_smc.quantization import (
    QuantizedBracket,
    TickSizeSpec,
)
from quantedge.strategy.manual_smc.sizing import (
    ContractSpecRegistry,
    PositionSizing,
)
from quantedge.strategy.manual_smc.strategy import (
    ManualSMCClose,
    ManualSMCEvaluation,
    ManualSMCFill,
    ManualSMCStrategy,
)

# ---------------------------------------------------------------------------
# Governance. Mirrors the frozen oracle's own guard (its L97-99 and L1529).
# ---------------------------------------------------------------------------
#: A backtest never authorises execution. Nothing in this package flips it.
LIVE_EXECUTION_AUTHORIZED: bool = False

DEFAULT_SYMBOLS: Tuple[str, ...] = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
CANONICAL_RELATIVE_PATH: Tuple[str, ...] = (
    "data", "canonical", "delta_exchange_india")
TIMEFRAME_DIRNAME: str = "1h"
PRIMARY_CSV_NAME: str = "full_history.csv"
FALLBACK_CSV_NAME: str = "2026.csv"
REQUIRED_CSV_COLUMNS: Tuple[str, ...] = (
    "timestamp", "open", "high", "low", "close")

class BacktestError(RuntimeError):
    """Base class for driver refusals."""


class BacktestDataError(BacktestError):
    """Historical input is missing, malformed or ambiguous. Fails closed."""


class BacktestGovernanceError(BacktestError):
    """Refuses to run while live execution is authorised."""


def _assert_not_live() -> None:
    """The oracle asserts this too. A backtest must never imply authority."""
    if LIVE_EXECUTION_AUTHORIZED:
        raise BacktestGovernanceError(
            "governance: live execution is authorised; a backtest must not be "
            "run in the same process")


# ---------------------------------------------------------------------------
# Historical data preparation (permitted driver responsibility)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Candle:
    """One closed candle plus its absolute index in the asset's history."""
    bar_idx: int
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def find_repo_root(start: Optional[Path] = None) -> Path:
    """
    Mirror of the oracle's `_find_repo_root`, by marker file and never a guess.

    The oracle falls back to `parents[5]` when no marker is found. This refuses
    instead: a wrong root silently yields an EMPTY dataset and therefore a
    zero-trade "baseline", which is a far worse outcome than an exception.
    """
    p = (start or Path(__file__)).resolve()
    for parent in p.parents:
        if ((parent / ".git").exists()
                or (parent / "backend").exists()
                or (parent / "docker-compose.yml").exists()):
            return parent
    raise BacktestDataError(
        f"no repository root above {p}; pass an explicit canonical base "
        f"directory rather than guessing one")


def default_canonical_base(start: Optional[Path] = None) -> Path:
    """`<repo>/data/canonical/delta_exchange_india`."""
    base = find_repo_root(start)
    for part in CANONICAL_RELATIVE_PATH:
        base = base / part
    return base


def canonical_csv_path(canonical_base: Path, symbol: str) -> Optional[Path]:
    """`full_history.csv`, else `2026.csv`, else None — the oracle's order."""
    tf_dir = Path(canonical_base) / symbol / TIMEFRAME_DIRNAME
    for name in (PRIMARY_CSV_NAME, FALLBACK_CSV_NAME):
        candidate = tf_dir / name
        if candidate.exists():
            return candidate
    return None


def _parse_ts(raw: str) -> datetime:
    """`fromisoformat`, UTC attached when naive. Exactly the oracle's rule."""
    ts = datetime.fromisoformat(raw)
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts

def load_canonical_candles(
    canonical_base: Path, symbol: str
) -> List[Candle]:
    """
    Read one symbol's canonical 1h history, indexed by full-history position.

    Returns `[]` when the symbol has no CSV at all — the oracle's behaviour, so
    a partially populated dataset yields the same asset coverage. A CSV that
    EXISTS but is malformed, or that carries duplicate timestamps, raises: an
    ambiguous bar index would make `entry_bar_from_bos` meaningless, and
    `evaluate_closed_candle` would refuse the replayed candle anyway.
    """
    csv_path = canonical_csv_path(canonical_base, symbol)
    if csv_path is None:
        return []
    rows: List[Tuple[datetime, float, float, float, float, float]] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_CSV_COLUMNS
                   if c not in (reader.fieldnames or ())]
        if missing:
            raise BacktestDataError(
                f"{csv_path}: missing required column(s) {missing}")
        for line_no, row in enumerate(reader, start=2):
            try:
                rows.append((
                    _parse_ts(row["timestamp"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row.get("volume") or 0.0),
                ))
            except (TypeError, ValueError) as exc:
                raise BacktestDataError(
                    f"{csv_path}:{line_no}: unreadable row {row!r}") from exc

    rows.sort(key=lambda r: r[0])
    seen = set()
    out: List[Candle] = []
    for i, (ts, o, h, l, c, v) in enumerate(rows):
        if ts in seen:
            raise BacktestDataError(
                f"{csv_path}: duplicate timestamp {ts.isoformat()}; refusing "
                f"to assign two bar indices to one candle")
        seen.add(ts)
        out.append(Candle(bar_idx=i, ts=ts, open=o, high=h, low=l, close=c,
                          volume=v))
    return out


def load_canonical_dataset(
    canonical_base: Optional[Path] = None,
    symbols: Optional[Sequence[str]] = None,
) -> Dict[str, List[Candle]]:
    """symbol -> candles, for every requested symbol (possibly empty lists)."""
    base = (Path(canonical_base) if canonical_base is not None
            else default_canonical_base())
    syms = tuple(symbols) if symbols is not None else DEFAULT_SYMBOLS
    return {s: load_canonical_candles(base, s) for s in syms}


def candles_from_ohlc(
    rows: Iterable[Tuple[int, float, float, float, float]],
    ts_of,
) -> List[Candle]:
    """`(bar_idx, o, h, l, c)` rows + a `bar_idx -> ts` clock -> candles."""
    return [Candle(bar_idx=b, ts=ts_of(b), open=o, high=h, low=l, close=c)
            for (b, o, h, l, c) in rows]

# ---------------------------------------------------------------------------
# The single global clock (permitted driver responsibility)
# ---------------------------------------------------------------------------
class TimelineRow(NamedTuple):
    """One `(timestamp, symbol, absolute bar index)` step of the global clock."""
    ts: datetime
    symbol: str
    bar_idx: int


def build_timeline(
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    symbols: Optional[Sequence[str]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> List[TimelineRow]:
    """
    Sorted by `(ts, symbol)` — exactly the oracle's key.

    The tie-break is the SYMBOL NAME, alphabetically, and it is load-bearing:
    four 1h assets share ONE trade slot, so at an identical timestamp the
    iteration order decides which setup wins it. Alphabetical order is
    deterministic and reproducible across runs and platforms; the iteration
    order of a caller's dict is not a promise worth depending on.

    A date filter drops timeline rows but never renumbers `bar_idx`.
    """
    syms = tuple(symbols) if symbols is not None else tuple(candles_by_symbol)
    rows: List[TimelineRow] = []
    for symbol in syms:
        for candle in candles_by_symbol.get(symbol) or ():
            if start_date is not None and candle.ts < start_date:
                continue
            if end_date is not None and candle.ts > end_date:
                continue
            rows.append(TimelineRow(candle.ts, symbol, candle.bar_idx))
    rows.sort(key=lambda r: (r.ts, r.symbol))
    return rows


# ---------------------------------------------------------------------------
# Ledger records (permitted driver responsibility: collection, not derivation)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _FillContext:
    """
    OB geometry snapshotted AT FILL, so the close row cannot read a mutated OB.

    `ManualOBRecord` is mutable and shared; copying the scalars at fill time is
    what keeps the ledger a record of what actually happened.
    """
    fill: ManualSMCFill
    ob_top: float
    ob_bottom: float
    ob_width: float
    proximal: float
    distal: float
    bos_dt: datetime
    formation_dt: datetime
    displacement_confirmed_dt: Optional[datetime]
    displacement_confirmed_bar: Optional[int]
    limit_active_from_bar: Optional[int]
    pre_displacement_touches: int
    entry_bar_from_bos: int
    ob_age_at_entry_hours: float
    retest_number: int
    mfe_from_proximal: float

    @classmethod
    def of(cls, fill: ManualSMCFill, ob: ManualOBRecord) -> "_FillContext":
        return cls(
            fill=fill,
            ob_top=ob.ob_top,
            ob_bottom=ob.ob_bottom,
            ob_width=ob.ob_width,
            proximal=ob.proximal,
            distal=ob.distal,
            bos_dt=ob.bos_dt,
            formation_dt=ob.formation_dt,
            displacement_confirmed_dt=ob.displacement_confirmed_dt,
            displacement_confirmed_bar=ob.displacement_confirmed_bar,
            limit_active_from_bar=ob.limit_active_from_bar,
            pre_displacement_touches=ob.pre_displacement_touches,
            entry_bar_from_bos=ob.entry_bar_from_bos,
            ob_age_at_entry_hours=ob.ob_age_at_entry_hours,
            retest_number=ob.retest_number,
            mfe_from_proximal=ob.mfe_from_proximal,
        )

@dataclass(frozen=True)
class BacktestTrade:
    """
    One closed trade, TRANSCRIBED from the strategy's own report.

    Every price, leverage, fee and R value is copied from `ManualSMCClose`,
    `PositionSizing`, `TradeSettlement` or the fill-time OB snapshot. The only
    driver-derived fields are `trade_id`, `cumulative_realized_r` (a running
    sum) and `holding_bars` / `holding_time_hours` (elapsed wall time between
    two timestamps the strategy recorded).
    """
    trade_id: int
    asset: str
    direction: str
    ob_id: str
    origin_bar_idx: int
    bos_bar_idx: int
    fill_bar_idx: int
    exit_bar_idx: int
    bos_dt: datetime
    formation_dt: datetime
    displacement_confirmed_dt: Optional[datetime]
    fill_dt: datetime
    exit_dt: datetime
    ob_top: float
    ob_bottom: float
    ob_width: float
    proximal: float
    distal: float
    entry_price: float
    sl_price: float
    tp_price: float
    exit_price: float
    risk_dist: float
    reward_dist: float
    sl_dist_pct: float
    theoretical_leverage: float
    applied_leverage: float
    leverage_clamped: bool
    starting_capital: float
    position_notional: float
    fees_usd: float
    gross_pnl_usd: float
    net_pnl_usd: float
    ending_capital: float
    return_pct: float
    realized_r: float
    cumulative_realized_r: float
    outcome: str
    reason_for_exit: str
    is_ambiguous: bool
    holding_bars: int
    holding_time_hours: float
    entry_bar_from_bos: int
    ob_age_at_entry_hours: float
    retest_number: int
    pre_displacement_touches: int
    mfe_from_proximal: float
    #: Section 11 separation: whether the fill HAD an exchange-valid bracket.
    #: Reported, never applied — strategy geometry above is unquantized.
    quantized_at_fill: bool
    quantization_refusal: Optional[str]
    #: The on-grid bracket the strategy computed at fill, retained VERBATIM so
    #: the exchange-executable baseline can be MEASURED post-hoc without any
    #: module re-quantizing a price. `None` when no product specification was
    #: injected, or when the grid snap was refused. Transcription, not
    #: derivation: `executable.py` reads it, this driver never acts on it.
    quantized_bracket: Optional[QuantizedBracket]
    #: The strategy's own `PositionSizing` for this trade, retained verbatim.
    #: The scalar sizing fields above are copied out of it for convenience; the
    #: whole object is kept so no reporting layer has to reassemble one from
    #: scalars and accidentally invent a field the strategy never computed.
    sizing_at_fill: PositionSizing
    data_timeframe: str
    displacement_mode: str = DISPLACEMENT_MODE
    strategy_name: str = MANUAL_SMC_STRATEGY_NAME
    strategy_version: str = MANUAL_SMC_STRATEGY_VERSION

    @property
    def is_win(self) -> bool:
        return self.outcome == OUTCOME_TP

    @property
    def is_loss(self) -> bool:
        return self.outcome == OUTCOME_SL

    @property
    def is_timeout(self) -> bool:
        return self.outcome == OUTCOME_TIMEOUT


@dataclass(frozen=True)
class EntryBlock:
    """
    A setup that touched its entry while the ONE global slot was taken.

    This is the observable proof that the lock is portfolio-wide. It is a
    diagnostic record and never an instruction: nothing cancels, amends or
    places an order because of it.
    """
    asset: str
    ob_id: str
    direction: str
    bar_idx: int
    ts: datetime
    detail: str
    rejection_code: Optional[str]
    holder_asset: Optional[str]
    holder_ob_id: Optional[str]
    #: When the holder acquired the ONE slot. A block at a STRICTLY LATER
    #: timestamp is exactly the case the oracle's `c_ts <= global_lock_until_dt`
    #: watermark failed to catch, and where it overwrote `active_trade`.
    holder_acquired_at: Optional[datetime]

    @property
    def oracle_would_have_overwritten(self) -> bool:
        """
        True when the frozen oracle would have admitted this entry instead.

        Diagnostic only — it names the historical defect, it does not implement
        it. The oracle blocked exclusively on `ts <= global_lock_until_dt`, so a
        block held by an open trade acquired at a STRICTLY EARLIER timestamp is
        a trade the oracle would have filled, stranding the previous one in
        TRADE_ACTIVE forever.
        """
        return (self.holder_acquired_at is not None
                and self.ts > self.holder_acquired_at)

@dataclass(frozen=True)
class Aggregate:
    """
    Summary statistics, computed exactly as the oracle's `_agg` computes them.

    `wins` counts FILLED_TP and `losses` counts FILLED_SL, so a FILLED_TIMEOUT
    is in NEITHER — that is the oracle's definition, reproduced deliberately so
    the two baselines are comparable. `timeouts` and `ambiguous` are reported
    ADDITIONALLY, because Step 8 asks for them by name and the oracle's dict
    never surfaced either.
    """
    trades: int
    wins: int
    losses: int
    timeouts: int
    ambiguous: int
    win_rate_pct: float
    total_r: float
    expectancy_r: float
    profit_factor: float
    #: `wins + losses + timeouts`. Equals `trades` when every outcome is known.
    classified: int

    @property
    def average_r(self) -> float:
        """Same number as `expectancy_r`; named as Step 8 section 6 names it."""
        return self.expectancy_r


def aggregate(trades: Sequence[BacktestTrade]) -> Aggregate:
    """Fold trade rows into an `_agg`-comparable summary. No strategy rule."""
    n = len(trades)
    if n == 0:
        return Aggregate(trades=0, wins=0, losses=0, timeouts=0, ambiguous=0,
                         win_rate_pct=0.0, total_r=0.0, expectancy_r=0.0,
                         profit_factor=0.0, classified=0)
    wins = sum(1 for t in trades if t.is_win)
    losses = sum(1 for t in trades if t.is_loss)
    timeouts = sum(1 for t in trades if t.is_timeout)
    ambiguous = sum(1 for t in trades if t.is_ambiguous)
    total_r = sum(t.realized_r for t in trades)
    gain_r = sum(t.realized_r for t in trades if t.is_win) if wins else 0.0
    loss_r = (abs(sum(t.realized_r for t in trades if t.is_loss))
              if losses else 1.0)
    return Aggregate(
        trades=n,
        wins=wins,
        losses=losses,
        timeouts=timeouts,
        ambiguous=ambiguous,
        win_rate_pct=round(wins / n * 100, 2),
        total_r=round(total_r, 2),
        expectancy_r=round(total_r / n, 4),
        profit_factor=round(gain_r / loss_r, 2) if loss_r > 0 else 99.0,
        classified=wins + losses + timeouts,
    )


@dataclass(frozen=True)
class BacktestResult:
    """Everything the Step 8 report needs, and nothing an exchange could use."""
    strategy_name: str
    strategy_version: str
    config: ManualSpecConfig
    symbols: Tuple[str, ...]
    candles_processed: int
    first_ts: Optional[datetime]
    last_ts: Optional[datetime]
    starting_capital: float
    ending_capital: float
    total_return_pct: float
    max_drawdown_pct: float
    overall: Aggregate
    asset_breakdown: Dict[str, Aggregate]
    trades: Tuple[BacktestTrade, ...]
    #: Every entry touch refused because the ONE global slot was occupied.
    entry_blocks: Tuple[EntryBlock, ...]
    invalidations: int
    #: A trade still open on the last candle is NOT in `trades` — it never
    #: closed, so it has no settled outcome. Named so it cannot be overlooked.
    open_trade_at_end: bool
    #: Section 11: quantization is reported, never mixed into the behaviour.
    fills_with_quantized_bracket: int
    quantization_refusals: Tuple[str, ...]

    def as_oracle_dict(self) -> Dict[str, Any]:
        """
        The oracle's result-dict keys, for a like-for-like baseline comparison.

        `trades_df` is deliberately absent: this module does not import pandas.
        """
        return {
            "config": vars(self.config),
            "starting_capital": self.starting_capital,
            "ending_capital": self.ending_capital,
            "total_return_pct": self.total_return_pct,
            "total_executed_trades": self.overall.trades,
            "wins": self.overall.wins,
            "losses": self.overall.losses,
            "win_rate_pct": self.overall.win_rate_pct,
            "expectancy_r": self.overall.expectancy_r,
            "total_realized_r": self.overall.total_r,
            "profit_factor": self.overall.profit_factor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "asset_breakdown": {
                s: {
                    "trades": a.trades, "wins": a.wins, "losses": a.losses,
                    "wr": a.win_rate_pct, "total_r": a.total_r,
                    "exp_r": a.expectancy_r, "pf": a.profit_factor,
                }
                for s, a in self.asset_breakdown.items()
            },
        }

# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------
class ManualSMCBacktest:
    """
    Chronological driver over ONE `ManualSMCStrategy`.

    Owns the loop and the ledger. Owns no strategy rule: `evaluate()` is a
    single call into `evaluate_closed_candle` plus bookkeeping on what came
    back. There is exactly ONE strategy instance and therefore exactly ONE
    `PortfolioLock`, which is what makes the single trade slot portfolio-wide
    rather than per asset.
    """

    def __init__(
        self,
        config: Optional[ManualSpecConfig] = None,
        symbols: Optional[Sequence[str]] = None,
        tick_specs: Optional[Mapping[str, TickSizeSpec]] = None,
        registry: Optional[ContractSpecRegistry] = None,
        account_id: str = "BACKTEST",
        starting_capital: Optional[float] = None,
        strategy: Optional[ManualSMCStrategy] = None,
    ) -> None:
        _assert_not_live()
        self.cfg: ManualSpecConfig = config or ManualSpecConfig()
        self.symbols: Tuple[str, ...] = (
            tuple(symbols) if symbols is not None else DEFAULT_SYMBOLS)
        self.strategy: ManualSMCStrategy = (
            strategy if strategy is not None else ManualSMCStrategy(
                config=self.cfg,
                assets=list(self.symbols),
                account_id=account_id,
                account_balance=starting_capital,
                tick_specs=tick_specs,
                registry=registry,
            ))
        #: A caller-supplied strategy is authoritative about its own config.
        self.cfg = self.strategy.cfg

        self._starting_capital: float = self.strategy.account_balance
        self._peak_capital: float = self._starting_capital
        self._max_dd_pct: float = 0.0
        self._cum_r: float = 0.0
        self._trade_seq: int = 0
        self._trades: List[BacktestTrade] = []
        self._blocks: List[EntryBlock] = []
        self._open_fills: Dict[str, _FillContext] = {}
        self._invalidations: int = 0
        self._quantized_fills: int = 0
        self._q_refusals: List[str] = []
        self._candles: int = 0
        self._first_ts: Optional[datetime] = None
        self._last_ts: Optional[datetime] = None

    # -- one candle -------------------------------------------------------
    def evaluate(self, symbol: str, candle: Candle) -> ManualSMCEvaluation:
        """ONE closed candle -> the strategy's own report. Recorded, then returned."""
        evaluation = self.strategy.evaluate_closed_candle(
            symbol, candle.bar_idx, candle.ts,
            candle.open, candle.high, candle.low, candle.close,
        )
        self._record(evaluation)
        return evaluation

    # -- the loop ---------------------------------------------------------
    def iter_run(
        self,
        timeline: Sequence[TimelineRow],
        candles_by_symbol: Mapping[str, Sequence[Candle]],
    ) -> Iterator[ManualSMCEvaluation]:
        """Walk the global clock, yielding each evaluation as it is produced."""
        index: Dict[str, Dict[int, Candle]] = {
            symbol: {c.bar_idx: c for c in rows}
            for symbol, rows in candles_by_symbol.items()
        }
        for row in timeline:
            candle = index.get(row.symbol, {}).get(row.bar_idx)
            if candle is None:
                raise BacktestDataError(
                    f"timeline references {row.symbol} bar {row.bar_idx} at "
                    f"{row.ts.isoformat()}, which is not in the loaded history")
            if candle.ts != row.ts:
                raise BacktestDataError(
                    f"{row.symbol} bar {row.bar_idx}: timeline timestamp "
                    f"{row.ts.isoformat()} disagrees with the candle's "
                    f"{candle.ts.isoformat()}")
            yield self.evaluate(row.symbol, candle)

    def run(
        self,
        timeline: Sequence[TimelineRow],
        candles_by_symbol: Mapping[str, Sequence[Candle]],
    ) -> BacktestResult:
        """Consume the whole timeline and return the collected result."""
        for _ in self.iter_run(timeline, candles_by_symbol):
            pass
        return self.result()

    # -- bookkeeping ------------------------------------------------------
    def _record(self, ev: ManualSMCEvaluation) -> None:
        """
        Transcribe one evaluation into the ledger.

        The close is recorded BEFORE the fill so the ledger reads in the order
        the lifecycle produced the events: a release always precedes the
        acquisition it enables.
        """
        self._candles += 1
        if self._first_ts is None:
            self._first_ts = ev.ts
        self._last_ts = ev.ts
        self._invalidations += len(ev.invalidated)

        for blocked in ev.blocked:
            rejection = blocked.lock_rejection
            holder = rejection.held_by if rejection is not None else None
            self._blocks.append(EntryBlock(
                asset=blocked.asset,
                ob_id=blocked.ob_id,
                direction=blocked.direction,
                bar_idx=blocked.bar_idx,
                ts=blocked.ts,
                detail=blocked.detail,
                rejection_code=(rejection.code.value
                                if rejection is not None else None),
                holder_asset=holder.asset if holder is not None else None,
                holder_ob_id=holder.ob_id if holder is not None else None,
                holder_acquired_at=(holder.acquired_at
                                    if holder is not None else None),
            ))

        if ev.closed is not None:
            self._on_close(ev.closed)
        if ev.filled is not None:
            self._on_fill(ev)

    def _on_fill(self, ev: ManualSMCEvaluation) -> None:
        fill = ev.filled
        assert fill is not None                    # guarded by the caller
        active = ev.active_trade
        if active is None or active.ob.ob_id != fill.ob_id:
            raise BacktestError(
                f"{fill.asset}: fill reported for {fill.ob_id} but the "
                f"lifecycle's active trade is "
                f"{None if active is None else active.ob.ob_id}")
        self._open_fills[fill.ob_id] = _FillContext.of(fill, active.ob)
        if fill.quantized is not None:
            self._quantized_fills += 1
        if fill.quantization_refusal is not None:
            self._q_refusals.append(
                f"{fill.asset} {fill.ob_id}: {fill.quantization_refusal}")

    def _on_close(self, close: ManualSMCClose) -> None:
        ctx = self._open_fills.pop(close.ob_id, None)
        if ctx is None:
            raise BacktestError(
                f"{close.asset}: close reported for {close.ob_id} with no "
                f"recorded fill; the ledger and the lifecycle disagree")
        exit_ = close.exit
        sizing = close.sizing
        settlement = close.settlement

        self._cum_r += settlement.realized_r
        capital = close.balance_after
        if capital > self._peak_capital:
            self._peak_capital = capital
        drawdown = ((self._peak_capital - capital) / self._peak_capital * 100.0
                    if self._peak_capital > 0 else 0.0)
        if drawdown > self._max_dd_pct:
            self._max_dd_pct = drawdown

        holding_hours = max(
            1.0, (exit_.exit_dt - exit_.fill_dt).total_seconds() / 3600.0)
        self._trade_seq += 1
        self._trades.append(BacktestTrade(
            trade_id=self._trade_seq,
            asset=close.asset,
            direction=close.direction,
            ob_id=close.ob_id,
            origin_bar_idx=exit_.origin_bar_idx,
            bos_bar_idx=exit_.bos_bar_idx,
            fill_bar_idx=exit_.fill_bar_idx,
            exit_bar_idx=exit_.exit_bar_idx,
            bos_dt=ctx.bos_dt,
            formation_dt=ctx.formation_dt,
            displacement_confirmed_dt=ctx.displacement_confirmed_dt,
            fill_dt=exit_.fill_dt,
            exit_dt=exit_.exit_dt,
            ob_top=ctx.ob_top,
            ob_bottom=ctx.ob_bottom,
            ob_width=ctx.ob_width,
            proximal=ctx.proximal,
            distal=ctx.distal,
            entry_price=exit_.entry_price,
            sl_price=exit_.sl_price,
            tp_price=exit_.tp_price,
            exit_price=exit_.exit_price,
            risk_dist=sizing.risk_dist,
            reward_dist=sizing.reward_dist,
            sl_dist_pct=sizing.sl_dist_pct,
            theoretical_leverage=sizing.theoretical_leverage,
            applied_leverage=sizing.applied_leverage,
            leverage_clamped=sizing.leverage_clamped,
            starting_capital=close.balance_before,
            position_notional=sizing.notional_usd,
            fees_usd=sizing.fee_usd,
            gross_pnl_usd=settlement.gross_pnl_usd,
            net_pnl_usd=settlement.net_pnl_usd,
            ending_capital=close.balance_after,
            return_pct=settlement.return_pct,
            realized_r=settlement.realized_r,
            cumulative_realized_r=self._cum_r,
            outcome=exit_.outcome,
            reason_for_exit=exit_.reason_for_exit,
            is_ambiguous=exit_.is_ambiguous,
            holding_bars=int(holding_hours),
            holding_time_hours=round(holding_hours, 2),
            entry_bar_from_bos=ctx.entry_bar_from_bos,
            ob_age_at_entry_hours=ctx.ob_age_at_entry_hours,
            retest_number=ctx.retest_number,
            pre_displacement_touches=ctx.pre_displacement_touches,
            mfe_from_proximal=ctx.mfe_from_proximal,
            quantized_at_fill=ctx.fill.quantized is not None,
            quantization_refusal=ctx.fill.quantization_refusal,
            quantized_bracket=ctx.fill.quantized,
            sizing_at_fill=sizing,
            data_timeframe=self.cfg.data_timeframe,
        ))

    # -- result -----------------------------------------------------------
    @property
    def trades(self) -> Tuple[BacktestTrade, ...]:
        return tuple(self._trades)

    @property
    def entry_blocks(self) -> Tuple[EntryBlock, ...]:
        return tuple(self._blocks)

    def result(self) -> BacktestResult:
        """Snapshot the ledger. Callable mid-run; does not end the run."""
        trades = tuple(self._trades)
        ending = self.strategy.account_balance
        start = self._starting_capital
        return BacktestResult(
            strategy_name=self.strategy.strategy_name,
            strategy_version=self.strategy.strategy_version,
            config=self.cfg,
            symbols=self.symbols,
            candles_processed=self._candles,
            first_ts=self._first_ts,
            last_ts=self._last_ts,
            starting_capital=start,
            ending_capital=ending,
            total_return_pct=((ending - start) / start * 100.0
                              if start > 0 else 0.0),
            max_drawdown_pct=round(self._max_dd_pct, 2),
            overall=aggregate(trades),
            asset_breakdown={
                symbol: aggregate([t for t in trades if t.asset == symbol])
                for symbol in self.symbols
            },
            trades=trades,
            entry_blocks=tuple(self._blocks),
            invalidations=self._invalidations,
            open_trade_at_end=self.strategy.lifecycle.active_trade is not None,
            fills_with_quantized_bracket=self._quantized_fills,
            quantization_refusals=tuple(self._q_refusals),
        )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def run_manual_smc_backtest(
    data_base_dir: Optional[Path] = None,
    config: Optional[ManualSpecConfig] = None,
    symbols: Optional[Sequence[str]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    tick_specs: Optional[Mapping[str, TickSizeSpec]] = None,
    registry: Optional[ContractSpecRegistry] = None,
    account_id: str = "BACKTEST",
) -> BacktestResult:
    """
    Load the canonical dataset and run it through `ManualSMCStrategy`.

    The signature deliberately parallels the frozen oracle's
    `run_manual_spec_backtest` so the two baselines can be produced under
    identical inputs — but this function shares NO logic with it, enforces the
    corrected `active_trade is not None` lock through the shared lifecycle, and
    returns a typed `BacktestResult` instead of a DataFrame-bearing dict.

    `tick_specs` is optional and affects REPORTING only: omitting it leaves
    every setup non-executable and changes no strategy geometry (section 11).
    """
    _assert_not_live()
    syms = tuple(symbols) if symbols is not None else DEFAULT_SYMBOLS
    dataset = load_canonical_dataset(data_base_dir, syms)
    timeline = build_timeline(dataset, syms, start_date, end_date)
    driver = ManualSMCBacktest(
        config=config, symbols=syms, tick_specs=tick_specs,
        registry=registry, account_id=account_id)
    return driver.run(timeline, dataset)


def run_manual_smc_backtest_from_candles(
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    config: Optional[ManualSpecConfig] = None,
    symbols: Optional[Sequence[str]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    tick_specs: Optional[Mapping[str, TickSizeSpec]] = None,
    registry: Optional[ContractSpecRegistry] = None,
    account_id: str = "BACKTEST",
    starting_capital: Optional[float] = None,
) -> BacktestResult:
    """
    In-memory variant: same driver, same strategy, no filesystem.

    This is the path the acceptance tests use, so a test can never accidentally
    exercise a different lifecycle than the CSV-fed production backtest does.
    """
    _assert_not_live()
    syms = (tuple(symbols) if symbols is not None
            else tuple(candles_by_symbol))
    timeline = build_timeline(candles_by_symbol, syms, start_date, end_date)
    driver = ManualSMCBacktest(
        config=config, symbols=syms, tick_specs=tick_specs,
        registry=registry, account_id=account_id,
        starting_capital=starting_capital)
    return driver.run(timeline, candles_by_symbol)


__all__ = [
    "LIVE_EXECUTION_AUTHORIZED",
    "DEFAULT_SYMBOLS",
    "CANONICAL_RELATIVE_PATH",
    "TIMEFRAME_DIRNAME",
    "PRIMARY_CSV_NAME",
    "FALLBACK_CSV_NAME",
    "REQUIRED_CSV_COLUMNS",
    "BacktestError",
    "BacktestDataError",
    "BacktestGovernanceError",
    "Candle",
    "TimelineRow",
    "BacktestTrade",
    "EntryBlock",
    "Aggregate",
    "BacktestResult",
    "ManualSMCBacktest",
    "find_repo_root",
    "default_canonical_base",
    "canonical_csv_path",
    "load_canonical_candles",
    "load_canonical_dataset",
    "candles_from_ohlc",
    "build_timeline",
    "aggregate",
    "run_manual_smc_backtest",
    "run_manual_smc_backtest_from_candles",
]
