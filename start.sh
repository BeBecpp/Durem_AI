#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -d .venv ] || [ ! -f .env ]; then
  ./setup.sh
fi
PY=".venv/bin/python"
host="$($PY -c 'from app.config import settings; print(settings.host)')"
port="$($PY -c 'from app.config import settings; print(settings.port)')"
printf 'DUREM AI 2.2 -> http://%s:%s\n' "$host" "$port"
exec "$PY" -m uvicorn app.main:app --host "$host" --port "$port" --no-server-header
