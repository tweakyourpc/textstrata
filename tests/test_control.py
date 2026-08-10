from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textstrata.control import backup_workspace, load_config, load_effective_config, process_approved_ingest, select_backup_files, verify_backup_manifest
from textstrata.ingest import ingest_text
from textstrata.store import TextStrataStore


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = TextStrataStore(self.root)
        self.store.ensure_dirs()

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_selects_markdown_by_tags_and_writes_manifest(self):
        ingest_text(self.store, "---\nid: keep.note\ntitle: Keep\ntype: note\ntags: [backup]\n---\nKeep")
        ingest_text(self.store, "---\nid: private.note\ntitle: Private\ntype: note\ntags: [private]\n---\nPrivate")
        config = {"backup": {"enabled": True, "target": "gdrive:test", "roots": ["normalized"], "exclude_tags": ["private"], "include_tags": [], "include_untagged": True}}
        entries = select_backup_files(self.root, config)
        self.assertEqual([entry.relative_path for entry in entries], ["normalized/keep.note.md"])
        with patch("textstrata.control.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            result = backup_workspace(self.root, config=config)
        self.assertEqual(result["files"], 1)
        self.assertTrue((self.root / ".fabric/control-state/backup-manifest.json").exists())
        self.assertEqual(run.call_args.args[0][0], "rclone")

    def test_backup_dry_run_does_not_write_state(self):
        config = {"backup": {"enabled": True, "target": "gdrive:test", "roots": ["normalized"]}}
        result = backup_workspace(self.root, config=config, dry_run=True)
        self.assertTrue(result["actions"][0]["dry_run"] if result["actions"] else True)
        self.assertFalse((self.root / ".fabric/control-state/backup-manifest.json").exists())

    def test_backup_manifest_verification_reports_missing_and_changed_files(self):
        ingest_text(self.store, "---\nid: verify.note\ntitle: Verify\ntype: note\n---\nOriginal")
        entries = select_backup_files(self.root, {"backup": {"roots": ["normalized"], "include_untagged": True}})
        manifest = [entry.__dict__ for entry in entries]
        self.assertTrue(verify_backup_manifest(self.root, manifest)["ok"])
        path = self.root / "normalized/verify.note.md"
        path.write_text(path.read_text(encoding="utf-8") + "Changed\n", encoding="utf-8")
        result = verify_backup_manifest(self.root, manifest)
        self.assertFalse(result["ok"])
        self.assertEqual(result["changed"], ["normalized/verify.note.md"])
        path.unlink()
        result = verify_backup_manifest(self.root, manifest)
        self.assertEqual(result["missing"], ["normalized/verify.note.md"])

    def test_approved_ingest_skips_unapproved_and_processed_items(self):
        queue = {"items": [{"id": "approved-1", "status": "approved", "type": "url", "payload": "https://example.test"}, {"id": "pending-1", "status": "pending", "type": "url", "payload": "https://pending.test"}]}
        config = {"textstrata_url": "http://textstrata.test", "ingest": {"enabled": True, "remote_queue": "gdrive:queue.json"}}

        def rclone(args, **_kwargs):
            Path(args[2]).write_text(json.dumps(queue), encoding="utf-8")
            return {"returncode": 0}

        responses = iter([{"job_id": 7}, {"jobs": [{"id": 7, "status": "completed", "result_path": "normalized/approved.md"}]}])
        with patch("textstrata.control._run_rclone", side_effect=rclone), patch("textstrata.control._http_json", side_effect=lambda *args, **kwargs: next(responses)):
            result = process_approved_ingest(self.root, config=config)
        self.assertEqual([entry["id"] for entry in result["processed"]], ["approved-1"])
        self.assertEqual(result["skipped"], ["pending-1"])
        state = json.loads((self.root / ".fabric/control-state/processed-ingest.json").read_text(encoding="utf-8"))
        self.assertEqual(state["approved-1"]["status"], "completed")

    def test_default_config_keeps_control_optional(self):
        config, path = load_config(self.root)
        self.assertFalse(config["backup"]["enabled"])
        self.assertFalse(config["ingest"]["enabled"])
        self.assertEqual(path, self.root / ".fabric/control.json")

    def test_remote_config_is_explicitly_pulled_for_mutating_actions(self):
        local = {"control": {"remote_config": "gdrive:control.json"}}
        remote = {"backup": {"enabled": False}, "ingest": {"enabled": False}}
        (self.root / ".fabric/control.json").write_text(json.dumps(local), encoding="utf-8")

        def rclone(args, **_kwargs):
            Path(args[2]).write_text(json.dumps(remote), encoding="utf-8")
            return {"returncode": 0}

        with patch("textstrata.control._run_rclone", side_effect=rclone):
            config, selected = load_effective_config(self.root)
        self.assertFalse(config["backup"]["enabled"])
        self.assertTrue(str(selected).endswith("remote-control.json"))


if __name__ == "__main__":
    unittest.main()
