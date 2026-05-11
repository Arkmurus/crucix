FROM python:3.13-slim

WORKDIR /app

# ── System dependencies for local OCR (EasyOCR + Tesseract + PyMuPDF) ──────
# python:3.13-slim is a minimal image. EasyOCR pulls PyTorch + OpenCV which
# need libgl1, libglib2.0-0, libsm6, libxext6, libxrender1 to run. Tesseract
# needs the OCR binary itself + language packs. Without these the pip install
# of easyocr appears to succeed but the import fails at runtime, leaving ARIA
# silently dependent on the OCR.space free fallback for every image.
#
# This adds ~120 MB to the image but unlocks fully-local independent OCR.
RUN apt-get update && apt-get install -y --no-install-recommends \
    # EasyOCR / OpenCV runtime
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    # Tesseract OCR binary + language packs
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-por \
    tesseract-ocr-fra \
    tesseract-ocr-spa \
    # Build tools — required for some pip wheels that compile from source
    gcc \
    g++ \
    # curl — used to download Lightpanda binary at build time
    curl \
    # SQLite for chromadb persistent backend (modern version)
    sqlite3 \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# ── RAG store mount point ──────────────────────────────────────────────────
# chromadb persists to /data/aria_rag — mount a fly.io volume here:
#   flyctl volumes create aria_data --region <region> --size 5 -a aria-intel
# If no volume is mounted, the rag_store will fall back to /tmp/aria_rag
# (works but doesn't persist across restarts).
RUN mkdir -p /data/aria_rag && chmod 755 /data/aria_rag

COPY aria_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ── Lightpanda headless browser (JS rendering, 24MB) ──────────────────────
# Written in Zig — 11x faster than Chrome, 9x less memory. Speaks CDP so
# Playwright connects as a thin client. Only invoked when httpx returns
# thin/JS-only content (React SPAs, dashboards, procurement portals).
# Lightpanda handles most JS rendering as the first fallback; Chromium is
# installed below (line ~90) for the heavy-fallback scraper.
RUN curl -fsSL -o /usr/local/bin/lightpanda \
    https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux \
    && chmod +x /usr/local/bin/lightpanda

# ── Pre-bake sentence-transformers model into image ─────────────────────────
# Past incident 2026-04-07: every cold start triggered the all-MiniLM-L6-v2
# model download from HuggingFace (~80MB, 30-90s blocking I/O), called
# inline by one of the intel module init functions during lifespan startup.
# This blocked uvicorn from binding to 0.0.0.0:8000, fly health checks
# failed, and the deploy rolled back.
#
# Baking the model at image build time means it's already on disk at boot,
# so the first _get_embedder() call is a fast file read instead of an HTTP
# download. Adds ~80MB to the image but makes cold starts predictable.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY aria_service/ ./aria_service/

# ── One-shot scripts (migrations, evals, smoke tests) ─────────────────────
# R-F261 (2026-05-11) — the Upstash → SQLite state migration script lives
# under scripts/migrate_state.py and is invoked via the HTTP admin endpoint
# `POST /api/aria/admin/migrate-state`. Without this COPY, the endpoint
# raises ImportError at runtime because scripts/ isn't in the image.
# Also future-proofs eval/smoke/admin scripts that need server-side execution.
COPY scripts/ ./scripts/

# ── Playwright Chromium install (2026-04-18) ──────────────────────────────
# Install Chromium + dependencies for the scraper package
# (aria_service/intel/scraper/*). This is the HEAVY browser fallback
# beyond Lightpanda — used only for JS-heavy sites that Lightpanda
# can't render AND aren't known publishers AND aren't covered by a
# direct API. Adds ~400 MB to the image but unlocks the final 20% of
# the crawl-capability audit (SEACE Peru, SSB Turkey, etc.).
#
# Memory: Chromium is ~300 MB resident per browser instance. We cap
# concurrency at 2 via ARIA_PLAYWRIGHT_MAX_CONCURRENT so the 4 GB
# fly.io machine has headroom.
#
# If this install ever blocks a deploy, set
# ARIA_PLAYWRIGHT_DISABLED=1 on fly and the scraper package falls back
# silently (crawl_enhancements detects ImportError and skips).
# Use `--with-deps` so Playwright's own upstream-maintained apt-package
# list is used (it tracks Debian trixie vs bookworm package renames like
# libasound2 → libasound2t64; hard-coding the list here drifted and broke
# a 2026-04-20 deploy). CRITICAL: no silent `|| echo` fallback — the pre-
# 2026-04-20 version had that, and when `--with-deps` partially failed
# on a base-OS upgrade the image shipped without chromium for weeks and
# the scraper was silently unavailable. If this step fails, the build
# fails loudly and we fix it. To intentionally skip Playwright: set
# ARIA_PLAYWRIGHT_DISABLED=1 at runtime.
RUN playwright install --with-deps chromium

# NOTE: EasyOCR model weights are NOT pre-warmed at build time anymore.
# Pre-warming with 4 languages forced ~600MB of PyTorch + model weights into
# the build container, then caused OOM kills on small fly.io machines because
# the running service couldn't fit alongside everything else. EasyOCR now
# downloads weights LAZILY on the first image request — that first OCR call
# takes ~30s but every subsequent one is instant. The languages are also
# English-only by default (set ARIA_OCR_LANGS=en,pt,fr,es to add more).

EXPOSE 8000

CMD ["python", "-m", "aria_service.main"]
