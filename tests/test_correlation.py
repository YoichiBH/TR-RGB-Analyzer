"""Tests de correlaciones de Pearson y Spearman."""

import math

import numpy as np
import pytest

from src.correlation import correlate, pearson_r, rankdata, spearman_rho, t_test_pvalue


def test_pearson_perfect():
    assert pearson_r([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1)
    assert pearson_r([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1)


def test_pearson_constant_is_nan():
    assert math.isnan(pearson_r([1, 1, 1], [1, 2, 3]))


def test_rankdata_ties():
    assert rankdata([10, 20, 20, 30]).tolist() == [1, 2.5, 2.5, 4]


def test_spearman_monotonic_nonlinear():
    x = np.array([1, 2, 3, 4, 5], dtype=float)
    assert spearman_rho(x, x ** 3) == pytest.approx(1)


def test_pvalue_known_value():
    # r = 0.5, n = 10 -> t = 1.633, gl = 8 -> p bilateral ≈ 0.1411
    assert t_test_pvalue(0.5, 10) == pytest.approx(0.1411, abs=1e-3)


def test_against_scipy_if_available():
    stats = pytest.importorskip("scipy.stats")
    rng = np.random.default_rng(1)
    x, y = rng.normal(size=12), rng.normal(size=12)
    assert pearson_r(x, y) == pytest.approx(stats.pearsonr(x, y)[0], abs=1e-10)
    assert spearman_rho(x, y) == pytest.approx(stats.spearmanr(x, y)[0], abs=1e-10)
    assert t_test_pvalue(pearson_r(x, y), 12) == pytest.approx(stats.pearsonr(x, y)[1], abs=1e-8)


def test_correlate_drops_missing_and_warns_small_n():
    res = correlate([1, 2, 3, None, 5], [2, 4, 7, 8, float("nan")], "humedad_raw_media", "GLI",
                    labels=list("ABCDE"))
    assert res.n == 3
    assert res.parcels == ["A", "B", "C"]
    assert "exploratorio" in res.warning
    assert "no permite establecer causalidad" in res.interpretation


def test_correlate_insufficient_data():
    res = correlate([1, 2], [3, 4], "x", "y")
    assert math.isnan(res.pearson_r) and "insuficientes" in res.warning


def test_null_results_are_reported():
    res = correlate([1, 2, 3, 4, 5, 6], [3, 1, 4, 1, 5, 2], "humedad_raw_media", "GLI")
    assert res.n == 6
    assert math.isfinite(res.pearson_r)
    assert "asociación" in res.interpretation


def test_spearman_exact_pvalue_n3_perfect_order():
    # Con n = 3 hay 6 permutaciones; 2 dan |ρ| = 1  ->  p = 2/6
    from src.correlation import spearman_exact_pvalue

    assert spearman_exact_pvalue([454, 502, 562], [0.228, 0.025, 0.004]) == pytest.approx(1 / 3)


def test_correlate_small_n_never_reports_p_zero():
    res = correlate([454, 502, 562], [0.228, 0.025, 0.004], "humedad_raw_media", "GLI")
    assert res.spearman_rho == pytest.approx(-1)
    assert res.spearman_p == pytest.approx(1 / 3)
    assert "exacto" in res.p_method


def test_spearman_exact_matches_scipy_permutation_for_n5():
    from src.correlation import spearman_exact_pvalue

    x, y = [1, 2, 3, 4, 5], [2, 1, 4, 3, 5]
    # ρ = 0.8; permutaciones con |ρ| >= 0.8 de 120 en total
    from itertools import permutations
    from src.correlation import spearman_rho
    expected = sum(abs(spearman_rho(x, list(p))) >= 0.8 - 1e-12 for p in permutations(y)) / 120
    assert spearman_exact_pvalue(x, y) == pytest.approx(expected)
