# Submission checklist (deadline: 23 Oct 2026, 12:00 PM PT = 9:00 PM Pisa time; aim for 22 Oct)

## Repository
- [ ] Create the GitHub repository and push this folder (`git init` was already done locally).
- [ ] Confirm `.env`, tokens and `data/` are not in the history: `git log --all --stat` and `git grep -i "secret\|token" $(git rev-list --all)`.
- [ ] Public repository (preferred): MIT `LICENSE` in the main folder, and check it appears in the repository's About section.
- [ ] If private instead: add testing@devpost.com and chris-trag, knmeiss, giolaq, anishamalde, mosesroth, emersonsklar as collaborators close to submission (invitations expire after 7 days).
- [ ] Clean-clone test: clone to a new folder, `pip install -r requirements.txt`, run the unit tests and the replay demo exactly as the README says.
- [ ] The code calls Ring at runtime (`scripts/poll_ring.py`, `rhythm/ring_api.py`): run it once against the Playground and keep the output for the video.

## Video (under 3 minutes, public YouTube or Vimeo)
- [ ] Record with `docs/demo_video_plan.md`; show it working through the simulator or device; label simulated data.
- [ ] Watch the uploaded video signed out to confirm it is public and under 3:00.

## Devpost form
- [ ] Project description from `docs/devpost_about_draft.md` (edit to your own voice and facts).
- [ ] Product feedback from `docs/product_feedback_draft.md`.
- [ ] Friction log from `docs/friction_log.md` (up to 10% bonus).
- [ ] Feature requests from `docs/feature_requests.md` (optional).
- [ ] Track: Ring. Mini challenges: none.
- [ ] Repository URL, video URL, built-with list, screenshots of the dashboard and the email.
- [ ] Video: no copyrighted music; leave out the Playground's Creative Commons bird video, or credit it.

## Honesty check
- [ ] Nothing says live Ring motion events, live webhooks or a delivered email unless you actually verified it.
- [ ] The disclaimer "Rhythm is a check-in aid, not a safety or medical device" appears in the README, email and dashboard.
