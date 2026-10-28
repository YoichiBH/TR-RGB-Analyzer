"""Tests de integración: flujo completo con imágenes sintéticas en una carpeta temporal.

Comprueba también que las fotografías originales no se modifican.
"""

import hashlib

import numpy as np
import pandas as pd
from PIL import Image

from src.field_data import measurements_to_records, parse_field_text
from src.pipeline import run_full_analysis
from src.project import Project
from src.roi import ROI
from src.utils import load_config


def _md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def _make_image(path, green: float):
    img = np.zeros((120, 160, 3), dtype=np.uint8)
    img[:] = (140, 110, 80)
    img[20:100, 20:70] = (int(150 - 100 * green), int(110 + 30 * green), 60)
    img[20:100, 90:140] = (60, 130, 40)
    img[0:5, 0:5] = 0  # píxeles negros (división por cero en GLI)
    Image.fromarray(img).save(path)


def test_full_pipeline(tmp_path):
    photos = []
    for i, g in enumerate([0.2, 0.6, 0.9]):
        p = tmp_path / f"foto_{i}.png"
        _make_image(p, g)
        photos.append(p)
    hashes = {p: _md5(p) for p in photos}

    proj = Project(name="test")
    added, _ = proj.add_photos(photos)
    assert len(added) == 3
    for i, ph in enumerate(proj.photos):
        proj.set_photo_date(ph.name, f"2024-01-0{i + 1}")
        proj.rois.add(ROI("A", "variable", ph.name, 20, 20, 70, 100))
        proj.rois.add(ROI("B", "verde", ph.name, 90, 20, 140, 100))
        proj.rois.add(ROI("C", "borde", ph.name, 150, 100, 400, 400))  # parcialmente fuera
    df = parse_field_text("parcela,medicion,humedad_raw\nA,1,700\nA,2,690\nB,1,500\nB,2,510\nC,1,600\n").df
    proj.field_measurements = measurements_to_records(df)

    out = tmp_path / "out"
    result = run_full_analysis(proj, out, load_config())

    # Las fotografías originales siguen intactas
    assert all(_md5(p) == h for p, h in hashes.items())
    # Tablas
    roi_df = result.images.roi_df
    assert len(roi_df) == 9
    assert {"GLI_mean", "GLI_median", "GLI_std", "GLI_min", "GLI_max", "VARI_mean", "ExG_mean"} <= set(roi_df.columns)
    assert roi_df["roi_recortada"].sum() == 3
    assert not np.isinf(roi_df.select_dtypes("number").to_numpy(dtype=float)).any()
    table = result.combined.parcel_table.set_index("parcela")
    assert table.loc["B", "GLI"] > table.loc["A", "GLI"]
    assert table.loc["A", "n_fotos"] == 3
    # Archivos generados
    for rel in ["analysis_metadata.json", "statistics/resultados_por_roi.csv", "exports/resultados_por_parcela.csv",
                "exports/correlaciones.csv", "exports/datos_campo.csv", "reports/informe.html", "reports/informe.md",
                "graphs/GLI_por_parcela.png", "graphs/humedad_raw_por_parcela.png", "graphs/GLI_vs_fecha.png",
                "indices/foto_0_GLI.png", "indices/foto_0_VARI.png", "indices/foto_0_ExG.png",
                "indices/foto_0_mascara_vegetacion.png", "rois/foto_0_parcelas.png"]:
        assert (out / rel).exists(), rel
    csv = pd.read_csv(out / "exports" / "resultados_por_parcela.csv", sep=";", decimal=",")
    assert set(csv["parcela"].astype(str)) == {"A", "B", "C"}
    html = (out / "reports" / "informe.html").read_text(encoding="utf-8")
    assert "NDVI" in html and "causalidad" in html
    assert "DEMO" not in html


def test_pipeline_without_rois_and_with_corrupt_image(tmp_path):
    good = tmp_path / "ok.jpg"
    Image.fromarray(np.full((50, 50, 3), 120, dtype=np.uint8)).save(good)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff garbage")
    proj = Project(name="t2")
    proj.add_photos([good, bad])
    result = run_full_analysis(proj, tmp_path / "o", load_config())
    assert result.images.roi_df.empty
    assert result.qc.has_errors
    assert (tmp_path / "o" / "indices" / "ok_GLI.png").exists()


def test_project_roundtrip(tmp_path):
    img = tmp_path / "a.png"
    Image.fromarray(np.zeros((10, 10, 3), dtype=np.uint8)).save(img)
    proj = Project(name="rt")
    proj.add_photos([img])
    proj.rois.add(ROI("A", "x", "a.png", 0, 0, 5, 5))
    proj.settings.experimental_indices = ["NGRDI"]
    path = proj.save(tmp_path / "p" / "proj.json")
    loaded = Project.load(path)
    assert loaded.photos[0].name == "a.png"
    assert loaded.rois.rois[0].coords == (0, 0, 5, 5)
    assert loaded.settings.experimental_indices == ["NGRDI"]
