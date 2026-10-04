# Project context and reviewed article changes

The normalized local article files are the authority. An optional `context`
frontmatter facet designates records for deterministic project bootstrap:

```yaml
context:
  projects: [neoforge]
  tier: 1
  status: current
  summary: A concise current fact for the bootstrap.
  supersedes: [older-record]
```

`projects` is a list of stable slugs; `all` applies to every project. `tier`
is 1, 2, or 3. `status` is `current`, `historical`, `superseded`, or `draft`.
The publication gate rejects malformed facets and summaries over 600
characters. Existing articles without this facet remain valid. This avoids
forcing all 180 historical articles into a new taxonomy. `type` already
describes purpose, and `provenance.source_kind` describes source.

`textstrata bootstrap neoforge` or MCP `get_project_context` returns up to
five essential full records, ten task summaries, and twenty linked records
for deeper reading. The response includes a SHA-256 digest of the selected
normalized files and does not call a model or require the SQLite catalog.
Current records are explicit; a superseded record can appear only as a deeper
link. Missing supersession targets and cycles make bootstrap fail closed.

## Change loop

MCP `propose_article_change` accepts `create` or `update`, complete Markdown
with frontmatter, a reason, and optional existing source IDs. It validates
the draft and queues it inertly. AI-authored drafts must identify the vendor
and exact active model. Existing articles acquire a SHA-256 precondition at
proposal time. An agent cannot approve its own proposal through this tool.

Review on the local machine:

```bash
textstrata knowledge-proposals list
textstrata knowledge-proposals show ARTICLE_CHANGE_PROPOSAL_ID
textstrata knowledge-proposals apply ARTICLE_CHANGE_PROPOSAL_ID --reviewer NAME
textstrata knowledge-proposals reject ARTICLE_CHANGE_PROPOSAL_ID --reviewer NAME
```

Apply takes the item lock, rechecks the precondition, validates again, and
publishes through the normal revision path. A changed article fails without
being overwritten. A supersession or merge is represented as reviewed
updates to the involved articles; there is no automatic deletion. A reviewer
must preserve each older article's unique content and mark its status and
relationships explicitly. Propose only durable findings, not every edit.

After publication, check `bootstrap`, search, and the normal Google Library
mirror. New local articles appear in the Library index. The Inbox Manifest
is a separate incoming-document ledger.
