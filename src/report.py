"""Informe automático (HTML autocontenido + Markdown).

El informe separa claramente:
1. Datos medidos (lecturas RAW, valoración visual) y valores obtenidos de las imágenes.
2. Cálculos (estadísticos y correlaciones).
3. Interpretación, con lenguaje prudente y sin afirmar causalidad.

NO se redactan conclusiones científicas: el informe sólo describe lo que los
datos muestran. Los resultados negativos o nulos se muestran igual que los demás.
"""

from __future__ import annotations

import base64
import html
import math
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from .correlation import CAUSALITY_NOTICE
from .field_data import CALIBRATION_NOTICE
from .indices import INDEX_DEFINITIONS
from .utils import APP_NAME, APP_VERSION, DEMO_LABEL, ensure_output_dirs, now_iso

if TYPE_CHECKING:  # pragma: no cover
    from .pipeline import AnalysisResult


def _fmt(v, decimals: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return "—"
        return f"{v:.{decimals}f}"
    return str(v)


def _main_parcel_columns(result: "AnalysisResult") -> list[str]:
    s = result.project.settings
    t = result.combined.parcel_table
    cols = ["parcela", "nombre", "n_fotos"] + list(s.main_indices)
    cols += ["cobertura_rgb_pct", "humedad_raw_media", "humedad_raw_sd", "n_mediciones",
             "humedad_estimada_pct_media", "humedad_pct_arduino_media", "estado_general", "observaciones"]
    return [c for c in cols if c in t]


def _df_html(df: pd.DataFrame, cols: list[str] | None = None, decimals: int = 3) -> str:
    if df is None or df.empty:
        return "<p class='muted'>Sin datos.</p>"
    cols = cols or list(df.columns)
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
    body = []
    for _, row in df[cols].iterrows():
        body.append("<tr>" + "".join(f"<td>{html.escape(_fmt(row[c], decimals))}</td>" for c in cols) + "</tr>")
    return f"<div class='tw'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _df_md(df: pd.DataFrame, cols: list[str] | None = None, decimals: int = 3) -> str:
    if df is None or df.empty:
        return "_Sin datos._\n"
    cols = cols or list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, row in df[cols].iterrows():
        lines.append("| " + " | ".join(_fmt(row[c], decimals).replace("|", "/").replace("\n", " ") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def _img_b64(path: Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


CSS = """
body{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222;line-height:1.45}
h1{border-bottom:3px solid #4a7c59;padding-bottom:6px}h2{color:#2f5d3a;margin-top:32px;border-bottom:1px solid #ccc}
table{border-collapse:collapse;font-size:13px;margin:8px 0}th,td{border:1px solid #bbb;padding:4px 8px;text-align:left}
th{background:#eef3ee}.tw{overflow-x:auto}.muted{color:#777}.demo{background:#ffe0e0;border:2px solid #c00;color:#900;
padding:10px;font-weight:bold;text-align:center;font-size:18px}.box{background:#f6f8f6;border-left:4px solid #4a7c59;padding:8px 12px;margin:8px 0}
.warn{background:#fff6e0;border-left:4px solid #e0a000;padding:8px 12px;margin:8px 0}
figure{margin:12px 0}figure img{max-width:100%;border:1px solid #ddd}figcaption{font-size:12px;color:#555}
code{background:#f0f0f0;padding:1px 4px}.ok{color:#2a7a2a}.warning{color:#b07000}.error{color:#b00000}
"""


def write_reports(result: "AnalysisResult", config: dict) -> list[Path]:
    """Genera ``reports/informe.html`` e ``reports/informe.md``."""
    from .pipeline import LIMITATIONS, vegetation_description

    dirs = ensure_output_dirs(result.output_dir)
    rdir = dirs["reports"]
    p, s, c = result.project, result.project.settings, result.combined
    date = now_iso()
    n_photos = len(p.photos)
    n_parcels = len(p.rois.parcel_ids())
    stat_label = "media" if s.aggregate_stat == "mean" else "mediana"
    graphs_ = [g for g in result.graph_files if Path(g).exists()]
    maps = [m for m in (result.images.map_files if result.images else []) if Path(m).exists()]
    parcel_cols = _main_parcel_columns(result)
    corr_cols = ["variable_x", "variable_y", "n", "pearson_r", "pearson_p", "spearman_rho", "spearman_p"]
    field_cols = [col for col in ["parcela", "n_mediciones", "humedad_raw_media", "humedad_raw_mediana",
                                  "humedad_raw_sd", "humedad_raw_min", "humedad_raw_max",
                                  "humedad_estimada_pct_media", "humedad_pct_arduino_media",
                                  "primera_medicion", "ultima_medicion"] if col in c.field_summary]
    obs_cols = ["parcela", "estado_general", "coloracion", "densidad", "estres_visible", "uniformidad", "observaciones"]

    # ---------------------------------------------------------------- HTML
    h: list[str] = [f"<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'><title>Informe — {html.escape(p.name)}</title>"
                    f"<style>{CSS}</style></head><body>"]
    if p.is_demo:
        h.append(f"<div class='demo'>{DEMO_LABEL}</div>")
    h.append(f"<h1>Informe de análisis RGB — {html.escape(p.name)}</h1>")
    h.append(f"<p>Generado el {date} con {APP_NAME} {APP_VERSION}. Último análisis de imágenes: "
             f"{html.escape(str(p.results.get('last_analysis', '—')))}.</p>")
    h.append("<h2>1. Resumen del análisis</h2><ul>")
    h.append(f"<li>Número de fotografías: <b>{n_photos}</b></li><li>Número de parcelas: <b>{n_parcels}</b> "
             f"({len(p.rois)} ROIs)</li>")
    h.append(f"<li>Índices principales: <b>{', '.join(s.main_indices) or '—'}</b></li>")
    if s.experimental_indices:
        h.append(f"<li>Índices experimentales (separados de los principales): {', '.join(s.experimental_indices)}</li>")
    h.append(f"<li>Píxeles analizados: {'solo vegetación (máscara RGB aproximada)' if s.only_vegetation else 'todos los píxeles válidos'}</li>")
    h.append(f"<li>Máscara RGB aproximada de vegetación — {html.escape(vegetation_description(s))}</li>")
    h.append(f"<li>Valor de cada ROI usado en la tabla de parcelas: {stat_label}; si una parcela aparece en varias "
             "fotos se promedia entre fotos.</li>")
    h.append(f"<li>Mapas: escala {'común a todas las fotos' if s.map_scale_mode == 'common' else 'automática por foto'}</li></ul>")
    h.append("<h3>Fórmulas</h3><ul>")
    for k in s.all_indices:
        d = INDEX_DEFINITIONS[k]
        h.append(f"<li><b>{k}</b>{' (experimental)' if d.experimental else ''}: <code>{html.escape(d.formula)}</code> — "
                 f"{html.escape(d.description)}</li>")
    h.append("</ul><h3>Preprocesamiento</h3><ul>")
    h += [f"<li>{html.escape(line)}</li>" for line in s.preprocessing_options().describe()]
    h.append("</ul>")

    h.append("<h2>2. Datos medidos</h2>")
    h.append("<h3>2.1 Humedad del suelo (lecturas RAW del sensor capacitivo)</h3>")
    h.append("<p class='box'>Valores RAW del convertidor analógico-digital (lectura_adc). No son porcentajes de "
             "humedad. Si aparece <code>humedad_pct_arduino_media</code>, es el porcentaje calculado por el propio "
             "Arduino con su calibración interna; se muestra como dato aparte y no se usa en las correlaciones.</p>")
    h.append(_df_html(c.field_summary, field_cols, 1))
    if p.calibration.is_usable:
        h.append(f"<p class='warn'>Calibración lineal aplicada (RAW seco = {p.calibration.raw_dry}, RAW húmedo = "
                 f"{p.calibration.raw_wet}). {CALIBRATION_NOTICE}</p>")
    h.append("<h3>2.2 Valoración visual del estado general (1 = muy pobre … 5 = muy bueno)</h3>")
    h.append(_df_html(c.observations_df, [x for x in obs_cols if x in c.observations_df], 0))
    h.append("<h3>2.3 Valores obtenidos de las imágenes (tabla experimental por parcela)</h3>")
    h.append(_df_html(c.parcel_table, parcel_cols, 3))
    h.append("<p class='muted'>Los resultados completos por ROI (media, mediana, desviación estándar, mínimo, máximo, "
             "percentiles y número de píxeles) están en <code>exports/resultados_completos.csv</code>.</p>")

    h.append("<h2>3. Cálculos: correlaciones</h2>")
    h.append("<p>Pearson r mide asociación lineal; Spearman ρ mide asociación monótona por rangos "
             "(más adecuada para el estado general, que es ordinal, y con pocas parcelas).</p>")
    h.append(_df_html(c.correlations_df, [x for x in corr_cols if x in c.correlations_df], 3))
    if not c.exp_correlations_df.empty:
        h.append("<h3>Correlaciones con índices EXPERIMENTALES (no forman parte del análisis principal)</h3>")
        h.append(_df_html(c.exp_correlations_df, [x for x in corr_cols if x in c.exp_correlations_df], 3))

    h.append("<h2>4. Interpretación (descriptiva y prudente)</h2>")
    h.append(f"<p class='warn'>{html.escape(CAUSALITY_NOTICE)}</p><ul>")
    for r in c.correlation_results:
        extra = f" <i>{html.escape(r.warning)}</i>" if r.warning else ""
        h.append(f"<li>{html.escape(r.interpretation)}{extra}</li>")
    h.append("</ul><p class='muted'>Nota: si el sensor da valores RAW más altos en suelo más seco, una correlación "
             "negativa entre RAW e índice correspondería a más verdor en suelos más húmedos. Compruebe el sentido "
             "de su sensor antes de interpretar el signo.</p>")

    h.append("<h2>5. Gráficas</h2>")
    for g in graphs_:
        h.append(f"<figure><img alt='{html.escape(Path(g).stem)}' src='data:image/png;base64,{_img_b64(g)}'>"
                 f"<figcaption>{html.escape(Path(g).name)}</figcaption></figure>")
    if not graphs_:
        h.append("<p class='muted'>No se han generado gráficas.</p>")
    h.append("<h2>6. Mapas de índices</h2>")
    if maps:
        h.append("<p>Abra los mapas (se encuentran en las carpetas <code>indices/</code> y <code>rois/</code>):</p><ul>")
        for m in maps:
            rel = Path("..") / Path(m).parent.name / Path(m).name
            h.append(f"<li><a href='{html.escape(rel.as_posix())}'>{html.escape(Path(m).name)}</a></li>")
        h.append("</ul>")
    else:
        h.append("<p class='muted'>No hay mapas en este análisis.</p>")
    h.append("<h2>7. Control de calidad</h2><ul>")
    for item in (result.qc.items if result.qc else []):
        h.append(f"<li class='{item.level}'>{html.escape(item.text())}</li>")
    h.append("</ul>")
    h.append("<h2>8. Limitaciones</h2><ul>")
    h += [f"<li>{html.escape(t)}</li>" for t in LIMITATIONS]
    h.append("</ul><h2>9. Reproducibilidad</h2><p>La configuración completa, fórmulas, fotos, parcelas y versiones "
             "de software están en <code>analysis_metadata.json</code>.</p>")
    if p.is_demo:
        h.append(f"<div class='demo'>{DEMO_LABEL}</div>")
    h.append("</body></html>")
    html_path = rdir / "informe.html"
    html_path.write_text("\n".join(h), encoding="utf-8")

    # ---------------------------------------------------------------- Markdown
    m: list[str] = []
    if p.is_demo:
        m.append(f"> **{DEMO_LABEL}**\n")
    m.append(f"# Informe de análisis RGB — {p.name}\n")
    m.append(f"Generado el {date} con {APP_NAME} {APP_VERSION}.\n")
    m.append("## 1. Resumen\n")
    m.append(f"- Número de fotografías: **{n_photos}**\n- Número de parcelas: **{n_parcels}** ({len(p.rois)} ROIs)\n"
             f"- Índices principales: **{', '.join(s.main_indices)}**\n"
             + (f"- Índices experimentales: {', '.join(s.experimental_indices)}\n" if s.experimental_indices else "")
             + f"- Píxeles analizados: {'solo vegetación (máscara RGB aproximada)' if s.only_vegetation else 'todos los válidos'}\n")
    m.append("### Fórmulas\n")
    m += [f"- **{k}**: `{INDEX_DEFINITIONS[k].formula}`" for k in s.all_indices]
    m.append("\n## 2. Datos medidos\n### Humedad RAW (sin calibrar)\n")
    m.append(_df_md(c.field_summary, field_cols, 1))
    if p.calibration.is_usable:
        m.append(f"\n> {CALIBRATION_NOTICE}\n")
    m.append("\n### Estado general (valoración visual)\n")
    m.append(_df_md(c.observations_df, [x for x in obs_cols if x in c.observations_df], 0))
    m.append("\n### Valores obtenidos de las imágenes (por parcela)\n")
    m.append(_df_md(c.parcel_table, parcel_cols, 3))
    m.append("\n## 3. Cálculos: correlaciones\n")
    m.append(_df_md(c.correlations_df, [x for x in corr_cols if x in c.correlations_df], 3))
    m.append("\n## 4. Interpretación\n")
    m.append(f"> {CAUSALITY_NOTICE}\n")
    m += [f"- {r.interpretation}" + (f" _{r.warning}_" if r.warning else "") for r in c.correlation_results]
    m.append("\n## 5. Gráficas\n")
    for g in graphs_:
        m.append(f"![{Path(g).stem}](../graphs/{Path(g).name})")
    m.append("\n## 6. Control de calidad\n")
    m += [f"- {i.text()}" for i in (result.qc.items if result.qc else [])]
    m.append("\n## 7. Limitaciones\n")
    m += [f"- {t}" for t in LIMITATIONS]
    if p.is_demo:
        m.append(f"\n> **{DEMO_LABEL}**")
    md_path = rdir / "informe.md"
    md_path.write_text("\n".join(m) + "\n", encoding="utf-8")
    return [html_path, md_path]
