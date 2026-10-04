"""Signed, expiring email reply links with persisted one-use nonces."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from datetime import date, datetime, timedelta, timezone

from .config import Settings
from .storage import Store

TOKEN_LIFETIME = timedelta(hours=24)


class InvalidReplyToken(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_reply_token(
    store: Store,
    settings: Settings,
    action: str,
    local_day: date,
    recipient: str,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Persist a one-time nonce and return the signed token and its nonce."""
    if action not in {"fine", "away"}:
        raise ValueError("Reply action must be 'fine' or 'away'.")
    if not settings.reply_token_secret:
        raise RuntimeError("Set REPLY_TOKEN_SECRET before sending email reply links.")
    if not recipient:
        raise RuntimeError("Set ALERT_TO before sending email reply links.")

    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires = created + TOKEN_LIFETIME
    store.purge_expired_reply_tokens(created.isoformat())
    nonce = secrets.token_urlsafe(18)
    payload = {
        "action": action,
        "day": local_day.isoformat(),
        "exp": int(expires.timestamp()),
        "nonce": nonce,
        "recipient": recipient,
        "v": 1,
    }
    encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64encode(
        hmac.new(
            settings.reply_token_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
        ).digest()
    )
    store.add_reply_token(nonce, action, local_day.isoformat(), recipient, expires.isoformat())
    return f"{encoded}.{signature}", nonce


def parse_reply_token(token: str, secret: str, now: datetime | None = None) -> dict[str, str | int]:
    if not secret or len(token) > 4096 or token.count(".") != 1:
        raise InvalidReplyToken("This reply link is invalid.")
    encoded, provided_signature = token.split(".", 1)
    expected_signature = _b64encode(
        hmac.new(secret.encode("utf-8"), encoded.encode("ascii", "ignore"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise InvalidReplyToken("This reply link is invalid.")
    try:
        payload = json.loads(_b64decode(encoded))
        action = payload["action"]
        day = date.fromisoformat(payload["day"])
        expiry = int(payload["exp"])
        nonce = payload["nonce"]
        recipient = payload["recipient"]
        version = payload["v"]
        if action not in {"fine", "away"} or version != 1 or not nonce or not recipient:
            raise ValueError
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidReplyToken("This reply link is invalid.") from exc
    current = int((now or datetime.now(timezone.utc)).timestamp())
    if expiry <= current:
        raise InvalidReplyToken("This reply link has expired.")
    return {
        "action": action,
        "day": day.isoformat(),
        "exp": expiry,
        "nonce": str(nonce),
        "recipient": str(recipient),
    }


def reply_token_is_available(
    store: Store, payload: dict[str, str | int], now: datetime | None = None
) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    return store.reply_token_available(
        str(payload["nonce"]), str(payload["action"]), str(payload["day"]), str(payload["recipient"]), current
    )


def email_reply_urls(store: Store, settings: Settings, local_day: date) -> tuple[dict[str, str], list[str]]:
    urls: dict[str, str] = {}
    nonces: list[str] = []
    try:
        for action in ("fine", "away"):
            token, nonce = issue_reply_token(store, settings, action, local_day, settings.alert_to)
            urls[action] = f"{settings.public_base_url}/reply/{token}"
            nonces.append(nonce)
    except Exception:
        store.revoke_reply_tokens(nonces)
        raise
    return urls, nonces
