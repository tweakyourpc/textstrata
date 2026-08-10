"""Tests for the vocabulary normalization layer (stemming + synonyms +
corpus inference) and its effect on similarity scoring."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from textstrata import review
from textstrata.models import ContentType, TextStrataItem, Provenance
from textstrata.similarity import build_similarity_edges, build_tfidf
from textstrata.store import TextStrataStore
from textstrata.vocabulary import (
    canonical_token,
    canonical_tokens,
    infer_synonyms,
    load_synonyms,
    save_synonyms,
    stem,
)


def _item(id, title, body, tags):
    return TextStrataItem(
        id=id, type=ContentType.NOTE, title=title, body=body,
        tags=list(tags), provenance=Provenance(),
    )


class StemmerTests(unittest.TestCase):
    def test_morphological_variants_collapse(self):
        self.assertEqual(stem("configuring"), stem("configured"))
        self.assertEqual(stem("configured"), stem("configuration"))

    def test_short_and_nonalpha_untouched(self):
        self.assertEqual(stem("k8s"), "k8s")     # has a digit
        self.assertEqual(stem("ci"), "ci")        # <= 2 chars
        self.assertEqual(stem("v1.2"), "v1.2")    # punctuation
        self.assertEqual(stem("fox"), "fox")      # no reducible suffix

    def test_reference_vectors(self):
        # A sampling from Porter's published test set.
        cases = {
            "caresses": "caress", "ponies": "poni", "cats": "cat",
            "motoring": "motor", "sing": "sing", "happy": "happi",
            "relational": "relat", "conditional": "condit",
            "rational": "ration", "digitizer": "digit",
            "operator": "oper", "revival": "reviv", "allowance": "allow",
            "adjustable": "adjust", "dependent": "depend",
        }
        for word, expected in cases.items():
            self.assertEqual(stem(word), expected, f"{word} -> {stem(word)} != {expected}")

    def test_deterministic(self):
        for w in ("kubernetes", "authentication", "partitioning"):
            self.assertEqual(stem(w), stem(w))


class SynonymMapTests(unittest.TestCase):
    def test_base_synonyms_applied_before_stemming(self):
        # k8s -> kubernetes (synonym), then stemmed
        self.assertEqual(canonical_token("k8s", load_synonyms()), stem("kubernetes"))

    def test_canonical_tokens_fold_vocabulary(self):
        toks = set(canonical_tokens("Running k8s with auth and configs"))
        # kubernetes, authentication, configuration all present in canonical form
        self.assertIn(stem("kubernetes"), toks)
        self.assertIn(stem("authentication"), toks)
        self.assertIn(stem("configuration"), toks)

    def test_trailing_punctuation_stripped(self):
        toks = canonical_tokens("deploy the service. update the config.")
        self.assertIn(stem("service"), toks)
        self.assertNotIn("service.", toks)

    def test_internal_punctuation_preserved(self):
        toks = canonical_tokens("use v1.2 and user_id")
        self.assertIn("v1.2", toks)
        self.assertIn("user_id", toks)

    def test_camel_case_is_split_before_stemming(self):
        toks = canonical_tokens("updateDatabase HTTPServer")
        self.assertIn(stem("update"), toks)
        self.assertIn(stem("database"), toks)
        self.assertIn(stem("http"), toks)
        self.assertIn(stem("server"), toks)

    def test_nfkc_folds_compatibility_characters(self):
        self.assertEqual(canonical_tokens("Ｄａｔａｂａｓｅ"), canonical_tokens("Database"))


class StoreOverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="textstrata-vocab-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_override_extends_base(self):
        save_synonyms(self.tmp, {"zanzibar": "database"})
        merged = load_synonyms(self.tmp)
        self.assertEqual(merged["zanzibar"], "database")
        # base entries still present
        self.assertEqual(merged["k8s"], "kubernetes")

    def test_override_persists_and_merges(self):
        save_synonyms(self.tmp, {"foo": "bar"})
        save_synonyms(self.tmp, {"baz": "qux"})
        merged = load_synonyms(self.tmp)
        self.assertEqual(merged["foo"], "bar")
        self.assertEqual(merged["baz"], "qux")


class SimilarityImpactTests(unittest.TestCase):
    def test_vocabulary_connects_divergent_notes(self):
        a = _item("a", "Scaling with k8s",
                  "We run microservices on k8s. Auth per service. Configuring deps.",
                  ["k8s", "infra"])
        b = _item("b", "Kubernetes operations",
                  "Services scale on kubernetes. Authentication per service. Configure dependencies.",
                  ["kubernetes", "infrastructure"])
        c = _item("c", "Sourdough bread",
                  "Mix flour water salt yeast. Knead and prove. Bake until golden.",
                  ["cooking"])
        model = build_tfidf([a, b, c])
        ab = model.cosine("a", "b")
        ac = model.cosine("a", "c")
        # The two same-topic notes should now be strongly connected...
        self.assertGreater(ab, 0.3)
        # ...and clearly more similar than the unrelated pair.
        self.assertGreater(ab, ac)
        self.assertLess(ac, 0.05)

    def test_edge_created_above_threshold(self):
        a = _item("a", "k8s notes", "k8s auth config deps", ["k8s"])
        b = _item("b", "kubernetes notes", "kubernetes authentication configuration dependencies", ["kubernetes"])
        edges = build_similarity_edges([a, b], threshold=0.08)
        pairs = {(e.source, e.target) for e in edges}
        self.assertIn(("a", "b"), pairs)


class InferenceTests(unittest.TestCase):
    def test_infers_prefix_abbreviation_from_cooccurrence(self):
        # A prefix-abbreviation pair co-tagged repeatedly -> proposal.
        # ("kube" is a prefix of "kubernetes"; string-closeness catches this,
        # unlike a purely semantic pair such as k8s/kubernetes which belongs
        # in the curated base map.)
        items = [
            _item(f"n{i}", f"Note {i}", "content", ["kube", "kubernetes", "ops"])
            for i in range(4)
        ]
        proposals = infer_synonyms(items, existing={}, min_cooccurrence=3)
        keys = {(p.variant, p.canonical) for p in proposals}
        self.assertIn(("kube", "kubernetes"), keys)

    def test_infers_shared_stem_pair(self):
        # "configuring" and "configuration" share a stem -> proposal.
        items = [
            _item(f"n{i}", "T", "b", ["configuring", "configuration"])
            for i in range(4)
        ]
        proposals = infer_synonyms(items, existing={}, min_cooccurrence=3)
        variants = {p.variant for p in proposals}
        canons = {p.canonical for p in proposals}
        self.assertTrue(variants and canons)
        # Both terms share a stem, so one folds into the other.
        self.assertTrue(
            {"configuring", "configuration"} & variants
            and {"configuring", "configuration"} & canons
        )

    def test_respects_min_cooccurrence(self):
        items = [
            _item("n1", "A", "x", ["alpha", "alphas"]),
            _item("n2", "B", "y", ["alpha", "alphas"]),
        ]
        # Only 2 co-occurrences; default threshold 3 -> nothing.
        self.assertEqual(infer_synonyms(items, existing={}, min_cooccurrence=3), [])
        # Lower the bar -> the near-identical pair surfaces.
        got = infer_synonyms(items, existing={}, min_cooccurrence=2)
        self.assertTrue(got)

    def test_skips_already_mapped(self):
        items = [
            _item(f"n{i}", "T", "b", ["k8s", "kubernetes"]) for i in range(5)
        ]
        # k8s already in base map -> not re-proposed.
        proposals = infer_synonyms(items, existing=load_synonyms(), min_cooccurrence=3)
        self.assertNotIn("k8s", {p.variant for p in proposals})

    def test_unrelated_tags_not_proposed(self):
        items = [
            _item(f"n{i}", "T", "b", ["cooking", "security"]) for i in range(5)
        ]
        proposals = infer_synonyms(items, existing={}, min_cooccurrence=3)
        # cooking/security co-occur but are not close forms -> no proposal.
        self.assertEqual(proposals, [])

    def test_deterministic_ordering(self):
        items = [
            _item(f"n{i}", "T", "b", ["config", "configuration", "auth", "authentication"])
            for i in range(4)
        ]
        first = infer_synonyms(items, existing={}, min_cooccurrence=3)
        second = infer_synonyms(items, existing={}, min_cooccurrence=3)
        self.assertEqual([p.as_dict() for p in first], [p.as_dict() for p in second])


class ReviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="textstrata-vocab-review-")
        self.store = TextStrataStore(Path(self.tmp))
        self.store.ensure_dirs()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refresh_enqueues_then_confirm_writes_synonyms(self):
        items = [
            _item(f"n{i}", "T", "b", ["kubernetes", "kubernetese"])  # near-typo pair
            for i in range(4)
        ]
        pending = review.refresh_synonym_proposals(self.store, items, min_cooccurrence=3)
        self.assertTrue(pending, "expected at least one proposal")
        p = pending[0]

        confirmed = review.confirm_synonym(self.store, p["variant"], p["canonical"])
        self.assertEqual(confirmed["status"], "confirmed")

        # It's now in the effective map for this store.
        merged = load_synonyms(self.store.root)
        self.assertEqual(merged.get(p["variant"]), p["canonical"])

        # And no longer pending.
        still_pending = review.list_pending_synonyms(self.store)
        self.assertNotIn(
            (p["variant"], p["canonical"]),
            {(e["variant"], e["canonical"]) for e in still_pending},
        )

    def test_reject_prevents_resuggestion(self):
        items = [
            _item(f"n{i}", "T", "b", ["provisioning", "provisioned"])
            for i in range(4)
        ]
        pending = review.refresh_synonym_proposals(self.store, items, min_cooccurrence=3)
        self.assertTrue(pending, "expected a proposal for the shared-stem pair")
        p = pending[0]
        review.reject_synonym(self.store, p["variant"], p["canonical"])
        # Refresh again: the rejected proposal must not come back.
        again = review.refresh_synonym_proposals(self.store, items, min_cooccurrence=3)
        self.assertNotIn(
            (p["variant"], p["canonical"]),
            {(e["variant"], e["canonical"]) for e in again},
        )


if __name__ == "__main__":
    unittest.main()
