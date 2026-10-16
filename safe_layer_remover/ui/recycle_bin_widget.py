"""Recycle bin tab."""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.recycle_bin import RecycleBin, RestoreResult
from ..core.texts import tr

ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1


class RecycleBinWidget(QWidget):
    restored = pyqtSignal(object)   # RestoreResult

    def __init__(self, recycle_bin: RecycleBin, parent=None):
        super().__init__(parent)
        self.bin = recycle_bin
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)

        info = QLabel(tr("A törölt rétegek a munkamenet végéig (vagy másik projekt megnyitásáig) "
                         "visszaállíthatók, a memóriarétegek adataival együtt."))
        info.setWordWrap(True)
        layout.addWidget(info)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([tr("Név"), tr("Típus"), tr("Hely"), tr("Törlés ideje")])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.itemDoubleClicked.connect(lambda *_: self.restore_selected())
        layout.addWidget(self.tree, 1)

        row = QHBoxLayout()
        self.restore_button = QPushButton(tr("Visszaállítás"))
        self.restore_button.clicked.connect(self.restore_selected)
        self.discard_button = QPushButton(tr("Végleges törlés"))
        self.discard_button.clicked.connect(self.discard_selected)
        self.clear_button = QPushButton(tr("Lomtár ürítése"))
        self.clear_button.clicked.connect(self.clear_bin)
        row.addWidget(self.restore_button)
        row.addWidget(self.discard_button)
        row.addStretch(1)
        row.addWidget(self.clear_button)
        layout.addLayout(row)

        self.bin.changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        self.tree.clear()
        for entry in reversed(self.bin.entries):
            where = " / ".join(entry.group_path) if entry.group_path else tr("(gyökér)")
            item = QTreeWidgetItem([
                entry.name,
                ", ".join(b.text for b in entry.badges[:2]),
                where,
                entry.deleted_at.strftime("%H:%M:%S"),
            ])
            item.setData(0, ENTRY_ROLE, entry.entry_id)
            if entry.is_memory:
                item.setToolTip(0, tr("Memóriaréteg, {n} elemmel").format(n=entry.feature_count))
            self.tree.addTopLevelItem(item)
        for c in range(4):
            self.tree.resizeColumnToContents(c)
        self._update_buttons()

    def _selected_ids(self) -> list[str]:
        return [it.data(0, ENTRY_ROLE) for it in self.tree.selectedItems()]

    def _update_buttons(self) -> None:
        has_sel = bool(self.tree.selectedItems())
        self.restore_button.setEnabled(has_sel)
        self.discard_button.setEnabled(has_sel)
        self.clear_button.setEnabled(len(self.bin) > 0)

    def restore_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        result: RestoreResult = self.bin.restore(ids)
        self.restored.emit(result)

    def discard_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        if QMessageBox.question(
            self, tr("Végleges törlés"),
            tr("A kijelölt {n} tétel véglegesen törlődik a lomtárból, és többé nem állítható vissza. Folytatod?")
            .format(n=len(ids)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self.bin.discard(ids)

    def clear_bin(self) -> None:
        if not len(self.bin):
            return
        if QMessageBox.question(
            self, tr("Lomtár ürítése"),
            tr("A lomtár összes tétele ({n}) véglegesen törlődik. Folytatod?").format(n=len(self.bin)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self.bin.clear()
