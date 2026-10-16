"""Hungarian explanation templates and article (a/az) handling.

All user visible strings go through tr() so they can be translated later.
"""

from __future__ import annotations

import os
from typing import Callable, Iterable, Optional

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QSettings

from .model import DepKind, Dependency, Severity, Status, Victim

CONTEXT = "SafeLayerRemover"


_LANG: Optional[str] = None
_EN: Optional[dict] = None


def ui_language() -> str:
    """Two letter UI language: "hu" on a Hungarian QGIS, otherwise the QGIS locale (English texts are used).

    Can be forced with the SAFE_LAYER_REMOVER_LANG environment variable (used by the tests).
    """
    global _LANG
    if _LANG is None:
        lang = os.environ.get("SAFE_LAYER_REMOVER_LANG", "")
        if not lang:
            try:
                from qgis.core import QgsApplication
                lang = QgsApplication.locale() or ""
            except Exception:  # noqa: BLE001
                lang = ""
        if not lang:
            settings = QSettings()
            if str(settings.value("locale/overrideFlag", False)).lower() in ("true", "1"):
                lang = settings.value("locale/userLocale", "") or ""
            lang = lang or QLocale.system().name()
        _LANG = (lang or "en")[:2].lower()
    return _LANG


def set_ui_language(lang: Optional[str]) -> None:
    """Override (or reset with None) the detected UI language."""
    global _LANG
    _LANG = lang[:2].lower() if lang else None


def tr(text: str) -> str:
    """Hungarian source text -> UI text. Qt translations (.qm) win, then the built-in English table."""
    translated = QCoreApplication.translate(CONTEXT, text)
    if translated != text or ui_language() == "hu":
        return translated
    global _EN
    if _EN is None:
        from ..i18n.en import EN
        _EN = EN
    return _EN.get(text, text)


_VOWELS = set("aáeéiíoóöőuúüűAÁEÉIÍOÓÖŐUÚÜŰ")
# consonant letters whose Hungarian name starts with a vowel (for acronyms: "az OSM", "az SQL")
_VOWEL_SOUNDING_LETTERS = set("FLMNRSXY")


def article(name: str) -> str:
    """Hungarian definite article for `name`: "a", "az" or "a(z)" when unsure ("" for other languages)."""
    if ui_language() != "hu":
        return ""
    text = (name or "").strip().lstrip("\"'„“”«»([{<")
    if not text:
        return "a(z)"
    first = text[0]
    if first.isdigit():
        digits = ""
        for ch in text:
            if ch.isdigit():
                digits += ch
            elif ch in " . " and digits:
                continue
            else:
                break
        if first == "5":
            return "az"           # öt, ötven, ötszáz...
        if first == "1":
            # egy (1), ezer (1000), egymillió (1000000) -> az; tíz, száz, tízezer -> a
            return "az" if len(digits) % 3 == 1 else "a"
        return "a"
    if first in _VOWELS:
        return "az"
    if first.isalpha():
        word = text.split()[0]
        letters = "".join(ch for ch in word if ch.isalpha())
        if len(letters) >= 2 and letters.isupper() and first.upper() in _VOWEL_SOUNDING_LETTERS:
            return "az"
        return "a"
    return "a(z)"


def with_article(name: str, capital: bool = False) -> str:
    art = article(name)
    if not art:
        return name
    if capital:
        art = art[0].upper() + art[1:]
    return f"{art} {name}"


def status_label(status: Status) -> str:
    return {
        Status.SAFE: tr("Törölhető"),
        Status.WARN: tr("Figyelmeztetés"),
        Status.UNCERTAIN: tr("Ellenőrizendő"),
        Status.BLOCKED: tr("Nem törölhető"),
        Status.LOCKED: tr("Zárolt"),
        Status.ERROR: tr("Elemzési hiba"),
    }[status]


def kind_label(kind: DepKind) -> str:
    return {
        DepKind.JOIN: tr("vektoros csatolás (join)"),
        DepKind.VIRTUAL_LAYER: tr("virtuális réteg forrása"),
        DepKind.RELATION: tr("kapcsolat"),
        DepKind.POLYMORPHIC_RELATION: tr("polimorf kapcsolat"),
        DepKind.VALUE_RELATION: tr("értékreláció widget"),
        DepKind.RELATION_REFERENCE: tr("kapcsolathivatkozás widget"),
        DepKind.LAYER_DEPENDENCY: tr("rétegfüggőség"),
        DepKind.EXPRESSION: tr("kifejezés"),
        DepKind.GROUP_LAYER: tr("csoportréteg tagja"),
        DepKind.MASKING: tr("szelektív maszkolás"),
        DepKind.XML_REFERENCE: tr("azonosító-hivatkozás"),
        DepKind.LAYOUT: tr("nyomtatási elrendezés"),
        DepKind.ATLAS: tr("atlasz lefedő réteg"),
        DepKind.MAP_THEME: tr("térképtéma"),
        DepKind.SNAPPING: tr("illesztés"),
        DepKind.AVOID_INTERSECTIONS: tr("átfedés elkerülése"),
        DepKind.TERRAIN: tr("projekt terepmodell"),
        DepKind.MAP_CANVAS: tr("térképablak"),
        DepKind.MEMORY_DATA_LOSS: tr("memóriaréteg adatvesztés"),
        DepKind.UNSAVED_EDITS: tr("mentetlen szerkesztés"),
        DepKind.AUXILIARY_STORAGE: tr("kiegészítő tároló"),
        DepKind.BROKEN_REFERENCE: tr("hibás hivatkozás"),
        DepKind.DYNAMIC_REFERENCE: tr("dinamikus hivatkozás"),
    }.get(kind, kind.value)


def short_reason(dep: Dependency) -> str:
    """One short phrase for the tree row."""
    n = dep.dependent_label
    k = dep.kind
    if k == DepKind.JOIN:
        return tr("{a} joinolja").format(a=with_article(n))
    if k == DepKind.VIRTUAL_LAYER:
        return tr("{a} virt. réteg használja").format(a=with_article(n))
    if k in (DepKind.RELATION, DepKind.POLYMORPHIC_RELATION):
        return tr("kapcsolat: {n}").format(n=n)
    if k == DepKind.VALUE_RELATION:
        return tr("{a} értéklistája").format(a=with_article(n))
    if k == DepKind.RELATION_REFERENCE:
        return tr("{a} kapcsolathivatkozása").format(a=with_article(n))
    if k == DepKind.LAYER_DEPENDENCY:
        return tr("{a} függősége").format(a=with_article(n))
    if k == DepKind.EXPRESSION:
        return tr("{a} kifejezése").format(a=with_article(n))
    if k == DepKind.GROUP_LAYER:
        return tr("{a} csoportréteg tagja").format(a=with_article(n))
    if k == DepKind.MASKING:
        return tr("{a} maszkolja").format(a=with_article(n))
    if k == DepKind.XML_REFERENCE:
        return tr("{a} beállításai hivatkoznak rá").format(a=with_article(n))
    if k == DepKind.MEMORY_DATA_LOSS:
        return tr("adatai elvesznek a törléskor")
    if k == DepKind.UNSAVED_EDITS:
        return tr("mentetlen szerkesztések")
    return n


def long_reason(dep: Dependency) -> str:
    """Full sentence for tooltips and the detail panel."""
    n = dep.dependent_label
    a_cap = with_article(n, capital=True)
    d = dep.detail
    k = dep.kind
    if k == DepKind.JOIN:
        return tr("{A} réteg vektoros csatolással (join) kapcsolja ehhez a réteghez: {d}.").format(A=a_cap, d=d)
    if k == DepKind.VIRTUAL_LAYER:
        return tr("{A} virtuális réteg forrásként használja ({d}).").format(A=a_cap, d=d)
    if k in (DepKind.RELATION, DepKind.POLYMORPHIC_RELATION):
        return tr("{A} réteggel kapcsolat áll fenn ({d}). Törléskor a kapcsolat és az űrlap elemei megszűnnek.").format(A=a_cap, d=d)
    if k == DepKind.VALUE_RELATION:
        return tr("{A} réteg {d} ebből a rétegből veszi az értékeit.").format(A=a_cap, d=d)
    if k == DepKind.RELATION_REFERENCE:
        return tr("{A} réteg {d} erre a rétegre mutat.").format(A=a_cap, d=d)
    if k == DepKind.LAYER_DEPENDENCY:
        return tr("{A} réteg függőségként jelöli meg ({d}).").format(A=a_cap, d=d)
    if k == DepKind.EXPRESSION:
        return tr("{A} réteg kifejezése hivatkozik rá ({d}).").format(A=a_cap, d=d)
    if k == DepKind.GROUP_LAYER:
        return tr("{A} csoportréteg tagja; törlésével a csoportréteg tartalma megváltozik.").format(A=a_cap)
    if k == DepKind.MASKING:
        return tr("{A} réteg szelektív maszkolása hivatkozik rá ({d}).").format(A=a_cap, d=d)
    if k == DepKind.XML_REFERENCE:
        return tr("{A} réteg beállításai hivatkoznak az azonosítójára ({d}).").format(A=a_cap, d=d)
    if dep.dependent_id is None:
        return f"{n}: {d}" if d else n
    return f"{a_cap}: {kind_label(k)} ({d})"


def chain_text(chain: list[Dependency], name_of: Callable[[str], str]) -> str:
    if not chain:
        return ""
    names = [name_of(chain[0].required_id)] + [name_of(dep.dependent_id or "") for dep in chain]
    return " → ".join(names)


def victim_short(victim: Victim, name_of: Callable[[str], str]) -> str:
    if victim.direct:
        txt = short_reason(victim.chain[0])
    else:
        txt = tr("közvetve: {c}").format(c=chain_text(victim.chain, name_of))
    if not victim.certain:
        txt += " " + tr("(bizonytalan)")
    return txt


def victim_long(victim: Victim, name_of: Callable[[str], str]) -> str:
    if victim.direct:
        txt = long_reason(victim.chain[0])
    else:
        parts = []
        for dep in victim.chain:
            parts.append(tr("{dep} – {kind}").format(dep=name_of(dep.dependent_id or ""), kind=kind_label(dep.kind)))
        txt = tr("Közvetett hatás: {c} ({p}).").format(c=chain_text(victim.chain, name_of), p="; ".join(parts))
    if not victim.certain:
        txt += " " + tr("A hivatkozás bizonytalan (heurisztikus egyezés).")
    return txt


def join_short(items: list[str], limit: int = 2) -> str:
    if len(items) <= limit:
        return "; ".join(items)
    return "; ".join(items[:limit]) + "; " + tr("+{n} további").format(n=len(items) - limit)


def excerpt(text: str, limit: int = 60) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"
