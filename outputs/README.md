# Rhythm synthetic logic check

Rhythm is a check-in aid, not a safety or medical device.

Run with `python outputs/synthetic_alert_test.py`.

The script generates 21 deterministic days of fake first-activity times in the Europe/Rome household timezone, with weekday/weekend differences and modest noise. It learns from the first 14 days and checks the next seven, which contain one late morning and one offline day.

- **Part 1** (default floors, 10:00 weekdays / 11:00 weekends) asserts no care alerts on the five normal mornings, one on the late morning, and the separate `device_offline` outcome on the offline day.
- **Part 2** lowers the floors so the *learned* history decides. It asserts that the weekday cutoff equals the latest learned morning plus the 15-minute margin, that a 09:30 morning then alerts while the 10:00 default floor would not, and that weekends still use the fixed fallback because two weeks contain only 4 weekend samples (minimum 5).

This is a logic test only. It does not prove that Ring provides the required real-world events or status, that the thresholds suit a real household, or that the system is safe or medically reliable. The silent-morning path (no events at all) is covered in `tests/test_rhythm.py`.
