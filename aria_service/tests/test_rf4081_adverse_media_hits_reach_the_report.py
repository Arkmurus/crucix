"""R-F4081 (C-129) — multilingual adverse-media hits were dropped by a
NameError, and the DD read clean.

`dd_orchestrator` gathers per-language adverse-media probes, then:

    for _lang, _res_list, _ok in _results:
            _adverse_hits.append({
                "title": getattr(_r, "title", "")[:240],   # _r is UNBOUND
                ...

The inner `for _r in _res_list:` was lost in `3e0d8497` (2026-08-13). Every
iteration raised `NameError`, the enclosing `except Exception as _r445_e:
logger.debug(...)` swallowed it, and `report.digital.web_footprint
["adverse_media_hits"]` was **never populated**.

So a subject with real adverse coverage in a non-English language produced an
empty hit list, indistinguishable from a genuinely clean subject. **A false
clean on the DD path**, latent for three days — the exact class C-39
(`derive_verified_sources` stamping never-searched lists as CLEAN) exists to
prevent, and R-F3955's own comment two lines above says the point of carrying
the outcome is that "a total sweep failure [must not be] indistinguishable from
a clean subject downstream".

Found by R-F1908's undefined-name gate. That gate was RED and sitting
unattributed in the standing failure set, which is why nobody had looked: a
permanently-red guard carries no information (§16).

These tests drive the transform directly rather than a whole DD run — the
defect is in how results are unpacked, and a full orchestration would need the
network. `test_the_shape_is_still_the_one_the_orchestrator_unpacks` pins the
producer/consumer contract so the two cannot drift apart again.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


class _Hit:
    def __init__(self, title, url, snippet=""):
        self.title = title
        self.url = url
        self.snippet = snippet


def _collect(results):
    """The transform exactly as dd_orchestrator now performs it."""
    hits = []
    for _lang, _res_list, _ok in results:
        for _r in (_res_list or []):
            hits.append({
                "lang": _lang,
                "title": getattr(_r, "title", "")[:240],
                "url": getattr(_r, "url", "")[:500],
                "snippet": (getattr(_r, "snippet", "")
                            or getattr(_r, "summary", "")
                            or "")[:400],
            })
    return hits


def test_hits_from_every_language_reach_the_list():
    results = [
        ("ru", [_Hit("Расследование", "https://ru.example/1", "s1")], True),
        ("zh", [_Hit("调查", "https://zh.example/1"),
                _Hit("制裁", "https://zh.example/2")], True),
    ]
    hits = _collect(results)
    assert len(hits) == 3, hits
    assert {h["lang"] for h in hits} == {"ru", "zh"}
    assert hits[0]["title"] == "Расследование"


def test_a_failed_probe_contributes_nothing_but_does_not_break_the_others():
    """`_ok=False` returns `[]` by design (R-F3955). It must not stop the
    languages that DID answer from reaching the report."""
    results = [
        ("ar", [], False),
        ("fr", [_Hit("Enquête", "https://fr.example/1")], True),
    ]
    hits = _collect(results)
    assert len(hits) == 1 and hits[0]["lang"] == "fr", hits


def test_an_empty_sweep_is_empty_not_an_exception():
    assert _collect([]) == []
    assert _collect([("de", None, True)]) == []


def test_the_orchestrator_body_binds_every_name_it_reads():
    """The regression itself: the loop body must not reference a name the loop
    never binds. Parsed from the real source, so a future edit that drops the
    inner loop again fails here rather than in production."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")
    i = src.index('"adverse_media_hits"')
    window = src[max(0, i - 2600):i]
    j = window.rindex("for _lang, _res_list, _ok in _results:")
    body = window[j:]
    assert "for _r in" in body, (
        "the loop body reads `_r` but nothing binds it — this is the exact "
        "NameError that emptied adverse_media_hits for three days")
    # and the inner loop must iterate the per-language result list
    assert "_res_list" in body.split("for _r in", 1)[1][:40], body[:400]


def test_the_shape_is_still_the_one_the_orchestrator_unpacks():
    """`_run_one` returns `(lang, results, ok)`. If that triple ever changes,
    the unpack above breaks silently again."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")
    i = src.index("async def _run_one(lang, q):")
    fn = src[i:i + 900]
    assert "return lang, await" in fn and ", True" in fn, fn[:300]
    assert "return lang, [], False" in fn, fn[:600]
