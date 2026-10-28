"""Carga de fotografías RGB (JPG, JPEG, PNG).

Principios:
* Los archivos originales se abren SIEMPRE en modo lectura; nunca se
  modifican ni se sobrescriben. Todo el trabajo se hace sobre arrays en memoria.
* Las imágenes se cargan de una en una para no agotar la RAM.
* Las coordenadas de píxel se refieren a la imagen tal como está almacenada
  (no se aplica la rotación EXIF), de forma que el visor y el análisis usan
  exactamente el mismo sistema de coordenadas.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from .utils import human_size

logger = logging.getLogger("tr_rgb.image_loader")

SUPPORTED_EXTENSIONS = (".jpg", ".jpeg", ".png")

# Evita el aviso "DecompressionBombWarning" de Pillow para fotos de dron grandes,
# pero mantiene un límite razonable (≈ 400 MP).
Image.MAX_IMAGE_PIXELS = 400_000_000

_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME_DIGITIZED = 36868
_EXIF_DATETIME = 306
_EXIF_IFD = 0x8769


@dataclass
class ImageInfo:
    """Información descriptiva de una fotografía (sin cargar los píxeles)."""

    path: str
    name: str
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    format: str = ""
    mode: str = ""
    date: str | None = None
    date_source: str = "no disponible"
    valid: bool = True
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1e6

    @property
    def resolution(self) -> str:
        return f"{self.width} × {self.height}"

    @property
    def size_human(self) -> str:
        return human_size(self.size_bytes)

    def estimated_ram_mb(self) -> float:
        """RAM aproximada para analizar la imagen (RGB float32 + 3 índices + máscaras)."""
        return self.width * self.height * 4 * (3 + 3 + 1) / 1e6

    def to_dict(self) -> dict:
        return asdict(self)


def is_supported(path: Path | str) -> bool:
    """True si la extensión es JPG, JPEG o PNG."""
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def find_images(folder: Path | str) -> list[Path]:
    """Lista ordenada de imágenes soportadas en ``folder`` (no recursivo)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and is_supported(p))


def _parse_exif_date(value: str) -> str | None:
    value = str(value).strip().replace("\x00", "")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return _dt.datetime.strptime(value, fmt).isoformat(sep=" ")
        except ValueError:
            continue
    return None


def read_exif_date(img: Image.Image) -> str | None:
    """Fecha de captura según EXIF (DateTimeOriginal, si existe), en ISO."""
    try:
        exif = img.getexif()
    except Exception:  # noqa: BLE001 - EXIF dañado no debe impedir el uso
        return None
    if not exif:
        return None
    candidates = []
    try:
        sub = exif.get_ifd(_EXIF_IFD)
        candidates += [sub.get(_EXIF_DATETIME_ORIGINAL), sub.get(_EXIF_DATETIME_DIGITIZED)]
    except Exception:  # noqa: BLE001
        pass
    candidates.append(exif.get(_EXIF_DATETIME))
    for c in candidates:
        if c:
            parsed = _parse_exif_date(c)
            if parsed:
                return parsed
    return None


def get_image_info(path: Path | str, max_megapixels_warning: float = 40) -> ImageInfo:
    """Lee la información básica de una imagen sin decodificar todos sus píxeles.

    Nunca lanza excepciones por archivos dañados: en ese caso devuelve
    ``valid=False`` y el mensaje en ``error``.
    """
    path = Path(path)
    info = ImageInfo(path=str(path), name=path.name)
    if not path.exists():
        info.valid, info.error = False, "El archivo no existe"
        return info
    if not is_supported(path):
        info.valid, info.error = False, f"Formato no soportado ({path.suffix})"
        return info
    try:
        info.size_bytes = path.stat().st_size
        with Image.open(path) as img:
            info.width, info.height = img.size
            info.format = img.format or path.suffix.upper().lstrip(".")
            info.mode = img.mode
            date = read_exif_date(img)
            if date:
                info.date, info.date_source = date, "EXIF"
        # verify() comprueba la estructura del archivo; requiere reabrirlo.
        with Image.open(path) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        info.valid = False
        info.error = f"Imagen corrupta o ilegible: {exc}"
        return info
    if info.width < 2 or info.height < 2:
        info.valid, info.error = False, "Imagen demasiado pequeña"
    if info.megapixels > max_megapixels_warning:
        info.warnings.append(
            f"Resolución muy grande ({info.megapixels:.1f} MP). El análisis puede necesitar "
            f"≈{info.estimated_ram_mb():.0f} MB de RAM. Puede activar 'Reducir resolución' "
            "en las opciones de análisis."
        )
    return info


def load_rgb_array(path: Path | str, max_side: int | None = None) -> tuple[np.ndarray, np.ndarray | None, float]:
    """Carga una imagen como array RGB ``uint8`` de forma (alto, ancho, 3).

    El archivo se abre en modo lectura y se cierra inmediatamente.

    Args:
        path: ruta de la imagen.
        max_side: si se indica, reduce la imagen (filtro BOX) para que su lado
            mayor no supere este valor. Sólo se aplica si el usuario lo activa.

    Returns:
        ``(rgb, alpha, scale)`` donde ``alpha`` es la máscara de transparencia
        (``True`` = píxel opaco) o ``None`` si la imagen no tiene canal alfa, y
        ``scale`` es el factor aplicado (1.0 = resolución original).

    Raises:
        OSError / ValueError: si la imagen está dañada.
    """
    path = Path(path)
    with Image.open(path) as img:
        img.load()
        alpha = None
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            alpha_img = rgba.getchannel("A")
            rgb_img = rgba.convert("RGB")
        else:
            alpha_img = None
            rgb_img = img.convert("RGB") if img.mode != "RGB" else img.copy()
    scale = 1.0
    if max_side and max(rgb_img.size) > max_side:
        scale = max_side / max(rgb_img.size)
        new_size = (max(1, round(rgb_img.width * scale)), max(1, round(rgb_img.height * scale)))
        rgb_img = rgb_img.resize(new_size, Image.Resampling.BOX)
        if alpha_img is not None:
            alpha_img = alpha_img.resize(new_size, Image.Resampling.NEAREST)
    rgb = np.asarray(rgb_img, dtype=np.uint8)
    if alpha_img is not None:
        alpha = np.asarray(alpha_img) > 0
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"La imagen {path.name} no tiene 3 canales RGB tras la conversión")
    return rgb, alpha, scale


def load_preview(path: Path | str, max_side: int = 2500) -> tuple[Image.Image, tuple[int, int]]:
    """Carga una versión reducida de la imagen para el visor.

    Returns:
        ``(imagen_preview_RGB, (ancho_original, alto_original))``.
    """
    with Image.open(path) as img:
        original_size = img.size
        if img.format == "JPEG":
            img.draft("RGB", (max_side, max_side))  # decodificación rápida reducida
        prev = img.convert("RGB")
        if max(prev.size) > max_side:
            prev.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
        else:
            prev = prev.copy()
    return prev, original_size
