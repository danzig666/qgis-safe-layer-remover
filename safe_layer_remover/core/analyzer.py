"""DependencyAnalyzer: runs the detectors, caches layer XML, assembles LayerInfo results."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from qgis.core import QgsMapLayer, QgsProject

from . import settings as plugin_settings
from .classifier import classify
from .detectors import ALL_DETECTORS, AnalysisContext
from .graph import DependencyGraph
from .locks import locked_ids as user_locked_ids
from .log import log_info
from .model import LAYOUT_KINDS, Badge, Dependency, LayerInfo, SetResult, Status
from .texts import tr


def _flag_set(flags, flag) -> bool:
    try:
        return bool(flags & flag)
    except TypeError:
        return (int(flags) & int(flag)) != 0


def is_locked(layer: QgsMapLayer, project: QgsProject) -> bool:
    # VERIFY: QgsMapLayer.LayerFlag.Removable (scoped) vs QgsMapLayer.Removable (Qt5 unscoped)
    removable = getattr(getattr(QgsMapLayer, "LayerFlag", QgsMapLayer), "Removable", None)
    if removable is None:
        removable = getattr(QgsMapLayer, "Removable", None)
    if removable is not None and not _flag_set(layer.flags(), removable):
        return True
    try:
        if hasattr(project, "requiredLayers") and layer in project.requiredLayers():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


@dataclass
class AnalysisResult:
    infos: dict[str, LayerInfo]
    graph: DependencyGraph
    dependencies: list[Dependency]
    errors: dict[str, list[str]] = field(default_factory=dict)
    global_errors: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    duration: float = 0.0
    names: dict[str, str] = field(default_factory=dict)

    def name_of(self, layer_id: Optional[str]) -> str:
        if layer_id is None:
            return ""
        return self.names.get(layer_id, layer_id)

    def evaluate(self, members: Iterable[str]) -> SetResult:
        res = self.graph.evaluate(members)
        if self.global_errors:
            res.errors = sorted(set(res.errors) | set(res.members))
        return res

    def is_safe(self, members: Iterable[str]) -> bool:
        return self.evaluate(members).is_safe(self.settings.get("uncertain_is_blocked", True))

    def counts(self) -> dict[Status, int]:
        out = {s: 0 for s in Status}
        for info in self.infos.values():
            out[info.status] += 1
        return out


class DependencyAnalyzer:
    def __init__(
        self,
        project: Optional[QgsProject] = None,
        iface: Any = None,
        settings: Optional[dict] = None,
        detectors: Optional[list] = None,
    ):
        self._project = project
        self.iface = iface
        self._settings_override = settings
        self.detector_classes = list(detectors) if detectors is not None else list(ALL_DETECTORS)
        self.xml_cache: dict[str, str] = {}
        self.derived_cache: dict[str, dict] = {}
        self.layout_cache: dict = {}
        self._collected: Optional[dict] = None   # result of the last full detector run
        self._stale = True
        self.last_result: Optional[AnalysisResult] = None

    @property
    def project(self) -> QgsProject:
        return self._project or QgsProject.instance()

    def settings(self) -> dict:
        values = plugin_settings.load()
        if self._settings_override:
            values.update(self._settings_override)
        return values

    def invalidate(self, layer_id: Optional[str] = None) -> None:
        self._stale = True
        if layer_id is None:
            self.xml_cache.clear()
            self.derived_cache.clear()
            self.layout_cache.clear()
        else:
            self.xml_cache.pop(layer_id, None)
            self.derived_cache.pop(layer_id, None)

    # ------------------------------------------------------------------
    def may_be_referenced(self, layer_ids) -> bool:
        """Fast check (no full analysis): could anything break if these layers were removed?

        False only when no other layer mentions their id or name anywhere in its settings, no relation
        uses them and no setting turns layout usage into a blocker. Uses the per-layer caches, so it is
        cheap once an analysis has run. When in doubt it returns True.
        """
        project = self.project
        ids = set(layer_ids)
        names = set()
        for lid in ids:
            layer = project.mapLayer(lid)
            if layer is not None and layer.name():
                names.add(layer.name().lower())
        settings = self.settings()
        if settings.get("block_on_layout") or settings.get("block_on_atlas"):
            return True
        try:
            for rel in project.relationManager().relations().values():
                if rel.referencingLayerId() in ids or rel.referencedLayerId() in ids:
                    return True
        except Exception:  # noqa: BLE001
            return True
        # whole-word match: a layer called "alpha" must not match every symbol's alpha="1" attribute
        name_patterns = [(n, re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)")) for n in names]
        ctx = AnalysisContext(project, settings=settings, iface=self.iface, xml_cache=self.xml_cache,
                              derived_cache=self.derived_cache)
        for lid, layer in ctx.layers_by_id.items():
            if lid in ids:
                continue
            try:
                if ctx.layer_tokens(layer) & ids:
                    return True
                if name_patterns:
                    low = ctx.layer_values_lower(layer)
                    if any(name in low and pattern.search(low) for name, pattern in name_patterns):
                        return True
            except Exception:  # noqa: BLE001 - fail-closed
                return True
        return False

    def forget(self, layer_id: str) -> None:
        """A layer was removed: drop its caches. The collected dependencies stay valid."""
        self.xml_cache.pop(layer_id, None)
        self.derived_cache.pop(layer_id, None)

    def mark_stale(self) -> None:
        """Something that can add or change a dependency happened: collect again on the next run."""
        self._stale = True

    @staticmethod
    def _layouts_signature(project: QgsProject) -> tuple:
        """Cheap fingerprint of all layouts; designer edits change it (layouts have no change signal)."""
        from .detectors.project_objects import ProjectObjectDetector
        try:
            return tuple(ProjectObjectDetector._layout_signature(lay)  # noqa: SLF001
                         for lay in project.layoutManager().layouts())
        except Exception:  # noqa: BLE001
            return (object(),)   # never equal: forces a new collection

    def _needs_collect(self, project: QgsProject, settings: dict, layout_sig: tuple) -> bool:
        last = self._collected
        if self._stale or last is None:
            return True
        if last["settings"] != settings or last["layout_sig"] != layout_sig:
            return True
        # a layer that appeared without us noticing could refer to anything
        return not set(project.mapLayers().keys()) <= last["layer_ids"]

    def _collect(self, project: QgsProject, settings: dict, layout_sig: tuple) -> None:
        """The expensive part: run every detector on every layer."""
        current = set(project.mapLayers().keys())
        for cache in (self.xml_cache, self.derived_cache):
            for lid in list(cache):
                if lid not in current:
                    del cache[lid]
        ctx = AnalysisContext(project, settings=settings, iface=self.iface, xml_cache=self.xml_cache,
                              derived_cache=self.derived_cache, layout_cache=self.layout_cache)
        for cls in self.detector_classes:
            detector = cls()
            try:
                deps = detector.detect(project, ctx)
            except Exception as exc:  # noqa: BLE001 - fail-closed for the whole project
                ctx.record_error(None, getattr(detector, "name", cls.__name__), exc)
                continue
            ctx.register(deps)
        self._collected = {
            "dependencies": list(dict.fromkeys(ctx.dependencies)),   # deduplicated
            "errors": {k: list(v) for k, v in ctx.errors.items()},
            "global_errors": list(ctx.global_errors),
            "settings": dict(settings),
            "layout_sig": layout_sig,
            "layer_ids": current,
        }
        self._stale = False

    def _current_graph(self, project: QgsProject):
        """Graph of the last collection, restricted to the layers that still exist.

        Removing layers never creates a new reference, so dropping the removed layers from the last
        collection gives the same answer as a new collection.
        """
        col = self._collected
        layers = project.mapLayers()
        ids = set(layers.keys())
        deps = [d for d in col["dependencies"]
                if d.required_id in ids and (d.dependent_id is None or d.dependent_id in ids)]
        errors = {k: v for k, v in col["errors"].items() if k in ids}
        user_locked = user_locked_ids(project) & ids
        locked = {lid for lid, lyr in layers.items() if is_locked(lyr, project)} | user_locked
        error_ids = set(errors.keys())
        if col["global_errors"]:
            error_ids |= ids
        return DependencyGraph(deps, ids, locked, error_ids), deps, errors, user_locked

    def graph_snapshot(self):
        """(graph, user locked ids) for the current project, collecting only when needed.

        Much cheaper than run(): no per-layer badges, closures or explanations.
        """
        project = self.project
        settings = self.settings()
        layout_sig = self._layouts_signature(project)
        if self._needs_collect(project, settings, layout_sig):
            self._collect(project, settings, layout_sig)
        graph, _deps, _errors, user_locked = self._current_graph(project)
        return graph, user_locked

    def evaluate_if_fresh(self, layer_ids) -> Optional[SetResult]:
        """Evaluate a removal set from the last collection, or None when that collection is outdated."""
        project = self.project
        settings = self.settings()
        if self._needs_collect(project, settings, self._layouts_signature(project)):
            return None
        graph, _deps, _errors, _locked = self._current_graph(project)
        res = graph.evaluate(set(layer_ids))
        if self._collected["global_errors"]:
            res.errors = sorted(set(res.errors) | set(res.members))
        return res

    def run(self, force: bool = False) -> AnalysisResult:
        t0 = time.perf_counter()
        project = self.project
        settings = self.settings()
        layout_sig = self._layouts_signature(project)
        collected = force or self._needs_collect(project, settings, layout_sig)
        if collected:
            self._collect(project, settings, layout_sig)
        col = self._collected
        graph, unique, errors, user_locked = self._current_graph(project)
        layers_by_id = project.mapLayers()
        global_errors = col["global_errors"]

        result = AnalysisResult(
            infos={},
            graph=graph,
            dependencies=unique,
            errors=errors,
            global_errors=list(global_errors),
            settings=settings,
            names={lid: lyr.name() for lid, lyr in layers_by_id.items()},
        )

        for lid, layer in layers_by_id.items():
            res = graph.evaluate({lid})
            status = res.status()
            error = None
            if lid in errors:
                error = "\n".join(errors[lid])
            elif global_errors:
                error = tr("A projektszintű elemzés sikertelen: {e}").format(e="; ".join(global_errors))
            if error:
                status = Status.ERROR
            reasons: list[Dependency] = []
            for victim in res.victims.values():
                if victim.direct:
                    # every edge that leads into the victim, not only the one the BFS used
                    edges = graph.edges_into(victim.layer_id, {lid}) or victim.chain[:1]
                    reasons.extend(sorted(edges, key=lambda d: (-d.certainty.value, d.kind.value)))
            reasons.extend(res.blocking_objects)
            reasons.extend(res.warnings)
            layout_uses = [d for d in graph.by_required.get(lid, ()) if d.kind.value in LAYOUT_KINDS]
            badges = classify(layer, project)
            layout_names = sorted({d.object_ref or d.dependent_label for d in layout_uses})
            if layout_names:
                badges.insert(1, Badge(tr("Elrendezésen ({n})").format(n=len(layout_names)), "indigo", False,
                                       tr("Megjelenik ezeken az elrendezéseken: {l}").format(l=", ".join(layout_names))))
            info = LayerInfo(
                layer_id=lid,
                name=layer.name(),
                badges=badges,
                status=status,
                reasons=reasons,
                depends_on=graph.depends_on(lid),
                transitive_victims=[v.layer_id for v in res.victims.values() if not v.direct],
                error=error,
                result=res,
                closure=graph.closure(lid),
                layout_uses=layout_uses,
                user_locked=lid in user_locked,
            )
            result.infos[lid] = info

        result.duration = time.perf_counter() - t0
        self.last_result = result
        log_info(f"Analysis finished: {len(result.infos)} layers, {len(unique)} dependencies, "
                 f"{result.duration * 1000:.0f} ms" + ("" if collected else " (cached dependencies)"))
        return result
