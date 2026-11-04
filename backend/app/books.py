"""One book (SQLite file) per install, opened with a bearer token.

The phone registers once (POST /books) and keeps the returned token. Only a
SHA-256 of the token is stored. Each book is its own file under DATA_DIR, so
books can never see each other, and backup / restore / delete are file ops.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .ledger import Ledger, LedgerError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Books:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        (self.dir / "books").mkdir(parents=True, exist_ok=True)
        self.registry = self.dir / "registry.db"
        self._cache: dict[str, Ledger] = {}
        self._lock = threading.Lock()
        with closing(self._connect()) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS book ("
                " id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,"
                " created_at TEXT NOT NULL, last_seen TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.registry)
        con.row_factory = sqlite3.Row
        return con

    def path(self, book_id: str) -> Path:
        return self.dir / "books" / f"{book_id}.db"

    def create(self, shop_name: str, currency: str) -> tuple[str, str, Ledger]:
        book_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        ledger = Ledger(self.path(book_id))
        try:
            ledger.set_shop(shop_name, currency)
        except LedgerError:
            self.path(book_id).unlink(missing_ok=True)
            raise
        with closing(self._connect()) as con, con:
            con.execute("INSERT INTO book (id, token_hash, created_at, last_seen) VALUES (?, ?, ?, ?)",
                        (book_id, _hash(token), _now(), _now()))
        self._cache[book_id] = ledger
        return book_id, token, ledger

    def open(self, token: str) -> tuple[str, Ledger] | None:
        if not token or len(token) > 200:
            return None
        with closing(self._connect()) as con, con:
            row = con.execute("SELECT id FROM book WHERE token_hash = ?", (_hash(token),)).fetchone()
            if row is None:
                return None
            con.execute("UPDATE book SET last_seen = ? WHERE id = ?", (_now(), row["id"]))
        book_id = row["id"]
        with self._lock:
            if book_id not in self._cache:
                self._cache[book_id] = Ledger(self.path(book_id))
            return book_id, self._cache[book_id]

    def open_id(self, book_id: str) -> Ledger | None:
        """Open a book by id (server-side only, e.g. a PayPal webhook for one of its invoices)."""
        if not book_id or not self.path(book_id).exists():
            return None
        with self._lock:
            if book_id not in self._cache:
                self._cache[book_id] = Ledger(self.path(book_id))
            return self._cache[book_id]

    def restore(self, book_id: str, upload: Path) -> Ledger:
        """Replace a book with a validated backup file (old copy kept as .bak)."""
        Ledger.validate_backup(upload)
        target = self.path(book_id)
        with self._lock:
            self._cache.pop(book_id, None)
            if target.exists():
                shutil.copy2(target, target.with_suffix(".bak"))
            for side in (target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")):
                side.unlink(missing_ok=True)
            shutil.copy2(upload, target)
            self._cache[book_id] = Ledger(target)  # also migrates old backups
            return self._cache[book_id]

    def delete(self, book_id: str) -> None:
        """Erase a book completely (user's right to delete their data)."""
        with self._lock:
            self._cache.pop(book_id, None)
            with closing(self._connect()) as con, con:
                con.execute("DELETE FROM book WHERE id = ?", (book_id,))
            base = self.path(book_id)
            for p in (base, base.with_suffix(".bak"), base.with_name(base.name + "-wal"),
                      base.with_name(base.name + "-shm")):
                p.unlink(missing_ok=True)


def default_data_dir() -> str:
    return os.getenv("SAYLEDGER_DATA", "data")
