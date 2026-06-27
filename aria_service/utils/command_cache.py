"""
Command Cache — prevents repetitive `run` calls and loop guards.
Stores command outputs keyed by command hash. Persists to disk for
cross-session use. Includes staleness check (1 hour TTL).

Usage:
    from aria_service.utils.command_cache import CommandCache

    cache = CommandCache(persist_file="/data/command_cache.json")
    cached = cache.get("ls -la")
    if cached:
        return cached["stdout"]
    result = run_command("ls -la")
    cache.set("ls -la", result.stdout, result.stderr, result.exit_code)
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional


class CommandCache:
    """In-memory + persistent cache for command outputs.

    Prevents the loop guard by returning cached results for identical
    commands. Entries expire after CACHE_TTL seconds (default 1 hour).
    """

    CACHE_TTL = 3600  # 1 hour

    def __init__(self, persist_file: str | Path | None = None):
        self._cache: dict[str, dict[str, Any]] = {}
        self._persist_file = Path(persist_file) if persist_file else None
        if self._persist_file and self._persist_file.exists():
            self._load()

    def _key(self, command: str) -> str:
        """Generate a stable key, normalising whitespace."""
        norm = " ".join(command.split())
        return hashlib.md5(norm.encode()).hexdigest()

    def get(self, command: str) -> dict[str, Any] | None:
        """Return cached output if fresh, else None."""
        key = self._key(command)
        entry = self._cache.get(key)
        if entry:
            age = time.time() - entry.get("timestamp", 0)
            if age < self.CACHE_TTL:
                return entry
            # Stale — remove
            del self._cache[key]
            self._save()
        return None

    def set(self, command: str, stdout: str, stderr: str = "",
            exit_code: int = 0) -> None:
        """Store command output."""
        key = self._key(command)
        self._cache[key] = {
            "command": command,
            "stdout": stdout[:2000],
            "stderr": stderr[:500],
            "exit_code": exit_code,
            "timestamp": time.time(),
        }
        self._save()

    def is_duplicate(self, command: str, output: str) -> bool:
        """Check if this command+output has been seen recently."""
        cached = self.get(command)
        if cached and cached["stdout"] == output[:2000]:
            return True
        return False

    def _save(self) -> None:
        """Persist to disk."""
        if self._persist_file:
            try:
                self._persist_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self._persist_file, "w") as f:
                    json.dump(self._cache, f, indent=2)
            except Exception:
                pass

    def _load(self) -> None:
        """Load from disk."""
        if self._persist_file and self._persist_file.exists():
            try:
                with open(self._persist_file) as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    def clear(self) -> None:
        self._cache.clear()
        self._save()
