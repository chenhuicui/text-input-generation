"""Crash-aware, append-once physical requests, outside the legacy MySQL tables."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .context import digest, encoded


class Ledger:
    def __init__(self, path: Path, manifest: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, dimensions TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL, response TEXT, result TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
            CREATE TABLE IF NOT EXISTS trajectories (id TEXT PRIMARY KEY, result TEXT NOT NULL);
        """)
        fingerprint = digest(manifest)
        previous = self.db.execute("SELECT value FROM metadata WHERE key='fingerprint'").fetchone()
        if previous and previous[0] != fingerprint:
            self.db.close()
            raise ValueError("Resume refused: protocol, inputs, oracle or source code changed. Use a new run directory.")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('fingerprint', ?)", (fingerprint,))
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('manifest', ?)", (encoded(manifest),))

    def ensure_resumable(self):
        rows = self.db.execute("SELECT id, state FROM requests WHERE state IN ('reserved','evaluating')").fetchall()
        if rows:
            raise RuntimeError(f"Uncertain interrupted requests: {[dict(r) for r in rows]}. "
                               "Reconcile their API/execution artifacts before resuming; automatic replay is disabled.")

    def get(self, request_id):
        row = self.db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def reserve(self, request_id, dimensions, payload):
        with self.db:
            self.db.execute("INSERT INTO requests(id, dimensions, payload, state) VALUES (?,?,?,'reserved')",
                            (request_id, encoded(dimensions), encoded(payload)))

    def responded(self, request_id, response):
        with self.db:
            self.db.execute("UPDATE requests SET state='responded', response=? WHERE id=?",
                            (encoded(response), request_id))

    def evaluating(self, request_id):
        with self.db:
            self.db.execute("UPDATE requests SET state='evaluating' WHERE id=?", (request_id,))

    def finish(self, request_id, result):
        with self.db:
            self.db.execute("UPDATE requests SET state='done', result=? WHERE id=?", (encoded(result), request_id))

    def trajectory(self, dimensions, result):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO trajectories VALUES (?,?)",
                            (digest(dimensions), encoded({**dimensions, **result})))

    def close(self):
        self.db.close()
