#!/usr/bin/env sh
set -u
cd "$(dirname "$0")"
printf '\nDUREM AI 2.2 Diagnostics\n========================\n'
if command -v python3 >/dev/null 2>&1; then
  python3 -c 'import sys; print("[OK] Python -", ".".join(map(str,sys.version_info[:3])))'
else
  printf '[CHECK] Python - not found\n'
fi
if command -v lemonade >/dev/null 2>&1; then printf '[OK] Lemonade CLI - detected\n'; else printf '[CHECK] Lemonade CLI - not found\n'; fi
if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 4 http://127.0.0.1:13305/v1/models >/dev/null 2>&1; then printf '[OK] Lemonade API - reachable\n'; else printf '[CHECK] Lemonade API - not reachable\n'; fi
if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 4 http://127.0.0.1:8080/login >/dev/null 2>&1; then printf '[OK] DUREM web - reachable\n'; else printf '[CHECK] DUREM web - not running on 127.0.0.1:8080\n'; fi
