"""Read-only Google Drive and Google Docs source adapter.

The Google client is deliberately injected at the boundary. Production uses
the official Google client libraries; tests use small fake resources and never
need network access or credentials.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from .ingest import _update_text, ingest_text
from .sources import NormalizedSourceRecord, SourceAdapter, normalize_text
from .store import TextStrataStore


READ_SCOPES = ("https://www.googleapis.com/auth/documents.readonly", "https://www.googleapis.com/auth/spreadsheets.readonly")
WRITE_SCOPES = ("https://www.googleapis.com/auth/documents.readonly", "https://www.googleapis.com/auth/spreadsheets")
DEFAULT_WORKSHEET = "Manifest"
REQUIRED_COLUMNS = ("id", "doc_id", "status")
STATUS_VALUES = {"ready", "updated"}
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class GoogleDriveError(RuntimeError):
    """Redacted, user-facing adapter error."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


@dataclass(frozen=True)
class GoogleDriveConfig:
    manifest_id: str
    worksheet: str = DEFAULT_WORKSHEET
    eligible_statuses: frozenset[str] = frozenset(STATUS_VALUES)
    writeback_enabled: bool = False
    success_status: str = "ingested"
    config_path: Path | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, path: Path | None = None) -> "GoogleDriveConfig":
        manifest_id = str(value.get("spreadsheet_id") or value.get("manifest_id") or "").strip()
        if not manifest_id:
            raise GoogleDriveError("INVALID_CONFIGURATION", "spreadsheet_id is required")
        if "${" in manifest_id:
            raise GoogleDriveError("INVALID_CONFIGURATION", "spreadsheet_id environment variable is not set")
        statuses = value.get("eligible_statuses", value.get("statuses", ["Ready", "Updated"]))
        if isinstance(statuses, str):
            statuses = [statuses]
        if not isinstance(statuses, list) or not statuses:
            raise GoogleDriveError("INVALID_CONFIGURATION", "eligible_statuses must contain at least one status")
        writeback = value.get("writeback", {})
        if not isinstance(writeback, Mapping):
            writeback = {}
        return cls(
            manifest_id,
            str(value.get("worksheet") or DEFAULT_WORKSHEET),
            frozenset(str(s).strip().casefold() for s in statuses),
            bool(writeback.get("enabled", False)),
            str(writeback.get("success_status") or "Ingested").strip().casefold(),
            path,
        )


def _substitute(value: Any, environ: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _substitute(item, environ) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, environ) for item in value]
    if isinstance(value, str):
        return _ENV_RE.sub(lambda match: environ.get(match.group(1), match.group(0)), value)
    return value


def load_google_config(path: str | Path, *, environ: Mapping[str, str] | None = None) -> GoogleDriveConfig:
    selected = Path(path).expanduser().resolve()
    try:
        raw = yaml.safe_load(selected.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise GoogleDriveError("INVALID_CONFIGURATION", "could not read source configuration") from exc
    if not isinstance(raw, dict):
        raise GoogleDriveError("INVALID_CONFIGURATION", "source configuration must be a mapping")
    google = raw.get("google_drive", raw.get("google-drive", raw))
    if not isinstance(google, dict):
        raise GoogleDriveError("INVALID_CONFIGURATION", "google_drive configuration must be a mapping")
    return GoogleDriveConfig.from_mapping(_substitute(google, environ or os.environ), path=selected)


def default_config_path(environ: Mapping[str, str] | None = None) -> Path:
    env = environ or os.environ
    return Path(env.get("TEXTSTRATA_SOURCES_CONFIG") or "config/sources.yaml").expanduser().resolve()


def _redact_error(exc: Exception) -> str:
    message = str(exc)
    message = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", message, flags=re.I)
    message = re.sub(r"(token|client_secret|refresh_token|access_token)[=:]\s*[^,\s]+", r"\1=[REDACTED]", message, flags=re.I)
    return message[:240]


def _validate_oauth_scopes(granted: Any, required: tuple[str, ...] = WRITE_SCOPES) -> None:
    if set(granted or ()) < set(required):
        raise GoogleDriveError(
            "AUTHENTICATION_SCOPE_MISSING",
            "cached OAuth token lacks Sheets write access; delete the token or set TEXTSTRATA_GOOGLE_REAUTH=1 and authorize again",
        )


def _parse_datetime(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value).strip() or None


def _column_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().casefold()).strip("_")


def _column_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _sheet_range(worksheet: str, cell_range: str) -> str:
    return f"'{worksheet.replace(chr(39), chr(39) * 2)}'!{cell_range}"


def _as_tags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(item.strip() for item in re.split(r"[,;]", str(value)) if item.strip())


def parse_manifest(values: list[list[Any]], eligible_statuses: frozenset[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a Sheets values response, rejecting bad rows independently."""
    if not values:
        raise GoogleDriveError("MISSING_COLUMN", "manifest is empty")
    headers = [_column_key(item) for item in values[0]]
    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing:
        raise GoogleDriveError("MISSING_COLUMN", ", ".join(missing))
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for row_number, raw in enumerate(values[1:], 2):
        data = {headers[index]: raw[index] if index < len(raw) else "" for index in range(len(headers))}
        data["_row_number"] = row_number
        record_id = str(data.get("id", "")).strip()
        doc_id = str(data.get("doc_id", "")).strip()
        status = str(data.get("status", "")).strip().casefold()
        if not record_id or not doc_id or not re.fullmatch(r"[A-Za-z0-9._-]+", record_id):
            errors.append(f"row={row_number} code=MALFORMED_ROW")
            continue
        if status not in eligible_statuses:
            continue
        rows.append(data)
    return rows, errors


def extract_google_doc(document: Mapping[str, Any]) -> str:
    """Extract meaningful paragraph and table text from a Docs API response."""
    chunks: list[str] = []
    def walk(elements: list[Mapping[str, Any]]) -> None:
        for element in elements:
            paragraph = element.get("paragraph")
            if isinstance(paragraph, Mapping):
                text = "".join(str(run.get("textRun", {}).get("content", "")) for run in paragraph.get("elements", []) if isinstance(run, Mapping))
                if text.strip():
                    chunks.append(text.rstrip("\n"))
            table = element.get("table")
            if isinstance(table, Mapping):
                for row in table.get("tableRows", []):
                    if isinstance(row, Mapping):
                        for cell in row.get("tableCells", []):
                            if isinstance(cell, Mapping):
                                walk(cell.get("content", []))
    body = document.get("body", {})
    if isinstance(body, Mapping):
        walk(body.get("content", []))
    return normalize_text("\n\n".join(chunks))


class GoogleDriveAdapter(SourceAdapter):
    """Read manifest rows and Google Docs through injected service objects."""

    def __init__(self, config: GoogleDriveConfig, sheets: Any, docs: Any) -> None:
        self.config, self.sheets, self.docs = config, sheets, docs
        self.row_errors: list[str] = []
        self.record_errors: list[dict[str, str]] = []

    def discover(self) -> list[NormalizedSourceRecord]:
        self.row_errors = []
        self.record_errors = []
        try:
            response = self.sheets.spreadsheets().values().get(spreadsheetId=self.config.manifest_id, range=self.config.worksheet).execute()
            values = response.get("values", [])
        except Exception as exc:
            message = _redact_error(exc)
            code = "WORKSHEET_NOT_FOUND" if "range" in message.casefold() or "worksheet" in message.casefold() else "MANIFEST_NOT_FOUND"
            raise GoogleDriveError(code, message) from exc
        rows, errors = parse_manifest(values, self.config.eligible_statuses)
        self.row_errors = errors
        records: list[NormalizedSourceRecord] = []
        for row in rows:
            try:
                records.append(self._record(row))
            except GoogleDriveError as exc:
                self.record_errors.append({"record": str(row.get("id", "")), "doc_id": str(row.get("doc_id", "")), "error": exc.code})
        return records

    def _record(self, row: Mapping[str, Any]) -> NormalizedSourceRecord:
        return self.fetch(str(row["doc_id"]).strip(), row=row)

    def fetch(self, external_id: str, *, row: Mapping[str, Any] | None = None) -> NormalizedSourceRecord:
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", external_id):
            raise GoogleDriveError("INVALID_DOCUMENT_ID", "document id format is invalid")
        try:
            document = self.docs.documents().get(documentId=external_id).execute()
        except Exception as exc:
            message = _redact_error(exc).casefold()
            code = "DOCUMENT_ACCESS_DENIED" if "403" in message or "permission" in message or "access" in message else "DOCUMENT_NOT_FOUND"
            raise GoogleDriveError(code, _redact_error(exc)) from exc
        source = row or {"doc_id": external_id, "id": external_id}
        content = extract_google_doc(document)
        title = str(source.get("title") or document.get("title") or external_id).strip()
        return NormalizedSourceRecord(
            source_type="google_drive",
            source_id=str(source.get("id") or external_id).strip(),
            external_id=external_id,
            title=title,
            content=content,
            created_at=_parse_datetime(source.get("created")),
            updated_at=_parse_datetime(source.get("updated")),
            tags=_as_tags(source.get("tags")),
            topic=str(source.get("topic") or "").strip() or None,
            source_metadata={
                "manifest_id": self.config.manifest_id,
                "worksheet": self.config.worksheet,
                "manifest_row": int(source.get("_row_number", 0)),
                "manifest_status": str(source.get("status", "")).strip(),
            },
        )

    def normalize(self, record: Any) -> NormalizedSourceRecord:
        if not isinstance(record, NormalizedSourceRecord):
            raise TypeError("Google Drive records must be NormalizedSourceRecord instances")
        return record

    def fingerprint(self, record: NormalizedSourceRecord) -> str:
        return record.content_hash


def _token_path(environ: Mapping[str, str] | None = None) -> Path:
    env = environ or os.environ
    if env.get("TEXTSTRATA_GOOGLE_TOKEN_PATH"):
        return Path(env["TEXTSTRATA_GOOGLE_TOKEN_PATH"]).expanduser()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "TextStrata"
    elif sys.platform == "win32":
        base = Path(env.get("APPDATA", Path.home() / "AppData/Roaming")) / "TextStrata"
    else:
        base = Path(env.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "textstrata"
    return base / "google-drive-token.json"


def build_google_services(*, writeback_enabled: bool = False, environ: Mapping[str, str] | None = None) -> tuple[Any, Any]:
    """Create read-only Google services using installed-app OAuth."""
    env = environ or os.environ
    scopes = WRITE_SCOPES if writeback_enabled else READ_SCOPES
    credentials_path = Path(env.get("TEXTSTRATA_GOOGLE_CREDENTIALS_PATH", "")).expanduser() if env.get("TEXTSTRATA_GOOGLE_CREDENTIALS_PATH") else Path.home() / ".config" / "textstrata" / "google-drive-credentials.json"
    token_path = _token_path(env)
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GoogleDriveError("AUTHENTICATION_FAILED", "install the google-drive extra") from exc
    if not credentials_path.is_file():
        raise GoogleDriveError("AUTHENTICATION_FAILED", "OAuth client credentials file is missing")
    try:
        force_reauth = env.get("TEXTSTRATA_GOOGLE_REAUTH", "").strip().casefold() in {"1", "true", "yes"}
        credentials = Credentials.from_authorized_user_file(str(token_path), scopes) if token_path.is_file() and not force_reauth else None
        if credentials:
            _validate_oauth_scopes(credentials.scopes, scopes)
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
            credentials = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(credentials.to_json(), encoding="utf-8")
            token_path.chmod(0o600)
        return build("sheets", "v4", credentials=credentials), build("docs", "v1", credentials=credentials)
    except GoogleDriveError:
        raise
    except Exception as exc:
        raise GoogleDriveError("AUTHENTICATION_FAILED", _redact_error(exc)) from exc


def _state_path(store: TextStrataStore) -> Path:
    configured = os.environ.get("TEXTSTRATA_STATE_DIR", "").strip()
    return (Path(configured).expanduser() if configured else store.metadata_dir) / "google-drive-state.json"


def _load_state(store: TextStrataStore) -> dict[str, dict[str, Any]]:
    path = _state_path(store)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoogleDriveError("INVALID_STATE", "local Google Drive state is unreadable") from exc
    return value if isinstance(value, dict) else {}


def _save_state(store: TextStrataStore, state: dict[str, dict[str, Any]]) -> None:
    path = _state_path(store)
    store._atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _markdown(record: NormalizedSourceRecord) -> str:
    fields: dict[str, Any] = {
        "id": re.sub(r"[^a-z0-9._-]+", "-", record.source_id.casefold()).strip("-") or "google-document",
        "title": record.title,
        "tags": list(record.tags),
        "type": "reference",
        "provenance": {"source_kind": "google_drive", "source_identity": record.external_id, "created_via": "textstrata-google-drive"},
        "source_metadata": record.source_metadata,
    }
    if record.topic:
        fields["topic"] = record.topic
    if record.created_at:
        fields["created_at"] = record.created_at
    if record.updated_at:
        fields["updated_at"] = record.updated_at
    return "---\n" + yaml.safe_dump(fields, sort_keys=False, allow_unicode=True).rstrip() + "\n---\n\n" + record.content


def _acknowledge(adapter: GoogleDriveAdapter, record: NormalizedSourceRecord, *, timestamp: str, content_hash: str) -> str:
    """Re-read the manifest and update only the configured acknowledgment cells."""
    try:
        response = adapter.sheets.spreadsheets().values().get(
            spreadsheetId=adapter.config.manifest_id,
            range=adapter.config.worksheet,
        ).execute()
    except Exception as exc:
        raise GoogleDriveError("MANIFEST_NOT_FOUND", _redact_error(exc)) from exc
    values = response.get("values", [])
    if not values:
        raise GoogleDriveError("MISSING_COLUMN", "manifest is empty")
    headers = [_column_key(item) for item in values[0]]
    missing = [column for column in ("id", "doc_id", "status", "ingested", "hash") if column not in headers]
    if missing:
        raise GoogleDriveError("MISSING_COLUMN", ", ".join(missing))
    target: tuple[int, list[Any]] | None = None
    id_match = False
    for row_number, raw in enumerate(values[1:], 2):
        row = {headers[index]: raw[index] if index < len(raw) else "" for index in range(len(headers))}
        if str(row.get("id", "")).strip() == record.source_id:
            id_match = True
        if str(row.get("id", "")).strip() == record.source_id and str(row.get("doc_id", "")).strip() == record.external_id:
            target = (row_number, raw)
            break
    if target is None:
        code = "ACK_TARGET_MISMATCH" if id_match else "ACK_TARGET_NOT_FOUND"
        raise GoogleDriveError(code, "manifest ID and Doc ID did not match the same row")
    row_number, raw = target
    status_index = headers.index("status")
    current_status = str(raw[status_index] if status_index < len(raw) else "").strip().casefold()
    if current_status == adapter.config.success_status:
        return "ALREADY_ACKNOWLEDGED"
    if current_status not in adapter.config.eligible_statuses:
        raise GoogleDriveError("UNEXPECTED_STATUS", "manifest row is not eligible for acknowledgment")
    payload = [
        {"range": _sheet_range(adapter.config.worksheet, f"{_column_letter(headers.index('status'))}{row_number}"), "values": [[adapter.config.success_status.title()]]},
        {"range": _sheet_range(adapter.config.worksheet, f"{_column_letter(headers.index('ingested'))}{row_number}"), "values": [[timestamp]]},
        {"range": _sheet_range(adapter.config.worksheet, f"{_column_letter(headers.index('hash'))}{row_number}"), "values": [[content_hash]]},
    ]
    try:
        adapter.sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=adapter.config.manifest_id,
            body={"valueInputOption": "RAW", "data": payload},
        ).execute()
    except Exception as exc:
        raise GoogleDriveError("ACKNOWLEDGMENT_FAILED", _redact_error(exc)) from exc
    return "ACKNOWLEDGED"


def ingest_google(store: TextStrataStore, adapter: GoogleDriveAdapter, *, dry_run: bool = False, catalog: Any | None = None) -> list[dict[str, Any]]:
    """Discover and ingest eligible records with hash-based idempotency."""
    store.ensure_dirs()
    state = _load_state(store)
    results: list[dict[str, Any]] = []
    for record in adapter.discover():
        item_id = re.sub(r"[^a-z0-9._-]+", "-", record.source_id.casefold()).strip("-")
        previous = state.get(item_id, {})
        local_path_exists = store.normalized_path_for_id(item_id) is not None
        if previous.get("content_hash") == record.content_hash and local_path_exists:
            action = "UNCHANGED"
        elif previous:
            action = "UPDATE"
        else:
            action = "NEW"
        manifest_status = str(record.source_metadata.get("manifest_status", "")).strip().casefold()
        ack_needed = adapter.config.writeback_enabled and manifest_status != adapter.config.success_status
        result: dict[str, Any] = {"record": record.source_id, "doc_id": record.external_id, "action": action, "content": action, "manifest": "ACK" if ack_needed else "UNCHANGED", "content_changed": action != "UNCHANGED"}
        if action != "UNCHANGED" and not dry_run:
            raw = _markdown(record)
            published = _update_text(store, raw, fallback_id=item_id) if store.normalized_path_for_id(item_id) else ingest_text(store, raw, fallback_id=item_id)
            if not published.published:
                result.update(action="ERROR", error="VALIDATION_FAILED", details=published.validation.errors)
                action = "ERROR"
            else:
                if catalog is not None:
                    catalog.index_item(published.item)
                state[item_id] = {"remote_source_id": record.source_id, "google_doc_id": record.external_id, "content_hash": record.content_hash, "remote_updated_at": record.updated_at, "last_successful_ingestion": datetime.now(timezone.utc).isoformat(), "local_item_id": item_id}
                _save_state(store, state)
        if ack_needed and not dry_run and action != "ERROR":
            try:
                result["manifest"] = _acknowledge(adapter, record, timestamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), content_hash=record.content_hash)
            except GoogleDriveError as exc:
                result.update(manifest="ACK_ERROR", ack_error=exc.code)
        if ack_needed:
            result["action"] = f"{action} + {result['manifest']}" if action != "ERROR" else "ERROR"
        results.append(result)
    results.extend({"record": item["record"], "doc_id": item["doc_id"], "action": "ERROR", "content_changed": False, "error": item["error"]} for item in adapter.record_errors)
    if not dry_run:
        _save_state(store, state)
    return results


def health_google(adapter: GoogleDriveAdapter) -> dict[str, Any]:
    """Validate manifest and at least one eligible document without mutation."""
    try:
        records = adapter.discover()
        checked = 0
        for record in records[:1]:
            checked += 1
            if not record.content:
                raise GoogleDriveError("DOCUMENT_ACCESS_DENIED", "document has no readable text")
        return {"ok": True, "source": "google_drive", "eligible_records": len(records), "documents_checked": checked, "row_errors": getattr(adapter, "row_errors", [])}
    except GoogleDriveError as exc:
        return {"ok": False, "source": "google_drive", "code": exc.code, "error": str(exc)}
