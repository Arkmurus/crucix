"""Capability test for find_direct_function_calls (R-F1268)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from pre_commit_checks import find_direct_function_calls


def test_find_direct_function_calls_detects_await():
    """await module.function() should be detected."""
    lines = ["    result = await some_module.some_function(arg1, arg2)"]
    calls = find_direct_function_calls(lines)
    assert len(calls) == 1
    assert calls[0]["object"] == "some_module"
    assert calls[0]["function"] == "some_function"


def test_find_direct_function_calls_detects_non_await():
    """module.function() without await should also be detected."""
    lines = ["    some_module.some_function(arg1, arg2)"]
    calls = find_direct_function_calls(lines)
    assert len(calls) == 1
    assert calls[0]["object"] == "some_module"
    assert calls[0]["function"] == "some_function"


def test_find_direct_function_calls_detects_both():
    """Both await and non-await calls should be detected."""
    lines = [
        "    await some_module.async_func()",
        "    some_module.sync_func()",
    ]
    calls = find_direct_function_calls(lines)
    assert len(calls) == 2


def test_find_direct_function_calls_skips_exempt():
    """Exempt modules (os, sys, json, etc.) should be skipped."""
    lines = [
        "    result = json.dumps(data)",
        "    path = os.pathsep",
    ]
    calls = find_direct_function_calls(lines)
    assert len(calls) == 0, f"Expected no exempt calls, got: {calls}"


def test_find_direct_function_calls_skips_dunder():
    """Dunder methods should be skipped."""
    lines = ["    obj.__init__()"]
    calls = find_direct_function_calls(lines)
    assert len(calls) == 0, f"Expected no dunder calls, got: {calls}"
