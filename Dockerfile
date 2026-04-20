FROM python:3.12-slim-bookworm AS base

ARG UPLOADER_VERSION=1.25.5
ARG TARGETARCH=amd64

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UPLOADER_BIN=/usr/local/bin/youtubeuploader \
    UPLOADER_SECRETS=/config/client_secrets.json \
    UPLOADER_TOKEN=/config/request.token \
    UPLOADER_WORK_DIR=/var/lib/youtube-remote

# docker-clean's post-invoke hook breaks on old overlay2; drop it.
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64)  ARCH=x86_64 ;; \
      arm64)  ARCH=arm64 ;; \
      arm)    ARCH=armv6 ;; \
      *)      echo "unsupported arch: ${TARGETARCH}" && exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/yu.tar.gz \
      "https://github.com/porjo/youtubeuploader/releases/download/v${UPLOADER_VERSION}/youtubeuploader_${UPLOADER_VERSION}_Linux_${ARCH}.tar.gz"; \
    tar -xzf /tmp/yu.tar.gz -C /usr/local/bin/ youtubeuploader; \
    chmod +x /usr/local/bin/youtubeuploader; \
    rm /tmp/yu.tar.gz; \
    /usr/local/bin/youtubeuploader -showAppVersion || true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Baked-in OAuth client secrets. Kept out of git via .gitignore.
COPY client_secrets.json /config/client_secrets.json

RUN mkdir -p /config "${UPLOADER_WORK_DIR}" \
    && useradd --system --uid 1000 --home-dir /app app \
    && chown -R app:app /app /config "${UPLOADER_WORK_DIR}" \
    && chmod 600 /config/client_secrets.json

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
