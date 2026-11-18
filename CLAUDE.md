# FX Decision Engine v2.0 — Claude Code Context

This file is the single source of truth for any Claude Code session continuing work on this repository. Read it in full before writing any code.

---

## Repository layout

```
trading-algo/
├── CLAUDE.md                    ← this file
├── README.md
├── pyproject.toml               ← pip install -e ".[dev]"
├── .env.example                 ← copy to .env, fill in, never commit
├── .gitignore
├── config/
│   ├── __init__.py
│   └── params.py                ← ALL 43 §14 parameters; EngineConfig dataclass
├── core/
│   ├── __init__.py
│   └── logging_setup.py         ← JsonFormatter, EngineLogger, setup_logging()
├── data/                        ← M1 target (gitignored: data/raw/, data/cache/)
├── engine/                      ← M2–M3 target (cascade stages)
├── backtest/                    ← M4 target
├── paper/                       ← M6 target (OANDA fxTrade Practice)
├── live/                        ← M7 target
└── tests/
    └── test_config.py           ← 44 M0 tests, all passing
```

---

## Build state

| Milestone | Description | Status |
|-----------|-------------|--------|
| M0 | Skeleton, typed config, governance guard | ✅ Done — 44/44 tests pass |
| M1 | Data layer — Dukascopy downloader, raw store, resampler | 🔜 Next |
| M2 | Primitives — swings, ATR, pools, displacement, session clock | |
| M3 | Full cascade engine — all 7 stages, 10 gates, rejection log | |
| M4 | Backtester — bid/ask fills, all §15.4 reports | |
| M5 | Calibration + spread sensitivity validation | |
| M6 | Paper trading — OANDA fxTrade Practice | |
| M7 | Live gating — minimum size, 3-month observation block | |

**Start here: M1.**

---

## M0 exit criterion (already met)

> "Config loads; setting a G value outside its range raises." — §17.3

`tests/test_config.py` has 44 tests across 5 classes:
- `TestDefaultConfig` — 7 tests: defaults, leverage=1.0, risk=1%, capital=£10k
- `TestGovernanceAssertions` — 29 tests: every G-tagged param raises `ConfigValidationError` with `[G]` in the message when set outside its governance range
- `TestFixedConstants` — 3 tests: `max_leverage`, `max_risk_pct` are `@property` — `EngineConfig(max_leverage=2.0)` raises `TypeError`
- `TestEParameterWarnings` — 2 tests: E-params warn, don't raise
- `TestConfigHash` — 3 tests: deterministic SHA-256[:16], changes with any value

---

## M1 exit criterion

> "5 years of M1/M5/H1/H4/D1/W1 bid/ask OHLCV downloaded, QC-checked, stored; resampler round-trips losslessly; spread series and s_norm computed; dataset manifest written." — §17.3

### What M1 must build

1. **Dukascopy downloader** (`data/dukascopy.py`)
   - Fetch bid/ask tick data for all 8 instruments (see instruments list below)
   - 5 years of history
   - Store as immutable Parquet files in `data/raw/` (gitignored)
   - QC report: gap count, spread statistics, start/end dates

2. **Resampler** (`data/resample.py`)
   - Input: raw tick store
   - Output: M1, M5, H1, H4, D1, W1 OHLCV bars (bid and ask separately)
   - Must round-trip losslessly (M5 resampled from M1 == M5 direct download)
   - Add `spread` column = ask_close − bid_close
   - Add `s_norm` column = spread / ATR(H1, period=14)

3. **DXY source** (`data/dxy.py`)
   - Try to fetch DXY index feed
   - Fallback: synthetic DXY from 6 component pairs weighted per official formula
   - Components: EUR/USD (57.6%), USD/JPY (13.6%), GBP/USD (11.9%), USD/CAD (9.1%), USD/SEK (4.2%), USD/CHF (3.6%)
   - Synthetic = geometric weighted product; store alongside instrument bars

4. **Dataset manifest** (`data/manifest.json`)
   - Per-instrument: start date, end date, bar count per timeframe, QC pass/fail
   - Written atomically after all downloads succeed

### Instruments

```python
INSTRUMENTS = [
    "EURUSD", "GBPUSD", "AUDUSD",   # USD quote pairs
    "USDJPY", "USDCAD", "USDCHF",   # USD base pairs
    "GBPJPY",                         # cross
    "XAUUSD",                         # gold
]
```

### Raw store layout

```
data/raw/
├── EURUSD/
│   ├── tick/          ← raw bid/ask ticks
│   ├── M1/            ← resampled OHLCV
│   ├── M5/
│   ├── H1/
│   ├── H4/
│   ├── D1/
│   └── W1/
├── GBPUSD/
│   └── ...
└── DXY/
    └── D1/            ← DXY daily bars
```

---

## The 43-parameter register (§14)

All parameters live in `config/params.py` as fields on `EngineConfig`.

### Tag meanings
- **[G] fixed** — `@property` on dataclass; `__init__` does not accept them → `TypeError` if passed
- **[G] governance** — assertion on construction; `ConfigValidationError` with `[G]` in message if violated; may be made STRICTER but NEVER LOOSER
- **[G] strict-only** — subset of [G]; must remain AT OR BELOW (or AT OR ABOVE) their default
- **[E] engineering** — `warnings.warn("[E] ...")` if outside range; not an error; tunable
- **[C] corpus** — pending empirical validation; treat as [E] until §18 corpus study done

### [G] fixed constants

| Name | Value | Governs |
|------|-------|---------|
| `MAX_RISK_PCT` | 0.01 | max drawdown per trade = 1% equity |
| `MAX_LEVERAGE` | 1.0 | notional ≤ equity at all times (C4) |
| `CAPITAL_BASE_GBP` | 10_000.0 | reference account size |

### [G] tunable governance rules

| Name | Default | Range | Governs |
|------|---------|-------|---------|
| `rho_kill` | 0.35 | 0.25–0.45 | G1 — kill correlation threshold |
| `max_stop_atr_h1` | 1.00 | 0.60–1.00 | G2 — max stop size as ATR multiple |
| `max_slippage` | 0.15 | 0.10–0.25 | G3 — max acceptable slippage (pips/pts) |
| `spread_gate_pct` | 0.10 | 0.05–0.10 | G10 — spread as % of stop (strict-only: smaller = stricter) |
| `spread_rel_max` | 1.50 | 1.25–1.50 | G10 — spread / calm_spread ratio cap (strict-only: smaller) |
| `spread_spike_mult` | 3.00 | 2.00–3.00 | G10 — spread spike multiplier (strict-only: smaller) |
| `spread_lock_min` | 10 | 10–30 | G10 — lockout minutes after spike (strict-only: larger = stricter) |
| `base_risk_pct` | 0.005 | 0.0025–0.0075 | G4 — base risk per trade |
| `min_rr` | 2.0 | 1.8–3.0 | G5 — minimum reward:risk |
| `daily_loss_limit` | 2.0 | 1.5–3.0 | G6 — daily drawdown % halt |
| `weekly_loss_limit` | 5.0 | 4.0–8.0 | G7 — weekly drawdown % halt |
| `max_positions` | 2 | 1–3 | G8 — max simultaneous open positions |
| `rho_portfolio_cap` | 0.70 | 0.60–0.80 | G9 — max portfolio pairwise correlation |

### [E] engineering defaults (tunable, warn if out of range)

| Name | Default | Range | Governs |
|------|---------|-------|---------|
| `swing_fractal_k` | 2 | 1–5 | fractal lookback for swing detection |
| `atr_period` | 14 | 10–21 | ATR calculation period |
| `req_gap_min` | 0.15 | 0.05–0.30 | min FVG gap as ATR fraction |
| `req_eq_tol` | 0.15 | 0.05–0.25 | equal-high/low tolerance |
| `inversion_conf` | 0.10 | 0.05–0.20 | inversion confirmation tolerance |
| `bias_threshold` | 3 | 2–5 | bias score threshold |
| `cisd_staleness` | 10 | 5–20 | bars before CISD level expires |
| `dol_asymmetry` | 0.75 | 0.60–0.90 | DOL asymmetry weight |
| `rho_min_smt` | 0.60 | 0.40–0.80 | min correlation for SMT divergence |
| `dxy_bias_threshold` | 3 | 2–5 | DXY bias score threshold |
| `dxy_agree_mode` | "strict" | "strict"/"permissive" | DXY agreement mode |
| `calm_spread_mult` | 1.5 | 1.0–2.5 | calm-period spread multiplier |
| `calm_range_mult` | 1.5 | 1.0–2.5 | calm-period range multiplier |
| `calm_bars` | 5 | 3–10 | lookback bars for calm baseline |
| `delta_min` | 0.05 | 0.01–0.15 | min displacement delta |
| `delta_max` | 0.75 | 0.50–1.00 | max displacement delta |
| `n_sweep` | 3 | 1–5 | bars to confirm liquidity sweep |
| `n_conf` | 12 | 6–24 | bars for CISD confirmation window |
| `disp_min_atr` | 1.5 | 0.75–3.0 | min displacement size in ATR |
| `eff_lrlr` | 0.50 | 0.30–0.70 | efficiency ratio LRLR threshold |
| `eff_hrlr` | 0.30 | 0.15–0.50 | efficiency ratio HRLR threshold |
| `stop_buffer` | 0.10 | 0.05–0.20 | stop ATR buffer fraction |
| `order_expiry` | 20 | 5–60 | limit order expiry (bars) |
| `sd_confluence_tol` | 0.20 | 0.10–0.40 | SD zone confluence tolerance |
| `score_min` | 60 | 50–80 | minimum conviction score to trade |
| `score_min_hrlr` | 80 | 70–90 | min score for HRLR setups |

### [C] corpus-asserted (treat as [E] until corpus study complete)

| Name | Default | Governs |
|------|---------|---------|
| `w_weekly_profile` | 0.0 | weight for weekly profile score component |

### Venue cost parameters ([E])

| Name | Default | Governs |
|------|---------|---------|
| `k_commission` | 0.0 | per-trade commission (pips) |
| `f_conv_price_units` | 0.0 | currency conversion factor |

### Session timing ([E] — NY time strings, 24h format)

| Name | Default | Governs |
|------|---------|---------|
| `t_london_open_start` | "02:00" | London open window start |
| `t_london_open_end` | "05:00" | London open window end |
| `t_london_sb_start` | "03:00" | London silver bullet start |
| `t_london_sb_end` | "04:00" | London silver bullet end |
| `t_ny_am_start` | "07:00" | NY AM window start |
| `t_ny_am_end` | "09:00" | NY AM window end |
| `t_ny_sb_start` | "08:00" | NY silver bullet start |
| `t_ny_sb_end` | "09:00" | NY silver bullet end |
| `t_manage_only` | "15:30" | no new entries after this |
| `t_hard_flat` | "16:55" | hard flatten — compliance (§2.5) |

---

## The 7-stage cascade (M3)

Every bar close runs all 8 instruments through this cascade in order. First gate failure = immediate rejection (logged to rejection log). No trade unless ALL 10 gates pass.

```
Stage 1 — Bias (§6)
  G1: rho_kill — correlation kill switch
  → bias_score per instrument (bearish/neutral/bullish)
  → DXY agreement check (dxy_agree_mode)

Stage 2 — Time (§7)
  G2 (implicit): only London Open / NY AM silver bullet windows
  → session_active boolean

Stage 3 — Liquidity Event / Sweep (§8)
  → swing highs/lows pool detection
  → sweep confirmation (n_sweep bars)

Stage 4 — Displacement / CISD (§9)
  → displacement ATR check (disp_min_atr)
  → CISD (Change In State of Delivery) detection
  → staleness check (cisd_staleness)

Stage 5 — Entry Array + Cost Gate (§10)
  G3: max_slippage
  G10: spread_gate_pct, spread_rel_max, spread_spike_mult, spread_lock_min
  → OB/FVG/IFVG entry confluence
  → stop placement (max_stop_atr_h1, stop_buffer)
  → R:R gate (min_rr)

Stage 6 — Conviction Score (§11)
  → score = weighted sum of confluence factors
  → score_min gate (or score_min_hrlr for HRLR setups)
  → w_weekly_profile weight (currently 0.0)

Stage 7 — Risk / Sizing / Exit (§12)
  G4: base_risk_pct
  G5: min_rr
  G6: daily_loss_limit
  G7: weekly_loss_limit
  G8: max_positions
  G9: rho_portfolio_cap
  → position size = (equity × base_risk_pct) / stop_distance
  → notional check: notional ≤ equity (MAX_LEVERAGE = 1.0)
  → partial exit at 1R (50%), trail remainder
```

### 10 hard gates

| Gate | Parameter | Stage | Description |
|------|-----------|-------|-------------|
| G1 | rho_kill | 1 | Correlation kill: if pairwise ρ > rho_kill, skip |
| G2 | — | 2 | Time window (London Open / NY AM only) |
| G3 | max_slippage | 5 | Slippage estimate ≤ max_slippage |
| G4 | base_risk_pct | 7 | Risk per trade bounded |
| G5 | min_rr | 5/7 | Minimum R:R ratio |
| G6 | daily_loss_limit | 7 | Daily drawdown halt |
| G7 | weekly_loss_limit | 7 | Weekly drawdown halt |
| G8 | max_positions | 7 | Portfolio concentration |
| G9 | rho_portfolio_cap | 7 | Portfolio correlation cap |
| G10 | spread_gate_pct / spread_rel_max / spread_spike_mult / spread_lock_min | 5 | Cost gate (new in v2.0) |

---

## Instrument state machine (per instrument, per bar)

```
DORMANT → ARMED → SWEPT → CONFIRMED → PENDING → IN_TRADE → MANAGING
                                                              ↓
                                                           HALTED
```

Transitions:
- DORMANT → ARMED: Stage 1 bias passes
- ARMED → SWEPT: Stage 3 sweep confirmed
- SWEPT → CONFIRMED: Stage 4 displacement + CISD
- CONFIRMED → PENDING: Stage 5 entry array found, limit placed
- PENDING → IN_TRADE: fill received
- IN_TRADE → MANAGING: 1R partial exit done
- Any → HALTED: G6/G7 hit, or compliance flat (16:55)
- HALTED → DORMANT: next trading day open

---

## Logging schema (§15.4)

Every log event is a JSON line with mandatory fields. See `core/logging_setup.py`.

Mandatory fields: `ts`, `event`, `instrument`, `timeframe`, `config_hash`, `bar_time`

Event types: `state_transition`, `rejection`, `trade_entry`, `trade_exit`, `scale_out`, `kill_switch`, `circuit_breaker`, `spread_lockout`, `cost_fault`, `execution_fault`, `compliance_flat`, `critical_fault`

**SECURITY**: API tokens and secrets must NEVER appear in log output. Callers are responsible for not passing credentials to any log call. (§17.7)

---

## One code path rule (§17.1)

> Backtest, paper, and live all run the SAME cascade code. Only the data adapters and execution adapters differ. Never fork the cascade logic.

```
data adapter → [cascade] → execution adapter
  ↑                              ↑
Dukascopy (M1/M4)         OANDA fxTrade (M6/M7)
CSV replay (M4)
```

---

## Security rules (§17.7) — NEVER VIOLATE

1. API tokens live in the **environment or a secrets store**, never in the repository, logs, or configuration files
2. `.env` is in `.gitignore` — never commit it
3. `.env.example` shows variable names with placeholder values — this IS committed
4. Never log credential values, even at DEBUG level
5. Never print credential values in error messages or stack traces

---

## Open decisions (§17.6)

| Item | Status | Notes |
|------|--------|-------|
| w_weekly_profile | Pending corpus study (§18) | Hardcoded 0.0 until validated |
| Dukascopy vs broker tick feed | Decision at M1 | Dukascopy preferred for history depth |
| Synthetic DXY vs index feed | Fallback at M1 | Build both; use feed if available |
| Score weighting | Pending backtester feedback | Current weights are §11 defaults |
| OANDA account currency | GBP preferred | Match CAPITAL_BASE_GBP |

---

## Environment setup

```bash
# Install
pip install -e ".[dev]"           # core + tests
pip install -e ".[dev,data]"      # + pandas/numpy/pyarrow for M1+

# Run tests
pytest tests/ -v

# Environment
cp .env.example .env
# edit .env with real values — NEVER commit .env
```

Python ≥ 3.11 required.

---

## Spec cross-references

The full methodology is in the project doc `claude/decision-engine-methodology-v2.md`. Key sections:

- §2 — Design constraints (C1–C9)
- §6 — Bias engine (Stage 1)
- §7 — Session clock (Stage 2)
- §8 — Liquidity / sweep detection (Stage 3)
- §9 — Displacement / CISD (Stage 4)
- §10 — Entry array + cost gate (Stage 5)
- §11 — Conviction score (Stage 6)
- §12 — Risk, sizing, exits (Stage 7)
- §14 — Full 43-parameter register
- §15 — Logging and reporting schema
- §17 — Build order and exit criteria (M0–M7)
- §17.1 — One code path rule
- §17.3 — M0 exit criterion (passed) and M1–M7 criteria
- §17.6 — Open decisions
- §17.7 — Secrets handling
- §18 — Corpus validation protocol (future)
