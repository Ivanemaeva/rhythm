#!/usr/bin/env python3
"""Feed a simulated household's history + one late webhook event through SQLite and the rule."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rhythm.config import Settings
from rhythm.ingestion import ingest_webhook
from rhythm.rules import decide_for_day
from rhythm.storage import Store


def make_settings(database_path: Path) -> Settings:
    return Settings(
        household_timezone=ZoneInfo("Europe/Rome"), learning_days=14, min_samples_per_bucket=5,
        max_margin_minutes=15, minimum_wait_weekday=time(10), minimum_wait_weekend=time(11),
        fallback_weekday=time(10), fallback_weekend=time(11),
        ring_api_base_url="https://api.amazonvision.com", ring_access_token="", ring_device_id="demo-device",
        ring_ingestion_mode="webhook", ring_poll_seconds=120, ring_webhook_secret="", database_path=database_path,
        smtp_host="", smtp_port=587, smtp_username="", smtp_password="", smtp_from="", alert_to="",
        smtp_use_starttls=True,
    )


def webhook(event_id: str, when: datetime, event_type: str = "motion_detected") -> dict:
    stamp = int(when.astimezone(timezone.utc).timestamp() * 1000)
    return {
        "meta": {"version": "1.1", "request_id": f"request-{event_id}", "account_id": "demo-account"},
        "data": {
            "id": event_id,
            "type": event_type,
            "attributes": {"source": "demo-device", "source_type": "devices", "timestamp": stamp},
        },
    }


def main() -> None:
    tz = ZoneInfo("Europe/Rome")
    target_day = date(2026, 10, 1)  # Thursday
    with TemporaryDirectory() as temp_dir:
        store = Store(Path(temp_dir) / "rhythm.sqlite3")
        settings = make_settings(Path(temp_dir) / "rhythm.sqlite3")
        store.set_state_once("learning_started_local", (target_day - timedelta(days=14)).isoformat())
        store.set_device_status("demo-device", True, datetime.now(timezone.utc).isoformat(), '{"online": true}')

        # Feed 14 days of ordinary history using the same ingestion path as webhooks.
        for offset in range(14, 0, -1):
            day = target_day - timedelta(days=offset)
            usual = time(9, 0) if day.weekday() >= 5 else time(8, 0)
            when = datetime.combine(day, usual, tzinfo=tz)
            ingest_webhook(webhook(f"history-{day.isoformat()}", when), store, tz)

        # One simulated late morning enters through webhook normalization and SQLite.
        late_when = datetime.combine(target_day, time(10, 35), tzinfo=tz)
        ingest_webhook(webhook("late-day-motion", late_when), store, tz)
        result = decide_for_day(store, settings, "demo-device", target_day)
        assert result["decision"] == "care_alert", result
        print(f"E2E day: {target_day.isoformat()} ({tz.key})")
        print("Ingestion: 14 simulated history webhooks + 1 simulated motion webhook stored in SQLite")
        print(f"Decision: {result['decision']}")
        print(f"Reason: {result['reason']}")
        store.close()


if __name__ == "__main__":
    main()
