"""Session recycle bin: QLR snapshots + memory layer data, restore into the original tree position."""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
    QgsLayerDefinition,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsMapLayer,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QObject, QTemporaryDir, pyqtSignal

from .classifier import classify
from .log import log_warning
from .model import Badge
from .texts import tr


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
    data_path: Optional[str] = None                  # GeoPackage with the memory layer features
    data_layer: Optional[QgsVectorLayer] = field(default=None, repr=False)  # in-memory fallback copy
    is_memory: bool = False
    feature_count: int = 0


@dataclass
class RestoreResult:
    restored: list[str] = field(default_factory=list)          # new layer ids
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


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


class RecycleBin(QObject):
    changed = pyqtSignal()

    def __init__(self, project: Optional[QgsProject] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._project = project
        self._tmp: Optional[QTemporaryDir] = None
        self.entries: list[BinEntry] = []

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

    # ------------------------------------------------------------------
    def store(self, layer_ids: list[str], batch_id: Optional[str] = None) -> list[BinEntry]:
        """Snapshot the layers before they are removed. Raises on failure (caller aborts the removal)."""
        batch_id = batch_id or uuid.uuid4().hex
        root = self.project.layerTreeRoot()
        stored: list[BinEntry] = []
        for lid in layer_ids:
            layer = self.project.mapLayer(lid)
            if layer is None:
                continue
            entry_id = uuid.uuid4().hex
            folder = os.path.join(self._dir(), entry_id)
            os.makedirs(folder, exist_ok=True)
            node = root.findLayer(lid)
            temp_node = None
            if node is None:
                temp_node = QgsLayerTreeLayer(layer)
                export_nodes = [temp_node]
                group_path, index, visible = [], 0, True
            else:
                export_nodes = [node]
                group_path = group_path_of(node)
                parent = node.parent()
                index = parent.children().index(node) if parent is not None else 0
                visible = node.itemVisibilityChecked()
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
                badges=classify(layer, self.project),
                deleted_at=datetime.now(),
                qlr_path=qlr,
                group_path=group_path,
                index=index,
                visible=visible,
                is_memory=layer.providerType() == "memory",
            )
            if entry.is_memory and isinstance(layer, QgsVectorLayer):
                entry.feature_count = layer.featureCount()
                gpkg = os.path.join(folder, "data.gpkg")
                ok, msg = write_vector_copy(layer, gpkg)
                if ok:
                    entry.data_path = gpkg
                else:
                    log_warning(f"GeoPackage copy of '{layer.name()}' failed ({msg}), keeping an in-memory copy")
                    entry.data_layer = layer.materialize(QgsFeatureRequest())
            stored.append(entry)
        self.entries.extend(stored)
        self.changed.emit()
        return stored

    # ------------------------------------------------------------------
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
        for entry in todo:
            try:
                new_id = self._restore_one(entry, result)
                result.restored.append(new_id)
                if entry.layer_id in locked:
                    # keep the personal lock on the restored layer (its id may have changed)
                    locks.set_locked([new_id], True, self.project)
                self.entries.remove(entry)
                self._delete_files(entry)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(tr("{n}: {e}").format(n=entry.name, e=exc))
        if result.restored:
            result.notes.append(tr("A visszaállított rétegek új azonosítót kaphatnak; a térképtémák és "
                                   "elrendezések hivatkozásai nem állnak vissza."))
        self.changed.emit()
        return result

    def _restore_one(self, entry: BinEntry, result: RestoreResult) -> str:
        group = self._ensure_group(entry.group_path)
        before = {n.layerId() for n in group.findLayers()}
        res = QgsLayerDefinition.loadLayerDefinition(entry.qlr_path, self.project, group)
        ok, msg = (res if isinstance(res, tuple) else (res, ""))
        if not ok:
            raise RuntimeError(msg or tr("a rétegdefiníció nem tölthető be"))
        new_nodes = [n for n in group.findLayers() if n.layerId() not in before]
        if not new_nodes:
            raise RuntimeError(tr("a visszaállított réteg nem található"))
        node = new_nodes[0]
        # move to the original position
        parent = node.parent()
        index = max(0, min(entry.index, len(group.children()) - 1))
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
            self._restore_memory_data(entry, layer, result)
        return layer.id()

    @staticmethod
    def _restore_memory_data(entry: BinEntry, layer: QgsVectorLayer, result: RestoreResult) -> None:
        if layer.featureCount() >= entry.feature_count and entry.feature_count > 0:
            return  # the QLR already carried the features
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
        self.changed.emit()

    def batch(self, batch_id: str) -> list[BinEntry]:
        return [e for e in self.entries if e.batch_id == batch_id]

    @staticmethod
    def _delete_files(entry: BinEntry) -> None:
        entry.data_layer = None
        folder = os.path.dirname(entry.qlr_path)
        shutil.rmtree(folder, ignore_errors=True)
