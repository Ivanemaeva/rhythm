#!/usr/bin/env python3
"""Rhythm's scheduled job: sync Ring, evaluate today, and send at most one email.

Run once:      python scripts/check_morning.py --once
Run forever:   python scripts/check_morning.py            (checks every RING_POLL_SECONDS)
Safe preview:  python scripts/check_morning.py --once --dry-run

The job covers the whole live path: Ring status + Event History -> SQLite -> rule -> email.
It is what turns a silent morning (no events at all) into a care alert once the cutoff passes.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time as sleep_time
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rhythm.config import Settings
from rhythm.email_delivery import deliver_care_alert, deliver_offline_notice, send_smtp_email
from rhythm.ring_api import RingApi
from rhythm.rules import decide_for_day
from rhythm.storage import Store

# A device-offline message is only sent from this local time on, so a night-time Wi-Fi blip stays quiet.
OFFLINE_NOTICE_AFTER = time(8, 0)


def print_sender(settings: Settings, subject: str, body: str) -> None:
    print(f"--- DRY RUN email (not sent) ---\nSubject: {subject}\n\n{body}\n--------------------------------")


def run_once(
    settings: Settings,
    store: Store,
    device_id: str,
    now: datetime | None = None,
    sync: bool = True,
    sender=send_smtp_email,
) -> dict[str, object]:
    """One check. Returns a summary dict; never raises because of a failed Ring sync."""
    tz = settings.household_timezone
    now_local = (now or datetime.now(tz)).astimezone(tz)
    summary: dict[str, object] = {"checked_at": now_local.isoformat(timespec="seconds")}

    if sync and settings.ring_access_token and settings.ring_device_id:
        try:
            summary["ring_sync"] = RingApi(settings, store).sync()
        except Exception as exc:  # network trouble must not stop the check
            summary["ring_sync"] = f"failed: {exc}"

    result = decide_for_day(store, settings, device_id, now_local.date(), now_local)
    summary["decision"] = result["decision"]
    summary["reason"] = result["reason"]

    if result["decision"] == "care_alert":
        summary["delivery"] = deliver_care_alert(store, settings, now_local.date(), result, sender)["delivery"]
    elif result["decision"] == "device_offline" and now_local.time() >= OFFLINE_NOTICE_AFTER:
        summary["delivery"] = deliver_offline_notice(store, settings, now_local.date(), result, sender)["delivery"]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--dry-run", action="store_true", help="print the email instead of sending it")
    parser.add_argument("--no-sync", action="store_true", help="skip the Ring API call and use stored data only")
    parser.add_argument("--now", help="pretend it is this household-local time (YYYY-MM-DDTHH:MM) for testing")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.dry_run and settings.database_path.exists():
        # Work on a throwaway copy so a preview never uses up the real one-alert-per-day slot.
        scratch = Path(tempfile.mkdtemp()) / settings.database_path.name
        shutil.copy(settings.database_path, scratch)
        settings = replace(settings, database_path=scratch)
    store = Store(settings.database_path)
    device_id = settings.ring_device_id or store.first_known_device_id()
    if not device_id:
        raise SystemExit("No device known yet. Set RING_DEVICE_ID, or run scripts/poll_ring.py --once first.")
    sender = print_sender if args.dry_run else send_smtp_email
    fixed_now = None
    if args.now:
        fixed_now = datetime.fromisoformat(args.now).replace(tzinfo=settings.household_timezone)

    try:
        while True:
            summary = run_once(settings, store, device_id, fixed_now, sync=not args.no_sync, sender=sender)
            print(summary)
            if args.once:
                break
            sleep_time.sleep(settings.ring_poll_seconds)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
