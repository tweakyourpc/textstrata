"""Temporary compatibility layer for the historical ``textstrata.presentation`` module.

Phase 2 keeps the public API stable while page composition lives in
``presentation.pages``. Remove this shim once external imports migrate.
"""

from __future__ import annotations

import yaml

from ..models import TextStrataItem
from ..render_context import RenderContext
from .pages.item import render_item_html
from .pages.library import render_library_index
from .pages.new_note import render_new_note_html


def render_text(ctx: RenderContext) -> str:
    item = ctx.item
    lines = [
        f"{ctx.title}",
        f"ID: {item.id}",
        f"Type: {item.type.value}",
        f"Preservation: {item.preservation.value}",
        f"Priority: {item.retrieval_priority}",
    ]
    if item.handling.value:
        lines.insert(3, f"Handling: {item.handling.value}")
    if item.tags:
        lines.append(f"Tags: {', '.join(item.tags)}")
    if item.related:
        lines.append(f"Related: {', '.join(item.related)}")
    if item.dependencies:
        lines.append(f"Dependencies: {', '.join(item.dependencies)}")
    if ctx.policy_handling or ctx.policy_preservation:
        lines.append(
            f"Suggested policy: {ctx.policy_handling or '-'} / {ctx.policy_preservation or '-'}"
        )
    if ctx.suggested_tags:
        lines.append(f"Suggested tags: {', '.join(ctx.suggested_tags)}")
    if ctx.validation_errors:
        lines.append("Validation errors:")
        lines.extend(f"  - {err}" for err in ctx.validation_errors)
    if ctx.validation_warnings:
        lines.append("Validation warnings:")
        lines.extend(f"  - {warn}" for warn in ctx.validation_warnings)
    if ctx.outgoing_links:
        lines.append("Outgoing links:")
        for link in ctx.outgoing_links:
            source = getattr(link, "source", item.id)
            target = getattr(link, "target", "")
            reason = getattr(link, "reason", "")
            weight = getattr(link, "weight", "")
            lines.append(f"  - {source} -> {target} ({reason}, w={weight})")
    lines.append("")
    lines.append("Body")
    lines.append("----")
    lines.append(item.body.rstrip())
    return "\n".join(lines).rstrip() + "\n"


def render_hugo_item(item: TextStrataItem) -> str:
    fields = {
        "id": item.id,
        "type": item.type.value,
        "tags": list(item.tags),
        "related": list(item.related),
        "dependencies": list(item.dependencies),
        "handling": item.handling.value,
        "preservation": item.preservation.value,
        "retrieval_priority": item.retrieval_priority,
    }
    if item.provenance.authorship:
        fields["author"] = item.provenance.authorship
    if item.provenance.source_url:
        fields["source_url"] = item.provenance.source_url
    if item.provenance.created_via:
        fields["created_via"] = item.provenance.created_via
    front = yaml.safe_dump(fields, sort_keys=True, allow_unicode=True).rstrip()
    return f"---\n{front}\n---\n\n{item.body}\n"


def render_hugo_page(item: TextStrataItem, site_section: str | None = None) -> dict:
    content = render_hugo_item(item)
    section = f"/{site_section}" if site_section else ""
    return {"path": f"{section}/{item.id}.md", "content": content}


def render_tui_item(ctx: RenderContext) -> str:
    return render_text(ctx)
