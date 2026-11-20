"""Interim DXY stand-in check (NOT the spec's real-DXY check).

Builds a synthetic dollar index from Federal Reserve H.10 daily NOON NEW YORK buying rates
(independent source, Board website, public domain: "cite the Board as the source") and compares it,
day by day, with the synthetic index built from OANDA M1 mid at 12:00 America/New_York.
This validates the OANDA data + the §17.2 formula implementation against an independent source.
It does not show the synthetic index matches the real ICE DXY.

    python scripts/h10_check.py <H10_data.xml> [store_dir_or_raw_dir]
Source file: https://www.federalreserve.gov/datadownload/Output.aspx?rel=H10&filetype=zip
Citation: Board of Governors of the Federal Reserve System, H.10 Foreign Exchange Rates.
"""
from __future__ import annotations

import glob
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from fx_engine.data.dxy import CORR_MIN, LEVEL_MAE_MAX  # noqa: E402
from fx_engine.data.instruments import DXY_CONSTANT, DXY_EXPONENTS  # noqa: E402
from fx_engine.data.store import read_verified  # noqa: E402

# H.10 code -> (our pair, quote is USD per unit?)
H10 = {"EU": ("EURUSD", True), "UK": ("GBPUSD", True), "JA": ("USDJPY", False),
       "CA": ("USDCAD", False), "SD": ("USDSEK", False), "SZ": ("USDCHF", False)}
NS = "{http://www.federalreserve.gov/structure/compact/common}"


def parse_h10(path: str) -> pd.DataFrame:
    out = {}
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag.endswith("}Series"):
            name = el.attrib.get("SERIES_NAME", "")
            if name.startswith("RXI") and "_N.B." in name and name.split(".")[-1] in H10:
                code = name.split(".")[-1]
                rows = {}
                for o in el.iter(NS + "Obs"):
                    v = o.attrib.get("OBS_VALUE")
                    if v not in (None, "", "ND") and float(v) > 0 and o.attrib.get("OBS_STATUS", "A") == "A":  # -9999 = missing
                        rows[pd.Timestamp(o.attrib["TIME_PERIOD"])] = float(v)
                pair, usd_per_unit = H10[code]
                s = pd.Series(rows, name=pair).sort_index()
                out[pair] = s  # EU/UK series are USD per unit (EURUSD, GBPUSD); the rest foreign per USD - both match our pair quotes
            el.clear()
    return pd.DataFrame(out)


def dxy(df: pd.DataFrame) -> pd.Series:
    v = DXY_CONSTANT * pd.Series(1.0, index=df.index)
    for k, e in DXY_EXPONENTS.items():
        v = v * df[k] ** e
    return v


def oanda_noon() -> pd.DataFrame:
    cols = {}
    for pair in DXY_EXPONENTS:
        m = pd.concat([read_verified(f) for f in sorted(glob.glob(str(ROOT / "data/raw" / pair / "M1" / "*.parquet")))]).sort_index()
        ny = m.index.tz_convert("America/New_York")
        sel = m[(ny.hour == 12) & (ny.minute == 0)]
        s = sel["mid_close"].copy()  # bar 12:00-12:01 closing mid; nearest M1 to the noon fix
        s.index = pd.DatetimeIndex(s.index.tz_convert("America/New_York").tz_localize(None).normalize())
        cols[pair] = s
    return pd.DataFrame(cols)


if __name__ == "__main__":
    h = parse_h10(sys.argv[1])
    o = oanda_noon()
    both = h.add_suffix("_h10").join(o.add_suffix("_oanda"), how="inner").dropna()
    a = dxy(both[[c + "_h10" for c in DXY_EXPONENTS]].rename(columns=lambda c: c[:-4]))
    b = dxy(both[[c + "_oanda" for c in DXY_EXPONENTS]].rename(columns=lambda c: c[:-6]))
    ra, rb = np.log(a).diff().dropna(), np.log(b).diff().dropna()
    res = {
        "days": int(len(both)), "start": str(both.index.min().date()), "end": str(both.index.max().date()),
        "return_corr": float(ra.corr(rb)),
        "level_mae": float((a - b).abs().mean()), "level_max_abs_diff": float((a - b).abs().max()),
        "mean_level_h10": float(a.mean()), "mean_level_oanda": float(b.mean()),
        "corr_min_spec": CORR_MIN, "level_mae_max_placeholder": LEVEL_MAE_MAX,
    }
    res["passed_standin"] = bool(res["return_corr"] >= CORR_MIN and res["level_mae"] <= LEVEL_MAE_MAX)
    # worst days for inspection
    d = (a - b).abs().sort_values(ascending=False).head(5)
    res["worst_days"] = {str(k.date()): round(float(v), 4) for k, v in d.items()}
    print(json.dumps(res, indent=2))
