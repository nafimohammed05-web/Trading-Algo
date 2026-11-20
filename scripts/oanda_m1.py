"""Download OANDA M1 bid/ask into the immutable raw store, then build the dataset.

    python scripts/oanda_m1.py download [--start 2021-11] [--end 2026-11]   (end exclusive, YYYY-MM)
    python scripts/oanda_m1.py build

Resumable: one immutable parquet per instrument-month at data/raw/<INST>/M1/YYYY-MM.parquet;
months already stored are skipped.  Token is read from .env / the environment, never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from fx_engine.data import oanda, pipeline  # noqa: E402
from fx_engine.data.instruments import REFERENCE, TRADED  # noqa: E402
from fx_engine.data.store import read_verified, write_immutable  # noqa: E402

INSTRUMENTS = list(TRADED) + [r for r in REFERENCE if r != "DXY"]  # + USDSEK
RAW = ROOT / "data" / "raw"
STORE = ROOT / "data" / "store"


def load_env() -> None:
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_]+)=([^#\s]*)", line)
            if m and m.group(2):
                os.environ.setdefault(m.group(1), m.group(2))


def month_path(inst: str, month: pd.Period) -> Path:
    return RAW / inst / "M1" / f"{month}.parquet"


def download(start: str, end: str) -> None:
    months = pd.period_range(start, pd.Period(end) - 1, freq="M")
    for inst in INSTRUMENTS:
        for m in months:
            p = month_path(inst, m)
            if p.exists():
                continue
            t0 = pd.Timestamp(m.start_time, tz="UTC")
            t1 = pd.Timestamp((m + 1).start_time, tz="UTC")
            df = oanda.fetch_range(inst, t0, t1, pause=0.1)
            if df.empty:
                print(f"{inst} {m}: NO DATA", flush=True)
                continue
            write_immutable(df, p)
            print(f"{inst} {m}: {len(df)} bars", flush=True)


def build(store: Path = STORE, standin: dict | None = None) -> None:
    m1s = {}
    for inst in INSTRUMENTS:
        files = sorted((RAW / inst / "M1").glob("*.parquet"))
        if files:
            m1s[inst] = pd.concat([read_verified(f) for f in files]).sort_index()
    man = pipeline.build_dataset(store, m1s, {k: "oanda-v20-M1-BA" for k in m1s}, dxy_standin=standin)
    for k, v in man["instruments"].items():
        print(k, "QC", "PASS" if v["qc_passed"] else "FAIL", v["qc_failures"])
    print("dxy:", man["dxy"])
    print("manifest written:", man["all_checks_passed"])


if __name__ == "__main__":
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download", "build"])
    ap.add_argument("--start", default="2021-11")
    ap.add_argument("--end", default="2026-11")
    ap.add_argument("--store", default=str(STORE))
    ap.add_argument("--standin-json", default=None, help="JSON output of scripts/h10_check.py (interim DXY stand-in)")
    a = ap.parse_args()
    download(a.start, a.end) if a.cmd == "download" else build(Path(a.store), json.loads(Path(a.standin_json).read_text()) if a.standin_json else None)
