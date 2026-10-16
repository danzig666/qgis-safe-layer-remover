"""Group layers (QgsGroupLayer): child layers are required by the group layer."""

from __future__ import annotations

from ..model import Certainty, DepKind, Severity
from ..texts import tr
from .base import BaseDetector

try:
    from qgis.core import QgsGroupLayer
except ImportError:  # pragma: no cover - QGIS < 3.24
    QgsGroupLayer = None


class GroupLayerDetector(BaseDetector):
    name = "group_layers"

    def detect_layer(self, layer, project, ctx):
        if QgsGroupLayer is None or not isinstance(layer, QgsGroupLayer):
            return []
        out = []
        for child in layer.childLayers():
            if child is None or child.id() not in ctx.layers_by_id or child.id() == layer.id():
                continue
            out.append(self.dep(child.id(), layer, DepKind.GROUP_LAYER, Severity.BLOCK, Certainty.HIGH,
                                tr("csoportréteg tagja")))
        return out
