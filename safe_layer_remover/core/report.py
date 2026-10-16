"""Report export (CSV / HTML) of the analysis: every layer, status and reasons."""

from __future__ import annotations

import csv
import html
from datetime import datetime

from qgis.core import QgsProject

from .analyzer import AnalysisResult
from .texts import kind_label, status_label, tr, victim_long


def _rows(analysis: AnalysisResult):
    for lid, info in sorted(analysis.infos.items(), key=lambda kv: kv[1].name.lower()):
        res = info.result
        victims = []
        effects = []
        if res is not None:
            victims = [f"{analysis.name_of(v.layer_id)}: {victim_long(v, analysis.name_of)}" for v in res.victims.values()]
            effects = [f"{d.dependent_label}: {kind_label(d.kind)} ({d.detail})"
                       for d in res.blocking_objects + res.warnings]
        depends = sorted({f"{analysis.name_of(d.required_id)} ({kind_label(d.kind)})" for d in info.depends_on})
        yield {
            "name": info.name,
            "id": lid,
            "type": ", ".join(b.text for b in info.badges),
            "status": status_label(info.status),
            "status_key": info.status.value,
            "victims": victims,
            "effects": effects,
            "depends_on": depends,
            "error": info.error or "",
            "layouts": sorted({f"{d.object_ref}: {d.detail}" for d in info.layout_uses}),
        }


def export_csv(analysis: AnalysisResult, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow([tr("Réteg"), tr("Azonosító"), tr("Típus"), tr("Állapot"), tr("Sérülő rétegek"),
                         tr("Egyéb hatás"), tr("Ezektől függ"), tr("Elrendezések"), tr("Hiba")])
        for r in _rows(analysis):
            writer.writerow([r["name"], r["id"], r["type"], r["status"], " | ".join(r["victims"]),
                             " | ".join(r["effects"]), " | ".join(r["depends_on"]), " | ".join(r["layouts"]), r["error"]])


def export_html(analysis: AnalysisResult, path: str) -> None:
    from ..ui.styles import STATUS_COLORS  # local import: keep core free of widgets at import time
    from .model import Status

    project = QgsProject.instance()
    title = tr("Rétegfüggőségi riport")
    e = html.escape
    rows = []
    for r in _rows(analysis):
        color = STATUS_COLORS[Status(r["status_key"])]
        lists = []
        for key in ("victims", "effects", "depends_on", "layouts"):
            items = r[key]
            lists.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>" if items else "")
        rows.append(
            f"<tr><td><b>{e(r['name'])}</b><br><small>{e(r['id'])}</small></td><td>{e(r['type'])}</td>"
            f"<td><span class='pill' style='background:{color}'>{e(r['status'])}</span>"
            f"{'<br><small>' + e(r['error']) + '</small>' if r['error'] else ''}</td>"
            f"<td>{lists[0]}</td><td>{lists[1]}</td><td>{lists[2]}</td><td>{lists[3]}</td></tr>"
        )
    counts = analysis.counts()
    summary = " · ".join(f"{status_label(s)}: {n}" for s, n in counts.items() if n)
    doc = f"""<!doctype html><html lang="hu"><head><meta charset="utf-8"><title>{e(title)}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}}
table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #ddd;padding:6px 8px;vertical-align:top;text-align:left}}
th{{background:#f3f4f6}}ul{{margin:0;padding-left:18px}}small{{color:#777}}
.pill{{color:#fff;border-radius:9px;padding:1px 8px;font-size:90%;white-space:nowrap}}
</style></head><body>
<h1>{e(title)}</h1>
<p>{e(tr('Projekt'))}: <b>{e(project.title() or project.fileName() or tr('(mentetlen projekt)'))}</b><br>
{e(tr('Készült'))}: {datetime.now():%Y-%m-%d %H:%M}<br>{e(summary)}</p>
<table><tr><th>{e(tr('Réteg'))}</th><th>{e(tr('Típus'))}</th><th>{e(tr('Állapot'))}</th>
<th>{e(tr('Ha törlöd, elromlik'))}</th><th>{e(tr('Egyéb hatás'))}</th><th>{e(tr('Ezektől függ'))}</th><th>{e(tr('Elrendezések'))}</th></tr>
{''.join(rows)}</table></body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
