"""
Structured logging for the FX Decision Engine v2.0.

Conventions (§17 Appendix C):
  - Every log record carries config_hash so it can be joined to its exact
    configuration after the fact.
  - Every state transition is logged with its triggering condition (§5.3).
  - Trade log fields match §15.4 required reporting exactly.
  - Rejection log records which gate failed — non-optional (§11.2).
  - Secrets (API keys, broker credentials) must never appear in any log.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_LEVEL_DEFAULT: str = "INFO"
LOG_DIR_DEFAULT: str   = "logs"


# ---------------------------------------------------------------------------
# JSON formatter — machine-readable, easy to parse for trade analytics
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line, always UTC, always with config_hash."""

    def __init__(self, config_hash: str = ""):
        super().__init__()
        self._config_hash = config_hash

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts":          datetime.now(tz=timezone.utc).isoformat(),
            "level":       record.levelname,
            "logger":      record.name,
            "config_hash": self._config_hash,
            "msg":         record.getMessage(),
        }
        # Merge any extra fields attached with logger.info("...", extra={...})
        for key, val in record.__dict__.items():
            if key not in {
                "args", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "message",
                "module", "msecs", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread",
                "threadName",
            } and not key.startswith("_"):
                payload[key] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Engine-level structured log helpers
# ---------------------------------------------------------------------------

class EngineLogger:
    """
    Thin wrapper around Python logging that enforces the §15.4 log schema.

    Every method emits a JSON record with a mandatory 'event' field so
    downstream queries can filter by event type rather than parsing free text.
    """

    def __init__(self, logger: logging.Logger, config_hash: str = "") -> None:
        self._log = logger
        self._hash = config_hash

    def _emit(self, level: int, event: str, **kwargs: Any) -> None:
        self._log.log(level, event, extra={"event": event, **kwargs})

    # ------------------------------------------------------------------
    # State machine transitions (§5.3)
    # ------------------------------------------------------------------

    def state_transition(self, inst: str, from_state: str, to_state: str,
                         trigger: str, **kwargs: Any) -> None:
        self._emit(
            logging.INFO, "state_transition",
            inst=inst, from_state=from_state, to_state=to_state,
            trigger=trigger, **kwargs,
        )

    # ------------------------------------------------------------------
    # Rejection log (§11.2 — non-optional)
    # ------------------------------------------------------------------

    def rejection(self, inst: str, gate: str, score: int | None = None,
                  reason: str = "", **kwargs: Any) -> None:
        """
        Log a gate failure.  gate must name the failed gate (G1–G10) or the
        named rejection reason (e.g. 'dollar_disagrees', 'notional_exhausted',
        'cost_gate', 'rho_kill').
        """
        self._emit(
            logging.INFO, "rejection",
            inst=inst, gate=gate, score=score, reason=reason, **kwargs,
        )

    # ------------------------------------------------------------------
    # Trade events (§15.4 trade log)
    # ------------------------------------------------------------------

    def trade_entry(self, inst: str, direction: str, entry: float,
                    stop: float, tp1: float, qty: float, score: int,
                    spread_at_placement: float, s_norm: float, cost_share: float,
                    bias_b: int, bias_b_usd: int, dxy_bias: str,
                    risk_pct: float, entry_array_type: str, pool_tier: str,
                    env_class: str, **kwargs: Any) -> None:
        self._emit(
            logging.INFO, "trade_entry",
            inst=inst, direction=direction, entry=entry, stop=stop, tp1=tp1,
            qty=qty, score=score,
            spread_at_placement=spread_at_placement, s_norm=s_norm,
            cost_share=cost_share, bias_b=bias_b, bias_b_usd=bias_b_usd,
            dxy_bias=dxy_bias, risk_pct=risk_pct,
            entry_array_type=entry_array_type, pool_tier=pool_tier,
            env_class=env_class, **kwargs,
        )

    def trade_exit(self, inst: str, direction: str, exit_price: float,
                   r_outcome: float, mae: float, mfe: float,
                   realised_slippage: float, spread_at_fill: float,
                   exec_fault: bool, reason: str, **kwargs: Any) -> None:
        self._emit(
            logging.INFO, "trade_exit",
            inst=inst, direction=direction, exit_price=exit_price,
            r_outcome=r_outcome, mae=mae, mfe=mfe,
            realised_slippage=realised_slippage, spread_at_fill=spread_at_fill,
            exec_fault=exec_fault, reason=reason, **kwargs,
        )

    def scale_out(self, inst: str, tranche: str, qty: float,
                  fill_price: float, r_at_fill: float, **kwargs: Any) -> None:
        self._emit(
            logging.INFO, "scale_out",
            inst=inst, tranche=tranche, qty=qty,
            fill_price=fill_price, r_at_fill=r_at_fill, **kwargs,
        )

    # ------------------------------------------------------------------
    # Operator events (§15.4 — per event)
    # ------------------------------------------------------------------

    def kill_switch(self, mode: str, operator: str, reason: str,
                    open_positions: int, open_orders: int, **kwargs: Any) -> None:
        self._emit(
            logging.WARNING, "kill_switch",
            mode=mode, operator=operator, reason=reason,
            open_positions=open_positions, open_orders=open_orders, **kwargs,
        )

    # ------------------------------------------------------------------
    # Circuit breakers
    # ------------------------------------------------------------------

    def circuit_breaker(self, trigger: str, value: float,
                        threshold: float, **kwargs: Any) -> None:
        self._emit(
            logging.WARNING, "circuit_breaker",
            trigger=trigger, value=value, threshold=threshold, **kwargs,
        )

    # ------------------------------------------------------------------
    # Execution quality
    # ------------------------------------------------------------------

    def spread_lockout(self, inst: str, spread: float, s_norm: float,
                       duration_min: int, **kwargs: Any) -> None:
        self._emit(
            logging.INFO, "spread_lockout",
            inst=inst, spread=spread, s_norm=s_norm,
            duration_min=duration_min, **kwargs,
        )

    def cost_fault(self, inst: str, cost_share: float, spread: float,
                   s_norm: float, **kwargs: Any) -> None:
        self._emit(
            logging.WARNING, "cost_fault",
            inst=inst, cost_share=cost_share, spread=spread,
            s_norm=s_norm, **kwargs,
        )

    def execution_fault(self, inst: str, slippage_atr: float,
                        threshold_atr: float, **kwargs: Any) -> None:
        self._emit(
            logging.WARNING, "execution_fault",
            inst=inst, slippage_atr=slippage_atr,
            threshold_atr=threshold_atr, **kwargs,
        )

    # ------------------------------------------------------------------
    # Compliance
    # ------------------------------------------------------------------

    def compliance_flat(self, reason: str, positions_closed: int,
                        **kwargs: Any) -> None:
        """Hard flat at 16:55 NY — if this fires late, that is a critical fault."""
        self._emit(
            logging.CRITICAL if reason == "position_open_at_1700" else logging.INFO,
            "compliance_flat", reason=reason,
            positions_closed=positions_closed, **kwargs,
        )

    def critical_fault(self, description: str, **kwargs: Any) -> None:
        """Any condition flagged as a critical fault in the spec."""
        self._emit(logging.CRITICAL, "critical_fault",
                   description=description, **kwargs)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def setup_logging(
    config_hash: str = "",
    log_dir: str | None = None,
    level: str | None = None,
) -> EngineLogger:
    """
    Configure Python logging and return an EngineLogger.

    Writes to stdout (always) and to a rotating file in log_dir (if set or
    if the LOG_DIR environment variable is set).  Secrets must never reach
    these sinks — callers are responsible for not passing credentials to any
    log call.
    """
    log_level = getattr(
        logging,
        (level or os.environ.get("LOG_LEVEL", LOG_LEVEL_DEFAULT)).upper(),
        logging.INFO,
    )

    root = logging.getLogger("fx_engine")
    root.setLevel(log_level)

    if not root.handlers:
        # Stdout handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter(config_hash))
        root.addHandler(handler)

        # File handler (optional)
        _log_dir = log_dir or os.environ.get("LOG_DIR")
        if _log_dir:
            Path(_log_dir).mkdir(parents=True, exist_ok=True)
            from logging.handlers import RotatingFileHandler
            fh = RotatingFileHandler(
                Path(_log_dir) / "fx_engine.jsonl",
                maxBytes=50 * 1024 * 1024,  # 50 MB
                backupCount=10,
            )
            fh.setFormatter(JsonFormatter(config_hash))
            root.addHandler(fh)

    return EngineLogger(root, config_hash)
