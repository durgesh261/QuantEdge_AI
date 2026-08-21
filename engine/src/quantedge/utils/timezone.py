"""
Timezone utilities for QuantEdge AI.

Canonical Policy:
- Internal market data timestamps, storage, CSVs, and SMC calculations are STRICTLY in UTC.
- Asia/Kolkata (UTC+05:30) is used exclusively for user-facing display, logs, charts, and reporting.
- All conversions use the IANA timezone identifier 'Asia/Kolkata' via zoneinfo.ZoneInfo.
"""

from datetime import datetime, timezone
from typing import Union
from zoneinfo import ZoneInfo

UTC_TIMEZONE = timezone.utc
IST_TIMEZONE = ZoneInfo("Asia/Kolkata")
IST_OFFSET_STR = "+05:30"


def to_utc(dt_or_ts: Union[datetime, int, float, str]) -> datetime:
    """
    Ensure the datetime or timestamp is a timezone-aware UTC datetime.
    
    Args:
        dt_or_ts: A timestamp (seconds), ISO string, or datetime object.
        
    Returns:
        timezone-aware datetime in UTC.
    """
    if isinstance(dt_or_ts, (int, float)):
        return datetime.fromtimestamp(dt_or_ts, tz=UTC_TIMEZONE)
    elif isinstance(dt_or_ts, str):
        cleaned = dt_or_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC_TIMEZONE)
        return dt.astimezone(UTC_TIMEZONE)
    elif isinstance(dt_or_ts, datetime):
        if dt_or_ts.tzinfo is None:
            return dt_or_ts.replace(tzinfo=UTC_TIMEZONE)
        return dt_or_ts.astimezone(UTC_TIMEZONE)
    else:
        raise TypeError(f"Unsupported type for UTC conversion: {type(dt_or_ts)}")


def to_ist(dt_or_ts: Union[datetime, int, float, str]) -> datetime:
    """
    Convert a UTC datetime, timestamp, or ISO string to an Asia/Kolkata datetime.
    
    Args:
        dt_or_ts: A timestamp (seconds), ISO string, or datetime object.
        
    Returns:
        timezone-aware datetime in Asia/Kolkata timezone (UTC+05:30).
    """
    utc_dt = to_utc(dt_or_ts)
    return utc_dt.astimezone(IST_TIMEZONE)


def format_ist(
    dt_or_ts: Union[datetime, int, float, str],
    fmt: str = "%Y-%m-%d %H:%M:%S %Z",
) -> str:
    """
    Format a datetime or timestamp in Asia/Kolkata timezone for user-facing display.
    
    Args:
        dt_or_ts: A timestamp (seconds), ISO string, or datetime object.
        fmt: strftime format string (default: "%Y-%m-%d %H:%M:%S %Z").
        
    Returns:
        Formatted string in Asia/Kolkata timezone.
    """
    ist_dt = to_ist(dt_or_ts)
    return ist_dt.strftime(fmt)


def from_ist_to_utc(dt: datetime) -> datetime:
    """
    Convert an Asia/Kolkata datetime back to UTC.
    
    Args:
        dt: A datetime object (if naive, assumed to be in Asia/Kolkata).
        
    Returns:
        timezone-aware datetime in UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST_TIMEZONE)
    return dt.astimezone(UTC_TIMEZONE)
