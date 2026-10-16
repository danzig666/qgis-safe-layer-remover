"""Dependency graph: reverse adjacency, transitive impact and set evaluation.

Pure Python, no QGIS imports, so it can be unit tested in isolation.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable, Optional

from .model import Certainty, Dependency, SetResult, Severity, Victim


class DependencyGraph:
    def __init__(
        self,
        dependencies: Iterable[Dependency],
        layer_ids: Iterable[str],
        locked_ids: Iterable[str] = (),
        error_ids: Iterable[str] = (),
    ):
        self.dependencies: list[Dependency] = list(dependencies)
        self.layer_ids: set[str] = set(layer_ids)
        self.locked_ids: set[str] = set(locked_ids)
        self.error_ids: set[str] = set(error_ids)

        # rev[required] -> [(dependent, dep)] for BLOCK layer-to-layer edges
        self.rev: dict[str, list[tuple[str, Dependency]]] = defaultdict(list)
        # every dependency keyed by the required layer (all severities, all targets)
        self.by_required: dict[str, list[Dependency]] = defaultdict(list)
        # every dependency keyed by the dependent layer
        self.by_dependent: dict[str, list[Dependency]] = defaultdict(list)

        for dep in self.dependencies:
            self.by_required[dep.required_id].append(dep)
            if dep.dependent_id is not None:
                self.by_dependent[dep.dependent_id].append(dep)
            if (
                dep.is_layer_edge
                and dep.severity == Severity.BLOCK
                and dep.dependent_id in self.layer_ids
            ):
                self.rev[dep.required_id].append((dep.dependent_id, dep))

    # ------------------------------------------------------------------
    def _bfs(self, members: set[str], allow_low: bool) -> dict[str, Dependency]:
        """Return {reached_layer_id: edge_used_to_reach_it} for nodes outside `members`."""
        parent: dict[str, Dependency] = {}
        visited = set(members)
        queue = deque(members)
        while queue:
            current = queue.popleft()
            for dependent, dep in self.rev.get(current, ()):
                if dependent in visited:
                    continue
                if not allow_low and dep.certainty == Certainty.LOW:
                    continue
                visited.add(dependent)
                parent[dependent] = dep
                queue.append(dependent)
        return parent

    @staticmethod
    def _chain(victim: str, parent: dict[str, Dependency], members: set[str]) -> list[Dependency]:
        chain: list[Dependency] = []
        node = victim
        guard = 0
        while node not in members and node in parent and guard < 10000:
            dep = parent[node]
            chain.append(dep)
            node = dep.required_id
            guard += 1
        chain.reverse()
        return chain

    def evaluate(self, members: Iterable[str]) -> SetResult:
        s = set(members)
        result = SetResult(members=frozenset(s))
        result.missing = sorted(m for m in s if m not in self.layer_ids)
        result.locked = sorted(m for m in s if m in self.locked_ids)
        result.errors = sorted(m for m in s if m in self.error_ids)

        # Two passes: certain victims are reachable without LOW edges.
        certain_parent = self._bfs(s, allow_low=False)
        all_parent = self._bfs(s, allow_low=True)

        for victim, _dep in certain_parent.items():
            result.victims[victim] = Victim(victim, self._chain(victim, certain_parent, s), True)
        for victim, _dep in all_parent.items():
            if victim in result.victims:
                continue
            result.victims[victim] = Victim(victim, self._chain(victim, all_parent, s), False)

        seen: set[tuple] = set()
        for req in s:
            for dep in self.by_required.get(req, ()):
                # effects on layers that are removed together do not matter
                if dep.dependent_id is not None and dep.dependent_id in s and dep.dependent_id != req:
                    continue
                if dep.is_layer_edge and dep.severity == Severity.BLOCK:
                    continue  # handled by the BFS
                key = (dep.dependent_id, dep.dependent_label, dep.kind, dep.detail, dep.required_id)
                if key in seen:
                    continue
                seen.add(key)
                if dep.severity == Severity.BLOCK:
                    # BLOCK towards a non-layer object (e.g. atlas when block_on_atlas is on)
                    result.blocking_objects.append(dep)
                elif dep.severity == Severity.WARN:
                    result.warnings.append(dep)
                else:
                    result.infos.append(dep)
        return result

    # ------------------------------------------------------------------
    def closure(self, layer_id: str) -> frozenset[str]:
        """{layer} plus every layer that transitively depends on it (any certainty)."""
        reached = self._bfs({layer_id}, allow_low=True)
        return frozenset({layer_id, *reached.keys()})

    def direct_dependents(self, layer_id: str) -> list[Dependency]:
        return [dep for _d, dep in self.rev.get(layer_id, ())]

    def depends_on(self, layer_id: str) -> list[Dependency]:
        return [d for d in self.by_dependent.get(layer_id, ()) if d.required_id != layer_id]

    def has_dependents(self, layer_id: str) -> bool:
        return bool(self.rev.get(layer_id))

    def edges_into(self, victim: str, sources: set[str]) -> list[Dependency]:
        """All BLOCK edges from any of `sources` into `victim` (for richer explanations)."""
        out: list[Dependency] = []
        for src in sources:
            for dependent, dep in self.rev.get(src, ()):
                if dependent == victim:
                    out.append(dep)
        return out


def most_severe(deps: Iterable[Dependency]) -> Optional[Dependency]:
    best = None
    for d in deps:
        if best is None or (d.severity.value, d.certainty.value) > (best.severity.value, best.certainty.value):
            best = d
    return best
