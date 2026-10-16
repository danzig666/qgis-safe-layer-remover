"""Safety net: layer ids found in another layer's XML that no typed detector explained."""

from __future__ import annotations

import re

from ..model import Certainty, DepKind, Severity
from ..texts import tr
from .base import BaseDetector

# Unicode aware \w: ids generated from Hungarian layer names may contain accented letters.
# VERIFY: QGIS replaces non word characters with "_" when generating layer ids, so a
# tokenizer on \w+ yields whole ids.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class XmlIdScanDetector(BaseDetector):
    name = "xml_id_scan"

    def detect(self, project, ctx):
        if not ctx.settings.get("use_xml_scan", True):
            return []
        return super().detect(project, ctx)

    def detect_layer(self, layer, project, ctx):
        own = layer.id()
        text = ctx.layer_xml(layer)
        tokens = set(_TOKEN_RE.findall(text))
        hits = (tokens & ctx.layers_by_id.keys()) - {own}
        out = []
        for lid in sorted(hits):
            if (own, lid) in ctx.covered_pairs:
                continue
            out.append(self.dep(lid, layer, DepKind.XML_REFERENCE, Severity.BLOCK, Certainty.MEDIUM,
                                tr("a réteg beállításai hivatkoznak az azonosítóra (közelebbről nem azonosított használat)")))
        return out
