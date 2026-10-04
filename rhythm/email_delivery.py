from __future__ import annotations

import smtplib
import ssl
from datetime import date, datetime, timezone
from email.message import EmailMessage
from typing import Callable

from .config import Settings
from .storage import Store

DISCLAIMER = "Rhythm is a check-in aid, not a safety or medical device."
DOORBELL_NOTE = (
    "A doorbell only sees the front door, not the inside of the home, so a quiet morning can be completely normal."
)


def send_smtp_email(settings: Settings, subject: str, body: str) -> None:
    required = {
        "SMTP_HOST": settings.smtp_host,
        "SMTP_FROM": settings.smtp_from,
        "ALERT_TO": settings.alert_to,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("Set these environment variables before sending email: " + ", ".join(missing))

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = settings.alert_to
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.ehlo()
        if settings.smtp_use_starttls:
            server.starttls(context=context)
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


def build_care_alert(result: dict[str, str | int]) -> tuple[str, str]:
    """Return (subject, plain-text body) for a care alert decision, including the human reason."""
    reason = str(result["reason"])
    first_activity = str(result.get("first_activity", "none"))
    cutoff = str(result.get("cutoff", "the usual time"))
    if first_activity == "none":
        headline = f"Rhythm has not seen any activity at the front door yet today, and her usual first activity is before {cutoff}."
    else:
        headline = f"Rhythm saw the first activity today at {first_activity} local time, later than usual."
    body = (
        "A gentle check-in from Rhythm\n\n"
        f"{headline}\n\n"
        f"Why Rhythm spoke up: {reason}\n\n"
        f"{DOORBELL_NOTE} When you have a moment, please check in with her.\n\n"
        f"{DISCLAIMER}"
    )
    return "A gentle check-in: later than usual", body


def build_offline_notice(result: dict[str, str | int]) -> tuple[str, str]:
    """Return (subject, body) for the device-offline message (not a care alert)."""
    body = (
        "A note from Rhythm\n\n"
        "We can't see anything from the Ring device right now, so Rhythm cannot tell whether her morning is usual.\n\n"
        f"Detail: {result['reason']}\n\n"
        "Please check that the device is powered and connected to Wi-Fi. This is not a care alert.\n\n"
        f"{DISCLAIMER}"
    )
    return "Rhythm: we can't see the device", body


def deliver_care_alert(
    store: Store,
    settings: Settings,
    target_day: date,
    result: dict[str, str | int],
    sender: Callable[[Settings, str, str], None] = send_smtp_email,
) -> dict[str, str]:
    """Send at most one care email per local calendar day; release the reservation if SMTP fails."""
    if result.get("decision") != "care_alert":
        return {"delivery": "not_sent", "reason": "The rule did not produce a care alert."}

    local_day = target_day.isoformat()
    reason = str(result["reason"])
    claimed = store.claim_alert_day(local_day, "care_alert", reason, datetime.now(timezone.utc).isoformat())
    if not claimed:
        return {"delivery": "suppressed", "reason": "A care alert was already sent or reserved for this local day."}

    subject, body = build_care_alert(result)
    try:
        sender(settings, subject, body)
    except Exception:
        store.release_alert_day(local_day)
        raise
    store.mark_alert_sent(local_day)
    return {"delivery": "sent", "reason": reason}


def deliver_offline_notice(
    store: Store,
    settings: Settings,
    target_day: date,
    result: dict[str, str | int],
    sender: Callable[[Settings, str, str], None] = send_smtp_email,
) -> dict[str, str]:
    """Send at most one device-offline message per local day (tracked separately from care alerts)."""
    if result.get("decision") != "device_offline":
        return {"delivery": "not_sent", "reason": "The device is not reported offline."}
    key = f"offline-{target_day.isoformat()}"
    claimed = store.claim_alert_day(key, "device_offline", str(result["reason"]), datetime.now(timezone.utc).isoformat())
    if not claimed:
        return {"delivery": "suppressed", "reason": "A device-offline message was already sent for this local day."}
    subject, body = build_offline_notice(result)
    try:
        sender(settings, subject, body)
    except Exception:
        store.release_alert_day(key)
        raise
    store.mark_alert_sent(key)
    return {"delivery": "sent", "reason": str(result["reason"])}
