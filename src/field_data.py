"""Datos de campo: humedad del suelo (Arduino), calibración y observaciones visuales.

Principios:
* Las lecturas RAW del sensor capacitivo se conservan SIEMPRE.
* NO se convierte RAW a porcentaje salvo que el usuario introduzca una
  calibración (RAW suelo seco / RAW suelo húmedo) y la active.
* Las filas no válidas del CSV se informan (no se descartan en silencio) y
  nunca se inventan valores.

Formato recomendado del CSV del Arduino::

    fecha,hora,parcela,lectura_adc,porcentaje_humedad
    2026-10-12,15:35:34,1,480,8
    2026-10-12,15:35:43,2,514,0
    2026-10-12,15:35:48,3,490,5

* ``lectura_adc`` es la lectura RAW del sensor (``analogRead``): es el dato principal.
* ``fecha`` y ``hora`` se combinan en una sola marca temporal.
* ``porcentaje_humedad`` (opcional) es el porcentaje que calcula el propio
  Arduino (normalmente con ``map()``/``constrain()``). Se conserva aparte como
  ``humedad_pct_arduino``, NUNCA sustituye a la lectura RAW y no se usa en las
  correlaciones principales: depende de la calibración programada en el Arduino.
* Si no hay columna ``medicion``, las lecturas de cada parcela se numeran por
  orden de aparición (1, 2, 3…).

También se acepta el formato antiguo ``parcela,medicion,humedad_raw``.
Se aceptan separadores ``,`` ``;`` o tabulador y decimales con coma.
"""

from __future__ import annotations

import csv
import io
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .statistics import describe

logger = logging.getLogger("tr_rgb.field_data")

COLUMN_ALIASES = {
    "parcela": ["parcela", "parcel", "plot", "id_parcela", "zona", "id"],
    "medicion": ["medicion", "medición", "n", "num", "numero", "número", "measurement", "repeticion"],
    "humedad_raw": ["lectura_adc", "humedad_raw", "adc", "lectura_raw", "raw", "valor_adc", "analog",
                    "moisture_raw", "humedad", "valor", "moisture", "sensor", "lectura"],
    "fecha": ["fecha", "date", "timestamp", "fecha_hora", "datetime"],
    "hora": ["hora", "time"],
    "humedad_pct_arduino": ["porcentaje_humedad", "humedad_porcentaje", "humedad_pct", "porcentaje", "humedad_%",
                            "moisture_percent", "pct"],
}

#: Columnas de las mediciones guardadas en el proyecto.
MEASUREMENT_COLUMNS = ["parcela", "medicion", "humedad_raw", "fecha", "humedad_pct_arduino"]

ARDUINO_PCT_NOTICE = (
    "porcentaje_humedad lo calcula el propio Arduino con su calibración interna; el programa lo conserva "
    "como dato aparte, pero el análisis principal usa siempre la lectura RAW (lectura_adc)."
)

#: Rango de valores de un convertidor analógico-digital habitual (Arduino 10 bits = 0–1023,
#: ESP32 12 bits = 0–4095). Valores fuera se avisan pero NO se eliminan.
RAW_PLAUSIBLE_RANGE = (0, 4095)

CALIBRATION_NOTICE = (
    "Esta estimación depende de la calibración realizada y no representa una "
    "humedad volumétrica universal."
)

VISUAL_SCALE = {1: "muy pobre", 2: "pobre", 3: "intermedio", 4: "bueno", 5: "muy bueno"}
OBSERVATION_FIELDS = ["parcela", "estado_general", "coloracion", "densidad", "estres_visible",
                      "uniformidad", "observaciones", "fecha"]


# ---------------------------------------------------------------------------
# Lectura del CSV del Arduino
# ---------------------------------------------------------------------------
@dataclass
class FieldDataLoadResult:
    """Resultado de leer un CSV de campo."""

    df: pd.DataFrame
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors and not self.df.empty


def empty_measurements() -> pd.DataFrame:
    return pd.DataFrame({"parcela": pd.Series(dtype=object), "medicion": pd.Series(dtype=float),
                         "humedad_raw": pd.Series(dtype=float), "fecha": pd.Series(dtype=object),
                         "humedad_pct_arduino": pd.Series(dtype=float)})


def _to_number(series: pd.Series) -> pd.Series:
    s = series.fillna("").astype(str).str.strip().str.replace(",", ".", regex=False)
    s = s.replace({"": None, "nan": None, "None": None})
    return pd.to_numeric(s, errors="coerce")


def _detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        first = text.splitlines()[0] if text.splitlines() else ""
        return max([",", ";", "\t"], key=first.count)


def parse_field_text(text: str, source: str = "") -> FieldDataLoadResult:
    """Interpreta el contenido de un CSV de humedad. Nunca lanza excepciones."""
    result = FieldDataLoadResult(df=empty_measurements(), source=source)
    if not text or not text.strip():
        result.errors.append("El archivo CSV está vacío.")
        return result
    delim = _detect_delimiter(text)
    try:
        raw = pd.read_csv(io.StringIO(text), sep=delim, dtype=str, skip_blank_lines=True,
                          comment="#", skipinitialspace=True)
    except Exception as exc:  # noqa: BLE001 - cualquier CSV mal formado
        result.errors.append(f"No se pudo leer el CSV: {exc}")
        return result
    if raw.empty:
        result.errors.append("El CSV no contiene filas de datos (sólo cabecera o vacío).")
        return result
    headers = {str(c).strip().lower(): c for c in raw.columns}
    colmap: dict[str, str] = {}
    for target, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in headers and headers[a] not in colmap.values():
                colmap[target] = headers[a]
                break
    missing = [c for c in ("parcela", "humedad_raw") if c not in colmap]
    if missing:
        names = {"parcela": "parcela", "humedad_raw": "lectura_adc (o humedad_raw)"}
        result.errors.append(
            "Faltan columnas obligatorias: " + ", ".join(names[m] for m in missing)
            + f". Columnas encontradas: {', '.join(map(str, raw.columns))}. "
            "Formato esperado: fecha,hora,parcela,lectura_adc,porcentaje_humedad"
        )
        return result

    def text_col(key: str) -> pd.Series:
        if key not in colmap:
            return pd.Series([""] * len(raw), index=raw.index, dtype=object)
        return raw[colmap[key]].fillna("").astype(str).str.strip()

    df = pd.DataFrame()
    df["parcela"] = text_col("parcela")
    df["humedad_raw"] = _to_number(raw[colmap["humedad_raw"]])
    df["medicion"] = _to_number(raw[colmap["medicion"]]) if "medicion" in colmap else np.nan
    # fecha + hora → una sola marca temporal ("2026-10-12 15:35:34").
    df["fecha"] = (text_col("fecha") + " " + text_col("hora")).str.strip()
    if "humedad_pct_arduino" in colmap:
        df["humedad_pct_arduino"] = _to_number(raw[colmap["humedad_pct_arduino"]])
    else:
        df["humedad_pct_arduino"] = np.nan
    df["_fila_csv"] = np.arange(2, len(df) + 2)

    bad_parcel = df["parcela"].isin(["", "nan", "None"])
    bad_value = df["humedad_raw"].isna()
    bad = bad_parcel | bad_value
    for _, row in df[bad].iterrows():
        reason = "parcela vacía" if row["parcela"] in ("", "nan", "None") else "humedad_raw no numérica o vacía"
        result.warnings.append(f"Fila {int(row['_fila_csv'])} omitida: {reason}.")
    df = df[~bad].copy()
    if df.empty:
        result.errors.append("Ninguna fila del CSV contiene datos válidos.")
        result.df = empty_measurements()
        return result

    if df["medicion"].isna().any():
        if "medicion" in colmap:
            result.warnings.append("Algunas filas no tienen número de medición; se numeran por orden de aparición.")
        df["medicion"] = df["medicion"].where(df["medicion"].notna(), df.groupby("parcela").cumcount() + 1)

    if "humedad_pct_arduino" in colmap:
        pct = df["humedad_pct_arduino"]
        if pct.notna().any():
            result.warnings.append(ARDUINO_PCT_NOTICE)
        outside = df[(pct < 0) | (pct > 100)]
        if not outside.empty:
            result.warnings.append(f"{len(outside)} valores de porcentaje_humedad fuera de 0–100 "
                                   f"(filas {', '.join(map(str, outside['_fila_csv'].astype(int).tolist()[:10]))}).")
        at_limit = int(((pct == 0) | (pct == 100)).sum())
        if at_limit:
            result.warnings.append(
                f"{at_limit} lecturas con porcentaje_humedad exactamente 0 % o 100 %: probablemente el Arduino "
                "recorta (constrain) valores fuera de su calibración. La lectura RAW sí conserva la diferencia.")

    lo, hi = RAW_PLAUSIBLE_RANGE
    out_of_range = df[(df["humedad_raw"] < lo) | (df["humedad_raw"] > hi)]
    if not out_of_range.empty:
        result.warnings.append(
            f"{len(out_of_range)} lecturas fuera del rango habitual {lo}–{hi} "
            f"(filas {', '.join(map(str, out_of_range['_fila_csv'].astype(int).tolist()[:10]))}). "
            "Se conservan; revise si son errores del sensor."
        )
    dups = df.duplicated(subset=["parcela", "medicion"], keep=False)
    if dups.any():
        result.warnings.append(f"{int(dups.sum())} filas tienen la misma parcela y número de medición.")
    result.df = df.drop(columns="_fila_csv").reset_index(drop=True)
    return result


def read_field_csv(path: Path | str) -> FieldDataLoadResult:
    """Lee un CSV de humedad del Arduino de forma robusta (nunca lanza excepciones)."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return FieldDataLoadResult(df=empty_measurements(), errors=[f"No se pudo abrir {path.name}: {exc}"],
                                   source=str(path))
    text = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return parse_field_text(text or "", source=str(path))


def measurements_to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame de mediciones → lista de diccionarios (para guardar en el proyecto)."""
    if df is None or df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        pct = r.get("humedad_pct_arduino", np.nan)
        out.append({
            "parcela": str(r["parcela"]),
            "medicion": None if pd.isna(r.get("medicion")) else float(r["medicion"]),
            "humedad_raw": float(r["humedad_raw"]),
            "fecha": "" if pd.isna(r.get("fecha", "")) else str(r.get("fecha", "")),
            "humedad_pct_arduino": None if pd.isna(pct) else float(pct),
        })
    return out


def records_to_measurements(records: list[dict] | None) -> pd.DataFrame:
    if not records:
        return empty_measurements()
    df = pd.DataFrame(records)
    for c in MEASUREMENT_COLUMNS:
        if c not in df:
            df[c] = "" if c == "fecha" else np.nan
    df["parcela"] = df["parcela"].astype(str)
    for c in ("humedad_raw", "medicion", "humedad_pct_arduino"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[MEASUREMENT_COLUMNS]


# ---------------------------------------------------------------------------
# Calibración opcional
# ---------------------------------------------------------------------------
@dataclass
class SensorCalibration:
    """Calibración lineal de dos puntos del sensor capacitivo (opcional).

    ``humedad_% = (RAW − RAW_seco) / (RAW_húmedo − RAW_seco) × 100``,
    limitada a 0–100 %. En muchos sensores capacitivos RAW_seco > RAW_húmedo;
    la fórmula funciona en ambos sentidos.
    """

    enabled: bool = False
    raw_dry: float | None = None
    raw_wet: float | None = None
    notes: str = ""

    def validate(self) -> list[str]:
        errors = []
        if self.raw_dry is None or self.raw_wet is None:
            errors.append("Debe introducir RAW de suelo seco y RAW de suelo húmedo.")
            return errors
        try:
            dry, wet = float(self.raw_dry), float(self.raw_wet)
        except (TypeError, ValueError):
            return ["Los valores de calibración deben ser numéricos."]
        if not (math.isfinite(dry) and math.isfinite(wet)):
            errors.append("Los valores de calibración deben ser finitos.")
        elif dry == wet:
            errors.append("RAW seco y RAW húmedo no pueden ser iguales (división por cero).")
        return errors

    @property
    def is_usable(self) -> bool:
        return self.enabled and not self.validate()

    def estimate(self, raw) -> np.ndarray:
        """Humedad estimada (%) limitada a 0–100. NaN si la calibración no es válida."""
        raw_arr = np.asarray(raw, dtype=float)
        if not self.is_usable:
            return np.full(raw_arr.shape, np.nan)
        dry, wet = float(self.raw_dry), float(self.raw_wet)
        pct = (raw_arr - dry) / (wet - dry) * 100.0
        return np.clip(pct, 0.0, 100.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "SensorCalibration":
        data = data or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def summarize_moisture(df: pd.DataFrame, calibration: SensorCalibration | None = None) -> pd.DataFrame:
    """Resumen por parcela de las lecturas RAW (y de la estimación calibrada si existe).

    Columnas: parcela, n_mediciones, humedad_raw_media, humedad_raw_mediana,
    humedad_raw_sd, humedad_raw_min, humedad_raw_max; si hay calibración
    activa, humedad_estimada_pct_media y humedad_estimada_pct_sd; si el CSV
    trae el porcentaje calculado por el Arduino, humedad_pct_arduino_media y
    humedad_pct_arduino_sd; y si hay fechas, primera_medicion y ultima_medicion.
    """
    cols = ["parcela", "n_mediciones", "humedad_raw_media", "humedad_raw_mediana", "humedad_raw_sd",
            "humedad_raw_min", "humedad_raw_max"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    use_cal = calibration is not None and calibration.is_usable
    for pid, grp in df.groupby("parcela", sort=True):
        d = describe(grp["humedad_raw"].to_numpy(dtype=float))
        row = {"parcela": str(pid), "n_mediciones": d["n"], "humedad_raw_media": d["mean"],
               "humedad_raw_mediana": d["median"], "humedad_raw_sd": d["std"],
               "humedad_raw_min": d["min"], "humedad_raw_max": d["max"]}
        if use_cal:
            est = calibration.estimate(grp["humedad_raw"].to_numpy(dtype=float))
            de = describe(est)
            row["humedad_estimada_pct_media"] = de["mean"]
            row["humedad_estimada_pct_sd"] = de["std"]
        if "humedad_pct_arduino" in grp and grp["humedad_pct_arduino"].notna().any():
            dp = describe(pd.to_numeric(grp["humedad_pct_arduino"], errors="coerce").to_numpy(dtype=float))
            row["humedad_pct_arduino_media"] = dp["mean"]
            row["humedad_pct_arduino_sd"] = dp["std"]
        if "fecha" in grp:
            dates = [str(f) for f in grp["fecha"] if isinstance(f, str) and f.strip()]
            if dates:
                row["primera_medicion"] = min(dates)
                row["ultima_medicion"] = max(dates)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Observaciones visuales
# ---------------------------------------------------------------------------
@dataclass
class VisualObservation:
    """Valoración visual del estado general de la vegetación de una parcela."""

    parcela: str
    estado_general: int | None = None
    coloracion: str = ""
    densidad: str = ""
    estres_visible: str = ""
    uniformidad: str = ""
    observaciones: str = ""
    fecha: str = ""

    def __post_init__(self) -> None:
        self.parcela = str(self.parcela).strip()
        if self.estado_general in ("", None) or (isinstance(self.estado_general, float) and math.isnan(self.estado_general)):
            self.estado_general = None
        else:
            self.estado_general = int(float(self.estado_general))
        for f in ("coloracion", "densidad", "estres_visible", "uniformidad", "observaciones", "fecha"):
            v = getattr(self, f)
            setattr(self, f, "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip())

    def validate(self) -> list[str]:
        errors = []
        if not self.parcela:
            errors.append("Falta la parcela.")
        if self.estado_general is not None and self.estado_general not in VISUAL_SCALE:
            errors.append("El estado general debe ser un entero de 1 a 5.")
        return errors

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VisualObservation":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def observations_to_df(observations: list[VisualObservation]) -> pd.DataFrame:
    if not observations:
        return pd.DataFrame(columns=OBSERVATION_FIELDS)
    df = pd.DataFrame([o.to_dict() for o in observations])
    df["estado_general"] = pd.to_numeric(df["estado_general"], errors="coerce")
    return df[OBSERVATION_FIELDS]


def read_observations_csv(path: Path | str) -> tuple[list[VisualObservation], list[str]]:
    """Lee observaciones desde CSV con columnas como :data:`OBSERVATION_FIELDS`."""
    path = Path(path)
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return [], [f"No se pudo abrir {path.name}: {exc}"]
    if not text.strip():
        return [], [f"{path.name}: el archivo está vacío"]
    try:
        df = pd.read_csv(io.StringIO(text), sep=_detect_delimiter(text), dtype=str).fillna("")
    except Exception as exc:  # noqa: BLE001
        return [], [f"{path.name}: no se pudo leer ({exc})"]
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "estado" in df and "estado_general" not in df:
        df = df.rename(columns={"estado": "estado_general"})
    if "parcela" not in df:
        return [], [f"{path.name}: falta la columna 'parcela'"]
    obs = []
    for i, row in df.iterrows():
        try:
            o = VisualObservation.from_dict(row.to_dict())
            errs = o.validate()
            if errs:
                errors.append(f"{path.name}, fila {i + 2}: {' '.join(errs)}")
                continue
            obs.append(o)
        except (ValueError, TypeError) as exc:
            errors.append(f"{path.name}, fila {i + 2}: {exc}")
    return obs, errors


def save_observations_csv(observations: list[VisualObservation], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    observations_to_df(observations).to_csv(path, index=False, encoding="utf-8-sig")
    return path
