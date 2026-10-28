"""Exportación de resultados a CSV y Excel (.xlsx).

Formatos CSV (config.json → ``csv_format``):
* ``"excel_es"`` (por defecto): separador ``;`` y decimal ``,``. Se abre
  directamente con Excel configurado en español/catalán.
* ``"standard"``: separador ``,`` y decimal ``.`` (Python, R, LibreOffice…).

Los CSV se escriben en UTF-8 con BOM para que Excel muestre bien los acentos.
Si el proyecto es de demostración se añade la columna ``origen_datos`` con el
aviso "DEMO — NO SON DATOS EXPERIMENTALES".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from .utils import APP_NAME, APP_VERSION, DEMO_LABEL, ensure_output_dirs, now_iso

if TYPE_CHECKING:  # pragma: no cover
    from .pipeline import AnalysisResult

logger = logging.getLogger("tr_rgb.export")

try:
    import openpyxl  # noqa: F401

    HAS_OPENPYXL = True
except Exception:  # noqa: BLE001
    HAS_OPENPYXL = False


def csv_options(csv_format: str) -> dict:
    if csv_format == "standard":
        return {"sep": ",", "decimal": "."}
    return {"sep": ";", "decimal": ","}


def _mark_demo(df: pd.DataFrame, is_demo: bool) -> pd.DataFrame:
    if not is_demo or df is None:
        return df
    df = df.copy()
    df.insert(0, "origen_datos", DEMO_LABEL)
    return df


def write_csv(df: pd.DataFrame | None, path: Path | str, csv_format: str = "excel_es",
              is_demo: bool = False) -> Path:
    """Escribe un DataFrame en CSV (crea la carpeta si no existe)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df if df is not None else pd.DataFrame()
    _mark_demo(df, is_demo).to_csv(path, index=False, encoding="utf-8-sig", float_format="%.6g",
                                   **csv_options(csv_format))
    return path


def summary_table(result: "AnalysisResult") -> pd.DataFrame:
    """Tabla clave-valor con el resumen del análisis (hoja 'Resumen')."""
    p = result.project
    s = p.settings
    c = result.combined
    rows = [
        ("Software", f"{APP_NAME} {APP_VERSION}"),
        ("Fecha de exportación", now_iso()),
        ("Proyecto", p.name),
        ("Datos de demostración", DEMO_LABEL if p.is_demo else "No"),
        ("Último análisis de imágenes", p.results.get("last_analysis", "—")),
        ("Número de fotografías", len(p.photos)),
        ("Número de parcelas", len(p.rois.parcel_ids())),
        ("Número de ROIs", len(p.rois)),
        ("Índices principales", ", ".join(s.main_indices)),
        ("Índices experimentales", ", ".join(s.experimental_indices) or "ninguno"),
        ("Píxeles analizados", "solo vegetación (máscara RGB aproximada)" if s.only_vegetation else "todos los válidos"),
        ("Estadístico por ROI usado en la tabla de parcelas", "media" if s.aggregate_stat == "mean" else "mediana"),
        ("Mediciones de humedad", len(c.field_df)),
        ("Calibración del sensor", "activa" if p.calibration.is_usable else "no aplicada (sólo RAW)"),
        ("Observaciones visuales", len(c.observations_df)),
        ("Tipo de imagen", "RGB (sin NIR; NDVI no calculable)"),
    ]
    return pd.DataFrame(rows, columns=["Elemento", "Valor"])


def photos_table(result: "AnalysisResult") -> pd.DataFrame:
    rows = [{"foto": ph.name, "ruta": ph.path, "ancho": ph.width, "alto": ph.height,
             "tamano_bytes": ph.size_bytes, "formato": ph.format, "fecha": ph.date,
             "origen_fecha": ph.date_source, "valida": ph.valid, "error": ph.error}
            for ph in result.project.photos]
    df = pd.DataFrame(rows)
    img = result.images.image_df if result.images is not None else result.project.image_summary_df()
    if img is not None and not img.empty and not df.empty:
        extra = img.drop(columns=[c for c in ("fecha_foto", "origen_fecha", "valida") if c in img])
        df = df.merge(extra, on="foto", how="left")
    return df


def export_all(result: "AnalysisResult", config: dict) -> list[Path]:
    """Escribe todas las tablas en ``statistics/`` y ``exports/`` (+ Excel)."""
    dirs = ensure_output_dirs(result.output_dir)
    fmt = config.get("csv_format", "excel_es")
    demo = result.project.is_demo
    c = result.combined
    roi_df = result.images.roi_df if result.images is not None else result.project.roi_results_df()
    files = []
    st, ex = dirs["statistics"], dirs["exports"]
    files.append(write_csv(roi_df, st / "resultados_por_roi.csv", fmt, demo))
    files.append(write_csv(photos_table(result), st / "resumen_imagenes.csv", fmt, demo))
    files.append(write_csv(c.comparison, st / "comparacion_entre_fotos.csv", fmt, demo))
    files.append(write_csv(c.temporal, st / "evolucion_temporal.csv", fmt, demo))
    files.append(write_csv(c.field_summary, st / "datos_campo_resumen.csv", fmt, demo))
    files.append(write_csv(c.observations_df, st / "observaciones.csv", fmt, demo))
    # Exportaciones finales
    files.append(write_csv(roi_df, ex / "resultados_completos.csv", fmt, demo))
    files.append(write_csv(c.parcel_table, ex / "resultados_por_parcela.csv", fmt, demo))
    files.append(write_csv(c.correlations_df, ex / "correlaciones.csv", fmt, demo))
    if not c.exp_correlations_df.empty:
        files.append(write_csv(c.exp_correlations_df, ex / "correlaciones_EXPERIMENTALES.csv", fmt, demo))
    files.append(write_csv(c.field_df, ex / "datos_campo.csv", fmt, demo))
    if result.qc is not None:
        files.append(write_csv(pd.DataFrame(result.qc.to_list()), st / "control_calidad.csv", fmt, demo))
    xlsx = export_excel(result, ex / "TR_RGB_resultados.xlsx")
    if xlsx:
        files.append(xlsx)
    return files


def export_excel(result: "AnalysisResult", path: Path | str) -> Path | None:
    """Genera un .xlsx con hojas Resumen, Fotos, Parcelas, ROIs, Campo, Observaciones,
    Correlaciones (y Correlaciones_EXP / Control_calidad). Requiere openpyxl."""
    if not HAS_OPENPYXL:
        logger.warning("openpyxl no está instalado: no se genera el archivo Excel.")
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    demo = result.project.is_demo
    c = result.combined
    roi_df = result.images.roi_df if result.images is not None else result.project.roi_results_df()
    sheets = {
        "Resumen": summary_table(result),
        "Fotos": photos_table(result),
        "Parcelas": c.parcel_table,
        "ROIs": roi_df,
        "Campo": c.field_df,
        "Campo_resumen": c.field_summary,
        "Observaciones": c.observations_df,
        "Correlaciones": c.correlations_df,
    }
    if not c.exp_correlations_df.empty:
        sheets["Correlaciones_EXP"] = c.exp_correlations_df
    if result.qc is not None:
        sheets["Control_calidad"] = pd.DataFrame(result.qc.to_list())
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for name, df in sheets.items():
                df = _mark_demo(df if df is not None else pd.DataFrame(), demo)
                df.to_excel(writer, sheet_name=name[:31], index=False)
                ws = writer.sheets[name[:31]]
                for col_cells in ws.columns:
                    width = max((len(str(cell.value)) if cell.value is not None else 0) for cell in col_cells[:200])
                    ws.column_dimensions[col_cells[0].column_letter].width = min(max(10, width + 2), 60)
    except PermissionError:
        logger.error("No se pudo escribir %s (¿está abierto en Excel?)", path)
        raise
    return path
