# The Legoin API on a CPU, with the legoin-ml v0.2.0 models baked in.
#
#   docker build -t legoin-api .
#   docker run -p 8000:8000 -v legoin-data:/data legoin-api
#
# One process only: scan jobs live in memory (ADR-0008). Keep /data on a persistent disk.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv

ENV UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/legoin/venv

WORKDIR /app

# The locked dependencies first, with the models extra, so a code change doesn't reinstall torch.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --extra models --no-install-project

# Bytecode is compiled once, here, so it isn't written twice in two layers; the app runs as a
# user who can't write it at start-up.
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --extra models --no-editable --compile-bytecode

# The model files, downloaded from the pinned release and checked by SHA-256 now, so the
# container starts without network access.
ENV LEGOIN_MODEL_TAG=v0.2.0 \
    LEGOIN_MODEL_DIR=/opt/legoin/models
RUN /opt/legoin/venv/bin/python -m legoin_api.weights

RUN useradd --system --uid 10001 --no-create-home legoin \
    && mkdir /data \
    && chown legoin /data
USER legoin

ENV PATH=/opt/legoin/venv/bin:$PATH \
    LEGOIN_CONTAINER=1 \
    LEGOIN_PIPELINE=models \
    LEGOIN_DATA_DIR=/data \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    PORT=8000
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

# Hosts that set $PORT get it; otherwise 8000.
CMD ["sh", "-c", "exec uvicorn --factory legoin_api.main:create_app --host 0.0.0.0 --port \"$PORT\""]
