"""Capability test for R-F2204 — trafilatura clean main-content extraction.

The regex extractor only reads <p> paragraphs, so modern div/React-based sites (content in
<div>, not <p>) extracted thin/empty — exactly where the operator's curated sources live.
trafilatura does proper main-content detection. This proves _extract_structured_html now
surfaces div-based body text it previously missed.

Run: python -m pytest aria_service/tests/test_trafilatura_extraction_rf2204.py -q
"""
from aria_service.intel import researcher


# Body content lives entirely in <div>s (NO <p>) — the old regex path would yield nothing.
DIV_HTML = """<html><head><title>Acme Defence Corp</title></head><body>
  <nav>Home About Products Contact</nav>
  <header><div>Acme Defence Corp</div></header>
  <main><div class="content"><div class="article-body">
    Acme Defence Corporation is a defence systems integrator headquartered in Lisbon, Portugal.
    The company designs and manufactures unmanned aerial vehicles and provides intelligence,
    surveillance and reconnaissance services to NATO member states. Founded in 2010, Acme
    employs more than 450 engineers across three facilities in Portugal and Spain. In 2024 it
    secured a EUR 40 million framework contract to supply tactical ISR drones to allied forces,
    and it is currently pursuing export licences for several Middle Eastern markets. The board
    is chaired by a former air-force general, and the firm reports annual revenue near EUR 120M.
  </div></div></main>
  <footer><div>(c) 2026 Acme Defence Corp</div></footer>
</body></html>"""


def test_trafilatura_extracts_div_based_main_content():
    res = researcher._extract_structured_html(DIV_HTML)
    text = res.get("text", "")
    assert "Acme Defence Corporation" in text, "div-based body text must be extracted (trafilatura)"
    assert "unmanned aerial vehicles" in text
    assert "ISR" in text or "intelligence" in text
    # title still captured by the regex path (parity preserved)
    assert "Acme Defence Corp" in (res.get("title") or "")


def test_extraction_never_crashes_on_empty():
    res = researcher._extract_structured_html("<html><body></body></html>")
    assert isinstance(res, dict) and "text" in res


if __name__ == "__main__":
    test_trafilatura_extracts_div_based_main_content()
    test_extraction_never_crashes_on_empty()
    print("ALL PASS")
