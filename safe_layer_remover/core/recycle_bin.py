"""Session recycle bin.

Every entry keeps what QGIS throws away when a layer is removed, so that undo brings back the same
layer (same id) into the same place:

* the layer's own XML (restored with ``QgsProject.readLayer``, which keeps the layer id),
  plus a QLR file as a fallback,
* its position in the layer tree,
* the data of memory layers (written to a temporary GeoPackage, not kept in RAM),
* the project state QGIS drops together with the layer: relations, joins of other layers pointing to
  it, map theme records, locked layer sets of layout maps and group layer membership.

Everything lives in a temporary folder that is deleted at the end of the session, when another
project is opened, when the bin is emptied or when the plugin is unloaded.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
    QgsLayerDefinition,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsMapThemeCollection,
    QgsProject,
    QgsReadWriteContext,
    QgsRelation,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerJoinInfo,
)
from qgis.PyQt.QtCore import QObject, QTemporaryDir, QTimer, pyqtSignal
from qgis.PyQt.QtXml import QDomDocument

import qgis.core as qcore

from .classifier import classify
from .log import log_warning
from .model import Badge
from .texts import tr

DEFAULT_MAX_ENTRIES = 50

# fields of QgsMapThemeCollection.MapThemeLayerRecord that are copied by value
_THEME_RECORD_FIELDS = ("usingCurrentStyle", "currentStyle", "usingLegendItems", "checkedLegendItems",
                        "expandedLegendItems", "expandedLayerNode", "isVisible")


@dataclass
class BinEntry:
    entry_id: str
    batch_id: str
    layer_id: str
    name: str
    badges: list[Badge]
    deleted_at: datetime
    qlr_path: str
    group_path: list[str]
    index: int
    visible: bool = True
    layer_xml: str = ""
    data_path: Optional[str] = None                  # GeoPackage with the memory layer features
    data_layer: Optional[QgsVectorLayer] = field(default=None, repr=False)  # in-memory fallback copy
    data_skipped: bool = False                       # memory layer too large, data not kept
    is_memory: bool = False
    feature_count: int = 0
    automatic: bool = False                          # snapshot of a removal outside the plugin
    relations: list[QgsRelation] = field(default_factory=list, repr=False)
    joins: list[tuple[str, QgsVectorLayerJoinInfo]] = field(default_factory=list, repr=False)
    themes: list[tuple[str, dict]] = field(default_factory=list, repr=False)
    layout_maps: list[tuple[str, str, int]] = field(default_factory=list)   # (layout, item uuid, index)
    group_layers: list[tuple[str, int]] = field(default_factory=list)       # (group layer id, index)
    virtual_dependents: list[str] = field(default_factory=list)


@dataclass
class RestoreResult:
    restored: list[str] = field(default_factory=list)          # layer ids after restore
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    new_ids: dict[str, str] = field(default_factory=dict)      # original id -> new id, when it changed


def group_path_of(node) -> list[str]:
    path: list[str] = []
    parent = node.parent() if node is not None else None
    while parent is not None and parent.parent() is not None:
        path.append(parent.name())
        parent = parent.parent()
    path.reverse()
    return path


def write_vector_copy(layer: QgsVectorLayer, path: str, layer_name: str = "data") -> tuple[bool, str]:
    """Write `layer` into a GeoPackage. Returns (ok, error message)."""
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    options.fileEncoding = "UTF-8"
    ctx = QgsProject.instance().transformContext()
    # VERIFY: writeAsVectorFormatV3 (QGIS >= 3.20) returns (error, message, new_filename, new_layer)
    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3"):
        res = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, ctx, options)
    else:  # pragma: no cover
        res = QgsVectorFileWriter.writeAsVectorFormatV2(layer, path, ctx, options)
    error = res[0] if isinstance(res, tuple) else res
    message = res[1] if isinstance(res, tuple) and len(res) > 1 else ""
    return error == QgsVectorFileWriter.WriterError.NoError, message


def _layer_xml(layer) -> str:
    doc = QDomDocument("qgis")
    el = doc.createElement("maplayer")
    doc.appendChild(el)
    if not layer.writeLayerXml(el, doc, QgsReadWriteContext()):
        return ""
    return doc.toString()


class RecycleBin(QObject):
    changed = pyqtSignal()

    def __init__(self, project: Optional[QgsProject] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._project = project
        self._tmp: Optional[QTemporaryDir] = None
        self.entries: list[BinEntry] = []
        self.max_entries = DEFAULT_MAX_ENTRIES
        # tracking (filled while QGIS is about to remove things)
        self._pending_positions: dict[str, tuple[list[str], int, bool]] = {}
        self._theme_cache: dict[str, list[tuple[str, dict]]] = {}
        self._theme_cache_dirty = False
        self.theme_cache_delay_ms = 1500
        self._theme_timer = QTimer(self)
        self._theme_timer.setSingleShot(True)
        self._theme_timer.timeout.connect(self._rebuild_theme_cache)
        self._conns: list = []

    @property
    def project(self) -> QgsProject:
        return self._project or QgsProject.instance()

    def _dir(self) -> str:
        if self._tmp is None or not self._tmp.isValid():
            self._tmp = QTemporaryDir()
            self._tmp.setAutoRemove(True)
        return self._tmp.path()

    def __len__(self) -> int:
        return len(self.entries)

    # ------------------------------------------------------------------ tracking
    def start_tracking(self) -> None:
        """Watch the layer tree (positions) and map themes (cache) so removals can be undone exactly."""
        self.stop_tracking()
        root = self.project.layerTreeRoot()
        self._conns.append(root.willRemoveChildren.connect(self._on_will_remove_children))
        themes = self.project.mapThemeCollection()
        for sig in ("mapThemesChanged", "mapThemeChanged"):
            if hasattr(themes, sig):
                self._conns.append(getattr(themes, sig).connect(self._invalidate_theme_cache))
        self._invalidate_theme_cache()

    def stop_tracking(self) -> None:
        for conn in self._conns:
            try:
                QObject.disconnect(conn)
            except (TypeError, RuntimeError):
                pass
        self._conns = []

    def _on_will_remove_children(self, node, index_from: int, index_to: int) -> None:
        """Remember tree positions: the Layers panel removes the node before the layer is removed."""
        try:
            children = node.children()
            for i in range(index_from, index_to + 1):
                child = children[i]
                layers = [child] if isinstance(child, QgsLayerTreeLayer) else child.findLayers()
                for lnode in layers:
                    parent = lnode.parent()
                    idx = parent.children().index(lnode) if parent is not None else 0
                    self._pending_positions[lnode.layerId()] = (group_path_of(lnode), idx,
                                                                lnode.itemVisibilityChecked())
        except Exception as exc:  # noqa: BLE001 - tracking must never break QGIS
            log_warning(f"Layer tree position tracking failed: {exc}")

    def was_in_layer_tree(self, layer_id: str) -> bool:
        """True if the layer is (or was, just before this removal) shown in the Layers panel."""
        if layer_id in self._pending_positions:
            return True
        return self.project.layerTreeRoot().findLayer(layer_id) is not None

    def _invalidate_theme_cache(self, *args) -> None:
        # rebuilt lazily outside the removal: reading theme records while QGIS removes a layer is unsafe
        self._theme_cache_dirty = True
        # rebuilt when the project has been quiet for a moment (several changes, one rebuild)
        self._theme_timer.start(self.theme_cache_delay_ms)

    def _rebuild_theme_cache(self) -> None:
        if not self._theme_cache_dirty:
            return
        cache: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        try:
            coll = self.project.mapThemeCollection()
            for theme in coll.mapThemes():
                for rec in coll.mapThemeState(theme).layerRecords():
                    layer = rec.layer()
                    if layer is None:
                        continue
                    cache[layer.id()].append((theme, {f: getattr(rec, f) for f in _THEME_RECORD_FIELDS
                                                      if hasattr(rec, f)}))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Map theme cache failed: {exc}")
        self._theme_cache = dict(cache)
        self._theme_cache_dirty = False

    # ------------------------------------------------------------------ store
    def store(self, layer_ids: list[str], batch_id: Optional[str] = None, automatic: bool = False,
              feature_limit: Optional[int] = None) -> list[BinEntry]:
        """Snapshot the layers before they are removed. Raises on failure (caller aborts the removal).

        `feature_limit`: memory layers with more features keep only their definition, not their data.
        """
        batch_id = batch_id or uuid.uuid4().hex
        if self._theme_cache_dirty and not automatic:
            self._rebuild_theme_cache()   # safe here: the layers are not being removed yet
        project = self.project
        root = project.layerTreeRoot()
        stored: list[BinEntry] = []
        for lid in layer_ids:
            layer = project.mapLayer(lid)
            if layer is None:
                continue
            entry_id = uuid.uuid4().hex
            folder = os.path.join(self._dir(), entry_id)
            os.makedirs(folder, exist_ok=True)
            node = root.findLayer(lid)
            if node is not None:
                export_nodes = [node]
                group_path = group_path_of(node)
                parent = node.parent()
                index = parent.children().index(node) if parent is not None else 0
                visible = node.itemVisibilityChecked()
            else:
                export_nodes = [QgsLayerTreeLayer(layer)]
                group_path, index, visible = self._pending_positions.get(lid, ([], 0, True))
            self._pending_positions.pop(lid, None)
            qlr = os.path.join(folder, "layer.qlr")
            res = QgsLayerDefinition.exportLayerDefinition(qlr, export_nodes)
            ok, msg = (res if isinstance(res, tuple) else (res, ""))
            if not ok:
                raise RuntimeError(tr("A(z) {n} réteg nem menthető a lomtárba: {m}").format(n=layer.name(), m=msg))
            entry = BinEntry(
                entry_id=entry_id,
                batch_id=batch_id,
                layer_id=lid,
                name=layer.name(),
                badges=classify(layer, project),
                deleted_at=datetime.now(),
                qlr_path=qlr,
                group_path=list(group_path),
                index=index,
                visible=visible,
                layer_xml=_layer_xml(layer),
                is_memory=layer.providerType() == "memory",
                automatic=automatic,
            )
            if entry.is_memory and isinstance(layer, QgsVectorLayer):
                self._store_memory_data(entry, layer, folder, feature_limit)
            self._store_project_state(entry, lid)
            stored.append(entry)
        self.entries.extend(stored)
        self._evict()
        self.changed.emit()
        return stored

    @staticmethod
    def _store_memory_data(entry: BinEntry, layer: QgsVectorLayer, folder: str, feature_limit: Optional[int]) -> None:
        entry.feature_count = layer.featureCount()
        if feature_limit is not None and entry.feature_count > feature_limit:
            entry.data_skipped = True
            return
        gpkg = os.path.join(folder, "data.gpkg")
        ok, msg = write_vector_copy(layer, gpkg)
        if ok:
            entry.data_path = gpkg
        elif feature_limit is None or entry.feature_count <= feature_limit:
            log_warning(f"GeoPackage copy of '{layer.name()}' failed ({msg}), keeping an in-memory copy")
            entry.data_layer = layer.materialize(QgsFeatureRequest())

    def _store_project_state(self, entry: BinEntry, lid: str) -> None:
        """What QGIS discards together with the layer. Each part is optional: failures are only logged."""
        project = self.project
        try:
            for rel in project.relationManager().relations().values():
                if lid in (rel.referencingLayerId(), rel.referencedLayerId()):
                    entry.relations.append(QgsRelation(rel))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Relation snapshot failed: {exc}")
        for other in project.mapLayers().values():
            if other.id() == lid:
                continue
            try:
                if isinstance(other, QgsVectorLayer):
                    for join in other.vectorJoins():
                        if join.joinLayerId() == lid:
                            entry.joins.append((other.id(), QgsVectorLayerJoinInfo(join)))
                    if other.providerType() == "virtual" and lid in other.source():
                        entry.virtual_dependents.append(other.id())
                group_cls = getattr(qcore, "QgsGroupLayer", None)
                if group_cls is not None and isinstance(other, group_cls):
                    children = [c.id() for c in other.childLayers() if c is not None]
                    if lid in children:
                        entry.group_layers.append((other.id(), children.index(lid)))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Snapshot of references from '{other.name()}' failed: {exc}")
        try:
            for layout in project.layoutManager().printLayouts():
                for item in layout.items():
                    if isinstance(item, qcore.QgsLayoutItemMap) and item.keepLayerSet():
                        ids = [lyr.id() for lyr in item.layers() if lyr is not None]
                        if lid in ids:
                            entry.layout_maps.append((layout.name(), item.uuid(), ids.index(lid)))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Layout snapshot failed: {exc}")
        entry.themes = list(self._theme_cache.get(lid, []))

    def _evict(self) -> None:
        limit = max(1, int(self.max_entries or DEFAULT_MAX_ENTRIES))
        while len(self.entries) > limit:
            oldest = min(self.entries, key=lambda e: e.deleted_at)
            self.entries.remove(oldest)
            self._delete_files(oldest)

    # ------------------------------------------------------------------ restore
    def _ensure_group(self, path: list[str]) -> QgsLayerTreeGroup:
        group = self.project.layerTreeRoot()
        for name in path:
            child = None
            for c in group.children():
                if isinstance(c, QgsLayerTreeGroup) and c.name() == name:
                    child = c
                    break
            if child is None:
                child = group.addGroup(name)
            group = child
        return group

    def restore(self, entry_ids: list[str]) -> RestoreResult:
        result = RestoreResult()
        by_id = {e.entry_id: e for e in self.entries}
        # restore in original order (lower index first) so positions are reproduced
        todo = sorted((by_id[i] for i in entry_ids if i in by_id), key=lambda e: (len(e.group_path), e.index))
        from . import locks
        locked = locks.locked_ids(self.project)
        done: list[tuple[BinEntry, Any]] = []
        # 1. layers first, so references between restored layers can be rebuilt afterwards
        for entry in todo:
            try:
                layer = self._restore_layer(entry, result)
                result.restored.append(layer.id())
                if layer.id() != entry.layer_id:
                    result.new_ids[entry.layer_id] = layer.id()
                if entry.layer_id in locked:
                    # keep the personal lock on the restored layer (its id may have changed)
                    locks.set_locked([layer.id()], True, self.project)
                done.append((entry, layer))
            except Exception as exc:  # noqa: BLE001
                result.errors.append(tr("{n}: {e}").format(n=entry.name, e=exc))
        # 2. project state that pointed to the layers
        for entry, layer in done:
            self._restore_project_state(entry, layer, result)
            self.entries.remove(entry)
            self._delete_files(entry)
        if result.new_ids:
            result.notes.append(tr("Néhány réteg új azonosítót kapott; az ezekre mutató hivatkozások egy része "
                                   "nem állt vissza."))
        self._invalidate_theme_cache()
        self.changed.emit()
        return result

    def _restore_layer(self, entry: BinEntry, result: RestoreResult):
        project = self.project
        group = self._ensure_group(entry.group_path)
        node = None
        # a) same id from the saved layer XML
        if entry.layer_xml and project.mapLayer(entry.layer_id) is None and hasattr(project, "readLayer"):
            doc = QDomDocument("qgis")
            doc.setContent(entry.layer_xml)
            try:
                ok = project.readLayer(doc.documentElement())
            except Exception as exc:  # noqa: BLE001
                log_warning(f"readLayer failed for '{entry.name}': {exc}")
                ok = False
            if ok and project.mapLayer(entry.layer_id) is not None:
                layer = project.mapLayer(entry.layer_id)
                node = project.layerTreeRoot().findLayer(entry.layer_id)
                if node is None:
                    node = group.insertLayer(0, layer)
        # b) fallback: the QLR (QGIS may assign a new id)
        if node is None:
            before = {n.layerId() for n in group.findLayers()}
            res = QgsLayerDefinition.loadLayerDefinition(entry.qlr_path, project, group)
            ok, msg = (res if isinstance(res, tuple) else (res, ""))
            if not ok:
                raise RuntimeError(msg or tr("a rétegdefiníció nem tölthető be"))
            new_nodes = [n for n in group.findLayers() if n.layerId() not in before]
            if not new_nodes:
                raise RuntimeError(tr("a visszaállított réteg nem található"))
            node = new_nodes[0]
        # move to the original position
        parent = node.parent()
        index = max(0, min(entry.index, len(group.children()) - (1 if parent is group else 0)))
        if parent is not group or group.children().index(node) != index:
            clone = node.clone()
            group.insertChildNode(index, clone)
            parent.removeChildNode(node)
            node = clone
        node.setItemVisibilityChecked(entry.visible)
        layer = node.layer()
        if layer is None:
            raise RuntimeError(tr("a visszaállított réteg érvénytelen"))
        if entry.is_memory and isinstance(layer, QgsVectorLayer):
            if entry.data_skipped:
                result.notes.append(tr("{n}: a memóriaréteg túl nagy volt, ezért csak a definíciója állt vissza, "
                                       "az adatai nem.").format(n=entry.name))
            else:
                self._restore_memory_data(entry, layer, result)
        return layer

    def _restore_project_state(self, entry: BinEntry, layer, result: RestoreResult) -> None:
        project = self.project
        lid = layer.id()
        old = entry.layer_id

        def fix(layer_id: str) -> str:
            return lid if layer_id == old else result.new_ids.get(layer_id, layer_id)

        rm = project.relationManager()
        for rel in entry.relations:
            try:
                if rm.relation(rel.id()).isValid():
                    continue
                new_rel = QgsRelation(rel)
                new_rel.setReferencingLayer(fix(rel.referencingLayerId()))
                new_rel.setReferencedLayer(fix(rel.referencedLayerId()))
                if project.mapLayer(new_rel.referencingLayerId()) and project.mapLayer(new_rel.referencedLayerId()):
                    rm.addRelation(new_rel)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Relation restore failed: {exc}")
        for dep_id, join in entry.joins:
            dep = project.mapLayer(fix(dep_id))
            if not isinstance(dep, QgsVectorLayer):
                continue
            try:
                if any(j.joinLayerId() == lid for j in dep.vectorJoins()):
                    continue
                info = QgsVectorLayerJoinInfo(join)
                info.setJoinLayer(layer)
                dep.addJoin(info)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Join restore failed: {exc}")
        coll = project.mapThemeCollection()
        for theme, values in entry.themes:
            try:
                if not coll.hasMapTheme(theme):
                    continue
                state = coll.mapThemeState(theme)
                records = list(state.layerRecords())
                if any(r.layer() is not None and r.layer().id() == lid for r in records):
                    continue
                rec = QgsMapThemeCollection.MapThemeLayerRecord(layer)
                for name, value in values.items():
                    try:
                        setattr(rec, name, value)
                    except Exception:  # noqa: BLE001
                        pass
                records.append(rec)
                state.setLayerRecords(records)
                coll.update(theme, state)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Map theme restore failed: {exc}")
        for layout_name, item_uuid, idx in entry.layout_maps:
            try:
                layout = project.layoutManager().layoutByName(layout_name)
                item = layout.itemByUuid(item_uuid) if layout is not None else None
                if item is None:
                    continue
                layers = [lyr for lyr in item.layers() if lyr is not None]
                if layer not in layers:
                    layers.insert(min(idx, len(layers)), layer)
                    item.setLayers(layers)
                    item.refresh()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Layout restore failed: {exc}")
        for group_id, idx in entry.group_layers:
            try:
                group_layer = project.mapLayer(fix(group_id))
                if group_layer is None:
                    continue
                children = [c for c in group_layer.childLayers() if c is not None]
                if layer not in children:
                    children.insert(min(idx, len(children)), layer)
                    group_layer.setChildLayers(children)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Group layer restore failed: {exc}")
        for vid in entry.virtual_dependents:
            vlayer = project.mapLayer(fix(vid))
            if isinstance(vlayer, QgsVectorLayer):
                try:
                    vlayer.setDataSource(vlayer.source(), vlayer.name(), vlayer.providerType())
                    vlayer.triggerRepaint()
                except Exception as exc:  # noqa: BLE001
                    log_warning(f"Virtual layer reload failed: {exc}")

    @staticmethod
    def _restore_memory_data(entry: BinEntry, layer: QgsVectorLayer, result: RestoreResult) -> None:
        if layer.featureCount() >= entry.feature_count and entry.feature_count > 0:
            return  # the definition already carried the features
        if entry.feature_count == 0:
            return
        source: Optional[QgsVectorLayer] = entry.data_layer
        if source is None and entry.data_path:
            source = QgsVectorLayer(f"{entry.data_path}|layername=data", "restore", "ogr")
        if source is None or not source.isValid():
            result.errors.append(tr("{n}: a memóriaréteg adatai nem állíthatók vissza").format(n=entry.name))
            return
        fields = layer.fields()
        mapping = [(fields.indexOf(f.name()), i) for i, f in enumerate(source.fields()) if fields.indexOf(f.name()) >= 0]
        features = []
        for src in source.getFeatures():
            feat = QgsFeature(fields)
            if src.hasGeometry():
                feat.setGeometry(src.geometry())
            attrs = [None] * fields.count()
            for dst_idx, src_idx in mapping:
                attrs[dst_idx] = src.attribute(src_idx)
            feat.setAttributes(attrs)
            features.append(feat)
        if features:
            ok = layer.dataProvider().addFeatures(features)
            ok = ok[0] if isinstance(ok, tuple) else ok
            if not ok:
                result.errors.append(tr("{n}: a memóriaréteg adatainak visszatöltése sikertelen").format(n=entry.name))
            layer.updateExtents()
            layer.triggerRepaint()

    # ------------------------------------------------------------------
    def discard(self, entry_ids: list[str]) -> None:
        keep = []
        for entry in self.entries:
            if entry.entry_id in entry_ids:
                self._delete_files(entry)
            else:
                keep.append(entry)
        self.entries = keep
        self.changed.emit()

    def clear(self) -> None:
        for entry in self.entries:
            self._delete_files(entry)
        self.entries = []
        self._pending_positions.clear()
        self.changed.emit()

    def batch(self, batch_id: str) -> list[BinEntry]:
        return [e for e in self.entries if e.batch_id == batch_id]

    @staticmethod
    def _delete_files(entry: BinEntry) -> None:
        entry.data_layer = None
        folder = os.path.dirname(entry.qlr_path)
        shutil.rmtree(folder, ignore_errors=True)
