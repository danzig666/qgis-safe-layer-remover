"""Detector base class and the shared analysis context."""

from __future__ import annotations

import re
import traceback
from collections import defaultdict
from typing import Any, Callable, Iterable, Optional
from urllib.parse import unquote

from qgis.core import QgsMapLayer, QgsProject, QgsReadWriteContext, QgsVectorLayer
from qgis.PyQt.QtXml import QDomDocument

from ..model import Certainty, DepKind, Dependency, Severity
from ..texts import tr

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_ATTR_VALUE_RE = re.compile(r'="([^"]*)"')
_TEXT_RE = re.compile(r">([^<]+)<")


def layer_xml(layer: QgsMapLayer) -> str:
    doc = QDomDocument("qgis")
    el = doc.createElement("maplayer")
    doc.appendChild(el)
    ok = layer.writeLayerXml(el, doc, QgsReadWriteContext())
    if ok is False:
        raise RuntimeError(tr("A réteg XML leírása nem állítható elő."))
    return doc.toString()


class AnalysisContext:
    """Everything the detectors need, built once per analysis run."""

    def __init__(
        self,
        project: QgsProject,
        settings: Optional[dict] = None,
        iface: Any = None,
        xml_cache: Optional[dict[str, str]] = None,
        derived_cache: Optional[dict[str, dict]] = None,
        layout_cache: Optional[dict] = None,
    ):
        self.project = project
        self.iface = iface
        self.settings: dict = dict(settings or {})
        self.layers: list[QgsMapLayer] = list(project.mapLayers().values())
        self.layers_by_id: dict[str, QgsMapLayer] = {lyr.id(): lyr for lyr in self.layers}
        self.layers_by_name: dict[str, list[str]] = defaultdict(list)
        for lyr in self.layers:
            self.layers_by_name[lyr.name().lower()].append(lyr.id())
        self.layer_ids_lower: dict[str, str] = {lid.lower(): lid for lid in self.layers_by_id}
        try:
            self.relations_by_id = dict(project.relationManager().relations())
        except Exception:  # noqa: BLE001
            self.relations_by_id = {}
        self.xml_cache: dict[str, str] = xml_cache if xml_cache is not None else {}
        # values computed from a layer's XML (tokens, parsed expressions ...), dropped with the XML
        self.derived_cache: dict[str, dict] = derived_cache if derived_cache is not None else {}
        # layout name -> (signature, tokens of the layout XML): rebuilding it is the slowest part
        self.layout_cache: dict = layout_cache if layout_cache is not None else {}
        self.dependencies: list[Dependency] = []
        self.covered_pairs: set[tuple[Optional[str], str]] = set()
        self.errors: dict[str, list[str]] = defaultdict(list)
        self.global_errors: list[str] = []

    # ------------------------------------------------------------------
    @property
    def vector_layers(self) -> list[QgsVectorLayer]:
        return [lyr for lyr in self.layers if isinstance(lyr, QgsVectorLayer)]

    def name_of(self, layer_id: Optional[str]) -> str:
        lyr = self.layers_by_id.get(layer_id or "")
        return lyr.name() if lyr is not None else (layer_id or "?")

    def layer_xml(self, layer: QgsMapLayer) -> str:
        lid = layer.id()
        text = self.xml_cache.get(lid)
        if text is None:
            text = layer_xml(layer)
            self.xml_cache[lid] = text
        return text

    def cached(self, layer: QgsMapLayer, key: str, compute: Callable[[], Any]) -> Any:
        """Per-layer value derived from the layer's XML, cached until the layer changes."""
        bucket = self.derived_cache.setdefault(layer.id(), {})
        if key not in bucket:
            bucket[key] = compute()
        return bucket[key]

    def layer_tokens(self, layer: QgsMapLayer) -> frozenset:
        return self.cached(layer, "tokens", lambda: frozenset(_WORD_RE.findall(self.layer_text(layer))))

    def layer_text(self, layer: QgsMapLayer) -> str:
        """Layer XML plus the url-decoded source (virtual layer queries are percent-encoded in the XML)."""
        def build():
            text = self.layer_xml(layer)
            try:
                if layer.providerType() == "virtual":
                    text += "\n" + unquote(layer.source())
            except Exception:  # noqa: BLE001
                pass
            return text
        return self.cached(layer, "text", build)

    def layer_text_lower(self, layer: QgsMapLayer) -> str:
        return self.cached(layer, "lower", lambda: self.layer_text(layer).lower())

    def layer_values_lower(self, layer: QgsMapLayer) -> str:
        """Only the attribute values and text content of the layer XML, lower case.

        Layer names are searched here: tag and attribute names (e.g. alpha="1") must not match a layer
        called "alpha".
        """
        def build():
            xml = self.layer_xml(layer)
            parts = _ATTR_VALUE_RE.findall(xml) + _TEXT_RE.findall(xml)
            try:
                if layer.providerType() == "virtual":
                    parts.append(unquote(layer.source()))
            except Exception:  # noqa: BLE001
                pass
            return "\n".join(parts).lower()
        return self.cached(layer, "values_lower", build)

    def resolve_layer_ref(self, text: str) -> list[tuple[str, Certainty]]:
        """Resolve a layer reference given as id or name.

        id match -> HIGH, unique name match -> MEDIUM, ambiguous name -> LOW for every candidate.
        """
        if text is None:
            return []
        t = str(text).strip()
        if not t:
            return []
        if t in self.layers_by_id:
            return [(t, Certainty.HIGH)]
        if t.lower() in self.layer_ids_lower:
            return [(self.layer_ids_lower[t.lower()], Certainty.HIGH)]
        candidates = self.layers_by_name.get(t.lower(), [])
        if len(candidates) == 1:
            return [(candidates[0], Certainty.MEDIUM)]
        return [(cid, Certainty.LOW) for cid in candidates]

    def register(self, deps: Iterable[Dependency]) -> None:
        for dep in deps:
            self.dependencies.append(dep)
            self.covered_pairs.add(dep.pair())

    def record_error(self, layer_id: Optional[str], detector: str, exc: BaseException) -> str:
        msg = f"{detector}: {exc.__class__.__name__}: {exc}"
        if layer_id:
            self.errors[layer_id].append(msg)
        else:
            self.global_errors.append(msg)
        from ..log import log_warning
        log_warning(f"[{detector}] {layer_id or 'project'}: {exc}\n{traceback.format_exc()}")
        return msg


class BaseDetector:
    name: str = "base"

    def detect(self, project: QgsProject, ctx: AnalysisContext) -> list[Dependency]:
        """Default: run detect_layer() for every layer, isolating failures per layer."""
        out: list[Dependency] = []
        for layer in ctx.layers:
            try:
                out.extend(self.detect_layer(layer, project, ctx) or [])
            except Exception as exc:  # noqa: BLE001 - fail-closed
                ctx.record_error(layer.id(), self.name, exc)
        return out

    def detect_layer(self, layer: QgsMapLayer, project: QgsProject, ctx: AnalysisContext) -> list[Dependency]:
        return []

    # helper ----------------------------------------------------------------
    def dep(
        self,
        required_id: str,
        dependent: Optional[QgsMapLayer],
        kind: DepKind,
        severity: Severity,
        certainty: Certainty,
        detail: str = "",
        label: Optional[str] = None,
    ) -> Dependency:
        return Dependency(
            required_id=required_id,
            dependent_id=dependent.id() if dependent is not None else None,
            dependent_label=label if label is not None else (dependent.name() if dependent is not None else ""),
            kind=kind,
            severity=severity,
            certainty=certainty,
            detail=detail,
            detector=self.name,
        )

    def broken_ref(self, dependent: QgsMapLayer, missing_ref: str, what: str) -> Dependency:
        """INFO item on the dependent layer: it already references a non-existing layer."""
        return Dependency(
            required_id=dependent.id(),
            dependent_id=None,
            dependent_label=dependent.name(),
            kind=DepKind.BROKEN_REFERENCE,
            severity=Severity.INFO,
            certainty=Certainty.HIGH,
            detail=tr("már most is hibás hivatkozást tartalmaz ({what}: {ref})").format(what=what, ref=missing_ref),
            detector=self.name,
        )
