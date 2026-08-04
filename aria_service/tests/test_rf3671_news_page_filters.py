"""R-F3671 — the news page must never offer a category filter it cannot serve.

THE OPERATOR'S SYMPTOM (https://imaria.io/news.html, 2026-08-04): nine filter
buttons across the top — All, crisis early warning, cyber security, defence
global, defence regional, geopolitics, maritime risk, regional news, security,
technology — above a "Coverage by Category" panel listing only seven. Clicking
"cyber security" or "security" returned nothing, every time, because the corpus
held zero articles in either.

The cause was that the two panels were built from two different populations: the
buttons from ``stats.categories`` (the CONFIGURED feeds) and the breakdown from
``stats.by_category`` (the RETAINED CORPUS). ``test_rf3671_rf3672_news_category_honesty``
covers the server half; this drives the REAL page renderer, because a correct
API can still be rendered into a lying page and nothing else in the suite
executes this file (aria-web is not on CI).

The payload below is the exact one measured on aria-intel on 2026-08-04.
Verified to FAIL against the pre-fix page with 7 assertions, including both
"clickable but has 0 articles" errors (§3c).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[2] / "public" / "news.html"

# Measured live on aria-intel 2026-08-04 via GET /api/aria/news/stats.
_LIVE_STATS = {
    "total_sources": 44,
    "recent_articles": 1000,
    "retention_limit": 1000,
    "by_category": {
        "regional_news": 647, "defence_global": 171, "geopolitics": 54,
        "maritime_risk": 51, "technology": 27, "crisis_early_warning": 26,
        "defence_regional": 24,
    },
    "categories": [
        "crisis_early_warning", "cyber_security", "defence_global",
        "defence_regional", "geopolitics", "maritime_risk", "regional_news",
        "security", "technology",
    ],
    "categories_with_articles": [
        "crisis_early_warning", "defence_global", "defence_regional",
        "geopolitics", "maritime_risk", "regional_news", "technology",
    ],
    "empty_categories": ["cyber_security", "security"],
    "poll_state": {},
}

_SHIM = """
class El {
  constructor(){ this._html=''; this.textContent=''; this.attrs={}; this.dataset={};
    this.style={}; this.classList={remove(){},add(){},contains:()=>false}; }
  set innerHTML(v){ this._html=v; } get innerHTML(){ return this._html; }
  setAttribute(k,v){ this.attrs[k]=v; } removeAttribute(k){ delete this.attrs[k]; }
  set title(v){ this.attrs.title=v; } get title(){ return this.attrs.title; }
  addEventListener(){}
  querySelectorAll(sel){
    const out=[]; const re=/<button([^>]*)>([\\s\\S]*?)<\\/button>/g; let m;
    while((m=re.exec(this._html))){
      const attrs=m[1];
      const disabled=/\\sdisabled(\\s|$)/.test(attrs);
      if(sel.includes(':not([disabled])') && disabled) continue;
      const b=new El();
      b.dataset.category=(attrs.match(/data-category="([^"]*)"/)||[])[1];
      out.push(b);
    }
    return out;
  }
}
const els={};
const document={ getElementById:(id)=>(els[id]||=new El()), addEventListener(){},
                 querySelectorAll:()=>[] };
const escapeHtml=(s)=>String(s==null?'':s);
"""


def _run_page(stats: dict) -> dict:
    """Execute the page's OWN renderer against `stats` and report what it drew."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the page renderer")

    src = PAGE.read_text(encoding="utf-8")
    m = re.search(r"<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>", src, re.S)
    assert m, "could not find the inline script block in news.html"
    body = m.group(1)

    # The page body is an IIFE, so the functions are not reachable from outside
    # it. Export them from INSIDE, immediately before it closes.
    close = body.rstrip().rfind("})();")
    assert close > 0, "could not find the IIFE close in news.html"
    body = (body[:close]
            + "\n;globalThis.__x={renderCategoryFilters,updateKPI};\n"
            + body[close:])

    js = (
        _SHIM
        + "const __run=new Function('document','escapeHtml','localStorage','fetch',"
          "'API','window'," + json.dumps(body + "\n;return globalThis.__x;") + ");\n"
        + "const x=__run(document,escapeHtml,{getItem:()=>''},()=>{},undefined,{});\n"
        + "const stats=" + json.dumps(stats) + ";\n"
        + "x.renderCategoryFilters(stats);\n"
        + "x.updateKPI(stats,[]);\n"
        + "const f=els['category-filter'];\n"
        + "process.stdout.write(JSON.stringify({\n"
        + "  html: f.innerHTML,\n"
        + "  enabled: f.querySelectorAll('.category-btn:not([disabled])')\n"
        + "            .map(b=>b.dataset.category).filter(c=>c&&c!=='all'),\n"
        + "  kpi: els['kpi-categories'].textContent,\n"
        + "  kpiTitle: els['kpi-categories'].title || '',\n"
        + "}));\n"
    )
    out = subprocess.run([node, "--input-type=module", "-e", js],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"page renderer failed to execute:\n{out.stderr}"
    return json.loads(out.stdout)


def test_capability_no_filter_button_is_offered_for_an_empty_category():
    """THE SYMPTOM: a clickable filter that can only ever answer nothing."""
    drawn = _run_page(_LIVE_STATS)
    by_cat = _LIVE_STATS["by_category"]
    offered_but_empty = [c for c in drawn["enabled"] if not by_cat.get(c)]
    assert not offered_but_empty, (
        "these filters are clickable but hold zero articles: "
        f"{offered_but_empty}\n{drawn['html']}"
    )


def test_capability_filters_match_the_breakdown_exactly():
    """The two panels answered different questions; now they cannot."""
    drawn = _run_page(_LIVE_STATS)
    assert sorted(drawn["enabled"]) == sorted(_LIVE_STATS["by_category"]), (
        "the filter row and Coverage by Category disagree"
    )


def test_dark_categories_are_shown_but_not_clickable():
    """Fixing this by DELETING the empty categories would hide a dead feed.

    `security`'s only source (Europol) was unreachable and `cyber_security`'s two
    feeds had stalled — precisely what an operator needs to see. They stay on the
    page, disabled, rather than vanishing.
    """
    drawn = _run_page(_LIVE_STATS)
    for cat in _LIVE_STATS["empty_categories"]:
        assert f'data-category="{cat}"' in drawn["html"], f"{cat} was hidden entirely"
        assert cat not in drawn["enabled"], f"{cat} is still clickable"


def test_kpi_tile_reports_covered_of_configured():
    """The tile read "9" for a corpus covering 7 — coverage overstated by two."""
    drawn = _run_page(_LIVE_STATS)
    assert drawn["kpi"] == "7 of 9", f'KPI tile reads {drawn["kpi"]!r}'
    assert "cyber security" in drawn["kpiTitle"], "the tooltip must name the dark categories"


def test_page_degrades_safely_against_a_server_without_the_new_fields():
    """A page deployed ahead of aria-intel must not resurrect the bug.

    Without `categories_with_articles`/`empty_categories` the renderer falls back
    to `by_category`, which still cannot offer an empty filter.
    """
    legacy = {k: v for k, v in _LIVE_STATS.items()
              if k not in ("categories_with_articles", "empty_categories")}
    drawn = _run_page(legacy)
    offered_but_empty = [c for c in drawn["enabled"] if not legacy["by_category"].get(c)]
    assert not offered_but_empty, (
        f"legacy-server fallback offers empty filters: {offered_but_empty}"
    )
