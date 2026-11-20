# FX Decision Engine v2.0

A rules-based FX trading algorithm built on the ICT Decision Engine methodology.

## What it does

Evaluates FX majors + XAU/USD through a 7-stage cascade on every bar close.
No discretion, no leverage, no overnight positions.

## Build milestones

| Milestone | Description | Status |
|-----------|-------------|--------|
| M0 | Skeleton, typed config, governance guard | ✅ Done |
| M1 | Data layer — OANDA M1 bid/ask, raw store, QC, resampler, s_norm, synthetic DXY, manifest | ✅ Done (real-DXY check owed before M5) |
| M2 | Primitives — swings, ATR, pools, displacement, session clock | 🔜 Next |
| M3 | Full cascade engine — all 7 stages, 10 gates, rejection log | |
| M4 | Backtester — bid/ask fills, all §15.4 reports | |
| M5 | Calibration + spread sensitivity validation | |
| M6 | Paper trading — OANDA fxTrade Practice | |
| M7 | Live gating — minimum size, 3-month observation block | |

## Quick start

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Environment variables

Copy `.env.example` to `.env` and fill in your API keys. Never commit `.env`.

## Spec

The full methodology is in `claude/decision-engine-methodology-v2.md` (project docs).
All 43 parameters are in `fx_engine/config/params.py`.

## Progress (2026-11-20)

- M0 and M1 complete; 123 tests passing on Python 3.12.
- Data: 5 years (Nov 2021 to Oct 2026) of OANDA M1 bid/ask for the 8 traded instruments plus USDSEK; QC clean; manifest written. Data files are local only (gitignored).
- Owed: the spec's real-DXY reference check (the stand-in against Fed H.10 passed with return correlation 0.9989) is due before M5.
- Measured median EURUSD spread (02:00-17:00 NY, 5y, practice quotes) is about 1.5 pips, above the third-party ~0.9 pip claim.
