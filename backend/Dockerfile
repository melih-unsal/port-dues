FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/data/hf \
    USE_TF=0 \
    DATA_DIR=/data

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv
WORKDIR /app

# dependencies first so source edits don't bust the layer cache
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

# Laya pulls in torch; the CPU wheel keeps the image a reasonable size.
# Build with --build-arg WITH_LAYA=false to drop the Laya scorer entirely.
ARG WITH_LAYA=true
RUN if [ "$WITH_LAYA" = "true" ]; then \
      uv pip install --system --no-cache --index-url https://download.pytorch.org/whl/cpu torch && \
      uv pip install --system --no-cache "laya>=0.4"; \
    fi

COPY portdues ./portdues
RUN uv pip install --system --no-cache --no-deps . \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data && chown app:app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "portdues.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
