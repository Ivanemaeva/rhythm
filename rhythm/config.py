from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def load_dotenv_file(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE lines without overriding real environment values."""
    if not path.exists():
        return
    # utf-8-sig: Windows Notepad can save a byte-order mark at the start of the file.
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv_file()


def parse_clock(value: str) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":", maxsplit=1))
        return time(hour, minute)
    except ValueError as exc:
        raise ValueError(f"Invalid time {value!r}; use HH:MM, for example 10:00.") from exc


def load_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"Unknown HOUSEHOLD_TIMEZONE {name!r}. Use an IANA name such as Europe/Rome. "
            "On Windows, also run: pip install tzdata"
        ) from exc


def load_public_base_url(value: str) -> str:
    """Validate the origin used in email links; permit plain HTTP only on localhost."""
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("PUBLIC_BASE_URL must be an absolute http(s) URL without credentials.")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("PUBLIC_BASE_URL must be an origin only, such as http://127.0.0.1:8000.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("PUBLIC_BASE_URL contains an invalid port.") from exc
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and parsed.hostname not in local_hosts:
        raise ValueError("PUBLIC_BASE_URL must use HTTPS except for localhost testing.")
    return normalized


@dataclass(frozen=True)
class Settings:
    household_timezone: ZoneInfo
    learning_days: int
    min_samples_per_bucket: int
    max_margin_minutes: int
    minimum_wait_weekday: time
    minimum_wait_weekend: time
    fallback_weekday: time
    fallback_weekend: time
    ring_api_base_url: str
    ring_access_token: str
    ring_device_id: str
    ring_ingestion_mode: str
    ring_poll_seconds: int
    ring_webhook_secret: str
    database_path: Path
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    alert_to: str
    smtp_use_starttls: bool
    # Optional shared secret protecting the dashboard API and reply endpoints.
    # Required whenever the app is reachable from outside this computer.
    admin_token: str = ""
    public_base_url: str = "http://127.0.0.1:8000"
    reply_token_secret: str = ""
    baseline_days: int = 56
    # Events before this local time (night trips, pets, car lights) never count as the first activity.
    morning_start: time = time(5, 0)
    # Wait this long after the cutoff before a silent-morning alert, because Ring events can arrive late.
    alert_grace_minutes: int = 10
    # If Ring has not answered for this long, Rhythm reports a lost connection instead of a quiet morning.
    ring_stale_minutes: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            household_timezone=load_timezone(os.getenv("HOUSEHOLD_TIMEZONE", "Europe/Rome")),
            learning_days=int(os.getenv("LEARNING_DAYS", "14")),
            min_samples_per_bucket=int(os.getenv("MIN_SAMPLES_PER_BUCKET", "5")),
            max_margin_minutes=int(os.getenv("MAX_MARGIN_MINUTES", "15")),
            minimum_wait_weekday=parse_clock(os.getenv("MIN_WAIT_WEEKDAY", "10:00")),
            minimum_wait_weekend=parse_clock(os.getenv("MIN_WAIT_WEEKEND", "11:00")),
            fallback_weekday=parse_clock(os.getenv("FALLBACK_WEEKDAY", "10:00")),
            fallback_weekend=parse_clock(os.getenv("FALLBACK_WEEKEND", "11:00")),
            ring_api_base_url=os.getenv("RING_API_BASE_URL", "https://api.amazonvision.com").rstrip("/"),
            ring_access_token=os.getenv("RING_ACCESS_TOKEN", ""),
            ring_device_id=os.getenv("RING_DEVICE_ID", ""),
            ring_ingestion_mode=os.getenv("RING_INGESTION_MODE", "poll").lower(),
            ring_poll_seconds=int(os.getenv("RING_POLL_SECONDS", "120")),
            ring_webhook_secret=os.getenv("RING_WEBHOOK_SECRET", ""),
            database_path=Path(os.getenv("DATABASE_PATH", "data/rhythm.sqlite3")),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            alert_to=os.getenv("ALERT_TO", ""),
            smtp_use_starttls=os.getenv("SMTP_USE_STARTTLS", "true").lower() == "true",
            admin_token=os.getenv("RHYTHM_ADMIN_TOKEN", ""),
            public_base_url=load_public_base_url(os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")),
            reply_token_secret=os.getenv("REPLY_TOKEN_SECRET", ""),
            baseline_days=max(1, int(os.getenv("BASELINE_DAYS", "56"))),
            morning_start=parse_clock(os.getenv("MORNING_START", "05:00")),
            alert_grace_minutes=max(0, int(os.getenv("ALERT_GRACE_MINUTES", "10"))),
            ring_stale_minutes=max(5, int(os.getenv("RING_STALE_MINUTES", "30"))),
        )

    def bucket(self, weekday: int) -> str:
        return "weekend" if weekday >= 5 else "weekday"
