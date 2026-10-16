"""Virtual layers: declared source layers (layer_ref) and layers referenced only in the SQL query."""

from __future__ import annotations

import re
from urllib.parse import unquote

from qgis.core import QgsVectorLayer
from qgis.PyQt.QtCore import QUrl

from ..model import Certainty, DepKind, Severity
from ..texts import excerpt, tr
from .base import BaseDetector

_LAYER_REF_RE = re.compile(r"layer_ref=([^&]+)")
_QUERY_RE = re.compile(r"(?:^|[?&])query=([^&]*)")


def _parse_definition(source: str):
    """Return (referenced [(layer_id, alias)], declared aliases set, query) for a virtual layer source."""
    refs: list[tuple[str, str]] = []
    aliases: set[str] = set()
    query = ""
    try:
        from qgis.core import QgsVirtualLayerDefinition
        d = QgsVirtualLayerDefinition.fromUrl(QUrl.fromEncoded(source.encode("utf-8")))
        for sl in d.sourceLayers():
            aliases.add(sl.name().lower())
            if sl.isReferenced():
                refs.append((sl.reference(), sl.name()))
            # an embedded (provider + source) layer does not depend on a project layer
        query = d.query() or ""
        if refs or query or d.sourceLayers():
            return refs, aliases, query
    except Exception:  # noqa: BLE001 - fall back to regex parsing
        pass
    # Fallback: regex on the url-decoded source
    for m in _LAYER_REF_RE.finditer(source):
        val = unquote(m.group(1))
        layer_id, _, alias = val.partition(":")
        refs.append((layer_id, alias or layer_id))
        aliases.add((alias or layer_id).lower())
    for m in re.finditer(r"layer=[^:&]*:[^:&]*:([^:&]+)", source):
        aliases.add(unquote(m.group(1)).lower())
    qm = _QUERY_RE.search(source)
    if qm:
        query = unquote(qm.group(1).replace("+", " "))
    return refs, aliases, query


def _query_tokens(query: str) -> list[tuple[str, bool]]:
    """Tokens that could be table names: (token, after_from_or_join)."""
    # remove comments and single quoted string literals
    q = re.sub(r"--[^\n]*", " ", query)
    q = re.sub(r"/\*.*?\*/", " ", q, flags=re.S)
    q = re.sub(r"'(?:[^']|'')*'", " ", q)
    tokens: list[tuple[str, bool]] = []
    pattern = re.compile(r'"((?:[^"]|"")+)"|\[([^\]]+)\]|`([^`]+)`|([^\W\d][\w]*)', re.UNICODE)
    prev_kw = False
    for m in pattern.finditer(q):
        tok = m.group(1) or m.group(2) or m.group(3) or m.group(4)
        if m.group(1):
            tok = tok.replace('""', '"')
        tokens.append((tok, prev_kw))
        prev_kw = (m.group(4) or "").upper() in ("FROM", "JOIN")
    return tokens


class VirtualLayerDetector(BaseDetector):
    name = "virtual_layers"

    def detect_layer(self, layer, project, ctx):
        if not isinstance(layer, QgsVectorLayer) or layer.providerType() != "virtual":
            return []
        out = []
        refs, aliases, query = _parse_definition(layer.source())
        declared: set[str] = set()
        for layer_id, alias in refs:
            if layer_id in ctx.layers_by_id:
                declared.add(layer_id)
                out.append(self.dep(layer_id, layer, DepKind.VIRTUAL_LAYER, Severity.BLOCK, Certainty.HIGH,
                                    tr("forrásként, '{a}' álnéven").format(a=alias)))
            else:
                out.append(self.broken_ref(layer, layer_id, tr("virtuális réteg forrása")))

        if query:
            seen: set[str] = set()
            for tok, after_kw in _query_tokens(query):
                low = tok.lower()
                if low in aliases:
                    continue
                matches = []
                if tok in ctx.layers_by_id or low in ctx.layer_ids_lower:
                    lid = tok if tok in ctx.layers_by_id else ctx.layer_ids_lower[low]
                    matches = [(lid, Certainty.MEDIUM)]
                else:
                    cands = ctx.layers_by_name.get(low, [])
                    if len(cands) == 1:
                        matches = [(cands[0], Certainty.MEDIUM)]
                    elif cands:
                        matches = [(c, Certainty.LOW) for c in cands]
                for lid, cert in matches:
                    if lid == layer.id() or lid in declared or lid in seen:
                        continue
                    seen.add(lid)
                    where = tr("a lekérdezés hivatkozik rá: {q}").format(q=excerpt(query))
                    if not after_kw:
                        where = tr("a lekérdezésben szerepel a neve: {q}").format(q=excerpt(query))
                    out.append(self.dep(lid, layer, DepKind.VIRTUAL_LAYER, Severity.BLOCK, cert, where))
        return out
