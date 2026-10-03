"""Signed Apps Script transport for the TextStrata Inbox and Library mirror."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from . import frontmatter
from .google_drive import _load_state, _markdown, _save_state, _substitute, default_config_path
from .ingest import _update_text, ingest_text, build_item
from .sources import NormalizedSourceRecord, normalize_text
from .store import TextStrataStore

PROTOCOL_VERSION = 1


class BridgeError(RuntimeError):
    """A bridge failure with a safe, stable error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BridgeConfig:
    endpoint: str
    secret: str


def load_bridge_config(path: str | Path | None = None, *, environ: Mapping[str, str] | None = None) -> BridgeConfig:
    """Load private bridge settings; the secret comes only from the environment."""
    env = os.environ if environ is None else environ
    selected = Path(path).expanduser().resolve() if path else default_config_path(env)
    try:
        values = yaml.safe_load(selected.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BridgeError("INVALID_CONFIGURATION") from exc
    section = values.get("google_bridge", {}) if isinstance(values, dict) else {}
    if not isinstance(section, dict):
        raise BridgeError("INVALID_CONFIGURATION")
    endpoint = str(_substitute(section.get("endpoint", ""), env)).strip()
    secret = env.get("TEXTSTRATA_GOOGLE_BRIDGE_SECRET", "")
    if not endpoint.startswith("https://") or "${" in endpoint or not secret:
        raise BridgeError("INVALID_CONFIGURATION")
    return BridgeConfig(endpoint, secret)


def canonical_string(version: int, action: str, timestamp: int, nonce: str, payload_json: str) -> str:
    """Stable wire signing string shared with Apps Script."""
    return f"{version}\n{action}\n{timestamp}\n{nonce}\n{payload_json}"


def signed_envelope(action: str, payload: Mapping[str, Any], secret: str, *, timestamp: int | None = None, nonce: str | None = None) -> dict[str, Any]:
    stamp = int(time.time()) if timestamp is None else timestamp
    unique = nonce or secrets.token_hex(16)
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    message = canonical_string(PROTOCOL_VERSION, action, stamp, unique, payload_json)
    return {"version": PROTOCOL_VERSION, "action": action, "timestamp": stamp, "nonce": unique, "payload_json": payload_json, "signature": hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()}


class BridgeClient:
    """A narrow signed client; responses never expose credentials in errors."""

    def __init__(self, config: BridgeConfig, *, transport: Callable[[str, bytes], Mapping[str, Any]] | None = None) -> None:
        self.config = config
        self.transport = transport or self._post

    @staticmethod
    def _post(url: str, body: bytes) -> Mapping[str, Any]:
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            action = json.loads(body).get("action")
        except (ValueError, AttributeError):
            action = None
        timeout = 300 if action in {"mirror_upsert", "complete_library_import", "fetch_library_revision"} else 45
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise BridgeError("BRIDGE_UNAVAILABLE") from exc
        if not isinstance(result, dict):
            raise BridgeError("INVALID_RESPONSE")
        return result

    def call(self, action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if action not in {"ping", "list_ready", "fetch_source", "ack", "mirror_status", "mirror_upsert", "list_library_updates", "fetch_library_revision", "complete_library_import", "mark_library_conflict"}:
            raise BridgeError("UNKNOWN_ACTION")
        envelope = signed_envelope(action, payload or {}, self.config.secret)
        body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result = self.transport(self.config.endpoint, body)
        if not isinstance(result, Mapping):
            raise BridgeError("INVALID_RESPONSE")
        if result.get("ok") is not True:
            raise BridgeError(str(result.get("code") or "BRIDGE_ERROR"))
        payload_result = result.get("result")
        if not isinstance(payload_result, Mapping):
            raise BridgeError("INVALID_RESPONSE")
        return dict(payload_result)


def ingest_bridge(store: TextStrataStore, client: BridgeClient, *, dry_run: bool = False, catalog: Any | None = None) -> list[dict[str, str]]:
    """Ingest only manifest-listed Inbox records, with independent ACK retry."""
    state = _load_state(store)
    listed = client.call("list_ready").get("records", [])
    if not isinstance(listed, list):
        raise BridgeError("INVALID_RESPONSE")
    results: list[dict[str, str]] = []
    for listed_record in listed:
        try:
            if not isinstance(listed_record, dict):
                raise BridgeError("INVALID_RESPONSE")
            source_id = str(listed_record.get("id", ""))
            doc_id = str(listed_record.get("doc_id", ""))
            if not source_id or not doc_id or str(listed_record.get("status", "")).casefold() not in {"ready", "updated"}:
                raise BridgeError("INVALID_RESPONSE")
            fetched = client.call("fetch_source", {"id": source_id, "expected_doc_id": doc_id})
            data = fetched.get("record", {})
            if not isinstance(data, dict) or data.get("id") != source_id or data.get("doc_id") != doc_id or data.get("origin") == "textstrata-library":
                raise BridgeError("INVALID_SOURCE")
            record = NormalizedSourceRecord("google_bridge", source_id, doc_id, str(data.get("title") or source_id), str(data.get("content") or ""), tags=tuple(data.get("tags") or ()), topic=data.get("topic"), created_at=data.get("created"), updated_at=data.get("updated"), source_metadata={"origin": "textstrata-inbox", "manifest_status": str(listed_record["status"])})
            item_id = re.sub(r"[^a-z0-9._-]+", "-", source_id.casefold()).strip("-")
            previous = state.get(item_id, {})
            existing = store.normalized_path_for_id(item_id)
            content_action = "UNCHANGED" if existing and previous.get("content_hash") == record.content_hash and previous.get("google_doc_id") == doc_id else "UPDATE" if existing else "NEW"
            outcome = {"record": source_id, "content": content_action, "ack": "PENDING", "action": content_action + " + ACK"}
            if not dry_run:
                if content_action != "UNCHANGED":
                    raw = _markdown(record)
                    published = _update_text(store, raw, fallback_id=item_id) if existing else ingest_text(store, raw, fallback_id=item_id)
                    if not published.published:
                        raise BridgeError("VALIDATION_FAILED")
                    if catalog is not None:
                        catalog.index_item(published.item)
                    state[item_id] = {"remote_source_id": source_id, "google_doc_id": doc_id, "content_hash": record.content_hash, "last_successful_ingestion": datetime.now(timezone.utc).isoformat(), "local_item_id": item_id}
                    _save_state(store, state)
                try:
                    client.call("ack", {"id": source_id, "doc_id": doc_id, "expected_status": listed_record["status"], "hash": record.content_hash})
                    outcome["ack"] = "OK"
                except BridgeError as exc:
                    outcome["ack"] = exc.code
            results.append(outcome)
        except BridgeError as exc:
            results.append({"record": str(listed_record.get("id", "")) if isinstance(listed_record, dict) else "", "action": "ERROR", "error": exc.code})
    return results


def _library_revision(remote: Mapping[str, Any], item_id: str) -> tuple[str, Any]:
    """Apply editable Library metadata to the mirrored canonical Markdown."""
    content = remote.get("content")
    if not isinstance(content, str):
        raise BridgeError("INVALID_RESPONSE")
    fm = frontmatter.parse(content)
    if fm.block_count != 1 or str(fm.data.get("id", "")) != item_id:
        raise BridgeError("LIBRARY_CONFLICT")
    data = fm.data
    data["title"] = str(remote.get("title") or data.get("title") or item_id)
    data["tags"] = [tag.strip() for tag in re.split(r"[,;]", str(remote.get("tags") or "")) if tag.strip()]
    extra = data.get("extra")
    if not isinstance(extra, dict):
        extra = {}
    extra["topic"] = str(remote.get("topic") or "")
    data["extra"] = extra
    raw = frontmatter.render(data, fm.body)
    item, _, _ = build_item(raw, fallback_id=item_id)
    if item.id != item_id:
        raise BridgeError("LIBRARY_CONFLICT")
    canonical = normalize_text(frontmatter.render(item.canonical_frontmatter(), item.body))
    return raw, (canonical, item)


def import_library_bridge(store: TextStrataStore, client: BridgeClient, *, dry_run: bool = False, catalog: Any | None = None) -> list[dict[str, str]]:
    """Import explicitly flagged Library revisions without overwriting local edits."""
    listed = client.call("list_library_updates").get("records", [])
    if not isinstance(listed, list):
        raise BridgeError("INVALID_RESPONSE")
    results: list[dict[str, str]] = []
    excluded = {value.strip() for value in os.environ.get("TEXTSTRATA_MIRROR_EXCLUDE_IDS", "").split(",") if value.strip()}
    for row in listed:
        item_id = str(row.get("id", "")) if isinstance(row, dict) else ""
        if item_id in excluded:
            continue
        try:
            if not isinstance(row, dict) or not re.fullmatch(r"[A-Za-z0-9._-]+", item_id) or not row.get("doc_id") or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("hash", ""))):
                raise BridgeError("INVALID_RESPONSE")
            expected = {"id": item_id, "expected_doc_id": row["doc_id"], "expected_hash": row["hash"]}
            remote = client.call("fetch_library_revision", expected).get("record")
            if not isinstance(remote, dict) or remote.get("id") != item_id or remote.get("doc_id") != row["doc_id"] or remote.get("hash") != row["hash"] or not re.fullmatch(r"[a-f0-9]{64}", str(remote.get("fingerprint", ""))):
                raise BridgeError("INVALID_RESPONSE")
            precondition = {**expected, "expected_fingerprint": remote["fingerprint"]}
            try:
                raw, (canonical, item) = _library_revision(remote, item_id)
            except BridgeError as exc:
                if exc.code != "LIBRARY_CONFLICT":
                    raise
                if not dry_run:
                    client.call("mark_library_conflict", precondition)
                results.append({"record": item_id, "action": "CONFLICT"})
                continue
            except (ValueError, yaml.YAMLError):
                if not dry_run:
                    client.call("mark_library_conflict", precondition)
                results.append({"record": item_id, "action": "CONFLICT"})
                continue
            desired_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            path = store.normalized_path_for_id(item_id)
            if path is None:
                action = "CONFLICT"
            elif dry_run:
                local_hash = hashlib.sha256(normalize_text(path.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()
                action = "IMPORT" if local_hash == row["hash"] else "RETRY" if local_hash == desired_hash else "CONFLICT"
            else:
                with store.item_lock(item_id):
                    local_hash = hashlib.sha256(normalize_text(path.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()
                    action = "IMPORT" if local_hash == row["hash"] else "RETRY" if local_hash == desired_hash else "CONFLICT"
                    if action == "IMPORT":
                        published = _update_text(store, raw, fallback_id=item_id)
                        if not published.published:
                            raise BridgeError("VALIDATION_FAILED")
                        if catalog is not None:
                            catalog.index_item(published.item)
                        actual_hash = hashlib.sha256(normalize_text(path.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()
                        if actual_hash != desired_hash:
                            raise BridgeError("LOCAL_DOCUMENT_ERROR")
                    if action != "CONFLICT":
                        client.call("complete_library_import", {**precondition, "title": item.title, "content": canonical, "hash": desired_hash, "updated": datetime.now(timezone.utc).isoformat(), "topic": str(item.extra.get("topic") or ""), "tags": item.tags, "source": str(item.provenance.source_kind or remote.get("source") or "local")})
            if action == "CONFLICT":
                if not dry_run:
                    client.call("mark_library_conflict", precondition)
                results.append({"record": item_id, "action": "CONFLICT"})
                continue
            results.append({"record": item_id, "action": action})
        except (BridgeError, OSError, ValueError) as exc:
            results.append({"record": item_id, "action": "ERROR", "error": exc.code if isinstance(exc, BridgeError) else "LOCAL_DOCUMENT_ERROR"})
    return results


def mirror_bridge(store: TextStrataStore, client: BridgeClient, *, dry_run: bool = False, catalog: Any | None = None) -> list[dict[str, str]]:
    """Import flagged Library edits, then mirror canonical local files."""
    results = import_library_bridge(store, client, dry_run=dry_run, catalog=catalog)
    blocked = {result["record"] for result in results if result["action"] in {"CONFLICT", "ERROR"} or dry_run and result["action"] in {"IMPORT", "RETRY"}}
    excluded = {value.strip() for value in os.environ.get("TEXTSTRATA_MIRROR_EXCLUDE_IDS", "").split(",") if value.strip()}
    for path in store.normalized_paths():
        try:
            canonical = normalize_text(path.read_text(encoding="utf-8"))
            item, _, _ = build_item(canonical, fallback_id=path.stem)
            if item.id in excluded or item.id in blocked:
                continue
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            remote = client.call("mirror_status", {"id": item.id}).get("record")
            if remote is not None and not isinstance(remote, dict):
                raise BridgeError("INVALID_RESPONSE")
            if remote is not None and remote.get("status") not in {None, "Active", "Pending"}:
                results.append({"record": item.id, "action": "PENDING"})
                continue
            action = "CREATE" if remote is None else "UNCHANGED" if remote.get("hash") == digest else "UPDATE"
            if action != "UNCHANGED" and not dry_run:
                client.call("mirror_upsert", {"id": item.id, "title": item.title, "content": canonical, "hash": digest, "topic": str(item.extra.get("topic") or ""), "tags": item.tags, "source": str(item.provenance.source_kind or "local"), "updated": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
            results.append({"record": item.id, "action": action})
        except (BridgeError, OSError, ValueError) as exc:
            results.append({"record": path.stem, "action": "ERROR", "error": exc.code if isinstance(exc, BridgeError) else "LOCAL_DOCUMENT_ERROR"})
    return results
