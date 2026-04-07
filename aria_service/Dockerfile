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

# ── Pre-warm EasyOCR model weights so first image isn't 30s slower ─────────
# EasyOCR downloads ~250MB on first call. Doing it at build time means the
# image is bigger but the first runtime image OCR is fast and offline.
# If the pre-warm fails (e.g. no network during build) easyocr will still
# download weights on first use at runtime.
RUN python -c "import easyocr; easyocr.Reader(['en','pt','fr','es'], gpu=False, verbose=False); print('EasyOCR pre-warmed successfully')" \
    || echo "EasyOCR pre-warm skipped (will download on first use)"

EXPOSE 8000

CMD ["python", "-m", "aria_service.main"]
