"""Deterministic YAML front-matter parsing with stacked-block merging.

Many real files (including this project's own architecture note) carry more
than one leading ``---`` block — e.g. a provenance block emitted by one tool
stacked above a semantic block written by another. Naive parsers read only the
first block and drop the rest into the rendered body, silently losing exactly
the metadata a machine-first system depends on.

The merge rule is fixed and documented so ingestion is reproducible:

* Blocks are processed in document order.
* List-valued keys (e.g. ``tags``) are unioned, preserving first-seen order.
* Scalar keys take the **first** non-empty value; a differing later value is
  recorded as a conflict rather than silently overwriting.
* Mapping-valued keys are merged shallowly under the same first-wins rule.

"First declaration wins, later blocks may only add" means an upstream tool
cannot quietly change an item's identity by appending a second block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

_FENCE = "---"
_LEADING_BLOCK_RE = re.compile(
    r"\A(?:\ufeff)?[ \t]*\n?"  # optional BOM / leading blank
    r"(?:---[ \t]*\n(?P<block>.*?)\n---[ \t]*\n?)",
    re.DOTALL,
)


@dataclass
class MergedFrontmatter:
    data: dict = field(default_factory=dict)
    body: str = ""
    block_count: int = 0
    conflicts: list[str] = field(default_factory=list)

    @property
    def had_stacked_blocks(self) -> bool:
        return self.block_count > 1


def _split_leading_blocks(text: str) -> tuple[list[str], str]:
    """Peel every consecutive leading ``---`` block off the top of ``text``.

    Returns the raw YAML strings (in order) and the remaining body.
    """
    blocks: list[str] = []
    rest = text
    while True:
        match = _LEADING_BLOCK_RE.match(rest)
        if not match:
            break
        blocks.append(match.group("block"))
        rest = rest[match.end():]
    return blocks, rest


_SIMPLE_KV_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:[ \t]*(.*?)[ \t]*)?$")


def _salvage_block(raw: str) -> dict:
    """Best-effort recovery when a block is not strictly valid YAML.

    Real files routinely carry unquoted colons in a title
    (``title: TextStrata: A Machine-First Substrate``), which is invalid
    YAML. Rather than crash and lose the whole block, salvage top-level
    ``key: value`` lines, treating the first colon as the separator and the
    rest as a literal string. Bracketed inline lists are parsed with YAML
    per-line where possible.
    """
    data: dict = {}
    last_key: str | None = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace() and last_key is not None and data.get(last_key) not in (None, ""):
            data[last_key] = f"{data[last_key]} {line.strip()}"
            continue
        match = _SIMPLE_KV_RE.match(line)
        if not match:
            last_key = None
            continue
        key, value = match.group(1), match.group(2)
        parsed: object = value.strip() if value else None
        last_key = key
        if parsed is None:
            data[key] = None
            continue
        if value[:1] in "[{" or value in ("true", "false", "null"):
            try:
                parsed = yaml.safe_load(value)
            except yaml.YAMLError:
                parsed = value
        else:
            parsed = value.strip().strip("\"'")
        data[key] = parsed
    return data


def _coerce_block(raw: str) -> dict:
    if not raw.strip():
        return {}
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return _salvage_block(raw)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        # A non-mapping front-matter block is malformed; surface it as data so
        # nothing is silently dropped, but keep it namespaced.
        return {"_nonmapping": loaded}
    return loaded


def _merge_into(acc: dict, incoming: dict, conflicts: list[str], block_index: int) -> None:
    for key, value in incoming.items():
        if key not in acc:
            acc[key] = value
            continue
        existing = acc[key]
        if isinstance(existing, list) or isinstance(value, list):
            merged = list(existing) if isinstance(existing, list) else [existing]
            for candidate in value if isinstance(value, list) else [value]:
                if candidate not in merged:
                    merged.append(candidate)
            acc[key] = merged
        elif isinstance(existing, dict) and isinstance(value, dict):
            sub_conflicts: list[str] = []
            _merge_into(existing, value, sub_conflicts, block_index)
            conflicts.extend(f"{key}.{c}" for c in sub_conflicts)
        elif existing != value:
            conflicts.append(
                f"{key!r}: kept {existing!r} from an earlier block, "
                f"ignored {value!r} in block {block_index + 1}"
            )
    # first-wins scalars: nothing to do when equal


def parse(text: str) -> MergedFrontmatter:
    """Parse and merge all leading front-matter blocks in ``text``."""
    blocks, body = _split_leading_blocks(text)
    merged: dict = {}
    conflicts: list[str] = []
    for index, raw in enumerate(blocks):
        _merge_into(merged, _coerce_block(raw), conflicts, index)
    return MergedFrontmatter(
        data=merged,
        body=body.lstrip("\n"),
        block_count=len(blocks),
        conflicts=conflicts,
    )


def render(data: dict, body: str) -> str:
    """Re-emit a single canonical front-matter block above ``body``."""
    front = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"{_FENCE}\n{front}\n{_FENCE}\n\n{body.rstrip()}\n"
