"""Removal, re-check, recycle bin and tree order tests."""

import tempfile
import unittest

import utilities  # noqa: F401
from utilities import analyze, mk, new_project

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsProject

from safe_layer_remover.core.analyzer import DependencyAnalyzer
from safe_layer_remover.core.model import Status
from safe_layer_remover.core.recycle_bin import RecycleBin
from safe_layer_remover.core.remover import LayerRemover, RemovalOptions
from safe_layer_remover.core.settings import DEFAULTS
from test_detectors import add_join


def fill(layer, n):
    feats = []
    for i in range(n):
        f = QgsFeature(layer.fields())
        f.setAttributes([i, f"hrsz/{i}"])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(650000 + i, 240000 + i)))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()


def make_remover():
    analyzer = DependencyAnalyzer(QgsProject.instance(), settings=dict(DEFAULTS))
    bin_ = RecycleBin(QgsProject.instance())
    return LayerRemover(analyzer, bin_), bin_


class RemoverTest(unittest.TestCase):
    def setUp(self):
        new_project()

    # 13
    def test_memory_layer_remove_and_restore(self):
        project = QgsProject.instance()
        group = project.layerTreeRoot().addGroup("Tervezés")
        a = mk("ideiglenes_pontok", add=False)
        fill(a, 7)
        project.addMapLayer(a, False)
        other = mk("masik", add=False)
        project.addMapLayer(other, False)
        group.addLayer(other)
        group.insertLayer(0, a)
        self.assertEqual(analyze().infos[a.id()].status, Status.WARN)

        a_id = a.id()
        remover, bin_ = make_remover()
        outcome = remover.remove([a_id], RemovalOptions(use_recycle_bin=True))
        self.assertTrue(outcome.ok, outcome.message)
        self.assertIsNone(project.mapLayer(a_id))
        self.assertEqual(len(bin_), 1)

        res = bin_.restore([e.entry_id for e in outcome.entries])
        self.assertEqual(res.errors, [])
        self.assertEqual(len(res.restored), 1)
        restored = project.mapLayer(res.restored[0])
        self.assertEqual(restored.featureCount(), 7)
        self.assertEqual(restored.name(), "ideiglenes_pontok")
        # tree position restored: first child of the group
        group = project.layerTreeRoot().findGroup("Tervezés")
        self.assertEqual(group.children()[0].layerId(), restored.id())
        self.assertEqual(len(bin_), 0)

    def test_save_memory_permanently(self):
        a = mk("pontok")
        fill(a, 3)
        remover, _bin = make_remover()
        with tempfile.TemporaryDirectory() as tmp:
            outcome = remover.remove([a.id()], RemovalOptions(use_recycle_bin=False, save_memory_dir=tmp))
            self.assertTrue(outcome.ok, outcome.message)
            self.assertEqual(len(outcome.saved_files), 1)
            from qgis.core import QgsVectorLayer
            saved = QgsVectorLayer(outcome.saved_files[0], "x", "ogr")
            self.assertEqual(saved.featureCount(), 3)
            del saved

    # 16
    def test_recheck_aborts_when_project_changed(self):
        a, b = mk("A"), mk("B")
        b_id = b.id()
        remover, _bin = make_remover()
        first = remover.analyzer.run()
        self.assertTrue(first.is_safe({b.id()}))
        add_join(a, b)  # the project changes before the user confirms
        outcome = remover.remove([b.id()], RemovalOptions(use_recycle_bin=False))
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.aborted)
        self.assertIsNotNone(QgsProject.instance().mapLayer(b_id))

    def test_unsafe_set_refused_and_chain_allowed(self):
        a, b, c = mk("A"), mk("B"), mk("C")
        add_join(a, b)
        add_join(c, a)
        remover, _bin = make_remover()
        self.assertFalse(remover.remove([b.id()], RemovalOptions(use_recycle_bin=False)).ok)
        closure = remover.analyzer.run().infos[b.id()].closure
        outcome = remover.remove(list(closure), RemovalOptions(use_recycle_bin=False))
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(QgsProject.instance().mapLayers(), {})

    def test_remove_empty_groups(self):
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        outer = root.addGroup("Külső")
        inner = outer.addGroup("Belső")
        a = mk("A", add=False)
        project.addMapLayer(a, False)
        inner.addLayer(a)
        remover, _bin = make_remover()
        outcome = remover.remove([a.id()], RemovalOptions(use_recycle_bin=False, remove_empty_groups=True))
        self.assertTrue(outcome.ok)
        self.assertIsNone(root.findGroup("Külső"))

    def test_restore_into_recreated_group(self):
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        g = root.addGroup("Alaptérképek")
        a = mk("A", add=False)
        project.addMapLayer(a, False)
        g.addLayer(a)
        remover, bin_ = make_remover()
        outcome = remover.remove([a.id()], RemovalOptions(use_recycle_bin=True, remove_empty_groups=True))
        self.assertIsNone(root.findGroup("Alaptérképek"))
        res = bin_.restore([e.entry_id for e in outcome.entries])
        self.assertEqual(res.errors, [])
        self.assertIsNotNone(root.findGroup("Alaptérképek"))


class TreeModelTest(unittest.TestCase):
    """17: the model order equals the layer tree order, with nested groups."""

    def setUp(self):
        new_project()

    def test_tree_order(self):
        from safe_layer_remover.ui.tree_model import IS_GROUP_ROLE, LAYER_ID_ROLE, LayerTreeModel

        project = QgsProject.instance()
        root = project.layerTreeRoot()
        top = mk("Felső")
        g1 = root.addGroup("Alaptérképek")
        g2 = g1.addGroup("Belső")
        l1, l2, l3 = mk("L1", add=False), mk("L2", add=False), mk("L3", add=False)
        for lyr in (l1, l2, l3):
            project.addMapLayer(lyr, False)
        g1.addLayer(l1)
        g2.addLayer(l2)
        root.addLayer(l3)

        model = LayerTreeModel()
        model.rebuild(project, analyze())

        def walk(parent_index, out, depth=0):
            for row in range(model.rowCount(parent_index)):
                idx = model.index(row, 0, parent_index)
                if idx.data(IS_GROUP_ROLE):
                    out.append(("G", idx.data(), depth))
                else:
                    out.append(("L", idx.data(LAYER_ID_ROLE), depth))
                walk(idx, out, depth + 1)
            return out

        from qgis.PyQt.QtCore import QModelIndex
        got = walk(QModelIndex(), [])
        expected = [
            ("L", top.id(), 0),
            ("G", "Alaptérképek", 0),
            ("G", "Belső", 1),
            ("L", l2.id(), 2),
            ("L", l1.id(), 1),
            ("L", l3.id(), 0),
        ]
        # groups are listed before layers only if they come first in the layer tree
        tree_order = []

        def tree_walk(group, depth=0):
            for child in group.children():
                if hasattr(child, "layerId"):
                    tree_order.append(("L", child.layerId(), depth))
                else:
                    tree_order.append(("G", child.name(), depth))
                    tree_walk(child, depth + 1)

        tree_walk(root)
        self.assertEqual(got, tree_order)
        self.assertEqual(sorted(got), sorted(expected))


if __name__ == "__main__":
    unittest.main()
