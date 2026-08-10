"""Settings and system-info use cases."""

from __future__ import annotations

from typing import Any

from ..operations import get_settings, save_settings
from ..store import TextStrataStore


def load_settings_payload(store: TextStrataStore) -> dict[str, Any]:
    return get_settings(store)


def save_settings_payload(store: TextStrataStore, payload: dict[str, Any]) -> dict[str, Any]:
    return save_settings(store, payload)


def build_system_info_payload(app: Any) -> dict[str, object]:
    return app.system_info()
