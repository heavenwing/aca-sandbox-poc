FROM ghcr.io/astral-sh/uv:0.9.26 AS uv

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY sum_site_job.py ./
RUN mkdir -p /mnt/output && chown -R app:app /app /mnt/output

USER app
ENTRYPOINT ["python", "sum_site_job.py"]