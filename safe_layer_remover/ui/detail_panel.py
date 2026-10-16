"""Detail panel: dependencies of the selected layer."""

from __future__ import annotations

from typing import Optional

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.analyzer import AnalysisResult
from ..core.model import LAYOUT_KINDS, Certainty, LayerInfo, Severity, Status
from ..core.texts import chain_text, kind_label, status_label, tr, victim_long
from .explain import object_long, sorted_victims
from .styles import status_icon, text_color, status_color

LAYER_ROLE = Qt.ItemDataRole.UserRole + 1
LAYOUT_ROLE = Qt.ItemDataRole.UserRole + 2


class DetailPanel(QWidget):
    jumpRequested = pyqtSignal(str)          # layer id
    chainRequested = pyqtSignal(str)         # layer id
    locateRequested = pyqtSignal(str)        # layer id
    layoutRequested = pyqtSignal(str)        # layout name

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer_id: Optional[str] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        self.title = QLabel()
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([tr("Réteg / objektum"), tr("Kapcsolat"), tr("Részlet")])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        icon_px = max(12, self.fontMetrics().height() - 2)
        self.tree.setIconSize(QSize(icon_px, icon_px))
        self.tree.setAlternatingRowColors(False)
        self.tree.header().setStretchLastSection(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 150)
        self.tree.setColumnWidth(1, 170)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.chain_button = QPushButton(tr("Lánc kijelölése"))
        self.chain_button.setToolTip(tr("A réteg és az összes tőle (közvetve is) függő réteg kijelölése. "
                                        "Együtt törölve semmi nem marad elrontva."))
        self.chain_button.clicked.connect(lambda: self._layer_id and self.chainRequested.emit(self._layer_id))
        self.locate_button = QPushButton(tr("Ugrás a rétegkezelőben"))
        self.locate_button.clicked.connect(lambda: self._layer_id and self.locateRequested.emit(self._layer_id))
        buttons.addWidget(self.chain_button)
        buttons.addWidget(self.locate_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.clear()

    # ------------------------------------------------------------------
    def clear(self, text: str = "") -> None:
        self._layer_id = None
        self.title.setText(text or tr("Részletek: válassz ki egy réteget a listában."))
        self.tree.clear()
        self.chain_button.setEnabled(False)
        self.chain_button.setText(tr("Lánc kijelölése"))
        self.locate_button.setEnabled(False)

    def show_group(self, name: str, summary: str) -> None:
        self.clear(tr("Csoport: {n} {s}").format(n=name, s=summary))

    def _section(self, text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        f = QFont(self.tree.font())
        f.setBold(True)
        item.setFont(0, f)
        item.setFirstColumnSpanned(True)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tree.addTopLevelItem(item)
        item.setFirstColumnSpanned(True)
        return item

    def _row(self, cols, status: Optional[Status] = None, layer_id: Optional[str] = None,
             tooltip: str = "") -> QTreeWidgetItem:
        item = QTreeWidgetItem(["   " + cols[0]] + list(cols[1:]))
        if status is not None:
            item.setIcon(0, status_icon(status, 14))
        if layer_id:
            item.setData(0, LAYER_ROLE, layer_id)
        if tooltip:
            for c in range(3):
                item.setToolTip(c, tooltip)
        self.tree.addTopLevelItem(item)
        return item

    def show_layer(self, info: LayerInfo, analysis: AnalysisResult, show_info_items: bool = False) -> None:
        self.tree.clear()
        self._layer_id = info.layer_id
        self.title.setText(tr("Részletek: {n} – {s}").format(n=info.name, s=status_label(info.status)))
        self.title.setStyleSheet(f"color: {text_color(status_color(info.status)).name()};")
        res = info.result

        if info.error:
            self._section(tr("Az elemzés sikertelen (a réteg ezért nem törölhető):"))
            for line in info.error.splitlines():
                self._row([line, "", ""], Status.ERROR, tooltip=line)
        if info.status == Status.LOCKED:
            self._section(tr("Zárolt réteg:"))
            self._row([tr("A projekt beállításai szerint a réteg nem eltávolítható."), "", ""], Status.LOCKED)

        if res is not None:
            victims = sorted_victims(res)
            if victims:
                self._section(tr("Ha törlöd, elromlik:"))
                for v in victims:
                    st = Status.BLOCKED if v.certain else Status.UNCERTAIN
                    if v.direct:
                        edges = analysis.graph.edges_into(v.layer_id, {info.layer_id}) or v.chain[:1]
                        kinds = "; ".join(dict.fromkeys(kind_label(e.kind) for e in edges))
                        detail = "; ".join(dict.fromkeys(e.detail for e in edges if e.detail))
                        if any(e.certainty == Certainty.LOW for e in edges) and not v.certain:
                            kinds += " " + tr("(bizonytalan)")
                    else:
                        kinds = tr("közvetve: {c}").format(c=chain_text(v.chain, analysis.name_of))
                        detail = " → ".join(kind_label(e.kind) for e in v.chain)
                    self._row([analysis.name_of(v.layer_id), kinds, detail], st, v.layer_id,
                              victim_long(v, analysis.name_of))
            blocking = [d for d in res.blocking_objects if d.kind.value not in LAYOUT_KINDS]
            if blocking:
                self._section(tr("Tiltó hatás:"))
                for d in blocking:
                    self._row([d.dependent_label, kind_label(d.kind), d.detail], Status.BLOCKED, tooltip=object_long(d))
            warnings = [d for d in res.warnings if d.kind.value not in LAYOUT_KINDS]
            if warnings:
                self._section(tr("Egyéb hatás:"))
                for d in warnings:
                    self._row([d.dependent_label, kind_label(d.kind), d.detail], Status.WARN,
                              d.dependent_id, object_long(d))
            infos = [d for d in res.infos if d.kind.value not in LAYOUT_KINDS]
            if show_info_items and infos:
                self._section(tr("Tájékoztatás:"))
                for d in infos:
                    self._row([d.dependent_label, kind_label(d.kind), d.detail], None, tooltip=object_long(d))

        if info.layout_uses:
            names = sorted({d.object_ref or d.dependent_label for d in info.layout_uses})
            self._section(tr("Megjelenik ezeken az elrendezéseken ({n}):").format(n=len(names)))
            for d in sorted(info.layout_uses, key=lambda x: (x.object_ref, x.dependent_label)):
                item_part = d.dependent_label.split(" / ", 1)[1] if " / " in d.dependent_label else kind_label(d.kind)
                st = {Severity.BLOCK: Status.BLOCKED, Severity.WARN: Status.WARN}.get(d.severity)
                row = self._row([d.object_ref or d.dependent_label, item_part, d.detail], st,
                                tooltip=tr("Dupla kattintás: az elrendezés megnyitása"))
                row.setData(0, LAYOUT_ROLE, d.object_ref)

        deps = [d for d in info.depends_on if d.required_id in analysis.infos]
        if deps:
            self._section(tr("Ez a réteg ezektől függ:"))
            seen = set()
            for d in deps:
                key = (d.required_id, d.kind)
                if key in seen:
                    continue
                seen.add(key)
                self._row([analysis.name_of(d.required_id), kind_label(d.kind), d.detail], None, d.required_id,
                          d.detail)

        if self.tree.topLevelItemCount() == 0:
            self._row([tr("Nincs függő réteg és mellékhatás, a réteg biztonságosan törölhető."), "", ""], Status.SAFE)

        n = len(info.closure)
        self.chain_button.setText(tr("Lánc kijelölése ({n} réteg)").format(n=n))
        self.chain_button.setEnabled(n > 1)
        self.locate_button.setEnabled(True)

    def _on_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        layout = item.data(0, LAYOUT_ROLE)
        if layout:
            self.layoutRequested.emit(layout)
            return
        lid = item.data(0, LAYER_ROLE)
        if lid:
            self.jumpRequested.emit(lid)
