"""Compatibility gateway for the original TextStrata acquisition engine.

The original service remains the deterministic converter and job runner for
URLs, YouTube, channels, and rich document uploads. TextStrata imports completed
items into its typed store and owns presentation, policy, linking, and MCP.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import yaml

from .ingest import ingest_text
from .store import TextStrataStore


class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class GatewayResponse:
    status: int
    content_type: str
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


class CompatibilityGateway:
    ALLOWED = {
        ("GET", "/api/library"),
        ("GET", "/api/queue"),
        ("GET", "/api/trash"),
        ("GET", "/api/maintenance/settings"),
        ("POST", "/api/ingest"),
        ("POST", "/api/queue/clear-completed"),
        ("POST", "/api/trash/empty"),
        ("POST", "/api/maintenance/restart"),
        ("POST", "/api/maintenance/settings"),
    }
    PREFIXES = {
        ("GET", "/api/item/"),
        ("DELETE", "/api/item/"),
        ("POST", "/api/queue/"),
        ("DELETE", "/api/queue/"),
        ("POST", "/api/trash/"),
        ("DELETE", "/api/trash/"),
        ("POST", "/api/channel/"),
    }

    def __init__(self, base_url: str | None = None, timeout: float = 60.0) -> None:
        value = (base_url or os.environ.get("TEXTSTRATA_UPSTREAM_URL") or os.environ.get("MARKBASE_UPSTREAM_URL") or "").rstrip("/")
        if not value:
            raise ValueError("TEXTSTRATA_UPSTREAM_URL is required when the compatibility gateway is enabled")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("TEXTSTRATA_UPSTREAM_URL must be an HTTP(S) origin without credentials")
        self.base_url = value
        self.timeout = timeout

    def _allowed(self, method: str, path: str) -> bool:
        clean = path.split("?", 1)[0]
        return (method, clean) in self.ALLOWED or any(method == m and clean.startswith(prefix) for m, prefix in self.PREFIXES)

    def request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None) -> GatewayResponse:
        method = method.upper()
        if not path.startswith("/api/") or not self._allowed(method, path):
            raise GatewayError("gateway-route-denied", "The requested compatibility route is not allowlisted.", 403)
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        req = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as response:
                return GatewayResponse(response.status, response.headers.get("Content-Type", "application/json"), response.read())
        except HTTPError as exc:
            payload = exc.read()
            message = payload.decode("utf-8", errors="replace")[:1000] or str(exc)
            raise GatewayError("upstream-request-rejected", message, exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GatewayError("upstream-unavailable", f"The TextStrata acquisition engine is unavailable: {exc}", 502) from exc

    def json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        return self.request(method, path, body, "application/json" if body is not None else None).json()

    def sync(self, store: TextStrataStore) -> dict[str, int]:
        index = self.json("GET", "/api/library")
        state_path = store.root / "upstream-sync.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        imported = 0
        unchanged = 0
        next_state: dict[str, dict[str, str]] = {}
        for meta in index.get("items", []):
            path = str(meta.get("path") or "").strip("/")
            if not path:
                continue
            fingerprint = hashlib.sha256(json.dumps(meta, sort_keys=True).encode("utf-8")).hexdigest()
            item_id = "textstrata." + _slug(str(meta.get("id") or path))
            next_state[path] = {"fingerprint": fingerprint, "item_id": item_id}
            prior = state.get(path)
            prior_fingerprint = prior.get("fingerprint") if isinstance(prior, dict) else prior
            if prior_fingerprint == fingerprint and store.normalized_path_for_id(item_id):
                unchanged += 1
                continue
            detail = self.json("GET", "/api/item/" + quote(path, safe="/"))
            markdown = str(detail.get("markdown") or "")
            tags = ["textstrata", str(meta.get("source_type") or "reference").replace("_", "-")]
            tags.extend(str(tag) for tag in meta.get("tags", []) if str(tag).strip())
            front = {
                "id": item_id,
                "title": str(meta.get("title") or meta.get("id") or "Imported TextStrata item"),
                "type": "reference",
                "tags": list(dict.fromkeys(tags)),
                "preservation": "preserve_exact",
                "created_via": "textstrata-compatibility-gateway",
                "contributor_chain": "via_script",
                "source_url": meta.get("source_url"),
                "upstream_path": path,
                "upstream_source_type": meta.get("source_type"),
                "upstream_date_ingested": meta.get("date_ingested"),
            }
            raw = "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip() + "\n---\n\n" + markdown
            result = ingest_text(store, raw, fallback_id=item_id)
            if result.published:
                imported += 1
        removed = 0
        for old_path, old_value in state.items():
            if old_path in next_state:
                continue
            old_id = old_value.get("item_id") if isinstance(old_value, dict) else None
            if old_id and store.normalized_path_for_id(old_id):
                store.trash_item(old_id)
                removed += 1
        store._atomic_write(state_path, json.dumps(next_state, indent=2, sort_keys=True) + "\n")
        return {"imported": imported, "unchanged": unchanged, "removed": removed, "total": len(next_state)}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "item"
