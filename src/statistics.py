"""Estadística descriptiva de índices por ROI y agregación por parcela.

* Sólo se usan valores finitos (los NaN de píxeles inválidos o divisiones por
  cero se excluyen y se contabilizan aparte).
* La desviación estándar es la muestral (ddof = 1).
* NO se eliminan outliers automáticamente.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

PERCENTILES = (5, 25, 75, 95)
STAT_KEYS = ("mean", "median", "std", "min", "max", "p5", "p25", "p75", "p95", "n")
STAT_LABELS_ES = {
    "mean": "media", "median": "mediana", "std": "desviación estándar", "min": "mínimo",
    "max": "máximo", "p5": "percentil 5", "p25": "percentil 25", "p75": "percentil 75",
    "p95": "percentil 95", "n": "número de píxeles",
}


def describe(values: np.ndarray) -> dict[str, float]:
    """Estadística descriptiva de un conjunto de valores.

    Args:
        values: array de cualquier forma; los valores no finitos se ignoran.

    Returns:
        Diccionario con mean, median, std, min, max, p5, p25, p75, p95 y n.
        Si no hay valores finitos, todos los estadísticos son NaN y n = 0.
    """
    arr = np.asarray(values, dtype=np.float64).ravel()
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n == 0:
        out = {k: math.nan for k in STAT_KEYS}
        out["n"] = 0
        return out
    pcts = np.percentile(arr, [5, 25, 50, 75, 95])
    return {
        "mean": float(arr.mean()),
        "median": float(pcts[2]),
        "std": float(arr.std(ddof=1)) if n > 1 else math.nan,
        "min": float(arr.min()),
        "max": float(arr.max()),
        "p5": float(pcts[0]),
        "p25": float(pcts[1]),
        "p75": float(pcts[3]),
        "p95": float(pcts[4]),
        "n": n,
    }


def describe_prefixed(values: np.ndarray, prefix: str) -> dict[str, float]:
    """Como :func:`describe` pero con claves ``f"{prefix}_{estadístico}"``."""
    return {f"{prefix}_{k}": v for k, v in describe(values).items()}


def coefficient_of_variation(values) -> float:
    """CV (%) = desviación estándar / |media| × 100. NaN si no es calculable."""
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return math.nan
    m = arr.mean()
    if abs(m) < 1e-12:
        return math.nan
    return float(arr.std(ddof=1) / abs(m) * 100)


def robust_range(values: np.ndarray, low: float = 2, high: float = 98,
                 max_samples: int = 2_000_000) -> tuple[float, float]:
    """Percentiles ``low`` y ``high`` de los valores finitos (para escalas de color).

    Para imágenes grandes se usa una submuestra regular (determinista).
    """
    arr = np.asarray(values).ravel()
    if arr.size > max_samples:
        step = int(math.ceil(arr.size / max_samples))
        arr = arr[::step]
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return math.nan, math.nan
    lo, hi = np.percentile(arr, [low, high])
    return float(lo), float(hi)


def summarize_series(series: pd.Series) -> dict[str, float]:
    """media, mediana, sd, mínimo, máximo y n de una serie (para datos de campo)."""
    d = describe(pd.to_numeric(series, errors="coerce").to_numpy(dtype=float))
    return {"media": d["mean"], "mediana": d["median"], "sd": d["std"],
            "min": d["min"], "max": d["max"], "n": d["n"]}


def aggregate_by_parcel(roi_df: pd.DataFrame, index_keys: list[str], stat: str = "mean",
                        experimental_keys: list[str] | None = None) -> pd.DataFrame:
    """Agrega los resultados por ROI (parcela × foto) a una fila por parcela.

    Para cada índice ``K`` se calcula, a partir del estadístico ``stat`` de
    cada ROI (``K_mean`` o ``K_median``):

    * ``K``: media entre fotografías,
    * ``K_sd_entre_fotos``: desviación estándar entre fotografías,
    * ``K_cv_entre_fotos_pct``: coeficiente de variación entre fotografías.

    Los índices experimentales se añaden con el prefijo ``EXP_``.
    """
    experimental_keys = experimental_keys or []
    if roi_df is None or roi_df.empty:
        return pd.DataFrame(columns=["parcela"])
    rows = []
    for pid, grp in roi_df.groupby("parcela", sort=False):
        row = {
            "parcela": pid,
            "nombre": next((n for n in grp.get("nombre_parcela", pd.Series(dtype=str)) if isinstance(n, str) and n), ""),
            "n_fotos": int(grp["foto"].nunique()),
            "fotos": ", ".join(sorted(grp["foto"].astype(str).unique())),
        }
        for key, out_key in [(k, k) for k in index_keys] + [(k, f"EXP_{k}") for k in experimental_keys]:
            col = f"{key}_{stat}"
            if col not in grp:
                continue
            vals = pd.to_numeric(grp[col], errors="coerce").to_numpy(dtype=float)
            finite = vals[np.isfinite(vals)]
            row[out_key] = float(finite.mean()) if finite.size else math.nan
            row[f"{out_key}_sd_entre_fotos"] = float(finite.std(ddof=1)) if finite.size > 1 else math.nan
            row[f"{out_key}_cv_entre_fotos_pct"] = coefficient_of_variation(finite)
        if "cobertura_rgb_pct" in grp:
            cov = pd.to_numeric(grp["cobertura_rgb_pct"], errors="coerce").to_numpy(dtype=float)
            cov = cov[np.isfinite(cov)]
            row["cobertura_rgb_pct"] = float(cov.mean()) if cov.size else math.nan
        rows.append(row)
    df = pd.DataFrame(rows)
    return df


def photo_comparison_table(roi_df: pd.DataFrame, index_keys: list[str], stat: str = "mean") -> pd.DataFrame:
    """Comparación de una misma parcela entre fotografías.

    Una fila por parcela e índice con: n_fotos, media, sd, CV, mínimo, máximo
    y la diferencia entre la última y la primera foto (si hay fechas).
    """
    if roi_df is None or roi_df.empty:
        return pd.DataFrame()
    rows = []
    for pid, grp in roi_df.groupby("parcela", sort=False):
        grp = grp.copy()
        grp["_fecha"] = pd.to_datetime(grp.get("fecha_foto"), errors="coerce")
        has_dates = grp["_fecha"].notna().all() and grp["_fecha"].nunique() > 1
        if has_dates:
            grp = grp.sort_values("_fecha")
        for key in index_keys:
            col = f"{key}_{stat}"
            if col not in grp:
                continue
            vals = pd.to_numeric(grp[col], errors="coerce").to_numpy(dtype=float)
            finite = vals[np.isfinite(vals)]
            rows.append({
                "parcela": pid,
                "indice": key,
                "estadistico_roi": stat,
                "n_fotos": int(finite.size),
                "media": float(finite.mean()) if finite.size else math.nan,
                "sd": float(finite.std(ddof=1)) if finite.size > 1 else math.nan,
                "cv_pct": coefficient_of_variation(finite),
                "min": float(finite.min()) if finite.size else math.nan,
                "max": float(finite.max()) if finite.size else math.nan,
                "cambio_ultima_menos_primera": float(vals[-1] - vals[0]) if has_dates and finite.size > 1 else math.nan,
                "orden_temporal": "por fecha" if has_dates else "sin fechas suficientes",
            })
    return pd.DataFrame(rows)


def temporal_table(roi_df: pd.DataFrame, index_keys: list[str], stat: str = "mean") -> pd.DataFrame:
    """Tabla parcela × fecha con el valor de cada índice (sólo fotos con fecha)."""
    if roi_df is None or roi_df.empty or "fecha_foto" not in roi_df:
        return pd.DataFrame()
    df = roi_df.copy()
    df["fecha"] = pd.to_datetime(df["fecha_foto"], errors="coerce")
    df = df[df["fecha"].notna()]
    if df.empty:
        return pd.DataFrame()
    cols = ["parcela", "foto", "fecha"] + [f"{k}_{stat}" for k in index_keys if f"{k}_{stat}" in df]
    return df[cols].sort_values(["parcela", "fecha"]).reset_index(drop=True)
