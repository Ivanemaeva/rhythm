from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any


class Store:
    """SQLite store shared by ingestion, rules, delivery and the web app.

    FastAPI runs synchronous endpoints in worker threads, so the connection must be
    usable from several threads. All access goes through one lock so multi-step
    operations (execute + commit) cannot interleave.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.connection.row_factory = sqlite3.Row
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at_utc TEXT NOT NULL,
                    occurred_at_local TEXT NOT NULL,
                    source TEXT NOT NULL,
                    raw_metadata TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_utc ON events(occurred_at_utc);
                CREATE INDEX IF NOT EXISTS idx_events_device_utc ON events(device_id, occurred_at_utc);
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    online INTEGER,
                    reported_at_utc TEXT,
                    raw_metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    local_day TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'reserved'
                );
                CREATE TABLE IF NOT EXISTS webhook_requests (
                    request_id TEXT PRIMARY KEY,
                    received_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_day TEXT NOT NULL,
                    action TEXT NOT NULL,
                    away_until_local TEXT,
                    created_at_utc TEXT NOT NULL
                );
                """
            )
            alert_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(alerts)")}
            if "status" not in alert_columns:
                self.connection.execute("ALTER TABLE alerts ADD COLUMN status TEXT NOT NULL DEFAULT 'reserved'")
            self.connection.commit()

    # ------------------------------------------------------------------ helpers
    def _fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.connection.execute(sql, params).fetchall()

    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cursor = self.connection.execute(sql, params)
            self.connection.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------- events
    def add_event(self, event: dict[str, Any]) -> bool:
        return self._write(
            """INSERT OR IGNORE INTO events
            (event_id, device_id, event_type, occurred_at_utc, occurred_at_local, source, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event["event_id"], event["device_id"], event["event_type"],
                event["occurred_at_utc"], event["occurred_at_local"], event["source"],
                event["raw_metadata"],
            ),
        ) == 1

    def events_for_device(self, device_id: str) -> list[sqlite3.Row]:
        """All events for the device in chronological order; the rule groups them by local day."""
        return self._fetchall(
            "SELECT * FROM events WHERE device_id = ? ORDER BY occurred_at_utc",
            (device_id,),
        )

    def list_events(self, device_id: str, from_local_day: str) -> list[sqlite3.Row]:
        return self._fetchall(
            """SELECT * FROM events WHERE device_id = ? AND substr(occurred_at_local, 1, 10) >= ?
            ORDER BY occurred_at_utc""",
            (device_id, from_local_day),
        )

    def earliest_event_day(self, device_id: str) -> str | None:
        row = self._fetchone(
            "SELECT MIN(substr(occurred_at_local, 1, 10)) AS first_day FROM events WHERE device_id = ?",
            (device_id,),
        )
        return None if row is None else row["first_day"]

    def count_events(self, device_id: str, source: str | None = None, exclude_source: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM events WHERE device_id = ?"
        params: list[str] = [device_id]
        if source is not None:
            sql += " AND source = ?"
            params.append(source)
        if exclude_source is not None:
            sql += " AND source != ?"
            params.append(exclude_source)
        row = self._fetchone(sql, tuple(params))
        return int(row["n"])

    def first_known_device_id(self) -> str:
        row = self._fetchone("SELECT device_id FROM devices ORDER BY device_id LIMIT 1")
        if row is None:
            row = self._fetchone("SELECT device_id FROM events ORDER BY occurred_at_utc DESC LIMIT 1")
        return "" if row is None else str(row["device_id"])

    # ------------------------------------------------------------------- alerts
    def list_alerts(self) -> list[sqlite3.Row]:
        return self._fetchall(
            "SELECT local_day, decision, reason, created_at_utc, status FROM alerts "
            "WHERE status IN ('sent', 'demo') ORDER BY local_day DESC"
        )

    def claim_alert_day(self, local_day: str, decision: str, reason: str, created_at_utc: str) -> bool:
        return self._write(
            "INSERT OR IGNORE INTO alerts(local_day, decision, reason, created_at_utc) VALUES (?, ?, ?, ?)",
            (local_day, decision, reason, created_at_utc),
        ) == 1

    def mark_alert_sent(self, local_day: str) -> None:
        self._write("UPDATE alerts SET status = 'sent' WHERE local_day = ?", (local_day,))

    def mark_alert_demo(self, local_day: str) -> None:
        self._write("UPDATE alerts SET status = 'demo' WHERE local_day = ?", (local_day,))

    def release_alert_day(self, local_day: str) -> None:
        self._write("DELETE FROM alerts WHERE local_day = ? AND status = 'reserved'", (local_day,))

    def alert_status(self, local_day: str) -> str | None:
        row = self._fetchone("SELECT status FROM alerts WHERE local_day = ?", (local_day,))
        return None if row is None else str(row["status"])

    def clear_demo_replay(self) -> None:
        """Remove only data created by scripts/replay_demo.py."""
        with self._lock:
            self.connection.execute("DELETE FROM events WHERE source = 'demo_replay'")
            self.connection.execute("DELETE FROM alerts WHERE status = 'demo'")
            self.connection.execute("DELETE FROM user_replies")
            self.connection.execute("DELETE FROM settings_state WHERE key = 'learning_started_local'")
            self.connection.commit()

    # ------------------------------------------------------------------ replies
    def record_reply(self, local_day: str, action: str, created_at_utc: str, away_until_local: str | None = None) -> None:
        self._write(
            "INSERT INTO user_replies(local_day, action, away_until_local, created_at_utc) VALUES (?, ?, ?, ?)",
            (local_day, action, away_until_local, created_at_utc),
        )

    def latest_reply(self, local_day: str) -> sqlite3.Row | None:
        return self._fetchone(
            "SELECT * FROM user_replies WHERE local_day = ? AND action = 'fine' ORDER BY id DESC LIMIT 1",
            (local_day,),
        )

    def active_away_until(self, local_day: str) -> str | None:
        row = self._fetchone(
            """SELECT MAX(away_until_local) AS until_day FROM user_replies
            WHERE action = 'away' AND away_until_local >= ?""",
            (local_day,),
        )
        return None if row is None else row["until_day"]

    # ------------------------------------------------------------------ devices
    def set_device_status(self, device_id: str, online: bool | None, reported_at_utc: str | None, raw_metadata: str) -> None:
        self._write(
            """INSERT INTO devices(device_id, online, reported_at_utc, raw_metadata)
            VALUES (?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET
            online=excluded.online, reported_at_utc=excluded.reported_at_utc,
            raw_metadata=excluded.raw_metadata""",
            (device_id, None if online is None else int(online), reported_at_utc, raw_metadata),
        )

    def device_online(self, device_id: str) -> bool | None:
        row = self._fetchone("SELECT online FROM devices WHERE device_id = ?", (device_id,))
        return None if row is None or row["online"] is None else bool(row["online"])

    # --------------------------------------------------------- webhooks & state
    def webhook_seen(self, request_id: str) -> bool:
        return self._fetchone("SELECT 1 FROM webhook_requests WHERE request_id = ?", (request_id,)) is not None

    def mark_webhook_seen(self, request_id: str, received_at_utc: str) -> None:
        self._write(
            "INSERT OR IGNORE INTO webhook_requests(request_id, received_at_utc) VALUES (?, ?)",
            (request_id, received_at_utc),
        )

    def get_state(self, key: str) -> str | None:
        row = self._fetchone("SELECT value FROM settings_state WHERE key = ?", (key,))
        if row is None or str(row["value"]) == "":
            return None
        return str(row["value"])

    def set_state(self, key: str, value: str) -> None:
        self._write(
            "INSERT INTO settings_state(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def set_state_once(self, key: str, value: str) -> None:
        """Set a value only if the key is missing or empty."""
        with self._lock:
            row = self.connection.execute("SELECT value FROM settings_state WHERE key = ?", (key,)).fetchone()
            if row is None or str(row["value"]) == "":
                self.connection.execute(
                    "INSERT INTO settings_state(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
                self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()
