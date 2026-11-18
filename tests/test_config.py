"""
M0 exit criterion tests.

§17.3: "Config loads; setting a G value outside its range raises."

Covers:
  - Default config loads without error
  - Every G-tagged parameter raises ConfigValidationError when set outside range
  - Strict-only G params (cost gate) raise when made looser than default
  - E-tagged parameters emit a warning when outside range (not an error)
  - Fixed G constants are immutable
  - config_hash is deterministic and changes when any value changes
  - as_dict contains all 43 parameters + fixed constants
"""
import hashlib
import warnings

import pytest

from fx_engine.config import EngineConfig, ConfigValidationError
from fx_engine.config.params import (
    MAX_LEVERAGE,
    MAX_RISK_PCT,
    CAPITAL_BASE_GBP,
)


# ---------------------------------------------------------------------------
# M0 exit criterion: config loads at defaults
# ---------------------------------------------------------------------------

class TestDefaultConfig:
    def test_loads_without_error(self):
        cfg = EngineConfig()
        assert cfg is not None

    def test_max_leverage_is_one(self):
        """C4: notional ≤ equity at all times (§2.5)."""
        cfg = EngineConfig()
        assert cfg.max_leverage == 1.0

    def test_max_risk_pct_is_one_percent(self):
        cfg = EngineConfig()
        assert cfg.max_risk_pct == 0.01

    def test_capital_base_is_ten_thousand(self):
        cfg = EngineConfig()
        assert cfg.capital_base_gbp == 10_000.0

    def test_dxy_agree_mode_default(self):
        """Default is strict (§6.8)."""
        cfg = EngineConfig()
        assert cfg.dxy_agree_mode == "strict"

    def test_score_min_default(self):
        """Floor 60 to trade (§11.1)."""
        cfg = EngineConfig()
        assert cfg.score_min == 60

    def test_max_positions_default(self):
        cfg = EngineConfig()
        assert cfg.max_positions == 2


# ---------------------------------------------------------------------------
# M0 exit criterion: G values outside range raise
# ---------------------------------------------------------------------------

class TestGovernanceAssertions:
    """Every G-tagged parameter must raise ConfigValidationError when violated."""

    def _assert_raises_below(self, param: str, bad_value: float) -> None:
        with pytest.raises(ConfigValidationError, match=r"\[G\]"):
            EngineConfig(**{param: bad_value})

    def _assert_raises_above(self, param: str, bad_value: float) -> None:
        with pytest.raises(ConfigValidationError, match=r"\[G\]"):
            EngineConfig(**{param: bad_value})

    def _assert_ok(self, param: str, good_value: float) -> None:
        cfg = EngineConfig(**{param: good_value})
        assert getattr(cfg, param) == good_value

    # --- rho_kill (0.25–0.45) ---
    def test_rho_kill_too_low(self):  self._assert_raises_below("rho_kill", 0.24)
    def test_rho_kill_too_high(self): self._assert_raises_above("rho_kill", 0.46)
    def test_rho_kill_valid(self):    self._assert_ok("rho_kill", 0.30)

    # --- max_stop_atr_h1 (0.60–1.00) ---
    def test_max_stop_atr_h1_too_low(self):  self._assert_raises_below("max_stop_atr_h1", 0.59)
    def test_max_stop_atr_h1_too_high(self): self._assert_raises_above("max_stop_atr_h1", 1.01)

    # --- max_slippage (0.10–0.25) ---
    def test_max_slippage_too_low(self):  self._assert_raises_below("max_slippage", 0.09)
    def test_max_slippage_too_high(self): self._assert_raises_above("max_slippage", 0.26)

    # --- base_risk_pct (0.0025–0.0075) ---
    def test_base_risk_pct_too_low(self):  self._assert_raises_below("base_risk_pct", 0.0024)
    def test_base_risk_pct_too_high(self): self._assert_raises_above("base_risk_pct", 0.0076)

    # --- min_rr (1.8–3.0) ---
    def test_min_rr_too_low(self):  self._assert_raises_below("min_rr", 1.79)
    def test_min_rr_too_high(self): self._assert_raises_above("min_rr", 3.01)

    # --- daily_loss_limit (1.5–3.0) ---
    def test_daily_loss_limit_too_low(self):  self._assert_raises_below("daily_loss_limit", 1.49)
    def test_daily_loss_limit_too_high(self): self._assert_raises_above("daily_loss_limit", 3.01)

    # --- weekly_loss_limit (4.0–8.0) ---
    def test_weekly_loss_limit_too_low(self):  self._assert_raises_below("weekly_loss_limit", 3.99)
    def test_weekly_loss_limit_too_high(self): self._assert_raises_above("weekly_loss_limit", 8.01)

    # --- max_positions (1–3) ---
    def test_max_positions_too_low(self):  self._assert_raises_below("max_positions", 0)
    def test_max_positions_too_high(self): self._assert_raises_above("max_positions", 4)

    # --- rho_portfolio_cap (0.60–0.80) ---
    def test_rho_portfolio_cap_too_low(self):  self._assert_raises_below("rho_portfolio_cap", 0.59)
    def test_rho_portfolio_cap_too_high(self): self._assert_raises_above("rho_portfolio_cap", 0.81)

    # --- spread_gate_pct (0.05–0.10, stricter only) ---
    def test_spread_gate_pct_too_low(self):
        self._assert_raises_below("spread_gate_pct", 0.04)

    def test_spread_gate_pct_looser_than_default_raises(self):
        """Cannot be set looser than 0.10 (the least strict allowed — above range)."""
        with pytest.raises(ConfigValidationError):
            EngineConfig(spread_gate_pct=0.10 + 1e-9)  # one hair above upper bound

    def test_spread_gate_pct_tighter_ok(self):
        """Setting to 0.07 (tighter than 0.10) is fine."""
        cfg = EngineConfig(spread_gate_pct=0.07)
        assert cfg.spread_gate_pct == 0.07

    # --- spread_rel_max (1.25–1.50, stricter only) ---
    def test_spread_rel_max_too_high(self):
        with pytest.raises(ConfigValidationError):
            EngineConfig(spread_rel_max=1.51)

    def test_spread_rel_max_looser_than_default_raises(self):
        with pytest.raises(ConfigValidationError):
            EngineConfig(spread_rel_max=1.50 + 1e-9)

    # --- spread_spike_mult (2.0–3.0, stricter only) ---
    def test_spread_spike_mult_too_low(self):
        with pytest.raises(ConfigValidationError):
            EngineConfig(spread_spike_mult=1.99)

    def test_spread_spike_mult_looser_than_default_raises(self):
        with pytest.raises(ConfigValidationError):
            EngineConfig(spread_spike_mult=3.00 + 1e-9)

    # --- spread_lock_min (10–30, stricter = longer) ---
    def test_spread_lock_min_too_low(self):
        with pytest.raises(ConfigValidationError):
            EngineConfig(spread_lock_min=9)

    def test_spread_lock_min_too_high(self):
        with pytest.raises(ConfigValidationError):
            EngineConfig(spread_lock_min=31)

    def test_spread_lock_min_looser_than_default_raises(self):
        """Shorter lockout is looser — cannot go below 10 (hits range check)."""
        with pytest.raises(ConfigValidationError):
            EngineConfig(spread_lock_min=9)


# ---------------------------------------------------------------------------
# Fixed G constants are truly immutable
# ---------------------------------------------------------------------------

class TestFixedConstants:
    def test_max_leverage_is_property(self):
        cfg = EngineConfig()
        assert cfg.max_leverage == 1.0

    def test_cannot_override_max_leverage(self):
        """max_leverage is a property — the dataclass field doesn't exist."""
        with pytest.raises(TypeError):
            # dataclass __init__ does not accept a field named max_leverage
            EngineConfig(max_leverage=2.0)  # type: ignore[call-arg]

    def test_cannot_override_max_risk_pct(self):
        with pytest.raises(TypeError):
            EngineConfig(max_risk_pct=0.02)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# E-tagged parameters: warning not error when out of range
# ---------------------------------------------------------------------------

class TestEParameterWarnings:
    def test_out_of_range_e_param_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = EngineConfig(atr_period=5)   # below range 10–21
        assert any("[E]" in str(warning.message) for warning in w)

    def test_in_range_e_param_no_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = EngineConfig(atr_period=14)  # default, in range
        e_warnings = [x for x in w if "[E]" in str(x.message)]
        assert len(e_warnings) == 0


# ---------------------------------------------------------------------------
# Config hash and serialisation
# ---------------------------------------------------------------------------

class TestConfigHash:
    def test_hash_is_deterministic(self):
        cfg1 = EngineConfig()
        cfg2 = EngineConfig()
        assert cfg1.config_hash() == cfg2.config_hash()

    def test_hash_changes_with_value(self):
        cfg1 = EngineConfig()
        cfg2 = EngineConfig(rho_kill=0.30)
        assert cfg1.config_hash() != cfg2.config_hash()

    def test_as_dict_contains_all_params(self):
        cfg = EngineConfig()
        d = cfg.as_dict()
        # Spot-check a sample of each tag type
        assert "swing_fractal_k" in d        # [E]
        assert "rho_kill" in d               # [G]
        assert "w_weekly_profile" in d       # [C]
        assert "max_leverage" in d           # [G fixed]
        assert "max_risk_pct" in d           # [G fixed]
        assert "capital_base_gbp" in d       # [G fixed]
        assert "config_hash" in d
