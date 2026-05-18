"""Knowledge packs — curated fact seeds for heatmap-weak cells.

R-F141 (2026-05-10) introduces the first pack: LatAm-non-Lusophone +
Asia-Pacific. Future packs land here as separate modules.

Each pack exposes:
  async seed_facts(skip_if_seeded: bool = True) -> dict
  def catalogue() -> dict
"""
from . import latam_asia_pac_seed  # noqa: F401
from . import balkans_seed  # R-F697 (2026-05-18) — gate #2 closure

__all__ = ["latam_asia_pac_seed", "balkans_seed"]
