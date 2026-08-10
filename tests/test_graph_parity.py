from __future__ import annotations

import unittest
from types import SimpleNamespace as NS

from textstrata.application.graph import build_graph_payload
from textstrata.linking import build_links
from textstrata.presentation.pages.graph import render_graph_html
from textstrata.similarity import build_similarity_edges, score_corpus


def item(item_id, title, tags, body, type_="reference", related=()):
    return NS(
        id=item_id,
        title=title,
        tags=list(tags),
        body=body,
        type=NS(value=type_),
        related=list(related),
        dependencies=[],
        retrieval_priority=50,
        provenance=NS(ingested_at="2026-07-08T12:00:00+00:00"),
    )


def corpus():
    return [
        item(
            "docker-guide",
            "Docker Guide",
            ["docker", "containers"],
            "docker compose containers volumes networking " * 10,
            related=["k8s-notes"],
        ),
        item(
            "k8s-notes",
            "Kubernetes Notes",
            ["k8s", "containers"],
            "kubernetes pods containers orchestration deployment " * 10,
        ),
        item(
            "recipe-pasta",
            "Pasta Recipe",
            ["cooking"],
            "boil water pasta sauce tomato basil garlic " * 10,
            type_="playbook",
        ),
        item(
            "orphan-note",
            "Lonely Note",
            [],
            "completely unrelated quantum entanglement zebra xylophone " * 5,
        ),
    ]


def payload(items):
    links = build_links(items)
    edges = build_similarity_edges(items)
    scores = score_corpus(
        items,
        [(link.source, link.target, float(link.weight)) for link in links],
    )
    return build_graph_payload(items, links, edges, scores)


class GraphParityTests(unittest.TestCase):
    def test_payload_is_deterministic_and_additive(self):
        result = payload(corpus())

        self.assertEqual(result, payload(corpus()))
        self.assertIn("communities", result)
        self.assertIn("attention", result)
        for node in result["nodes"]:
            for key in (
                "pagerank",
                "hub",
                "authority",
                "in",
                "out",
                "degree",
                "orphan",
                "ingested",
            ):
                self.assertIn(key, node)

    def test_payload_explains_similarity_and_attention(self):
        result = payload(corpus())
        pair = [
            edge
            for edge in result["similarity"]
            if {edge["source"], edge["target"]} == {"docker-guide", "k8s-notes"}
        ]

        self.assertTrue(pair)
        self.assertTrue(pair[0]["shared"])
        surfaced = set(result["attention"]["orphans"]) | set(result["attention"]["weak"])
        self.assertIn("orphan-note", surfaced)

    def test_page_restores_reference_interaction_contract(self):
        html = render_graph_html()

        for marker in (
            "d3.zoom()",
            "Search nodes",
            "id=\"inspector\"",
            "function focusOn(d)",
            "function expandFocus()",
            "function focusCommunity(label)",
            "d.fx=e.x;d.fy=e.y",
            "e.shared.slice(0,3).join",
            "params.get('focus')",
            "params.get('community')",
        ):
            self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main()
