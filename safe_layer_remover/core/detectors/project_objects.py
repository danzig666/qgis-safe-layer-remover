"""Non-layer project objects: layouts, atlases, map themes, snapping, terrain, extra map canvases."""

from __future__ import annotations

import re

from qgis.core import QgsReadWriteContext
from qgis.PyQt.QtXml import QDomDocument

import qgis.core as qcore

from ..model import Certainty, DepKind, Severity
from ..texts import tr
from .base import BaseDetector

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class ProjectObjectDetector(BaseDetector):
    name = "project_objects"

    def detect(self, project, ctx):
        out = []
        for part in (self._layouts, self._map_themes, self._snapping, self._terrain, self._canvases):
            try:
                out.extend(part(project, ctx))
            except Exception as exc:  # noqa: BLE001 - unattributable failure: fail-closed globally
                ctx.record_error(None, f"{self.name}.{part.__name__.strip('_')}", exc)
        return out

    # ------------------------------------------------------------------
    def _mk(self, layer_id, label, kind, severity, detail, certainty=Certainty.HIGH, ref=""):
        dep = self.dep(layer_id, None, kind, severity, certainty, detail, label=label)
        if ref:
            from dataclasses import replace
            dep = replace(dep, object_ref=ref)
        return dep

    def _layouts(self, project, ctx):
        out = []
        layout_sev = Severity.BLOCK if ctx.settings.get("block_on_layout") else Severity.WARN
        atlas_sev = Severity.BLOCK if ctx.settings.get("block_on_atlas") else Severity.WARN
        for layout in project.layoutManager().layouts():
            lname = layout.name()
            label = tr("Elrendezés: {n}").format(n=lname)
            found: list = []
            try:
                found = self._layout_items(layout, label, layout_sev, atlas_sev, project, ctx)
            except Exception as exc:  # noqa: BLE001 - fall back to the XML scan only
                from ..log import log_warning
                log_warning(f"[{self.name}] layout '{lname}' item analysis failed: {exc}")
            out.extend(found)
            # safety net: ids in the layout XML that were not explained above
            known = {d.required_id for d in found}
            for lid in self._xml_ids(layout, ctx):
                if lid not in known:
                    out.append(self._mk(lid, label, DepKind.LAYOUT, layout_sev,
                                        tr("az elrendezés beállításai hivatkoznak a rétegre"), Certainty.MEDIUM, lname))
        return out

    @staticmethod
    def _visible_in_tree(project, layer_id) -> bool:
        node = project.layerTreeRoot().findLayer(layer_id)
        return node is not None and node.isVisible()

    def _layout_items(self, layout, label, layout_sev, atlas_sev, project, ctx):
        out = []
        lname = layout.name()
        if not hasattr(layout, "items"):
            return out  # e.g. QgsReport: covered by the XML scan
        themes = project.mapThemeCollection()
        for item in layout.items():
            cls = type(item).__name__
            item_name = item.displayName() if hasattr(item, "displayName") else (item.id() if hasattr(item, "id") else cls)
            item_label = f"{label} / {item_name}"
            if isinstance(item, qcore.QgsLayoutItemMap):
                if item.keepLayerSet():
                    for lyr in item.layers():
                        if lyr is not None and lyr.id() in ctx.layers_by_id:
                            out.append(self._mk(lyr.id(), item_label, DepKind.LAYOUT, layout_sev,
                                                tr("rögzített rétegkészlet"), ref=lname))
                elif item.followVisibilityPreset() and item.followVisibilityPresetName():
                    theme = item.followVisibilityPresetName()
                    if themes.hasMapTheme(theme):
                        for lid in themes.mapThemeVisibleLayerIds(theme):
                            if lid in ctx.layers_by_id:
                                out.append(self._mk(lid, item_label, DepKind.LAYOUT, layout_sev,
                                                    tr("a térképelem a(z) „{t}” térképtémát követi").format(t=theme),
                                                    ref=lname))
                else:
                    # live map: shows the layers currently visible in the layer tree
                    for lid in ctx.layers_by_id:
                        if self._visible_in_tree(project, lid):
                            out.append(self._mk(lid, item_label, DepKind.LAYOUT, Severity.INFO,
                                                tr("élő térkép: a réteg jelenleg látható a rétegkezelőben"),
                                                ref=lname))
            elif isinstance(item, qcore.QgsLayoutItemLegend):
                if not item.autoUpdateModel():
                    try:
                        ids = item.model().rootGroup().findLayerIds()
                    except Exception:  # noqa: BLE001
                        ids = []
                    for lid in ids:
                        if lid in ctx.layers_by_id:
                            out.append(self._mk(lid, item_label, DepKind.LAYOUT, layout_sev,
                                                tr("jelmagyarázat (kézzel szerkesztett)"), ref=lname))
            elif hasattr(qcore, "QgsLayoutItemElevationProfile") and isinstance(item, qcore.QgsLayoutItemElevationProfile):
                # VERIFY: QgsLayoutItemElevationProfile.layers() (QGIS >= 3.30)
                if hasattr(item, "layers"):
                    for lyr in item.layers():
                        if lyr is not None and lyr.id() in ctx.layers_by_id:
                            out.append(self._mk(lyr.id(), item_label, DepKind.LAYOUT, layout_sev,
                                                tr("magassági profil"), ref=lname))
        if hasattr(layout, "multiFrames"):
            for frame in layout.multiFrames():
                if isinstance(frame, qcore.QgsLayoutItemAttributeTable):
                    lyr = frame.vectorLayer()
                    if lyr is not None and lyr.id() in ctx.layers_by_id:
                        out.append(self._mk(lyr.id(), label, DepKind.LAYOUT, layout_sev, tr("attribútumtábla"),
                                            ref=lname))
        if hasattr(layout, "atlas"):
            atlas = layout.atlas()
            if atlas is not None and atlas.enabled():
                lyr = atlas.coverageLayer()
                if lyr is not None and lyr.id() in ctx.layers_by_id:
                    out.append(self._mk(lyr.id(), label, DepKind.ATLAS, atlas_sev, tr("atlasz lefedő réteg"),
                                        ref=lname))
        return out

    @staticmethod
    def _xml_ids(layout, ctx) -> set[str]:
        doc = QDomDocument("qgis")
        el = layout.writeXml(doc, QgsReadWriteContext())
        doc.appendChild(el)
        tokens = set(_TOKEN_RE.findall(doc.toString()))
        return tokens & set(ctx.layers_by_id)

    # ------------------------------------------------------------------
    def _map_themes(self, project, ctx):
        out = []
        coll = project.mapThemeCollection()
        for theme in coll.mapThemes():
            label = tr("Térképtéma: {n}").format(n=theme)
            for record in coll.mapThemeState(theme).layerRecords():
                lyr = record.layer()
                if lyr is not None and lyr.id() in ctx.layers_by_id:
                    out.append(self._mk(lyr.id(), label, DepKind.MAP_THEME, Severity.INFO, tr("a téma tartalmazza")))
        return out

    def _snapping(self, project, ctx):
        out = []
        cfg = project.snappingConfig()
        try:
            individual = cfg.individualLayerSettings()
        except Exception:  # noqa: BLE001
            individual = {}
        for lyr, settings in individual.items():
            try:
                enabled = settings.enabled()
            except Exception:  # noqa: BLE001
                enabled = True
            if lyr is not None and enabled and lyr.id() in ctx.layers_by_id:
                out.append(self._mk(lyr.id(), tr("Illesztési beállítások"), DepKind.SNAPPING, Severity.INFO,
                                    tr("rétegenkénti illesztés")))
        for lyr in project.avoidIntersectionsLayers():
            if lyr is not None and lyr.id() in ctx.layers_by_id:
                out.append(self._mk(lyr.id(), tr("Átfedés elkerülése"), DepKind.AVOID_INTERSECTIONS, Severity.INFO,
                                    tr("átfedés elkerülése ezzel a réteggel")))
        return out

    def _terrain(self, project, ctx):
        out = []
        if not hasattr(project, "elevationProperties"):
            return out
        provider = project.elevationProperties().terrainProvider()
        if provider is not None and hasattr(provider, "layer"):
            lyr = provider.layer()
            if lyr is not None and lyr.id() in ctx.layers_by_id:
                out.append(self._mk(lyr.id(), tr("Projekt terepmodell"), DepKind.TERRAIN, Severity.WARN,
                                    tr("a projekt magassági (terep) beállítása ezt a réteget használja")))
        return out

    def _canvases(self, project, ctx):
        out = []
        iface = ctx.iface
        if iface is None or not hasattr(iface, "mapCanvases"):
            return out
        main = iface.mapCanvas()
        for canvas in iface.mapCanvases():
            if canvas is main or canvas.theme():
                continue
            name = canvas.objectName() or tr("térképablak")
            for lyr in canvas.layers():
                if lyr is not None and lyr.id() in ctx.layers_by_id:
                    out.append(self._mk(lyr.id(), tr("Térképablak: {n}").format(n=name), DepKind.MAP_CANVAS,
                                        Severity.INFO, tr("rögzített rétegkészlet")))
        return out
