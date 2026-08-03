FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# System deps needed to build some Python packages (grpcio, cryptography).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Run as non-root
RUN useradd --create-home appuser
USER appuser

# Cloud Run injects $PORT (default 8080) — do NOT hardcode 8000 here.
# Single worker is correct: Cloud Run scales by adding instances, not workers.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
