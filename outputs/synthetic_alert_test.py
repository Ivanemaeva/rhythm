#!/usr/bin/env python3
"""Deterministic synthetic check for Rhythm's morning alert rule (pure logic, no database).

Run with: python outputs/synthetic_alert_test.py

Part 1 uses the default demo floors (10:00 weekdays, 11:00 weekends).
Part 2 lowers the floors so the learned history, not the floor, decides the cutoff.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from random import Random
from zoneinfo import ZoneInfo

HOUSEHOLD_TZ = ZoneInfo("Europe/Rome")
START_DAY = date(2026, 9, 7)  # Monday
LEARNING_DAYS = 14
LATE_DAY_INDEX = 17
OFFLINE_DAY_INDEX = 20
MIN_SAMPLES = 5
DEFAULT_FLOORS = {"weekday": time(10, 0), "weekend": time(11, 0)}
MAX_MARGIN = timedelta(minutes=15)


@dataclass(frozen=True)
class Day:
    day: date
    first_activity: datetime | None
    online: bool = True

    @property
    def bucket(self) -> str:
        return "weekend" if self.day.weekday() >= 5 else "weekday"


def make_scenario() -> list[Day]:
    rng = Random(240923)
    days: list[Day] = []
    for index in range(21):
        current = START_DAY + timedelta(days=index)
        bucket = "weekend" if current.weekday() >= 5 else "weekday"
        usual = time(9, 0) if bucket == "weekend" else time(8, 0)
        noise = rng.randint(-20, 20) if bucket == "weekday" else rng.randint(-25, 25)
        activity_time = (datetime.combine(current, usual) + timedelta(minutes=noise)).time()

        if index == LATE_DAY_INDEX:
            activity_time = time(11, 30)
        if index == OFFLINE_DAY_INDEX:
            days.append(Day(current, None, online=False))
        else:
            days.append(Day(current, datetime.combine(current, activity_time, tzinfo=HOUSEHOLD_TZ)))
    return days


def calculate_cutoffs(history: list[Day], floors: dict[str, time] = DEFAULT_FLOORS) -> dict[str, time]:
    samples: dict[str, list[time]] = defaultdict(list)
    for day in history:
        if day.first_activity is not None:
            samples[day.bucket].append(day.first_activity.timetz().replace(tzinfo=None))

    cutoffs: dict[str, time] = {}
    for bucket, floor in floors.items():
        bucket_samples = samples[bucket]
        if len(bucket_samples) < MIN_SAMPLES:
            # Conservative fallback used until a bucket has enough observations.
            cutoffs[bucket] = floor
        else:
            ordered = sorted(bucket_samples)
            rank = max(
                0, min(len(ordered) - 1, -(-95 * len(ordered) // 100) - 1)
            )  # 95th percentile, nearest rank
            margin_cutoff = (datetime.combine(date.min, ordered[rank]) + MAX_MARGIN).time()
            cutoffs[bucket] = max(floor, margin_cutoff)
    return cutoffs


def evaluate(day: Day, cutoffs: dict[str, time]) -> str:
    if not day.online:
        return "device_offline"
    if day.first_activity is None:
        return "no_activity"
    if day.first_activity.timetz().replace(tzinfo=None) > cutoffs[day.bucket]:
        return "care_alert"
    return "normal"


def main() -> None:
    days = make_scenario()
    history = days[:LEARNING_DAYS]
    evaluation_days = days[LEARNING_DAYS:]
    cutoffs = calculate_cutoffs(history)

    outcomes = [(day, evaluate(day, cutoffs)) for day in evaluation_days]
    care_alerts = [day for day, outcome in outcomes if outcome == "care_alert"]
    offline_days = [day for day, outcome in outcomes if outcome == "device_offline"]
    normal_days = [day for day, outcome in outcomes if outcome == "normal"]
    assert len(days) == 21
    assert len(care_alerts) == 1, f"expected exactly one care alert, got {len(care_alerts)}"
    assert care_alerts[0].day == days[LATE_DAY_INDEX].day
    assert len(offline_days) == 1 and offline_days[0].day == days[OFFLINE_DAY_INDEX].day
    assert len(normal_days) == 5, f"expected five normal evaluation days, got {len(normal_days)}"

    print("Scenario: 21 days generated in Europe/Rome local time (14 learning, 7 evaluation)")
    print(
        f"Cutoffs (default floors): weekday {cutoffs['weekday'].strftime('%H:%M')}, weekend {cutoffs['weekend'].strftime('%H:%M')}"
    )
    print("Evaluation outcomes:")
    for day, outcome in outcomes:
        activity = day.first_activity.strftime("%H:%M %Z") if day.first_activity else "none"
        print(f"  {day.day.isoformat()} activity={activity:>12} -> {outcome}")
    print(
        "PASS 1: 0 care alerts on normal mornings; 1 on the late day; offline day uses the device_offline branch."
    )

    # Part 2: with low floors the learned history decides. Weekdays have 10 samples (>= 5);
    # weekends have only 4 in two weeks, so they still use the fixed fallback (the floor).
    low_floors = {"weekday": time(6, 0), "weekend": time(6, 0)}
    learned = calculate_cutoffs(history, low_floors)
    latest_weekday = max(
        d.first_activity.timetz().replace(tzinfo=None) for d in history if d.bucket == "weekday"
    )
    expected = (datetime.combine(date.min, latest_weekday) + MAX_MARGIN).time()
    assert learned["weekday"] == expected, (learned["weekday"], expected)
    assert (
        learned["weekday"] < DEFAULT_FLOORS["weekday"]
    ), "learned cutoff should be earlier than the 10:00 floor"
    assert learned["weekend"] == time(6, 0), "4 weekend samples is below the minimum, so the fallback applies"
    nine_thirty = Day(
        date(2026, 9, 24), datetime.combine(date(2026, 9, 24), time(9, 30), tzinfo=HOUSEHOLD_TZ)
    )
    assert evaluate(nine_thirty, learned) == "care_alert" and evaluate(nine_thirty, cutoffs) == "normal"
    print(
        f"PASS 2: with low floors the learned weekday cutoff is {learned['weekday'].strftime('%H:%M')}; "
        "a 09:30 morning alerts, while the 10:00 default floor would not. Weekends use the fallback (4 samples < 5)."
    )


if __name__ == "__main__":
    main()
