# TextStrata 0.5.5

0.5.5 is the production-hardening release. It keeps the published storage,
HTTP, CLI, and MCP contracts stable while making recovery state and release
verification explicit.

## What changed

- Acquisition jobs persist `started_at`, `finished_at`, `stage_updated_at`,
  and `last_error_at` in addition to their existing stage, attempt count,
  retryability, and error fields.
- Jobs interrupted by a service restart return to the queue with a visible
  recovery message and a fresh queued-stage timestamp.
- The queue response includes a bounded `duration_seconds` value when a job
  has started, allowing clients to explain slow or failed work without
  inspecting SQLite directly.
- Backup manifests can be verified read-only against a workspace or restored
  copy. Missing and changed files are reported separately.
- The release gate exercises backup, restore, and catalog rebuild on a
  disposable workspace. SQLite remains derived and is rebuilt after restore.

## Upgrade and rollback rule

Back up the workspace before upgrading. Restore the Markdown and asset files
into a disposable workspace, rebuild the catalog, and verify the manifest
before touching production. Promotion is a controlled service restart. If the
new process fails health or route checks, restart the recorded prior checkout
against the unchanged workspace. No corpus migration or rewrite is part of
0.5.5.

Run the portable checks from the repository root:

```bash
.quality-gate
python scripts/release_audit.py --root SNAPSHOT --strict-source-only
```
