"""Side effects that belong to the removed layer itself (memory data loss, unsaved edits ...)."""

from __future__ import annotations

from qgis.core import QgsVectorLayer

from ..model import Certainty, DepKind, Severity
from ..texts import tr
from .base import BaseDetector


class SelfEffectDetector(BaseDetector):
    name = "self_effects"

    def detect_layer(self, layer, project, ctx):
        out = []
        lid = layer.id()
        name = layer.name()
        if layer.providerType() == "memory":
            out.append(self.dep(lid, None, DepKind.MEMORY_DATA_LOSS, Severity.WARN, Certainty.HIGH,
                                tr("a memóriaréteg adatai véglegesen elvesznek (felajánljuk a mentést)"), label=name))
        if isinstance(layer, QgsVectorLayer):
            # unsaved edits are added live by the analyzer (they change with every edit)
            try:
                if layer.auxiliaryLayer() is not None:
                    out.append(self.dep(lid, None, DepKind.AUXILIARY_STORAGE, Severity.INFO, Certainty.HIGH,
                                        tr("a kiegészítő tároló adatai (pl. címkepozíciók) elvesznek"), label=name))
            except Exception:  # noqa: BLE001
                pass
        return out
