"""Removal: final re-check, optional permanent save of memory layers, recycle bin, removal, empty groups."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from qgis.core import QgsLayerTreeGroup, QgsProject, QgsVectorLayer

from .analyzer import AnalysisResult, DependencyAnalyzer
from .log import log_info
from .model import SetResult
from .recycle_bin import BinEntry, RecycleBin, write_vector_copy
from .texts import tr


@dataclass
class RemovalOptions:
    use_recycle_bin: bool = True
    save_memory_dir: Optional[str] = None      # folder for permanent GeoPackage copies, None = do not save
    remove_empty_groups: bool = False


@dataclass
class RemovalOutcome:
    ok: bool
    aborted: bool = False
    message: str = ""
    removed: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    batch_id: str = ""
    entries: list[BinEntry] = field(default_factory=list)
    saved_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    analysis: Optional[AnalysisResult] = None
    set_result: Optional[SetResult] = None


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    return cleaned or "layer"


def unique_path(folder: str, base: str, ext: str = ".gpkg") -> str:
    path = os.path.join(folder, base + ext)
    i = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}_{i}{ext}")
        i += 1
    return path


class LayerRemover:
    def __init__(self, analyzer: DependencyAnalyzer, recycle_bin: Optional[RecycleBin] = None,
                 project: Optional[QgsProject] = None):
        self.analyzer = analyzer
        self.recycle_bin = recycle_bin
        self._project = project

    @property
    def project(self) -> QgsProject:
        return self._project or QgsProject.instance()

    def recheck(self, layer_ids) -> tuple[bool, SetResult, AnalysisResult]:
        """Run a fresh analysis (no XML cache) and evaluate the set."""
        self.analyzer.invalidate()
        analysis = self.analyzer.run()
        res = analysis.evaluate(set(layer_ids))
        return res.is_safe(analysis.settings.get("uncertain_is_blocked", True)), res, analysis

    def remove(self, layer_ids, options: Optional[RemovalOptions] = None) -> RemovalOutcome:
        options = options or RemovalOptions()
        ids = [lid for lid in dict.fromkeys(layer_ids)]
        if not ids:
            return RemovalOutcome(ok=False, aborted=True, message=tr("Nincs kijelölt réteg."))

        safe, res, analysis = self.recheck(ids)
        if not safe:
            return RemovalOutcome(
                ok=False, aborted=True, analysis=analysis, set_result=res,
                message=tr("A projekt időközben megváltozott, a kijelölés már nem biztonságos."),
            )

        outcome = RemovalOutcome(ok=True, analysis=analysis, set_result=res, batch_id=uuid.uuid4().hex)
        layers = [self.project.mapLayer(lid) for lid in ids]
        outcome.names = [lyr.name() for lyr in layers if lyr is not None]

        # 1. permanent copies of memory layers
        if options.save_memory_dir:
            os.makedirs(options.save_memory_dir, exist_ok=True)
            for lyr in layers:
                if isinstance(lyr, QgsVectorLayer) and lyr.providerType() == "memory":
                    path = unique_path(options.save_memory_dir, _safe_filename(lyr.name()))
                    ok, msg = write_vector_copy(lyr, path, _safe_filename(lyr.name()))
                    if ok:
                        outcome.saved_files.append(path)
                    else:
                        return RemovalOutcome(
                            ok=False, aborted=True, analysis=analysis,
                            message=tr("A(z) {n} memóriaréteg mentése sikertelen: {m}. A törlés elmaradt.")
                            .format(n=lyr.name(), m=msg),
                        )

        # 2. recycle bin snapshot (a failure aborts the removal)
        if options.use_recycle_bin and self.recycle_bin is not None:
            try:
                outcome.entries = self.recycle_bin.store(ids, outcome.batch_id)
            except Exception as exc:  # noqa: BLE001
                return RemovalOutcome(ok=False, aborted=True, analysis=analysis,
                                      message=tr("A lomtárba mentés sikertelen, a törlés elmaradt: {e}").format(e=exc))

        # 3. remember the parent groups for the empty group cleanup
        root = self.project.layerTreeRoot()
        parents: list[QgsLayerTreeGroup] = []
        for lid in ids:
            node = root.findLayer(lid)
            if node is not None and node.parent() is not None and node.parent() is not root:
                if node.parent() not in parents:
                    parents.append(node.parent())

        # 4. remove
        self.project.removeMapLayers(ids)
        outcome.removed = [lid for lid in ids if self.project.mapLayer(lid) is None]
        not_removed = [lid for lid in ids if lid not in outcome.removed]
        if not_removed:
            outcome.errors.append(tr("Nem sikerült eltávolítani: {l}").format(l=", ".join(not_removed)))

        # 5. empty groups
        if options.remove_empty_groups:
            self._remove_empty_groups(parents)

        log_info(f"Removed {len(outcome.removed)} layer(s): {', '.join(outcome.names)}")
        outcome.ok = not outcome.errors
        outcome.message = tr("{n} réteg törölve.").format(n=len(outcome.removed))
        return outcome

    def _remove_empty_groups(self, groups: list[QgsLayerTreeGroup]) -> None:
        root = self.project.layerTreeRoot()
        pending = list(groups)
        while pending:
            group = pending.pop(0)
            try:
                if group is root or group.children():
                    continue
                parent = group.parent()
                if parent is None:
                    continue
                parent.removeChildNode(group)
                if parent is not root and parent not in pending:
                    pending.append(parent)
            except RuntimeError:
                continue  # C++ object already deleted
