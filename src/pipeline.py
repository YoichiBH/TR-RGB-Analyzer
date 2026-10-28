"""Flujo de análisis completo.

FOTOS → PREPROCESAMIENTO → ÍNDICES → ESTADÍSTICAS POR ROI → MAPAS →
DATOS DE CAMPO → TABLA EXPERIMENTAL → CORRELACIONES → GRÁFICAS →
EXPORTACIÓN → INFORME → METADATOS

Las imágenes se procesan de una en una y la memoria se libera tras cada una.
Para los mapas se guarda en ``indices/_cache`` una versión submuestreada de
cada índice; así los mapas con escala común se generan sin recalcular nada.
Las estadísticas se calculan SIEMPRE a resolución completa (o a la resolución
reducida si el usuario activa esa opción, lo que queda registrado).
"""

from __future__ import annotations

import gc
import logging
import math
import platform
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import graphs
from .correlation import CAUSALITY_NOTICE, HAS_SCIPY, CorrelationResult, correlation_table, main_pairs
from .field_data import CALIBRATION_NOTICE, summarize_moisture
from .image_loader import load_rgb_array
from .indices import (EXG_MODES, VEGETATION_METHODS, compute_index, formulas,
                      vegetation_mask)
from .preprocessing import preprocess
from .project import Project
from .roi import validate_roi
from .statistics import (aggregate_by_parcel, describe_prefixed, photo_comparison_table, robust_range,
                         temporal_table)
from .utils import APP_NAME, APP_VERSION, DEMO_LABEL, ensure_output_dirs, now_iso, slugify, write_json
from .validation import QCItem, QCReport, build_qc_report

logger = logging.getLogger("tr_rgb.pipeline")

ProgressCallback = Callable[[float, str], None]

LIMITATIONS = [
    "Los índices utilizados se obtienen a partir de imágenes RGB y deben interpretarse como indicadores "
    "relativos. No equivalen a NDVI y no constituyen mediciones directas de clorofila, contenido hídrico "
    "o salud vegetal.",
    CAUSALITY_NOTICE,
    "La calidad de los resultados depende de las condiciones de iluminación, altura de vuelo, orientación "
    "de la cámara, resolución, calibración del sensor y selección de las parcelas.",
    "La máscara de vegetación es una clasificación RGB aproximada; la cobertura vegetal se estima mediante "
    "RGB y puede confundir sombras, vegetación seca o suelos verdosos.",
    "Las lecturas RAW del sensor capacitivo dependen del sensor, del tipo de suelo y de la inserción. "
    "Sin calibración no representan un porcentaje de humedad. En muchos sensores capacitivos un valor RAW "
    "más alto indica suelo MÁS SECO.",
    "Con pocas parcelas las correlaciones tienen valor exploratorio: un único punto puede cambiar el resultado.",
    "Las imágenes de una cámara de consumo aplican balance de blancos y procesado automáticos que pueden "
    "variar entre fotografías.",
]


class AnalysisCancelled(Exception):
    """El usuario ha cancelado el análisis."""


@dataclass
class ImageAnalysisOutput:
    roi_df: pd.DataFrame
    image_df: pd.DataFrame
    map_files: list[Path] = field(default_factory=list)
    issues: list[QCItem] = field(default_factory=list)
    scale_ranges: dict = field(default_factory=dict)


@dataclass
class CombinedResults:
    field_df: pd.DataFrame
    field_summary: pd.DataFrame
    observations_df: pd.DataFrame
    parcel_table: pd.DataFrame
    comparison: pd.DataFrame
    temporal: pd.DataFrame
    correlations_df: pd.DataFrame
    correlation_results: list[CorrelationResult]
    exp_correlations_df: pd.DataFrame
    exp_correlation_results: list[CorrelationResult]


@dataclass
class AnalysisResult:
    project: Project
    output_dir: Path
    images: ImageAnalysisOutput | None
    combined: CombinedResults
    graph_files: list[Path] = field(default_factory=list)
    export_files: list[Path] = field(default_factory=list)
    report_files: list[Path] = field(default_factory=list)
    qc: QCReport | None = None
    metadata: dict = field(default_factory=dict)


def _noop_progress(_f: float, _m: str) -> None:
    pass


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise AnalysisCancelled("Análisis cancelado por el usuario")


def photo_stems(project: Project) -> dict[str, str]:
    """Nombre base (único y seguro) de cada foto para los archivos de salida."""
    stems: dict[str, str] = {}
    used: set[str] = set()
    for p in project.photos:
        stem = slugify(Path(p.name).stem, "foto")
        if stem in used:
            stem = slugify(p.name, "foto")
        while stem in used:
            stem += "_"
        used.add(stem)
        stems[p.name] = stem
    return stems


def vegetation_description(settings) -> str:
    method = VEGETATION_METHODS.get(settings.vegetation_method, settings.vegetation_method)
    if settings.vegetation_method == "exg":
        mode = ("RGB normalizado 0–1" if settings.vegetation_exg_mode == "normalized"
                else "coordenadas cromáticas")
        method = f"ExG > {settings.vegetation_threshold:g} ({mode})"
    return f"Método: {method}"


# ---------------------------------------------------------------------------
# 1. Análisis de imágenes
# ---------------------------------------------------------------------------
def analyze_images(project: Project, output_dir: Path | str, config: dict,
                   progress: ProgressCallback | None = None,
                   cancel: threading.Event | None = None) -> ImageAnalysisOutput:
    """Calcula índices y estadísticas por ROI para todas las fotos del proyecto.

    Las fotos se procesan una a una. Los archivos originales sólo se leen.
    """
    progress = progress or _noop_progress
    settings = project.settings
    keys = settings.all_indices
    if not keys:
        raise ValueError("Seleccione al menos un índice RGB para analizar.")
    dirs = ensure_output_dirs(output_dir)
    cache_dir = dirs["indices"] / "_cache"
    cache_dir.mkdir(exist_ok=True)
    opts = settings.preprocessing_options()
    eps = float(config.get("zero_division_eps", 1e-6))
    p_lo, p_hi = config.get("auto_scale_percentiles", [2, 98])
    map_max_side = int(config.get("map_max_side", 1600))
    min_side, min_area = int(config.get("roi_min_side", 5)), int(config.get("roi_min_area", 100))
    stems = photo_stems(project)
    pixel_mode = "solo vegetación (máscara RGB aproximada)" if settings.only_vegetation else "todos los píxeles válidos"

    rows, img_rows, issues = [], [], []
    photos = list(project.photos)
    n_photos = len(photos)
    for i, photo in enumerate(photos):
        _check_cancel(cancel)
        progress(i / max(n_photos, 1) * 0.85, f"Procesando {photo.name} ({i + 1}/{n_photos})")
        img_row = {"foto": photo.name, "fecha_foto": photo.date, "origen_fecha": photo.date_source,
                   "valida": False}
        if not photo.valid:
            issues.append(QCItem("error", "imagen", f"Imagen corrupta o ilegible: {photo.name}"))
            img_rows.append(img_row)
            continue
        try:
            arr, alpha, scale = load_rgb_array(photo.path, opts.downscale_max_side)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de lectura
            issues.append(QCItem("error", "imagen", f"No se pudo cargar {photo.name}: {exc}"))
            img_row["error"] = str(exc)
            img_rows.append(img_row)
            continue
        pre = preprocess(arr, opts, alpha)
        del arr, alpha
        h, w = pre.valid_mask.shape
        veg = vegetation_mask(pre.rgb, settings.vegetation_method, settings.vegetation_threshold,
                              settings.vegetation_exg_mode, pre.valid_mask, eps)
        idx_maps: dict[str, np.ndarray] = {}
        clip_maps: dict[str, np.ndarray] = {}
        img_row.update({"valida": True, "ancho_analizado": w, "alto_analizado": h, "factor_escala": scale,
                        "pixeles_totales": pre.n_total, "pixeles_validos": pre.n_valid})
        for reason, count in pre.counts.items():
            img_row[f"excluidos_{reason}"] = count
        img_row["cobertura_rgb_foto_pct"] = float(veg.sum() / pre.n_valid * 100) if pre.n_valid else math.nan
        for k in keys:
            res = compute_index(k, pre.rgb, pre.valid_mask, eps, settings.exg_mode, settings.vari_clip)
            idx_maps[k] = res.values
            if res.clipped is not None:
                clip_maps[k] = res.clipped
                img_row[f"{k}_recortados"] = res.n_clipped
            lo, hi = robust_range(res.values, p_lo, p_hi)
            img_row[f"{k}_P{p_lo:g}"] = lo
            img_row[f"{k}_P{p_hi:g}"] = hi
            img_row[f"{k}_div_cero"] = res.n_zero_division
            img_row[f"{k}_no_finitos"] = res.n_nonfinite
            if res.n_nonfinite:
                issues.append(QCItem("warning", "analisis", f"{photo.name}: {res.n_nonfinite} valores no finitos en {k}"))
        img_rows.append(img_row)

        for roi in project.rois.for_photo(photo.name):
            r = roi.scaled(scale)
            problems = validate_roi(r, w, h, max(1, round(min_side * scale)), max(1, round(min_area * scale * scale)))
            for level, msg in problems:
                issues.append(QCItem(level, "roi", msg))
            if any(level == "error" for level, _ in problems):
                continue
            rc = r.clipped(w, h)
            sl = (slice(rc.y1, rc.y2), slice(rc.x1, rc.x2))
            sub_valid = pre.valid_mask[sl]
            sub_veg = veg[sl]
            n_valid = int(sub_valid.sum())
            n_veg = int(sub_veg.sum())
            sel = sub_veg if settings.only_vegetation else sub_valid
            row = {
                "parcela": roi.parcel_id, "nombre_parcela": roi.name, "foto": photo.name,
                "fecha_foto": photo.date, "x1": roi.x1, "y1": roi.y1, "x2": roi.x2, "y2": roi.y2,
                "roi_recortada": not r.is_inside(w, h), "pixeles_roi": rc.area, "pixeles_validos": n_valid,
                "pixeles_vegetacion": n_veg, "pixeles_analizados": int(sel.sum()), "modo_pixeles": pixel_mode,
                "cobertura_rgb_pct": n_veg / n_valid * 100 if n_valid else math.nan,
                "descripcion": roi.description, "uid": roi.uid,
            }
            for k in keys:
                sub = idx_maps[k][sl]
                row.update(describe_prefixed(sub[sel], k))
                row[f"{k}_div_cero"] = int((~np.isfinite(sub) & sub_valid).sum())
                if k in clip_maps:
                    row[f"{k}_recortados"] = int((clip_maps[k][sl] & sel).sum())
            if row["pixeles_analizados"] == 0:
                issues.append(QCItem("error", "roi", f"ROI {roi.parcel_id} ({photo.name}): ningún píxel analizable"
                                     + (" (ningún píxel clasificado como vegetación)" if settings.only_vegetation else "")))
            rows.append(row)

        # Caché submuestreada para los mapas.
        step = max(1, int(math.ceil(max(h, w) / map_max_side)))
        factor = 255.0 if opts.normalize else 1.0
        rgb_small = np.clip(np.nan_to_num(pre.rgb[::step, ::step] * factor, nan=128), 0, 255).astype(np.uint8)
        np.savez_compressed(cache_dir / f"{stems[photo.name]}.npz", rgb=rgb_small,
                            valid=pre.valid_mask[::step, ::step], veg=veg[::step, ::step],
                            step=step, scale=scale, coverage=img_row["cobertura_rgb_foto_pct"],
                            **{f"idx_{k}": v[::step, ::step].astype(np.float32) for k, v in idx_maps.items()})
        del pre, veg, idx_maps, clip_maps, rgb_small
        gc.collect()

    roi_df = pd.DataFrame(rows)
    image_df = pd.DataFrame(img_rows)
    _check_cancel(cancel)
    out = ImageAnalysisOutput(roi_df=roi_df, image_df=image_df, issues=issues)
    progress(0.86, "Generando mapas de índices…")
    out.map_files, out.scale_ranges = render_maps(project, output_dir, config, image_df, cancel=cancel,
                                                  progress=progress)
    project.rois.save_json(dirs["rois"] / "parcelas.json")
    project.rois.save_csv(dirs["rois"] / "parcelas.csv")
    progress(1.0, "Análisis de imágenes completado")
    return out


def compute_scale_ranges(image_df: pd.DataFrame, keys: list[str], mode: str, config: dict) -> dict:
    """Rangos de color de los mapas.

    * ``auto``: cada imagen usa sus percentiles P_bajo–P_alto.
    * ``common``: todas las imágenes usan el mismo rango (mínimo de los P_bajo
      y máximo de los P_alto de todas las fotos) → comparables entre sí.
    """
    p_lo, p_hi = config.get("auto_scale_percentiles", [2, 98])
    ranges = {"mode": mode, "percentiles": [p_lo, p_hi], "per_photo": {}, "common": {}}
    if image_df is None or image_df.empty:
        return ranges
    valid = image_df[image_df.get("valida", False) == True]  # noqa: E712
    for k in keys:
        lo_col, hi_col = f"{k}_P{p_lo:g}", f"{k}_P{p_hi:g}"
        if lo_col not in valid:
            continue
        los = pd.to_numeric(valid[lo_col], errors="coerce")
        his = pd.to_numeric(valid[hi_col], errors="coerce")
        ranges["common"][k] = [float(los.min()), float(his.max())]
        for _, r in valid.iterrows():
            ranges["per_photo"].setdefault(r["foto"], {})[k] = [float(r[lo_col]), float(r[hi_col])]
    return ranges


def render_maps(project: Project, output_dir: Path | str, config: dict, image_df: pd.DataFrame,
                scale_mode: str | None = None, cancel: threading.Event | None = None,
                progress: ProgressCallback | None = None) -> tuple[list[Path], dict]:
    """Genera los PNG de mapas de índices, máscaras y parcelas desde la caché."""
    progress = progress or _noop_progress
    settings = project.settings
    keys = settings.all_indices
    mode = scale_mode or settings.map_scale_mode
    dirs = ensure_output_dirs(output_dir)
    cache_dir = dirs["indices"] / "_cache"
    ranges = compute_scale_ranges(image_df, keys, mode, config)
    p_lo, p_hi = ranges["percentiles"]
    stems = photo_stems(project)
    cmap = config.get("map_colormap", "RdYlGn")
    dpi = int(config.get("dpi", 150))
    files: list[Path] = []
    photos = [p for p in project.photos if (cache_dir / f"{stems[p.name]}.npz").exists()]
    for i, photo in enumerate(photos):
        _check_cancel(cancel)
        progress(0.86 + 0.13 * i / max(len(photos), 1), f"Mapas de {photo.name} ({i + 1}/{len(photos)})")
        stem = stems[photo.name]
        with np.load(cache_dir / f"{stem}.npz") as data:
            step, scale = int(data["step"]), float(data["scale"])
            f = scale / step
            rois_scaled = [(r.parcel_id, r.x1 * f, r.y1 * f, r.x2 * f, r.y2 * f)
                           for r in project.rois.for_photo(photo.name)]
            note = f"Mapa submuestreado ×1/{step} para visualización; estadísticas a resolución completa."
            if scale != 1:
                note = f"Imagen analizada reducida (factor {scale:.3f}). " + note
            for k in keys:
                if f"idx_{k}" not in data:
                    continue
                if mode == "common" and k in ranges["common"]:
                    vmin, vmax = ranges["common"][k]
                    desc = f"escala común (P{p_lo:g}–P{p_hi:g} de todas las fotos)"
                else:
                    vmin, vmax = ranges["per_photo"].get(photo.name, {}).get(k, [math.nan, math.nan])
                    desc = f"escala automática de esta foto (P{p_lo:g}–P{p_hi:g})"
                files.append(graphs.index_map(data[f"idx_{k}"], k, photo.name, vmin, vmax, desc,
                                              dirs["indices"] / f"{stem}_{k}.png", rois_scaled, cmap,
                                              project.is_demo, dpi, note))
            files.append(graphs.mask_map(data["veg"], data["valid"], photo.name, float(data["coverage"]),
                                         dirs["indices"] / f"{stem}_mascara_vegetacion.png",
                                         vegetation_description(settings), rois_scaled, project.is_demo, dpi))
            files.append(graphs.rgb_with_rois(data["rgb"], photo.name, dirs["rois"] / f"{stem}_parcelas.png",
                                              rois_scaled, project.is_demo, dpi))
    return files, ranges


# ---------------------------------------------------------------------------
# 2. Combinación con datos de campo y correlaciones
# ---------------------------------------------------------------------------
def build_parcel_table(project: Project, roi_df: pd.DataFrame, field_summary: pd.DataFrame,
                       observations_df: pd.DataFrame) -> pd.DataFrame:
    """Tabla experimental central: una fila por parcela con índices, cobertura,
    humedad (RAW y, si procede, estimada) y estado general."""
    s = project.settings
    agg = aggregate_by_parcel(roi_df, s.main_indices, s.aggregate_stat, s.experimental_indices)
    if agg.empty:
        agg = pd.DataFrame({"parcela": pd.Series(dtype=str)})
    agg["parcela"] = agg["parcela"].astype(str)
    table = agg
    if field_summary is not None and not field_summary.empty:
        fcols = ["parcela", "humedad_raw_media", "humedad_raw_sd", "n_mediciones"]
        fcols += [c for c in ("humedad_estimada_pct_media", "humedad_estimada_pct_sd",
                              "humedad_pct_arduino_media", "humedad_pct_arduino_sd") if c in field_summary]
        fs = field_summary[fcols].copy()
        fs["parcela"] = fs["parcela"].astype(str)
        table = table.merge(fs, on="parcela", how="outer")
    if observations_df is not None and not observations_df.empty:
        ob = observations_df.drop(columns=[c for c in ("fecha",) if c in observations_df]).copy()
        ob["parcela"] = ob["parcela"].astype(str)
        table = table.merge(ob, on="parcela", how="outer")
    for c in ("humedad_raw_media", "estado_general", "cobertura_rgb_pct"):
        if c not in table:
            table[c] = np.nan
    order = ["parcela", "nombre", "n_fotos", "fotos"]
    for k in s.main_indices:
        order += [k, f"{k}_sd_entre_fotos", f"{k}_cv_entre_fotos_pct"]
    order += ["cobertura_rgb_pct", "humedad_raw_media", "humedad_raw_sd", "n_mediciones",
              "humedad_estimada_pct_media", "humedad_estimada_pct_sd", "humedad_pct_arduino_media",
              "humedad_pct_arduino_sd", "estado_general", "coloracion",
              "densidad", "estres_visible", "uniformidad", "observaciones"]
    for k in s.experimental_indices:
        order += [f"EXP_{k}", f"EXP_{k}_sd_entre_fotos", f"EXP_{k}_cv_entre_fotos_pct"]
    cols = [c for c in order if c in table] + [c for c in table.columns if c not in order]
    from .roi import _natural_key

    table = table[cols]
    table = table.iloc[sorted(range(len(table)), key=lambda i: _natural_key(table["parcela"].iloc[i]))]
    return table.reset_index(drop=True)


def combine_results(project: Project, roi_df: pd.DataFrame | None, config: dict) -> CombinedResults:
    """Combina resultados de imagen y datos de campo y calcula correlaciones."""
    s = project.settings
    roi_df = roi_df if roi_df is not None else pd.DataFrame()
    field_df = project.field_df()
    field_summary = summarize_moisture(field_df, project.calibration)
    obs_df = project.observations_df()
    table = build_parcel_table(project, roi_df, field_summary, obs_df)
    comparison = photo_comparison_table(roi_df, s.main_indices, s.aggregate_stat) if not roi_df.empty else pd.DataFrame()
    temporal = temporal_table(roi_df, s.main_indices, s.aggregate_stat) if not roi_df.empty else pd.DataFrame()
    min_n = int(config.get("correlation_min_n", 3))
    expl_n = int(config.get("correlation_exploratory_n", 10))
    corr_df, corr_res = correlation_table(table, main_pairs(s.main_indices), min_n=min_n, exploratory_n=expl_n)
    exp_pairs = [(v, f"EXP_{k}") for k in s.experimental_indices for v in ("humedad_raw_media", "estado_general")]
    exp_df, exp_res = correlation_table(table, exp_pairs, min_n=min_n, exploratory_n=expl_n)
    return CombinedResults(field_df=field_df, field_summary=field_summary, observations_df=obs_df,
                           parcel_table=table, comparison=comparison, temporal=temporal,
                           correlations_df=corr_df, correlation_results=corr_res,
                           exp_correlations_df=exp_df, exp_correlation_results=exp_res)


# ---------------------------------------------------------------------------
# 3. Gráficas
# ---------------------------------------------------------------------------
def generate_graphs(project: Project, combined: CombinedResults, output_dir: Path | str,
                    config: dict) -> list[Path]:
    """Genera todas las gráficas de resultados en ``graphs/``."""
    s = project.settings
    gdir = ensure_output_dirs(output_dir)["graphs"]
    demo, dpi = project.is_demo, int(config.get("dpi", 150))
    trend_n = int(config.get("trend_line_min_n", 5))
    stat_label = "media" if s.aggregate_stat == "mean" else "mediana"
    t = combined.parcel_table
    files: list[Path | None] = []
    for k in s.main_indices:
        files.append(graphs.bar_by_parcel(
            t, k, f"{k} por parcela", f"{k} ({stat_label} de la ROI; media entre fotos)", gdir / f"{k}_por_parcela.png",
            err_col=f"{k}_sd_entre_fotos", err_label="desviación estándar entre fotografías", n_col="n_fotos",
            is_demo=demo, dpi=dpi, note="Índice RGB relativo."))
    for k in s.experimental_indices:
        files.append(graphs.bar_by_parcel(
            t, f"EXP_{k}", f"{k} por parcela (ÍNDICE EXPERIMENTAL)", f"{k} (experimental)",
            gdir / f"EXP_{k}_por_parcela.png", err_col=f"EXP_{k}_sd_entre_fotos",
            err_label="desviación estándar entre fotografías", is_demo=demo, dpi=dpi))
    files.append(graphs.bar_by_parcel(
        t, "cobertura_rgb_pct", "Cobertura vegetal estimada mediante RGB", "Cobertura estimada (%)",
        gdir / "cobertura_rgb_por_parcela.png", ylim=(0, 100), is_demo=demo, dpi=dpi,
        note="Máscara RGB aproximada; no es una medida exacta."))
    fs = combined.field_summary
    files.append(graphs.bar_by_parcel(
        fs, "humedad_raw_media", "Humedad RAW por parcela", "Lectura RAW del sensor (sin calibrar)",
        gdir / "humedad_raw_por_parcela.png", err_col="humedad_raw_sd",
        err_label="desviación estándar de las mediciones", n_col="n_mediciones", is_demo=demo, dpi=dpi,
        note="RAW no es % de humedad. En muchos sensores capacitivos RAW alto = suelo más seco."))
    if "humedad_estimada_pct_media" in fs:
        files.append(graphs.bar_by_parcel(
            fs, "humedad_estimada_pct_media", "Humedad estimada por parcela (calibración lineal)",
            "Humedad estimada (%)", gdir / "humedad_estimada_por_parcela.png", err_col="humedad_estimada_pct_sd",
            err_label="desviación estándar", ylim=(0, 100), is_demo=demo, dpi=dpi, note=CALIBRATION_NOTICE))
    if "humedad_pct_arduino_media" in fs:
        files.append(graphs.bar_by_parcel(
            fs, "humedad_pct_arduino_media", "Porcentaje de humedad calculado por el Arduino",
            "porcentaje_humedad del Arduino (%)", gdir / "humedad_pct_arduino_por_parcela.png",
            err_col="humedad_pct_arduino_sd", err_label="desviación estándar de las mediciones",
            n_col="n_mediciones", ylim=(0, 100), is_demo=demo, dpi=dpi,
            note="Calculado por el Arduino con su propia calibración; no es una humedad volumétrica universal. "
                 "El análisis principal usa la lectura RAW."))
    files.append(graphs.bar_by_parcel(
        t, "estado_general", "Estado general por parcela (valoración visual)", "Estado general (1–5)",
        gdir / "estado_general_por_parcela.png", ylim=(0, 5), is_demo=demo, dpi=dpi,
        note="1 = muy pobre … 5 = muy bueno (valoración subjetiva)."))
    for res in combined.correlation_results + combined.exp_correlation_results:
        name = f"scatter_{slugify(res.var_x)}_vs_{slugify(res.var_y)}.png"
        files.append(graphs.scatter_correlation(t, res, gdir / name, trend_min_n=trend_n, is_demo=demo, dpi=dpi))
    for k in s.main_indices:
        files.append(graphs.temporal_plot(combined.temporal, k, s.aggregate_stat, gdir / f"{k}_vs_fecha.png",
                                          is_demo=demo, dpi=dpi))
    return [Path(f) for f in files if f]


# ---------------------------------------------------------------------------
# 4. Metadatos de reproducibilidad
# ---------------------------------------------------------------------------
def _library_versions() -> dict:
    versions = {"python": platform.python_version()}
    for mod in ("numpy", "pandas", "PIL", "matplotlib", "scipy", "openpyxl"):
        try:
            m = __import__(mod)
            versions["Pillow" if mod == "PIL" else mod] = getattr(m, "__version__", "?")
        except Exception:  # noqa: BLE001
            versions[mod] = "no instalado"
    return versions


def build_metadata(project: Project, config: dict, output_dir: Path, scale_ranges: dict | None = None,
                   files: list[Path] | None = None, qc: QCReport | None = None) -> dict:
    """Información necesaria para repetir el análisis (analysis_metadata.json)."""
    s = project.settings
    now = now_iso()
    out_dir = Path(output_dir)

    def rel(p: Path) -> str:
        try:
            return str(Path(p).relative_to(out_dir))
        except ValueError:
            return str(p)

    return {
        "software": APP_NAME,
        "version_software": APP_VERSION,
        "fecha": now.split(" ")[0],
        "hora": now.split(" ")[1],
        "fecha_hora": now,
        "sistema": f"{platform.system()} {platform.release()}",
        "librerias": _library_versions(),
        "scipy_disponible": HAS_SCIPY,
        "aviso_demo": DEMO_LABEL if project.is_demo else None,
        "proyecto": {"nombre": project.name, "archivo": str(project.path) if project.path else None,
                     "es_demo": project.is_demo, "ultimo_analisis_imagenes": project.results.get("last_analysis")},
        "tipo_imagen": "RGB (sin banda NIR; NDVI no calculable)",
        "indices_principales": s.main_indices,
        "indices_experimentales": s.experimental_indices,
        "formulas": formulas(s.all_indices),
        "modo_exg": f"{s.exg_mode}: {EXG_MODES.get(s.exg_mode, '')}",
        "modo_exg_mascara_vegetacion": f"{s.vegetation_exg_mode}: {EXG_MODES.get(s.vegetation_exg_mode, '')}",
        "vari_recorte": "valores fuera de [−1, 1] recortados a ese intervalo" if s.vari_clip else "sin recorte",
        "preprocesamiento": {"opciones": s.preprocessing, "descripcion": s.preprocessing_options().describe()},
        "mascara_vegetacion": {"metodo": s.vegetation_method, "umbral": s.vegetation_threshold,
                               "descripcion": vegetation_description(s)},
        "pixeles_analizados": "solo vegetación (máscara RGB aproximada)" if s.only_vegetation else "todos los píxeles válidos",
        "estadistico_por_roi_para_tabla_parcelas": s.aggregate_stat,
        "escala_mapas": scale_ranges or {"mode": s.map_scale_mode},
        "division_por_cero": f"|denominador| <= {config.get('zero_division_eps')} → NaN (excluido)",
        "outliers": "No se eliminan valores extremos automáticamente",
        "convencion_coordenadas_roi": "Píxeles de la imagen original; origen arriba-izquierda; x2 e y2 excluidos",
        "fotos": [{"nombre": p.name, "ruta": p.path, "resolucion": f"{p.width}x{p.height}",
                   "tamano_bytes": p.size_bytes, "formato": p.format, "fecha": p.date,
                   "origen_fecha": p.date_source, "valida": p.valid} for p in project.photos],
        "parcelas": project.rois.to_list(),
        "datos_campo": {"origen": project.field_source, "n_mediciones": len(project.field_measurements),
                        "calibracion": project.calibration.to_dict(),
                        "aviso_calibracion": CALIBRATION_NOTICE if project.calibration.enabled else None,
                        "n_observaciones": len(project.observations)},
        "configuracion": config,
        "control_calidad": qc.to_list() if qc else [],
        "limitaciones": LIMITATIONS,
        "archivos_generados": sorted(rel(f) for f in (files or [])),
    }


# ---------------------------------------------------------------------------
# 5. Flujo completo (modo consola / demo)
# ---------------------------------------------------------------------------
def run_full_analysis(project: Project, output_dir: Path | str, config: dict,
                      progress: ProgressCallback | None = None, cancel: threading.Event | None = None,
                      analyze: bool = True, make_report: bool = True,
                      field_errors: list[str] | None = None) -> AnalysisResult:
    """Ejecuta todo el flujo y escribe todos los archivos de resultados."""
    from .export import export_all  # import local para evitar ciclos
    from .report import write_reports

    progress = progress or _noop_progress
    output_dir = Path(output_dir)
    ensure_output_dirs(output_dir)
    images = None
    if analyze:
        images = analyze_images(project, output_dir, config,
                                progress=lambda f, m: progress(f * 0.8, m), cancel=cancel)
        project.set_roi_results(images.roi_df, images.image_df, str(output_dir))
        roi_df = images.roi_df
    else:
        roi_df = project.roi_results_df()
    progress(0.82, "Combinando con datos de campo…")
    combined = combine_results(project, roi_df, config)
    progress(0.86, "Generando gráficas…")
    graph_files = generate_graphs(project, combined, output_dir, config)
    qc = build_qc_report(project.photo_dicts(), list(project.rois), roi_df, combined.field_df,
                         combined.observations_df, images.issues if images else None,
                         field_errors if field_errors is not None else project.field_messages,
                         int(config.get("roi_min_side", 5)), int(config.get("roi_min_area", 100)))
    result = AnalysisResult(project=project, output_dir=output_dir, images=images, combined=combined,
                            graph_files=graph_files, qc=qc)
    progress(0.92, "Exportando resultados…")
    result.export_files = export_all(result, config)
    if make_report:
        progress(0.96, "Generando informe…")
        result.report_files = write_reports(result, config)
    all_files = (images.map_files if images else []) + graph_files + result.export_files + result.report_files
    result.metadata = build_metadata(project, config, output_dir, images.scale_ranges if images else None,
                                     all_files, qc)
    write_json(output_dir / "analysis_metadata.json", result.metadata)
    write_json(output_dir / "reports" / "analysis_metadata.json", result.metadata)
    progress(1.0, "Análisis completado")
    return result
