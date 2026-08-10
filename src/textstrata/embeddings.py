"""Semantic search via sentence-transformers embeddings.

Optional dependency: pip install sentence-transformers
Set TEXTSTRATA_EMBEDDING_MODEL to choose the model (default: all-MiniLM-L6-v2).
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import TextStrataItem

EMBEDDING_MODEL = os.environ.get("TEXTSTRATA_EMBEDDING_MODEL") or os.environ.get("FABRIC_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDINGS_FILE = "embeddings.json"


def _build_item_text(item: TextStrataItem) -> str:
    parts = []
    if item.title:
        parts.append(f"title: {item.title}")
    if item.tags:
        parts.append(f"tags: {', '.join(item.tags)}")
    if item.body:
        parts.append(item.body)
    return "\n".join(parts)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = norm_a = norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    norm_a = math.sqrt(norm_a)
    norm_b = math.sqrt(norm_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


_model_cache: dict[str, object] = {}


def _get_model(model_name: str):
    if model_name not in _model_cache:
        from sentence_transformers import SentenceTransformer
        _model_cache[model_name] = SentenceTransformer(model_name)
    return _model_cache[model_name]


def compute_embeddings(
    items: list[TextStrataItem],
    model_name: str = EMBEDDING_MODEL,
) -> dict[str, list[float]]:
    model = _get_model(model_name)
    texts = [_build_item_text(it) for it in items]
    vectors = model.encode(texts, show_progress_bar=False)
    return {it.id: vectors[i].tolist() for i, it in enumerate(items)}


def embeddings_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / ".fabric" / EMBEDDINGS_FILE


def save_embeddings(
    root: Path,
    embeddings: dict[str, list[float]],
    model_name: str = EMBEDDING_MODEL,
) -> None:
    data = {"version": 1, "model": model_name, "items": embeddings}
    path = embeddings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def load_embeddings(root: Path) -> dict[str, list[float]] | None:
    path = embeddings_path(root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("items")
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def ensure_embeddings(
    items: list[TextStrataItem],
    root: Path,
    model_name: str = EMBEDDING_MODEL,
) -> dict[str, list[float]]:
    cached = load_embeddings(root)
    if cached is not None and set(cached.keys()) == {it.id for it in items}:
        return cached
    embeddings = compute_embeddings(items, model_name)
    save_embeddings(root, embeddings, model_name)
    return embeddings


def search_semantic(
    query: str,
    embeddings: dict[str, list[float]],
    model_name: str = EMBEDDING_MODEL,
    top_k: int = 10,
) -> list[tuple[str, float]]:
    model = _get_model(model_name)
    query_vec = model.encode([query], show_progress_bar=False)[0].tolist()
    scored = [
        (item_id, _cosine_similarity(query_vec, vec))
        for item_id, vec in embeddings.items()
    ]
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]
