---
id: system.changelog
title: TextStrata Release Notes
type: reference
version: 0.5.6
updated: 2026-08-25
tags: [system, release-notes, version]
handling: human_plus_ai
preservation: rewrite_allowed
retrieval_priority: 90
provenance:
  created_via: textstrata-runtime
  authorship: system
---

# TextStrata Release Notes

This document records concise, user-visible release notes. Internal
experimentation, deployment details, and private development history do not
belong in the workspace documentation.

## 0.5.0

- Local-first Markdown and filesystem storage with a rebuildable SQLite search index.
- Deterministic ingestion, validation, cross-links, similarity scoring, and review workflows.
- Web library with Setup, New Note, Search, Media, Review, Graph, and Settings surfaces.
- Optional MCP server, acquisition packs, backup control plane, and AI-assisted commands.
- Core installation remains free of Ollama, embeddings, model downloads, and external service requirements.
- Optional capabilities are detected explicitly and can be installed independently.

For installation and capability requirements, see the repository README and
`docs/setup-capabilities.md`.

## 0.5.5

- Acquisition jobs now expose lifecycle timestamps, stages, attempts, retryability, and recovery state across restarts.
- Backup verification compares restored workspace files against SHA-256 manifests before the disposable catalog is rebuilt.
- The release quality gate now exercises backup, restore, and catalog-rebuild upgrade behavior in an isolated workspace.
- Existing HTTP, CLI, MCP, Markdown, and filesystem contracts remain unchanged.

## 0.5.6

- A stored original is written once and never replaced, so re-ingesting different content under an existing id is refused rather than overwriting the first copy.
- Revisions move into and out of the trash as a unit, restore intact, and leave no content behind anywhere in the workspace after a purge.
- Publication of a single item is serialized, so concurrent writes to the same id no longer interleave.
- Frontmatter recovery and merging are type-aware, and malformed provenance values are normalized instead of rejected.
- A server no longer fails to start because one item carries a malformed `contributor_chain`; the value is normalized and the item is indexed.
- Existing HTTP, CLI, MCP, Markdown, and filesystem contracts remain unchanged.
