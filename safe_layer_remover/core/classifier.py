"""Layer type classification (badges) and icons."""

from __future__ import annotations

import os
from typing import Optional

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsMapLayer,
    QgsProject,
    QgsProviderRegistry,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QIcon

from .model import Badge
from .texts import tr

_ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons")


def _decode(layer: QgsMapLayer) -> dict:
    try:
        return QgsProviderRegistry.instance().decodeUri(layer.providerType(), layer.source()) or {}
    except Exception:  # noqa: BLE001
        return {}


def _extension(layer: QgsMapLayer) -> str:
    path = _decode(layer).get("path") or ""
    if not path:
        path = layer.source().split("|")[0]
    return os.path.splitext(str(path))[1].lower().lstrip(".")


def primary_badge(layer: QgsMapLayer) -> Badge:
    provider = (layer.providerType() or "").lower()
    ltype = layer.type()

    if ltype == Qgis.LayerType.Group:
        return Badge(tr("Csoportréteg"), "indigo", True)
    if ltype == Qgis.LayerType.Mesh:
        return Badge(tr("Mesh"), "teal", True)
    if ltype == Qgis.LayerType.PointCloud:
        return Badge(tr("Pontfelhő"), "teal", True)
    if ltype == Qgis.LayerType.VectorTile:
        return Badge(tr("Vektorcsempe"), "orange", True)
    if hasattr(Qgis.LayerType, "TiledScene") and ltype == Qgis.LayerType.TiledScene:
        return Badge(tr("3D csempézett"), "teal", True)
    if ltype == Qgis.LayerType.Annotation:
        return Badge(tr("Annotáció"), "gray", True)
    if ltype == Qgis.LayerType.Plugin:
        return Badge(tr("Plugin réteg"), "gray", True)

    if provider == "memory":
        return Badge(tr("Memória"), "purple", True, tr("Ideiglenes réteg, az adatai csak a memóriában vannak."))
    if provider == "virtual":
        return Badge(tr("Virtuális"), "blue", True, tr("SQL lekérdezéssel definiált virtuális réteg."))
    if provider == "virtualraster":
        return Badge(tr("Virtuális raszter"), "blue", True)
    if provider == "ogr":
        ext = _extension(layer)
        if ext == "gpkg":
            return Badge(tr("GeoPackage"), "slate", True)
        if ext == "shp":
            return Badge(tr("Shapefile"), "gray", True)
        if ext in ("geojson", "json"):
            return Badge(tr("GeoJSON"), "gray", True)
        label = tr("Fájl (OGR)") + (f" .{ext}" if ext else "")
        return Badge(label, "gray", True)
    if provider == "delimitedtext":
        return Badge(tr("CSV/szöveg"), "gray", True)
    if provider == "spatialite":
        return Badge(tr("SpatiaLite"), "slate", True)
    if provider == "postgres":
        return Badge(tr("PostGIS"), "cyan", True)
    if provider in ("oracle", "mssql", "hana"):
        names = {"oracle": "Oracle", "mssql": "MS SQL", "hana": "SAP HANA"}
        return Badge(tr("Adatbázis") + " " + names[provider], "cyan", True)
    if provider == "wms":
        src = layer.source()
        if "type=xyz" in src:
            return Badge(tr("XYZ csempe"), "orange", True)
        return Badge(tr("WMS/WMTS"), "orange", True)
    if provider in ("wfs", "oapif"):
        return Badge(tr("WFS / OGC API"), "orange", True)
    if provider in ("arcgisfeatureserver", "arcgismapserver", "afs", "ams"):
        return Badge(tr("ArcGIS REST"), "orange", True)
    if provider == "gdal":
        ext = _extension(layer)
        label = tr("Raszter") + (f" .{ext}" if ext else "")
        return Badge(label, "brown", True)
    if ltype == Qgis.LayerType.Raster:
        return Badge(tr("Raszter") + f" ({provider})", "brown", True)
    if ltype == Qgis.LayerType.Vector:
        return Badge(tr("Vektor") + f" ({provider})", "gray", True)
    return Badge(provider or tr("Ismeretlen"), "gray", True)


def flag_badges(layer: QgsMapLayer, project: Optional[QgsProject] = None) -> list[Badge]:
    project = project or QgsProject.instance()
    badges: list[Badge] = []
    if isinstance(layer, QgsVectorLayer):
        try:
            gt = layer.geometryType()
            names = {
                Qgis.GeometryType.Point: tr("Pont"),
                Qgis.GeometryType.Line: tr("Vonal"),
                Qgis.GeometryType.Polygon: tr("Felület"),
                Qgis.GeometryType.Null: tr("Geometria nélküli"),
            }
            if gt in names:
                badges.append(Badge(names[gt], "neutral"))
        except Exception:  # noqa: BLE001
            pass
    if not layer.isValid():
        badges.append(Badge(tr("Érvénytelen forrás"), "red", False, tr("A réteg adatforrása nem érhető el.")))
    if isinstance(layer, QgsVectorLayer):
        if layer.isEditable():
            if layer.isModified():
                badges.append(Badge(tr("Mentetlen módosítás"), "amber"))
            else:
                badges.append(Badge(tr("Szerkesztés alatt"), "amber"))
        try:
            if layer.vectorJoins():
                badges.append(Badge(tr("Joinokat használ"), "neutral"))
        except Exception:  # noqa: BLE001
            pass
        try:
            if layer.auxiliaryLayer() is not None:
                badges.append(Badge(tr("Kiegészítő tároló"), "neutral", False,
                                    tr("Címkepozíciók és egyéb kiegészítő adatok törléskor elvesznek.")))
        except Exception:  # noqa: BLE001
            pass
    try:
        if project.layerIsEmbedded(layer.id()):
            badges.append(Badge(tr("Beágyazott"), "neutral"))
    except Exception:  # noqa: BLE001
        pass
    try:
        if hasattr(layer, "subsetString") and layer.subsetString():
            badges.append(Badge(tr("Szűrt"), "neutral", False, layer.subsetString()))
    except Exception:  # noqa: BLE001
        pass
    return badges


def classify(layer: QgsMapLayer, project: Optional[QgsProject] = None) -> list[Badge]:
    try:
        return [primary_badge(layer)] + flag_badges(layer, project)
    except Exception:  # noqa: BLE001
        return [Badge(tr("Ismeretlen"), "gray", True)]


def layer_icon(layer: Optional[QgsMapLayer]) -> QIcon:
    if layer is None:
        return QIcon()
    try:
        from qgis.core import QgsIconUtils
        icon = QgsIconUtils.iconForLayer(layer)
        if icon is not None and not icon.isNull():
            return icon
    except Exception:  # noqa: BLE001
        pass
    return QgsApplication.getThemeIcon("/mIconLayer.png")


def group_icon() -> QIcon:
    icon = QgsApplication.getThemeIcon("/mActionFolder.svg")
    if icon.isNull():
        icon = QgsApplication.getThemeIcon("/mIconFolder.svg")
    return icon


def provider_mini_icon(layer: QgsMapLayer) -> Optional[QIcon]:
    """Small extra icon for memory / virtual layers (shown next to the name)."""
    provider = layer.providerType()
    name = {"memory": "/mActionCreateMemory.svg", "virtual": "/mActionAddVirtualLayer.svg"}.get(provider)
    if not name:
        return None
    icon = QgsApplication.getThemeIcon(name)
    if icon.isNull():
        fallback = os.path.join(_ICON_DIR, f"{provider}.svg")
        if os.path.exists(fallback):
            return QIcon(fallback)
        return None
    return icon


def plugin_icon(name: str) -> QIcon:
    return QIcon(os.path.join(_ICON_DIR, name))
