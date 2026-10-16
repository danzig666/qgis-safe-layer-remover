"""Vector joins: the join target layer is required by the layer that joins it."""

from __future__ import annotations

import re

from qgis.core import QgsVectorLayer

from ..model import Certainty, DepKind, Severity
from ..texts import tr
from .base import BaseDetector


class JoinDetector(BaseDetector):
    name = "joins"

    def detect_layer(self, layer, project, ctx):
        if not isinstance(layer, QgsVectorLayer):
            return []
        out = []
        aux = None
        try:
            aux = layer.auxiliaryLayer()
        except Exception:  # noqa: BLE001
            aux = None
        aux_id = aux.id() if aux is not None else None
        for join in layer.vectorJoins():
            target = join.joinLayerId()
            if aux_id and target == aux_id:
                continue  # internal join of the auxiliary storage, not a project layer
            if target not in ctx.layers_by_id:
                if target:
                    out.append(self.broken_ref(layer, target, tr("join")))
                continue
            prefix = join.prefix() or ""
            detail = tr("{t} = {j}").format(t=join.targetFieldName(), j=join.joinFieldName())
            if prefix:
                detail += tr(", előtag: {p}").format(p=prefix)
            if self._joined_fields_used(layer, join, ctx):
                detail += tr("; a csatolt mezőket a címkézés/szimbológia is használja")
            out.append(self.dep(target, layer, DepKind.JOIN, Severity.BLOCK, Certainty.HIGH, detail))
        return out

    @staticmethod
    def _joined_fields_used(layer, join, ctx) -> bool:
        """Best effort enrichment: are the joined field names referenced in the layer's style?"""
        try:
            target = ctx.layers_by_id.get(join.joinLayerId())
            if target is None:
                return False
            prefix = join.prefix() or f"{target.name()}_"
            subset = join.joinFieldNamesSubset()
            names = list(subset) if subset else [f.name() for f in target.fields()]
            names = [prefix + n for n in names if n != join.joinFieldName()]
            if not names:
                return False
            xml = ctx.layer_xml(layer)
            # only look at the symbology and labeling sections
            sections = re.findall(r"<renderer-v2\b.*?</renderer-v2>|<labeling\b.*?</labeling>", xml, flags=re.S)
            text = "\n".join(sections)
            if not text:
                return False
            for n in names:
                if re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", text):
                    return True
        except Exception:  # noqa: BLE001
            return False
        return False
