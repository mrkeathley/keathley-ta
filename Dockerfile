FROM python:3.13-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    KTA_DATABASE_PATH=/data/kta.db

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

RUN groupadd --gid 10001 kta \
    && useradd --uid 10001 --gid kta --home-dir /app --no-create-home kta \
    && mkdir -p /data \
    && chown -R kta:kta /app /data

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=2)"

CMD ["kta", "daemon"]
