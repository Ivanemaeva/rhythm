"""Evaluate Rhythm's rule on simulated households (SYNTHETIC data, clearly labelled as such).

Each simulated household has its own routine: a weekday wake time, a later weekend time,
day-to-day noise, occasional sleep-ins, night events, doorbell presses and one announced trip.
Some days carry a true change that the family would want to hear about:

* a late morning: the first activity comes 2 to 4 hours after her usual time
* a silent day: no activity at all

The real Rhythm rule (`rules.decide_for_day`, same code as the app) is run on every day after
the 14-day learning period, and its alerts are compared with the ground truth.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone

from .config import Settings
from .ingestion import ingest_webhook
from .rules import decide_for_day
from .storage import Store

DEVICE = "sim-device"
START = date(2026, 1, 5)  # a Monday


@dataclass
class SimDay:
    day: date
    first_minutes: int | None  # minutes after midnight of her first activity, None for a silent day
    truth: str  # "normal", "late", "silent" or "away"
    usual_minutes: int  # her usual time for this kind of day
    extra_events: list[tuple[int, str]] = field(default_factory=list)  # (minutes, "motion"|"doorbell")


def simulate_household(rng: random.Random, days: int) -> list[SimDay]:
    weekday_mean = rng.randint(6 * 60 + 30, 8 * 60 + 30)
    weekend_mean = weekday_mean + rng.randint(20, 90)
    noise = rng.uniform(10, 25)
    trip_start = rng.randint(30, days - 10)
    trip_days = set(range(trip_start, trip_start + rng.randint(3, 6)))
    result = []
    for index in range(days):
        day = START + timedelta(days=index)
        usual = weekend_mean if day.weekday() >= 5 else weekday_mean
        extras: list[tuple[int, str]] = []
        if rng.random() < 0.2:
            extras.append((rng.randint(60, 270), "motion"))  # night trip, pet or car lights (01:00-04:30)
        if rng.random() < 0.15:
            extras.append((rng.randint(8 * 60, 12 * 60), "doorbell"))  # a visitor or a delivery
        roll = rng.random()
        if index in trip_days:
            result.append(SimDay(day, None, "away", usual, [e for e in extras if e[1] == "doorbell"]))
            continue
        if index >= 14 and roll < 1 / 120:
            truth, first = "silent", None
        elif index >= 14 and roll < 1 / 120 + 1 / 40:
            truth, first = "late", usual + rng.randint(120, 240)
        else:
            truth = "normal"
            first = int(usual + rng.gauss(0, noise))
            if rng.random() < 0.05:
                first += rng.randint(30, 90)  # an ordinary sleep-in: not something to alert about
            first = max(5 * 60 + 15, first)
        if first is not None:
            extras += [(first + rng.randint(20, 300), "motion") for _ in range(3)]
        result.append(SimDay(day, first, truth, usual, extras))
    return result


def _webhook(event_id: str, when: datetime, kind: str) -> dict:
    return {
        "meta": {"request_id": f"r-{event_id}"},
        "data": {
            "id": event_id,
            "type": "button_press" if kind == "doorbell" else "motion_detected",
            "attributes": {
                "source": DEVICE,
                "timestamp": int(when.astimezone(timezone.utc).timestamp() * 1000),
            },
        },
    }


def run_household(settings: Settings, sim: list[SimDay]) -> list[dict]:
    """Run the real rule over one simulated household; return one outcome per evaluated day."""
    tz = settings.household_timezone
    store = Store(":memory:")
    store.set_device_status(DEVICE, True, "sim", "{}")
    store.set_state("learning_started_local", sim[0].day.isoformat())
    away = [d for d in sim if d.truth == "away"]
    if away:  # the family announced the trip with "She's away"
        store.record_reply(away[0].day.isoformat(), "away", "sim", away[-1].day.isoformat())
    outcomes = []
    for index, d in enumerate(sim):
        events = list(d.extra_events)
        if d.first_minutes is not None:
            events.append((d.first_minutes, "motion"))
        for n, (minutes, kind) in enumerate(events):
            when = datetime.combine(d.day, time(0), tzinfo=tz) + timedelta(minutes=minutes)
            ingest_webhook(_webhook(f"{d.day}-{n}", when, kind), store, tz, source="simulation")
        if index < settings.learning_days:
            continue
        result = decide_for_day(
            store, settings, DEVICE, d.day, datetime.combine(d.day, time(23, 59), tzinfo=tz)
        )
        alerted = result["decision"] == "care_alert"
        alert_minutes = None
        if alerted:
            cutoff_h, cutoff_m = (int(x) for x in str(result["cutoff"]).split(":"))
            silent_alert_at = cutoff_h * 60 + cutoff_m + settings.alert_grace_minutes
            alert_minutes = (
                silent_alert_at if d.first_minutes is None else min(d.first_minutes, silent_alert_at)
            )
            store.claim_alert_day(d.day.isoformat(), "care_alert", "sim", "sim")
            store.mark_alert_sent(d.day.isoformat())
        outcomes.append(
            {
                "truth": d.truth,
                "alerted": alerted,
                "delay_after_usual": None if alert_minutes is None else alert_minutes - d.usual_minutes,
            }
        )
    store.close()
    return outcomes


def evaluate(settings: Settings, households: int = 60, days: int = 112, seed: int = 2026) -> dict:
    """Run every simulated household and summarise the results."""
    rng = random.Random(seed)
    sims = [simulate_household(rng, days) for _ in range(households)]
    outcomes = [o for sim in sims for o in run_household(settings, sim)]
    normal = [o for o in outcomes if o["truth"] == "normal"]
    changes = [o for o in outcomes if o["truth"] in {"late", "silent"}]
    away = [o for o in outcomes if o["truth"] == "away"]
    delays = [o["delay_after_usual"] for o in changes if o["alerted"]]
    false_alerts = sum(o["alerted"] for o in normal)
    return {
        "households": households,
        "evaluated_days": len(outcomes),
        "normal_days": len(normal),
        "false_alerts": false_alerts,
        "false_alerts_per_month": 30 * false_alerts / max(1, len(normal)),
        "true_changes": len(changes),
        "late_caught": sum(o["alerted"] for o in changes if o["truth"] == "late"),
        "late_total": sum(1 for o in changes if o["truth"] == "late"),
        "silent_caught": sum(o["alerted"] for o in changes if o["truth"] == "silent"),
        "silent_total": sum(1 for o in changes if o["truth"] == "silent"),
        "detection_rate": sum(o["alerted"] for o in changes) / max(1, len(changes)),
        "away_alerts": sum(o["alerted"] for o in away),
        "median_minutes_after_usual": statistics.median(delays) if delays else None,
    }


def margin_sweep(base: Settings, margins: list[int], **kwargs) -> list[dict]:
    """Detection and false alerts for each margin, with and without the minimum-wait floor."""
    rows = []
    for with_floor in (True, False):
        for margin in margins:
            settings = replace(base, max_margin_minutes=margin)
            if not with_floor:
                settings = replace(settings, minimum_wait_weekday=time(0), minimum_wait_weekend=time(0))
            summary = evaluate(settings, **kwargs)
            rows.append({"margin": margin, "floor": with_floor, **summary})
    return rows
