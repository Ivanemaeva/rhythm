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
            self.connection.executescript("""
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
                    created_at_utc TEXT NOT NULL,
                    recipient TEXT
                );
                CREATE TABLE IF NOT EXISTS family_members (
                    email TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT 'en'
                );
                CREATE TABLE IF NOT EXISTS notices (
                    notice_key TEXT PRIMARY KEY,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reply_tokens (
                    nonce TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    local_day TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    used_at_utc TEXT
                );
                """)
            alert_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(alerts)")}
            if "status" not in alert_columns:
                self.connection.execute(
                    "ALTER TABLE alerts ADD COLUMN status TEXT NOT NULL DEFAULT 'reserved'"
                )
            reply_columns = {
                row["name"] for row in self.connection.execute("PRAGMA table_info(user_replies)")
            }
            if "recipient" not in reply_columns:
                self.connection.execute("ALTER TABLE user_replies ADD COLUMN recipient TEXT")
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
        return (
            self._write(
                """INSERT OR IGNORE INTO events
            (event_id, device_id, event_type, occurred_at_utc, occurred_at_local, source, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["event_id"],
                    event["device_id"],
                    event["event_type"],
                    event["occurred_at_utc"],
                    event["occurred_at_local"],
                    event["source"],
                    event["raw_metadata"],
                ),
            )
            == 1
        )

    def events_for_device(self, device_id: str) -> list[sqlite3.Row]:
        """All events for the device in chronological order; the rule groups them by local day."""
        return self._fetchall(
            "SELECT * FROM events WHERE device_id = ? ORDER BY occurred_at_utc",
            (device_id,),
        )

    def events_for_devices(self, device_ids: list[str] | None = None) -> list[sqlite3.Row]:
        if device_ids:
            marks = ",".join("?" for _ in device_ids)
            return self._fetchall(
                f"SELECT * FROM events WHERE device_id IN ({marks}) ORDER BY occurred_at_utc",
                tuple(device_ids),
            )
        return self._fetchall("SELECT * FROM events ORDER BY occurred_at_utc")

    def all_device_ids(self) -> list[str]:
        return [
            str(r[0])
            for r in self._fetchall("SELECT device_id FROM devices UNION SELECT device_id FROM events")
        ]

    def home_online(self) -> bool | None:
        """True if any device is online, False only if every device reports offline, else unknown."""
        rows = self._fetchall("SELECT online FROM devices")
        if any(r["online"] == 1 for r in rows):
            return True
        if rows and all(r["online"] == 0 for r in rows):
            return False
        return None

    def save_family(self, members: list[dict[str, str]]) -> None:
        with self._lock:
            self.connection.execute("DELETE FROM family_members")
            self.connection.executemany(
                "INSERT INTO family_members(email, name, language) VALUES (?, ?, ?)",
                [(m["email"], m["name"], m["language"]) for m in members],
            )
            self.connection.commit()

    def family(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._fetchall("SELECT * FROM family_members ORDER BY email")]

    def add_notice(self, key: str) -> bool:
        """Record that a one-off message was sent; False if it was already recorded."""
        return self._write("INSERT OR IGNORE INTO notices VALUES (?, datetime('now'))", (key,)) == 1

    def has_notice(self, key: str) -> bool:
        return self._fetchone("SELECT 1 FROM notices WHERE notice_key = ?", (key,)) is not None

    def away_ranges(self) -> list[tuple[str, str]]:
        """(start_day, end_day) of every recorded away period."""
        rows = self._fetchall("SELECT local_day, away_until_local FROM user_replies WHERE action = 'away'")
        return [(str(r["local_day"]), str(r["away_until_local"] or r["local_day"])) for r in rows]

    def recent_replies(self, limit: int = 10) -> list[dict[str, str]]:
        rows = self._fetchall(
            "SELECT local_day, action, recipient, created_at_utc FROM user_replies ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def latest_reply_any(self, local_day: str) -> sqlite3.Row | None:
        return self._fetchone(
            "SELECT * FROM user_replies WHERE local_day = ? ORDER BY id DESC LIMIT 1", (local_day,)
        )

    def delete_old_events(self, before_local_day: str) -> int:
        return self._write("DELETE FROM events WHERE substr(occurred_at_local,1,10) < ?", (before_local_day,))

    def export_data(self) -> dict[str, object]:
        return {
            "events": [dict(r) for r in self.events_for_devices()],
            "alerts": [dict(r) for r in self.list_alerts()],
            "replies": [dict(r) for r in self._fetchall("SELECT * FROM user_replies")],
            "devices": [
                dict(r) for r in self._fetchall("SELECT device_id,online,reported_at_utc FROM devices")
            ],
            "family": self.family(),
            "settings": {
                r["key"]: r["value"] for r in self._fetchall("SELECT key,value FROM settings_state")
            },
        }

    def delete_everything(self) -> None:
        with self._lock:
            for table in (
                "events",
                "alerts",
                "webhook_requests",
                "settings_state",
                "user_replies",
                "reply_tokens",
                "family_members",
                "notices",
                "devices",
            ):
                self.connection.execute(f"DELETE FROM {table}")
            self.connection.commit()

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

    def count_events(
        self, device_id: str, source: str | None = None, exclude_source: str | None = None
    ) -> int:
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
        return (
            self._write(
                "INSERT OR IGNORE INTO alerts(local_day, decision, reason, created_at_utc) VALUES (?, ?, ?, ?)",
                (local_day, decision, reason, created_at_utc),
            )
            == 1
        )

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
    def record_reply(
        self,
        local_day: str,
        action: str,
        created_at_utc: str,
        away_until_local: str | None = None,
        recipient: str | None = None,
    ) -> None:
        self._write(
            "INSERT INTO user_replies(local_day, action, away_until_local, created_at_utc, recipient) VALUES (?, ?, ?, ?, ?)",
            (local_day, action, away_until_local, created_at_utc, recipient),
        )

    def add_reply_token(
        self,
        nonce: str,
        action: str,
        local_day: str,
        recipient: str,
        expires_at_utc: str,
    ) -> None:
        self._write(
            "INSERT INTO reply_tokens(nonce, action, local_day, recipient, expires_at_utc) VALUES (?, ?, ?, ?, ?)",
            (nonce, action, local_day, recipient, expires_at_utc),
        )

    def purge_expired_reply_tokens(self, now_utc: str) -> None:
        self._write("DELETE FROM reply_tokens WHERE expires_at_utc < ?", (now_utc,))

    def reply_token_available(
        self,
        nonce: str,
        action: str,
        local_day: str,
        recipient: str,
        now_utc: str,
    ) -> bool:
        return (
            self._fetchone(
                """SELECT 1 FROM reply_tokens WHERE nonce = ? AND action = ? AND local_day = ?
            AND recipient = ? AND expires_at_utc >= ? AND used_at_utc IS NULL""",
                (nonce, action, local_day, recipient, now_utc),
            )
            is not None
        )

    def revoke_reply_tokens(self, nonces: list[str]) -> None:
        if not nonces:
            return
        with self._lock:
            self.connection.executemany(
                "DELETE FROM reply_tokens WHERE nonce = ? AND used_at_utc IS NULL", [(n,) for n in nonces]
            )
            self.connection.commit()

    def consume_reply_token(
        self,
        nonce: str,
        action: str,
        token_day: str,
        reply_day: str,
        recipient: str,
        now_utc: str,
        away_until_local: str | None = None,
    ) -> bool:
        """Atomically consume a valid token and save its reply exactly once."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self.connection.execute(
                    """UPDATE reply_tokens SET used_at_utc = ? WHERE nonce = ? AND action = ? AND local_day = ?
                    AND recipient = ? AND expires_at_utc >= ? AND used_at_utc IS NULL""",
                    (now_utc, nonce, action, token_day, recipient, now_utc),
                )
                if cursor.rowcount != 1:
                    self.connection.rollback()
                    return False
                self.connection.execute(
                    """INSERT INTO user_replies(local_day, action, away_until_local, created_at_utc, recipient)
                    VALUES (?, ?, ?, ?, ?)""",
                    (reply_day, action, away_until_local, now_utc, recipient),
                )
                self.connection.commit()
                return True
            except Exception:
                self.connection.rollback()
                raise

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
    def set_device_status(
        self, device_id: str, online: bool | None, reported_at_utc: str | None, raw_metadata: str
    ) -> None:
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
        return (
            self._fetchone("SELECT 1 FROM webhook_requests WHERE request_id = ?", (request_id,)) is not None
        )

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
