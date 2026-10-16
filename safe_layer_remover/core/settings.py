"""Plugin settings stored with QgsSettings under the SafeLayerRemover/ prefix."""

from __future__ import annotations

from qgis.core import QgsSettings

PREFIX = "SafeLayerRemover/"

DEFAULTS: dict[str, bool] = {
    "block_on_atlas": False,
    "block_on_layout": False,
    "uncertain_is_blocked": True,
    "use_xml_scan": True,
    "recycle_bin_enabled": True,
    "memory_save_default": True,
    "remove_empty_groups": False,
    "sync_selection": True,
    "show_info_items": False,
    "show_tree_indicators": True,
    "sentinel_mode": True,
    "undo_all_removals": False,
}

INT_DEFAULTS: dict[str, int] = {
    "undo_max_entries": 50,              # recycle bin size, the oldest entries are dropped
    "undo_memory_feature_limit": 200000,  # bigger memory layers keep only their definition (automatic undo)
}


def _to_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load() -> dict:
    s = QgsSettings()
    return {k: _to_bool(s.value(PREFIX + k, v), v) for k, v in DEFAULTS.items()}


def save(values: dict) -> None:
    s = QgsSettings()
    for k in DEFAULTS:
        if k in values:
            s.setValue(PREFIX + k, bool(values[k]))


def get_int(key: str) -> int:
    default = INT_DEFAULTS[key]
    try:
        return int(QgsSettings().value(PREFIX + key, default))
    except (TypeError, ValueError):
        return default


def set_int(key: str, value: int) -> None:
    QgsSettings().setValue(PREFIX + key, int(value))


def get(key: str) -> bool:
    return load().get(key, DEFAULTS.get(key, False))


def get_str(key: str, default: str = "") -> str:
    value = QgsSettings().value(PREFIX + key, default)
    return str(value) if value is not None else default


def set_str(key: str, value: str) -> None:
    QgsSettings().setValue(PREFIX + key, value)
