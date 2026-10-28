"""Tests de las fórmulas de los índices RGB y de la protección frente a divisiones por cero."""

import numpy as np
import pytest

from src.indices import (EXPERIMENTAL_INDICES, MAIN_INDICES, compute_index, exg, gli, safe_divide, vari,
                         vegetation_mask)
from src.preprocessing import PreprocessingOptions, preprocess


def px(r, g, b):
    """Imagen 1×1 normalizada 0–1."""
    return np.array([[[r, g, b]]], dtype=np.float32)


def test_gli_formula():
    # GLI = (2G - R - B) / (2G + R + B) = (1.2 - 0.2 - 0.1) / (1.2 + 0.2 + 0.1) = 0.9 / 1.5
    v, n0 = gli(px(0.2, 0.6, 0.1))
    assert v[0, 0] == pytest.approx(0.6, abs=1e-6)
    assert n0 == 0


def test_vari_formula():
    # VARI = (G - R) / (G + R - B) = (0.6 - 0.2) / (0.6 + 0.2 - 0.1) = 0.4 / 0.7
    v, _ = vari(px(0.2, 0.6, 0.1))
    assert v[0, 0] == pytest.approx(0.4 / 0.7, abs=1e-6)


def test_exg_formula_normalized():
    # ExG = 2G - R - B = 1.2 - 0.2 - 0.1
    v, _ = exg(px(0.2, 0.6, 0.1))
    assert v[0, 0] == pytest.approx(0.9, abs=1e-6)


def test_exg_formula_chromatic():
    # r = 0.2/0.9, g = 0.6/0.9, b = 0.1/0.9  ->  2g - r - b = (1.2 - 0.3) / 0.9 = 1.0
    v, _ = exg(px(0.2, 0.6, 0.1), mode="chromatic")
    assert v[0, 0] == pytest.approx(1.0, abs=1e-6)


def test_gray_pixel_values():
    rgb = px(0.5, 0.5, 0.5)
    assert gli(rgb)[0][0, 0] == pytest.approx(0)
    assert exg(rgb)[0][0, 0] == pytest.approx(0)
    assert vari(rgb)[0][0, 0] == pytest.approx(0)


def test_gli_division_by_zero_black_pixel():
    v, n0 = gli(px(0, 0, 0))
    assert np.isnan(v[0, 0])
    assert n0 == 1


def test_vari_division_by_zero():
    # G + R - B = 0.2 + 0.3 - 0.5 = 0
    v, n0 = vari(px(0.3, 0.2, 0.5))
    assert np.isnan(v[0, 0])
    assert n0 == 1


def test_safe_divide_never_returns_inf():
    out, n0 = safe_divide(np.array([1.0, 1.0, 0.0]), np.array([0.0, 1e-9, 0.0]))
    assert np.isnan(out).all()
    assert n0 == 3
    assert not np.isinf(out).any()


def test_indices_scale_invariance():
    # GLI y VARI no dependen de la normalización; ExG sí (por eso se normaliza).
    a, b = px(0.2, 0.6, 0.1), px(51, 153, 25.5)
    assert gli(a)[0][0, 0] == pytest.approx(gli(b)[0][0, 0], rel=1e-5)
    assert vari(a)[0][0, 0] == pytest.approx(vari(b)[0][0, 0], rel=1e-5)


def test_compute_index_respects_valid_mask():
    rgb = np.array([[[0.2, 0.6, 0.1], [0, 0, 0]]], dtype=np.float32)
    mask = np.array([[True, False]])
    res = compute_index("GLI", rgb, mask)
    assert res.values[0, 0] == pytest.approx(0.6)
    assert np.isnan(res.values[0, 1])


def test_ndvi_is_rejected():
    with pytest.raises(ValueError, match="NIR"):
        compute_index("NDVI", px(0.1, 0.2, 0.3))


def test_all_indices_compute_without_inf():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 256, (50, 50, 3), dtype=np.uint8)
    img[0, 0] = 0  # píxel negro: provoca divisiones por cero
    pre = preprocess(img, PreprocessingOptions())
    for key in MAIN_INDICES + EXPERIMENTAL_INDICES:
        res = compute_index(key, pre.rgb, pre.valid_mask)
        assert not np.isinf(res.values).any(), key
        assert res.values.dtype == np.float32


def test_vegetation_mask():
    rgb = np.array([[[0.2, 0.6, 0.1], [0.55, 0.45, 0.3]]], dtype=np.float32)  # verde, suelo
    assert vegetation_mask(rgb, "exg", 0.1).tolist() == [[True, False]]
    assert vegetation_mask(rgb, "rgb").tolist() == [[True, False]]


def test_preprocess_does_not_modify_input():
    img = np.full((4, 4, 3), 255, dtype=np.uint8)
    copy = img.copy()
    pre = preprocess(img, PreprocessingOptions(exclude_saturated=True))
    assert np.array_equal(img, copy)
    assert pre.n_valid == 0
    assert pre.counts["saturados"] == 16


def test_vari_clipped_to_unit_interval():
    # Píxel 1: G + R - B = 0.40 + 0.21 - 0.60 = 0.01  ->  VARI = 0.19 / 0.01 = 19 (extremo) -> se recorta a 1
    # Píxel 2: valor normal (0.4 / 0.7), no se modifica
    rgb = np.array([[[0.21, 0.40, 0.60], [0.2, 0.6, 0.1]]], dtype=np.float32)
    res = compute_index("VARI", rgb)
    assert res.values[0, 0] == pytest.approx(1.0)
    assert res.values[0, 1] == pytest.approx(0.4 / 0.7, abs=1e-6)  # valor normal: no cambia
    assert res.n_clipped == 1
    assert res.clipped.tolist() == [[True, False]]


def test_vari_clip_can_be_disabled():
    rgb = np.array([[[0.21, 0.40, 0.60]]], dtype=np.float32)
    res = compute_index("VARI", rgb, vari_clip=False)
    assert res.values[0, 0] == pytest.approx(19.0, rel=1e-3)
    assert res.n_clipped == 0


def test_only_vari_is_clipped():
    rgb = np.array([[[0.0, 1.0, 0.0]]], dtype=np.float32)  # ExG normalizado = 2 (> 1)
    res = compute_index("ExG", rgb)
    assert res.values[0, 0] == pytest.approx(2.0)
    assert res.clipped is None


def test_default_settings_use_chromatic_exg_and_normalized_mask():
    from src.project import AnalysisSettings

    s = AnalysisSettings()
    assert s.exg_mode == "chromatic"
    assert s.vegetation_exg_mode == "normalized"
    assert s.vari_clip is True
