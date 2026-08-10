import os
import tempfile
import unittest

from textstrata.ingest import ingest_text
from textstrata.mcp_server import TextStrataMCP
from textstrata.store import TextStrataStore

NOTE = """---
id: note.mcp
title: MCP Notes
type: reference
tags: [mcp, rag]
---

# MCP Notes

Read via the local server.
"""


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = TextStrataStore(self.tmp)
        ingest_text(self.store, NOTE)

    def test_tool_listing_and_search(self):
        server = TextStrataMCP(self.tmp)
        tools = server.tools()
        self.assertTrue(any(tool["name"] == "search_knowledge" for tool in tools))
        result = server.call("search_knowledge", {"query": "MCP", "limit": 20})
        text = result["content"][0]["text"]
        self.assertIn("note.mcp", text)
        server.close()

    def test_render_tool(self):
        server = TextStrataMCP(self.tmp)
        result = server.call("render_item", {"item_id": "note.mcp", "format": "text"})
        self.assertIn("MCP Notes", result["content"][0]["text"])
        server.close()

    def test_ai_ingest_requires_model_identity(self):
        server = TextStrataMCP(self.tmp)
        result = server.call("ingest_text", {"content": "# Missing identity"})
        self.assertTrue(result.get("isError"))
        self.assertIn("require ai_vendor and ai_model", result["content"][0]["text"])
        server.close()

    def test_ai_ingest_persists_exact_model_identity(self):
        server = TextStrataMCP(self.tmp)
        content = "---\nid: test.model-identity\ntitle: Model identity\n---\n\n# Model identity\n"
        result = server.call("ingest_text", {"content": content, "ai_vendor": "Anthropic", "ai_model": "claude-opus-4.1 high", "ai_operation": "authored"})
        self.assertIn("published: test.model-identity", result["content"][0]["text"])
        item = server._read_item("test.model-identity")
        self.assertEqual(item.provenance.ai_vendor, "Anthropic")
        self.assertEqual(item.provenance.ai_model, "claude-opus-4.1 high")
        self.assertEqual(item.provenance.ai_operation, "authored")
        self.assertEqual(item.provenance.authorship, "Claude Code")
        server.close()

    def test_ai_ingest_uses_server_identity_defaults(self):
        old = {key: os.environ.get(key) for key in ("MARKBASE_AI_VENDOR", "MARKBASE_AI_MODEL", "MARKBASE_AI_AUTHOR")}
        try:
            os.environ.update({"MARKBASE_AI_VENDOR": "OpenAI", "MARKBASE_AI_MODEL": "gpt-test", "MARKBASE_AI_AUTHOR": "Codex"})
            server = TextStrataMCP(self.tmp)
            result = server.call("ingest_text", {"content": "---\nid: test.defaults\ntitle: Defaults\n---\n\nBody"})
            self.assertIn("published: test.defaults", result["content"][0]["text"])
            item = server._read_item("test.defaults")
            self.assertEqual(item.provenance.authorship, "Codex")
            self.assertEqual(item.provenance.contributor_chain, "via_ai")
            self.assertEqual(item.provenance.ai_model, "gpt-test")
            server.close()
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

if __name__ == "__main__":
    unittest.main()
