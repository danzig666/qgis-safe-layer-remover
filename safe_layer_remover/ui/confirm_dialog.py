"""Removal confirmation dialog."""

from __future__ import annotations

import os
from typing import Optional

from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFrame,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..core import settings as plugin_settings
from ..core.analyzer import AnalysisResult
from ..core.classifier import layer_icon
from ..core.model import DepKind, SetResult, Status
from ..core.remover import RemovalOptions
from ..core.texts import kind_label, tr
from .explain import object_long
from .styles import danger_button_style, status_color, status_icon, text_color

_WARN_GROUPS = [
    ({DepKind.MEMORY_DATA_LOSS}, "Memóriarétegek adatvesztése"),
    ({DepKind.UNSAVED_EDITS}, "Mentetlen szerkesztések"),
    ({DepKind.LAYOUT, DepKind.ATLAS}, "Nyomtatási elrendezések"),
    ({DepKind.TERRAIN}, "Projekt terepmodell"),
    ({DepKind.MASKING}, "Szelektív maszkolás"),
]


def default_memory_dir() -> str:
    home = QgsProject.instance().homePath()
    if home and os.path.isdir(home):
        return home
    saved = plugin_settings.get_str("last_memory_dir")
    return saved if saved and os.path.isdir(saved) else os.path.expanduser("~")


class ConfirmDialog(QDialog):
    def __init__(self, layer_ids: list[str], set_result: SetResult, analysis: AnalysisResult,
                 chain_root: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Rétegek törlése"))
        self.setMinimumWidth(520)
        self.layer_ids = list(layer_ids)
        settings = analysis.settings
        project = QgsProject.instance()
        layout = QVBoxLayout(self)

        n = len(self.layer_ids)
        head = QLabel(tr("A következő {n} réteg törlődik a projektből:").format(n=n))
        f = head.font()
        f.setBold(True)
        head.setFont(f)
        layout.addWidget(head)

        if chain_root:
            others = [i for i in self.layer_ids if i != chain_root]
            if others:
                warn = QLabel(tr("Figyelem: a lánc törlése a(z) {r} rétegtől függő rétegeket is törli: {l}.")
                              .format(r=analysis.name_of(chain_root), l=", ".join(analysis.name_of(i) for i in others)))
                warn.setWordWrap(True)
                warn.setStyleSheet(f"color: {text_color(status_color(Status.BLOCKED)).name()}; font-weight: bold;")
                layout.addWidget(warn)

        self.list = QTreeWidget()
        self.list.setColumnCount(2)
        self.list.setHeaderLabels([tr("Réteg"), tr("Típus")])
        self.list.setRootIsDecorated(False)
        for lid in self.layer_ids:
            info = analysis.infos.get(lid)
            layer = project.mapLayer(lid)
            item = QTreeWidgetItem([analysis.name_of(lid), ", ".join(b.text for b in (info.badges if info else []))])
            item.setIcon(0, layer_icon(layer))
            if chain_root and lid != chain_root:
                item.setToolTip(0, tr("függő réteg, a lánc része"))
            self.list.addTopLevelItem(item)
        self.list.resizeColumnToContents(0)
        row_h = max(self.list.sizeHintForRow(0), self.fontMetrics().height() + 6)
        header_h = self.list.header().sizeHint().height()
        self.list.setFixedHeight(min(header_h + row_h * min(n, 8) + 2 * self.list.frameWidth() + 2, 320))
        layout.addWidget(self.list)

        # warnings grouped
        if set_result.warnings:
            # scrollable and height limited: hundreds of warnings must not push the buttons off screen
            box = QTextBrowser()
            box.setHtml(self._warnings_html(set_result))
            box.setFrameShape(QFrame.Shape.NoFrame)
            box.setStyleSheet("QTextBrowser { background: transparent; }")
            box.document().setDocumentMargin(2)
            box.document().setTextWidth(480)      # the dialog's usual width, to estimate the height
            doc_height = int(box.document().size().height()) + 10
            box.setFixedHeight(max(60, min(doc_height, 150)))
            layout.addWidget(box)

        note = QLabel(tr("A törlés csak a projektből távolítja el a réteget, az adatforrást nem érinti "
                         "(a memóriarétegek kivételével)."))
        note.setWordWrap(True)
        note.setEnabled(False)
        layout.addWidget(note)

        # options
        self.cb_bin = QCheckBox(tr("Lomtárba helyezés (a munkamenet végéig visszaállítható)"))
        self.cb_bin.setChecked(settings.get("recycle_bin_enabled", True))
        layout.addWidget(self.cb_bin)

        self.has_memory = any(
            (project.mapLayer(lid) is not None and project.mapLayer(lid).providerType() == "memory")
            for lid in self.layer_ids
        )
        self.cb_memory = QCheckBox(tr("Memóriarétegek adatainak mentése GeoPackage-be"))
        self.cb_memory.setChecked(settings.get("memory_save_default", True))
        self.memory_dir = QLineEdit(default_memory_dir())
        browse = QPushButton(tr("Tallózás…"))
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.setContentsMargins(24, 0, 0, 0)
        row.addWidget(QLabel(tr("Mappa:")))
        row.addWidget(self.memory_dir, 1)
        row.addWidget(browse)
        self.cb_memory.toggled.connect(self.memory_dir.setEnabled)
        self.cb_memory.toggled.connect(browse.setEnabled)
        if self.has_memory:
            layout.addWidget(self.cb_memory)
            layout.addLayout(row)

        self.cb_groups = QCheckBox(tr("Kiürült csoportok törlése"))
        self.cb_groups.setChecked(settings.get("remove_empty_groups", False))
        layout.addWidget(self.cb_groups)

        buttons = QDialogButtonBox()
        self.cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setText(tr("Mégse"))
        self.delete_button = QPushButton(tr("Törlés ({n})").format(n=n))
        self.delete_button.setStyleSheet(danger_button_style())
        self.delete_button.setAutoDefault(False)
        buttons.addButton(self.delete_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus()
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # never taller than the screen, so the buttons always stay reachable
        screen = self.screen() if hasattr(self, "screen") else None
        if screen is not None:
            self.setMaximumHeight(int(screen.availableGeometry().height() * 0.9))

    def _warnings_html(self, res: SetResult) -> str:
        parts = []
        remaining = list(res.warnings)
        groups = []
        for kinds, title in _WARN_GROUPS:
            items = [d for d in remaining if d.kind in kinds]
            if items:
                groups.append((tr(title), items))
                remaining = [d for d in remaining if d.kind not in kinds]
        if remaining:
            groups.append((tr("Egyéb mellékhatások"), remaining))
        color = text_color(status_color(Status.WARN)).name()
        compact = {tr("Memóriarétegek adatvesztése"): tr("{n} memóriaréteg adatai elvesznek (a mentés lent "
                                                         "választható): {l}"),
                   tr("Mentetlen szerkesztések"): tr("{n} rétegen mentetlen szerkesztések vannak: {l}")}
        for title, items in groups:
            head = f"<p style='margin:4px 0 0 0'><b style='color:{color}'>⚠ {_esc(title)}</b></p>"
            if title in compact:
                # one short paragraph with the layer names instead of one long line per layer
                names = sorted({d.dependent_label for d in items}, key=str.casefold)
                body = f"<p style='margin:0 0 0 14px'>{_esc(compact[title].format(n=len(names), l=', '.join(names)))}</p>"
            else:
                body = "<ul style='margin-top:0'>" + "".join(
                    f"<li>{_esc(self._warning_line(d))}</li>" for d in items) + "</ul>"
            parts.append(head + body)
        return "".join(parts)

    @staticmethod
    def _warning_line(dep) -> str:
        if dep.dependent_id is None:
            return f"{dep.dependent_label}: {dep.detail}"
        return f"{dep.dependent_label}: {kind_label(dep.kind)} ({dep.detail})"

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Mentési mappa"), self.memory_dir.text())
        if folder:
            self.memory_dir.setText(folder)

    def options(self) -> RemovalOptions:
        save_dir = None
        if self.has_memory and self.cb_memory.isChecked():
            save_dir = self.memory_dir.text().strip() or None
            if save_dir:
                plugin_settings.set_str("last_memory_dir", save_dir)
        return RemovalOptions(
            use_recycle_bin=self.cb_bin.isChecked(),
            save_memory_dir=save_dir,
            remove_empty_groups=self.cb_groups.isChecked(),
        )


def _esc(text: str) -> str:
    import html
    return html.escape(text or "")
