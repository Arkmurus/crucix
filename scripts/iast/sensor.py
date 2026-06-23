"""R-F1808 — IAST-lite runtime sensor.

NOT a commercial IAST agent and NOT a full taint tracker. An honest, in-process
instrument: it wraps the high-severity injection/deserialization/SSRF/file "sinks"
and records — the first time each *app-code call site* reaches a sink during a run
— the sink, an argument sample, and the app frame (file:line) that invoked it.

Output answers the IAST question — "which dangerous sinks did real exercised code
paths actually reach, and from where in source?" — which a post-run analysis then
turns into a confirmed-exploitable judgement by reading that source and deciding
whether the argument is attacker-controlled and unsanitised.

Hot path uses sys._getframe (no source reads) so it stays cheap across a full QA run.
SQL (sqlite3/aiosqlite) is a C-extension method, not reliably monkeypatchable
in-process — reported as a coverage gap for source review instead.
"""
from __future__ import annotations

import builtins
import json
import os
import sys

_SEEN: dict = {}        # (sink, caller_site) -> record
_INSTALLED = False
_orig: dict = {}


def _is_app(fn: str) -> bool:
    fn = fn.replace("\\", "/")
    if "/site-packages/" in fn or "/lib/python" in fn:
        return False
    if "scripts/iast/" in fn:
        return False
    return ("/aria_service/" in fn or "/lib/" in fn or fn.endswith("server.mjs"))


def _iter_frames(start: int):
    try:
        f = sys._getframe(start)
    except ValueError:
        return
    while f is not None:
        yield f
        f = f.f_back


def _app_caller():
    for f in _iter_frames(3):
        if _is_app(f.f_code.co_filename):
            return (f.f_code.co_filename.replace("\\", "/") + ":" + str(f.f_lineno), f.f_code.co_name)
    for f in _iter_frames(3):
        fn = f.f_code.co_filename.replace("\\", "/")
        if "/site-packages/" in fn or "scripts/iast/" in fn:
            continue
        return (fn + ":" + str(f.f_lineno), f.f_code.co_name)
    return ("?", "?")


def _has_app_frame(maxdepth: int = 8) -> bool:
    n = 0
    for f in _iter_frames(3):
        if _is_app(f.f_code.co_filename):
            return True
        n += 1
        if n >= maxdepth:
            break
    return False


def _preview(val) -> str:
    try:
        s = val if isinstance(val, str) else repr(val)
    except Exception:
        s = "<unrepr>"
    return s[:300]


def _record(sink: str, category: str, arg) -> None:
    site, func = _app_caller()
    key = (sink, site)
    rec = _SEEN.get(key)
    if rec is not None:
        rec["count"] += 1
        return
    _SEEN[key] = {
        "sink": sink, "category": category, "caller": site, "func": func,
        "arg_sample": _preview(arg), "count": 1,
    }


def _wrap(mod, name, sink_label, category, arg_index=0):
    if not hasattr(mod, name):
        return
    orig = getattr(mod, name)
    _orig[(mod, name)] = orig

    def wrapper(*args, **kwargs):
        try:
            if _has_app_frame():
                if len(args) > arg_index:
                    arg = args[arg_index]
                else:
                    arg = kwargs.get("args") or kwargs.get("url") or kwargs.get("file") or ""
                _record(sink_label, category, arg)
        except Exception:
            pass
        return orig(*args, **kwargs)

    setattr(mod, name, wrapper)


def _wrap_open():
    orig = builtins.open
    _orig[(builtins, "open")] = orig

    def wrapper(file, *a, **k):
        try:
            mode = (a[0] if a else k.get("mode", "r"))
            interesting = ("w" in str(mode) or "a" in str(mode) or "+" in str(mode))
            if interesting and _has_app_frame():
                _record("builtins.open(write)", "path_or_file_write", str(file))
        except Exception:
            pass
        return orig(file, *a, **k)

    builtins.open = wrapper


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    import subprocess
    # NB: do NOT wrap subprocess.Popen — it is a CLASS that stdlib (asyncio
    # windows_utils) subclasses; replacing it with a function breaks subclassing.
    # Wrap the function-level command entry points app code actually calls.
    _wrap(subprocess, "run", "subprocess.run", "command_injection")
    _wrap(subprocess, "call", "subprocess.call", "command_injection")
    _wrap(subprocess, "check_call", "subprocess.check_call", "command_injection")
    _wrap(subprocess, "check_output", "subprocess.check_output", "command_injection")
    _wrap(os, "system", "os.system", "command_injection")
    _wrap(os, "popen", "os.popen", "command_injection")

    # NB: builtins.eval/exec are intentionally NOT globally wrapped — Python's
    # import machinery + pytest's assertion rewrite call exec() on code objects
    # during bootstrap, so wrapping them is invasive and fragile. Code-injection
    # sinks (eval/exec/compile) are covered by source review instead (reported as
    # a coverage note).

    import pickle
    _wrap(pickle, "loads", "pickle.loads", "deserialization")
    _wrap(pickle, "load", "pickle.load", "deserialization")
    try:
        import marshal
        _wrap(marshal, "loads", "marshal.loads", "deserialization")
    except Exception:
        pass
    try:
        import yaml
        _wrap(yaml, "load", "yaml.load", "deserialization")
    except Exception:
        pass

    _wrap_open()

    try:
        import httpx
        _wrap(httpx.Client, "request", "httpx.Client.request", "ssrf", arg_index=1)
        _wrap(httpx.AsyncClient, "request", "httpx.AsyncClient.request", "ssrf", arg_index=1)
    except Exception:
        pass
    try:
        import requests.sessions as _rs
        _wrap(_rs.Session, "request", "requests.Session.request", "ssrf", arg_index=1)
    except Exception:
        pass

    _INSTALLED = True


def records() -> list:
    return list(_SEEN.values())


def dump(path: str) -> int:
    recs = records()
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return len(recs)
