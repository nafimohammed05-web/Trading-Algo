"""Source-agnostic dataset build (§17.2 stages 2-6).

Takes M1 bar frames from ANY source (tick-derived or broker M1 bid/ask candles) and:
QC -> write all timeframes + s_norm to the immutable store -> optional synthetic-DXY
reference check -> manifest.  The manifest is written only if every instrument passes QC
and (when a DXY reference is supplied) the DXY check passes.

Layout under ``root``::

    <INST>/<TF>/bars.parquet        (M1, M5, M15, H1, H4, D1, W1)
    <INST>/snorm.parquet            (s_norm table: session x 5-min bucket)
    DXY/<TF>/bars.parquet           (synthetic index, mid close only, if built)
    qc/<INST>.json                  (QC report)
    manifest.json
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import dxy as dxymod
from .instruments import DXY_EXPONENTS, TIMEFRAMES
from .manifest import build_manifest, write_manifest
from .qc import QCReport, QCThresholds, TickQC, qc_m1
from .resample import resample_bars
from .snorm import compute_snorm
from .store import sha256_file, write_immutable


def _write_qc(root: Path, report: QCReport) -> None:
    p = Path(root) / "qc" / f"{report.instrument}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report.to_dict(), indent=2, default=str))


def build_dataset(root: Path, m1_by_instrument: dict[str, pd.DataFrame], sources: dict[str, str],
                  tick_qc: dict[str, TickQC] | None = None, thresholds: QCThresholds | None = None,
                  dxy_reference: pd.Series | None = None, timeframes=TIMEFRAMES) -> dict:
    """Build the dataset.  Returns the manifest dict (written to root/manifest.json if all checks pass)."""
    root = Path(root)
    tick_qc = tick_qc or {}
    checksums: dict[str, str] = {}
    entries: dict[str, dict] = {}

    def rel(p: Path) -> str:
        return p.relative_to(root).as_posix()

    for inst, m1 in m1_by_instrument.items():
        report = qc_m1(m1, inst, tick_qc.get(inst), thresholds)
        _write_qc(root, report)
        counts: dict[str, int] = {}
        if report.passed:  # a dataset that fails QC is not used (§17.2) - nothing is written for it
            for tf in timeframes:
                bars = resample_bars(m1, tf)
                p = root / inst / tf / "bars.parquet"
                checksums[rel(p)] = write_immutable(bars, p)
                counts[tf] = len(bars)
            sn = compute_snorm(m1["spread"])
            p = root / inst / "snorm.parquet"
            checksums[rel(p)] = write_immutable(sn, p)
        entries[inst] = {
            "start": report.start, "end": report.end, "bars_per_timeframe": counts,
            "qc_passed": report.passed, "qc_failures": report.failures,
        }

    dxy_info: dict = {}
    if set(DXY_EXPONENTS) <= set(m1_by_instrument) and all(entries[k]["qc_passed"] for k in DXY_EXPONENTS):
        syn = dxymod.synthetic_dxy({k: m1_by_instrument[k]["mid_close"] for k in DXY_EXPONENTS})
        dxy_info["built"] = True
        if dxy_reference is not None:
            chk = dxymod.check_against_reference(syn.resample("1D").last(), dxy_reference.resample("1D").last())
            dxy_info["reference_check"] = chk.to_dict()
            dxy_info["passed"] = chk.passed
            if chk.passed:
                p = root / "DXY" / "M1" / "bars.parquet"
                checksums[rel(p)] = write_immutable(syn.dropna().to_frame("mid_close"), p)
        else:
            dxy_info["passed"] = False
            dxy_info["note"] = "no reference series supplied; synthetic index not validated, not stored"
    else:
        dxy_info = {"built": False, "passed": False, "note": "DXY components missing or failed QC"}

    manifest = build_manifest(entries, checksums, sources, dxy_info)
    manifest["all_checks_passed"] = bool(manifest["all_qc_passed"] and dxy_info.get("passed"))
    manifest["all_qc_passed"] = manifest["all_checks_passed"]  # write_manifest gates on this
    if manifest["all_checks_passed"]:
        write_manifest(manifest, root / "manifest.json")
    return manifest
