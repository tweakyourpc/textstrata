---
id: system.changelog
title: TextStrata Release Notes
type: reference
version: 0.5.5
updated: 2026-08-10
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
