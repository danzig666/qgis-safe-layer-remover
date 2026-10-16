"""Settings dialog."""

from __future__ import annotations

from qgis.PyQt.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QLabel, QVBoxLayout

from ..core import settings as plugin_settings
from ..core.texts import tr

_LABELS = [
    (tr("Elemzés"), [
        ("uncertain_is_blocked", tr("A bizonytalan (heurisztikus) hivatkozások tiltsák a törlést")),
        ("use_xml_scan", tr("XML biztonsági háló (azonosító-keresés a rétegbeállításokban)")),
        ("block_on_atlas", tr("Az atlasz lefedő réteg törlése legyen tiltva (figyelmeztetés helyett)")),
        ("block_on_layout", tr("Nyomtatási elrendezésben használt réteg törlése legyen tiltva")),
    ]),
    (tr("Törlés"), [
        ("recycle_bin_enabled", tr("Lomtár használata (visszavonható törlés)")),
        ("memory_save_default", tr("Memóriarétegek mentése alapból bejelölve")),
        ("remove_empty_groups", tr("Kiürült csoportok törlése alapból bejelölve")),
    ]),
    (tr("Megjelenés"), [
        ("sync_selection", tr("Kijelölés szinkronizálása a QGIS rétegkezelőjével")),
        ("show_info_items", tr("Tájékoztató tételek (térképtémák, illesztés) megjelenítése a részleteknél")),
        ("show_tree_indicators", tr("Láncszem jelző a QGIS rétegkezelőjében a függőséggel rendelkező rétegeken")),
        ("sentinel_mode", tr("Őrszem mód: figyelmeztetés és visszaállítás, ha a natív rétegkezelőből "
                             "törölt réteg mást elrontott")),
    ]),
]


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Biztonságos rétegtörlés – beállítások"))
        values = plugin_settings.load()
        self._boxes: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        for title, items in _LABELS:
            group = QGroupBox(title)
            gl = QVBoxLayout(group)
            for key, label in items:
                cb = QCheckBox(label)
                cb.setChecked(values.get(key, plugin_settings.DEFAULTS[key]))
                gl.addWidget(cb)
                self._boxes[key] = cb
            layout.addWidget(group)
        hint = QLabel(tr("A változások a következő elemzéskor lépnek életbe."))
        hint.setEnabled(False)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                                   | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._defaults)
        layout.addWidget(buttons)

    def _defaults(self) -> None:
        for key, cb in self._boxes.items():
            cb.setChecked(plugin_settings.DEFAULTS[key])

    def _save(self) -> None:
        plugin_settings.save({k: cb.isChecked() for k, cb in self._boxes.items()})
        self.accept()
