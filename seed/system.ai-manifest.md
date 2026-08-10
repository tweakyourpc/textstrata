---
id: system.ai-manifest
title: TextStrata AI Capabilities Manifest
type: reference
version: 0.2.0
updated: 2026-07-05
tags: [system, ai, mcp, capabilities, manifest, agent]
handling: human_plus_ai
preservation: rewrite_allowed
retrieval_priority: 100
provenance:
  created_via: textstrata-runtime
  authorship: system
---

# TextStrata, AI Capabilities Manifest

This manifest describes every MCP capability, resource URI, and tool
available in the current version. AI agents should read this first to
understand the surface without scanning the codebase.

---

## Server Info

- name: textstrata
- version: 0.2.0
- protocol: JSON-RPC 2.0 over stdio (Content-Length framing)
- MCP protocol version: 2024-11-05

---

## MCP Resources

### Concrete URIs (returned by resources/list)

| URI | MIME type | Description |
|---|---|---|
| textstrata://items | application/json | All items |
| textstrata://tags | application/json | Tag index with counts |
| textstrata://types | application/json | Content type index |
| textstrata://stats | application/json | KB statistics |
| textstrata://activity | application/json | Recent activity |
| textstrata://settings | application/json | Current settings |
| textstrata://graph/scoreboard | application/json | Knowledge scores (ranked) |

### URI Templates (returned by resources/templates/list)

| URI Template | MIME type | Description |
|---|---|---|
| textstrata://items/{item_id} | text/markdown | Single item (rendered) |
| textstrata://items/{item_id}/json | application/json | Single item (JSON) |
| textstrata://items/{item_id}/history | application/json | Revision history |
| textstrata://graph/similar/{item_id} | application/json | Similar items |
| textstrata://graph/links/{item_id} | application/json | Cross-links |
| textstrata://graph/knowledge-score/{item_id} | application/json | Knowledge score |

### Item ID conventions

- System docs use the `system.` prefix (e.g. `system.manual`)
- Templates use the `incident.` prefix
- User items should use a descriptive `namespace.name` pattern
- Valid: lowercase alphanumeric, dots, hyphens, underscores

---

## MCP Tools

### Search & Read

- **search_knowledge**(query: string, limit?: int): FTS5 full-text search
- **list_items**(): list all items (id, type, title)
- **read_item**(item_id: string): render an item as text
- **render_item**(item_id: string, format?: "text"|"html"): render with skin

### Ingest & Modify

- **ingest_text**(content: string, fallback_id?: string): ingest raw markdown
- **preview_item**(path: string, fallback_id?: string): preview before ingest
- **delete_item**(item_id: string): move item to trash

### Knowledge Graph

- **get_similar**(item_id: string, threshold?: float, top_k?: int): content+tag similarity
- **get_links**(item_id: string): inbound/outbound cross-links
- **get_knowledge_scores**(item_id?: string): PageRank, HITS, community

### Analysis

- **analyze_gaps**(): untagged, orphaned, stale, missing fields
- **get_stats**(): KB statistics (counts by type, tags, links)
- **get_activity**(limit?: int): operation log
- **get_settings**(): current textstrata configuration

### Research (requires Ollama)

- **research_query**(query: string, model?: string): RAG Q&A from KB
- **synthesize_topic**(topic: string, model?: string): comprehensive briefing

---

## Content Types

The following types are recognized. Use the `type` field in frontmatter.

- policy
- prompt_template
- playbook
- command_recipe
- standard
- style_guide
- code_sample
- architecture_note
- reference
- anti_pattern
- decision_record
- incident
- known_error
- note (default)

---

## Handling Modes

Controls who may process an item.

| Mode | Meaning |
|---|---|
| human_only | Only humans may edit |
| human_plus_ai | AI may suggest; human must approve |
| ai_only_eyes | AI may edit but not delete |
| auto_sanitize_then_review | AI may sanitize; human reviews |

## Preservation Modes

Controls how an item may be transformed.

| Mode | Meaning |
|---|---|
| preserve_exact | No changes allowed |
| summarize_allowed | May be summarized |
| remove_fluff_allowed | Packaging removed; core preserved |
| tag_only | Only tags may be set |
| rewrite_allowed | Full rewrite permitted |

---

## Update Contract for AI Agents

This documentation set (`system.*`) is part of the textstrata and is expected
to evolve. Here are the rules:

1. **Keep IDs stable.** Never change `id:` in the frontmatter of system docs.
2. **Keep the manifest accurate.** If you add a tool or resource, update
   `system.ai-manifest` and `system.help-index` immediately.
3. **Update release notes carefully.** Keep `system.changelog` concise and
   user-facing; do not record internal experiments or deployment history.
4. **Preserve structure.** The manifest's tables and sections are
   machine-parsed. Keep the markdown table format intact.
5. **Update the version.** When making meaningful changes, bump the version
   in the frontmatter and the `initialize` response in `mcp_server.py`.
6. **Validate before publish.** Run `validate()` on any item you create or
   modify. Check the errors array before publishing.

### Files to update when adding capabilities

- `src/textstrata/mcp_server.py`: add the tool/resource handler
- `seed/system.ai-manifest.md`: document the new capability
- `seed/system.help-index.md`: list the new capability
- `seed/system.changelog.md`: record a concise user-visible release note when appropriate
- `seed/system.developer-guide.md`: document any new patterns
