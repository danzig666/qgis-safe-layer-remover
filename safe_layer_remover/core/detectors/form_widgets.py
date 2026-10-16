"""Attribute form widgets referencing other layers (ValueRelation, RelationReference)."""

from __future__ import annotations

from qgis.core import QgsVectorLayer

from ..model import Certainty, DepKind, Severity
from ..texts import tr
from .base import BaseDetector


class FormWidgetDetector(BaseDetector):
    name = "form_widgets"

    def detect_layer(self, layer, project, ctx):
        if not isinstance(layer, QgsVectorLayer):
            return []
        out = []
        fields = layer.fields()
        for idx in range(fields.count()):
            setup = layer.editorWidgetSetup(idx)
            wtype = setup.type()
            cfg = setup.config() or {}
            fname = fields.at(idx).name()
            if wtype == "ValueRelation":
                detail = tr("'{f}' mező értékreláció widgetje").format(f=fname)
                ref_id = cfg.get("Layer") or ""
                if ref_id in ctx.layers_by_id:
                    out.append(self.dep(ref_id, layer, DepKind.VALUE_RELATION, Severity.BLOCK, Certainty.HIGH, detail))
                    continue
                resolved = self._resolve_fallback(cfg, ctx)
                if resolved:
                    for lid, cert in resolved:
                        out.append(self.dep(lid, layer, DepKind.VALUE_RELATION, Severity.BLOCK, cert, detail))
                elif ref_id or cfg.get("LayerName"):
                    out.append(self.broken_ref(layer, ref_id or cfg.get("LayerName"), detail))
            elif wtype == "RelationReference":
                detail = tr("'{f}' mező kapcsolathivatkozás widgetje").format(f=fname)
                rel = project.relationManager().relation(cfg.get("Relation", "") or "")
                target = None
                if rel is not None and rel.isValid():
                    target = rel.referencedLayerId()
                if not target:
                    # newer QGIS versions may store the referenced layer directly
                    target = cfg.get("ReferencedLayerId") or ""
                if target in ctx.layers_by_id and target != layer.id():
                    out.append(self.dep(target, layer, DepKind.RELATION_REFERENCE, Severity.BLOCK, Certainty.HIGH, detail))
                elif target and target != layer.id():
                    resolved = []
                    if cfg.get("ReferencedLayerName"):
                        resolved = [(lid, Certainty.MEDIUM if c != Certainty.LOW else c)
                                    for lid, c in ctx.resolve_layer_ref(cfg.get("ReferencedLayerName"))]
                    if resolved:
                        for lid, cert in resolved:
                            out.append(self.dep(lid, layer, DepKind.RELATION_REFERENCE, Severity.BLOCK, cert, detail))
                    else:
                        out.append(self.broken_ref(layer, target, detail))
        return out

    @staticmethod
    def _resolve_fallback(cfg, ctx):
        name = cfg.get("LayerName") or ""
        source = cfg.get("LayerSource") or ""
        candidates = []
        if name:
            candidates = [lid for lid in ctx.layers_by_name.get(name.lower(), [])]
        if source:
            by_source = [lid for lid in (candidates or ctx.layers_by_id)
                         if ctx.layers_by_id[lid].source() == source]
            if by_source:
                candidates = by_source
        if len(candidates) == 1:
            return [(candidates[0], Certainty.MEDIUM)]
        return [(c, Certainty.LOW) for c in candidates]
