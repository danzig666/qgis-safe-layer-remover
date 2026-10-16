"""Selective masking: a label mask or a mask symbol layer references symbol layers of another layer."""

from __future__ import annotations

from qgis.core import QgsRenderContext, QgsVectorLayer

from ..model import Certainty, DepKind, Severity
from ..texts import tr
from .base import BaseDetector

try:
    from qgis.core import QgsMaskMarkerSymbolLayer
except ImportError:  # pragma: no cover
    QgsMaskMarkerSymbolLayer = None


def _iter_symbol_layers(symbol, depth=0):
    if symbol is None or depth > 10:
        return
    for sl in symbol.symbolLayers():
        yield sl
        sub = sl.subSymbol() if hasattr(sl, "subSymbol") else None
        if sub is not None:
            yield from _iter_symbol_layers(sub, depth + 1)


class MaskingDetector(BaseDetector):
    name = "masking"

    def detect_layer(self, layer, project, ctx):
        if not isinstance(layer, QgsVectorLayer):
            return []
        masked: dict[str, str] = {}

        # label masks. VERIFY: QgsTextFormat.mask().maskedSymbolLayers() (QGIS >= 3.12)
        labeling = layer.labeling()
        if labeling is not None:
            for pid in labeling.subProviders() or [""]:
                try:
                    settings = labeling.settings(pid)
                except Exception:  # noqa: BLE001
                    settings = None
                if settings is None:
                    continue
                mask = settings.format().mask()
                if hasattr(mask, "maskedSymbolLayers") and mask.enabled():
                    for ref in mask.maskedSymbolLayers():
                        masked.setdefault(ref.layerId(), tr("címke maszk"))

        # mask marker symbol layers. VERIFY: QgsMaskMarkerSymbolLayer.masks()
        renderer = layer.renderer()
        if renderer is not None and QgsMaskMarkerSymbolLayer is not None:
            for symbol in renderer.symbols(QgsRenderContext()):
                for sl in _iter_symbol_layers(symbol):
                    if isinstance(sl, QgsMaskMarkerSymbolLayer) and hasattr(sl, "masks"):
                        for ref in sl.masks():
                            masked.setdefault(ref.layerId(), tr("maszk szimbólumréteg"))

        out = []
        for lid, what in masked.items():
            if lid == layer.id() or lid not in ctx.layers_by_id:
                continue
            out.append(self.dep(lid, layer, DepKind.MASKING, Severity.WARN, Certainty.HIGH, what))
        return out
