#!/bin/sh
# Role-based entrypoint: api | worker | beat | migrate | seed | <any command>
set -e

case "$1" in
  api)
    python scripts/migrate.py
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
      --workers "${API_WORKERS:-2}" --proxy-headers --forwarded-allow-ips="*"
    ;;
  worker)
    exec celery -A app.worker.celery_app worker -Q default,browser \
      --concurrency "${WORKER_CONCURRENCY:-2}" --max-tasks-per-child 50 --loglevel "${LOG_LEVEL:-INFO}"
    ;;
  beat)
    exec celery -A app.worker.celery_app beat --loglevel "${LOG_LEVEL:-INFO}" -s /data/celerybeat-schedule
    ;;
  migrate)
    exec python scripts/migrate.py
    ;;
  seed)
    shift
    exec python scripts/seed_db.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
