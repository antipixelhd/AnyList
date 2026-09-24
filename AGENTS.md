# Media Tracker handoff

This is the authorized working copy of Scrob. Preserve the reference repositories in ../reference Repos/.

Before implementing, read these canonical project documents:

1. docs/media-tracker/PLAN.md — accepted product specification and current approval status.
2. docs/media-tracker/IMPLEMENTATION.md — stages, engineering defaults, acceptance checks, backlog.
3. docs/media-tracker/DECISIONS.md — accepted decisions and superseded proposals.
4. docs/media-tracker/GLOSSARY.md — domain terminology.
5. docs/media-tracker/UI-REFERENCE.md and its screenshots — visual/interaction reference.

These canonical documents live in this repository under docs/media-tracker/. Keep them current. Do not treat earlier superseded conversation suggestions as requirements. In particular: zero is unrated; show ratings do not inherit to seasons; Completed does not automatically resume Watching in release one; streaming-library membership is separate from tracked entries and survives tracked-entry deletion.

The repository was cloned from local reference commit 3d75f172fc054ed90c39af9d336d5f5feda40d54. Its origin points to the reference repository: do not push to it. The user confirmed the specification and authorized implementation on 2026-09-18. Continue implementation without requesting that approval again. Read docs/media-tracker/STATUS.md for progress.

## Visual changes

Follow [AGENT.md](AGENT.md) for the shared typography categories, exact fallback font stacks, root-unit conventions, and stylesheet ownership.
