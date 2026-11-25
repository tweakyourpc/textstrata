#!/usr/bin/env python3
"""Restore original MarkBase ingest dates that a workspace migration overwrote.

Legacy MarkBase items keep their first capture time in
``extra.legacy_markbase_metadata.date_ingested``. When a workspace is copied,
the stored ``ingested_at`` can be re-derived from the new file mtime, which makes
old notes look new. This script moves such items back to their legacy date in
both the item metadata sidecar and the search catalog.

Dry run by default; pass ``--apply`` to write.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from textstrata.catalog import Catalog  # noqa: E402
from textstrata.ingest import build_item  # noqa: E402
from textstrata.store import TextStrataStore  # noqa: E402


def legacy_date(item: object) -> str | None:
    """Return the legacy MarkBase capture time recorded on an item, if any."""
    legacy = item.extra.get("legacy_markbase_metadata")  # type: ignore[attr-defined]
    if isinstance(legacy, dict) and legacy.get("date_ingested"):
        return str(legacy["date_ingested"])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workspace", type=Path, help="TextStrata workspace root")
    parser.add_argument("--apply", action="store_true", help="write the corrected dates")
    args = parser.parse_args()

    store = TextStrataStore(args.workspace)
    changes: list[tuple[str, str, str]] = []
    for path in store.normalized_paths():
        item = build_item(path.read_text(encoding="utf-8"), fallback_id=path.stem)[0]
        original = legacy_date(item)
        if not original:
            continue
        metadata = store.item_metadata(item.id)
        current = str(metadata.get("ingested_at") or "")
        if current and current > original:
            changes.append((item.id, current, original))

    for item_id, current, original in changes:
        print(f"{item_id}: {current} -> {original}")
    print(f"{len(changes)} item(s) {'updated' if args.apply else 'would change'}")
    if not args.apply or not changes:
        return 0

    catalog = Catalog(args.workspace)
    try:
        for item_id, _current, original in changes:
            with store.item_lock(item_id):
                metadata = store.item_metadata(item_id)
                metadata["ingested_at"] = original
                store._write_item_metadata(item_id, metadata)
            catalog.conn.execute(
                "UPDATE item_meta SET ingested_at = ? WHERE id = ?", (original, item_id)
            )
        catalog.conn.commit()
    finally:
        catalog.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
