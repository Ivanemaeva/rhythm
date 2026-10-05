\# Friction log: Ring Developer experience (Rhythm)



Severity scale: \*\*High\*\* = blocked progress; \*\*Medium\*\* = cost significant time or forced a design change; \*\*Low\*\* = minor annoyance.



\## 1. Starting without a Ring device

\- \*\*Task attempted:\*\* begin building for the Ring track with no physical device.

\- \*\*Steps taken:\*\* developer.ring.com > Getting Started > Requirements.

\- \*\*Expected:\*\* a clear path for developers without hardware.

\- \*\*Actual:\*\* the requirements say a Ring device is needed. The Developer Playground, which needs none, was only discoverable through the FAQ, the forum and the `ring-api-helloworld` README.

\- \*\*Severity:\*\* High

\- \*\*Workaround:\*\* searched the FAQ and forum, then followed the sample repository README to the Playground.

\- \*\*Suggestion:\*\* add a "No device? Start with the Playground" link on Getting Started and Requirements.



\## 2. Developer registration

\- \*\*Task attempted:\*\* register as an individual developer.

\- \*\*Steps taken:\*\* opened the registration form and chose the individual option.

\- \*\*Expected:\*\* an explanation of each required field and of what is shown publicly.

\- \*\*Actual:\*\* the form asks for a home address and a "customer-facing business name", with no note on whether either is public. It made me doubt the page was legitimate.

\- \*\*Severity:\*\* Low

\- \*\*Workaround:\*\* read the documentation line about the business name, then continued.

\- \*\*Suggestion:\*\* inline help saying which fields are public and why the address is needed.



\## 3. Saving app credentials

\- \*\*Task attempted:\*\* create a private app and store its credentials.

\- \*\*Steps taken:\*\* Console > Create new app > Confirm.

\- \*\*Expected:\*\* the ability to view or rotate the secret later.

\- \*\*Actual:\*\* the client secret and HMAC key are shown once; losing them means deleting and recreating the app.

\- \*\*Severity:\*\* Medium

\- \*\*Workaround:\*\* downloaded the CSV immediately and kept the values in a local `.env` file.

\- \*\*Suggestion:\*\* allow secret rotation without recreating the app.



\## 4. Using the Playground after generating a token

\- \*\*Task attempted:\*\* explore the APIs and simulate an event.

\- \*\*Steps taken:\*\* clicked Generate Token, then scrolled to "Explore APIs" and "Simulate live view event".

\- \*\*Expected:\*\* steps 2 to 4 become active.

\- \*\*Actual:\*\* they stayed greyed out ("Generate a token to get started") although the token was valid.

\- \*\*Severity:\*\* Low

\- \*\*Workaround:\*\* reloaded the page and generated a new token.

\- \*\*Suggestion:\*\* refresh the page state after token generation, or explain why the steps are locked.



\## 5. Reading Event History after simulating motion

\- \*\*Task attempted:\*\* see a simulated motion event through `GET /v1/history/devices/{id}/events`.

\- \*\*Steps taken:\*\* simulated "motion" in the Playground, then called the endpoint immediately and again a few minutes later.

\- \*\*Expected:\*\* one `motion` event.

\- \*\*Actual:\*\* 0 events at first; about 2 minutes later one event appeared with `event\_type: on\_demand` (a live-view session), not `motion`.

\- \*\*Severity:\*\* High (Rhythm depends on motion events)

\- \*\*Workaround:\*\* ignored `on\_demand` in the code and drove the demo with synthetic `motion\_detected` payloads in the documented format, clearly labelled in the README.

\- \*\*Suggestion:\*\* let the Playground create real `motion` and `ding` history events, and document the delay.



\## 6. Testing webhooks

\- \*\*Task attempted:\*\* receive a signed webhook for a Playground event.

\- \*\*Steps taken:\*\* looked for a way to register my own URL for Playground events.

\- \*\*Expected:\*\* simulated events delivered to my endpoint.

\- \*\*Actual:\*\* I found no way to deliver Playground events to my own URL.

\- \*\*Severity:\*\* Medium

\- \*\*Workaround:\*\* built the handler from the documented payload and HMAC-SHA256 signature format and tested it with locally signed synthetic payloads.

\- \*\*Suggestion:\*\* a "send test webhook" button in the Playground, with a sample signature.



\## 7. Learning a routine from history

\- \*\*Task attempted:\*\* backfill past days to build a baseline.

\- \*\*Steps taken:\*\* called Event History on a new account.

\- \*\*Expected:\*\* past events, or a clear statement that none exist.

\- \*\*Actual:\*\* only events created after access were returned.

\- \*\*Severity:\*\* Medium

\- \*\*Workaround:\*\* a silent 14-day learning period, plus a replay script with simulated history for the demo.

\- \*\*Suggestion:\*\* a sample history dataset for the Playground device.



\## 8. Converting event times to local time

\- \*\*Task attempted:\*\* group events by the household's local morning.

\- \*\*Steps taken:\*\* read `start` from Event History items and called the device location endpoint.

\- \*\*Expected:\*\* a timezone, or a local timestamp.

\- \*\*Actual:\*\* epoch milliseconds in UTC only; device location gives country/state, not a timezone.

\- \*\*Severity:\*\* Low

\- \*\*Workaround:\*\* the household timezone is a required setting, and daylight-saving changes are covered by tests.

\- \*\*Suggestion:\*\* expose the device's IANA timezone.



\## 9. Testing the device-offline path

\- \*\*Task attempted:\*\* check that Rhythm sends "we can't see the device" instead of a care alert.

\- \*\*Steps taken:\*\* looked for a way to set the Playground device offline.

\- \*\*Expected:\*\* an offline toggle or a `device\_offline` test event.

\- \*\*Actual:\*\* the Playground device is always online.

\- \*\*Severity:\*\* Medium

\- \*\*Workaround:\*\* unit tests and a demo scenario with a synthetic offline status.

\- \*\*Suggestion:\*\* an online/offline toggle for the Playground device.



\## 10. Working longer than 30 minutes

\- \*\*Task attempted:\*\* develop with one Playground token across a work session.

\- \*\*Steps taken:\*\* kept using the same token.

\- \*\*Expected:\*\* a longer-lived development token, or a visible expiry time.

\- \*\*Actual:\*\* the token expired after about 30 minutes and the API returned 401.

\- \*\*Severity:\*\* Low

\- \*\*Workaround:\*\* regenerated the token; Rhythm prints a clear "token expired" message on 401 and reports a lost connection instead of a false alert.

\- \*\*Suggestion:\*\* show the remaining token validity in the Playground.

