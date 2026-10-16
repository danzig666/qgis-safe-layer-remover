"""Thin logging helpers around QgsMessageLog."""

from qgis.core import Qgis, QgsMessageLog

TAG = "SafeLayerRemover"


def log_info(msg: str) -> None:
    QgsMessageLog.logMessage(msg, TAG, Qgis.MessageLevel.Info)


def log_warning(msg: str) -> None:
    QgsMessageLog.logMessage(msg, TAG, Qgis.MessageLevel.Warning)


def log_critical(msg: str) -> None:
    QgsMessageLog.logMessage(msg, TAG, Qgis.MessageLevel.Critical)
