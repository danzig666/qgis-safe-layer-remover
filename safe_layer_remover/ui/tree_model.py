"""QStandardItemModel mirroring the QGIS layer tree, annotated with the analysis results."""

from __future__ import annotations

from typing import Optional

from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer, QgsProject
from qgis.PyQt.QtCore import QModelIndex, Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QStandardItem, QStandardItemModel

from ..core.analyzer import AnalysisResult
from ..core.classifier import group_icon, layer_icon, provider_mini_icon
from ..core.model import Status
from ..core.texts import status_label, tr
from .explain import layer_short_reason, layer_tooltip
from .styles import row_tint

LAYER_ID_ROLE = Qt.ItemDataRole.UserRole + 1
STATUS_ROLE = Qt.ItemDataRole.UserRole + 2
IS_GROUP_ROLE = Qt.ItemDataRole.UserRole + 3
BADGES_ROLE = Qt.ItemDataRole.UserRole + 4
SHORT_REASON_ROLE = Qt.ItemDataRole.UserRole + 5
VISIBLE_ROLE = Qt.ItemDataRole.UserRole + 6
SUMMARY_ROLE = Qt.ItemDataRole.UserRole + 7
GROUP_KEY_ROLE = Qt.ItemDataRole.UserRole + 8
REMOVABLE_ROLE = Qt.ItemDataRole.UserRole + 9
MINI_ICON_ROLE = Qt.ItemDataRole.UserRole + 10
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 11
LAYOUTS_ROLE = Qt.ItemDataRole.UserRole + 12
UNTOUCHED_ROLE = Qt.ItemDataRole.UserRole + 13
USER_LOCKED_ROLE = Qt.ItemDataRole.UserRole + 14

COL_NAME, COL_TYPE, COL_STATUS = 0, 1, 2

_CHECKED = Qt.CheckState.Checked
_UNCHECKED = Qt.CheckState.Unchecked
_PARTIAL = Qt.CheckState.PartiallyChecked


class LayerTreeModel(QStandardItemModel):
    checkedChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalHeaderLabels([tr("Név"), tr("Típus"), tr("Állapot")])
        self._guard = False
        self._layer_items: dict[str, QStandardItem] = {}
        self.group_expanded: dict[str, bool] = {}   # group key -> layer tree expanded state
        self.uncertain_is_blocked = True
        self.analysis: Optional[AnalysisResult] = None
        self.itemChanged.connect(self._on_item_changed)

    # ------------------------------------------------------------------
    def rebuild(self, project: QgsProject, analysis: AnalysisResult, checked: Optional[set] = None) -> None:
        checked = set(checked or ())
        self.analysis = analysis
        self.uncertain_is_blocked = analysis.settings.get("uncertain_is_blocked", True)
        self._guard = True
        self.blockSignals(True)
        try:
            self.removeRows(0, self.rowCount())
            self._layer_items.clear()
            self.group_expanded.clear()
            root = self.invisibleRootItem()
            self._add_children(project.layerTreeRoot(), root, analysis, checked, "")
            # layers that are in the project but not in the layer tree
            in_tree = set(self._layer_items)
            for lid, info in analysis.infos.items():
                if lid not in in_tree:
                    layer = project.mapLayer(lid)
                    root.appendRow(self._layer_row(layer, lid, analysis, checked, True))
            for row in range(root.rowCount()):
                self._update_group_state(root.child(row))
        finally:
            self.blockSignals(False)
            self._guard = False
        self.beginResetModel()
        self.endResetModel()

    def _add_children(self, group: QgsLayerTreeGroup, parent_item: QStandardItem, analysis, checked, key):
        for node in group.children():
            if isinstance(node, QgsLayerTreeGroup):
                gkey = f"{key}/{node.name()}"
                # make keys unique when sibling groups share a name
                n = 2
                base = gkey
                while gkey in self.group_expanded:
                    gkey = f"{base}#{n}"
                    n += 1
                self.group_expanded[gkey] = node.isExpanded()
                items = self._group_row(node, gkey)
                parent_item.appendRow(items)
                self._add_children(node, items[0], analysis, checked, gkey)
            elif isinstance(node, QgsLayerTreeLayer):
                lid = node.layerId()
                if lid in self._layer_items or lid not in analysis.infos:
                    continue
                parent_item.appendRow(self._layer_row(node.layer(), lid, analysis, checked, node.isVisible()))

    def _group_row(self, node: QgsLayerTreeGroup, gkey: str) -> list[QStandardItem]:
        name = QStandardItem(group_icon(), node.name())
        name.setData(True, IS_GROUP_ROLE)
        name.setData(gkey, GROUP_KEY_ROLE)
        name.setData(node.isVisible(), VISIBLE_ROLE)
        name.setData(node.name(), SEARCH_ROLE)
        name.setEditable(False)
        type_item = QStandardItem("")
        type_item.setEditable(False)
        type_item.setData(True, IS_GROUP_ROLE)
        status_item = QStandardItem("")
        status_item.setEditable(False)
        status_item.setData(True, IS_GROUP_ROLE)
        return [name, type_item, status_item]

    def _layer_row(self, layer, lid, analysis: AnalysisResult, checked: set, visible: bool) -> list[QStandardItem]:
        info = analysis.infos[lid]
        removable = info.is_removable(self.uncertain_is_blocked)
        name = QStandardItem(layer_icon(layer), info.name)
        name.setEditable(False)
        name.setData(lid, LAYER_ID_ROLE)
        name.setData(False, IS_GROUP_ROLE)
        name.setData(info.status.value, STATUS_ROLE)
        name.setData(visible, VISIBLE_ROLE)
        name.setData(removable, REMOVABLE_ROLE)
        name.setData(info.name, SEARCH_ROLE)
        name.setData(sorted({d.object_ref or d.dependent_label for d in info.layout_uses}), LAYOUTS_ROLE)
        name.setData(info.untouched, UNTOUCHED_ROLE)
        name.setData(info.user_locked, USER_LOCKED_ROLE)
        mini = provider_mini_icon(layer) if layer is not None else None
        if mini is not None:
            name.setData(mini, MINI_ICON_ROLE)
        is_checked = lid in checked
        name.setCheckable(True)
        name.setCheckState(_CHECKED if is_checked else _UNCHECKED)
        self._apply_checkable(name, removable or is_checked)
        tooltip = layer_tooltip(info, analysis)
        name.setToolTip(tooltip)

        type_item = QStandardItem(info.badges[0].text if info.badges else "")
        type_item.setEditable(False)
        type_item.setData(info.badges, BADGES_ROLE)
        type_item.setData(lid, LAYER_ID_ROLE)
        type_item.setData(False, IS_GROUP_ROLE)
        type_item.setToolTip("\n".join(b.text + (f": {b.tooltip}" if b.tooltip else "") for b in info.badges))

        status_item = QStandardItem(status_label(info.status))
        status_item.setEditable(False)
        status_item.setData(info.status.value, STATUS_ROLE)
        status_item.setData(layer_short_reason(info, analysis), SHORT_REASON_ROLE)
        status_item.setData(lid, LAYER_ID_ROLE)
        status_item.setData(False, IS_GROUP_ROLE)
        status_item.setToolTip(tooltip)

        if not removable:
            brush = QBrush(row_tint(info.status))
            for it in (name, type_item, status_item):
                it.setData(brush, Qt.ItemDataRole.BackgroundRole)
        self._layer_items[lid] = name
        return [name, type_item, status_item]

    @staticmethod
    def _apply_checkable(item: QStandardItem, enabled: bool) -> None:
        flags = item.flags()
        if enabled:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        else:
            flags &= ~Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)

    # ------------------------------------------------------------------
    def layer_item(self, layer_id: str) -> Optional[QStandardItem]:
        return self._layer_items.get(layer_id)

    def index_for_layer(self, layer_id: str) -> QModelIndex:
        item = self._layer_items.get(layer_id)
        return item.index() if item is not None else QModelIndex()

    def checked_ids(self) -> set[str]:
        return {lid for lid, item in self._layer_items.items() if item.checkState() == _CHECKED}

    def _descendant_layers(self, item: QStandardItem) -> list[QStandardItem]:
        out = []
        for row in range(item.rowCount()):
            child = item.child(row, 0)
            if child is None:
                continue
            if child.data(IS_GROUP_ROLE):
                out.extend(self._descendant_layers(child))
            else:
                out.append(child)
        return out

    def set_checked(self, layer_ids, checked: bool = True, exclusive: bool = False) -> None:
        """Programmatic check (e.g. chain selection). Allows checking otherwise blocked layers."""
        ids = set(layer_ids)
        self._guard = True
        try:
            for lid, item in self._layer_items.items():
                if lid in ids:
                    item.setCheckState(_CHECKED if checked else _UNCHECKED)
                    self._apply_checkable(item, bool(item.data(REMOVABLE_ROLE)) or checked)
                elif exclusive:
                    item.setCheckState(_UNCHECKED)
                    self._apply_checkable(item, bool(item.data(REMOVABLE_ROLE)))
            root = self.invisibleRootItem()
            for row in range(root.rowCount()):
                self._update_group_state(root.child(row))
        finally:
            self._guard = False
        self.checkedChanged.emit()

    def check_all_removable(self) -> None:
        ids = [lid for lid, item in self._layer_items.items() if item.data(REMOVABLE_ROLE)]
        self.set_checked(ids, True)

    def check_all_untouched(self) -> None:
        """Check every layer whose removal affects nothing at all."""
        ids = [lid for lid, item in self._layer_items.items() if item.data(UNTOUCHED_ROLE)]
        self.set_checked(ids, True)

    def clear_checks(self) -> None:
        self.set_checked([], True, exclusive=True)

    def _update_group_state(self, item: QStandardItem) -> tuple[int, int, int]:
        """Recompute check state and the "(x/y removable)" summary. Returns (checked, removable, total)."""
        if item is None or not item.data(IS_GROUP_ROLE):
            return (0, 0, 0)
        checked = removable = total = 0
        for row in range(item.rowCount()):
            child = item.child(row, 0)
            if child is None:
                continue
            if child.data(IS_GROUP_ROLE):
                c, r, t = self._update_group_state(child)
            else:
                c = 1 if child.checkState() == _CHECKED else 0
                r = 1 if child.data(REMOVABLE_ROLE) else 0
                t = 1
            checked += c
            removable += r
            total += t
        status_item = self._sibling(item, COL_STATUS)
        if status_item is not None:
            summary = tr("({r}/{t} törölhető)").format(r=removable, t=total) if total else tr("(üres csoport)")
            status_item.setData(summary, SUMMARY_ROLE)
            status_item.setText(summary)
        item.setCheckable(True)
        self._apply_checkable(item, removable > 0 or checked > 0)
        if checked == 0:
            item.setCheckState(_UNCHECKED)
        elif checked >= total:
            item.setCheckState(_CHECKED)
        else:
            item.setCheckState(_PARTIAL)
        return checked, removable, total

    def _sibling(self, item: QStandardItem, column: int) -> Optional[QStandardItem]:
        parent = item.parent() or self.invisibleRootItem()
        return parent.child(item.row(), column)

    def _on_item_changed(self, item: QStandardItem) -> None:
        if self._guard or item.column() != COL_NAME:
            return
        self._guard = True
        try:
            if item.data(IS_GROUP_ROLE):
                state = item.checkState()
                for child in self._descendant_layers(item):
                    if state == _CHECKED:
                        if child.data(REMOVABLE_ROLE):
                            child.setCheckState(_CHECKED)
                    else:
                        child.setCheckState(_UNCHECKED)
                        self._apply_checkable(child, bool(child.data(REMOVABLE_ROLE)))
            else:
                if item.checkState() != _CHECKED and not item.data(REMOVABLE_ROLE):
                    self._apply_checkable(item, False)
            root = self.invisibleRootItem()
            for row in range(root.rowCount()):
                self._update_group_state(root.child(row))
        finally:
            self._guard = False
        self.checkedChanged.emit()
