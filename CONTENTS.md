# Rhythm bundle contents

- `rhythm/`: app, SQLite storage, Ring API client, rules, email, templates, and `python -m rhythm` launcher.
- `scripts/`: polling, scheduled checks, replay, device listing, and synthetic end-to-end/demo scripts.
- `tests/`: 53 local tests using synthetic events, mocked Ring calls, and fake senders.
- `outputs/`: synthetic logic test and original demo artifact.
- `docs/`: demo plan, Devpost draft, product feedback, feature status, hardware-validation checklist, links, and submission notes.
- `.env.example`, `requirements.txt`, `LICENSE`, `README.md`.

No real tokens, `.env`, local SQLite database, installed dependencies, or `.test-deps` folder are included. `docs/revised_features.md` lists the implementation status and the physical-device validation still needed.
