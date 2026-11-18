"""Session-normal spread model s_norm (§4.4).

s_norm(t) = median spread within the same five-minute time-of-day bucket over the
trailing 20 sessions, recomputed daily.  A thin or event-distorted bucket falls back
to the median of its two neighbouring buckets.

Design notes
------------
* Sessions run 17:00 NY -> 17:00 NY and are labelled by closing date (timeutil).
* Buckets are 5-minute slots of NY wall-clock time since the 17:00 rollover (0..287).
* The value for session s uses ONLY sessions s-20 .. s-1 (no look-ahead): it is known
  at the session open.
* Pooled median: all M1 spreads from the 20 sessions in the bucket's 5 minutes.
* Fails closed: a bucket without enough history or observations is NaN, never guessed.
* "Event-distorted" buckets need the economic calendar (M2).  ``distorted`` lets the
  caller mask them (session_date, bucket) so they take the neighbour fallback.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .timeutil import minutes_into_session, session_end_date

BUCKET_MINUTES = 5
N_BUCKETS = 24 * 60 // BUCKET_MINUTES  # 288
_SLOTS = 10  # M1 values per bucket; 25-hour (fall-back) sessions can hold up to 10


def session_and_bucket(index: pd.DatetimeIndex) -> tuple[pd.DatetimeIndex, np.ndarray]:
    return session_end_date(index), minutes_into_session(index) // BUCKET_MINUTES


def compute_snorm(
    spread: pd.Series,
    window: int = 20,
    min_sessions: int = 20,
    min_obs: int = 20,
    distorted: set[tuple[pd.Timestamp, int]] | None = None,
) -> pd.DataFrame:
    """Return a DataFrame (index = session date, columns = bucket 0..287) of s_norm."""
    sessions, bucket = session_and_bucket(spread.index)
    df = pd.DataFrame({"s": sessions, "b": bucket, "v": spread.to_numpy(dtype=float)})
    df = df[np.isfinite(df["v"])]
    df["slot"] = df.groupby(["s", "b"]).cumcount()
    df = df[df["slot"] < _SLOTS]

    uniq = np.array(sorted(df["s"].unique()))
    pos = {s: i for i, s in enumerate(uniq)}
    arr = np.full((len(uniq), N_BUCKETS, _SLOTS), np.nan)
    arr[df["s"].map(pos).to_numpy(), df["b"].to_numpy(), df["slot"].to_numpy()] = df["v"].to_numpy()

    table = np.full((len(uniq), N_BUCKETS), np.nan)
    for i in range(len(uniq)):
        lo = max(0, i - window)
        if i - lo < min_sessions:
            continue  # not enough history -> stays NaN (fail closed)
        win = arr[lo:i]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            med = np.nanmedian(win, axis=(0, 2))
        cnt = np.sum(~np.isnan(win), axis=(0, 2))
        med[cnt < min_obs] = np.nan
        if distorted:
            for (d, b) in distorted:
                if d == uniq[i]:
                    med[b] = np.nan
        table[i] = med

    # Neighbour fallback: a thin/distorted bucket takes the median of its two neighbours.
    filled = table.copy()
    thin = np.isnan(table)
    left = np.roll(table, 1, axis=1)
    right = np.roll(table, -1, axis=1)
    left[:, 0] = np.nan
    right[:, -1] = np.nan
    ok = thin & np.isfinite(left) & np.isfinite(right)
    filled[ok] = (left[ok] + right[ok]) / 2.0  # median of two values == their mean
    # Sessions that had no usable history at all stay NaN (the fallback requires both neighbours).
    return pd.DataFrame(filled, index=pd.DatetimeIndex(uniq, name="session"),
                        columns=pd.RangeIndex(N_BUCKETS, name="bucket"))


def lookup(table: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """s_norm for each UTC timestamp in ``index`` (NaN if the session/bucket is unknown)."""
    sessions, bucket = session_and_bucket(index)
    rows = table.index.get_indexer(sessions)
    vals = np.full(len(index), np.nan)
    good = rows >= 0
    vals[good] = table.to_numpy()[rows[good], bucket[good]]
    return pd.Series(vals, index=index, name="s_norm")
