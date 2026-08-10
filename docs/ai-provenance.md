# AI provenance contract

TextStrata distinguishes the contributor class from the agent identity:

- `contributor_chain: via_ai` means an AI agent authored or edited the item.
- `authorship` is the human-readable agent name shown on item pages.
- `ai_vendor`, `ai_model`, and `ai_operation` provide exact machine-readable
  provenance.
- `contributor_chain: via_script` is reserved for deterministic pipelines that
  did not author content as an AI agent.

## MCP configuration

Configure these variables in each coding agent's MCP server environment:

```text
TEXTSTRATA_AI_VENDOR=OpenAI
TEXTSTRATA_AI_MODEL=your-active-model
TEXTSTRATA_AI_AUTHOR=your-agent-name
```

For Claude Code, use `Anthropic`, the exact active Claude model/deployment
label, and `Claude Code`. Tool-call values override environment defaults. If
the server has no identity configured, `ingest_text` rejects the write rather
than creating an unattributed AI note.

The CLI and deterministic acquisition paths remain separate: they should use
`via_script` only when the content was actually produced by the pipeline.
Never backfill an old note's model identity by inference; update it only when
the original authoring environment is known.
