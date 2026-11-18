"""Tick -> M1 -> higher timeframe bars (§4.1, §17.2 stage 3).

Bars are built for bid, ask and mid.  Columns:
    {bid,ask,mid}_{open,high,low,close}, ticks, spread
where ``spread`` = ask_close - bid_close.  The index is the bar START in UTC.
Structure is read from mid; fills/stops/costs from bid/ask (§4.3).
"""
from __future__ import annotations

import pandas as pd

from .timeutil import anchored_bar_start, require_utc

SIDES = ("bid", "ask", "mid")
OHLC = ("open", "high", "low", "close")
BAR_COLUMNS = [f"{s}_{c}" for s in SIDES for c in OHLC] + ["ticks", "spread"]

_FIXED_RULES = {"M1": "1min", "M5": "5min", "M15": "15min", "H1": "1h"}
_ANCHORED = ("H4", "D1", "W1")

_AGG = {f"{s}_open": "first" for s in SIDES}
_AGG.update({f"{s}_high": "max" for s in SIDES})
_AGG.update({f"{s}_low": "min" for s in SIDES})
_AGG.update({f"{s}_close": "last" for s in SIDES})
_AGG["ticks"] = "sum"


def ticks_to_m1(ticks: pd.DataFrame) -> pd.DataFrame:
    """Aggregate bid/ask ticks (UTC index, columns bid & ask) into M1 bars."""
    require_utc(ticks.index)
    if not ticks.index.is_monotonic_increasing:
        raise ValueError("ticks must be sorted by time")
    bid, ask = ticks["bid"], ticks["ask"]
    mid = (bid + ask) / 2.0
    parts = []
    for name, series in (("bid", bid), ("ask", ask), ("mid", mid)):
        o = series.resample("1min", label="left", closed="left").ohlc()
        o.columns = [f"{name}_{c}" for c in OHLC]
        parts.append(o)
    m1 = pd.concat(parts, axis=1)
    m1["ticks"] = bid.resample("1min", label="left", closed="left").count()
    m1 = m1[m1["ticks"] > 0].copy()
    m1["spread"] = m1["ask_close"] - m1["bid_close"]
    return m1[BAR_COLUMNS]


def resample_bars(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample M1 bars to ``tf`` (M1, M5, M15, H1, H4, D1, W1)."""
    require_utc(m1.index)
    if tf == "M1":
        return m1.copy()
    if tf in _FIXED_RULES:
        out = m1.resample(_FIXED_RULES[tf], label="left", closed="left").agg(_AGG)
    elif tf in _ANCHORED:
        key = anchored_bar_start(m1.index, tf)
        out = m1.groupby(key).agg(_AGG)
        out.index.name = m1.index.name
    else:
        raise ValueError(f"unknown timeframe {tf}")
    out = out[out["ticks"] > 0].copy()
    out["ticks"] = out["ticks"].astype("int64")
    out["spread"] = out["ask_close"] - out["bid_close"]
    return out[BAR_COLUMNS]


def build_all(ticks: pd.DataFrame,
              timeframes=("M1", "M5", "M15", "H1", "H4", "D1", "W1")) -> dict[str, pd.DataFrame]:
    m1 = ticks_to_m1(ticks)
    return {tf: resample_bars(m1, tf) for tf in timeframes}
