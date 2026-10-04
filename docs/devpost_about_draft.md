# About the project

**Track:** Ring (priority category: caretaking). **Mini challenges:** none.

## Inspiration

Many families have a relative who lives alone. Doorbell and motion apps tell people about everything, so the notifications get muted, and the one morning that matters is lost in the noise. I wanted the opposite: a tool that stays quiet, learns what a normal morning looks like at one home, and speaks up once, with a reason, when it is not.

## What it does

Rhythm reads Ring motion and doorbell **metadata only** (never video or images). It records the first activity of each day, keeps weekdays and weekends separate, and stays silent for a two-week learning period. After that it sends at most one gentle email per day when no activity has been seen by the household's usual cutoff (the 95th percentile of past mornings plus a margin, never earlier than a minimum-wait floor, with a fixed conservative cutoff until there is enough history). The email explains why it was sent and includes signed, expiring reply links. Opening a link shows a confirmation page; pressing the button records "She's fine" or opens an away-date form. If Ring reports the device offline, Rhythm sends a different message ("we can't see the device") instead of a care alert. A small dashboard shows the learned routine, each alert with its saved reason, and local reply controls.

Rhythm is a check-in aid, not a safety or medical device.

## How I built it

Python, FastAPI, SQLite and a plain HTML/JavaScript dashboard. A Ring API client reads device status and Event History; a signed-webhook handler accepts `motion_detected`, `button_press` and device online/offline events; a scheduled job (`scripts/check_morning.py`) evaluates each morning and sends the email over SMTP. An automated test suite covers the rule, the one-alert-per-day logic, away/fine handling, time zones and daylight saving, webhook signatures, and the dashboard.

## Challenges

I had no Ring device. The Ring Developer Playground gave me a fake doorbell and real API responses, but only a simulated live-view event (`on_demand`), no past days, no offline transition, and no webhook delivery. I therefore did not treat that as real motion data. The routine in the demo is simulated, and the README says exactly which parts are real and which are simulated. A doorbell also sees the front door, not the inside of the home, so a quiet morning can be normal; the email says so.

## Accomplishments

An alert that explains itself, a rule that is simple enough to read in one minute, and confirmation-before-write reply links. The one-alert-per-day rule and the reply-link security behaviours are covered by automated tests.

## What I learned

Event History is time-gated, timestamps carry no timezone, and "no events" and "device offline" must be treated differently.

## What's next

Multi-family delivery and reply attribution, an all-clear message when activity is later recorded, a weekly plain-language summary, and live webhook delivery once a physical device is available.

## Built with

Python, FastAPI, SQLite, httpx, Ring Partner API (device status, Event History, webhooks), SMTP.
