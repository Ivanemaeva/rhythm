# Rhythm

**A quiet morning check-in for a relative who lives alone. One gentle email, with the reason, only when the morning is later than usual.**

> Rhythm is a check-in aid, not a safety or medical device.

Built for the Amazon "Build, Ship, Shape" hackathon (Ring track, caretaking).

## The problem

Doorbell and motion apps notify about everything, so families mute them, and the one morning that matters gets lost. Rhythm does the opposite. It learns when the first activity of the day normally happens at one home, keeps weekdays and weekends separate, stays silent while it learns, and then speaks up **at most once a day**, only when nothing has been recorded by that household's usual cutoff. Every email explains why it was sent, and the family can answer with one tap.

## What the family experiences

1. **Setup.** On the Setup page: household name, timezone, up to three family emails, email language (English, Italian, French) and sensitivity (Relaxed, Standard, Careful).
2. **Learning.** For the first 14 days Rhythm only watches. It sends nothing.
3. **A normal morning.** Activity is recorded before the cutoff. Rhythm stays silent.
4. **A later-than-usual morning.** Nothing is recorded by the cutoff. Every family member gets one email with the reason, a reminder that a quiet morning can be normal, and two buttons: **She's fine** and **She's away until…**
5. **Someone answers.** One tap confirms. The other family members are told who checked in, so not everyone calls at once.
6. **Activity is recorded later.** One short follow-up: "Activity was recorded at 11:35 (front door). This may be her or a visitor." Rhythm never claims she was seen.
7. **The device is offline.** A different message: "We can't see the device." This is not a care alert.
8. **Sunday evening.** A short, calm weekly summary.

## Privacy by design

- Rhythm reads **event metadata only**: motion and doorbell-press times, plus device online status.
- The code never requests or processes video, images, or Ring Media endpoints. (A Ring app scope may bundle media permissions, so this guarantee comes from the code: `rhythm/ring_api.py` only calls device listing, device status and Event History.)
- Stored events keep only IDs, device ID, event type and timestamps.
- Data stays in a local SQLite file. Event data older than 90 days is deleted automatically, and the dashboard has **Export my data** and **Delete everything**.
- Activity cannot identify a person: it may be a visitor. The emails say so.

## How the rule works

- **Bucket:** today is a weekday or a weekend day; each has its own routine.
- **Baseline:** the first activity of each day in the last 8 weeks (`BASELINE_DAYS=56`), leaving out days that triggered an alert and days marked away. The 14-day learning period happens once, at the start, and never restarts.
- **Cutoff:** the 95th percentile of the baseline + a margin, never earlier than a minimum wait (10:00 weekdays, 11:00 weekends). With fewer than 5 mornings in a bucket, a conservative fixed cutoff is used.
- **Sensitivity:** Relaxed waits 30 minutes longer (margin 30 min), Careful speaks up 30 minutes earlier (margin 5 min), Standard uses the `.env` values. These are demo choices, not calibrated values.
- **Decision:** if nothing is recorded by the cutoff, or the first activity comes after it, Rhythm sends one care alert.

The dashboard shows all of this: the learned usual window, the cutoff line on the chart, and today's live status ("Waiting: nothing recorded yet; Rhythm will speak up after 10:00"). It has light, dark and high-contrast display modes.

## Real vs. simulated (please read)

| Part | Status |
|---|---|
| Ring API calls (device list, status, Event History) | **Real**, tested against the Ring Developer Playground with a short-lived token |
| Device | The Playground's **fake Doorbell Pro**. No physical Ring device was used |
| Several devices per home | Implemented and tested with mocked API responses; the Playground account had one device |
| Past activity (weeks of mornings) | **Simulated.** Event History only returns events created after access, and the Playground only produced `on_demand` live-view events, which Rhythm deliberately ignores (a live-view session is not motion or a doorbell press) |
| Webhook handler | Written from the documented payload and signature format, tested with synthetic payloads. **Not verified with live Ring webhook delivery** |
| Offline transition | Handled in code and tests; **not observed live** (the Playground device stays online) |
| Email | **Real.** A labeled `[SIMULATED DEMO]` alert was delivered to a phone through Gmail SMTP (event times were synthetic) |
| Email reply links | Signed, one-use, 24-hour links with a confirmation page; tested locally. **A phone click through a public HTTPS tunnel is not yet verified** |

The demo replay labels its data as synthetic.

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
python -m rhythm
```

Open http://127.0.0.1:8000/. You will see three weeks of routine, then a silent morning: Rhythm records one care alert with its reason, and a second attempt the same day is suppressed. Click **Setup** to configure the household.

`python -m rhythm` starts the dashboard and the background worker together. The worker syncs Ring, runs the morning check and sends emails every `RING_POLL_SECONDS`.

### 2. Call the real Ring API (Developer Playground)

1. Open the Ring Developer Playground and click **Generate Token** (valid about 30 minutes).
2. Put the token in `.env` as `RING_ACCESS_TOKEN`, then run `python scripts/list_devices.py`. `RING_DEVICE_ID` can stay empty (all devices are discovered) or be set as a fallback.
3. Run `python scripts/poll_ring.py --once`. Expect `'online': True` and `'history_available': True`.

### 3. Send real emails

Fill `SMTP_*` and `ALERT_TO` in `.env` (for Gmail, use an app password), or add family members on the Setup page. Set `REPLY_TOKEN_SECRET` to a private random value to include the reply buttons:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
python scripts/replay_demo.py --reset-demo --send-email
```

The email subject starts with `[SIMULATED DEMO]`. To preview the morning check without sending: `python scripts/check_morning.py --once --dry-run`. Without `REPLY_TOKEN_SECRET` the alert is still sent, just without buttons.

Reply links point to `PUBLIC_BASE_URL` (default `http://127.0.0.1:8000`, which works when clicked on the same laptop). To click them on a phone during a demo, set a private `RHYTHM_ADMIN_TOKEN`, start a temporary HTTPS tunnel (for example `cloudflared tunnel --url http://127.0.0.1:8000`), set `PUBLIC_BASE_URL` to the HTTPS address it prints, and restart. Never tunnel the app without an admin token.

## Project layout

```
rhythm/      __main__.py (one-command launcher), app.py (FastAPI: dashboard, setup, replies, webhooks),
             morning.py (the morning check), rules.py (decision and baseline), profile.py (setup + sensitivity),
             email_delivery.py (alerts, follow-ups, weekly summary, translations), reply_links.py (signed links),
             ring_api.py, ingestion.py, storage.py (SQLite), config.py, dashboard.html
scripts/     list_devices.py, poll_ring.py, check_morning.py, replay_demo.py, e2e_simulated_day.py,
             test_email_delivery.py
tests/       automated unittest suite (synthetic data; network and email are mocked)
outputs/     synthetic_alert_test.py (pure-logic scenario)
docs/        demo plan, Devpost text, product feedback, friction log, hardware checklist, test results
```

## Security notes

- Never commit `.env`, tokens or `data/` (all in `.gitignore`).
- The app listens on 127.0.0.1 only. When `RHYTHM_ADMIN_TOKEN` is set, the dashboard API, Setup, export and delete require it (open the dashboard as `/?token=...`). Without it, those routes are disabled in webhook mode or when `PUBLIC_BASE_URL` is not local.
- Reply links are signed with `REPLY_TOKEN_SECRET`, expire after 24 hours and work once. Opening a link never records anything; only the confirmation button does, so email scanners can't trigger it.
- Ring webhooks are verified with an HMAC-SHA256 signature over the raw body and de-duplicated by request ID.
- Each family member is tracked separately: a broken address never causes repeat emails to the others.

## Limitations

- Thresholds are demo defaults and have **not** been calibrated with real household data. False alerts and missed changes are possible.
- A doorbell sees the front door, not the inside of the home, so a quiet morning is not evidence that anything is wrong. Rhythm asks family to check in; it never claims to know what happened.
- Automatic deletion and emails only run while `python -m rhythm` (or `scripts/check_morning.py`) is running.
- One email language per household; the safety disclaimer stays in English.
- Not verified: live Ring webhook delivery, offline transitions, real motion or doorbell events from a physical device, a European Ring Indoor Cam (see `docs/hardware_validation.md`), real-world threshold quality.
- No medical or safety claims are made.

## License

MIT. See `LICENSE`.
