# Release snapshot and rollback

Create a disposable private-GitHub source snapshot containing `src/`, `tests/`, `docs/`, generic `seed/`, generic `scripts/`, `pyproject.toml`, `README.md`, Docker files, and quality configuration. Exclude workspaces, corpora, private instructions, machine service files, personal paths/IPs, caches, virtual environments, media, and model data.

For the 0.5.5 hardening release, run the repository quality gate and
`python scripts/release_audit.py --root SNAPSHOT --strict-source-only` before
packaging. The audit must report `release audit: clean`, and the quality gate
must report `phase5 backup/restore/upgrade smoke: ok`. The source snapshot
contains only portable application code, tests, generic documentation, and
examples. Workspaces, credentials, service files, private instructions, and
model caches remain outside the source snapshot.

Promotion is a controlled restart only after `/whoami`, `/healthz`, ingestion, search, item rendering, acquisition, and browser checks pass on an isolated development port selected by the operator. Record the prior production checkout and version. Rollback means stopping the promoted service and restarting that recorded prior checkout against the unchanged workspace. Do not migrate, merge, rewrite, or delete the corpus during promotion.
