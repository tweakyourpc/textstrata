"""Optional Google Drive control plane for backup and approved ingest.

The core workspace remains usable without this module. When enabled, rclone
is the only external transport; credentials and its configuration stay on the
host. This module deliberately implements one-way backup and approved ingest,
not bidirectional synchronization.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .ingest import build_item


DEFAULT_CONFIG = {
    "version": 1,
    "textstrata_url": "http://127.0.0.1:8700",
    "control": {"remote_config": "", "status_target": ""},
    "backup": {
        "enabled": False,
        "target": "",
        "roots": ["normalized"],
        "include_tags": [],
        "exclude_tags": ["private", "draft"],
        "include_untagged": True,
    },
    "ingest": {
        "enabled": False,
        "remote_queue": "",
        "poll_interval_minutes": 10,
        "auto_approve_submissions": False,
        "status_target": "",
    },
}


@dataclass(frozen=True)
class BackupEntry:
    relative_path: str
    size: int
    sha256: str


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge(dict(result[key]), value)
        else:
            result[key] = value
    return result


def config_path(root: str | Path, environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("TEXTSTRATA_CONTROL_CONFIG") or env.get("MARKBASE_CONTROL_CONFIG") or (Path(root) / ".fabric" / "control.json")).expanduser().resolve()


def load_config(root: str | Path, *, path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> tuple[dict[str, Any], Path]:
    selected = Path(path).expanduser().resolve() if path else config_path(root, environ)
    if not selected.exists():
        return _merge({}, DEFAULT_CONFIG), selected
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid control configuration: {selected}") from exc
    if not isinstance(value, dict):
        raise ValueError("control configuration must be a JSON object")
    return _merge(_merge({}, DEFAULT_CONFIG), value), selected


def load_effective_config(root: str | Path, *, path: str | Path | None = None, rclone: str = "rclone") -> tuple[dict[str, Any], Path]:
    """Load local config, then apply an explicitly configured remote config."""
    workspace = Path(root).expanduser().resolve()
    config, selected = load_config(workspace, path=path)
    control = config.get("control", {})
    remote = str(control.get("remote_config") or "").strip() if isinstance(control, Mapping) else ""
    if not remote:
        return config, selected
    state = _state_dir(workspace)
    remote_path = state / "remote-control.json"
    _run_rclone(["copyto", remote, str(remote_path)], executable=rclone)
    return load_config(workspace, path=remote_path)


def _list(value: object) -> list[str]:
    return [str(item).strip().casefold().lstrip("#") for item in value] if isinstance(value, list) else []


def _allowed_tags(tags: list[str], config: Mapping[str, Any]) -> bool:
    backup = config.get("backup", {})
    if not isinstance(backup, Mapping):
        return False
    normalized = {tag.casefold().lstrip("#") for tag in tags}
    excluded = set(_list(backup.get("exclude_tags", [])))
    included = set(_list(backup.get("include_tags", [])))
    if normalized & excluded:
        return False
    if not included:
        return True
    if normalized & included:
        return True
    return bool(backup.get("include_untagged", False) and not normalized)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_backup_files(root: str | Path, config: Mapping[str, Any]) -> list[BackupEntry]:
    workspace = Path(root).expanduser().resolve()
    backup = config.get("backup", {})
    roots = backup.get("roots", ["normalized"]) if isinstance(backup, Mapping) else ["normalized"]
    selected: list[BackupEntry] = []
    for configured_root in roots if isinstance(roots, list) else ["normalized"]:
        base = (workspace / str(configured_root)).resolve()
        if workspace not in base.parents and base != workspace:
            raise ValueError(f"backup root escapes workspace: {configured_root}")
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            if not path.is_file() or any(part in {"_trash", "_uploads"} for part in path.relative_to(workspace).parts):
                continue
            try:
                item, _, _ = build_item(path.read_text(encoding="utf-8"), fallback_id=path.stem)
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if _allowed_tags(item.tags, config):
                selected.append(BackupEntry(str(path.relative_to(workspace)), path.stat().st_size, _sha256(path)))
    return sorted(selected, key=lambda entry: entry.relative_path)


def verify_backup_manifest(root: str | Path, manifest: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    """Verify a manifest against the authoritative workspace files.

    Verification is read-only. It is intentionally separate from transport so
    local tests and operators can validate a restored copy without contacting a
    remote provider or trusting the derived SQLite catalog.
    """
    workspace = Path(root).expanduser().resolve()
    missing: list[str] = []
    changed: list[str] = []
    verified: list[str] = []
    for raw in manifest:
        relative = str(raw.get("relative_path") or "")
        if not relative:
            changed.append("<missing-relative-path>")
            continue
        candidate = (workspace / relative).resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError:
            changed.append(relative)
            continue
        if not candidate.is_file():
            missing.append(relative)
            continue
        try:
            expected_size = int(raw.get("size", -1))
        except (TypeError, ValueError):
            expected_size = -1
        expected_hash = str(raw.get("sha256") or "")
        if candidate.stat().st_size != expected_size or _sha256(candidate) != expected_hash:
            changed.append(relative)
            continue
        verified.append(relative)
    return {
        "ok": not missing and not changed and len(verified) == len(manifest),
        "files": len(manifest),
        "verified": verified,
        "missing": missing,
        "changed": changed,
    }


def _state_dir(root: Path) -> Path:
    path = root / ".fabric" / "control-state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_rclone(args: list[str], *, executable: str = "rclone", dry_run: bool = False) -> dict[str, Any]:
    command = [executable, *args]
    if dry_run:
        return {"command": command, "dry_run": True, "returncode": 0, "stdout": "", "stderr": ""}
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"rclone failed to start: {exc}") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"rclone exited with {result.returncode}")
    return {"command": command, "dry_run": False, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def backup_workspace(root: str | Path, *, config: Mapping[str, Any], dry_run: bool = False, rclone: str = "rclone") -> dict[str, Any]:
    workspace = Path(root).expanduser().resolve()
    backup = config.get("backup", {})
    if not isinstance(backup, Mapping) or not backup.get("enabled", True):
        return {"enabled": False, "files": 0, "manifest": [], "actions": []}
    target = str(backup.get("target") or "").strip()
    if not target:
        raise ValueError("backup.target is required when backup is enabled")
    entries = select_backup_files(workspace, config)
    manifest = [entry.__dict__ for entry in entries]
    state = _state_dir(workspace)
    manifest_path = state / "backup-manifest.json"
    manifest_payload = {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "files": manifest}
    if not dry_run:
        manifest_path.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")
    actions: list[dict[str, Any]] = []
    if entries:
        relative_list = state / "backup-files.txt"
        if not dry_run:
            relative_list.write_text("\n".join(entry.relative_path for entry in entries) + "\n", encoding="utf-8")
        actions.append(_run_rclone(["copy", str(workspace), target, "--files-from", str(relative_list)], executable=rclone, dry_run=dry_run))
    if not dry_run:
        actions.append(_run_rclone(["copyto", str(manifest_path), f"{target.rstrip('/')}/backup-manifest.json"], executable=rclone))
    else:
        actions.append(_run_rclone(["copyto", str(manifest_path), f"{target.rstrip('/')}/backup-manifest.json"], executable=rclone, dry_run=True))
    return {"enabled": True, "files": len(entries), "manifest": manifest, "manifest_path": str(manifest_path), "target": target, "actions": actions}


def _http_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, method=method, headers={"Content-Type": "application/json"} if body else {})
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"local legacy acquisition engine request failed: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("local legacy acquisition engine response was not a JSON object")
    return value


def _queue_item(root: Path, config: Mapping[str, Any], item: Mapping[str, Any]) -> tuple[int | None, str, dict[str, Any]]:
    # ``markbase_url`` remains accepted for control-file compatibility.
    base_url = str(config.get("textstrata_url") or config.get("markbase_url") or "http://127.0.0.1:8700").rstrip("/")
    item_type = str(item.get("type") or "url")
    payload = str(item.get("payload") or "").strip()
    if not payload:
        raise ValueError("approved ingest item has empty payload")
    if item_type == "note":
        response = _http_json(f"{base_url}/api/ingest", method="POST", payload={"content": payload, "title": str(item.get("title") or "control ingest")})
        return None, base_url, response
    else:
        response = _http_json(f"{base_url}/api/acquisition/ingest", method="POST", payload={"url": payload, "title": str(item.get("title") or ""), "notes": str(item.get("notes") or "")})
    job_id = response.get("job_id")
    if not isinstance(job_id, int):
        raise RuntimeError("legacy acquisition engine ingest response did not include an integer job_id")
    return job_id, base_url, response


def _wait_for_job(base_url: str, job_id: int, *, timeout: int = 300) -> dict[str, Any]:
    deadline = datetime.now(timezone.utc).timestamp() + timeout
    while datetime.now(timezone.utc).timestamp() < deadline:
        queue = _http_json(f"{base_url}/api/acquisition/queue")
        jobs = queue.get("jobs", [])
        if isinstance(jobs, list):
            for job in jobs:
                if not isinstance(job, dict) or job.get("id") != job_id:
                    continue
                status = str(job.get("status") or "").casefold()
                if status in {"completed", "done", "success"}:
                    return job
                if status in {"failed", "error", "cancelled"}:
                    raise RuntimeError(str(job.get("error") or job.get("error_message") or f"job {job_id} ended {status}"))
        import time
        time.sleep(1)
    raise RuntimeError(f"timed out waiting for legacy acquisition engine job {job_id}")


def _write_ingest_status(workspace: Path, config: Mapping[str, Any], result: Mapping[str, Any], *, rclone: str) -> str | None:
    ingest = config.get("ingest", {}) if isinstance(config.get("ingest"), Mapping) else {}
    control = config.get("control", {}) if isinstance(config.get("control"), Mapping) else {}
    target = str(ingest.get("status_target") or control.get("status_target") or "").strip()
    if not target:
        return None
    status_path = _state_dir(workspace) / f"ingest-{result.get('id', 'unknown')}.json"
    status_path.write_text(json.dumps({"version": 1, "timestamp": datetime.now(timezone.utc).isoformat(), "action": "ingest", **dict(result)}, indent=2) + "\n", encoding="utf-8")
    _run_rclone(["copyto", str(status_path), f"{target.rstrip('/')}/{status_path.name}"], executable=rclone)
    return str(status_path)


def process_approved_ingest(root: str | Path, *, config: Mapping[str, Any], rclone: str = "rclone", dry_run: bool = False) -> dict[str, Any]:
    workspace = Path(root).expanduser().resolve()
    ingest = config.get("ingest", {})
    if not isinstance(ingest, Mapping) or not ingest.get("enabled", False):
        return {"enabled": False, "processed": [], "skipped": []}
    remote_queue = str(ingest.get("remote_queue") or "").strip()
    if not remote_queue:
        raise ValueError("ingest.remote_queue is required when approved ingest is enabled")
    state = _state_dir(workspace)
    processed_path = state / "processed-ingest.json"
    try:
        processed = json.loads(processed_path.read_text(encoding="utf-8")) if processed_path.exists() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid processed ingest state: {processed_path}") from exc
    if not isinstance(processed, dict):
        processed = {}
    with tempfile.TemporaryDirectory(prefix="textstrata-control-") as temp:
        queue_path = Path(temp) / "queue.json"
        _run_rclone(["copyto", remote_queue, str(queue_path)], executable=rclone, dry_run=dry_run)
        if dry_run:
            return {"enabled": True, "processed": [], "skipped": [], "dry_run": True, "remote_queue": remote_queue}
        try:
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("remote ingest queue is not valid JSON") from exc
    items = queue.get("items", []) if isinstance(queue, dict) else []
    if not isinstance(items, list):
        raise ValueError("remote ingest queue items must be a list")
    completed: list[dict[str, Any]] = []
    skipped: list[str] = []
    for raw in items:
        if not isinstance(raw, dict):
            skipped.append("<invalid>")
            continue
        item_id = str(raw.get("id") or "").strip()
        if not item_id or str(raw.get("status") or "").casefold() != "approved" or item_id in processed:
            skipped.append(item_id or "<invalid>")
            continue
        try:
            job_id, base_url, response = _queue_item(workspace, config, raw)
            result = {"id": item_id, "job_id": job_id, "status": "submitted", "submitted_at": datetime.now(timezone.utc).isoformat(), "base_url": base_url}
            if job_id is None:
                result["status"] = "completed" if response.get("published", True) else "failed"
                result["item_id"] = response.get("item_id")
            else:
                result["job"] = _wait_for_job(base_url, job_id)
                result["status"] = "completed"
            processed[item_id] = result
            completed.append(result)
        except (RuntimeError, ValueError) as exc:
            result = {"id": item_id, "status": "failed", "error": str(exc), "failed_at": datetime.now(timezone.utc).isoformat()}
            processed[item_id] = result
            completed.append(result)
    processed_path.write_text(json.dumps(processed, indent=2) + "\n", encoding="utf-8")
    for result in completed:
        if result.get("status") in {"completed", "failed"}:
            result["status_path"] = _write_ingest_status(workspace, config, result, rclone=rclone)
    return {"enabled": True, "processed": completed, "skipped": skipped, "state_path": str(processed_path)}


def control_doctor(root: str | Path, *, config: Mapping[str, Any], config_file: Path, rclone: str = "rclone") -> dict[str, Any]:
    workspace = Path(root).expanduser().resolve()
    backup = config.get("backup", {}) if isinstance(config.get("backup"), Mapping) else {}
    ingest = config.get("ingest", {}) if isinstance(config.get("ingest"), Mapping) else {}
    return {
        "workspace": str(workspace),
        "config": str(config_file),
        "config_exists": config_file.exists(),
        "rclone": shutil.which(rclone) is not None,
        "backup_enabled": bool(backup.get("enabled", True)),
        "backup_target_configured": bool(str(backup.get("target") or "").strip()),
        "ingest_enabled": bool(ingest.get("enabled", False)),
        "remote_queue_configured": bool(str(ingest.get("remote_queue") or "").strip()),
        "credentials_external": True,
    }
