"""Non-layer project objects: layouts, atlases, map themes, snapping, terrain, extra map canvases."""

from __future__ import annotations

import re

import xml.etree.ElementTree as ET

from qgis.core import QgsLayoutItemRegistry, QgsReadWriteContext
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
            for lid in self._cached_xml_ids(layout, ctx):
                if lid not in known:
                    out.append(self._mk(lid, label, DepKind.LAYOUT, layout_sev,
                                        tr("az elrendezés beállításai hivatkoznak a rétegre"), Certainty.MEDIUM, lname))
        return out

    def _layout_items(self, layout, label, layout_sev, atlas_sev, project, ctx):
        out = []
        lname = layout.name()
        if not hasattr(layout, "items"):
            return out  # e.g. QgsReport: covered by the XML scan
        themes = project.mapThemeCollection()
        items = list(layout.items())

        def item_label(item):
            name = item.displayName() if hasattr(item, "displayName") else type(item).__name__
            return f"{label} / {name}"

        # 1. map items: only locked layer sets and maps following a theme put layers on the layout.
        #    A live map shows whatever is visible in the layer tree right now, so it does not count.
        shown_by_map: dict[str, set[str]] = {}
        for item in items:
            if not isinstance(item, qcore.QgsLayoutItemMap):
                continue
            ids: set[str] = set()
            detail = ""
            if item.keepLayerSet():
                ids = {lyr.id() for lyr in item.layers() if lyr is not None and lyr.id() in ctx.layers_by_id}
                detail = tr("rögzített rétegkészlet")
            elif item.followVisibilityPreset() and item.followVisibilityPresetName():
                theme = item.followVisibilityPresetName()
                if themes.hasMapTheme(theme):
                    ids = {lid for lid in themes.mapThemeVisibleLayerIds(theme) if lid in ctx.layers_by_id}
                detail = tr("a térképelem a(z) „{t}” térképtémát követi").format(t=theme)
            shown_by_map[item.uuid()] = ids
            for lid in sorted(ids):
                out.append(self._mk(lid, item_label(item), DepKind.LAYOUT, layout_sev, detail, ref=lname))

        # 2. other items
        for item in items:
            if isinstance(item, qcore.QgsLayoutItemLegend):
                # a legend never puts a layer on the layout by itself: a manually edited legend tree
                # often holds the whole project. Only layers its linked (counted) map shows are listed.
                if item.autoUpdateModel():
                    continue
                linked = item.linkedMap()
                allowed = shown_by_map.get(linked.uuid(), set()) if linked is not None else set()
                if not allowed:
                    continue
                try:
                    ids = item.model().rootGroup().findLayerIds()
                except Exception:  # noqa: BLE001
                    ids = []
                for lid in ids:
                    if lid in allowed:
                        out.append(self._mk(lid, item_label(item), DepKind.LAYOUT, layout_sev,
                                            tr("jelmagyarázat (kézzel szerkesztett)"), ref=lname))
            elif hasattr(qcore, "QgsLayoutItemElevationProfile") and isinstance(item, qcore.QgsLayoutItemElevationProfile):
                # VERIFY: QgsLayoutItemElevationProfile.layers() (QGIS >= 3.30)
                if hasattr(item, "layers"):
                    for lyr in item.layers():
                        if lyr is not None and lyr.id() in ctx.layers_by_id:
                            out.append(self._mk(lyr.id(), item_label(item), DepKind.LAYOUT, layout_sev,
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
    def _layout_signature(layout) -> tuple:
        """Changes whenever the layout is edited (designer edits go through its undo stack)."""
        parts: list = [layout.name()]
        try:
            stack = layout.undoStack().stack()
            parts += [stack.index(), stack.count()]
        except Exception:  # noqa: BLE001
            parts.append(None)
        for attr in ("items", "multiFrames"):
            try:
                parts.append(len(getattr(layout, attr)()))
            except Exception:  # noqa: BLE001
                parts.append(None)
        try:
            atlas = layout.atlas()
            cov = atlas.coverageLayer() if atlas is not None else None
            parts.append((atlas.enabled(), cov.id() if cov is not None else None) if atlas is not None else None)
        except Exception:  # noqa: BLE001
            parts.append(None)
        return tuple(parts)

    def _cached_xml_ids(self, layout, ctx) -> set[str]:
        name = layout.name()
        signature = self._layout_signature(layout)
        cached = ctx.layout_cache.get(name)
        if cached is None or cached[0] != signature:
            cached = (signature, frozenset(self._xml_tokens(layout)))
            ctx.layout_cache[name] = cached
        return set(cached[1] & ctx.layers_by_id.keys())

    @staticmethod
    def _xml_ids(layout, ctx) -> set[str]:
        return ProjectObjectDetector._xml_tokens(layout) & set(ctx.layers_by_id)

    @staticmethod
    def _xml_tokens(layout) -> set[str]:
        """Layer ids in the layout XML outside the parts analysed above.

        Skipped: custom properties (other plugins often store whole layer lists there), and the
        map / legend / elevation profile items, which are handled item by item.
        """
        doc = QDomDocument("qgis")
        el = layout.writeXml(doc, QgsReadWriteContext())
        doc.appendChild(el)
        try:
            root = ET.fromstring(doc.toString().encode("utf-8"))
        except ET.ParseError:
            return set(_TOKEN_RE.findall(doc.toString()))
        skip_types = set()
        for name in ("LayoutMap", "LayoutLegend", "LayoutElevationProfile"):
            value = getattr(QgsLayoutItemRegistry.ItemType, name, None)
            if value is not None:
                skip_types.add(str(int(value)))
        found: set[str] = set()

        def visit(node):
            if node.tag == "customproperties":
                return
            if node.tag == "LayoutItem" and node.get("type") in skip_types:
                return
            for value in list(node.attrib.values()) + [node.text or ""]:
                if value:
                    found.update(_TOKEN_RE.findall(value))
            for child in node:
                visit(child)

        visit(root)
        return found

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
