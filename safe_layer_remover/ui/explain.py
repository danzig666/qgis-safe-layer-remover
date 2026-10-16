"""Turn analysis results into short / long human readable explanations."""

from __future__ import annotations

import html
from typing import Optional

from ..core.analyzer import AnalysisResult
from ..core.model import DepKind, Dependency, LayerInfo, SetResult, Status, Victim
from ..core.texts import (
    join_short,
    kind_label,
    long_reason,
    short_reason,
    status_label,
    tr,
    victim_long,
    victim_short,
)


def sorted_victims(result: SetResult) -> list[Victim]:
    return sorted(result.victims.values(), key=lambda v: (not v.certain, not v.direct, len(v.chain)))


def object_short(dep: Dependency) -> str:
    if dep.kind in (DepKind.MEMORY_DATA_LOSS, DepKind.UNSAVED_EDITS):
        return short_reason(dep)
    return f"{dep.dependent_label}: {kind_label(dep.kind)}"


def object_long(dep: Dependency) -> str:
    if dep.kind in (DepKind.MEMORY_DATA_LOSS, DepKind.UNSAVED_EDITS, DepKind.AUXILIARY_STORAGE):
        return dep.detail[:1].upper() + dep.detail[1:] + "."
    if dep.dependent_id is not None:
        return long_reason(dep)
    return f"{dep.dependent_label} – {kind_label(dep.kind)}: {dep.detail}"


def layer_short_reason(info: LayerInfo, analysis: AnalysisResult) -> str:
    if info.status == Status.ERROR:
        return tr("Az elemzés sikertelen")
    if info.status == Status.LOCKED:
        if info.user_locked:
            return tr("saját zárolás – oldd fel a törléshez")
        return tr("a réteg zárolt, nem eltávolítható")
    res = info.result
    if res is None:
        return ""
    if info.status in (Status.BLOCKED, Status.UNCERTAIN):
        items = [victim_short(v, analysis.name_of) for v in sorted_victims(res)]
        items += [object_short(d) for d in res.blocking_objects]
        return join_short(items)
    if info.status == Status.WARN:
        return join_short([object_short(d) for d in res.warnings])
    return ""


def layer_tooltip(info: LayerInfo, analysis: AnalysisResult) -> str:
    lines = [f"<b>{html.escape(info.name)}</b> – {html.escape(status_label(info.status))}"]
    if info.error:
        lines.append(html.escape(tr("Az elemzés sikertelen, ezért a réteg nem törölhető:")))
        lines.append(f"<small>{html.escape(info.error)}</small>")
    if info.user_locked:
        lines.append(html.escape(tr("Saját zárolás: a réteget te jelölted meg nem törölhetőként. "
                                    "A Feloldás paranccsal szüntetheted meg.")))
    elif info.status == Status.LOCKED:
        lines.append(html.escape(tr("A réteg a projektben kötelezőként van megjelölve (nem eltávolítható).")))
    res = info.result
    if res is not None:
        victims = sorted_victims(res)
        if victims:
            lines.append(html.escape(tr("Ha törlöd, elromlik:")))
            for v in victims:
                lines.append("• " + html.escape(f"{analysis.name_of(v.layer_id)}: {victim_long(v, analysis.name_of)}"))
        if res.blocking_objects:
            lines.append(html.escape(tr("Tiltó hatás:")))
            for d in res.blocking_objects:
                lines.append("• " + html.escape(object_long(d)))
        if res.warnings:
            lines.append(html.escape(tr("Egyéb hatás:")))
            for d in res.warnings:
                lines.append("• " + html.escape(object_long(d)))
    if info.themes and analysis.settings.get("show_theme_membership", True):
        lines.append(html.escape(tr("Szerepel ezekben a térképtémákban: {l}").format(l=", ".join(info.themes))))
    return "<br>".join(lines)


def set_summary(res: SetResult, analysis: AnalysisResult, uncertain_is_blocked: bool) -> tuple[bool, str]:
    n = len(res.members)
    if n == 0:
        return False, tr("Nincs kijelölt réteg.")
    if res.is_safe(uncertain_is_blocked):
        txt = tr("Kijelölve: {n} réteg – biztonságos").format(n=n)
        if res.warnings:
            txt += tr(" ({w} figyelmeztetéssel)").format(w=len(res.warnings))
        return True, txt
    problems = []
    if res.errors:
        problems.append(tr("elemzési hiba: {l}").format(l=", ".join(analysis.name_of(i) for i in res.errors)))
    if res.locked:
        problems.append(tr("zárolt: {l}").format(l=", ".join(analysis.name_of(i) for i in res.locked)))
    if res.missing:
        problems.append(tr("már nem létező réteg"))
    victims = sorted_victims(res)
    if victims:
        problems.append(tr("sérülne: {l}").format(l=join_short([analysis.name_of(v.layer_id) for v in victims], 3)))
    if res.blocking_objects:
        problems.append(join_short([d.dependent_label for d in res.blocking_objects], 2))
    return False, tr("Kijelölve: {n} réteg – nem biztonságos ({p})").format(n=n, p="; ".join(problems))
