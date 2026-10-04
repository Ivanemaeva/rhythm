#!/usr/bin/env python3
"""Exercise SMTP delivery and same-day suppression with a simulated morning.

Use --dry-run to exercise the rule and daily dedupe without making an SMTP connection.
Without --dry-run, SMTP_HOST, SMTP_FROM, and ALERT_TO must be configured.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rhythm.config import Settings
from rhythm.email_delivery import deliver_care_alert
from rhythm.ingestion import ingest_webhook
from rhythm.rules import decide_for_day
from rhythm.storage import Store


def settings_for(database_path: Path) -> Settings:
    settings = Settings.from_env()
    return Settings(**{**settings.__dict__, "database_path": database_path})


def make_webhook(event_id: str, when: datetime) -> dict:
    timestamp = int(when.astimezone(timezone.utc).timestamp() * 1000)
    return {
        "meta": {"request_id": f"request-{event_id}"},
        "data": {
            "id": event_id,
            "type": "motion_detected",
            "attributes": {"source": "email-demo-device", "timestamp": timestamp},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="use a local no-op sender instead of SMTP")
    args = parser.parse_args()

    household_tz = ZoneInfo("Europe/Rome")
    target_day = datetime.now(household_tz).date()
    with TemporaryDirectory() as temp:
        database = Path(temp) / "rhythm.sqlite3"
        settings = settings_for(database)
        store = Store(database)
        store.set_state_once("learning_started_local", (target_day - timedelta(days=14)).isoformat())
        store.set_device_status("email-demo-device", True, datetime.now(timezone.utc).isoformat(), '{"online": true}')

        for offset in range(14, 0, -1):
            prior_day = target_day - timedelta(days=offset)
            usual = time(9) if prior_day.weekday() >= 5 else time(8)
            ingest_webhook(
                make_webhook(f"history-{prior_day.isoformat()}", datetime.combine(prior_day, usual, tzinfo=household_tz)),
                store,
                household_tz,
            )
        late_time = time(11, 35) if target_day.weekday() >= 5 else time(10, 35)
        ingest_webhook(
            make_webhook("today-late-motion", datetime.combine(target_day, late_time, tzinfo=household_tz)),
            store,
            household_tz,
        )

        def sender(current_settings: Settings, subject: str, body: str) -> None:
            if args.dry_run:
                print(f"DRY RUN: {subject}")
                print("Email body includes reason:", "Why Rhythm spoke up:" in body)
                return
            from rhythm.email_delivery import send_smtp_email
            send_smtp_email(
                current_settings,
                "[SIMULATED TEST] " + subject,
                "This is a simulated delivery test. No live Ring activity was read.\n\n" + body,
            )

        decision = decide_for_day(store, settings, "email-demo-device", target_day)
        first = deliver_care_alert(store, settings, target_day, decision, sender)
        second_decision = decide_for_day(store, settings, "email-demo-device", target_day)
        second = deliver_care_alert(store, settings, target_day, second_decision, sender)

        print(f"Rule: {decision['decision']} — {decision['reason']}")
        first_label = "dry_run_passed" if args.dry_run and first["delivery"] == "sent" else first["delivery"]
        print(f"First attempt: {first_label}")
        print(f"Second attempt: {second['delivery']} — {second['reason']}")
        if first["delivery"] != "sent":
            raise SystemExit("First delivery did not send.")
        if second["delivery"] != "suppressed":
            raise SystemExit("Second same-day delivery was not suppressed.")
        store.close()


if __name__ == "__main__":
    main()
