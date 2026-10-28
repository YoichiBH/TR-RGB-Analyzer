"""TR RGB Analyzer — interfaz gráfica.

Uso (Windows): doble clic en run.bat, o bien::

    python app.py
    python app.py --project projects/mi_proyecto.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _enable_windows_dpi_awareness() -> None:
    """Texto nítido en pantallas con escalado (Windows 8.1+)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass


def main(project_path: Path | str | None = None) -> int:
    """Arranca la interfaz gráfica. Funciona aunque todavía no haya fotografías."""
    from src.utils import ensure_app_dirs, load_config, setup_logging

    ensure_app_dirs()
    setup_logging(logging.INFO)
    log = logging.getLogger("tr_rgb.app")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("ERROR: Tkinter no está disponible. Reinstale Python marcando la opción 'tcl/tk and IDLE'.")
        return 1
    _enable_windows_dpi_awareness()
    from gui.main_window import MainWindow

    config = load_config()
    try:
        app = MainWindow(config, project_path=Path(project_path) if project_path else None)
        app.mainloop()
    except Exception:  # noqa: BLE001
        log.exception("Error fatal en la interfaz")
        raise
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TR RGB Analyzer — interfaz gráfica")
    parser.add_argument("--project", type=Path, help="Proyecto JSON a abrir al iniciar")
    args = parser.parse_args()
    sys.exit(main(args.project))
