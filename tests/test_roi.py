"""Tests de parcelas / ROIs."""

import numpy as np
import pytest

from src.roi import ROI, ROIManager, extract_roi, validate_roi


def test_roi_coordinates_are_sorted():
    r = ROI("A", "n", "f.jpg", 600, 700, 100, 200)
    assert r.coords == (100, 200, 600, 700)
    assert r.width == 500 and r.height == 500


def test_extract_roi_exclusive_end():
    arr = np.arange(100).reshape(10, 10)
    sub = extract_roi(arr, ROI("A", "", "f", 2, 3, 5, 7))
    assert sub.shape == (4, 3)
    assert sub[0, 0] == 32


def test_roi_out_of_bounds_is_clipped():
    arr = np.zeros((10, 10))
    sub = extract_roi(arr, ROI("A", "", "f", 8, 8, 20, 20))
    assert sub.shape == (2, 2)
    issues = validate_roi(ROI("A", "", "f", 8, 8, 20, 20), 10, 10, min_side=1, min_area=1)
    assert any(level == "warning" and "fuera" in msg for level, msg in issues)


def test_roi_completely_outside_is_error():
    issues = validate_roi(ROI("A", "", "f", 50, 50, 60, 60), 10, 10)
    assert any(level == "error" for level, _ in issues)
    with pytest.raises(ValueError):
        extract_roi(np.zeros((10, 10)), ROI("A", "", "f", 50, 50, 60, 60))


def test_empty_and_small_roi():
    assert any(level == "error" for level, _ in validate_roi(ROI("A", "", "f", 5, 5, 5, 9), 100, 100))
    issues = validate_roi(ROI("A", "", "f", 0, 0, 3, 3), 100, 100, min_side=5, min_area=100)
    assert any("pequeña" in m for _, m in issues)


def test_manager_crud_and_persistence(tmp_path):
    m = ROIManager()
    a = m.add(ROI("A", "densa", "f1.jpg", 0, 0, 100, 100, "desc"))
    with pytest.raises(ValueError):
        m.add(ROI("A", "otra", "f1.jpg", 0, 0, 10, 10))  # misma parcela y misma foto
    m.add(ROI("A", "densa", "f2.jpg", 0, 0, 100, 100))
    dup = m.duplicate(a.uid)
    assert dup.parcel_id == "B" and dup.x1 == 20
    copy = m.duplicate(a.uid, photo="f3.jpg")
    assert copy.parcel_id == "A" and copy.coords == a.coords
    m.rename(dup.uid, "nueva", "C")
    assert m.get(dup.uid).parcel_id == "C"
    m.update(a.uid, x2=150)
    assert m.get(a.uid).x2 == 150
    m.delete(copy.uid)
    assert len(m) == 3
    assert m.parcel_ids() == ["A", "C"]

    m.save_json(tmp_path / "r.json")
    m.save_csv(tmp_path / "r.csv")
    j = ROIManager.load_json(tmp_path / "r.json")
    c, errs = ROIManager.load_csv(tmp_path / "r.csv")
    assert errs == []
    assert [r.to_dict() for r in j] == [r.to_dict() for r in m]
    assert [r.coords for r in c] == [r.coords for r in m]


def test_load_csv_semicolon_and_bad_rows(tmp_path):
    p = tmp_path / "p.csv"
    p.write_text("parcela;foto;x1;y1;x2;y2\nA;f.jpg;0;0;10;10\nB;f.jpg;abc;0;10;10\n", encoding="utf-8")
    m, errs = ROIManager.load_csv(p)
    assert len(m) == 1 and len(errs) == 1


def test_load_csv_empty(tmp_path):
    p = tmp_path / "e.csv"
    p.write_text("", encoding="utf-8")
    m, errs = ROIManager.load_csv(p)
    assert len(m) == 0 and errs
