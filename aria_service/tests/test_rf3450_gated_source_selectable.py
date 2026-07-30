"""R-F3450 — a gated source that is not usable yet must still be ORDERABLE.

THE DEFECT, reported by the operator as "the CCJ select option is missing from the new DD
interface". The row rendered, but its checkbox did not: `dd-reports.html` emitted

    const disabled = o.available ? '' : ' disabled';

so Registry Trust (the ONLY authoritative source for England & Wales CCJs, and the only
resolver IS-17b declares) could never be ticked. Find Case Law was in the same position.

Two consequences, and the second is worse than the first:

  * The operator cannot order the search, so demand for it is invisible — exactly the
    signal needed to justify the commercial agreement that would make it available.
  * `ddBuildScope` turns every unticked box into a WAIVER. A disabled box is always
    unticked, so the form was recording a waiver the operator never gave — and a waiver
    "stays in the denominator and can never improve a score" (R-F3406). The report then
    reads as though the customer declined a check they were never offered.

The backend has always handled the honest case: an election against an unusable source
comes back ORDERED BUT NOT SEARCHED, names the blocker, and is excluded from anything
chargeable (R-F3408/R-F3436). Only the form stood in the way.

WHY THIS TEST EXECUTES THE RENDERER. A grep for the absence of `disabled` proves the
string is gone, not that the markup is right — the UI-unverified-claim class. So the real
callback is extracted from the page and RUN in node against the three option shapes the
live endpoint returns, and the emitted HTML is asserted.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PAGE = REPO / "public" / "dd-reports.html"
CSS = REPO / "public" / "css" / "aria.css"

# The three rows the live /dd/scope-options endpoint returns for a GB company today.
_OPTIONS = [
    {"source_id": "registry_trust",
     "name": "Registry Trust (Register of Judgments, Orders and Fines)",
     "available": False, "required": True,
     "unavailable_reason": "No CCJ backend configured. Registry Trust is the only "
                           "authoritative source for England & Wales and has no public API.",
     "decision": "BLOCKING: these questions cannot be answered without it",
     "required_for": [{"question_id": "IS-17b"}], "enhances": []},
    {"source_id": "opensanctions", "name": "OpenSanctions consolidated screening",
     "available": True, "required": True, "unavailable_reason": "",
     "decision": "REQUIRED: usable now; select to search, decline to waive",
     "required_for": [{"question_id": "IS-13"}, {"question_id": "IS-13b"}], "enhances": []},
    {"source_id": "find_case_law", "name": "Find Case Law (The National Archives)",
     "available": False, "required": False,
     "unavailable_reason": "The Open Justice Licence forbids computational analysis "
                           "without a separate application to The National Archives.",
     "decision": "OPTIONAL: unavailable, and something else covers these",
     "required_for": [], "enhances": [{"question_id": "IS-17a"}]},
]


def _render() -> str:
    """Extract the REAL renderer from the page and execute it."""
    src = PAGE.read_text(encoding="utf-8")
    # Anchored on the OUTER indentation: the row builder contains its own inner
    # `}).join('')` for the question chips, and a lazy match stops at that one instead —
    # which is how the first cut of this test failed against correct page code.
    m = re.search(r"box\.className = 'dd-src-list';(.*?)\n      \}\)\.join\(''\);", src, re.S)
    assert m, "could not find the gated-source renderer in dd-reports.html"
    body = "box.className = 'dd-src-list';" + m.group(1) + "\n}).join('');"

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the renderer")

    js = (
        "const escText = (s) => String(s == null ? '' : s).replace(/[&<>\"']/g,"
        " c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
        "const d = " + json.dumps({"options": _OPTIONS}) + ";\n"
        "const box = {};\n"
        + body + "\n"
        "process.stdout.write(box.innerHTML);\n"
    )
    out = subprocess.run([node, "--input-type=module", "-e", js],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"renderer failed to execute:\n{out.stderr}"
    return out.stdout


def test_capability_the_ccj_checkbox_is_selectable():
    """THE OPERATOR'S SYMPTOM: the CCJ option must be tickable."""
    html = _render()
    assert "registry_trust" in html, "the Registry Trust row is not rendered at all"
    # The whole defect in one assertion.
    assert " disabled" not in html, (
        "a gated source is still rendered disabled, so it cannot be ordered:\n" + html)
    rt = next(r for r in html.split("<label class=") if "registry_trust" in r)
    assert 'type="checkbox"' in rt, f"the CCJ row has no checkbox:\n{rt}"
    assert "dd-scope-src" in rt, "the CCJ checkbox is not collected by ddBuildScope"


def test_an_unusable_source_is_not_pre_ticked():
    """Selectable is not the same as selected. Pre-ticking would promise a search that
    cannot happen — the false-promise direction of the same bug."""
    html = _render()
    rows = html.split("<label class=")
    rt = next(r for r in rows if "registry_trust" in r)
    fcl = next(r for r in rows if "find_case_law" in r)
    osx = next(r for r in rows if "opensanctions" in r)
    assert " checked" not in rt and " checked" not in fcl
    # REQUIRED and usable -> pre-ticked, because that is the default the standard expects.
    assert " checked" in osx


def test_the_blocker_and_the_consequence_are_both_stated():
    """A gap the reader cannot act on is not a disclosure."""
    html = _render()
    assert "Why it cannot run yet" in html
    assert "order it anyway" in html
    assert "never charged" in html or "never reported as a clean one" in html
    assert "Open Justice Licence" in html, "the Find Case Law blocker text is missing"


def test_rows_use_stylesheet_classes_not_inline_styles():
    """R-F3453 — the row markup carried its own colours and spacing inline, which is why
    the panel rendered ragged. Alignment has to come from the stylesheet."""
    html = _render()
    assert "style=" not in html, f"inline styles are back in the row markup:\n{html[:400]}"
    for cls in ("dd-src", "dd-src-head", "dd-src-name", "dd-src-pill",
                "dd-src-decision", "dd-src-note"):
        assert cls in html, f"row markup does not use .{cls}"


def test_the_classes_the_markup_uses_actually_exist_in_the_stylesheet():
    """A class that is not defined renders as unstyled text — the 'looks horrible'
    symptom. Verify the two files agree."""
    css = CSS.read_text(encoding="utf-8")
    html = _render()
    used = set(re.findall(r'class="([^"]+)"', html))
    names = {c for group in used for c in group.split() if c.startswith("dd-src")}
    assert names, "no dd-src classes found in the rendered markup"
    missing = sorted(n for n in names if f".{n}" not in css)
    assert not missing, f"classes used by the markup but absent from aria.css: {missing}"


def test_the_stylesheet_bump_is_not_forgotten():
    """A CSS change behind a cache-buster the page still pins to the old value ships
    invisible. This is aria-web, which is NOT on CI, so nothing else would catch it."""
    page = PAGE.read_text(encoding="utf-8")
    m = re.search(r"css/aria\.css\?v=(\d+)", page)
    assert m, "the page no longer pins a cache-busted stylesheet"
    assert int(m.group(1)) >= 16, (
        "aria.css gained the .dd-src rules but dd-reports.html still requests an older "
        "cached version, so returning users would see unstyled rows")
