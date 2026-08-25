# TextStrata 0.5.6

0.5.6 is the invariant-hardening release. It keeps the published storage,
HTTP, CLI, and MCP contracts stable while making the storage guarantees
enforced rather than merely observed.

## What changed

- A stored original is written once and never replaced. Re-ingesting
  different content under an existing id is refused, and the first original
  survives the refusal.
- Revisions move into and out of the trash transactionally. A trashed item
  carries its revisions with it, leaves nothing in the live store, and
  restores intact.
- A purge leaves no copy of the purged body anywhere under the workspace,
  including revision directories.
- Publication of a single item is serialized under a per-item lock, so
  concurrent writes to the same id no longer interleave.
- Frontmatter salvage and merge are type-aware. Malformed provenance values
  are normalized to their declared types instead of being passed through.
- `provenance.contributor_chain` declared as a YAML sequence is normalized to
  the canonical comma-separated form. Previously such a value reached the
  catalog as a list and aborted server startup during the index rescan,
  making an entire workspace unservable because of one item.
- Writes are durable, and revision limits and timestamps are configurable and
  covered.

## Upgrade and rollback rule

Back up the workspace before upgrading. Restore the Markdown and asset files
into a disposable workspace, rebuild the catalog, and verify the manifest
before touching production. Promotion is a controlled service restart. If the
new process fails health or route checks, restart the recorded prior checkout
against the unchanged workspace. No corpus migration or rewrite is part of
0.5.6.

The storage path changes write semantics, so rehearse on a copy of the
workspace rather than on the production store, and confirm the development
port is not pointed at the production workspace before trusting the result.

Run the portable checks from the repository root:

```bash
.quality-gate
python scripts/release_audit.py --root SNAPSHOT --strict-source-only
```
