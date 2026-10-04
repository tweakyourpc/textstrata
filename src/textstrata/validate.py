"""Validation gate. An item is published to the normalized store only after
it passes here, so the textstrata never exposes a contradictory or unusable item
to agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    CONTRADICTORY_POLICY,
    ContentType,
    TextStrataItem,
    is_valid_id,
    list_value_comparison_key,
)

_PROTECTED_DIAGNOSTIC_KEYS = frozenset(
    {"id", "type", "title", "handling", "preservation", "dependencies", "related"}
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate(item: TextStrataItem, diagnostics=None) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    for diagnostic in diagnostics or ():
        key = str(diagnostic).partition(":")[0].strip()
        message = f"front-matter conflict: {diagnostic}"
        if key in _PROTECTED_DIAGNOSTIC_KEYS:
            errors.append(message)
        else:
            warnings.append(message)

    if not item.id:
        errors.append("item has no id")
    elif not is_valid_id(item.id):
        errors.append(
            f"id {item.id!r} is not a stable slug "
            "(lowercase alphanumerics separated by . _ or -)"
        )

    if not item.title.strip():
        errors.append("item has no title")

    if not isinstance(item.type, ContentType):
        errors.append(f"unknown content type: {item.type!r}")

    if (item.handling, item.preservation) in CONTRADICTORY_POLICY:
        errors.append(
            f"policy conflict: handling={item.handling.value} forbids "
            f"preservation={item.preservation.value}"
        )

    if "context" in item.extra:
        from .context import context_errors

        errors.extend(context_errors(item))

    def duplicate_values(field_name: str, values: list[str]) -> list[str]:
        seen: set[object] = set()
        duplicates: list[str] = []
        for value in values:
            key = list_value_comparison_key(field_name, value)
            if key in seen and value not in duplicates:
                duplicates.append(value)
            seen.add(key)
        return duplicates

    for label, values in (
        ("tags", item.tags),
        ("aliases", item.aliases),
        ("related", item.related),
        ("dependencies", item.dependencies),
    ):
        duplicates = duplicate_values(label, values)
        if duplicates:
            errors.append(f"item has duplicate {label}: {', '.join(repr(value) for value in duplicates)}")

    # Structural hygiene that agents rely on but that should not block
    # publication on its own.
    if not item.tags:
        warnings.append("item has no tags; cross-linking and retrieval will be weaker")
    for dep in item.dependencies:
        if dep == item.id:
            warnings.append("item lists itself as a dependency")

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)
