"""Deterministic YAML front-matter parsing with stacked-block merging.

Many real files (including this project's own architecture note) carry more
than one leading ``---`` block — e.g. a provenance block emitted by one tool
stacked above a semantic block written by another. Naive parsers read only the
first block and drop the rest into the rendered body, silently losing exactly
the metadata a machine-first system depends on.

The merge rule is fixed and documented so ingestion is reproducible:

* Blocks are processed in document order.
* Unprotected list-valued keys (e.g. ``tags``) are unioned, preserving
  first-seen order.
* Scalar keys take the **first** non-empty value; a differing later value is
  recorded as a conflict rather than silently overwriting.
* Mapping-valued keys are merged shallowly under the same first-wins rule.
* Identity and policy keys never merge across blocks unless exactly equal.

"First declaration wins, later blocks may only add" means an upstream tool
cannot quietly change an item's identity by appending a second block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

import yaml

from .models import list_value_comparison_key

_FENCE = "---"
_PROTECTED_KEYS = frozenset(
    {"id", "type", "title", "handling", "preservation", "dependencies", "related"}
)
_LEADING_BLOCK_RE = re.compile(
    r"\A(?:\ufeff)?[ \t]*\n?"  # optional BOM / leading blank
    r"(?:---[ \t]*\n(?P<block>.*?)\n---[ \t]*\n?)",
    re.DOTALL,
)
_SIMPLE_KV_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:[ \t]*(.*?)[ \t]*)?$")
_BLOCK_LIST_ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(.*?)[ \t]*$")
_MARKDOWN_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)


@dataclass
class MergedFrontmatter:
    data: dict = field(default_factory=dict)
    body: str = ""
    block_count: int = 0
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def had_stacked_blocks(self) -> bool:
        return self.block_count > 1


def _looks_like_prose(raw: str) -> bool:
    lines = [line for line in raw.splitlines() if line.strip()]
    key_lines = sum(bool(_SIMPLE_KV_RE.match(line)) for line in lines)
    structurally_prose = len(lines) - key_lines > key_lines
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return structurally_prose
    if isinstance(loaded, dict):
        return False
    if _MARKDOWN_HEADING_RE.search(raw):
        return True
    return structurally_prose


def _split_leading_blocks(text: str) -> tuple[list[str], str, list[str], list[str]]:
    """Peel every consecutive leading ``---`` block off the top of ``text``.

    Returns accepted raw YAML strings, the remaining body, and parser warnings.
    """
    blocks: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    rest = text
    while True:
        match = _LEADING_BLOCK_RE.match(rest)
        if not match:
            break
        candidate = match.group("block")
        if _looks_like_prose(candidate):
            declared = {
                match.group(1)
                for line in candidate.splitlines()
                if line and not line[0].isspace()
                if (match := _SIMPLE_KV_RE.match(line))
            }
            if "id" in declared and ("type" in declared or "title" in declared):
                errors.append(
                    f"malformed structured front-matter candidate block {len(blocks) + 1}; quote colon-containing values"
                )
            warnings.append(
                f"rejected front-matter candidate block {len(blocks) + 1}: "
                "parsed as prose and was preserved as body"
            )
            break
        blocks.append(candidate)
        rest = rest[match.end():]
    return blocks, rest, warnings, errors


def _literal_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
        return stripped[1:-1]
    return stripped


def _coerce_yaml_dates(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_coerce_yaml_dates(item) for item in value]
    if isinstance(value, dict):
        return {
            _coerce_yaml_dates(key): _coerce_yaml_dates(item)
            for key, item in value.items()
        }
    return value


def _parse_salvaged_value(line: str, key: str, literal: str) -> object:
    try:
        loaded = yaml.safe_load(line)
    except yaml.YAMLError:
        return _literal_value(literal)
    if not isinstance(loaded, dict) or key not in loaded:
        return _literal_value(literal)
    return _coerce_yaml_dates(loaded[key])


def _parse_salvaged_list_item(line: str, literal: str) -> object:
    try:
        loaded = yaml.safe_load(line)
    except yaml.YAMLError:
        return _literal_value(literal)
    if not isinstance(loaded, list) or len(loaded) != 1:
        return _literal_value(literal)
    return _coerce_yaml_dates(loaded[0])


def _salvage_block(raw: str) -> dict:
    """Best-effort recovery when a block is not strictly valid YAML.

    Real files routinely carry unquoted colons in a title
    (``title: TextStrata: A Machine-First Substrate``), which is invalid
    YAML. Rather than crash and lose the whole block, salvage top-level
    ``key: value`` lines. Each line is parsed independently as YAML, with a
    literal fallback only for the malformed line.
    """
    data: dict = {}
    last_key: str | None = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        list_match = _BLOCK_LIST_ITEM_RE.match(line)
        if (
            list_match
            and last_key is not None
            and (data.get(last_key) is None or isinstance(data.get(last_key), list))
        ):
            item = _parse_salvaged_list_item(line, list_match.group(1))
            if data[last_key] is None:
                data[last_key] = []
            data[last_key].append(item)
            continue
        if line[:1].isspace() and last_key is not None and data.get(last_key) not in (None, ""):
            data[last_key] = f"{data[last_key]} {line.strip()}"
            continue
        match = _SIMPLE_KV_RE.match(line)
        if not match:
            last_key = None
            continue
        key, value = match.group(1), match.group(2)
        last_key = key
        if value is None or not value.strip():
            data[key] = None
            continue
        data[key] = _parse_salvaged_value(line, key, value)
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


def _conflict_message(
    key: str,
    earlier: object,
    later: object,
    earlier_block: int,
    later_block: int,
) -> str:
    return (
        f"{key}: kept {earlier!r} from block {earlier_block}, "
        f"rejected {later!r} in block {later_block}"
    )


def _establishment_message(key: str, later_block: int, anchor_block: int) -> str:
    return (
        f"{key}: cannot be established in block {later_block}; "
        f"identity fixed by block {anchor_block}"
    )


def _remember_origins(
    value: object,
    path: tuple[str, ...],
    block_number: int,
    origins: dict[tuple[str, ...], int],
) -> None:
    origins.setdefault(path, block_number)
    if isinstance(value, dict):
        for key, child in value.items():
            _remember_origins(child, (*path, str(key)), block_number, origins)


def _dedupe_list(field_name: str, values: list) -> list:
    deduped: list = []
    comparison_keys: list[object] = []
    for value in values:
        comparison_key = list_value_comparison_key(field_name, value)
        if comparison_key not in comparison_keys:
            deduped.append(value)
            comparison_keys.append(comparison_key)
    return deduped


def _merge_into(
    acc: dict,
    incoming: dict,
    conflicts: list[str],
    block_index: int,
    origins: dict[tuple[str, ...], int],
    anchor_block: int | None,
    path: tuple[str, ...] = (),
) -> None:
    later_block = block_index + 1
    for key, value in incoming.items():
        key_path = (*path, str(key))
        if key not in acc:
            if (
                not path
                and key in _PROTECTED_KEYS
                and anchor_block is not None
                and later_block > anchor_block
            ):
                conflicts.append(
                    _establishment_message(str(key), later_block, anchor_block)
                )
                continue
            if isinstance(value, list):
                value = _dedupe_list(".".join(key_path), value)
            acc[key] = value
            _remember_origins(value, key_path, later_block, origins)
            continue
        existing = acc[key]
        if type(existing) is type(value) and existing == value:
            continue
        if not path and key in _PROTECTED_KEYS:
            conflicts.append(
                _conflict_message(
                    str(key),
                    existing,
                    value,
                    origins.get(key_path, 1),
                    later_block,
                )
            )
        elif isinstance(existing, list) and isinstance(value, list):
            acc[key] = _dedupe_list(".".join(key_path), [*existing, *value])
        elif isinstance(existing, dict) and isinstance(value, dict):
            _merge_into(
                existing,
                value,
                conflicts,
                block_index,
                origins,
                anchor_block,
                key_path,
            )
        else:
            conflicts.append(
                _conflict_message(
                    ".".join(key_path),
                    existing,
                    value,
                    origins.get(key_path, 1),
                    later_block,
                )
            )


def parse(text: str) -> MergedFrontmatter:
    """Parse and merge all leading front-matter blocks in ``text``."""
    blocks, body, warnings, errors = _split_leading_blocks(text)
    merged: dict = {}
    conflicts: list[str] = []
    origins: dict[tuple[str, ...], int] = {}
    anchor_block: int | None = None
    for index, raw in enumerate(blocks):
        incoming = _coerce_block(raw)
        if anchor_block is None and any(key in _PROTECTED_KEYS for key in incoming):
            anchor_block = index + 1
        _merge_into(merged, incoming, conflicts, index, origins, anchor_block)
    return MergedFrontmatter(
        data=merged,
        body=body.lstrip("\n"),
        block_count=len(blocks),
        conflicts=conflicts,
        warnings=warnings,
        errors=errors,
    )


def render(data: dict, body: str) -> str:
    """Re-emit a single canonical front-matter block above ``body``."""
    front = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"{_FENCE}\n{front}\n{_FENCE}\n\n{body.rstrip()}\n"
