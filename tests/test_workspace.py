from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from textstrata.workspace import apply_config_environment, load_cascading_config, resolve_workspace


class WorkspaceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="textstrata-workspace-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_precedence_cli_then_environment_then_cwd_fallback(self):
        env_root = self.root / "env"
        cli_root = self.root / "cli"
        self.assertEqual(
            resolve_workspace(cli_root, environ={"MARKBASE_WORKSPACE": str(env_root)}),
            cli_root.resolve(),
        )
        self.assertEqual(
            resolve_workspace(environ={"MARKBASE_WORKSPACE": str(env_root)}),
            env_root.resolve(),
        )
        self.assertEqual(
            resolve_workspace(environ={"TEXTSTRATA_WORKSPACE": str(self.root / "textstrata"), "MARKBASE_WORKSPACE": str(env_root)}),
            (self.root / "textstrata").resolve(),
        )
        self.assertEqual(
            resolve_workspace(environ={}, cwd=self.root),
            (self.root / ".workspace").resolve(),
        )

    def test_workspace_config_recursively_overrides_global(self):
        global_path = self.root / "global.toml"
        global_path.write_text(
            '[network]\nhost = "127.0.0.1"\nport = 7000\n[llm]\nendpoint = "http://global"\n[vocabulary.synonyms]\ndb = "database"\n',
            encoding="utf-8",
        )
        metadata = self.root / "workspace" / ".fabric"
        metadata.mkdir(parents=True)
        (metadata / "config.toml").write_text(
            '[network]\nport = 9000\n[llm]\nmodel = "local"\n', encoding="utf-8"
        )
        (metadata / "synonyms.json").write_text(
            json.dumps({"kb": "knowledge-base"}), encoding="utf-8"
        )
        config = load_cascading_config(metadata.parent, global_path=global_path)
        self.assertEqual(config["network"], {"host": "127.0.0.1", "port": 9000})
        self.assertEqual(config["llm"], {"endpoint": "http://global", "model": "local"})
        self.assertEqual(config["vocabulary"]["synonyms"]["kb"], "knowledge-base")
        self.assertEqual(config["vocabulary"]["synonyms"]["db"], "database")

    def test_config_environment_preserves_explicit_environment(self):
        with patch.dict("os.environ", {"FABRIC_PORT": "7777"}, clear=True):
            apply_config_environment({
                "network": {"host": "0.0.0.0", "port": 9000},
                "llm": {"endpoint": "http://workspace", "model": "local"},
            })
            import os

            self.assertEqual(os.environ["FABRIC_PORT"], "7777")
            self.assertEqual(os.environ["FABRIC_HOST"], "0.0.0.0")
            self.assertEqual(os.environ["OLLAMA_HOST"], "http://workspace")
            self.assertEqual(os.environ["FABRIC_LLM_MODEL"], "local")
