#!/usr/bin/env python3
"""Call the Ring API for device status and Event History and store the metadata.

Run once:      python scripts/poll_ring.py --once
Run forever:   python scripts/poll_ring.py            (every RING_POLL_SECONDS)

Needs RING_ACCESS_TOKEN and RING_DEVICE_ID in .env (a Playground token lasts about 30 minutes).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rhythm.config import Settings
from rhythm.profile import effective_settings
from rhythm.ring_api import RingApi
from rhythm.storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--once", action="store_true", help="sync once and exit")
    parser.add_argument("--verbose", action="store_true", help="print each Ring request and Ring's answer")
    args = parser.parse_args()

    settings = Settings.from_env()
    if settings.ring_ingestion_mode != "poll" and not args.once:
        raise SystemExit("Set RING_INGESTION_MODE=poll to use the polling loop (or pass --once).")
    store = Store(settings.database_path)
    if not args.once:
        print(f"Polling Ring status and metadata every {settings.ring_poll_seconds}s; Ctrl+C to stop.")
    try:
        while True:
            try:
                print(RingApi(effective_settings(settings, store), store, verbose=args.verbose).sync())
            except Exception as exc:
                print(f"Ring sync failed: {exc}", file=sys.stderr)
                if args.once:
                    raise SystemExit(1)
            if args.once:
                break
            time.sleep(settings.ring_poll_seconds)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
