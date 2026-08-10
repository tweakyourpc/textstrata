---
id: system.textstrata-1-workbench-upgrade
title: TextStrata Workbench, Media-First Knowledge Workspace
type: reference
tags: [textstrata, workspace, media, photos, obsidian]
aliases: [TextStrata workbench, Media library]
authorship: Codex
contributor_chain: via_ai
created_via: textstrata-mcp
ai_vendor: OpenAI
ai_model: gpt-5.6-luna medium
ai_operation: authored
preservation: preserve_exact
---

# TextStrata Workbench, Media-First Knowledge Workspace

## Upgrade contract

TextStrata keeps its deterministic Markdown storage, explainable links, ingestion
queue, provenance, graph, search, and safe vault interoperability while adding
a workspace layer for daily knowledge work. The first visible slice is the
Media Library: uploaded files remain content-addressed and reusable instead of
being trapped inside one note.

## Photo behavior

Pasted or dropped images are stored once under their SHA-256 identity. The
asset metadata records the original filename, media type, dimensions, size,
and an optional WebP preview. `/media` provides a stable gallery and a Copy
embed action. Markdown image embeds render with lazy loading, async decoding,
and click-to-enlarge behavior. The original remains available through the
immutable `/asset/<sha256>` URL.

This is stronger than a basic vault attachment folder because one photo can be
reused across notes without duplicate files, while the original and derived
preview have explicit retention and provenance boundaries.

## Deterministic rules

- Asset identity is SHA-256 of bytes; metadata listing is newest-first with ID
  tie-breaking, never filesystem enumeration order.
- A missing preview falls back to the original asset; non-image files remain
  visible in the same library without pretending they are photos.
- The Media Library is a browser surface over the existing asset store, not a
  second storage system.
- Markdown remains the interchange format, so exports and vault workflows do
  not depend on the web UI.

## Verification

Run the media unit tests, compile the package, run `node --check` on generated
page scripts, then restart the service before checking `/media`,
`/api/textstrata/assets`, and an item containing an image embed.
