"""Parcelas / regiones de interés (ROI) rectangulares.

Convención de coordenadas
-------------------------
* Coordenadas en píxeles de la imagen ORIGINAL, con origen (0, 0) en la
  esquina superior izquierda; X hacia la derecha, Y hacia abajo.
* ``x1, y1`` = esquina superior izquierda (incluida).
* ``x2, y2`` = esquina inferior derecha (EXCLUIDA), como en el recorte de
  NumPy ``imagen[y1:y2, x1:x2]``. Por tanto ancho = x2 − x1.

Cada ROI pertenece a una parcela (``parcel_id``, p. ej. "A") y a una foto.
Una misma parcela puede tener ROIs en varias fotografías (evolución temporal).
"""

from __future__ import annotations

import csv
import logging
import string
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .utils import read_json, write_json

logger = logging.getLogger("tr_rgb.roi")

#: Correspondencia entre atributos y columnas CSV (en español).
CSV_COLUMNS = {
    "parcel_id": "id_parcela",
    "name": "nombre",
    "photo": "foto",
    "x1": "x1",
    "y1": "y1",
    "x2": "x2",
    "y2": "y2",
    "description": "descripcion",
    "uid": "uid",
}
_CSV_ALIASES = {
    "parcel_id": ["id_parcela", "parcela", "id", "parcel_id", "parcel"],
    "name": ["nombre", "name"],
    "photo": ["foto", "photo", "imagen", "image", "archivo"],
    "x1": ["x1"], "y1": ["y1"], "x2": ["x2"], "y2": ["y2"],
    "description": ["descripcion", "descripción", "description", "desc"],
    "uid": ["uid"],
}


def _new_uid() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class ROI:
    """Región rectangular de una fotografía asociada a una parcela."""

    parcel_id: str
    name: str
    photo: str
    x1: int
    y1: int
    x2: int
    y2: int
    description: str = ""
    uid: str = field(default_factory=_new_uid)

    def __post_init__(self) -> None:
        self.parcel_id = str(self.parcel_id).strip()
        self.name = str(self.name or "").strip()
        self.photo = str(self.photo).strip()
        self.description = str(self.description or "").strip()
        self.x1, self.y1, self.x2, self.y2 = (int(round(float(v))) for v in (self.x1, self.y1, self.x2, self.y2))
        # Coordenadas siempre ordenadas (x1 < x2, y1 < y2).
        if self.x1 > self.x2:
            self.x1, self.x2 = self.x2, self.x1
        if self.y1 > self.y2:
            self.y1, self.y2 = self.y2, self.y1
        if not self.uid:
            self.uid = _new_uid()

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def area(self) -> int:
        return max(self.width, 0) * max(self.height, 0)

    @property
    def coords(self) -> tuple[int, int, int, int]:
        return self.x1, self.y1, self.x2, self.y2

    def label(self) -> str:
        return f"{self.parcel_id} — {self.name}" if self.name else self.parcel_id

    def copy(self, **changes) -> "ROI":
        data = asdict(self)
        data.update(changes)
        if "uid" not in changes:
            data["uid"] = _new_uid()
        return ROI(**data)

    def scaled(self, factor: float) -> "ROI":
        """ROI con coordenadas multiplicadas por ``factor`` (mismo uid)."""
        if factor == 1:
            return self
        return ROI(self.parcel_id, self.name, self.photo,
                   int(np.floor(self.x1 * factor)), int(np.floor(self.y1 * factor)),
                   int(np.ceil(self.x2 * factor)), int(np.ceil(self.y2 * factor)),
                   self.description, self.uid)

    def clipped(self, width: int, height: int) -> "ROI":
        """ROI recortada a los límites de una imagen de ``width × height``."""
        return ROI(self.parcel_id, self.name, self.photo,
                   min(max(self.x1, 0), width), min(max(self.y1, 0), height),
                   min(max(self.x2, 0), width), min(max(self.y2, 0), height),
                   self.description, self.uid)

    def is_inside(self, width: int, height: int) -> bool:
        return self.x1 >= 0 and self.y1 >= 0 and self.x2 <= width and self.y2 <= height

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x < self.x2 and self.y1 <= y < self.y2

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ROI":
        fields = {k: data.get(k) for k in cls.__dataclass_fields__ if k in data}
        missing = [k for k in ("parcel_id", "photo", "x1", "y1", "x2", "y2") if fields.get(k) in (None, "")]
        if missing:
            raise ValueError(f"Faltan campos en la ROI: {', '.join(missing)}")
        fields.setdefault("name", "")
        return cls(**fields)


def validate_roi(roi: ROI, img_width: int | None = None, img_height: int | None = None,
                 min_side: int = 5, min_area: int = 100) -> list[tuple[str, str]]:
    """Comprueba una ROI y devuelve una lista de ``(nivel, mensaje)``.

    Niveles: ``"error"`` (la ROI no se puede analizar) o ``"warning"``.
    """
    issues: list[tuple[str, str]] = []
    tag = f"ROI {roi.parcel_id} ({roi.photo})"
    if not roi.parcel_id:
        issues.append(("error", f"{tag}: falta el ID de parcela"))
    if roi.width <= 0 or roi.height <= 0:
        issues.append(("error", f"{tag}: ROI vacía (ancho o alto = 0)"))
        return issues
    if img_width is not None and img_height is not None:
        clipped = roi.clipped(img_width, img_height)
        if clipped.area == 0:
            issues.append(("error", f"{tag}: completamente fuera de los límites de la imagen "
                                    f"({img_width}×{img_height})"))
            return issues
        if not roi.is_inside(img_width, img_height):
            issues.append(("warning", f"{tag}: parcialmente fuera de los límites; se analizará sólo "
                                      f"la parte dentro de la imagen ({clipped.coords})"))
        roi = clipped
    if roi.width < min_side or roi.height < min_side:
        issues.append(("warning", f"{tag}: ROI demasiado estrecha ({roi.width}×{roi.height} px; "
                                  f"mínimo recomendado {min_side} px por lado)"))
    if roi.area < min_area:
        issues.append(("warning", f"{tag}: ROI demasiado pequeña ({roi.area} píxeles; "
                                  f"mínimo recomendado {min_area})"))
    return issues


def extract_roi(arr: np.ndarray, roi: ROI) -> np.ndarray:
    """Recorte (vista, sin copia) de ``arr`` correspondiente a la ROI.

    La ROI se recorta a los límites del array.

    Raises:
        ValueError: si la ROI queda vacía tras recortarla.
    """
    h, w = arr.shape[:2]
    r = roi.clipped(w, h)
    if r.area == 0:
        raise ValueError(f"La ROI {roi.parcel_id} está vacía o fuera de la imagen")
    return arr[r.y1:r.y2, r.x1:r.x2]


class ROIManager:
    """Colección de ROIs con operaciones de creación, edición y persistencia."""

    def __init__(self, rois: list[ROI] | None = None) -> None:
        self.rois: list[ROI] = []
        for r in rois or []:
            self.add(r)

    # -- consultas -----------------------------------------------------------
    def __len__(self) -> int:
        return len(self.rois)

    def __iter__(self):
        return iter(self.rois)

    def get(self, uid: str) -> ROI | None:
        return next((r for r in self.rois if r.uid == uid), None)

    def for_photo(self, photo: str) -> list[ROI]:
        return [r for r in self.rois if r.photo == photo]

    def for_parcel(self, parcel_id: str) -> list[ROI]:
        return [r for r in self.rois if r.parcel_id == parcel_id]

    def parcel_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.rois:
            seen.setdefault(r.parcel_id, None)
        return sorted(seen, key=_natural_key)

    def photos(self) -> list[str]:
        return sorted({r.photo for r in self.rois})

    def find(self, parcel_id: str, photo: str) -> ROI | None:
        return next((r for r in self.rois if r.parcel_id == parcel_id and r.photo == photo), None)

    def next_parcel_id(self, photo: str | None = None) -> str:
        """Sugiere el siguiente ID libre (A, B, C, …, Z, P27, …)."""
        used = {r.parcel_id for r in (self.for_photo(photo) if photo else self.rois)}
        for letter in string.ascii_uppercase:
            if letter not in used:
                return letter
        i = 27
        while f"P{i}" in used:
            i += 1
        return f"P{i}"

    # -- modificación --------------------------------------------------------
    def add(self, roi: ROI) -> ROI:
        """Añade una ROI. No se permite repetir la misma parcela en la misma foto."""
        if not roi.parcel_id:
            raise ValueError("El ID de parcela no puede estar vacío")
        if self.find(roi.parcel_id, roi.photo):
            raise ValueError(f"Ya existe una ROI de la parcela '{roi.parcel_id}' en '{roi.photo}'")
        while self.get(roi.uid):
            roi.uid = _new_uid()
        self.rois.append(roi)
        return roi

    def update(self, uid: str, **changes) -> ROI:
        """Modifica campos de una ROI existente (coordenadas, nombre, …)."""
        roi = self.get(uid)
        if roi is None:
            raise KeyError(f"ROI no encontrada: {uid}")
        data = roi.to_dict()
        data.update({k: v for k, v in changes.items() if k in data and k != "uid"})
        new = ROI(**data)
        other = self.find(new.parcel_id, new.photo)
        if other is not None and other.uid != uid:
            raise ValueError(f"Ya existe una ROI de la parcela '{new.parcel_id}' en '{new.photo}'")
        idx = self.rois.index(roi)
        self.rois[idx] = new
        return new

    def rename(self, uid: str, new_name: str | None = None, new_parcel_id: str | None = None) -> ROI:
        changes = {}
        if new_name is not None:
            changes["name"] = new_name
        if new_parcel_id is not None:
            changes["parcel_id"] = new_parcel_id
        return self.update(uid, **changes)

    def delete(self, uid: str) -> ROI:
        roi = self.get(uid)
        if roi is None:
            raise KeyError(f"ROI no encontrada: {uid}")
        self.rois.remove(roi)
        return roi

    def duplicate(self, uid: str, new_parcel_id: str | None = None, photo: str | None = None,
                  offset: tuple[int, int] = (20, 20)) -> ROI:
        """Duplica una ROI.

        * Si ``photo`` es otra foto, copia la parcela con las mismas coordenadas
          (útil para seguir la misma parcela en varias fechas).
        * Si es la misma foto, crea una parcela nueva desplazada ``offset``.
        """
        roi = self.get(uid)
        if roi is None:
            raise KeyError(f"ROI no encontrada: {uid}")
        target_photo = photo or roi.photo
        if target_photo != roi.photo:
            pid = new_parcel_id or roi.parcel_id
            new = roi.copy(parcel_id=pid, photo=target_photo)
        else:
            pid = new_parcel_id or self.next_parcel_id(target_photo)
            dx, dy = offset
            new = roi.copy(parcel_id=pid, name=f"{roi.name} (copia)".strip(),
                           x1=roi.x1 + dx, y1=roi.y1 + dy, x2=roi.x2 + dx, y2=roi.y2 + dy)
        return self.add(new)

    def remove_photo(self, photo: str) -> int:
        before = len(self.rois)
        self.rois = [r for r in self.rois if r.photo != photo]
        return before - len(self.rois)

    # -- persistencia --------------------------------------------------------
    def to_list(self) -> list[dict]:
        return [r.to_dict() for r in self.rois]

    @classmethod
    def from_list(cls, items: list[dict]) -> "ROIManager":
        mgr = cls()
        for item in items or []:
            mgr.add(ROI.from_dict(item))
        return mgr

    def save_json(self, path: Path | str) -> Path:
        return write_json(path, {"rois": self.to_list(),
                                 "convencion": "x2/y2 excluidos; píxeles de la imagen original"})

    @classmethod
    def load_json(cls, path: Path | str) -> "ROIManager":
        data = read_json(path)
        items = data.get("rois", []) if isinstance(data, dict) else data
        return cls.from_list(items)

    def save_csv(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS.values()))
            writer.writeheader()
            for r in self.rois:
                d = r.to_dict()
                writer.writerow({CSV_COLUMNS[k]: d[k] for k in CSV_COLUMNS})
        return path

    @classmethod
    def load_csv(cls, path: Path | str) -> tuple["ROIManager", list[str]]:
        """Lee ROIs de un CSV (separador ``,`` o ``;``).

        Returns:
            ``(manager, lista_de_errores)``. Las filas erróneas se omiten y se
            informan; nunca se inventan coordenadas.
        """
        path = Path(path)
        errors: list[str] = []
        mgr = cls()
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if not text.strip():
            return mgr, [f"{path.name}: el archivo está vacío"]
        first = text.splitlines()[0]
        delim = ";" if first.count(";") > first.count(",") else ","
        reader = csv.DictReader(text.splitlines(), delimiter=delim)
        headers = {h.strip().lower(): h for h in (reader.fieldnames or [])}
        colmap = {}
        for attr, aliases in _CSV_ALIASES.items():
            for a in aliases:
                if a in headers:
                    colmap[attr] = headers[a]
                    break
        missing = [a for a in ("parcel_id", "photo", "x1", "y1", "x2", "y2") if a not in colmap]
        if missing:
            return mgr, [f"{path.name}: faltan columnas obligatorias: {', '.join(missing)}"]
        for i, row in enumerate(reader, start=2):
            try:
                data = {attr: (row.get(col) or "").strip() for attr, col in colmap.items()}
                for c in ("x1", "y1", "x2", "y2"):
                    data[c] = float(data[c].replace(",", "."))
                mgr.add(ROI.from_dict(data))
            except (ValueError, TypeError) as exc:
                errors.append(f"{path.name}, fila {i}: {exc}")
        return mgr, errors


def _natural_key(text: str):
    import re

    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(text))]
