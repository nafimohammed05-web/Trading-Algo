"""OANDA candle parsing/paging tests (offline, synthetic payloads)."""
from __future__ import annotations

import pandas as pd
import pytest

from fx_engine.data import oanda
from fx_engine.data.resample import BAR_COLUMNS


def _candle(t, bid=1.0800, ask=1.0801, complete=True, vol=7):
    b = {k: f"{bid:.5f}" for k in "ohlc"}
    a = {k: f"{ask:.5f}" for k in "ohlc"}
    return {"complete": complete, "volume": vol, "time": t, "bid": b, "ask": a}


def test_name_mapping():
    assert oanda.oanda_name("EURUSD") == "EUR_USD"
    assert oanda.oanda_name("XAUUSD") == "XAU_USD"
    with pytest.raises(ValueError):
        oanda.oanda_name("DXY")


def test_parse_columns_utc_spread_and_drops_incomplete():
    p = {"candles": [_candle("2024-01-08T00:00:00.000000000Z"),
                     _candle("2024-01-08T00:01:00.000000000Z", complete=False)]}
    df = oanda.parse_candles(p)
    assert list(df.columns) == BAR_COLUMNS and len(df) == 1
    assert str(df.index.tz) == "UTC"
    assert df["spread"].iloc[0] == pytest.approx(0.0001)
    assert df["mid_close"].iloc[0] == pytest.approx(1.08005)
    assert df["ticks"].iloc[0] == 7


def test_parse_empty():
    df = oanda.parse_candles({"candles": []})
    assert df.empty and list(df.columns) == BAR_COLUMNS


def test_url_has_ba_m1_and_no_token():
    u = oanda.candles_url("EURUSD", pd.Timestamp("2024-01-08", tz="UTC"))
    assert "price=BA" in u and "granularity=M1" in u and "EUR_USD" in u and "fxpractice" in u
    with pytest.raises(ValueError):
        oanda.candles_url("EURUSD", pd.Timestamp("2024-01-08", tz="UTC"), count=5001)


def test_missing_token_raises_without_network(monkeypatch):
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="OANDA_API_TOKEN"):
        oanda.fetch_chunk("EURUSD", pd.Timestamp("2024-01-08", tz="UTC"))


def test_fetch_range_pages_and_stops_at_end():
    times = pd.date_range("2024-01-08", periods=10, freq="1min", tz="UTC")

    def fake(inst, start, env="practice"):  # serves 4 candles per call from `start`
        sel = [t for t in times if t >= start][:4]
        return {"candles": [_candle(t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")) for t in sel]}

    df = oanda.fetch_range("EURUSD", times[0], times[7], pause=0, fetch=fake)
    assert list(df.index) == list(times[:7])
