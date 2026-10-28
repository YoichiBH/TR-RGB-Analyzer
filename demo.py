"""Modo demostración de TR RGB Analyzer.

Genera DATOS SINTÉTICOS (fotos, parcelas, humedad y observaciones) en la
carpeta ``demo/`` y ejecuta el análisis completo para comprobar que el
programa funciona sin fotografías reales.

    DEMO — NO SON DATOS EXPERIMENTALES

Los datos sintéticos se guardan SIEMPRE en ``demo/`` (nunca en ``input/`` ni
``output/``) y todas las salidas llevan la marca DEMO, de modo que no pueden
mezclarse con los datos reales del TR.

Uso::

    python demo.py          # genera y analiza
    python demo.py --gui    # además abre el proyecto demo en la interfaz gráfica
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.field_data import VisualObservation, measurements_to_records, read_field_csv  # noqa: E402
from src.pipeline import run_full_analysis  # noqa: E402
from src.project import AnalysisSettings, Project  # noqa: E402
from src.roi import ROI  # noqa: E402
from src.utils import DEMO_LABEL, ensure_app_dirs, load_config, setup_logging  # noqa: E402

DEMO_DIR = ROOT / "demo"
WIDTH, HEIGHT = 1200, 900
DATES = ["2024:03:01 11:00:00", "2024:03:15 11:10:00", "2024:03:29 10:55:00"]

# Parcelas sintéticas: (id, nombre, vigor inicial 0–1, densidad 0–1, nivel de humedad 0–1, tendencia)
PARCELS = [
    ("A", "Vegetación densa", 0.85, 0.85, 0.80, +0.03),
    ("B", "Vegetación media", 0.60, 0.65, 0.60, 0.00),
    ("C", "Vegetación escasa", 0.35, 0.40, 0.35, -0.03),
    ("D", "Zona seca", 0.20, 0.25, 0.15, -0.05),
    ("E", "Vegetación media-alta", 0.70, 0.70, 0.55, +0.01),
    ("F", "Mixta", 0.50, 0.50, 0.45, -0.01),
]


def parcel_rects() -> dict[str, tuple[int, int, int, int]]:
    """Rectángulos de las parcelas en una cuadrícula 3 × 2."""
    rects = {}
    w, h, gap_x, gap_y, x0, y0 = 300, 300, 75, 100, 75, 100
    for i, (pid, *_rest) in enumerate(PARCELS):
        col, row = i % 3, i // 3
        x1 = x0 + col * (w + gap_x)
        y1 = y0 + row * (h + gap_y)
        rects[pid] = (x1, y1, x1 + w, y1 + h)
    return rects


def _smooth_noise(rng: np.random.Generator, h: int, w: int, cell: int = 12) -> np.ndarray:
    """Ruido suave (manchas) para simular plantas."""
    small = rng.random((h // cell + 2, w // cell + 2))
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w + 2 * cell, h + 2 * cell), Image.Resampling.BICUBIC)
    return np.asarray(img, dtype=np.float32)[cell:cell + h, cell:cell + w] / 255.0


def make_photo(index: int, rng: np.random.Generator) -> Image.Image:
    """Crea una foto sintética con suelo y parcelas de distinto verdor."""
    soil = np.array([150, 115, 80], dtype=np.float32)
    img = soil + rng.normal(0, 10, (HEIGHT, WIDTH, 3)).astype(np.float32)
    rects = parcel_rects()
    for pid, _name, vigor, density, _moist, trend in PARCELS:
        v = float(np.clip(vigor + trend * index * 3, 0.05, 0.95))
        x1, y1, x2, y2 = rects[pid]
        h, w = y2 - y1, x2 - x1
        noise = _smooth_noise(rng, h, w)
        plants = noise < density
        dry = np.array([160, 150, 75], dtype=np.float32)
        lush = np.array([45, 125, 40], dtype=np.float32)
        color = dry * (1 - v) + lush * v
        patch = img[y1:y2, x1:x2]
        patch[plants] = color + rng.normal(0, 12, (int(plants.sum()), 3))
    brightness = [1.0, 1.04, 0.96][index % 3]  # iluminación ligeramente distinta en cada vuelo
    img = np.clip(img * brightness, 0, 255).astype(np.uint8)
    pil = Image.fromarray(img)
    draw = ImageDraw.Draw(pil)
    try:
        font = ImageFont.load_default(size=30)
    except TypeError:  # Pillow antiguo
        font = ImageFont.load_default()
    draw.rectangle((0, 0, WIDTH, 50), fill=(255, 255, 255))
    draw.text((20, 10), "DEMO - NO SON DATOS EXPERIMENTALES (imagen sintetica)", fill=(200, 0, 0), font=font)
    return pil


def generate_demo_data(rng_seed: int = 42) -> dict:
    """Escribe fotos, CSV de humedad, observaciones y parcelas sintéticas en demo/."""
    rng = np.random.default_rng(rng_seed)
    for sub in ("fotos", "datos_campo"):
        (DEMO_DIR / sub).mkdir(parents=True, exist_ok=True)
    (DEMO_DIR / "LEEME_DEMO.txt").write_text(
        f"{DEMO_LABEL}\n\nEsta carpeta contiene datos SINTÉTICOS generados por demo.py para comprobar que el "
        "programa funciona.\nNo los utilice como resultados del Treball de Recerca.\n", encoding="utf-8")
    photos = []
    for i, date in enumerate(DATES):
        path = DEMO_DIR / "fotos" / f"DEMO_vuelo_{i + 1:02d}.jpg"
        exif = Image.Exif()
        exif[306] = date
        exif[270] = DEMO_LABEL.encode("ascii", "replace").decode()
        make_photo(i, rng).save(path, quality=92, exif=exif)
        photos.append(path)

    # Humedad RAW sintética: sensor capacitivo → más humedad = RAW más bajo.
    # Formato del Arduino: fecha,hora,parcela,lectura_adc,porcentaje_humedad
    # (el porcentaje imita un map(raw, 800, 400, 0, 100) + constrain() hecho en el Arduino).
    lines = ["fecha,hora,parcela,lectura_adc,porcentaje_humedad"]
    second = 0
    for pid, _n, _v, _d, moist, _t in PARCELS:
        for _m in range(1, 6):
            raw = round(760 - 330 * moist + rng.normal(0, 12))
            pct = int(min(max((raw - 800) / (400 - 800) * 100, 0), 100))
            second += 7
            lines.append(f"2024-03-29,11:{30 + second // 60:02d}:{second % 60:02d},{pid},{raw},{pct}")
    field_csv = DEMO_DIR / "datos_campo" / "humedad_arduino_DEMO.csv"
    field_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    observations = []
    for pid, _n, vigor, density, _m, _t in PARCELS:
        estado = int(np.clip(round(1 + vigor * 4 + rng.normal(0, 0.4)), 1, 5))
        observations.append(VisualObservation(
            parcela=pid, estado_general=estado,
            coloracion="verde intenso" if vigor > 0.7 else "verde claro" if vigor > 0.45 else "amarillenta",
            densidad="alta" if density > 0.7 else "media" if density > 0.45 else "baja",
            estres_visible="no" if vigor > 0.5 else "sí, leve" if vigor > 0.3 else "sí",
            uniformidad="uniforme" if density > 0.6 else "irregular",
            observaciones="DEMO (sintético)", fecha="2024-03-29"))
    return {"photos": photos, "field_csv": field_csv, "observations": observations}


def build_demo_project(config: dict) -> Project:
    data = generate_demo_data()
    proj = Project(name="DEMO - datos sinteticos", is_demo=True, settings=AnalysisSettings.from_config(config))
    proj.output_dir = str(DEMO_DIR / "output")
    proj.add_photos(data["photos"])
    rects = parcel_rects()
    for photo in proj.photos:
        for pid, name, *_ in PARCELS:
            x1, y1, x2, y2 = rects[pid]
            # Margen de 10 px para no incluir el borde de la parcela.
            proj.rois.add(ROI(pid, name, photo.name, x1 + 10, y1 + 10, x2 - 10, y2 - 10, "Parcela sintética (DEMO)"))
    res = read_field_csv(data["field_csv"])
    proj.field_measurements = measurements_to_records(res.df)
    proj.field_source = str(data["field_csv"])
    for o in data["observations"]:
        proj.set_observation(o)
    proj.rois.save_csv(DEMO_DIR / "parcelas_DEMO.csv")
    proj.save(DEMO_DIR / "DEMO_proyecto.json")
    return proj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera datos sintéticos y ejecuta el análisis completo.")
    parser.add_argument("--gui", action="store_true", help="Abrir el proyecto demo en la interfaz gráfica")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ensure_app_dirs()
    setup_logging()
    config = load_config()
    print("=" * 60)
    print(DEMO_LABEL)
    print("=" * 60)
    print(f"Generando datos sintéticos en {DEMO_DIR} …")
    proj = build_demo_project(config)
    out = Path(proj.output_dir)

    def progress(f: float, msg: str) -> None:
        bar = "█" * int(f * 20) + "░" * (20 - int(f * 20))
        sys.stdout.write(f"\r{bar} {f * 100:5.1f}%  {msg[:55]:<55}")
        sys.stdout.flush()

    result = run_full_analysis(proj, out, config, progress=progress)
    proj.save()
    print("\n")
    print(result.qc.to_text())
    print("\nTabla experimental (DEMO):")
    cols = [c for c in ["parcela", "GLI", "VARI", "ExG", "cobertura_rgb_pct", "humedad_raw_media", "estado_general"]
            if c in result.combined.parcel_table]
    print(result.combined.parcel_table[cols].round(3).to_string(index=False))
    print("\nCorrelaciones (DEMO):")
    for r in result.combined.correlation_results:
        print(f"  • {r.interpretation}")
    print(f"\nResultados DEMO en: {out}")
    print(f"Informe: {result.report_files[0] if result.report_files else '—'}")
    print(f"Proyecto DEMO (se puede abrir en la GUI): {proj.path}")
    print("\n" + DEMO_LABEL)
    if args.gui:
        from app import main as gui_main

        return gui_main(project_path=proj.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
