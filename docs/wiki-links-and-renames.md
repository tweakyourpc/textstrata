# Wiki links, aliases, and safe renames

TextStrata supports Obsidian-style `[[item-id]]`, `[[Title]]`, and
`[[Alias|display text]]` links. Targets resolve case-insensitively against an
item ID, title, or frontmatter `aliases` list. Resolved links render as item
links and become explainable `wikilink` graph edges; unresolved links remain
visible as missing references.

Rename an item through:

`POST /api/textstrata/item/<item-id>/rename`

with `{ "new_id": "new-item-id" }`. The operation rejects invalid or
colliding IDs, updates normalized frontmatter and inbound wiki links, rebuilds
the derived catalog, and moves the original and cleaned files without
rewriting their contents. Existing aliases remain valid after a rename.

The filesystem remains authoritative. A rename is therefore a coordinated
normalized-store and catalog mutation, not a database-only update.
