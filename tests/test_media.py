import unittest
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from textstrata.acquisition import AssetStore
from textstrata.presentation.markdown import inline_markdown
from textstrata.presentation.pages.media import render_media_html
from textstrata.presentation.skin import PAPER_SKIN


class MediaLibraryTests(unittest.TestCase):
    def test_asset_index_is_stable_and_has_embed_metadata(self):
        with TemporaryDirectory() as directory:
            store = AssetStore(Path(directory))
            asset = store.put(b"photo bytes", "holiday.jpg", "image/jpeg")
            listed = store.list_assets()
            self.assertEqual([entry["id"] for entry in listed], [asset.id])
            self.assertEqual(listed[0]["url"], f"/asset/{asset.id}")
            self.assertTrue(listed[0]["is_image"])

    def test_rendered_images_mark_lightbox_and_gallery_copies_embed(self):
        asset_id = "a" * 64
        rendered = inline_markdown(f"![Holiday](/asset/{asset_id})")
        self.assertIn('data-lightbox="1"', rendered)
        page = render_media_html([{
            "id": asset_id, "original_name": "holiday.jpg", "media_type": "image/jpeg",
            "size": 1024, "is_image": True, "preview_url": f"/asset/{asset_id}?preview=1",
            "url": f"/asset/{asset_id}", "width": 100, "height": 80,
        }], PAPER_SKIN, version="test")
        self.assertIn("Media library", page)
        self.assertIn("Copy embed", page)
        self.assertIn('id="media-upload-form"', page)
        self.assertIn("/api/asset/upload", page)
        self.assertIn(f"![holiday.jpg](/asset/{asset_id})", page)

    def test_media_surface_has_composed_navigation_and_mobile_controls(self):
        page = render_media_html([], PAPER_SKIN, version="test")
        self.assertIn("Media library", page)
        self.assertIn("media-filter", page)
        self.assertIn(".menu-dropdown.open", page)
        self.assertIn("@media (max-width:700px)", page)
        ids = re.findall(r'id="([^"]+)"', page)
        self.assertEqual(len(ids), len(set(ids)))
        for index, script in enumerate(re.findall(r"<script>(.*?)</script>", page, flags=re.S)):
            script_path = Path(f"/tmp/textstrata-media-script-{index}.js")
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(script_path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
