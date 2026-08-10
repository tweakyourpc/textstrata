#!/usr/bin/env python3
"""Disposable backup, restore, and upgrade smoke check for TextStrata."""
from pathlib import Path
import shutil
import tempfile
from textstrata.control import select_backup_files, verify_backup_manifest
from textstrata.ingest import ingest_text
from textstrata.store import TextStrataStore
from textstrata.catalog import Catalog

def main():
    with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as restored:
        store = TextStrataStore(source)
        store.ensure_dirs()
        result = ingest_text(store, "---\\nid: smoke.backup\\ntitle: Backup Smoke\\ntype: note\\ntags: [smoke]\\n---\\n\\n# Backup Smoke\\n")
        assert result.published
        config = {"backup": {"enabled": True, "roots": ["normalized"], "include_untagged": True}}
        entries = select_backup_files(source, config)
        manifest = [entry.__dict__ for entry in entries]
        assert verify_backup_manifest(source, manifest)["ok"]

        archive = Path(tempfile.mkdtemp()) / "workspace"
        shutil.copytree(source, archive)
        assert verify_backup_manifest(archive, manifest)["ok"]
        restored_root = Path(restored) / "workspace"
        shutil.copytree(archive, restored_root, dirs_exist_ok=True)
        restored_store = TextStrataStore(restored_root)
        restored_store.ensure_dirs()
        catalog = Catalog(restored_root)
        assert catalog.rescan(restored_store) == 1
        catalog.close()
        assert verify_backup_manifest(restored_root, manifest)["ok"]

        # An upgrade must preserve the authoritative Markdown and rebuild the
        # disposable catalog. This models a version transition without touching
        # a user's real workspace.
        upgraded = Path(restored) / "upgraded"
        shutil.copytree(restored_root, upgraded)
        upgraded_store = TextStrataStore(upgraded)
        upgraded_store.ensure_dirs()
        catalog = Catalog(upgraded)
        assert catalog.rescan(upgraded_store) == len(manifest)
        assert catalog.count() == len(manifest)
        catalog.close()
    print("phase5 backup/restore/upgrade smoke: ok")

if __name__ == "__main__":
    main()
