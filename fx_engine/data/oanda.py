"""OANDA v20 M1 bid/ask candle source (§17.2) - the chosen data source.

Endpoint: GET {host}/v3/instruments/{INST}/candles?price=BA&granularity=M1&from=...&count=5000
Hosts: api-fxpractice.oanda.com (practice).  Auth: Bearer token from the environment
(OANDA_API_TOKEN) - never logged, never put in an error message (§17.7).

Caveats (record, do not hide):
  * Candles are built from OANDA's own quotes: venue-consistent with M6 paper trading.
  * ``price=BA`` returns bid and ask OHLC only.  Mid OHLC here is the AVERAGE of the bid and ask
    OHLC components, an approximation of true mid OHLC (exact for open/close, not for high/low).
  * ``ticks`` = the candle ``volume`` (tick count).  Minutes with no quotes have no candle.
  * M1 history depth for 5 years is UNVERIFIED until the one-hour/one-month smoke run.
  * Incomplete (still-forming) candles are dropped.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from .resample import BAR_COLUMNS

HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}
MAX_COUNT = 5000
_OHLC = ("o", "h", "l", "c")
_NAMES = ("open", "high", "low", "close")


def oanda_name(instrument: str) -> str:
    """EURUSD -> EUR_USD, XAUUSD -> XAU_USD."""
    if len(instrument) != 6:
        raise ValueError(f"unexpected instrument {instrument!r}")
    return f"{instrument[:3]}_{instrument[3:]}"


def parse_candles(payload: dict) -> pd.DataFrame:
    """Parse one candles response into an M1 frame (UTC index, resample.BAR_COLUMNS)."""
    rows, times = [], []
    for c in payload.get("candles", []):
        if not c.get("complete", False):
            continue
        row = {}
        for side in ("bid", "ask"):
            for k, name in zip(_OHLC, _NAMES):
                row[f"{side}_{name}"] = float(c[side][k])
        for name in _NAMES:
            row[f"mid_{name}"] = (row[f"bid_{name}"] + row[f"ask_{name}"]) / 2.0
        row["ticks"] = int(c["volume"])
        times.append(pd.Timestamp(c["time"]))
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([], tz="UTC", name="time"))
    idx = pd.DatetimeIndex(times, name="time").tz_convert("UTC").floor("min")
    df = pd.DataFrame(rows, index=idx)
    df["spread"] = df["ask_close"] - df["bid_close"]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[BAR_COLUMNS]


def candles_url(instrument: str, start: pd.Timestamp, count: int = MAX_COUNT, env: str = "practice") -> str:
    if not 1 <= count <= MAX_COUNT:
        raise ValueError("count must be 1..5000")
    q = urllib.parse.urlencode({"price": "BA", "granularity": "M1", "count": count,
                                "from": pd.Timestamp(start).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.000000000Z")})
    return f"{HOSTS[env]}/v3/instruments/{oanda_name(instrument)}/candles?{q}"


def _token() -> str:
    tok = os.environ.get("OANDA_API_TOKEN", "")
    if not tok or tok.startswith("your-"):
        raise RuntimeError("OANDA_API_TOKEN is not set (put it in .env / the environment; never commit it)")
    return tok


def fetch_chunk(instrument: str, start: pd.Timestamp, env: str = "practice", retries: int = 3,
                pause: float = 1.0, timeout: float = 30.0) -> dict:
    url = candles_url(instrument, start, env=env)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}",
                                               "Accept-Datetime-Format": "RFC3339"})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404):  # not retryable; message deliberately omits headers
                raise RuntimeError(f"OANDA request rejected: HTTP {e.code} for {instrument}") from None
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(pause * (2 ** attempt))
    raise RuntimeError(f"OANDA fetch failed for {instrument}") from None


def fetch_range(instrument: str, start: pd.Timestamp, end: pd.Timestamp, env: str = "practice",
                pause: float = 0.1, fetch=fetch_chunk) -> pd.DataFrame:
    """Page M1 candles from ``start`` up to (excluding) ``end``.  ``fetch`` is injectable for tests."""
    start, end = pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")
    frames, cur = [], start
    while cur < end:
        df = parse_candles(fetch(instrument, cur, env=env))
        if df.empty:
            # no candles in this window (e.g. weekend): jump ahead one chunk's worth of minutes
            cur += pd.Timedelta(minutes=MAX_COUNT)
            continue
        frames.append(df[df.index < end])
        nxt = df.index.max() + pd.Timedelta(minutes=1)
        if nxt <= cur:
            raise RuntimeError("OANDA paging made no progress")
        cur = nxt
        time.sleep(pause)
    if not frames:
        return parse_candles({})
    out = pd.concat(frames)
    return out[~out.index.duplicated(keep="last")].sort_index()
