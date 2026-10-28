"""Tests de estadística descriptiva y agregación."""

import math

import numpy as np
import pandas as pd
import pytest

from src.statistics import aggregate_by_parcel, coefficient_of_variation, describe, photo_comparison_table


def test_describe_basic():
    d = describe(np.array([1, 2, 3, 4, 5], dtype=float))
    assert d["mean"] == pytest.approx(3)
    assert d["median"] == pytest.approx(3)
    assert d["std"] == pytest.approx(np.std([1, 2, 3, 4, 5], ddof=1))
    assert d["min"] == 1 and d["max"] == 5
    assert d["n"] == 5
    assert d["p25"] == pytest.approx(2)


def test_describe_ignores_nan_and_inf():
    d = describe(np.array([1, np.nan, 3, np.inf, -np.inf]))
    assert d["n"] == 2
    assert d["mean"] == pytest.approx(2)


def test_describe_empty():
    d = describe(np.array([]))
    assert d["n"] == 0
    assert math.isnan(d["mean"])


def test_describe_single_value_std_is_nan():
    d = describe(np.array([7.0]))
    assert d["n"] == 1 and d["mean"] == 7
    assert math.isnan(d["std"])


def test_cv():
    assert coefficient_of_variation([10, 10, 10]) == pytest.approx(0)
    assert math.isnan(coefficient_of_variation([0, 0]))


def roi_df():
    return pd.DataFrame({
        "parcela": ["A", "A", "B"], "nombre_parcela": ["x", "x", "y"], "foto": ["f1", "f2", "f1"],
        "fecha_foto": ["2024-01-01", "2024-01-10", "2024-01-01"],
        "GLI_mean": [0.2, 0.4, 0.1], "GLI_median": [0.25, 0.35, 0.1], "cobertura_rgb_pct": [50, 70, 20],
    })


def test_aggregate_by_parcel_mean_between_photos():
    agg = aggregate_by_parcel(roi_df(), ["GLI"], "mean").set_index("parcela")
    assert agg.loc["A", "GLI"] == pytest.approx(0.3)
    assert agg.loc["A", "GLI_sd_entre_fotos"] == pytest.approx(np.std([0.2, 0.4], ddof=1))
    assert agg.loc["A", "n_fotos"] == 2
    assert agg.loc["A", "cobertura_rgb_pct"] == pytest.approx(60)
    assert math.isnan(agg.loc["B", "GLI_sd_entre_fotos"])


def test_aggregate_median_stat():
    agg = aggregate_by_parcel(roi_df(), ["GLI"], "median")
    assert agg.set_index("parcela").loc["A", "GLI"] == pytest.approx(0.3)


def test_photo_comparison_temporal_change():
    comp = photo_comparison_table(roi_df(), ["GLI"])
    a = comp[comp["parcela"] == "A"].iloc[0]
    assert a["cambio_ultima_menos_primera"] == pytest.approx(0.2)
    assert a["n_fotos"] == 2
