#!/usr/bin/env python3
"""Replay three normal weeks and then a silent morning into Rhythm's SQLite store.

All events are SYNTHETIC. The last day shows the real product behaviour: no activity by the
household-local cutoff -> one gentle care alert with its reason; she then appears later.

  python scripts/replay_demo.py --reset-demo                 # fast replay, no email
  python scripts/replay_demo.py --reset-demo --send-email    # also sends one labeled test email
"""

from __future__ import annotations

import argparse
import random
import sys
import time as sleep_time
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rhythm.config import Settings
from rhythm.email_delivery import deliver_care_alert, send_smtp_email
from rhythm.ingestion import ingest_webhook
from rhythm.rules import decide_for_day
from rhythm.storage import Store


def make_webhook(device_id: str, event_id: str, when: datetime) -> dict:
    stamp = int(when.astimezone(timezone.utc).timestamp() * 1000)
    return {
        "meta": {"version": "1.1", "request_id": f"replay-{event_id}", "account_id": "rhythm-demo"},
        "data": {
            "id": event_id,
            "type": "motion_detected",
            "attributes": {"source": device_id, "source_type": "devices", "timestamp": stamp},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, help="SQLite path (defaults to DATABASE_PATH)")
    parser.add_argument("--device-id", help="defaults to RING_DEVICE_ID or rhythm-demo-device")
    parser.add_argument("--delay-seconds", type=float, default=0.25, help="pause after each virtual day; 0 runs instantly")
    parser.add_argument("--reset-demo", action="store_true", help="remove this script's earlier demo data first")
    parser.add_argument("--send-email", action="store_true", help="send one labeled simulated email to ALERT_TO via SMTP")
    parser.add_argument("--today", type=date.fromisoformat, help="treat this date (YYYY-MM-DD) as the final day")
    args = parser.parse_args()
    if args.delay_seconds < 0:
        parser.error("--delay-seconds must be zero or greater")

    settings = Settings.from_env()
    if args.database:
        settings = replace(settings, database_path=args.database)
    device_id = args.device_id or settings.ring_device_id or "rhythm-demo-device"
    settings = replace(settings, ring_device_id=device_id)
    tz = settings.household_timezone
    final_day = args.today or datetime.now(tz).date()
    start_day = final_day - timedelta(days=21)
    store = Store(settings.database_path)

    if store.count_events(device_id, exclude_source="demo_replay"):
        store.close()
        raise SystemExit("This device already has non-demo events in that database. Use a fresh demo DATABASE_PATH.")
    if store.count_events(device_id, source="demo_replay") and not args.reset_demo:
        store.close()
        raise SystemExit("A replay already exists here. Pass --reset-demo to replace only replay data.")
    if args.reset_demo:
        store.clear_demo_replay()

    store.set_state("learning_started_local", start_day.isoformat())
    store.set_device_status(device_id, True, datetime.now(timezone.utc).isoformat(), '{"online": true, "source": "demo_replay"}')
    rng = random.Random(241004)
    days = [start_day + timedelta(days=offset) for offset in range(22)]
    print(f"Replay (SYNTHETIC): 21 normal days + a silent morning, {tz.key}, store={settings.database_path}")

    for index, day in enumerate(days[:-1]):
        bucket = settings.bucket(day.weekday())
        center = time(9, 0) if bucket == "weekend" else time(8, 0)
        noise = rng.randint(-25, 25) if bucket == "weekend" else rng.randint(-20, 20)
        first_time = (datetime.combine(day, center) + timedelta(minutes=noise)).time()
        payload = make_webhook(device_id, f"{day.isoformat()}-first-activity", datetime.combine(day, first_time, tzinfo=tz))
        ingest_webhook(payload, store, tz, source="demo_replay")
        end_of_day = datetime.combine(day, time(23, 59), tzinfo=tz)
        decision = decide_for_day(store, settings, device_id, day, end_of_day)
        print(f"Day {index + 1:02d}/22  {day.isoformat()}  first activity {first_time.strftime('%H:%M')}  -> {decision['decision']}")
        if args.delay_seconds:
            sleep_time.sleep(args.delay_seconds)

    # Final day: nothing is seen. Evaluate just before and just after the cutoff.
    morning = datetime.combine(final_day, time(6, 0), tzinfo=tz)
    pending = decide_for_day(store, settings, device_id, final_day, morning)
    cutoff_text = str(pending["cutoff"])
    cutoff_hour, cutoff_minute = (int(part) for part in cutoff_text.split(":"))
    cutoff_at = datetime.combine(final_day, time(cutoff_hour, cutoff_minute), tzinfo=tz)
    print(f"Day 22/22  {final_day.isoformat()}  06:00 no activity yet -> {pending['decision']} (cutoff {cutoff_text})")

    after_cutoff = cutoff_at + timedelta(minutes=1)
    decision = decide_for_day(store, settings, device_id, final_day, after_cutoff)
    print(f"Day 22/22  {final_day.isoformat()}  {after_cutoff.strftime('%H:%M')} still nothing -> {decision['decision']}")
    if decision.get("decision") != "care_alert":
        store.close()
        raise SystemExit(f"Expected a care alert on the silent morning, got: {decision}")

    if args.send_email:
        def labeled_smtp(current: Settings, subject: str, body: str) -> None:
            send_smtp_email(
                current,
                "[SIMULATED DEMO] " + subject,
                "This is a demo replay using synthetic event times; it is not a live Ring observation.\n\n" + body,
            )

        delivery = deliver_care_alert(store, settings, final_day, decision, labeled_smtp)
        print(f"Email: {delivery['delivery']}")
    elif store.claim_alert_day(final_day.isoformat(), "care_alert", str(decision["reason"]), datetime.now(timezone.utc).isoformat()):
        store.mark_alert_demo(final_day.isoformat())
        print("Email: demo-only alert recorded (no email sent).")
    print(f"Why: {decision['reason']}")

    # She appears later; the alert already fired once and repeats stay suppressed.
    late_at = cutoff_at + timedelta(minutes=35)
    ingest_webhook(make_webhook(device_id, f"{final_day.isoformat()}-first-activity", late_at), store, tz, source="demo_replay")
    again = decide_for_day(store, settings, device_id, final_day, late_at + timedelta(minutes=1))
    repeat = deliver_care_alert(store, settings, final_day, again, lambda *_: None)
    print(f"First activity finally at {late_at.strftime('%H:%M')}; second alert attempt: {repeat['delivery']}")
    print("Replay complete. Open the dashboard to show the chart and the stored reason.")
    store.close()


if __name__ == "__main__":
    main()
