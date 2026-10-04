#!/usr/bin/env python3
"""Print the device IDs your Ring token can see (metadata only). Needs RING_ACCESS_TOKEN in .env."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rhythm.config import Settings


def main() -> None:
    settings = Settings.from_env()
    if not settings.ring_access_token:
        raise SystemExit("Set RING_ACCESS_TOKEN in .env first (Playground tokens last about 30 minutes).")
    response = httpx.get(
        f"{settings.ring_api_base_url}/v1/devices",
        headers={"Authorization": f"Bearer {settings.ring_access_token}", "Accept": "application/json"},
        timeout=20,
    )
    if response.status_code == 401:
        raise SystemExit("Ring rejected the token (401). Generate a fresh one in the Playground.")
    response.raise_for_status()
    devices = response.json().get("data", [])
    print(f"Found {len(devices)} device(s). Copy the ID into RING_DEVICE_ID in .env:")
    for device in devices:
        print(f"  {device['attributes'].get('name', 'unnamed')}: {device['id']}")


if __name__ == "__main__":
    main()
