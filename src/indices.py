"""Índices de vegetación calculados EXCLUSIVAMENTE a partir de bandas RGB.

IMPORTANTE (validez científica)
-------------------------------
* Las imágenes son RGB: NO existe banda del infrarrojo cercano (NIR), por lo
  que NO se calcula NDVI ni ningún índice que requiera NIR.
* GLI, VARI y ExG son los índices PRINCIPALES del experimento.
* ExR, ExGR y NGRDI se ofrecen como índices EXPERIMENTALES y se mantienen
  separados de los resultados principales.
* Todos los índices son indicadores RELATIVOS de "verdor". No son mediciones
  absolutas de clorofila, contenido de agua ni salud vegetal.

Convenciones
------------
* ``R``, ``G``, ``B`` son los canales en la escala elegida en el
  preprocesamiento (normalmente normalizados 0–1, es decir valor/255).
* Coordenadas cromáticas (opcional para ExG/ExR/ExGR):
  ``r = R/(R+G+B)``, ``g = G/(R+G+B)``, ``b = B/(R+G+B)``.
* División por cero: si ``|denominador| <= eps`` el resultado es ``NaN`` (no se
  inventa ningún valor) y el píxel se contabiliza como "división problemática".
* VARI: cuando el denominador G + R − B es muy pequeño (pero no cero) el índice
  toma valores extremos (p. ej. −85 o +79) que no tienen sentido físico. Por
  defecto se RECORTAN al intervalo [−1, 1] (opción ``vari_clip``) y se
  contabilizan los píxeles recortados. El resto de índices no se recortan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

DEFAULT_EPS = 1e-6

MAIN_INDICES = ("GLI", "VARI", "ExG")
EXPERIMENTAL_INDICES = ("ExR", "ExGR", "NGRDI")
EXG_MODES = {
    "normalized": "Valores RGB normalizados 0–1",
    "chromatic": "Coordenadas cromáticas r, g, b (R/(R+G+B), …)",
}


@dataclass(frozen=True)
class IndexDefinition:
    """Documentación científica de un índice RGB."""

    key: str
    name: str
    formula: str
    description: str
    typical_range: str
    interpretation: str
    limitations: str
    reference: str
    experimental: bool = False


INDEX_DEFINITIONS: dict[str, IndexDefinition] = {
    "GLI": IndexDefinition(
        key="GLI",
        name="Green Leaf Index",
        formula="GLI = (2G − R − B) / (2G + R + B)",
        description="Contraste del canal verde frente a rojo y azul, normalizado por el brillo total.",
        typical_range="Teórico: −1 a +1. Suelo desnudo ≈ −0.1 a 0.05; vegetación verde ≈ 0.1 a 0.5.",
        interpretation="Valores más altos indican, de forma relativa, más predominio del verde en la zona.",
        limitations="Sensible a la iluminación, sombras, balance de blancos de la cámara y color del suelo. "
                    "No mide clorofila directamente.",
        reference="Louhaichi, Borman & Johnson (2001), Geocarto International 16(1).",
    ),
    "VARI": IndexDefinition(
        key="VARI",
        name="Visible Atmospherically Resistant Index",
        formula="VARI = (G − R) / (G + R − B)",
        description="Diferencia verde-rojo con una corrección parcial atmosférica usando el azul.",
        typical_range="Habitualmente −1 a +1, pero puede tomar valores extremos cuando G + R − B ≈ 0.",
        interpretation="Valores más altos indican, de forma relativa, más verde frente a rojo. "
                       "Se recomienda revisar la mediana además de la media.",
        limitations="Inestable cuando el denominador es cercano a 0 (píxeles azulados, sombras, agua). "
                    "Por defecto los valores fuera de [−1, 1] se recortan a ese intervalo y se "
                    "contabilizan en el control de calidad.",
        reference="Gitelson et al. (2002), Remote Sensing of Environment 80(1).",
    ),
    "ExG": IndexDefinition(
        key="ExG",
        name="Excess Green",
        formula="ExG = 2G − R − B",
        description="Exceso de verde: diferencia entre el doble del verde y la suma de rojo y azul.",
        typical_range="Con valores 0–1: −2 a +2 (en la práctica ≈ −0.2 a 0.8). "
                      "Con coordenadas cromáticas: −1 a +2.",
        interpretation="Valores más altos indican más verde relativo. Muy utilizado para separar "
                       "vegetación del suelo.",
        limitations="Depende de la escala RGB utilizada (por eso se normaliza) y de la exposición. "
                    "No es una medida de biomasa ni de salud.",
        reference="Woebbecke et al. (1995), Transactions of the ASAE 38(1).",
    ),
    "ExR": IndexDefinition(
        key="ExR",
        name="Excess Red (experimental)",
        formula="ExR = 1.4R − G",
        description="Exceso de rojo; tiende a resaltar suelo y vegetación seca o rojiza.",
        typical_range="Con valores 0–1: −1 a +1.4.",
        interpretation="Valores más altos indican más rojo relativo (no más vegetación).",
        limitations="Experimental. Muy sensible al color del suelo y a la iluminación.",
        reference="Meyer et al. (1998); Meyer & Neto (2008).",
        experimental=True,
    ),
    "ExGR": IndexDefinition(
        key="ExGR",
        name="Excess Green minus Excess Red (experimental)",
        formula="ExGR = ExG − ExR = (2G − R − B) − (1.4R − G)",
        description="Combina ExG y ExR para mejorar el contraste vegetación/suelo.",
        typical_range="Con valores 0–1: aproximadamente −2.4 a +3.",
        interpretation="Valores > 0 suelen asociarse a píxeles verdes.",
        limitations="Experimental. Hereda las limitaciones de ExG y ExR.",
        reference="Meyer & Neto (2008), Computers and Electronics in Agriculture 63(2).",
        experimental=True,
    ),
    "NGRDI": IndexDefinition(
        key="NGRDI",
        name="Normalized Green-Red Difference Index (experimental)",
        formula="NGRDI = (G − R) / (G + R)",
        description="Diferencia normalizada entre verde y rojo.",
        typical_range="−1 a +1.",
        interpretation="Valores > 0 indican más verde que rojo.",
        limitations="Experimental. NO es NDVI ni equivale a él (no usa infrarrojo).",
        reference="Tucker (1979), Remote Sensing of Environment 8(2).",
        experimental=True,
    ),
}


@dataclass
class IndexResult:
    """Mapa de un índice y recuento de problemas numéricos."""

    key: str
    values: np.ndarray  # float32, NaN donde no es calculable
    n_zero_division: int = 0
    n_nonfinite: int = 0
    n_clipped: int = 0  # píxeles recortados a [−1, 1] (sólo VARI)
    clipped: np.ndarray | None = None  # máscara de píxeles recortados (sólo VARI)


# ---------------------------------------------------------------------------
# Operaciones básicas
# ---------------------------------------------------------------------------
def safe_divide(num: np.ndarray, den: np.ndarray, eps: float = DEFAULT_EPS) -> tuple[np.ndarray, int]:
    """División elemento a elemento protegida contra divisiones por cero.

    Donde ``|den| <= eps`` (o donde num/den no son finitos) el resultado es NaN.

    Returns:
        ``(resultado_float32, n_divisiones_problemáticas)`` donde el recuento
        incluye sólo los píxeles con numerador y denominador finitos pero
        denominador ≈ 0.
    """
    num = np.asarray(num, dtype=np.float32)
    den = np.asarray(den, dtype=np.float32)
    out = np.full(np.broadcast(num, den).shape, np.nan, dtype=np.float32)
    finite = np.isfinite(num) & np.isfinite(den)
    ok = finite & (np.abs(den) > eps)
    np.divide(num, den, out=out, where=ok)
    n_zero = int((finite & ~ok).sum())
    return out, n_zero


def split_channels(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Devuelve los canales R, G, B (vistas, sin copiar memoria)."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"Se esperaba una imagen (alto, ancho, 3) y se recibió {rgb.shape}")
    return rgb[..., 0], rgb[..., 1], rgb[..., 2]


def chromatic_coordinates(rgb: np.ndarray, eps: float = DEFAULT_EPS) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Coordenadas cromáticas r = R/(R+G+B), g, b. Píxeles negros (suma 0) → NaN."""
    R, G, B = split_channels(rgb)
    total = R + G + B
    r, n0 = safe_divide(R, total, eps)
    g, _ = safe_divide(G, total, eps)
    b, _ = safe_divide(B, total, eps)
    return r, g, b, n0


# ---------------------------------------------------------------------------
# Índices
# ---------------------------------------------------------------------------
def gli(rgb: np.ndarray, eps: float = DEFAULT_EPS) -> tuple[np.ndarray, int]:
    """GLI = (2G − R − B) / (2G + R + B)."""
    R, G, B = split_channels(rgb)
    return safe_divide(2 * G - R - B, 2 * G + R + B, eps)


def vari(rgb: np.ndarray, eps: float = DEFAULT_EPS) -> tuple[np.ndarray, int]:
    """VARI = (G − R) / (G + R − B)."""
    R, G, B = split_channels(rgb)
    return safe_divide(G - R, G + R - B, eps)


def exg(rgb: np.ndarray, eps: float = DEFAULT_EPS, mode: str = "normalized") -> tuple[np.ndarray, int]:
    """ExG = 2G − R − B (con valores normalizados o coordenadas cromáticas)."""
    if mode == "chromatic":
        r, g, b, n0 = chromatic_coordinates(rgb, eps)
        return (2 * g - r - b).astype(np.float32), n0
    R, G, B = split_channels(rgb)
    return (2 * G - R - B).astype(np.float32), 0


def exr(rgb: np.ndarray, eps: float = DEFAULT_EPS, mode: str = "normalized") -> tuple[np.ndarray, int]:
    """ExR = 1.4R − G (experimental)."""
    if mode == "chromatic":
        r, g, _b, n0 = chromatic_coordinates(rgb, eps)
        return (1.4 * r - g).astype(np.float32), n0
    R, G, _B = split_channels(rgb)
    return (1.4 * R - G).astype(np.float32), 0


def exgr(rgb: np.ndarray, eps: float = DEFAULT_EPS, mode: str = "normalized") -> tuple[np.ndarray, int]:
    """ExGR = ExG − ExR (experimental)."""
    a, n1 = exg(rgb, eps, mode)
    b, _n2 = exr(rgb, eps, mode)
    return (a - b).astype(np.float32), n1


def ngrdi(rgb: np.ndarray, eps: float = DEFAULT_EPS) -> tuple[np.ndarray, int]:
    """NGRDI = (G − R) / (G + R) (experimental; NO es NDVI)."""
    R, G, _B = split_channels(rgb)
    return safe_divide(G - R, G + R, eps)


_FUNCS: dict[str, Callable[..., tuple[np.ndarray, int]]] = {
    "GLI": lambda rgb, eps, mode: gli(rgb, eps),
    "VARI": lambda rgb, eps, mode: vari(rgb, eps),
    "ExG": lambda rgb, eps, mode: exg(rgb, eps, mode),
    "ExR": lambda rgb, eps, mode: exr(rgb, eps, mode),
    "ExGR": lambda rgb, eps, mode: exgr(rgb, eps, mode),
    "NGRDI": lambda rgb, eps, mode: ngrdi(rgb, eps),
}


def available_indices(include_experimental: bool = True) -> list[str]:
    """Lista de claves de índices disponibles."""
    keys = list(MAIN_INDICES)
    if include_experimental:
        keys += list(EXPERIMENTAL_INDICES)
    return keys


def is_experimental(key: str) -> bool:
    return key in EXPERIMENTAL_INDICES


VARI_CLIP_RANGE = (-1.0, 1.0)


def compute_index(key: str, rgb: np.ndarray, valid_mask: np.ndarray | None = None,
                  eps: float = DEFAULT_EPS, exg_mode: str = "normalized",
                  vari_clip: bool = True) -> IndexResult:
    """Calcula un índice RGB sobre toda la imagen.

    Args:
        key: "GLI", "VARI", "ExG", "ExR", "ExGR" o "NGRDI".
        rgb: imagen float32 (H, W, 3) preprocesada.
        valid_mask: píxeles válidos; fuera de ella el resultado es NaN.
        eps: umbral para considerar un denominador como cero.
        exg_mode: "normalized" o "chromatic" (sólo afecta a ExG, ExR y ExGR).
        vari_clip: si es True, los valores de VARI fuera de [−1, 1] se recortan a
            ese intervalo (se cuentan en ``n_clipped``).

    Returns:
        :class:`IndexResult`.
    """
    if key == "NDVI":
        raise ValueError("NDVI requiere banda NIR y NO puede calcularse con imágenes RGB.")
    if key not in _FUNCS:
        raise KeyError(f"Índice desconocido: {key}")
    if exg_mode not in EXG_MODES:
        raise ValueError(f"Modo ExG desconocido: {exg_mode}")
    values, n_zero = _FUNCS[key](rgb, eps, exg_mode)
    values = values.astype(np.float32, copy=False)
    nonfinite = ~np.isfinite(values)
    if valid_mask is not None:
        nonfinite &= valid_mask
        values[~valid_mask] = np.nan
    # Valores no finitos en píxeles válidos que NO se deben a una división por cero.
    n_nonfinite = max(int(nonfinite.sum()) - n_zero, 0)
    values[np.isinf(values)] = np.nan
    clipped = None
    n_clipped = 0
    if key == "VARI" and vari_clip:
        lo, hi = VARI_CLIP_RANGE
        with np.errstate(invalid="ignore"):
            clipped = np.isfinite(values) & ((values < lo) | (values > hi))
        n_clipped = int(clipped.sum())
        np.clip(values, lo, hi, out=values, where=np.isfinite(values))
    return IndexResult(key=key, values=values, n_zero_division=n_zero, n_nonfinite=n_nonfinite,
                       n_clipped=n_clipped, clipped=clipped)


# ---------------------------------------------------------------------------
# Máscara aproximada de vegetación
# ---------------------------------------------------------------------------
VEGETATION_METHODS = {
    "exg": "ExG > umbral",
    "rgb": "G > R y G > B (verde dominante)",
}

VEGETATION_MASK_LABEL = "Máscara RGB aproximada de vegetación"
COVERAGE_LABEL = "Cobertura vegetal estimada mediante RGB"


def vegetation_mask(rgb: np.ndarray, method: str = "exg", threshold: float = 0.1,
                    exg_mode: str = "normalized", valid_mask: np.ndarray | None = None,
                    eps: float = DEFAULT_EPS) -> np.ndarray:
    """Máscara RGB APROXIMADA de vegetación (True = píxel clasificado como vegetación).

    No identifica perfectamente la vegetación: hojas en sombra, vegetación
    seca o suelos verdosos pueden clasificarse mal.

    Args:
        method: "exg" (ExG > umbral) o "rgb" (G > R y G > B).
        threshold: umbral para el método "exg".
    """
    if method == "exg":
        values, _ = exg(rgb, eps, exg_mode)
        with np.errstate(invalid="ignore"):
            mask = values > threshold
    elif method == "rgb":
        R, G, B = split_channels(rgb)
        with np.errstate(invalid="ignore"):
            mask = (G > R) & (G > B)
    else:
        raise ValueError(f"Método de máscara desconocido: {method}")
    mask &= np.isfinite(rgb).all(axis=2)
    if valid_mask is not None:
        mask &= valid_mask
    return mask


def formulas(keys: list[str] | tuple[str, ...]) -> dict[str, str]:
    """Diccionario ``{índice: fórmula}`` para documentar un análisis."""
    return {k: INDEX_DEFINITIONS[k].formula for k in keys if k in INDEX_DEFINITIONS}


def index_documentation_text(keys: list[str] | tuple[str, ...] | None = None) -> str:
    """Texto con fórmula, descripción, rango y limitaciones de cada índice."""
    keys = keys or list(INDEX_DEFINITIONS)
    blocks = []
    for k in keys:
        d = INDEX_DEFINITIONS[k]
        tag = " [EXPERIMENTAL]" if d.experimental else " [PRINCIPAL]"
        blocks.append(
            f"{d.key} — {d.name}{tag}\n"
            f"  Fórmula: {d.formula}\n"
            f"  Descripción: {d.description}\n"
            f"  Rango aproximado: {d.typical_range}\n"
            f"  Interpretación: {d.interpretation}\n"
            f"  Limitaciones: {d.limitations}\n"
            f"  Referencia: {d.reference}"
        )
    return "\n\n".join(blocks)
