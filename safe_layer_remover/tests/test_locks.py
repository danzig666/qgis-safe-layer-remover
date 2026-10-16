"""Personal layer locks."""

import os
import tempfile
import unittest

import utilities  # noqa: F401
from utilities import analyze, mk, new_project

from qgis.core import QgsProject

from safe_layer_remover.core import locks
from safe_layer_remover.core.model import Status
from test_detectors import add_join


class LockTest(unittest.TestCase):
    def setUp(self):
        new_project()

    def test_lock_unlock(self):
        a, b = mk("A"), mk("B")
        locks.set_locked([a.id(), b.id()], True)
        r = analyze()
        self.assertEqual(r.infos[a.id()].status, Status.LOCKED)
        self.assertTrue(r.infos[a.id()].user_locked)
        self.assertFalse(r.is_safe({a.id()}))
        locks.set_locked([a.id()], False)
        r = analyze()
        self.assertNotEqual(r.infos[a.id()].status, Status.LOCKED)
        self.assertFalse(r.infos[a.id()].user_locked)
        self.assertEqual(r.infos[b.id()].status, Status.LOCKED)

    def test_locked_layer_blocks_chain(self):
        a, b = mk("A"), mk("B")
        add_join(a, b)                       # A depends on B
        locks.set_locked([a.id()], True)
        r = analyze()
        self.assertFalse(r.is_safe(r.infos[b.id()].closure))

    def test_lock_saved_with_project(self):
        from qgis.core import QgsVectorLayer
        with tempfile.TemporaryDirectory() as tmp:
            # a file based layer keeps its id across save / load
            src = mk("A", add=False)
            from safe_layer_remover.core.recycle_bin import write_vector_copy
            gpkg = os.path.join(tmp, "a.gpkg")
            self.assertTrue(write_vector_copy(src, gpkg)[0])
            layer = QgsVectorLayer(f"{gpkg}|layername=data", "A", "ogr")
            QgsProject.instance().addMapLayer(layer)
            lid = layer.id()
            locks.set_locked([lid], True)
            path = os.path.join(tmp, "p.qgz")
            self.assertTrue(QgsProject.instance().write(path))
            new_project()
            self.assertEqual(locks.locked_ids(), set())
            self.assertTrue(QgsProject.instance().read(path))
            self.assertEqual(locks.locked_ids(), {lid})
            new_project()

    def test_removed_layers_are_dropped(self):
        a = mk("A")
        a_id = a.id()
        locks.set_locked([a_id], True)
        QgsProject.instance().removeMapLayer(a_id)
        b = mk("B")
        locks.set_locked([b.id()], True)
        self.assertEqual(locks.locked_ids(), {b.id()})


class LockUiTest(unittest.TestCase):
    def setUp(self):
        new_project()

    def test_window_multi_select_lock(self):
        from qgis.PyQt.QtCore import QItemSelectionModel
        from safe_layer_remover import classFactory
        from test_plugin import make_iface

        iface, _window = make_iface()
        plugin = classFactory(iface)
        plugin.initGui()
        try:
            a, b, c = mk("A"), mk("B"), mk("C")
            win = plugin.dock
            win.refresh()
            sel = win.view.selectionModel()
            for lyr in (a, b):
                idx = win.proxy.mapFromSource(win.model.index_for_layer(lyr.id()))
                sel.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            self.assertEqual(set(win.selected_layer_ids()), {a.id(), b.id()})
            self.assertTrue(win.lock_button.isEnabled())
            win.lock_button.click()
            self.assertEqual(locks.locked_ids(), {a.id(), b.id()})
            self.assertEqual(win.analysis.infos[a.id()].status, Status.LOCKED)
            item = win.model.layer_item(a.id())
            from safe_layer_remover.ui.tree_model import USER_LOCKED_ROLE
            self.assertTrue(item.data(USER_LOCKED_ROLE))
            # Ctrl+L toggles back when every selected layer is locked
            for lyr in (a, b):
                idx = win.proxy.mapFromSource(win.model.index_for_layer(lyr.id()))
                sel.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            win.toggle_lock_selected()
            self.assertEqual(locks.locked_ids(), set())
        finally:
            plugin.unload()

    def test_sentinel_restores_locked_layer_with_lock(self):
        from qgis.PyQt.QtWidgets import QApplication
        from safe_layer_remover import classFactory
        from test_plugin import make_iface

        iface, _window = make_iface()
        plugin = classFactory(iface)
        plugin.initGui()
        try:
            a = mk("A")
            a_id = a.id()
            locks.set_locked([a_id], True)
            QgsProject.instance().removeMapLayer(a_id)
            QApplication.processEvents()
            self.assertEqual(len(plugin.recycle_bin), 1)
            res = plugin.recycle_bin.restore([plugin.recycle_bin.entries[0].entry_id])
            self.assertEqual(res.errors, [])
            self.assertEqual(locks.locked_ids(), set(res.restored))
        finally:
            plugin.unload()


if __name__ == "__main__":
    unittest.main()
