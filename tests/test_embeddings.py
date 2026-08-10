import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from textstrata.embeddings import (
    EMBEDDINGS_FILE,
    _build_item_text,
    _cosine_similarity,
    compute_embeddings,
    embeddings_path,
    ensure_embeddings,
    load_embeddings,
    save_embeddings,
    search_semantic,
)
from textstrata.models import ContentType, TextStrataItem


def _make_item(item_id: str, title: str = "", tags=None, body: str = ""):
    return TextStrataItem(
        id=item_id,
        type=ContentType.NOTE,
        title=title,
        tags=tags or [],
        body=body,
    )


class TestBuildItemText(unittest.TestCase):
    def test_title_tags_body(self):
        item = _make_item("t.1", title="Hello", tags=["a", "b"], body="World")
        text = _build_item_text(item)
        self.assertIn("title: Hello", text)
        self.assertIn("tags: a, b", text)
        self.assertIn("World", text)

    def test_no_body(self):
        item = _make_item("t.2", title="Hello", tags=["a"])
        text = _build_item_text(item)
        self.assertIn("title: Hello", text)
        self.assertIn("tags: a", text)

    def test_no_tags(self):
        item = _make_item("t.3", title="Hello", body="World")
        text = _build_item_text(item)
        self.assertIn("title: Hello", text)
        self.assertIn("World", text)

    def test_minimal(self):
        item = _make_item("t.4")
        text = _build_item_text(item)
        self.assertEqual(text, "")


class TestCosineSimilarity(unittest.TestCase):
    def test_identical(self):
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(_cosine_similarity(v, v), 1.0)

    def test_orthogonal(self):
        self.assertAlmostEqual(_cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_opposite(self):
        self.assertAlmostEqual(_cosine_similarity([1, 0], [-1, 0]), -1.0)

    def test_partial(self):
        sim = _cosine_similarity([1, 0, 0], [1, 1, 0])
        expected = 1 / math.sqrt(2)
        self.assertAlmostEqual(sim, expected)

    def test_zero_vector(self):
        self.assertEqual(_cosine_similarity([0, 0], [1, 0]), 0.0)

    def test_both_zero(self):
        self.assertEqual(_cosine_similarity([0, 0], [0, 0]), 0.0)


class FakeArray:
    def __init__(self, vals):
        self.vals = vals
    def tolist(self):
        return self.vals


class TestComputeEmbeddings(unittest.TestCase):
    @patch("textstrata.embeddings._get_model")
    def test_compute(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.encode.return_value = [
            FakeArray([0.1, 0.2]),
            FakeArray([0.3, 0.4]),
        ]
        mock_get_model.return_value = mock_model

        items = [
            _make_item("a.1", title="One", body="first"),
            _make_item("b.2", title="Two", body="second"),
        ]
        result = compute_embeddings(items)
        self.assertEqual(result, {"a.1": [0.1, 0.2], "b.2": [0.3, 0.4]})

    @patch("textstrata.embeddings._get_model")
    def test_empty_items(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        mock_get_model.return_value = mock_model

        result = compute_embeddings([])
        self.assertEqual(result, {})

    @patch("textstrata.embeddings._get_model")
    def test_text_passed_to_model(self, mock_get_model):
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        items = [_make_item("x", title="Test", tags=["tag1"], body="body text")]
        compute_embeddings(items)
        mock_model.encode.assert_called_once()
        texts_arg = mock_model.encode.call_args[0][0]
        self.assertEqual(len(texts_arg), 1)
        self.assertIn("title: Test", texts_arg[0])
        self.assertIn("tags: tag1", texts_arg[0])
        self.assertIn("body text", texts_arg[0])


class TestEmbeddingsFileIO(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_path(self):
        self.assertEqual(embeddings_path(self.tmp), self.tmp / ".fabric" / EMBEDDINGS_FILE)

    def test_save_and_load(self):
        data = {"a": [0.1, 0.2], "b": [0.3, 0.4]}
        save_embeddings(self.tmp, data, model_name="test-model")
        loaded = load_embeddings(self.tmp)
        self.assertEqual(loaded, data)
        self.assertTrue((self.tmp / ".fabric" / EMBEDDINGS_FILE).exists())

    def test_load_missing(self):
        self.assertIsNone(load_embeddings(self.tmp))

    def test_load_corrupted_json(self):
        (self.tmp / EMBEDDINGS_FILE).write_text("not json", encoding="utf-8")
        self.assertIsNone(load_embeddings(self.tmp))

    def test_load_missing_items_key(self):
        (self.tmp / EMBEDDINGS_FILE).write_text(
            json.dumps({"version": 1}), encoding="utf-8"
        )
        self.assertIsNone(load_embeddings(self.tmp))

    def test_ensure_embeddings_cached(self):
        items = [_make_item("x.1", body="hello")]
        with patch("textstrata.embeddings._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = [FakeArray([0.5, 0.6])]
            mock_get_model.return_value = mock_model

            result1 = ensure_embeddings(items, self.tmp)
            self.assertEqual(result1, {"x.1": [0.5, 0.6]})

            mock_model.encode.reset_mock()
            result2 = ensure_embeddings(items, self.tmp)
            self.assertEqual(result2, {"x.1": [0.5, 0.6]})
            mock_model.encode.assert_not_called()

    def test_ensure_embeddings_stale(self):
        save_embeddings(self.tmp, {"old.id": [0.1, 0.2]})

        items = [_make_item("new.id", body="new")]
        with patch("textstrata.embeddings._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = [FakeArray([0.7, 0.8])]
            mock_get_model.return_value = mock_model

            result = ensure_embeddings(items, self.tmp)
            self.assertEqual(result, {"new.id": [0.7, 0.8]})

            loaded = load_embeddings(self.tmp)
            self.assertEqual(loaded, {"new.id": [0.7, 0.8]})

    def test_ensure_embeddings_empty_root(self):
        items = [_make_item("x.1", body="hello")]
        empty_tmp = Path(tempfile.mkdtemp())

        with patch("textstrata.embeddings._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = [FakeArray([0.5, 0.6])]
            mock_get_model.return_value = mock_model

            result = ensure_embeddings(items, empty_tmp)
            self.assertEqual(result, {"x.1": [0.5, 0.6]})


class TestSearchSemantic(unittest.TestCase):
    @patch("textstrata.embeddings._get_model")
    def test_search_returns_sorted_by_similarity(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.encode.return_value = [FakeArray([0.9, 0.1])]
        mock_get_model.return_value = mock_model

        embeddings = {
            "close": [0.8, 0.2],
            "far": [0.1, 0.9],
            "middle": [0.5, 0.5],
        }
        results = search_semantic("test query", embeddings, top_k=3)
        self.assertEqual(results[0][0], "close")
        self.assertEqual(len(results), 3)

    @patch("textstrata.embeddings._get_model")
    def test_search_top_k(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.encode.return_value = [FakeArray([1.0, 0.0])]
        mock_get_model.return_value = mock_model

        embeddings = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [0.5, 0.5]}
        results = search_semantic("q", embeddings, top_k=2)
        self.assertEqual(len(results), 2)

    @patch("textstrata.embeddings._get_model")
    def test_search_empty_embeddings(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.encode.return_value = [FakeArray([1.0, 0.0])]
        mock_get_model.return_value = mock_model

        results = search_semantic("q", {}, top_k=5)
        self.assertEqual(results, [])

    @patch("textstrata.embeddings._get_model")
    def test_scores_are_floats(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.encode.return_value = [FakeArray([1.0, 0.0])]
        mock_get_model.return_value = mock_model

        results = search_semantic("q", {"a": [1.0, 0.0]}, top_k=1)
        item_id, score = results[0]
        self.assertEqual(item_id, "a")
        self.assertAlmostEqual(score, 1.0)


if __name__ == "__main__":
    unittest.main()
