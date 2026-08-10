"""Standalone acquisition, job queue, and reusable asset storage for TextStrata."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy as email_policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


import yaml

from .captions import CaptionCue, CaptionSource, normalize_cues, parse_webvtt, transcript_markdown, write_caption_artifacts
from .catalog import Catalog
from .ingest import ingest_text
from .source_identity import source_identity, youtube_source_kind
from .store import TextStrataStore


MAX_REMOTE_BYTES = 32 * 1024 * 1024
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".csv", ".json", ".xml", ".yaml", ".yml"}
AUDIO_EXTENSIONS = {".m4a", ".m4b", ".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".aac", ".wma", ".aif", ".aiff"}
# Video is transcribed through the same path; ffmpeg drops the video stream with -vn.
# This deliberately runs ahead of the MarkItDown fallback, whose audio converter would
# otherwise reach for cloud speech recognition.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

# Whisper is resolved as an external CLI, like markitdown/yt-dlp/tesseract, so the
# backend stays swappable per host. FABRIC_WHISPER_BIN overrides discovery.
WHISPER_TOOL_NAMES = ("whisper-ctranslate2", "whisper")
DEFAULT_WHISPER_MODEL = "base"
DEFAULT_WHISPER_COMPUTE = "int8"
DEFAULT_WHISPER_THREADS = 1
TRANSCODE_TIMEOUT = 600
TRANSCRIBE_TIMEOUT = 3600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "acquired-item"


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _normalize_source_date(value: object) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{8}", raw):
        try:
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None
    try:
        return _iso_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    try:
        return _iso_utc(parsedate_to_datetime(raw))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _format_transcript_stamp(raw: str) -> str:
    parts = raw.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = (int(part) for part in parts)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
    if len(parts) == 2:
        minutes, seconds = (int(part) for part in parts)
        return f"{minutes:02d}:{seconds:02d}"
    return raw


def _caption_file_metadata(name: str) -> tuple[str | None, str]:
    stem = Path(name).stem.lower()
    parts = stem.split(".")
    origin = "automatic" if any(part in {"auto", "asr", "automatic"} or "auto" in part for part in parts) else "manual"
    language = next((part for part in parts if re.fullmatch(r"[a-z]{2,3}(?:[-_][a-z]{2,4})?(?:-orig)?", part) and part not in {"vtt", "webvtt"}), None)
    if language and language.endswith("-orig"):
        language = language[:-5]
    return language, origin


def _caption_file_priority(name: str) -> tuple[int, int, int, str]:
    language, origin = _caption_file_metadata(name)
    lower = name.lower()
    manual_rank = 0 if origin == "manual" else 1
    language_rank = 0 if language == "en" else 1 if language and language.startswith("en") else 2
    format_rank = 0 if lower.endswith(".vtt") else 1
    return manual_rank, language_rank, format_rank, lower


def _extract_html_source_date(data: bytes) -> str | None:
    text = data.decode("utf-8", errors="ignore")
    patterns = [
        r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|og:published_time|datePublished|pubdate|publish-date|dc.date|dcterms.created)["\'][^>]+content=["\']([^"\']+)["\']',
        r'<time[^>]+datetime=["\']([^"\']+)["\']',
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"uploadDate"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            normalized = _normalize_source_date(match.group(1))
            if normalized:
                return normalized
    return None


def _tool(name: str) -> str | None:
    sibling = Path(os.sys.executable).parent / name
    if sibling.is_file():
        return str(sibling)
    return shutil.which(name)


def _worker_enabled() -> bool:
    return os.environ.get("FABRIC_ACQUISITION_WORKER", "1").strip().lower() not in {"0", "false", "no", "off"}


def _whisper_tool() -> str | None:
    override = os.environ.get("FABRIC_WHISPER_BIN", "").strip()
    if override:
        candidate = Path(override)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return _tool(override)
    for name in WHISPER_TOOL_NAMES:
        found = _tool(name)
        if found:
            return found
    return None


def _whisper_model() -> str:
    return os.environ.get("FABRIC_WHISPER_MODEL", "").strip() or DEFAULT_WHISPER_MODEL


def _whisper_threads() -> int:
    """Single-threaded by default. Measured on this host (4-core i5-7500T at load ~16)
    over 128s of audio: int8/1 thread 201s, int8/2 threads 999s. Extra intra-op threads
    on a busy box cost far more to contention than they win. Raise
    FABRIC_WHISPER_THREADS on an idle or many-core host."""
    raw = os.environ.get("FABRIC_WHISPER_THREADS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_WHISPER_THREADS


def _whisper_tuning_flags(tool: str) -> list[str]:
    """Backend-specific speedups. --threads is common to both supported CLIs;
    --compute_type is CTranslate2-only. int8 measured 1.8x faster than float32 here
    (201s vs 369s) despite this CPU lacking AVX512/VNNI; set FABRIC_WHISPER_COMPUTE
    to float32 if a host regresses."""
    flags = ["--threads", str(_whisper_threads())]
    if Path(tool).name.startswith("whisper-ctranslate2"):
        compute = os.environ.get("FABRIC_WHISPER_COMPUTE", "").strip() or DEFAULT_WHISPER_COMPUTE
        flags.extend(["--compute_type", compute])
    return flags


def capabilities() -> dict[str, Any]:
    tools = {name: _tool(name) for name in ("markitdown", "yt-dlp", "tesseract", "ffmpeg")}
    tools["whisper"] = _whisper_tool()
    return {
        "standalone": True,
        "tools": {name: bool(path) for name, path in tools.items()},
        "documents": bool(tools["markitdown"]),
        "images": bool(tools["tesseract"]),
        "web": bool(tools["markitdown"]),
        "youtube": bool(tools["yt-dlp"]),
        "audio_transcription": bool(tools["ffmpeg"] and tools["whisper"]),
    }


@dataclass(frozen=True)
class Asset:
    id: str
    path: Path
    media_type: str
    size: int
    metadata: dict[str, Any]


class AssetStore:
    """Content-addressed blobs shared by any number of TextStrata records."""

    def __init__(self, root: Path) -> None:
        self.root = root / "assets"
        self.originals = self.root / "originals" / "sha256"
        self.derivatives = self.root / "derivatives" / "sha256"
        self.metadata_dir = self.root / "metadata"
        for directory in (self.originals, self.derivatives, self.metadata_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, filename: str, media_type: str | None = None) -> Asset:
        digest = hashlib.sha256(data).hexdigest()
        suffix = Path(filename).suffix.lower()
        guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        media = media_type or guessed
        target = self.originals / digest[:2] / f"{digest}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fd, temp = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, target)
            finally:
                Path(temp).unlink(missing_ok=True)
        meta: dict[str, Any] = {
            "id": digest,
            "sha256": digest,
            "original_name": Path(filename).name,
            "media_type": media,
            "size": len(data),
            "created_at": _now(),
            "path": str(target.relative_to(self.root)),
        }
        self._image_metadata(target, digest, meta)
        self._write_json(self.metadata_dir / f"{digest}.json", meta)
        return Asset(digest, target, media, len(data), meta)

    def _image_metadata(self, path: Path, digest: str, meta: dict[str, Any]) -> None:
        try:
            from PIL import Image, ImageOps

            Image.MAX_IMAGE_PIXELS = 80_000_000
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                meta.update({"width": image.width, "height": image.height, "format": image.format})
                preview = image.copy()
                preview.thumbnail((1600, 1600))
                if preview.mode not in {"RGB", "RGBA"}:
                    preview = preview.convert("RGB")
                target = self.derivatives / digest[:2] / digest / "preview.webp"
                target.parent.mkdir(parents=True, exist_ok=True)
                preview.save(target, "WEBP", quality=86, method=6)
                meta["preview_path"] = str(target.relative_to(self.root))
        except (ImportError, OSError, ValueError):
            return

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)

    def resolve(self, asset_id: str, preview: bool = False) -> Asset:
        if not re.fullmatch(r"[0-9a-f]{64}", asset_id):
            raise ValueError("invalid asset ID")
        meta_path = self.metadata_dir / f"{asset_id}.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileNotFoundError(asset_id) from exc
        relative = meta.get("preview_path") if preview and meta.get("preview_path") else meta["path"]
        path = self.root / str(relative)
        if not path.is_file():
            raise FileNotFoundError(asset_id)
        media = "image/webp" if preview and meta.get("preview_path") else str(meta.get("media_type") or "application/octet-stream")
        return Asset(asset_id, path, media, path.stat().st_size, meta)

    def list_assets(self) -> list[dict[str, Any]]:
        """Return the durable asset index in a stable, newest-first order."""
        assets: list[dict[str, Any]] = []
        for meta_path in sorted(self.metadata_dir.glob("[0-9a-f]" * 64 + ".json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                asset_id = str(meta.get("id") or meta_path.stem)
                if not re.fullmatch(r"[0-9a-f]{64}", asset_id):
                    continue
                original = self.resolve(asset_id)
            except (OSError, ValueError, FileNotFoundError, json.JSONDecodeError, KeyError):
                continue
            entry = dict(meta)
            entry.update({
                "id": asset_id,
                "url": f"/asset/{asset_id}",
                "preview_url": f"/asset/{asset_id}?preview=1" if meta.get("preview_path") else f"/asset/{asset_id}",
                "size": original.size,
                "is_image": str(meta.get("media_type", "")).startswith("image/"),
            })
            assets.append(entry)
        assets.sort(key=lambda value: (str(value.get("created_at", "")), str(value.get("id", ""))), reverse=True)
        return assets


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _validate_remote_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must be an HTTP(S) address without embedded credentials")
    allow_private = os.environ.get("FABRIC_ALLOW_PRIVATE_FETCH", "").lower() in {"1", "true", "yes", "on"}
    for info in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(info[4][0])
        if not allow_private and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast):
            raise ValueError("URL resolves to a non-public address; set FABRIC_ALLOW_PRIVATE_FETCH=1 only for trusted LAN sources")
    return value


def _fetch_url(url: str) -> tuple[bytes, str, str, dict[str, str]]:
    opener = build_opener(_NoRedirect)
    current = url
    for _ in range(6):
        _validate_remote_url(current)
        request = Request(current, headers={"User-Agent": "TextStrata/0.2 (local knowledge acquisition)"})
        try:
            response = opener.open(request, timeout=30)
        except HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                current = urljoin(current, exc.headers["Location"])
                continue
            raise
        try:
            length = int(response.headers.get("Content-Length", "0") or 0)
            if length > MAX_REMOTE_BYTES:
                raise OverflowError("remote resource exceeds 32 MiB")
            data = response.read(MAX_REMOTE_BYTES + 1)
            if len(data) > MAX_REMOTE_BYTES:
                raise OverflowError("remote resource exceeds 32 MiB")
            headers = {key.lower(): value for key, value in response.headers.items()}
            return data, response.headers.get_content_type(), response.geturl(), headers
        finally:
            response.close()
    raise ValueError("too many redirects")


class AcquisitionService:
    """Persistent SQLite queue with one bounded local worker."""

    def __init__(self, store: TextStrataStore) -> None:
        self.store = store
        self.root = store.metadata_dir / "acquisition"
        self.uploads = self.root / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "jobs.db"
        self.assets = AssetStore(store.root)
        self._stop = threading.Event()
        self._init_db()
        # Several services can share one workspace, and therefore one jobs.db. Only one
        # of them should claim work, or jobs land in whichever codebase wins the race.
        if _worker_enabled():
            self._worker: threading.Thread | None = threading.Thread(target=self._run, name="textstrata-acquisition", daemon=True)
            self._worker.start()
        else:
            self._worker = None

    def close(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, original_name TEXT, title TEXT, notes TEXT,
                keep_original INTEGER NOT NULL DEFAULT 0, result_item_id TEXT,
                error_message TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                ocr_mode TEXT DEFAULT 'both', stage TEXT DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0, retryable INTEGER NOT NULL DEFAULT 0,
                started_at TEXT, finished_at TEXT, stage_updated_at TEXT,
                last_error_at TEXT
            )""")
            try:
                con.execute("ALTER TABLE jobs ADD COLUMN ocr_mode TEXT DEFAULT 'both'")
            except sqlite3.OperationalError:
                pass
            for column, definition in (("stage", "TEXT DEFAULT 'queued'"), ("attempts", "INTEGER NOT NULL DEFAULT 0"), ("retryable", "INTEGER NOT NULL DEFAULT 0")):
                try:
                    con.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError:
                    pass
            for column in ("started_at", "finished_at", "stage_updated_at", "last_error_at"):
                try:
                    con.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT")
                except sqlite3.OperationalError:
                    pass
            for column, definition in (("source_identity", "TEXT"), ("source_kind", "TEXT")):
                try:
                    con.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError:
                    pass
            # Only backfill identities that are unambiguous. Historical duplicates
            # remain readable and are deliberately not merged.
            rows = con.execute("SELECT id, type, payload FROM jobs WHERE source_identity IS NULL AND type IN ('youtube','url')").fetchall()
            for row in rows:
                identity = source_identity(row[2], row[1])
                if identity and con.execute("SELECT COUNT(*) FROM jobs WHERE source_identity=?", (identity,)).fetchone()[0] == 0 and con.execute("SELECT COUNT(*) FROM jobs WHERE type=? AND payload=?", (row[1], row[2])).fetchone()[0] == 1:
                    con.execute("UPDATE jobs SET source_identity=?, source_kind=? WHERE id=?", (identity, youtube_source_kind(row[2]) if row[1] == "youtube" else "url", row[0]))
            con.execute("DROP INDEX IF EXISTS jobs_source_identity_unique")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_source_identity_unique ON jobs(source_identity) WHERE source_identity IS NOT NULL AND status IN ('queued', 'processing', 'done')")
            recovered_at = _now()
            con.execute(
                "UPDATE jobs SET status='queued', stage='queued', error_message='Recovered after restart', "
                "stage_updated_at=?, updated_at=? WHERE status='processing'",
                (recovered_at, recovered_at),
            )

    def enqueue_url(self, url: str, *, title: str = "", notes: str = "", keep_original: bool = False, ocr_mode: str = "both") -> int:
        return int(self.enqueue_url_result(url, title=title, notes=notes, keep_original=keep_original, ocr_mode=ocr_mode)["job_id"])

    def enqueue_url_result(self, url: str, *, title: str = "", notes: str = "", keep_original: bool = False, ocr_mode: str = "both") -> dict[str, Any]:
        kind = "youtube" if re.search(r"(?:youtube\.com|youtu\.be|^@)", url, re.I) else "url"
        if kind == "youtube" and not _tool("yt-dlp"):
            raise ValueError("YouTube ingestion is unavailable on this instance because yt-dlp is not installed")
        identity = source_identity(url, kind)
        return self._enqueue_result(kind, url, Path(urlparse(url).path).name, title, notes, keep_original, ocr_mode, identity)

    def enqueue_file(self, data: bytes, filename: str, *, media_type: str = "", title: str = "", notes: str = "", keep_original: bool = False, ocr_mode: str = "both") -> int:
        if not data:
            raise ValueError("uploaded file is empty")
        safe_name = Path(filename).name or "upload.bin"
        digest = hashlib.sha256(data).hexdigest()
        target = self.uploads / f"{digest}-{safe_name}"
        if not target.exists():
            target.write_bytes(data)
        payload = json.dumps({"path": str(target), "media_type": media_type})
        return int(self._enqueue_result("file", payload, safe_name, title, notes, keep_original, ocr_mode, f"file:sha256:{digest}")["job_id"])

    def _enqueue(self, kind: str, payload: str, original_name: str, title: str, notes: str, keep_original: bool, ocr_mode: str = "both") -> int:
        return int(self._enqueue_result(kind, payload, original_name, title, notes, keep_original, ocr_mode, source_identity(payload, kind))["job_id"])

    def _enqueue_result(self, kind: str, payload: str, original_name: str, title: str, notes: str, keep_original: bool, ocr_mode: str = "both", identity: str | None = None) -> dict[str, Any]:
        if ocr_mode not in ("both", "image", "text"):
            raise ValueError("ocr_mode must be 'both', 'image', or 'text'")
        now = _now()
        with self._connect() as con:
            existing = con.execute("SELECT * FROM jobs WHERE ((source_identity=? AND source_identity IS NOT NULL) OR (type=? AND payload=?)) AND status IN ('queued', 'processing', 'done') ORDER BY id LIMIT 1", (identity, kind, payload)).fetchone() if identity else con.execute("SELECT * FROM jobs WHERE type=? AND payload=? AND status IN ('queued', 'processing', 'done')", (kind, payload)).fetchone()
            if existing:
                return {"job_id": int(existing["id"]), "status": existing["status"], "deduplicated": True, "source_identity": existing["source_identity"], "result_item_id": existing["result_item_id"]}
            try:
                cur = con.execute(
                    "INSERT INTO jobs(type,status,payload,original_name,title,notes,keep_original,ocr_mode,created_at,updated_at,stage_updated_at,source_identity,source_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (kind, "queued", payload, original_name, title, notes, int(keep_original), ocr_mode, now, now, now, identity, youtube_source_kind(payload) if kind == "youtube" else ("url" if kind == "url" else kind)),
                )
            except sqlite3.IntegrityError:
                existing = con.execute("SELECT * FROM jobs WHERE source_identity=? AND status IN ('queued','processing','done') ORDER BY id LIMIT 1", (identity,)).fetchone()
                if existing is None:
                    raise
                return {"job_id": int(existing["id"]), "status": existing["status"], "deduplicated": True, "source_identity": existing["source_identity"], "result_item_id": existing["result_item_id"]}
            return {"job_id": int(cur.lastrowid), "status": "queued", "deduplicated": False, "source_identity": identity, "result_item_id": None}

    def list_jobs(self) -> dict[str, Any]:
        with self._connect() as con:
            rows = [dict(row) for row in con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 200")]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            row["result_path"] = row.get("result_item_id")
            row["duplicate_of"] = row.get("source_identity") if row.get("deduplicated") else None
            started = _parse_timestamp(row.get("started_at"))
            finished = _parse_timestamp(row.get("finished_at"))
            row["duration_seconds"] = round(max(0.0, (finished or datetime.now(timezone.utc)).timestamp() - started.timestamp()), 3) if started else None
        return {"jobs": rows, "counts": counts}

    def get_settings(self) -> dict[str, Any]:
        path = self.root / "settings.json"
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
        return {
            "retain_original_uploads_default": bool(saved.get("retain_original_uploads_default", False)),
            "retained_originals_purge_mode": saved.get("retained_originals_purge_mode", "days") if saved.get("retained_originals_purge_mode", "days") in {"days", "never"} else "days",
            "retained_originals_days": min(3650, max(1, int(saved.get("retained_originals_days", 30)))),
            "paths": {"acquisition": str(self.root), "assets": str(self.assets.root)},
            "capabilities": capabilities(),
        }

    def save_settings(self, value: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        mode = str(value.get("retained_originals_purge_mode", current["retained_originals_purge_mode"]))
        if mode not in {"days", "never"}:
            raise ValueError("retained_originals_purge_mode must be days or never")
        days = min(3650, max(1, int(value.get("retained_originals_days", current["retained_originals_days"]))))
        saved = {"retain_original_uploads_default": bool(value.get("retain_original_uploads_default", current["retain_original_uploads_default"])), "retained_originals_purge_mode": mode, "retained_originals_days": days}
        AssetStore._write_json(self.root / "settings.json", saved)
        return self.get_settings()

    def purge_channel(self, handle: str) -> int:
        removed = 0
        with self._connect() as con:
            rows = [dict(row) for row in con.execute("SELECT * FROM jobs WHERE type='youtube' AND lower(payload) LIKE ?", (f"%{handle.lower()}%",))]
        for row in rows:
            if row.get("result_item_id") and self.store.normalized_path_for_id(str(row["result_item_id"])):
                self.store.trash_item(str(row["result_item_id"]))
            self.delete(int(row["id"]))
            removed += 1
        return removed

    def retry(self, job_id: int) -> None:
        with self._connect() as con:
            row = con.execute("SELECT status, retryable FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise FileNotFoundError(str(job_id))
            if row["status"] != "failed" or not row["retryable"]:
                raise ValueError("only retryable failed jobs can be retried")
            now = _now()
            con.execute("UPDATE jobs SET status='queued', stage='queued', error_message=NULL, cancel_requested=0, finished_at=NULL, stage_updated_at=?, updated_at=? WHERE id=?", (now, now, job_id))

    def cancel(self, job_id: int) -> None:
        with self._connect() as con:
            con.execute("UPDATE jobs SET cancel_requested=1, status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END, updated_at=? WHERE id=?", (_now(), job_id))

    def delete(self, job_id: int) -> None:
        with self._connect() as con:
            row = con.execute("SELECT status,payload,type FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise FileNotFoundError(str(job_id))
            if row["status"] == "processing":
                raise ValueError("processing jobs must be cancelled before deletion")
            con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self._delete_staged_upload(dict(row))

    def clear_completed(self) -> int:
        with self._connect() as con:
            rows = [dict(row) for row in con.execute("SELECT * FROM jobs WHERE status IN ('done','failed','cancelled')")]
            cur = con.execute("DELETE FROM jobs WHERE status IN ('done','failed','cancelled')")
        for row in rows:
            self._delete_staged_upload(row)
        return cur.rowcount

    def purge_output(self, job_id: int) -> dict[str, str]:
        with self._connect() as con:
            row = con.execute("SELECT result_item_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or not row["result_item_id"]:
            raise FileNotFoundError(str(job_id))
        return self.store.trash_item(str(row["result_item_id"]))

    def _delete_staged_upload(self, row: dict[str, Any]) -> None:
        if row.get("type") != "file":
            return
        try:
            Path(json.loads(row["payload"])["path"]).unlink(missing_ok=True)
        except (KeyError, TypeError, json.JSONDecodeError):
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._connect() as con:
                row = con.execute("SELECT * FROM jobs WHERE status='queued' AND cancel_requested=0 ORDER BY id LIMIT 1").fetchone()
                if row:
                    now = _now()
                    con.execute("UPDATE jobs SET status='processing', stage='metadata', attempts=attempts+1, started_at=COALESCE(started_at, ?), finished_at=NULL, stage_updated_at=?, updated_at=? WHERE id=?", (now, now, now, row["id"]))
            if row is None:
                self._stop.wait(0.4)
                continue
            job = dict(row)
            try:
                item_id = self._process(job)
                with self._connect() as con:
                    cancelled = con.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job["id"],)).fetchone()
                    status = "cancelled" if cancelled and cancelled[0] else "done"
                    now = _now()
                    con.execute("UPDATE jobs SET status=?, stage='published', result_item_id=?, retryable=0, finished_at=?, stage_updated_at=?, updated_at=? WHERE id=?", (status, item_id, now, now, now, job["id"]))
            except Exception as exc:  # job boundary: error is persisted for UI recovery
                with self._connect() as con:
                    message = str(exc)[:2000]
                    retryable = int(isinstance(exc, (TimeoutError, ConnectionError, OSError)))
                    now = _now()
                    con.execute("UPDATE jobs SET status='failed', stage='failed', error_message=?, retryable=?, finished_at=?, last_error_at=?, stage_updated_at=?, updated_at=? WHERE id=?", (message, retryable, now, now, now, now, job["id"]))

    def _process(self, job: dict[str, Any]) -> str:
        job_id = job.get("id")
        if job_id is not None:
            self._set_stage(int(job_id), "metadata")
        extra_meta: dict[str, Any] = {}
        ocr_mode = job.get("ocr_mode") or "both"
        if job["type"] == "file":
            payload = json.loads(job["payload"])
            path = Path(payload["path"])
            data = path.read_bytes()
            markdown, source_url, asset_ids, extra_meta = self._convert_file(data, job["original_name"] or path.name, payload.get("media_type") or "", ocr_mode=ocr_mode)
        elif job["type"] == "youtube":
            if job_id is not None:
                self._set_stage(int(job_id), "captions")
            markdown, source_url, extra_meta = self._convert_youtube(job["payload"])
            asset_ids = []
        else:
            markdown, source_url, extra_meta = self._convert_url(job["payload"])
            asset_ids = []
        if job_id is not None:
            self._set_stage(int(job_id), "normalizing")
        caption_cues = normalize_cues(extra_meta.pop("_caption_cues", []))
        extra_tags = extra_meta.pop("_extra_tags", [])
        title = str(job.get("title") or "").strip() or self._title_from_markdown(markdown) or job.get("original_name") or source_url
        identity = job.get("source_identity")
        item_id = self._stable_item_id(identity, title)
        if identity:
            existing = self._existing_item_for_identity(identity)
            if existing:
                return existing
        front = {
            "id": item_id,
            "title": title,
            "type": "reference",
            "tags": ["acquired", job["type"], *extra_tags],
            "preservation": "preserve_exact",
            "created_via": "textstrata-standalone-acquisition",
            "contributor_chain": "via_script",
            "source_url": source_url or None,
            "source_kind": job.get("source_kind") or job.get("type"),
            "source_identity": identity,
            "assets": asset_ids,
            "acquisition_notes": job.get("notes") or None,
            **{k: v for k, v in extra_meta.items() if v not in (None, "", [], {})},
        }
        raw = "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip() + "\n---\n\n" + markdown.strip() + "\n"
        if job_id is not None:
            self._set_stage(int(job_id), "publishing")
        result = ingest_text(self.store, raw, fallback_id=item_id)
        if not result.published:
            raise ValueError("; ".join(result.validation.errors) or "converted content failed TextStrata validation")
        catalog = Catalog(self.store.root)
        try:
            catalog.index_item(result.item)
        finally:
            catalog.close()
        if caption_cues:
            write_caption_artifacts(self.store.root, result.item.id, caption_cues, result.item.body)
        return result.item.id

    def _set_stage(self, job_id: int, stage: str) -> None:
        with self._connect() as con:
            now = _now()
            con.execute("UPDATE jobs SET stage=?, stage_updated_at=?, updated_at=? WHERE id=?", (stage, now, now, job_id))

    def _stable_item_id(self, identity: str | None, title: str) -> str:
        if identity and identity.startswith("youtube:video:"):
            return "youtube-video-" + identity.rsplit(":", 1)[-1].lower()
        return _slug(title)

    def _existing_item_for_identity(self, identity: str) -> str | None:
        from .ingest import build_item
        for path in sorted(self.store.normalized_dir.glob("*.md")):
            try:
                item = build_item(path.read_text(encoding="utf-8"), fallback_id=path.stem)[0]
            except (OSError, ValueError):
                continue
            if item.provenance.source_identity == identity:
                return item.id
        return None

    @staticmethod
    def _title_from_markdown(markdown: str) -> str:
        match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _convert_file(self, data: bytes, filename: str, media_type: str, ocr_mode: str = "both") -> tuple[str, str, list[str], dict[str, Any]]:
        suffix = Path(filename).suffix.lower()
        if suffix in AUDIO_EXTENSIONS or suffix in VIDEO_EXTENSIONS or media_type.startswith(("audio/", "video/")):
            return self._convert_audio(data, filename, media_type)
        if suffix in IMAGE_EXTENSIONS or media_type.startswith("image/"):
            asset = self.assets.put(data, filename, media_type or None)
            ocr = self._ocr(asset.path)
            alt = Path(filename).stem.replace("-", " ").replace("_", " ")
            details = [f"- SHA-256: `{asset.id}`", f"- Media type: `{asset.media_type}`", f"- Size: {asset.size} bytes"]
            if asset.metadata.get("width"):
                details.append(f"- Dimensions: {asset.metadata['width']} × {asset.metadata['height']}")
            if ocr_mode == "text":
                body = f"# {alt}\n\n" + (ocr if ocr else "(no OCR text extracted)")
                return body, "", [asset.id], {}
            body = f"# {alt}\n\n![{alt}](/asset/{asset.id}?preview=1)\n\n## Asset metadata\n\n" + "\n".join(details)
            if ocr_mode == "both" and ocr:
                body += "\n\n## OCR text\n\n" + ocr
            return body, "", [asset.id], {}
        if suffix in TEXT_EXTENSIONS:
            try:
                return data.decode("utf-8"), "", [], {}
            except UnicodeDecodeError:
                pass
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(data)
            temp = Path(handle.name)
        try:
            return self._markitdown(str(temp)), "", [], {}
        finally:
            temp.unlink(missing_ok=True)

    def _convert_audio(self, data: bytes, filename: str, media_type: str) -> tuple[str, str, list[str], dict[str, Any]]:
        asset = self.assets.put(data, filename, media_type or None)
        title = Path(filename).stem.replace("-", " ").replace("_", " ")
        cues = self._transcribe(asset.path)
        details = [f"- SHA-256: `{asset.id}`", f"- Media type: `{asset.media_type}`", f"- Size: {asset.size} bytes"]
        duration = self._audio_duration(asset.path)
        if duration:
            details.append(f"- Duration: {duration}")
        details.append(f"- Transcribed with: `{Path(_whisper_tool() or 'whisper').name}` (model `{_whisper_model()}`)")
        transcript = transcript_markdown(cues)
        body = (
            f"# {title}\n\n[Original media](/asset/{asset.id})\n\n## Source media\n\n"
            + "\n".join(details)
            + "\n\n## Timestamped transcript\n\n"
            + (transcript or "(no speech detected)")
        )
        meta: dict[str, Any] = {"_extra_tags": ["audio", "transcript"], "caption_language": os.environ.get("FABRIC_WHISPER_LANGUAGE") or None, "caption_origin": "local", "acquisition_tool": f"{Path(_whisper_tool() or 'whisper').name}:{_whisper_model()}"}
        if cues:
            meta["_caption_cues"] = cues
        return body, "", [asset.id], meta

    @staticmethod
    def _transcribe(path: Path) -> list[CaptionCue]:
        tool = _whisper_tool()
        if not tool:
            raise RuntimeError(
                "audio transcription requires a Whisper CLI; install the textstrata[audio] extra "
                "or point FABRIC_WHISPER_BIN at one"
            )
        ffmpeg = _tool("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("audio transcription requires ffmpeg for decoding")
        with tempfile.TemporaryDirectory(prefix="textstrata-audio-") as tmp:
            wav = Path(tmp) / "audio.wav"
            decode = subprocess.run(
                [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                capture_output=True, text=True, timeout=TRANSCODE_TIMEOUT,
            )
            if decode.returncode or not wav.is_file():
                raise RuntimeError(decode.stderr.strip() or "ffmpeg could not decode the audio file")
            # Base flags are the subset openai-whisper and whisper-ctranslate2 share;
            # _whisper_tuning_flags adds anything backend-specific.
            command = [tool, str(wav), "--output_dir", tmp, "--output_format", "vtt", "--model", _whisper_model()]
            command.extend(_whisper_tuning_flags(tool))
            language = os.environ.get("FABRIC_WHISPER_LANGUAGE", "").strip()
            if language:
                command.extend(["--language", language])
            proc = subprocess.run(command, capture_output=True, text=True, timeout=TRANSCRIBE_TIMEOUT)
            if proc.returncode:
                raise RuntimeError(proc.stderr.strip() or "whisper transcription failed")
            files = sorted(Path(tmp).glob("*.vtt"))
            if not files:
                raise RuntimeError("whisper produced no caption output")
            return parse_webvtt(files[0].read_text(encoding="utf-8", errors="replace"))

    @staticmethod
    def _audio_duration(path: Path) -> str:
        probe = _tool("ffprobe")
        if not probe:
            return ""
        proc = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode:
            return ""
        try:
            total = int(float(proc.stdout.strip()))
        except ValueError:
            return ""
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    def _convert_url(self, url: str) -> tuple[str, str, dict[str, Any]]:
        data, media_type, final_url, headers = _fetch_url(url)
        suffix = Path(urlparse(final_url).path).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix or (".html" if media_type == "text/html" else ".bin"), delete=False) as handle:
            handle.write(data)
            temp = Path(handle.name)
        try:
            markdown = self._markitdown(str(temp))
        finally:
            temp.unlink(missing_ok=True)
        source_date = _extract_html_source_date(data) if media_type == "text/html" else None
        if not source_date:
            source_date = _normalize_source_date(headers.get("last-modified"))
        meta = {"document_date": source_date} if source_date else {}
        return markdown, final_url, meta

    def _convert_youtube(self, value: str) -> tuple[str, str, dict[str, Any]]:
        tool = _tool("yt-dlp")
        if not tool:
            raise RuntimeError("YouTube ingestion requires yt-dlp; install the textstrata[youtube] extra")
        url = value if not value.startswith("@") else f"https://www.youtube.com/{value}"
        proc = subprocess.run([tool, "--dump-single-json", "--skip-download", "--no-warnings", url], capture_output=True, text=True, timeout=180)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or "yt-dlp metadata extraction failed")
        info = json.loads(proc.stdout)
        if info.get("_type") in {"playlist", "multi_video"} or info.get("entries"):
            entries = [entry for entry in info.get("entries", []) if entry]
            lines = [f"# {info.get('title') or value}", "", f"Source: {url}", "", "## Videos", ""]
            for entry in entries:
                video_url = entry.get("webpage_url") or entry.get("url") or ""
                lines.append(f"- [{entry.get('title') or entry.get('id')}]({video_url})")
            playlist_date = _normalize_source_date(info.get("modified_date") or info.get("upload_date") or info.get("release_date") or info.get("release_timestamp"))
            meta = {"document_date": playlist_date} if playlist_date else {}
            return "\n".join(lines), url, meta
        title = info.get("title") or info.get("id") or "YouTube video"
        source = info.get("webpage_url") or url
        transcript, caption_cues, caption_source = self._youtube_captions(tool, source)
        lines = [f"# {title}", "", f"Source: {source}", "", f"Channel: {info.get('channel') or info.get('uploader') or 'Unknown'}"]
        if info.get("description"):
            lines.extend(["", "## Description", "", str(info["description"])])
        lines.extend(["", "## Timestamped transcript", "", transcript or "No captions were available. Install the audio extra for local Whisper fallback."])
        published = _normalize_source_date(info.get("release_timestamp") or info.get("timestamp") or info.get("upload_date") or info.get("release_date"))
        meta = {"document_date": published} if published else {}
        meta.update({"caption_language": caption_source.language, "caption_origin": caption_source.origin, "acquisition_tool": caption_source.tool, "acquisition_status": caption_source.status})
        if caption_cues:
            meta["_caption_cues"] = caption_cues
        return "\n".join(lines), source, meta

    @staticmethod
    def _youtube_captions(tool: str, url: str) -> tuple[str, list[CaptionCue], CaptionSource]:
        with tempfile.TemporaryDirectory(prefix="textstrata-subs-") as tmp:
            template = str(Path(tmp) / "captions.%(ext)s")
            proc = subprocess.run([tool, "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt", "-o", template, url], capture_output=True, text=True, timeout=240)
            if proc.returncode:
                return "", [], CaptionSource(status="failed", tool=Path(tool).name)
            files = sorted(Path(tmp).glob("*.vtt"), key=lambda path: _caption_file_priority(path.name))
            if not files:
                return "", [], CaptionSource(status="unavailable", tool=Path(tool).name)
            selected = files[0]
            language, origin = _caption_file_metadata(selected.name)
            cues = normalize_cues(parse_webvtt(selected.read_text(encoding="utf-8", errors="replace")))
            return transcript_markdown(cues), cues, CaptionSource(language=language, origin=origin, tool=Path(tool).name)

    @staticmethod
    def _markitdown(path: str) -> str:
        tool = _tool("markitdown")
        if not tool:
            raise RuntimeError("document and web ingestion require MarkItDown; install the textstrata[documents] extra")
        proc = subprocess.run([tool, path], capture_output=True, text=True, timeout=600)
        if proc.returncode or not proc.stdout.strip():
            raise RuntimeError(proc.stderr.strip() or "MarkItDown produced no output")
        return proc.stdout

    @staticmethod
    def _ocr(path: Path) -> str:
        tool = _tool("tesseract")
        if not tool:
            return ""
        proc = subprocess.run([tool, str(path), "stdout", "--psm", "3"], capture_output=True, text=True, timeout=300)
        return proc.stdout.strip() if proc.returncode == 0 else ""


def parse_acquisition_multipart(content_type: str, body: bytes) -> dict[str, Any]:
    try:
        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid multipart Content-Type") from exc
    message = BytesParser(policy=email_policy.default).parsebytes(header + body)
    if not message.is_multipart():
        raise ValueError("invalid multipart body")
    result: dict[str, Any] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        raw = part.get_payload(decode=True) or b""
        if name == "file" and part.get_filename():
            result.update(file=raw, filename=Path(part.get_filename() or "upload.bin").name, media_type=part.get_content_type())
        elif name:
            result[name] = raw.decode(part.get_content_charset() or "utf-8", errors="strict")
    return result
