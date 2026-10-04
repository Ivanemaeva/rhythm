"""Run Rhythm with one command: `python -m rhythm`.

Starts the local dashboard (http://127.0.0.1:8000) and a background worker that syncs Ring,
runs the morning check and sends emails every RING_POLL_SECONDS.
"""

from __future__ import annotations

import threading
import time

import uvicorn

from .config import Settings
from .morning import run_once
from .storage import Store


def worker() -> None:
    settings = Settings.from_env()
    store = Store(settings.database_path)
    while True:
        try:
            device_id = settings.ring_device_id or store.first_known_device_id()
            if device_id or settings.ring_access_token:
                print(run_once(settings, store, device_id, sync=True), flush=True)
        except Exception as exc:  # keep the worker alive whatever happens
            print(f"Rhythm worker error: {exc}", flush=True)
        time.sleep(max(30, settings.ring_poll_seconds))


def main() -> None:
    threading.Thread(target=worker, name="rhythm-worker", daemon=True).start()
    uvicorn.run("rhythm.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
