"""Deterministic knowledge graph payload assembly."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def build_graph_payload(
    items: Sequence[Any],
    links: Iterable[Any],
    similarity_edges: Iterable[Any],
    scores: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the additive graph API contract used by the interactive page."""
    links = list(links)
    similarity_edges = list(similarity_edges)
    in_degree: Counter[str] = Counter()
    out_degree: Counter[str] = Counter()
    similarity_degree: Counter[str] = Counter()

    for link in links:
        out_degree[link.source] += 1
        in_degree[link.target] += 1
    for edge in similarity_edges:
        similarity_degree[edge.source] += 1

    def total_degree(item_id: str) -> int:
        return in_degree[item_id] + out_degree[item_id] + similarity_degree[item_id]

    nodes = []
    for item in items:
        score = scores.get(item.id)
        nodes.append(
            {
                "id": item.id,
                "title": item.title,
                "type": item.type.value,
                "score": round(score.score, 1) if score else 0,
                "community": score.community if score else None,
                "neighbours": score.neighbours if score else [],
                "tags": item.tags,
                "pagerank": score.pagerank if score else 0.0,
                "hub": score.hub if score else 0.0,
                "authority": score.authority if score else 0.0,
                "in": in_degree[item.id],
                "out": out_degree[item.id],
                "degree": total_degree(item.id),
                "orphan": total_degree(item.id) == 0,
                "ingested": str(getattr(item.provenance, "ingested_at", "") or "")[:10],
            }
        )

    community_members: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if node["community"]:
            community_members.setdefault(node["community"], []).append(node)

    node_communities = {node["id"]: node["community"] for node in nodes}
    external_links: Counter[str] = Counter()
    for link in links:
        source = node_communities.get(link.source)
        target = node_communities.get(link.target)
        if source and target and source != target:
            external_links[source] += 1

    titles = {node["id"]: node["title"] for node in nodes}
    communities = [
        {
            "label": label,
            "anchor_title": titles.get(label, label),
            "size": len(group),
            "top": [
                member["id"]
                for member in sorted(group, key=lambda member: (-member["score"], member["id"]))[:5]
            ],
            "external_links": external_links[label],
        }
        for label, group in sorted(
            community_members.items(), key=lambda pair: (-len(pair[1]), pair[0])
        )
    ]

    strong_degree: Counter[str] = Counter()
    for link in links:
        if link.weight >= 2:
            strong_degree[link.source] += 1
            strong_degree[link.target] += 1
    for edge in similarity_edges:
        strong_degree[edge.source] += 1

    orphans = sorted(node["id"] for node in nodes if node["orphan"])
    scores_by_id = {node["id"]: node["score"] for node in nodes}
    weak = sorted(
        (
            node["id"]
            for node in nodes
            if not node["orphan"] and strong_degree[node["id"]] <= 1
        ),
        key=lambda item_id: (scores_by_id[item_id], item_id),
    )

    return {
        "nodes": nodes,
        "links": [
            {
                "source": link.source,
                "target": link.target,
                "reason": link.reason,
                "weight": link.weight,
            }
            for link in links
        ],
        "similarity": [
            {
                "source": edge.source,
                "target": edge.target,
                "score": round(edge.score, 3),
                "content": round(edge.content, 3),
                "tag": round(edge.tag, 3),
                "shared": list(edge.shared_terms),
            }
            for edge in similarity_edges
        ],
        "communities": communities,
        "attention": {"orphans": orphans, "weak": weak},
    }
