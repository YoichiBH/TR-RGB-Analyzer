"""Preprocesamiento de imágenes RGB.

Todo el procesamiento es OPCIONAL y conservador. No se aplican correcciones
de color, ecualizaciones ni filtros automáticos. Las opciones activadas se
guardan en el proyecto y en ``analysis_metadata.json``.

Pasos posibles:
1. Conversión a RGB (siempre, al cargar la imagen).
2. Comprobación de que el array es válido (dimensiones, tipo, valores finitos).
3. Conversión a ``float32``.
4. Normalización 0–1 (dividir entre 255). Recomendada: hace que ExG/ExR sean
   comparables entre cámaras de 8 bits. GLI, VARI y NGRDI no dependen de ella.
5. Exclusión opcional de píxeles inválidos:
   * no finitos (siempre),
   * transparentes (canal alfa = 0),
   * saturados (algún canal = 255, información recortada),
   * muy oscuros (sombras profundas).
6. Reducción opcional de resolución (se aplica al cargar).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass
class PreprocessingOptions:
    """Opciones de preprocesamiento seleccionadas por el usuario."""

    normalize: bool = True
    exclude_saturated: bool = False
    exclude_dark: bool = False
    dark_threshold: int = 10
    exclude_transparent: bool = True
    downscale_max_side: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "PreprocessingOptions":
        data = data or {}
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        opts = cls(**valid)
        if opts.downscale_max_side in (0, "", "0"):
            opts.downscale_max_side = None
        if opts.downscale_max_side is not None:
            opts.downscale_max_side = int(opts.downscale_max_side)
        opts.dark_threshold = int(opts.dark_threshold)
        return opts

    def describe(self) -> list[str]:
        """Descripción legible de las opciones (para informes y metadatos)."""
        lines = ["Conversión a RGB de 3 canales (8 bits por canal)", "Conversión a float32"]
        lines.append("Normalización 0–1 (valor / 255)" if self.normalize else "Sin normalización (escala 0–255)")
        lines.append("Exclusión de píxeles no finitos (NaN/inf)")
        if self.exclude_transparent:
            lines.append("Exclusión de píxeles transparentes (alfa = 0)")
        if self.exclude_saturated:
            lines.append("Exclusión de píxeles saturados (algún canal = 255)")
        if self.exclude_dark:
            lines.append(f"Exclusión de píxeles muy oscuros (media RGB < {self.dark_threshold}/255)")
        if self.downscale_max_side:
            lines.append(f"Reducción de resolución: lado mayor ≤ {self.downscale_max_side} px (filtro BOX)")
        else:
            lines.append("Resolución original (sin reducción)")
        return lines


@dataclass
class PreprocessResult:
    """Resultado del preprocesamiento de una imagen."""

    rgb: np.ndarray  # float32 (H, W, 3)
    valid_mask: np.ndarray  # bool (H, W)
    n_total: int = 0
    counts: dict = field(default_factory=dict)

    @property
    def n_valid(self) -> int:
        return int(self.valid_mask.sum())

    @property
    def n_invalid(self) -> int:
        return self.n_total - self.n_valid


def check_valid_array(arr: np.ndarray) -> None:
    """Comprueba que ``arr`` es una imagen RGB utilizable.

    Raises:
        ValueError: si el array no es (H, W, 3) o está vacío.
    """
    if not isinstance(arr, np.ndarray):
        raise ValueError("La imagen no es un array de NumPy")
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"Se esperaba una imagen (alto, ancho, 3) y se recibió {arr.shape}")
    if arr.shape[0] < 1 or arr.shape[1] < 1:
        raise ValueError("La imagen está vacía")


def to_float(arr: np.ndarray, normalize: bool = True) -> np.ndarray:
    """Convierte la imagen a ``float32`` y, opcionalmente, la normaliza a 0–1.

    Si el array es ``uint8`` se divide entre 255; si ya es float se asume que
    está en 0–255 cuando su máximo es > 1, o en 0–1 en caso contrario.
    """
    out = arr.astype(np.float32, copy=True)
    if normalize:
        if arr.dtype == np.uint8:
            out /= 255.0
        elif arr.dtype == np.uint16:
            out /= 65535.0
        else:
            finite_max = np.nanmax(out) if out.size else 0
            if finite_max > 1.0:
                out /= 255.0
    return out


def preprocess(arr: np.ndarray, options: PreprocessingOptions | None = None,
               alpha: np.ndarray | None = None) -> PreprocessResult:
    """Aplica el preprocesamiento seleccionado a una imagen RGB.

    La imagen de entrada no se modifica (se trabaja sobre una copia).

    Args:
        arr: imagen RGB (H, W, 3), normalmente ``uint8``.
        options: opciones de preprocesamiento.
        alpha: máscara de opacidad (True = opaco) o ``None``.

    Returns:
        :class:`PreprocessResult` con la imagen float32, la máscara de píxeles
        válidos y el recuento de píxeles excluidos por cada motivo.
    """
    options = options or PreprocessingOptions()
    check_valid_array(arr)
    raw_u8 = arr if arr.dtype == np.uint8 else None
    rgb = to_float(arr, options.normalize)
    h, w = rgb.shape[:2]
    valid = np.isfinite(rgb).all(axis=2)
    counts = {"no_finitos": int((~valid).sum())}

    if options.exclude_transparent and alpha is not None:
        transparent = ~alpha.astype(bool)
        counts["transparentes"] = int((transparent & valid).sum())
        valid &= ~transparent
    if options.exclude_saturated:
        sat = (raw_u8 >= 255).any(axis=2) if raw_u8 is not None else (rgb >= (1.0 if options.normalize else 255.0)).any(axis=2)
        counts["saturados"] = int((sat & valid).sum())
        valid &= ~sat
    if options.exclude_dark:
        if raw_u8 is not None:
            dark = raw_u8.mean(axis=2) < options.dark_threshold
        else:
            factor = 255.0 if options.normalize else 1.0
            dark = rgb.mean(axis=2) * factor < options.dark_threshold
        counts["oscuros"] = int((dark & valid).sum())
        valid &= ~dark

    # Los píxeles no válidos se marcan como NaN para que nunca entren en cálculos.
    rgb[~valid] = np.nan
    return PreprocessResult(rgb=rgb, valid_mask=valid, n_total=h * w, counts=counts)
