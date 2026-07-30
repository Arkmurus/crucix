"""R-F3465 — the New DD panel: block hints, a separated decision, no inline overrides.

Operator: the panel "design looks off" and "the text is looks horrible, should aligned and
professional". R-F3453 fixed the gated-source ROWS; this closes the panel around them.

THREE THINGS, each a different kind of wrong:

  * A four-line explanation was living INSIDE a <label> as inline muted text, so it
    wrapped into the label itself and rendered as one ragged block. Help that runs to a
    sentence or more is a paragraph. Short "(optional)" suffixes are not, and stay inline
    via .opt — that distinction is the point, not a detail.

  * The gated-source block sat in the same undifferentiated stack as Entity name and
    Jurisdiction. Everything above it DESCRIBES the subject; this one is a decision about
    spend and coverage that the run is then held to. Same visual weight made a decision
    look like another text field.

  * `<div id="dd-r-scope" style="display:flex;...;gap:8px">` — an inline style BEATS a
    class, so the .dd-src-list spacing added in aria.css was silently overridden by markup
    written before it existed. This is the quiet one: the stylesheet looked correct, and
    the page ignored it.

Field ORDER was reviewed and left alone: name, type, jurisdiction, registration number,
website, product context, depth, then gated sources — narrowing identity first, then the
run's shape. The gated block already sat directly above the Run DD button in sc-mod-foot.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PAGE = REPO / "public" / "dd-reports.html"
CSS = REPO / "public" / "css" / "aria.css"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return CSS.read_text(encoding="utf-8")


def test_the_long_hint_is_a_paragraph_not_a_label_suffix(page):
    assert '<p class="sc-hint">Choose before running.' in page, (
        "the gated-source explanation is back inside the label")
    # The label itself is now just the label.
    m = re.search(r"<label>Paid &amp; licence-gated sources</label>", page)
    assert m, "the label still carries the explanatory text"


def test_short_hints_stay_inline(page):
    """The guard against over-correcting: "(optional)" must NOT become a paragraph, or
    every field grows a second line and the form gets longer for no gain."""
    assert '<label>Jurisdiction <span class="opt">(optional)</span></label>' in page
    assert '<label>Entity name <span class="opt">(required)</span></label>' in page


def test_the_gated_block_is_visually_separated(page, css):
    assert 'class="sc-mod-field sc-mod-section" id="dd-r-scope-wrap"' in page
    assert ".sc-mod-section" in css, "the separator class is not defined"
    assert "border-top" in css.split(".sc-mod-section")[1][:200], (
        "sc-mod-section does not actually separate anything")


def test_no_inline_style_overrides_the_row_spacing(page):
    """An inline style beats the class, so this silently defeated aria.css."""
    assert 'id="dd-r-scope" class="dd-src-list"' in page
    assert 'id="dd-r-scope" style=' not in page, (
        "an inline layout style is back on the scope container and will override "
        ".dd-src-list")


def test_the_hint_class_is_defined_and_readable(css):
    assert ".sc-hint" in css
    block = css.split(".sc-hint")[1][:260]
    assert "line-height" in block, "hint text with no line-height is the 'horrible' symptom"
    assert "max-width" in block, (
        "an unbounded measure makes long help text run the full panel width and read as a "
        "wall")


def test_the_stylesheet_bump_is_not_forgotten(page):
    """aria-web is NOT on CI, so nothing else catches a stale cache-buster."""
    m = re.search(r"css/aria\.css\?v=(\d+)", page)
    assert m and int(m.group(1)) >= 18, (
        "aria.css gained .sc-hint/.sc-mod-section but the page still requests an older "
        "cached version")


def test_the_field_order_is_the_reviewed_one(page):
    """Order was reviewed rather than assumed; pin it so a later edit is deliberate."""
    body = page[page.index('id="dd-run-error"'):page.index('id="dd-run-submit"')]
    seq = [i for i in ("dd-r-name", "dd-r-type", "dd-r-jur", "dd-r-reg", "dd-r-url",
                       "dd-r-prod", "dd-r-mode", "dd-r-scope-wrap")]
    positions = [body.index(f'id="{i}"') for i in seq]
    assert positions == sorted(positions), (
        f"the New DD field order changed: {seq} resolved to {positions}")


def test_the_gated_selection_precedes_the_run_button(page):
    """The operator's requirement: the selection is made BEFORE the run is launched."""
    assert page.index('id="dd-r-scope-wrap"') < page.index('id="dd-run-submit"')
