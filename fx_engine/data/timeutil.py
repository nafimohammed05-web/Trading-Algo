"""Time handling for the data layer.

Raw data is stored in UTC and converted to America/New_York on read (§4.1).
The daily boundary is the 17:00 New York rollover (§7.3, App. B.3), not midnight.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
ROLLOVER_HOUR = 17
_ROLLOVER = pd.Timedelta(hours=ROLLOVER_HOUR)


def require_utc(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Raise unless the index is a tz-aware UTC DatetimeIndex."""
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError("index must be a tz-aware DatetimeIndex (UTC)")
    if str(index.tz) != "UTC":
        raise ValueError(f"index must be UTC, got {index.tz}")
    return index


def to_ny(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """UTC -> America/New_York (DST-aware)."""
    return require_utc(index).tz_convert(NY)


def _shifted_wall(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Naive NY wall-clock time minus 17:00, so the 17:00 rollover becomes midnight."""
    return to_ny(index).tz_localize(None) - _ROLLOVER


def session_end_date(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Session label: the NY date on which the session CLOSES (17:00 -> 17:00).

    Sun 17:00 -> Mon 17:00 is the "Monday" session.  Naive, normalised dates.
    """
    return _shifted_wall(index).floor("D") + pd.Timedelta(days=1)


def minutes_into_session(index: pd.DatetimeIndex) -> np.ndarray:
    """Wall-clock minutes since the 17:00 NY rollover (0..1439)."""
    sh = _shifted_wall(index)
    return ((sh - sh.floor("D")) // pd.Timedelta(minutes=1)).to_numpy().astype(int)


def anchored_bar_start(index: pd.DatetimeIndex, tf: str) -> pd.DatetimeIndex:
    """UTC bar-start timestamps for the NY-anchored timeframes H4, D1, W1.

    * D1: 17:00 NY to 17:00 NY.
    * H4: 4-hour bins starting 17:00, 21:00, 01:00, 05:00, 09:00, 13:00 NY wall-clock.
    * W1: Sunday 17:00 NY to the next Sunday 17:00 NY.

    Bins are formed on wall-clock time, so on DST-change days a bin can contain
    one hour more or fewer than nominal.  On the fall-back day the repeated
    01:00-02:00 hour falls in the same H4 bin; its start (01:00) is ambiguous and
    resolved to the first (EDT) occurrence.
    """
    sh = _shifted_wall(index)
    if tf == "D1":
        start = sh.floor("D")
    elif tf == "H4":
        start = sh.floor("4h")
    elif tf == "W1":
        days_since_sunday = (sh.day_of_week + 1) % 7
        start = sh.floor("D") - pd.to_timedelta(days_since_sunday, unit="D")
    else:
        raise ValueError(f"not an anchored timeframe: {tf}")
    wall = start + _ROLLOVER
    local = wall.tz_localize(NY, ambiguous=np.ones(len(wall), dtype=bool),
                             nonexistent="shift_forward")
    return local.tz_convert("UTC")


def dst_transition_dates(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """NY calendar dates (naive) in [start, end] on which the UTC offset changes."""
    days = pd.date_range(pd.Timestamp(start).tz_localize(None).normalize(),
                         pd.Timestamp(end).tz_localize(None).normalize(), freq="D")
    out = []
    for d in days:
        a = d.tz_localize(NY, nonexistent="shift_forward").utcoffset()
        b = (d + pd.Timedelta(days=1)).tz_localize(NY, nonexistent="shift_forward").utcoffset()
        if a != b:
            out.append(d)
    return out
