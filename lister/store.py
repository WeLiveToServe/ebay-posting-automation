"""SQLite persistence. One row per physical item, the record itself stored as JSON."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from .models import Item, Status, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    sku TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def new_sku(self, prefix: str) -> str:
        """Stable, human-readable SKU: PREFIX-YYMMDD-NNN (counter is per day)."""
        day = date.today().strftime("%y%m%d")
        name = f"{prefix}-{day}"
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO counters(name, value) VALUES (?, 1) "
                "ON CONFLICT(name) DO UPDATE SET value = value + 1",
                (name,),
            )
            (value,) = db.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        return f"{name}-{value:03d}"

    def create(self, prefix: str) -> Item:
        item = Item(sku=self.new_sku(prefix))
        self.save(item)
        return item

    def save(self, item: Item) -> Item:
        item.updated_at = utcnow()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO items(sku, status, created_at, updated_at, data) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(sku) DO UPDATE SET status = excluded.status, "
                "updated_at = excluded.updated_at, data = excluded.data",
                (item.sku, item.status.value, item.created_at, item.updated_at, item.model_dump_json()),
            )
        return item

    def get(self, sku: str) -> Item | None:
        with self._connect() as db:
            row = db.execute("SELECT data FROM items WHERE sku = ?", (sku,)).fetchone()
        return Item.model_validate_json(row[0]) if row else None

    def list(self, *statuses: Status) -> list[Item]:
        query = "SELECT data FROM items"
        params: tuple = ()
        if statuses:
            query += f" WHERE status IN ({','.join('?' * len(statuses))})"
            params = tuple(s.value for s in statuses)
        query += " ORDER BY created_at DESC, sku DESC"
        with self._connect() as db:
            return [Item.model_validate_json(r[0]) for r in db.execute(query, params)]

    def delete(self, sku: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM items WHERE sku = ?", (sku,))

    def claim_next(self) -> Item | None:
        """Atomically move the oldest queued item to processing and return it."""
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT sku FROM items WHERE status = ? ORDER BY updated_at LIMIT 1",
                (Status.QUEUED.value,),
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE items SET status = ? WHERE sku = ?", (Status.PROCESSING.value, row[0]))
        item = self.get(row[0])
        if item:
            item.status = Status.PROCESSING
            self.save(item)
        return item

    def requeue_stale(self) -> int:
        """Items left in processing by a crash go back to the queue on startup."""
        items = self.list(Status.PROCESSING)
        for item in items:
            item.status = Status.QUEUED
            self.save(item)
        return len(items)
