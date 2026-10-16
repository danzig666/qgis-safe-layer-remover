"""Main window of the plugin (a separate, non dockable window owned by the QGIS main window)."""

from __future__ import annotations

import html
import re
from typing import Optional

from qgis.core import Qgis, QgsApplication, QgsProject, QgsSettings, QgsVectorLayer
from qgis.PyQt.QtCore import QByteArray, QEvent, QModelIndex, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

try:  # Qt6
    from qgis.PyQt.QtGui import QShortcut
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QShortcut

from ..core import locks
from ..core.analyzer import AnalysisResult, DependencyAnalyzer
from ..core.log import log_warning
from ..core.model import Status
from ..core.recycle_bin import RecycleBin, RestoreResult, write_vector_copy
from ..core.remover import LayerRemover
from ..core.texts import status_label, tr
from .confirm_dialog import ConfirmDialog, default_memory_dir
from .delegates import BadgeDelegate, NameDelegate, StatusDelegate
from .detail_panel import DetailPanel
from .explain import layer_tooltip, set_summary
from .filter_proxy import LayerFilterProxy
from .recycle_bin_widget import RecycleBinWidget
from .settings_dialog import SettingsDialog
from .styles import danger_button_style, status_color, status_icon, text_color
from .tree_model import (
    COL_NAME,
    COL_STATUS,
    COL_TYPE,
    GROUP_KEY_ROLE,
    IS_GROUP_ROLE,
    LAYER_ID_ROLE,
    SUMMARY_ROLE,
    REMOVABLE_ROLE,
    USER_LOCKED_ROLE,
    LayerTreeModel,
)

_REMOVABLE = {Status.SAFE, Status.WARN}
UNTOUCHED = "untouched"   # status combo entry: layers whose removal affects nothing
_NOT_REMOVABLE = {Status.UNCERTAIN, Status.BLOCKED, Status.LOCKED, Status.ERROR}

# counter buttons: (statuses, label)
_COUNTERS = [
    ({Status.SAFE}, "törölhető"),
    ({Status.WARN}, "figyelmeztetés"),
    ({Status.UNCERTAIN}, "ellenőrizendő"),
    ({Status.BLOCKED}, "nem törölhető"),
    ({Status.LOCKED, Status.ERROR}, "zárolt / hibás"),
]


def _theme_icon(*names):
    for n in names:
        icon = QgsApplication.getThemeIcon(n)
        if not icon.isNull():
            return icon
    return QgsApplication.getThemeIcon(names[0])


GEOMETRY_KEY = "SafeLayerRemover/windowGeometry"
SPLITTER_KEY = "SafeLayerRemover/splitterState"
HEADER_KEY = "SafeLayerRemover/headerState"
HEADER_MANUAL_KEY = "SafeLayerRemover/headerResizedByUser"


class SafeLayerRemoverWindow(QWidget):
    analysisUpdated = pyqtSignal(object)   # AnalysisResult
    visibilityChanged = pyqtSignal(bool)

    def __init__(self, iface, analyzer: DependencyAnalyzer, recycle_bin: RecycleBin, parent=None):
        # a normal window that can be maximized and closed, but not minimized
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMaximizeButtonHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle(tr("Biztonságos rétegtörlés"))
        self.setObjectName("SafeLayerRemoverWindow")
        self._geometry_restored = False
        self.iface = iface
        self.analyzer = analyzer
        self.recycle_bin = recycle_bin
        self.remover = LayerRemover(analyzer, recycle_bin)
        self.analysis: Optional[AnalysisResult] = None
        self._pending = True
        self._expanded: dict[str, bool] = {}
        self._chain_root: Optional[str] = None
        self._last_duration = 0.0
        self.own_removal = False   # True while this dock removes layers (for the sentinel)

        self._build_ui()
        self._build_shortcuts()
        self.visibilityChanged.connect(self._on_visibility_changed)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        # search / filter row
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Keresés rétegnévre…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        row.addWidget(self.search, 1)

        self.type_button = QToolButton()
        self.type_button.setText(tr("Típus"))
        self.type_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.type_menu = QMenu(self.type_button)
        self.type_button.setMenu(self.type_menu)
        row.addWidget(self.type_button)

        self.layout_button = QToolButton()
        self.layout_button.setText(tr("Elrendezés"))
        self.layout_button.setToolTip(tr("Szűrés nyomtatási elrendezésen való megjelenésre"))
        self.layout_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.layout_menu = QMenu(self.layout_button)
        self.layout_button.setMenu(self.layout_menu)
        row.addWidget(self.layout_button)

        self.status_combo = QComboBox()
        self.status_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._status_filters = [
            (tr("Minden állapot"), None),
            (tr("Csak törölhetők"), _REMOVABLE),
            (tr("Csak nem törölhetők"), _NOT_REMOVABLE),
            (tr("Csak érintetlenek"), UNTOUCHED),
        ] + [(status_label(s), {s}) for s in Status]
        for label, _ in self._status_filters:
            self.status_combo.addItem(label)
        self.status_combo.currentIndexChanged.connect(self._on_status_combo)
        row.addWidget(self.status_combo)

        self.refresh_button = QToolButton()
        self.refresh_button.setIcon(_theme_icon("/mActionRefresh.svg", "/mActionDraw.svg"))
        self.refresh_button.setToolTip(tr("Frissítés (F5)"))
        self.refresh_button.clicked.connect(lambda: self.refresh(full=True))
        row.addWidget(self.refresh_button)

        self.more_button = QToolButton()
        self.more_button.setIcon(_theme_icon("/mActionOptions.svg", "/propertyicons/settings.svg"))
        self.more_button.setToolTip(tr("Beállítások és riport"))
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more = QMenu(self.more_button)
        more.addAction(tr("Beállítások…"), self.open_settings)
        more.addSeparator()
        more.addAction(tr("Riport exportálása HTML-be…"), lambda: self.export_report("html"))
        more.addAction(tr("Riport exportálása CSV-be…"), lambda: self.export_report("csv"))
        self.more_button.setMenu(more)
        row.addWidget(self.more_button)
        outer.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumHeight(4)
        self.progress.setTextVisible(False)
        self.progress.hide()
        outer.addWidget(self.progress)

        # counters
        counters = QHBoxLayout()
        counters.setSpacing(2)
        self.counter_buttons: list[tuple[set, QToolButton]] = []
        for statuses, _label in _COUNTERS:
            btn = QToolButton()
            btn.setCheckable(True)
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setIcon(status_icon(Status.LOCKED if Status.LOCKED in statuses else next(iter(statuses))))
            btn.setToolTip(tr("Kattints a szűréshez"))
            btn.clicked.connect(lambda checked, s=statuses, b=btn: self._on_counter(s, b, checked))
            counters.addWidget(btn)
            self.counter_buttons.append((statuses, btn))
        counters.addStretch(1)
        outer.addLayout(counters)

        self.error_banner = QLabel()
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        outer.addWidget(self.error_banner)

        # tabs
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        layers_tab = QWidget()
        lt = QVBoxLayout(layers_tab)
        lt.setContentsMargins(0, 4, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(True)

        self.model = LayerTreeModel(self)
        self.proxy = LayerFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.view = QTreeView()
        self.view.setModel(self.proxy)
        self.view.setUniformRowHeights(True)
        self.view.setAllColumnsShowFocus(True)
        # Ctrl / Shift click selects several rows (used by Lock / Unlock)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.setItemDelegateForColumn(COL_NAME, NameDelegate(self.view))
        self.view.setItemDelegateForColumn(COL_TYPE, BadgeDelegate(self.view))
        self.view.setItemDelegateForColumn(COL_STATUS, StatusDelegate(self.view))
        header = self.view.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.Interactive)
        self.view.setColumnWidth(COL_NAME, 210)
        self.view.setColumnWidth(COL_TYPE, 150)
        # the name column follows the window width until the user resizes it by hand
        self._name_width_manual = False
        self._auto_resizing = False
        header.sectionResized.connect(self._on_section_resized)
        self.view.viewport().installEventFilter(self)
        self.view.expanded.connect(lambda idx: self._remember_expanded(idx, True))
        self.view.collapsed.connect(lambda idx: self._remember_expanded(idx, False))
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._update_lock_buttons())
        self.model.checkedChanged.connect(self._update_selection_status)
        self.splitter.addWidget(self.view)

        self.detail = DetailPanel()
        self.detail.jumpRequested.connect(self.jump_to)
        self.detail.chainRequested.connect(self.select_chain)
        self.detail.locateRequested.connect(self.locate_in_layer_tree)
        self.detail.layoutRequested.connect(self.open_layout)
        self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        lt.addWidget(self.splitter, 1)

        self.selection_label = QLabel()
        self.selection_label.setWordWrap(True)
        lt.addWidget(self.selection_label)

        buttons = QHBoxLayout()
        self.select_all_button = QPushButton(tr("Összes érintetlen kijelölése"))
        self.select_all_button.setToolTip(tr("Bejelöli az összes réteget, amelynek törlése semmit nem érint: nem függ "
                                             "tőle réteg, nincs figyelmeztetése, és nem szerepel elrendezésen vagy "
                                             "térképtémában."))
        self.select_all_button.clicked.connect(self.model.check_all_untouched)
        self.clear_button = QPushButton(tr("Kijelölés törlése"))
        self.clear_button.clicked.connect(self._clear_checks)
        self.delete_button = QPushButton(tr("Kijelöltek törlése"))
        self.delete_button.setIcon(_theme_icon("/mActionRemoveLayer.svg", "/mActionDeleteSelected.svg"))
        self.delete_button.setStyleSheet(danger_button_style())
        self.delete_button.clicked.connect(self.delete_checked)
        buttons.addWidget(self.select_all_button)
        buttons.addWidget(self.clear_button)
        self.lock_button = QPushButton(status_icon(Status.LOCKED), tr("Zárolás"))
        self.lock_button.setToolTip(tr("A kijelölt sorok rétegeinek zárolása: nem lesznek törölhetők (Ctrl+L). "
                                       "Több sor Ctrl vagy Shift kattintással jelölhető ki."))
        self.lock_button.clicked.connect(lambda: self.set_locked(self.selected_layer_ids(), True))
        self.unlock_button = QPushButton(tr("Feloldás"))
        self.unlock_button.setToolTip(tr("A kijelölt sorok rétegeinek feloldása (Ctrl+L)."))
        self.unlock_button.clicked.connect(lambda: self.set_locked(self.selected_layer_ids(), False))
        buttons.addWidget(self.lock_button)
        buttons.addWidget(self.unlock_button)
        buttons.addStretch(1)
        buttons.addWidget(self.delete_button)
        lt.addLayout(buttons)

        note = QLabel(tr("A törlés csak a projektből távolítja el a réteget, az adatforrást nem érinti "
                         "(a memóriarétegek kivételével)."))
        note.setWordWrap(True)
        f = note.font()
        if f.pointSizeF() > 0:
            f.setPointSizeF(f.pointSizeF() * 0.9)
        note.setFont(f)
        note.setEnabled(False)  # dimmed, theme aware
        lt.addWidget(note)
        self.tabs.addTab(layers_tab, tr("Rétegek"))

        self.bin_widget = RecycleBinWidget(self.recycle_bin)
        self.bin_widget.restored.connect(self._on_restored)
        self.tabs.addTab(self.bin_widget, tr("Lomtár"))
        self.recycle_bin.changed.connect(self._update_bin_tab)
        self._update_bin_tab()

        wrapper = QVBoxLayout(self)
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.addWidget(root)
        self._update_selection_status()
        self._update_lock_buttons()

    def _build_shortcuts(self) -> None:
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for seq, slot in (
            (QKeySequence(Qt.Key.Key_Delete), self.delete_checked),
            (QKeySequence("Ctrl+F"), self._focus_search),
            (QKeySequence(Qt.Key.Key_F5), lambda: self.refresh(full=True)),
            (QKeySequence("Ctrl+L"), self.toggle_lock_selected),
        ):
            sc = QShortcut(seq, self)
            sc.setContext(ctx)
            sc.activated.connect(slot)

    # ------------------------------------------------------------------ window state
    def show_window(self) -> None:
        if not self._geometry_restored:
            self.restore_window_state()
        self.show()
        self.raise_()
        self.activateWindow()

    def restore_window_state(self) -> None:
        self._geometry_restored = True
        s = QgsSettings()
        geometry = s.value(GEOMETRY_KEY, None)
        restored = False
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            restored = self.restoreGeometry(geometry)
        if not restored:
            self.resize(720, 860)
            parent = self.parentWidget()
            if parent is not None:
                center = parent.frameGeometry().center()
                self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        splitter = s.value(SPLITTER_KEY, None)
        if isinstance(splitter, QByteArray) and not splitter.isEmpty():
            self.splitter.restoreState(splitter)
        header = s.value(HEADER_KEY, None)
        manual = str(s.value(HEADER_MANUAL_KEY, False)).lower() in ("true", "1")
        if manual and isinstance(header, QByteArray) and not header.isEmpty():
            self._auto_resizing = True
            try:
                self.view.header().restoreState(header)
            finally:
                self._auto_resizing = False
            self._name_width_manual = True

    def save_window_state(self) -> None:
        if not self._geometry_restored:
            return  # never shown: keep the stored state
        s = QgsSettings()
        s.setValue(GEOMETRY_KEY, self.saveGeometry())
        s.setValue(SPLITTER_KEY, self.splitter.saveState())
        s.setValue(HEADER_KEY, self.view.header().saveState())
        s.setValue(HEADER_MANUAL_KEY, bool(self._name_width_manual))

    # ------------------------------------------------------------------ column widths
    def _on_section_resized(self, index: int, _old: int, _new: int) -> None:
        if index == COL_NAME and not self._auto_resizing:
            self._name_width_manual = True

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if obj is self.view.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self.fit_name_column)
        return super().eventFilter(obj, event)

    def fit_name_column(self) -> None:
        """Give the name column the room it needs when the window is wide enough."""
        if self._name_width_manual:
            return
        available = self.view.viewport().width()
        if available <= 0:
            return
        wanted = max(210, self.view.sizeHintForColumn(COL_NAME) + 12)
        type_w = self.view.columnWidth(COL_TYPE)
        # keep at least ~260 px for the status column
        width = max(210, min(wanted, available - type_w - 260))
        if width != self.view.columnWidth(COL_NAME):
            self._auto_resizing = True
            try:
                self.view.setColumnWidth(COL_NAME, width)
            finally:
                self._auto_resizing = False

    def showEvent(self, event):  # noqa: N802 - Qt API
        if not self._geometry_restored:
            self.restore_window_state()
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def hideEvent(self, event):  # noqa: N802 - Qt API
        self.save_window_state()
        super().hideEvent(event)
        self.visibilityChanged.emit(False)

    def _focus_search(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.search.setFocus()
        self.search.selectAll()

    # ------------------------------------------------------------------ refresh
    def _on_visibility_changed(self, visible: bool) -> None:
        if visible and self._pending:
            QTimer.singleShot(0, self.refresh)

    def schedule_refresh(self) -> None:
        if self.isVisible():
            self.refresh()
        else:
            self._pending = True

    def refresh(self, full: bool = False) -> None:
        if full:
            self.analyzer.invalidate()
        self._pending = False
        current_id = self._current_layer_id()
        visible_before = self._visible_layer_ids()   # to move on to the next row if current one drops out
        checked = self.model.checked_ids()
        scroll = self.view.verticalScrollBar().value()
        slow = self._last_duration > 0.3
        if slow:
            self.progress.show()
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
        try:
            analysis = self.analyzer.run()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Analysis failed: {exc}")
            self._push(tr("Az elemzés sikertelen: {e}").format(e=exc), Qgis.MessageLevel.Critical)
            return
        finally:
            if slow:
                QApplication.restoreOverrideCursor()
                self.progress.hide()
        self._last_duration = analysis.duration
        self.analysis = analysis
        checked = {lid for lid in checked if lid in analysis.infos}
        if self._chain_root and self._chain_root not in analysis.infos:
            self._chain_root = None

        self.view.setUpdatesEnabled(False)
        try:
            self.model.rebuild(QgsProject.instance(), analysis, checked)
            self._restore_expansion()
            self._rebuild_type_menu()
            self._rebuild_layout_menu()
            self._update_counters()
            self._update_error_banner()
            # keep the active filters; if the current layer no longer passes them (e.g. it was just
            # locked while "Untouched only" is on), select the next visible row instead
            if not (current_id and current_id in analysis.infos
                    and self.jump_to(current_id, scroll=False, reveal=False)):
                if not self._select_next_visible(current_id, visible_before):
                    self.detail.clear()
            self.view.verticalScrollBar().setValue(scroll)
        finally:
            self.view.setUpdatesEnabled(True)
        self._update_selection_status()
        self._update_lock_buttons()
        self.fit_name_column()
        self.analysisUpdated.emit(analysis)

    def _restore_expansion(self) -> None:
        if self.proxy.is_active():
            self.view.expandAll()
            return

        def walk(parent: QModelIndex):
            for row in range(self.proxy.rowCount(parent)):
                idx = self.proxy.index(row, 0, parent)
                if idx.data(IS_GROUP_ROLE):
                    key = idx.data(GROUP_KEY_ROLE)
                    state = self._expanded.get(key, self.model.group_expanded.get(key, True))
                    self.view.setExpanded(idx, state)
                    walk(idx)

        walk(QModelIndex())

    def _remember_expanded(self, idx: QModelIndex, state: bool) -> None:
        if self.proxy.is_active():
            return
        key = idx.data(GROUP_KEY_ROLE)
        if key:
            self._expanded[key] = state

    def _update_counters(self) -> None:
        counts = self.analysis.counts() if self.analysis else {s: 0 for s in Status}
        active = self.proxy.statuses
        for (statuses, label), (_s, btn) in zip(_COUNTERS, self.counter_buttons):
            n = sum(counts.get(s, 0) for s in statuses)
            btn.setText(f"{n} {tr(label)}")
            btn.setVisible(n > 0 or statuses == {Status.SAFE})
            btn.setChecked(active is not None and set(active) == set(statuses))
        n_untouched = sum(1 for info in self.analysis.infos.values() if info.untouched) if self.analysis else 0
        for i, (_label, value) in enumerate(self._status_filters):
            if value == UNTOUCHED:
                self.status_combo.setItemText(i, tr("Csak érintetlenek ({n})").format(n=n_untouched))
                self.status_combo.setItemData(
                    i, tr("Csak azok a rétegek, amelyek törlése semmit nem érint: nem függ tőlük réteg, "
                          "nincs figyelmeztetésük, és nem szerepelnek elrendezésen vagy térképtémában."),
                    Qt.ItemDataRole.ToolTipRole)

    def _update_error_banner(self) -> None:
        a = self.analysis
        if a is not None and a.global_errors:
            color = text_color(status_color(Status.ERROR)).name()
            self.error_banner.setText(
                f"<span style='color:{color}'><b>{html.escape(tr('Az elemzés hibára futott, ezért egyetlen réteg sem törölhető.'))}</b>"
                f"<br>{html.escape('; '.join(a.global_errors))}</span>")
            self.error_banner.show()
        else:
            self.error_banner.hide()

    def _rebuild_type_menu(self) -> None:
        if not self.analysis:
            return
        previous = self.proxy._types  # noqa: SLF001 - own proxy
        types = sorted({info.badges[0].text for info in self.analysis.infos.values() if info.badges})
        self.type_menu.clear()
        all_action = self.type_menu.addAction(tr("Minden típus"))
        all_action.triggered.connect(lambda: self._set_types(None))
        self.type_menu.addSeparator()
        for t in types:
            act = self.type_menu.addAction(t)
            act.setCheckable(True)
            act.setChecked(previous is None or t in previous)
            act.toggled.connect(self._on_type_toggled)
        self.type_button.setText(tr("Típus") if previous is None else tr("Típus ({n})").format(n=len(previous)))

    def _rebuild_layout_menu(self) -> None:
        self.layout_menu.clear()
        names = sorted({d.object_ref for info in (self.analysis.infos.values() if self.analysis else [])
                        for d in info.layout_uses if d.object_ref})
        all_layouts = [lay.name() for lay in QgsProject.instance().layoutManager().layouts()]
        current = self.proxy._layout  # noqa: SLF001 - own proxy
        if current and current not in all_layouts:
            current = None
            self.proxy.set_layout(None)
        self.layout_menu.addAction(tr("Minden réteg"), lambda: self._set_layout_filter(None))
        act = self.layout_menu.addAction(tr("Bármely elrendezésen szereplők"), lambda: self._set_layout_filter(""))
        act.setEnabled(bool(names))
        if all_layouts:
            self.layout_menu.addSeparator()
        for name in sorted(all_layouts):
            a = self.layout_menu.addAction(name, lambda n=name: self._set_layout_filter(n))
            a.setCheckable(True)
            a.setChecked(current == name)
        self.layout_button.setEnabled(bool(all_layouts))
        self._update_layout_button(current)

    def _set_layout_filter(self, layout) -> None:
        self.proxy.set_layout(layout)
        self._update_layout_button(layout)
        self._restore_expansion()

    def _update_layout_button(self, layout) -> None:
        if layout is None:
            self.layout_button.setText(tr("Elrendezés"))
        elif layout == "":
            self.layout_button.setText(tr("Elrendezésen"))
        else:
            self.layout_button.setText(tr("Elr.: {n}").format(n=layout))

    def open_layout(self, name: str) -> None:
        layout = QgsProject.instance().layoutManager().layoutByName(name)
        if layout is None:
            self._push(tr("A(z) {n} elrendezés nem található.").format(n=name), Qgis.MessageLevel.Warning)
            return
        if self.iface is not None:
            self.iface.openLayoutDesigner(layout)

    def _on_type_toggled(self, _checked: bool) -> None:
        actions = [a for a in self.type_menu.actions() if a.isCheckable()]
        selected = {a.text() for a in actions if a.isChecked()}
        self._set_types(None if len(selected) == len(actions) else selected)

    def _set_types(self, types) -> None:
        self.proxy.set_types(types)
        if types is None:
            for a in self.type_menu.actions():
                if a.isCheckable():
                    a.blockSignals(True)
                    a.setChecked(True)
                    a.blockSignals(False)
        self.type_button.setText(tr("Típus") if types is None else tr("Típus ({n})").format(n=len(types)))
        self._restore_expansion()

    def _on_search(self, text: str) -> None:
        self.proxy.set_text(text)
        self._restore_expansion()

    def _on_status_combo(self, index: int) -> None:
        value = self._status_filters[index][1]
        self.proxy.set_untouched(value == UNTOUCHED)
        self.proxy.set_statuses(None if value == UNTOUCHED else value)
        self._update_counters()
        self._restore_expansion()

    def _on_counter(self, statuses: set, button: QToolButton, checked: bool) -> None:
        target = statuses if checked else None
        idx = 0
        for i, (_label, st) in enumerate(self._status_filters):
            if isinstance(st, set) and target is not None and set(st) == set(target):
                idx = i
                break
        if target is not None and idx == 0:
            # combined counter (locked + error) has no combo entry
            self.status_combo.blockSignals(True)
            self.status_combo.setCurrentIndex(0)
            self.status_combo.blockSignals(False)
            self.proxy.set_untouched(False)
            self.proxy.set_statuses(target)
            self._update_counters()
            self._restore_expansion()
        else:
            self.status_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------ selection / details
    def _current_layer_id(self) -> Optional[str]:
        idx = self.view.currentIndex()
        if not idx.isValid():
            return None
        return self.proxy.index(idx.row(), COL_NAME, idx.parent()).data(LAYER_ID_ROLE)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid() or self.analysis is None:
            self.detail.clear()
            return
        name_idx = self.proxy.index(current.row(), COL_NAME, current.parent())
        if name_idx.data(IS_GROUP_ROLE):
            summary = self.proxy.index(current.row(), COL_STATUS, current.parent()).data(SUMMARY_ROLE) or ""
            self.detail.show_group(name_idx.data(), summary)
            return
        lid = name_idx.data(LAYER_ID_ROLE)
        info = self.analysis.infos.get(lid)
        if info is None:
            self.detail.clear()
            return
        self.detail.show_layer(info, self.analysis, self.analysis.settings.get("show_info_items", False))
        if self.analysis.settings.get("sync_selection", True):
            self._sync_layer_tree(lid)

    def _sync_layer_tree(self, layer_id: str) -> None:
        if self.iface is None:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        try:
            view = self.iface.layerTreeView()
            if layer is not None and view is not None and view.currentLayer() is not layer:
                view.setCurrentLayer(layer)
        except Exception:  # noqa: BLE001
            pass

    def _visible_layer_ids(self) -> list[str]:
        """Layer ids in the order they are shown (filtered, groups flattened)."""
        out: list[str] = []

        def walk(parent: QModelIndex):
            for row in range(self.proxy.rowCount(parent)):
                idx = self.proxy.index(row, COL_NAME, parent)
                lid = idx.data(LAYER_ID_ROLE)
                if lid:
                    out.append(lid)
                walk(idx)

        walk(QModelIndex())
        return out

    def _select_next_visible(self, previous_id: Optional[str], order_before: list[str]) -> bool:
        """After a refresh: select the row that followed `previous_id` (or the one before it)."""
        if not previous_id or previous_id not in order_before:
            return False
        visible = set(self._visible_layer_ids())
        pos = order_before.index(previous_id)
        candidates = order_before[pos + 1:] + list(reversed(order_before[:pos]))
        for lid in candidates:
            if lid in visible and self.jump_to(lid, scroll=False, reveal=False):
                return True
        return False

    def jump_to(self, layer_id: str, scroll: bool = True, reveal: bool = True) -> bool:
        """Select a layer row. With reveal=False a layer hidden by the filters is not shown (returns False)."""
        src = self.model.index_for_layer(layer_id)
        if not src.isValid():
            return False
        idx = self.proxy.mapFromSource(src)
        if not idx.isValid():
            if not reveal:
                return False
            # hidden by a filter: reset the filters
            self.search.clear()
            self.status_combo.setCurrentIndex(0)
            self._set_types(None)
            self._set_layout_filter(None)
            idx = self.proxy.mapFromSource(src)
        if not idx.isValid():
            return False
        parent = idx.parent()
        while parent.isValid():
            self.view.setExpanded(parent, True)
            parent = parent.parent()
        self.view.setCurrentIndex(idx)
        if scroll:
            self.view.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
        return True

    def locate_in_layer_tree(self, layer_id: str) -> None:
        if self.iface is None:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return
        try:
            self.iface.layerTreeView().setCurrentLayer(layer)
            self.iface.setActiveLayer(layer)
            node = QgsProject.instance().layerTreeRoot().findLayer(layer_id)
            parent = node.parent() if node is not None else None
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
        except Exception:  # noqa: BLE001
            pass

    def select_chain(self, layer_id: str) -> None:
        if self.analysis is None or layer_id not in self.analysis.infos:
            return
        closure = self.analysis.infos[layer_id].closure
        self._chain_root = layer_id
        self.model.set_checked(closure, True)
        res = self.analysis.evaluate(closure)
        if not res.is_safe(self.analysis.settings.get("uncertain_is_blocked", True)):
            self._push(tr("A lánc nem törölhető: zárolt vagy hibás réteg is van benne."), Qgis.MessageLevel.Warning)

    def _clear_checks(self) -> None:
        self._chain_root = None
        self.model.clear_checks()

    def _update_selection_status(self) -> None:
        ids = self.model.checked_ids()
        if self.analysis is None:
            self.selection_label.setText("")
            self.delete_button.setEnabled(False)
            return
        uib = self.analysis.settings.get("uncertain_is_blocked", True)
        res = self.analysis.evaluate(ids)
        safe, text = set_summary(res, self.analysis, uib)
        if not ids:
            color = self.palette().color(self.foregroundRole()).name()
        else:
            color = text_color(status_color(Status.SAFE if safe else Status.BLOCKED)).name()
        self.selection_label.setText(f"<span style='color:{color}'>{html.escape(text)}</span>")
        self.delete_button.setEnabled(bool(ids) and safe)
        self.delete_button.setText(tr("Kijelöltek törlése ({n})").format(n=len(ids)) if ids
                                   else tr("Kijelöltek törlése"))

    # ------------------------------------------------------------------ context menu
    def _context_menu(self, pos) -> None:
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        if self.analysis is None:
            return
        # act on the whole selection when the clicked row is part of it, else on the clicked row only
        if not self.view.selectionModel().isRowSelected(idx.row(), idx.parent()):
            self.view.setCurrentIndex(idx)
        targets = self.selected_layer_ids()
        menu = QMenu(self.view)
        self._add_mark_actions(menu, targets)
        self._add_lock_actions(menu, targets)
        lid = self.proxy.index(idx.row(), COL_NAME, idx.parent()).data(LAYER_ID_ROLE)
        if not lid:
            if not menu.isEmpty():
                menu.exec(self.view.viewport().mapToGlobal(pos))
            return
        menu.addSeparator()
        info = self.analysis.infos.get(lid)
        layer = QgsProject.instance().mapLayer(lid)
        menu.addAction(tr("Ugrás a rétegkezelőben"), lambda: self.locate_in_layer_tree(lid))
        if self.iface is not None and layer is not None:
            menu.addAction(tr("Réteg tulajdonságai…"), lambda: self.iface.showLayerProperties(layer))
        if info is not None and len(info.closure) > 1:
            menu.addAction(tr("Lánc kijelölése ({n} réteg)").format(n=len(info.closure)), lambda: self.select_chain(lid))
        if info is not None and info.layout_uses:
            names = sorted({d.object_ref for d in info.layout_uses if d.object_ref})
            sub = menu.addMenu(tr("Elrendezések, amelyeken szerepel ({n})").format(n=len(names)))
            for name in names:
                details = sorted({d.detail for d in info.layout_uses if d.object_ref == name})
                act = sub.addAction(name, lambda n=name: self.open_layout(n))
                act.setToolTip("; ".join(details))
        menu.addAction(tr("Indoklás másolása"), lambda: self._copy_reason(lid))
        if isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory":
            menu.addAction(tr("Memóriaréteg mentése…"), lambda: self._save_memory_layer(lid))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ personal locks
    def selected_layer_ids(self) -> list[str]:
        """Layer ids of the selected rows; a selected group stands for every layer below it."""
        ids: list[str] = []
        for proxy_idx in self.view.selectionModel().selectedRows(COL_NAME):
            src = self.proxy.mapToSource(proxy_idx)
            item = self.model.itemFromIndex(src)
            if item is None:
                continue
            if item.data(IS_GROUP_ROLE):
                ids.extend(child.data(LAYER_ID_ROLE) for child in self.model._descendant_layers(item))  # noqa: SLF001
            elif item.data(LAYER_ID_ROLE):
                ids.append(item.data(LAYER_ID_ROLE))
        return list(dict.fromkeys(i for i in ids if i))

    def _add_mark_actions(self, menu: QMenu, layer_ids: list[str]) -> None:
        if not layer_ids:
            return
        checked = self.model.checked_ids()
        to_mark = [i for i in layer_ids if i not in checked]
        to_unmark = [i for i in layer_ids if i in checked]
        if to_mark:
            menu.addAction(tr("Megjelölés törlésre ({n} réteg)").format(n=len(to_mark)),
                           lambda: self.mark_for_removal(to_mark, True))
        if to_unmark:
            menu.addAction(tr("Törlési jelölés levétele ({n} réteg)").format(n=len(to_unmark)),
                           lambda: self.mark_for_removal(to_unmark, False))
        if to_mark or to_unmark:
            menu.addSeparator()

    def mark_for_removal(self, layer_ids, mark: bool) -> None:
        """Check / uncheck several layers at once. Layers that cannot be removed are skipped when marking."""
        ids = list(layer_ids)
        if not mark:
            self.model.set_checked(ids, False)
            return
        removable = []
        for lid in ids:
            item = self.model.layer_item(lid)
            if item is not None and item.data(REMOVABLE_ROLE):
                removable.append(lid)
        if removable:
            self.model.set_checked(removable, True)
        skipped = len(ids) - len(removable)
        if skipped:
            self._push(tr("{n} réteg nem jelölhető meg, mert nem törölhető (a függő rétegekkel együtt a "
                          "Lánc kijelölése segít).").format(n=skipped), Qgis.MessageLevel.Info)

    def _add_lock_actions(self, menu: QMenu, layer_ids: list[str]) -> None:
        if not layer_ids:
            return
        locked = locks.locked_ids()
        to_lock = [i for i in layer_ids if i not in locked]
        to_unlock = [i for i in layer_ids if i in locked]
        if to_lock:
            act = menu.addAction(status_icon(Status.LOCKED), tr("Zárolás ({n} réteg)").format(n=len(to_lock)),
                                 lambda: self.set_locked(to_lock, True))
            act.setToolTip(tr("Nem lesznek törölhetők, amíg fel nem oldod őket."))
        if to_unlock:
            menu.addAction(tr("Feloldás ({n} réteg)").format(n=len(to_unlock)), lambda: self.set_locked(to_unlock, False))

    def toggle_lock_selected(self) -> None:
        ids = self.selected_layer_ids()
        if not ids:
            return
        locked = locks.locked_ids()
        self.set_locked(ids, not all(i in locked for i in ids))

    def set_locked(self, layer_ids, locked: bool) -> None:
        ids = list(layer_ids)
        if not ids:
            return
        locks.set_locked(ids, locked)
        if locked:
            # a locked layer cannot stay in the removal selection
            self.model.set_checked(ids, False)
        n = len(ids)
        self._push(tr("{n} réteg zárolva.").format(n=n) if locked else tr("{n} réteg feloldva.").format(n=n),
                   Qgis.MessageLevel.Info)
        self.refresh()

    def _update_lock_buttons(self) -> None:
        ids = self.selected_layer_ids()
        locked = locks.locked_ids()
        self.lock_button.setEnabled(any(i not in locked for i in ids))
        self.unlock_button.setEnabled(any(i in locked for i in ids))

    def _copy_reason(self, layer_id: str) -> None:
        info = self.analysis.infos.get(layer_id) if self.analysis else None
        if info is None:
            return
        rich = layer_tooltip(info, self.analysis)
        plain = html.unescape(re.sub(r"<[^>]+>", "", rich.replace("<br>", "\n")))
        QApplication.clipboard().setText(plain)

    def _save_memory_layer(self, layer_id: str) -> None:
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer):
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Memóriaréteg mentése"),
                                              f"{default_memory_dir()}/{layer.name()}.gpkg", "GeoPackage (*.gpkg)")
        if not path:
            return
        ok, msg = write_vector_copy(layer, path, layer.name())
        if ok:
            self._push(tr("A(z) {n} réteg adatai elmentve: {p}").format(n=layer.name(), p=path), Qgis.MessageLevel.Success)
        else:
            self._push(tr("A mentés sikertelen: {m}").format(m=msg), Qgis.MessageLevel.Critical)

    # ------------------------------------------------------------------ deletion
    def delete_checked(self) -> None:
        ids = sorted(self.model.checked_ids())
        if not ids:
            return
        chain_root = None
        if self._chain_root and self.analysis and self._chain_root in ids:
            closure = self.analysis.infos.get(self._chain_root)
            if closure is not None and closure.closure <= set(ids):
                chain_root = self._chain_root
        self.request_delete(ids, chain_root)

    def request_delete(self, ids: list[str], chain_root: Optional[str] = None) -> bool:
        """Full removal flow: re-check, unsaved edits, confirmation, recycle bin, removal, undo."""
        ok, res, analysis = self.remover.recheck(ids)
        if not ok:
            self.refresh()
            self.model.set_checked(ids, True)
            _safe, text = set_summary(res, analysis, analysis.settings.get("uncertain_is_blocked", True))
            if self.analysis is not None and self.analysis is not analysis and set(ids) <= set(analysis.infos):
                msg = tr("A projekt időközben megváltozott, a kijelölés már nem biztonságos.")
            else:
                msg = tr("A kijelölés nem törölhető biztonságosan.")
            self._push(f"{msg} {text}", Qgis.MessageLevel.Warning)
            return False

        if not self._handle_unsaved_edits(ids):
            return False
        # unsaved edits may have changed the warnings: evaluate again
        ok, res, analysis = self.remover.recheck(ids)
        if not ok:
            self._push(tr("A projekt időközben megváltozott, a kijelölés már nem biztonságos."), Qgis.MessageLevel.Warning)
            self.refresh()
            return False

        dlg = ConfirmDialog(ids, res, analysis, chain_root, self)
        if dlg.exec() != ConfirmDialog.DialogCode.Accepted:
            return False
        options = dlg.options()

        self.own_removal = True
        try:
            outcome = self.remover.remove(ids, options)
        except Exception as exc:  # noqa: BLE001
            self._push(tr("A törlés sikertelen: {e}").format(e=exc), Qgis.MessageLevel.Critical)
            return False
        finally:
            self.own_removal = False

        if outcome.aborted:
            self._push(outcome.message, Qgis.MessageLevel.Warning)
            self.refresh()
            return False
        for err in outcome.errors:
            self._push(err, Qgis.MessageLevel.Critical)
        self._chain_root = None
        text = outcome.message
        if outcome.saved_files:
            text += " " + tr("Memóriaréteg mentve: {f}").format(f=", ".join(outcome.saved_files))
        self._push_undo(text, outcome.batch_id if outcome.entries else None)
        self.refresh()
        return True

    def _handle_unsaved_edits(self, ids: list[str]) -> bool:
        project = QgsProject.instance()
        for lid in ids:
            layer = project.mapLayer(lid)
            if not isinstance(layer, QgsVectorLayer) or not (layer.isEditable() and layer.isModified()):
                continue
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(tr("Mentetlen szerkesztések"))
            box.setText(tr("A(z) {n} rétegen mentetlen szerkesztések vannak.").format(n=layer.name()))
            box.setInformativeText(tr("Mentsük a módosításokat a törlés előtt, vagy vessük el őket?"))
            box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                   | QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            box.button(QMessageBox.StandardButton.Save).setText(tr("Módosítások mentése"))
            box.button(QMessageBox.StandardButton.Discard).setText(tr("Elvetés"))
            box.button(QMessageBox.StandardButton.Cancel).setText(tr("Mégse"))
            answer = box.exec()
            if answer == QMessageBox.StandardButton.Save:
                if not layer.commitChanges():
                    self._push(tr("A(z) {n} réteg módosításai nem menthetők: {e}")
                               .format(n=layer.name(), e="; ".join(layer.commitErrors())), Qgis.MessageLevel.Critical)
                    return False
            elif answer == QMessageBox.StandardButton.Discard:
                layer.rollBack()
            else:
                return False
        return True

    def _undo(self, batch_id: str, widget=None) -> None:
        entries = self.recycle_bin.batch(batch_id)
        if not entries:
            self._push(tr("A visszavonás már nem lehetséges (a lomtár tételei nem érhetők el)."), Qgis.MessageLevel.Warning)
            return
        result = self.recycle_bin.restore([e.entry_id for e in entries])
        if widget is not None and self.iface is not None:
            try:
                self.iface.messageBar().popWidget(widget)
            except Exception:  # noqa: BLE001
                pass
        self._on_restored(result)

    def _on_restored(self, result: RestoreResult) -> None:
        for err in result.errors:
            self._push(tr("Visszaállítási hiba: {e}").format(e=err), Qgis.MessageLevel.Critical)
        if result.restored:
            text = tr("{n} réteg visszaállítva.").format(n=len(result.restored))
            if result.notes:
                text += " " + " ".join(result.notes)
            self._push(text, Qgis.MessageLevel.Success)
        self.refresh()

    def _update_bin_tab(self) -> None:
        n = len(self.recycle_bin)
        self.tabs.setTabText(1, tr("Lomtár ({n})").format(n=n) if n else tr("Lomtár"))

    # ------------------------------------------------------------------ misc
    def open_settings(self) -> None:
        if SettingsDialog(self).exec() == SettingsDialog.DialogCode.Accepted:
            from ..core import settings as plugin_settings
            self.recycle_bin.max_entries = plugin_settings.get_int("undo_max_entries")
            self.refresh(full=True)

    def export_report(self, fmt: str) -> None:
        if self.analysis is None:
            self.refresh()
        if self.analysis is None:
            return
        from ..core.report import export_csv, export_html
        filt = "HTML (*.html)" if fmt == "html" else "CSV (*.csv)"
        path, _ = QFileDialog.getSaveFileName(self, tr("Riport exportálása"),
                                              f"{default_memory_dir()}/retegfuggosegek.{fmt}", filt)
        if not path:
            return
        try:
            (export_html if fmt == "html" else export_csv)(self.analysis, path)
            self._push(tr("Riport elmentve: {p}").format(p=path), Qgis.MessageLevel.Success)
        except Exception as exc:  # noqa: BLE001
            self._push(tr("A riport mentése sikertelen: {e}").format(e=exc), Qgis.MessageLevel.Critical)

    def _push(self, text: str, level=Qgis.MessageLevel.Info) -> None:
        if self.iface is not None:
            self.iface.messageBar().pushMessage(tr("Biztonságos rétegtörlés"), text, level, 8)
        else:
            log_warning(text)

    def _push_undo(self, text: str, batch_id: Optional[str]) -> None:
        if self.iface is None:
            return
        bar = self.iface.messageBar()
        widget = bar.createMessage(tr("Biztonságos rétegtörlés"), text)
        if batch_id:
            button = QPushButton(tr("Visszavonás"))
            button.clicked.connect(lambda: self._undo(batch_id, widget))
            widget.layout().addWidget(button)
        bar.pushWidget(widget, Qgis.MessageLevel.Success, 12)
