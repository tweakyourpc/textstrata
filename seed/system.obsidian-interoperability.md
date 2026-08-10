---
id: system.obsidian-interoperability
title: TextStrata, Obsidian Interoperability and Link-Aware Editing
type: reference
tags: [textstrata, obsidian, wiki-links, interoperability]
aliases: [Obsidian interoperability, Link-aware editor]
contributor_chain: via_script
created_via: textstrata-development
preservation: preserve_exact
---

# TextStrata, Obsidian Interoperability and Link-Aware Editing

## What changed

TextStrata now supports Obsidian-style wiki links in three forms: `[[item-id]]`,
`[[Title]]`, and `[[Alias|display text]]`. IDs, titles, and aliases resolve
case-insensitively. Resolved links render as normal item links and contribute
explainable `wikilink` edges to the knowledge graph. Unresolved links remain
visible as missing references.

Item pages now provide a link-aware editing surface. Typing `[[` loads matching
IDs, titles, and aliases; arrow keys and Enter or Tab insert a canonical link.
The live preview resolves the same targets as the published renderer. Aliases
can be added or removed without hand-editing frontmatter.

## Vault interoperability

`textstrata vault-import PATH` imports Markdown notes and attachments from an Obsidian
vault. It assigns deterministic IDs from existing valid IDs or relative paths,
preserves titles and aliases, rewrites unambiguous wiki links, and stores
attachments in TextStrata's content-addressed asset store. Existing items are
skipped unless `--overwrite` is explicit.

`textstrata vault-export PATH` writes normalized notes as an Obsidian-compatible
Markdown vault, including aliases and available attachments. TextStrata's original
source files remain authoritative and are not rewritten by export.

## Safe rename contract

`POST /api/textstrata/item/<item-id>/rename` with `{"new_id": "new-id"}` updates
normalized frontmatter, inbound wiki links, file paths, and the derived catalog
as one local operation. Original source contents are preserved. Existing aliases
continue to resolve after a rename.

## Verification

The feature is covered by focused wiki-link, alias, rename, import, export, and
generated-browser-script tests. The package version was advanced to `0.4.0` for
this interoperability release.
