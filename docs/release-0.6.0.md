# TextStrata 0.6.0

0.6.0 adds Google Drive integration and makes library dates trustworthy. Markdown and
filesystem contracts are unchanged and no migration is required.

## What changed

- **Google Drive bridge.** A signed Apps Script web app lets TextStrata import Ready
  documents from an Inbox folder and mirror canonical articles to a Library folder.
  Library edits flow back only through guarded revision sync, and conflicts are resolved
  explicitly with `google-conflict`. Setup, security model and scheduling are in
  `docs/google-bridge-setup.md`.
- **`TEXTSTRATA_MIRROR_SINCE`.** Set an ISO date to stop the mirror from creating
  Library Docs for articles ingested before it. Articles that already have a Library Doc
  keep syncing.
- **Project bootstrap and reviewed proposals.** `bootstrap` assembles deterministic
  project context from current articles; `knowledge-proposals` reviews structured article
  changes and refuses stale writes.
- **Search.** CLI search accepts natural-language questions and ranks them through the
  trusted retrieval path.
- **Validation.** Malformed structured frontmatter is rejected before publication.

## Library dates

The web library parsed normalized files without the per-item metadata sidecar
(`.fabric/item-metadata/<id>.json`). Items without a declared `ingested_at` therefore
received the current time when the page was built, so cards showed today's date and
Recent imports and Recent changes were effectively arbitrary. The search catalog already
read the sidecar, which is why sorted searches were correct.

0.6.0 hydrates the sidecar wherever the web app loads items. The library home now lists
newest first, and Newest and Oldest work with an empty search box.

## Repairing migrated dates

When a workspace is copied, sidecars that were missing are recreated from file
modification times, so migrated items can carry the copy time. For items imported from
MarkBase, the original capture time is still recorded in
`extra.legacy_markbase_metadata.date_ingested`.

```
python scripts/repair_legacy_ingested_at.py WORKSPACE          # dry run
python scripts/repair_legacy_ingested_at.py WORKSPACE --apply  # write sidecar and catalog
```

The script only moves dates earlier, never later, and a second run reports no changes.
Back up `.fabric/item-metadata/` and `.fabric/catalog.db` before applying it.

## Upgrade and rollback rule

Back up the workspace before upgrading. Promotion is a controlled service restart. If the
new process fails health or route checks, restart the recorded prior checkout against the
unchanged workspace. The date repair is optional and is reversed by restoring the two
backed-up paths.
