FROM python:3.13-slim

# R-F513 (2026-05-14) — build-time identity. Passed in via
# `--build-arg` from .github/workflows/deploy-fly.yml (CI) or from the
# local `flyctl deploy --build-arg ...` invocation (manual). Read by
# aria_service/main.py at module-load time so /api/aria/health/live
# reports the actual SHA running, not a hand-edited constant.
#
# Before R-F513 main.py:53 was a hand-edited string that drifted: 27
# commits on 2026-05-14 shipped with zero bumps, so /health/live kept
# reporting R-F422 while R-F509/F510 were live. Verify-after-fix was
# unreliable as a result.
ARG ARIA_BUILD_GIT_SHA=unknown
ARG ARIA_BUILD_R_TAG=
ENV ARIA_BUILD_GIT_SHA=$ARIA_BUILD_GIT_SHA
ENV ARIA_BUILD_R_TAG=$ARIA_BUILD_R_TAG

# R-F979 (2026-05-28) — cap math-library thread pools at the IMAGE level.
# R-F857 already calls torch.set_num_threads(1), but OMP/MKL/OpenBLAS read
# their thread counts at IMPORT time (before any Python runs), so the only
# reliable lever is an env var on the machine. It was a fly secret
# (OMP_NUM_THREADS) that may be unset; baking it here makes it durable on
# every boot, independent of secrets. On the shared-cpu box the all-MiniLM
# encoder is GIL-bound and tiny — extra OpenMP/BLAS worker threads only
# oversubscribe the CPU and lengthen the GIL hold during the cold-boot model
# load + first encode (the residual R-F703 cold-boot stall; heatmap was fixed
# in R-F931). 1 thread is sufficient (steady-state encodes ~50-100ms) and
# releases the GIL cleaner. TOKENIZERS_PARALLELISM=false stops HF tokenizers
# spawning its own fork-parallel threads. Mitigation, not elimination — fully
# removing the cold-boot GIL hold needs a subprocess encoder (deferred).
ENV OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    TOKENIZERS_PARALLELISM=false

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
    git \
    # SQLite for chromadb persistent backend (modern version)
    sqlite3 \
    libsqlite3-dev \
    # R-F958 — system ffmpeg removed (R-F956 added it for voice decode, but
    # faster-whisper decodes Opus/OGG via bundled PyAV/libav — system ffmpeg is
    # redundant ~70MB and every MB matters against Fly's 8GB image cap).
    && rm -rf /var/lib/apt/lists/*

# ── RAG store mount point ──────────────────────────────────────────────────
# chromadb persists to /data/aria_rag — mount a fly.io volume here:
#   flyctl volumes create aria_data --region <region> --size 5 -a aria-intel
# If no volume is mounted, the rag_store will fall back to /tmp/aria_rag
# (works but doesn't persist across restarts).
RUN mkdir -p /data/aria_rag && chmod 755 /data/aria_rag

COPY aria_service/requirements.txt ./requirements.txt
# R-F1709: install the CPU-only torch wheel FIRST. torch is pulled transitively
# (sentence-transformers / easyocr); with no pin, PyPI serves the CUDA build
# (2.12.0+cu130 ≈ 2 GB of nvidia-cuda-* libs the CPU-only Fly machine can never
# use). That download dominated — and timed out — cold deploys and pushed the
# image toward Fly's 8 GB cap. The CPU wheel is the SAME version (2.12.0 → zero
# runtime change; torch executes on CPU either way) at ~1/10th the size. Pinned
# to the live version so it's a byte-for-byte runtime equivalent, just smaller.
# Installing it before `-r requirements.txt` means torch is already satisfied,
# so the requirements install never pulls the CUDA build.
RUN pip install --no-cache-dir torch==2.12.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu
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

# ── Playwright Chromium install (R-F1709: moved BEFORE the app/.git COPYs) ────
# This ~400 MB step (chromium + apt --with-deps) does NOT depend on app code.
# It used to sit AFTER `COPY aria_service/` and `COPY .git/HEAD` — and .git/HEAD
# changes on EVERY commit, busting this layer's cache, so chromium re-installed
# on every single deploy (a major deploy-time cost). Placed here (after the
# pip install that provides the `playwright` CLI, before any code COPY) it stays
# cached across commits and only re-runs when deps actually change.
# Use `--with-deps` so Playwright's own upstream-maintained apt list is used (it
# tracks Debian package renames like libasound2 → libasound2t64; hard-coding the
# list drifted and broke a 2026-04-20 deploy). No silent `|| echo` fallback — if
# this fails the build fails loudly. To intentionally skip: ARIA_PLAYWRIGHT_DISABLED=1.
RUN playwright install --with-deps chromium

# ── R-F958 (2026-05-28) — Whisper model is NOT baked into the image ──────────
# R-F956 baked the faster-whisper 'base' model here, but stacked on top of the
# already-baked MiniLM + torch + Chromium + OCR deps it pushed the image over
# Fly's 8GB UNCOMPRESSED limit ("Not enough space to unpack image"). Releases
# v1092/v1093 failed, the machine rolled back to v1091, and ALL aria-intel
# deploys were blocked. Mirroring the EasyOCR lazy-download pattern: the model
# is fetched ONCE to the persistent /data volume on first transcription
# (voice_transcribe.py download_root) and loaded from there offline thereafter,
# so the image stays under 8GB.

COPY aria_service/ ./aria_service/

# R-F589 (2026-05-16) — runtime build_rev fallback. The CI workflow +
# scripts/fly_deploy.sh wrapper pass --build-arg ARIA_BUILD_GIT_SHA,
# but bare `flyctl deploy` invocations skip the flag, leaving the
# ARG at its "unknown" default. /api/aria/health.build_rev then
# reports UNKNOWN-BUILD even though the code IS live. Belt-and-braces:
# bake .git/HEAD + refs into the image (~50KB) so main.py can resolve
# the SHA at runtime regardless of how the deploy was invoked. See the
# matching .dockerignore exceptions for !.git/HEAD / !.git/refs.
COPY .git/HEAD ./.git/HEAD
COPY .git/refs ./.git/refs
# packed-refs is optional — present on long-lived clones with many tags
# (most production deploys), absent on fresh shallow clones. The COPY
# would fail if the source file doesn't exist — guard with a conditional
# RUN against a probed copy instead.
COPY .git/packed-refs* ./.git/

# ── One-shot scripts (migrations, evals, smoke tests) ─────────────────────
# R-F261 (2026-05-11) — the Upstash → SQLite state migration script lives
# under scripts/migrate_state.py and is invoked via the HTTP admin endpoint
# `POST /api/aria/admin/migrate-state`. Without this COPY, the endpoint
# raises ImportError at runtime because scripts/ isn't in the image.
# Also future-proofs eval/smoke/admin scripts that need server-side execution.
COPY scripts/ ./scripts/

# NOTE: Playwright Chromium is installed earlier (before the app COPY) — see the
# R-F1709 note above. It used to live here, but that placement re-ran the
# ~400 MB install on every commit (cache busted by the .git/HEAD COPY).

# NOTE: EasyOCR model weights are NOT pre-warmed at build time anymore.
# Pre-warming with 4 languages forced ~600MB of PyTorch + model weights into
# the build container, then caused OOM kills on small fly.io machines because
# the running service couldn't fit alongside everything else. EasyOCR now
# downloads weights LAZILY on the first image request — that first OCR call
# takes ~30s but every subsequent one is instant. The languages are also
# English-only by default (set ARIA_OCR_LANGS=en,pt,fr,es to add more).

EXPOSE 8000

CMD ["python", "-m", "aria_service.main"]
