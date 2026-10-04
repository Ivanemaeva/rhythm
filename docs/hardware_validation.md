# Manual Ring Indoor Cam validation checklist

This task cannot be completed with the supplied Playground account because it contains no physical Ring device. Ring's developer getting-started guide lists a Ring camera or doorbell as a testing requirement, and its test guidance uses a personal Ring account/device. The Playground can exercise mock/simulated flows but does not establish real-device or European-region behavior.

When an Indoor Cam is available:

1. Confirm the device is supported and appears in `GET /v1/devices` for the European Ring account.
2. Confirm the app is authorized for the device and inspect only `/status` and `/history/devices/{id}/events` responses.
3. Trigger real motion and a doorbell press on supported hardware; record actual event type names and timestamp shape. Confirm Rhythm ingests them as activity metadata.
4. Confirm every home device is discovered and the earliest event across them drives the day.
5. Test an offline transition and restoration; verify all-offline status uses the offline notice branch, never a care alert.
6. Confirm Event History starts at the consent/access boundary and do not represent it as historical backfill from before authorization.

Do not call or add Media, video, image-snapshot, or face-analysis endpoints during this validation.
