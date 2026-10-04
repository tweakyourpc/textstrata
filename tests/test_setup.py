from __future__ import annotations

import re
import json
import tempfile
import unittest
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from textstrata.__main__ import build_parser, cmd_restart, main
from textstrata.application.setup import initialize_workspace, setup_status
from textstrata.presentation import PAPER_SKIN, render_setup_html
from textstrata.workspace import load_installation_config, resolve_workspace


class SetupUseCaseTests(unittest.TestCase):
    def test_workspace_alias_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(resolve_workspace(environ={"TEXTSTRATA_WORKSPACE": str(root / "textstrata")}), (root / "textstrata").resolve())
            self.assertEqual(resolve_workspace(environ={"FABRIC_ROOT": str(root / "legacy")}), (root / "legacy").resolve())
            self.assertEqual(resolve_workspace(environ={"FABRIC_ROOT": str(root / "legacy"), "MARKBASE_WORKSPACE": str(root / "canonical")}), (root / "canonical").resolve())

    def test_status_is_read_only_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = set(root.iterdir())
            first = setup_status(root)
            second = setup_status(root)
            self.assertFalse(first["initialized"])
            self.assertEqual(first, second)
            self.assertEqual(set(root.iterdir()), before)
            self.assertEqual([item["id"] for item in first["optional_capabilities"]], sorted(item["id"] for item in first["optional_capabilities"]))

    def test_initialize_is_idempotent_and_creates_no_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = initialize_workspace(root)
            second = initialize_workspace(root)
            self.assertTrue(first["initialized"])
            self.assertTrue(second["initialized"])
            self.assertTrue(first["created"])
            self.assertEqual(second["created"], [])
            self.assertEqual(list((root / "normalized").glob("*.md")), [])

    def test_setup_page_has_stable_contract_and_unique_ids(self):
        html = render_setup_html(setup_status(tempfile.mkdtemp()), PAPER_SKIN, version="test")
        self.assertIn("Setup &amp; capabilities", html)
        self.assertIn("/api/textstrata/setup/initialize", html)
        ids = re.findall(r'id="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))

    def test_cli_setup_and_config_check_use_selected_config_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            config = root / "settings" / "installation.json"
            workspace = root / "vault"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--config", str(config), "setup", "--non-interactive", "--storage", str(workspace), "--port", "7543"]), 0)
                self.assertEqual(main(["--config", str(config), "config", "check"]), 0)
            self.assertEqual(load_installation_config(path=config)["workspace"], str(workspace))
            self.assertTrue((workspace / "normalized").is_dir())

    def test_installation_and_google_source_configs_remain_independent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            workspace = root / "vault"
            installation = root / "installation.json"
            source = root / "sources.yaml"
            installation.write_text(json.dumps({
                "schema_version": 1, "workspace": str(workspace),
                "network": {"mode": "local", "host": "127.0.0.1", "port": 7543},
            }), encoding="utf-8")
            source.write_text("google_bridge: {}\n", encoding="utf-8")
            args = build_parser().parse_args([
                "--config", str(installation), "mirror", "google-bridge", "--config", str(source),
            ])
            self.assertEqual(args.installation_config, str(installation))
            self.assertEqual(args.source_config, str(source))
            source_only = build_parser().parse_args(["mirror", "google-bridge", "--config", str(source)])
            self.assertIsNone(source_only.installation_config)
            self.assertEqual(source_only.source_config, str(source))
            with patch("textstrata.__main__.cmd_bridge", return_value=0) as bridge:
                self.assertEqual(main([
                    "--config", str(installation), "mirror", "google-bridge", "--config", str(source),
                ]), 0)
            bridge.assert_called_once_with("mirror", dry_run=False, config_path=str(source))
            self.assertEqual(os.environ["TEXTSTRATA_CONFIG"], str(installation))

    def test_cli_setup_rejects_lan_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            config = root / "installation.json"
            with self.assertRaises(ValueError):
                main(["--config", str(config), "setup", "--non-interactive", "--storage", str(root / "vault"), "--host", "0.0.0.0"])
            self.assertFalse(config.exists())
            self.assertFalse((root / "vault").exists())

    def test_cli_setup_repairs_invalid_config_only_with_explicit_storage(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            config = root / "installation.json"
            config.write_text("not JSON", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pass --storage"):
                main(["--config", str(config), "setup", "--non-interactive"])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--config", str(config), "setup", "--non-interactive", "--storage", str(root / "vault")]), 0)
            self.assertEqual(load_installation_config(path=config)["workspace"], str(root / "vault"))

    def test_restart_rejects_unsafe_overrides_before_touching_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid_path = root / ".fabric" / "server.pid"
            pid_path.parent.mkdir()
            pid_path.write_text("12345", encoding="utf-8")
            for host, port in (("0.0.0.0", 8765), ("127.0.0.1", 0)):
                with self.subTest(host=host, port=port), self.assertRaises(ValueError):
                    cmd_restart({"mode": "local", "host": host, "port": port}, root, root / "state")
            self.assertEqual(pid_path.read_text(encoding="utf-8"), "12345")


if __name__ == "__main__":
    unittest.main()
