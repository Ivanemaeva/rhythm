from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

from .config import Settings
from .ingestion import ingest_webhook
from .ring_api import RingApi
from .rules import decide_for_day
from .storage import Store

settings = Settings.from_env()
store = Store(settings.database_path)
app = FastAPI(title="Rhythm", description="Metadata-only Ring check-in aid")


def household_today() -> date:
    return datetime.now(settings.household_timezone).date()


def require_admin(x_admin_token: str = Header(default="")) -> None:
    """Protect the dashboard data, reply and sync endpoints.

    * RHYTHM_ADMIN_TOKEN set: every protected request must send it as X-Admin-Token.
    * Not set and running in webhook mode (the app is meant to be reachable from the internet):
      protected endpoints are disabled instead of left open.
    * Not set and running in poll mode (local demo on 127.0.0.1): open.
    """
    if settings.admin_token:
        if not hmac.compare_digest(x_admin_token, settings.admin_token):
            raise HTTPException(status_code=401, detail="Missing or invalid admin token.")
    elif settings.ring_ingestion_mode == "webhook":
        raise HTTPException(
            status_code=403,
            detail="Set RHYTHM_ADMIN_TOKEN to use the dashboard API while webhook mode is enabled.",
        )


@app.get("/", include_in_schema=False)
def dashboard_page() -> FileResponse:
    return FileResponse(Path(__file__).with_name("dashboard.html"))


@app.get("/api/dashboard", dependencies=[Depends(require_admin)])
def dashboard_data() -> dict[str, object]:
    today = household_today()
    start_day = today - timedelta(days=21)
    device_id = settings.ring_device_id or store.first_known_device_id()

    alerts = [dict(row) for row in store.list_alerts()]
    alert_days = {str(alert["local_day"]) for alert in alerts}
    points: dict[str, dict[str, object]] = {}
    for row in store.list_events(device_id, start_day.isoformat()) if device_id else []:
        local = datetime.fromisoformat(row["occurred_at_local"])
        local_date = local.date()
        key = local_date.isoformat()
        point = points.get(key)
        if point is None or local.isoformat() < str(point["_sort"]):
            points[key] = {
                "date": key,
                "bucket": "weekend" if local_date.weekday() >= 5 else "weekday",
                "time": local.strftime("%H:%M"),
                "minutes": local.hour * 60 + local.minute,
                "alert": key in alert_days,
                "_sort": local.isoformat(),
            }
    routine = [{key: value for key, value in point.items() if key != "_sort"} for point in points.values()]

    learning_started = store.get_state("learning_started_local") or today.isoformat()
    active_on = date.fromisoformat(learning_started) + timedelta(days=settings.learning_days)
    learning = today < active_on

    away_until = store.active_away_until(today.isoformat())
    alert_status = store.alert_status(today.isoformat())
    if store.latest_reply(today.isoformat()) is not None:
        suppression = "Today marked fine; care alerts suppressed."
    elif away_until:
        suppression = f"Away mode is suppressing alerts through {away_until}."
    elif alert_status == "demo":
        suppression = "A simulated care alert fired today; repeats are suppressed."
    elif alert_status in {"sent", "reserved"}:
        suppression = "One care alert has already been sent or reserved today; repeats are suppressed."
    else:
        suppression = "No care alert has been sent today."

    online = store.device_online(device_id) if device_id else None
    device_state = "online" if online is True else "offline" if online is False else "unknown"
    return {
        "timezone": settings.household_timezone.key,
        "device_id": device_id or None,
        "routine": routine,
        "alerts": alerts,
        "state": {
            "mode": "learning" if learning else "active",
            "learning_started": learning_started,
            "active_on": active_on.isoformat(),
            "suppression": suppression,
            "device": device_state,
            "today": today.isoformat(),
            "away_until": away_until,
        },
    }


@app.post("/api/reply/fine", dependencies=[Depends(require_admin)])
def reply_fine() -> dict[str, str]:
    today = household_today().isoformat()
    store.record_reply(today, "fine", datetime.now(timezone.utc).isoformat())
    return {"status": "ok", "message": "Thanks. Today's care alert is suppressed."}


@app.post("/api/reply/away", dependencies=[Depends(require_admin)])
def reply_away(payload: dict[str, str]) -> dict[str, str]:
    until_text = payload.get("until", "")
    try:
        until = date.fromisoformat(until_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter an away-through date in YYYY-MM-DD format.") from exc
    today = household_today()
    if until < today:
        raise HTTPException(status_code=422, detail="The away-through date must be today or later.")
    store.record_reply(today.isoformat(), "away", datetime.now(timezone.utc).isoformat(), until.isoformat())
    return {"status": "ok", "message": f"Alerts are paused through {until.isoformat()}."}


@app.get("/api/explanation", dependencies=[Depends(require_admin)])
def current_explanation() -> dict[str, str | int]:
    device_id = settings.ring_device_id or store.first_known_device_id()
    if not device_id:
        return {"decision": "unavailable", "reason": "Set RING_DEVICE_ID or sync a device before evaluating today."}
    return decide_for_day(store, settings, device_id, household_today())


def valid_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "ingestion_mode": settings.ring_ingestion_mode}


@app.post("/webhooks/ring")
async def ring_webhook(request: Request, x_signature: str = Header(default="")) -> dict[str, str]:
    raw = await request.body()
    if not valid_signature(raw, x_signature, settings.ring_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid Ring webhook signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "ignored", "ingested": "invalid_json"}
    if not isinstance(payload, dict):
        return {"status": "ignored", "ingested": "invalid_payload"}
    request_id = str((payload.get("meta") or {}).get("request_id", ""))
    if request_id and store.webhook_seen(request_id):
        return {"status": "duplicate"}
    result = ingest_webhook(payload, store, settings.household_timezone)
    return {"status": "accepted", "ingested": result}


@app.post("/admin/sync-ring", dependencies=[Depends(require_admin)])
def sync_ring() -> dict[str, object]:
    try:
        return RingApi(settings, store).sync()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
