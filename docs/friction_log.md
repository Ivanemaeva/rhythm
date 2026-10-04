# Friction log (Ring Developer experience)

Format from the Official Rules: task attempted, steps taken, expected vs. actual result, severity, workaround, suggestion.
Severity scale used: **High** = blocked progress; **Medium** = cost significant time or forced a design change; **Low** = minor annoyance.

### 1. Start building without a Ring device
- **Task:** Begin developing for the Ring track with no physical device.
- **Steps:** Opened developer.ring.com, then Getting Started, then read Requirements.
- **Expected:** A clear path for developers without hardware.
- **Actual:** Requirements say a Ring device is needed. The Developer Playground was only discoverable through the FAQ, the forum and the `ring-api-helloworld` README.
- **Severity:** High
- **Workaround:** Searched the FAQ and forum, then followed the sample repository README to the Playground.
- **Suggestion:** Add a "No device? Start with the Playground" link on Getting Started and the Requirements page.

### 2. Register as an individual developer
- **Task:** Complete developer registration.
- **Steps:** Opened the registration form and chose the individual option.
- **Expected:** An explanation of why each field is required and what is public.
- **Actual:** The form asks for a home address and a "customer-facing business name", with no note on whether either is shown publicly. It made me unsure whether the page was legitimate.
- **Severity:** Low
- **Workaround:** Read the documentation line about the business name, then continued.
- **Suggestion:** Add inline help saying which fields are public and why the address is needed.

### 3. Save app credentials
- **Task:** Create a private app and store its credentials.
- **Steps:** Console, then Create new app, then confirmed.
- **Expected:** The ability to view or rotate the secret later.
- **Actual:** The client secret and HMAC key are shown once; losing them means deleting and recreating the app.
- **Severity:** Medium
- **Workaround:** Downloaded the CSV immediately and stored values in a local `.env` file.
- **Suggestion:** Allow secret rotation without recreating the app.

### 4. Use the Playground after generating a token
- **Task:** Explore APIs and simulate an event in the Playground.
- **Steps:** Generated a token, then scrolled to "Explore APIs" and "Simulate live view event".
- **Expected:** Steps 2 to 4 become active.
- **Actual:** They stayed greyed out ("Generate a token to get started") even though the token was valid.
- **Severity:** Low
- **Workaround:** Reloaded the page and generated a new token.
- **Suggestion:** Refresh the page state after token generation, or show an error explaining why the steps are locked.

### 5. Read event history after simulating motion
- **Task:** See a simulated motion event through the Event History API.
- **Steps:** Simulated "motion" in the Playground, then called `GET /v1/history/devices/{id}/events` straight away.
- **Expected:** One `motion` event.
- **Actual:** 0 events at first. About two minutes later, one event appeared with `event_type: on_demand` (a live-view session), not `motion`.
- **Severity:** High (my project depends on motion/doorbell events)
- **Workaround:** Ignored `on_demand` in the code and drove the demo with synthetic `motion_detected` webhook payloads in the documented format; this is stated clearly in the README.
- **Suggestion:** Let the Playground create real `motion` and `ding` history events, and document the delay.

### 6. Test webhooks
- **Task:** Receive a signed webhook for a Playground event.
- **Steps:** Looked for a way to register a webhook URL for Playground events.
- **Expected:** Simulated events delivered to my endpoint.
- **Actual:** I found no way to deliver Playground events to my own URL.
- **Severity:** Medium
- **Workaround:** Built the handler from the documented payload and signature format and tested it with locally signed synthetic payloads.
- **Suggestion:** Add "send test webhook" to the Playground, with a sample signature.

### 7. Learn a routine from history
- **Task:** Backfill past days to build a baseline.
- **Steps:** Called Event History on a new account.
- **Expected:** Past events, or a clear statement that there are none.
- **Actual:** Only events created after the account existed (after simulation) were returned.
- **Severity:** Medium
- **Workaround:** A silent learning period, plus a replay script with simulated history for the demo.
- **Suggestion:** Provide a sample history dataset for the Playground device.

### 8. Convert event times to local time
- **Task:** Group events by the household's local morning.
- **Steps:** Read `start` from history events and called the device location endpoint.
- **Expected:** A timezone, or a local timestamp.
- **Actual:** Epoch milliseconds in UTC only; location gives country/state, not a timezone.
- **Severity:** Low
- **Workaround:** Made the household timezone a required setting and covered daylight-saving changes with tests.
- **Suggestion:** Expose the device's IANA timezone.

### 9. Test the "device offline" path
- **Task:** Check that Rhythm sends "we can't see the device" instead of a care alert.
- **Steps:** Looked for a way to set the Playground device offline.
- **Expected:** An offline toggle or a `device_offline` test event.
- **Actual:** The device is always `online: true`.
- **Severity:** Medium
- **Workaround:** Unit tests with synthetic `device_offline` payloads and stored status.
- **Suggestion:** Add an online/offline toggle to the Playground device.

### 10. Keep working across a session
- **Task:** Develop for more than 30 minutes.
- **Steps:** Used one Playground token.
- **Expected:** A longer-lived development token, or a visible expiry time.
- **Actual:** The token expired after about 30 minutes, and the API returned 401.
- **Severity:** Low
- **Workaround:** Regenerated the token; my client prints a clear "token expired" message on 401.
- **Suggestion:** Show the remaining validity in the Playground.
