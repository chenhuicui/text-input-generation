"""Crash-safe, append-once ledger for E2-structure-v1.

External model and Android calls are reserved before submission.  A process
death while a call is reserved is intentionally not recoverable by automatic
replay: the remote side may already have acted.  Completed paired blocks can
be resumed without repeating their shared first generation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "".join(
        f"\\u{ord(char):04x}" if 0xD800 <= ord(char) <= 0xDFFF else char
        for char in serialized
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class UncertainCallError(RuntimeError):
    """Raised when replay could duplicate an external side effect."""


class StructuredLedger:
    def __init__(self, path: Path, manifest: dict[str, Any]):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS external_calls (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                dimensions TEXT NOT NULL,
                payload TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('reserved','done')),
                result TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS trajectories (
                id TEXT PRIMARY KEY,
                dimensions TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('running','done')),
                events TEXT NOT NULL,
                result TEXT,
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            """
        )
        fingerprint = digest(manifest)
        previous = self.db.execute(
            "SELECT value FROM metadata WHERE key='fingerprint'"
        ).fetchone()
        if previous is not None and previous[0] != fingerprint:
            self.db.close()
            raise ValueError(
                "Resume refused: frozen protocol, sources, Oracles, or run selection changed. "
                "Use a new run directory."
            )
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO metadata(key,value) VALUES ('fingerprint',?)",
                (fingerprint,),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO metadata(key,value) VALUES ('manifest',?)",
                (canonical_json(manifest),),
            )

    def ensure_resumable(self) -> None:
        uncertain = self.db.execute(
            "SELECT id,kind FROM external_calls WHERE state='reserved' ORDER BY created_at,id"
        ).fetchall()
        running = self.db.execute(
            "SELECT id FROM trajectories WHERE state='running' ORDER BY id"
        ).fetchall()
        if uncertain or running:
            raise UncertainCallError(
                "Automatic resume refused because external effects may already exist: "
                f"reserved_calls={[dict(row) for row in uncertain]}, "
                f"running_trajectories={[row['id'] for row in running]}. "
                "Reconcile the saved evidence and start a separately documented repair run."
            )

    def call(self, call_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM external_calls WHERE id=?", (call_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["dimensions"] = json.loads(value["dimensions"])
        value["payload"] = json.loads(value["payload"])
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def reserve_call(
        self,
        call_id: str,
        kind: str,
        dimensions: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        existing = self.call(call_id)
        if existing is not None:
            if existing["kind"] != kind or existing["dimensions"] != dimensions or existing["payload"] != payload:
                raise ValueError(f"call identity drift for {call_id}")
            if existing["state"] == "reserved":
                raise UncertainCallError(f"external call {call_id} is reserved and cannot be replayed")
            return existing["result"]
        with self.db:
            self.db.execute(
                "INSERT INTO external_calls(id,kind,dimensions,payload,state) VALUES (?,?,?,?, 'reserved')",
                (call_id, kind, canonical_json(dimensions), canonical_json(payload)),
            )
        return None

    def finish_call(self, call_id: str, result: dict[str, Any]) -> None:
        with self.db:
            cursor = self.db.execute(
                "UPDATE external_calls SET state='done',result=?,"
                "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=? AND state='reserved'",
                (canonical_json(result), call_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"call {call_id} is not reserved")

    def trajectory(self, trajectory_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM trajectories WHERE id=?", (trajectory_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["dimensions"] = json.loads(value["dimensions"])
        value["events"] = json.loads(value["events"])
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def begin_trajectory(self, trajectory_id: str, dimensions: dict[str, Any]) -> None:
        existing = self.trajectory(trajectory_id)
        if existing is not None:
            if existing["dimensions"] != dimensions:
                raise ValueError(f"trajectory identity drift for {trajectory_id}")
            if existing["state"] == "done":
                return
            raise UncertainCallError(f"trajectory {trajectory_id} was interrupted")
        with self.db:
            self.db.execute(
                "INSERT INTO trajectories(id,dimensions,state,events) VALUES (?,?,'running','[]')",
                (trajectory_id, canonical_json(dimensions)),
            )

    def append_event(self, trajectory_id: str, event: dict[str, Any]) -> None:
        current = self.trajectory(trajectory_id)
        if current is None or current["state"] != "running":
            raise ValueError(f"trajectory {trajectory_id} is not running")
        events = [*current["events"], event]
        with self.db:
            self.db.execute(
                "UPDATE trajectories SET events=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=? AND state='running'",
                (canonical_json(events), trajectory_id),
            )

    def finish_trajectory(self, trajectory_id: str, result: dict[str, Any]) -> None:
        with self.db:
            cursor = self.db.execute(
                "UPDATE trajectories SET state='done',result=?,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=? AND state='running'",
                (canonical_json(result), trajectory_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"trajectory {trajectory_id} is not running")

    def completed_trajectory_ids(self) -> set[str]:
        return {
            row[0]
            for row in self.db.execute("SELECT id FROM trajectories WHERE state='done'").fetchall()
        }

    def close(self) -> None:
        # Every formal claim owns a separate ledger, so there is no reason to
        # leave committed rows only in a WAL after the worker exits.  A
        # truncated checkpoint gives the local acceptance audit one durable
        # SQLite file to hash and later mirror into MySQL.
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.db.close()
