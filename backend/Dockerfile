# AutoApply AI backend image (API, Celery worker and beat share it).
# Build from the repository root:  docker build -f backend/Dockerfile -t autoapply-backend .
#
# Based on Microsoft's official Playwright image: Chromium and every system library it needs
# are preinstalled, and the Python package is pinned to the same version as the browsers.
ARG PLAYWRIGHT_VERSION=1.56.0
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble

ARG PLAYWRIGHT_VERSION
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PROMPTS_DIR=/app/prompts \
    LOCAL_STORAGE_PATH=/data/storage

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt "playwright==${PLAYWRIGHT_VERSION}"

COPY backend/ /app/
COPY prompts/ /app/prompts/
COPY scripts/ /app/scripts/

# pwuser is the unprivileged user shipped with the Playwright image
RUN mkdir -p /data/storage \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R pwuser:pwuser /app /data

USER pwuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fsS http://localhost:8000/health || exit 1
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
