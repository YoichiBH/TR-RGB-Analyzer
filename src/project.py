"""Proyectos de TR RGB Analyzer (archivo JSON).

Un proyecto conserva: fotografías utilizadas (rutas, NUNCA copias modificadas),
parcelas/ROIs, configuración del análisis, índices seleccionados, datos de
campo (copiados dentro del proyecto para que sea reproducible), calibración,
observaciones visuales, resultados por ROI y fecha del último análisis.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from .field_data import (SensorCalibration, VisualObservation, observations_to_df,
                         records_to_measurements)
from .image_loader import get_image_info
from .indices import EXPERIMENTAL_INDICES, MAIN_INDICES
from .preprocessing import PreprocessingOptions
from .roi import ROIManager
from .utils import (APP_NAME, APP_VERSION, DEFAULT_CONFIG, PROJECT_ROOT, now_iso, read_json,
                    resolve_stored_path, slugify, to_relative, write_json)

logger = logging.getLogger("tr_rgb.project")

PROJECT_FORMAT_VERSION = 1


@dataclass
class PhotoEntry:
    """Fotografía incluida en el proyecto (sólo metadatos y ruta)."""

    path: str
    name: str
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    format: str = ""
    date: str | None = None
    date_source: str = "no disponible"
    valid: bool = True
    error: str | None = None
    relative_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PhotoEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AnalysisSettings:
    """Configuración del análisis elegida por el usuario."""

    main_indices: list[str] = field(default_factory=lambda: list(MAIN_INDICES))
    experimental_indices: list[str] = field(default_factory=list)
    exg_mode: str = "chromatic"  # coordenadas cromáticas (Woebbecke et al., 1995)
    vegetation_method: str = "exg"
    vegetation_threshold: float = 0.1
    # La máscara de vegetación usa siempre ExG sobre RGB 0–1, independientemente del
    # modo del índice ExG, para que la cobertura vegetal no cambie al cambiar exg_mode.
    vegetation_exg_mode: str = "normalized"
    vari_clip: bool = True  # recorta VARI a [−1, 1]
    only_vegetation: bool = False
    map_scale_mode: str = "common"  # "common" | "auto"
    aggregate_stat: str = "mean"  # "mean" | "median"
    preprocessing: dict = field(default_factory=lambda: PreprocessingOptions().to_dict())

    def __post_init__(self) -> None:
        self.main_indices = [k for k in MAIN_INDICES if k in self.main_indices]
        self.experimental_indices = [k for k in EXPERIMENTAL_INDICES if k in self.experimental_indices]
        self.vegetation_threshold = float(self.vegetation_threshold)
        self.preprocessing = PreprocessingOptions.from_dict(self.preprocessing).to_dict()

    @property
    def all_indices(self) -> list[str]:
        return list(self.main_indices) + list(self.experimental_indices)

    def preprocessing_options(self) -> PreprocessingOptions:
        return PreprocessingOptions.from_dict(self.preprocessing)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "AnalysisSettings":
        data = data or {}
        return cls(**{k: copy.deepcopy(v) for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_config(cls, config: dict | None) -> "AnalysisSettings":
        cfg = (config or DEFAULT_CONFIG).get("default_settings", {})
        return cls.from_dict(cfg)


class Project:
    """Proyecto completo de análisis."""

    def __init__(self, name: str = "Proyecto sin título", is_demo: bool = False,
                 settings: AnalysisSettings | None = None) -> None:
        self.name = name
        self.is_demo = is_demo
        self.path: Path | None = None
        self.created = now_iso()
        self.modified = self.created
        self.photos: list[PhotoEntry] = []
        self.rois = ROIManager()
        self.settings = settings or AnalysisSettings()
        self.field_measurements: list[dict] = []
        self.field_source: str = ""
        self.field_messages: list[str] = []
        self.calibration = SensorCalibration()
        self.observations: list[VisualObservation] = []
        self.output_dir: str | None = None
        self.results: dict = {}
        self.dirty = False

    # -- fotografías -------------------------------------------------------------
    def photo(self, name: str) -> PhotoEntry | None:
        return next((p for p in self.photos if p.name == name), None)

    def photo_names(self) -> list[str]:
        return [p.name for p in self.photos]

    def add_photos(self, paths: list[Path | str], max_megapixels_warning: float = 40) -> tuple[list[PhotoEntry], list[str]]:
        """Añade fotografías (sólo se guarda la ruta; el archivo no se toca).

        Returns:
            ``(fotos_añadidas, mensajes)``. Las fotos corruptas se añaden
            marcadas como no válidas para que aparezcan en el control de calidad.
        """
        added, messages = [], []
        for p in paths:
            p = Path(p)
            info = get_image_info(p, max_megapixels_warning)
            existing = self.photo(info.name)
            if existing is not None:
                if Path(existing.path).resolve() == p.resolve():
                    messages.append(f"{info.name}: ya estaba en el proyecto")
                else:
                    messages.append(f"{info.name}: ya existe otra foto con el mismo nombre; renómbrela para importarla")
                continue
            entry = PhotoEntry(path=str(p.resolve()), name=info.name, width=info.width, height=info.height,
                               size_bytes=info.size_bytes, format=info.format, date=info.date,
                               date_source=info.date_source, valid=info.valid, error=info.error)
            self.photos.append(entry)
            added.append(entry)
            if not info.valid:
                messages.append(f"{info.name}: {info.error}")
            messages += [f"{info.name}: {w}" for w in info.warnings]
        if added:
            self.touch()
        return added, messages

    def remove_photo(self, name: str, remove_rois: bool = True) -> int:
        """Quita una foto del PROYECTO (el archivo no se borra nunca)."""
        self.photos = [p for p in self.photos if p.name != name]
        n = self.rois.remove_photo(name) if remove_rois else 0
        self.touch()
        return n

    def set_photo_date(self, name: str, date: str | None) -> None:
        p = self.photo(name)
        if p is None:
            raise KeyError(name)
        p.date = date or None
        p.date_source = "manual" if date else "no disponible"
        self.touch()

    def photo_dicts(self) -> list[dict]:
        return [p.to_dict() for p in self.photos]

    # -- datos de campo ------------------------------------------------------------
    def field_df(self) -> pd.DataFrame:
        return records_to_measurements(self.field_measurements)

    def observations_df(self) -> pd.DataFrame:
        return observations_to_df(self.observations)

    def set_observation(self, obs: VisualObservation) -> None:
        """Añade o reemplaza la observación de una parcela."""
        self.observations = [o for o in self.observations if o.parcela != obs.parcela]
        self.observations.append(obs)
        self.observations.sort(key=lambda o: o.parcela)
        self.touch()

    def delete_observation(self, parcela: str) -> None:
        self.observations = [o for o in self.observations if o.parcela != parcela]
        self.touch()

    def all_parcels(self) -> list[str]:
        ids = set(self.rois.parcel_ids())
        ids |= {str(m["parcela"]) for m in self.field_measurements}
        ids |= {o.parcela for o in self.observations}
        return sorted(ids)

    # -- resultados ------------------------------------------------------------------
    def roi_results_df(self) -> pd.DataFrame:
        records = self.results.get("roi_results") or []
        return pd.DataFrame(records)

    def set_roi_results(self, df: pd.DataFrame, image_summary: pd.DataFrame | None = None,
                        output_dir: str | None = None) -> None:
        self.results["roi_results"] = df.to_dict(orient="records") if df is not None else []
        if image_summary is not None:
            self.results["image_summary"] = image_summary.to_dict(orient="records")
        self.results["last_analysis"] = now_iso()
        self.results["settings_used"] = self.settings.to_dict()
        if output_dir:
            self.results["output_dir"] = str(output_dir)
        self.touch()

    def image_summary_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.results.get("image_summary") or [])

    # -- salida ------------------------------------------------------------------------
    def default_output_dir(self) -> Path:
        if self.output_dir:
            return Path(self.output_dir)
        if self.is_demo:
            return PROJECT_ROOT / "demo" / "output"
        return PROJECT_ROOT / "output" / slugify(self.name)

    def touch(self) -> None:
        self.modified = now_iso()
        self.dirty = True

    # -- serialización -------------------------------------------------------------
    def to_dict(self, base_dir: Path | None = None) -> dict:
        photos = []
        for p in self.photos:
            d = p.to_dict()
            if base_dir is not None:
                d["relative_path"] = to_relative(p.path, base_dir)
            photos.append(d)
        return {
            "format_version": PROJECT_FORMAT_VERSION,
            "app": APP_NAME,
            "app_version": APP_VERSION,
            "name": self.name,
            "is_demo": self.is_demo,
            "demo_notice": "DEMO — NO SON DATOS EXPERIMENTALES" if self.is_demo else None,
            "created": self.created,
            "modified": self.modified,
            "photos": photos,
            "rois": self.rois.to_list(),
            "settings": self.settings.to_dict(),
            "field_data": {"source": self.field_source, "measurements": self.field_measurements,
                           "messages": self.field_messages},
            "calibration": self.calibration.to_dict(),
            "observations": [o.to_dict() for o in self.observations],
            "output_dir": self.output_dir,
            "results": self.results,
        }

    @classmethod
    def from_dict(cls, data: dict, base_dir: Path | None = None) -> "Project":
        proj = cls(name=data.get("name", "Proyecto"), is_demo=bool(data.get("is_demo", False)),
                   settings=AnalysisSettings.from_dict(data.get("settings")))
        proj.created = data.get("created", proj.created)
        proj.modified = data.get("modified", proj.modified)
        for pd_ in data.get("photos", []):
            entry = PhotoEntry.from_dict(pd_)
            resolved = resolve_stored_path(entry.path, entry.relative_path, base_dir)
            if resolved is not None:
                entry.path = str(resolved)
            proj.photos.append(entry)
        proj.rois = ROIManager.from_list(data.get("rois", []))
        fd = data.get("field_data", {}) or {}
        proj.field_measurements = list(fd.get("measurements", []))
        proj.field_source = fd.get("source", "")
        proj.field_messages = list(fd.get("messages", []))
        proj.calibration = SensorCalibration.from_dict(data.get("calibration"))
        proj.observations = [VisualObservation.from_dict(o) for o in data.get("observations", [])]
        out = data.get("output_dir")
        if out and base_dir is not None and not Path(out).is_absolute():
            out = str((base_dir / out).resolve())
        proj.output_dir = out
        proj.results = data.get("results", {}) or {}
        proj.dirty = False
        return proj

    def save(self, path: Path | str | None = None) -> Path:
        """Guarda el proyecto en JSON (por defecto en su ruta actual)."""
        path = Path(path) if path else self.path
        if path is None:
            raise ValueError("El proyecto todavía no tiene ruta; use 'Guardar como'.")
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        self.modified = now_iso()
        write_json(path, self.to_dict(base_dir=path.parent))
        self.path = path
        self.dirty = False
        return path

    @classmethod
    def load(cls, path: Path | str) -> "Project":
        path = Path(path)
        data = read_json(path)
        if not isinstance(data, dict) or "photos" not in data:
            raise ValueError(f"{path.name} no es un proyecto de {APP_NAME}")
        proj = cls.from_dict(data, base_dir=path.parent)
        proj.path = path
        return proj

    def clone(self) -> "Project":
        """Copia independiente (para analizar en segundo plano sin bloquear la GUI)."""
        clone = Project.from_dict(copy.deepcopy(self.to_dict()))
        clone.path = self.path
        return clone
