"""The scheduled bridge must run both independent directions each pass."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "google-bridge-sync.sh"


class GoogleBridgeSyncTests(unittest.TestCase):
    def test_both_directions_run_and_failures_remain_visible(self):
        for ingest_status, mirror_status in ((0, 0), (1, 0), (0, 1), (1, 1)):
            with self.subTest(ingest_status=ingest_status, mirror_status=mirror_status):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    calls = root / "calls"
                    fake_bin = root / "textstrata"
                    fake_bin.write_text(
                        '#!/usr/bin/env bash\n'
                        'printf "%s %s\\n" "$1" "$2" >> "$CALLS"\n'
                        'if [[ "$1" == ingest ]]; then exit "$INGEST_STATUS"; fi\n'
                        'exit "$MIRROR_STATUS"\n',
                        encoding="utf-8",
                    )
                    fake_bin.chmod(0o700)
                    env_file = root / "bridge.env"
                    env_file.write_text(
                        f'TEXTSTRATA_SOURCES_CONFIG={root / "sources.yaml"}\n'
                        'TEXTSTRATA_GOOGLE_BRIDGE_URL=https://example.invalid/exec\n'
                        'TEXTSTRATA_GOOGLE_BRIDGE_SECRET=synthetic-secret\n'
                        f'TEXTSTRATA_WORKSPACE={root / "workspace"}\n'
                        f'TEXTSTRATA_STATE_DIR={root / "state"}\n'
                        f'TEXTSTRATA_BIN={fake_bin}\n',
                        encoding="utf-8",
                    )
                    env = {**os.environ, "TEXTSTRATA_BRIDGE_ENV_FILE": str(env_file), "CALLS": str(calls), "INGEST_STATUS": str(ingest_status), "MIRROR_STATUS": str(mirror_status)}
                    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=False)
                    self.assertEqual(calls.read_text(encoding="utf-8"), "ingest google-bridge\nmirror google-bridge\n")
                    self.assertEqual(result.returncode, int(bool(ingest_status or mirror_status)))
                    if ingest_status or mirror_status:
                        self.assertIn(f"ingest_status={ingest_status} mirror_status={mirror_status}", result.stderr)


if __name__ == "__main__":
    unittest.main()
