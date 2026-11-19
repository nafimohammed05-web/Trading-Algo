"""Data quality control (§17.2 stage 2).  A dataset that fails QC is not used.

Checks required by the spec: missing bars and gaps by session, duplicate
timestamps, ask below bid, spread outliers, DST transition days, weekend and
holiday handling, abnormal jumps.

The numeric pass/fail thresholds below are ENGINEERING PLACEHOLDERS (the spec
gives none).  They are not §14 parameters; confirm them before relying on a verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from .timeutil import dst_transition_dates, require_utc, to_ny


@dataclass
class QCThresholds:
    gap_minutes: int = 15            # a run of missing M1 minutes this long counts as a gap
    max_missing_frac: float = 0.03   # fail if > 3% of expected open-market minutes are missing (OANDA omits no-quote minutes; March 2024 EURUSD was 0.97%)
    max_crossed_frac: float = 1e-4   # fail if ask<bid ticks exceed this fraction
    spread_outlier_mult: float = 10  # M1 spread > mult x median spread -> outlier (informational)
    jump_mult: float = 20            # |M1 return| > mult x robust sigma -> jump (informational)


@dataclass
class TickQC:
    """Mergeable per-chunk tick statistics (compute per day; ticks are too big to hold at once)."""
    n: int = 0
    duplicates: int = 0
    non_positive: int = 0
    ask_below_bid: int = 0
    unsorted: int = 0

    def merge(self, other: "TickQC") -> "TickQC":
        return TickQC(*(getattr(self, f) + getattr(other, f) for f in ("n", "duplicates", "non_positive", "ask_below_bid", "unsorted")))


def qc_ticks(ticks: pd.DataFrame) -> TickQC:
    require_utc(ticks.index)
    return TickQC(
        n=len(ticks),
        duplicates=int(ticks.reset_index().duplicated().sum()),
        non_positive=int(((ticks["bid"] <= 0) | (ticks["ask"] <= 0)).sum()),
        ask_below_bid=int((ticks["ask"] < ticks["bid"]).sum()),
        unsorted=int((ticks.index.to_series().diff().dropna() < pd.Timedelta(0)).sum()),
    )


def _market_open_mask(index_utc: pd.DatetimeIndex) -> np.ndarray:
    """True when the FX market is nominally open: NOT between Fri 17:00 and Sun 17:00 NY."""
    ny = to_ny(index_utc)
    dow, hour = ny.day_of_week, ny.hour
    closed = ((dow == 4) & (hour >= 17)) | (dow == 5) | ((dow == 6) & (hour < 17))
    return ~np.asarray(closed)


@dataclass
class QCReport:
    instrument: str
    start: str | None
    end: str | None
    m1_bars: int
    expected_minutes: int
    missing_minutes: int
    missing_frac: float
    gaps: int
    longest_gap_minutes: int
    gap_sessions: list[str] = field(default_factory=list)
    ticks: dict = field(default_factory=dict)
    spread_median: float = float("nan")
    spread_p99: float = float("nan")
    spread_outliers: int = 0
    jumps: int = 0
    dst_days_in_range: list[str] = field(default_factory=list)
    dst_days_with_data: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        d = asdict(self)
        d["passed"] = self.passed
        return d


def qc_m1(m1: pd.DataFrame, instrument: str, tick_qc: TickQC | None = None,
          th: QCThresholds | None = None) -> QCReport:
    """QC an M1 bar frame (columns from resample.BAR_COLUMNS)."""
    th = th or QCThresholds()
    require_utc(m1.index)
    if m1.empty:
        return QCReport(instrument, None, None, 0, 0, 0, 0.0, 0, 0, failures=["no data"])

    full = pd.date_range(m1.index.min(), m1.index.max(), freq="1min")
    expected_idx = full[_market_open_mask(full)]
    missing_idx = expected_idx.difference(m1.index)
    # Gaps: runs of consecutive missing minutes of at least gap_minutes.
    gaps, longest, gap_days = 0, 0, set()
    if len(missing_idx):
        run_id = (missing_idx.to_series().diff() != pd.Timedelta(minutes=1)).cumsum().to_numpy()
        for rid in np.unique(run_id):
            run = missing_idx[run_id == rid]
            longest = max(longest, len(run))
            if len(run) >= th.gap_minutes:
                gaps += 1
                gap_days.add(str(to_ny(run[:1])[0].date()))

    spread = m1["spread"]
    med = float(spread.median())
    mid = m1["mid_close"]
    r = np.log(mid).diff().dropna()
    sigma = 1.4826 * float((r - r.median()).abs().median())
    jumps = int((r.abs() > th.jump_mult * sigma).sum()) if sigma > 0 else 0

    dst_all = dst_transition_dates(m1.index.min(), m1.index.max())
    ny_dates = set(to_ny(m1.index).date)
    dst_with = [d for d in dst_all if d.date() in ny_dates]

    rep = QCReport(
        instrument=instrument,
        start=str(m1.index.min()), end=str(m1.index.max()),
        m1_bars=len(m1), expected_minutes=len(expected_idx),
        missing_minutes=len(missing_idx),
        missing_frac=len(missing_idx) / max(len(expected_idx), 1),
        gaps=gaps, longest_gap_minutes=longest, gap_sessions=sorted(gap_days),
        ticks=asdict(tick_qc) if tick_qc else {},
        spread_median=med, spread_p99=float(spread.quantile(0.99)),
        spread_outliers=int((spread > th.spread_outlier_mult * med).sum()) if med > 0 else 0,
        jumps=jumps,
        dst_days_in_range=[str(d.date()) for d in dst_all],
        dst_days_with_data=[str(d.date()) for d in dst_with],
    )
    if rep.missing_frac > th.max_missing_frac:
        rep.failures.append(f"missing_frac {rep.missing_frac:.4f} > {th.max_missing_frac}")
    if (spread < 0).any():
        rep.failures.append("negative M1 spread (ask_close < bid_close)")
    if tick_qc and tick_qc.n:
        if tick_qc.non_positive:
            rep.failures.append(f"{tick_qc.non_positive} non-positive prices")
        if tick_qc.unsorted:
            rep.failures.append(f"{tick_qc.unsorted} out-of-order ticks")
        if tick_qc.ask_below_bid / tick_qc.n > th.max_crossed_frac:
            rep.failures.append(f"crossed quotes {tick_qc.ask_below_bid}/{tick_qc.n} exceed {th.max_crossed_frac}")
    missing_dst = sorted(set(rep.dst_days_in_range) - set(rep.dst_days_with_data))
    if missing_dst:
        rep.failures.append(f"no data on DST transition days: {missing_dst}")
    return rep
