"""Expressions referencing other layers (get_feature, aggregate, overlay_*, layer_property ...)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Iterator, Optional

import qgis.core as qcore
from qgis.core import (
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFields,
    QgsVectorLayer,
)

from ..model import Certainty, DepKind, Severity
from ..texts import excerpt, tr
from .base import BaseDetector

LAYER_FUNCTIONS = {
    "get_feature", "get_feature_by_id", "aggregate",
    "overlay_intersects", "overlay_contains", "overlay_crosses", "overlay_equals",
    "overlay_touches", "overlay_within", "overlay_disjoint", "overlay_nearest",
    "overlay_equals_exact", "overlay_equals_fuzzy", "overlay_equals_topological",
    "layer_property", "is_layer_visible", "decode_uri",
}
RELATION_FUNCTIONS = {"relation_aggregate"}  # first argument: relation id

_PREFILTER = re.compile(
    r"\b(" + "|".join(sorted(LAYER_FUNCTIONS | RELATION_FUNCTIONS, key=len, reverse=True)) + r")\s*\(",
    re.IGNORECASE,
)
_TEMPLATE_BLOCK = re.compile(r"\[%(.*?)%\]", re.S)

_NodeFunction = getattr(qcore, "QgsExpressionNodeFunction", None)
_NodeLiteral = getattr(qcore, "QgsExpressionNodeLiteral", None)

# XML tags -> human readable location. Tags covered by the typed sources are skipped (None).
_XML_LOCATIONS = [
    ("expressionfields", None),
    ("defaults", None),
    ("constraintExpressions", None),
    ("previewExpression", None),
    ("mapTip", None),
    ("attributeactions", None),
    ("rules", "szabályalapú szimbólum"),
    ("labeling", "címkézés"),
    ("SingleCategoryDiagramRenderer", "diagram"),
    ("LinearlyInterpolatedDiagramRenderer", "diagram"),
    ("StackedDiagramRenderer", "diagram"),
    ("renderer-v2", "szimbológia"),
    ("geometryOptions", "geometria beállítások"),
    ("attributetableconfig", "attribútumtábla"),
    ("editform", "űrlap"),
    ("attributeEditorForm", "űrlap"),
    ("conditionalstyles", "feltételes formázás"),
    ("elevation", "magassági beállítások"),
    ("temporal", "időbeli beállítások"),
]


def _origin_expression():
    # VERIFY: Qgis.FieldOrigin.Expression (3.38+) vs QgsFields.FieldOrigin.OriginExpression (older)
    fo = getattr(qcore.Qgis, "FieldOrigin", None)
    if fo is not None and hasattr(fo, "Expression"):
        return fo.Expression
    fo = getattr(QgsFields, "FieldOrigin", None)
    if fo is not None and hasattr(fo, "OriginExpression"):
        return fo.OriginExpression
    return getattr(QgsFields, "OriginExpression", None)


def _children(node) -> list:
    """Generic child enumeration for expression nodes (guarded with hasattr)."""
    out = []
    if node is None:
        return out
    if hasattr(node, "args") and node.args() is not None:
        out.extend(node.args().list())
    for attr in ("opLeft", "opRight", "operand", "container", "index", "lowerBound", "higherBound"):
        if hasattr(node, attr):
            try:
                out.append(getattr(node, attr)())
            except Exception:  # noqa: BLE001
                pass
    if hasattr(node, "node") and callable(node.node):
        try:
            out.append(node.node())
        except Exception:  # noqa: BLE001
            pass
    if hasattr(node, "list") and callable(node.list) and not hasattr(node, "args"):
        try:
            lst = node.list()
            out.extend(lst.list() if hasattr(lst, "list") else lst)
        except Exception:  # noqa: BLE001
            pass
    if hasattr(node, "conditions"):
        try:
            for wt in node.conditions():
                out.append(wt.whenExp())
                out.append(wt.thenExp())
        except Exception:  # noqa: BLE001
            pass
        if hasattr(node, "elseExp"):
            out.append(node.elseExp())
    return [c for c in out if c is not None and hasattr(c, "nodeType")]


def _walk(node) -> Iterator:
    stack = [node]
    seen = 0
    while stack and seen < 100000:
        n = stack.pop()
        seen += 1
        yield n
        stack.extend(_children(n))


def _literals(node) -> list[str]:
    return [str(n.value()) for n in _walk(node)
            if _NodeLiteral is not None and isinstance(n, _NodeLiteral) and isinstance(n.value(), str)]


class ExpressionDetector(BaseDetector):
    name = "expressions"

    def __init__(self):
        self._fn_names: Optional[list[str]] = None

    def _fn_name(self, node) -> str:
        if self._fn_names is None:
            self._fn_names = [f.name().lower() for f in QgsExpression.Functions()]
        idx = node.fnIndex()
        return self._fn_names[idx] if 0 <= idx < len(self._fn_names) else ""

    # ------------------------------------------------------------------
    def _sources(self, layer: QgsVectorLayer, ctx) -> list[tuple[str, str]]:
        sources: list[tuple[str, str]] = []
        fields = layer.fields()
        origin_expr = _origin_expression()
        for i in range(fields.count()):
            fname = fields.at(i).name()
            try:
                if origin_expr is not None and fields.fieldOrigin(i) == origin_expr:
                    sources.append((tr("'{f}' kifejezésmező").format(f=fname), layer.expressionField(i)))
            except Exception:  # noqa: BLE001
                pass
            dv = layer.defaultValueDefinition(i).expression()
            if dv:
                sources.append((tr("'{f}' alapértelmezett értéke").format(f=fname), dv))
            ce = layer.constraintExpression(i)
            if ce:
                sources.append((tr("'{f}' megszorítása").format(f=fname), ce))
        if layer.displayExpression():
            sources.append((tr("megjelenítési kifejezés"), layer.displayExpression()))
        for block in _TEMPLATE_BLOCK.findall(layer.mapTipTemplate() or ""):
            sources.append((tr("térképtipp"), block))
        try:
            for action in layer.actions().actions():
                for block in _TEMPLATE_BLOCK.findall(action.command() or ""):
                    sources.append((tr("művelet: {n}").format(n=action.name()), block))
        except Exception:  # noqa: BLE001
            pass
        sources.extend(self._xml_sources(layer, ctx))
        # dedupe
        seen = set()
        out = []
        for where, text in sources:
            key = (where, text)
            if text and key not in seen:
                seen.add(key)
                out.append((where, text))
        return out

    def _xml_sources(self, layer, ctx) -> list[tuple[str, str]]:
        xml = ctx.layer_xml(layer)
        if not _PREFILTER.search(xml):
            return []
        root = ET.fromstring(xml.encode("utf-8"))
        out: list[tuple[str, str]] = []

        def visit(el, location: Optional[str], skip: bool):
            tag = el.tag
            for t, label in _XML_LOCATIONS:
                if tag == t:
                    if label is None:
                        skip = True
                    elif location is None:
                        location = label
                    break
            if not skip:
                values = list(el.attrib.values())
                if el.text:
                    values.append(el.text)
                for v in values:
                    if v and _PREFILTER.search(v):
                        blocks = _TEMPLATE_BLOCK.findall(v) if "[%" in v else [v]
                        for b in blocks:
                            out.append((location or tr("réteg beállítás"), b))
            for child in el:
                visit(child, location, skip)

        visit(root, None, False)
        return out

    # ------------------------------------------------------------------
    def detect_layer(self, layer, project, ctx):
        if not isinstance(layer, QgsVectorLayer):
            return []
        out = []
        for where, text in self._sources(layer, ctx):
            if not _PREFILTER.search(text):
                continue
            out.extend(self._analyze(layer, where, text, ctx))
        return out

    def _analyze(self, layer, where, text, ctx):
        out = []
        detail_base = f"{where}: {excerpt(text)}"
        expr = QgsExpression(text)
        root = expr.rootNode() if not expr.hasParserError() else None
        if root is None or _NodeFunction is None:
            # regex fallback on quoted strings, LOW certainty
            for m in re.finditer(r"\b(\w+)\s*\(\s*['\"]([^'\"]+)['\"]", text):
                fn = m.group(1).lower()
                if fn in LAYER_FUNCTIONS:
                    for lid, _c in ctx.resolve_layer_ref(m.group(2)):
                        if lid != layer.id():
                            out.append(self.dep(lid, layer, DepKind.EXPRESSION, Severity.BLOCK, Certainty.LOW,
                                                detail_base + " " + tr("(nem értelmezhető kifejezés)")))
            return out

        for node in _walk(root):
            if not isinstance(node, _NodeFunction):
                continue
            fn = self._fn_name(node)
            if fn not in LAYER_FUNCTIONS and fn not in RELATION_FUNCTIONS:
                continue
            args = node.args().list() if node.args() is not None else []
            if not args:
                continue
            arg = args[0]
            is_literal = _NodeLiteral is not None and isinstance(arg, _NodeLiteral)
            detail = f"{detail_base} ({fn})"
            if fn in RELATION_FUNCTIONS:
                rel_id = str(arg.value()) if is_literal else ""
                rel = ctx.relations_by_id.get(rel_id)
                if rel is None:
                    if is_literal:
                        out.append(self.broken_ref(layer, rel_id, tr("kapcsolat")))
                    continue
                for lid in (rel.referencingLayerId(), rel.referencedLayerId()):
                    if lid and lid != layer.id() and lid in ctx.layers_by_id:
                        out.append(self.dep(lid, layer, DepKind.EXPRESSION, Severity.BLOCK, Certainty.HIGH, detail))
                continue

            if is_literal:
                value = arg.value()
                if not isinstance(value, str):
                    continue
                resolved = ctx.resolve_layer_ref(value)
                if not resolved:
                    out.append(self.broken_ref(layer, value, tr("kifejezés")))
                for lid, cert in resolved:
                    if lid != layer.id():
                        out.append(self.dep(lid, layer, DepKind.EXPRESSION, Severity.BLOCK, cert, detail))
                continue

            # dynamic (non literal) layer parameter
            candidates = self._dynamic_candidates(layer, arg, ctx)
            dyn_detail = detail + " – " + tr("dinamikus rétegparaméter, nem ellenőrizhető")
            if candidates:
                for lid in candidates:
                    if lid != layer.id():
                        out.append(self.dep(lid, layer, DepKind.EXPRESSION, Severity.BLOCK, Certainty.LOW, dyn_detail))
            else:
                out.append(self.dep(layer.id(), None, DepKind.DYNAMIC_REFERENCE, Severity.INFO, Certainty.LOW,
                                    dyn_detail, label=layer.name()))
        return out

    @staticmethod
    def _dynamic_candidates(layer, arg, ctx) -> list[str]:
        found: list[str] = []
        try:
            sub = QgsExpression(arg.dump())
            ectx = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            value = sub.evaluate(ectx)
            if not sub.hasEvalError():
                if isinstance(value, str):
                    found.extend(lid for lid, _ in ctx.resolve_layer_ref(value))
                elif hasattr(value, "id") and callable(getattr(value, "id")):
                    found.append(value.id())
        except Exception:  # noqa: BLE001
            pass
        for lit in _literals(arg):
            found.extend(lid for lid, _ in ctx.resolve_layer_ref(lit))
        return list(dict.fromkeys(found))
