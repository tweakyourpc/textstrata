# TextStrata knowledge audit, 2026-10-03

## Scope and authority

This is a read-only baseline of the live normalized corpus at
`/home/chris/Projects/TESTING/markbase-fabric/modular-fork/.workspace/normalized`.
The Markdown files are canonical; the SQLite FTS5 catalog is rebuildable. The
Google Inbox `Manifest` tracks incoming Docs, while the separate `Library`
sheet tracks article mirrors and explicitly requested revisions. No item was
deleted or merged during this audit.

The running `/whoami` endpoint identifies Textstrata 0.5.7 on the brokered
8707 port. Its user service runs the `modular-fork` checkout. A separate
`textstrata-impl` worktree contains the Google bridge code; the 15-minute cron
entry calls that worktree's wrapper and loads private configuration outside Git.
These are deployment facts, not portable default paths or ports.

## Corpus measurements

| Measure | Count |
| --- | ---: |
| Parsed normalized Markdown articles | 180 |
| Reference / note | 91 / 53 |
| Policy / architecture note / playbook | 14 / 9 / 7 |
| Other types | 6 |
| `preserve_exact` / `rewrite_allowed` | 170 / 10 |
| Missing `provenance.contributor_chain` | 18 |
| Missing or empty `related` | 167 |
| Explicit summary frontmatter | 0 |
| Under 200 words | 62 |

There are no exact duplicate bodies in the 180 records. Short records include
intentional asset records, so word count alone is not a deletion criterion.
Tags currently mix source (`youtube`), technology (`mcp`), topic
(`networking`), and purpose (`architecture`). The typed `type` field already
models document purpose; `provenance.source_kind` exists for source origin.
A full schema expansion is unwarranted until bootstrap and retrieval tests
show which additional facets earn their maintenance cost.

## High-confidence findings and preserve-first actions

| Record or area | Finding | Action before any deletion or merge |
| --- | --- | --- |
| `neoforge-internals` | Describes an older `/home/auggie` two-node bridge and port 5055. Current `/home/chris/NeoForge/README.md` and `manifests/roots.json` describe a control-plane root with existing roots left in place. | Preserve as a historical implementation record. Add a current canonical NeoForge record and mark the old one superseded with a link. |
| `project-context` | Calls the system AuggieAgentWiki and gives an old 8700 endpoint. Its filesystem-authority and provenance rules remain useful. | Preserve the historical context and link to a current TextStrata architecture record. |
| `system.manual`, `system.product-positioning`, `system.ai-manifest` | MarkBase-era branding and capabilities can be mistaken for current operational instructions. | Check each claim against release code and service configuration; keep historical details, label versions, and link to current authority. |
| `system-service-catalog`, `network-topology` | Contain many old paths, hosts, and ports. | Verify each service from current user units, port broker, and listener state before replacing operational facts. |
| 18 records without contributor chain | Provenance is incomplete. | Investigate original/revision records; do not infer a human or AI contributor. Mark unknown where evidence is absent. |
| 167 records without related links | Navigation and supersession are weak. | Add targeted links among canonical records and their historical implementation evidence, avoiding blanket auto-linking. |
| Potential near duplicate `pro-micro-unplug-to-lock-deployment` / `pro-micro-lock-dongle-retired-after-disconnect-failure` | Similar subject, apparently different historical stages. | Compare chronology and unique failure evidence before proposing a merge; retain both for now. |

## Code and deployment discrepancies

- The live server code is on local `main`; the Google bridge feature and its
  Apps Script live in another branch/worktree and an open GitHub PR. The
  deployed cron wrapper must be tested against the code it actually invokes.
- Current ingestion and MCP tools support search, reading, and review-queue
  proposals. There is no deterministic `bootstrap <project>` or
  `get_project_context(project)` contract in the core release.
- The article model has a typed `type`, open `extra` metadata, related IDs,
  and explicit provenance. Search indexes title, aliases, tags, and body, but
  has no current-project or supersession preference.
- The Google bridge handles a Library row marked `Updated` by checking its
  last-mirrored hash. Concurrent local and Google edits enter `Conflict`;
  ordinary mirroring does not overwrite the row. A documented, auditable
  resolution operation remains to be implemented.
- The 15-minute job can take several minutes because it checks every local
  article against the Google bridge. Completion inside the interval must be
  measured after curation; the lock prevents overlap but skipped runs can
  increase latency.
- `/home/chris/NeoForge` is not a Git repository and has no current agent
  loader file. `/home/chris/Neoforge4` is a legacy variant, not the canonical
  control plane.

## Curation and validation order

1. Add a deterministic small bootstrap from explicitly designated current
   records. Require referenced IDs to exist, reject supersession cycles, and
   cap output by section and character budget.
2. Publish current TextStrata and NeoForge architecture records locally.
   Give historical records explicit version/status and links without removing
   their unique content.
3. Add a reviewed proposal path for meaningful discoveries and updates.
   Keep the existing review queue as the approval boundary.
4. Test retrieval questions and a cold-agent read from the bootstrap alone.
5. After local sync, compare the Google Library mirror against the same
   questions. Keep Google as a mirror, never the origin of bulk cleanup.

No destructive cleanup is proposed in this checkpoint. The likely near
duplicate pair and historical overview records require item-level review.
