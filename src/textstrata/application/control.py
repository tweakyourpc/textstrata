"""Shared control-plane use cases for CLI, web, and agent adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..control import (
    backup_preview,
    backup_workspace,
    control_doctor,
    load_config,
    load_effective_config,
    restore_preview,
    restore_workspace,
)


def control_status(root: str | Path, *, rclone: str = "rclone") -> dict[str, Any]:
    config, config_file = load_config(root)
    return control_doctor(root, config=config, config_file=config_file, rclone=rclone)


def control_backup_preview(root: str | Path, *, rclone: str = "rclone") -> dict[str, Any]:
    config, config_file = load_config(root)
    result = backup_preview(root, config=config)
    result["config"] = str(config_file)
    return result


def control_backup(root: str | Path, *, dry_run: bool = False, rclone: str = "rclone") -> dict[str, Any]:
    config, config_file = load_effective_config(root, rclone=rclone)
    result = backup_workspace(root, config=config, dry_run=dry_run, rclone=rclone)
    result["config"] = str(config_file)
    return result


def control_restore_preview(source: str | Path) -> dict[str, Any]:
    return restore_preview(source)


def control_restore(source: str | Path, destination: str | Path) -> dict[str, Any]:
    return restore_workspace(source, destination)
