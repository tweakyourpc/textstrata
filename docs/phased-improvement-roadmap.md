# TextStrata Improvement Roadmap

This roadmap applies to the modular fork preview on the broker-assigned 8707
service. The 8758 reference instance remains unchanged.

## Product Direction

TextStrata should be a dependable local knowledge workspace, not only a
note viewer. Its core loop is:

1. Capture text, documents, media, and URLs with honest provenance.
2. Normalize sources into durable, inspectable notes.
3. Retrieve useful context through search, links, scores, and review.
4. Let a human safely correct, organize, restore, and maintain the corpus.
5. Expose the same contracts to browser users, scripts, and agents.

## Phase 1: Operational Trust

Status: implemented on the modular fork.

- Keep Settings limited to durable appearance and library preferences.
- Give Review, Trash, Import History, and Maintenance dedicated dialogs.
- Require explicit confirmation for destructive operations.
- Preserve deep links for every operational surface.
- Remove library-only code and DOM from item pages.
- Verify generated JavaScript syntax and real HTTP route behavior.

Gate: all dialogs render once, deep links work, destructive APIs enforce the
confirmation header, retention values round-trip, Trash restores through HTTP,
and the full test suite passes.

## Phase 2A: Workspace Shell

Status: implemented on the modular fork.

- Keep the workspace identity and primary destinations visible on desktop.
- Use a mobile drawer without duplicating navigation DOM.
- Present notes as compact, scannable rows with retrieval and provenance signals.
- Make Recent and Needs curation addressable through stable view URLs.
- Preserve contributor filtering and explain why search results matched.

Gate: desktop and mobile navigation share one contract, saved views are URL-addressable, generated JavaScript passes syntax checks, and the full suite passes.

### Reference parity increment

Status: implemented on the modular fork.

- Match the 8758 graph payload and interactive exploration controls.
- Preserve selection-bounded, highlighted, resumable Read Aloud behavior.
- Restore direct tag correction and an addressable Orphaned corpus view.
- Keep classifier-derived graph inputs aligned with the reference rules.

Gate: graph summaries match on the shared corpus, browser focus and selection interactions execute successfully, mutations reindex immediately, and the full suite passes.

### Focused capture increment

Status: implemented on the modular fork.

- Replace the mixed-source New Note drawer with an addressable `/new` task page.
- Present Web link, File upload, and Blank text as mutually exclusive source modes.
- Keep file-only OCR and retention controls beside the file drop target.
- Keep title and acquisition notes secondary, with unsupported text-note metadata disabled explicitly.
- Preserve multiple-file import, 64 MiB checks, image paste/drop embedding, queue polling, import-history access, the `N` shortcut, and legacy `?panel=new` links.

Gate: each tab exposes one source panel and one source-specific action, generated JavaScript passes `node --check`, desktop and 390-pixel Chrome renders have no horizontal overflow, and the full suite passes.


## Phase 2B: Browser Code Boundaries

Status: complete on the modular fork.

- Split the library client into focused modules for ingestion, operations,
  review, preferences, and navigation.
- Introduce a shared fetch/error helper and one dialog lifecycle contract.
- Completed first increment: shared browser primitives, confirmation lifecycle,
  New Note ownership, and library navigation/saved-view extraction.
- Completed second increment: review queue rendering, API orchestration, and
  event handling now have one browser module owner.
- Completed third increment: settings loading, appearance persistence, and
  revision-limit persistence now have one preferences module owner.
- Completed fourth increment: imports, trash, maintenance, About, and command
  routing now have one operations module owner.
- Completed fifth increment: library and New Note behavior are served through strict, versioned, immutable JavaScript asset URLs.
- Completed final increment: real Chromium checks cover saved-view and settings deep links, external asset execution, and the New Note workflow at 390 pixels.

Gate: generated pages contain no duplicated handlers, modules have focused
tests, and browser checks cover keyboard and mobile interaction paths.

## Phase 3: Retrieval and Navigation

Status: in progress on the modular fork.

- Completed first increment: `/recent`, `/needs-curation`, and `/untagged` are deterministic server-backed corpus views; legacy `?view=` links redirect to their canonical routes.
- Completed second increment: result chips explain field matches plus importance or indexed-date sort metadata.

- Make saved searches and filters addressable with stable URLs.
- Improve result explanations so importance, text matches, and relationships
  are understandable rather than opaque scores.
- Add useful corpus views such as recently changed, untagged, isolated, and
  pending review without turning the library into a dashboard of cards.
- Measure search quality against a small checked-in relevance fixture.

Gate: representative queries have deterministic ranking tests and every corpus
view remains usable with keyboard-only navigation.

## Phase 4: Ingestion Observability

Status: implementation in progress on the modular fork.

- Duplicate submissions with the same source identity now reuse queued, processing, or completed jobs.
- Retry is explicit at `POST /api/acquisition/queue/<id>/retry` and only requeues failed jobs marked retryable.


- Acquisition jobs persist stage, attempt count, and retryability across restarts and failures.

- Give jobs structured stages, timestamps, retryability, and actionable errors.
- Make source identity and duplicate handling explicit before publication.
- Surface retained-original lifecycle and storage impact clearly.
- Keep queue actions idempotent so browser retries do not corrupt job state.

Gate: acquisition fixtures cover success, retry, cancellation, duplicate input,
and retained-original cleanup without using live external sources.

## Phase 5: Production Hardening

Status: readiness and recovery contracts implemented; backup and upgrade gate remains.

- Define migrations and backup/restore checks for every durable data format.
- Add LAN deployment controls appropriate to the trust boundary, including
  write protection if the service is reachable beyond a trusted network.
- Add health, readiness, and recovery verification around the server wrapper.
- Establish a supported upgrade path between released TextStrata versions.

Gate: a disposable workspace can be upgraded, backed up, restored, restarted,
and smoke-tested by one documented quality command.

## Working Rule

Each phase must preserve the published HTTP and item-storage contracts unless a
migration is deliberate, documented, and tested. Visual progress does not count
as complete when the backing action, failure state, or recovery path is missing.
