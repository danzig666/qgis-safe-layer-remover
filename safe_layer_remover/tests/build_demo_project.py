"""Build a demo project that exercises every dependency type.

Inside QGIS (Python console):
    exec(open(r"<plugin folder>/tests/build_demo_project.py", encoding="utf-8").read()); build_demo()

Standalone (saves a .qgz next to a GeoPackage):
    python-qgis.bat build_demo_project.py C:/temp/slr_demo
"""

from __future__ import annotations

import os
import sys

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsEditorWidgetSetup,
    QgsFeature,
    QgsGeometry,
    QgsLayerTreeGroup,
    QgsMapLayerDependency,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRelation,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerJoinInfo,
)


def _memory(name, geom="Polygon", fields="field=id:integer&field=hrsz:string&field=ovezet:string"):
    return QgsVectorLayer(f"{geom}?crs=EPSG:23700&{fields}", name, "memory")


def _fill_polygons(layer, n, x0=650000, y0=240000):
    feats = []
    for i in range(n):
        f = QgsFeature(layer.fields())
        attrs = [i + 1, f"{100 + i}/1", "Lk" if i % 2 else "Gksz"][: layer.fields().count()]
        f.setAttributes(attrs + [None] * (layer.fields().count() - len(attrs)))
        x, y = x0 + (i % 5) * 120, y0 + (i // 5) * 120
        f.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(x, y), QgsPointXY(x + 100, y), QgsPointXY(x + 100, y + 100),
                                                   QgsPointXY(x, y + 100), QgsPointXY(x, y)]]))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()


def _to_gpkg(layer, path, table):
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = table
    if os.path.exists(path):
        opts.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
    QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsCoordinateTransformContext(), opts)
    return QgsVectorLayer(f"{path}|layername={table}", layer.name(), "ogr")


def build_demo(folder: str | None = None) -> QgsProject:
    project = QgsProject.instance()
    project.clear()
    root = project.layerTreeRoot()

    def add(layer, group: QgsLayerTreeGroup | None = None):
        project.addMapLayer(layer, False)
        (group or root).addLayer(layer)
        return layer

    gpkg = os.path.join(folder, "demo.gpkg") if folder else None

    def persist(layer, table):
        if gpkg is None:
            return layer
        return _to_gpkg(layer, gpkg, table)

    alap = root.addGroup("Alaptérképek")
    terv = root.addGroup("Tervezés")
    seged = root.addGroup("Segédrétegek")

    osm = QgsVectorLayer("type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0",
                         "OSM", "wms")
    if not osm.isValid():
        from qgis.core import QgsRasterLayer
        osm = QgsRasterLayer("type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0",
                             "OSM", "wms")
    add(osm, alap)

    parcellak_mem = _memory("Parcellák")
    _fill_polygons(parcellak_mem, 10)
    parcellak = add(persist(parcellak_mem, "parcellak"), terv)

    telkek_mem = _memory("Telkek")
    _fill_polygons(telkek_mem, 10)
    telkek = add(persist(telkek_mem, "telkek"), terv)

    ovezetek_mem = _memory("Övezetek", fields="field=id:integer&field=hrsz:string&field=ovezet:string")
    _fill_polygons(ovezetek_mem, 4)
    ovezetek = add(persist(ovezetek_mem, "ovezetek"), terv)

    # Telkek joins Parcellák (Parcellák is required by Telkek)
    join = QgsVectorLayerJoinInfo()
    join.setJoinLayer(parcellak)
    join.setJoinFieldName("hrsz")
    join.setTargetFieldName("hrsz")
    join.setPrefix("parc_")
    telkek.addJoin(join)

    # Övezetek joins Telkek -> indirect chain Parcellák -> Telkek -> Övezetek
    join2 = QgsVectorLayerJoinInfo()
    join2.setJoinLayer(telkek)
    join2.setJoinFieldName("hrsz")
    join2.setTargetFieldName("hrsz")
    ovezetek.addJoin(join2)

    # virtual layer on Telkek
    stat = QgsVectorLayer(f"?layer_ref={telkek.id()}:t&query=SELECT ovezet, count(*) AS db FROM t GROUP BY ovezet",
                          "Telek_stat", "virtual")
    add(stat, terv)

    # expression field referring to Telek_stat by name
    riport = _memory("Riport", geom="None", fields="field=id:integer")
    from qgis.core import QgsField
    try:
        from qgis.PyQt.QtCore import QMetaType
        fld = QgsField("db", QMetaType.Type.Int)
    except Exception:  # noqa: BLE001
        from qgis.PyQt.QtCore import QVariant
        fld = QgsField("db", QVariant.Int)
    add(riport, terv)
    riport.addExpressionField("aggregate('Telek_stat', 'sum', \"db\")", fld)

    # relation Övezetek_kod (parent) <- Telkek (child)
    kodok_mem = _memory("Övezeti kódok", geom="None", fields="field=kod:string&field=nev:string")
    kodok = add(persist(kodok_mem, "ovezeti_kodok"), seged)
    rel = QgsRelation()
    rel.setId("telek_ovezet")
    rel.setName("Telek övezete")
    rel.setReferencingLayer(telkek.id())
    rel.setReferencedLayer(kodok.id())
    rel.addFieldPair("ovezet", "kod")
    if rel.isValid():
        project.relationManager().addRelation(rel)

    # value relation widget on Parcellák -> Jogcímek
    jogcim_mem = _memory("Jogcímek", geom="None", fields="field=kod:string&field=nev:string")
    jogcim = add(persist(jogcim_mem, "jogcimek"), seged)
    idx = parcellak.fields().indexOf("ovezet")
    parcellak.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("ValueRelation", {"Layer": jogcim.id(), "Key": "kod",
                                                                                "Value": "nev"}))

    # explicit dependency + temporary memory layers
    pontok = _memory("ideiglenes_pontok", geom="Point", fields="field=id:integer")
    add(pontok, seged)
    f = QgsFeature(pontok.fields())
    f.setAttributes([1])
    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(650050, 240050)))
    pontok.dataProvider().addFeatures([f])
    add(_memory("régi_vázlat", geom="LineString", fields="field=id:integer"), seged)
    segedvonal = add(_memory("Segédvonal", geom="LineString", fields="field=id:integer"), seged)
    segedvonal.setDependencies([QgsMapLayerDependency(pontok.id())])

    # two layers with the same name, one referenced ambiguously by a virtual layer query
    add(_memory("Épületek", geom="Polygon", fields="field=id:integer"), seged)
    add(_memory("Épületek", geom="Polygon", fields="field=id:integer"), seged)
    epstat = QgsVectorLayer('?query=SELECT count(*) AS db FROM "Épületek"', "Épület_stat", "virtual")
    if epstat.isValid():
        add(epstat, seged)

    # layout with atlas on Övezetek
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("A3 terv")
    layout.atlas().setEnabled(True)
    layout.atlas().setCoverageLayer(ovezetek)
    project.layoutManager().addLayout(layout)

    alap.setExpanded(True)
    terv.setExpanded(True)
    seged.setExpanded(True)

    if folder:
        project.write(os.path.join(folder, "slr_demo.qgz"))
    return project


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import utilities  # noqa: F401 - starts QGIS

    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.getcwd(), "slr_demo")
    os.makedirs(target, exist_ok=True)
    build_demo(target)
    print("Demo project written to", os.path.join(target, "slr_demo.qgz"))
    utilities.QGISAPP.exitQgis()
    os._exit(0)
