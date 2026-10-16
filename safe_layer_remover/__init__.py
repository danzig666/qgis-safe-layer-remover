"""SafeLayerRemover QGIS plugin entry point."""


def classFactory(iface):  # noqa: N802 - QGIS API
    from .plugin import SafeLayerRemoverPlugin

    return SafeLayerRemoverPlugin(iface)
