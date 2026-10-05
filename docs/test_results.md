# Test results

Checked on 2026-10-04 with Python 3.13. The automated tests use synthetic data; Ring HTTP and SMTP are mocked.

| Check | Command | Result |
|---|---|---|
| Automated suite (53 tests) | `python -m unittest discover -s tests -v` | OK |
| Pure-logic scenario (21 days, 2 parts) | `python outputs/synthetic_alert_test.py` | PASS 1 and PASS 2 |
| Ingestion to rule | `python scripts/e2e_simulated_day.py` | `care_alert` at 10:35 vs 10:00 cutoff |
| Email send path and same-day suppression (fake sender) | `python scripts/test_email_delivery.py --dry-run` | first attempt passed, second suppressed |
| Morning check preview (Italian, Careful) | `python scripts/check_morning.py --once --dry-run --no-sync` | localized alert printed, cutoff 09:30 |
| Evaluation (60 simulated households, 16 weeks) | `python scripts/evaluate.py` | 0 false alerts/month, 60/60 silent days and 94/140 late mornings caught (Standard) |
| Offline demo scenario | `python scripts/replay_demo.py --reset-demo --scenario offline` | `device_offline`, no care alert |
| One-command launcher | `python -m rhythm` | dashboard on 127.0.0.1:8000 and worker running |
| Demo replay + dashboard + setup | `replay_demo.py`, `GET /api/dashboard`, `POST /setup` | HTTP 200, learned window and cutoff shown, profile saved |
| Real Ring API (manual, earlier) | `python scripts/poll_ring.py --once` against the Playground | `online: True`, `history_available: True` |
| Real email (manual, earlier) | `python scripts/replay_demo.py --reset-demo --send-email` | labeled demo alert delivered to a phone |

## What the automated suite covers

Silent and late mornings, the morning window (night events ignored), doorbell presses not counted as her activity, the 10-minute grace period, lost Ring connection reported instead of a care alert, consent required on Setup, 8-week retention, away periods starting in the future, verbose Ring logging that never prints the token, the richer alert email, the simulation (every silent day caught, no alerts during trips), learning period, fixed fallback, rolling 8-week baseline excluding alert and away days, sensitivity settings, timezone changes, offline devices, one alert per day per family member, SMTP failures, signed reply links (forged, expired, reused, scanner-safe GET, after-midnight replies), the activity follow-up, the weekly summary, family reply notes, Italian and French emails, multi-device Ring sync with mocked HTTP, webhook signatures and de-duplication, dashboard and setup endpoints, admin-token rules, export and delete-everything.

## Bugs found in review and fixed (each has a regression test that failed before the fix)

- The activity follow-up never fired after a silent-morning alert, because later activity kept the day in the `care_alert` branch.
- With several family members, one failing address made Rhythm re-send the alert to the others on every check (every 2 minutes). Each member is now tracked separately.
- A failed weekly summary was marked as sent and never retried, and the failure stopped the rest of the check.
- The dashboard's "usual window" mixed weekdays, weekends and the alert day itself; it now shows the learned baseline for today's bucket.
- Changing the timezone on the Setup page left stored events in the old timezone; local times are now computed from UTC.
- Found by the evaluation: an away period also paused alerts on the days before it started. Fixed and tested.
- Sensitivity had no visible effect with the default minimum waits; Relaxed and Careful now also move the minimum wait by 30 minutes.

## Not verified

- A reply-link click from a phone through a public HTTPS tunnel.
- Live Ring webhook delivery, offline transitions, real `motion`/`ding` events from a physical device, several real devices on one account, and a European Ring Indoor Cam.
- Real-household threshold quality.
