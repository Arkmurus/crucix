"""Return the canonical ARIA landing page.

R-F3297 — The landing page now uses the licensed Pelican template directly.
Keeping a second, hand-built HTML generator would allow the retired design and
its stale claims to overwrite the canonical page, so this compatibility entry
point reads the reviewed artefact instead.
"""

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_LANDING = _ROOT / "public" / "index.html"


def build_landing_page() -> str:
    """Return the complete, reviewed landing-page HTML."""

    return _LANDING.read_text(encoding="utf-8")


if __name__ == "__main__":
    print(build_landing_page())
