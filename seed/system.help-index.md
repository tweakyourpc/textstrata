---
id: system.help-index
title: TextStrata Help Index
type: reference
version: 0.2.0
updated: 2026-07-05
tags: [system, help, meta, manual, index, toc]
handling: human_plus_ai
preservation: rewrite_allowed
retrieval_priority: 100
provenance:
  created_via: textstrata-runtime
  authorship: system
---

# TextStrata Help Index

**Version:** 0.2.0
**Updated:** 2026-07-05
**Project:** TextStrata

A machine-first knowledge substrate with typed content, deterministic
cross-linking, policy enforcement, presentation skins, and an MCP interface
for AI agents.

---

## System Documentation

These items are part of the textstrata itself, they ship with every store and
describe how the system works, how to use it, and how to extend it.

| Item ID | Title | Audience |
|---|---|---|
| `system.manual` | User Manual | End-users |
| `system.developer-guide` | Developer Guide | Developers |
| `system.ai-manifest` | AI Capabilities Manifest | AI agents |
| `system.changelog` | Release notes | Everyone |
| `system.help-index` | This file | Everyone |
| `system.operations-error-reference` | Operations & Error Reference | Operators |

### Quick links (MCP)

- `textstrata://items/system.manual`
- `textstrata://items/system.developer-guide`
- `textstrata://items/system.ai-manifest`
- `textstrata://items/system.changelog`
- `textstrata://items/system.help-index`
- `textstrata://items/system.operations-error-reference`

---

## Current MCP Surface

The server exposes **16 tools** and **13 resource URIs**.

### Tools

| Tool | Purpose |
|---|---|
| `search_knowledge` | Full-text search across all items |
| `list_items` | List all published items |
| `read_item` | Read one item as rendered text |
| `preview_item` | Preview a file before ingestion |
| `ingest_text` | Ingest raw text |
| `render_item` | Render item as text or HTML |
| `get_similar` | Find similar items by content+tags |
| `get_links` | Show inbound/outbound cross-links |
| `get_knowledge_scores` | PageRank, HITS, community scores |
| `analyze_gaps` | Untagged, orphaned, stale analysis |
| `get_stats` | Knowledge base statistics |
| `get_activity` | Operation log |
| `get_settings` | Current textstrata settings |
| `delete_item` | Move item to trash |
| `research_query` | RAG Q&A from the knowledge base |
| `synthesize_topic` | Comprehensive topic briefing |

### Resource URIs

See `system.ai-manifest` for the full URI template table.

---

## Update Contract

This documentation set (`system.*`) follows the same conventions as
`system.operations-error-reference`:

- Keep item IDs stable.
- AI agents may rewrite explanations, add examples, and improve clarity
  as long as the structure and IDs remain valid.
- The `system.changelog` contains concise user-visible release notes; do not use it as an internal work log.
- The `system.ai-manifest` must stay machine-parseable.
