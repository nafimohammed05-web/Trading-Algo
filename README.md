# FX Decision Engine v2.0

A rules-based FX trading algorithm built on the ICT Decision Engine methodology.

## What it does

Evaluates FX majors + XAU/USD through a 7-stage cascade on every bar close.
No discretion, no leverage, no overnight positions.

## Build milestones

| Milestone | Description | Status |
|-----------|-------------|--------|
| M0 | Skeleton, typed config, governance guard | ✅ Done |
| M1 | Data layer — Dukascopy downloader, raw store, resampler | 🔜 |
| M2 | Primitives — swings, ATR, pools, displacement, session clock | |
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
All 43 parameters are in `config/params.py`.
