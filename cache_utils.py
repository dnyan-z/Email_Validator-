"""Shared caching utilities: thread-safe in-memory TTL/LRU and SQLite persistent cache."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    sets: int = 0

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total


class ThreadSafeTTLCache:
    """In-memory thread-safe TTL cache with LRU eviction."""

    def __init__(self, maxsize: int = 5000) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._stats = CacheStats()

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                self._stats.misses += 1
                return None
            expires_at, value = entry
            if expires_at < now:
                self._data.pop(key, None)
                self._stats.misses += 1
                return None
            self._data.move_to_end(key)
            self._stats.hits += 1
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expires_at = time.time() + max(1, ttl_seconds)
        with self._lock:
            if key in self._data:
                self._data.pop(key, None)
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
            self._stats.sets += 1

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(self._stats.hits, self._stats.misses, self._stats.sets)


class SQLiteTTLCache:
    """Persistent cache backed by SQLite with TTL semantics."""

    def __init__(self, db_path: str) -> None:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._stats = CacheStats()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    namespace TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (namespace, cache_key)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_expiry ON cache_entries(expires_at)"
            )

    def get(self, namespace: str, key: str) -> Any | None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value_json, expires_at FROM cache_entries WHERE namespace=? AND cache_key=?",
                (namespace, key),
            ).fetchone()
            if not row:
                self._stats.misses += 1
                return None
            value_json, expires_at = row
            if float(expires_at) < now:
                conn.execute(
                    "DELETE FROM cache_entries WHERE namespace=? AND cache_key=?",
                    (namespace, key),
                )
                self._stats.misses += 1
                return None
            self._stats.hits += 1
            return json.loads(value_json)

    def set(self, namespace: str, key: str, value: Any, ttl_seconds: int) -> None:
        now = time.time()
        expires_at = now + max(1, ttl_seconds)
        value_json = json.dumps(value, ensure_ascii=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cache_entries(namespace, cache_key, value_json, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, cache_key)
                DO UPDATE SET value_json=excluded.value_json,
                              expires_at=excluded.expires_at,
                              updated_at=excluded.updated_at
                """,
                (namespace, key, value_json, expires_at, now),
            )
            self._stats.sets += 1

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM cache_entries WHERE expires_at < ?", (now,))
            return cursor.rowcount

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(self._stats.hits, self._stats.misses, self._stats.sets)


class HybridTTLCache:
    """Two-level cache: in-memory first, SQLite fallback."""

    def __init__(
        self,
        namespace: str,
        sqlite_cache: SQLiteTTLCache,
        memory_maxsize: int = 5000,
        default_ttl_seconds: int = 3600,
    ) -> None:
        self._namespace = namespace
        self._sqlite = sqlite_cache
        self._memory = ThreadSafeTTLCache(maxsize=memory_maxsize)
        self._default_ttl = default_ttl_seconds

    def get(self, key: str) -> Any | None:
        value = self._memory.get(key)
        if value is not None:
            return value
        value = self._sqlite.get(self._namespace, key)
        if value is not None:
            self._memory.set(key, value, self._default_ttl)
        return value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        self._memory.set(key, value, ttl)
        self._sqlite.set(self._namespace, key, value, ttl)

    def stats(self) -> dict[str, float | int]:
        memory_stats = self._memory.stats()
        sqlite_stats = self._sqlite.stats()
        return {
            "memory_hits": memory_stats.hits,
            "memory_misses": memory_stats.misses,
            "memory_sets": memory_stats.sets,
            "memory_hit_ratio": memory_stats.hit_ratio,
            "sqlite_hits": sqlite_stats.hits,
            "sqlite_misses": sqlite_stats.misses,
            "sqlite_sets": sqlite_stats.sets,
            "sqlite_hit_ratio": sqlite_stats.hit_ratio,
        }
