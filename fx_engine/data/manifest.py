"""Dataset manifest (§17.2 stage 6): sources, date ranges, QC results and a hash, so any
result can be tied to the exact data that produced it.  Written atomically, only after
all downloads succeed.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def dataset_hash(file_checksums: dict[str, str]) -> str:
    """Hash over (relative path, sha256) pairs, order-independent."""
    blob = json.dumps(sorted(file_checksums.items()))
    return hashlib.sha256(blob.encode()).hexdigest()


def build_manifest(instruments: dict[str, dict], file_checksums: dict[str, str],
                   sources: dict[str, str], dxy: dict | None = None) -> dict:
    """``instruments[name]`` should hold: start, end, bars_per_timeframe, qc_passed, qc (report dict)."""
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "instruments": instruments,
        "dxy": dxy or {},
        "all_qc_passed": all(v.get("qc_passed") for v in instruments.values()) and bool(instruments),
        "file_count": len(file_checksums),
        "dataset_hash": dataset_hash(file_checksums),
    }


def write_manifest(manifest: dict, path: Path) -> None:
    """Atomic write (temp file + rename).  Refuses to write if any QC failed."""
    if not manifest.get("all_qc_passed"):
        raise ValueError("refusing to write manifest: QC has not passed for every instrument")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    os.replace(tmp, path)
