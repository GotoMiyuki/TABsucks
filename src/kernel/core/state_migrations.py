"""Pure, sequential migrations for persisted workshop state (unversioned = v0)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

CURRENT_SCHEMA_VERSION = 1


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Return an independent current-version document; never modify the input.

    Future schemas are refused before any disk write. Add each future migration
    as a separate version step, keeping old migrations deterministic.
    """
    if not isinstance(raw, dict):
        raise ValueError("state 根必须是 dict")
    version = raw.get("SchemaVersion", 0)
    if type(version) is not int or version < 0:
        raise ValueError("SchemaVersion 必须是非负整数")
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported future SchemaVersion: {version}")
    result = deepcopy(raw)
    while version < CURRENT_SCHEMA_VERSION:
        if version == 0:
            # v0 and v1 share the tab layout; v1 makes the contract explicit.
            result["SchemaVersion"] = 1
        version += 1
    tabs = result.get("TabState", {})
    if not isinstance(tabs, dict):
        raise ValueError("TabState 必须是 dict")
    for tab in ("Tab1", "Tab2", "Tab3", "Tab4"):
        value = tabs.get(tab, {})
        if value is None:  # historical null tabs mean an empty tab
            value = tabs[tab] = {}
        if not isinstance(value, dict):
            raise ValueError(f"{tab} 必须是 dict")
        if tab in ("Tab3", "Tab4"):
            for key, item in value.items():
                if not isinstance(key, str) or not isinstance(item, dict):
                    raise ValueError(f"{tab}.{key} 必须是 dict")
    return result
