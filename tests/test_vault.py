import tempfile
import unittest
from pathlib import Path

from textstrata.ingest import build_item
from textstrata.store import TextStrataStore
from textstrata.vault import export_obsidian_vault, import_obsidian_vault


class ObsidianVaultTests(unittest.TestCase):
    def test_import_rewrites_links_preserves_aliases_and_imports_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            workspace = Path(tmp) / "workspace"
            (vault / "folder").mkdir(parents=True)
            (vault / "attachments").mkdir()
            (vault / "folder/target.md").write_text("---\ntitle: Target\naliases: [Canonical Target]\n---\n\nTarget body\n", encoding="utf-8")
            (vault / "source.md").write_text("# Source\n\nSee [[folder/target|the target]].\n\n![[attachments/picture.txt]]\n", encoding="utf-8")
            (vault / "attachments/picture.txt").write_text("asset", encoding="utf-8")
            store = TextStrataStore(workspace)
            store.ensure_dirs()

            result = import_obsidian_vault(store, vault)

            self.assertEqual(result["imported"], 2)
            self.assertEqual(result["attachments"], 1)
            source_path = store.normalized_path_for_id("source")
            self.assertIsNotNone(source_path)
            source = source_path.read_text(encoding="utf-8")
            self.assertIn("[[folder-target|the target]]", source)
            self.assertIn("/asset/", source)
            target = build_item(store.normalized_path_for_id("folder-target").read_text(encoding="utf-8"), fallback_id="folder-target")[0]
            self.assertIn("Canonical Target", target.aliases)

    def test_export_writes_obsidian_notes_and_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            target = Path(tmp) / "exported"
            store = TextStrataStore(workspace)
            store.ensure_dirs()
            raw = "---\nid: source\ntitle: Source\naliases: [Old Source]\n---\n\nSee [[target]].\n"
            from textstrata.ingest import ingest_text
            ingest_text(store, raw)
            ingest_text(store, "---\nid: target\ntitle: Target\n---\n\nTarget.\n")
            original = store.original_dir.joinpath("source.md").read_text(encoding="utf-8")

            result = export_obsidian_vault(store, target)

            self.assertEqual(result["exported"], 2)
            self.assertIn("[[target]]", (target / "source.md").read_text(encoding="utf-8"))
            self.assertEqual(store.original_dir.joinpath("source.md").read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
