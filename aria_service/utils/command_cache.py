"""
Command cache to prevent loop guard.
Stores (command_hash -> output) so ARIA never repeats an identical run call.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any


class CommandCache:
    """In-memory cache of command outputs to prevent repetitive run calls."""

    def __init__(self, max_size: int = 200):
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_size = max_size

    def _hash(self, command: str) -> str:
        return hashlib.md5(command.encode()).hexdigest()

    def get(self, command: str) -> dict[str, Any] | None:
        """Return cached output if command was run before with same result."""
        key = self._hash(command)
        entry = self._cache.get(key)
        if entry:
            # Move to end (most recently used)
            self._cache.move_to_end(key)
        return entry

    def set(self, command: str, output: str, exit_code: int = 0) -> None:
        """Cache a command's output."""
        key = self._hash(command)
        self._cache[key] = {
            "output": output[:500],
            "exit_code": exit_code,
            "full_output": output,
        }
        # Evict oldest if over max size
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def is_duplicate(self, command: str, output: str) -> bool:
        """Check if this command+output has been seen before."""
        cached = self.get(command)
        if cached and cached["output"] == output[:500]:
            return True
        return False

    def clear(self) -> None:
        self._cache.clear()


# Singleton
_command_cache = CommandCache()


def get_command_cache() -> CommandCache:
    return _command_cache


def should_skip_run(command: str, current_output: str) -> bool:
    """Check if a run command should be skipped (duplicate)."""
    cache = get_command_cache()
    if cache.is_duplicate(command, current_output):
        return True
    cache.set(command, current_output)
    return False
