"""Rebuildable retrieval catalog backed by SQLite FTS5.

The catalog is a derived index, never a source of truth: it can be dropped and
fully rebuilt from ``normalized/`` at any time via :meth:`Catalog.rescan`. That
keeps the filesystem authoritative and the index disposable.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .ingest import build_item
from .models import TextStrataItem
from .similarity import KnowledgeScore
from .store import TextStrataStore


@dataclass
class SearchHit:
    id: str
    title: str
    type: str
    tags: str
    snippet: str
    knowledge_score: float = 0.0
    ingested_at: str = ""
    contributor_chain: str = ""


class Catalog:
    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.metadata_dir = self.workspace_root / ".fabric"
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            legacy = self.workspace_root / f"catalog.db{suffix}"
            target = self.metadata_dir / f"catalog.db{suffix}"
            if legacy.exists() and not target.exists():
                legacy.replace(target)
        self.db_path = str(self.metadata_dir / "catalog.db")
        self.conn = sqlite3.connect(self.db_path, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS items USING fts5(
                id UNINDEXED,
                title,
                tags,
                type UNINDEXED,
                body,
                priority UNINDEXED,
                tokenize = 'porter unicode61'
            );
            CREATE TABLE IF NOT EXISTS item_meta (
                id TEXT PRIMARY KEY,
                knowledge_score REAL DEFAULT 0.0,
                ingested_at TEXT DEFAULT '',
                community TEXT DEFAULT '',
                relative_path TEXT DEFAULT ''
            );
            """
        )
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(item_meta)")}
        if "community" not in columns:
            self.conn.execute("ALTER TABLE item_meta ADD COLUMN community TEXT DEFAULT ''")
        if "relative_path" not in columns:
            self.conn.execute("ALTER TABLE item_meta ADD COLUMN relative_path TEXT DEFAULT ''")
        if "contributor_chain" not in columns:
            self.conn.execute("ALTER TABLE item_meta ADD COLUMN contributor_chain TEXT DEFAULT ''")
        self.conn.commit()

    def _validated_relative_path(self, path: Path) -> str:
        if path.is_absolute():
            try:
                path = path.resolve().relative_to(self.workspace_root)
            except ValueError as exc:
                raise ValueError("catalog path must be inside workspace_root") from exc
        if not path.parts or ".." in path.parts:
            raise ValueError("catalog path must be a workspace-relative path")
        return path.as_posix()

    def _upsert(self, item: TextStrataItem, relative_path: Path | None = None) -> None:
        stored_path = self._validated_relative_path(
            relative_path or Path("normalized") / f"{item.id}.md"
        )
        self.conn.execute("DELETE FROM items WHERE id = ?", (item.id,))
        self.conn.execute(
            "INSERT INTO items (id, title, tags, type, body, priority) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                item.id,
                item.title + (" " + " ".join(item.aliases) if item.aliases else ""),
                " ".join(item.tags),
                item.type.value,
                item.body,
                item.retrieval_priority,
            ),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO item_meta (id, knowledge_score, ingested_at, community, relative_path, contributor_chain) "
            "VALUES (?, COALESCE((SELECT knowledge_score FROM item_meta WHERE id = ?), 0.0), ?, "
            "COALESCE((SELECT community FROM item_meta WHERE id = ?), ''), ?, "
            "COALESCE((SELECT contributor_chain FROM item_meta WHERE id = ?), ?))",
            (item.id, item.id, item.provenance.ingested_at, item.id, stored_path, item.id, item.provenance.contributor_chain),
        )

    def index_item(self, item: TextStrataItem) -> None:
        self._upsert(item)
        self.conn.commit()

    def remove_item(self, item_id: str) -> None:
        """Remove one item from the FTS index and its meta row."""
        self.conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        self.conn.execute("DELETE FROM item_meta WHERE id = ?", (item_id,))
        self.conn.commit()

    def list_items(self) -> list[SearchHit]:
        """Lightweight catalog-backed listing (no markdown re-parse).

        Returns id/title/type/tags plus knowledge score and ingest date for
        every indexed item, newest first. Body/snippet is left empty.
        """
        rows = self.conn.execute(
            "SELECT items.id, items.title, items.type, items.tags, "
            "COALESCE(m.knowledge_score, 0.0) AS knowledge_score, "
            "COALESCE(m.ingested_at, '') AS ingested_at, "
            "COALESCE(m.contributor_chain, '') AS contributor_chain "
            "FROM items LEFT JOIN item_meta m ON items.id = m.id "
            "ORDER BY m.ingested_at DESC"
        ).fetchall()
        return [
            SearchHit(
                id=r["id"], title=r["title"], type=r["type"],
                tags=r["tags"], snippet="",
                knowledge_score=r["knowledge_score"],
                ingested_at=r["ingested_at"],
                contributor_chain=r["contributor_chain"],
            )
            for r in rows
        ]

    def update_scores(self, scores: dict[str, KnowledgeScore]) -> None:
        """Bulk-update knowledge scores from score_corpus() output.

        Accepts the dict of item_id -> KnowledgeScore (or any object with
        .score and .pagerank attributes).
        """
        for item_id, ks in scores.items():
            self.conn.execute(
                "INSERT OR REPLACE INTO item_meta (id, knowledge_score, ingested_at, community, relative_path, contributor_chain) "
                "VALUES (?, ?, COALESCE((SELECT ingested_at FROM item_meta WHERE id = ?), ''), ?, "
                "COALESCE((SELECT relative_path FROM item_meta WHERE id = ?), ''), "
                "COALESCE((SELECT contributor_chain FROM item_meta WHERE id = ?), ''))",
                (item_id, ks.score, item_id, getattr(ks, "community", ""), item_id, item_id),
            )
        self.conn.commit()

    def list_communities(self) -> list[dict[str, object]]:
        """Return persisted communities, largest first, from the derived index."""
        rows = self.conn.execute(
            "SELECT community, COUNT(*) AS item_count, MAX(knowledge_score) AS top_score "
            "FROM item_meta WHERE community != '' GROUP BY community "
            "ORDER BY item_count DESC, community ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def community_item_ids(self, community: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM item_meta WHERE community = ? "
            "ORDER BY knowledge_score DESC, id ASC",
            (community,),
        ).fetchall()
        return [row["id"] for row in rows]

    def resolve_item_path(self, item_id: str) -> Path | None:
        row = self.conn.execute(
            "SELECT relative_path FROM item_meta WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None or not row["relative_path"]:
            return None
        relative = Path(row["relative_path"])
        self._validated_relative_path(relative)
        resolved = (self.workspace_root / relative).resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("catalog path escapes workspace_root") from exc
        return resolved

    def rescan(self, store: TextStrataStore) -> int:
        """Drop and rebuild the FTS index from the normalized store.

        Preserves existing ``item_meta`` rows (knowledge scores, ingested_at)
        so that sort-by-score and sort-by-date survive rescans.
        Returns the number of items indexed.
        """
        self.conn.execute("DELETE FROM items")
        count = 0
        for path in store.normalized_paths():
            item, _suggested, _fm = build_item(
                path.read_text(encoding="utf-8"), fallback_id=path.stem
            )
            self._upsert(item, path.relative_to(self.workspace_root))
            count += 1
        # Prune meta rows for items no longer in the normalized store.
        known = self.conn.execute("SELECT id FROM items").fetchall()
        if known:
            ids = tuple(r["id"] for r in known)
            self.conn.execute(
                f"DELETE FROM item_meta WHERE id NOT IN ({','.join('?' * len(ids))})",
                ids,
            )
        self.conn.commit()
        return count

    def search(self, query: str, limit: int = 10, sort: str = "relevance",
               contributor_filter: list[str] | None = None) -> list[SearchHit]:
        sort = sort or "relevance"
        contributor_filter = contributor_filter or []
        has_filter = bool(contributor_filter)
        use_sort = sort != "relevance" or has_filter
        order_clause = {
            "relevance": "ORDER BY items.priority DESC, rank",
            "score": "ORDER BY m.knowledge_score DESC, rank",
            "newest": "ORDER BY m.ingested_at DESC, rank",
            "oldest": "ORDER BY m.ingested_at ASC, rank",
        }
        order_sql = order_clause.get(sort, order_clause["relevance"])
        contributor_clause = ""
        contributor_params: list[str] = []
        if has_filter:
            contributor_clause = " AND " + " AND ".join(
                "m.contributor_chain LIKE ?" for _ in contributor_filter
            )
            contributor_params = [f"%{c}%" for c in contributor_filter]
        if use_sort:
            sql = (
                "SELECT items.id, items.title, items.type, items.tags, "
                "COALESCE(m.knowledge_score, 0.0) AS knowledge_score, "
                "COALESCE(m.ingested_at, '') AS ingested_at, "
                "COALESCE(m.contributor_chain, '') AS contributor_chain, "
                "snippet(items, 4, '[', ']', ' … ', 12) AS snip "
                "FROM items LEFT JOIN item_meta m ON items.id = m.id "
                "WHERE items MATCH ?" + contributor_clause + " " + order_sql + " LIMIT ?"
            )
        else:
            sql = (
                "SELECT id, title, type, tags, 0.0 AS knowledge_score, "
                "'' AS ingested_at, '' AS contributor_chain, "
                "snippet(items, 4, '[', ']', ' … ', 12) AS snip "
                "FROM items WHERE items MATCH ?" + contributor_clause + " " + order_sql + " LIMIT ?"
            )
        try:
            rows = self.conn.execute(sql, (query, *contributor_params, limit)).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError(f"invalid FTS query {query!r}: {exc}") from exc
        return [
            SearchHit(
                id=r["id"], title=r["title"], type=r["type"],
                tags=r["tags"], snippet=r["snip"],
                knowledge_score=r["knowledge_score"],
                ingested_at=r["ingested_at"],
                contributor_chain=r["contributor_chain"],
            )
            for r in rows
        ]

    def count(self) -> int:
        return self.conn.execute("SELECT count(*) AS c FROM items").fetchone()["c"]

    def close(self) -> None:
        self.conn.close()
