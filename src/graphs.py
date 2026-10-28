"""Gráficas y mapas de índices (Matplotlib, sin pyplot: seguro en hilos).

Reglas de honestidad gráfica:
* Las barras parten siempre de 0.
* Los ejes de los scatter se ajustan automáticamente a los datos.
* Los mapas indican SIEMPRE el modo de escala (automática o común) y el
  rango utilizado; los valores fuera del rango se saturan y la barra de color
  lo indica con flechas.
* Las líneas de tendencia sólo se dibujan con suficientes datos.
* Los datos de demostración se marcan con una marca de agua.
"""

from __future__ import annotations

import logging
import math
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # backend sin ventana; la GUI muestra los PNG generados

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import colormaps  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from .correlation import CorrelationResult, label  # noqa: E402
from .indices import COVERAGE_LABEL, INDEX_DEFINITIONS, VEGETATION_MASK_LABEL  # noqa: E402
from .utils import DEMO_LABEL  # noqa: E402

logger = logging.getLogger("tr_rgb.graphs")

BAR_COLOR = "#4a7c59"
POINT_COLOR = "#2b5d8a"


def _figure(size=(8, 5)) -> Figure:
    fig = Figure(figsize=size, layout="constrained")
    FigureCanvasAgg(fig)
    return fig


def _watermark(fig: Figure, is_demo: bool) -> None:
    if is_demo:
        fig.text(0.5, 0.5, DEMO_LABEL, fontsize=18, color="red", alpha=0.25, ha="center", va="center",
                 rotation=25, weight="bold")


def _footer(fig: Figure, text: str, width: int = 140) -> None:
    """Nota al pie que el diseño "constrained" tiene en cuenta (no se solapa con los ejes)."""
    if text:
        fig.supxlabel("\n".join(textwrap.wrap(text, width)), fontsize=7, color="#444", x=0.01, ha="left")


def save_figure(fig: Figure, path: Path | str, dpi: int = 150) -> Path:
    """Guarda la figura en PNG (crea la carpeta si no existe) y libera memoria."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi)
    fig.clear()
    return path


# ---------------------------------------------------------------------------
# Mapas
# ---------------------------------------------------------------------------
def _draw_rois(ax, rois_scaled: list[tuple[str, float, float, float, float]], color: str = "black") -> None:
    for pid, x1, y1, x2, y2 in rois_scaled:
        ax.add_patch(Rectangle((x1, y1), x2 - x1, y2 - y1, fill=False, edgecolor=color, linewidth=1.5))
        ax.text(x1 + 3, y1 + 3, pid, color="white", fontsize=9, va="top", ha="left", weight="bold",
                bbox={"facecolor": "black", "alpha": 0.6, "pad": 1.5, "edgecolor": "none"})


def colorize(values: np.ndarray, vmin: float, vmax: float, cmap_name: str = "RdYlGn",
             nan_color=(128, 128, 128)) -> np.ndarray:
    """Convierte un mapa de índice en una imagen RGB uint8 (para el visor)."""
    cmap = colormaps[cmap_name]
    span = vmax - vmin if vmax > vmin else 1.0
    norm = np.clip((values - vmin) / span, 0, 1)
    rgba = cmap(np.nan_to_num(norm, nan=0.0))
    out = (rgba[..., :3] * 255).astype(np.uint8)
    out[~np.isfinite(values)] = nan_color
    return out


def index_map(values: np.ndarray, key: str, photo_name: str, vmin: float, vmax: float,
              scale_description: str, path: Path | str, rois_scaled=None, cmap_name: str = "RdYlGn",
              is_demo: bool = False, dpi: int = 150, note: str = "") -> Path:
    """Guarda el mapa de un índice con título, barra de color y escala."""
    h, w = values.shape
    fig = _figure((9, 9 * h / max(w, 1) * 0.85 + 1.2))
    ax = fig.add_subplot(111)
    cmap = colormaps[cmap_name].with_extremes(bad="#808080")
    if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmin >= vmax:
        vmin, vmax = (vmin - 0.5, vmin + 0.5) if math.isfinite(vmin) else (-1, 1)
    im = ax.imshow(np.ma.masked_invalid(values), cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
    if rois_scaled:
        _draw_rois(ax, rois_scaled)
    d = INDEX_DEFINITIONS.get(key)
    exp = " (experimental)" if d and d.experimental else ""
    ax.set_title(f"{key}{exp} — {photo_name}\n{d.formula if d else ''}", fontsize=11)
    ax.set_xlabel("X (px)")
    ax.set_ylabel("Y (px)")
    cb = fig.colorbar(im, ax=ax, shrink=0.85, extend="both")
    cb.set_label(f"{key} — {scale_description} [{vmin:.3f}, {vmax:.3f}]")
    footer = "Gris = píxeles excluidos o no calculables. Índice RGB relativo (no es NDVI)."
    if note:
        footer += " " + note
    _footer(fig, footer)
    _watermark(fig, is_demo)
    return save_figure(fig, path, dpi)


def rgb_with_rois(rgb: np.ndarray, photo_name: str, path: Path | str, rois_scaled=None,
                  is_demo: bool = False, dpi: int = 150) -> Path:
    """Guarda la fotografía (reducida) con las parcelas dibujadas."""
    h, w = rgb.shape[:2]
    fig = _figure((9, 9 * h / max(w, 1) * 0.85 + 0.8))
    ax = fig.add_subplot(111)
    ax.imshow(rgb)
    if rois_scaled:
        _draw_rois(ax, rois_scaled, color="yellow")
    ax.set_title(f"Parcelas / ROIs — {photo_name}", fontsize=11)
    ax.set_xlabel("X (px)")
    ax.set_ylabel("Y (px)")
    _watermark(fig, is_demo)
    return save_figure(fig, path, dpi)


def mask_map(mask: np.ndarray, valid: np.ndarray, photo_name: str, coverage_pct: float, path: Path | str,
             method_description: str, rois_scaled=None, is_demo: bool = False, dpi: int = 150) -> Path:
    """Guarda la máscara RGB aproximada de vegetación."""
    h, w = mask.shape
    img = np.full((h, w, 3), 230, dtype=np.uint8)
    img[valid & ~mask] = (166, 124, 82)
    img[mask] = (46, 139, 87)
    img[~valid] = (128, 128, 128)
    fig = _figure((9, 9 * h / max(w, 1) * 0.85 + 1.0))
    ax = fig.add_subplot(111)
    ax.imshow(img, interpolation="nearest")
    if rois_scaled:
        _draw_rois(ax, rois_scaled)
    ax.set_title(f"{VEGETATION_MASK_LABEL} — {photo_name}\n{method_description}", fontsize=11)
    _footer(fig, f"Verde = clasificado como vegetación; marrón = resto; gris = excluido. "
                 f"{COVERAGE_LABEL} (foto completa): {coverage_pct:.1f} %. Clasificación aproximada, no perfecta.")
    _watermark(fig, is_demo)
    return save_figure(fig, path, dpi)


# ---------------------------------------------------------------------------
# Gráficas por parcela
# ---------------------------------------------------------------------------
def bar_by_parcel(df: pd.DataFrame, value_col: str, title: str, ylabel: str, path: Path | str,
                  err_col: str | None = None, err_label: str = "", n_col: str | None = None,
                  ylim: tuple[float, float] | None = None, is_demo: bool = False, dpi: int = 150,
                  note: str = "") -> Path | None:
    """Gráfica de barras por parcela (las barras parten de 0)."""
    if df is None or df.empty or value_col not in df:
        return None
    data = df[["parcela", value_col] + ([err_col] if err_col and err_col in df else [])
              + ([n_col] if n_col and n_col in df else [])].copy()
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
    fig = _figure((max(6, 0.9 * len(data) + 3), 5))
    ax = fig.add_subplot(111)
    x = np.arange(len(data))
    vals = data[value_col].to_numpy(dtype=float)
    errs = None
    if err_col and err_col in data:
        errs = pd.to_numeric(data[err_col], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(errs).any():
            errs = None
    plot_vals = np.where(np.isfinite(vals), vals, 0)
    ax.bar(x, plot_vals, color=BAR_COLOR, edgecolor="black", linewidth=0.6,
           yerr=None if errs is None else np.where(np.isfinite(errs), errs, 0), capsize=4)
    for i, v in enumerate(vals):
        if not np.isfinite(v):
            ax.text(i, 0, "sin datos", ha="center", va="bottom", fontsize=8, color="#a00", rotation=90)
        else:
            txt = f"{v:.3f}" if abs(v) < 100 else f"{v:.0f}"
            if n_col and n_col in data:
                txt += f" (n={int(data[n_col].iloc[i])})" if pd.notna(data[n_col].iloc[i]) else ""
            e = errs[i] if errs is not None and np.isfinite(errs[i]) else 0.0
            y_txt = v + e if v >= 0 else v - e
            ax.annotate(txt, (i, y_txt), textcoords="offset points", xytext=(0, 3 if v >= 0 else -12),
                        ha="center", fontsize=8)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(x, data["parcela"].astype(str))
    ax.set_xlabel("Parcela")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if ylim:
        ax.set_ylim(*ylim)
    ax.grid(axis="y", alpha=0.3)
    footer = []
    if errs is not None and err_label:
        footer.append(f"Barras de error: {err_label}.")
    if note:
        footer.append(note)
    _footer(fig, " ".join(footer))
    _watermark(fig, is_demo)
    return save_figure(fig, path, dpi)


def scatter_correlation(df: pd.DataFrame, res: CorrelationResult, path: Path | str,
                        trend_min_n: int = 5, show_trend: bool = True, is_demo: bool = False,
                        dpi: int = 150) -> Path | None:
    """Diagrama de dispersión con Pearson, Spearman, n y tendencia opcional."""
    vx, vy = res.var_x, res.var_y
    if df is None or df.empty or vx not in df or vy not in df:
        return None
    x = pd.to_numeric(df[vx], errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(df[vy], errors="coerce").to_numpy(dtype=float)
    ids = df["parcela"].astype(str).to_numpy() if "parcela" in df else np.array([""] * len(x))
    ok = np.isfinite(x) & np.isfinite(y)
    fig = _figure((7, 5.5))
    ax = fig.add_subplot(111)
    ax.scatter(x[ok], y[ok], s=60, color=POINT_COLOR, edgecolor="black", zorder=3)
    for xi, yi, pid in zip(x[ok], y[ok], ids[ok]):
        ax.annotate(pid, (xi, yi), textcoords="offset points", xytext=(5, 5), fontsize=9)
    n = int(ok.sum())
    trend_drawn = False
    if show_trend and n >= trend_min_n and np.ptp(x[ok]) > 0:
        slope, intercept = np.polyfit(x[ok], y[ok], 1)
        xs = np.linspace(x[ok].min(), x[ok].max(), 50)
        ax.plot(xs, slope * xs + intercept, "--", color="#c0392b", label="Tendencia lineal (descriptiva)")
        ax.legend(loc="lower right", fontsize=8)
        trend_drawn = True

    def _f(v, fmt="{:.2f}"):
        return fmt.format(v) if v is not None and math.isfinite(v) else "—"

    stats_line = (f"Pearson r = {_f(res.pearson_r)} (p = {_f(res.pearson_p, '{:.3f}')})   ·   "
                  f"Spearman ρ = {_f(res.spearman_rho)} (p = {_f(res.spearman_p, '{:.3f}')})   ·   n = {res.n}")
    if res.warning:
        stats_line += "   ·   " + ("exploratorio" if res.n >= 3 else "datos insuficientes")
    ax.set_xlabel(label(vx))
    ax.set_ylabel(label(vy))
    if vx == "estado_general":
        ax.set_xlim(0.5, 5.5)
        ax.set_xticks([1, 2, 3, 4, 5])
    ax.margins(0.12)
    ax.set_title(f"{label(vx)} vs {label(vy)}\n{stats_line}", fontsize=10)
    ax.grid(alpha=0.3)
    footer = "Asociación ≠ causalidad."
    if show_trend and not trend_drawn:
        footer += f" Sin línea de tendencia (n < {trend_min_n} o datos insuficientes)."
    _footer(fig, footer)
    _watermark(fig, is_demo)
    return save_figure(fig, path, dpi)


def temporal_plot(temporal_df: pd.DataFrame, key: str, stat: str, path: Path | str,
                  is_demo: bool = False, dpi: int = 150) -> Path | None:
    """Evolución de un índice frente a la fecha de la foto, una línea por parcela."""
    col = f"{key}_{stat}"
    if temporal_df is None or temporal_df.empty or col not in temporal_df:
        return None
    if temporal_df["fecha"].nunique() < 2:
        return None
    fig = _figure((8, 5))
    ax = fig.add_subplot(111)
    for pid, grp in temporal_df.groupby("parcela"):
        grp = grp.sort_values("fecha")
        ax.plot(grp["fecha"], pd.to_numeric(grp[col], errors="coerce"), marker="o", label=str(pid))
    ax.set_xlabel("Fecha de la fotografía")
    ax.set_ylabel(f"{key} ({'media' if stat == 'mean' else 'mediana'} de la ROI)")
    ax.set_title(f"{key} vs fecha")
    ax.grid(alpha=0.3)
    ax.legend(title="Parcela", fontsize=8, loc="center left", bbox_to_anchor=(1.01, 0.5))
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(30)
        lbl.set_ha("right")
    _watermark(fig, is_demo)
    return save_figure(fig, path, dpi)
