# Product feedback (Devpost form)

Answers follow the five questions in the Official Rules. Edit them into your own words before submitting.

## 1. Which developer tools, APIs and SDKs did you use, and for what?

- **Ring Partner API** (`https://api.amazonvision.com`):
  - `GET /v1/devices` to find the device
  - `GET /v1/devices/{id}/status` to read the `online` field, which drives Rhythm's "we can't see the device" message instead of a care alert
  - `GET /v1/history/devices/{id}/events` to read event metadata (type, start and end time) for learning first-activity times
- **Ring webhooks** (documented format): Rhythm has a handler for `motion_detected`, `button_press`, `device_online` and `device_offline`, with HMAC-SHA256 signature checks and request-ID de-duplication. It is tested with synthetic payloads only.
- **Ring Developer Playground**: a short-lived token, a fake Doorbell Pro, and simulated live-view events, since I have no Ring device.
- **ring-api-helloworld** sample repository: to confirm my token and see real response shapes before writing my own client.
- **Ring Developer Console**: to create the private app and get credentials.

## 2. What worked well?

- Going from no device to a real API response took minutes with the Playground token and the hello-world explorer script.
- The JSON:API response shapes were consistent between endpoints, so one small client handled devices, status and history.
- The device status response made the `online` field easy to find.
- The sample repository ran on the first try with Python.

## 3. What needs work?

- Simulating "motion" in the Playground created only an `on_demand` history event, never `motion` or `ding`. So a project built on motion and doorbell events cannot see those events without a physical device. The workaround was synthetic webhook payloads.
- Event History was empty until I simulated an event, and new events took about two minutes to appear. An empty list does not tell you whether the endpoint works or there is simply no data.
- History only contains events created after the account exists, so there are no past days to learn from.
- Timestamps are UTC epoch milliseconds, and device location gives only country/state, so the household timezone has to be collected separately.
- The Playground device is always online, so the offline path and `device_offline` webhooks cannot be tested.
- I could not find a way to get Playground events delivered to my own webhook URL.
- The Playground token lasts about 30 minutes, so it must be regenerated every working session.

## 4. How was your onboarding experience (zero to hello world)?

Mixed. Getting Started says a Ring device is required, which nearly stopped me, since I don't own one. I only found the Playground through the FAQ, the forum and the sample repository README. After that, hello world was fast: generate a token, run the explorer script, and see the device and its status.

Two things slowed me down on the way:

- The registration form asks for a home address, and it was not clear why or whether it is shown publicly.
- The app secret and HMAC key are shown only once.

Also, the Playground steps stayed greyed out until I reloaded the page and generated a new token.

## 5. Would you build with these devices and services again?

Yes. Event metadata plus device status is enough to build a useful caretaking tool that never touches video, which is a strong privacy story. I would build again with a physical device, or with a Playground that can simulate real `motion`/`ding` events, offline transitions and webhook delivery.
