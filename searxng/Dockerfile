# R-F1603 — self-hosted SearXNG for ARIA's native search backend (§6: no paid
# third-party). Restores the robust multi-backend search that regressed when
# Brave was declined + the public SearXNG instances were removed (2026-04-20).
# Queried by aria-intel via aria-searxng.internal:8080 (search_searxng adapter,
# SEARXNG_URL). The official entrypoint substitutes the secret_key placeholder
# with $SEARXNG_SECRET at boot, so no secret lives in the repo.
FROM searxng/searxng:latest

# Our settings enable the JSON API (off by default) that the adapter needs.
# Build context is the searxng/ dir (see fly.searxng.toml), so path is local.
COPY settings.yml /etc/searxng/settings.yml
