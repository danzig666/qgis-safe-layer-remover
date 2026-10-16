"""Dependency detectors. The order matters: xml_id_scan must run last."""

from .base import AnalysisContext, BaseDetector
from .expressions import ExpressionDetector
from .form_widgets import FormWidgetDetector
from .group_layers import GroupLayerDetector
from .joins import JoinDetector
from .layer_dependencies import LayerDependencyDetector
from .masking import MaskingDetector
from .project_objects import ProjectObjectDetector
from .relations import RelationDetector
from .self_effects import SelfEffectDetector
from .virtual_layers import VirtualLayerDetector
from .xml_id_scan import XmlIdScanDetector

ALL_DETECTORS = [
    JoinDetector,
    VirtualLayerDetector,
    RelationDetector,
    FormWidgetDetector,
    LayerDependencyDetector,
    GroupLayerDetector,
    ExpressionDetector,
    MaskingDetector,
    ProjectObjectDetector,
    SelfEffectDetector,
    XmlIdScanDetector,
]

__all__ = ["ALL_DETECTORS", "AnalysisContext", "BaseDetector"]
