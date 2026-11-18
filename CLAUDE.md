# FX Decision Engine v2.0 — Claude Code Context

This file is the context for any Claude Code session continuing work on this repository. Read it in full before writing any code.

**Authority:** the methodology spec (`claude/decision-engine-methodology-v2.md` in the project docs, not in this repo; "Liquidity-Driven Intraday FX Execution Framework", v2.0, 18 Nov 2026) is authoritative. If this file and the spec disagree, the spec wins. Where spec prose and Appendix A pseudocode differ, Appendix A governs. The spec ends at §17 plus Appendices A–C.

---

## Repository layout

```
Trading-Algo/
├── CLAUDE.md                    ← this file
├── README.md
├── pyproject.toml               ← pip install -e ".[dev]"
├── .env.example                 ← copy to .env, fill in, never commit
├── .gitignore
├── fx_engine/                   ← the installable package (import fx_engine.*)
│   ├── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── params.py            ← ALL 43 §14 parameters; EngineConfig dataclass
│   ├── core/
│   │   ├── __init__.py
│   │   └── logging_setup.py     ← JsonFormatter, EngineLogger, setup_logging()
│   ├── adapters/                ← broker/data adapters (stub today)
│   ├── data/                    ← M1 target (data/raw/, data/cache/ are gitignored)
│   ├── engine/                  ← M2–M3 target (not yet created)
│   ├── backtest/                ← M4 target (not yet created)
│   ├── paper/                   ← M6 target (not yet created)
│   └── live/                    ← M7 target (not yet created)
└── tests/
    └── test_config.py           ← 44 M0 tests, all passing
```

Python target: **3.12** (CI also exercises 3.11; `requires-python >= 3.11`).

---

## Build state

| Milestone | Description (spec §17.3) | Status |
|-----------|--------------------------|--------|
| M0 | Skeleton, typed config from §14, G-parameter assertions, logging, CI, secrets handling | ✅ Done — 44/44 tests pass |
| M1 | Data layer — downloaders, raw store, QC report, resampling, spread series, s_norm, DXY source/synthetic index, dataset manifest | 🔧 In progress — offline code + tests done (see "M1 progress"); no real data downloaded yet |
| M2 | Primitives (§4.3) and time services: session clock, calendar integration, calm test, DST tests, 20 hand-labelled setups | |
| M3 | Bias (§6) and cascade (§8–§11) in the state machine, incl. G10, deferred placement, rejection log | |
| M4 | Backtester and cost model: simulated adapter, bid/ask fills, §15.4 reports, spread-sensitivity surface, null-model benchmark | |
| M5 | Calibration and validation V2–V4 with pre-registered arms (§15.6) | |
| M6 | Paper execution — OANDA fxTrade Practice adapter, reconciliation, heartbeat/watchdog, kill switch | |
| M7 | Live gating — governance review, scholarly guidance, V6 at 10% of permitted notional | |

Each milestone has an exit criterion that must be met before the next begins.

**Current work: finish M1 (see "M1 progress").**

---

## M0 exit criterion (already met)

> "Config loads; setting a G value outside its range raises." — §17.3

`tests/test_config.py` has 44 tests across 5 classes:
- `TestDefaultConfig` — defaults, leverage=1.0, risk ceiling=1%, capital=£10k
- `TestGovernanceAssertions` — every G-tagged param raises `ConfigValidationError` with `[G]` in the message when set outside its governance range
- `TestFixedConstants` — `max_leverage`, `max_risk_pct` are `@property`; `EngineConfig(max_leverage=2.0)` raises `TypeError`
- `TestEParameterWarnings` — E-params warn, don't raise
- `TestConfigHash` — deterministic SHA-256[:16], changes with any value

---

## M1 — Data layer

### Exit criterion (§17.3)

> "Five years of bid/ask for the trading universe in the store; QC clean; DXY reference check passed."

The DXY reference check is: if a synthetic dollar index is used, its **return correlation against a reference copy of the real index must be ≥ 0.99** over an overlapping period, with a small mean absolute level difference (§17.2). M1 is not done until this passes. If it fails, or component feeds are stale, every dollar-dependent gate fails closed (§6.8).

### What M1 must build (§17.2 pipeline stages)

1. **Download** into an immutable raw store, **stored in UTC**, with a checksum per file. Raw files are never edited.
   - Candidate source: Dukascopy free historical tick data (as-is, usage terms apply). Community downloaders may be wrapped.
   - **Unverified, must be checked:** that bid and ask are separately available; licence terms (read and record them); data quality.
   - Need: bid and ask, 5 years, for the 8 traded instruments plus the read-only series below.
2. **Quality control** producing a report: missing bars and gaps by session, duplicate timestamps, ask below bid, spread outliers, DST transition days, weekend/holiday handling, abnormal jumps. A dataset that fails QC is not used.
3. **Resample** from tick/M1 to **M1, M5, M15, H1, H4, D1, W1** for bid, ask and mid (§4.1).
   - Raw data is UTC. Convert to **America/New_York on read** (DST-aware). Session and day boundaries follow the New York clock, not UTC (§4.1); the daily rollover is 17:00 NY (§7.3, App. B.3).
   - Structure is computed on mid; fills, stops and costs on bid/ask (§4.3).
4. **Spread series and `s_norm`** (§4.4)
   - `s_t` = ask − bid at time t, in price units, from the live quote.
   - `s_norm(t)` = **median spread within the same 5-minute time-of-day bucket over the trailing 20 sessions**, recomputed daily. A thin or event-distorted bucket falls back to the median of its two neighbouring buckets. (It is NOT spread / ATR.)
5. **Dollar index** (`dxy` source). The source is an **open decision** (§17.6): a licensed feed carrying the ICE index versus a synthetic index. Build the synthetic construction and its validity check regardless.
   - Synthetic formula (§17.2), from the published basket:
     `DXY ≈ 50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × GBPUSD^-0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036`
   - Needs **USD/SEK as a read-only feed**. USD/SEK is not a traded instrument.
6. **Dataset manifest** recording sources, date ranges, QC results and a hash, so any result can be tied to the exact data that produced it. Written atomically after all downloads succeed.

Engineering check (not a spec exit criterion): M5/M15/H1… resampled from M1 should match the same bars resampled directly from ticks.

M1 data requirements by series (§4.1): traded pair bid/ask at M1–W1 (5 yrs); DXY OHLC at M1, M5, M15, H1, D1 (5 yrs); partner pair OHLC at M5, M15; spread series at M1 (≥ 1 year of real quotes — backtests with an assumed constant spread are not admissible for V3).

### M1 progress

Built and unit-tested offline in `fx_engine/data/` (`tests/test_data.py`, synthetic data only, no network):

| Module | Does | Notes |
|--------|------|-------|
| `instruments.py` | 8 traded + reference series, timeframes, DXY exponents, Dukascopy point values | point values UNVERIFIED |
| `timeutil.py` | UTC→NY, 17:00-NY anchored H4/D1/W1 bar starts, session label (closing date), DST dates | wall-clock bins; fall-back day repeated hour shares one H4 bin |
| `resample.py` | ticks→M1→M5/M15/H1/H4/D1/W1, bid/ask/mid OHLC, `ticks`, `spread` | M1 `spread` = ask_close − bid_close |
| `store.py` | immutable parquet store, sha256 sidecar, atomic write, verified read | |
| `snorm.py` | §4.4 `s_norm`: pooled median, same 5-min NY bucket, trailing 20 sessions, no look-ahead, neighbour fallback, NaN (fail closed) when history is short | event-distorted mask is a hook; needs the M2 calendar |
| `dxy.py` | synthetic DXY (§17.2), stale-component fail-closed, reference check (corr ≥ 0.99) | level-MAE threshold 0.5 is MY placeholder, spec says only "small" |
| `qc.py` | gaps, weekend-aware, duplicates, crossed quotes, spread outliers, jumps, DST-day coverage, pass/fail | thresholds are engineering placeholders, not §14 params |
| `manifest.py` | manifest + dataset hash; atomic write; refuses if any QC failed | |
| `dukascopy.py` | URL builder, `.bi5` decoder, hourly fetch, per-day immutable download | **never run against the real server** |

`pipeline.py` (source-agnostic): M1 frames from any source → QC → all timeframes + `s_norm` to the immutable store → DXY reference check → manifest (written only if every instrument passes QC and the DXY check passes; failed-QC instruments are not written).

Still to do for the M1 exit criterion: (1) resolve the data-source/licensing question above; (2) one-hour smoke test of whichever source is chosen (URL/record layout, scaling, bid+ask present; web sources agree on the 20-byte big-endian `.bi5` record layout but none confirms the JPY/gold scaling of 1000); (3) run the pipeline over 5 years of the 8 instruments + USDSEK; (4) decide the dollar-index source and obtain a real DXY reference series so the ≥ 0.99 check can pass; (5) ≥ 1 year of real quotes for the spread series (comes with a bid/ask source).

### Traded instruments (Table 2.1)

```python
INSTRUMENTS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "USDCAD", "NZDUSD",
    "XAUUSD",          # LONG ONLY (C5); short signals discarded at Stage 0
]
# Read-only reference series (never traded):
REFERENCE = ["DXY", "USDSEK"]   # USDSEK only to build the synthetic DXY
```

Notes from the spec: USD/CHF and EUR/USD are never held together in identical directions (P3); NZD/USD is Tier-2 (thin). Signal timeframe default M5 for EUR/USD and GBP/USD, M15 for the rest (§4.2). Pair partner for divergence: EUR/USD↔GBP/USD.

### Raw store layout

```
data/raw/
├── EURUSD/
│   ├── tick/          ← raw bid/ask ticks (UTC)
│   ├── M1/  M5/  M15/  H1/  H4/  D1/  W1/
├── GBPUSD/ ... (one folder per traded instrument)
├── USDSEK/            ← read-only, for synthetic DXY
└── DXY/               ← index or synthetic, M1/M5/M15/H1/D1
```

---

## The 43-parameter register (§14)

All parameters live in `fx_engine/config/params.py` as fields on `EngineConfig`. "Nothing in the engine may reference a magic number that does not appear here." 43 entries: 40 tunable, 3 fixed. Only the 26 tagged E may be optimised, max 5 per cycle, chosen in advance (§15.2).

### Tag meanings
- **[G]** governance — risk/control constraint. Assertion, not a setting. May be made stricter at any time; loosening needs a spec revision. Never optimised.
- **[G] fixed** — `@property` on the dataclass; `EngineConfig(max_leverage=2.0)` → `TypeError`.
- **[G] strict-only** — must remain at or below (or at or above) its default.
- **[E]** engineering default — tunable within range; `warnings.warn("[E] ...")` if outside range.
- **[C]** corpus-asserted — `w_weekly_profile` only; stays 0.0 until validated (§6.6).

### [G] fixed constants

| Name | Value | Governs |
|------|-------|---------|
| `MAX_RISK_PCT` | 0.01 | absolute per-trade risk ceiling (1% of equity) |
| `MAX_LEVERAGE` | 1.0 | C4: notional ≤ equity at all times |
| `CAPITAL_BASE_GBP` | 10_000.0 | reference account size |

### [G] tunable governance parameters

| Name | Default | Range | Governs |
|------|---------|-------|---------|
| `rho_kill` | 0.35 | 0.25–0.45 | §7.5 decoupling suspension: \|ρ60\| vs DXY below this for >30 bars suspends the pair |
| `max_stop_atr_h1` | 1.00 | 0.60–1.00 | §12.2 reject trade if stop > this × ATR(14, H1). 0.60 is pre-registered sensitivity X2 |
| `max_slippage` | 0.15 | 0.10–0.25 | §10.5 execution-fault threshold, **in ATR units** (not pips) |
| `spread_gate_pct` | 0.10 | 0.05–0.10 | G10 max all-in cost as share of R (strict-only: smaller) |
| `spread_rel_max` | 1.50 | 1.25–1.50 | G10 live spread vs `s_norm` (strict-only: smaller) |
| `spread_spike_mult` | 3.00 | 2.00–3.00 | §10.6.4 lockout trigger (strict-only: smaller) |
| `spread_lock_min` | 10 | 10–30 | §10.6.4 lockout minutes (strict-only: larger) |
| `base_risk_pct` | 0.005 | 0.0025–0.0075 | §12.1 risk-per-trade **ceiling**, not a target |
| `min_rr` | 2.0 | 1.8–3.0 | G8 minimum reward-to-risk, R net of spread |
| `daily_loss_limit` | 2.0 | 1.5–3.0 | §12.8 daily realised loss halt, **in R** |
| `weekly_loss_limit` | 5.0 | 4.0–8.0 | §12.8 weekly realised loss halt, **in R** |
| `max_positions` | 2 | 1–3 | P1 concurrency (in practice 1 while the notional cap binds) |
| `rho_portfolio_cap` | 0.70 | 0.60–0.80 | P2 correlation halving threshold |

### [E] engineering defaults (§14, Table 14.1)

| Name | Default | Range | Governs |
|------|---------|-------|---------|
| `swing_fractal_k` | 2 | 2–3 | swing point confirmation |
| `atr_period` | 14 | 10–21 | all volatility scaling |
| `req_gap_min` | 0.15 | 0.08–0.30 | inefficiency qualification (× ATR) |
| `req_eq_tol` | 0.15 | 0.08–0.25 | relative-equal-highs clustering (× ATR) |
| `inversion_conf` | 0.10 | 0.05–0.20 | inefficiency inversion (× ATR) |
| `bias_threshold` | 3 | 2–4 | minimum \|B\| to trade |
| `cisd_staleness` | 10 | 5–20 | D1 CISD decay, trading days |
| `dol_asymmetry` | 0.75 | 0.60–0.90 | B3 nearer-pool test |
| `rho_min_smt` | 0.60 | 0.50–0.75 | divergence admissibility |
| `dxy_bias_threshold` | 3 | 2–4 | minimum \|B_usd\| for a directional dollar bias |
| `dxy_agree_mode` | "strict" | strict / permissive | whether flat dollar bias passes (§6.8); permissive = ablation X1 |
| `calm_spread_mult` | 1.5 | 1.25–2.0 | calm test spread vs `s_norm` (§7.4) |
| `calm_range_mult` | 1.5 | 1.25–2.0 | calm test M1 range vs pre-event baseline |
| `calm_bars` | 5 | 3–10 | consecutive M1 bars required |
| `delta_min` | 0.05 | 0.02–0.10 | min sweep penetration (× ATR) |
| `delta_max` | 0.75 | 0.50–1.20 | breakout exclusion (× ATR) |
| `n_sweep` | 3 | 2–5 | rejection promptness, bars |
| `n_conf` | 12 | 8–20 | setup expiry, bars |
| `disp_min_atr` | 1.5 | 1.0–2.5 | displacement magnitude (× ATR) |
| `eff_lrlr` | 0.50 | 0.40–0.65 | low-resistance efficiency threshold |
| `eff_hrlr` | 0.30 | 0.20–0.40 | high-resistance efficiency threshold |
| `stop_buffer` | 0.10 | 0.05–0.20 | stop buffer (× ATR) |
| `order_expiry` | 20 | 10–30 | limit order lifetime, bars |
| `sd_confluence_tol` | 0.20 | 0.10–0.35 | projection-to-level confluence (× ATR(H1)) |
| `score_min` | 60 | 50–75 | minimum conviction score |
| `score_min_hrlr` | 80 | 70–90 | minimum score in HRLR environments |

### [C] corpus-asserted

| Name | Default | Governs |
|------|---------|---------|
| `w_weekly_profile` | 0.0 | weekly profile modifier (B6). Raised to 1.0 only if own walk-forward test confirms at p<0.05 across ≥100 weeks (§6.6) |

### Venue cost and session-timing fields (read from `params.py`)

Venue cost (both `float`, default `0.0`, tagged "E — venue measurement": set to the chosen venue's measured cost, never tuned to improve a backtest):
- `k_commission` — round-turn commission per unit, price units (zero on a spread-only venue)
- `f_conv_price_units` — expected currency-conversion charge per round turn, price units

Session timing, NY-time `"HH:MM"` strings (not in the §14 table):

| Field | Default |
|-------|---------|
| `t_london_open_start` / `t_london_open_end` | "02:00" / "05:00" |
| `t_london_sb_start` / `t_london_sb_end` | "03:00" / "04:00" |
| `t_ny_am_start` / `t_ny_am_end` | "07:00" / "09:00" |
| `t_ny_sb_start` / `t_ny_sb_end` | "08:00" / "09:00" |
| `t_manage_only` | "15:30" |
| `t_hard_flat` | "16:55" |

Not yet encoded in `params.py` (spec §7.2/§7.3 has them): Macro 09:50–10:10 and 10:50–11:10, PM Silver Bullet 14:00–15:00, the blocked windows (London lull 05:00–07:00, NY lunch 12:00–13:30, final-hour macro 15:15–15:45), the 02:00 bias freeze, the 17:00 rollover/assert-flat time, and Tier-1 event timings. Add them in M2 when the session clock is built.

Other facts about the code: `max_risk_pct`, `max_leverage` and `capital_base_gbp` are properties backed by module constants `MAX_RISK_PCT`, `MAX_LEVERAGE`, `CAPITAL_BASE_GBP`. `base_risk_pct` is checked both as a [G] bound (hard error) and in the [E] soft range check.

### Session boundary (17:00 New York)

The spec says day boundaries follow the New York clock (§4.1). The daily boundary is the **17:00 NY rollover** (§7.3, App. B.3), not midnight: D1 bars and "previous day" levels run 17:00 NY to 17:00 NY. **Raw data is stored in UTC and converted to America/New_York on read** (DST-aware); never store NY-local timestamps.

---

## The decision cascade (§5, Table 5.1)

Evaluated on the close of every signal-timeframe bar, never intra-bar. All **ten hard gates** must pass; one failure ends evaluation. Stage 6 is a score, Stage 7 is risk/exit. Cheap, high-rejection gates run first.

| Stage | Name | Gate(s) |
|-------|------|---------|
| 0 | Admissibility | G1 |
| 1 | Bias (§6) | G2 |
| 2 | Time (§7) | G3, G4 |
| 3 | Liquidity event / sweep (§8) | G5 |
| 4 | Displacement / CISD (§9) | G6 |
| 5 | Entry array, order placement, cost control (§10) | G7, G10 |
| 6 | Conviction score (§11) | score ≥ 60 (80 in HRLR) |
| 7 | Risk, sizing, management, exit (§12–13) | G8, G9 |

### The 10 hard gates (Table 5.1)

| Gate | Condition | Failure means | Spec |
|------|-----------|---------------|------|
| G1 | Compliance and account state admissible; operator kill switch not set; candidate direction permitted for the instrument (gold: long only) | System halt (a forbidden direction is simply discarded) | §2.3, §12.10 |
| G2 | \|Bias score\| ≥ 3, candidate direction equals bias direction, **and the standalone dollar bias agrees** | No conviction, or dollar contradicts; stand down for the session | §6.7, §6.8 |
| G3 | Current time inside an approved session window | Wait for the next window | §7.2 |
| G4 | No Tier-1 event blackout for either currency, **and the market has measurably calmed** after any recent event | Wait for blackout to clear and calm to hold | §7.4 |
| G5 | A qualified sweep of an un-breached pool has completed | No trigger; keep monitoring | §8.1 |
| G6 | Displacement with body-close CISD, leaving ≥ 1 qualifying inefficiency | Sweep unconfirmed; setup expires after N bars | §9.1 |
| G7 | Entry price in discount (long) or premium (short) of the displacement range | Chasing; reject | §10.3 |
| G8 | Reward-to-risk to first target ≥ 2.0R, R measured net of spread | Geometry insufficient | §12.4 |
| G9 | Daily loss limit intact; concurrent-position, correlation and notional caps respected | Portfolio-level block | §12.8, §13 |
| G10 | Cost gate: all-in cost ≤ 10% of R **and** live spread ≤ 1.5× `s_norm` | Do not place, or cancel if pending | §10.6 |

Other binding rules: the body-close rule (structure confirmed on closes only, never wicks) is [G]; bias and dollar bias are computed once at 02:00 NY and frozen for the session; 15:30 NY cancel all resting orders, 16:55 NY liquidate, 17:00 NY assert flat (a position at 17:00 is a critical fault); loss limits are 2.0R daily and 5.0R weekly. Notional ≤ equity (P6) is asserted in code before every order and after every fill.

---

## Instrument state machine (§5.3, per instrument)

| State | Exits to |
|-------|----------|
| DORMANT | ARMED at window open with valid bias |
| ARMED | SWEPT on qualified sweep; DORMANT at window close |
| SWEPT | CONFIRMED on displacement + CISD; ARMED on expiry (12 bars) |
| CONFIRMED | PENDING once the limit order is placed; ARMED if the array expires. Order may be deferred here if G10 fails (§10.6.2) |
| PENDING | IN_TRADE on fill; ARMED on expiry (20 bars), invalidation, or cancel-on-widen with no valid re-placement |
| IN_TRADE | MANAGING after the first scale-out fills |
| MANAGING | DORMANT on final exit |
| HALTED | Daily/weekly loss limit, circuit breaker, or operator kill switch. Manual reset only (kill switch: operator alone, with a logged reason) |

Every transition is logged with its triggering condition.

---

## Logging schema (§15.4, §17.4)

Every log event is a JSON line with mandatory fields. See `fx_engine/core/logging_setup.py`.

Mandatory fields: `ts`, `event`, `instrument`, `timeframe`, `config_hash`, `bar_time`

Event types: `state_transition`, `rejection`, `trade_entry`, `trade_exit`, `scale_out`, `kill_switch`, `circuit_breaker`, `spread_lockout`, `cost_fault`, `execution_fault`, `compliance_flat`, `critical_fault`

The rejection log is mandatory (§11.2): every candidate that reaches Stage 6 below 60 is logged with its factor breakdown; every G10 rejection with spread, `s_norm` and cost share c.

**SECURITY**: API tokens and secrets must NEVER appear in log output. (§17.7)

---

## One code path rule (§17.1)

> The decision engine is one code path. The function that takes bars and state and returns actions is identical in backtest, paper and live. Only the adapters around it change (data source, order execution, clock).

Target shape: `on_bar_close(state, bars, session) → (state, actions)` plus the M1-close spread monitor. A `BrokerAdapter` interface with three implementations: simulated (backtest), practice (paper), live (§17.4). Never fork the cascade logic.

---

## Security rules (§17.7) — NEVER VIOLATE

1. API tokens live in the **environment or a secrets store**, never in the repository, logs or configuration files
2. `.env` is in `.gitignore` — never commit it
3. `.env.example` shows variable names with placeholder values — this IS committed
4. Never log credential values, even at DEBUG level
5. Never print credential values in error messages or stack traces
6. Paper and live use different credentials and config files; the adapter refuses to start live without an explicit, logged governance flag

---

## Open decisions (§17.6)

| Item | Needed by | Status / notes |
|------|-----------|----------------|
| **Dollar-index source** | M1 exit | **Open decision.** Licensed feed carrying the ICE index vs synthetic construction from the six component pairs (§17.2). A retail FX broker is not assumed to carry DXY. Synthetic must pass the ≥ 0.99 correlation reference check |
| **Data licensing / source (BLOCKING M1 download)** | M1 | Dukascopy site Terms of Use (read 2026-11-18, undated page, can change at any time): downloads licensed "ONLY for your own personal, non-commercial use"; no "scraper/robot/bot/spider/data mining" access without prior express written consent; site information "may not be used to construct a database of any kind". A scripted downloader and a local raw store are what those clauses restrict, so `dukascopy.download_*` refuse unless `terms_cleared=True`. Options: written consent from Dukascopy; manual use of their Historical Data Export tool; or OANDA v20 M1 `price=BA` candles (the spec's own venue-consistent candidate, needs a practice token in `.env`). Not legal advice. |
| Scholarly guidance | Before M7, ideally before M6 | Issue 2 (hand-to-hand exchange) for the chosen product, and gold. May narrow the universe (§2.3) |
| Practice and live venue | M6 / M7 | Candidate: OANDA fxTrade Practice. Third-party spread figure (~0.9 pip EUR/USD) is unverified and must be measured. Funded-account requirement and regional availability to be checked at sign-up. Trading 212 dropped (no forex API) |
| Currency-conversion charge | M4 | One source lists 0.50%. Whether it applies to realised P&L, margin or full notional must be confirmed; on full notional it would be about £50 on £10,000 and exceed any realistic edge |
| Hosting and uptime | M6 | Engine must run from before 02:00 to after 17:00 NY every trading day, with reliable time sync |
| `w_weekly_profile` | Validation | Held at 0.0 until own walk-forward test confirms (§6.6) |

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

Python 3.12 target (≥ 3.11 required).

---

## Spec cross-references

Spec: `claude/decision-engine-methodology-v2.md` (project docs; not stored in this repo). Key sections:

- §2 — Scope, universe, binding constraints; compliance gates C1–C5; capital and leverage model (§2.5)
- §4 — Data requirements, notation, primitive definitions, spread/cost notation (§4.4)
- §5 — Decision cascade, gate table, state machine
- §6 — Bias engine (Stage 1), incl. standalone dollar bias (§6.8)
- §7 — Temporal admissibility, event blackout and calm test, correlation kill
- §8 — Liquidity event / sweep detection
- §9 — Displacement, CISD, environment classification
- §10 — Entry array, order lifecycle, cost gate (§10.6)
- §11 — Conviction scorecard
- §12 — Risk, sizing, exits, circuit breakers, kill switch (§12.10)
- §13 — Portfolio constraints P1–P6
- §14 — Parameter register
- §15 — Validation V1–V6, governance, falsification, pre-registered arms
- §16 — Limitations and risks
- §17 — Implementation architecture, build order and exit criteria, open decisions, security
- Appendix A — reference pseudocode (governs over prose)
