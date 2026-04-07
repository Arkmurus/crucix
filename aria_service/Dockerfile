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

# NOTE: EasyOCR model weights are NOT pre-warmed at build time anymore.
# Pre-warming with 4 languages forced ~600MB of PyTorch + model weights into
# the build container, then caused OOM kills on small fly.io machines because
# the running service couldn't fit alongside everything else. EasyOCR now
# downloads weights LAZILY on the first image request — that first OCR call
# takes ~30s but every subsequent one is instant. The languages are also
# English-only by default (set ARIA_OCR_LANGS=en,pt,fr,es to add more).

EXPOSE 8000

CMD ["python", "-m", "aria_service.main"]
