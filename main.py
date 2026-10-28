"""TR RGB Analyzer — modo consola (sin interfaz gráfica).

Ejemplos::

    python main.py                                # usa input/fotos, input/parcelas, input/datos_campo → output/
    python main.py --project projects/mi_tr.json  # analiza un proyecto guardado desde la GUI
    python main.py --input input/fotos --output output
    python main.py --gui                          # abre la interfaz gráfica

Ejecute ``python main.py --help`` para ver todas las opciones.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.field_data import SensorCalibration, measurements_to_records, read_field_csv, read_observations_csv  # noqa: E402
from src.image_loader import find_images  # noqa: E402
from src.indices import EXPERIMENTAL_INDICES, MAIN_INDICES  # noqa: E402
from src.pipeline import AnalysisCancelled, run_full_analysis  # noqa: E402
from src.project import AnalysisSettings, Project  # noqa: E402
from src.roi import ROIManager  # noqa: E402
from src.utils import (APP_NAME, APP_VERSION, PROJECT_ROOT, ensure_app_dirs, load_config, new_run_dir,  # noqa: E402
                       results_exist, setup_logging)

log = logging.getLogger("tr_rgb.cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description=f"{APP_NAME} {APP_VERSION} — análisis de imágenes RGB de dron (modo consola).",
        epilog="Sin argumentos se analizan las carpetas input/fotos, input/parcelas e input/datos_campo.",
    )
    p.add_argument("--project", type=Path, help="Proyecto JSON creado con la interfaz gráfica")
    p.add_argument("--input", type=Path, help="Carpeta con las fotografías (por defecto input/fotos)")
    p.add_argument("--rois", type=Path, help="Archivo de parcelas/ROIs (.json o .csv). Por defecto input/parcelas/*")
    p.add_argument("--field", type=Path, help="CSV del Arduino (por defecto los CSV de input/datos_campo)")
    p.add_argument("--observations", type=Path, help="CSV de observaciones visuales (estado general 1–5)")
    p.add_argument("--output", type=Path, help="Carpeta de resultados (por defecto output/)")
    p.add_argument("--indices", default=None, help="Índices principales separados por comas (GLI,VARI,ExG)")
    p.add_argument("--experimental", default="", help=f"Índices experimentales opcionales ({','.join(EXPERIMENTAL_INDICES)})")
    p.add_argument("--only-vegetation", action="store_true", help="Analizar sólo píxeles de la máscara RGB de vegetación")
    p.add_argument("--scale", choices=["common", "auto"], help="Escala de color de los mapas")
    p.add_argument("--stat", choices=["mean", "median"], help="Estadístico por ROI para la tabla de parcelas")
    p.add_argument("--cal-dry", type=float, help="Calibración opcional: RAW en suelo seco")
    p.add_argument("--cal-wet", type=float, help="Calibración opcional: RAW en suelo húmedo")
    p.add_argument("--overwrite", action="store_true", help="Sobrescribir resultados existentes sin preguntar")
    p.add_argument("--no-report", action="store_true", help="No generar el informe HTML/Markdown")
    p.add_argument("--config", type=Path, help="Archivo de configuración alternativo")
    p.add_argument("--gui", action="store_true", help="Abrir la interfaz gráfica")
    p.add_argument("-v", "--verbose", action="store_true", help="Mostrar más mensajes")
    return p


def _parse_keys(text: str | None, allowed: tuple[str, ...]) -> list[str]:
    if not text:
        return []
    keys = [k.strip() for k in text.split(",") if k.strip()]
    lookup = {a.lower(): a for a in allowed}
    out = []
    for k in keys:
        if k.upper() == "NDVI":
            raise SystemExit("ERROR: NDVI requiere banda NIR y no puede calcularse con imágenes RGB.")
        if k.lower() not in lookup:
            raise SystemExit(f"ERROR: índice desconocido '{k}'. Opciones: {', '.join(allowed)}")
        out.append(lookup[k.lower()])
    return out


def project_from_folders(args, config: dict) -> tuple[Project, list[str]]:
    """Construye un proyecto a partir de las carpetas de entrada."""
    messages: list[str] = []
    input_dir = args.input or PROJECT_ROOT / "input" / "fotos"
    proj = Project(name="Analisis_consola", settings=AnalysisSettings.from_config(config))
    photos = find_images(input_dir)
    _added, msgs = proj.add_photos(photos, config.get("max_megapixels_warning", 40))
    messages += msgs

    roi_files = [args.rois] if args.rois else sorted((PROJECT_ROOT / "input" / "parcelas").glob("*.json")) + \
        sorted((PROJECT_ROOT / "input" / "parcelas").glob("*.csv"))
    for f in roi_files:
        if not f.exists():
            messages.append(f"No existe el archivo de parcelas {f}")
            continue
        try:
            if f.suffix.lower() == ".json":
                mgr, errs = ROIManager.load_json(f), []
            else:
                mgr, errs = ROIManager.load_csv(f)
            messages += errs
            for r in mgr:
                try:
                    proj.rois.add(r)
                except ValueError as exc:
                    messages.append(f"{f.name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            messages.append(f"No se pudo leer {f.name}: {exc}")

    field_dir = PROJECT_ROOT / "input" / "datos_campo"
    field_files = [args.field] if args.field else [f for f in sorted(field_dir.glob("*.csv"))
                                                   if not f.name.lower().startswith("observ")]
    records = []
    for f in field_files:
        res = read_field_csv(f)
        messages += [f"{Path(f).name}: {e}" for e in res.errors + res.warnings]
        if res.ok:
            records += measurements_to_records(res.df)
            proj.field_source = ", ".join(filter(None, [proj.field_source, str(f)]))
    proj.field_measurements = records
    proj.field_messages = [m for m in messages if ".csv" in m]

    obs_files = [args.observations] if args.observations else sorted(field_dir.glob("observ*.csv"))
    for f in obs_files:
        obs, errs = read_observations_csv(f)
        messages += errs
        for o in obs:
            proj.set_observation(o)
    return proj, messages


def apply_cli_settings(proj: Project, args) -> None:
    s = proj.settings
    if args.indices:
        s.main_indices = _parse_keys(args.indices, MAIN_INDICES)
    if args.experimental:
        s.experimental_indices = _parse_keys(args.experimental, EXPERIMENTAL_INDICES)
    if args.only_vegetation:
        s.only_vegetation = True
    if args.scale:
        s.map_scale_mode = args.scale
    if args.stat:
        s.aggregate_stat = args.stat
    if args.cal_dry is not None or args.cal_wet is not None:
        proj.calibration = SensorCalibration(enabled=True, raw_dry=args.cal_dry, raw_wet=args.cal_wet)
        errs = proj.calibration.validate()
        if errs:
            raise SystemExit("ERROR de calibración: " + " ".join(errs))


def choose_output_dir(output: Path, overwrite: bool) -> Path:
    """Respeta los resultados existentes: pregunta o crea una carpeta nueva."""
    if not results_exist(output) or overwrite:
        return output
    if sys.stdin and sys.stdin.isatty():
        ans = input(f"Ya existen resultados en {output}.\n¿Desea sobrescribir los resultados? [s/N]: ").strip().lower()
        if ans in ("s", "si", "sí", "y", "yes"):
            return output
    new = new_run_dir(output)
    print(f"Los resultados existentes se conservan. Nueva carpeta: {new}")
    return new


def _progress(fraction: float, message: str) -> None:
    width = 20
    filled = int(round(fraction * width))
    bar = "█" * filled + "░" * (width - filled)
    sys.stdout.write(f"\r{bar} {fraction * 100:5.1f}%  {message[:60]:<60}")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:  # evita errores al imprimir símbolos (✓, ρ, █) en consolas antiguas
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ensure_app_dirs()
    setup_logging(logging.DEBUG if args.verbose else logging.WARNING)
    if args.gui:
        from app import main as gui_main

        return gui_main()
    config = load_config(args.config)
    print(f"{APP_NAME} {APP_VERSION} — modo consola\n")

    if args.project:
        if not args.project.exists():
            print(f"ERROR: no existe el proyecto {args.project}")
            return 1
        try:
            proj = Project.load(args.project)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: no se pudo abrir el proyecto: {exc}")
            return 1
        messages = list(proj.field_messages)
    else:
        proj, messages = project_from_folders(args, config)
    apply_cli_settings(proj, args)

    if proj.is_demo:
        print("AVISO: DEMO — NO SON DATOS EXPERIMENTALES\n")
    for m in messages:
        print(f"  ⚠ {m}")
    if not proj.photos:
        folder = args.input or PROJECT_ROOT / "input" / "fotos"
        print(f"No hay fotografías JPG/PNG en {folder}.\n"
              "Copie sus fotos en esa carpeta (o use --input) y defina las parcelas en input/parcelas/parcelas.csv,\n"
              "o utilice la interfaz gráfica: python app.py\n"
              "Para probar el programa con datos sintéticos: python demo.py")
        return 0
    if len(proj.rois) == 0:
        print("AVISO: no hay parcelas/ROIs definidas. Se generarán los mapas de índices, pero no habrá estadísticas "
              "por parcela.\n  Cree input/parcelas/parcelas.csv con columnas id_parcela,nombre,foto,x1,y1,x2,y2,descripcion")

    output = args.output or (proj.default_output_dir() if args.project else PROJECT_ROOT / "output")
    output = choose_output_dir(Path(output), args.overwrite)
    print(f"Fotografías: {len(proj.photos)} | Parcelas: {len(proj.rois.parcel_ids())} | ROIs: {len(proj.rois)} | "
          f"Mediciones de humedad: {len(proj.field_measurements)} | Observaciones: {len(proj.observations)}")
    print(f"Índices: {', '.join(proj.settings.main_indices)}"
          + (f" | Experimentales: {', '.join(proj.settings.experimental_indices)}" if proj.settings.experimental_indices else ""))
    print(f"Resultados en: {output}\n")
    try:
        result = run_full_analysis(proj, output, config, progress=_progress, make_report=not args.no_report)
    except AnalysisCancelled:
        print("\nAnálisis cancelado.")
        return 1
    except ValueError as exc:
        print(f"\nERROR: {exc}")
        return 1
    print("\n")
    print(result.qc.to_text() if result.qc else "")
    print("\nCORRELACIONES (valor exploratorio; no implican causalidad)")
    for r in result.combined.correlation_results:
        print(f"  • {r.interpretation}")
    if not args.project:
        proj.save(Path(output) / "proyecto_usado.json")
    elif proj.path:
        proj.save(proj.path)
    print(f"\nArchivos generados: {len(result.export_files) + len(result.graph_files) + len(result.report_files)}"
          f" (+ {len(result.images.map_files) if result.images else 0} mapas)")
    if result.report_files:
        print(f"Informe: {result.report_files[0]}")
    print(f"Metadatos: {Path(output) / 'analysis_metadata.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
