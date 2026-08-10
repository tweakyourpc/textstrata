"""Temporary presentation compatibility API.

Phase 2 keeps only the historical imports still used by CLI, web, MCP, and
tests. Remove this shim after callers migrate off ``textstrata.presentation``.
"""

from __future__ import annotations

from .legacy import (
    render_hugo_item,
    render_hugo_page,
    render_item_html,
    render_library_index,
    render_new_note_html,
    render_text,
    render_tui_item,
)
from .pages.media import render_media_html
from .pages.setup import render_setup_html
from .markdown import markdown_to_html
from .skin import CONSOLE_SKIN, PAPER_SKIN, WIKIPEDIA_SKIN, Skin, skin_from_settings
from .view_models import RenderContext

__all__ = [
    "CONSOLE_SKIN",
    "PAPER_SKIN",
    "WIKIPEDIA_SKIN",
    "RenderContext",
    "Skin",
    "markdown_to_html",
    "render_hugo_item",
    "render_hugo_page",
    "render_item_html",
    "render_library_index",
    "render_new_note_html",
    "render_media_html",
    "render_setup_html",
    "render_text",
    "render_tui_item",
    "skin_from_settings",
]
