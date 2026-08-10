"""Item detail page use case."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .. import classify
from ..linking import build_links, links_for
from ..render_context import RenderContext
from ..similarity import build_similarity_edges, score_corpus
from ..validate import validate


def build_item_render_context(
    item: Any,
    corpus: Sequence[Any],
    *,
    synonyms: Mapping[str, str] | None = None,
    raw_markdown: str | None = None,
) -> RenderContext:
    policy = classify.suggest_policy(item.type, item.title, item.body)
    result = validate(item)
    all_links = build_links(list(corpus))
    explicit = [(link.source, link.target, float(link.weight)) for link in all_links]
    scores = score_corpus(list(corpus), explicit, synonyms=dict(synonyms or {}))
    similarity_edges = build_similarity_edges(list(corpus), synonyms=dict(synonyms or {}))
    known_ids = {other.id for other in corpus}
    titles = {other.id: other.title for other in corpus}
    aliases = {
        alias: other.id
        for other in corpus
        for alias in getattr(other, "aliases", ())
        if alias.strip()
    }
    this_score = scores.get(item.id)
    neighbour_ids = list(this_score.neighbours) if this_score else []
    incoming_links = [link for link in all_links if link.target == item.id]
    why_related: dict[str, str] = {}
    for link in all_links:
        if link.source == item.id:
            why_related.setdefault(link.target, link.reason.replace("_", " "))
        if link.target == item.id:
            why_related.setdefault(link.source, link.reason.replace("_", " "))
    for edge in similarity_edges:
        if edge.source != item.id:
            continue
        if edge.shared_terms:
            why_related.setdefault(edge.target, "shared terms: " + ", ".join(edge.shared_terms[:3]))
        else:
            why_related.setdefault(edge.target, "similar content")

    return RenderContext(
        title=item.title,
        item=item,
        validation_errors=result.errors,
        validation_warnings=result.warnings,
        suggested_tags=[],
        policy_handling=policy.handling.value,
        policy_preservation=policy.preservation.value,
        policy_reason=policy.rationale,
        outgoing_links=links_for(item.id, all_links),
        known_ids=known_ids,
        similar_ids=neighbour_ids,
        knowledge_score=this_score.score if this_score else None,
        community=this_score.community if this_score else None,
        raw_markdown=raw_markdown,
        incoming_links=incoming_links,
        why_related=why_related,
        item_titles=titles,
        item_aliases=aliases,
    )
