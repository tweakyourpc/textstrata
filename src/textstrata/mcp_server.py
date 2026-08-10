"""Minimal MCP-style stdio server for TextStrata.

The server speaks JSON-RPC 2.0 over stdio with content-length framing. It is
intentionally small and dependency-free so local agents can read and write the
textstrata without adding a hidden runtime stack.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__, activity, classify, linking, operations, research, review, similarity
from .analyze import analyze as analyze_gaps
from .catalog import Catalog
from .ingest import build_item, ingest_text
from .models import TextStrataItem
from .presentation import PAPER_SKIN, RenderContext, render_item_html, render_text
from .store import TextStrataStore
from .validate import validate
from .workspace import apply_config_environment, load_cascading_config, resolve_workspace

# System doc seeds are auto-ingested on startup. Path relative to this file:
#   src/textstrata/mcp_server.py -> ../../seed/
_SEED_DIR = Path(__file__).resolve().parent.parent.parent / "seed"
_SYSTEM_SEEDS = [
    "system.help-index.md",
    "system.manual.md",
    "system.developer-guide.md",
    "system.ai-manifest.md",
    "system.changelog.md",
]


def _seed_system_docs(store: TextStrataStore) -> None:
    """Auto-ingest system documentation items if not already present."""
    for name in _SYSTEM_SEEDS:
        path = _SEED_DIR / name
        if not path.exists():
            continue
        item_id = name.replace(".md", "")
        if store.normalized_path_for_id(item_id):
            continue
        text = path.read_text(encoding="utf-8")
        result = ingest_text(store, text, fallback_id=item_id)
        if result.published:
            activity.write(store.root, "seed", item_id=item_id, outcome="seeded")


def _item_to_dict(item: TextStrataItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": item.type.value,
        "title": item.title,
        "tags": list(item.tags),
        "related": list(item.related),
        "dependencies": list(item.dependencies),
        "handling": item.handling.value,
        "preservation": item.preservation.value,
        "retrieval_priority": item.retrieval_priority,
        "provenance": {k: v for k, v in item.provenance.to_dict().items() if v is not None},
        "extra": dict(item.extra) if item.extra else {},
        "contributor_chain": item.provenance.contributor_chain,
    }


class TextStrataMCP:
    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = TextStrataStore(self.root)
        self.catalog = Catalog(self.root)
        self._all_items_cache: list[TextStrataItem] | None = None
        # MCP clients can provide identity once in the server environment so
        # every tool call does not need to repeat it. Explicit tool arguments
        # still win, and an unconfigured server remains safely fail-closed.
        self.ai_vendor = (os.environ.get("TEXTSTRATA_AI_VENDOR") or os.environ.get("MARKBASE_AI_VENDOR", "")).strip()
        self.ai_model = (
            (os.environ.get("TEXTSTRATA_AI_MODEL") or os.environ.get("MARKBASE_AI_MODEL", "")).strip()
            or os.environ.get("CODEX_MODEL", "").strip()
            or os.environ.get("OPENAI_MODEL", "").strip()
            or os.environ.get("ANTHROPIC_MODEL", "").strip()
            or os.environ.get("CLAUDE_MODEL", "").strip()
        )
        self.ai_author = (os.environ.get("TEXTSTRATA_AI_AUTHOR") or os.environ.get("MARKBASE_AI_AUTHOR", "")).strip()
        _seed_system_docs(self.store)
        operations.ensure_article(self.store)

    def close(self) -> None:
        self.catalog.close()

    def _invalidate_cache(self) -> None:
        self._all_items_cache = None

    def _read_item(self, item_id: str) -> TextStrataItem:
        path = self.store.normalized_path_for_id(item_id)
        if path is None:
            raise FileNotFoundError(item_id)
        item, _, _ = build_item(path.read_text(encoding="utf-8"), fallback_id=item_id)
        return item

    def _all_items(self) -> list[TextStrataItem]:
        if self._all_items_cache is not None:
            return self._all_items_cache
        items: list[TextStrataItem] = []
        for path in self.store.normalized_paths():
            item, _, _ = build_item(path.read_text(encoding="utf-8"), fallback_id=path.stem)
            items.append(item)
        self._all_items_cache = items
        return items

    # ------------------------------------------------------------------ #
    # Resources
    # ------------------------------------------------------------------ #

    def list_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": "textstrata://items",
                "name": "All Items",
                "description": "List of all items in the knowledge base",
                "mimeType": "application/json",
            },
            {
                "uri": "textstrata://tags",
                "name": "Tag Index",
                "description": "All tags with item counts",
                "mimeType": "application/json",
            },
            {
                "uri": "textstrata://types",
                "name": "Type Index",
                "description": "All content types with item counts",
                "mimeType": "application/json",
            },
            {
                "uri": "textstrata://stats",
                "name": "Knowledge Base Stats",
                "description": "Summary statistics for the knowledge base",
                "mimeType": "application/json",
            },
            {
                "uri": "textstrata://activity",
                "name": "Activity Log",
                "description": "Recent operations and events",
                "mimeType": "application/json",
            },
            {
                "uri": "textstrata://settings",
                "name": "TextStrata Settings",
                "description": "Current server and presentation settings",
                "mimeType": "application/json",
            },
            {
                "uri": "textstrata://health",
                "name": "Server Health",
                "description": "Server health, version, and item count",
                "mimeType": "application/json",
            },
            {
                "uri": "textstrata://dashboard",
                "name": "Dashboard Summary",
                "description": "Recent items, needs-curation items, and top tags",
                "mimeType": "application/json",
            },
            {
                "uri": "textstrata://graph/scoreboard",
                "name": "Knowledge Scoreboard",
                "description": "All items ranked by knowledge score",
                "mimeType": "application/json",
            },
        ]

    def resource_templates(self) -> list[dict[str, Any]]:
        return [
            {
                "uriTemplate": "textstrata://items/{item_id}",
                "name": "Single Item",
                "description": "A single knowledge base item by ID",
                "mimeType": "text/markdown",
            },
            {
                "uriTemplate": "textstrata://items/{item_id}/json",
                "name": "Single Item (JSON)",
                "description": "A single knowledge base item as structured JSON",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "textstrata://graph/similar/{item_id}",
                "name": "Similar Items",
                "description": "Items similar to a given item",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "textstrata://graph/links/{item_id}",
                "name": "Item Links",
                "description": "Cross-links for a given item",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "textstrata://graph/knowledge-score/{item_id}",
                "name": "Item Knowledge Score",
                "description": "Knowledge graph score for a single item",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "textstrata://items/{item_id}/history",
                "name": "Item Revision History",
                "description": "Revision history for a single item",
                "mimeType": "application/json",
            },
        ]

    def read_resource(self, uri: str) -> dict[str, Any]:
        items = self._all_items()

        if uri == "textstrata://items":
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps([_item_to_dict(it) for it in items], indent=2, default=str),
                }]
            }

        if uri == "textstrata://tags":
            from collections import Counter
            counts: Counter[str] = Counter()
            for it in items:
                counts.update(t.lower() for t in it.tags)
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(dict(counts.most_common()), indent=2),
                }]
            }

        if uri == "textstrata://types":
            from collections import Counter
            counts: Counter[str] = Counter()
            for it in items:
                counts[it.type.value] += 1
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(dict(counts.most_common()), indent=2),
                }]
            }

        if uri == "textstrata://stats":
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(self._compute_stats(items), indent=2),
                }]
            }

        if uri == "textstrata://activity":
            entries = activity.read(self.store.root, limit=50)
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(entries, indent=2, default=str),
                }]
            }

        if uri == "textstrata://settings":
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(operations.get_settings(self.store), indent=2),
                }]
            }

        if uri == "textstrata://health":
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps({
                        "service": "textstrata",
                        "version": __version__,
                        "item_count": len(items),
                        "store_root": str(self.store.root),
                    }, indent=2),
                }]
            }

        if uri == "textstrata://dashboard":
            recent = sorted(items, key=lambda it: it.provenance.ingested_at or "", reverse=True)[:5]
            needs_tags = [it for it in items if not it.tags][:5]
            from collections import Counter
            tag_counts: Counter[str] = Counter()
            for it in items:
                tag_counts.update(t.lower() for t in it.tags)
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps({
                        "recent": [{"id": it.id, "title": it.title, "type": it.type.value} for it in recent],
                        "needs_tags": [{"id": it.id, "title": it.title, "type": it.type.value} for it in needs_tags],
                        "top_tags": dict(tag_counts.most_common(10)),
                    }, indent=2),
                }]
            }

        if uri == "textstrata://graph/scoreboard":
            scores = self._all_scores(items)
            board = [
                {"item_id": sid, "score": ks.score, "community": ks.community,
                 "pagerank": ks.pagerank, "authority": ks.authority,
                 "degree": ks.degree, "neighbours": len(ks.neighbours)}
                for sid, ks in sorted(scores.items(), key=lambda x: -x[1].score)
            ]
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(board, indent=2),
                }]
            }

        # Template URIs -- parse the path
        parts = uri.removeprefix("textstrata://").split("/")

        if len(parts) == 2 and parts[0] == "items":
            item_id = parts[1]
            item = self._read_item(item_id)
            ctx = RenderContext(title=item.title, item=item, validation_errors=[], validation_warnings=[], suggested_tags=[])
            text = render_text(ctx)
            return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}

        if len(parts) == 3 and parts[0] == "items" and parts[2] == "json":
            item_id = parts[1]
            item = self._read_item(item_id)
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(_item_to_dict(item), indent=2, default=str),
                }]
            }

        if len(parts) == 3 and parts[0] == "items" and parts[2] == "history":
            item_id = parts[1]
            revisions = self.store.list_revisions(item_id)
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(revisions, indent=2, default=str),
                }]
            }

        if len(parts) == 3 and parts[0] == "graph" and parts[1] == "similar":
            item_id = parts[2]
            if not any(it.id == item_id for it in items):
                raise FileNotFoundError(f"item not found: {item_id}")
            edges = similarity.build_similarity_edges(items, threshold=0.08, top_k=8)
            related = [e for e in edges if e.source == item_id]
            result = [
                {"target": e.target, "score": round(e.score, 4),
                 "content": round(e.content, 4), "tag": round(e.tag, 4),
                 "shared_terms": list(e.shared_terms)}
                for e in sorted(related, key=lambda x: -x.score)
            ]
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(result, indent=2),
                }]
            }

        if len(parts) == 3 and parts[0] == "graph" and parts[1] == "links":
            item_id = parts[2]
            if not any(it.id == item_id for it in items):
                raise FileNotFoundError(f"item not found: {item_id}")
            all_links = linking.build_links(items)
            outbound = linking.links_for(item_id, all_links)
            inbound = [ln for ln in all_links if ln.target == item_id]
            result = {
                "outbound": [{"target": ln.target, "reason": ln.reason, "weight": ln.weight} for ln in outbound],
                "inbound": [{"source": ln.source, "reason": ln.reason, "weight": ln.weight} for ln in inbound],
            }
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(result, indent=2),
                }]
            }

        if len(parts) == 3 and parts[0] == "graph" and parts[1] == "knowledge-score":
            item_id = parts[2]
            scores = self._all_scores(items)
            ks = scores.get(item_id)
            if ks is None:
                raise FileNotFoundError(f"no score for item: {item_id}")
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps({
                        "item_id": ks.item_id,
                        "score": ks.score,
                        "pagerank": ks.pagerank,
                        "hub": ks.hub,
                        "authority": ks.authority,
                        "degree": ks.degree,
                        "community": ks.community,
                        "neighbours": ks.neighbours,
                    }, indent=2),
                }]
            }

        raise ValueError(f"unknown resource: {uri}")

    def _compute_stats(self, items: list[TextStrataItem]) -> dict[str, Any]:
        from collections import Counter
        by_type: Counter[str] = Counter()
        tag_counts: Counter[str] = Counter()
        total_tags: set[str] = set()
        total_links = 0
        for it in items:
            by_type[it.type.value] += 1
            tag_counts.update(t.lower() for t in it.tags)
            total_tags.update(t.lower() for t in it.tags)
            total_links += len(it.related) + len(it.dependencies)
        return {
            "total_items": len(items),
            "by_type": dict(by_type.most_common()),
            "total_tags": len(total_tags),
            "top_tags": dict(tag_counts.most_common(15)),
            "total_cross_links": total_links,
        }

    def _all_scores(self, items: list[TextStrataItem]) -> dict[str, similarity.KnowledgeScore]:
        all_links = linking.build_links(items)
        explicit = [(ln.source, ln.target, float(ln.weight)) for ln in all_links]
        return similarity.score_corpus(items, explicit_edges=explicit)

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_knowledge",
                "description": "Search the local TextStrata index.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_items",
                "description": "List all published normalized items.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "read_item",
                "description": "Read one normalized item by id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "preview_item",
                "description": "Preview a local file before ingestion.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "fallback_id": {"type": "string"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "ingest_text",
                "description": "Ingest raw text into the textstrata.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "fallback_id": {"type": "string"},
                        "ai_vendor": {"type": "string"},
                        "ai_model": {"type": "string"},
                        "ai_operation": {"type": "string"},
                        "authorship": {"type": "string", "description": "Human-readable agent name; defaults from the configured MCP identity."},
                    },
                    "required": ["content"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "render_item",
                "description": "Render an item as text or HTML using the presentation skin.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "format": {"type": "string", "enum": ["text", "html"]},
                    },
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_similar",
                "description": "Find items similar to a given item using TF-IDF + tag Jaccard similarity.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "threshold": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_links",
                "description": "Show inbound and outbound cross-links for an item.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_knowledge_scores",
                "description": "Get knowledge graph scores for all items or a single item.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            {
                "name": "analyze_gaps",
                "description": "Run a knowledge base gap analysis (untagged, orphaned, stale, missing fields).",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_stats",
                "description": "Get summary statistics about the knowledge base.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_activity",
                "description": "Get recent activity from the operation log.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_settings",
                "description": "Get current textstrata settings (skin, paths, revision limit).",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "delete_item",
                "description": "Move an item to the trash.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "research_query",
                "description": "Ask a question answered from the knowledge base using RAG (requires Ollama).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "model": {"type": "string"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "synthesize_topic",
                "description": "Synthesize a comprehensive briefing on a topic from all relevant KB items.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "model": {"type": "string"},
                    },
                    "required": ["topic"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "export_hugo",
                "description": "Export items as Hugo-compatible page bundles.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "description": "Export a single item by ID. Omit to export all."},
                        "site_section": {"type": "string", "description": "Hugo section to place pages under."},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_dashboard",
                "description": "Get a dashboard summary of recent items, needs-curation items, and top tags.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_pending_reviews",
                "description": "List all items pending human review of auto-suggested tags.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "confirm_review",
                "description": "Confirm suggested tags for a pending review entry.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional: specific tags to confirm. Defaults to all suggested.",
                        },
                    },
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "reject_review",
                "description": "Reject/dismiss a pending review entry without applying suggested tags.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "propose_note",
                "description": "Queue a new note draft for human review without publishing it.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "source_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "body"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "propose_tags",
                "description": "Queue tag additions for an existing item without changing it.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                    },
                    "required": ["item_id", "tags"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "propose_synonym",
                "description": "Queue a synonym mapping for human review without applying it.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "variant": {"type": "string"},
                        "canonical": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["variant", "canonical"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_agent_proposals",
                "description": "List pending note, tag, and synonym proposals from agents.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        ]

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}

        if name == "search_knowledge":
            self._invalidate_cache()
            limit = max(1, min(int(arguments.get("limit", 5)), 20))
            self.catalog.rescan(self.store)
            hits = self.catalog.search(arguments["query"])[:limit]
            text = "\n".join(
                f"{hit.id} [{hit.type}] {hit.title}\n{hit.snippet.strip()}"
                for hit in hits
            ) or "no matches"
            return {"content": [{"type": "text", "text": text}]}

        if name == "list_items":
            items = self._all_items()
            text = "\n".join(f"{item.id} [{item.type.value}] {item.title}" for item in items) or "no items"
            return {"content": [{"type": "text", "text": text}]}

        if name == "read_item":
            item = self._read_item(arguments["item_id"])
            ctx = RenderContext(title=item.title, item=item, validation_errors=[], validation_warnings=[], suggested_tags=[])
            return {"content": [{"type": "text", "text": render_text(ctx)}]}

        if name == "preview_item":
            path = Path(arguments["path"])
            item, suggested, fm = build_item(path.read_text(encoding="utf-8"), fallback_id=arguments.get("fallback_id") or path.stem)
            v = validate(item)
            policy = classify.suggest_policy(item.type, item.title, item.body)
            ctx = RenderContext(
                title=item.title,
                item=item,
                validation_errors=v.errors,
                validation_warnings=v.warnings,
                suggested_tags=suggested,
                policy_handling=policy.handling.value,
                policy_preservation=policy.preservation.value,
                policy_reason=policy.rationale,
            )
            text = render_text(ctx)
            return {"content": [{"type": "text", "text": text}]}

        if name == "ingest_text":
            self._invalidate_cache()
            content = arguments["content"]
            ai_vendor = str(arguments.get("ai_vendor") or self.ai_vendor).strip()
            ai_model = str(arguments.get("ai_model") or self.ai_model).strip()
            if not ai_vendor or not ai_model:
                return {"content": [{"type": "text", "text": "rejected: AI-authored MCP writes require ai_vendor and ai_model; configure TEXTSTRATA_AI_VENDOR and TEXTSTRATA_AI_MODEL for the MCP server, or pass them per call"}], "isError": True}
            authorship = str(arguments.get("authorship") or self.ai_author).strip()
            if not authorship:
                authorship = "Codex" if ai_vendor.casefold() == "openai" else "Claude Code" if ai_vendor.casefold() == "anthropic" else ai_vendor
            # If the incoming text has no contributor_chain in frontmatter,
            # default to via_ai (MCP is the agent-facing API).
            if "contributor_chain" not in content[:500]:
                if content.startswith("---\n"):
                    # Inject into the existing frontmatter block.
                    head, sep, tail = content[4:].partition("\n---\n")
                    content = "---\n" + head + "\ncontributor_chain: via_ai\n---\n" + tail
                else:
                    content = "---\ncontributor_chain: via_ai\n---\n\n" + content
            metadata = {
                "authorship": authorship,
                "ai_vendor": ai_vendor,
                "ai_model": ai_model,
                "ai_operation": str(arguments.get("ai_operation") or "authored").strip(),
            }
            if metadata:
                if content.startswith("---\n"):
                    head, sep, tail = content[4:].partition("\n---\n")
                    content = "---\n" + head + "\n" + "\n".join(f"{k}: {v}" for k, v in metadata.items()) + "\n---\n" + tail
                else:
                    content = "---\n" + "\n".join(f"{k}: {v}" for k, v in metadata.items()) + "\n---\n\n" + content
            res = ingest_text(self.store, content, fallback_id=arguments.get("fallback_id"))
            status = "published" if res.published else "rejected"
            if res.suggested_tags and res.published:
                policy = classify.suggest_policy(res.item.type, res.item.title, res.item.body)
                review.enqueue(
                    self.store,
                    res.item.id,
                    res.item.title,
                    res.suggested_tags,
                    policy_handling=policy.handling.value,
                    policy_preservation=policy.preservation.value,
                    policy_reason=policy.rationale,
                )
            text = f"{status}: {res.item.id} ({res.item.type.value})"
            if res.suggested_tags:
                text += f"\nSuggested tags: {', '.join(res.suggested_tags)} (pending review)"
            return {"content": [{"type": "text", "text": text}]}

        if name == "render_item":
            item = self._read_item(arguments["item_id"])
            policy = classify.suggest_policy(item.type, item.title, item.body)
            ctx = RenderContext(
                title=item.title,
                item=item,
                validation_errors=[],
                validation_warnings=[],
                suggested_tags=[],
                policy_handling=policy.handling.value,
                policy_preservation=policy.preservation.value,
                policy_reason=policy.rationale,
            )
            if arguments.get("format", "text") == "html":
                html = render_item_html(ctx, PAPER_SKIN)
                return {"content": [{"type": "text", "text": html}]}
            return {"content": [{"type": "text", "text": render_text(ctx)}]}

        if name == "get_similar":
            item_id = arguments["item_id"]
            threshold = float(arguments.get("threshold", 0.08))
            top_k = int(arguments.get("top_k", 8))
            items = self._all_items()
            if not any(it.id == item_id for it in items):
                return {"content": [{"type": "text", "text": f"item not found: {item_id}"}]}
            edges = similarity.build_similarity_edges(items, threshold=threshold, top_k=top_k)
            related = [e for e in edges if e.source == item_id]
            if not related:
                return {"content": [{"type": "text", "text": "no similar items found"}]}
            lines = [f"{'Target':30s} {'Score':>6s} {'Content':>7s} {'Tag':>7s}  Shared terms"]
            for e in sorted(related, key=lambda x: -x.score):
                shared = ", ".join(e.shared_terms[:3])
                lines.append(f"{e.target:30s} {e.score:.3f}  {e.content:.3f}  {e.tag:.3f}  {shared}")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        if name == "get_links":
            item_id = arguments["item_id"]
            items = self._all_items()
            if not any(it.id == item_id for it in items):
                return {"content": [{"type": "text", "text": f"item not found: {item_id}"}]}
            all_links = linking.build_links(items)
            outbound = linking.links_for(item_id, all_links)
            inbound = [ln for ln in all_links if ln.target == item_id]
            parts = [f"Outbound links ({len(outbound)}):"]
            for ln in outbound:
                parts.append(f"  -> {ln.target} ({ln.reason}, weight={ln.weight})")
            parts.append(f"\nInbound links ({len(inbound)}):")
            for ln in inbound:
                parts.append(f"  <- {ln.source} ({ln.reason}, weight={ln.weight})")
            text = "\n".join(parts) if outbound or inbound else "no links found"
            return {"content": [{"type": "text", "text": text}]}

        if name == "get_knowledge_scores":
            items = self._all_items()
            scores = self._all_scores(items)
            item_id = arguments.get("item_id")
            if item_id:
                ks = scores.get(item_id)
                if ks is None:
                    return {"content": [{"type": "text", "text": f"item not found: {item_id}"}]}
                text = (
                    f"Knowledge score for {ks.item_id}\n"
                    f"  Score:      {ks.score:.2f}/100\n"
                    f"  PageRank:   {ks.pagerank:.6f}\n"
                    f"  HITS auth:  {ks.authority:.6f}\n"
                    f"  HITS hub:   {ks.hub:.6f}\n"
                    f"  Degree:     {ks.degree}\n"
                    f"  Community:  {ks.community}\n"
                    f"  Neighbours: {len(ks.neighbours)}"
                )
                return {"content": [{"type": "text", "text": text}]}
            text = "\n".join(
                f"{ks.item_id:30s} {ks.score:>7.2f}  community={ks.community}"
                for ks in sorted(scores.values(), key=lambda x: -x.score)
            ) or "no scores"
            return {"content": [{"type": "text", "text": text}]}

        if name == "analyze_gaps":
            items = self._all_items()
            report = analyze_gaps(items, store=self.store)
            parts = [f"Gap Analysis — {report['summary']['total']} items"]
            parts.append(f"  Untagged:     {report['summary']['untagged']}")
            parts.append(f"  Unresolved:   {report['summary']['unresolved']}")
            parts.append(f"  Orphaned:     {report['summary']['orphaned']}")
            parts.append(f"  Stale (>6mo): {report['summary']['stale']}")
            parts.append("")
            if report.get("orphaned_items"):
                parts.append(f"Orphaned items: {', '.join(report['orphaned_items'][:10])}")
            if report.get("stale_items"):
                for s in report["stale_items"][:5]:
                    parts.append(f"  Stale: {s['id']} ({s['days_since_edit']}d)")
            text = "\n".join(parts)
            return {"content": [{"type": "text", "text": text}]}

        if name == "get_stats":
            items = self._all_items()
            stats = self._compute_stats(items)
            parts = [f"Knowledge Base Stats — {stats['total_items']} items"]
            parts.append("\nBy type:")
            for t, n in stats["by_type"].items():
                parts.append(f"  {t:25s} {n}")
            parts.append(f"\nTags: {stats['total_tags']} unique")
            parts.append(f"Cross-links: {stats['total_cross_links']}")
            parts.append("\nTop tags:")
            for tag, n in stats["top_tags"].items():
                parts.append(f"  {tag:20s} {n}")
            return {"content": [{"type": "text", "text": "\n".join(parts)}]}

        if name == "get_activity":
            limit = max(1, min(int(arguments.get("limit", 20)), 100))
            entries = activity.read(self.store.root, limit=limit, tail=True)
            if not entries:
                return {"content": [{"type": "text", "text": "no activity recorded"}]}
            lines = [f"{'Timestamp':30s} {'Action':20s} {'Item':25s} {'Outcome'}", "-" * 85]
            for e in entries:
                ts = e.get("timestamp", "?")[:19]
                action = e.get("action", "?")
                item_id = e.get("item_id", "") or "-"
                outcome = e.get("outcome", "?")
                lines.append(f"{ts:30s} {action:20s} {item_id:25s} {outcome}")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        if name == "get_settings":
            settings = operations.get_settings(self.store)
            parts = ["TextStrata Settings"]
            parts.append(f"  Skin:           {settings['presentation']['skin']}")
            parts.append(f"  Accent:         {settings['presentation']['accent']}")
            parts.append(f"  Density:        {settings['presentation']['density']}")
            parts.append(f"  Font scale:     {settings['presentation']['font_scale']}%")
            parts.append(f"  Content width:  {settings['presentation']['content_width']}")
            parts.append(f"  Revision limit: {settings['revision_limit']}")
            parts.append(f"  Root:           {settings['paths']['root']}")
            return {"content": [{"type": "text", "text": "\n".join(parts)}]}

        if name == "delete_item":
            item_id = arguments["item_id"]
            result = self.store.trash_item(item_id)
            self._invalidate_cache()
            text = f"trashed: {result['item_id']} -> {result['trash_name']}"
            return {"content": [{"type": "text", "text": text}]}

        if name == "research_query":
            query = arguments["query"]
            model = arguments.get("model")
            try:
                result = research.research(query, self.store, self.catalog, model=model)
            except RuntimeError as exc:
                return {"content": [{"type": "text", "text": f"Research error: {exc}"}]}
            source_lines = "\n".join(
                f"  [{i}] {s.title} (`{s.item_id}`)" for i, s in enumerate(result.sources, 1)
            )
            text = f"Research: {query}\n\n{result.answer}\n\nSources:\n{source_lines}" if result.sources else result.answer
            return {"content": [{"type": "text", "text": text}]}

        if name == "synthesize_topic":
            topic = arguments["topic"]
            model = arguments.get("model")
            try:
                result = research.synthesize(topic, self.store, self.catalog, model=model)
            except RuntimeError as exc:
                return {"content": [{"type": "text", "text": f"Synthesis error: {exc}"}]}
            source_lines = "\n".join(
                f"  [{i}] {s.title} (`{s.item_id}`)" for i, s in enumerate(result.sources, 1)
            )
            text = f"Synthesis: {topic}\n\n{result.answer}\n\nSources:\n{source_lines}" if result.sources else result.answer
            return {"content": [{"type": "text", "text": text}]}

        if name == "export_hugo":
            items = self._all_items()
            if arguments.get("item_id"):
                items = [it for it in items if it.id == arguments["item_id"]]
                if not items:
                    return {"content": [{"type": "text", "text": f"item not found: {arguments['item_id']}"}]}
            from .presentation import render_hugo_page
            pages = [render_hugo_page(it, site_section=arguments.get("site_section")) for it in items]
            parts = [f"Exported {len(pages)} Hugo page(s):", ""]
            for p in pages:
                parts.append(f"--- {p['path']} ---")
                parts.append(p["content"])
            return {"content": [{"type": "text", "text": "\n".join(parts)}]}

        if name == "get_dashboard":
            items = self._all_items()
            recent = sorted(items, key=lambda it: it.provenance.ingested_at or "", reverse=True)[:5]
            needs_tags = [it for it in items if not it.tags][:5]
            from collections import Counter
            tag_counts: Counter[str] = Counter()
            for it in items:
                tag_counts.update(t.lower() for t in it.tags)
            sections = [
                f"Dashboard — {len(items)} items",
                "",
                "Recent imports:",
            ]
            for it in recent:
                sections.append(f"  \u2022 {it.title} ({it.id}) [{it.type.value}]")
            sections.extend(["", "Needs curation (no tags):"])
            for it in needs_tags:
                sections.append(f"  \u2022 {it.title} ({it.id})")
            sections.extend(["", "Top tags:"])
            for tag, n in tag_counts.most_common(10):
                sections.append(f"  #{tag}: {n}")
            return {"content": [{"type": "text", "text": "\n".join(sections)}]}

        if name == "list_pending_reviews":
            pending = review.list_pending(self.store)
            if not pending:
                return {"content": [{"type": "text", "text": "no pending reviews"}]}
            lines = [f"{'Item ID':30s} {'Title':40s} {'Suggested Tags':30s} {'Policy':20s}", "-" * 120]
            for r in pending:
                tags = ", ".join(r.get("suggested_tags", []))
                policy = r.get("policy_handling", "") or ""
                lines.append(f"{r['item_id']:30s} {r.get('item_title', ''):40s} {tags:30s} {policy:20s}")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        if name == "confirm_review":
            result = review.confirm_tags(self.store, arguments["item_id"], arguments.get("tags"))
            if result is None:
                return {"content": [{"type": "text", "text": "review entry not found"}]}
            tags = ", ".join(result.get("confirmed_tags", []))
            return {"content": [{"type": "text", "text": f"confirmed tags for {arguments['item_id']}: {tags}"}]}

        if name == "reject_review":
            result = review.reject_suggestions(self.store, arguments["item_id"])
            if result is None:
                return {"content": [{"type": "text", "text": "review entry not found"}]}
            return {"content": [{"type": "text", "text": f"dismissed review for {arguments['item_id']}"}]}

        if name == "propose_note":
            title = str(arguments.get("title", "")).strip()
            body = str(arguments.get("body", "")).strip()
            if not title or not body:
                raise ValueError("title and body are required")
            entry = review.enqueue_agent_proposal(self.store, "note", {
                "title": title,
                "body": body,
                "tags": sorted({str(tag).strip().lower() for tag in arguments.get("tags", []) if str(tag).strip()}),
                "source_ids": sorted({str(item_id).strip() for item_id in arguments.get("source_ids", []) if str(item_id).strip()}),
            })
            return {"content": [{"type": "text", "text": f"queued note proposal {entry['proposal_id']}"}]}

        if name == "propose_tags":
            item_id = str(arguments.get("item_id", "")).strip()
            self._read_item(item_id)
            tags = sorted({str(tag).strip().lower() for tag in arguments.get("tags", []) if str(tag).strip()})
            if not tags:
                raise ValueError("at least one tag is required")
            entry = review.enqueue_agent_proposal(self.store, "tags", {
                "item_id": item_id,
                "tags": tags,
                "reason": str(arguments.get("reason", "")).strip(),
            })
            return {"content": [{"type": "text", "text": f"queued tag proposal {entry['proposal_id']}"}]}

        if name == "propose_synonym":
            variant = str(arguments.get("variant", "")).strip().casefold()
            canonical = str(arguments.get("canonical", "")).strip().casefold()
            if not variant or not canonical or variant == canonical:
                raise ValueError("variant and canonical are required and must differ")
            entry = review.enqueue_agent_proposal(self.store, "synonym", {
                "variant": variant,
                "canonical": canonical,
                "reason": str(arguments.get("reason", "")).strip(),
            })
            return {"content": [{"type": "text", "text": f"queued synonym proposal {entry['proposal_id']}"}]}

        if name == "list_agent_proposals":
            pending = review.list_pending_agent_proposals(self.store)
            text = json.dumps(pending, ensure_ascii=False, sort_keys=True, indent=2) if pending else "no pending agent proposals"
            return {"content": [{"type": "text", "text": text}]}

        raise ValueError(f"unknown tool: {name}")


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    stream = sys.stdin.buffer
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip(b"\r\n")
        if not line:
            break
        if b":" not in line:
            continue
        key, value = line.split(b":", 1)
        headers[key.decode("ascii", "ignore").lower()] = value.decode("ascii", "ignore").strip()
    length = int(headers.get("content-length", "0"))
    if not length:
        return None
    body = stream.read(length)
    return json.loads(body.decode("utf-8"))


def serve_stdio() -> None:
    workspace_root = resolve_workspace()
    apply_config_environment(load_cascading_config(workspace_root))
    server = TextStrataMCP(workspace_root)
    try:
        while True:
            msg = _read_message()
            if msg is None:
                break
            msg_id = msg.get("id")
            method = msg.get("method")

            if method == "initialize":
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "serverInfo": {"name": "textstrata", "version": __version__},
                            "capabilities": {
                                "tools": {"listChanged": False},
                                "resources": {},
                            },
                        },
                    }
                )
                continue

            if method == "notifications/initialized":
                continue

            if method == "tools/list":
                _write_message({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": server.tools()}})
                continue

            if method == "tools/call":
                try:
                    result = server.call(msg.get("params", {}).get("name"), msg.get("params", {}).get("arguments"))
                    _write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})
                except Exception as exc:
                    _write_message({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(exc)}})
                continue

            if method == "resources/list":
                try:
                    result = {"resources": server.list_resources()}
                    _write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})
                except Exception as exc:
                    _write_message({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(exc)}})
                continue

            if method == "resources/templates/list":
                try:
                    result = {"resourceTemplates": server.resource_templates()}
                    _write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})
                except Exception as exc:
                    _write_message({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(exc)}})
                continue

            if method == "resources/read":
                try:
                    params = msg.get("params", {})
                    result = server.read_resource(params.get("uri", ""))
                    _write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})
                except Exception as exc:
                    _write_message({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(exc)}})
                continue

            _write_message({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"unknown method: {method}"}})
    finally:
        server.close()


def main() -> None:
    serve_stdio()


if __name__ == "__main__":
    main()
