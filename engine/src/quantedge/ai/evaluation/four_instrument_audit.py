"""
Four-Instrument Canonical Dataset Audit.

Audits canonical historical Delta Exchange India data availability across
BTCUSD, ETHUSD, SOLUSD, and XRPUSD without fabricating missing data.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd


@dataclass(frozen=True)
class InstrumentAuditRecord:
    symbol: str
    timeframe: str
    available: bool
    path: Optional[str]
    candle_count: int
    start_date: Optional[str]
    end_date: Optional[str]
    file_size_kb: float
    status_summary: str


def _find_canonical_dir() -> Path:
    cur = Path(__file__).resolve()
    for parent in cur.parents:
        cand = parent / "data" / "canonical" / "delta_exchange_india"
        if cand.exists():
            return cand
    return cur.parents[4] / "data" / "canonical" / "delta_exchange_india"


def audit_four_instruments(base_dir: Optional[Path] = None) -> List[InstrumentAuditRecord]:
    """
    Audits the four core instruments (BTCUSD, ETHUSD, SOLUSD, XRPUSD).
    Never fabricates missing files or candle rows.
    """
    if base_dir is None:
        base_dir = _find_canonical_dir()

    symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    records = []

    for sym in symbols:
        sym_dir = base_dir / sym / "1h"
        csv_file = sym_dir / "2026.csv"

        if csv_file.exists():
            try:
                df = pd.read_csv(csv_file)
                count = len(df)
                ts_col = "timestamp" if "timestamp" in df.columns else df.columns[0]
                df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
                start_str = df[ts_col].min().strftime("%Y-%m-%d %H:%M:%S UTC")
                end_str = df[ts_col].max().strftime("%Y-%m-%d %H:%M:%S UTC")
                size_kb = csv_file.stat().st_size / 1024.0

                records.append(
                    InstrumentAuditRecord(
                        symbol=sym,
                        timeframe="1h",
                        available=True,
                        path=str(csv_file),
                        candle_count=count,
                        start_date=start_str,
                        end_date=end_str,
                        file_size_kb=round(size_kb, 1),
                        status_summary=f"READY ({count:,} real candles, {start_str} to {end_str})",
                    )
                )
            except Exception as e:
                records.append(
                    InstrumentAuditRecord(
                        symbol=sym,
                        timeframe="1h",
                        available=False,
                        path=str(csv_file),
                        candle_count=0,
                        start_date=None,
                        end_date=None,
                        file_size_kb=0.0,
                        status_summary=f"CORRUPT/UNREADABLE: {e}",
                    )
                )
        else:
            records.append(
                InstrumentAuditRecord(
                    symbol=sym,
                    timeframe="1h",
                    available=False,
                    path=str(csv_file),
                    candle_count=0,
                    start_date=None,
                    end_date=None,
                    file_size_kb=0.0,
                    status_summary="NOT_AVAILABLE (No canonical 2026.csv present in repo)",
                )
            )

    return records


def format_four_instrument_report(records: List[InstrumentAuditRecord]) -> str:
    """Formats the audit table for reports."""
    lines = [
        "| Symbol | Timeframe | Available | Candles | Historical Date Range | File Size | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        avail_str = "✅ YES" if r.available else "❌ NO"
        date_range = f"{r.start_date} → {r.end_date}" if r.available else "N/A"
        candles_str = f"{r.candle_count:,}" if r.available else "0"
        size_str = f"{r.file_size_kb:.1f} KB" if r.available else "0 KB"
        lines.append(
            f"| **{r.symbol}** | {r.timeframe} | {avail_str} | {candles_str} | {date_range} | {size_str} | `{r.status_summary}` |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    recs = audit_four_instruments()
    print("\nFour-Instrument Canonical Dataset Audit:")
    print(format_four_instrument_report(recs))
