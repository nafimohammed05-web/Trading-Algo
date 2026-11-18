"""Dukascopy tick downloader (§17.2) - CANDIDATE source, not yet cleared for use.

STATUS: the decoder is unit-tested against synthetic bytes only.  Nothing here has
been run against the real server.  Before any download:
  1. Read and record Dukascopy's data usage terms (open decision, §17.6).
  2. Verify with ONE hour of one instrument that the format assumptions hold:
     - URL layout (month is 0-indexed),
     - 20-byte big-endian records: ms offset, ask, bid (uint32 each), ask vol, bid vol (float32),
     - integer price scaling (instruments.DUKASCOPY_POINT_VALUE),
     - that bid and ask are both present (the spec lists this as unverified).

Data are stored decoded, one parquet file per UTC day, in the immutable raw store:
    <root>/<INST>/tick/YYYY/MM/DD.parquet
"""
from __future__ import annotations

import lzma
import struct
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .instruments import DUKASCOPY_POINT_VALUE, DUKASCOPY_POINT_VALUE_DEFAULT
from .store import write_immutable

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
_REC = struct.Struct(">IIIff")  # ms_offset, ask, bid, ask_vol, bid_vol


def hour_url(instrument: str, hour_start: datetime) -> str:
    """URL of the hourly tick file.  Dukascopy months are 0-indexed (January = 00)."""
    h = hour_start.astimezone(timezone.utc)
    return f"{BASE_URL}/{instrument}/{h.year:04d}/{h.month - 1:02d}/{h.day:02d}/{h.hour:02d}h_ticks.bi5"


def decode_bi5(raw: bytes, hour_start: datetime, instrument: str) -> pd.DataFrame:
    """Decode one hourly .bi5 payload into a tick frame (UTC index; bid, ask, bid_vol, ask_vol)."""
    cols = ["bid", "ask", "bid_vol", "ask_vol"]
    if not raw:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], tz="UTC", name="time"), dtype="float64")
    data = lzma.decompress(raw)
    if len(data) % _REC.size:
        raise ValueError(f"corrupt tick file: {len(data)} bytes is not a multiple of {_REC.size}")
    arr = np.frombuffer(data, dtype=np.dtype([("ms", ">u4"), ("ask", ">u4"), ("bid", ">u4"),
                                              ("av", ">f4"), ("bv", ">f4")]))
    pv = DUKASCOPY_POINT_VALUE.get(instrument, DUKASCOPY_POINT_VALUE_DEFAULT)
    base = pd.Timestamp(hour_start.astimezone(timezone.utc))
    idx = pd.DatetimeIndex(base + pd.to_timedelta(arr["ms"].astype("int64"), unit="ms"), name="time")
    return pd.DataFrame({"bid": arr["bid"] / pv, "ask": arr["ask"] / pv,
                         "bid_vol": arr["bv"].astype("float64"), "ask_vol": arr["av"].astype("float64")},
                        index=idx)


def fetch_hour(instrument: str, hour_start: datetime, retries: int = 3, pause: float = 1.0,
               timeout: float = 30.0) -> bytes:
    """Fetch one hourly file.  404 -> b"" (no ticks, e.g. weekend); other errors retry then raise."""
    url = hour_url(instrument, hour_start)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fx-engine-data-client"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(pause * (2 ** attempt))
    raise RuntimeError(f"failed to fetch {url}") from last


def day_path(root: Path, instrument: str, day: date) -> Path:
    return Path(root) / instrument / "tick" / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}.parquet"


def download_day(root: Path, instrument: str, day: date, pause: float = 0.2) -> Path | None:
    """Download one UTC day into the immutable store.  Skips days already stored.  NOT YET RUN."""
    path = day_path(root, instrument, day)
    if path.exists():
        return path
    frames = []
    for h in range(24):
        hs = datetime(day.year, day.month, day.day, h, tzinfo=timezone.utc)
        frames.append(decode_bi5(fetch_hour(instrument, hs), hs, instrument))
        time.sleep(pause)
    ticks = pd.concat(frames) if frames else frames
    if ticks.empty:
        return None  # whole day empty (weekend / holiday): nothing stored
    write_immutable(ticks.sort_index(), path)
    return path


def download_range(root: Path, instrument: str, start: date, end: date) -> list[Path]:
    out, d = [], start
    while d <= end:
        p = download_day(root, instrument, d)
        if p:
            out.append(p)
        d += timedelta(days=1)
    return out
