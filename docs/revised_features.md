# Revised feature checklist (items 2–12)

| Item | Status in this bundle | Notes |
|---|---|---|
| 2. Activity follow-up | Implemented (fixed in review: it never fired after a silent-morning alert) | One per local day after a care alert. Reports recorded time and front door/device, says it may be her or a visitor, never says “seen.” |
| 3. Multiple family members | Implemented (fixed in review: one failing address re-sent the alert to the others every check) | Up to three named email recipients; signed reply stores recipient and notifies the other configured members. Dashboard replies ask who is replying. |
| 4. All Ring devices | Implemented in API client | Device discovery, per-device status/history, and first activity aggregation. Mock-tested only; current Playground result was one fake device. |
| 5. Explainable chart/status | Implemented (fixed in review: usual window now shows the learned baseline for today's bucket) | Eight-week view with usual-time shading, cutoff line, live decision/reason, alert history. |
| 5b. Display modes | Implemented | Light, dark, high contrast on dashboard and reply pages; follows OS preference initially and remembers local choice. |
| 6. Setup page | Implemented (fixed in review: timezone changes now apply to stored events; sensitivity also moves the minimum wait) | Household, timezone, family, language, sensitivity. Secrets remain in `.env`. |
| 7. One command | Implemented | `python -m rhythm` launches the localhost dashboard and background polling/morning worker. |
| 8. Rolling baseline | Implemented | Default 56 days (`BASELINE_DAYS`); initial 14-day learning is not restarted; alert and away-range days excluded. |
| 9. Weekly summary | Implemented (fixed in review: a failed summary is now retried) | Plain template email once on Sunday evening; no generative service. |
| 10. Privacy controls | Implemented | Raw event times auto-deleted after 8 weeks (the learning window) while the worker runs; dashboard export and delete-everything. |
| 11. Italian/French email | Implemented | Care headline, threshold reasons, reply labels, activity follow-up, and weekly summary templates are localized. The legal/safety disclaimer remains the required English sentence. |
| 12. Physical Ring Indoor Cam | Not testable in this code-only environment | No camera was purchased or connected. Verify European account/API access and real event behavior before describing hardware validation. |

Deliberate exclusions remain: no video, image, face recognition, busier-than-usual alerts, generalized anomaly detection, or public server.
