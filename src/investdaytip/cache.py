"""SQLite cache for yfinance data with per-type TTL.

Cache keys are ``{ticker}:info`` (fundamentals + metadata, 1 day TTL) and
``{ticker}:history`` (price history, 5 min TTL).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path.home() / ".investdaytip"
CACHE_DB = CACHE_DIR / "cache.db"

TTL_PRICES = 300          # 5 minutes
TTL_FUNDAMENTALS = 86400  # 1 day


class CacheDB:
    """Thread-safe SQLite cache with automatic table creation.

    Each thread gets its own connection via ``threading.local()`` so that
    concurrent reads never share the same ``sqlite3.Connection`` object.
    Writes are serialised by ``_write_lock`` to avoid ``SQLITE_BUSY``.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else CACHE_DB
        self._local = threading.local()
        self._write_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                "  key TEXT PRIMARY KEY,"
                "  data TEXT NOT NULL,"
                "  expires_at REAL NOT NULL"
                ")"
            )
            self._local.conn = conn
        return conn

    def get(self, key: str) -> str | None:
        """Return cached value or None if missing/expired."""
        conn = self._connect()
        row = conn.execute(
            "SELECT data, expires_at FROM cache WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        data, expires_at = row
        if time.time() > expires_at:
            return None
        return data

    def set(self, key: str, data: str, ttl: int) -> None:
        """Insert or update a cache entry."""
        conn = self._connect()
        now = time.time()
        with self._write_lock:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, data, expires_at) VALUES (?, ?, ?)",
                (key, data, now + ttl),
            )
            conn.commit()

    def clear(self) -> None:
        """Delete all cached entries."""
        conn = self._connect()
        with self._write_lock:
            conn.execute("DELETE FROM cache")
            conn.commit()

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


_db: CacheDB | None = None
_db_lock = threading.Lock()
enabled = True


def get_db() -> CacheDB:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = CacheDB()
    return _db


def _cache_key(ticker: str, data_type: str) -> str:
    return f"{ticker}:{data_type}"


# ── Public helpers ──────────────────────────────────────────────────────────


def set_enabled(flag: bool) -> None:
    """Enable or disable caching globally."""
    global enabled
    enabled = flag


def cache_info_get(ticker: str) -> dict[str, Any] | None:
    """Return cached ``info`` dict or None."""
    if not enabled:
        return None
    raw = get_db().get(_cache_key(ticker, "info"))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def cache_info_set(ticker: str, info: dict[str, Any]) -> None:
    """Store ``info`` dict in cache with fundamentals TTL."""
    if not enabled:
        return
    get_db().set(_cache_key(ticker, "info"), json.dumps(info), TTL_FUNDAMENTALS)


def cache_history_get(ticker: str) -> str | None:
    """Return cached history JSON string or None."""
    if not enabled:
        return None
    return get_db().get(_cache_key(ticker, "history"))


def cache_history_set(ticker: str, history_json: str) -> None:
    """Store history JSON string in cache with prices TTL."""
    if not enabled:
        return
    get_db().set(_cache_key(ticker, "history"), history_json, TTL_PRICES)


def clear_cache() -> None:
    """Purge all cached data."""
    get_db().clear()
