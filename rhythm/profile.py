"""Household profile saved on the Setup page, applied on top of the .env settings."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

from .config import Settings, load_timezone
from .storage import Store

# Sensitivity changes both the margin after her usual time and the minimum wait.
# (margin in minutes, shift of the minimum-wait floors and fixed fallbacks in minutes)
SENSITIVITY = {
    "Relaxed": (30, 30),
    "Standard": (None, 0),  # None keeps the MAX_MARGIN_MINUTES value from .env
    "Careful": (5, -30),
}


def _shift(value: time, minutes: int) -> time:
    return (datetime.combine(date.min, value) + timedelta(minutes=minutes)).time()


def effective_settings(base: Settings, store: Store) -> Settings:
    """Return base settings with the saved timezone and sensitivity applied exactly once."""
    settings = base
    timezone_name = store.get_state("profile_timezone")
    if timezone_name:
        settings = replace(settings, household_timezone=load_timezone(timezone_name))
    sensitivity = store.get_state("profile_sensitivity")
    if sensitivity in SENSITIVITY:
        margin, shift = SENSITIVITY[sensitivity]
        settings = replace(
            settings,
            max_margin_minutes=base.max_margin_minutes if margin is None else margin,
            minimum_wait_weekday=_shift(base.minimum_wait_weekday, shift),
            minimum_wait_weekend=_shift(base.minimum_wait_weekend, shift),
            fallback_weekday=_shift(base.fallback_weekday, shift),
            fallback_weekend=_shift(base.fallback_weekend, shift),
        )
    return settings


def family_members(store: Store, settings: Settings) -> list[dict[str, str]]:
    """Family from the Setup page, or the ALERT_TO address(es) from .env."""
    members = store.family()
    if members:
        return members
    language = store.get_state("profile_language") or "en"
    return [
        {"email": email.strip(), "name": "", "language": language}
        for email in settings.alert_to.split(",")
        if email.strip()
    ]
