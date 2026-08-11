#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

say() { printf '%s\n' "$1"; }
command -v python3 >/dev/null 2>&1 || { say "Python 3.11+ шаардлагатай."; exit 1; }
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' || { say "Python 3.11+ шаардлагатай."; exit 1; }

say ""
say "DUREM AI 2.2 Setup"
say "=================="

if [ ! -d .venv ]; then
  say "[1/5] Virtual environment үүсгэж байна..."
  python3 -m venv .venv
fi
PY=".venv/bin/python"
PIP=".venv/bin/pip"

say "[2/5] Dependencies суулгаж байна..."
"$PY" -m pip install --upgrade pip >/dev/null
"$PIP" install -r requirements.txt

if [ ! -f .env ]; then
  say "[3/5] Secure local configuration үүсгэж байна..."
  printf 'Company name [Сутайн Буянт]: '
  IFS= read -r company || true
  [ -n "${company:-}" ] || company="Сутайн Буянт"
  printf 'Bind host [127.0.0.1] (LAN-д 0.0.0.0): '
  IFS= read -r bind || true
  [ -n "${bind:-}" ] || bind="127.0.0.1"

  if [ "$bind" = "127.0.0.1" ]; then
    allowed="127.0.0.1,localhost"
  else
    machine="$(hostname 2>/dev/null || true)"
    ips="$(hostname -I 2>/dev/null | tr ' ' ',' | sed 's/,$//' || true)"
    allowed="localhost,127.0.0.1"
    [ -n "$machine" ] && allowed="$allowed,$machine"
    [ -n "$ips" ] && allowed="$allowed,$ips"
    printf 'Нэмэлт LAN hostname/IP (optional): '
    IFS= read -r extra || true
    [ -n "${extra:-}" ] && allowed="$allowed,$extra"
  fi

  while :; do
    printf 'Admin password (12+ chars, 3 character classes): '
    stty -echo 2>/dev/null || true
    IFS= read -r password || true
    stty echo 2>/dev/null || true
    printf '\n'
    if DUREM_PASSWORD="$password" "$PY" - <<'PY' >/dev/null 2>&1
import os, re, sys
p=os.environ.get('DUREM_PASSWORD','')
classes=sum(bool(re.search(x,p)) for x in (r'[a-z]',r'[A-Z]',r'\d',r'[^A-Za-z0-9\s]'))
sys.exit(0 if len(p)>=12 and classes>=3 else 1)
PY
    then break; fi
    say "Password policy хангахгүй байна."
  done

  secret="$($PY -c 'import secrets; print(secrets.token_urlsafe(64))')"
  cat > .env <<EOF
DUREM_COMPANY_NAME=$company
DUREM_HOST=$bind
DUREM_PORT=8080
DUREM_SECRET_KEY=$secret
DUREM_BOOTSTRAP_PASSWORD=$password
DUREM_SESSION_HOURS=12
DUREM_SESSION_IDLE_MINUTES=120
DUREM_MAX_SESSIONS_PER_USER=8
DUREM_PASSWORD_MIN_LENGTH=12
DUREM_SECURE_COOKIES=false
DUREM_ENABLE_DOCS=false
DUREM_ALLOWED_HOSTS=$allowed
DUREM_RATE_LIMIT_PER_MINUTE=20
DUREM_LOGIN_ATTEMPTS_PER_10M=10
DUREM_LOGIN_IP_ATTEMPTS_PER_10M=30
DUREM_UPLOAD_MAX_MB=40
DUREM_UPLOAD_MAX_UNCOMPRESSED_MB=250
DUREM_RESTORE_MAX_MB=2048
LEMONADE_BASE_URL=http://127.0.0.1:13305
LEMONADE_API_KEY=
DUREM_ALLOW_EXTERNAL_AI=false
LEMONADE_MODEL=Qwen3-8B-GGUF
LEMONADE_EMBEDDING_MODEL=Qwen3-Embedding-0.6B-GGUF
LEMONADE_TIMEOUT_SECONDS=240
DUREM_LLM_MAX_TOKENS=900
DUREM_DISABLE_MODEL_THINKING=true
DUREM_EMBEDDINGS_ENABLED=true
DUREM_MOCK_MODE=false
DUREM_DEMO_DATA=false
EOF
  chmod 600 .env 2>/dev/null || true
  say "Trusted hosts: $allowed"
else
  say "[3/5] .env байгаа тул хэвээр үлдээлээ."
fi

mkdir -p data/documents data/backups
chmod 700 data data/documents data/backups 2>/dev/null || true

say "[4/5] Database initialize/migrate хийж байна..."
"$PY" -c "from app.db import init_db; from app.auth import ensure_default_admin; init_db(); ensure_default_admin(); print('Database ready')"
# Bootstrap credential-ийг initialization дуусмагц config-оос арилгана.
"$PY" - <<'PY'
from pathlib import Path
import re
p=Path('.env')
s=p.read_text(encoding='utf-8')
s=re.sub(r'(?m)^DUREM_BOOTSTRAP_PASSWORD=.*$', 'DUREM_BOOTSTRAP_PASSWORD=', s)
p.write_text(s, encoding='utf-8')
PY
chmod 600 .env 2>/dev/null || true

say "[5/5] Local AI шалгаж байна..."
if command -v lemonade >/dev/null 2>&1; then
  say "Lemonade detected. AMD setup хэрэгтэй бол setup-amd-windows.ps1-ийг Windows дээр ашиглана."
else
  say "Lemonade CLI PATH дээр олдсонгүй. Backend бэлэн; local AI runtime-аа тусад нь асаана."
fi
say ""
say "Setup дууслаа. ./start.sh ажиллуулна уу."
