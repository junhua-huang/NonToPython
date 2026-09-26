#!/usr/bin/env sh
set -eu

echo "[entrypoint] running database migrations..."
alembic upgrade head

echo "[entrypoint] starting NanTuPy backend..."
exec uvicorn app.main:app --host 0.0.0.0 --port 5000
