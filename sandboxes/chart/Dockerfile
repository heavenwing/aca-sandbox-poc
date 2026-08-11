FROM python:3.14-slim-trixie

ARG UV_DEFAULT_INDEX=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Sandbox 中的图表代码要求使用 SimHei；安装 fontconfig 并刷新缓存，避免中文显示为方块。
RUN apt-get update && \
    apt-get install -y --no-install-recommends fontconfig && \
    rm -rf /var/lib/apt/lists/*

COPY simhei.ttf /usr/share/fonts/truetype/simhei/SimHei.ttf

RUN fc-cache -f && \
    python -m pip install --no-cache-dir --index-url "${UV_DEFAULT_INDEX}" \
        "matplotlib>=3.10.0" "seaborn>=0.13.2"

RUN useradd --create-home --shell /usr/sbin/nologin sandbox
USER sandbox
WORKDIR /tmp
