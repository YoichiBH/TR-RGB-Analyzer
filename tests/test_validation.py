"""Tests de control de calidad, lectura de CSV de campo, calibración y observaciones."""

import numpy as np
import pandas as pd
import pytest

from src.field_data import (SensorCalibration, VisualObservation, parse_field_text, read_field_csv,
                            read_observations_csv, summarize_moisture)
from src.image_loader import get_image_info
from src.roi import ROI
from src.validation import build_qc_report, check_finite


# -- CSV del Arduino ------------------------------------------------------------------
def test_read_recommended_csv(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("parcela,medicion,humedad_raw\nA,1,612\nA,2,605\nA,3,618\nB,1,701\nB,2,694\nB,3,708\n")
    res = read_field_csv(p)
    assert res.ok and len(res.df) == 6
    s = summarize_moisture(res.df).set_index("parcela")
    assert s.loc["A", "humedad_raw_media"] == pytest.approx((612 + 605 + 618) / 3)
    assert s.loc["A", "humedad_raw_mediana"] == 612
    assert s.loc["B", "n_mediciones"] == 3
    assert s.loc["A", "humedad_raw_sd"] == pytest.approx(np.std([612, 605, 618], ddof=1))
    assert s.loc["A", "humedad_raw_min"] == 605 and s.loc["A", "humedad_raw_max"] == 618


ARDUINO_CSV = """fecha,hora,parcela,lectura_adc,porcentaje_humedad
2026-10-12,15:35:34,1,480,8
2026-10-12,15:35:43,2,514,0
2026-10-12,15:35:48,3,490,5
2026-10-12,15:36:02,1,470,12
2026-10-12,15:36:10,2,520,0
"""


def test_arduino_format_fecha_hora_lectura_adc(tmp_path):
    p = tmp_path / "arduino.csv"
    p.write_text(ARDUINO_CSV, encoding="utf-8")
    res = read_field_csv(p)
    assert res.ok, res.errors
    df = res.df
    assert len(df) == 5
    # lectura_adc es el dato RAW principal
    assert df["humedad_raw"].tolist() == [480, 514, 490, 470, 520]
    # fecha y hora se combinan
    assert df["fecha"].iloc[0] == "2026-10-12 15:35:34"
    # sin columna medicion: numeración por parcela en orden de aparición
    assert df[df["parcela"] == "1"]["medicion"].tolist() == [1, 2]
    # el porcentaje del Arduino se conserva aparte
    assert df["humedad_pct_arduino"].tolist() == [8, 0, 5, 12, 0]
    assert any("Arduino" in w for w in res.warnings)
    assert any("0 % o 100 %" in w for w in res.warnings)  # valores recortados avisados

    s = summarize_moisture(df).set_index("parcela")
    assert s.loc["1", "humedad_raw_media"] == pytest.approx(475)
    assert s.loc["1", "humedad_pct_arduino_media"] == pytest.approx(10)
    assert s.loc["1", "primera_medicion"] == "2026-10-12 15:35:34"
    assert s.loc["2", "n_mediciones"] == 2


def test_arduino_format_roundtrip_in_project():
    from src.field_data import measurements_to_records, records_to_measurements

    df = parse_field_text(ARDUINO_CSV).df
    back = records_to_measurements(measurements_to_records(df))
    assert back["humedad_pct_arduino"].tolist() == [8, 0, 5, 12, 0]
    assert back["humedad_raw"].tolist() == df["humedad_raw"].tolist()


def test_arduino_format_semicolon_without_percentage():
    res = parse_field_text("fecha;hora;parcela;lectura_adc\n12/10/2026;15:35:34;A;480\n")
    assert res.ok
    assert res.df["humedad_raw"].iloc[0] == 480
    assert res.df["fecha"].iloc[0] == "12/10/2026 15:35:34"
    assert res.df["humedad_pct_arduino"].isna().all()


def test_empty_csv_does_not_crash(tmp_path):
    p = tmp_path / "vacio.csv"
    p.write_text("")
    res = read_field_csv(p)
    assert not res.ok and res.errors


def test_header_only_csv(tmp_path):
    p = tmp_path / "cab.csv"
    p.write_text("parcela,medicion,humedad_raw\n")
    assert not read_field_csv(p).ok


def test_csv_missing_columns():
    res = parse_field_text("zona_x;valorx\nA;1\n")
    assert not res.ok and "Faltan columnas" in res.errors[0]


def test_csv_semicolon_decimal_comma_and_bad_rows():
    res = parse_field_text("parcela;humedad_raw\nA;612,5\nB;error\n;600\n")
    assert res.ok
    assert len(res.df) == 1
    assert res.df["humedad_raw"].iloc[0] == pytest.approx(612.5)
    assert len(res.warnings) >= 2  # las filas omitidas se informan, no se ocultan
    assert res.df["medicion"].iloc[0] == 1


def test_nonexistent_csv():
    assert not read_field_csv("no_existe_123.csv").ok


def test_raw_is_never_converted_without_calibration():
    df = parse_field_text("parcela,medicion,humedad_raw\nA,1,600\n").df
    s = summarize_moisture(df, SensorCalibration(enabled=False, raw_dry=800, raw_wet=400))
    assert "humedad_estimada_pct_media" not in s.columns
    assert s["humedad_raw_media"].iloc[0] == 600


# -- Calibración -----------------------------------------------------------------------
def test_calibration_linear_and_clipped():
    cal = SensorCalibration(enabled=True, raw_dry=800, raw_wet=400)
    assert cal.estimate([800, 600, 400, 900, 300]).tolist() == pytest.approx([0, 50, 100, 0, 100])


def test_calibration_equal_values_is_invalid():
    cal = SensorCalibration(enabled=True, raw_dry=500, raw_wet=500)
    assert cal.validate()
    assert np.isnan(cal.estimate([500])).all()


# -- Observaciones ----------------------------------------------------------------------
def test_observation_validation():
    assert VisualObservation("A", 6).validate()
    assert not VisualObservation("A", 4).validate()


def test_observations_csv(tmp_path):
    p = tmp_path / "obs.csv"
    p.write_text("parcela;estado;observaciones\nA;4;bien\nB;9;mal\n", encoding="utf-8")
    obs, errs = read_observations_csv(p)
    assert len(obs) == 1 and obs[0].estado_general == 4
    assert len(errs) == 1


# -- Imágenes ---------------------------------------------------------------------------
def test_corrupt_image_detected(tmp_path):
    p = tmp_path / "rota.jpg"
    p.write_bytes(b"esto no es una imagen")
    info = get_image_info(p)
    assert not info.valid and "corrupta" in info.error.lower()


def test_check_finite():
    assert check_finite(np.array([1, np.nan, np.inf])) == (1, 1)


# -- Panel de control de calidad ----------------------------------------------------------
def test_qc_report_detects_problems():
    photos = [{"name": "f1.jpg", "valid": True, "width": 100, "height": 100},
              {"name": "f2.jpg", "valid": True, "width": 100, "height": 100},
              {"name": "rota.jpg", "valid": False, "width": 0, "height": 0, "error": "corrupta"}]
    rois = [ROI("A", "", "f1.jpg", 0, 0, 50, 50), ROI("B", "", "f1.jpg", 200, 200, 300, 300)]
    field = pd.DataFrame({"parcela": ["A", "C"], "humedad_raw": [600.0, 700.0], "medicion": [1, 1], "fecha": ["", ""]})
    text = (qc := build_qc_report(photos, rois, None, field, None)).to_text()
    assert "Imagen corrupta" in text
    assert "fuera de los límites" in text           # ROI B fuera de la imagen
    assert "Imagen sin parcela: f2.jpg" in text
    assert "sin humedad: B" in text                  # parcela sin datos de campo
    assert "sin imagen/ROI: parcelas C" in text      # medición de campo sin imagen
    assert qc.has_errors
