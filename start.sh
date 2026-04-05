#!/bin/sh
exec gunicorn app:app \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 1 \
  --timeout 600 \
  --log-level info \
  --access-logfile - \
  --error-logfile -
