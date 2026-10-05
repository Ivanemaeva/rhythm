from __future__ import annotations

import smtplib
import ssl
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from html import escape
import re
from dataclasses import replace
from typing import Callable

from .config import Settings
from .profile import family_members
from .reply_links import email_reply_urls
from .rules import counts_as_activity, local_time
from .storage import Store

DISCLAIMER = "Rhythm is a check-in aid, not a safety or medical device."
DOORBELL_NOTE = "A doorbell only sees the front door, not the inside of the home, so a quiet morning can be completely normal."


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
    message.add_alternative(_html_email_body(body), subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.ehlo()
        if settings.smtp_use_starttls:
            server.starttls(context=context)
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


def _html_email_body(body: str) -> str:
    """Turn the two signed reply URL lines into email buttons; escape all other content."""
    paragraphs: list[str] = []
    button_line = re.compile(
        r"^(She's fine|She's away until…|Sta bene|È via fino a|Elle va bien|Elle est absente jusqu'au): (https?://\S+)$"
    )
    for line in body.splitlines():
        match = button_line.fullmatch(line)
        if match:
            label, url = match.groups()
            paragraphs.append(
                '<p style="margin:12px 0"><a href="' + escape(url, quote=True) + '" '
                'style="display:inline-block;background:#2d7665;color:#fff;padding:12px 18px;'
                'border-radius:8px;text-decoration:none;font-weight:700">'
                + escape(label, quote=False)
                + "</a></p>"
            )
        elif line:
            paragraphs.append('<p style="margin:0 0 12px">' + escape(line) + "</p>")
    return (
        '<!doctype html><html><body style="font:16px/1.5 Arial,sans-serif;color:#19302d;max-width:620px;margin:24px auto">'
        + "".join(paragraphs)
        + "</body></html>"
    )


def build_care_alert(result: dict[str, str | int], urls: dict[str, str] | None = None) -> tuple[str, str]:
    """Return (subject, plain-text body) for a care alert decision, including the human reason."""
    reason = str(result["reason"])
    first_activity = str(result.get("first_activity", "none"))
    cutoff = str(result.get("cutoff", "the usual time"))
    if first_activity == "none":
        headline = f"Rhythm has not seen any activity at the front door yet today, and her usual first activity is before {cutoff}."
    else:
        headline = f"Rhythm saw the first activity today at {first_activity} local time, later than usual."
    reply_section = ""
    if urls:
        reply_section = (
            "Reply securely (links expire after 24 hours; opening a link does not record a reply):\n"
            f"She's fine: {urls['fine']}\n"
            f"She's away until…: {urls['away']}\n\n"
            "The away link opens a page where you can choose the date. Pressing its confirmation button records the reply.\n\n"
        )
    body = (
        "A gentle check-in from Rhythm\n\n"
        f"{headline}\n\n"
        f"Why Rhythm spoke up: {reason}\n\n"
        f"{DOORBELL_NOTE} When you have a moment, please check in with her.\n\n"
        f"{reply_section}"
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


def _notice_key(kind: str, local_day: str, email: str) -> str:
    return f"{kind}:{local_day}:{email.strip().lower()}"


def deliver_care_alert(
    store: Store,
    settings: Settings,
    target_day: date,
    result: dict[str, str | int],
    sender: Callable[[Settings, str, str], None] = send_smtp_email,
) -> dict[str, object]:
    """Send at most one care email per family member per local day.

    Each member is tracked separately: if one address fails, the members who already received
    the alert are never emailed again, and only the failed address is retried on the next check.
    """
    if result.get("decision") != "care_alert":
        return {"delivery": "not_sent", "reason": "The rule did not produce a care alert."}

    local_day = target_day.isoformat()
    reason = str(result["reason"])
    claimed = store.claim_alert_day(local_day, "care_alert", reason, datetime.now(timezone.utc).isoformat())
    if not claimed and store.alert_status(local_day) != "sent":
        return {
            "delivery": "suppressed",
            "reason": "A care alert was already sent or reserved for this local day.",
        }

    members = family_members(store, settings)
    if not members:
        if claimed:
            store.release_alert_day(local_day)
        raise RuntimeError("No recipients: set ALERT_TO in .env or add family members on the Setup page.")
    pending = [m for m in members if not store.has_notice(_notice_key("care", local_day, m["email"]))]
    if not claimed and not pending:
        return {
            "delivery": "suppressed",
            "reason": "A care alert was already sent or reserved for this local day.",
        }

    errors: list[str] = []
    newly_sent = 0
    for member in pending:
        recipient_settings = replace(settings, alert_to=member["email"])
        nonces: list[str] = []
        try:
            urls = None
            # The care alert must never depend on optional reply links: without REPLY_TOKEN_SECRET
            # the email is still sent, just without the buttons.
            if settings.reply_token_secret:
                urls, nonces = email_reply_urls(store, recipient_settings, target_day)
            subject, body = localize(*build_care_alert(result, urls), member.get("language", "en"))
            sender(recipient_settings, subject, body)
            store.add_notice(_notice_key("care", local_day, member["email"]))
            newly_sent += 1
        except Exception as exc:  # one bad address must not block or repeat the others
            store.revoke_reply_tokens(nonces)
            errors.append(f"{member['email']}: {exc}")

    if newly_sent and str(result.get("first_activity")) == "none":
        # Remember it was a silent morning, so later activity can be reported in a follow-up.
        store.add_notice(f"silent-alert:{local_day}")
    delivered = len(members) - len(pending) + newly_sent
    if delivered == 0:
        store.release_alert_day(local_day)
        raise RuntimeError("Care alert could not be sent. " + "; ".join(errors))
    store.mark_alert_sent(local_day)
    if not claimed:
        return {"delivery": "retried" if newly_sent else "suppressed", "reason": reason, "failed": errors}
    return {"delivery": "partial" if errors else "sent", "reason": reason, "failed": errors}


TRANSLATIONS = {
    "it": {
        "A gentle check-in: later than usual": "Un piccolo controllo: più tardi del solito",
        "A gentle check-in from Rhythm": "Un piccolo controllo da Rhythm",
        "A gentle check-in": "Un piccolo controllo",
        "Why Rhythm spoke up:": "Perché Rhythm ti avvisa:",
        "A doorbell only sees the front door, not the inside of the home, so a quiet morning can be completely normal.": "Un campanello rileva solo la porta d'ingresso, non l'interno della casa: una mattina tranquilla può essere del tutto normale.",
        "When you have a moment, please check in with her.": "Quando puoi, contattala per sapere come sta.",
        "Reply securely (links expire after 24 hours; opening a link does not record a reply):": "Rispondi in modo sicuro (i link scadono dopo 24 ore; aprire un link non registra la risposta):",
        "She's fine:": "Sta bene:",
        "She's away until…:": "È via fino a:",
        "The away link opens a page where you can choose the date. Pressing its confirmation button records the reply.": "Il link per l'assenza apre una pagina dove puoi scegliere la data. La risposta viene registrata solo dopo la conferma.",
        "This may be her or a visitor.": "Potrebbe essere lei o un visitatore.",
        "Activity was recorded": "È stata registrata attività",
        "Rhythm's weekly summary": "Il riepilogo settimanale di Rhythm",
        "This week looked like a normal week.": "Questa settimana è sembrata nella norma.",
    },
    "fr": {
        "A gentle check-in: later than usual": "Petit message de Rhythm : plus tard que d'habitude",
        "A gentle check-in from Rhythm": "Un petit message de Rhythm",
        "A gentle check-in": "Un petit message",
        "Why Rhythm spoke up:": "Pourquoi Rhythm vous écrit :",
        "A doorbell only sees the front door, not the inside of the home, so a quiet morning can be completely normal.": "Une sonnette ne détecte que la porte d'entrée, pas l'intérieur du logement ; une matinée calme peut être tout à fait normale.",
        "When you have a moment, please check in with her.": "Quand vous le pouvez, prenez de ses nouvelles.",
        "Reply securely (links expire after 24 hours; opening a link does not record a reply):": "Réponse sécurisée (les liens expirent après 24 heures ; ouvrir un lien n'enregistre pas de réponse) :",
        "She's fine:": "Elle va bien:",
        "She's away until…:": "Elle est absente jusqu'au:",
        "The away link opens a page where you can choose the date. Pressing its confirmation button records the reply.": "Le lien d'absence ouvre une page où choisir la date. La réponse est enregistrée après confirmation.",
        "This may be her or a visitor.": "Il peut s'agir d'elle ou d'un visiteur.",
        "Activity was recorded": "Une activité a été enregistrée",
        "Rhythm's weekly summary": "Le résumé hebdomadaire de Rhythm",
        "This week looked like a normal week.": "Cette semaine a semblé normale.",
    },
}


def localize(subject: str, body: str, language: str) -> tuple[str, str]:
    mapping = TRANSLATIONS.get(language, {})
    if language in {"it", "fr"}:
        weekday = "feriali" if language == "it" else "jours de semaine"
        weekend = "fine settimana" if language == "it" else "week-end"
        translations = [
            (
                r"Only (\d+) (weekday|weekend) samples so far; using the conservative fixed cutoff ([\d:]+) local time\.",
                lambda m: (
                    f"Solo {m[1]} campioni {weekday if m[2]=='weekday' else weekend}; uso il limite prudenziale delle {m[3]} ora locale."
                    if language == "it"
                    else f"Seulement {m[1]} observations ({weekday if m[2]=='weekday' else weekend}) ; seuil prudent fixé à {m[3]} heure locale."
                ),
            ),
            (
                r"(\d+) (weekday|weekend) samples; 95th-percentile time plus (\d+) min, with a ([\d:]+) minimum wait floor\.",
                lambda m: (
                    f"{m[1]} campioni {weekday if m[2]=='weekday' else weekend}; percentile alto più {m[3]} min, con attesa minima alle {m[4]}."
                    if language == "it"
                    else f"{m[1]} observations ({weekday if m[2]=='weekday' else weekend}) ; percentile élevé plus {m[3]} min, avec une attente minimale jusqu'à {m[4]}."
                ),
            ),
            (
                r"No activity had been seen by ([\d:]+)\.",
                lambda m: (
                    f"Nessuna attività registrata entro le {m[1]}."
                    if language == "it"
                    else f"Aucune activité enregistrée avant {m[1]}."
                ),
            ),
            (
                r"No activity yet; Rhythm waits until ([\d:]+) before speaking up\.",
                lambda m: (
                    f"Nessuna attività per ora; Rhythm attende le {m[1]} prima di avvisare."
                    if language == "it"
                    else f"Aucune activité pour le moment ; Rhythm attend {m[1]} avant d'envoyer un message."
                ),
            ),
            (
                r"First activity ([\d:]+) is later than ([\d:]+)\.",
                lambda m: (
                    f"La prima attività delle {m[1]} è oltre il limite delle {m[2]}."
                    if language == "it"
                    else f"La première activité à {m[1]} est après le seuil de {m[2]}."
                ),
            ),
            (
                r"First activity ([\d:]+) is not later than cutoff ([\d:]+)\.",
                lambda m: (
                    f"La prima attività delle {m[1]} non supera il limite delle {m[2]}."
                    if language == "it"
                    else f"La première activité à {m[1]} ne dépasse pas le seuil de {m[2]}."
                ),
            ),
            (
                r"There were (\d+) later-than-usual morning alert\(s\) this week\.",
                lambda m: (
                    f"Questa settimana ci sono stati {m[1]} avvisi per mattine più tarde del solito."
                    if language == "it"
                    else f"Il y a eu {m[1]} alerte(s) pour un début de matinée plus tardif cette semaine."
                ),
            ),
            (
                r"Activity was recorded on (\d+) of the last 7 days\.",
                lambda m: (
                    f"È stata registrata attività in {m[1]} giorni su 7."
                    if language == "it"
                    else f"Une activité a été enregistrée {m[1]} jours sur les 7 derniers."
                ),
            ),
            (
                r"Rhythm has not seen any activity at the front door yet today, and her usual first activity is before ([\d:]+)\.",
                lambda m: (
                    f"Oggi non è stata ancora registrata attività alla porta d'ingresso; di solito la prima attività avviene prima delle {m[1]}."
                    if language == "it"
                    else f"Aucune activité n'a encore été enregistrée à la porte aujourd'hui ; sa première activité habituelle survient avant {m[1]}."
                ),
            ),
            (
                r"Rhythm saw the first activity today at ([\d:]+) local time, later than usual\.",
                lambda m: (
                    f"La prima attività di oggi è stata registrata alle {m[1]} ora locale, più tardi del solito."
                    if language == "it"
                    else f"La première activité du jour a été enregistrée à {m[1]} heure locale, plus tard que d'habitude."
                ),
            ),
        ]
        for pattern, replacement in translations:
            subject = re.sub(pattern, replacement, subject)
            body = re.sub(pattern, replacement, body)
    for en, translated in mapping.items():
        subject = subject.replace(en, translated)
        body = body.replace(en, translated)
    return subject, body


def _send_to_family(
    store: Store, settings: Settings, kind: str, key_day: str, subject: str, body: str, sender
) -> dict[str, object]:
    """Send one localized note to every family member who has not received it yet."""
    sent, errors = 0, []
    members = family_members(store, settings)
    if not members:
        return {
            "delivery": "failed",
            "failed": ["No recipients: set ALERT_TO or add family members on the Setup page."],
        }
    for member in members:
        key = _notice_key(kind, key_day, member["email"])
        if store.has_notice(key):
            continue
        try:
            sender(
                replace(settings, alert_to=member["email"]),
                *localize(subject, body, member.get("language", "en")),
            )
            store.add_notice(key)
            sent += 1
        except Exception as exc:  # retried on the next check; never blocks other members
            errors.append(f"{member['email']}: {exc}")
    return {"delivery": "sent" if sent else ("failed" if errors else "suppressed"), "failed": errors}


def deliver_all_clear(
    store: Store, settings: Settings, local_day: date, sender=send_smtp_email
) -> dict[str, object]:
    """After a silent-morning alert, report the first activity recorded later that day, once.

    It states what is known (activity was recorded) and never claims that she was seen.
    """
    day_text = local_day.isoformat()
    if store.alert_status(day_text) != "sent" or not store.has_notice(f"silent-alert:{day_text}"):
        # Only a silent-morning alert gets a follow-up; a late-activity alert already said when activity began.
        return {"delivery": "not_sent", "reason": "No silent-morning alert was sent today."}
    tz = settings.household_timezone
    later = [
        row
        for row in store.events_for_devices()
        if local_time(row, tz).date() == local_day and counts_as_activity(row, settings)
    ]
    if not later:
        return {"delivery": "not_sent", "reason": "No activity recorded after the alert yet."}
    event = later[0]
    what = "motion on a Ring device"
    sentence = (
        f"Activity was recorded at {local_time(event, tz):%H:%M} ({what}). This may be her or a visitor. "
        "If you haven't reached her yet, a quick call is still a good idea."
    )
    result = _send_to_family(
        store,
        settings,
        "all-clear",
        day_text,
        "Activity was recorded after the morning alert",
        sentence + "\n\n" + DISCLAIMER,
        sender,
    )
    result["reason"] = sentence
    return result


def deliver_weekly_summary(
    store: Store, settings: Settings, now_local: datetime, sender=send_smtp_email
) -> dict[str, object]:
    """One calm plain-language summary per week, sent on Sunday from 18:00 household time."""
    if now_local.weekday() != 6 or now_local.time() < time(18, 0):
        return {"delivery": "not_due"}
    tz = settings.household_timezone
    end = now_local.date()
    start = end - timedelta(days=6)
    active_days = {
        local_time(row, tz).date()
        for row in store.events_for_devices()
        if start <= local_time(row, tz).date() <= end and counts_as_activity(row, settings)
    }
    alerts = sum(
        1
        for row in store.list_alerts()
        if row["decision"] == "care_alert" and start.isoformat() <= str(row["local_day"]) <= end.isoformat()
    )
    sentence = (
        "This week looked like a normal week."
        if alerts == 0
        else f"There were {alerts} later-than-usual morning alert(s) this week."
    )
    sentence += f" Activity was recorded on {len(active_days)} of the last 7 days."
    year, week, _ = end.isocalendar()
    return _send_to_family(
        store,
        settings,
        "weekly",
        f"{year}-W{week:02d}",
        "Rhythm's weekly summary",
        sentence + "\n\n" + DISCLAIMER,
        sender,
    )


def build_connection_notice(result: dict[str, str | int]) -> tuple[str, str]:
    """Return (subject, body) for the lost-connection message (not a care alert)."""
    body = (
        "A note from Rhythm\n\n"
        "Rhythm has lost its connection to Ring, so it cannot tell whether her morning is usual.\n\n"
        f"Detail: {result['reason']}\n\n"
        "Please reconnect Rhythm to Ring (for example, renew the access token). This is not a care alert.\n\n"
        f"{DISCLAIMER}"
    )
    return "Rhythm: connection to Ring lost", body


def deliver_connection_notice(
    store: Store,
    settings: Settings,
    target_day: date,
    result: dict[str, str | int],
    sender: Callable[[Settings, str, str], None] = send_smtp_email,
) -> dict[str, object]:
    """Send at most one lost-connection message per local day (never a care alert)."""
    if result.get("decision") != "connection_lost":
        return {"delivery": "not_sent", "reason": "The Ring connection is fine."}
    subject, body = build_connection_notice(result)
    sent = _send_to_family(store, settings, "connection", target_day.isoformat(), subject, body, sender)
    sent["reason"] = str(result["reason"])
    return sent


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
    claimed = store.claim_alert_day(
        key, "device_offline", str(result["reason"]), datetime.now(timezone.utc).isoformat()
    )
    if not claimed:
        return {
            "delivery": "suppressed",
            "reason": "A device-offline message was already sent for this local day.",
        }
    subject, body = build_offline_notice(result)
    sent = _send_to_family(store, settings, "offline", target_day.isoformat(), subject, body, sender)
    if sent["delivery"] == "failed":
        store.release_alert_day(key)
        raise RuntimeError("Device-offline message could not be sent. " + "; ".join(sent["failed"]))
    store.mark_alert_sent(key)
    return {"delivery": "sent", "reason": str(result["reason"]), "failed": sent["failed"]}
