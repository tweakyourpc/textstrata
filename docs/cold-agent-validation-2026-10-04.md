# TextStrata cold-agent and Google mirror validation, 2026-10-04

## Method

I treated the JSON output of `textstrata bootstrap neoforge --json` as the only initial local context, then answered the seven entry questions from its three full Tier 1 records, four Tier 2 summaries, and three Tier 3 links. The snapshot is bounded, about 11 KB, and derived from normalized local Markdown; it is not a model-generated summary. IDs in the response identify records to read next. This is a constrained evidence test, not an independently run agent session.

For the Google-only comparison, I used the connected Google Drive interface to read the `Library` index and seven mirrored Docs: current TextStrata and NeoForge architecture/state, Google bridge, engineering principles, and knowledge workflow. I did not use the local bootstrap when evaluating what those Docs expose. I compared Library hashes with normalized local file hashes after a bridge pass.

## Questions a new agent can answer

| Question | Local bootstrap answer | Google-only result |
| --- | --- | --- |
| What is this environment? | NeoForge is the control-plane root at `/home/chris/NeoForge`; RAG, AGENT_HOME, ENGRAM, WikiForge, and Ringling remain active roots. | Current NeoForge architecture and state Docs give the same identity and roots. |
| How does TextStrata work? | Normalized Markdown is authoritative. SQLite FTS5, MCP/search, and Google Library are derived access surfaces. Ingest and updates pass deterministic validation; AI is optional. | Current TextStrata architecture and Google bridge Docs give the same authority and flows. |
| How does NeoForge work? | It coordinates policies and roots without relocating them. Agents read its manifest for the task; this root is not a Git repository. | Current NeoForge architecture Doc conveys this. |
| What principles govern work? | Inspect instructions, code, Git, and live services; preserve knowledge and provenance; use PortBroker; review meaningful TextStrata changes; run the repository quality gate. | Engineering principles and workflow Docs convey these rules. |
| What changed recently? | Dated state summary points to canonical records, bootstrap, reviewed updates, safer retrieval, faster 15-minute Google sync, and conflict handling. | Current state and bridge Docs contain the same changes. |
| What should the agent work on next? | NeoForge state names failed operator-watch/daily-digest runs for diagnosis, AGENT_HOME decomposition, and path decoupling. TextStrata state calls for sync monitoring and targeted stale-record curation. | Both state Docs expose these priorities. |
| What would be dangerous? | Moving active roots based on an old design; treating a superseded record or Google mirror as authority; guessing ports; overwriting a conflict; publishing unreviewed AI text; committing secrets. | The current architecture, principles, and bridge Docs support the same constraints. |

## Verified mirror evidence

The `TextStrata Manifest` spreadsheet has separate `Manifest` (Inbox acquisition) and `Library` (published mirror) tabs. New local records appear in `Library`; the older Inbox rows remain in `Manifest`. The seven current Docs above are indexed as `Active`. After the 15-minute wrapper completed successfully, the Library hash matched normalized local SHA-256 for `system.textstrata-current-state`, `system.google-bridge-current`, `neoforge-current-architecture`, and `system.agent-engineering-principles`. Connected Drive reads found the current bootstrap, conflict-resolution, provenance, and NeoForge root facts in the corresponding Docs. The bridge pass that propagated the state and conflict article edits exited 0 in 96 seconds, below the 15-minute interval.

The subsequent scheduled pass reported one `BRIDGE_UNAVAILABLE` for the historical manual, while its Library row already held the matching new hash and remained `Active`. A retry of the same locked wrapper exited 0 in 83 seconds. This is an ambiguous transport response, not evidence that the local and Google copies diverged. Current architecture and NeoForge supersession edits also reached Active rows with matching local hashes.

## Retrieval probe

The shared keyword retrieval path put the current canonical article first for TextStrata architecture, Google bridge operation, NeoForge agent entry, engineering principles, recent changes, and the document superseding the old NeoForge implementation. The port 8707 question returned a historical service incident first and the current state second; both identify the TextStrata service, but current operational confirmation still requires `/whoami` and PortBroker. These results were checked after rebuilding the catalog from 189 normalized articles. They show useful curation gains without claiming that every free-form question ranks perfectly.

## Gaps and follow-up

- A Google-only agent can reconstruct the system from clearly titled current Docs, but Google has no deterministic `bootstrap neoforge` action. It must find and read the canonical Docs. A small mirrored entry-point record or generated index may improve discovery if real Google-only use shows missed records.
- The bootstrap contains dated service observations, not live health. Before an operational change, inspect user units, listeners, and PortBroker. This test found an incorrect broker reservation name in the TextStrata architecture article; it was corrected through a reviewed update and verified in the Google mirror.
- The current corpus still contains historical and short records. The audit preserves their unique details; targeted review remains preferable to bulk deletion.
- A production Google `Conflict` row was not fabricated to test resolution. Unit and Apps Script contract tests cover stale fingerprints, both-side snapshots, explicit choices, and guarded writes; the deployed bridge advertises the new actions. A real conflict should be resolved only when one occurs.
