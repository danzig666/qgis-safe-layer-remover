"""Search / type / status filtering that keeps the group hierarchy (recursive filtering)."""

from __future__ import annotations

import unicodedata
from typing import Optional

from qgis.PyQt.QtCore import QSortFilterProxyModel

from ..core.model import Status
from .tree_model import BADGES_ROLE, COL_NAME, COL_TYPE, IS_GROUP_ROLE, LAYOUTS_ROLE, SEARCH_ROLE, STATUS_ROLE


def normalize(text: str) -> str:
    """Accent and case insensitive form: "Övezetek" -> "ovezetek"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


class LayerFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)
        self._text = ""
        self._types: Optional[set[str]] = None      # None = all types
        self._statuses: Optional[set[Status]] = None  # None = all statuses
        self._layout: Optional[str] = None            # "" = on any layout, name = on that layout

    # ------------------------------------------------------------------
    def set_text(self, text: str) -> None:
        self._text = normalize(text.strip())
        self.invalidateFilter()

    def set_types(self, types: Optional[set[str]]) -> None:
        self._types = set(types) if types is not None else None
        self.invalidateFilter()

    def set_statuses(self, statuses: Optional[set[Status]]) -> None:
        self._statuses = set(statuses) if statuses is not None else None
        self.invalidateFilter()

    def set_layout(self, layout: Optional[str]) -> None:
        self._layout = layout
        self.invalidateFilter()

    @property
    def statuses(self) -> Optional[set[Status]]:
        return self._statuses

    def is_active(self) -> bool:
        return bool(self._text) or self._types is not None or self._statuses is not None or self._layout is not None

    # ------------------------------------------------------------------
    def filterAcceptsRow(self, source_row, source_parent):  # noqa: N802 - Qt API
        model = self.sourceModel()
        idx = model.index(source_row, COL_NAME, source_parent)
        if not idx.isValid():
            return False
        if idx.data(IS_GROUP_ROLE):
            # without a filter every group is shown; with a filter the recursive
            # filtering keeps a group when one of its descendants matches
            if not self.is_active():
                return True
            return bool(self._text) and self._types is None and self._statuses is None and self._layout is None \
                and self._text in normalize(idx.data(SEARCH_ROLE) or "")
        if self._text and self._text not in normalize(idx.data(SEARCH_ROLE) or ""):
            return False
        if self._types is not None:
            badges = model.index(source_row, COL_TYPE, source_parent).data(BADGES_ROLE) or []
            primary = badges[0].text if badges else ""
            if primary not in self._types:
                return False
        if self._layout is not None:
            layouts = idx.data(LAYOUTS_ROLE) or []
            if not layouts or (self._layout and self._layout not in layouts):
                return False
        if self._statuses is not None:
            value = idx.data(STATUS_ROLE)
            if not value or Status(value) not in self._statuses:
                return False
        return True
