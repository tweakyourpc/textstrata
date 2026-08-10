"""Workspace resolution and deterministic cascading configuration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


def resolve_workspace(
    cli_workspace: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    """Resolve CLI, environment, then current-directory workspace precedence."""
    env = os.environ if environ is None else environ
    # TEXTSTRATA_WORKSPACE is the public name. The older names remain accepted
    # for existing workspaces and integrations.
    selected = cli_workspace or env.get("TEXTSTRATA_WORKSPACE") or env.get("MARKBASE_WORKSPACE") or env.get("FABRIC_ROOT")
    base = cwd or Path.cwd()
    return Path(selected).expanduser().resolve() if selected else (base / ".workspace").resolve()


def global_config_path(
    *, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    env = os.environ if environ is None else environ
    user_home = home or Path.home()
    if sys.platform == "win32":
        base = Path(env.get("APPDATA", user_home / "AppData" / "Roaming"))
        return base / "TextStrata" / "config.toml"
    if sys.platform == "darwin":
        return user_home / "Library" / "Application Support" / "TextStrata" / "config.toml"
    base = Path(env.get("XDG_CONFIG_HOME", user_home / ".config"))
    return base / "textstrata" / "config.toml"


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import tomllib
    except ImportError:  # Python 3.10 compatibility
        data = _read_basic_toml(path)
    else:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"configuration root must be a table: {path}")
    return data


def _read_basic_toml(path: Path) -> dict[str, Any]:
    """Parse the small TOML subset used by TextStrata on Python 3.10."""
    result: dict[str, Any] = {}
    table = result
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            table = result
            for part in line[1:-1].strip().split("."):
                if not part:
                    raise ValueError(f"invalid TOML table at {path}:{line_number}")
                value = table.setdefault(part, {})
                if not isinstance(value, dict):
                    raise ValueError(f"TOML table conflicts with value at {path}:{line_number}")
                table = value
            continue
        if "=" not in line:
            raise ValueError(f"invalid TOML assignment at {path}:{line_number}")
        key, raw_value = (part.strip() for part in line.split("=", 1))
        if not key:
            raise ValueError(f"empty TOML key at {path}:{line_number}")
        if raw_value.startswith('"') and raw_value.endswith('"'):
            value: Any = json.loads(raw_value)
        elif raw_value in {"true", "false"}:
            value = raw_value == "true"
        else:
            try:
                value = int(raw_value)
            except ValueError as exc:
                raise ValueError(
                    f"unsupported TOML value at {path}:{line_number}; Python 3.10 fallback supports strings, integers, and booleans"
                ) from exc
        table[key] = value
    return result


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def load_cascading_config(
    workspace_root: str | Path, *, global_path: Path | None = None
) -> dict[str, Any]:
    """Deep-merge global TOML, workspace TOML, and workspace vocabulary."""
    metadata = Path(workspace_root).expanduser().resolve() / ".fabric"
    config = _merge(
        _read_toml(global_path or global_config_path()),
        _read_toml(metadata / "config.toml"),
    )
    synonyms_path = metadata / "synonyms.json"
    if synonyms_path.exists():
        try:
            synonyms = json.loads(synonyms_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid workspace synonyms JSON: {synonyms_path}") from exc
        if not isinstance(synonyms, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in synonyms.items()
        ):
            raise ValueError("workspace synonyms must be a string-to-string object")
        vocabulary = config.get("vocabulary", {})
        if not isinstance(vocabulary, Mapping):
            vocabulary = {}
        config = _merge(config, {"vocabulary": {"synonyms": synonyms}})
    return config


def apply_config_environment(config: Mapping[str, Any]) -> None:
    """Expose merged engine settings to existing runtime seams without override."""
    mappings = (
        ("network", "host", "FABRIC_HOST"),
        ("network", "port", "FABRIC_PORT"),
        ("llm", "endpoint", "OLLAMA_HOST"),
        ("llm", "model", "FABRIC_LLM_MODEL"),
    )
    for section, key, environment_key in mappings:
        values = config.get(section, {})
        if isinstance(values, Mapping) and key in values:
            os.environ.setdefault(environment_key, str(values[key]))
    network = config.get("network", {})
    if isinstance(network, Mapping):
        if "host" in network:
            os.environ.setdefault("TEXTSTRATA_HOST", str(network["host"]))
        if "port" in network:
            os.environ.setdefault("TEXTSTRATA_PORT", str(network["port"]))
    llm = config.get("llm", {})
    if isinstance(llm, Mapping) and "model" in llm:
        os.environ.setdefault("TEXTSTRATA_LLM_MODEL", str(llm["model"]))
