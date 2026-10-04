"""A malformed structured header must not become an unrelated fallback item."""

import tempfile
import unittest

from textstrata.ingest import ingest_text
from textstrata.store import TextStrataStore


MALFORMED = """---
id: current-state
type: reference
title: Current State
context:
  projects: [sample]
  tier: 1
  status: current
  summary: Recent changes: local bootstrap and review
provenance:
  origin: ai
  contributor_chain: via_ai
  ai_vendor: Test Vendor
  ai_model: synthetic-model
---

# Current State

The body must not be published under a fallback ID.
"""


class MalformedFrontmatterTests(unittest.TestCase):
    def test_nested_invalid_yaml_is_rejected_without_fallback_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            result = ingest_text(store, MALFORMED, fallback_id="uploaded-file")
            self.assertFalse(result.published)
            self.assertIn("front-matter", " ".join(result.validation.errors))
            self.assertFalse(store.normalized_paths())


if __name__ == "__main__":
    unittest.main()
