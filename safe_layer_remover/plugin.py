"""SafeLayerRemoverPlugin: GUI registration, dock, layer tree integration, sentinel mode."""

from __future__ import annotations

import os
from typing import Optional

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.PyQt.QtCore import QCoreApplication, QLocale, QObject, QSettings, Qt, QTimer, QTranslator
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QPushButton

try:  # Qt6
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction

from .core import locks
from .core import settings as plugin_settings
from .core.analyzer import DependencyAnalyzer
from .core.log import log_info, log_warning
from .core.model import Status
from .core.recycle_bin import RecycleBin
from .core.texts import kind_label, tr
from .core.watcher import ProjectWatcher

PLUGIN_DIR = os.path.dirname(__file__)
MENU_NAME = "&Biztonságos rétegtörlés"


class SafeLayerRemoverPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.translator: Optional[QTranslator] = None
        self.action: Optional[QAction] = None
        self.tree_action: Optional[QAction] = None
        self.lock_action: Optional[QAction] = None
        self.dock = None
        self.analyzer: Optional[DependencyAnalyzer] = None
        self.recycle_bin: Optional[RecycleBin] = None
        self.watcher: Optional[ProjectWatcher] = None
        self._indicators: dict = {}   # (layer id, kind) -> (tree node, indicator, tooltip)
        self._clearing = False
        self._project_conns: list = []
        self._load_translation()

    # ------------------------------------------------------------------
    def _load_translation(self) -> None:
        locale = (QSettings().value("locale/userLocale", QLocale.system().name()) or "hu")[:2]
        path = os.path.join(PLUGIN_DIR, "i18n", f"safe_layer_remover_{locale}.qm")
        if os.path.exists(path):
            self.translator = QTranslator()
            if self.translator.load(path):
                QCoreApplication.installTranslator(self.translator)

    def initGui(self) -> None:  # noqa: N802 - QGIS API
        icon = QIcon(os.path.join(PLUGIN_DIR, "icons", "icon.png"))
        self.action = QAction(icon, tr("Biztonságos rétegtörlés"), self.iface.mainWindow())
        self.action.setObjectName("SafeLayerRemoverAction")
        self.action.setCheckable(True)
        self.action.setToolTip(tr("Rétegek biztonságos törlése a függőségek ellenőrzésével"))
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(tr(MENU_NAME), self.action)
        try:
            self.iface.layerMenu().addAction(self.action)
        except Exception:  # noqa: BLE001
            pass

        # core objects
        project = QgsProject.instance()
        self.analyzer = DependencyAnalyzer(iface=self.iface)
        self.recycle_bin = RecycleBin()
        self.recycle_bin.max_entries = plugin_settings.get_int("undo_max_entries")
        self.recycle_bin.eager_theme_cache = lambda: plugin_settings.get("undo_all_removals")
        self.recycle_bin.start_tracking()
        self.watcher = ProjectWatcher()
        self.watcher.layerDirty.connect(self.analyzer.invalidate)
        self.watcher.layerGone.connect(self.analyzer.forget)
        self.watcher.dependenciesChanged.connect(self.analyzer.dependencies_maybe_changed)
        self.watcher.projectReset.connect(self.analyzer.mark_stale)

        from .ui.dock import SafeLayerRemoverWindow
        # a separate (non dockable) window owned by the QGIS main window
        self.dock = SafeLayerRemoverWindow(self.iface, self.analyzer, self.recycle_bin, self.iface.mainWindow())
        self.dock.setWindowIcon(icon)
        self.dock.hide()
        self.dock.visibilityChanged.connect(self._on_dock_visibility)
        self.dock.analysisUpdated.connect(self._update_indicators)
        self.watcher.changed.connect(self._on_project_changed)
        self.watcher.treeVisibilityChanged.connect(self._on_tree_visibility_changed)
        self._bg_timer = QTimer()
        self._bg_timer.setSingleShot(True)
        self._bg_timer.setInterval(1500)
        self._bg_timer.timeout.connect(self._background_analysis)
        self.watcher.start()

        # project level hooks
        self._connect(project.cleared, self._on_project_reset)
        self._connect(project.readProject, self._on_project_reset)
        if hasattr(project, "aboutToBeCleared"):
            self._connect(project.aboutToBeCleared, self._on_about_to_clear)
        self._connect(project.layersWillBeRemoved, self._on_layers_will_be_removed)

        # "Safe remove" in the layer tree context menu, for every layer type
        self.tree_action = QAction(icon, tr("Biztonságos törlés…"), self.iface.mainWindow())
        self.tree_action.triggered.connect(self._remove_selected_from_layer_tree)
        from .ui.styles import status_icon
        self.lock_action = QAction(status_icon(Status.LOCKED), tr("Törlés elleni zárolás be/ki"),
                                   self.iface.mainWindow())
        self.lock_action.setToolTip(tr("A kijelölt rétegek személyes zárolása vagy feloldása (Biztonságos rétegtörlés)"))
        self.lock_action.triggered.connect(self._toggle_lock_from_layer_tree)
        for ltype in self._layer_types():
            for act in (self.tree_action, self.lock_action):
                try:
                    self.iface.addCustomActionForLayerType(act, "", ltype, True)
                except Exception:  # noqa: BLE001
                    pass

        # initial indicators (cheap: analysis runs only when the project has layers)
        QTimer.singleShot(1500, self._background_analysis)

    def unload(self) -> None:
        if getattr(self, "_bg_timer", None) is not None:
            self._bg_timer.stop()
        self._remove_indicators()
        for act in (self.tree_action, getattr(self, "lock_action", None)):
            if act is not None:
                try:
                    self.iface.removeCustomActionForLayerType(act)
                except Exception:  # noqa: BLE001
                    pass
        for conn in self._project_conns:
            try:
                QObject.disconnect(conn)
            except (TypeError, RuntimeError):
                pass
        self._project_conns.clear()
        if self.watcher is not None:
            self.watcher.stop()
        if self.dock is not None:
            self.dock.save_window_state()
            self.dock.hide()
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu(tr(MENU_NAME), self.action)
            self.iface.removeToolBarIcon(self.action)
            try:
                self.iface.layerMenu().removeAction(self.action)
            except Exception:  # noqa: BLE001
                pass
            self.action.deleteLater()
        if self.recycle_bin is not None:
            self.recycle_bin.stop_tracking()
            self.recycle_bin.clear()
        if self.translator is not None:
            QCoreApplication.removeTranslator(self.translator)

    # ------------------------------------------------------------------
    def _connect(self, signal, slot) -> None:
        self._project_conns.append(signal.connect(slot))

    @staticmethod
    def _layer_types():
        names = ("Vector", "Raster", "Plugin", "Mesh", "VectorTile", "Annotation", "PointCloud", "Group",
                 "TiledScene")
        return [getattr(Qgis.LayerType, n) for n in names if hasattr(Qgis.LayerType, n)]

    def _toggle_dock(self, checked: bool) -> None:
        if self.dock is None:
            return
        if checked:
            self.dock.show_window()
        else:
            self.dock.hide()

    def _on_dock_visibility(self, visible: bool) -> None:
        if self.action is not None and self.action.isChecked() != visible:
            self.action.blockSignals(True)
            self.action.setChecked(visible)
            self.action.blockSignals(False)

    def _on_project_changed(self) -> None:
        if self.dock is None:
            return
        if self.dock.isVisible():
            self.dock.refresh()
        else:
            self.dock.schedule_refresh()
            if plugin_settings.get("show_tree_indicators"):
                self._bg_timer.start()   # restarts while the project keeps changing

    def _on_tree_visibility_changed(self) -> None:
        if self.dock is None:
            return
        if self.dock.isVisible():
            self.dock.model.update_visibility(QgsProject.instance())
        else:
            self.dock.schedule_refresh()   # dimmed names are brought up to date when shown

    def _background_analysis(self) -> None:
        """Keep the layer tree indicators current even when the dock is hidden."""
        if self.analyzer is None or not plugin_settings.get("show_tree_indicators"):
            self._remove_indicators()
            return
        if self.dock is not None and self.dock.isVisible():
            return  # the dock refresh will update them
        if not QgsProject.instance().mapLayers():
            self._remove_indicators()
            return
        try:
            self._update_indicators()   # graph only: no full result needed for the markers
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Background analysis failed: {exc}")

    def _on_about_to_clear(self) -> None:
        self._clearing = True
        self._remove_indicators()

    def _on_project_reset(self, *args) -> None:
        self._clearing = False
        if self.recycle_bin is not None:
            self.recycle_bin.clear()
            self.recycle_bin.start_tracking()   # the map theme collection is recreated on clear
        if self.analyzer is not None:
            self.analyzer.invalidate()

    # ------------------------------------------------------------------ layer tree integration
    def _remove_selected_from_layer_tree(self) -> None:
        view = self.iface.layerTreeView()
        layers = view.selectedLayers() if view is not None else []
        if not layers:
            return
        if self.action is not None:
            self.action.setChecked(True)
        self.dock.show_window()
        self.dock.refresh()
        ids = [lyr.id() for lyr in layers]
        self.dock.model.set_checked(ids, True, exclusive=True)
        self.dock.jump_to(ids[0])
        self.dock.request_delete(ids)

    def _toggle_lock_from_layer_tree(self) -> None:
        view = self.iface.layerTreeView()
        layers = view.selectedLayers() if view is not None else []
        ids = [lyr.id() for lyr in layers]
        if not ids:
            return
        locked = locks.locked_ids()
        lock = not all(i in locked for i in ids)
        if self.dock is not None:
            self.dock.set_locked(ids, lock)   # also refreshes the analysis and the indicators
        else:
            locks.set_locked(ids, lock)

    def _remove_indicators(self) -> None:
        view = self.iface.layerTreeView() if self.iface is not None else None
        for node, indicator, _tip in self._indicators.values():
            try:
                if view is not None:
                    view.removeIndicator(node, indicator)
            except Exception:  # noqa: BLE001 - node may already be deleted
                pass
        self._indicators = {}

    def _desired_indicators(self, graph, user_locked) -> dict:
        """{(layer id, kind): tooltip} for the layers that need a marker."""
        project = QgsProject.instance()

        def name_of(lid):
            lyr = project.mapLayer(lid)
            return lyr.name() if lyr is not None else lid

        desired = {}
        for lid in user_locked:
            desired[(lid, "lock")] = tr("Biztonságos rétegtörlés: saját zárolás, a réteg nem törölhető.")
        for lid in graph.layer_ids:
            if not graph.has_dependents(lid):
                continue
            res = graph.evaluate({lid})
            if not res.victims:
                continue
            lines = [tr("Biztonságos rétegtörlés: más rétegek függenek ettől a rétegtől:")]
            for victim in res.victims.values():
                kinds = ", ".join(dict.fromkeys(kind_label(d.kind) for d in victim.chain[:1]))
                prefix = "" if victim.direct else tr("közvetve, ")
                lines.append(f"• {name_of(victim.layer_id)} ({prefix}{kinds})")
            desired[(lid, "link")] = "\n".join(lines)
        return desired

    def _update_indicators(self, analysis=None) -> None:
        """Bring the Layers panel markers in line with the analysis, touching only what changed."""
        if not plugin_settings.get("show_tree_indicators"):
            self._remove_indicators()
            return
        try:
            from qgis.gui import QgsLayerTreeViewIndicator
        except ImportError:
            return
        view = self.iface.layerTreeView()
        if view is None:
            return
        if analysis is not None:
            user_locked = {lid for lid, info in analysis.infos.items() if info.user_locked}
            desired = self._desired_indicators(analysis.graph, user_locked)
        else:
            graph, user_locked = self.analyzer.graph_snapshot()
            desired = self._desired_indicators(graph, user_locked)

        root = QgsProject.instance().layerTreeRoot()
        # drop markers that are no longer wanted, or whose node changed (layer moved / removed)
        for key, (node, indicator, tip) in list(self._indicators.items()):
            current = root.findLayer(key[0])
            if key not in desired or current is None or current is not node:
                try:
                    view.removeIndicator(node, indicator)
                except Exception:  # noqa: BLE001 - node may already be deleted
                    pass
                del self._indicators[key]
            elif desired[key] != tip:
                indicator.setToolTip(desired[key])
                self._indicators[key] = (node, indicator, desired[key])
        if any(kind == "link" for _lid, kind in desired):
            icon = QgsApplication.getThemeIcon("/mIconDependencies.svg")
            if icon.isNull():
                icon = QgsApplication.getThemeIcon("/mActionLink.svg")
            if icon.isNull():
                from .ui.styles import status_icon
                icon = status_icon(Status.BLOCKED)
        from .ui.styles import status_icon
        lock_icon = status_icon(Status.LOCKED)
        for key, tip in desired.items():
            if key in self._indicators:
                continue
            node = root.findLayer(key[0])
            if node is None:
                continue
            indicator = QgsLayerTreeViewIndicator(view)
            indicator.setIcon(lock_icon if key[1] == "lock" else icon)
            indicator.setToolTip(tip)
            view.addIndicator(node, indicator)
            self._indicators[key] = (node, indicator, tip)

    # ------------------------------------------------------------------ sentinel mode
    def _on_layers_will_be_removed(self, items) -> None:
        if self._clearing or self.dock is None or self.dock.own_removal:
            return
        sentinel = plugin_settings.get("sentinel_mode")
        undo_all = plugin_settings.get("undo_all_removals")
        if not sentinel and not undo_all:
            return
        ids = [i if isinstance(i, str) else i.id() for i in items]
        if not ids:
            return
        project = QgsProject.instance()
        names = {i: (project.mapLayer(i).name() if project.mapLayer(i) is not None else i) for i in ids}
        broken: list[str] = []
        locked_now = locks.locked_ids()
        user_locked = [i for i in ids if i in locked_now]
        if sentinel:
            try:
                # 1. the last collected dependencies, when nothing relevant changed since (instant)
                res = self.analyzer.evaluate_if_fresh(ids)
                # 2. otherwise a quick text check, and the full analysis only if it finds a reference
                if res is None and (user_locked or self.analyzer.may_be_referenced(ids)):
                    res = self.analyzer.run().evaluate(set(ids))
                if res is not None:
                    def name_of(lid):
                        lyr = project.mapLayer(lid)
                        return lyr.name() if lyr is not None else lid
                    for v in res.victims.values():
                        broken.append(f"{name_of(v.layer_id)} ({kind_label(v.chain[-1].kind)})")
                    for d in res.blocking_objects:
                        broken.append(f"{d.dependent_label} ({kind_label(d.kind)})")
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Sentinel analysis failed: {exc}")
        problem = bool(broken or user_locked)
        if not problem:
            # helper layers that other plugins add without showing them are not worth an undo copy
            ids = [i for i in ids if self.recycle_bin.was_in_layer_tree(i)]
            names = {i: names[i] for i in ids}
        if not ids or (not undo_all and not problem):
            return
        try:
            self.recycle_bin.max_entries = plugin_settings.get_int("undo_max_entries")
            entries = self.recycle_bin.store(
                ids, automatic=True, feature_limit=plugin_settings.get_int("undo_memory_feature_limit"))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot before removal failed: {exc}")
            entries = []
        removed = ", ".join(names[i] for i in ids)
        parts = []
        if broken:
            parts.append(tr("{r} törlése elrontotta: {b}.").format(r=removed, b=", ".join(broken)))
        if user_locked:
            parts.append(tr("Zárolt réteget töröltél: {l}.").format(l=", ".join(names[i] for i in user_locked)))
        if not parts:
            parts.append(tr("Eltávolítva: {r}.").format(r=removed))
        skipped = [e.name for e in entries if e.data_skipped]
        if skipped:
            parts.append(tr("Túl nagy memóriaréteg, az adatai nem állíthatók vissza: {l}.").format(l=", ".join(skipped)))
        text = " ".join(parts)
        batch = entries[0].batch_id if entries else None
        log_info(f"Removal snapshot: {text}")
        QTimer.singleShot(0, lambda: self._sentinel_message(text, batch, problem))

    def _undo_from_message(self, batch_id: str, widget) -> None:
        if self.dock is None:   # plugin unloaded / reloaded since the message was shown
            return
        self.dock._undo(batch_id, widget)  # noqa: SLF001

    def _sentinel_message(self, text: str, batch_id: Optional[str], problem: bool = True) -> None:
        bar = self.iface.messageBar()
        widget = bar.createMessage(tr("Biztonságos rétegtörlés"), text)
        if batch_id and self.dock is not None:
            button = QPushButton(tr("Visszaállítás") if problem else tr("Visszavonás"))
            button.clicked.connect(lambda: self._undo_from_message(batch_id, widget))
            widget.layout().addWidget(button)
        if problem:
            bar.pushWidget(widget, Qgis.MessageLevel.Warning, 0)       # stays until closed
        else:
            bar.pushWidget(widget, Qgis.MessageLevel.Info, 15)
