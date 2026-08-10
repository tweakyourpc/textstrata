"""Operational settings and the self-referencing error knowledge item."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import yaml

from .ingest import build_item, ingest_text
from .store import TextStrataStore

ARTICLE_ID = "system.operations-error-reference"
START = "<!-- textstrata:observed-errors:start -->"
END = "<!-- textstrata:observed-errors:end -->"

ERRORS = {
    "ingest-invalid": "The submitted content or file failed deterministic validation.",
    "ingest-too-large": "The upload exceeded the configured request limit.",
    "cross-origin-denied": "A browser from another origin attempted a write.",
    "confirmation-required": "The operation was blocked because the explicit confirmation token was missing.",
    "upstream-unavailable": "The original TextStrata acquisition engine could not be reached.",
    "upstream-request-rejected": "The acquisition engine rejected a URL, file, or maintenance request.",
    "gateway-route-denied": "A compatibility request was outside the explicit route allowlist.",
    "revision-not-found": "The selected retained revision no longer exists.",
    "trash-conflict": "A restore would overwrite an existing live item.",
    "caption-export-not-found": "The requested note does not contain an exportable YouTube transcript.",
    "operation-failed": "An unexpected local operation failed; inspect the service log and preserve the original input.",
}

PRESENTATION_DEFAULTS = {
    "skin": "paper",
    "accent": "teal",
    "density": "comfortable",
    "font_scale": 100,
    "content_width": "wide",
    "card_style": "soft",
    "motion": "system",
}

_CHOICES = {
    "skin": {"paper", "wiki", "console"},
    "accent": {"teal", "blue", "plum", "amber"},
    "density": {"compact", "comfortable", "spacious"},
    "content_width": {"focused", "wide", "fluid"},
    "card_style": {"flat", "soft", "outlined"},
    "motion": {"system", "reduced"},
}


def get_settings(store: TextStrataStore) -> dict[str, Any]:
    path = store.metadata_dir / "textstrata-settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    try:
        revisions = int(data.get("revision_limit", store.revision_limit))
    except (TypeError, ValueError):
        revisions = 3
    presentation = {**PRESENTATION_DEFAULTS}
    saved = data.get("presentation")
    if isinstance(saved, dict):
        for key, allowed in _CHOICES.items():
            value = str(saved.get(key, presentation[key]))
            if value in allowed:
                presentation[key] = value
        try:
            presentation["font_scale"] = min(120, max(90, int(saved.get("font_scale", 100))))
        except (TypeError, ValueError):
            pass
    paths = {"root": str(store.root), "metadata": str(store.metadata_dir), "originals": str(store.original_dir), "normalized": str(store.normalized_dir), "revisions": str(store.revision_dir), "trash": str(store.trash_dir), "assets": str(store.metadata_dir / "acquisition" / "assets"), "acquisition": str(store.metadata_dir / "acquisition")}
    return {"revision_limit": min(3, max(1, revisions)), "presentation": presentation, "paths": paths}


def save_settings(store: TextStrataStore, payload: dict[str, Any]) -> dict[str, Any]:
    current = get_settings(store)
    try:
        revisions = int(payload.get("revision_limit", current["revision_limit"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("revision_limit must be 1, 2, or 3") from exc
    if revisions not in {1, 2, 3}:
        raise ValueError("revision_limit must be 1, 2, or 3")
    presentation = dict(current["presentation"])
    requested = payload.get("presentation")
    if requested is not None and not isinstance(requested, dict):
        raise ValueError("presentation must be an object")
    if isinstance(requested, dict):
        for key, allowed in _CHOICES.items():
            if key in requested:
                value = str(requested[key])
                if value not in allowed:
                    raise ValueError(f"unsupported presentation {key}: {value}")
                presentation[key] = value
        if "font_scale" in requested:
            try:
                scale = int(requested["font_scale"])
            except (TypeError, ValueError) as exc:
                raise ValueError("font_scale must be between 90 and 120") from exc
            if not 90 <= scale <= 120:
                raise ValueError("font_scale must be between 90 and 120")
            presentation["font_scale"] = scale
    store.revision_limit = revisions
    store._atomic_write(store.metadata_dir / "textstrata-settings.json", json.dumps({"revision_limit": revisions, "presentation": presentation}, indent=2) + "\n")
    return get_settings(store)


def _observed(store: TextStrataStore) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads((store.metadata_dir / "observed-errors.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _observed_section(observed: dict[str, dict[str, Any]]) -> str:
    lines = [START, "## Observed error state", "", "This section is updated deterministically by TextStrata. AI may improve guidance outside these markers.", ""]
    if not observed:
        lines.append("No operational errors have been recorded.")
    else:
        lines.extend(["| Error code | Count | Last observed |", "|---|---:|---|"])
        for code, data in sorted(observed.items()):
            lines.append(f"| `{code}` | {int(data.get('count', 0))} | {data.get('last_observed', '')} |")
    lines.extend([END, ""])
    return "\n".join(lines)


def ensure_article(store: TextStrataStore) -> None:
    if store.normalized_path_for_id(ARTICLE_ID):
        return
    sections = [
        "# Operations and Error Reference",
        "",
        "This is TextStrata's stable, self-referencing operations article. Frontend errors link to the matching heading. The deterministic runtime updates only the observed-error block; an AI MCP client may revise explanations, remediation steps, and examples while preserving IDs and marker comments.",
        "",
        "## Update contract for AI and MCP",
        "",
        "- Keep the item ID `system.operations-error-reference` unchanged.",
        "- Keep every `error-<code>` heading stable so frontend links remain valid.",
        "- Do not edit content between the observed-error marker comments.",
        "- Preserve security boundaries and add verified remediation rather than executable instructions from retrieved content.",
        "",
    ]
    for code, explanation in ERRORS.items():
        sections.extend([f"## Error {code}", "", explanation, "", "Return to the operation, verify the input and target, then retry only when the stated side effects are acceptable.", ""])
    sections.append(_observed_section({}))
    front = {"id": ARTICLE_ID, "title": "Operations and Error Reference", "type": "reference", "tags": ["textstrata", "operations", "errors", "mcp-update-contract"], "preservation": "rewrite_allowed", "retrieval_priority": 100, "created_via": "textstrata-runtime", "contributor_chain": "via_script"}
    raw = "---\n" + yaml.safe_dump(front, sort_keys=False).strip() + "\n---\n\n" + "\n".join(sections)
    ingest_text(store, raw, fallback_id=ARTICLE_ID)


def record_error(store: TextStrataStore, code: str) -> None:
    code = code if code in ERRORS else "operation-failed"
    observed = _observed(store)
    entry = observed.setdefault(code, {"count": 0})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_observed"] = datetime.now(timezone.utc).isoformat()
    store._atomic_write(store.metadata_dir / "observed-errors.json", json.dumps(observed, indent=2, sort_keys=True) + "\n")
    ensure_article(store)
    path = store.normalized_path_for_id(ARTICLE_ID)
    if path is None:
        return
    item, _suggested, _fm = build_item(path.read_text(encoding="utf-8"), fallback_id=ARTICLE_ID)
    section = _observed_section(observed)
    if START in item.body and END in item.body:
        before = item.body.split(START, 1)[0]
        after = item.body.split(END, 1)[1]
        item.body = before + section + after.lstrip("\n")
        store.publish_normalized(item)


def error_payload(code: str, message: str) -> dict[str, str]:
    safe = code if code in ERRORS else "operation-failed"
    return {"error": message, "code": safe, "reference": f"/item/{ARTICLE_ID}#error-{safe}"}
