"""Data model: enums and dataclasses shared by the analyzer, the graph and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(Enum):
    BLOCK = 3   # another layer breaks
    WARN = 2    # no layer breaks, but there is data loss / a side effect
    INFO = 1    # informative only, does not affect the classification


class Certainty(Enum):
    HIGH = 3    # explicit, typed reference (e.g. join layer id)
    MEDIUM = 2  # strong heuristic (e.g. layer id inside a text)
    LOW = 1     # weak heuristic (e.g. ambiguous layer name in an expression)


LAYOUT_KINDS = ("layout", "atlas")


class DepKind(Enum):
    JOIN = "join"
    VIRTUAL_LAYER = "virtual_layer"
    RELATION = "relation"
    POLYMORPHIC_RELATION = "polymorphic_relation"
    VALUE_RELATION = "value_relation"
    RELATION_REFERENCE = "relation_reference"
    LAYER_DEPENDENCY = "layer_dependency"
    EXPRESSION = "expression"
    GROUP_LAYER = "group_layer"
    MASKING = "masking"
    XML_REFERENCE = "xml_reference"
    LAYOUT = "layout"
    ATLAS = "atlas"
    MAP_THEME = "map_theme"
    SNAPPING = "snapping"
    AVOID_INTERSECTIONS = "avoid_intersections"
    TERRAIN = "terrain"
    MAP_CANVAS = "map_canvas"
    MEMORY_DATA_LOSS = "memory_data_loss"
    UNSAVED_EDITS = "unsaved_edits"
    AUXILIARY_STORAGE = "auxiliary_storage"
    BROKEN_REFERENCE = "broken_reference"
    DYNAMIC_REFERENCE = "dynamic_reference"


class Status(Enum):
    SAFE = "safe"
    WARN = "warn"
    UNCERTAIN = "uncertain"
    BLOCKED = "blocked"
    LOCKED = "locked"
    ERROR = "error"


# Ordering used for sorting / aggregation (higher = worse)
STATUS_RANK = {
    Status.SAFE: 0,
    Status.WARN: 1,
    Status.UNCERTAIN: 2,
    Status.BLOCKED: 3,
    Status.LOCKED: 4,
    Status.ERROR: 5,
}


@dataclass(frozen=True)
class Dependency:
    required_id: str               # the layer that would be removed
    dependent_id: Optional[str]    # the layer that would break; None if not a layer (e.g. layout)
    dependent_label: str           # display name ("Telkek" or "Elrendezés: A3 terv")
    kind: DepKind
    severity: Severity
    certainty: Certainty
    detail: str                    # human readable detail (field name, expression excerpt...)
    detector: str                  # name of the detector that found it
    object_ref: str = ""           # e.g. the layout name for layout related items

    @property
    def is_layer_edge(self) -> bool:
        return self.dependent_id is not None and self.dependent_id != self.required_id

    def pair(self) -> tuple[Optional[str], str]:
        return (self.dependent_id, self.required_id)


@dataclass(frozen=True)
class Badge:
    text: str
    color: str          # key into styles.BADGE_COLORS
    primary: bool = False
    tooltip: str = ""


@dataclass
class Victim:
    """A layer outside the removal set that would break."""
    layer_id: str
    chain: list[Dependency]        # edges from a removed layer to this victim, in order
    certain: bool                  # False when only reachable through LOW certainty edges

    @property
    def direct(self) -> bool:
        return len(self.chain) == 1


@dataclass
class SetResult:
    members: frozenset[str]
    victims: dict[str, Victim] = field(default_factory=dict)
    blocking_objects: list[Dependency] = field(default_factory=list)  # non-layer BLOCK items
    warnings: list[Dependency] = field(default_factory=list)
    infos: list[Dependency] = field(default_factory=list)
    locked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # ids no longer in the project

    @property
    def certain_victims(self) -> list[Victim]:
        return [v for v in self.victims.values() if v.certain]

    @property
    def uncertain_victims(self) -> list[Victim]:
        return [v for v in self.victims.values() if not v.certain]

    @property
    def blocked(self) -> bool:
        return bool(self.certain_victims or self.blocking_objects)

    @property
    def uncertain(self) -> bool:
        return bool(self.uncertain_victims)

    def is_safe(self, uncertain_is_blocked: bool = True) -> bool:
        if not self.members or self.locked or self.errors or self.missing:
            return False
        if self.blocked:
            return False
        if self.uncertain and uncertain_is_blocked:
            return False
        return True

    def status(self) -> Status:
        if self.errors:
            return Status.ERROR
        if self.locked:
            return Status.LOCKED
        if self.blocked:
            return Status.BLOCKED
        if self.uncertain:
            return Status.UNCERTAIN
        if self.warnings:
            return Status.WARN
        return Status.SAFE


@dataclass
class LayerInfo:
    layer_id: str
    name: str
    badges: list[Badge]
    status: Status
    reasons: list[Dependency] = field(default_factory=list)       # why it is (not) removable
    depends_on: list[Dependency] = field(default_factory=list)    # what this layer uses (informative)
    transitive_victims: list[str] = field(default_factory=list)   # indirectly broken layer ids
    error: Optional[str] = None
    result: Optional[SetResult] = None
    closure: frozenset[str] = frozenset()
    layout_uses: list[Dependency] = field(default_factory=list)  # layouts the layer appears on
    user_locked: bool = False      # locked by the user (personal lock stored in the project)
    themes: list[str] = field(default_factory=list)   # saved map themes the layer is visible in

    @property
    def untouched(self) -> bool:
        """Removing the layer breaks nothing: no dependent layer, not on a layout, no warning.

        Not counted as a restriction: the data of a memory layer (saving is offered when it is
        removed) and being visible in map themes (the theme is simply kept without the layer).
        """
        if self.status not in (Status.SAFE, Status.WARN) or self.layout_uses or self.result is None:
            return False
        r = self.result
        if r.victims or r.blocking_objects:
            return False
        # informative items (map themes, snapping, auxiliary storage ...) never count
        return all(d.kind == DepKind.MEMORY_DATA_LOSS for d in r.warnings)

    def is_removable(self, uncertain_is_blocked: bool = True) -> bool:
        if self.status in (Status.SAFE, Status.WARN):
            return True
        if self.status == Status.UNCERTAIN and not uncertain_is_blocked:
            return True
        return False
