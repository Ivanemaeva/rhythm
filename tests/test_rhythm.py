"""Automated checks for Rhythm. Run with:  python -m unittest discover -s tests -v

Everything here is synthetic: no network, no real Ring data, no real email.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_APP_DIR = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = str(Path(_APP_DIR) / "app.sqlite3")
os.environ["RING_WEBHOOK_SECRET"] = "test-secret"
os.environ["RING_DEVICE_ID"] = "device-1"
os.environ["RING_INGESTION_MODE"] = "poll"
os.environ.pop("RHYTHM_ADMIN_TOKEN", None)

from rhythm.config import Settings  # noqa: E402
from rhythm.email_delivery import (
    _html_email_body,
    deliver_care_alert,
    deliver_offline_notice,
    deliver_all_clear,
    localize,
)  # noqa: E402
from rhythm.ingestion import ingest_history_item, ingest_webhook, timestamp_ms_to_local  # noqa: E402
from rhythm.ring_api import RingApi  # noqa: E402
from rhythm.reply_links import issue_reply_token  # noqa: E402
from rhythm.rules import decide_for_day  # noqa: E402
from rhythm.storage import Store  # noqa: E402
from scripts.check_morning import run_once  # noqa: E402

TZ = ZoneInfo("Europe/Rome")
DEVICE = "device-1"
THURSDAY = date(2026, 10, 1)
SUNDAY = date(2026, 10, 4)


def make_settings(path: Path) -> Settings:
    return replace(
        Settings.from_env(),
        database_path=path,
        ring_device_id=DEVICE,
        ring_access_token="",
        alert_to="family@example.test",
        reply_token_secret="test-reply-secret",
        public_base_url="http://127.0.0.1:8000",
    )


def webhook(event_id: str, when: datetime, kind: str = "motion_detected") -> dict:
    stamp = int(when.astimezone(timezone.utc).timestamp() * 1000)
    return {
        "meta": {"version": "1.1", "request_id": f"req-{event_id}"},
        "data": {"id": event_id, "type": kind, "attributes": {"source": DEVICE, "timestamp": stamp}},
    }


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


class RhythmCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.sqlite3"
        self.settings = make_settings(self.path)
        self.store = Store(self.path)
        self.store.set_device_status(DEVICE, True, datetime.now(timezone.utc).isoformat(), "{}")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def seed_history(self, target: date, days: int = 14) -> None:
        self.store.set_state("learning_started_local", (target - timedelta(days=days)).isoformat())
        for offset in range(days, 0, -1):
            day = target - timedelta(days=offset)
            usual = time(9) if day.weekday() >= 5 else time(8)
            ingest_webhook(webhook(f"h-{day}", datetime.combine(day, usual, tzinfo=TZ)), self.store, TZ)


class RuleTests(RhythmCase):
    def test_silent_morning_waits_then_alerts_after_cutoff(self) -> None:
        self.seed_history(THURSDAY)
        before = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 9, 30))
        after = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 10, 11))
        self.assertEqual(before["decision"], "pending")
        self.assertEqual(after["decision"], "care_alert")
        self.assertEqual(after["first_activity"], "none")
        self.assertIn("No activity had been seen by 10:00", str(after["reason"]))

    def test_normal_and_late_first_activity(self) -> None:
        self.seed_history(THURSDAY)
        ingest_webhook(webhook("today", at(THURSDAY, 8, 20)), self.store, TZ)
        normal = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 12))
        self.assertEqual(normal["decision"], "normal")
        self.store.close()
        self.store = Store(self.path)
        late_day = THURSDAY + timedelta(days=1)
        self.store.set_device_status(DEVICE, True, datetime.now(timezone.utc).isoformat(), "{}")
        self.seed_history(late_day)
        ingest_webhook(webhook("late", at(late_day, 10, 35)), self.store, TZ)
        late = decide_for_day(self.store, self.settings, DEVICE, late_day, at(late_day, 12))
        self.assertEqual(late["decision"], "care_alert")
        self.assertEqual(late["first_activity"], "10:35")

    def test_alerted_mornings_do_not_move_the_baseline(self) -> None:
        self.seed_history(THURSDAY)
        before = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 11))
        self.assertEqual(before["samples"], 10)
        # A late morning on a previous weekday that already triggered an alert is excluded from learning.
        late_day = THURSDAY - timedelta(days=1)
        self.store.claim_alert_day(
            late_day.isoformat(), "care_alert", "late", datetime.now(timezone.utc).isoformat()
        )
        self.store.mark_alert_sent(late_day.isoformat())
        after = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 11))
        self.assertEqual(after["samples"], 9)

    def test_learning_period_is_silent(self) -> None:
        self.store.set_state("learning_started_local", (THURSDAY - timedelta(days=3)).isoformat())
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 15))
        self.assertEqual(result["decision"], "learning")

    def test_few_weekend_samples_use_fixed_fallback(self) -> None:
        self.seed_history(SUNDAY)  # 14 days contain only 4 weekend days
        result = decide_for_day(self.store, self.settings, DEVICE, SUNDAY, at(SUNDAY, 11, 11))
        self.assertEqual(result["decision"], "care_alert")
        self.assertIn("Only 4 weekend samples", str(result["reason"]))

    def test_fine_suppresses_even_after_a_later_away_reply(self) -> None:
        self.seed_history(THURSDAY)
        now = datetime.now(timezone.utc).isoformat()
        self.store.record_reply(THURSDAY.isoformat(), "fine", now)
        self.store.record_reply(THURSDAY.isoformat(), "away", now, (THURSDAY - timedelta(days=1)).isoformat())
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 11))
        self.assertEqual(result["decision"], "suppressed_fine")

    def test_away_pauses_then_alerts_resume(self) -> None:
        self.seed_history(THURSDAY)
        self.store.record_reply(
            THURSDAY.isoformat(), "away", datetime.now(timezone.utc).isoformat(), "2026-10-02"
        )
        for day in (THURSDAY, THURSDAY + timedelta(days=1)):
            result = decide_for_day(self.store, self.settings, DEVICE, day, at(day, 11))
            self.assertEqual(result["decision"], "paused_away")
        resumed_day = THURSDAY + timedelta(days=2)  # Saturday, away ended on Friday
        self.assertEqual(self.store.active_away_until(resumed_day.isoformat()), None)

    def test_away_period_does_not_pause_days_before_it_starts(self) -> None:
        self.seed_history(THURSDAY)
        trip_start = THURSDAY + timedelta(days=3)
        self.store.record_reply(
            trip_start.isoformat(), "away", "x", (trip_start + timedelta(days=4)).isoformat()
        )
        before = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 11))
        self.assertEqual(before["decision"], "care_alert")

    def test_offline_device_is_not_a_care_alert(self) -> None:
        self.seed_history(THURSDAY)
        self.store.set_device_status(DEVICE, False, datetime.now(timezone.utc).isoformat(), "{}")
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 11))
        self.assertEqual(result["decision"], "device_offline")


class DeliveryTests(RhythmCase):
    def test_one_alert_per_day_and_failure_releases_reservation(self) -> None:
        self.seed_history(THURSDAY)
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 11))
        sent: list[tuple[str, str]] = []

        def failing(settings, subject, body):
            raise OSError("smtp down")

        with self.assertRaises(RuntimeError):
            deliver_care_alert(self.store, self.settings, THURSDAY, result, failing)
        self.assertIsNone(self.store.alert_status(THURSDAY.isoformat()))  # released, so a retry can send

        first = deliver_care_alert(
            self.store, self.settings, THURSDAY, result, lambda s, subj, body: sent.append((subj, body))
        )
        second = deliver_care_alert(
            self.store, self.settings, THURSDAY, result, lambda s, subj, body: sent.append((subj, body))
        )
        self.assertEqual((first["delivery"], second["delivery"]), ("sent", "suppressed"))
        self.assertEqual(len(sent), 1)
        body = sent[0][1]
        self.assertIn("Why Rhythm spoke up:", body)
        self.assertIn("not a safety or medical device", body)
        self.assertIn("front door", body)
        self.assertIn("/reply/", body)
        self.assertIn("She's fine:", body)
        self.assertIn("She's away until…:", body)
        html_body = _html_email_body(body)
        self.assertIn('href="http://127.0.0.1:8000/reply/', html_body)
        self.assertIn("She's fine</a>", html_body)

    def test_alert_is_still_sent_without_reply_link_secret(self) -> None:
        self.seed_history(THURSDAY)
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 11))
        outbox: list[str] = []
        settings = replace(self.settings, reply_token_secret="", alert_to="family@example.test")
        delivery = deliver_care_alert(
            self.store, settings, THURSDAY, result, lambda s, subject, body: outbox.append(body)
        )
        self.assertEqual(delivery["delivery"], "sent")
        self.assertIn("Why Rhythm spoke up:", outbox[0])
        self.assertNotIn("/reply/", outbox[0])

    def test_run_once_sends_silent_morning_alert_once(self) -> None:
        self.seed_history(THURSDAY)
        outbox: list[str] = []
        sender = lambda s, subject, body: outbox.append(subject)  # noqa: E731
        early = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 9, 0), sync=False, sender=sender)
        late = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 10, 15), sync=False, sender=sender)
        later = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 10, 45), sync=False, sender=sender)
        self.assertEqual(early["decision"], "pending")
        self.assertEqual(late["delivery"], "sent")
        self.assertEqual(later["delivery"], "suppressed")
        self.assertEqual(len(outbox), 1)

    def test_offline_notice_waits_until_morning_and_is_sent_once(self) -> None:
        self.seed_history(THURSDAY)
        self.store.set_device_status(DEVICE, False, datetime.now(timezone.utc).isoformat(), "{}")
        outbox: list[str] = []
        sender = lambda s, subject, body: outbox.append(subject)  # noqa: E731
        night = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 3, 0), sync=False, sender=sender)
        morning = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 9, 0), sync=False, sender=sender)
        again = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 9, 30), sync=False, sender=sender)
        self.assertNotIn("delivery", night)
        self.assertEqual((morning["delivery"], again["delivery"]), ("sent", "suppressed"))
        self.assertEqual(outbox, ["Rhythm: we can't see the device"])
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 9))
        self.assertEqual(
            deliver_offline_notice(self.store, self.settings, THURSDAY, result, sender)["delivery"],
            "suppressed",
        )


class IngestionTests(RhythmCase):
    def test_timestamps_are_utc_milliseconds_and_follow_daylight_saving(self) -> None:
        summer = int(datetime(2026, 10, 24, 6, 30, tzinfo=timezone.utc).timestamp() * 1000)
        winter = int(datetime(2026, 10, 26, 6, 30, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(timestamp_ms_to_local(summer, TZ)[1].strftime("%H:%M %Z"), "08:30 CEST")
        self.assertEqual(timestamp_ms_to_local(winter, TZ)[1].strftime("%H:%M %Z"), "07:30 CET")

    def test_history_ingests_motion_and_ding_but_not_on_demand(self) -> None:
        def item(item_id: str, kind: str) -> dict:
            return {
                "id": item_id,
                "attributes": {"event_type": kind, "start": 1791136314302, "end": 1791136354014},
            }

        results = [
            ingest_history_item(item(f"e-{kind}", kind), self.store, TZ, DEVICE)
            for kind in ("motion", "ding", "on_demand")
        ]
        self.assertEqual(results, [True, True, False])
        self.assertEqual(self.store.count_events(DEVICE), 2)

    def test_duplicate_event_id_is_ignored(self) -> None:
        payload = webhook("same", at(THURSDAY, 8))
        ingest_webhook(payload, self.store, TZ)
        ingest_webhook(payload, self.store, TZ)
        self.assertEqual(self.store.count_events(DEVICE), 1)

    def test_offline_and_online_webhooks_update_device_status(self) -> None:
        ingest_webhook(webhook("off", at(THURSDAY, 8), "device_offline"), self.store, TZ)
        self.assertIs(self.store.device_online(DEVICE), False)
        ingest_webhook(webhook("on", at(THURSDAY, 9), "device_online"), self.store, TZ)
        self.assertIs(self.store.device_online(DEVICE), True)


class RingApiTests(RhythmCase):
    """Cover Ring's HTTP boundary with local mock responses, never network access."""

    def test_sync_stores_status_and_only_supported_history_events(self) -> None:
        first = at(THURSDAY, 8, 15)
        stamp = int(first.astimezone(timezone.utc).timestamp() * 1000)
        status = {"data": {"attributes": {"online": True, "reported_at": "2026-10-01T06:15:00Z"}}}
        history = {
            "data": [
                {"id": "motion-1", "attributes": {"event_type": "motion", "start": stamp}},
                {"id": "live-view-1", "attributes": {"event_type": "on_demand", "start": stamp}},
                {"id": "ding-1", "attributes": {"event_type": "ding", "start": stamp + 60_000}},
            ],
            "links": {},
        }
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.side_effect = [
            httpx.Response(
                200,
                json={"data": [{"id": DEVICE}]},
                request=httpx.Request("GET", "https://example.test/devices"),
            ),
            httpx.Response(200, json=status, request=httpx.Request("GET", "https://example.test/status")),
            httpx.Response(200, json=history, request=httpx.Request("GET", "https://example.test/events")),
        ]

        with patch("rhythm.ring_api.httpx.Client", return_value=client):
            result = RingApi(replace(self.settings, ring_access_token="test-token"), self.store).sync()

        self.assertIs(result["online"], True)
        self.assertEqual(result["events_added"], 2)
        self.assertIs(self.store.device_online(DEVICE), True)
        events = self.store.events_for_device(DEVICE)
        self.assertEqual([row["event_type"] for row in events], ["motion", "doorbell"])
        self.assertEqual(events[0]["occurred_at_local"], first.isoformat())
        self.assertEqual(client.get.call_count, 3)

    def test_forbidden_history_starts_learning_without_losing_status(self) -> None:
        status = {"online": True, "reported_at": "2026-10-01T06:15:00Z"}
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.side_effect = [
            httpx.Response(
                200,
                json={"data": [{"id": DEVICE}]},
                request=httpx.Request("GET", "https://example.test/devices"),
            ),
            httpx.Response(200, json=status, request=httpx.Request("GET", "https://example.test/status")),
            httpx.Response(403, json={}, request=httpx.Request("GET", "https://example.test/events")),
        ]

        with patch("rhythm.ring_api.httpx.Client", return_value=client):
            result = RingApi(replace(self.settings, ring_access_token="test-token"), self.store).sync()

        self.assertFalse(result["history_available"])
        self.assertIs(self.store.device_online(DEVICE), True)
        self.assertEqual(self.store.get_state("learning_started_local"), datetime.now(TZ).date().isoformat())

    def test_sync_discovers_all_devices(self) -> None:
        stamp = int(at(THURSDAY, 8).astimezone(timezone.utc).timestamp() * 1000)
        responses = [
            {"data": [{"id": "one"}, {"id": "two"}]},
            {"online": True},
            {"data": [{"id": "e1", "attributes": {"event_type": "motion", "start": stamp}}], "links": {}},
            {"online": False},
            {
                "data": [{"id": "e2", "attributes": {"event_type": "ding", "start": stamp + 60000}}],
                "links": {},
            },
        ]
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.side_effect = [
            httpx.Response(200, json=x, request=httpx.Request("GET", "https://example.test/"))
            for x in responses
        ]
        settings = replace(self.settings, ring_device_id="", ring_access_token="token")
        with patch("rhythm.ring_api.httpx.Client", return_value=client):
            result = RingApi(settings, self.store).sync()
        self.assertEqual(result["devices"], 2)
        self.assertEqual({r["device_id"] for r in self.store.events_for_devices()}, {"one", "two"})
        self.assertIs(self.store.home_online(), True)


class AddedFeatureTests(RhythmCase):
    def test_localized_threshold_reason_and_reply_buttons(self) -> None:
        subject, body = localize(
            "A gentle check-in: later than usual",
            "Why Rhythm spoke up: First activity 10:35 is later than 10:00. She's fine: https://example.test/token",
            "it",
        )
        self.assertIn("Perché Rhythm ti avvisa", body)
        self.assertIn("La prima attività delle 10:35", body)
        self.assertIn("Sta bene: https://example.test/token", body)
        self.assertIn("Un piccolo controllo", subject)

    def test_first_activity_aggregates_devices_and_rolling_window(self) -> None:
        self.store.set_state("learning_started_local", (THURSDAY - timedelta(days=20)).isoformat())
        other = "device-2"
        self.store.set_device_status(other, True, datetime.now(timezone.utc).isoformat(), "{}")
        old_day = THURSDAY - timedelta(days=100)
        self.store.add_event(
            {
                "event_id": "old",
                "device_id": other,
                "event_type": "motion",
                "occurred_at_utc": at(old_day, 6).astimezone(timezone.utc).isoformat(),
                "occurred_at_local": at(old_day, 6).isoformat(),
                "source": "test",
                "raw_metadata": "{}",
            }
        )
        self.store.add_event(
            {
                "event_id": "multi",
                "device_id": other,
                "event_type": "motion",
                "occurred_at_utc": at(THURSDAY, 7, 30).astimezone(timezone.utc).isoformat(),
                "occurred_at_local": at(THURSDAY, 7, 30).isoformat(),
                "source": "test",
                "raw_metadata": "{}",
            }
        )
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 12))
        self.assertEqual(result["decision"], "normal")
        self.assertEqual(result["first_activity"], "07:30")
        self.assertEqual(result["samples"], 0)

    def test_all_clear_reports_activity_without_claiming_person(self) -> None:
        day = datetime.now(TZ).date()
        local = datetime.combine(day, time(11, 35), tzinfo=TZ)
        alert_created = (local - timedelta(minutes=40)).astimezone(timezone.utc).isoformat()
        self.store.claim_alert_day(day.isoformat(), "care_alert", "late", alert_created)
        self.store.mark_alert_sent(day.isoformat())
        self.store.add_notice(f"silent-alert:{day.isoformat()}")  # the alert was about a silent morning
        self.store.add_event(
            {
                "event_id": "after-alert",
                "device_id": DEVICE,
                "event_type": "motion",
                "occurred_at_utc": local.astimezone(timezone.utc).isoformat(),
                "occurred_at_local": local.isoformat(),
                "source": "test",
                "raw_metadata": "{}",
            }
        )
        emails = []
        first = deliver_all_clear(self.store, self.settings, day, lambda s, sub, b: emails.append(b))
        second = deliver_all_clear(self.store, self.settings, day, lambda s, sub, b: emails.append(b))
        self.assertEqual(first["delivery"], "sent")
        self.assertEqual(second["delivery"], "suppressed")
        self.assertIn(
            "Activity was recorded at 11:35 (motion on a Ring device). This may be her or a visitor.",
            emails[0],
        )
        self.assertNotIn("seen", emails[0].lower())

    def test_privacy_retention_and_delete_everything(self) -> None:
        day = THURSDAY - timedelta(days=100)
        self.store.add_event(
            {
                "event_id": "old",
                "device_id": DEVICE,
                "event_type": "motion",
                "occurred_at_utc": at(day, 7).astimezone(timezone.utc).isoformat(),
                "occurred_at_local": at(day, 7).isoformat(),
                "source": "test",
                "raw_metadata": "{}",
            }
        )
        self.assertEqual(self.store.delete_old_events(THURSDAY.isoformat()), 1)
        self.store.save_family([{"name": "A", "email": "a@example.test", "language": "en"}])
        self.assertEqual(len(self.store.export_data()["family"]), 1)
        self.store.delete_everything()
        self.assertEqual(self.store.all_device_ids(), [])
        self.assertEqual(self.store.family(), [])


class MorningLogicTests(RhythmCase):
    """Morning window, doorbell presses, grace period and lost Ring connection."""

    def test_night_event_does_not_hide_a_silent_morning(self) -> None:
        self.seed_history(THURSDAY)
        ingest_webhook(webhook("night", at(THURSDAY, 3, 0)), self.store, TZ)
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 10, 11))
        self.assertEqual(result["decision"], "care_alert")
        self.assertEqual(result["first_activity"], "none")

    def test_doorbell_press_does_not_count_as_her_activity(self) -> None:
        self.seed_history(THURSDAY)
        ingest_webhook(webhook("visitor", at(THURSDAY, 9, 0), "button_press"), self.store, TZ)
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 10, 11))
        self.assertEqual(result["decision"], "care_alert")

    def test_grace_period_waits_for_late_arriving_events(self) -> None:
        self.seed_history(THURSDAY)
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 10, 5))
        self.assertEqual(result["decision"], "pending")

    def test_lost_ring_connection_is_not_a_care_alert(self) -> None:
        self.seed_history(THURSDAY)
        polling = replace(self.settings, ring_access_token="token")
        self.store.set_state(
            "ring_last_contact_utc", (at(THURSDAY, 8, 0)).astimezone(timezone.utc).isoformat()
        )
        sent: list[str] = []
        summary = run_once(
            polling,
            self.store,
            DEVICE,
            at(THURSDAY, 10, 15),
            sync=False,
            sender=lambda s, subject, body: sent.append(subject),
        )
        run_once(
            polling,
            self.store,
            DEVICE,
            at(THURSDAY, 10, 30),
            sync=False,
            sender=lambda s, subject, body: sent.append(subject),
        )
        self.assertEqual(summary["decision"], "connection_lost")
        self.assertEqual(sent, ["Rhythm: connection to Ring lost"])
        self.assertIsNone(self.store.alert_status(THURSDAY.isoformat()))

    def test_recent_ring_contact_allows_the_care_alert(self) -> None:
        self.seed_history(THURSDAY)
        polling = replace(self.settings, ring_access_token="token")
        self.store.set_state(
            "ring_last_contact_utc", (at(THURSDAY, 10, 10)).astimezone(timezone.utc).isoformat()
        )
        result = decide_for_day(self.store, polling, DEVICE, THURSDAY, at(THURSDAY, 10, 15))
        self.assertEqual(result["decision"], "care_alert")

    def test_verbose_sync_shows_requests_but_never_the_token(self) -> None:
        import contextlib
        import io

        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        request = httpx.Request("GET", "https://example.test")
        client.get.side_effect = [
            httpx.Response(200, json={"data": [{"id": DEVICE}]}, request=request),
            httpx.Response(200, json={"data": {"attributes": {"online": True}}}, request=request),
            httpx.Response(200, json={"data": [], "links": {}}, request=request),
        ]
        output = io.StringIO()
        with patch("rhythm.ring_api.httpx.Client", return_value=client), contextlib.redirect_stdout(output):
            RingApi(
                replace(self.settings, ring_access_token="secret-token-123"), self.store, verbose=True
            ).sync()
        self.assertIn("-> GET", output.getvalue())
        self.assertIn("/status", output.getvalue())
        self.assertNotIn("secret-token-123", output.getvalue())

    def test_care_email_shows_usual_window_and_device_status(self) -> None:
        self.seed_history(THURSDAY)
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 11))
        bodies: list[str] = []
        deliver_care_alert(
            self.store, self.settings, THURSDAY, result, lambda s, subject, body: bodies.append(body)
        )
        self.assertIn("Her usual first activity on weekdays is between 08:00 and 08:00.", bodies[0])
        self.assertIn("The Ring device is online", bodies[0])

    def test_successful_sync_records_ring_contact(self) -> None:
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        request = httpx.Request("GET", "https://example.test")
        client.get.side_effect = [
            httpx.Response(200, json={"data": [{"id": DEVICE}]}, request=request),
            httpx.Response(200, json={"data": {"attributes": {"online": True}}}, request=request),
            httpx.Response(200, json={"data": [], "links": {}}, request=request),
        ]
        with patch("rhythm.ring_api.httpx.Client", return_value=client):
            RingApi(replace(self.settings, ring_access_token="token"), self.store).sync()
        self.assertIsNotNone(self.store.get_state("ring_last_contact_utc"))


class EvaluationTests(unittest.TestCase):
    def test_simulation_catches_every_silent_day_and_never_alerts_during_trips(self) -> None:
        from rhythm.evaluation import evaluate

        settings = replace(make_settings(Path(":memory:")), ring_access_token="")
        summary = evaluate(settings, households=4, days=70, seed=7)
        self.assertEqual(summary["silent_caught"], summary["silent_total"])
        self.assertEqual(summary["away_alerts"], 0)
        self.assertLess(summary["false_alerts_per_month"], 1.0)


class ReviewRegressionTests(RhythmCase):
    """Bugs found in review; each test failed before its fix."""

    def outbox(self, fail_for: str = "", fail_subject: str = ""):
        sent: list[tuple[str, str]] = []

        def sender(settings, subject, body):
            if fail_for and settings.alert_to == fail_for:
                raise OSError("smtp reject")
            if fail_subject and fail_subject in subject:
                raise OSError("smtp down")
            sent.append((settings.alert_to, subject))

        return sent, sender

    def test_all_clear_is_sent_after_silent_morning_then_late_activity(self) -> None:
        self.seed_history(THURSDAY)
        sent, sender = self.outbox()
        run_once(self.settings, self.store, DEVICE, at(THURSDAY, 10, 15), sync=False, sender=sender)
        ingest_webhook(webhook("late", at(THURSDAY, 11, 35)), self.store, TZ)
        summary = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 11, 40), sync=False, sender=sender)
        run_once(self.settings, self.store, DEVICE, at(THURSDAY, 11, 45), sync=False, sender=sender)
        self.assertIn("Activity was recorded at 11:35", summary["all_clear"])
        self.assertEqual(
            [subject for _, subject in sent],
            ["A gentle check-in: later than usual", "Activity was recorded after the morning alert"],
        )

    def test_no_all_clear_when_alert_was_triggered_by_the_late_activity_itself(self) -> None:
        self.seed_history(THURSDAY)
        ingest_webhook(webhook("late", at(THURSDAY, 10, 35)), self.store, TZ)
        sent, sender = self.outbox()
        run_once(self.settings, self.store, DEVICE, at(THURSDAY, 10, 40), sync=False, sender=sender)
        run_once(self.settings, self.store, DEVICE, at(THURSDAY, 10, 45), sync=False, sender=sender)
        self.assertEqual(len(sent), 1)

    def test_broken_family_address_never_repeats_alert_to_the_others(self) -> None:
        self.seed_history(THURSDAY)
        self.store.save_family(
            [
                {"name": "Ana", "email": "ana@example.test", "language": "en"},
                {"name": "Bob", "email": "bob@broken.test", "language": "en"},
            ]
        )
        sent, sender = self.outbox(fail_for="bob@broken.test")
        for minute in (15, 17, 19):
            summary = run_once(
                self.settings, self.store, DEVICE, at(THURSDAY, 10, minute), sync=False, sender=sender
            )
        self.assertEqual([to for to, _ in sent], ["ana@example.test"])
        self.assertEqual(self.store.alert_status(THURSDAY.isoformat()), "sent")
        self.assertIn(summary["delivery"], {"suppressed", "retried"})

    def test_weekly_summary_is_retried_after_a_failure(self) -> None:
        sunday = THURSDAY + timedelta(days=3)
        self.seed_history(sunday)
        _, failing = self.outbox(fail_subject="weekly summary")
        first = run_once(self.settings, self.store, DEVICE, at(sunday, 18, 30), sync=False, sender=failing)
        self.assertIn("weekly_summary", first)
        sent, sender = self.outbox()
        run_once(self.settings, self.store, DEVICE, at(sunday, 19, 0), sync=False, sender=sender)
        run_once(self.settings, self.store, DEVICE, at(sunday, 19, 30), sync=False, sender=sender)
        self.assertEqual([subject for _, subject in sent].count("Rhythm's weekly summary"), 1)

    def test_usual_window_excludes_alert_days_and_uses_todays_bucket(self) -> None:
        self.seed_history(THURSDAY)
        late_day = THURSDAY - timedelta(days=1)
        ingest_webhook(webhook("very-late", at(late_day, 7, 0)), self.store, TZ)  # earlier than usual
        self.store.claim_alert_day(
            late_day.isoformat(), "care_alert", "x", datetime.now(timezone.utc).isoformat()
        )
        self.store.mark_alert_sent(late_day.isoformat())
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 9))
        self.assertEqual(result["usual_window"], ["08:00", "08:00"])  # weekday only, alert day left out

    def test_timezone_change_is_applied_to_stored_events(self) -> None:
        self.seed_history(THURSDAY)
        ingest_webhook(webhook("today", at(THURSDAY, 8, 20)), self.store, TZ)
        self.store.set_state("profile_timezone", "Europe/London")  # one hour behind Rome
        from rhythm.profile import effective_settings

        london = effective_settings(self.settings, self.store)
        result = decide_for_day(self.store, london, DEVICE, THURSDAY, at(THURSDAY, 12))
        self.assertEqual(result["first_activity"], "07:20")

    def test_french_email_is_translated(self) -> None:
        from rhythm.email_delivery import build_care_alert

        result = {
            "reason": "No activity had been seen by 10:00.",
            "first_activity": "none",
            "cutoff": "10:00",
        }
        subject, body = localize(*build_care_alert(result), "fr")
        self.assertIn("plus tard que d'habitude", subject)
        self.assertIn("Pourquoi Rhythm vous écrit", body)
        self.assertIn("Aucune activité enregistrée avant 10:00.", body)

    def test_sensitivity_changes_the_minimum_wait(self) -> None:
        from rhythm.profile import effective_settings

        self.store.set_state("profile_sensitivity", "Careful")
        careful = effective_settings(self.settings, self.store)
        self.store.set_state("profile_sensitivity", "Relaxed")
        relaxed = effective_settings(self.settings, self.store)
        self.assertEqual((careful.minimum_wait_weekday, careful.max_margin_minutes), (time(9, 30), 5))
        self.assertEqual((relaxed.minimum_wait_weekday, relaxed.max_margin_minutes), (time(10, 30), 30))


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient

        import rhythm.app as app_module

        cls.app_module = app_module
        cls.client = TestClient(app_module.app)

    def signed(self, payload: dict) -> tuple[bytes, dict[str, str]]:
        raw = json.dumps(payload).encode()
        digest = hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        return raw, {"X-Signature": f"sha256={digest}", "Content-Type": "application/json"}

    def set_reply_secret_and_token(self, action: str, now: datetime | None = None) -> tuple[str, object]:
        original = self.app_module.settings
        self.app_module.settings = replace(original, reply_token_secret="email-link-test-secret")
        token, _ = issue_reply_token(
            self.app_module.store,
            self.app_module.settings,
            action,
            self.app_module.household_today(),
            "family@example.test",
            now,
        )
        return token, original

    def test_dashboard_endpoint_works_from_a_worker_thread(self) -> None:
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("state", response.json())

    def test_webhook_rejects_bad_signature_and_accepts_good_one_once(self) -> None:
        raw, headers = self.signed(webhook("w1", at(THURSDAY, 8)))
        self.assertEqual(
            self.client.post(
                "/webhooks/ring", content=raw, headers={"X-Signature": "sha256=bad"}
            ).status_code,
            401,
        )
        self.assertEqual(self.client.post("/webhooks/ring", content=raw).status_code, 401)
        first = self.client.post("/webhooks/ring", content=raw, headers=headers)
        second = self.client.post("/webhooks/ring", content=raw, headers=headers)
        self.assertEqual(first.json()["status"], "accepted")
        self.assertEqual(second.json()["status"], "duplicate")

    def test_reply_endpoints_validate_input(self) -> None:
        today = self.app_module.household_today()
        self.assertEqual(self.client.post("/api/reply/away", json={"until": "not-a-date"}).status_code, 422)
        self.assertEqual(
            self.client.post(
                "/api/reply/away", json={"until": (today - timedelta(days=1)).isoformat()}
            ).status_code,
            422,
        )
        ok = self.client.post("/api/reply/away", json={"until": today.isoformat()})
        self.assertEqual(ok.status_code, 200)

    def test_email_link_get_is_inert_and_post_is_one_use(self) -> None:
        token, original = self.set_reply_secret_and_token("fine")
        try:
            opened = self.client.get(f"/reply/{token}")
            self.assertEqual(opened.status_code, 200)
            self.assertIn("Confirm she’s fine", opened.text)
            self.assertIsNone(
                self.app_module.store.latest_reply(self.app_module.household_today().isoformat())
            )

            confirmed = self.client.post(f"/reply/{token}")
            self.assertEqual(confirmed.status_code, 200)
            self.assertIsNotNone(
                self.app_module.store.latest_reply(self.app_module.household_today().isoformat())
            )
            self.assertEqual(self.client.post(f"/reply/{token}").status_code, 410)
        finally:
            self.app_module.settings = original

    def test_email_link_rejects_forged_and_expired_tokens(self) -> None:
        token, original = self.set_reply_secret_and_token("fine")
        try:
            forged = token[:-1] + ("A" if token[-1] != "A" else "B")
            self.assertEqual(self.client.get(f"/reply/{forged}").status_code, 400)
            expired, _ = issue_reply_token(
                self.app_module.store,
                self.app_module.settings,
                "fine",
                self.app_module.household_today(),
                "family@example.test",
                datetime.now(timezone.utc) - timedelta(hours=25),
            )
            self.assertEqual(self.client.get(f"/reply/{expired}").status_code, 410)
        finally:
            self.app_module.settings = original

    def test_away_email_link_requires_explicit_post_and_date(self) -> None:
        token, original = self.set_reply_secret_and_token("away")
        try:
            opened = self.client.get(f"/reply/{token}")
            self.assertEqual(opened.status_code, 200)
            self.assertIn('type="date"', opened.text)
            self.assertIsNone(
                self.app_module.store.active_away_until(self.app_module.household_today().isoformat())
            )
            until = (self.app_module.household_today() + timedelta(days=3)).isoformat()
            confirmed = self.client.post(f"/reply/{token}", data={"until": until})
            self.assertEqual(confirmed.status_code, 200)
            self.assertEqual(
                self.app_module.store.active_away_until(self.app_module.household_today().isoformat()), until
            )
        finally:
            self.app_module.settings = original

    def test_fine_link_clicked_after_midnight_answers_the_alert_day(self) -> None:
        original = self.app_module.settings
        self.app_module.settings = replace(original, reply_token_secret="email-link-test-secret")
        alert_day = self.app_module.household_today() - timedelta(days=1)
        try:
            token, _ = issue_reply_token(
                self.app_module.store,
                self.app_module.settings,
                "fine",
                alert_day,
                "family@example.test",
            )
            self.assertEqual(self.client.post(f"/reply/{token}").status_code, 200)
            self.assertIsNotNone(self.app_module.store.latest_reply(alert_day.isoformat()))
        finally:
            self.app_module.settings = original

    def test_other_family_members_are_told_who_checked_in(self) -> None:
        original = self.app_module.settings
        self.app_module.settings = replace(original, reply_token_secret="email-link-test-secret")
        self.app_module.store.save_family(
            [
                {"name": "Ana", "email": "ana@example.test", "language": "en"},
                {"name": "Bob", "email": "bob@example.test", "language": "fr"},
            ]
        )
        sent: list[tuple[str, str]] = []
        try:
            day = self.app_module.household_today() + timedelta(days=5)  # a day no other test touches
            token, _ = issue_reply_token(
                self.app_module.store, self.app_module.settings, "fine", day, "ana@example.test"
            )
            with patch(
                "rhythm.app.send_smtp_email",
                side_effect=lambda s, subj, body: sent.append((s.alert_to, body)),
            ):
                self.assertEqual(self.client.post(f"/reply/{token}").status_code, 200)
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][0], "bob@example.test")
            self.assertIn("Ana", sent[0][1])
        finally:
            self.app_module.store.save_family([])
            self.app_module.settings = original

    def test_admin_token_is_enforced_when_set(self) -> None:
        original = self.app_module.settings
        self.app_module.settings = replace(original, admin_token="s3cret")
        try:
            self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
            self.assertEqual(
                self.client.get("/api/dashboard", headers={"X-Admin-Token": "s3cret"}).status_code, 200
            )
            self.assertEqual(self.client.post("/api/reply/fine").status_code, 401)
        finally:
            self.app_module.settings = original

    def test_setup_requires_her_consent(self) -> None:
        response = self.client.post(
            "/setup",
            data={
                "household": "Home",
                "timezone": "Europe/Rome",
                "members": "",
                "language": "en",
                "sensitivity": "Standard",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("consent", response.text.lower())

    def test_setup_saves_profile_and_family_recipients(self) -> None:
        self.assertEqual(self.client.get("/setup").status_code, 200)
        response = self.client.post(
            "/setup",
            data={
                "household": "Demo Home",
                "timezone": "Europe/Rome",
                "members": "Marco,marco@example.test\nAnna,anna@example.test",
                "language": "it",
                "sensitivity": "Careful",
                "consent": "yes",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.app_module.store.get_state("profile_household"), "Demo Home")
        self.assertEqual(self.app_module.store.get_state("profile_sensitivity"), "Careful")
        self.assertEqual(
            [m["email"] for m in self.app_module.store.family()], ["anna@example.test", "marco@example.test"]
        )
        self.app_module.store.delete_everything()

    def test_dashboard_api_is_disabled_in_webhook_mode_without_a_token(self) -> None:
        original = self.app_module.settings
        self.app_module.settings = replace(original, ring_ingestion_mode="webhook", admin_token="")
        try:
            self.assertEqual(self.client.get("/api/dashboard").status_code, 403)
            self.assertEqual(self.client.get("/health").status_code, 200)
        finally:
            self.app_module.settings = original

        self.app_module.settings = replace(
            original,
            ring_ingestion_mode="poll",
            admin_token="",
            public_base_url="https://rhythm-demo.trycloudflare.com",
        )
        try:
            self.assertEqual(self.client.get("/api/dashboard").status_code, 403)
            self.assertEqual(self.client.get("/health").status_code, 200)
        finally:
            self.app_module.settings = original


if __name__ == "__main__":
    unittest.main()
