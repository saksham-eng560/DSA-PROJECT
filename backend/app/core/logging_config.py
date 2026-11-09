"""Logging + optional Sentry setup."""

from __future__ import annotations

import logging

from app.config import settings

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpx2", "httpcore", "googleapiclient.discovery_cache", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if settings.SENTRY_DSN:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.SENTRY_DSN, environment=settings.ENVIRONMENT, traces_sample_rate=0.1,
                        send_default_pii=False)
    _configured = True
