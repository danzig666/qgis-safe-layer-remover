"""Project change watcher with debounce and per-layer dirty flags."""

from __future__ import annotations

from typing import Callable, Optional

from qgis.core import QgsMapLayer, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

DEBOUNCE_MS = 400


class ProjectWatcher(QObject):
    """Emits `changed` (debounced) when anything relevant for the analysis changes.

    `layerDirty(layer_id)` is emitted immediately so the analyzer can drop the layer's XML cache.
    """

    changed = pyqtSignal()
    layerDirty = pyqtSignal(str)
    projectReset = pyqtSignal()

    def __init__(self, project: Optional[QgsProject] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._project = project
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.changed.emit)
        self._project_conns: list = []
        self._sub_conns: list = []
        self._layer_conns: dict[str, list] = {}
        self._active = False
        self.suspended = False

    @property
    def project(self) -> QgsProject:
        return self._project or QgsProject.instance()

    # ------------------------------------------------------------------
    def _connect(self, store: list, signal, slot: Callable) -> None:
        try:
            # keep the connection handle: disconnecting through it is safe even
            # when the sender has been deleted meanwhile
            store.append(signal.connect(slot))
        except (TypeError, AttributeError, RuntimeError):
            pass

    @staticmethod
    def _disconnect_all(store: list) -> None:
        for conn in store:
            try:
                QObject.disconnect(conn)
            except (TypeError, RuntimeError):
                pass
        store.clear()

    def _connect_sub_objects(self) -> None:
        """Signals of project sub objects that QgsProject.clear() may recreate."""
        self._disconnect_all(self._sub_conns)
        p = self.project
        c = self._sub_conns
        root = p.layerTreeRoot()
        for sig in ("addedChildren", "removedChildren", "nameChanged", "visibilityChanged"):
            if hasattr(root, sig):
                self._connect(c, getattr(root, sig), self.touch)
        self._connect(c, p.relationManager().changed, self.touch)
        lm = p.layoutManager()
        for sig in ("layoutAdded", "layoutRemoved", "layoutRenamed"):
            if hasattr(lm, sig):
                self._connect(c, getattr(lm, sig), self.touch)
        self._connect(c, p.mapThemeCollection().mapThemesChanged, self.touch)

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        p = self.project
        c = self._project_conns
        self._connect(c, p.layersAdded, self._on_layers_added)
        # disconnect while the layers are still alive; after layersRemoved they are deleted
        self._connect(c, p.layersWillBeRemoved, self._on_layers_will_be_removed)
        self._connect(c, p.layersRemoved, self._on_layers_removed)
        self._connect(c, p.cleared, self._on_reset)
        self._connect(c, p.readProject, self._on_reset)  # VERIFY: QgsProject.readProject(QDomDocument)
        self._connect_sub_objects()
        for layer in p.mapLayers().values():
            self._watch_layer(layer)

    def stop(self) -> None:
        self._timer.stop()
        self._disconnect_all(self._project_conns)
        self._disconnect_all(self._sub_conns)
        for store in self._layer_conns.values():
            self._disconnect_all(store)
        self._layer_conns.clear()
        self._active = False

    # ------------------------------------------------------------------
    def _watch_layer(self, layer: QgsMapLayer) -> None:
        lid = layer.id()
        if lid in self._layer_conns:
            return
        store: list = []
        dirty = lambda *args, _lid=lid: self._mark_dirty(_lid)  # noqa: E731
        for sig in ("nameChanged", "dataSourceChanged", "styleChanged", "rendererChanged", "dependenciesChanged",
                    "flagsChanged", "customPropertyChanged"):
            if hasattr(layer, sig):
                self._connect(store, getattr(layer, sig), dirty)
        if isinstance(layer, QgsVectorLayer):
            for sig in ("updatedFields", "editingStarted", "editingStopped", "layerModified",
                        "editFormConfigChanged", "displayExpressionChanged", "mapTipTemplateChanged"):
                if hasattr(layer, sig):
                    self._connect(store, getattr(layer, sig), dirty)
        self._layer_conns[lid] = store

    def _unwatch_layer(self, lid: str) -> None:
        store = self._layer_conns.pop(lid, None)
        if store:
            self._disconnect_all(store)

    def _on_layers_added(self, layers) -> None:
        for layer in layers:
            self._watch_layer(layer)
            self.layerDirty.emit(layer.id())
        # no touch(): layers added to the Layers panel trigger it through the layer tree signals,
        # helper layers that plugins add and remove in a loop should not start an analysis

    def _on_layers_will_be_removed(self, items) -> None:
        for item in items:
            self._unwatch_layer(item if isinstance(item, str) else item.id())

    def _on_layers_removed(self, layer_ids) -> None:
        for lid in layer_ids:
            store = self._layer_conns.pop(lid, None)
            if store:
                self._disconnect_all(store)  # safe: handle based
            self.layerDirty.emit(lid)
        # no touch() here either, see _on_layers_added

    def _on_reset(self, *args) -> None:
        # layers of the previous project are deleted; Qt dropped their connections
        alive = set(self.project.mapLayers().keys())
        for lid in list(self._layer_conns):
            if lid not in alive:
                self._disconnect_all(self._layer_conns.pop(lid))
        if self._active:
            self._connect_sub_objects()
        for layer in self.project.mapLayers().values():
            self._watch_layer(layer)
        self.projectReset.emit()
        self.touch()

    def _mark_dirty(self, lid: str) -> None:
        self.layerDirty.emit(lid)
        self.touch()

    def touch(self, *args) -> None:
        if self.suspended:
            return
        self._timer.start()

    def flush(self) -> None:
        """Fire a pending change notification immediately."""
        if self._timer.isActive():
            self._timer.stop()
            self.changed.emit()
