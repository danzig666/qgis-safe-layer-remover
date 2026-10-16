"""Undo for every layer removal, recycle bin limits, and the related window features."""

import unittest

import utilities  # noqa: F401
from utilities import mk, new_project

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsLayoutItemMap,
    QgsMapThemeCollection,
    QgsPrintLayout,
    QgsProject,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication

from safe_layer_remover import classFactory
from safe_layer_remover.core import settings as plugin_settings
from safe_layer_remover.core.recycle_bin import RecycleBin
from test_detectors import add_join, add_relation
from test_plugin import make_iface
from test_remover import fill


def start_plugin(**settings):
    values = dict(plugin_settings.DEFAULTS)
    values.update({k: v for k, v in settings.items() if k in plugin_settings.DEFAULTS})
    plugin_settings.save(values)
    for key in plugin_settings.INT_DEFAULTS:
        plugin_settings.set_int(key, settings.get(key, plugin_settings.INT_DEFAULTS[key]))
    iface, window = make_iface()
    plugin = classFactory(iface)
    plugin.initGui()
    plugin._keep = window  # noqa: SLF001 - keep the main window alive
    return plugin


def reset_settings():
    plugin_settings.save(dict(plugin_settings.DEFAULTS))
    for key, value in plugin_settings.INT_DEFAULTS.items():
        plugin_settings.set_int(key, value)


class UndoAllTest(unittest.TestCase):
    def setUp(self):
        new_project()

    def tearDown(self):
        reset_settings()

    def _build(self):
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = root.addGroup("G")
        first = mk("first", add=False)
        a = mk("A", add=False)
        fill(a, 5)
        for lyr in (first, a):
            project.addMapLayer(lyr, False)
            group.addLayer(lyr)
        b, c = mk("B"), mk("C")
        add_join(b, a)                      # B joins A
        add_relation(c, a, "rel_ca")         # C is a child of A
        state = QgsMapThemeCollection.MapThemeRecord()
        state.setLayerRecords([QgsMapThemeCollection.MapThemeLayerRecord(a)])
        project.mapThemeCollection().insert("T", state)
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName("L")
        m = QgsLayoutItemMap(layout)
        m.setKeepLayerSet(True)
        m.setLayers([a, b])
        layout.addLayoutItem(m)
        project.layoutManager().addLayout(layout)
        from qgis.core import QgsGroupLayer
        gl = QgsGroupLayer("GL", QgsGroupLayer.LayerOptions(QgsCoordinateTransformContext()))
        gl.setChildLayers([b, a])
        project.addMapLayer(gl)
        QApplication.processEvents()   # map theme cache is built outside removals
        return a, b, c, m, gl

    def _check_restored(self, plugin, a_id, b, m, gl):
        project = QgsProject.instance()
        self.assertEqual(len(plugin.recycle_bin), 1)
        res = plugin.recycle_bin.restore([plugin.recycle_bin.entries[0].entry_id])
        self.assertEqual(res.errors, [])
        self.assertEqual(res.restored, [a_id], "the original layer id is kept")
        a = project.mapLayer(a_id)
        self.assertEqual(a.featureCount(), 5)
        group = project.layerTreeRoot().findGroup("G")
        self.assertEqual([n.layerId() for n in group.findLayers()][1], a_id)
        self.assertTrue(any(j.joinLayerId() == a_id for j in b.vectorJoins()))
        self.assertTrue(project.relationManager().relation("rel_ca").isValid())
        records = project.mapThemeCollection().mapThemeState("T").layerRecords()
        self.assertIn(a_id, [r.layer().id() for r in records if r.layer() is not None])
        self.assertIn(a_id, [lyr.id() for lyr in m.layers() if lyr is not None])
        self.assertIn(a_id, [lyr.id() for lyr in gl.childLayers() if lyr is not None])

    def test_api_removal_is_undone_exactly(self):
        plugin = start_plugin(undo_all_removals=True)
        try:
            a, b, c, m, gl = self._build()
            a_id = a.id()
            QgsProject.instance().removeMapLayer(a_id)
            QApplication.processEvents()
            self.assertFalse(b.vectorJoins())          # QGIS dropped the join...
            self._check_restored(plugin, a_id, b, m, gl)   # ...and undo brings everything back
        finally:
            plugin.unload()

    def test_layers_panel_removal_keeps_position(self):
        plugin = start_plugin(undo_all_removals=True)
        try:
            a, b, c, m, gl = self._build()
            a_id = a.id()
            node = QgsProject.instance().layerTreeRoot().findLayer(a_id)
            node.parent().removeChildNode(node)          # what the Layers panel does
            QApplication.processEvents()
            self.assertIsNone(QgsProject.instance().mapLayer(a_id))
            self._check_restored(plugin, a_id, b, m, gl)
        finally:
            plugin.unload()

    def test_off_by_default_for_safe_removals(self):
        plugin = start_plugin()
        try:
            a = mk("A")
            QgsProject.instance().removeMapLayer(a.id())
            QApplication.processEvents()
            self.assertEqual(len(plugin.recycle_bin), 0)
        finally:
            plugin.unload()

    def test_memory_feature_limit(self):
        plugin = start_plugin(undo_all_removals=True, undo_memory_feature_limit=2)
        try:
            a = mk("big")
            fill(a, 5)
            a_id = a.id()
            QgsProject.instance().removeMapLayer(a_id)
            entry = plugin.recycle_bin.entries[0]
            self.assertTrue(entry.data_skipped)
            self.assertIsNone(entry.data_path)
            res = plugin.recycle_bin.restore([entry.entry_id])
            self.assertEqual(QgsProject.instance().mapLayer(a_id).featureCount(), 0)
            self.assertTrue(res.notes)
        finally:
            plugin.unload()

    def test_sentinel_with_theme_and_relation_does_not_crash(self):
        plugin = start_plugin()
        try:
            a, b = mk("A"), mk("B")
            add_join(a, b)
            add_relation(a, b, "r_ab")
            state = QgsMapThemeCollection.MapThemeRecord()
            state.setLayerRecords([QgsMapThemeCollection.MapThemeLayerRecord(b)])
            QgsProject.instance().mapThemeCollection().insert("T", state)
            QgsProject.instance().removeMapLayer(b.id())
            QApplication.processEvents()
            self.assertEqual(len(plugin.recycle_bin), 1)
        finally:
            plugin.unload()


class BinLimitTest(unittest.TestCase):
    def setUp(self):
        new_project()

    def test_oldest_entries_evicted(self):
        bin_ = RecycleBin(QgsProject.instance())
        bin_.max_entries = 2
        layers = [mk(f"L{i}") for i in range(3)]
        for lyr in layers:
            bin_.store([lyr.id()])
        self.assertEqual(len(bin_), 2)
        self.assertEqual([e.name for e in bin_.entries], ["L1", "L2"])
        bin_.clear()


class WindowTest(unittest.TestCase):
    def setUp(self):
        new_project()

    def tearDown(self):
        reset_settings()

    def test_window_buttons_and_marking(self):
        plugin = start_plugin()
        try:
            win = plugin.dock
            flags = win.windowFlags()
            self.assertFalse(bool(flags & Qt.WindowType.WindowMinimizeButtonHint))
            self.assertTrue(bool(flags & Qt.WindowType.WindowMaximizeButtonHint))
            self.assertTrue(bool(flags & Qt.WindowType.WindowCloseButtonHint))

            from qgis.core import QgsVectorLayer
            free = QgsVectorLayer("?query=SELECT 1 AS id", "free", "virtual")
            QgsProject.instance().addMapLayer(free)
            a, b = mk("A"), mk("B")
            add_join(a, b)                     # B cannot be removed alone, A is a memory layer (warning)
            win.refresh()
            win.select_all_button.click()
            self.assertEqual(win.model.checked_ids(), {free.id()})   # only untouched layers
            win.model.clear_checks()
            win.mark_for_removal([a.id(), b.id(), free.id()], True)
            self.assertEqual(win.model.checked_ids(), {a.id(), free.id()})  # B skipped
            win.mark_for_removal([a.id()], False)
            self.assertEqual(win.model.checked_ids(), {free.id()})
        finally:
            plugin.unload()


if __name__ == "__main__":
    unittest.main()
