"""Synthetic dollar index and its validity check (§17.2, §6.8).

DXY ~= 50.14348112 x EURUSD^-0.576 x USDJPY^0.136 x GBPUSD^-0.119
                    x USDCAD^0.091 x USDSEK^0.042 x USDCHF^0.036

The dollar-index SOURCE (licensed feed vs this synthetic) is an open decision
(§17.6).  Whatever is used must pass the reference check before any decision
relies on it, and every dollar-dependent gate fails closed otherwise (§6.8).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .instruments import DXY_CONSTANT, DXY_EXPONENTS

# Spec §17.2: "return correlation of at least 0.99".
CORR_MIN = 0.99
# Spec says only "a small mean absolute level difference".  The number is an ENGINEERING
# choice (index points), to be confirmed.
LEVEL_MAE_MAX = 0.5


def synthetic_dxy(components: dict[str, pd.Series], max_stale_bars: int = 2) -> pd.Series:
    """Build the synthetic index from component prices (use mids at a common timeframe).

    ``components`` maps each of the six pair names to a price Series on a UTC index.
    A component may be forward-filled for at most ``max_stale_bars`` bars (§6.8: "stale
    by more than two signal bars" fails closed); beyond that the output is NaN.
    """
    missing = set(DXY_EXPONENTS) - set(components)
    if missing:
        raise ValueError(f"missing DXY components: {sorted(missing)}")
    frame = pd.concat({k: components[k] for k in DXY_EXPONENTS}, axis=1).sort_index()
    frame = frame.ffill(limit=max_stale_bars)
    if (frame <= 0).any().any():
        raise ValueError("non-positive component price")
    logv = np.log(DXY_CONSTANT) + sum(e * np.log(frame[k]) for k, e in DXY_EXPONENTS.items())
    out = np.exp(logv)
    out.name = "dxy_synthetic"
    return out


@dataclass
class DxyCheck:
    n_overlap: int
    return_corr: float
    level_mae: float
    corr_min: float
    level_mae_max: float
    passed: bool

    def to_dict(self) -> dict:
        return asdict(self)


def check_against_reference(synthetic: pd.Series, reference: pd.Series,
                            corr_min: float = CORR_MIN,
                            level_mae_max: float = LEVEL_MAE_MAX) -> DxyCheck:
    """Compare the synthetic series with a reference copy of the real index."""
    both = pd.concat({"s": synthetic, "r": reference}, axis=1).dropna()
    if len(both) < 3:
        return DxyCheck(len(both), float("nan"), float("nan"), corr_min, level_mae_max, False)
    ret = np.log(both).diff().dropna()
    corr = float(ret["s"].corr(ret["r"]))
    mae = float((both["s"] - both["r"]).abs().mean())
    passed = bool(np.isfinite(corr) and np.isfinite(mae) and corr >= corr_min and mae <= level_mae_max)
    return DxyCheck(len(both), corr, mae, corr_min, level_mae_max, passed)
