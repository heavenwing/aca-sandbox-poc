FROM python:3.13-slim-trixie

ARG UV_DEFAULT_INDEX=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 固定依赖让 runner 能先静态抓取，再在需要时使用 Chromium 渲染 SPA。
RUN python -m pip install --no-cache-dir --index-url "${UV_DEFAULT_INDEX}" \
        httpx beautifulsoup4 lxml readability-lxml pydantic playwright && \
    python -m playwright install --with-deps chromium && \
    chmod -R a+rX "${PLAYWRIGHT_BROWSERS_PATH}" && \
    rm -rf /var/lib/apt/lists/* /root/.cache

RUN useradd --create-home --shell /usr/sbin/nologin sandbox
COPY --chown=sandbox:sandbox fetch_runner.py /opt/sum-site/fetch_runner.py

USER sandbox
WORKDIR /tmp
