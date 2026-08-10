---
id: system.app-first-product-boundary
title: TextStrata App-First Product Boundary and Portability
type: architecture_note
tags: [textstrata, product, ux, portability, architecture]
aliases: [App-first TextStrata, Product boundary]
authorship: Codex
contributor_chain: via_ai
created_via: textstrata-mcp
ai_vendor: OpenAI
ai_model: gpt-5.6-luna medium
ai_operation: authored
preservation: preserve_exact
---

# TextStrata App-First Product Boundary and Portability

## Product stance

TextStrata is a local knowledge app first and a substrate plus automation toolkit
second. The web UI should make the common path obvious: capture, browse, find,
read, edit, link, review, and manage media. CLI and MCP remain first-class
power surfaces for batch operations, automation, agents, backups, and
diagnostics, but advanced operations should not compete with the primary UI.

## Portability contract

The core is portable because it uses Python, Markdown files, SQLite, a
stdlib HTTP server, and a filesystem workspace. The base runtime needs Python
3.10+ and PyYAML. Documents, images, YouTube, and audio are optional capability
packs; they add MarkItDown, Pillow, yt-dlp, ffmpeg/tesseract, and Whisper as
available. Docker is the most turnkey Linux deployment; the native package
remains the best lightweight cross-platform option.

Measured on the development host on 2026-08-09: source and tests are about
2.7 MB, the active workspace is about 94 MB, and the full development virtual
environment is about 708 MB. Workspace size is dominated by retained originals,
derivatives, revisions, and model caches. Whisper/model caches should be
treated as optional gigabyte-scale capacity, not a core install requirement.

## App-first implementation rules

- Keep the primary navigation small: Library, New note, Search, Media, Review,
  and Settings/Help. Put maintenance, migrations, diagnostics, and bulk
  commands behind an Advanced or Operations boundary.
- Add features as vertical slices: domain rule, application use case, web
  adapter, CLI/MCP adapter where useful, focused UI, tests, and one compact
  documentation contract.
- Make the application use case the shared seam. Web, CLI, and MCP must call
  the same operation instead of reimplementing behavior in each surface.
- Use capability detection to explain unavailable tools in the UI rather than
  failing with hidden dependencies or bloating the base install.
- Require a user journey and a deletion/retention story before adding a new
  surface. Prefer improving an existing workflow over adding another menu.
- Treat browser DOM, generated JavaScript syntax, accessibility states, and
  live startup as release contracts alongside Python tests.

## Turnkey target

The next packaging increment should provide one setup/doctor flow that creates
the workspace, checks optional tools, allocates or reports the service port,
starts the app, opens the URL, and verifies backup/restore. A capability card
should show what is active and how to install only the missing pack. This can
be built on the current application and service layers without replacing the
HTTP server, Markdown format, SQLite catalog, or filesystem store.

## Extension rule

Every new feature should have one owner module and stable contracts at the
application boundary. Presentation should consume a view model; persistence
should remain rebuildable; documentation should describe the user behavior
and the automation equivalent. This keeps app polish from forcing a broad
refactor when a new feature arrives.
