"""Control de calidad (QC) de datos de entrada y resultados.

Detecta: imágenes corruptas, ROIs fuera de límites / demasiado pequeñas /
vacías, divisiones por cero, valores NaN o infinitos, CSV incorrectos,
parcelas sin datos de campo, mediciones de campo sin imagen e imágenes sin
parcela. Nunca corrige ni elimina datos: sólo informa.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .roi import ROI, validate_roi

SYMBOLS = {"ok": "✓", "warning": "⚠", "error": "✗", "info": "•"}


@dataclass
class QCItem:
    level: str  # "ok" | "warning" | "error" | "info"
    category: str
    message: str

    def text(self) -> str:
        return f"{SYMBOLS.get(self.level, '•')} {self.message}"


@dataclass
class QCReport:
    """Conjunto de comprobaciones de calidad."""

    items: list[QCItem] = field(default_factory=list)

    def add(self, level: str, category: str, message: str) -> None:
        self.extend([QCItem(level, category, message)])

    def ok(self, category: str, message: str) -> None:
        self.add("ok", category, message)

    def warning(self, category: str, message: str) -> None:
        self.add("warning", category, message)

    def error(self, category: str, message: str) -> None:
        self.add("error", category, message)

    def extend(self, items: list[QCItem]) -> None:
        """Añade comprobaciones sin repetir mensajes idénticos."""
        seen = {(i.level, i.message) for i in self.items}
        for item in items:
            if (item.level, item.message) not in seen:
                self.items.append(item)
                seen.add((item.level, item.message))

    @property
    def n_errors(self) -> int:
        return sum(1 for i in self.items if i.level == "error")

    @property
    def n_warnings(self) -> int:
        return sum(1 for i in self.items if i.level == "warning")

    @property
    def has_errors(self) -> bool:
        return self.n_errors > 0

    def to_text(self) -> str:
        lines = ["CONTROL DE CALIDAD", ""]
        lines += [i.text() for i in self.items]
        lines += ["", f"Resumen: {self.n_errors} errores, {self.n_warnings} avisos."]
        return "\n".join(lines)

    def to_list(self) -> list[dict]:
        return [asdict(i) for i in self.items]


def check_finite(arr: np.ndarray) -> tuple[int, int]:
    """Número de valores NaN e infinitos de un array."""
    a = np.asarray(arr, dtype=float)
    return int(np.isnan(a).sum()), int(np.isinf(a).sum())


def validate_rois(rois: list[ROI], image_sizes: dict[str, tuple[int, int]],
                  min_side: int = 5, min_area: int = 100) -> list[QCItem]:
    """Valida todas las ROIs frente al tamaño de su imagen.

    Args:
        image_sizes: ``{nombre_foto: (ancho, alto)}`` de las fotos válidas.
    """
    items = []
    for roi in rois:
        if roi.photo not in image_sizes:
            items.append(QCItem("error", "roi", f"ROI {roi.parcel_id}: la foto '{roi.photo}' no está en el proyecto o no es válida"))
            continue
        w, h = image_sizes[roi.photo]
        for level, msg in validate_roi(roi, w, h, min_side, min_area):
            items.append(QCItem(level, "roi", msg))
    return items


def build_qc_report(photos: list[dict], rois: list[ROI], roi_df: pd.DataFrame | None = None,
                    field_df: pd.DataFrame | None = None, observations_df: pd.DataFrame | None = None,
                    analysis_issues: list[QCItem] | None = None, field_errors: list[str] | None = None,
                    min_side: int = 5, min_area: int = 100) -> QCReport:
    """Construye el panel de control de calidad completo.

    Args:
        photos: lista de diccionarios con al menos ``name``, ``valid``,
            ``width``, ``height`` y opcionalmente ``error``.
        rois: ROIs del proyecto.
        roi_df: resultados por ROI (si ya se ha analizado).
        field_df: mediciones de humedad.
        observations_df: observaciones visuales.
        analysis_issues: problemas detectados durante el análisis.
        field_errors: errores/avisos de lectura de los CSV de campo.
    """
    qc = QCReport()

    # -- Imágenes ---------------------------------------------------------------
    valid_photos = [p for p in photos if p.get("valid", True)]
    invalid_photos = [p for p in photos if not p.get("valid", True)]
    if not photos:
        qc.warning("imagen", "No hay fotografías cargadas")
    else:
        qc.ok("imagen", f"{len(valid_photos)} imágenes válidas")
    for p in invalid_photos:
        qc.error("imagen", f"Imagen corrupta o ilegible: {p.get('name')}")
    sizes = {p["name"]: (int(p.get("width", 0)), int(p.get("height", 0))) for p in valid_photos}

    # -- ROIs ---------------------------------------------------------------------
    roi_items: list[QCItem] = []
    analyzable_parcels: set[str] = set()
    for roi in rois:
        items = validate_rois([roi], sizes, min_side, min_area)
        roi_items += items
        if not any(i.level == "error" for i in items):
            analyzable_parcels.add(roi.parcel_id)
    parcels = sorted({r.parcel_id for r in rois})
    if not rois:
        qc.warning("roi", "No hay parcelas/ROIs definidas")
    else:
        qc.ok("roi", f"{len(analyzable_parcels)} parcelas válidas ({len(rois)} ROIs definidas)")
    qc.extend(roi_items)
    photos_with_roi = {r.photo for r in rois}
    for p in valid_photos:
        if p["name"] not in photos_with_roi:
            qc.warning("imagen", f"Imagen sin parcela: {p['name']}")

    # -- Resultados del análisis ---------------------------------------------------
    if analysis_issues:
        qc.extend(analysis_issues)
    if roi_df is not None and not roi_df.empty:
        qc.ok("analisis", f"{len(roi_df)} ROIs analizadas")
        stat_cols = [c for c in roi_df.columns if c.endswith(("_mean", "_median", "_std", "_min", "_max"))]
        values = roi_df[stat_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float) if stat_cols else np.empty(0)
        n_nan, n_inf = check_finite(values) if values.size else (0, 0)
        # La sd es NaN legítimamente si sólo hay 1 píxel; se cuentan aparte las medias.
        mean_cols = [c for c in stat_cols if c.endswith("_mean")]
        n_nan_mean = int(roi_df[mean_cols].isna().sum().sum()) if mean_cols else 0
        if n_nan_mean:
            qc.warning("analisis", f"{n_nan_mean} medias de índice son NaN (ROI sin píxeles válidos o "
                                   "sin píxeles de vegetación)")
        elif n_nan == 0:
            qc.ok("analisis", "Sin valores NaN en los resultados")
        else:
            qc.warning("analisis", f"{n_nan} estadísticos NaN en los resultados")
        if n_inf:
            qc.error("analisis", f"{n_inf} valores infinitos en los resultados")
        div_cols = [c for c in roi_df.columns if c.endswith("_div_cero")]
        n_div = int(pd.to_numeric(roi_df[div_cols].stack(), errors="coerce").fillna(0).sum()) if div_cols else 0
        if n_div == 0:
            qc.ok("analisis", "Sin divisiones problemáticas")
        else:
            qc.warning("analisis", f"{n_div} píxeles con división por cero dentro de las ROIs "
                                   "(excluidos de las estadísticas)")
        clip_cols = [c for c in roi_df.columns if c.endswith("_recortados")]
        for c in clip_cols:
            n_clip = int(pd.to_numeric(roi_df[c], errors="coerce").fillna(0).sum())
            if n_clip:
                qc.warning("analisis", f"{n_clip} píxeles de {c[:-len('_recortados')]} dentro de las ROIs "
                                       "estaban fuera de [−1, 1] y se han recortado a ese intervalo")
        if "pixeles_analizados" in roi_df:
            empty = roi_df[pd.to_numeric(roi_df["pixeles_analizados"], errors="coerce").fillna(0) == 0]
            for _, r in empty.iterrows():
                qc.error("analisis", f"ROI vacía tras el filtrado: parcela {r['parcela']} en {r['foto']}")
    elif rois and photos:
        qc.add("info", "analisis", "Todavía no se ha ejecutado el análisis")

    # -- Datos de campo -------------------------------------------------------------
    for msg in field_errors or []:
        qc.warning("campo", msg)
    field_parcels = set()
    if field_df is not None and not field_df.empty:
        field_parcels = set(field_df["parcela"].astype(str))
        qc.ok("campo", f"{len(field_df)} mediciones de humedad en {len(field_parcels)} parcelas")
    else:
        qc.warning("campo", "No hay datos de humedad (CSV Arduino) cargados")
    obs_parcels = set()
    if observations_df is not None and not observations_df.empty:
        with_state = observations_df[pd.to_numeric(observations_df["estado_general"], errors="coerce").notna()]
        obs_parcels = set(with_state["parcela"].astype(str))
        qc.ok("campo", f"Estado general registrado en {len(obs_parcels)} parcelas")
    else:
        qc.warning("campo", "No hay observaciones de estado general")

    image_parcels = set(parcels)
    missing_moisture = sorted(image_parcels - field_parcels) if field_parcels else []
    if missing_moisture:
        qc.warning("campo", f"{len(missing_moisture)} parcela(s) sin humedad: {', '.join(missing_moisture)}")
    missing_state = sorted(image_parcels - obs_parcels) if obs_parcels else []
    if missing_state:
        qc.warning("campo", f"{len(missing_state)} parcela(s) sin estado general: {', '.join(missing_state)}")
    no_image = sorted((field_parcels | obs_parcels) - image_parcels)
    if no_image:
        qc.warning("campo", f"Mediciones de campo sin imagen/ROI: parcelas {', '.join(no_image)}")
    return qc
