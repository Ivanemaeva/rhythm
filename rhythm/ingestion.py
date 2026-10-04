from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .storage import Store

ACTIVITY_TYPES = {"motion_detected": "motion", "button_press": "doorbell"}
HISTORY_ACTIVITY_TYPES = {"motion": "motion", "ding": "doorbell"}


def timestamp_ms_to_local(value: int | float, household_timezone: ZoneInfo) -> tuple[datetime, datetime]:
    """Ring timestamps are epoch milliseconds (UTC); convert to UTC and household-local time."""
    utc = datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    return utc, utc.astimezone(household_timezone)


def ingest_webhook(payload: dict[str, Any], store: Store, household_timezone: ZoneInfo, source: str = "webhook") -> str:
    """Normalize one Ring webhook payload (metadata only) and store it. Returns the outcome label."""
    data = payload.get("data") or {}
    meta = payload.get("meta") or {}
    event_type = str(data.get("type", ""))
    attrs = data.get("attributes") or {}
    device_id = str(attrs.get("source", ""))
    timestamp_ms = attrs.get("timestamp")

    if event_type in {"device_offline", "device_online"} and device_id:
        stamp = (
            datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=timezone.utc)
            if timestamp_ms
            else datetime.now(timezone.utc)
        )
        store.set_device_status(device_id, event_type == "device_online", stamp.isoformat(), json.dumps(payload, sort_keys=True))
        outcome = event_type
    elif event_type in ACTIVITY_TYPES and device_id and timestamp_ms is not None:
        utc, local = timestamp_ms_to_local(timestamp_ms, household_timezone)
        store.add_event({
            "event_id": str(data.get("id") or meta.get("request_id")),
            "device_id": device_id,
            "event_type": ACTIVITY_TYPES[event_type],
            "occurred_at_utc": utc.isoformat(),
            "occurred_at_local": local.isoformat(),
            "source": source,
            "raw_metadata": json.dumps(payload, sort_keys=True),
        })
        # If webhooks are the first data path used, begin the silent period on this local day.
        # A prior poll/backfill sets this marker first when history is available.
        store.set_state_once("learning_started_local", local.date().isoformat())
        outcome = ACTIVITY_TYPES[event_type]
    else:
        outcome = "ignored"

    request_id = meta.get("request_id")
    if request_id:
        store.mark_webhook_seen(str(request_id), datetime.now(timezone.utc).isoformat())
    return outcome


def ingest_history_item(item: dict[str, Any], store: Store, household_timezone: ZoneInfo, default_device_id: str) -> bool:
    """Store one Event History item if it is motion or a doorbell press; skip everything else."""
    attrs = item.get("attributes") or {}
    event_type = HISTORY_ACTIVITY_TYPES.get(str(attrs.get("event_type", "")))
    # on_demand is a live-view event, not motion or a doorbell press, so it is intentionally excluded.
    if event_type is None or attrs.get("start") is None or "id" not in item:
        return False
    source = (item.get("relationships") or {}).get("source", {}).get("data", {}) or {}
    device_id = str(source.get("id") or default_device_id)
    utc, local = timestamp_ms_to_local(attrs["start"], household_timezone)
    return store.add_event({
        "event_id": str(item["id"]),
        "device_id": device_id,
        "event_type": event_type,
        "occurred_at_utc": utc.isoformat(),
        "occurred_at_local": local.isoformat(),
        "source": "event_history",
        "raw_metadata": json.dumps(item, sort_keys=True),
    })
