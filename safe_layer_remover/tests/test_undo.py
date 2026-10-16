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
    plugin.recycle_bin.theme_cache_delay_ms = 0   # tests process events right away
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


class FastPathTest(unittest.TestCase):
    def setUp(self):
        new_project()

    def tearDown(self):
        reset_settings()

    def test_may_be_referenced(self):
        from qgis.core import QgsVectorLayer
        from safe_layer_remover.core.analyzer import DependencyAnalyzer
        a, b, free = mk("Alpha"), mk("Bravo"), mk("Charlie")
        add_join(a, b)
        v = QgsVectorLayer('?query=SELECT * FROM "Charlie"', "V", "virtual")
        an = DependencyAnalyzer(QgsProject.instance(), settings=dict(plugin_settings.DEFAULTS))
        self.assertTrue(an.may_be_referenced([b.id()]))        # joined by Alpha
        self.assertFalse(an.may_be_referenced([a.id()]))       # nothing refers to Alpha
        self.assertFalse(an.may_be_referenced([free.id()]))
        QgsProject.instance().addMapLayer(v)
        an.invalidate()
        self.assertTrue(an.may_be_referenced([free.id()]))     # referenced by name in a virtual layer query

    def test_removal_reuses_collected_dependencies(self):
        from safe_layer_remover.core.model import Status
        plugin = start_plugin()
        try:
            an = plugin.analyzer
            a, b, c = mk("A"), mk("B"), mk("C")
            add_join(a, b)                               # A depends on B
            QApplication.processEvents()
            self.assertEqual(an.run().infos[b.id()].status, Status.BLOCKED)
            collected = an._collected                    # noqa: SLF001
            QgsProject.instance().removeMapLayer(a.id())
            r = an.run()
            self.assertIs(an._collected, collected, "a removal must not trigger a new collection")  # noqa: SLF001
            self.assertNotEqual(r.infos[b.id()].status, Status.BLOCKED)
            add_join(c, b)                               # a new dependency: collected again
            QApplication.processEvents()
            r = an.run()
            self.assertIsNot(an._collected, collected)   # noqa: SLF001
            self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)
            self.assertIsNotNone(an.evaluate_if_fresh([b.id()]))
            self.assertTrue(an.evaluate_if_fresh([b.id()]).blocked)
        finally:
            plugin.unload()

    def test_helper_layers_are_not_snapshotted(self):
        plugin = start_plugin(undo_all_removals=True)
        try:
            from qgis.core import QgsVectorLayer
            helper = QgsVectorLayer("Point?crs=EPSG:23700&field=id:integer", "helper", "memory")
            QgsProject.instance().addMapLayer(helper, False)      # not shown in the Layers panel
            QgsProject.instance().removeMapLayer(helper.id())
            self.assertEqual(len(plugin.recycle_bin), 0)
            shown = mk("shown")
            QgsProject.instance().removeMapLayer(shown.id())
            self.assertEqual(len(plugin.recycle_bin), 1)
        finally:
            plugin.unload()


class FilterStaysTest(unittest.TestCase):
    def setUp(self):
        new_project()

    def tearDown(self):
        reset_settings()

    def test_lock_keeps_filter_and_moves_to_next_row(self):
        from qgis.core import QgsVectorLayer
        from safe_layer_remover.ui.dock import UNTOUCHED
        plugin = start_plugin()
        try:
            layers = []
            for name in ("one", "two", "three"):
                lyr = QgsVectorLayer("?query=SELECT 1 AS id", name, "virtual")
                QgsProject.instance().addMapLayer(lyr)
                layers.append(lyr)
            win = plugin.dock
            win.refresh()
            idx = [i for i, (_l, v) in enumerate(win._status_filters) if v == UNTOUCHED][0]  # noqa: SLF001
            win.status_combo.setCurrentIndex(idx)
            order = win._visible_layer_ids()                 # noqa: SLF001
            self.assertEqual(len(order), 3)
            middle = order[1]
            win.jump_to(middle)
            win.set_locked([middle], True)
            self.assertEqual(win.status_combo.currentIndex(), idx, "the filter must stay")
            self.assertEqual(win._visible_layer_ids(), [order[0], order[2]])  # noqa: SLF001
            self.assertEqual(win._current_layer_id(), order[2], "the next row is selected")  # noqa: SLF001
            # locking the last row moves back to the previous one
            win.set_locked([order[2]], True)
            self.assertEqual(win._current_layer_id(), order[0])  # noqa: SLF001
        finally:
            plugin.unload()


class ReviewFixesTest(unittest.TestCase):
    def setUp(self):
        new_project()

    def tearDown(self):
        reset_settings()

    def test_group_selection_respects_filter(self):
        from qgis.core import QgsVectorLayer
        from qgis.PyQt.QtCore import QItemSelectionModel
        from safe_layer_remover.ui.dock import UNTOUCHED
        plugin = start_plugin()
        try:
            project = QgsProject.instance()
            group = project.layerTreeRoot().addGroup("G")
            free = QgsVectorLayer("?query=SELECT 1 AS id", "free", "virtual")
            a, b = mk("A", add=False), mk("B", add=False)
            for lyr in (free, a, b):
                project.addMapLayer(lyr, False)
                group.addLayer(lyr)
            add_join(a, b)                 # B is not removable, so not untouched
            win = plugin.dock
            win.refresh()
            idx = [i for i, (_l, v) in enumerate(win._status_filters) if v == UNTOUCHED][0]  # noqa: SLF001
            win.status_combo.setCurrentIndex(idx)
            group_idx = win.proxy.index(0, 0)
            win.view.selectionModel().select(group_idx, QItemSelectionModel.SelectionFlag.ClearAndSelect
                                             | QItemSelectionModel.SelectionFlag.Rows)
            self.assertEqual(set(win.selected_layer_ids()), {free.id(), a.id()})
        finally:
            plugin.unload()

    def test_editing_does_not_trigger_collection(self):
        from qgis.core import QgsFeature
        from safe_layer_remover.core.model import DepKind
        plugin = start_plugin()
        try:
            an = plugin.analyzer
            a = mk("A")
            QApplication.processEvents()
            an.run()
            collected = an._collected                      # noqa: SLF001
            a.startEditing()
            a.addFeature(QgsFeature(a.fields()))
            QApplication.processEvents()
            r = an.run()
            self.assertIs(an._collected, collected)        # noqa: SLF001
            self.assertIn(DepKind.UNSAVED_EDITS, {d.kind for d in r.infos[a.id()].result.warnings})
            a.rollBack()
            r = an.run()
            self.assertNotIn(DepKind.UNSAVED_EDITS, {d.kind for d in r.infos[a.id()].result.warnings})
        finally:
            plugin.unload()

    def test_new_relation_is_noticed(self):
        from safe_layer_remover.core.model import Status
        plugin = start_plugin()
        try:
            an = plugin.analyzer
            child, parent = mk("Child"), mk("Parent")
            QApplication.processEvents()
            self.assertNotEqual(an.run().infos[parent.id()].status, Status.BLOCKED)
            add_relation(child, parent, "rel_new")
            QApplication.processEvents()
            self.assertEqual(an.run().infos[parent.id()].status, Status.BLOCKED)
        finally:
            plugin.unload()

    def test_undo_puts_removed_group_back_in_place(self):
        from safe_layer_remover.core.remover import LayerRemover, RemovalOptions
        from safe_layer_remover.core.analyzer import DependencyAnalyzer
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        for name in ("G1", "G2", "G3"):
            grp = root.addGroup(name)
            lyr = mk(f"L_{name}", add=False)
            project.addMapLayer(lyr, False)
            grp.addLayer(lyr)
        target = project.mapLayersByName("L_G2")[0].id()
        bin_ = RecycleBin(project)
        remover = LayerRemover(DependencyAnalyzer(project, settings=dict(plugin_settings.DEFAULTS)), bin_)
        outcome = remover.remove([target], RemovalOptions(use_recycle_bin=True, remove_empty_groups=True))
        self.assertIsNone(root.findGroup("G2"))
        bin_.restore([e.entry_id for e in outcome.entries])
        self.assertEqual([c.name() for c in root.children()], ["G1", "G2", "G3"])

    def test_eviction_keeps_batches_whole(self):
        bin_ = RecycleBin(QgsProject.instance())
        bin_.max_entries = 3
        a, b, c, d = mk("a"), mk("b"), mk("c"), mk("d")
        bin_.store([a.id(), b.id()])          # batch 1
        bin_.store([c.id()])                  # batch 2
        bin_.store([d.id()])                  # batch 3 -> 4 entries, batch 1 goes as a whole
        self.assertEqual(sorted(e.name for e in bin_.entries), ["c", "d"])
        bin_.clear()

    def test_confirm_dialog_stays_small_with_many_memory_layers(self):
        from safe_layer_remover.core.analyzer import DependencyAnalyzer
        from safe_layer_remover.ui.confirm_dialog import ConfirmDialog
        layers = [mk(f"{i}. módosítási pont másolata") for i in range(60)]
        ids = [lyr.id() for lyr in layers]
        analysis = DependencyAnalyzer(QgsProject.instance(), settings=dict(plugin_settings.DEFAULTS)).run()
        dlg = ConfirmDialog(ids, analysis.evaluate(set(ids)), analysis)
        self.assertLess(dlg.sizeHint().height(), 900)


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
            self.assertEqual(win.model.checked_ids(), {free.id(), a.id()})   # untouched: free and memory layer A
            win.model.clear_checks()
            win.mark_for_removal([a.id(), b.id(), free.id()], True)
            self.assertEqual(win.model.checked_ids(), {a.id(), free.id()})  # B skipped
            win.mark_for_removal([a.id()], False)
            self.assertEqual(win.model.checked_ids(), {free.id()})
        finally:
            plugin.unload()


if __name__ == "__main__":
    unittest.main()
