# Checks run on this version

Commands were run on Windows with Python 3.13 on 2026-10-04. All data is synthetic; no real Ring device, Ring webhook or SMTP server was involved. The Ring client checks use in-process mocked HTTP responses.

| Check | Command | Result |
|---|---|---|
| Automated suite (22 tests) | `python -m unittest discover -s tests -v` | OK |
| Pure-logic scenario (21 days, 2 parts) | `python outputs/synthetic_alert_test.py` | PASS 1 and PASS 2 |
| Ingestion to rule | `python scripts/e2e_simulated_day.py` | `care_alert` at 10:35 vs 10:00 cutoff |
| Email send path and same-day suppression (fake sender) | `python scripts/test_email_delivery.py --dry-run` | first attempt passed, second suppressed |
| Demo replay + live server | `python scripts/replay_demo.py --reset-demo`, `uvicorn rhythm.app:app`, `GET /api/dashboard` | HTTP 200, 22 chart points, 1 stored alert with reason |

## What the automated suite covers

Silent morning (pending before the cutoff, `care_alert` after it), normal and late first activity, learning period, fixed fallback with few samples, "fine" beating a later "away" reply, away pause and resume, offline device, one alert per day, SMTP failure releasing the reservation, offline notice sent once and not at night, alerted mornings excluded from the learned baseline, UTC milliseconds to local time across the October daylight-saving change, history ingestion (motion and ding stored, `on_demand` ignored), duplicate events, webhook signature checks and de-duplication, dashboard endpoints (including from worker threads), and the admin-token rules.

## Not verified

- Live Ring webhook delivery, offline transitions, and real `motion`/`ding` history from a physical device.
- Anything about real-household threshold quality.
- A fresh `git clone` walkthrough on Windows (do this before submitting).
