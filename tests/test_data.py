"""M1 data-layer tests (offline: synthetic data only, no network)."""
from __future__ import annotations

import lzma
import struct
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from fx_engine.data import dxy, dukascopy, manifest, qc, resample, snorm, store, timeutil
from fx_engine.data.instruments import DXY_EXPONENTS, REFERENCE, TIMEFRAMES, TRADED


def utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def make_ticks(start: str, minutes: int, per_min: int = 4, seed: int = 0, base: float = 1.1,
               spread: float = 0.0001) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = minutes * per_min
    t0 = utc(start)
    idx = t0 + pd.to_timedelta(np.arange(n) * (60_000 // per_min), unit="ms")
    mid = base + np.cumsum(rng.normal(0, 1e-5, n))
    df = pd.DataFrame({"bid": mid - spread / 2, "ask": mid + spread / 2}, index=pd.DatetimeIndex(idx, name="time"))
    return df


# --------------------------------------------------------------------------- universe
class TestUniverse:
    def test_eight_traded_instruments(self):
        assert set(TRADED) == {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "XAUUSD"}
        assert "GBPJPY" not in TRADED

    def test_usdsek_is_reference_only(self):
        assert "USDSEK" in REFERENCE and "USDSEK" not in TRADED
        assert "USDSEK" in DXY_EXPONENTS

    def test_timeframes_include_m15(self):
        assert TIMEFRAMES == ("M1", "M5", "M15", "H1", "H4", "D1", "W1")


# --------------------------------------------------------------------------- time
class TestTime:
    def test_requires_utc(self):
        with pytest.raises(ValueError):
            timeutil.require_utc(pd.DatetimeIndex(["2024-01-08"]))
        with pytest.raises(ValueError):
            timeutil.require_utc(pd.DatetimeIndex(["2024-01-08"], tz="America/New_York"))

    def test_d1_boundary_is_17_ny_winter(self):
        idx = pd.DatetimeIndex([utc("2024-01-08 21:59"), utc("2024-01-08 22:00")])
        start = timeutil.anchored_bar_start(idx, "D1")
        assert start[0] == utc("2024-01-07 22:00")  # 16:59 NY belongs to the session opened Sun 17:00
        assert start[1] == utc("2024-01-08 22:00")  # 17:00 NY opens a new session

    def test_d1_boundary_is_17_ny_summer(self):
        idx = pd.DatetimeIndex([utc("2024-07-08 20:59"), utc("2024-07-08 21:00")])
        start = timeutil.anchored_bar_start(idx, "D1")
        assert start[0] == utc("2024-07-07 21:00")
        assert start[1] == utc("2024-07-08 21:00")

    def test_w1_starts_sunday_17_ny(self):
        idx = pd.DatetimeIndex([utc("2024-01-10 12:00"), utc("2024-01-07 22:00"), utc("2024-01-12 21:59")])
        start = timeutil.anchored_bar_start(idx, "W1")
        assert (start == utc("2024-01-07 22:00")).all()

    def test_h4_boundaries(self):
        idx = pd.DatetimeIndex([utc("2024-01-08 14:00"), utc("2024-01-08 10:30"), utc("2024-01-08 22:00")])
        start = timeutil.anchored_bar_start(idx, "H4")
        assert list(start) == [utc("2024-01-08 14:00"), utc("2024-01-08 10:00"), utc("2024-01-08 22:00")]

    def test_h4_fall_back_day_repeated_hour_one_bin(self):
        idx = pd.DatetimeIndex([utc("2024-11-03 05:30"), utc("2024-11-03 06:30"), utc("2024-11-03 10:00")])
        start = timeutil.anchored_bar_start(idx, "H4")
        assert start[0] == start[1] == utc("2024-11-03 05:00")
        assert start[2] == utc("2024-11-03 10:00")

    def test_session_end_date_label(self):
        idx = pd.DatetimeIndex([utc("2024-01-07 22:00"), utc("2024-01-08 21:59"), utc("2024-01-08 22:00")])
        lab = timeutil.session_end_date(idx)
        assert list(lab) == [pd.Timestamp("2024-01-08")] * 2 + [pd.Timestamp("2024-01-09")]

    def test_dst_transition_dates_2024(self):
        d = timeutil.dst_transition_dates(utc("2024-01-01"), utc("2024-12-31"))
        assert [x.strftime("%Y-%m-%d") for x in d] == ["2024-03-10", "2024-11-03"]


# --------------------------------------------------------------------------- resample
class TestResample:
    def setup_method(self):
        # Sunday 22:00Z (17:00 NY) for 3 days: crosses D1 boundaries.
        self.ticks = make_ticks("2024-01-07 22:00", 3 * 1440)
        self.m1 = resample.ticks_to_m1(self.ticks)

    def test_m1_columns_and_spread(self):
        assert list(self.m1.columns) == resample.BAR_COLUMNS
        assert np.allclose(self.m1["spread"], self.m1["ask_close"] - self.m1["bid_close"])
        assert np.allclose(self.m1["spread"], 0.0001)

    def test_mid_is_average_of_ticks(self):
        first = self.ticks.iloc[0]
        assert self.m1["mid_open"].iloc[0] == pytest.approx((first["bid"] + first["ask"]) / 2)

    @pytest.mark.parametrize("tf,rule", [("M5", "5min"), ("M15", "15min"), ("H1", "1h")])
    def test_resampled_from_m1_equals_direct_from_ticks(self, tf, rule):
        got = resample.resample_bars(self.m1, tf)
        for side, series in (("bid", self.ticks["bid"]), ("ask", self.ticks["ask"]),
                             ("mid", (self.ticks["bid"] + self.ticks["ask"]) / 2)):
            direct = series.resample(rule, label="left", closed="left").ohlc()
            direct = direct.loc[got.index]
            for c in resample.OHLC:
                assert np.allclose(got[f"{side}_{c}"], direct[c]), (tf, side, c)

    @pytest.mark.parametrize("tf", ["M5", "M15", "H1", "H4", "D1", "W1"])
    def test_tick_count_conserved_and_ohlc_sane(self, tf):
        b = resample.resample_bars(self.m1, tf)
        assert b["ticks"].sum() == len(self.ticks)
        for s in resample.SIDES:
            assert (b[f"{s}_high"] >= b[[f"{s}_open", f"{s}_close"]].max(axis=1) - 1e-12).all()
            assert (b[f"{s}_low"] <= b[[f"{s}_open", f"{s}_close"]].min(axis=1) + 1e-12).all()

    def test_d1_bars_start_at_17_ny(self):
        d1 = resample.resample_bars(self.m1, "D1")
        assert len(d1) == 3
        assert all(ts.hour == 22 for ts in d1.index)  # 17:00 EST == 22:00Z

    def test_m1_to_d1_equals_direct_ohlc(self):
        d1 = resample.resample_bars(self.m1, "D1")
        first_day = self.ticks[self.ticks.index < utc("2024-01-08 22:00")]
        assert d1["bid_open"].iloc[0] == first_day["bid"].iloc[0]
        assert d1["bid_close"].iloc[0] == first_day["bid"].iloc[-1]
        assert d1["bid_high"].iloc[0] == first_day["bid"].max()
        assert d1["bid_low"].iloc[0] == first_day["bid"].min()

    def test_unsorted_ticks_rejected(self):
        with pytest.raises(ValueError):
            resample.ticks_to_m1(self.ticks.iloc[::-1])

    def test_build_all_returns_every_timeframe(self):
        out = resample.build_all(self.ticks)
        assert tuple(out) == TIMEFRAMES


# --------------------------------------------------------------------------- store
class TestStore:
    def test_write_then_verified_read(self, tmp_path):
        df = make_ticks("2024-01-08 00:00", 5)
        p = tmp_path / "EURUSD" / "tick" / "2024" / "01" / "08.parquet"
        digest = store.write_immutable(df, p)
        assert len(digest) == 64
        back = store.read_verified(p)
        assert len(back) == len(df)

    def test_refuses_overwrite(self, tmp_path):
        df = make_ticks("2024-01-08 00:00", 5)
        p = tmp_path / "a.parquet"
        store.write_immutable(df, p)
        with pytest.raises(FileExistsError):
            store.write_immutable(df, p)

    def test_detects_tampering(self, tmp_path):
        df = make_ticks("2024-01-08 00:00", 5)
        p = tmp_path / "a.parquet"
        store.write_immutable(df, p)
        with open(p, "ab") as f:
            f.write(b"x")
        with pytest.raises(ValueError, match="checksum mismatch"):
            store.read_verified(p)

    def test_no_temp_file_left(self, tmp_path):
        p = tmp_path / "a.parquet"
        store.write_immutable(make_ticks("2024-01-08 00:00", 5), p)
        assert not list(tmp_path.glob("*.tmp"))


# --------------------------------------------------------------------------- s_norm
class TestSNorm:
    def _spread(self, sessions=30, value=0.0001, drop_bucket=None):
        idx = pd.date_range("2024-01-01 22:00", periods=sessions * 1440, freq="1min", tz="UTC")
        s = pd.Series(value, index=idx)
        if drop_bucket is not None:
            _, b = snorm.session_and_bucket(idx)
            s = s[b != drop_bucket]
        return s

    def test_constant_spread_gives_constant_snorm_after_20_sessions(self):
        t = snorm.compute_snorm(self._spread())
        assert t.shape[1] == 288
        assert t.iloc[:20].isna().all().all()  # not enough history -> fail closed
        assert np.allclose(t.iloc[20:].to_numpy(), 0.0001)

    def test_no_lookahead_own_session_excluded(self):
        s = self._spread()
        t0 = snorm.compute_snorm(s)
        s2 = s.copy()
        s2.iloc[-1440:] = 0.5  # last session becomes huge
        t1 = snorm.compute_snorm(s2)
        assert np.allclose(t0.to_numpy(), t1.to_numpy(), equal_nan=True)  # its own value uses earlier sessions only

    def test_uses_median_not_mean(self):
        s = self._spread()
        s.iloc[5000] = 1.0  # a single wild spread in history
        t = snorm.compute_snorm(s)
        assert np.allclose(t.iloc[-1].dropna().to_numpy(), 0.0001)

    def test_thin_bucket_falls_back_to_neighbours(self):
        s = self._spread(drop_bucket=100)
        t = snorm.compute_snorm(s)
        assert np.allclose(t.iloc[25][100], 0.0001)  # filled from buckets 99 and 101

    def test_thin_bucket_without_neighbours_is_nan(self):
        s = self._spread()
        _, b = snorm.session_and_bucket(s.index)
        s = s[~np.isin(b, [99, 100, 101])]
        t = snorm.compute_snorm(s)
        assert np.isnan(t.iloc[25][100])  # both neighbours also empty -> fail closed

    def test_lookup_by_timestamp(self):
        s = self._spread()
        t = snorm.compute_snorm(s)
        q = pd.DatetimeIndex([utc("2024-01-25 10:07"), utc("2024-01-02 10:07")])
        v = snorm.lookup(t, q)
        assert v.iloc[0] == pytest.approx(0.0001)
        assert np.isnan(v.iloc[1])  # early session: no history yet


# --------------------------------------------------------------------------- DXY
class TestDxy:
    PRICES = {"EURUSD": 1.08, "USDJPY": 150.0, "GBPUSD": 1.27, "USDCAD": 1.35, "USDSEK": 10.5, "USDCHF": 0.88}

    def _components(self, n=50, noise=0.0, seed=0):
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2024-01-08", periods=n, freq="5min", tz="UTC")
        out = {}
        for k, p in self.PRICES.items():
            out[k] = pd.Series(p * np.exp(np.cumsum(rng.normal(0, noise, n))), index=idx)
        return out

    def test_formula_matches_manual_calculation(self):
        comp = {k: pd.Series([p], index=pd.DatetimeIndex([utc("2024-01-08")])) for k, p in self.PRICES.items()}
        got = dxy.synthetic_dxy(comp).iloc[0]
        expect = 50.14348112
        for k, e in DXY_EXPONENTS.items():
            expect *= self.PRICES[k] ** e
        assert got == pytest.approx(expect)
        assert 95 < got < 115  # plausible index level

    def test_missing_component_raises(self):
        comp = self._components()
        del comp["USDSEK"]
        with pytest.raises(ValueError, match="USDSEK"):
            dxy.synthetic_dxy(comp)

    def test_stale_component_fails_closed(self):
        comp = self._components()
        comp["USDSEK"] = comp["USDSEK"].copy()
        comp["USDSEK"].iloc[10:15] = np.nan  # 5 missing bars
        out = dxy.synthetic_dxy(comp, max_stale_bars=2)
        assert out.iloc[10] == out.iloc[10] and out.iloc[11] == out.iloc[11]  # forward-filled <= 2 bars
        assert np.isnan(out.iloc[12:15]).all()  # then NaN

    def test_reference_check_passes_for_near_copy(self):
        syn = dxy.synthetic_dxy(self._components(noise=1e-3))
        rng = np.random.default_rng(1)
        ref = syn * (1 + rng.normal(0, 1e-5, len(syn)))
        chk = dxy.check_against_reference(syn, ref)
        assert chk.passed and chk.return_corr >= 0.99

    def test_reference_check_fails_for_uncorrelated(self):
        syn = dxy.synthetic_dxy(self._components(noise=1e-3, seed=1))
        ref = dxy.synthetic_dxy(self._components(noise=1e-3, seed=2))
        assert not dxy.check_against_reference(syn, ref).passed

    def test_reference_check_fails_on_level_offset(self):
        syn = dxy.synthetic_dxy(self._components(noise=1e-3))
        assert not dxy.check_against_reference(syn, syn + 5.0).passed  # perfect correlation, wrong level

    def test_reference_check_fails_without_overlap(self):
        syn = dxy.synthetic_dxy(self._components())
        ref = pd.Series([100.0], index=pd.DatetimeIndex([utc("2030-01-01")]))
        assert not dxy.check_against_reference(syn, ref).passed


# --------------------------------------------------------------------------- QC
class TestQC:
    def _m1(self, start="2024-01-09 08:00", minutes=600, **kw):
        return resample.ticks_to_m1(make_ticks(start, minutes, per_min=2, **kw))

    def test_clean_data_passes(self):
        rep = qc.qc_m1(self._m1(), "EURUSD", qc.qc_ticks(make_ticks("2024-01-09 08:00", 600, per_min=2)))
        assert rep.passed, rep.failures
        assert rep.missing_minutes == 0 and rep.gaps == 0

    def test_gap_detected_and_fails_when_large(self):
        m1 = self._m1()
        m1 = m1.drop(m1.index[100:160])  # 60 missing minutes
        rep = qc.qc_m1(m1, "EURUSD", th=qc.QCThresholds(max_missing_frac=0.01))
        assert rep.gaps == 1 and rep.longest_gap_minutes == 60
        assert any("missing_frac" in f for f in rep.failures)
        assert not rep.passed

    def test_weekend_is_not_a_gap(self):
        # Fri 21:00-21:59Z (16:00-16:59 NY) then Sun 22:00Z (17:00 NY) onwards.
        a = self._m1("2024-01-12 21:00", 60)
        b = self._m1("2024-01-14 22:00", 60, seed=1)
        rep = qc.qc_m1(pd.concat([a, b]), "EURUSD")
        assert rep.missing_minutes == 0, rep.missing_minutes
        assert rep.gaps == 0

    def test_crossed_quotes_fail(self):
        ticks = make_ticks("2024-01-09 08:00", 600, per_min=2)
        ticks.iloc[::50, ticks.columns.get_loc("ask")] = ticks["bid"].iloc[::50] - 0.0002  # ask < bid
        tq = qc.qc_ticks(ticks)
        assert tq.ask_below_bid > 0
        rep = qc.qc_m1(resample.ticks_to_m1(ticks), "EURUSD", tq)
        assert any("crossed" in f for f in rep.failures)

    def test_non_positive_price_fails(self):
        ticks = make_ticks("2024-01-09 08:00", 60, per_min=2)
        ticks.iloc[3, ticks.columns.get_loc("bid")] = 0.0
        tq = qc.qc_ticks(ticks)
        assert tq.non_positive == 1
        rep = qc.qc_m1(resample.ticks_to_m1(ticks), "EURUSD", tq)
        assert any("non-positive" in f for f in rep.failures)

    def test_duplicates_counted(self):
        ticks = make_ticks("2024-01-09 08:00", 10, per_min=2)
        ticks = pd.concat([ticks, ticks.iloc[:3]]).sort_index(kind="stable")
        assert qc.qc_ticks(ticks).duplicates == 3

    def test_tickqc_merge(self):
        a, b = qc.TickQC(n=10, ask_below_bid=1), qc.TickQC(n=5, duplicates=2)
        m = a.merge(b)
        assert (m.n, m.ask_below_bid, m.duplicates) == (15, 1, 2)

    def test_dst_day_without_data_fails(self):
        a = self._m1("2024-03-08 12:00", 60)
        b = self._m1("2024-03-12 12:00", 60, seed=1)  # nothing on 2024-03-10
        rep = qc.qc_m1(pd.concat([a, b]), "EURUSD")
        assert "2024-03-10" in rep.dst_days_in_range and "2024-03-10" not in rep.dst_days_with_data
        assert any("DST" in f for f in rep.failures)

    def test_empty_fails(self):
        empty = resample.ticks_to_m1(make_ticks("2024-01-09 08:00", 1, per_min=1)).iloc[0:0]
        assert not qc.qc_m1(empty, "EURUSD").passed


# --------------------------------------------------------------------------- manifest
class TestManifest:
    def _inst(self, ok=True):
        return {"EURUSD": {"start": "a", "end": "b", "bars_per_timeframe": {"M1": 1}, "qc_passed": ok, "qc": {}}}

    def test_hash_is_order_independent(self):
        assert manifest.dataset_hash({"a": "1", "b": "2"}) == manifest.dataset_hash({"b": "2", "a": "1"})

    def test_hash_changes_with_content(self):
        assert manifest.dataset_hash({"a": "1"}) != manifest.dataset_hash({"a": "2"})

    def test_refuses_when_qc_failed(self, tmp_path):
        m = manifest.build_manifest(self._inst(ok=False), {"a": "1"}, {"EURUSD": "dukascopy"})
        with pytest.raises(ValueError, match="QC"):
            manifest.write_manifest(m, tmp_path / "manifest.json")
        assert not (tmp_path / "manifest.json").exists()

    def test_refuses_empty(self, tmp_path):
        m = manifest.build_manifest({}, {}, {})
        with pytest.raises(ValueError):
            manifest.write_manifest(m, tmp_path / "manifest.json")

    def test_atomic_write(self, tmp_path):
        m = manifest.build_manifest(self._inst(), {"a": "1"}, {"EURUSD": "dukascopy"})
        manifest.write_manifest(m, tmp_path / "d" / "manifest.json")
        assert (tmp_path / "d" / "manifest.json").exists()
        assert not list((tmp_path / "d").glob("*.tmp"))


# --------------------------------------------------------------------------- dukascopy decoder (offline)
class TestDukascopyDecode:
    HOUR = datetime(2024, 1, 15, 10, tzinfo=timezone.utc)

    def _payload(self, recs):
        raw = b"".join(struct.pack(">IIIff", *r) for r in recs)
        return lzma.compress(raw)

    def test_url_month_is_zero_indexed(self):
        assert dukascopy.hour_url("EURUSD", self.HOUR).endswith("/EURUSD/2024/00/15/10h_ticks.bi5")

    def test_decode_scaling_and_time(self):
        df = dukascopy.decode_bi5(self._payload([(500, 108_345, 108_343, 1.5, 2.5), (1500, 108_346, 108_344, 1.0, 1.0)]),
                                  self.HOUR, "EURUSD")
        assert df["ask"].iloc[0] == pytest.approx(1.08345) and df["bid"].iloc[0] == pytest.approx(1.08343)
        assert df.index[0] == utc("2024-01-15 10:00:00.500")
        assert df.index[1] == utc("2024-01-15 10:00:01.500")
        assert (df["ask"] >= df["bid"]).all()

    def test_jpy_uses_1000_point_value(self):
        df = dukascopy.decode_bi5(self._payload([(0, 148_505, 148_503, 1.0, 1.0)]), self.HOUR, "USDJPY")
        assert df["ask"].iloc[0] == pytest.approx(148.505)

    def test_empty_payload_gives_empty_frame(self):
        df = dukascopy.decode_bi5(b"", self.HOUR, "EURUSD")
        assert df.empty and str(df.index.tz) == "UTC"

    def test_corrupt_length_raises(self):
        with pytest.raises(ValueError, match="corrupt"):
            dukascopy.decode_bi5(lzma.compress(b"\x00" * 21), self.HOUR, "EURUSD")

    def test_day_path_layout(self, tmp_path):
        from datetime import date
        p = dukascopy.day_path(tmp_path, "EURUSD", date(2024, 1, 5))
        assert p.parts[-4:] == ("tick", "2024", "01", "05.parquet")


class TestQCSessionAndGoldBreak:
    def test_gold_daily_break_not_counted_missing(self):
        m1 = resample.ticks_to_m1(make_ticks("2024-01-08 00:00", 4 * 1440, per_min=1, seed=1))
        ny = timeutil.to_ny(m1.index)
        m1 = m1[~((ny.hour == 17) & (ny.day_of_week < 4))]  # drop the 17:00-18:00 NY hour Mon-Thu
        assert qc.qc_m1(m1, "XAUUSD").missing_minutes == 0
        assert qc.qc_m1(m1, "EURUSD").missing_minutes > 0

    def test_rollover_hour_gaps_do_not_fail_session_check(self):
        m1 = resample.ticks_to_m1(make_ticks("2024-01-08 00:00", 4 * 1440, per_min=1, seed=1))
        ny = timeutil.to_ny(m1.index)
        m1 = m1[~((ny.hour == 17) & (ny.day_of_week < 4))]
        r = qc.qc_m1(m1, "EURUSD")
        assert r.missing_frac > 0.03 and r.session_missing_frac == 0 and r.passed
