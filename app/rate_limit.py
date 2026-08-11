from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic

from fastapi import HTTPException, Request

from .config import settings

_hits: dict[int, deque[float]] = defaultdict(deque)
_login_hits: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(user_id: int) -> None:
    now = monotonic()
    window = now - 60
    bucket = _hits[user_id]
    while bucket and bucket[0] < window:
        bucket.popleft()
    if len(bucket) >= settings.rate_limit_per_minute:
        raise HTTPException(status_code=429, detail="Хэт олон хүсэлт илгээгдлээ. Түр хүлээгээд дахин оролдоно уу.")
    bucket.append(now)


def _trim_login_bucket(key: str, window: float) -> deque[float]:
    bucket = _login_hits[key]
    while bucket and bucket[0] < window:
        bucket.popleft()
    if not bucket:
        # Keep the current bucket object for this request but remove stale map
        # entries opportunistically to bound memory in normal operation.
        _login_hits.pop(key, None)
        bucket = _login_hits[key]
    return bucket


def check_login_rate_limit(request: Request, username: str) -> str:
    ip = request.client.host if request.client else "unknown"
    normalized = username.strip().lower()
    pair_key = f"pair:{ip}|{normalized}"
    ip_key = f"ip:{ip}"
    now = monotonic()
    window = now - 600

    # IP-wide throttling runs first so rotating usernames cannot create an
    # unbounded number of pair buckets or bypass brute-force protection.
    ip_bucket = _trim_login_bucket(ip_key, window)
    if len(ip_bucket) >= settings.login_ip_attempts_per_10m:
        raise HTTPException(status_code=429, detail="Энэ төхөөрөмжөөс нэвтрэх оролдлого хэт олон байна. 10 минутын дараа дахин оролдоно уу.")
    ip_bucket.append(now)

    pair_bucket = _trim_login_bucket(pair_key, window)
    if len(pair_bucket) >= settings.login_attempts_per_10m:
        raise HTTPException(status_code=429, detail="Нэвтрэх оролдлого хэт олон байна. 10 минутын дараа дахин оролдоно уу.")
    pair_bucket.append(now)
    return pair_key


def clear_login_rate_limit(key: str) -> None:
    # Clear only the successful username/IP pair. Keep the IP-wide bucket so a
    # successful login cannot reset protection for other username guesses.
    _login_hits.pop(key, None)
