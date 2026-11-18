"""Immutable raw store (§17.2 stage 1): parquet files, never edited, checksum per file."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pandas as pd


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def write_immutable(df: pd.DataFrame, path: Path) -> str:
    """Write ``df`` to parquet at ``path`` plus a .sha256 sidecar.

    Refuses to overwrite an existing file.  Written to a temp file and renamed
    so a crash never leaves a partial file at the final path.  Returns the checksum.
    """
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"raw store file already exists (immutable): {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp)
    digest = sha256_file(tmp)
    os.replace(tmp, path)
    _sidecar(path).write_text(digest + "\n")
    return digest


def read_verified(path: Path) -> pd.DataFrame:
    """Read a stored parquet file after verifying its checksum."""
    path = Path(path)
    expected = _sidecar(path).read_text().strip()
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"checksum mismatch for {path}: expected {expected}, got {actual}")
    return pd.read_parquet(path)
