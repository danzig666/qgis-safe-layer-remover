"""Personal layer locks: layers the user marked as not removable, stored in the project file."""

from __future__ import annotations

from typing import Iterable, Optional

from qgis.core import QgsProject

SCOPE = "SafeLayerRemover"
KEY = "lockedLayers"


def locked_ids(project: Optional[QgsProject] = None) -> set[str]:
    project = project or QgsProject.instance()
    values, _ok = project.readListEntry(SCOPE, KEY)
    return {v for v in (values or []) if v}


def is_user_locked(layer_id: str, project: Optional[QgsProject] = None) -> bool:
    return layer_id in locked_ids(project)


def set_locked(layer_ids: Iterable[str], locked: bool, project: Optional[QgsProject] = None) -> set[str]:
    """Lock or unlock layers. Ids of layers no longer in the project are dropped. Returns the new set."""
    project = project or QgsProject.instance()
    current = locked_ids(project)
    ids = set(layer_ids)
    new = (current | ids) if locked else (current - ids)
    existing = set(project.mapLayers().keys())
    new &= existing
    if new != current:
        if new:
            project.writeEntry(SCOPE, KEY, sorted(new))
        else:
            project.removeEntry(SCOPE, KEY)
    return new
