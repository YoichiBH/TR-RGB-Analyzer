"""Utilidades generales de TR RGB Analyzer.

Contiene constantes de la aplicación, gestión de carpetas, configuración,
registro (logging) y funciones auxiliares de lectura/escritura JSON.

Ninguna función de este módulo elimina archivos del usuario.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import logging
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np

APP_NAME = "TR RGB Analyzer"
APP_VERSION = "1.0.0"

#: Carpeta raíz de la aplicación (la que contiene app.py y main.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Subcarpetas que se crean dentro de cada carpeta de resultados.
OUTPUT_SUBDIRS = ("indices", "rois", "statistics", "graphs", "reports", "exports")

#: Carpetas que la aplicación necesita y crea automáticamente al arrancar.
REQUIRED_DIRS = (
    "input/fotos",
    "input/parcelas",
    "input/datos_campo",
    "output/indices",
    "output/rois",
    "output/statistics",
    "output/graphs",
    "output/reports",
    "output/exports",
    "projects",
    "logs",
)

DEMO_LABEL = "DEMO — NO SON DATOS EXPERIMENTALES"

#: Configuración por defecto. config.json sólo necesita contener los valores
#: que se quieran cambiar; el resto se toma de aquí.
DEFAULT_CONFIG: dict[str, Any] = {
    "max_megapixels_warning": 40,
    "viewer_max_side": 2500,
    "map_max_side": 1600,
    "roi_min_side": 5,
    "roi_min_area": 100,
    "zero_division_eps": 1e-6,
    "auto_scale_percentiles": [2, 98],
    "map_colormap": "RdYlGn",
    "trend_line_min_n": 5,
    "correlation_min_n": 3,
    "correlation_exploratory_n": 10,
    "csv_format": "excel_es",
    "dpi": 150,
    "default_settings": {
        "main_indices": ["GLI", "VARI", "ExG"],
        "experimental_indices": [],
        "exg_mode": "chromatic",
        "vegetation_method": "exg",
        "vegetation_threshold": 0.1,
        "vegetation_exg_mode": "normalized",
        "vari_clip": True,
        "only_vegetation": False,
        "map_scale_mode": "common",
        "aggregate_stat": "mean",
        "preprocessing": {
            "normalize": True,
            "exclude_saturated": False,
            "exclude_dark": False,
            "dark_threshold": 10,
            "exclude_transparent": True,
            "downscale_max_side": None,
        },
    },
}

logger = logging.getLogger("tr_rgb")


# ---------------------------------------------------------------------------
# Carpetas
# ---------------------------------------------------------------------------
def ensure_app_dirs(root: Path | str = PROJECT_ROOT) -> list[Path]:
    """Crea (si no existen) todas las carpetas necesarias de la aplicación.

    Args:
        root: carpeta raíz de la aplicación.

    Returns:
        Lista de rutas de las carpetas garantizadas.
    """
    root = Path(root)
    created = []
    for rel in REQUIRED_DIRS:
        p = root / rel
        p.mkdir(parents=True, exist_ok=True)
        created.append(p)
    return created


def ensure_output_dirs(output_dir: Path | str) -> dict[str, Path]:
    """Crea la carpeta de resultados y sus subcarpetas estándar.

    Returns:
        Diccionario ``{"indices": Path, "graphs": Path, ..., "root": Path}``.
    """
    output_dir = Path(output_dir)
    dirs = {"root": output_dir}
    output_dir.mkdir(parents=True, exist_ok=True)
    for sub in OUTPUT_SUBDIRS:
        d = output_dir / sub
        d.mkdir(parents=True, exist_ok=True)
        dirs[sub] = d
    return dirs


def results_exist(output_dir: Path | str) -> bool:
    """Indica si en ``output_dir`` ya hay resultados de un análisis anterior."""
    output_dir = Path(output_dir)
    markers = [
        output_dir / "analysis_metadata.json",
        output_dir / "statistics" / "resultados_por_roi.csv",
    ]
    return any(m.exists() for m in markers)


def new_run_dir(output_dir: Path | str) -> Path:
    """Devuelve una carpeta nueva (con fecha y hora) dentro de ``output_dir``.

    Se usa cuando el usuario NO quiere sobrescribir resultados existentes.
    """
    base = Path(output_dir) / f"analisis_{timestamp_slug()}"
    candidate = base
    i = 2
    while candidate.exists():
        candidate = Path(f"{base}_{i}")
        i += 1
    return candidate


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
def deep_merge(base: dict, override: dict) -> dict:
    """Combina dos diccionarios de forma recursiva sin modificar los originales."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Path | str | None = None) -> dict:
    """Carga config.json combinándolo con la configuración por defecto.

    Si el archivo no existe o está dañado se usan los valores por defecto y se
    registra un aviso (la aplicación nunca se detiene por esto).
    """
    path = Path(path) if path else PROJECT_ROOT / "config.json"
    user_cfg: dict = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user_cfg = json.load(fh)
            if not isinstance(user_cfg, dict):
                raise ValueError("config.json no contiene un objeto JSON")
        except (OSError, ValueError) as exc:
            logger.warning("No se pudo leer %s (%s). Se usa la configuración por defecto.", path, exc)
            user_cfg = {}
    # Las claves que empiezan por "_" son comentarios en config.json.
    user_cfg = {k: v for k, v in user_cfg.items() if not str(k).startswith("_")}
    return deep_merge(DEFAULT_CONFIG, user_cfg)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(level: int = logging.INFO, log_dir: Path | str | None = None) -> logging.Logger:
    """Configura el registro en consola y en ``logs/tr_rgb_analyzer.log``."""
    log_dir = Path(log_dir) if log_dir else PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("tr_rgb")
    if root.handlers:
        root.setLevel(level)
        return root
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        fh = logging.FileHandler(log_dir / "tr_rgb_analyzer.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:  # p. ej. carpeta de sólo lectura
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root.addHandler(sh)
    return root


# ---------------------------------------------------------------------------
# Tiempo y texto
# ---------------------------------------------------------------------------
def now_iso() -> str:
    """Fecha y hora actual en formato ISO (sin microsegundos)."""
    return _dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def timestamp_slug() -> str:
    """Fecha y hora actual apta para nombres de archivo: ``20240512_153000``."""
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def slugify(text: str, default: str = "proyecto") -> str:
    """Convierte un texto en un nombre de archivo seguro (sin acentos ni espacios)."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9_\-]+", "_", text).strip("_")
    return text or default


def human_size(n_bytes: float) -> str:
    """Tamaño legible: ``2.4 MB``."""
    n = float(n_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_num(value: Any, decimals: int = 4) -> str:
    """Formatea un número para mostrarlo; NaN/None se muestran como ``—``."""
    if value is None:
        return "—"
    if isinstance(value, (bool, np.bool_)):
        return "sí" if value else "no"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(v) or math.isinf(v):
        return "—"
    return f"{v:.{decimals}f}"


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def json_default(obj: Any) -> Any:
    """Serializa tipos de NumPy/Path/fechas para ``json.dump``."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    raise TypeError(f"Tipo no serializable: {type(obj)}")


def _clean_floats(obj: Any) -> Any:
    """Sustituye NaN/inf por None para producir JSON estándar."""
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {k: _clean_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_floats(v) for v in obj]
    return obj


def write_json(path: Path | str, data: Any) -> Path:
    """Escribe ``data`` como JSON UTF-8 legible (crea la carpeta si hace falta).

    Escribe primero en un archivo temporal y luego lo renombra, para no dejar
    un JSON a medias si el programa se interrumpe.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_clean_floats(data), fh, ensure_ascii=False, indent=2, default=json_default)
    tmp.replace(path)
    return path


def read_json(path: Path | str) -> Any:
    """Lee un archivo JSON UTF-8."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
def to_relative(path: Path | str, base: Path | str) -> str | None:
    """Ruta relativa de ``path`` respecto a ``base`` o ``None`` si no es posible
    (por ejemplo, si están en unidades distintas en Windows)."""
    try:
        import os

        return os.path.relpath(Path(path).resolve(), Path(base).resolve())
    except ValueError:
        return None


def resolve_stored_path(stored_abs: str | None, stored_rel: str | None, base: Path | None) -> Path | None:
    """Resuelve una ruta guardada en un proyecto, prefiriendo la relativa si existe."""
    if stored_rel and base is not None:
        cand = (Path(base) / stored_rel).resolve()
        if cand.exists():
            return cand
    if stored_abs:
        return Path(stored_abs)
    if stored_rel and base is not None:
        return (Path(base) / stored_rel).resolve()
    return None
