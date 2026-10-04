from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Settings
from .storage import Store


def compute_cutoff(settings: Settings, bucket: str, samples: list[time]) -> tuple[time, str]:
    """Return the local-time cutoff for a weekday/weekend bucket and a human-readable explanation."""
    floor = settings.minimum_wait_weekend if bucket == "weekend" else settings.minimum_wait_weekday
    fallback = settings.fallback_weekend if bucket == "weekend" else settings.fallback_weekday
    if len(samples) < settings.min_samples_per_bucket:
        cutoff = max(floor, fallback)
        reason = (
            f"Only {len(samples)} {bucket} samples so far; using the conservative fixed "
            f"cutoff {cutoff.strftime('%H:%M')} local time."
        )
        return cutoff, reason
    # 95th percentile by nearest rank: the value at position ceil(0.95 * n) in sorted order.
    ordered = sorted(samples)
    rank = max(0, min(len(ordered) - 1, -(-95 * len(ordered) // 100) - 1))
    percentile = ordered[rank]
    margin_cutoff = (
        datetime.combine(date.min, percentile) + timedelta(minutes=settings.max_margin_minutes)
    ).time()
    cutoff = max(floor, margin_cutoff)
    reason = (
        f"{len(samples)} {bucket} samples; 95th-percentile time plus {settings.max_margin_minutes} min, "
        f"with a {floor.strftime('%H:%M')} minimum wait floor."
    )
    return cutoff, reason


def local_time(row, tz: ZoneInfo) -> datetime:
    """Event time in the household's current timezone (computed from UTC, so a timezone change is safe)."""
    return datetime.fromisoformat(row["occurred_at_utc"]).astimezone(tz)


def baseline(store: Store, settings: Settings, target_day: date) -> tuple[list[time], datetime | None]:
    """Learned first-activity times for target_day's bucket, and today's first activity.

    The baseline uses only the last `baseline_days` days, and leaves out days that triggered
    an alert and days the family marked as away, so unusual days never teach a wrong routine.
    """
    tz = settings.household_timezone
    window_start = target_day - timedelta(days=settings.baseline_days)
    excluded = {str(row["local_day"]) for row in store.list_alerts()}
    for start_text, end_text in store.away_ranges():
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        excluded.update((start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1))

    first_by_day: dict[date, datetime] = {}
    today_first: datetime | None = None
    for row in store.events_for_devices():
        local = local_time(row, tz)
        day = local.date()
        if day == target_day:
            if today_first is None or local < today_first:
                today_first = local
        elif window_start <= day < target_day and day.isoformat() not in excluded:
            if day not in first_by_day or local < first_by_day[day]:
                first_by_day[day] = local

    bucket = settings.bucket(target_day.weekday())
    samples = [dt.time() for day, dt in first_by_day.items() if settings.bucket(day.weekday()) == bucket]
    return samples, today_first


def usual_window(samples: list[time]) -> list[str] | None:
    """Earliest and latest learned first-activity time, for display."""
    if not samples:
        return None
    return [min(samples).strftime("%H:%M"), max(samples).strftime("%H:%M")]


def decide_for_day(
    store: Store,
    settings: Settings,
    device_id: str,
    target_day: date,
    now: datetime | None = None,
) -> dict[str, str | int]:
    """Decide what Rhythm should do about one household-local day.

    Decisions: suppressed_fine, paused_away, device_offline, learning, pending,
    normal, care_alert. A care_alert is produced in two situations:
      * no activity has been seen and the local cutoff time has passed (a silent morning), or
      * the first activity of the day arrived after the cutoff.
    `now` defaults to the current time and exists so tests and demos can replay a day.
    """
    tz = settings.household_timezone
    now_local = (now or datetime.now(tz)).astimezone(tz)
    local_day = target_day.isoformat()

    if store.latest_reply(local_day) is not None:
        return {
            "decision": "suppressed_fine",
            "reason": "Family marked her fine; today's care alert is suppressed.",
        }
    away_until = store.active_away_until(local_day)
    if away_until is not None:
        return {
            "decision": "paused_away",
            "reason": f"Alerts are paused while she is away through {away_until}.",
        }
    if store.home_online() is False or (
        not store.all_device_ids() and store.device_online(device_id) is False
    ):
        return {
            "decision": "device_offline",
            "reason": "Ring reports the device offline, so Rhythm cannot see anything. Check the device; no care alert was created.",
        }

    learning_start_text = store.get_state("learning_started_local")
    if learning_start_text:
        learning_start = date.fromisoformat(learning_start_text)
        if target_day < learning_start + timedelta(days=settings.learning_days):
            return {
                "decision": "learning",
                "reason": (
                    f"Silent learning began {learning_start.isoformat()}; "
                    f"the {settings.learning_days}-day period is not complete."
                ),
            }

    samples, today_first = baseline(store, settings, target_day)
    bucket = settings.bucket(target_day.weekday())
    cutoff, reason_base = compute_cutoff(settings, bucket, samples)
    cutoff_text = cutoff.strftime("%H:%M")
    cutoff_at = datetime.combine(target_day, cutoff, tzinfo=tz)
    common = {"samples": len(samples), "cutoff": cutoff_text, "usual_window": usual_window(samples)}

    if today_first is None:
        if now_local > cutoff_at:
            return {
                "decision": "care_alert",
                "reason": f"No activity had been seen by {cutoff_text}. {reason_base}",
                "first_activity": "none",
                **common,
            }
        return {
            "decision": "pending",
            "reason": f"No activity yet; Rhythm waits until {cutoff_text} before speaking up. {reason_base}",
            **common,
        }

    first_time = today_first.time()
    first_text = first_time.strftime("%H:%M")
    if first_time > cutoff:
        return {
            "decision": "care_alert",
            "reason": f"First activity {first_text} is later than {cutoff_text}. {reason_base}",
            "first_activity": first_text,
            **common,
        }
    return {
        "decision": "normal",
        "reason": f"First activity {first_text} is not later than cutoff {cutoff_text}. {reason_base}",
        "first_activity": first_text,
        **common,
    }
