import unittest

from textstrata.models import ContentType, TextStrataItem
from textstrata.similarity import (
    build_similarity_edges,
    build_tfidf,
    hits,
    jaccard,
    label_propagation,
    pagerank,
    score_corpus,
    SimilarityPolicy,
    tokenize,
)
from types import SimpleNamespace


def _item(item_id, title, body, tags=(), typ=ContentType.NOTE, related=(), deps=()):
    return TextStrataItem(
        id=item_id, type=typ, title=title, body=body,
        tags=list(tags), related=list(related), dependencies=list(deps),
    )


CORPUS = [
    _item("sec.csp", "Content Security Policy", "strict CSP blocks external origins and inline scripts", ["security", "web"]),
    _item("sec.ssrf", "SSRF Protection", "validate URLs, block private addresses, prevent server side request forgery", ["security", "web"]),
    _item("cook.pasta", "Pasta Recipe", "boil water add salt cook pasta drain serve with sauce", ["cooking", "food"]),
    _item("cook.bread", "Bread Recipe", "mix flour water yeast salt knead prove bake bread", ["cooking", "food"]),
]


class TokenizeTfidfTests(unittest.TestCase):
    def test_tokenize_drops_stopwords_and_short(self):
        toks = tokenize("The quick brown fox is a FOX")
        self.assertNotIn("the", toks)
        self.assertNotIn("is", toks)
        self.assertNotIn("a", toks)
        self.assertIn("quick", toks)
        self.assertEqual(toks.count("fox"), 2)  # case-folded

    def test_tfidf_vectors_are_normalized(self):
        model = build_tfidf(CORPUS)
        for vec in model.vectors.values():
            norm = sum(w * w for w in vec.values()) ** 0.5
            self.assertAlmostEqual(norm, 1.0, places=6)

    def test_related_docs_more_similar_than_unrelated(self):
        model = build_tfidf(CORPUS)
        sec = model.cosine("sec.csp", "sec.ssrf")
        cross = model.cosine("sec.csp", "cook.pasta")
        self.assertGreater(sec, cross)

    def test_jaccard(self):
        self.assertEqual(jaccard({"a", "b"}, {"a", "b"}), 1.0)
        self.assertEqual(jaccard({"a"}, {"b"}), 0.0)
        self.assertAlmostEqual(jaccard({"a", "b"}, {"b", "c"}), 1 / 3)


class SimilarityEdgeTests(unittest.TestCase):
    def test_edges_connect_topical_neighbours(self):
        edges = build_similarity_edges(CORPUS, threshold=0.05)
        pairs = {(e.source, e.target) for e in edges}
        self.assertIn(("sec.csp", "sec.ssrf"), pairs)
        self.assertIn(("cook.pasta", "cook.bread"), pairs)

    def test_determinism(self):
        a = build_similarity_edges(CORPUS)
        b = build_similarity_edges(CORPUS)
        self.assertEqual(
            [(e.source, e.target, round(e.score, 6)) for e in a],
            [(e.source, e.target, round(e.score, 6)) for e in b],
        )

    def test_long_transcript_edges_are_dampened_by_length(self):
        short_a = _item(
            "guide.a",
            "Guide A",
            "backup restore verify recovery plan " * 20,
            ["backup"],
            typ=ContentType.PLAYBOOK,
        )
        short_b = _item(
            "guide.b",
            "Guide B",
            "backup restore verify recovery checklist " * 20,
            ["backup"],
            typ=ContentType.PLAYBOOK,
        )
        long_transcript = TextStrataItem(
            id="transcript.long",
            type=ContentType.REFERENCE,
            title="Long Transcript",
            body="backup restore verify recovery discussion " * 800,
            tags=["backup"],
        )
        edges = build_similarity_edges([short_a, short_b, long_transcript], threshold=0.01, top_k=3)
        by_pair = {(e.source, e.target): e.score for e in edges}
        self.assertGreater(by_pair[("guide.a", "guide.b")], by_pair[("guide.a", "transcript.long")])

    def test_common_features_are_capped_as_candidates(self):
        items = [_item(f"common.{index:03d}", f"Item {index}", f"unique{index}", ["common"]) for index in range(20)]
        self.assertEqual(build_similarity_edges(items, policy=SimilarityPolicy(max_feature_frequency=4)), [])


class GraphAlgoTests(unittest.TestCase):
    def test_pagerank_sums_to_one(self):
        ids = ["a", "b", "c"]
        edges = [("a", "b", 1.0), ("b", "c", 1.0), ("c", "a", 1.0)]
        pr = pagerank(ids, edges)
        self.assertAlmostEqual(sum(pr.values()), 1.0, places=6)

    def test_pagerank_rewards_incoming_links(self):
        ids = ["hub", "a", "b", "c"]
        edges = [("a", "hub", 1.0), ("b", "hub", 1.0), ("c", "hub", 1.0)]
        pr = pagerank(ids, edges)
        self.assertGreater(pr["hub"], pr["a"])

    def test_hits_authority_and_hub_roles(self):
        # a and b point to auth; auth points nowhere.
        ids = ["a", "b", "auth"]
        edges = [("a", "auth", 1.0), ("b", "auth", 1.0)]
        hs = hits(ids, edges)
        self.assertGreater(hs.authority["auth"], hs.authority["a"])
        self.assertGreater(hs.hub["a"], hs.hub["auth"])

    def test_pagerank_deterministic(self):
        ids = ["a", "b", "c"]
        edges = [("a", "b", 1.0), ("b", "c", 2.0)]
        self.assertEqual(pagerank(ids, edges), pagerank(ids, edges))

    def test_empty_graph(self):
        self.assertEqual(pagerank([], []), {})
        self.assertEqual(hits([], []).hub, {})


class CommunityTests(unittest.TestCase):
    def test_two_topics_form_two_communities(self):
        edges = [
            ("sec.csp", "sec.ssrf", 1.0),
            ("cook.pasta", "cook.bread", 1.0),
        ]
        labels = label_propagation(["sec.csp", "sec.ssrf", "cook.pasta", "cook.bread"], edges)
        self.assertEqual(labels["sec.csp"], labels["sec.ssrf"])
        self.assertEqual(labels["cook.pasta"], labels["cook.bread"])
        self.assertNotEqual(labels["sec.csp"], labels["cook.pasta"])

    def test_label_propagation_deterministic(self):
        edges = [("a", "b", 1.0), ("b", "c", 1.0)]
        self.assertEqual(
            label_propagation(["a", "b", "c"], edges),
            label_propagation(["a", "b", "c"], edges),
        )


class ScoreCorpusTests(unittest.TestCase):
    def test_scores_and_communities(self):
        scores = score_corpus(CORPUS, similarity_threshold=0.05)
        self.assertEqual(set(scores), {it.id for it in CORPUS})
        for s in scores.values():
            self.assertGreaterEqual(s.score, 0.0)
            self.assertLessEqual(s.score, 100.0)
        # security docs share a community distinct from cooking docs
        self.assertEqual(scores["sec.csp"].community, scores["sec.ssrf"].community)
        self.assertEqual(scores["cook.pasta"].community, scores["cook.bread"].community)
        self.assertNotEqual(scores["sec.csp"].community, scores["cook.pasta"].community)

    def test_deterministic_scores(self):
        a = score_corpus(CORPUS, similarity_threshold=0.05)
        b = score_corpus(CORPUS, similarity_threshold=0.05)
        self.assertEqual(
            {k: v.score for k, v in a.items()},
            {k: v.score for k, v in b.items()},
        )

    def test_empty_corpus(self):
        self.assertEqual(score_corpus([]), {})

    def test_explicit_relationships_and_type_bias_outrank_raw_transcripts(self):
        playbook = _item(
            "backup-restore-playbook",
            "Backup Restore Playbook",
            "backup restore verification recovery sequence " * 20,
            ["backup", "recovery"],
            typ=ContentType.PLAYBOOK,
            related=("restore-checklist",),
        )
        checklist = _item(
            "restore-checklist",
            "Restore Checklist",
            "backup restore verification checklist steps " * 18,
            ["backup", "recovery"],
            typ=ContentType.NOTE,
            related=("backup-restore-playbook",),
        )
        transcript_a = TextStrataItem(
            id="youtube.transcript.a",
            type=ContentType.REFERENCE,
            title="Backup Transcript A",
            body="backup restore verification recovery conversation " * 1000,
            tags=["backup", "transcript"],
        )
        transcript_b = TextStrataItem(
            id="youtube.transcript.b",
            type=ContentType.REFERENCE,
            title="Backup Transcript B",
            body="backup restore verification recovery interview " * 1000,
            tags=["backup", "transcript"],
        )
        corpus = [playbook, checklist, transcript_a, transcript_b]
        explicit_edges = [
            ("backup-restore-playbook", "restore-checklist", 4.0),
            ("restore-checklist", "backup-restore-playbook", 4.0),
        ]
        scores = score_corpus(corpus, explicit_edges, similarity_threshold=0.01, top_k=4)
        self.assertGreater(
            scores["backup-restore-playbook"].score,
            scores["youtube.transcript.a"].score,
        )

    def test_type_multiplier_favors_curated_types_over_reference(self):
        items = [
            SimpleNamespace(id="guide", title="Guide", body="same topic words " * 30, tags=["topic"], type=SimpleNamespace(value="playbook")),
            SimpleNamespace(id="reference", title="Reference", body="same topic words " * 30, tags=["topic"], type=SimpleNamespace(value="reference")),
            SimpleNamespace(id="source.a", title="Source A", body="same topic words " * 30, tags=["topic"], type=SimpleNamespace(value="note")),
            SimpleNamespace(id="source.b", title="Source B", body="same topic words " * 30, tags=["topic"], type=SimpleNamespace(value="note")),
        ]
        explicit_edges = [
            ("source.a", "guide", 4.0),
            ("source.b", "guide", 4.0),
            ("source.a", "reference", 4.0),
            ("source.b", "reference", 4.0),
        ]
        scores = score_corpus(items, explicit_edges, similarity_threshold=0.01, top_k=4)
        self.assertGreater(scores["guide"].score, scores["reference"].score)


if __name__ == "__main__":
    unittest.main()
