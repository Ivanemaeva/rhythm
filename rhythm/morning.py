"""One morning check: Ring sync, decision, and the emails that follow from it."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from .config import Settings
from .email_delivery import (
    deliver_all_clear,
    deliver_care_alert,
    deliver_connection_notice,
    deliver_offline_notice,
    deliver_weekly_summary,
    send_smtp_email,
)
from .profile import effective_settings
from .ring_api import RingApi
from .rules import decide_for_day
from .storage import Store

# Device-offline and lost-connection messages are only sent from this local time on, so a night-time blip stays quiet.
OFFLINE_NOTICE_AFTER = time(8, 0)


def run_once(
    base_settings: Settings,
    store: Store,
    device_id: str,
    now: datetime | None = None,
    sync: bool = True,
    sender=send_smtp_email,
) -> dict[str, object]:
    """One check. Never raises because of Ring or email trouble; problems are reported in the summary."""
    settings = effective_settings(base_settings, store)
    tz = settings.household_timezone
    now_local = (now or datetime.now(tz)).astimezone(tz)
    today = now_local.date()
    summary: dict[str, object] = {"checked_at": now_local.isoformat(timespec="seconds")}

    if sync and settings.ring_access_token:
        try:
            summary["ring_sync"] = RingApi(settings, store).sync()
        except Exception as exc:  # network trouble must not stop the check
            summary["ring_sync"] = f"failed: {exc}"

    result = decide_for_day(store, settings, device_id, today, now_local)
    summary["decision"] = result["decision"]
    summary["reason"] = result["reason"]

    try:
        if result["decision"] == "care_alert":
            summary["delivery"] = deliver_care_alert(store, settings, today, result, sender)["delivery"]
        elif result["decision"] == "device_offline" and now_local.time() >= OFFLINE_NOTICE_AFTER:
            summary["delivery"] = deliver_offline_notice(store, settings, today, result, sender)["delivery"]
        elif result["decision"] == "connection_lost" and now_local.time() >= OFFLINE_NOTICE_AFTER:
            summary["delivery"] = deliver_connection_notice(store, settings, today, result, sender)[
                "delivery"
            ]
    except Exception as exc:
        summary["delivery"] = f"failed: {exc}"

    # After an alert, activity recorded later the same day triggers one factual follow-up.
    try:
        clear = deliver_all_clear(store, settings, today, sender)
        if clear["delivery"] in {"sent", "failed"}:
            summary["all_clear"] = clear["reason"] if clear["delivery"] == "sent" else "failed"
    except Exception as exc:
        summary["all_clear"] = f"failed: {exc}"

    try:
        weekly = deliver_weekly_summary(store, settings, now_local, sender)
        if weekly["delivery"] in {"sent", "failed"}:
            summary["weekly_summary"] = weekly["delivery"]
    except Exception as exc:
        summary["weekly_summary"] = f"failed: {exc}"

    # Privacy retention: raw event times are kept only as long as the learning window needs them.
    store.delete_old_events((today - timedelta(days=settings.baseline_days)).isoformat())
    return summary
