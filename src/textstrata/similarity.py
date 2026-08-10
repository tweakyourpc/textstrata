"""Deterministic similarity and graph authority scoring.

No AI, no embeddings, no heavy dependencies — everything here is classic
information-retrieval and graph theory that runs in milliseconds on thousands
of items and returns identical results on identical input. Three layers:

1. Similarity      — how alike two items are, from text (TF-IDF cosine) and
                     metadata (Jaccard over tags). Tokenization runs through
                     the vocabulary layer (``vocabulary.py``): Porter stemming
                     plus a synonym map, so "k8s"/"kubernetes" and
                     "configuring"/"configuration" fold together and notes
                     using different words for the same concept still connect.
2. Authority       — how much weight an item carries in the mesh, via PageRank
                     and HITS over the weighted link graph. This is the
                     "symbiotic link-back": nodes lend each other weight
                     through iteration until the scores converge.
3. Communities     — emergent topic clusters via deterministic label
                     propagation, so classifications surface as the corpus
                     grows instead of being declared up front.

The implementations are intentionally self-contained (stdlib only). Each has a
clean seam where a library (networkx, rank-bm25, datasketch, yake) can be
dropped in later without changing callers.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .models import TextStrataItem

# --------------------------------------------------------------------------- #
# Tokenization (deterministic)
# --------------------------------------------------------------------------- #

from .vocabulary import canonical_tokens


def tokenize(text: str, synonyms: dict[str, str] | None = None) -> list[str]:
    """Tokenize *text* into canonical tokens for TF-IDF.

    Delegates to the vocabulary pipeline: stopword filtering, synonym folding
    ("k8s" -> "kubernetes"), and Porter stemming ("configuring" -> "configur").
    This makes graph similarity vocabulary-aware and finally consistent with
    the porter-stemmed FTS5 catalog. Passing ``synonyms`` threads a per-store
    override map through; omitting it uses the curated base map.
    """
    return canonical_tokens(text, synonyms)


# --------------------------------------------------------------------------- #
# TF-IDF vectors + cosine similarity
# --------------------------------------------------------------------------- #


@dataclass
class TfidfModel:
    idf: dict[str, float]
    vectors: dict[str, dict[str, float]]  # item_id -> {term: weight} (L2-normalized)

    def cosine(self, a_id: str, b_id: str) -> float:
        va, vb = self.vectors.get(a_id), self.vectors.get(b_id)
        if not va or not vb:
            return 0.0
        # iterate the smaller vector
        if len(vb) < len(va):
            va, vb = vb, va
        return sum(w * vb.get(term, 0.0) for term, w in va.items())


def _item_text(item: TextStrataItem) -> str:
    # Title and tags carry extra signal; repeat them so they weigh more than a
    # single body mention without needing per-field weighting machinery.
    return f"{item.title} {item.title} {' '.join(item.tags)} {' '.join(item.tags)} {item.body}"


def build_tfidf(items: list[TextStrataItem], synonyms: dict[str, str] | None = None) -> TfidfModel:
    docs = {it.id: Counter(tokenize(_item_text(it), synonyms)) for it in items}
    n = len(docs) or 1
    df: Counter = Counter()
    for counts in docs.values():
        df.update(counts.keys())
    # smoothed idf, always positive
    idf = {term: math.log((1 + n) / (1 + d)) + 1.0 for term, d in df.items()}

    vectors: dict[str, dict[str, float]] = {}
    for item_id, counts in docs.items():
        total = sum(counts.values()) or 1
        vec = {term: (freq / total) * idf[term] for term, freq in counts.items()}
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        vectors[item_id] = {term: w / norm for term, w in vec.items()}
    return TfidfModel(idf=idf, vectors=vectors)


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


# --------------------------------------------------------------------------- #
# Similarity edges
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SimilarityEdge:
    source: str
    target: str
    score: float          # blended 0..1
    content: float        # tf-idf cosine
    tag: float            # jaccard over tags
    shared_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SimilarityPolicy:
    """Named, deterministic knobs for similarity and graph scoring."""

    content_weight: float = 0.7
    tag_weight: float = 0.3
    verbosity_baseline_words: float = 800.0
    similarity_threshold: float = 0.08
    top_k: int = 8
    max_feature_frequency: int = 64
    community_threshold_multiplier: float = 1.5
    community_min_content: float = 0.12
    type_multipliers: tuple[tuple[str, float], ...] = (
        ("playbook", 1.5),
        ("guide", 1.5),
        ("architecture", 1.5),
        ("architecture_note", 1.5),
        ("script", 1.0),
        ("utility", 1.0),
        ("code_sample", 1.0),
        ("command_recipe", 1.0),
        ("transcript", 0.7),
        ("reference", 0.7),
    )


DEFAULT_SIMILARITY_POLICY = SimilarityPolicy()

_WORD_RE = re.compile(r"\w+")


def _body_word_count(item: TextStrataItem) -> int:
    return len(_WORD_RE.findall(item.body))


def _verbosity_penalty(source_words: int, target_words: int, baseline: float) -> float:
    total_words = max(source_words + target_words, 1)
    if total_words <= baseline:
        return 1.0
    return math.sqrt(baseline / total_words)


def _type_multiplier(item: TextStrataItem, policy: SimilarityPolicy) -> float:
    return dict(policy.type_multipliers).get(item.type.value, 1.0)


def _edge_weight_with_type_bias(
    source_id: str,
    target_id: str,
    base_weight: float,
    items_by_id: dict[str, TextStrataItem],
    policy: SimilarityPolicy,
) -> float:
    target = items_by_id[target_id]
    return base_weight * _type_multiplier(target, policy)


def _normalized_link_weight(weight: float) -> float:
    if weight >= 4.0:
        return weight * 1.2
    if weight >= 2.0:
        return 0.6
    return 0.2


def _candidate_pairs(
    ids: list[str],
    model: TfidfModel,
    tag_sets: dict[str, set[str]],
    max_feature_frequency: int,
) -> list[tuple[str, str]]:
    """Build sparse deterministic candidates from shared terms or tags.

    Very common terms/tags are ignored as candidates once their posting list
    exceeds the policy cap. This prevents a ubiquitous tag from recreating an
    all-pairs similarity matrix while preserving focused topical signals.
    """
    if max_feature_frequency <= 1:
        return []
    index: dict[str, set[str]] = {}
    for item_id in sorted(ids):
        for term in model.vectors.get(item_id, {}):
            index.setdefault(f"term:{term}", set()).add(item_id)
        for tag in tag_sets[item_id]:
            index.setdefault(f"tag:{tag}", set()).add(item_id)

    pairs: set[tuple[str, str]] = set()
    for members in index.values():
        ordered = sorted(members)
        if len(ordered) > max_feature_frequency:
            continue
        for index_a, source in enumerate(ordered):
            for target in ordered[index_a + 1:]:
                pairs.add((source, target))
    return sorted(pairs)


def build_similarity_edges(
    items: list[TextStrataItem],
    *,
    threshold: float | None = None,
    top_k: int | None = None,
    synonyms: dict[str, str] | None = None,
    policy: SimilarityPolicy = DEFAULT_SIMILARITY_POLICY,
) -> list[SimilarityEdge]:
    """Sparse similarity above ``threshold``, keeping each item's top-k.

    Candidates come only from shared terms or tags, with common-feature posting
    lists capped by ``policy.max_feature_frequency``. This keeps routine work
    proportional to useful overlap rather than corpus size squared.
    """
    threshold = policy.similarity_threshold if threshold is None else threshold
    top_k = policy.top_k if top_k is None else top_k
    model = build_tfidf(items, synonyms)
    tag_sets = {it.id: {t.lower() for t in it.tags} for it in items}
    word_counts = {it.id: _body_word_count(it) for it in items}
    ids = [it.id for it in items]

    # Symmetric: compute once per unordered candidate pair, emit strongest per
    # node below. Candidate generation is deterministic and sparse.
    raw: dict[str, list[SimilarityEdge]] = {i: [] for i in ids}
    for a, b in _candidate_pairs(ids, model, tag_sets, policy.max_feature_frequency):
        penalty = _verbosity_penalty(word_counts[a], word_counts[b], policy.verbosity_baseline_words)
        content = model.cosine(a, b) * penalty
        tag = jaccard(tag_sets[a], tag_sets[b])
        score = policy.content_weight * content + policy.tag_weight * tag
        if score < threshold:
            continue
        shared = tuple(
            sorted(
                set(model.vectors.get(a, {})) & set(model.vectors.get(b, {})),
                key=lambda term: (-model.idf.get(term, 0.0), term),
            )[:5]
        )
        edge_ab = SimilarityEdge(a, b, score, content, tag, shared)
        edge_ba = SimilarityEdge(b, a, score, content, tag, shared)
        raw[a].append(edge_ab)
        raw[b].append(edge_ba)

    edges: list[SimilarityEdge] = []
    for node_id, node_edges in raw.items():
        node_edges.sort(key=lambda e: (-e.score, e.target))
        edges.extend(node_edges[:top_k])
    edges.sort(key=lambda e: (-e.score, e.source, e.target))
    return edges


# --------------------------------------------------------------------------- #
# Authority scoring: PageRank + HITS (stdlib, deterministic)
# --------------------------------------------------------------------------- #


def _weighted_adjacency(
    ids: list[str], edges: list[tuple[str, str, float]]
) -> dict[str, dict[str, float]]:
    adj: dict[str, dict[str, float]] = {i: {} for i in ids}
    for s, t, w in edges:
        if s in adj and t in adj and s != t:
            adj[s][t] = adj[s].get(t, 0.0) + w
    return adj


def pagerank(
    ids: list[str],
    edges: list[tuple[str, str, float]],
    *,
    damping: float = 0.85,
    iterations: int = 100,
    tol: float = 1e-9,
) -> dict[str, float]:
    """Weighted PageRank. Deterministic; converges in a few dozen iterations."""
    if not ids:
        return {}
    n = len(ids)
    adj = _weighted_adjacency(ids, edges)
    out_weight = {i: sum(adj[i].values()) for i in ids}
    rank = {i: 1.0 / n for i in ids}
    base = (1.0 - damping) / n

    for _ in range(iterations):
        nxt = {i: base for i in ids}
        dangling = damping * sum(rank[i] for i in ids if out_weight[i] == 0.0) / n
        for i in ids:
            nxt[i] += dangling
        for i in ids:
            if out_weight[i] == 0.0:
                continue
            share = damping * rank[i] / out_weight[i]
            for target, w in adj[i].items():
                nxt[target] += share * w
        delta = sum(abs(nxt[i] - rank[i]) for i in ids)
        rank = nxt
        if delta < tol:
            break
    return rank


@dataclass
class HitsScores:
    hub: dict[str, float]
    authority: dict[str, float]


def hits(
    ids: list[str],
    edges: list[tuple[str, str, float]],
    *,
    iterations: int = 100,
    tol: float = 1e-9,
) -> HitsScores:
    """Weighted HITS. Hubs point to good authorities; authorities are pointed
    to by good hubs — the mutually-reinforcing "symbiotic link-back"."""
    if not ids:
        return HitsScores({}, {})
    adj = _weighted_adjacency(ids, edges)
    rev: dict[str, dict[str, float]] = {i: {} for i in ids}
    for s in adj:
        for t, w in adj[s].items():
            rev[t][s] = rev[t].get(s, 0.0) + w

    hub = {i: 1.0 for i in ids}
    auth = {i: 1.0 for i in ids}
    for _ in range(iterations):
        new_auth = {i: sum(hub[s] * w for s, w in rev[i].items()) for i in ids}
        norm = math.sqrt(sum(v * v for v in new_auth.values())) or 1.0
        new_auth = {i: v / norm for i, v in new_auth.items()}

        new_hub = {i: sum(new_auth[t] * w for t, w in adj[i].items()) for i in ids}
        norm = math.sqrt(sum(v * v for v in new_hub.values())) or 1.0
        new_hub = {i: v / norm for i, v in new_hub.items()}

        delta = sum(abs(new_hub[i] - hub[i]) for i in ids) + sum(
            abs(new_auth[i] - auth[i]) for i in ids
        )
        hub, auth = new_hub, new_auth
        if delta < tol:
            break
    return HitsScores(hub=hub, authority=auth)


# --------------------------------------------------------------------------- #
# Emergent communities: deterministic label propagation
# --------------------------------------------------------------------------- #


def label_propagation(
    ids: list[str],
    edges: list[tuple[str, str, float]],
    *,
    iterations: int = 50,
) -> dict[str, str]:
    """Assign each node a community label from its neighbours' labels.

    Made deterministic by (a) seeding each node with its own id, (b) processing
    nodes in sorted order, and (c) breaking ties by the lexicographically
    smallest candidate label. Communities emerge from the edge structure; no
    count is specified in advance.
    """
    if not ids:
        return {}
    neigh: dict[str, dict[str, float]] = {i: {} for i in ids}
    for s, t, w in edges:
        if s in neigh and t in neigh and s != t:
            neigh[s][t] = neigh[s].get(t, 0.0) + w
            neigh[t][s] = neigh[t].get(s, 0.0) + w

    label = {i: i for i in ids}
    ordered = sorted(ids)
    for _ in range(iterations):
        changed = False
        for node in ordered:
            if not neigh[node]:
                continue
            weight_by_label: dict[str, float] = {}
            for nb, w in neigh[node].items():
                weight_by_label[label[nb]] = weight_by_label.get(label[nb], 0.0) + w
            best = min(
                weight_by_label.items(), key=lambda kv: (-kv[1], kv[0])
            )[0]
            if label[node] != best:
                label[node] = best
                changed = True
        if not changed:
            break
    return label


# --------------------------------------------------------------------------- #
# Combined knowledge scoring
# --------------------------------------------------------------------------- #


@dataclass
class KnowledgeScore:
    item_id: str
    score: float          # 0..100, blended headline number
    pagerank: float
    hub: float
    authority: float
    degree: int           # number of similarity + explicit neighbours
    community: str
    neighbours: list[str] = field(default_factory=list)


def _relabel_by_authority(
    communities: dict[str, str], authority: dict[str, float]
) -> dict[str, str]:
    """Rename each community after its highest-authority member, so labels are
    meaningful ("this cluster is anchored by X") rather than arbitrary node ids.
    Deterministic: ties break on the smaller id."""
    members: dict[str, list[str]] = {}
    for node, label in communities.items():
        members.setdefault(label, []).append(node)
    remap: dict[str, str] = {}
    for label, group in members.items():
        anchor = max(group, key=lambda i: (authority.get(i, 0.0), [-ord(c) for c in i]))
        remap[label] = anchor
    return {node: remap[label] for node, label in communities.items()}


def score_corpus(
    items: list[TextStrataItem],
    explicit_edges: list[tuple[str, str, float]] | None = None,
    *,
    similarity_threshold: float | None = None,
    top_k: int | None = None,
    synonyms: dict[str, str] | None = None,
    policy: SimilarityPolicy = DEFAULT_SIMILARITY_POLICY,
) -> dict[str, KnowledgeScore]:
    """Score every item by how much it enriches the mesh.

    ``explicit_edges`` are (source, target, weight) triples from the existing
    deterministic linker (dependency / reference / shared_tag / same_type);
    similarity edges are computed here and blended in. ``synonyms`` optionally
    threads a per-store vocabulary map so "k8s" and "kubernetes" contribute to
    the same edges. Returns a map of item_id -> KnowledgeScore. Fully
    deterministic.
    """
    ids = sorted(it.id for it in items)
    if not ids:
        return {}
    items_by_id = {it.id: it for it in items}

    sim_edges = build_similarity_edges(
        items, threshold=similarity_threshold, top_k=top_k, synonyms=synonyms, policy=policy
    )
    graph_edges: list[tuple[str, str, float]] = [
        (
            e.source,
            e.target,
            _edge_weight_with_type_bias(e.source, e.target, e.score, items_by_id, policy),
        )
        for e in sim_edges
    ]
    if explicit_edges:
        graph_edges.extend(
            (
                s,
                t,
                _edge_weight_with_type_bias(
                    s,
                    t,
                    _normalized_link_weight(w),
                    items_by_id,
                    policy,
                ),
            )
            for s, t, w in explicit_edges
        )

    pr = pagerank(ids, graph_edges)
    hs = hits(ids, graph_edges)

    # Community detection needs a SPARSE graph or one label swamps everything.
    # The blended graph is near-complete (shared broad tags + same_type edges
    # connect nearly all pairs), so clusters can't separate on it. Use only
    # strong content-similarity edges — the signal that actually distinguishes
    # topics — at a higher bar.
    community_edges = [
        (e.source, e.target, e.content)
        for e in sim_edges
        if e.content >= max(
            (policy.similarity_threshold if similarity_threshold is None else similarity_threshold)
            * policy.community_threshold_multiplier,
            policy.community_min_content,
        )
    ]
    raw_communities = label_propagation(ids, community_edges)
    communities = _relabel_by_authority(raw_communities, hs.authority)

    degree: dict[str, int] = {i: 0 for i in ids}
    neighbours: dict[str, set[str]] = {i: set() for i in ids}
    for s, t, _w in graph_edges:
        degree[s] += 1
        neighbours[s].add(t)

    pr_max = max(pr.values()) if pr else 1.0
    auth_max = max(hs.authority.values()) if hs.authority else 1.0
    hub_max = max(hs.hub.values()) if hs.hub else 1.0

    scores: dict[str, KnowledgeScore] = {}
    for i in ids:
        pr_n = pr[i] / pr_max if pr_max else 0.0
        auth_n = hs.authority[i] / auth_max if auth_max else 0.0
        hub_n = hs.hub[i] / hub_max if hub_max else 0.0
        blended = 100.0 * (0.5 * pr_n + 0.3 * auth_n + 0.2 * hub_n)
        scores[i] = KnowledgeScore(
            item_id=i,
            score=round(blended, 2),
            pagerank=round(pr[i], 6),
            hub=round(hs.hub[i], 6),
            authority=round(hs.authority[i], 6),
            degree=degree[i],
            community=communities[i],
            neighbours=sorted(neighbours[i]),
        )
    return scores
