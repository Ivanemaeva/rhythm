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
from rhythm.email_delivery import deliver_care_alert, deliver_offline_notice  # noqa: E402
from rhythm.ingestion import ingest_history_item, ingest_webhook, timestamp_ms_to_local  # noqa: E402
from rhythm.ring_api import RingApi  # noqa: E402
from rhythm.rules import decide_for_day  # noqa: E402
from rhythm.storage import Store  # noqa: E402
from scripts.check_morning import run_once  # noqa: E402

TZ = ZoneInfo("Europe/Rome")
DEVICE = "device-1"
THURSDAY = date(2026, 10, 1)
SUNDAY = date(2026, 10, 4)


def make_settings(path: Path) -> Settings:
    return replace(Settings.from_env(), database_path=path, ring_device_id=DEVICE, ring_access_token="")


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
        after = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 10, 1))
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
        self.store.claim_alert_day(late_day.isoformat(), "care_alert", "late", datetime.now(timezone.utc).isoformat())
        self.store.mark_alert_sent(late_day.isoformat())
        after = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 11))
        self.assertEqual(after["samples"], 9)

    def test_learning_period_is_silent(self) -> None:
        self.store.set_state("learning_started_local", (THURSDAY - timedelta(days=3)).isoformat())
        result = decide_for_day(self.store, self.settings, DEVICE, THURSDAY, at(THURSDAY, 15))
        self.assertEqual(result["decision"], "learning")

    def test_few_weekend_samples_use_fixed_fallback(self) -> None:
        self.seed_history(SUNDAY)  # 14 days contain only 4 weekend days
        result = decide_for_day(self.store, self.settings, DEVICE, SUNDAY, at(SUNDAY, 11, 1))
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
        self.store.record_reply(THURSDAY.isoformat(), "away", datetime.now(timezone.utc).isoformat(), "2026-10-02")
        for day in (THURSDAY, THURSDAY + timedelta(days=1)):
            result = decide_for_day(self.store, self.settings, DEVICE, day, at(day, 11))
            self.assertEqual(result["decision"], "paused_away")
        resumed_day = THURSDAY + timedelta(days=2)  # Saturday, away ended on Friday
        self.assertEqual(self.store.active_away_until(resumed_day.isoformat()), None)

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

        with self.assertRaises(OSError):
            deliver_care_alert(self.store, self.settings, THURSDAY, result, failing)
        self.assertIsNone(self.store.alert_status(THURSDAY.isoformat()))  # released, so a retry can send

        first = deliver_care_alert(self.store, self.settings, THURSDAY, result, lambda s, subj, body: sent.append((subj, body)))
        second = deliver_care_alert(self.store, self.settings, THURSDAY, result, lambda s, subj, body: sent.append((subj, body)))
        self.assertEqual((first["delivery"], second["delivery"]), ("sent", "suppressed"))
        self.assertEqual(len(sent), 1)
        body = sent[0][1]
        self.assertIn("Why Rhythm spoke up:", body)
        self.assertIn("not a safety or medical device", body)
        self.assertIn("front door", body)

    def test_run_once_sends_silent_morning_alert_once(self) -> None:
        self.seed_history(THURSDAY)
        outbox: list[str] = []
        sender = lambda s, subject, body: outbox.append(subject)  # noqa: E731
        early = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 9, 0), sync=False, sender=sender)
        late = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 10, 5), sync=False, sender=sender)
        later = run_once(self.settings, self.store, DEVICE, at(THURSDAY, 10, 35), sync=False, sender=sender)
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
        self.assertEqual(deliver_offline_notice(self.store, self.settings, THURSDAY, result, sender)["delivery"], "suppressed")


class IngestionTests(RhythmCase):
    def test_timestamps_are_utc_milliseconds_and_follow_daylight_saving(self) -> None:
        summer = int(datetime(2026, 10, 24, 6, 30, tzinfo=timezone.utc).timestamp() * 1000)
        winter = int(datetime(2026, 10, 26, 6, 30, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(timestamp_ms_to_local(summer, TZ)[1].strftime("%H:%M %Z"), "08:30 CEST")
        self.assertEqual(timestamp_ms_to_local(winter, TZ)[1].strftime("%H:%M %Z"), "07:30 CET")

    def test_history_ingests_motion_and_ding_but_not_on_demand(self) -> None:
        def item(item_id: str, kind: str) -> dict:
            return {"id": item_id, "attributes": {"event_type": kind, "start": 1791136314302, "end": 1791136354014}}

        results = [ingest_history_item(item(f"e-{kind}", kind), self.store, TZ, DEVICE) for kind in ("motion", "ding", "on_demand")]
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
        history = {"data": [
            {"id": "motion-1", "attributes": {"event_type": "motion", "start": stamp}},
            {"id": "live-view-1", "attributes": {"event_type": "on_demand", "start": stamp}},
            {"id": "ding-1", "attributes": {"event_type": "ding", "start": stamp + 60_000}},
        ], "links": {}}
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.side_effect = [
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
        self.assertEqual(client.get.call_count, 2)

    def test_forbidden_history_starts_learning_without_losing_status(self) -> None:
        status = {"online": True, "reported_at": "2026-10-01T06:15:00Z"}
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.side_effect = [
            httpx.Response(200, json=status, request=httpx.Request("GET", "https://example.test/status")),
            httpx.Response(403, json={}, request=httpx.Request("GET", "https://example.test/events")),
        ]

        with patch("rhythm.ring_api.httpx.Client", return_value=client):
            result = RingApi(replace(self.settings, ring_access_token="test-token"), self.store).sync()

        self.assertFalse(result["history_available"])
        self.assertIs(self.store.device_online(DEVICE), True)
        self.assertEqual(self.store.get_state("learning_started_local"), datetime.now(TZ).date().isoformat())


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

    def test_dashboard_endpoint_works_from_a_worker_thread(self) -> None:
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("state", response.json())

    def test_webhook_rejects_bad_signature_and_accepts_good_one_once(self) -> None:
        raw, headers = self.signed(webhook("w1", at(THURSDAY, 8)))
        self.assertEqual(self.client.post("/webhooks/ring", content=raw, headers={"X-Signature": "sha256=bad"}).status_code, 401)
        self.assertEqual(self.client.post("/webhooks/ring", content=raw).status_code, 401)
        first = self.client.post("/webhooks/ring", content=raw, headers=headers)
        second = self.client.post("/webhooks/ring", content=raw, headers=headers)
        self.assertEqual(first.json()["status"], "accepted")
        self.assertEqual(second.json()["status"], "duplicate")

    def test_reply_endpoints_validate_input(self) -> None:
        today = self.app_module.household_today()
        self.assertEqual(self.client.post("/api/reply/away", json={"until": "not-a-date"}).status_code, 422)
        self.assertEqual(self.client.post("/api/reply/away", json={"until": (today - timedelta(days=1)).isoformat()}).status_code, 422)
        ok = self.client.post("/api/reply/away", json={"until": today.isoformat()})
        self.assertEqual(ok.status_code, 200)

    def test_admin_token_is_enforced_when_set(self) -> None:
        original = self.app_module.settings
        self.app_module.settings = replace(original, admin_token="s3cret")
        try:
            self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
            self.assertEqual(self.client.get("/api/dashboard", headers={"X-Admin-Token": "s3cret"}).status_code, 200)
            self.assertEqual(self.client.post("/api/reply/fine").status_code, 401)
        finally:
            self.app_module.settings = original

    def test_dashboard_api_is_disabled_in_webhook_mode_without_a_token(self) -> None:
        original = self.app_module.settings
        self.app_module.settings = replace(original, ring_ingestion_mode="webhook", admin_token="")
        try:
            self.assertEqual(self.client.get("/api/dashboard").status_code, 403)
            self.assertEqual(self.client.get("/health").status_code, 200)
        finally:
            self.app_module.settings = original


if __name__ == "__main__":
    unittest.main()
