# Safe Layer Remover for QGIS

Removing a layer in QGIS is easy. Finding out afterwards that it was the join target of three other
layers, the source of a virtual layer and the atlas of your print layout is less fun.

This plugin lists every layer of the project, shows what depends on what, and only lets you remove
layers whose removal doesn't break anything else.

![Main window](docs/window.png)

## What it checks

- joins, virtual layers (declared sources and table names in the SQL), relations, value relation and
  relation reference widgets, layer dependencies, group layers
- expressions that point to other layers (`get_feature`, `aggregate`, `overlay_*`, …) in expression
  fields, defaults, constraints, map tips, actions, symbology and labels
- print layouts: locked layer sets, maps following a map theme, live maps, legends, attribute tables,
  atlas coverage
- memory layers (data loss) and unsaved edits

Indirect effects are followed too: if C uses A and A uses B, removing B breaks C. Several layers can be
removed together when they only depend on each other ("Select chain").

![Confirmation](docs/confirm.png)

Removed layers go to a recycle bin and can be restored, memory layer data included. If you remove
something from the normal Layers panel that breaks other layers, the plugin notices and offers to put it
back. Layers that others depend on get a small marker in the Layers panel.

![Warning after removing a layer from the Layers panel](docs/sentinel.png)

The UI is English, or Hungarian on a Hungarian QGIS.

## Install

Download `safe_layer_remover.zip` from the
[latest release](https://github.com/danzig666/qgis-safe-layer-remover/releases/latest) and use
*Plugins › Manage and Install Plugins › Install from ZIP*.

Works with QGIS 3.34 and newer, including QGIS 4 (Qt6).

## Tests

```
python-qgis.bat safe_layer_remover/tests/run_tests.py
```

`tests/build_demo_project.py` builds a small project with every kind of dependency, handy for trying
things out.

## License

GPL-2.0-or-later
