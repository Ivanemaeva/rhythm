# Rhythm

**A quiet morning check-in for a relative who lives alone. One gentle email, with the reason, only when the morning is later than usual.**

> Rhythm is a check-in aid, not a safety or medical device.

Built for the Amazon "Build, Ship, Shape" hackathon (Ring track).

## The problem

Doorbell and motion apps notify about everything, so people mute them. Rhythm does the opposite: it learns when the first activity of the day normally happens at a home, keeps weekdays and weekends separate, stays silent while it learns, and then sends **at most one email per day**, only when nothing has been seen by that household's usual cutoff. The email says why it was sent, so family can decide whether to call.

## Privacy by design

- Rhythm reads **event metadata only**: motion and doorbell-press times, plus device online status.
- The code never requests or processes video, images, or Ring Media endpoints. (A Ring app scope may technically bundle media permissions, so this guarantee comes from the code in `rhythm/ring_api.py`, which only calls the device status and Event History endpoints.)
- Data stays in a local SQLite file. Nothing is sent anywhere except the one alert email.
- Alert emails state the main limitation: a doorbell sees the front door, not the inside of the home, so a quiet morning can be normal.

## How it works

1. **Ingest.** `scripts/poll_ring.py` calls the Ring API (device status and Event History). `POST /webhooks/ring` also accepts signed Ring webhooks (`motion_detected`, `button_press`, `device_online`, `device_offline`). Timestamps arrive as UTC epoch milliseconds and are converted to the household timezone.
2. **Learn.** Each day's first activity is stored. The first 14 days are a silent learning period.
3. **Decide.** For the day's bucket (weekday or weekend): cutoff = 95th percentile of past first-activity times + 15 min, never earlier than a minimum-wait floor (10:00 weekdays, 11:00 weekends). With fewer than 5 samples in a bucket, a fixed conservative cutoff is used.
4. **Notify.** `scripts/check_morning.py` runs the decision on a schedule. If no activity has been seen by the cutoff (or the first activity arrives after it), it sends one care email that includes the reason. A separate message says "we can't see the device" when Ring reports it offline.
5. **Reply.** The dashboard has "She's fine" (suppresses today's alert) and "She's away until a date" (pauses alerts). One care alert per local day, enforced in SQLite.

## Real vs. simulated (please read)

| Part | Status |
|---|---|
| Ring API calls (`/v1/devices/{id}/status`, Event History) | **Real**, tested against the Ring Developer Playground with a short-lived token |
| Device | The Playground's **fake Doorbell Pro**. No physical Ring device was used |
| Past activity (weeks of mornings) | **Simulated.** Event History only returns events created after the account exists, and the Playground only produced `on_demand` live-view events, which Rhythm deliberately ignores (a live-view session is not motion or a doorbell press) |
| Webhook handler | Written from the documented payload and signature format, covered by tests with synthetic payloads. **Not verified with live Ring webhook delivery** |
| Offline transition | Handled in code and tests; **not observed live** (the Playground device stays online) |
| Email | **Real.** A labeled `[SIMULATED DEMO]` alert was delivered to a phone through Gmail SMTP (event times were synthetic) |

The demo replay clearly labels its data as synthetic.

## Quick start (Windows PowerShell; on macOS/Linux use `cp` and `export`)

Requires Python 3.10+.

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env        # then edit .env
python -m unittest discover -s tests -v
```

### 1. Run the demo (no Ring account or email needed)

```powershell
$env:DATABASE_PATH = "data/rhythm-demo.sqlite3"
python scripts/replay_demo.py --reset-demo
uvicorn rhythm.app:app
```

Open http://127.0.0.1:8000/. You will see three weeks of routine, then a silent morning: Rhythm records one care alert with its reason, and a second attempt the same day is suppressed.

### 2. Call the real Ring API (Developer Playground)

1. Open the Ring Developer Playground and click **Generate Token** (valid about 30 minutes).
2. Put the token in `.env` as `RING_ACCESS_TOKEN`, then run `python scripts/list_devices.py` and copy the device ID it prints into `RING_DEVICE_ID`.
3. Run `python scripts/poll_ring.py --once`. Expect `{'online': True, ..., 'history_available': True}`.

### 3. Send a real test email

Fill `SMTP_*` and `ALERT_TO` in `.env` (for Gmail, use an app password), then:

```powershell
python scripts/replay_demo.py --reset-demo --send-email
```

The email subject starts with `[SIMULATED DEMO]`. To preview the scheduled job without sending: `python scripts/check_morning.py --once --dry-run`.

## Project layout

```
rhythm/            app.py (FastAPI + dashboard API), rules.py (decision), ring_api.py, ingestion.py,
                   storage.py (SQLite), email_delivery.py, config.py, dashboard.html
scripts/           list_devices.py, poll_ring.py, check_morning.py (scheduled job), replay_demo.py, e2e_simulated_day.py,
                   test_email_delivery.py
tests/             automated unittest suite (synthetic data, no network)
outputs/           synthetic_alert_test.py (pure-logic scenario)
docs/              demo plan, Devpost text, product feedback, friction log, checklist
```

## Security notes

- Never commit `.env`, tokens or `data/` (all in `.gitignore`).
- The dashboard API and reply buttons are for local use. If you expose the app to the internet for webhooks, set `RHYTHM_ADMIN_TOKEN`; without it those endpoints are disabled in webhook mode. Only `/webhooks/ring` and `/health` need to be public.
- Webhook requests are verified with an HMAC-SHA256 signature over the raw body and de-duplicated by request ID.

## Limitations

- Thresholds are demo defaults and have **not** been calibrated with real household data.
- A doorbell sees the front door, not the inside of the home, so a quiet morning is not evidence that anything is wrong. Rhythm asks family to check in; it never claims to know what happened.
- Reply buttons live on the local dashboard. A production version would put signed reply links in the email.
- Not verified: live Ring webhook delivery, offline transitions, real motion or doorbell event types from a physical device, real-world threshold quality.
- No medical or safety claims are made.

## License

MIT. See `LICENSE`.
