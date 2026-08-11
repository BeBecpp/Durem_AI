from app.db import connect, init_db
from app.auth import ensure_default_admin


def test_seed_database():
    init_db(); ensure_default_admin()
    with connect() as conn:
        # Fresh production installs intentionally do not seed fake company policies.
        assert conn.execute("SELECT COUNT(*) c FROM rules WHERE active=1").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM users WHERE active=1").fetchone()["c"] >= 1
        assert conn.execute("SELECT COUNT(*) c FROM roles WHERE active=1 AND is_admin=1").fetchone()["c"] >= 1
