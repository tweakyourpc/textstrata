import tempfile
import unittest

from textstrata.application.items import rename_item
from textstrata.ingest import build_item, ingest_text
from textstrata.linking import build_links, link_collisions
from textstrata.presentation.markdown import markdown_to_html
from textstrata.store import TextStrataStore


class WikiLinkFeatureTests(unittest.TestCase):
    def test_alias_wikilinks_resolve_to_real_target_and_render(self):
        target, _, _ = build_item("---\nid: target\ntitle: Target Note\naliases: [Old Target]\n---\n\nBody")
        source, _, _ = build_item("---\nid: source\ntitle: Source\n---\n\nSee [[Old Target|the target]].")

        links = build_links([source, target])
        self.assertEqual([(link.target, link.reason) for link in links if link.source == "source" and link.reason == "wikilink"], [("target", "wikilink")])
        rendered = markdown_to_html(
            source.body,
            link_resolver={"old target": ("target", "Target Note")},
        )
        self.assertIn('href="/item/target"', rendered)
        self.assertIn("the target", rendered)

    def test_ambiguous_title_does_not_choose_a_winner(self):
        source, _, _ = build_item("---\nid: source\ntitle: Source\n---\n\nSee [[Shared title]].")
        first, _, _ = build_item("---\nid: note.first\ntitle: Shared title\n---\n\nFirst")
        second, _, _ = build_item("---\nid: note.second\ntitle: Shared title\n---\n\nSecond")
        warnings: list[str] = []
        links = build_links([source, first, second], warnings=warnings)
        self.assertEqual(link_collisions([source, first, second]), {"shared title": ("note.first", "note.second")})
        self.assertNotIn("wikilink", {link.reason for link in links if link.source == "source"})
        self.assertEqual(len(warnings), 1)
        self.assertIn("note.first, note.second", warnings[0])

    def test_inferred_groups_are_bounded(self):
        items = [build_item(f"---\nid: note.{index:03d}\ntitle: Note {index}\ntags: [common]\n---\n\nBody")[0] for index in range(100)]
        links = build_links(items)
        outgoing: dict[str, int] = {}
        for link in links:
            outgoing[link.source] = outgoing.get(link.source, 0) + 1
        self.assertLessEqual(max(outgoing.values()), 12)

    def test_rename_updates_ids_and_wikilinks_but_preserves_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TextStrataStore(tmp)
            store.ensure_dirs()
            ingest_text(store, "---\nid: target\ntitle: Target\naliases: [Old Target]\n---\n\nTarget body")
            ingest_text(store, "---\nid: source\ntitle: Source\n---\n\n[[target]] and [[Old Target]].")

            result = rename_item(store, "target", "renamed-target")

            self.assertTrue(result["renamed"])
            self.assertEqual(result["updated_references"], 2)
            self.assertIsNone(store.normalized_path_for_id("target"))
            self.assertIn("[[renamed-target]]", store.normalized_path_for_id("source").read_text())
            renamed = build_item(store.normalized_path_for_id("renamed-target").read_text(), fallback_id="renamed-target")[0]
            self.assertEqual(renamed.aliases, ["Old Target"])


if __name__ == "__main__":
    unittest.main()
