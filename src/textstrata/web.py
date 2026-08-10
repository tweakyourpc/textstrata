"""Local HTTP presentation surface for TextStrata.

This is the end-user wrapper over the same textstrata substrate. It is intentionally
small and stdlib-only so it can run on a dev box without extra runtime baggage.
"""

from __future__ import annotations

import json
import os
import platform as _platform
import re
import shutil
import sys
import threading
from email import policy as email_policy
from email.parser import BytesParser
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__
from .acquisition import AcquisitionService, capabilities, parse_acquisition_multipart
from .application.acquisition import acquisition_maintenance_settings_payload, acquisition_queue_payload, build_ingest_submission, clear_acquisition_completed, save_acquisition_maintenance_settings
from .application.items import rename_item, update_aliases
from .application.graph import build_graph_payload
from .application.item_detail import build_item_render_context
from .application.library import corpus_view, orphaned_items, render_dashboard, search_library
from .application.reviews import review_queue_payload
from .application.settings import build_system_info_payload, load_settings_payload, save_settings_payload
from .application.setup import initialize_workspace, setup_status
from .captions import export_caption
from .source_identity import source_identity, youtube_source_kind
from .catalog import Catalog
from .ingest import build_item, ingest_text
from .models import VALID_CONTRIBUTORS, append_contributor
from .gateway import GatewayError, CompatibilityGateway
from .operations import error_payload, get_settings, record_error
from .linking import build_links
from .similarity import build_similarity_edges, score_corpus
from .presentation.browser_assets import client_asset_content
from .presentation import render_item_html, render_library_index, render_media_html, render_new_note_html, render_setup_html, render_text, skin_from_settings
from .presentation.pages.graph import render_graph_html
from .store import TextStrataStore
from .validate import validate
from .workspace import apply_config_environment, load_cascading_config, resolve_workspace
from . import classify
from . import review

MAX_INGEST_BYTES = 5 * 1024 * 1024
MAX_ACQUIRE_BYTES = 64 * 1024 * 1024
_ITEM_SAVE_RE = re.compile(r"^/api/textstrata/item/([^/]+)/save$")
_ITEM_RENAME_RE = re.compile(r"^/api/textstrata/item/([^/]+)/rename$")
_ITEM_ALIASES_RE = re.compile(r"^/api/textstrata/item/([^/]+)/aliases$")
_NOTE_CAPTION_EXPORT_RE = re.compile(r"^/api/notes/([^/]+)/export/(vtt|srt)$")
_CLIENT_ASSET_RE = re.compile(r"^/static/textstrata-(library|new-note)-([^/]+)\.js$")


def _canonical_path(path: str) -> str:
    """Map the pre-TextStrata API prefix to the current route namespace.

    The old prefix remains a supported wire-level alias so existing browser
    clients and integrations keep working after the product rename.
    """
    if path == "/api/fabric" or path.startswith("/api/fabric/"):
        return "/api/textstrata" + path[len("/api/fabric"):]
    if path.startswith("/static/fabric-"):
        return "/static/textstrata-" + path[len("/static/fabric-"):]
    return path


class TextStrataWebApp:
    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = TextStrataStore(self.root)
        self.store.ensure_dirs()
        self.store.revision_limit = get_settings(self.store)["revision_limit"]
        self.catalog = Catalog(self.root)
        self.acquisition = AcquisitionService(self.store)
        self.started_at = datetime.now(timezone.utc).isoformat()
        # Parsed-items cache, invalidated by a cheap stat()-based fingerprint
        # of the normalized store (name, mtime, size). Avoids re-reading and
        # YAML-parsing every markdown file on every request.
        self._items_lock = threading.Lock()
        self._items_cache: list | None = None
        self._items_fingerprint: tuple | None = None
        # Cached per-store vocabulary map (base synonyms + synonyms.json),
        # refreshed when the override file's mtime changes.
        self._synonyms_cache: dict[str, str] | None = None
        self._synonyms_mtime: int | None = None
        # Set when a graceful restart has been requested via the web UI.
        self._restart_requested = threading.Event()
        self._server = None  # set by serve()
        self.gateway = CompatibilityGateway() if os.environ.get("FABRIC_ENABLE_PARITY", "").lower() in {"1", "true", "yes", "on"} else None
        self._sync_lock = threading.Lock()
        # One-time startup rescan so the disposable catalog reflects disk
        # state. The operations reference is created lazily when an error is
        # recorded, keeping a brand-new workspace genuinely empty. From here on the catalog is
        # maintained incrementally on ingest / save / trash / restore instead
        # of per-request rescans.
        self.catalog.rescan(self.store)
        if self.gateway:
            threading.Thread(target=self.sync_upstream, name="textstrata-upstream-sync", daemon=True).start()

    @staticmethod
    def _detect_install_type() -> str:
        if os.path.exists("/.dockerenv"):
            return "docker"
        plat = sys.platform
        if plat == "linux":
            return "linux-systemd" if shutil.which("systemctl") else "linux-other"
        if plat == "darwin":
            return "macos"
        if plat == "win32":
            return "windows"
        return "other"

    def system_info(self) -> dict[str, object]:
        return {
            "version": __version__,
            "platform": _platform.system(),
            "platform_release": _platform.release(),
            "architecture": _platform.machine(),
            "install_type": self._detect_install_type(),
            "pid": os.getpid(),
            "docker": os.path.exists("/.dockerenv"),
            "has_systemd": bool(shutil.which("systemctl")),
        }

    def close(self) -> None:
        self.acquisition.close()
        self.catalog.close()

    def whoami(self, host: str, port: int) -> dict[str, object]:
        return {
            "service": "textstrata",
            "version": __version__,
            "pid": os.getpid(),
            "startedAt": self.started_at,
            "host": host,
            "port": port,
        }

    def _store_fingerprint(self) -> tuple:
        """Cheap O(n) stat-only fingerprint of the normalized store."""
        entries = []
        for path in self.store.normalized_paths():
            try:
                st = path.stat()
            except OSError:
                continue
            entries.append((path.name, st.st_mtime_ns, st.st_size))
        return tuple(sorted(entries))

    def items(self):
        """Return all normalized items, served from cache when unchanged.

        The cache is invalidated automatically whenever any normalized file
        is added, removed, or modified (mtime/size fingerprint), so mutation
        routes don't need to invalidate explicitly.
        """
        fingerprint = self._store_fingerprint()
        with self._items_lock:
            if self._items_cache is not None and self._items_fingerprint == fingerprint:
                return list(self._items_cache)
        parsed = [
            build_item(path.read_text(encoding="utf-8"), fallback_id=path.stem)[0]
            for path in self.store.normalized_paths()
        ]
        with self._items_lock:
            self._items_cache = parsed
            self._items_fingerprint = fingerprint
        return list(parsed)

    def synonyms(self) -> dict[str, str]:
        """Effective vocabulary map for this store (base + synonyms.json).

        Reloaded when the override file's mtime changes so edits to
        synonyms.json take effect without a restart, but cheap on the hot
        path (a single stat + cached dict otherwise).
        """
        from . import vocabulary

        syn_path = self.store.metadata_dir / "synonyms.json"
        try:
            mtime = syn_path.stat().st_mtime_ns
        except OSError:
            mtime = 0
        with self._items_lock:
            if self._synonyms_cache is not None and self._synonyms_mtime == mtime:
                return self._synonyms_cache
        mapping = vocabulary.load_synonyms(self.root)
        with self._items_lock:
            self._synonyms_cache = mapping
            self._synonyms_mtime = mtime
        return mapping

    def invalidate_synonyms(self) -> None:
        with self._items_lock:
            self._synonyms_cache = None
            self._synonyms_mtime = None

    def request_restart(self) -> None:
        """Gracefully shut down the HTTP server; textstrata-server restarts us.

        The PID file is intentionally left in place on this path — that is
        the signal textstrata-server uses to distinguish "restart me" (exit 0,
        PID file present) from a clean stop (exit 0, PID file gone).
        """
        self._restart_requested.set()
        server = self._server
        if server is not None:
            # shutdown() blocks until serve_forever() exits, so it must be
            # called from a different thread than the serve loop; the caller
            # runs it in a background thread after the response is sent.
            server.shutdown()

    def item_by_id(self, item_id: str):
        path = self.store.normalized_path_for_id(item_id)
        if path is None:
            raise FileNotFoundError(item_id)
        return build_item(path.read_text(encoding="utf-8"), fallback_id=item_id)[0]

    def sync_upstream(self):
        if not self.gateway or not self._sync_lock.acquire(blocking=False):
            return {"imported": 0, "unchanged": 0, "total": 0}
        try:
            result = self.gateway.sync(self.store)
            catalog = Catalog(self.root)
            try:
                catalog.rescan(self.store)
            finally:
                catalog.close()
            return result
        except GatewayError as exc:
            record_error(self.store, exc.code)
            return {"error": str(exc), "code": exc.code}
        finally:
            self._sync_lock.release()


def create_handler(app: TextStrataWebApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"TextStrata/{__version__}"

        def log_message(self, fmt: str, *args) -> None:  # quiet by default
            return

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, object]) -> None:
            self._send(status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

        def _html(self, status: int, body: str) -> None:
            self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))

        def _failure(self, status: int, code: str, message: str) -> None:
            record_error(app.store, code)
            self._json(status, error_payload(code, message))

        def _read_raw(self, limit: int = MAX_ACQUIRE_BYTES) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > limit:
                raise OverflowError(f"request exceeds the {limit // (1024 * 1024)} MiB limit")
            return self.rfile.read(length) if length else b""

        def _read_json_body(self) -> dict[str, object]:
            raw = self._read_raw(MAX_INGEST_BYTES)
            try:
                value = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("request body must be valid UTF-8 JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("request body must be a JSON object")
            return value

        def _read_raw_text(self, limit: int = MAX_INGEST_BYTES) -> str:
            raw = self._read_raw(limit)
            ct = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if ct == "application/json":
                data = json.loads(raw.decode("utf-8")) if raw else {}
                if isinstance(data, dict):
                    return data.get("content", "") if isinstance(data.get("content"), str) else ""
                return ""
            return raw.decode("utf-8")

        def _gateway(self) -> CompatibilityGateway:
            if app.gateway is None:
                raise GatewayError("upstream-unavailable", "The compatibility gateway is disabled.", 503)
            return app.gateway

        def _proxy(self, method: str, upstream_path: str, body: bytes | None = None) -> None:
            try:
                response = self._gateway().request(method, upstream_path, body, self.headers.get("Content-Type") if body is not None else None)
                self._send(response.status, response.content_type, response.body)
            except GatewayError as exc:
                self._failure(exc.status, exc.code, str(exc))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = _canonical_path(parsed.path.rstrip("/") or "/")
            query = parse_qs(parsed.query)

            if path == "/healthz":
                # Readiness: catalog responds to a query and the normalized
                # store directory is reachable. 200 healthy / 503 otherwise.
                try:
                    catalog = Catalog(app.root)
                    try:
                        indexed = catalog.count()
                    finally:
                        catalog.close()
                    store_ok = app.store.normalized_dir.is_dir()
                except Exception as exc:  # noqa: BLE001 — health must not raise
                    self._json(503, {"status": "unhealthy", "error": str(exc)})
                    return
                if not store_ok:
                    self._json(503, {"status": "unhealthy", "error": "normalized store missing"})
                    return
                self._json(200, {"status": "ok", "indexed": indexed, "version": __version__})
                return

            if path == "/whoami":
                self._json(200, app.whoami(self.server.server_address[0], self.server.server_address[1]))  # type: ignore[attr-defined]
                return

            client_asset = _CLIENT_ASSET_RE.fullmatch(path)
            if client_asset:
                try:
                    body = client_asset_content(client_asset.group(1), client_asset.group(2), __version__).encode("utf-8")
                except (KeyError, ValueError):
                    self._failure(404, "operation-failed", "Client asset not found.")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
                return

            if path.startswith("/asset/"):
                asset_id = path.removeprefix("/asset/").strip("/")
                try:
                    asset = app.acquisition.assets.resolve(asset_id, preview=query.get("preview", [""])[0] == "1")
                    body = asset.path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", asset.media_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(body)
                except (ValueError, FileNotFoundError):
                    self._failure(404, "operation-failed", "Asset not found.")
                return

            if path == "/api/acquisition/capabilities":
                self._json(200, capabilities())
                return

            if path == "/api/acquisition/source-identity":
                value = query.get("url", [""])[0]
                identity = source_identity(value)
                self._json(200, {"source_identity": identity, "source_kind": youtube_source_kind(value) or ("url" if identity else None)})
                return

            if path == "/api/textstrata/link-targets":
                self._json(200, {"targets": [{"id": item.id, "title": item.title, "aliases": list(getattr(item, "aliases", ())) } for item in app.items()]})
                return

            if path == "/api/textstrata/assets":
                self._json(200, {"assets": app.acquisition.assets.list_assets()})
                return

            caption_export = _NOTE_CAPTION_EXPORT_RE.fullmatch(path)
            if caption_export:
                note_id = unquote(caption_export.group(1))
                format_name = caption_export.group(2)
                try:
                    item = app.item_by_id(note_id)
                    text = export_caption(app.root, item, format_name)
                except (FileNotFoundError, ValueError):
                    self._failure(404, "caption-export-not-found", "Caption export not found.")
                    return
                body = text.encode("utf-8")
                content_type = (
                    "text/vtt; charset=utf-8"
                    if format_name == "vtt"
                    else "application/x-subrip; charset=utf-8"
                )
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename=\"{item.id}.{format_name}\"",
                )
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/api/acquisition/queue":
                self._json(200, acquisition_queue_payload(app.acquisition))
                return

            if path == "/api/acquisition/trash":
                self._json(200, {"items": []})
                return

            if path == "/api/acquisition/maintenance/settings":
                self._json(200, acquisition_maintenance_settings_payload(app.acquisition))
                return

            if path == "/api/textstrata/system-info":
                self._json(200, build_system_info_payload(app))
                return

            if path == "/api/textstrata/setup/status":
                host, port = self.server.server_address  # type: ignore[attr-defined]
                self._json(200, setup_status(app.root, version=__version__, host=host, port=port))
                return

            if path == "/api/textstrata/settings":
                self._json(200, load_settings_payload(app.store))
                return

            if path == "/api/textstrata/review":
                self._json(200, review_queue_payload(app.store, app.items()))
                return

            if path == "/api/textstrata/vocabulary":
                # Current effective map plus any pending inferred proposals.
                synonyms = app.synonyms()
                pending = review.list_pending_synonyms(app.store)
                self._json(200, {
                    "synonyms": synonyms,
                    "pending": pending,
                    "count": len(pending),
                })
                return

            if path == "/api/textstrata/vocabulary/refresh":
                pending = review.refresh_synonym_proposals(app.store, app.items())
                self._json(200, {"pending": pending, "count": len(pending)})
                return

            if path == "/api/textstrata/trash":
                self._json(200, {"items": app.store.list_trash()})
                return

            if path.startswith("/api/textstrata/item/") and path.endswith("/revisions"):
                item_id = path.removeprefix("/api/textstrata/item/").removesuffix("/revisions").strip("/")
                try:
                    self._json(200, {"item_id": item_id, "revisions": app.store.list_revisions(item_id)})
                except ValueError as exc:
                    self._failure(400, "revision-not-found", str(exc))
                return

            if path in {"/api/parity/sync", "/api/acquisition/sync"}:
                self._json(200, app.sync_upstream())
                return

            parity_get = {
                "/api/parity/queue": "/api/queue",
                "/api/parity/trash": "/api/trash",
                "/api/parity/maintenance/settings": "/api/maintenance/settings",
                "/api/parity/library": "/api/library",
            }
            if path in parity_get:
                self._proxy("GET", parity_get[path])
                if path == "/api/parity/queue" and app.gateway:
                    threading.Thread(target=app.sync_upstream, name="textstrata-queue-sync", daemon=True).start()
                return

            if path == "/api/textstrata/graph":
                items = list(app.items())
                synonyms = app.synonyms()
                links = build_links(items)
                edges = build_similarity_edges(items, synonyms=synonyms)
                scores = score_corpus(items, [(link.source, link.target, float(link.weight)) for link in links], synonyms=synonyms)
                self._json(200, build_graph_payload(items, links, edges, scores))
                return

            if path == "/graph":
                items = list(app.items())
                self._html(200, render_graph_html(skin_from_settings(get_settings(app.store))))
                return

            if path == "/new":
                self._html(200, render_new_note_html(skin_from_settings(get_settings(app.store)), version=__version__))
                return

            if path == "/setup":
                host, port = self.server.server_address  # type: ignore[attr-defined]
                status = setup_status(app.root, version=__version__, host=host, port=port)
                self._html(200, render_setup_html(status, skin_from_settings(get_settings(app.store)), version=__version__))
                return

            if path == "/":
                if query.get("panel", [""])[0] == "new":
                    self.send_response(303)
                    self.send_header("Location", "/new")
                    self.end_headers()
                    return
                legacy_view = query.get("view", [""])[0]
                if legacy_view in {"recent", "needs-curation", "untagged"}:
                    self.send_response(303)
                    self.send_header("Location", f"/{legacy_view}")
                    self.end_headers()
                    return
                items = list(app.items())
                dashboard_html, sidebar_extra_html = render_dashboard(items)
                body = render_library_index(items, skin_from_settings(get_settings(app.store)), version=__version__, dashboard_html=dashboard_html, sidebar_extra_html=sidebar_extra_html)
                self._html(200, body)
                return

            if path in {"/recent", "/needs-curation", "/untagged"}:
                view = path.removeprefix("/")
                all_items = list(app.items())
                matches = corpus_view(all_items, view)
                _dashboard_html, sidebar_extra_html = render_dashboard(all_items)
                titles = {"recent": "Recent", "needs-curation": "Needs curation", "untagged": "Untagged"}
                descriptions = {
                    "recent": "The 30 most recently changed notes.",
                    "needs-curation": "Notes missing tags or source context.",
                    "untagged": "Notes without any tags.",
                }
                self._html(200, render_library_index(
                    matches, skin_from_settings(get_settings(app.store)), version=__version__,
                    page_title=titles[view],
                    page_meta=f"{len(matches)} note" + ("" if len(matches) == 1 else "s") + f". {descriptions[view]}",
                    sidebar_extra_html=sidebar_extra_html,
                    empty_title=f"Nothing in {titles[view].lower()}",
                    empty_message='This corpus view is currently empty. <a href="/">View all notes</a>.',
                ))
                return

            if path == "/search":
                q = query.get("q", [""])[0].strip()
                sort = query.get("sort", ["relevance"])[0].strip()
                if sort not in ("relevance", "score", "newest", "oldest"):
                    sort = "relevance"
                contributor_filter = query.get("contributor", [])
                items = list(app.items())
                dashboard_html, sidebar_extra_html = render_dashboard(items)
                if not q:
                    self._html(200, render_library_index(
                        items,
                        skin_from_settings(get_settings(app.store)),
                        version=__version__,
                        page_title="Search",
                        page_meta='Use search to find notes across titles, tags, IDs, and full text. <a href="/">View all notes</a>.',
                        dashboard_html=dashboard_html,
                        sidebar_extra_html=sidebar_extra_html,
                        contributor_filter=contributor_filter,
                    ))
                    return
                try:
                    matched_items, search_reasons = search_library(
                        app.root,
                        items,
                        q,
                        sort=sort,
                        contributor_filter=contributor_filter,
                    )
                except ValueError as exc:
                    self._html(400, render_library_index(
                        [],
                        skin_from_settings(get_settings(app.store)),
                        version=__version__,
                        page_title="Invalid search",
                        page_meta=escape(str(exc)),
                        search_query=q,
                        contributor_filter=contributor_filter,
                        empty_title="Invalid search",
                        empty_message=escape(str(exc)),
                    ))
                    return
                self._html(200, render_library_index(
                    matched_items,
                    skin_from_settings(get_settings(app.store)),
                    version=__version__,
                    page_title=f'Search results for "{escape(q)}"',
                    page_meta=f'{len(matched_items)} result' + ('' if len(matched_items) == 1 else 's') + ' across the knowledge base. <a href="/">View all notes</a>.',
                    search_query=q,
                    sort=sort,
                    sidebar_extra_html=sidebar_extra_html,
                    search_reasons=search_reasons,
                    contributor_filter=contributor_filter,
                    empty_title="No matching notes",
                    empty_message='Try a different search, or <a href="/">view all notes</a>.',
                ))
                return

            if path.startswith("/tag/"):
                tag = unquote(path.removeprefix("/tag/").strip()).lower()
                all_items = list(app.items())
                matches = [it for it in all_items if tag in {t.lower() for t in it.tags}]
                _dashboard_html, sidebar_extra_html = render_dashboard(all_items)
                self._html(200, render_library_index(matches, skin_from_settings(get_settings(app.store)), version=__version__, active_tag=tag, sidebar_extra_html=sidebar_extra_html, empty_title=f"No notes tagged {tag}", empty_message=f'Nothing currently uses the tag <strong>{escape(tag)}</strong>. <a href="/">View all notes</a>.'))
                return

            if path == "/orphaned":
                all_items = list(app.items())
                matches = orphaned_items(all_items, app.store)
                _dashboard_html, sidebar_extra_html = render_dashboard(all_items)
                self._html(200, render_library_index(
                    matches,
                    skin_from_settings(get_settings(app.store)),
                    version=__version__,
                    page_title="Orphaned items",
                    page_meta=f"{len(matches)} item" + ("" if len(matches) == 1 else "s") + " with no cross-links to any other item. <a href=\"/graph\">Inspect the graph</a>.",
                    sidebar_extra_html=sidebar_extra_html,
                    empty_title="Nothing orphaned",
                    empty_message="Every item currently has at least one cross-link. <a href=\"/\">View all notes</a>.",
                ))
                return

            if path.startswith("/community/"):
                community = unquote(path.removeprefix("/community/").strip())
                catalog = Catalog(app.root)
                try:
                    ordered_ids = catalog.community_item_ids(community)
                finally:
                    catalog.close()
                order = {item_id: index for index, item_id in enumerate(ordered_ids)}
                all_items = list(app.items())
                matches = sorted(
                    (item for item in all_items if item.id in order),
                    key=lambda item: order[item.id],
                )
                _dashboard_html, sidebar_extra_html = render_dashboard(all_items)
                self._html(200, render_library_index(
                    matches,
                    skin_from_settings(get_settings(app.store)),
                    version=__version__,
                    page_title=f"Community: {escape(community)}",
                    page_meta=f'{len(matches)} item' + ('' if len(matches) == 1 else 's') + ' in this derived graph community. <a href="/graph">View graph</a>.',
                    sidebar_extra_html=sidebar_extra_html,
                    empty_title="Community not found",
                    empty_message='Run <code>textstrata score</code> to refresh persisted communities.',
                ))
                return

            if path.startswith("/item/"):
                item_id = path.removeprefix("/item/").strip()
                try:
                    item = app.item_by_id(item_id)
                except FileNotFoundError:
                    self._html(404, "<h1>Not found</h1>")
                    return
                corpus = list(app.items())
                raw_path = app.store.normalized_path_for_id(item.id)
                ctx = build_item_render_context(
                    item,
                    corpus,
                    synonyms=app.synonyms(),
                    raw_markdown=raw_path.read_text(encoding="utf-8") if raw_path else None,
                )
                if query.get("format", ["html"])[0] == "text":
                    self._send(200, "text/plain; charset=utf-8", render_text(ctx).encode("utf-8"))
                else:
                    self._html(200, render_item_html(ctx, skin_from_settings(get_settings(app.store)), version=__version__))
                return

            if path == "/media":
                self._html(200, render_media_html(app.acquisition.assets.list_assets(), skin_from_settings(get_settings(app.store)), version=__version__))
                return

            self._html(404, "<h1>Not found</h1>")

        def _confirmed(self) -> bool:
            return any(
                self.headers.get(name, "").lower() == "true"
                for name in ("X-TextStrata-Confirm", "X-Fabric-Confirm")
            )

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urlparse(origin).netloc == self.headers.get("Host", "")

        def _apply_ingest_metadata(self, content: str, metadata: dict[str, str]) -> str:
            if not metadata:
                return content
            lines = [f"{key}: {value}" for key, value in metadata.items() if value]
            if not lines:
                return content
            block = "\n".join(lines)
            if content.startswith("---\n"):
                head, sep, tail = content[4:].partition("\n---\n")
                if sep:
                    return "---\n" + head + "\n" + block + "\n---\n" + tail
            return "---\n" + block + "\n---\n\n" + content

        def _read_ingest_payload(self) -> tuple[str, str, dict[str, str]]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length <= 0:
                raise ValueError("ingest content is required")
            if length > MAX_INGEST_BYTES:
                raise OverflowError("ingest payload exceeds the 5 MiB limit")
            body = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "").strip()
            media_type = content_type.split(";", 1)[0].lower()

            if media_type == "application/json":
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("request body must be valid UTF-8 JSON") from exc
                content = payload.get("content", "") if isinstance(payload, dict) else ""
                fallback = payload.get("filename") or payload.get("title") or "web-ingest" if isinstance(payload, dict) else "web-ingest"
                if not isinstance(content, str):
                    raise ValueError("content must be a string")
                metadata = {}
                if isinstance(payload, dict):
                    for key in ("contributor_chain", "ai_vendor", "ai_model", "ai_operation"):
                        value = payload.get(key)
                        if value is not None and str(value).strip():
                            metadata[key] = str(value).strip()
                return content, str(fallback), metadata

            if media_type == "application/x-www-form-urlencoded":
                try:
                    fields = parse_qs(body.decode("utf-8"), keep_blank_values=True)
                except UnicodeDecodeError as exc:
                    raise ValueError("form content must be UTF-8") from exc
                return fields.get("content", [""])[0], fields.get("title", ["web-ingest"])[0] or "web-ingest", {}

            if media_type == "multipart/form-data":
                try:
                    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
                except UnicodeEncodeError as exc:
                    raise ValueError("invalid multipart Content-Type") from exc
                message = BytesParser(policy=email_policy.default).parsebytes(header + body)
                if not message.is_multipart():
                    raise ValueError("invalid multipart body")
                text_content = ""
                title = "web-ingest"
                file_content = ""
                filename = ""
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    raw = part.get_payload(decode=True) or b""
                    try:
                        value = raw.decode(part.get_content_charset() or "utf-8")
                    except (LookupError, UnicodeDecodeError) as exc:
                        raise ValueError("uploaded file must be UTF-8 text") from exc
                    if name == "file" and part.get_filename() and value:
                        file_content = value
                        filename = part.get_filename() or "upload.md"
                    elif name == "content":
                        text_content = value
                    elif name == "title" and value.strip():
                        title = value.strip()
                if file_content:
                    return file_content, filename, {}
                return text_content, title, {}

            raise ValueError("supported content types are JSON, form data, and multipart form data")

        def _control_post(self, path: str) -> bool:
            is_control = path.startswith("/api/parity/") or path.startswith("/api/acquisition/") or path.startswith("/api/textstrata/")
            if not is_control or path == "/api/textstrata/ingest":
                return False
            if not self._same_origin():
                self._failure(403, "cross-origin-denied", "Cross-origin writes are not allowed.")
                return True
            destructive = (
                path.endswith("/cancel") or path.endswith("/purge-output") or path.endswith("/empty")
                or path.endswith("/restart") or path.endswith("/clear-completed") or "/purge" in path
                or path.endswith("/trash") or path.endswith("/maintenance/settings")
            )
            if destructive and not self._confirmed():
                self._failure(409, "confirmation-required", "Confirm this operation in the frontend before retrying.")
                return True
            try:
                if path == "/api/acquisition/queue/clear-completed":
                    self._json(200, clear_acquisition_completed(app.acquisition))
                    return True
                if path == "/api/acquisition/trash/empty":
                    self._json(200, {"purged": 0})
                    return True
                if path == "/api/acquisition/maintenance/settings":
                    self._json(200, save_acquisition_maintenance_settings(app.acquisition, self._read_json_body()))
                    return True
                if path == "/api/acquisition/maintenance/restart":
                    self._json(200, {"rechecked": True, "capabilities": capabilities()})
                    return True
                if path.startswith("/api/acquisition/queue/"):
                    rest = path.removeprefix("/api/acquisition/queue/").strip("/")
                    if rest.endswith("/retry"):
                        job_id = int(rest.removesuffix("/retry"))
                        app.acquisition.retry(job_id)
                        self._json(200, {"queued": job_id})
                        return True
                    if rest.endswith("/cancel"):
                        app.acquisition.cancel(int(rest.removesuffix("/cancel")))
                        self._json(200, {"cancelled": int(rest.removesuffix("/cancel"))})
                        return True
                    if rest.endswith("/purge-output"):
                        self._json(200, app.acquisition.purge_output(int(rest.removesuffix("/purge-output"))))
                        return True
                if path.startswith("/api/acquisition/channel/") and path.endswith("/purge"):
                    handle = unquote(path.removeprefix("/api/acquisition/channel/").removesuffix("/purge").strip("/"))
                    self._json(200, {"purged": app.acquisition.purge_channel(handle)})
                    return True
                if path == "/api/parity/ingest":
                    body = self._read_raw(MAX_ACQUIRE_BYTES)
                    self._proxy("POST", "/api/ingest", body)
                    return True
                if path == "/api/parity/sync":
                    self._json(200, app.sync_upstream())
                    return True
                parity = path.replace("/api/parity", "/api", 1)
                parity_prefixes = ("/api/queue/", "/api/trash/", "/api/channel/", "/api/maintenance/")
                parity_fixed = {"/api/queue/clear-completed", "/api/trash/empty"}
                if parity in parity_fixed or parity.startswith(parity_prefixes):
                    body = self._read_raw(MAX_INGEST_BYTES)
                    self._proxy("POST", parity, body if body else None)
                    return True
                if path == "/api/textstrata/settings":
                    self._json(200, save_settings_payload(app.store, self._read_json_body()))
                    return True
                if path == "/api/textstrata/setup/initialize":
                    host, port = self.server.server_address  # type: ignore[attr-defined]
                    self._json(200, initialize_workspace(app.root, version=__version__, host=host, port=port))
                    return True
                if path == "/api/textstrata/restart":
                    self._json(200, {"restarting": True})
                    # Graceful: the response above is fully written before
                    # this handler returns; server.shutdown() then stops the
                    # accept loop and serve() runs its normal cleanup
                    # (catalog close, WAL checkpoint, server_close) before
                    # exiting 0 with the PID file left as the restart signal.
                    threading.Thread(target=app.request_restart, daemon=True).start()
                    return True
                if path == "/api/textstrata/review/confirm":
                    body = self._read_json_body()
                    item_id = str(body.get("item_id", ""))
                    tags = body.get("tags")
                    result = review.confirm_tags(app.store, item_id, tags)
                    if result is None:
                        self._failure(404, "operation-failed", "Review entry not found.")
                        return True
                    self._json(200, result)
                    return True
                if path == "/api/textstrata/review/reject":
                    body = self._read_json_body()
                    item_id = str(body.get("item_id", ""))
                    result = review.reject_suggestions(app.store, item_id)
                    if result is None:
                        self._failure(404, "operation-failed", "Review entry not found.")
                        return True
                    self._json(200, result)
                    return True
                if path == "/api/textstrata/review/dismiss":
                    body = self._read_json_body()
                    item_id = str(body.get("item_id", ""))
                    ok = review.dismiss(app.store, item_id)
                    if not ok:
                        self._failure(404, "operation-failed", "Review entry not found.")
                        return True
                    self._json(200, {"dismissed": item_id})
                    return True
                if path == "/api/textstrata/vocabulary/confirm":
                    body = self._read_json_body()
                    variant = str(body.get("variant", "")).strip().lower()
                    canonical = str(body.get("canonical", "")).strip().lower()
                    if not variant or not canonical or variant == canonical:
                        self._failure(400, "operation-failed", "variant and canonical are required and must differ.")
                        return True
                    result = review.confirm_synonym(app.store, variant, canonical)
                    # New mapping changes graph scoring — drop the cached map.
                    app.invalidate_synonyms()
                    self._json(200, result)
                    return True
                if path == "/api/textstrata/vocabulary/reject":
                    body = self._read_json_body()
                    variant = str(body.get("variant", "")).strip().lower()
                    canonical = str(body.get("canonical", "")).strip().lower()
                    result = review.reject_synonym(app.store, variant, canonical)
                    if result is None:
                        self._failure(404, "operation-failed", "Proposal not found.")
                        return True
                    self._json(200, result)
                    return True
                item_save = _ITEM_SAVE_RE.match(path)
                if item_save:
                    item_id = unquote(item_save.group(1))
                    raw_text = self._read_raw_text()
                    if not raw_text.strip():
                        raise ValueError("item content is required")
                    current_path = app.store.normalized_path_for_id(item_id)
                    existing_item = app.item_by_id(item_id) if current_path is not None else None
                    existing_raw = current_path.read_text(encoding="utf-8") if current_path else ""
                    if current_path is not None and not raw_text.lstrip().startswith("---"):
                        if existing_raw.startswith("---\n") and "\n---\n" in existing_raw[4:]:
                            _front, _sep, _body = existing_raw[4:].partition("\n---\n")
                            raw_text = f"---\n{_front}\n---\n\n{raw_text.strip()}\n"
                    item, suggested, fm = build_item(raw_text, fallback_id=item_id)
                    if existing_item is not None:
                        item.provenance.ingested_at = existing_item.provenance.ingested_at
                        if not item.provenance.source_url:
                            item.provenance.source_url = existing_item.provenance.source_url
                        if not item.provenance.authorship:
                            item.provenance.authorship = existing_item.provenance.authorship
                        # Carry forward existing contributor chain; append if header present and content changed.
                        content_changed = existing_raw != raw_text
                        contributor = (
                            self.headers.get("X-TextStrata-Contributor")
                            or self.headers.get("X-Fabric-Contributor", "")
                        ).strip().lower()
                        if contributor in VALID_CONTRIBUTORS and content_changed:
                            chain = append_contributor(
                                existing_item.provenance.contributor_chain, contributor
                            )
                        else:
                            chain = existing_item.provenance.contributor_chain
                        item.provenance.contributor_chain = chain
                    item.extra = {**item.extra, "last_edited_at": datetime.now(timezone.utc).isoformat()}
                    result = validate(item)
                    app.store.save_original(item.id, raw_text)
                    if not result.ok:
                        raise ValueError("; ".join(result.errors) or "Content validation failed.")
                    app.store.publish_normalized(item)
                    catalog = Catalog(app.root)
                    try:
                        catalog.index_item(item)
                        # If the save changed the item's id, drop the old row
                        # (only when the old normalized file is truly gone).
                        if item.id != item_id and app.store.normalized_path_for_id(item_id) is None:
                            catalog.remove_item(item_id)
                    finally:
                        catalog.close()
                    self._json(200, {"saved": True, "item_id": item.id, "title": item.title})
                    return True
                item_rename = _ITEM_RENAME_RE.match(path)
                if item_rename:
                    item_id = unquote(item_rename.group(1))
                    new_id = str(self._read_json_body().get("new_id") or "").strip()
                    result = rename_item(app.store, item_id, new_id)
                    self._json(200, result)
                    return True
                item_aliases = _ITEM_ALIASES_RE.match(path)
                if item_aliases:
                    item_id = unquote(item_aliases.group(1))
                    payload = self._read_json_body()
                    aliases = payload.get("aliases") if isinstance(payload.get("aliases"), list) else []
                    self._json(200, update_aliases(app.store, item_id, [str(alias) for alias in aliases]))
                    return True
                if path.startswith("/api/textstrata/item/") and path.endswith("/tags/remove"):
                    item_id = unquote(path.removeprefix("/api/textstrata/item/").removesuffix("/tags/remove").strip("/"))
                    tag = str(self._read_json_body().get("tag", "")).strip()
                    if not tag:
                        raise ValueError("tag is required")
                    item = app.item_by_id(item_id)
                    remaining = [existing for existing in item.tags if existing.lower() != tag.lower()]
                    if len(remaining) == len(item.tags):
                        self._failure(404, "operation-failed", "Item does not have that tag.")
                        return True
                    item.tags = remaining
                    app.store.publish_normalized(item)
                    catalog = Catalog(app.root)
                    try:
                        catalog.index_item(item)
                    finally:
                        catalog.close()
                    self._json(200, {"item_id": item.id, "tags": item.tags})
                    return True
                if path.startswith("/api/textstrata/item/") and path.endswith("/trash"):
                    item_id = unquote(path.removeprefix("/api/textstrata/item/").removesuffix("/trash").strip("/"))
                    item = app.item_by_id(item_id)
                    upstream_path = item.extra.get("upstream_path")
                    if upstream_path and app.gateway:
                        app.gateway.request("DELETE", "/api/item/" + quote(str(upstream_path), safe="/"))
                    trashed = app.store.trash_item(item_id)
                    # Update the catalog BEFORE responding so a client that
                    # searches immediately after the 200 sees the removal.
                    catalog = Catalog(app.root)
                    try:
                        catalog.remove_item(item_id)
                    finally:
                        catalog.close()
                    self._json(200, trashed)
                    return True
                if path.startswith("/api/textstrata/item/") and "/revisions/" in path and path.endswith("/restore"):
                    rest = path.removeprefix("/api/textstrata/item/").removesuffix("/restore").strip("/")
                    item_id, revision = rest.split("/revisions/", 1)
                    app.store.restore_revision(unquote(item_id), unquote(revision))
                    catalog = Catalog(app.root)
                    try:
                        catalog.index_item(app.item_by_id(unquote(item_id)))
                    finally:
                        catalog.close()
                    self._json(200, {"restored": unquote(item_id), "revision": unquote(revision)})
                    return True
                if path.startswith("/api/textstrata/trash/") and path.endswith("/restore"):
                    name = unquote(path.removeprefix("/api/textstrata/trash/").removesuffix("/restore").strip("/"))
                    item_id = app.store.restore_trash(name)
                    catalog = Catalog(app.root)
                    try:
                        catalog.index_item(app.item_by_id(item_id))
                    finally:
                        catalog.close()
                    self._json(200, {"restored": item_id})
                    return True
                if path == "/api/textstrata/trash/empty":
                    self._json(200, {"purged": app.store.purge_trash()})
                    return True
            except GatewayError as exc:
                self._failure(exc.status, exc.code, str(exc))
                return True
            except OverflowError as exc:
                self._failure(413, "ingest-too-large", str(exc))
                return True
            except (ValueError, FileNotFoundError) as exc:
                self._failure(400, "operation-failed", str(exc))
                return True
            except FileExistsError as exc:
                self._failure(409, "trash-conflict", str(exc))
                return True
            self._failure(404, "operation-failed", "Unknown TextStrata operation.")
            return True

        def do_POST(self) -> None:
            path = _canonical_path(urlparse(self.path).path.rstrip("/") or "/")
            if path == "/api/asset/upload":
                ct = self.headers.get("Content-Type", "")
                if "multipart/form-data" not in ct:
                    self._failure(400, "asset-invalid", "multipart form data required")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > MAX_ACQUIRE_BYTES:
                        raise OverflowError("payload too large")
                    header = f"Content-Type: {ct}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
                    data = self.rfile.read(length)
                    message = BytesParser(policy=email_policy.default).parsebytes(header + data)
                    if not message.is_multipart():
                        raise ValueError("invalid multipart body")
                    for part in message.iter_parts():
                        raw = part.get_payload(decode=True) or b""
                        name = part.get_param("name", header="content-disposition")
                        filename = (part.get_filename() or f"upload-{name or 'asset'}").strip()
                        if name in ("file", "image", "asset") and raw:
                            asset = app.acquisition.assets.put(raw, filename)
                            self._json(201, {"id": asset.id, "media_type": asset.media_type, "size": asset.size, "url": f"/asset/{asset.id}"})
                            return
                    raise ValueError("no file in request")
                except (ValueError, OverflowError) as exc:
                    self._failure(400, "asset-invalid", str(exc))
                return
            if path == "/api/acquisition/ingest":
                if not self._same_origin():
                    self._failure(403, "cross-origin-denied", "Cross-origin ingestion is not allowed.")
                    return
                try:
                    media_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    if media_type == "multipart/form-data":
                        fields = parse_acquisition_multipart(self.headers.get("Content-Type", ""), self._read_raw(MAX_ACQUIRE_BYTES))
                    elif media_type == "application/json":
                        fields = self._read_json_body()
                    else:
                        raise ValueError("acquisition requires multipart form data or JSON")
                    self._json(202, build_ingest_submission(app.acquisition, fields))
                except OverflowError as exc:
                    self._failure(413, "ingest-too-large", str(exc))
                except (ValueError, UnicodeDecodeError) as exc:
                    self._failure(400, "ingest-invalid", str(exc))
                return
            if self._control_post(path):
                return
            if path not in {"/ingest", "/api/ingest"}:
                self._json(404, {"error": "not found"})
                return
            if not self._same_origin():
                self._failure(403, "cross-origin-denied", "Cross-origin ingestion is not allowed.")
                return
            try:
                content, fallback, metadata = self._read_ingest_payload()
            except OverflowError as exc:
                self._failure(413, "ingest-too-large", str(exc))
                return
            except ValueError as exc:
                self._failure(400, "ingest-invalid", str(exc))
                return
            if not content.strip():
                self._failure(400, "ingest-invalid", "Ingest content is required.")
                return

            content = self._apply_ingest_metadata(content, metadata)
            result = ingest_text(app.store, content, fallback_id=Path(fallback).stem or "web-ingest")
            payload = {
                "published": result.published,
                "item_id": result.item.id,
                "title": result.item.title,
                "type": result.item.type.value,
                "suggested_tags": result.suggested_tags,
                "warnings": result.validation.warnings,
                "errors": result.validation.errors,
            }
            if not result.published:
                record_error(app.store, "ingest-invalid")
                payload.update(error_payload("ingest-invalid", "; ".join(result.validation.errors) or "Content validation failed."))
                self._json(422, payload)
                return

            catalog = Catalog(app.root)
            try:
                catalog.index_item(result.item)
            finally:
                catalog.close()

            if result.suggested_tags:
                policy = classify.suggest_policy(result.item.type, result.item.title, result.item.body)
                review.enqueue(
                    app.store,
                    result.item.id,
                    result.item.title,
                    result.suggested_tags,
                    policy_handling=policy.handling.value,
                    policy_preservation=policy.preservation.value,
                    policy_reason=policy.rationale,
                )

            if self.headers.get("Content-Type", "").split(";", 1)[0].lower() == "application/json":
                self._json(201, payload)
            else:
                self.send_response(303)
                self.send_header("Location", f"/item/{result.item.id}")
                self.send_header("Content-Length", "0")
                self.end_headers()

        def do_DELETE(self) -> None:
            path = _canonical_path(urlparse(self.path).path.rstrip("/") or "/")
            if not self._same_origin():
                self._failure(403, "cross-origin-denied", "Cross-origin writes are not allowed.")
                return
            if not self._confirmed():
                self._failure(409, "confirmation-required", "Confirm permanent deletion in the frontend before retrying.")
                return
            if path.startswith("/api/acquisition/queue/"):
                try:
                    app.acquisition.delete(int(path.removeprefix("/api/acquisition/queue/").strip("/")))
                    self._json(200, {"deleted": True})
                except (ValueError, FileNotFoundError) as exc:
                    self._failure(400, "operation-failed", str(exc))
                return
            if path.startswith("/api/parity/queue/") or path.startswith("/api/parity/trash/"):
                self._proxy("DELETE", path.replace("/api/parity", "/api", 1))
                return
            if path.startswith("/api/textstrata/trash/"):
                name = unquote(path.removeprefix("/api/textstrata/trash/").strip("/"))
                try:
                    self._json(200, {"purged": app.store.purge_trash(name)})
                except (ValueError, FileNotFoundError) as exc:
                    self._failure(404, "operation-failed", str(exc))
                return
            self._failure(404, "operation-failed", "Unknown delete operation.")

    return Handler


def serve(workspace_root: Path, host: str = "0.0.0.0", port: int = 8700) -> None:
    import socket as _socket
    app = TextStrataWebApp(workspace_root)
    handler = create_handler(app)

    # Write the PID file BEFORE the server binds/listens so there is no
    # window where the server accepts connections but cmd_restart / the
    # textstrata-server wrapper can't see who owns the port.
    pid_path = app.store.metadata_dir / "server.pid"
    try:
        pid_path.write_text(str(os.getpid()))
    except OSError:
        pass

    ThreadingHTTPServer.allow_reuse_address = True
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError:
        # Bind failed (e.g. port contention) — don't leave a PID file that
        # points at a process which never served anything.
        pid_path.unlink(missing_ok=True)
        app.close()
        raise
    server.socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    server.daemon_threads = True
    app._server = server

    try:
        print(f"textstrata web listening on http://{host}:{port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # On a graceful restart request the PID file is deliberately kept:
        # textstrata-server treats "exit 0 + PID file present" as the restart
        # signal, and "exit 0 + PID file gone" as a clean stop.
        if not app._restart_requested.is_set():
            pid_path.unlink(missing_ok=True)
        app.close()
        server.server_close()


def main() -> None:
    workspace_root = resolve_workspace()
    config = load_cascading_config(workspace_root)
    apply_config_environment(config)
    network = config.get("network", {})
    if not isinstance(network, dict):
        network = {}
    host = os.environ.get("TEXTSTRATA_HOST") or os.environ.get("FABRIC_HOST", str(network.get("host", "0.0.0.0")))
    port = int(os.environ.get("TEXTSTRATA_PORT") or os.environ.get("FABRIC_PORT", network.get("port", 8700)))
    serve(workspace_root=workspace_root, host=host, port=port)
