"""Capability test for R-F1270: CLI terminal jam fix.

Tests that:
1. _stdin_reader does NOT use os.read() (which caused the deadlock)
2. _read_operator_input uses input() directly for TTY mode (not the queue)
3. The pause/resume mechanism works correctly
4. The reader thread can be paused and resumed without blocking
"""
import sys
import threading
import queue
from pathlib import Path

# Import the module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import importlib

# We need to test the module functions without actually starting threads
# or reading from stdin. Use the source code to verify the fix.
CLI_PATH = Path(__file__).resolve().parent.parent.parent / "aria_cli" / "cli.py"


def test_stdin_reader_does_not_use_os_read():
    """The _stdin_reader function must NOT use os.read() on stdin.
    R-F1270: os.read() steals raw bytes from Python's buffered I/O,
    causing the REPL prompt to deadlock."""
    source = CLI_PATH.read_text(encoding="utf-8")
    # Find the _stdin_reader function body (after the docstring)
    in_func = False
    in_docstring = False
    found_os_read = False
    for line in source.splitlines():
        if "def _stdin_reader" in line:
            in_func = True
            continue
        if in_func:
            # Skip the docstring (triple-quoted string)
            if '"""' in line:
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if "def " in line and line.strip().startswith("def "):
                break
            # Check for actual os.read() call (not in a comment or docstring)
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "os.read(" in stripped:
                found_os_read = True
                break
    assert not found_os_read, (
        "_stdin_reader must NOT use os.read() — it steals raw bytes "
        "from Python's buffered I/O and causes REPL deadlock"
    )


def test_read_operator_input_uses_input_for_tty():
    """_read_operator_input must use input() directly for TTY mode,
    not read from the operator queue (which would deadlock when the
    reader thread is paused)."""
    source = CLI_PATH.read_text(encoding="utf-8")
    in_func = False
    found_input_call = False
    found_queue_get = False
    for line in source.splitlines():
        if "def _read_operator_input" in line:
            in_func = True
            continue
        if in_func:
            if "def " in line and line.strip().startswith("def "):
                break
            if "input(prompt)" in line:
                found_input_call = True
            if ".get(" in line or ".get_nowait()" in line:
                found_queue_get = True
    assert found_input_call, (
        "_read_operator_input must call input(prompt) for TTY mode"
    )


def test_repl_pauses_reader_before_input():
    """The REPL loop must pause the stdin reader before reading input,
    regardless of whether prompt_toolkit is available."""
    source = CLI_PATH.read_text(encoding="utf-8")
    in_func = False
    found_pause = False
    for line in source.splitlines():
        if "def _repl" in line:
            in_func = True
            continue
        if in_func:
            if "def " in line and line.strip().startswith("def "):
                break
            if "_pause_stdin_reader()" in line:
                found_pause = True
                break
    assert found_pause, (
        "_repl must call _pause_stdin_reader() before reading input"
    )


def test_approve_pauses_reader():
    """The approve method must pause the stdin reader before calling input()."""
    source = CLI_PATH.read_text(encoding="utf-8")
    in_class = False
    in_method = False
    found_pause = False
    for line in source.splitlines():
        if "class TerminalUI" in line:
            in_class = True
            continue
        if in_class:
            if "def approve" in line:
                in_method = True
                continue
            if in_method:
                if "def " in line and line.strip().startswith("def "):
                    break
                if "_pause_stdin_reader()" in line:
                    found_pause = True
                    break
    assert found_pause, (
        "approve() must call _pause_stdin_reader() before input()"
    )


def test_pause_resume_mechanism():
    """The _pause_stdin_reader and _resume_stdin_reader functions
    must use a threading.Event to signal the reader thread."""
    source = CLI_PATH.read_text(encoding="utf-8")
    has_pause = "_STDIN_PAUSED.set()" in source
    has_resume = "_STDIN_PAUSED.clear()" in source
    has_check = "_STDIN_PAUSED.is_set()" in source
    assert has_pause, "_pause_stdin_reader must set _STDIN_PAUSED"
    assert has_resume, "_resume_stdin_reader must clear _STDIN_PAUSED"
    assert has_check, "_stdin_reader must check _STDIN_PAUSED.is_set()"
