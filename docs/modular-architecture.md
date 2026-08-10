# Modular Architecture Notes

This fork keeps the current stdlib HTTP server and server-rendered layout, but splits the first unstable surfaces into clearer layers.

## Current Layer Boundaries

- `textstrata.presentation.skin` owns skins, appearance preference mapping, and CSS variable serialization.
- `textstrata.presentation.markdown` owns deterministic Markdown-to-HTML rendering, anchors, timestamp rows, and TOC generation.
- `textstrata.presentation.view_models` owns render context types shared by web, CLI, and MCP surfaces.
- `textstrata.presentation.client_scripts` owns item-page browser behavior and re-exports the library client for compatibility.
- `textstrata.presentation.library_client` owns library navigation, preferences, review, Trash, import-history, and maintenance behavior.
- `textstrata.presentation.new_note_client` owns source-mode selection, direct publication, acquisition enqueue, file handling, image embedding, and compact queue status for the focused New Note workflow.
- `textstrata.presentation.browser_common` owns shared DOM, escaping, fetch/error, and toast primitives emitted into page-local scripts.
- `textstrata.presentation.dialog_client` owns shared confirmation and dialog-dismiss lifecycle behavior.
- `textstrata.presentation.library_navigation` owns sidebar state, saved-view selection, and row filtering.
- `textstrata.presentation.pages` owns the item, library, graph, and New Note page composers.
- `textstrata.presentation.legacy` retains text and Hugo compatibility helpers while page composition lives under `presentation.pages`.
- `textstrata.application.library` owns dashboard/search orchestration and search reason labels.
- `textstrata.application.item_detail` owns item detail context assembly, including policy, validation, links, similarity, backlinks, and related labels.
- `textstrata.application.reviews` enriches persisted review entries with current note title, tags, and a bounded body excerpt without mutating queue storage.
- `textstrata.web` should parse requests, enforce HTTP safety checks, call application use cases, and serialize responses.

## Compatibility Rules

- Keep `from textstrata.presentation import ...` working until all downstream callers are migrated.
- Do not change page layout during modularization passes.
- Keep existing public routes and JSON response shapes stable.
- Keep generated inline JavaScript syntax-checked until scripts become served assets.
- Compose shared browser primitives once per page; do not copy fetch, toast, confirmation, or navigation handlers into another page bundle.
- Review APIs may enrich queue records for presentation, but derived note context must never be persisted back into the review queue.
- Run tests with `PYTHONPATH=src`; otherwise the parent editable install may import the active source instead of this fork.

## Phase 3 Retrieval Boundary

- `textstrata.application.library.corpus_view` owns deterministic Recent, Needs curation, and Untagged membership.
- Canonical routes are `/recent`, `/needs-curation`, and `/untagged`; legacy root `?view=` links redirect.
- Corpus view tests must distinguish result rows from contextual sidebar links.
- `retrieval_labels` owns field-match and sort explanations; importance and indexed dates must be labeled rather than shown as unexplained numbers.

## Next Extraction Targets

- Move direct ingest, acquisition enqueue, asset upload, and queue maintenance orchestration into `textstrata.application.ingestion`.
- Move item save/trash/restore/revision operations into `textstrata.application.items`.
- Move settings persistence wrappers into `textstrata.application.settings` so the web handler no longer calls `operations.save_settings` directly.
- Library browser behavior has focused navigation, preferences, review, and operations modules. Library and New Note scripts now use strict versioned asset URLs with immutable caching; real Chromium coverage now verifies deep links, dialog startup, and mobile New Note execution. Phase 2B is complete.
- Later, serve client scripts as versioned assets; do not introduce a bundler until behavior modules are stable.
