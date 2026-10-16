"""Smoke tests of the plugin entry point and the dock with a mocked iface."""

import unittest
from unittest import mock

import utilities  # noqa: F401
from utilities import mk, new_project

from qgis.core import QgsProject
from qgis.PyQt.QtWidgets import QApplication, QMainWindow

from test_detectors import add_join


def make_iface():
    iface = mock.MagicMock()
    window = QMainWindow()
    iface.mainWindow.return_value = window
    iface.layerTreeView.return_value = None
    iface.mapCanvases.return_value = []
    return iface, window


class PluginTest(unittest.TestCase):
    def setUp(self):
        new_project()

    def test_init_refresh_unload(self):
        from safe_layer_remover import classFactory

        iface, _window = make_iface()
        plugin = classFactory(iface)
        plugin.initGui()
        try:
            a, b = mk("A"), mk("B")
            add_join(a, b)
            plugin.action.setChecked(True)
            plugin.dock.refresh()
            self.assertEqual(len(plugin.dock.analysis.infos), 2)
            # the blocked layer cannot be checked by the user
            item = plugin.dock.model.layer_item(b.id())
            from qgis.PyQt.QtCore import Qt
            self.assertFalse(bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable))
            # chain selection makes the set safe
            plugin.dock.select_chain(b.id())
            self.assertEqual(plugin.dock.model.checked_ids(), {a.id(), b.id()})
            self.assertTrue(plugin.dock.delete_button.isEnabled())
        finally:
            plugin.unload()

    def test_untouched_filter(self):
        from qgis.core import QgsVectorLayer
        from safe_layer_remover import classFactory
        from safe_layer_remover.ui.dock import UNTOUCHED
        from safe_layer_remover.ui.tree_model import LAYER_ID_ROLE

        iface, _window = make_iface()
        plugin = classFactory(iface)
        plugin.initGui()
        try:
            free = QgsVectorLayer("?query=SELECT 1 AS id", "free", "virtual")
            QgsProject.instance().addMapLayer(free)
            a, b = mk("A"), mk("B")
            add_join(a, b)
            win = plugin.dock
            win.refresh()
            idx = [i for i, (_l, v) in enumerate(win._status_filters) if v == UNTOUCHED][0]
            win.status_combo.setCurrentIndex(idx)
            self.assertIn("(2)", win.status_combo.itemText(idx))   # free + A (memory layer, nothing depends on it)
            visible = []
            for row in range(win.proxy.rowCount()):
                visible.append(win.proxy.index(row, 0).data(LAYER_ID_ROLE))
            self.assertEqual(set(visible), {free.id(), a.id()})
            win.status_combo.setCurrentIndex(0)
            self.assertEqual(win.proxy.rowCount(), 3)
        finally:
            plugin.unload()

    def test_sentinel_snapshots_unsafe_native_removal(self):
        from safe_layer_remover import classFactory

        iface, _window = make_iface()
        plugin = classFactory(iface)
        plugin.initGui()
        try:
            a, b = mk("A"), mk("B")
            add_join(a, b)
            b_id = b.id()
            QgsProject.instance().removeMapLayer(b_id)   # "native" removal, not through the dock
            self.assertEqual(len(plugin.recycle_bin), 1)
            QApplication.processEvents()
            entries = plugin.recycle_bin.entries
            res = plugin.recycle_bin.restore([entries[0].entry_id])
            self.assertEqual(res.errors, [])
            self.assertEqual(len(QgsProject.instance().mapLayersByName("B")), 1)
        finally:
            plugin.unload()

    def test_project_clear_does_not_trigger_sentinel(self):
        from safe_layer_remover import classFactory

        iface, _window = make_iface()
        plugin = classFactory(iface)
        plugin.initGui()
        try:
            a, b = mk("A"), mk("B")
            add_join(a, b)
            QgsProject.instance().clear()
            self.assertEqual(len(plugin.recycle_bin), 0)
        finally:
            plugin.unload()


if __name__ == "__main__":
    unittest.main()
