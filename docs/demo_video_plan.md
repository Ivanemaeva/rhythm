# Demo video plan (target 2:40, hard limit under 3:00)

Keep a small on-screen label "Simulated Ring metadata" during replay and dashboard shots. Read cutoff times from the screen instead of memorising them: they depend on whether the replay date is a weekday (10:00 cutoff) or a weekend (11:00).

| Time | Shot and narration |
|---|---|
| 0:00-0:15 | Person at home looking at a phone. "A relative of mine lives alone. I want to know when her usual morning changes, without an alert for every little thing." |
| 0:15-0:35 | Terminal: `python scripts/poll_ring.py --once` printing `online: True`. "Rhythm calls the real Ring API. Here it is reading device status from the Ring Developer Playground. Metadata only: no video, no images." |
| 0:35-0:55 | Terminal: replay advancing day by day. "With no real device, I replay three simulated weeks. Rhythm learns the first-activity time and keeps weekdays and weekends separate." |
| 0:55-1:15 | Dashboard chart. "Three quiet weeks of a familiar routine, with a little variation." |
| 1:15-1:40 | Replay reaches the last day: pending, then `care_alert`. "Today nothing has been seen by the usual cutoff. Rhythm speaks up once, and a second attempt is suppressed." |
| 1:40-2:00 | Phone showing the `[SIMULATED DEMO]` email. "The email says why it was sent, and that a quiet morning can be normal." |
| 2:00-2:25 | Dashboard: alert with its saved reason; click "She's fine"; show suppression text. "Family can say she's fine, or away until a date." |
| 2:25-2:40 | Calm phone call; end on the disclaimer. "Rhythm gives family a reason to check in. It doesn't decide what happened. It's a check-in aid, not a safety device." |

## Rehearsal commands

```powershell
pip install -r requirements.txt
$env:DATABASE_PATH = "data/rhythm-video-demo.sqlite3"
python scripts/replay_demo.py --delay-seconds 0.15 --reset-demo --send-email
python scripts/poll_ring.py --once        # run AFTER the replay: it stores the real device status
python -m rhythm
```

Open http://127.0.0.1:8000/. Configure `SMTP_*` and `ALERT_TO` first so the email shot is real; otherwise drop `--send-email` and say the alert is recorded without sending. Do not claim live Ring motion events. Upload as public YouTube or Vimeo and test the link while signed out.
