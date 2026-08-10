"""Deterministic setup and capability status shared by CLI and web surfaces."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Mapping

from .. import __version__
from ..acquisition import capabilities as acquisition_capabilities
from ..store import TextStrataStore


def _tool(name: str) -> str | None:
    return shutil.which(name)


def _check(path: Path, *, writable: bool = False) -> dict[str, object]:
    exists = path.is_dir()
    can_write = bool(exists and os.access(path, os.W_OK)) if writable else True
    return {
        "id": path.name or "workspace",
        "label": f"{path.name or 'Workspace'} directory",
        "available": exists and can_write,
        "detail": str(path) if exists else "Directory does not exist yet.",
    }


def _optional_capabilities() -> list[dict[str, object]]:
    detected = acquisition_capabilities()
    def card(
        capability_id: str,
        label: str,
        available: bool,
        dependency: str,
        hint: str,
        *,
        model_download_required: bool = False,
    ) -> dict[str, object]:
        return {
            "id": capability_id,
            "label": label,
            "available": bool(available),
            "missing_dependency": None if available else dependency,
            "install_hint": hint,
            "model_download_required": model_download_required,
        }

    cards = [
        card("documents", "Documents", bool(detected.get("documents")), "markitdown", "pip install -e '.[documents]'") ,
        card("images", "Images and OCR", bool(detected.get("images")), "tesseract", "Install tesseract-ocr, then optionally pip install -e '.[images]'") ,
        card("youtube", "YouTube", bool(detected.get("youtube")), "yt-dlp", "pip install -e '.[youtube]'") ,
        card(
            "audio",
            "Local audio transcription",
            bool(detected.get("audio_transcription")),
            "ffmpeg and whisper CLI",
            "Install ffmpeg and pip install -e '.[audio]'; the selected Whisper model may download on first use.",
            model_download_required=True,
        ),
        card(
            "ai-assisted-commands",
            "Optional AI-assisted commands",
            bool(_tool("ollama")),
            "Ollama (optional)",
            "Install Ollama separately and pull a model before using textstrata ask/research.",
            model_download_required=True,
        ),
    ]
    return sorted(cards, key=lambda item: str(item["id"]))


def setup_status(
    workspace_root: str | Path,
    *,
    version: str = __version__,
    host: str = "",
    port: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return read-only setup state. This function does not create files."""
    root = Path(workspace_root).expanduser().resolve()
    normalized = root / "normalized"
    metadata = root / ".fabric"
    exists = root.is_dir()
    item_count = len(list(normalized.glob("*.md"))) if normalized.is_dir() else 0
    storage_ready = normalized.is_dir() and metadata.is_dir()
    initialized = storage_ready or item_count > 0
    checks = [
        {"id": "python", "label": "Python runtime", "available": True, "detail": sys.version.split()[0]},
        {"id": "workspace", "label": "Workspace directory", "available": exists and os.access(root, os.W_OK), "detail": str(root)},
        {"id": "storage", "label": "Filesystem storage", "available": storage_ready, "detail": "normalized/, .fabric/" if storage_ready else "Run initialization to create storage directories."},
        {"id": "sqlite", "label": "SQLite catalog", "available": True, "detail": "SQLite is provided by the Python runtime."},
    ]
    return {
        "workspace": str(root),
        "initialized": initialized,
        "empty": item_count == 0,
        "item_count": item_count,
        "storage_ready": storage_ready,
        "required_core_checks": checks,
        "core_ready": all(bool(check["available"]) for check in checks),
        "optional_capabilities": _optional_capabilities(),
        "installation_guidance": {
            "core": "pip install -e .",
            "policy": "Optional packs never install packages or download models automatically.",
        },
        "service": {"service": "textstrata", "version": version, "host": host, "port": port},
        "documentation": {"installation": "/docs/installation", "backup_restore": "/docs/backup-restore"},
    }


def initialize_workspace(
    workspace_root: str | Path,
    *,
    version: str = __version__,
    host: str = "",
    port: int | None = None,
) -> dict[str, object]:
    """Idempotently create only the standard workspace directories."""
    root = Path(workspace_root).expanduser().resolve()
    before = {str(path) for path in (root, root / ".fabric", root / "original", root / "normalized", root / "cleaned", root / "trash", root / ".fabric" / "revisions") if path.exists()}
    store = TextStrataStore(root)
    store.ensure_dirs()
    status = setup_status(root, version=version, host=host, port=port)
    created = sorted(str(path) for path in (root, root / ".fabric", root / "original", root / "normalized", root / "cleaned", root / "trash", root / ".fabric" / "revisions") if path.exists() and str(path) not in before)
    return {"initialized": True, "created": created, "status": status}
