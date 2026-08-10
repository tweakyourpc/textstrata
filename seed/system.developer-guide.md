---
id: system.developer-guide
title: TextStrata Developer Guide
type: reference
version: 0.2.0
updated: 2026-07-05
tags: [system, developer, architecture, contributing, extending]
handling: human_plus_ai
preservation: rewrite_allowed
retrieval_priority: 80
provenance:
  created_via: textstrata-runtime
  authorship: system
---

# TextStrata Developer Guide

## Codebase Layout

```
src/textstrata/
  __init__.py      — Package exports
  __main__.py      — CLI entrypoint and subcommands
  mcp_server.py    — MCP stdio server (JSON-RPC 2.0)
  models.py        — ContentType, TextStrataItem, HandlingMode, PreservationMode
  store.py         — Filesystem store (atomic writes, revisions, trash)
  ingest.py        — Ingestion pipeline (parse, classify, validate, publish)
  frontmatter.py   — YAML frontmatter parser (stacked blocks, merge, render)
  validate.py      — Validation rules (IDs, titles, contradictory policy)
  classify.py      — Content type detection, tag suggestion, policy suggestion
  catalog.py       — SQLite FTS5 search index
  linking.py       — Deterministic cross-linking (tags, types, references)
  similarity.py    — TF-IDF cosine, PageRank, HITS, community detection
  analyze.py       — Gap analysis (untagged, orphaned, stale, missing fields)
  activity.py      — Operation event log (JSONL)
  operations.py    — Settings, error reference article, error recording
  presentation.py  — Render contexts, skins (paper, wiki, console)
  research.py      — RAG pipeline (search, retrieve, synthesize via Ollama)
  embeddings.py    — Local embedding model for semantic search
  acquisition.py   — File/URL/YouTube acquisition
  gateway.py       — Optional external acquisition gateway
  web.py           — Web shell (Flask-based)
seed/               — Seed items auto-ingested into fresh stores
tests/              — pytest test suite (108+ tests)
```

## How to Add a New MCP Tool

1. **Define the tool schema**: add an entry in `TextStrataMCP.tools()` with
   name, description, and JSON Schema inputSchema.

2. **Implement the handler**: add an `if name == "your_tool":` branch in
   `TextStrataMCP.call()`. Return `{"content": [{"type": "text", "text": "..."}]}`.

3. **Use existing backend**: most tools are thin wrappers around one of the
   domain modules (store, catalog, similarity, linking, research, etc.).

4. **Handle errors**: exceptions raised in `call()` are caught and returned
   as JSON-RPC errors (code -32000).

5. **Update the docs**: add the tool to `system.help-index` and the AI
   manifest in `system.ai-manifest`.

## How to Add a New MCP Resource

1. **Register the URI**: add to `list_resources()` (concrete URIs) or
   `resource_templates()` (URI templates with `{variable}`).

2. **Handle resolution**: add a branch in `read_resource()` that parses the
   URI, fetches data, and returns `{"contents": [{"uri": ..., "mimeType": ...,
   "text": ...}]}`.

3. **Update the docs**: add the URI to `system.manual` and `system.ai-manifest`.

## How to Add a New Content Type

1. Add the member to `ContentType` enum in `models.py`.
2. Add classification rules in `classify.py` (`detect_type` function).
3. Add handling/preservation defaults if needed.
4. Update `system.developer-guide` and `system.ai-manifest`.

## Conventions

- **Item IDs**: `namespace.name`: lowercase, dot-separated. System items
  use the `system.` prefix.
- **Tags**: lowercase, singular, hyphenated for multi-word.
- **Frontmatter**: YAML, `---` delimiters. Stacked blocks are merged.
- **Validation**: items must have a valid ID, a title, and non-contradictory
  handling/preservation policy.
- **Testing**: all backend modules have pytest tests. New tools should be
  tested through `TextStrataMCP.call()` in `test_mcp.py`.
