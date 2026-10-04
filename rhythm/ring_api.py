from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from .config import Settings
from .ingestion import ingest_history_item
from .storage import Store


class RingApi:
    """Metadata-only Ring client: device status and Event History. It never calls Media endpoints."""

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def _get(self, client: httpx.Client, path_or_url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if path_or_url.startswith("https://"):
            url = path_or_url
        else:
            url = urljoin(self.settings.ring_api_base_url + "/", path_or_url.lstrip("/"))
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def sync(self) -> dict[str, Any]:
        """Fetch device status and Event History once and store the metadata."""
        if not self.settings.ring_access_token:
            raise RuntimeError("Set RING_ACCESS_TOKEN before connecting to Ring.")
        if not self.settings.ring_device_id:
            raise RuntimeError("Set RING_DEVICE_ID before syncing Ring.")
        device_id = self.settings.ring_device_id
        headers = {"Authorization": f"Bearer {self.settings.ring_access_token}", "Accept": "application/json"}
        try:
            with httpx.Client(headers=headers, timeout=20) as client:
                status_body = self._get(client, f"/v1/devices/{device_id}/status")
                data = status_body.get("data", status_body)
                attrs = data.get("attributes", data)
                online = attrs.get("online")
                reported_at = attrs.get("reported_at") or status_body.get("meta", {}).get("time")
                self.store.set_device_status(device_id, online, reported_at, json.dumps(status_body, sort_keys=True))
                return self._sync_history(client, device_id, online)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise RuntimeError(
                    "Ring rejected the token (401). Playground tokens expire after about 30 minutes; generate a new one."
                ) from exc
            raise

    def _sync_history(self, client: httpx.Client, device_id: str, online: bool | None) -> dict[str, Any]:
        url: str | None = f"/v1/history/devices/{device_id}/events"
        params: dict[str, str] | None = {"event_types": "motion,ding"}
        count = 0
        pages = 0
        while url:
            try:
                body = self._get(client, url, params=params)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == 400 and params is not None and pages == 0:
                    params = None  # the filter was not accepted; ingestion filters event types itself
                    continue
                if code in {403, 404} and pages == 0:
                    # History permission can be unavailable independently of device status.
                    self._start_learning_today()
                    return {"online": online, "events_added": 0, "history_pages": 0, "history_available": False}
                raise
            pages += 1
            for item in body.get("data", []):
                count += int(ingest_history_item(item, self.store, self.settings.household_timezone, device_id))
            url = body.get("links", {}).get("next")
            params = None  # links.next carries its own cursor and filters
        earliest = self.store.earliest_event_day(device_id)
        self.store.set_state_once("learning_started_local", earliest or self._today_text())
        return {"online": online, "events_added": count, "history_pages": pages, "history_available": True}

    def _today_text(self) -> str:
        return datetime.now(self.settings.household_timezone).date().isoformat()

    def _start_learning_today(self) -> None:
        self.store.set_state_once("learning_started_local", self._today_text())
