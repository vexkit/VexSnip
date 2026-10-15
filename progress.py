#!/usr/bin/env python3
"""CloudChecker v3.1 – Progress persistence for resume support.

Tracks the current index into the username list. Saves periodically.
On restart with the same list (verified by signature), resumes.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


class ProgressTracker:
    """Atomic progress checkpointing.

    Signature = SHA1 of:
      - first 100 names (order-sensitive)
      - total count
      - sum of lengths of first 100 names

    This is fast, stable, and detects "same list" without storing
    the full list on disk.
    """

    def __init__(self, path: str | Path = "data/progress.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_saved_idx = 0
        self._last_save_time = 0.0

    @staticmethod
    def compute_signature(names: list[str]) -> str:
        if not names:
            return "empty"
        sample = names[:100]
        h = hashlib.sha1()
        for n in sample:
            h.update(n.encode("utf-8"))
            h.update(b"\n")
        h.update(str(len(names)).encode("ascii"))
        h.update(b"|")
        h.update(str(sum(len(n) for n in sample)).encode("ascii"))
        return h.hexdigest()[:16]

    def load(self) -> dict:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self, idx: int, total: int, signature: str) -> None:
        data = {
            "idx": int(idx),
            "total": int(total),
            "signature": signature,
            "saved_at": time.time(),
        }
        tmp = str(self.path) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
            self._last_saved_idx = idx
            self._last_save_time = time.time()
        except Exception:
            pass

    def can_resume(self, names: list[str]) -> tuple[bool, int]:
        """Returns (can_resume, saved_idx)."""
        data = self.load()
        if not data:
            return False, 0
        if data.get("signature") != self.compute_signature(names):
            return False, 0
        if int(data.get("total", -1)) != len(names):
            return False, 0
        idx = int(data.get("idx", 0))
        if idx <= 0 or idx >= len(names):
            return False, 0
        return True, idx

    def clear(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass