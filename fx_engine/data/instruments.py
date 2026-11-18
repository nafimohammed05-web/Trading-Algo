"""Instrument universe and data constants (spec Table 2.1, §4.1, §17.2)."""
from __future__ import annotations

# The 8 traded instruments.  XAUUSD is LONG ONLY (C5) - a trading rule, not a data rule.
TRADED: tuple[str, ...] = (
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "USDCAD", "NZDUSD",
    "XAUUSD",
)

# Read-only reference series - never traded.  USDSEK exists only to build the synthetic DXY.
REFERENCE: tuple[str, ...] = ("DXY", "USDSEK")

# §4.1 timeframes.
TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15", "H1", "H4", "D1", "W1")

# DXY basket exponents (§17.2).  EURUSD / GBPUSD enter with negative exponents.
DXY_CONSTANT: float = 50.14348112
DXY_EXPONENTS: dict[str, float] = {
    "EURUSD": -0.576,
    "USDJPY": 0.136,
    "GBPUSD": -0.119,
    "USDCAD": 0.091,
    "USDSEK": 0.042,
    "USDCHF": 0.036,
}

# Dukascopy integer price scaling ("point value").  UNVERIFIED against real files -
# confirm with a single-hour smoke test before any bulk download.
DUKASCOPY_POINT_VALUE: dict[str, int] = {
    "USDJPY": 1_000,
    "XAUUSD": 1_000,
}
DUKASCOPY_POINT_VALUE_DEFAULT: int = 100_000
