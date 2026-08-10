"""Research synthesis engine: RAG pipeline over the local knowledge base."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Any

from .catalog import Catalog
from .ingest import build_item
from .store import TextStrataStore


def _ollama_base() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


@dataclass
class Source:
    item_id: str
    title: str
    type: str
    tags: list[str]
    chunk: str
    score: float


@dataclass
class ResearchResult:
    answer: str
    sources: list[Source]
    model: str
    query: str


def _fts_safe(query: str) -> str:
    """Strip FTS5 special characters from a query string."""
    return re.sub(r'[^\w\s-]', ' ', query).strip()


def _chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph breaks."""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks = []
    paragraphs = re.split(r'\n\n+', text)
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append(current.strip())
            current = para
        else:
            if current:
                current += "\n\n" + para
            else:
                current = para
    if current.strip():
        chunks.append(current.strip())
    if len(chunks) == 0:
        return [text[:max_chars]]
    return chunks


def _call_ollama(
    model: str,
    prompt: str,
    system_prompt: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if system_prompt:
        payload["system"] = system_prompt
    req = urllib.request.Request(
        f"{_ollama_base()}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode())
    return result.get("response", "").strip()


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
    "also", "and", "because", "before", "between", "does", "it",
    "its", "into", "through", "during", "before", "after", "above",
    "below", "get", "got", "make", "made", "know", "like", "see",
    "come", "take", "want", "use", "tell", "ask", "work", "seem",
    "feel", "try", "leave", "call", "give", "find", "let", "keep",
    "put", "set", "new", "good", "first", "last", "long", "great",
    "little", "right", "old", "big", "high", "follow", "show",
    "need", "mean", "name", "help", "line", "turn", "cause", "much",
    "many", "well", "back", "even", "still", "way", "thing", "part",
    "place", "point", "case", "week", "company", "system", "group",
    "number", "world", "area", "hand", "room", "eye", "face", "side",
    "end", "head", "fact", "month", "side", "sort", "kind", "type",
    "does", "doesnt", "dont", "wont", "wont", "cant", "cannot",
    "wouldnt", "couldnt", "shouldnt", "mightnt", "neednt",
}


def _extract_keywords(query: str) -> str:
    """Extract meaningful keywords from a natural language query."""
    safe = _fts_safe(query)
    terms = [w for w in safe.lower().split() if w not in STOPWORDS and len(w) > 2]
    return " ".join(terms) if terms else safe


def _search_kb(
    query: str,
    catalog: Catalog,
    store: TextStrataStore,
    limit: int = 5,
    min_chars: int = 100,
) -> list[Source]:
    keywords = _extract_keywords(query)
    hits = catalog.search(keywords, limit=limit * 2) if keywords else []
    if not hits:
        hits = catalog.search(query, limit=limit * 2)
    sources: list[Source] = []
    for h in hits[:limit]:
        path = store.normalized_path_for_id(h.id)
        if not path:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            item, _, _ = build_item(text, fallback_id=h.id)
        except Exception:
            continue
        if not item.body or len(item.body.strip()) < min_chars:
            continue
        chunks = _chunk_text(item.body)
        query_terms = set(keywords.lower().split()) if keywords else set()
        best_chunk = chunks[0]
        best_score = 0.0
        for ch in chunks:
            body_lower = ch.lower()
            matches = sum(1 for t in query_terms if t in body_lower)
            score = matches / max(len(query_terms), 1)
            if score > best_score:
                best_score = score
                best_chunk = ch
        sources.append(Source(
            item_id=item.id,
            title=item.title,
            type=item.type.value,
            tags=list(item.tags),
            chunk=best_chunk[:1200],
            score=best_score,
        ))
    if not sources:
        return sources
    sources.sort(key=lambda s: -s.score)
    return sources[:limit]


def research(
    query: str,
    store: TextStrataStore,
    catalog: Catalog,
    model: str | None = None,
    depth: str = "balanced",
) -> ResearchResult:
    """Run the full RAG pipeline: search -> retrieve -> synthesize -> answer."""
    model = model or os.environ.get("TEXTSTRATA_LLM_MODEL") or os.environ.get("FABRIC_LLM_MODEL", "phi3:mini")
    sources = _search_kb(query, catalog, store, limit=7 if depth == "deep" else 5)
    if not sources:
        return ResearchResult(
            answer="No relevant content found in the knowledge base.",
            sources=[],
            model=model,
            query=query,
        )
    context_parts = []
    for i, src in enumerate(sources, 1):
        header = f"[SOURCE {i}] {src.title} ({src.item_id}) [{src.type}]"
        context_parts.append(f"{header}\n{src.chunk}")
    context = "\n\n".join(context_parts)
    system = (
        "You are a research assistant grounded in a personal knowledge base. "
        "Answer the question using ONLY the provided sources. "
        "Cite every factual claim by its source number like [1] or [2-3]. "
        "If the sources lack sufficient information, say so clearly. "
        "Be concise but thorough — 3-6 sentences typically."
    )
    prompt = (
        f"## Sources\n\n{context}\n\n"
        f"## Question\n\n{query}\n\n"
        f"## Instructions\n\n"
        f"Answer the question using only the sources above. "
        f"Cite sources with brackets like [1] or [2][3]. "
        f"If sources contradict each other, note it. "
        f"If there isn't enough info, say what's missing."
    )
    try:
        answer = _call_ollama(model, prompt, system_prompt=system, temperature=0.2)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                f"Model '{model}' not found in Ollama. "
                f"Run: ollama pull {model}"
            ) from exc
        raise RuntimeError(f"Ollama error (HTTP {exc.code}): {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"Could not reach Ollama at {_ollama_base()}: {exc}") from exc
    return ResearchResult(
        answer=answer,
        sources=sources,
        model=model,
        query=query,
    )


def synthesize(
    topic: str,
    store: TextStrataStore,
    catalog: Catalog,
    model: str | None = None,
) -> ResearchResult:
    """Synthesize a briefing/topic overview from all relevant items."""
    return research(
        f"Provide a comprehensive overview of {topic}. Synthesize information from all relevant sources into a coherent briefing.",
        store, catalog, model=model, depth="deep",
    )


def daily_briefing(
    store: TextStrataStore,
    catalog: Catalog,
    model: str | None = None,
    days: int = 1,
) -> ResearchResult:
    """Generate a daily briefing from recent activity."""
    from .activity import read
    model = model or os.environ.get("TEXTSTRATA_LLM_MODEL") or os.environ.get("FABRIC_LLM_MODEL", "phi3:mini")
    activity = read(store.root, limit=50)
    recent = [a for a in activity if a.get("outcome") == "published"][:10]
    if not recent:
        return ResearchResult(
            answer="No recent activity to summarize.",
            sources=[], model=model, query="daily briefing",
        )
    items_text = ""
    for entry in recent:
        item_id = entry.get("item_id", "")
        path = store.normalized_path_for_id(item_id)
        if not path:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            item, _, _ = build_item(text, fallback_id=item_id)
            ts = entry.get("timestamp", "?")[:10]
            items_text += f"- [{ts}] `{item.id}` ({item.type.value}): {item.title}\n"
            if item.body:
                items_text += f"  {item.body[:300].strip()}\n\n"
        except Exception:
            continue
    if not items_text:
        return ResearchResult(
            answer="No recent activity to summarize.",
            sources=[], model=model, query="daily briefing",
        )
    system = (
        "You are a briefing assistant. Summarize today's additions to a personal knowledge base. "
        "Group related items, highlight key themes, and suggest what to explore next."
    )
    prompt = (
        f"## Today's Knowledge Base Activity\n\n{items_text}\n\n"
        f"## Instructions\n\n"
        f"Write a concise daily briefing covering: "
        f"1) What was added, 2) Key themes/connections, "
        f"3) One thing worth exploring further. "
        f"Be specific — reference item titles and types."
    )
    try:
        answer = _call_ollama(model, prompt, system_prompt=system, temperature=0.4)
    except Exception as exc:
        answer = f"(Ollama unavailable: {exc})\n\nRaw activity:\n{items_text}"
    return ResearchResult(
        answer=answer,
        sources=[],
        model=model,
        query="daily briefing",
    )


def relate(
    item_ids: list[str],
    store: TextStrataStore,
    catalog: Catalog,
    model: str | None = None,
) -> ResearchResult:
    """Find and explain hidden connections between items."""
    model = model or os.environ.get("TEXTSTRATA_LLM_MODEL") or os.environ.get("FABRIC_LLM_MODEL", "phi3:mini")
    items_info = []
    for iid in item_ids:
        path = store.normalized_path_for_id(iid)
        if not path:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            item, _, _ = build_item(text, fallback_id=iid)
            items_info.append(item)
        except Exception:
            continue
    if len(items_info) < 2:
        return ResearchResult(
            answer="Need at least 2 valid items to relate.",
            sources=[], model=model, query=f"relate: {item_ids}",
        )
    item_blocks = []
    for it in items_info:
        item_blocks.append(
            f"--- {it.id} ---\n"
            f"Title: {it.title}\n"
            f"Type: {it.type.value}\n"
            f"Tags: {', '.join(it.tags)}\n"
            f"Body:\n{(it.body or '')[:1000]}"
        )
    context = "\n\n".join(item_blocks)
    system = (
        "You are a knowledge connection analyst. Given several knowledge base items, "
        "find surprising or non-obvious connections between them. "
        "Look for shared concepts, complementary ideas, or conflicting viewpoints."
    )
    prompt = (
        f"## Items\n\n{context}\n\n"
        f"## Task\n\n"
        f"Analyze these items and identify:\n"
        f"1) Common threads or themes\n"
        f"2) How they complement or contradict each other\n"
        f"3) A novel insight that emerges from combining them\n\n"
        f"Be specific and cite item IDs."
    )
    try:
        answer = _call_ollama(model, prompt, system_prompt=system, temperature=0.5)
    except Exception as exc:
        answer = f"(Ollama unavailable: {exc})"
    return ResearchResult(
        answer=answer,
        sources=[Source(it.id, it.title, it.type.value, list(it.tags), "", 0) for it in items_info],
        model=model,
        query=f"relate: {', '.join(item_ids)}",
    )
