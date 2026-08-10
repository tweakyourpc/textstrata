"""Deterministic cross-linking.

Links come from cheap, reproducible signals only — no graph database, no
embedding model. Every edge names the signal that produced it and carries a
small integer weight, so the mesh is explainable and stable across runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from .models import TextStrataItem

# Relative strength of each signal. Explicit author references outrank
# inferred ones.
@dataclass(frozen=True)
class LinkPolicy:
    """Deterministic limits for inferred links.

    Explicit links are never capped. Weak inferred signals use a bounded
    neighborhood so a popular tag or content type cannot create a Cartesian
    product of edges.
    """

    weights: dict[str, int] = field(default_factory=lambda: {
        "dependency": 5,
        "wikilink": 5,
        "reference": 4,
        "shared_tag": 2,
        "same_type": 1,
    })
    max_inferred_neighbors: int = 12


DEFAULT_LINK_POLICY = LinkPolicy()
_WIKILINK_RE = re.compile(r"\[\[(?P<target>[^\]|#]+)(?:#[^\]|]+)?(?:\|(?P<label>[^\]]+))?\]\]")


@dataclass(frozen=True)
class Link:
    source: str
    target: str
    reason: str
    weight: int


def _add(edges: dict, source: str, target: str, reason: str, policy: LinkPolicy) -> None:
    if source == target:
        return
    key = (source, target)
    weight = policy.weights[reason]
    existing = edges.get(key)
    if existing is None or weight > existing[1]:
        edges[key] = (reason, weight)
    # keep the single strongest reason per ordered pair


def parse_wikilinks(body: str) -> list[tuple[str, str | None]]:
    """Return wiki-link targets and optional display labels in source order."""
    return [(match.group("target").strip(), (match.group("label") or "").strip() or None) for match in _WIKILINK_RE.finditer(body)]


def _link_targets(items: list[TextStrataItem]) -> dict[str, tuple[str, ...]]:
    targets: dict[str, set[str]] = {}
    for item in items:
        for value in (item.id, item.title, *getattr(item, "aliases", ())):
            key = value.strip().casefold()
            if key:
                targets.setdefault(key, set()).add(item.id)
    return {key: tuple(sorted(ids)) for key, ids in targets.items()}


def link_collisions(items: list[TextStrataItem]) -> dict[str, tuple[str, ...]]:
    """Return ambiguous title/alias targets without choosing a winner."""
    return {
        key: ids
        for key, ids in _link_targets(items).items()
        if len(ids) > 1
    }


def _bounded_group_pairs(ids: list[str], max_neighbors: int) -> list[tuple[str, str]]:
    """Produce deterministic, bounded pairs for a tag/type group."""
    ordered = sorted(set(ids))
    if max_neighbors <= 0 or len(ordered) < 2:
        return []
    pairs: list[tuple[str, str]] = []
    for index, source in enumerate(ordered):
        start = max(0, index - max_neighbors // 2)
        stop = min(len(ordered), start + max_neighbors + 1)
        for target in ordered[start:stop]:
            if source < target:
                pairs.append((source, target))
    return pairs


def build_links(
    items: list[TextStrataItem],
    *,
    policy: LinkPolicy = DEFAULT_LINK_POLICY,
    warnings: list[str] | None = None,
) -> list[Link]:
    by_id = {it.id: it for it in items}
    targets = _link_targets(items)
    edges: dict[tuple[str, str], tuple[str, int]] = {}

    # Explicit author-declared edges.
    for it in items:
        for dep in it.dependencies:
            if dep in by_id:
                _add(edges, it.id, dep, "dependency", policy)
        for ref in it.related:
            if ref in by_id:
                _add(edges, it.id, ref, "reference", policy)
        for target_text, _label in parse_wikilinks(it.body):
            target_ids = targets.get(target_text.casefold(), ())
            if len(target_ids) == 1 and target_ids[0] in by_id:
                _add(edges, it.id, target_ids[0], "wikilink", policy)
            elif len(target_ids) > 1 and warnings is not None:
                warnings.append(
                    f"ambiguous wikilink {target_text!r} in {it.id}: "
                    f"matches {', '.join(target_ids)}"
                )

    # Inferred, symmetric edges from shared tags.
    tag_index: dict[str, list[str]] = {}
    for it in items:
        for tag in {t.lower() for t in it.tags}:
            tag_index.setdefault(tag, []).append(it.id)
    for ids in tag_index.values():
        for a, b in _bounded_group_pairs(ids, policy.max_inferred_neighbors):
            _add(edges, a, b, "shared_tag", policy)
            _add(edges, b, a, "shared_tag", policy)

    # Weak edges from shared content type.
    type_index: dict[str, list[str]] = {}
    for it in items:
        type_index.setdefault(it.type.value, []).append(it.id)
    for ids in type_index.values():
        for a, b in _bounded_group_pairs(ids, policy.max_inferred_neighbors):
            _add(edges, a, b, "same_type", policy)
            _add(edges, b, a, "same_type", policy)

    # Apply the cap across all inferred signals per source. Explicit author
    # links remain uncapped and always win when they share a pair with an
    # inferred edge.
    selected: list[tuple[tuple[str, str], tuple[str, int]]] = []
    by_source: dict[str, list[tuple[tuple[str, str], tuple[str, int]]]] = {}
    for key, value in edges.items():
        by_source.setdefault(key[0], []).append((key, value))
    explicit_reasons = {"dependency", "reference", "wikilink"}
    for source, candidates in by_source.items():
        explicit = [entry for entry in candidates if entry[1][0] in explicit_reasons]
        inferred = sorted(
            (entry for entry in candidates if entry[1][0] not in explicit_reasons),
            key=lambda entry: (-entry[1][1], entry[0][1]),
        )[: policy.max_inferred_neighbors]
        selected.extend(explicit + inferred)

    links = [
        Link(source=s, target=t, reason=reason, weight=weight)
        for (s, t), (reason, weight) in selected
    ]
    links.sort(key=lambda link: (-link.weight, link.source, link.target))
    return links


def links_for(item_id: str, links: list[Link]) -> list[Link]:
    return [link for link in links if link.source == item_id]
