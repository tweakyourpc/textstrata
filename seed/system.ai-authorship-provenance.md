---
id: system.ai-authorship-provenance
title: AI Authorship Provenance, MCP Identity Contract
type: standard
tags: [textstrata, provenance, mcp, ai-authorship]
aliases: [AI provenance contract, MCP authorship]
authorship: Codex
contributor_chain: via_ai
created_via: textstrata-mcp
ai_vendor: OpenAI
ai_model: gpt-5.6-luna medium
ai_operation: authored
preservation: preserve_exact
---

# AI Authorship Provenance, MCP Identity Contract

TextStrata separates contributor class from agent identity. `via_ai` means an AI
agent authored or edited content; `authorship` is the display name; and
`ai_vendor`, `ai_model`, and `ai_operation` are the machine-readable record.

Every MCP coding environment should configure `TEXTSTRATA_AI_VENDOR`,
`TEXTSTRATA_AI_MODEL`, and optionally `TEXTSTRATA_AI_AUTHOR` on its MCP server
process. The MCP `ingest_text` tool uses those values as defaults, accepts
per-call overrides, and rejects unidentifiable AI writes. This prevents the
UI's `unknown author` fallback without guessing a model after the fact.

CLI and deterministic acquisition remain separate paths and should use
`via_script` only when a deterministic pipeline authored the content. Existing
notes must not be backfilled by inference; update provenance only when the
original authoring environment is known.

## Verification

Inspect the normalized frontmatter after ingestion and confirm the item page
shows authorship plus the AI vendor/model. Run the MCP provenance tests and the
full repository quality gate before releasing changes.
