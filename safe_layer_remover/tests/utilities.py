"""Shared test helpers. Importing this module starts a QgsApplication once."""

from __future__ import annotations

import os
import sys

# make "safe_layer_remover" importable when running from the tests folder
_PLUGIN_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PLUGIN_PARENT not in sys.path:
    sys.path.insert(0, _PLUGIN_PARENT)

# the tests assert on the Hungarian texts; test_i18n switches the language explicitly
os.environ.setdefault("SAFE_LAYER_REMOVER_LANG", "hu")

from qgis.testing import start_app  # noqa: E402

QGISAPP = start_app()

from qgis.core import QgsField, QgsProject, QgsVectorLayer  # noqa: E402

from safe_layer_remover.core.analyzer import DependencyAnalyzer  # noqa: E402
from safe_layer_remover.core.settings import DEFAULTS  # noqa: E402


def string_field(name: str) -> QgsField:
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QgsField(name, QMetaType.Type.QString)
    except (ImportError, AttributeError, TypeError):
        from qgis.PyQt.QtCore import QVariant
        return QgsField(name, QVariant.String)


def new_project() -> QgsProject:
    project = QgsProject.instance()
    project.clear()
    return project


def mk(name: str, geom: str = "Point", add: bool = True) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{geom}?crs=EPSG:23700&field=id:integer&field=hrsz:string", name, "memory")
    assert layer.isValid()
    if add:
        QgsProject.instance().addMapLayer(layer)
    return layer


def analyze(**overrides):
    settings = dict(DEFAULTS)
    settings.update(overrides)
    return DependencyAnalyzer(QgsProject.instance(), settings=settings).run()
