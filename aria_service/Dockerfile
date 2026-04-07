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
    && rm -rf /var/lib/apt/lists/*

COPY aria_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

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
