---
id: system.manual
title: TextStrata User Manual
type: reference
version: 0.2.0
updated: 2026-07-05
tags: [system, manual, user-guide, help]
handling: human_plus_ai
preservation: rewrite_allowed
retrieval_priority: 90
provenance:
  created_via: textstrata-runtime
  authorship: system
---

# TextStrata User Manual

## What It Is

TextStrata is a typed, policy-aware knowledge substrate. It stores
markdown content with structured frontmatter, enforces validation rules,
builds a search index, computes cross-links and similarity scores, and
exposes everything through an MCP server so AI agents can read and write
the knowledge base programmatically.

## Quick Start

### Prerequisites

- Python 3.10+
- An existing or empty directory for the textstrata store (default: `./textstrata-store`)

### Running the MCP Server

```bash
# From the project root:
python3 -m textstrata.mcp_server

# Or with a custom store location:
python3 -m textstrata --workspace /path/to/store mcp
```

The server speaks JSON-RPC 2.0 over stdio with Content-Length framing.
Any MCP-compatible client (Claude Desktop, OpenCode, custom tooling) can
connect to it.

### CLI Commands

```bash
# Ingest a markdown file into the textstrata:
python3 -m textstrata ingest path/to/file.md

# Search the knowledge base:
python3 -m textstrata search "query terms"

# List all items:
python3 -m textstrata search "" --limit 100

# Get knowledge base statistics:
python3 -m textstrata stats

# Run gap analysis:
python3 -m textstrata analyze

# View the activity log:
python3 -m textstrata log

# Start the web shell:
python3 -m textstrata web
```

### Using MCP Tools

Once the MCP server is running, an AI agent can:

1. **Search**: `search_knowledge(query="...")`
2. **Read**: `read_item(item_id="...")`
3. **Ingest**: `ingest_text(content="...")`
4. **Explore**: `get_similar(item_id="...")`, `get_links(item_id="...")`
5. **Analyze**: `analyze_gaps()`, `get_stats()`, `get_knowledge_scores()`
6. **Research**: `research_query(query="...")` (requires Ollama)

See `system.ai-manifest` for full schemas and `system.developer-guide` for
extending the surface.

## Content Model

Every item in the textstrata has:

- **id**: unique stable identifier (`namespace.name` format)
- **type**: one of 14 content types (reference, architecture_note, etc.)
- **title**: human-readable name
- **tags**: categorical labels
- **handling**: who may touch it (human_only, human_plus_ai, etc.)
- **preservation**: how it may be transformed (preserve_exact, rewrite_allowed, etc.)
- **body**: the markdown content
- **provenance**: where it came from
- **related / dependencies**: explicit cross-references

Items are stored as markdown files with YAML frontmatter in the
`textstrata-store/normalized/` directory.

## MCP Resources

The server exposes several URI-addressable resources:

- `textstrata://items`: all items as JSON
- `textstrata://items/{id}`: single item as rendered markdown
- `textstrata://items/{id}/json`: single item as structured JSON
- `textstrata://items/{id}/history`: revision history
- `textstrata://tags`: tag index with counts
- `textstrata://types`: type index with counts
- `textstrata://stats`: knowledge base statistics
- `textstrata://activity`: recent operations
- `textstrata://settings`: current configuration
- `textstrata://graph/similar/{id}`: similar items
- `textstrata://graph/links/{id}`: cross-links
- `textstrata://graph/knowledge-score/{id}`: knowledge graph score
- `textstrata://graph/scoreboard`: all items ranked by score
