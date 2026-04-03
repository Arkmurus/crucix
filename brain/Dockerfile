# CRUCIX Brain — Render Docker Image
# Render has ephemeral filesystem — all persistence goes through Redis.

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for scikit-learn + chromadb
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Pre-download the embedding model so it ships in the image (no cold-start delay)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"


# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Runtime system deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages
COPY --from=builder /install /usr/local
# Copy cached model
COPY --from=builder /root/.cache /root/.cache

# Copy application code
COPY . /app

# ── Environment defaults (override in Render dashboard) ──────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BRAIN_SWEEP_HOURS=6

EXPOSE 5001

# ── Entrypoint ────────────────────────────────────────────────────────────────
CMD ["python", "main_brain.py"]
