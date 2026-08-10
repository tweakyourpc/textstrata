"""Versioned delivery helpers for generated browser scripts."""

from __future__ import annotations

import re
from html import escape


_SCRIPT_TAG_RE = re.compile(r"^\s*<script>(.*)</script>\s*$", re.S)


def client_asset_path(name: str, version: str) -> str:
    version = version or "dev"
    return f"/static/textstrata-{name}-{version}.js"


def client_asset_tag(name: str, version: str) -> str:
    path = escape(client_asset_path(name, version), quote=True)
    return f'<script src="{path}" defer></script>'


def client_asset_content(name: str, version: str, current_version: str) -> str:
    if version != current_version:
        raise KeyError(name)
    if name == "library":
        from .library_client import library_page_script

        html = library_page_script()
    elif name == "new-note":
        from .new_note_client import new_note_page_script

        html = new_note_page_script()
    else:
        raise KeyError(name)
    match = _SCRIPT_TAG_RE.match(html)
    if not match:
        raise ValueError(f"invalid generated script wrapper: {name}")
    return match.group(1).strip() + "\n"
