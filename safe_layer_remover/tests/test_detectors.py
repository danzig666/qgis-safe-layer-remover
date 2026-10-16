"""Detector tests on in-memory projects."""

import unittest

import utilities  # noqa: F401
from utilities import analyze, mk, new_project, string_field

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsEditorWidgetSetup,
    QgsMapLayer,
    QgsMapLayerDependency,
    QgsPrintLayout,
    QgsProject,
    QgsRelation,
    QgsVectorLayer,
    QgsVectorLayerJoinInfo,
)

from safe_layer_remover.core.analyzer import DependencyAnalyzer
from safe_layer_remover.core.detectors import ALL_DETECTORS
from safe_layer_remover.core.detectors.base import BaseDetector
from safe_layer_remover.core.model import Certainty, DepKind, Status
from safe_layer_remover.core.settings import DEFAULTS
from safe_layer_remover.core.texts import article


def add_join(layer, target, field="id"):
    info = QgsVectorLayerJoinInfo()
    info.setJoinLayer(target)
    info.setJoinFieldName(field)
    info.setTargetFieldName(field)
    assert layer.addJoin(info)


def add_relation(child, parent, rel_id="rel1"):
    rel = QgsRelation()
    rel.setId(rel_id)
    rel.setName(rel_id)
    rel.setReferencingLayer(child.id())
    rel.setReferencedLayer(parent.id())
    rel.addFieldPair("hrsz", "hrsz")
    assert rel.isValid(), rel.validationError()
    QgsProject.instance().relationManager().addRelation(rel)
    return rel


class DetectorTest(unittest.TestCase):
    def setUp(self):
        new_project()

    # 1
    def test_join(self):
        a, b = mk("A"), mk("B")
        add_join(a, b)
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)
        self.assertEqual(r.infos[a.id()].status, Status.WARN)  # memory layer: data loss warning only
        reason = r.infos[b.id()].reasons[0]
        self.assertEqual(reason.kind, DepKind.JOIN)
        self.assertEqual(reason.dependent_id, a.id())

    # 2
    def test_virtual_declared(self):
        b = mk("B")
        v = QgsVectorLayer(f"?layer_ref={b.id()}:t&query=SELECT * FROM t", "V", "virtual")
        self.assertTrue(v.isValid())
        QgsProject.instance().addMapLayer(v)
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)
        kinds = {(d.kind, d.certainty) for d in r.infos[b.id()].reasons}
        self.assertIn((DepKind.VIRTUAL_LAYER, Certainty.HIGH), kinds)
        self.assertEqual(r.infos[v.id()].status, Status.SAFE)

    # 3
    def test_virtual_query_by_name(self):
        b = mk("Parcellak")
        v = QgsVectorLayer("?query=SELECT * FROM Parcellak", "V", "virtual")
        self.assertTrue(v.isValid())
        QgsProject.instance().addMapLayer(v)
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)
        dep = [d for d in r.infos[b.id()].reasons if d.kind == DepKind.VIRTUAL_LAYER][0]
        self.assertIn(dep.certainty, (Certainty.MEDIUM, Certainty.HIGH))

    def test_virtual_query_ambiguous_name(self):
        b1 = mk("Parcellak")
        v = QgsVectorLayer("?query=SELECT * FROM Parcellak", "V", "virtual")
        self.assertTrue(v.isValid())
        QgsProject.instance().addMapLayer(v)
        b2 = mk("Parcellak")
        # the virtual provider may also declare the dependency through dependencies();
        # analyze without provider-declared dependencies to test the name heuristics
        r = analyze()
        statuses = {r.infos[b1.id()].status, r.infos[b2.id()].status}
        self.assertTrue(statuses <= {Status.UNCERTAIN, Status.BLOCKED}, statuses)
        self.assertIn(Status.UNCERTAIN, statuses)

    # 4 + 11
    def test_relation_both_sides_and_cycle(self):
        child, parent = mk("Gyerek"), mk("Szulo")
        add_relation(child, parent)
        r = analyze()
        self.assertEqual(r.infos[child.id()].status, Status.BLOCKED)
        self.assertEqual(r.infos[parent.id()].status, Status.BLOCKED)
        self.assertTrue(r.is_safe({child.id(), parent.id()}))
        self.assertEqual(r.infos[child.id()].closure, frozenset({child.id(), parent.id()}))

    # 5
    def test_value_relation(self):
        a, b = mk("A"), mk("B")
        idx = a.fields().indexOf("hrsz")
        a.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("ValueRelation", {"Layer": b.id(), "Key": "id", "Value": "hrsz"}))
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)
        self.assertEqual(r.infos[b.id()].reasons[0].kind, DepKind.VALUE_RELATION)

    def test_relation_reference_widget(self):
        child, parent = mk("Gyerek"), mk("Szulo")
        add_relation(child, parent, "relref")
        idx = child.fields().indexOf("hrsz")
        child.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("RelationReference", {"Relation": "relref"}))
        r = analyze()
        kinds = {d.kind for d in r.infos[parent.id()].reasons}
        self.assertIn(DepKind.RELATION_REFERENCE, kinds)

    # 6
    def test_expression_field_literal_name(self):
        a, b = mk("A"), mk("Telkek")
        a.addExpressionField("get_feature('Telkek', 'id', 1)", string_field("x"))
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)
        dep = r.infos[b.id()].reasons[0]
        self.assertEqual(dep.kind, DepKind.EXPRESSION)
        self.assertEqual(dep.certainty, Certainty.MEDIUM)

    def test_expression_literal_id(self):
        a, b = mk("A"), mk("Telkek")
        a.addExpressionField(f"aggregate('{b.id()}', 'count', \"id\")", string_field("x"))
        r = analyze()
        dep = [d for d in r.infos[b.id()].reasons if d.kind == DepKind.EXPRESSION][0]
        self.assertEqual(dep.certainty, Certainty.HIGH)

    def test_expression_dynamic(self):
        a, b = mk("A"), mk("Telkek")
        a.addExpressionField("get_feature('Tel' || 'kek', 'id', 1)", string_field("x"))
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.UNCERTAIN)
        self.assertFalse(r.is_safe({b.id()}))

    def test_expression_in_display_expression_and_maptip(self):
        a, b, c = mk("A"), mk("Telkek"), mk("Ovezet")
        a.setDisplayExpression("attribute(get_feature('Telkek','id',1), 'hrsz')")
        a.setMapTipTemplate("<b>[% aggregate('Ovezet','count',\"id\") %]</b>")
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)
        self.assertEqual(r.infos[c.id()].status, Status.BLOCKED)

    def test_expression_in_rule_based_renderer(self):
        from qgis.core import QgsRuleBasedRenderer, QgsSymbol
        a, b = mk("A"), mk("Telkek")
        root = QgsRuleBasedRenderer.Rule(None)
        rule = QgsRuleBasedRenderer.Rule(QgsSymbol.defaultSymbol(a.geometryType()), 0, 0,
                                         "overlay_intersects('Telkek')", "Lakóterület")
        root.appendChild(rule)
        a.setRenderer(QgsRuleBasedRenderer(root))
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)

    # 7
    def test_layer_dependency(self):
        a, b = mk("A"), mk("B")
        a.setDependencies([QgsMapLayerDependency(b.id())])
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)
        self.assertEqual(r.infos[b.id()].reasons[0].kind, DepKind.LAYER_DEPENDENCY)

    # 8
    def test_group_layer(self):
        from qgis.core import QgsGroupLayer
        a, b = mk("A"), mk("B")
        g = QgsGroupLayer("Csoport", QgsGroupLayer.LayerOptions(QgsCoordinateTransformContext()))
        g.setChildLayers([a, b])
        QgsProject.instance().addMapLayer(g)
        r = analyze()
        self.assertEqual(r.infos[a.id()].status, Status.BLOCKED)
        self.assertEqual(r.infos[a.id()].reasons[0].kind, DepKind.GROUP_LAYER)
        self.assertEqual(r.infos[g.id()].status, Status.SAFE)

    # 9
    def test_indirect_chain(self):
        a, b, c = mk("A"), mk("B"), mk("C")
        add_join(a, b)   # A -> B
        add_join(c, a)   # C -> A
        r = analyze()
        info = r.infos[b.id()]
        self.assertIn(c.id(), info.transitive_victims)
        victim = info.result.victims[c.id()]
        self.assertFalse(victim.direct)
        self.assertEqual([d.dependent_id for d in victim.chain], [a.id(), c.id()])

    # 10
    def test_set_evaluation(self):
        a, b = mk("A"), mk("B")
        add_join(a, b)
        r = analyze()
        self.assertTrue(r.is_safe({a.id(), b.id()}))
        self.assertFalse(r.is_safe({b.id()}))

    # 12
    def test_layout_atlas(self):
        a = mk("A")
        project = QgsProject.instance()
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName("A3 terv")
        layout.atlas().setEnabled(True)
        layout.atlas().setCoverageLayer(a)
        project.layoutManager().addLayout(layout)
        r = analyze()
        self.assertEqual(r.infos[a.id()].status, Status.WARN)
        self.assertIn(DepKind.ATLAS, {d.kind for d in r.infos[a.id()].reasons})
        r2 = analyze(block_on_atlas=True)
        self.assertEqual(r2.infos[a.id()].status, Status.BLOCKED)

    def test_layout_map_locked_layers(self):
        from qgis.core import QgsLayoutItemMap
        a = mk("A")
        project = QgsProject.instance()
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName("Terv")
        m = QgsLayoutItemMap(layout)
        m.setKeepLayerSet(True)
        m.setLayers([a])
        layout.addLayoutItem(m)
        project.layoutManager().addLayout(layout)
        r = analyze()
        self.assertIn(DepKind.LAYOUT, {d.kind for d in r.infos[a.id()].reasons})
        r2 = analyze(block_on_layout=True)
        self.assertEqual(r2.infos[a.id()].status, Status.BLOCKED)

    def test_layout_theme_and_live_map(self):
        from qgis.core import QgsLayoutItemMap, QgsMapThemeCollection
        a, b, c = mk("A"), mk("B"), mk("C")
        project = QgsProject.instance()
        rec = QgsMapThemeCollection.MapThemeLayerRecord(a)
        state = QgsMapThemeCollection.MapThemeRecord()
        state.setLayerRecords([rec])
        project.mapThemeCollection().insert("Csak A", state)

        themed = QgsPrintLayout(project)
        themed.initializeDefaults()
        themed.setName("Témás")
        m = QgsLayoutItemMap(themed)
        m.setFollowVisibilityPreset(True)
        m.setFollowVisibilityPresetName("Csak A")
        themed.addLayoutItem(m)
        project.layoutManager().addLayout(themed)

        live = QgsPrintLayout(project)
        live.initializeDefaults()
        live.setName("Élő")
        live.addLayoutItem(QgsLayoutItemMap(live))
        project.layoutManager().addLayout(live)
        project.layerTreeRoot().findLayer(c.id()).setItemVisibilityChecked(False)

        r = analyze()
        on = lambda lid: sorted({d.object_ref for d in r.infos[lid].layout_uses})  # noqa: E731
        # only the theme-following map counts; a live map does not put layers on the layout
        self.assertEqual(on(a.id()), ["Témás"])
        self.assertEqual(on(b.id()), [])
        self.assertEqual(on(c.id()), [])
        self.assertTrue(any(bd.text.startswith("Elrendezésen (1)") for bd in r.infos[a.id()].badges))
        self.assertFalse(any(bd.text.startswith("Elrendezésen") for bd in r.infos[b.id()].badges))
        self.assertIn(DepKind.LAYOUT, {d.kind for d in r.infos[a.id()].reasons})
        self.assertEqual(r.infos[b.id()].status, Status.WARN)  # memory warning only
        r2 = analyze(block_on_layout=True)
        self.assertEqual(r2.infos[a.id()].status, Status.BLOCKED)
        self.assertNotEqual(r2.infos[b.id()].status, Status.BLOCKED)

    def test_untouched(self):
        from qgis.core import QgsLayoutItemMap, QgsMapThemeCollection
        project = QgsProject.instance()

        def ogr_like(name):
            # a non-memory layer (no data loss warning): a virtual layer without sources
            lyr = QgsVectorLayer("?query=SELECT 1 AS id", name, "virtual")
            self.assertTrue(lyr.isValid())
            project.addMapLayer(lyr)
            return lyr

        free, used, on_layout, in_theme = ogr_like("free"), ogr_like("used"), ogr_like("layout"), ogr_like("theme")
        mem = mk("mem")                                    # memory layer: data loss warning
        dep = ogr_like("dep")
        dep.setDependencies([QgsMapLayerDependency(used.id())])

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName("L")
        m = QgsLayoutItemMap(layout)
        m.setKeepLayerSet(True)
        m.setLayers([on_layout])
        layout.addLayoutItem(m)
        project.layoutManager().addLayout(layout)

        state = QgsMapThemeCollection.MapThemeRecord()
        state.setLayerRecords([QgsMapThemeCollection.MapThemeLayerRecord(in_theme)])
        project.mapThemeCollection().insert("T", state)

        r = analyze()
        got = {r.infos[lyr.id()].name for lyr in (free, used, on_layout, in_theme, mem, dep)
               if r.infos[lyr.id()].untouched}
        self.assertEqual(got, {"free", "dep"})

    def test_memory_layer_warn(self):
        a = mk("A")
        r = analyze()
        self.assertEqual(r.infos[a.id()].status, Status.WARN)
        self.assertEqual(r.infos[a.id()].reasons[0].kind, DepKind.MEMORY_DATA_LOSS)

    # 14
    def test_locked_layer(self):
        a = mk("A")
        removable = QgsMapLayer.LayerFlag.Removable
        try:
            a.setFlags(a.flags() & ~removable)
        except TypeError:
            a.setFlags(QgsMapLayer.LayerFlags(int(a.flags()) & ~int(removable)))
        r = analyze()
        self.assertEqual(r.infos[a.id()].status, Status.LOCKED)
        self.assertFalse(r.is_safe({a.id()}))

    # 15
    def test_detector_exception_fail_closed(self):
        a, b = mk("A"), mk("Boom")

        class BoomDetector(BaseDetector):
            name = "boom"

            def detect_layer(self, layer, project, ctx):
                if layer.name() == "Boom":
                    raise RuntimeError("mock failure")
                return []

        settings = dict(DEFAULTS)
        r = DependencyAnalyzer(QgsProject.instance(), settings=settings,
                               detectors=list(ALL_DETECTORS) + [BoomDetector]).run()
        self.assertEqual(r.infos[b.id()].status, Status.ERROR)
        self.assertIn("mock failure", r.infos[b.id()].error)
        self.assertFalse(r.is_safe({b.id()}))
        self.assertNotEqual(r.infos[a.id()].status, Status.ERROR)

    def test_project_level_exception_blocks_everything(self):
        a = mk("A")

        class ProjectBoom(BaseDetector):
            name = "project_boom"

            def detect(self, project, ctx):
                raise RuntimeError("project failure")

        r = DependencyAnalyzer(QgsProject.instance(), settings=dict(DEFAULTS),
                               detectors=list(ALL_DETECTORS) + [ProjectBoom]).run()
        self.assertEqual(r.infos[a.id()].status, Status.ERROR)
        self.assertFalse(r.is_safe({a.id()}))

    def test_xml_safety_net(self):
        a, b = mk("A"), mk("B")
        a.setCustomProperty("someplugin/target", b.id())
        r = analyze()
        self.assertEqual(r.infos[b.id()].status, Status.BLOCKED)
        dep = r.infos[b.id()].reasons[0]
        self.assertEqual(dep.kind, DepKind.XML_REFERENCE)
        self.assertEqual(dep.certainty, Certainty.MEDIUM)
        r2 = analyze(use_xml_scan=False)
        self.assertNotEqual(r2.infos[b.id()].status, Status.BLOCKED)

    def test_join_not_double_reported_by_xml_scan(self):
        a, b = mk("A"), mk("B")
        add_join(a, b)
        r = analyze()
        kinds = [d.kind for d in r.infos[b.id()].reasons if d.dependent_id == a.id()]
        self.assertEqual(kinds, [DepKind.JOIN])

    def test_map_theme_info_only(self):
        from qgis.core import QgsMapThemeCollection
        a = mk("A")
        project = QgsProject.instance()
        rec = QgsMapThemeCollection.MapThemeLayerRecord(a)
        state = QgsMapThemeCollection.MapThemeRecord()
        state.setLayerRecords([rec])
        project.mapThemeCollection().insert("Téma", state)
        r = analyze()
        self.assertEqual(r.infos[a.id()].status, Status.WARN)  # only the memory warning
        self.assertTrue(any(d.kind == DepKind.MAP_THEME for d in r.infos[a.id()].result.infos))


class ArticleTest(unittest.TestCase):
    def test_article(self):
        self.assertEqual(article("Övezetek"), "az")
        self.assertEqual(article("Telkek"), "a")
        self.assertEqual(article("1. réteg"), "az")
        self.assertEqual(article("10 m"), "a")
        self.assertEqual(article("5 km"), "az")
        self.assertEqual(article("1000 pont"), "az")
        self.assertEqual(article("2023 ortofotó"), "a")
        self.assertEqual(article("OSM"), "az")
        self.assertEqual(article("SQL réteg"), "az")
        self.assertEqual(article("KSH"), "a")
        self.assertEqual(article("_temp"), "a(z)")


if __name__ == "__main__":
    unittest.main()
