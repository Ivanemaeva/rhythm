from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import Settings, load_timezone
from .ingestion import ingest_webhook
from .ring_api import RingApi
from .reply_links import InvalidReplyToken, parse_reply_token, reply_token_is_available
from .profile import effective_settings as profile_settings, family_members
from .rules import baseline, counts_as_activity, decide_for_day, local_time, usual_window
from .storage import Store
from .email_delivery import send_smtp_email, localize
from dataclasses import replace

settings = Settings.from_env()
store = Store(settings.database_path)
app = FastAPI(title="Rhythm", description="Metadata-only Ring check-in aid")


def effective_settings() -> Settings:
    return profile_settings(settings, store)


def household_today() -> date:
    return datetime.now(effective_settings().household_timezone).date()


def require_admin(x_admin_token: str = Header(default=""), token: str = "") -> None:
    """Protect the dashboard data, reply and sync endpoints.

    * RHYTHM_ADMIN_TOKEN set: every protected request must send it as X-Admin-Token.
    * Not set and running in webhook mode or with a non-local PUBLIC_BASE_URL:
      protected endpoints are disabled instead of left open.
    * Not set and running in poll mode (local demo on 127.0.0.1): open.
    """
    if settings.admin_token:
        x_admin_token = x_admin_token or token
        if not hmac.compare_digest(x_admin_token, settings.admin_token):
            raise HTTPException(status_code=401, detail="Missing or invalid admin token.")
    elif settings.ring_ingestion_mode == "webhook" or urlsplit(settings.public_base_url).hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise HTTPException(
            status_code=403,
            detail="Set RHYTHM_ADMIN_TOKEN before using the dashboard API in webhook or public-link mode.",
        )


@app.get("/", include_in_schema=False)
def dashboard_page() -> FileResponse:
    return FileResponse(Path(__file__).with_name("dashboard.html"))


SETUP_STYLE = """
body{font:17px/1.5 system-ui,sans-serif;max-width:650px;margin:40px auto;padding:20px;background:#f6f7f2;color:#19302d}
label{display:block;margin:14px 0}input,textarea,select{font:inherit;padding:9px;width:100%;box-sizing:border-box}
button{padding:12px 18px;background:#2d7665;color:#fff;border:0;border-radius:8px;font:inherit;cursor:pointer}
"""


def _option(value: str, label: str, current: str) -> str:
    selected = " selected" if value == current else ""
    return f'<option value="{value}"{selected}>{label}</option>'


@app.get("/setup", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def setup_page() -> HTMLResponse:
    profile = {
        k: store.get_state("profile_" + k) or "" for k in ("household", "timezone", "language", "sensitivity")
    }
    members = "\n".join(f"{m['name']},{m['email']}" for m in store.family())
    languages = "".join(
        _option(code, name, profile["language"] or "en")
        for code, name in (("en", "English"), ("it", "Italiano"), ("fr", "Français"))
    )
    sensitivities = "".join(
        _option(name, f"{name}: {hint}", profile["sensitivity"] or "Standard")
        for name, hint in (
            ("Relaxed", "waits 30 min longer"),
            ("Standard", "default"),
            ("Careful", "speaks up 30 min earlier"),
        )
    )
    timezone_name = profile["timezone"] or settings.household_timezone.key
    consent_checked = " checked" if store.get_state("profile_consent_date") else ""
    return HTMLResponse(f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Rhythm setup</title>
<style>{SETUP_STYLE}</style>
<h1>Household setup</h1>
<form method="post" action="/setup">
  <label>Household name<input name="household" value="{escape(profile['household'], quote=True)}" required></label>
  <label>Timezone (IANA, for example Europe/Rome)<input name="timezone" value="{escape(timezone_name, quote=True)}" required></label>
  <label>Family members, one per line: Name,email (up to three)
    <textarea name="members" rows="4">{escape(members)}</textarea></label>
  <label>Email language<select name="language">{languages}</select></label>
  <label>Sensitivity<select name="sensitivity">{sensitivities}</select></label>
  <label style="display:flex;gap:10px;align-items:flex-start"><input type="checkbox" name="consent" value="yes" required style="width:auto;margin-top:6px"{consent_checked}>
    <span>She knows Rhythm is running, has seen what it uses (activity times and device status only, never video or images), and agreed to it. She can ask to pause or stop it at any time.</span></label>
  <button>Save settings</button>
</form>
<p>SMTP passwords, Ring tokens and signing secrets stay in the .env file.</p>
<script>const q = location.search; if (q) document.querySelector("form").action += q;</script>
</html>""")


@app.post("/setup", dependencies=[Depends(require_admin)])
async def save_setup(request: Request) -> HTMLResponse:
    form = parse_qs((await request.body()).decode())

    def val(k):
        return (form.get(k) or [""])[0].strip()

    try:
        tz = load_timezone(val("timezone"))
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if val("sensitivity") not in {"Relaxed", "Standard", "Careful"} or val("language") not in {
        "en",
        "it",
        "fr",
    }:
        raise HTTPException(status_code=422, detail="Choose a supported language and sensitivity.")
    members = []
    for line in val("members").splitlines():
        if not line.strip():
            continue
        pair = line.rsplit(",", 1)
        if len(pair) != 2 or "@" not in pair[1]:
            raise HTTPException(status_code=422, detail="Each family line must be Name,email.")
        members.append({"name": pair[0].strip(), "email": pair[1].strip(), "language": val("language")})
    if len(members) > 3:
        raise HTTPException(status_code=422, detail="Use at most three family recipients.")
    if val("consent") != "yes":
        raise HTTPException(
            status_code=422,
            detail="Rhythm is only set up with her knowledge and agreement. Please confirm consent.",
        )
    store.set_state_once("profile_consent_date", datetime.now(tz).date().isoformat())
    for k, v in {
        "household": val("household"),
        "timezone": tz.key,
        "language": val("language"),
        "sensitivity": val("sensitivity"),
    }.items():
        store.set_state("profile_" + k, v)
    store.save_family(members)
    store.set_state_once("learning_started_local", datetime.now(tz).date().isoformat())
    target = "/?token=" + request.query_params.get("token", "") if request.query_params.get("token") else "/"
    return HTMLResponse(
        f'<meta http-equiv="refresh" content="1;url={escape(target,quote=True)}"><p>Settings saved. <a href="{escape(target,quote=True)}">Open dashboard</a>.</p>'
    )


@app.get("/api/export", dependencies=[Depends(require_admin)])
def export_data():
    return JSONResponse(
        store.export_data(), headers={"Content-Disposition": "attachment; filename=rhythm-export.json"}
    )


@app.post("/api/delete-everything", dependencies=[Depends(require_admin)])
def delete_everything():
    store.delete_everything()
    return {"status": "deleted"}


@app.get("/api/dashboard", dependencies=[Depends(require_admin)])
def dashboard_data() -> dict[str, object]:
    live_settings = effective_settings()
    today = household_today()
    start_day = today - timedelta(days=56)
    device_id = settings.ring_device_id or store.first_known_device_id()

    alerts = [dict(row) for row in store.list_alerts()]
    alert_days = {str(alert["local_day"]) for alert in alerts}
    points: dict[str, dict[str, object]] = {}
    for row in store.events_for_devices():
        if not counts_as_activity(row, live_settings):
            continue  # doorbell presses and night events are not her morning activity
        local = local_time(row, live_settings.household_timezone)
        local_date = local.date()
        if local_date < start_day:
            continue
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
    active_on = date.fromisoformat(learning_started) + timedelta(days=live_settings.learning_days)
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

    online = store.home_online()
    device_state = "online" if online is True else "offline" if online is False else "unknown"
    explanation = (
        decide_for_day(store, live_settings, device_id, today)
        if device_id
        else {"decision": "unavailable", "reason": "No Ring device connected."}
    )
    learned, _ = baseline(store, live_settings, today)
    members = family_members(store, live_settings)  # Setup page list, or ALERT_TO from .env
    responders = store.recent_replies()
    return {
        "timezone": live_settings.household_timezone.key,
        "device_id": ", ".join(store.all_device_ids()) or device_id or None,
        "routine": routine,
        "cutoff": explanation.get("cutoff"),
        "decision": explanation.get("decision"),
        "live_reason": explanation.get("reason"),
        # Learned window for today's bucket (weekday or weekend), excluding alert and away days.
        "usual_window": usual_window(learned),
        "usual_bucket": live_settings.bucket(today.weekday()),
        "household": store.get_state("profile_household") or "",
        "consent_date": store.get_state("profile_consent_date"),
        "retention_days": live_settings.baseline_days,
        "responders": responders,
        "family_count": len(members),
        "family": members,
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
def reply_fine(payload: dict[str, str] = Body(default={})) -> dict[str, str]:
    today = household_today().isoformat()
    store.record_reply(
        today, "fine", datetime.now(timezone.utc).isoformat(), recipient=payload.get("recipient")
    )
    notify_family_reply(today, "fine", "")
    return {"status": "ok", "message": "Thanks. Today's care alert is suppressed."}


@app.post("/api/reply/away", dependencies=[Depends(require_admin)])
def reply_away(payload: dict[str, str]) -> dict[str, str]:
    until_text = payload.get("until", "")
    try:
        until = date.fromisoformat(until_text)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Enter an away-through date in YYYY-MM-DD format."
        ) from exc
    today = household_today()
    if until < today:
        raise HTTPException(status_code=422, detail="The away-through date must be today or later.")
    store.record_reply(
        today.isoformat(),
        "away",
        datetime.now(timezone.utc).isoformat(),
        until.isoformat(),
        payload.get("recipient"),
    )
    notify_family_reply(today.isoformat(), "away", until.isoformat())
    return {"status": "ok", "message": f"Alerts are paused through {until.isoformat()}."}


def notify_family_reply(day: str, action: str, until: str) -> None:
    """Tell the other family members who answered, once per responder and day."""
    row = store.latest_reply_any(day)
    if row is None or not row["recipient"]:
        return
    recipient = str(row["recipient"])
    if not store.add_notice(f"reply-notice:{day}:{recipient.lower()}"):
        return
    live = effective_settings()
    members = store.family()
    who = next((m["name"] for m in members if m["email"] == recipient and m["name"]), recipient)
    when = datetime.fromisoformat(row["created_at_utc"]).astimezone(live.household_timezone).strftime("%H:%M")
    text = (
        f"{who} checked in at {when}."
        if action == "fine"
        else f"{who} marked her away through {until} (at {when})."
    )
    for member in members:
        if member["email"] == recipient:
            continue
        subject, body = localize("Rhythm family check-in", text, member.get("language", "en"))
        try:
            send_smtp_email(replace(live, alert_to=member["email"]), subject, body)
        except Exception:  # a failed courtesy note must never break the reply itself
            pass


def email_reply_html(title: str, message: str, token: str | None = None, action: str | None = None) -> str:
    """Small no-framework confirmation page; only POST applies the reply."""
    form = ""
    if token and action == "fine":
        form = (
            f'<form method="post" action="/reply/{escape(token, quote=True)}">'
            '<button type="submit">Confirm she’s fine</button></form>'
        )
    elif token and action == "away":
        today = household_today().isoformat()
        form = (
            f'<form method="post" action="/reply/{escape(token, quote=True)}">'
            f'<label for="until">Away through</label> <input id="until" name="until" type="date" min="{today}" value="{today}" required> '
            '<button type="submit">Confirm away dates</button></form>'
        )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Rhythm reply</title><style>:root{--bg:#f6f7f2;--fg:#19302d;--card:#fff;--line:#dce5df;--btn:#2d7665}body{font:16px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);max-width:560px;margin:12vh auto;padding:24px}"
        "[data-theme=dark]{--bg:#18211d;--fg:#edf4ef;--card:#222e28;--line:#394a43;--btn:#39866f}[data-theme=contrast]{--bg:#fff;--fg:#000;--card:#fff;--line:#000;--btn:#000}main{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px}button{background:var(--btn);color:white;border:0;border-radius:8px;padding:12px 18px;font:inherit;cursor:pointer}"
        "input{font:inherit;padding:9px;border:1px solid #cbd8d0;border-radius:7px}</style><main>"
        f"<h1>{escape(title)}</h1><p>{escape(message)}</p>{form}"
        f'<p style="color:#71817b;font-size:13px">{"Opening this page does not save a reply. " if form else ""}Rhythm is a check-in aid, not a safety or medical device.</p><label>Display <select id="theme"><option value="light">Light</option><option value="dark">Dark</option><option value="contrast">High contrast</option></select></label>'
        '</main><script>const s=localStorage.getItem("rhythm-theme")||(matchMedia("(prefers-contrast: more)").matches?"contrast":matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=s;document.getElementById("theme").value=s;document.getElementById("theme").onchange=e=>{document.documentElement.dataset.theme=e.target.value;localStorage.setItem("rhythm-theme",e.target.value)}</script></html>'
    )


def validated_email_reply(token: str) -> dict[str, str | int]:
    try:
        payload = parse_reply_token(token, settings.reply_token_secret)
    except InvalidReplyToken as exc:
        status = 410 if "expired" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    if not reply_token_is_available(store, payload):
        raise HTTPException(
            status_code=410, detail="This reply link has already been used or is no longer available."
        )
    return payload


@app.get("/reply/{token}", response_class=HTMLResponse)
def email_reply_confirmation(token: str) -> HTMLResponse:
    payload = validated_email_reply(token)
    if payload["action"] == "fine":
        title = "Confirm she’s fine"
        message = "Press the button below to suppress today’s care alert."
    else:
        title = "She’s away"
        message = "Choose the last date of the away period, then confirm. Rhythm will pause alerts through that date."
    return HTMLResponse(email_reply_html(title, message, token, str(payload["action"])))


@app.post("/reply/{token}", response_class=HTMLResponse)
async def email_reply_submit(token: str, request: Request) -> HTMLResponse:
    payload = validated_email_reply(token)
    action = str(payload["action"])
    away_until: str | None = None
    if action == "away":
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        until_text = (form.get("until") or [""])[0]
        try:
            until = date.fromisoformat(until_text)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Choose an away-through date in YYYY-MM-DD format."
            ) from exc
        if until < household_today():
            raise HTTPException(status_code=422, detail="The away-through date must be today or later.")
        away_until = until.isoformat()

    now_utc = datetime.now(timezone.utc).isoformat()
    # "Fine" answers the alert of the day the email was about, even if clicked after midnight.
    reply_day = str(payload["day"]) if action == "fine" else household_today().isoformat()
    consumed = store.consume_reply_token(
        str(payload["nonce"]),
        action,
        str(payload["day"]),
        reply_day,
        str(payload["recipient"]),
        now_utc,
        away_until,
    )
    if not consumed:
        raise HTTPException(
            status_code=410, detail="This reply link has already been used or is no longer available."
        )
    if action == "fine":
        notify_family_reply(reply_day, "fine", "")
        return HTMLResponse(email_reply_html("Thanks for checking in", "Today’s care alert is suppressed."))
    notify_family_reply(reply_day, "away", away_until or "")
    return HTMLResponse(email_reply_html("Away mode is on", f"Alerts are paused through {away_until}."))


@app.get("/api/explanation", dependencies=[Depends(require_admin)])
def current_explanation() -> dict[str, str | int]:
    device_id = settings.ring_device_id or store.first_known_device_id()
    if not device_id:
        return {
            "decision": "unavailable",
            "reason": "Set RING_DEVICE_ID or sync a device before evaluating today.",
        }
    return decide_for_day(store, effective_settings(), device_id, household_today())


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
    result = ingest_webhook(payload, store, effective_settings().household_timezone)
    return {"status": "accepted", "ingested": result}


@app.post("/admin/sync-ring", dependencies=[Depends(require_admin)])
def sync_ring() -> dict[str, object]:
    try:
        return RingApi(effective_settings(), store).sync()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
