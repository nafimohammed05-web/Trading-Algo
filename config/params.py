"""
FX Decision Engine v2.0 — Consolidated Parameter Register (§14)

Provenance tags:
  [E] Engineering default — may be optimised within range (max 5 params/cycle, §15.2)
  [G] Governance rule    — assertion only; may be made STRICTER, NEVER optimised
  [C] Corpus-asserted    — fixed constant; never changed without a full governance review

43 parameters total: 26 [E], 14 [G] (13 tunable + 3 fixed), 3 [C] (1 pending + 2 implicit).

Rule: Nothing in the engine may reference a magic number that does not appear here.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal


class ConfigValidationError(ValueError):
    """Raised when a G-tagged governance rule is violated."""


# ---------------------------------------------------------------------------
# Governance rule assertions — these are NOT configurable; they are facts
# about what the system is allowed to do.  Any attempt to construct an
# EngineConfig whose G values fall outside the permitted range raises
# ConfigValidationError before the object is returned.
# ---------------------------------------------------------------------------

_G_BOUNDS: dict[str, tuple[float, float]] = {
    # param_name: (min_inclusive, max_inclusive)
    "rho_kill":           (0.25, 0.45),   # §6.4 — decoupling suspension
    "max_stop_atr_h1":    (0.60, 1.00),   # §12.2 — trade rejection on wide stop
    "max_slippage":       (0.10, 0.25),   # §12 — execution fault threshold (ATR units)
    "spread_gate_pct":    (0.05, 0.10),   # §10.6.1 G10 — max cost share of R (stricter only)
    "spread_rel_max":     (1.25, 1.50),   # §10.6 G10 — live spread vs session-normal (stricter only)
    "spread_spike_mult":  (2.00, 3.00),   # §10.6.4 — spread lockout trigger (stricter only)
    "spread_lock_min":    (10,   30),      # §10.6.4 — spread lockout duration in minutes
    "base_risk_pct":      (0.0025, 0.0075),  # §12.1 — risk ceiling per trade (0.25%–0.75%)
    "min_rr":             (1.8,  3.0),    # §12.4 G8 — minimum reward-to-risk
    "daily_loss_limit":   (1.5,  3.0),    # §12.8 — session circuit breaker in R
    "weekly_loss_limit":  (4.0,  8.0),    # §12.8 — weekly circuit breaker in R
    "max_positions":      (1,    3),       # §13 — concurrent positions (in practice 1 while notional cap binds)
    "rho_portfolio_cap":  (0.60, 0.80),   # §13.2 — correlation halving threshold
}

# G parameters that may ONLY be made stricter (smaller), never looser.
# "stricter" for a cap means smaller; for a floor means larger.
_G_STRICT_ONLY: dict[str, Literal["smaller", "larger"]] = {
    "spread_gate_pct":   "smaller",   # stricter = lower cost-share tolerance
    "spread_rel_max":    "smaller",   # stricter = lower spread multiple allowed
    "spread_spike_mult": "smaller",   # stricter = lower spike multiple triggers lockout sooner
    "spread_lock_min":   "larger",    # stricter = longer lockout duration
}

# Default values for the strict-only G params (the LEAST strict permitted value).
_G_STRICT_DEFAULTS: dict[str, float] = {
    "spread_gate_pct":   0.10,
    "spread_rel_max":    1.50,
    "spread_spike_mult": 3.00,
    "spread_lock_min":   10,
}

# Fixed G values — cannot be changed under any circumstances.
# Any deviation requires a governance review (§15, §17).
MAX_RISK_PCT: float  = 0.01   # [G fixed] 1.0% — absolute ceiling
MAX_LEVERAGE: float  = 1.0    # [G fixed] C4: notional ≤ equity at all times
CAPITAL_BASE_GBP: float = 10_000.0  # [G fixed] £10,000 reference capital


def _assert_g(name: str, value: float) -> None:
    """Raise ConfigValidationError if value violates governance bounds."""
    lo, hi = _G_BOUNDS[name]
    if not (lo <= value <= hi):
        raise ConfigValidationError(
            f"[G] Parameter '{name}' = {value} violates governance rule "
            f"(permitted range: [{lo}, {hi}]).  "
            f"G-tagged values are assertions, not settings — they may only be "
            f"made stricter, never optimised (§14, §15.2)."
        )
    if name in _G_STRICT_ONLY:
        default = _G_STRICT_DEFAULTS[name]
        direction = _G_STRICT_ONLY[name]
        if direction == "smaller" and value > default:
            raise ConfigValidationError(
                f"[G] Parameter '{name}' = {value} is LOOSER than the default "
                f"{default}.  Cost-gate G params may only be made stricter (§10.6, §14)."
            )
        if direction == "larger" and value < default:
            raise ConfigValidationError(
                f"[G] Parameter '{name}' = {value} is LOOSER than the default "
                f"{default}.  Cost-gate G params may only be made stricter (§10.6, §14)."
            )


@dataclass
class EngineConfig:
    """
    Typed configuration class for the FX Decision Engine v2.0.

    Every tunable quantity from §14 Table 14.1, in one place.
    G-tagged parameters assert governance rules on construction; violation raises
    ConfigValidationError.  E-tagged parameters may be optimised within range.
    C-tagged constants are wired to fixed values.

    Usage
    -----
    >>> cfg = EngineConfig()          # loads defaults
    >>> cfg = EngineConfig(rho_kill=0.30)  # tighten a G value → ok
    >>> cfg = EngineConfig(rho_kill=0.50)  # outside range → raises ConfigValidationError
    """

    # ------------------------------------------------------------------
    # [E] Structural primitives
    # ------------------------------------------------------------------

    swing_fractal_k: int = 2
    """Swing point confirmation (bars each side).  Range 2–3.  [E]"""

    atr_period: int = 14
    """ATR lookback for all volatility scaling.  Range 10–21.  [E]"""

    req_gap_min: float = 0.15
    """Inefficiency qualification minimum (ATR multiple).  Range 0.08–0.30.  [E]"""

    req_eq_tol: float = 0.15
    """Relative-equal-highs clustering tolerance (ATR multiple).  Range 0.08–0.25.  [E]"""

    inversion_conf: float = 0.10
    """Inefficiency inversion confirmation (ATR multiple).  Range 0.05–0.20.  [E]"""

    # ------------------------------------------------------------------
    # [E] Bias engine
    # ------------------------------------------------------------------

    bias_threshold: int = 3
    """Minimum |B| to trade (pair bias).  Range 2–4.  [E]"""

    cisd_staleness: int = 10
    """D1 CISD decay in trading days.  Range 5–20.  [E]"""

    dol_asymmetry: float = 0.75
    """B3 nearer-pool test asymmetry factor.  Range 0.60–0.90.  [E]"""

    rho_min_smt: float = 0.60
    """Minimum |ρ₆₀| for divergence admissibility (B4).  Range 0.50–0.75.  [E]"""

    dxy_bias_threshold: int = 3
    """Minimum |B_usd| for a directional dollar bias (§6.8).  Range 2–4.  [E]"""

    dxy_agree_mode: Literal["strict", "permissive"] = "strict"
    """Whether a flat dollar bias passes the agreement rule (§6.8).  [E]"""

    # ------------------------------------------------------------------
    # [E] Time and news
    # ------------------------------------------------------------------

    calm_spread_mult: float = 1.5
    """Calm test: spread vs session-normal threshold (§7.4).  Range 1.25–2.0.  [E]"""

    calm_range_mult: float = 1.5
    """Calm test: M1 range vs pre-event baseline threshold (§7.4).  Range 1.25–2.0.  [E]"""

    calm_bars: int = 5
    """Calm test: consecutive M1 bars required (§7.4).  Range 3–10.  [E]"""

    # ------------------------------------------------------------------
    # [E] Sweep and displacement
    # ------------------------------------------------------------------

    delta_min: float = 0.05
    """Minimum pool penetration depth (ATR multiple).  Range 0.02–0.10.  [E]"""

    delta_max: float = 0.75
    """Breakout exclusion ceiling (ATR multiple).  Range 0.50–1.20.  [E]"""

    n_sweep: int = 3
    """Rejection promptness: max bars to close back inside pool.  Range 2–5.  [E]"""

    n_conf: int = 12
    """SWEPT→CONFIRMED setup expiry in bars.  Range 8–20.  [E]"""

    disp_min_atr: float = 1.5
    """Displacement magnitude minimum (ATR multiple on signal TF).  Range 1.0–2.5.  [E]"""

    eff_lrlr: float = 0.50
    """Low-resistance efficiency threshold.  Range 0.40–0.65.  [E]"""

    eff_hrlr: float = 0.30
    """High-resistance efficiency threshold.  Range 0.20–0.40.  [E]"""

    # ------------------------------------------------------------------
    # [E] Execution
    # ------------------------------------------------------------------

    stop_buffer: float = 0.10
    """Stop placement buffer beyond structural level (ATR multiple).  Range 0.05–0.20.  [E]"""

    order_expiry: int = 20
    """PENDING limit order lifetime in bars.  Range 10–30.  [E]"""

    sd_confluence_tol: float = 0.20
    """Standard-deviation projection to level confluence tolerance (ATR(H1) multiple).
    Range 0.10–0.35.  [E]"""

    # ------------------------------------------------------------------
    # [E] Scoring
    # ------------------------------------------------------------------

    score_min: int = 60
    """Trade admissibility floor (conviction score 0–100).  Range 50–75.  [E]"""

    score_min_hrlr: int = 80
    """High-resistance admissibility floor.  Range 70–90.  [E]"""

    # ------------------------------------------------------------------
    # [G] Governance rules — assertions; range enforcement on __post_init__
    # ------------------------------------------------------------------

    rho_kill: float = 0.35
    """Decoupling suspension threshold: suspend if |ρ₆₀| < rho_kill.  Range 0.25–0.45.  [G]"""

    max_stop_atr_h1: float = 1.00
    """Maximum stop distance in ATR(H1) units before trade is rejected (§12.2).
    Range 0.60–1.00 (v1.0 default was 0.60).  [G]"""

    max_slippage: float = 0.15
    """Execution fault threshold in ATR units.  Range 0.10–0.25.  [G]"""

    spread_gate_pct: float = 0.10
    """G10: maximum cost share of R (all-in cost / R ≤ this).  Range 0.05–0.10 (stricter only).  [G]"""

    spread_rel_max: float = 1.50
    """G10: live spread vs session-normal ceiling.  Range 1.25–1.50 (stricter only).  [G]"""

    spread_spike_mult: float = 3.00
    """Spread lockout trigger: lockout if spread > spread_spike_mult × s_norm.
    Range 2.0–3.0 (stricter only).  [G]"""

    spread_lock_min: int = 10
    """Spread lockout duration in minutes.  Range 10–30 (stricter only: longer is tighter).  [G]"""

    base_risk_pct: float = 0.005
    """Risk per trade ceiling as a fraction of equity (0.5% default).  Range 0.0025–0.0075.  [G]"""

    min_rr: float = 2.0
    """Minimum reward-to-risk ratio to TP1 (G8).  Range 1.8–3.0.  [G]"""

    daily_loss_limit: float = 2.0
    """Session circuit-breaker threshold in R.  Range 1.5–3.0.  [G]"""

    weekly_loss_limit: float = 5.0
    """Weekly circuit-breaker threshold in R.  Range 4.0–8.0.  [G]"""

    max_positions: int = 2
    """Maximum concurrent open positions (§13, in practice 1 while notional cap binds).
    Range 1–3.  [G]"""

    rho_portfolio_cap: float = 0.70
    """Correlation halving threshold: if |ρ₆₀| ≥ this, halve each position.  Range 0.60–0.80.  [G]"""

    # ------------------------------------------------------------------
    # [C] Corpus-asserted constants
    # These are wired to their values by the corpus.  Changing them is a
    # governance review, not a configuration change.
    # ------------------------------------------------------------------

    w_weekly_profile: float = 0.0
    """B6 weekly profile modifier weight.  Default 0.0 (pending empirical validation, §6.6).  [C]"""

    # ------------------------------------------------------------------
    # [G fixed] Hard constants — declared as class attributes, not fields,
    # so they cannot be overridden on construction.
    # ------------------------------------------------------------------

    @property
    def max_risk_pct(self) -> float:          # [G fixed]
        """Absolute risk ceiling per trade (1.0%). Never changed."""
        return MAX_RISK_PCT

    @property
    def max_leverage(self) -> float:          # [G fixed]
        """C4: notional ≤ equity at all times (1.0×). Never changed."""
        return MAX_LEVERAGE

    @property
    def capital_base_gbp(self) -> float:      # [G fixed]
        """Reference account size in GBP (£10,000). Changed only by governance review."""
        return CAPITAL_BASE_GBP

    # ------------------------------------------------------------------
    # Venue cost parameters — set to measured costs of the chosen venue,
    # NEVER to whatever improves the backtest (§14, §15.2).
    # These are E-tagged but cannot be optimised; they are measurements.
    # ------------------------------------------------------------------

    k_commission: float = 0.0
    """Round-turn commission per unit in price units.  Zero on a spread-only venue.
    Set to the chosen venue's measured cost (§17.6).  [E — venue measurement]"""

    f_conv_price_units: float = 0.0
    """Expected currency-conversion charge per round-turn in price units.
    Set to the chosen venue's measured cost (§17.6).  [E — venue measurement]"""

    # ------------------------------------------------------------------
    # Session timing constants (New York time, §7.2)
    # Not in the §14 table but referenced throughout the spec.
    # ------------------------------------------------------------------

    t_london_open_start: str  = "02:00"   # NY — London Open window start
    t_london_open_end:   str  = "05:00"   # NY — London Open window end
    t_london_sb_start:   str  = "03:00"   # NY — London Silver Bullet start
    t_london_sb_end:     str  = "04:00"   # NY — London Silver Bullet end
    t_ny_am_start:       str  = "07:00"   # NY — NY AM window start
    t_ny_am_end:         str  = "09:00"   # NY — NY AM window end
    t_ny_sb_start:       str  = "08:00"   # NY — NY AM Silver Bullet start
    t_ny_sb_end:         str  = "09:00"   # NY — NY AM Silver Bullet end
    t_manage_only:       str  = "15:30"   # NY — no new entries after this
    t_hard_flat:         str  = "16:55"   # NY — compliance: liquidate all positions

    def __post_init__(self) -> None:
        """Enforce all governance rules.  Called automatically on construction."""
        for name in _G_BOUNDS:
            _assert_g(name, getattr(self, name))
        self._validate_e_ranges()

    def _validate_e_ranges(self) -> None:
        """Soft validation for E-tagged parameter ranges (warnings, not errors)."""
        import warnings

        e_ranges = {
            "swing_fractal_k":  (2, 3),
            "atr_period":       (10, 21),
            "req_gap_min":      (0.08, 0.30),
            "req_eq_tol":       (0.08, 0.25),
            "inversion_conf":   (0.05, 0.20),
            "bias_threshold":   (2, 4),
            "cisd_staleness":   (5, 20),
            "dol_asymmetry":    (0.60, 0.90),
            "rho_min_smt":      (0.50, 0.75),
            "dxy_bias_threshold": (2, 4),
            "calm_spread_mult": (1.25, 2.0),
            "calm_range_mult":  (1.25, 2.0),
            "calm_bars":        (3, 10),
            "delta_min":        (0.02, 0.10),
            "delta_max":        (0.50, 1.20),
            "n_sweep":          (2, 5),
            "n_conf":           (8, 20),
            "disp_min_atr":     (1.0, 2.5),
            "eff_lrlr":         (0.40, 0.65),
            "eff_hrlr":         (0.20, 0.40),
            "stop_buffer":      (0.05, 0.20),
            "order_expiry":     (10, 30),
            "sd_confluence_tol":(0.10, 0.35),
            "score_min":        (50, 75),
            "score_min_hrlr":   (70, 90),
            "base_risk_pct":    (0.0025, 0.0075),
        }
        for name, (lo, hi) in e_ranges.items():
            val = getattr(self, name)
            if not (lo <= val <= hi):
                warnings.warn(
                    f"[E] Parameter '{name}' = {val} is outside its register range "
                    f"[{lo}, {hi}] (§14 Table 14.1).  Proceeding, but optimisation "
                    f"must stay within this range (§15.2).",
                    stacklevel=3,
                )

    # ------------------------------------------------------------------
    # Configuration fingerprint — written into every log row (§17 App. C)
    # ------------------------------------------------------------------

    def config_hash(self) -> str:
        """
        SHA-256 of the serialised parameter values.
        Written into every log row so post-hoc analysis can join the trade log
        to the exact config that generated it (§17 Appendix C).
        """
        d = {
            k: v
            for k, v in self.__dict__.items()
            if not k.startswith("_")
        }
        # Add fixed G constants
        d["max_risk_pct"]    = MAX_RISK_PCT
        d["max_leverage"]    = MAX_LEVERAGE
        d["capital_base_gbp"] = CAPITAL_BASE_GBP
        blob = json.dumps(d, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def as_dict(self) -> dict:
        """Return all parameters as a plain dict (for logging / serialisation)."""
        d = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        d["max_risk_pct"]    = MAX_RISK_PCT
        d["max_leverage"]    = MAX_LEVERAGE
        d["capital_base_gbp"] = CAPITAL_BASE_GBP
        d["config_hash"]     = self.config_hash()
        return d
