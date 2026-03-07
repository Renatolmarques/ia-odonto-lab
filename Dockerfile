# Dockerfile — Multi-stage Build
# IA Odonto Lab | Sprint 4
#
# Stage 1 (builder): instala dependências em ambiente isolado
# Stage 2 (runner):  imagem final enxuta, sem ferramentas de build
#
# Build: docker build -t ia-odonto-lab:latest .
# Run:   docker-compose up -d

# ─────────────────────────────────────────
# Stage 1: Builder
# ─────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Dependências de sistema para compilar psycopg2 / pgvector
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependências Python em /install para copiar depois
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt


# ─────────────────────────────────────────
# Stage 2: Runner (imagem final)
# ─────────────────────────────────────────
FROM python:3.11-slim AS runner

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Apenas runtime do postgres (libpq)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copiar dependências compiladas do builder
COPY --from=builder /install /usr/local

# Copiar código da aplicação
COPY app /app/app
COPY init_db.py /app/init_db.py

# Usuário não-root (boa prática de segurança)
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Uvicorn com reload desligado em produção
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
