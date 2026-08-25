"""Shared, explainable retrieval for TextStrata.

The catalog remains a rebuildable index.  This module owns the query
normalization, candidate loading, chunk selection, and evidence gate used by
CLI, MCP, and answer-generation paths.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .catalog import Catalog
from .ingest import build_item
from .store import TextStrataStore


STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "what", "which", "who", "whom", "this", "that", "these",
    "those", "of", "in", "on", "at", "to", "for", "by", "with", "from",
    "up", "down", "out", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "not", "only", "own", "same", "so", "than",
    "too", "very", "just", "about", "above", "across", "after",
    "also", "and", "because", "before", "between", "below", "get", "got",
    "make", "made", "know", "like", "see", "come", "take", "want", "use",
    "tell", "ask", "work", "seem", "feel", "try", "leave", "call", "give",
    "find", "let", "keep", "put", "set", "new", "good", "first", "last",
    "long", "great", "little", "right", "old", "big", "high", "follow",
    "show", "mean", "name", "help", "line", "turn", "cause", "much", "many",
    "well", "back", "even", "still", "way", "thing", "part", "place", "point",
    "case", "week", "company", "system", "group", "number", "world", "area",
    "hand", "room", "eye", "face", "side", "end", "head", "fact", "month",
    "sort", "kind", "type", "does", "doesnt", "dont", "wont", "cant", "cannot",
    "wouldnt", "couldnt", "shouldnt", "mightnt", "neednt",
}


@dataclass(frozen=True)
class RetrievalCandidate:
    item_id: str
    title: str
    type: str
    tags: tuple[str, ...]
    chunk: str
    matched_terms: tuple[str, ...]
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    strategy: str
    query_terms: tuple[str, ...]
    candidates: tuple[RetrievalCandidate, ...]
    evidence_score: float
    sufficient_evidence: bool
    reason: str

    @property
    def sources(self) -> tuple[RetrievalCandidate, ...]:
        return self.candidates

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "strategy": self.strategy,
            "query_terms": list(self.query_terms),
            "candidates": [asdict(candidate) | {
                "tags": list(candidate.tags),
                "matched_terms": list(candidate.matched_terms),
            } for candidate in self.candidates],
            "evidence_score": round(self.evidence_score, 4),
            "sufficient_evidence": self.sufficient_evidence,
            "reason": self.reason,
        }


def _fts_safe(query: str) -> str:
    """Strip FTS5 special characters from a query string."""
    return re.sub(r"[^\w\s-]", " ", query).strip()


def extract_keywords(query: str) -> tuple[str, ...]:
    """Extract stable, meaningful terms from a natural-language query."""
    safe = _fts_safe(query)
    return tuple(word for word in safe.lower().split() if word not in STOPWORDS and len(word) > 2)


def chunk_text(text: str, max_chars: int = 800) -> list[str]:
    """Split text into paragraph-aware chunks for prompt context."""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\n+", text):
        if len(current) + len(paragraph) + 2 > max_chars and current:
            chunks.append(current.strip())
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text[:max_chars]]


def _candidate_score(chunk: str, terms: tuple[str, ...]) -> tuple[float, tuple[str, ...]]:
    body = chunk.lower()
    matched = tuple(term for term in terms if term in body)
    return len(matched) / max(len(terms), 1), matched


def retrieve(
    query: str,
    catalog: Catalog,
    store: TextStrataStore,
    *,
    limit: int = 5,
    strategy: str = "keyword",
    min_body_chars: int = 1,
    min_evidence_score: float = 0.2,
) -> RetrievalResult:
    """Retrieve explainable source chunks using the current catalog path.

    ``strategy`` is recorded for diagnostics.  The ``keyword`` strategy is
    the default and preserves the existing FTS5 behavior.  Semantic search is
    intentionally not mixed into this first trust milestone.
    """
    if strategy != "keyword":
        raise ValueError(f"unsupported retrieval strategy: {strategy}")

    terms = extract_keywords(query)
    if not terms:
        return RetrievalResult(query, strategy, terms, (), 0.0, False, "no usable query terms")
    search_query = " ".join(terms) or _fts_safe(query)

    hits = catalog.search(search_query, limit=max(limit * 2, limit))
    if not hits and search_query != query:
        hits = catalog.search(query, limit=max(limit * 2, limit))

    candidates: list[RetrievalCandidate] = []
    for hit in hits:
        path = store.normalized_path_for_id(hit.id)
        if not path:
            continue
        try:
            item, _, _ = build_item(path.read_text(encoding="utf-8"), fallback_id=hit.id)
        except (OSError, UnicodeError, ValueError):
            continue
        if not item.body or len(item.body.strip()) < min_body_chars:
            continue
        chunks = chunk_text(item.body)
        scored = [(_candidate_score(chunk, terms), chunk) for chunk in chunks]
        (score, matched), chunk = max(scored, key=lambda value: (value[0][0], value[1]))
        candidates.append(RetrievalCandidate(
            item_id=item.id,
            title=item.title,
            type=item.type.value,
            tags=tuple(item.tags),
            chunk=chunk[:1200],
            matched_terms=matched,
            score=score,
        ))

    candidates.sort(key=lambda candidate: (-candidate.score, candidate.item_id))
    selected = tuple(candidates[:limit])
    evidence_score = selected[0].score if selected else 0.0
    if not terms:
        reason = "no usable query terms"
    elif not selected:
        reason = "no body-bearing candidates"
    elif evidence_score < min_evidence_score:
        reason = f"top candidate coverage {evidence_score:.2f} is below {min_evidence_score:.2f}"
    else:
        reason = "sufficient keyword evidence"
    return RetrievalResult(
        query=query,
        strategy=strategy,
        query_terms=terms,
        candidates=selected,
        evidence_score=evidence_score,
        sufficient_evidence=bool(selected) and bool(terms) and evidence_score >= min_evidence_score,
        reason=reason,
    )
