# TextStrata 0.5.7

0.5.7 makes classification auditable. It keeps the published storage, HTTP, CLI, and MCP
contracts stable, and adds no migration.

## What changed

- Ingestion records the policy and tags it suggested for an item as an `EditRecord`,
  published under `extra.edited_by`, so what the rules proposed survives next to what was
  actually approved.
- New `show-misclassifications` command reports items whose declared policy or tags
  disagree with the suggestion. The point is repetition: one override is a judgement
  call, the same override across every item of a content type is a rule to fix.
- `EditRecord` gains `suggested_policy` and `suggested_tags`. Both are emitted only when
  set, so records without suggestions serialize exactly as before and no already-published
  item changes bytes.
- `Origin` gains `VIA_SCRIPT` for deterministic rule-driven work, which is neither a
  person nor a model. `Origin.coerce` still maps unrecognized values to `UNKNOWN`.

See `docs/classification-feedback.md` for the recorded shape and for what does and does
not count as a disagreement.

## Determinism

The suggestion record deliberately carries no timestamp. `canonical_frontmatter`
serializes `edited_by` into `extra`, so a generated timestamp would leak into every
published file and identical input would stop normalizing to identical bytes. Normalized
output remains a pure function of the input bytes, `Provenance` already handles ingest
time the same way by emitting only a *declared* `ingested_at`, and the tests covering both
are unchanged and green.

## Upgrade and rollback rule

Nothing to migrate. Items published before this release carry no suggestion record, are
not reported, and are not rewritten; the report fills in as the corpus turns over.

Back up the workspace before upgrading. Restore the Markdown and asset files into a
disposable workspace, rebuild the catalog, and verify the manifest before touching
production. Promotion is a controlled service restart. If the new process fails health or
route checks, restart the recorded prior checkout against the unchanged workspace.

Note that ingestion behaviour changes on restart: items published afterwards carry a
suggestion record and items published before do not, which is expected and needs no
reconciliation.

Run the portable checks from the repository root:

```bash
.quality-gate
python scripts/release_audit.py --root SNAPSHOT --strict-source-only
```
