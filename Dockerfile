# syntax=docker/dockerfile:1
# Dual-venv image: light API + isolated MinerU + isolated MolScribe
FROM python:3.11-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/cache/huggingface \
    DATA_DIR=/data \
    MINERU_VENV=/opt/venvs/mineru \
    MOLSCRIBE_VENV=/opt/venvs/molscribe \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        curl \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgomp1 \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- API venv (lightweight) ----
RUN python -m venv /opt/venvs/api
COPY requirements-api.txt /tmp/requirements-api.txt
RUN /opt/venvs/api/bin/pip install --upgrade pip \
    && /opt/venvs/api/bin/pip install -r /tmp/requirements-api.txt

# ---- MinerU venv ----
RUN python -m venv /opt/venvs/mineru
COPY requirements-mineru.txt /tmp/requirements-mineru.txt
RUN /opt/venvs/mineru/bin/pip install --upgrade pip \
    && /opt/venvs/mineru/bin/pip install -r /tmp/requirements-mineru.txt

# ---- MolScribe venv (torch CPU by default; override TORCH_INDEX for CUDA builds) ----
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN python -m venv /opt/venvs/molscribe
COPY requirements-molscribe.txt /tmp/requirements-molscribe.txt
RUN /opt/venvs/molscribe/bin/pip install --upgrade pip \
    && /opt/venvs/molscribe/bin/pip install torch torchvision --index-url ${TORCH_INDEX} \
    && /opt/venvs/molscribe/bin/pip install -r /tmp/requirements-molscribe.txt

COPY app /app/app
COPY workers /app/workers

RUN mkdir -p /data/jobs /data/cache \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /data /app

USER appuser
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["/opt/venvs/api/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
