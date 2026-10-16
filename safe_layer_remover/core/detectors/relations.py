"""Project relations (both directions) and polymorphic relations."""

from __future__ import annotations

from ..model import Certainty, DepKind, Severity
from ..texts import tr
from .base import BaseDetector


class RelationDetector(BaseDetector):
    name = "relations"

    def detect(self, project, ctx):
        out = []
        rm = project.relationManager()
        for rel_id, rel in rm.relations().items():
            child_id = rel.referencingLayerId()
            parent_id = rel.referencedLayerId()
            child = ctx.layers_by_id.get(child_id)
            parent = ctx.layers_by_id.get(parent_id)
            if child is None or parent is None or child_id == parent_id:
                continue
            name = rel.name() or rel_id
            # removing the parent: the child's relation reference widget and the relation disappear
            out.append(self.dep(parent_id, child, DepKind.RELATION, Severity.BLOCK, Certainty.HIGH,
                                tr("kapcsolat: {n}, ez a réteg a szülő").format(n=name)))
            # removing the child: the relation editor on the parent's form disappears
            out.append(self.dep(child_id, parent, DepKind.RELATION, Severity.BLOCK, Certainty.HIGH,
                                tr("kapcsolat: {n}, ez a réteg a gyermek").format(n=name)))

        # VERIFY: polymorphicRelations() exists since QGIS 3.18
        if hasattr(rm, "polymorphicRelations"):
            for prel_id, prel in rm.polymorphicRelations().items():
                child_id = prel.referencingLayerId()
                child = ctx.layers_by_id.get(child_id)
                if child is None:
                    continue
                name = prel.name() or prel_id
                for parent_id in prel.referencedLayerIds():
                    parent = ctx.layers_by_id.get(parent_id)
                    if parent is None or parent_id == child_id:
                        continue
                    out.append(self.dep(parent_id, child, DepKind.POLYMORPHIC_RELATION, Severity.BLOCK,
                                        Certainty.HIGH, tr("polimorf kapcsolat: {n}, ez a réteg a szülő").format(n=name)))
                    out.append(self.dep(child_id, parent, DepKind.POLYMORPHIC_RELATION, Severity.BLOCK,
                                        Certainty.HIGH, tr("polimorf kapcsolat: {n}, ez a réteg a gyermek").format(n=name)))
        return out
