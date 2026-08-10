---
created_via: "textstrata-mcp"
authorship: "Codex"
ai_processing: "none"
---
---
id: textstrata.architecture
title: TextStrata: A Machine-First Knowledge Substrate
type: architecture-note
version: 1.0.0
updated: 2026-07-03
tags: [textstrata, architecture, wiki, mcp, retrieval, presentation]
---

# TextStrata: A Machine-First Knowledge Substrate

TextStrata is evolving from a markdown-backed note store into a machine-first knowledge substrate that can also present well to humans. The useful abstraction is not a wiki, and not a document archive, but a textstrata: a linked, typed, policy-aware body of content that can be rendered through multiple presentation wrappers without losing meaning.

## Core idea

The substrate should define what content is, how it may be transformed, and how it may be presented. Presentation wrappers such as Hugo, a TUI, or a web view should be skins over the same semantic layer.

## Recommended layers

Content, semantic, policy, presentation, and enforcement. A skin may change visual style, density, typography, color palette, layout, and navigation. A skin may not change document hierarchy, semantic meaning, link targets, accessibility order, policy declarations, or retrieval metadata.

## Ingestion flow

The ingestion process should be policy-driven and deterministic before it becomes AI-assisted. Detect content class, suggest tags, flag source-of-truth versus disposable context, decide what transforms are allowed, store the original separately from transformed output, publish only after validation, and expose the result with its handling policy attached.

## Why this matters

The markdown is the storage format, but the textstrata is the system.
