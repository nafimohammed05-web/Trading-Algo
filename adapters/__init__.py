"""
Adapters — the only layer that changes between backtest / paper / live.
One code path rule (§17.1): the cascade engine is identical across all modes;
only adapters differ.
"""
