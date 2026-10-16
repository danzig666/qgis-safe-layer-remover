"""Explicit layer dependencies (Layer Properties > Dependencies)."""

from __future__ import annotations

from ..model import Certainty, DepKind, Severity
from ..texts import tr
from .base import BaseDetector


class LayerDependencyDetector(BaseDetector):
    name = "layer_dependencies"

    def detect_layer(self, layer, project, ctx):
        out = []
        if not hasattr(layer, "dependencies"):
            return out
        for dep in layer.dependencies():
            lid = dep.layerId()
            if not lid or lid == layer.id():
                continue
            if lid not in ctx.layers_by_id:
                out.append(self.broken_ref(layer, lid, tr("rétegfüggőség")))
                continue
            # dependencies coming from the provider (e.g. virtual layer sources) are
            # already reported by the dedicated detector
            if (layer.id(), lid) in ctx.covered_pairs:
                continue
            out.append(self.dep(lid, layer, DepKind.LAYER_DEPENDENCY, Severity.BLOCK, Certainty.HIGH,
                                tr("Rétegtulajdonságok > Függőségek")))
        return out
