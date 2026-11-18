"""Pipeline and download-guard tests (offline, synthetic)."""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pytest

from fx_engine.data import dukascopy, pipeline, resample
from fx_engine.data.instruments import DXY_EXPONENTS
from fx_engine.data.store import read_verified
from tests.test_data import make_ticks

BASES = {"EURUSD": 1.08, "USDJPY": 150.0, "GBPUSD": 1.27, "USDCAD": 1.35, "USDSEK": 10.5, "USDCHF": 0.88}


def _m1s(minutes=4 * 1440, drop=None):
    out = {}
    for i, (k, base) in enumerate(BASES.items()):
        m1 = resample.ticks_to_m1(make_ticks("2024-01-08 00:00", minutes, per_min=1, seed=i, base=base,
                                             spread=base * 1e-4))
        if drop and k == drop:
            m1 = m1.drop(m1.index[500:700])  # 200 missing minutes -> QC failure
        out[k] = m1
    return out


def _reference(m1s):
    from fx_engine.data import dxy
    syn = dxy.synthetic_dxy({k: m1s[k]["mid_close"] for k in DXY_EXPONENTS})
    rng = np.random.default_rng(5)
    return syn * (1 + rng.normal(0, 1e-7, len(syn)))


class TestPipeline:
    def test_full_build_writes_everything_and_manifest(self, tmp_path):
        m1s = _m1s()
        man = pipeline.build_dataset(tmp_path, m1s, {k: "synthetic" for k in m1s}, dxy_reference=_reference(m1s))
        assert man["all_checks_passed"], man
        assert (tmp_path / "manifest.json").exists()
        for tf in ("M1", "M5", "M15", "H1", "H4", "D1", "W1"):
            assert (tmp_path / "EURUSD" / tf / "bars.parquet").exists()
        assert (tmp_path / "EURUSD" / "snorm.parquet").exists()
        assert (tmp_path / "DXY" / "M1" / "bars.parquet").exists()
        assert (tmp_path / "qc" / "EURUSD.json").exists()
        # every stored file is in the manifest and verifies
        on_disk = json.loads((tmp_path / "manifest.json").read_text())
        assert on_disk["file_count"] == len(list(tmp_path.rglob("*.parquet")))
        read_verified(tmp_path / "EURUSD" / "D1" / "bars.parquet")

    def test_no_reference_means_no_manifest(self, tmp_path):
        m1s = _m1s()
        man = pipeline.build_dataset(tmp_path, m1s, {})
        assert not man["all_checks_passed"] and not (tmp_path / "manifest.json").exists()
        assert not (tmp_path / "DXY").exists()  # unvalidated synthetic index is never stored

    def test_failed_qc_instrument_is_not_written(self, tmp_path):
        m1s = _m1s(drop="USDCHF")
        man = pipeline.build_dataset(tmp_path, m1s, {}, dxy_reference=_reference(_m1s()))
        assert not man["instruments"]["USDCHF"]["qc_passed"]
        assert not (tmp_path / "USDCHF").exists()
        assert (tmp_path / "qc" / "USDCHF.json").exists()  # the failure is still reported
        assert not (tmp_path / "manifest.json").exists()
        assert not man["dxy"]["built"]  # a component failed QC -> no synthetic index

    def test_rebuild_into_same_root_refuses_to_overwrite(self, tmp_path):
        m1s = _m1s()
        ref = _reference(m1s)
        pipeline.build_dataset(tmp_path, m1s, {}, dxy_reference=ref)
        with pytest.raises(FileExistsError):
            pipeline.build_dataset(tmp_path, m1s, {}, dxy_reference=ref)


class TestDownloadGuard:
    def test_download_day_refuses_without_clearance(self, tmp_path):
        with pytest.raises(PermissionError, match="terms"):
            dukascopy.download_day(tmp_path, "EURUSD", date(2024, 1, 5))

    def test_download_range_refuses_without_clearance(self, tmp_path):
        with pytest.raises(PermissionError):
            dukascopy.download_range(tmp_path, "EURUSD", date(2024, 1, 5), date(2024, 1, 6))
        assert not any(tmp_path.iterdir())  # nothing created, no network touched
