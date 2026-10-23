"""Tiny SQLite key/value cache with expiry (saves scraping credits and time)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

DB_PATH = os.environ.get("CACHE_DB", os.path.join(os.environ.get("DATA_DIR", "/tmp"), "lorcana-cache.sqlite3"))


class Cache:
    def __init__(self, path: str = DB_PATH) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, exp REAL)")
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            row = self._db.execute("SELECT v, exp FROM kv WHERE k=?", (key,)).fetchone()
        if not row or row[1] < time.time():
            return None
        return json.loads(row[0])

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._db.execute("REPLACE INTO kv VALUES (?,?,?)", (key, json.dumps(value), time.time() + ttl))
            self._db.commit()

    def clear(self, prefix: str = "") -> None:
        with self._lock:
            self._db.execute("DELETE FROM kv WHERE k LIKE ?", (prefix + "%",))
            self._db.commit()
