"""Ventana principal de TR RGB Analyzer."""

from __future__ import annotations

import datetime as _dt
import logging
import os
import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk


from src.export import HAS_OPENPYXL, export_all, export_excel
from src.field_data import (SensorCalibration, VisualObservation, VISUAL_SCALE, measurements_to_records,
                            read_field_csv, read_observations_csv)
from src.image_loader import SUPPORTED_EXTENSIONS
from src.indices import EXPERIMENTAL_INDICES, INDEX_DEFINITIONS, MAIN_INDICES, index_documentation_text
from src.pipeline import (LIMITATIONS, AnalysisCancelled, AnalysisResult, build_metadata, combine_results,
                          generate_graphs, run_full_analysis)
from src.project import AnalysisSettings, Project
from src.report import write_reports
from src.roi import ROI, ROIManager
from src.utils import (APP_NAME, APP_VERSION, DEMO_LABEL, PROJECT_ROOT, ensure_output_dirs, new_run_dir,
                       results_exist, slugify, write_json)
from src.validation import QCItem, build_qc_report

from .field_view import FieldView
from .image_viewer import ImageViewer
from .options_dialog import AnalysisOptionsDialog
from .photos_view import PhotosView
from .results_view import ResultsView
from .roi_editor import RoiPanel, ask_roi
from .widgets import (COLOR_ACCENT, COLOR_DEMO, FONT, FONT_BOLD, FONT_TITLE, FormDialog, ProgressPanel,
                      ScrollableFrame, ToolTip, ask_overwrite, show_text_window)

log = logging.getLogger("tr_rgb.gui")

TAB_VIEWER, TAB_PHOTOS, TAB_FIELD, TAB_RESULTS, TAB_QC, TAB_LIMITS = range(6)


class MainWindow(tk.Tk):
    """Aplicación gráfica completa."""

    def __init__(self, config: dict, project_path: Path | None = None) -> None:
        super().__init__()
        self.config_data = config
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1400x880")
        self.minsize(1100, 700)
        self.option_add("*Font", FONT)
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Accent.TButton", font=FONT_BOLD)
        style.configure("Section.TLabelframe.Label", font=FONT_BOLD, foreground=COLOR_ACCENT)

        self.project = Project(settings=AnalysisSettings.from_config(config))
        self.current_photo: str | None = None
        self.selected_roi: str | None = None
        self.combined = None
        self.results_stale = False
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind_all("<Control-s>", lambda e: self.save_project())
        self.bind_all("<Control-o>", lambda e: self.open_project())
        self.bind_all("<Control-n>", lambda e: self.new_project())
        self.refresh_all()
        if project_path:
            self.after(200, lambda: self.open_project(Path(project_path)))

    # ================================================================== UI
    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(10, 6))
        header.pack(fill="x")
        ttk.Label(header, text="TR RGB ANALYZER", font=FONT_TITLE, foreground=COLOR_ACCENT).pack(side="left")
        ttk.Label(header, text="  Análisis de imágenes RGB de dron · agricultura de precisión",
                  foreground="#555").pack(side="left", pady=(6, 0))
        self.demo_label = tk.Label(header, text="", fg="white", bg=COLOR_DEMO, font=FONT_BOLD)
        self.demo_label.pack(side="right")

        pbar = ttk.Frame(self, padding=(10, 0, 10, 4))
        pbar.pack(fill="x")
        ttk.Label(pbar, text="Proyecto:", font=FONT_BOLD).pack(side="left")
        for txt, cmd, tip in (("Nuevo", self.new_project, "Ctrl+N"), ("Abrir…", self.open_project, "Ctrl+O"),
                              ("Guardar", self.save_project, "Ctrl+S"), ("Guardar como…", self.save_project_as, "")):
            b = ttk.Button(pbar, text=txt, command=cmd)
            b.pack(side="left", padx=2)
            if tip:
                ToolTip(b, tip)
        self.project_label = ttk.Label(pbar, text="", foreground="#333")
        self.project_label.pack(side="left", padx=10)
        ttk.Button(pbar, text="Limitaciones científicas", command=lambda: self.nb.select(TAB_LIMITS)).pack(side="right")

        ttk.Separator(self).pack(fill="x")
        self.progress = ProgressPanel(self, on_cancel=self.cancel_task)
        self.progress.pack(side="bottom", fill="x")
        ttk.Separator(self).pack(side="bottom", fill="x")

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ScrollableFrame(paned, width=300)
        paned.add(left, weight=0)
        self._build_controls(left.inner)
        self.nb = ttk.Notebook(paned)
        paned.add(self.nb, weight=1)

        # Visor + parcelas
        viewer_tab = ttk.PanedWindow(self.nb, orient="vertical")
        self.viewer = ImageViewer(viewer_tab, on_photo_selected=lambda n: self.show_photo(n),
                                  on_roi_drawn=self.on_roi_drawn, on_roi_selected=self.roi_select,
                                  on_roi_changed=self.on_roi_changed, on_roi_edit=self.roi_edit,
                                  on_roi_delete=self.roi_delete, get_settings=lambda: self.project.settings,
                                  viewer_max_side=int(self.config_data.get("viewer_max_side", 2500)))
        viewer_tab.add(self.viewer, weight=4)
        self.roi_panel = RoiPanel(viewer_tab, self)
        viewer_tab.add(self.roi_panel, weight=1)
        self.nb.add(viewer_tab, text="  Visor y parcelas  ")
        self.photos_view = PhotosView(self.nb, self)
        self.nb.add(self.photos_view, text="  Fotografías  ")
        self.field_view = FieldView(self.nb, self)
        self.nb.add(self.field_view, text="  Datos de campo  ")
        self.results_view = ResultsView(self.nb, self)
        self.nb.add(self.results_view, text="  Resultados  ")
        self.nb.add(self._build_qc_tab(), text="  Control de calidad  ")
        self.nb.add(self._build_limits_tab(), text="  Limitaciones  ")

    def _section(self, parent, title: str) -> ttk.LabelFrame:
        f = ttk.LabelFrame(parent, text=title, padding=8, style="Section.TLabelframe")
        f.pack(fill="x", padx=6, pady=5)
        return f

    def _build_controls(self, parent) -> None:
        def btn(frame, text, cmd, tip="", style=None, row=None, col=0, colspan=1):
            b = ttk.Button(frame, text=text, command=cmd, style=style or "TButton")
            b.grid(row=row, column=col, columnspan=colspan, sticky="we", padx=2, pady=2)
            if tip:
                ToolTip(b, tip)
            return b

        s = self._section(parent, "1. FOTOGRAFÍAS")
        s.columnconfigure((0, 1), weight=1)
        btn(s, "Importar fotos…", self.import_photos, "JPG, JPEG o PNG. Puede seleccionar varias a la vez. "
            "Los originales nunca se modifican.", row=0, colspan=2)
        self.photo_count = ttk.Label(s, text="0 imágenes cargadas", foreground="#555")
        self.photo_count.grid(row=1, column=0, columnspan=2, sticky="w")

        s = self._section(parent, "2. PARCELAS / ROIs")
        s.columnconfigure((0, 1, 2), weight=1)
        btn(s, "Crear parcela", self.roi_start_draw, "Dibuje un rectángulo sobre la foto en el visor", row=0, col=0)
        btn(s, "Editar", lambda: self._with_roi(self.roi_edit), row=0, col=1)
        btn(s, "Eliminar", lambda: self._with_roi(self.roi_delete), row=0, col=2)
        btn(s, "Duplicar", lambda: self._with_roi(self.roi_duplicate), row=1, col=0)
        btn(s, "Coordenadas…", self.roi_manual, "Introducir coordenadas manualmente", row=1, col=1, colspan=2)
        self.roi_count = ttk.Label(s, text="", foreground="#555")
        self.roi_count.grid(row=2, column=0, columnspan=3, sticky="w")

        s = self._section(parent, "3. ÍNDICES RGB")
        self.index_vars = {}
        row = ttk.Frame(s)
        row.pack(fill="x")
        for k in MAIN_INDICES:
            v = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(row, text=k, variable=v, command=self._on_index_toggle)
            cb.pack(side="left", padx=(0, 10))
            ToolTip(cb, f"{INDEX_DEFINITIONS[k].formula}\n{INDEX_DEFINITIONS[k].description}")
            self.index_vars[k] = v
        b = ttk.Button(row, text="ⓘ", width=3, command=self.show_formulas)
        b.pack(side="right")
        ToolTip(b, "Fórmulas, rangos y limitaciones de cada índice")
        exp = ttk.LabelFrame(s, text="Índices experimentales", padding=4)
        exp.pack(fill="x", pady=4)
        for k in EXPERIMENTAL_INDICES:
            v = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(exp, text=k, variable=v, command=self._on_index_toggle)
            cb.pack(side="left", padx=(0, 8))
            ToolTip(cb, f"EXPERIMENTAL — {INDEX_DEFINITIONS[k].formula}\n{INDEX_DEFINITIONS[k].limitations}")
            self.index_vars[k] = v
        ttk.Button(s, text="Opciones de análisis…", command=self.open_options).pack(fill="x", pady=2)
        self.options_label = ttk.Label(s, text="", foreground="#555", wraplength=260, justify="left")
        self.options_label.pack(anchor="w")
        self.analyze_btn = ttk.Button(s, text="▶  Analizar", style="Accent.TButton", command=self.analyze)
        self.analyze_btn.pack(fill="x", pady=(6, 2), ipady=4)
        self.analysis_label = ttk.Label(s, text="", foreground="#555", wraplength=260, justify="left")
        self.analysis_label.pack(anchor="w")

        s = self._section(parent, "4. DATOS DE CAMPO")
        s.columnconfigure(0, weight=1)
        btn(s, "Importar CSV Arduino…", self.import_field_csv, "CSV con columnas fecha,hora,parcela,lectura_adc,porcentaje_humedad\n(también parcela,medicion,humedad_raw)", row=0)
        btn(s, "Introducir observaciones…", self.add_observation, "Estado general (1–5) y descripción visual", row=1)
        btn(s, "Calibración del sensor…", lambda: self.nb.select(TAB_FIELD), "Opcional: RAW seco / RAW húmedo", row=2)
        self.field_label = ttk.Label(s, text="", foreground="#555", wraplength=260, justify="left")
        self.field_label.grid(row=3, column=0, sticky="w")

        s = self._section(parent, "5. RESULTADOS")
        s.columnconfigure((0, 1), weight=1)
        btn(s, "Estadísticas", self.show_statistics, "Tabla experimental por parcela y estadísticas por ROI", row=0, col=0)
        btn(s, "Correlaciones", self.show_correlations, "Pearson y Spearman (valor exploratorio)", row=0, col=1)
        btn(s, "Gráficas", self.make_graphs, "Genera y muestra todas las gráficas", row=1, col=0)
        btn(s, "Mapas", self.show_maps, "Mapas de índices de cada foto", row=1, col=1)
        btn(s, "Control de calidad", self.run_qc, row=2, col=0, colspan=2)

        s = self._section(parent, "6. EXPORTACIÓN")
        s.columnconfigure((0, 1), weight=1)
        btn(s, "Exportar CSV", self.export_csv, "Resultados completos, por parcela, correlaciones y datos de campo",
            row=0, col=0)
        btn(s, "Exportar Excel", self.export_xlsx, "Archivo .xlsx con hojas Resumen, Fotos, Parcelas, Campo, "
            "Correlaciones…", row=0, col=1)
        btn(s, "Generar informe", self.generate_report, "Informe HTML y Markdown", style="Accent.TButton", row=1,
            colspan=2)
        self.output_label = ttk.Label(s, text="", foreground="#555", wraplength=260, justify="left")
        self.output_label.grid(row=2, column=0, columnspan=2, sticky="w")
        btn(s, "Cambiar carpeta…", self.change_output_dir, row=3, col=0)
        btn(s, "Abrir carpeta", self.open_output_dir, row=3, col=1)

    def _build_qc_tab(self) -> ttk.Frame:
        f = ttk.Frame(self.nb, padding=8)
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Button(bar, text="Actualizar control de calidad", command=self.run_qc).pack(side="left")
        self.qc_text = tk.Text(f, wrap="word", font=("Segoe UI", 11), height=20)
        self.qc_text.pack(fill="both", expand=True, pady=6)
        self.qc_text.tag_configure("ok", foreground="#1e7b1e")
        self.qc_text.tag_configure("warning", foreground="#b07000")
        self.qc_text.tag_configure("error", foreground="#b00000")
        self.qc_text.tag_configure("info", foreground="#555")
        self.qc_text.tag_configure("title", font=("Segoe UI", 13, "bold"))
        return f

    def _build_limits_tab(self) -> ttk.Frame:
        f = ttk.Frame(self.nb, padding=8)
        txt = tk.Text(f, wrap="word", font=("Segoe UI", 11))
        sb = ttk.Scrollbar(f, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.tag_configure("h", font=("Segoe UI", 13, "bold"), foreground=COLOR_ACCENT)
        txt.insert("end", "LIMITACIONES\n\n", "h")
        for line in LIMITATIONS:
            txt.insert("end", f"• {line}\n\n")
        txt.insert("end", "QUÉ NO HACE ESTE PROGRAMA\n\n", "h")
        txt.insert("end", "• No calcula NDVI (las imágenes RGB no tienen banda NIR).\n"
                          "• No inventa datos ni rellena valores ausentes.\n"
                          "• No modifica las fotografías originales ni aplica correcciones sin permiso.\n"
                          "• No convierte RAW a humedad sin una calibración introducida por el usuario.\n"
                          "• No elimina outliers automáticamente ni oculta resultados negativos.\n"
                          "• No afirma que una planta esté enferma a partir de RGB ni causalidad a partir de una correlación.\n\n")
        txt.insert("end", "ÍNDICES RGB (fórmulas documentadas)\n\n", "h")
        txt.insert("end", index_documentation_text() + "\n")
        txt.configure(state="disabled")
        return f

    # ================================================================== refresco
    def refresh_all(self) -> None:
        p = self.project
        name = p.name + (" *" if p.dirty else "")
        path = f"  —  {p.path}" if p.path else "  —  (sin guardar)"
        self.project_label.configure(text=name + path)
        self.title(f"{APP_NAME} {APP_VERSION} — {p.name}" + (" [DEMO]" if p.is_demo else ""))
        self.demo_label.configure(text=f"  {DEMO_LABEL}  " if p.is_demo else "")
        for k, v in self.index_vars.items():
            v.set(k in p.settings.all_indices)
        self._refresh_options_label()
        names = p.photo_names()
        if self.current_photo not in names:
            self.current_photo = None
        self.viewer.set_photo_list(names, self.current_photo)
        n = len(names)
        self.photo_count.configure(text=f"{n} imagen cargada" if n == 1 else f"{n} imágenes cargadas")
        self.photos_view.refresh(p)
        self.field_view.refresh(p)
        nmeas, nobs = len(p.field_measurements), len(p.observations)
        self.field_label.configure(text=f"{nmeas} mediciones de humedad · {nobs} observaciones"
                                   + (" · calibración activa" if p.calibration.is_usable else ""))
        self.output_label.configure(text=f"Resultados en: {p.default_output_dir()}")
        last = p.results.get("last_analysis")
        txt = f"Último análisis: {last}" if last else "Todavía no analizado."
        if self.results_stale and last:
            txt += "\n⚠ Las parcelas o las opciones han cambiado: vuelva a pulsar Analizar."
        self.analysis_label.configure(text=txt)
        self.refresh_rois()
        self._refresh_results_tables()

    def _refresh_options_label(self) -> None:
        s = self.project.settings
        parts = ["solo vegetación" if s.only_vegetation else "todos los píxeles",
                 "escala común" if s.map_scale_mode == "common" else "escala automática",
                 "media" if s.aggregate_stat == "mean" else "mediana"]
        if s.preprocessing.get("downscale_max_side"):
            parts.append(f"reducción ≤{s.preprocessing['downscale_max_side']} px")
        self.options_label.configure(text="Opciones: " + ", ".join(parts))

    def refresh_rois(self) -> None:
        p = self.project
        all_rois = list(p.rois)
        shown = [r for r in all_rois if r.photo == self.current_photo] if self.roi_panel.only_current.get() else all_rois
        self.roi_panel.refresh(shown, self.selected_roi)
        self.viewer.set_rois(p.rois.for_photo(self.current_photo) if self.current_photo else [], self.selected_roi)
        self.roi_count.configure(text=f"{len(p.rois.parcel_ids())} parcelas · {len(all_rois)} ROIs")

    def _refresh_results_tables(self) -> None:
        roi_df = self.project.roi_results_df()
        try:
            self.combined = combine_results(self.project, roi_df, self.config_data)
        except Exception:  # noqa: BLE001
            log.exception("Error combinando resultados")
            self.combined = None
        self.results_view.show_tables(roi_df, self.combined)
        out = self._current_output()
        self.results_view.show_graph_files(out)
        self.results_view.show_map_files(out)

    def _mark_changed(self, stale: bool = False) -> None:
        self.project.touch()
        if stale and self.project.results.get("last_analysis"):
            self.results_stale = True
        self.refresh_all()

    # ================================================================== proyecto
    def _confirm_discard(self) -> bool:
        if not self.project.dirty:
            return True
        ans = messagebox.askyesnocancel("Proyecto modificado", "¿Desea guardar los cambios del proyecto actual?",
                                        parent=self)
        if ans is None:
            return False
        if ans:
            return self.save_project()
        return True

    def new_project(self) -> None:
        if self._busy() or not self._confirm_discard():
            return
        name = simpledialog.askstring("Nuevo proyecto", "Nombre del proyecto:", parent=self,
                                      initialvalue=f"TR_{_dt.date.today().isoformat()}")
        if not name:
            return
        self.project = Project(name=name.strip(), settings=AnalysisSettings.from_config(self.config_data))
        self.current_photo = self.selected_roi = None
        self.results_stale = False
        self.viewer.clear("Importe fotografías (sección 1).")
        self.refresh_all()
        self.progress.set_message(f"Proyecto '{name}' creado. Siguiente paso: importar fotografías.")

    def open_project(self, path: Path | None = None) -> None:
        if self._busy() or not self._confirm_discard():
            return
        if path is None:
            fn = filedialog.askopenfilename(parent=self, title="Abrir proyecto", initialdir=PROJECT_ROOT / "projects",
                                            filetypes=[("Proyecto TR RGB", "*.json"), ("Todos", "*.*")])
            if not fn:
                return
            path = Path(fn)
        try:
            proj = Project.load(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Abrir proyecto", f"No se pudo abrir el proyecto:\n{exc}", parent=self)
            return
        self.project = proj
        self.results_stale = False
        self.selected_roi = None
        missing = [p.name for p in proj.photos if not Path(p.path).exists()]
        self.current_photo = proj.photos[0].name if proj.photos else None
        self.refresh_all()
        if self.current_photo:
            self.show_photo(self.current_photo)
        else:
            self.viewer.clear("El proyecto no tiene fotografías.")
        if proj.is_demo:
            messagebox.showwarning("Proyecto DEMO", f"{DEMO_LABEL}\n\nEste proyecto contiene datos sintéticos "
                                   "generados por demo.py. No los use como resultados del TR.", parent=self)
        if missing:
            messagebox.showwarning("Fotos no encontradas", "No se encuentran estas fotografías (¿se han movido?):\n"
                                   + "\n".join(missing[:15]), parent=self)
        self.progress.set_message(f"Proyecto abierto: {path}")

    def save_project(self) -> bool:
        if self.project.path is None:
            return self.save_project_as()
        try:
            self.project.save()
        except OSError as exc:
            messagebox.showerror("Guardar", f"No se pudo guardar:\n{exc}", parent=self)
            return False
        self._autosave_rois()
        self.refresh_all()
        self.progress.set_message(f"Proyecto guardado: {self.project.path}")
        return True

    def save_project_as(self) -> bool:
        initial = PROJECT_ROOT / "projects"
        fn = filedialog.asksaveasfilename(parent=self, title="Guardar proyecto como", initialdir=initial,
                                          initialfile=f"{slugify(self.project.name)}.json", defaultextension=".json",
                                          filetypes=[("Proyecto TR RGB", "*.json")])
        if not fn:
            return False
        try:
            self.project.save(Path(fn))
        except OSError as exc:
            messagebox.showerror("Guardar", f"No se pudo guardar:\n{exc}", parent=self)
            return False
        self._autosave_rois()
        self.refresh_all()
        self.progress.set_message(f"Proyecto guardado: {self.project.path}")
        return True

    def _autosave_rois(self) -> None:
        """Guarda automáticamente las parcelas en JSON y CSV junto al proyecto."""
        p = self.project
        if p.path is not None:
            base = p.path.parent / f"{p.path.stem}_parcelas"
        else:
            base = PROJECT_ROOT / "projects" / "_autoguardado" / f"{slugify(p.name)}_parcelas"
        try:
            p.rois.save_json(base.with_suffix(".json"))
            p.rois.save_csv(base.with_suffix(".csv"))
        except OSError as exc:
            log.warning("No se pudieron autoguardar las parcelas: %s", exc)

    def on_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            if not messagebox.askyesno("Salir", "Hay un análisis en curso. ¿Cancelarlo y salir?", parent=self):
                return
            self._cancel.set()
        if not self._confirm_discard():
            return
        self.destroy()

    # ================================================================== fotos
    def import_photos(self) -> None:
        if self._busy():
            return
        exts = " ".join(f"*{e} *{e.upper()}" for e in SUPPORTED_EXTENSIONS)
        files = filedialog.askopenfilenames(parent=self, title="Importar fotografías", initialdir=PROJECT_ROOT / "input" / "fotos",
                                            filetypes=[("Imágenes JPG/PNG", exts), ("Todos", "*.*")])
        if not files:
            return
        demo_dir = (PROJECT_ROOT / "demo").resolve()
        if not self.project.is_demo and any(demo_dir in Path(f).resolve().parents or Path(f).name.startswith("DEMO_")
                                            for f in files):
            if not messagebox.askyesno("Datos de demostración", f"Algunas fotos parecen de la DEMO ({DEMO_LABEL}).\n"
                                       "No deben mezclarse con datos reales. ¿Importarlas igualmente?", parent=self,
                                       icon="warning"):
                return
        self.progress.set_message("Leyendo información de las fotografías…")
        self.update_idletasks()
        added, messages = self.project.add_photos(files, self.config_data.get("max_megapixels_warning", 40))
        if added and self.current_photo is None:
            self.current_photo = added[0].name
        self.refresh_all()
        if added:
            self.show_photo(self.current_photo)
        if messages:
            messagebox.showwarning("Importar fotografías", "\n".join(messages[:20]), parent=self)
        self.progress.set_message(f"{len(added)} fotografías añadidas. Siguiente paso: crear parcelas.")

    def show_photo(self, name: str | None, switch_tab: bool = False) -> None:
        if not name:
            return
        p = self.project.photo(name)
        if p is None:
            return
        self.current_photo = name
        if self.selected_roi and (r := self.project.rois.get(self.selected_roi)) and r.photo != name:
            self.selected_roi = None
        if not p.valid:
            self.viewer.clear(f"La imagen {name} no es válida:\n{p.error}")
        elif not Path(p.path).exists():
            self.viewer.clear(f"No se encuentra el archivo:\n{p.path}")
        else:
            self.config(cursor="watch")
            self.update_idletasks()
            self.viewer.load_image(p.path, name)
            self.config(cursor="")
        self.viewer.set_photo_list(self.project.photo_names(), name)
        self.refresh_rois()
        if switch_tab:
            self.nb.select(TAB_VIEWER)

    def edit_photo_date(self, name: str) -> None:
        p = self.project.photo(name)
        if p is None:
            return
        txt = simpledialog.askstring("Fecha de la fotografía", f"Fecha de {name}\n(AAAA-MM-DD o AAAA-MM-DD HH:MM; "
                                     "vacío = sin fecha):", initialvalue=p.date or "", parent=self)
        if txt is None:
            return
        txt = txt.strip()
        if txt:
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%Y %H:%M"):
                try:
                    parsed = _dt.datetime.strptime(txt, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                messagebox.showerror("Fecha", "Formato de fecha no reconocido.", parent=self)
                return
            txt = parsed.isoformat(sep=" ")
        self.project.set_photo_date(name, txt or None)
        self._mark_changed(stale=True)

    def remove_photo(self, name: str) -> None:
        n = len(self.project.rois.for_photo(name))
        if not messagebox.askyesno("Quitar foto", f"¿Quitar '{name}' del proyecto?\n"
                                   f"También se quitarán sus {n} parcelas/ROIs.\n\nEl archivo original NO se borra.",
                                   parent=self):
            return
        self.project.remove_photo(name)
        if self.current_photo == name:
            self.current_photo = self.project.photos[0].name if self.project.photos else None
            if self.current_photo:
                self.show_photo(self.current_photo)
            else:
                self.viewer.clear("No hay fotografías.")
        self._autosave_rois()
        self._mark_changed(stale=True)

    # ================================================================== parcelas
    def _image_sizes(self) -> dict[str, tuple[int, int]]:
        return {p.name: (p.width, p.height) for p in self.project.photos if p.valid}

    def _with_roi(self, func) -> None:
        if self.selected_roi is None:
            messagebox.showinfo("Parcelas", "Seleccione primero una parcela (clic sobre ella en el visor o en la lista).",
                                parent=self)
            return
        func(self.selected_roi)

    def roi_start_draw(self) -> None:
        if not self.current_photo:
            messagebox.showinfo("Crear parcela", "Primero importe y seleccione una fotografía.", parent=self)
            return
        self.nb.select(TAB_VIEWER)
        self.viewer.draw_mode.set(True)
        self.viewer._update_cursor()
        self.progress.set_message("Arrastre sobre la foto para dibujar la parcela (Esc para cancelar).")

    def on_roi_drawn(self, coords: tuple[int, int, int, int]) -> None:
        self._create_roi(coords)

    def roi_manual(self) -> None:
        if not self.project.photos:
            messagebox.showinfo("Coordenadas", "Primero importe fotografías.", parent=self)
            return
        self._create_roi(None)

    def _create_roi(self, coords) -> None:
        photo = self.current_photo or self.project.photos[0].name
        vals = ask_roi(self, "Nueva parcela", self.project.photo_names(), coords=coords, photo=photo,
                       suggested_id=self.project.rois.next_parcel_id(photo), image_sizes=self._image_sizes(),
                       min_side=int(self.config_data.get("roi_min_side", 5)),
                       min_area=int(self.config_data.get("roi_min_area", 100)))
        if not vals:
            return
        try:
            roi = self.project.rois.add(ROI(**vals))
        except ValueError as exc:
            messagebox.showerror("Parcela", str(exc), parent=self)
            return
        self.selected_roi = roi.uid
        if roi.photo != self.current_photo:
            self.show_photo(roi.photo)
        self._rois_changed(f"Parcela {roi.parcel_id} creada.")

    def roi_edit(self, uid: str) -> None:
        roi = self.project.rois.get(uid)
        if roi is None:
            return
        vals = ask_roi(self, f"Editar parcela {roi.parcel_id}", self.project.photo_names(), roi=roi,
                       image_sizes=self._image_sizes(), min_side=int(self.config_data.get("roi_min_side", 5)),
                       min_area=int(self.config_data.get("roi_min_area", 100)))
        if not vals:
            return
        try:
            self.project.rois.update(uid, **vals)
        except ValueError as exc:
            messagebox.showerror("Parcela", str(exc), parent=self)
            return
        self._rois_changed(f"Parcela {vals['parcel_id']} modificada.")

    def roi_rename(self, uid: str) -> None:
        roi = self.project.rois.get(uid)
        if roi is None:
            return
        dlg = FormDialog(self, "Renombrar parcela", [
            {"key": "parcel_id", "label": "ID parcela", "default": roi.parcel_id},
            {"key": "name", "label": "Nombre", "default": roi.name}],
            validate=lambda v: [] if v["parcel_id"] else ["El ID no puede estar vacío."])
        if not dlg.result:
            return
        try:
            self.project.rois.rename(uid, dlg.result["name"], dlg.result["parcel_id"])
        except ValueError as exc:
            messagebox.showerror("Renombrar", str(exc), parent=self)
            return
        self._rois_changed("Parcela renombrada.")

    def roi_duplicate(self, uid: str) -> None:
        try:
            new = self.project.rois.duplicate(uid)
        except (ValueError, KeyError) as exc:
            messagebox.showerror("Duplicar", str(exc), parent=self)
            return
        self.selected_roi = new.uid
        self._rois_changed(f"Parcela duplicada como {new.parcel_id}. Arrástrela a su posición.")

    def roi_copy_to_photo(self, uid: str) -> None:
        roi = self.project.rois.get(uid)
        if roi is None:
            return
        others = [n for n in self.project.photo_names() if n != roi.photo]
        if not others:
            messagebox.showinfo("Copiar", "No hay otras fotografías en el proyecto.", parent=self)
            return
        win = tk.Toplevel(self)
        win.title(f"Copiar parcela {roi.parcel_id} a otras fotos")
        win.transient(self)
        ttk.Label(win, text="Seleccione las fotos destino (Ctrl/Mayús para varias).\nSe usarán las mismas "
                            "coordenadas: revise que la parcela coincide en cada foto.", padding=8).pack()
        lb = tk.Listbox(win, selectmode="extended", width=50, height=min(15, len(others)))
        for n in others:
            lb.insert("end", n)
        lb.pack(padx=8, fill="both", expand=True)
        result: list[str] = []

        def ok():
            result.extend(others[i] for i in lb.curselection())
            win.destroy()

        ttk.Button(win, text="Copiar", command=ok).pack(pady=8)
        win.grab_set()
        self.wait_window(win)
        errors, done = [], 0
        for photo in result:
            try:
                self.project.rois.duplicate(uid, photo=photo)
                done += 1
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            messagebox.showwarning("Copiar", "\n".join(errors), parent=self)
        if done:
            self._rois_changed(f"Parcela {roi.parcel_id} copiada a {done} foto(s).")

    def roi_delete(self, uid: str) -> None:
        roi = self.project.rois.get(uid)
        if roi is None:
            return
        if not messagebox.askyesno("Eliminar parcela", f"¿Eliminar la parcela {roi.parcel_id} de {roi.photo}?",
                                   parent=self):
            return
        self.project.rois.delete(uid)
        self.selected_roi = None
        self._rois_changed(f"Parcela {roi.parcel_id} eliminada.")

    def roi_select(self, uid: str | None) -> None:
        self.selected_roi = uid
        roi = self.project.rois.get(uid) if uid else None
        if roi is not None and roi.photo != self.current_photo:
            self.show_photo(roi.photo)
        self.viewer.select_roi(uid)
        self.roi_panel.select(uid)
        if roi is not None:
            self.progress.set_message(f"Parcela {roi.parcel_id} ({roi.name}) — {roi.photo} — ({roi.x1}, {roi.y1}) – "
                                      f"({roi.x2}, {roi.y2}), {roi.width}×{roi.height} px")

    def on_roi_changed(self, uid: str, coords: tuple[int, int, int, int]) -> None:
        x1, y1, x2, y2 = coords
        try:
            self.project.rois.update(uid, x1=x1, y1=y1, x2=x2, y2=y2)
        except (ValueError, KeyError) as exc:
            messagebox.showerror("Parcela", str(exc), parent=self)
        self._rois_changed(f"Parcela movida/redimensionada: ({x1}, {y1}) – ({x2}, {y2})")

    def roi_import(self) -> None:
        fn = filedialog.askopenfilename(parent=self, title="Importar parcelas", initialdir=PROJECT_ROOT / "input" / "parcelas",
                                        filetypes=[("Parcelas", "*.json *.csv"), ("Todos", "*.*")])
        if not fn:
            return
        try:
            if fn.lower().endswith(".json"):
                mgr, errors = ROIManager.load_json(fn), []
            else:
                mgr, errors = ROIManager.load_csv(fn)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Importar parcelas", f"No se pudo leer el archivo:\n{exc}", parent=self)
            return
        added = 0
        photos = set(self.project.photo_names())
        for r in mgr:
            if r.photo not in photos:
                errors.append(f"{r.parcel_id}: la foto '{r.photo}' no está en el proyecto (no se importa)")
                continue
            try:
                self.project.rois.add(r)
                added += 1
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            messagebox.showwarning("Importar parcelas", "\n".join(errors[:20]), parent=self)
        self._rois_changed(f"{added} parcelas importadas.")

    def roi_export(self) -> None:
        fn = filedialog.asksaveasfilename(parent=self, title="Exportar parcelas", initialdir=PROJECT_ROOT / "input" / "parcelas",
                                          initialfile="parcelas.csv", defaultextension=".csv",
                                          filetypes=[("CSV", "*.csv"), ("JSON", "*.json")])
        if not fn:
            return
        if fn.lower().endswith(".json"):
            self.project.rois.save_json(fn)
        else:
            self.project.rois.save_csv(fn)
        self.progress.set_message(f"Parcelas exportadas a {fn}")

    def _rois_changed(self, message: str = "") -> None:
        self._autosave_rois()
        self._mark_changed(stale=True)
        if message:
            self.progress.set_message(message)

    # ================================================================== opciones
    def _on_index_toggle(self) -> None:
        s = self.project.settings
        s.main_indices = [k for k in MAIN_INDICES if self.index_vars[k].get()]
        s.experimental_indices = [k for k in EXPERIMENTAL_INDICES if self.index_vars[k].get()]
        self._mark_changed(stale=True)

    def show_formulas(self) -> None:
        show_text_window(self, "Índices RGB — fórmulas y limitaciones",
                         "Los índices se calculan con bandas RGB. NO hay banda NIR: NO se calcula NDVI.\n"
                         "Todos son indicadores RELATIVOS.\n\n" + index_documentation_text())

    def open_options(self) -> None:
        dlg = AnalysisOptionsDialog(self, self.project.settings)
        if dlg.result is None:
            return
        data = self.project.settings.to_dict()
        data.update(dlg.result)
        self.project.settings = AnalysisSettings.from_dict(data)
        self.viewer.invalidate_views()
        self._mark_changed(stale=True)

    # ================================================================== tareas en segundo plano
    def _busy(self) -> bool:
        if self._worker is not None and self._worker.is_alive():
            messagebox.showinfo("Procesando", "Espere a que termine la tarea en curso (o pulse Cancelar).", parent=self)
            return True
        return False

    def run_task(self, message: str, func, on_done) -> None:
        """Ejecuta ``func(progress, cancel)`` en un hilo sin congelar la interfaz."""
        if self._busy():
            return
        self._cancel = threading.Event()
        q = self._queue

        def progress(f: float, m: str) -> None:
            q.put(("progress", f, m))

        def target() -> None:
            try:
                res = func(progress, self._cancel)
                q.put(("done", on_done, res))
            except AnalysisCancelled:
                q.put(("cancelled",))
            except Exception as exc:  # noqa: BLE001
                log.exception("Error en tarea")
                q.put(("error", str(exc), traceback.format_exc()))

        self.progress.set_busy(True)
        self.progress.update_progress(0, message)
        self.analyze_btn.configure(state="disabled")
        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()
        self.after(100, self._poll_queue)

    def _poll_queue(self) -> None:
        finished = False
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.progress.update_progress(msg[1], msg[2])
                elif kind == "done":
                    finished = True
                    self._end_task()
                    msg[1](msg[2])
                elif kind == "cancelled":
                    finished = True
                    self._end_task()
                    self.progress.set_message("Tarea cancelada. No se han guardado resultados parciales en el proyecto.")
                elif kind == "error":
                    finished = True
                    self._end_task()
                    self.progress.set_message(f"Error: {msg[1]}")
                    messagebox.showerror("Error", f"{msg[1]}\n\nDetalles en logs/tr_rgb_analyzer.log", parent=self)
        except queue.Empty:
            pass
        if not finished:
            self.after(100, self._poll_queue)

    def _end_task(self) -> None:
        self.progress.set_busy(False)
        self.analyze_btn.configure(state="normal")

    def cancel_task(self) -> None:
        self._cancel.set()
        self.progress.set_message("Cancelando…")

    def _choose_output(self) -> Path | None:
        out = self.project.default_output_dir()
        if results_exist(out):
            choice = ask_overwrite(self, out)
            if choice is None:
                return None
            if choice == "new":
                out = new_run_dir(out)
        return out

    # ================================================================== análisis
    def analyze(self) -> None:
        p = self.project
        if self._busy():
            return
        if not p.photos:
            messagebox.showinfo("Analizar", "Primero importe fotografías (sección 1).", parent=self)
            return
        if not p.settings.all_indices:
            messagebox.showinfo("Analizar", "Seleccione al menos un índice RGB (sección 3).", parent=self)
            return
        if len(p.rois) == 0 and not messagebox.askyesno(
                "Analizar", "No hay parcelas definidas. Sólo se generarán mapas de índices, sin estadísticas por "
                "parcela.\n¿Continuar?", parent=self):
            return
        out = self._choose_output()
        if out is None:
            return
        clone = p.clone()
        config = self.config_data

        def work(progress, cancel):
            return run_full_analysis(clone, out, config, progress=progress, cancel=cancel,
                                     field_errors=clone.field_messages)

        def done(result: AnalysisResult):
            p.results = result.project.results
            p.results["qc_issues"] = [i.__dict__ for i in (result.images.issues if result.images else [])]
            p.results["last_output_dir"] = str(out)
            if out != p.default_output_dir():
                p.results["note"] = f"Último análisis guardado en carpeta nueva: {out}"
            p.touch()
            self.results_stale = False
            if p.path:
                p.save()
            self.refresh_all()
            self.results_view.show_graph_files(out)
            self.results_view.show_map_files(out)
            self._show_qc(result.qc)
            self.results_view.show_tab("parcelas")
            self.nb.select(TAB_RESULTS)
            qc = result.qc
            self.progress.update_progress(1, f"Análisis completado: {len(result.images.roi_df) if result.images else 0} "
                                             f"ROIs. Resultados en {out}")
            if qc and qc.has_errors:
                messagebox.showwarning("Control de calidad", f"El análisis terminó con {qc.n_errors} errores y "
                                       f"{qc.n_warnings} avisos. Revise la pestaña 'Control de calidad'.", parent=self)

        self.run_task("Procesando…", work, done)

    def _require_results(self) -> bool:
        if self.project.roi_results_df().empty and not self.project.field_measurements:
            messagebox.showinfo("Resultados", "Todavía no hay resultados. Pulse 'Analizar' (sección 3).", parent=self)
            return False
        return True

    def _current_output(self) -> Path:
        return Path(self.project.results.get("last_output_dir") or self.project.default_output_dir())

    def show_statistics(self) -> None:
        if not self._require_results():
            return
        self._refresh_results_tables()
        self.results_view.show_tab("parcelas")
        self.nb.select(TAB_RESULTS)

    def show_correlations(self) -> None:
        if not self._require_results():
            return
        self._refresh_results_tables()
        self.results_view.show_tab("correlaciones")
        self.nb.select(TAB_RESULTS)

    def show_maps(self) -> None:
        self.results_view.show_map_files(self._current_output())
        self.results_view.show_tab("mapas")
        self.nb.select(TAB_RESULTS)

    def make_graphs(self) -> None:
        if not self._require_results():
            return
        proj = self.project.clone()
        out = self._current_output()
        config = self.config_data

        def work(progress, cancel):
            progress(0.1, "Combinando datos…")
            combined = combine_results(proj, proj.roi_results_df(), config)
            progress(0.3, "Generando gráficas…")
            return generate_graphs(proj, combined, out, config)

        def done(files):
            self._refresh_results_tables()
            self.results_view.show_graph_files(out)
            self.results_view.show_tab("graficas")
            self.nb.select(TAB_RESULTS)
            self.progress.update_progress(1, f"{len(files)} gráficas generadas en {out / 'graphs'}")

        self.run_task("Generando gráficas…", work, done)

    def _build_result(self, out: Path) -> AnalysisResult:
        combined = combine_results(self.project, self.project.roi_results_df(), self.config_data)
        qc = self._qc_report(combined)
        return AnalysisResult(project=self.project, output_dir=out, images=None, combined=combined, qc=qc)

    def export_csv(self) -> None:
        if not self._require_results():
            return
        out = self._current_output()
        if (out / "exports" / "resultados_por_parcela.csv").exists():
            if not messagebox.askyesno("Exportar CSV", f"Ya existen archivos exportados en\n{out / 'exports'}\n\n"
                                       "¿Desea sobrescribir los resultados?", parent=self, icon="warning"):
                return
        try:
            files = export_all(self._build_result(out), self.config_data)
        except PermissionError as exc:
            messagebox.showerror("Exportar", f"No se pudo escribir (¿archivo abierto en Excel?):\n{exc}", parent=self)
            return
        self.progress.set_message(f"{len(files)} archivos exportados en {out}")
        if messagebox.askyesno("Exportar CSV", f"Se han exportado {len(files)} archivos.\n¿Abrir la carpeta?", parent=self):
            self._open(out / "exports")

    def export_xlsx(self) -> None:
        if not HAS_OPENPYXL:
            messagebox.showwarning("Excel", "Para exportar a Excel instale openpyxl:\n  pip install openpyxl\n\n"
                                   "Mientras tanto puede usar 'Exportar CSV'.", parent=self)
            return
        if not self._require_results():
            return
        out = self._current_output()
        path = out / "exports" / "TR_RGB_resultados.xlsx"
        if path.exists() and not messagebox.askyesno("Exportar Excel", f"{path.name} ya existe.\n¿Desea sobrescribir "
                                                     "los resultados?", parent=self, icon="warning"):
            return
        try:
            export_excel(self._build_result(out), path)
        except PermissionError:
            messagebox.showerror("Excel", f"No se pudo escribir {path.name}. ¿Está abierto en Excel?", parent=self)
            return
        self.progress.set_message(f"Excel exportado: {path}")
        if messagebox.askyesno("Exportar Excel", "Archivo Excel generado. ¿Abrirlo?", parent=self):
            self._open(path)

    def generate_report(self) -> None:
        if not self._require_results():
            return
        out = self._current_output()
        if (out / "reports" / "informe.html").exists():
            if not messagebox.askyesno("Generar informe", "Ya existe un informe en esta carpeta.\n¿Desea sobrescribir "
                                       "los resultados?", parent=self, icon="warning"):
                return
        proj = self.project.clone()
        config = self.config_data

        def work(progress, cancel):
            progress(0.1, "Combinando datos…")
            combined = combine_results(proj, proj.roi_results_df(), config)
            qc = self._qc_report(combined, proj)
            progress(0.3, "Generando gráficas…")
            graphs_ = generate_graphs(proj, combined, out, config)
            maps = sorted((out / "indices").glob("*.png")) + sorted((out / "rois").glob("*.png"))
            from src.pipeline import ImageAnalysisOutput

            images = ImageAnalysisOutput(roi_df=proj.roi_results_df(), image_df=proj.image_summary_df(), map_files=maps)
            result = AnalysisResult(project=proj, output_dir=out, images=images, combined=combined,
                                    graph_files=graphs_, qc=qc)
            progress(0.7, "Exportando tablas…")
            result.export_files = export_all(result, config)
            progress(0.85, "Escribiendo informe…")
            result.report_files = write_reports(result, config)
            meta = build_metadata(proj, config, out, None, maps + graphs_ + result.export_files + result.report_files, qc)
            write_json(out / "analysis_metadata.json", meta)
            write_json(out / "reports" / "analysis_metadata.json", meta)
            return result

        def done(result):
            self.results_view.show_graph_files(out)
            self.progress.update_progress(1, f"Informe generado: {result.report_files[0]}")
            if messagebox.askyesno("Informe", f"Informe generado:\n{result.report_files[0]}\n\n¿Abrirlo ahora?", parent=self):
                self._open(result.report_files[0])

        self.run_task("Generando informe…", work, done)

    # ================================================================== control de calidad
    def _qc_report(self, combined=None, project: Project | None = None):
        p = project or self.project
        issues = [QCItem(**d) for d in p.results.get("qc_issues", [])]
        combined = combined or combine_results(p, p.roi_results_df(), self.config_data)
        return build_qc_report(p.photo_dicts(), list(p.rois), p.roi_results_df(), combined.field_df,
                               combined.observations_df, issues, p.field_messages,
                               int(self.config_data.get("roi_min_side", 5)), int(self.config_data.get("roi_min_area", 100)))

    def run_qc(self) -> None:
        self._show_qc(self._qc_report())
        self.nb.select(TAB_QC)

    def _show_qc(self, qc) -> None:
        t = self.qc_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", "CONTROL DE CALIDAD\n\n", "title")
        if qc is None:
            t.insert("end", "Sin datos.\n", "info")
        else:
            for item in qc.items:
                t.insert("end", item.text() + "\n", item.level)
            t.insert("end", f"\nResumen: {qc.n_errors} errores, {qc.n_warnings} avisos.\n", "info")
        if self.results_stale:
            t.insert("end", "\n⚠ Las parcelas u opciones han cambiado desde el último análisis.\n", "warning")
        t.configure(state="disabled")

    # ================================================================== datos de campo
    def import_field_csv(self) -> None:
        fn = filedialog.askopenfilename(parent=self, title="Importar CSV del Arduino",
                                        initialdir=PROJECT_ROOT / "input" / "datos_campo",
                                        filetypes=[("CSV", "*.csv *.txt"), ("Todos", "*.*")])
        if not fn:
            return
        res = read_field_csv(fn)
        if not res.ok:
            messagebox.showerror("CSV del Arduino", "No se han podido importar datos:\n\n" + "\n".join(res.errors + res.warnings[:10]),
                                 parent=self)
            return
        p = self.project
        if p.field_measurements and not messagebox.askyesno(
                "CSV del Arduino", "El proyecto ya tiene mediciones de humedad.\n¿Reemplazarlas por las del nuevo archivo?",
                parent=self):
            return
        p.field_measurements = measurements_to_records(res.df)
        p.field_source = fn
        p.field_messages = [f"{Path(fn).name}: {w}" for w in res.warnings]
        unknown = sorted(set(res.df["parcela"]) - set(p.rois.parcel_ids()))
        self._mark_changed()
        self.nb.select(TAB_FIELD)
        msg = f"{len(res.df)} mediciones importadas de {res.df['parcela'].nunique()} parcelas."
        if res.warnings:
            msg += "\n\nAvisos:\n" + "\n".join(res.warnings[:10])
        if unknown and len(p.rois):
            msg += f"\n\nParcelas del CSV sin ROI en las fotos: {', '.join(unknown)}"
        messagebox.showinfo("CSV del Arduino", msg, parent=self)

    def clear_field_data(self) -> None:
        if self.project.field_measurements and messagebox.askyesno(
                "Datos de humedad", "¿Quitar las mediciones de humedad del proyecto?\n(El archivo CSV no se borra.)", parent=self):
            self.project.field_measurements = []
            self.project.field_source = ""
            self.project.field_messages = []
            self._mark_changed()

    def set_calibration(self, enabled: bool, dry: str, wet: str) -> None:
        def num(v):
            v = str(v).strip().replace(",", ".")
            return float(v) if v else None

        try:
            cal = SensorCalibration(enabled=enabled, raw_dry=num(dry), raw_wet=num(wet))
        except ValueError:
            messagebox.showerror("Calibración", "Los valores deben ser numéricos.", parent=self)
            return
        if enabled:
            errs = cal.validate()
            if errs:
                messagebox.showerror("Calibración", "\n".join(errs), parent=self)
                return
        self.project.calibration = cal
        self._mark_changed()
        self.progress.set_message("Calibración " + ("aplicada (estimación, no humedad volumétrica universal)."
                                                    if enabled else "desactivada: sólo se usan valores RAW."))

    def add_observation(self, parcela: str | None = None) -> None:
        p = self.project
        parcels = p.all_parcels()
        existing = next((o for o in p.observations if o.parcela == parcela), None) if parcela else None
        scale = [f"{k} = {v}" for k, v in VISUAL_SCALE.items()]
        default_state = next((s for s in scale if existing and existing.estado_general == int(s[0])), "")
        fields = [
            {"key": "parcela", "label": "Parcela *", "type": "choice", "options": parcels, "strict": False,
             "default": parcela or (parcels[0] if parcels else "")},
            {"key": "estado", "label": "Estado general", "type": "choice", "options": [""] + scale, "default": default_state},
            {"key": "coloracion", "label": "Coloración", "default": existing.coloracion if existing else ""},
            {"key": "densidad", "label": "Densidad", "default": existing.densidad if existing else ""},
            {"key": "estres_visible", "label": "Estrés visible", "default": existing.estres_visible if existing else ""},
            {"key": "uniformidad", "label": "Uniformidad", "default": existing.uniformidad if existing else ""},
            {"key": "observaciones", "label": "Observaciones", "type": "text", "default": existing.observaciones if existing else ""},
            {"key": "fecha", "label": "Fecha observación", "default": existing.fecha if existing else _dt.date.today().isoformat()},
        ]
        dlg = FormDialog(self, "Observaciones de la parcela", fields,
                         validate=lambda v: [] if v["parcela"] else ["Indique la parcela."],
                         intro="Valoración visual (subjetiva) del estado general de la vegetación. "
                               "Si ya existe una observación para la parcela, se reemplaza.")
        if not dlg.result:
            return
        v = dlg.result
        estado = int(v["estado"][0]) if v.get("estado") else None
        obs = VisualObservation(parcela=v["parcela"], estado_general=estado, coloracion=v["coloracion"],
                                densidad=v["densidad"], estres_visible=v["estres_visible"], uniformidad=v["uniformidad"],
                                observaciones=v["observaciones"], fecha=v["fecha"])
        p.set_observation(obs)
        self._mark_changed()
        self.nb.select(TAB_FIELD)

    def delete_observation(self, parcela: str) -> None:
        if messagebox.askyesno("Observaciones", f"¿Eliminar la observación de la parcela {parcela}?", parent=self):
            self.project.delete_observation(parcela)
            self._mark_changed()

    def import_observations(self) -> None:
        fn = filedialog.askopenfilename(parent=self, title="Importar observaciones",
                                        initialdir=PROJECT_ROOT / "input" / "datos_campo",
                                        filetypes=[("CSV", "*.csv"), ("Todos", "*.*")])
        if not fn:
            return
        obs, errors = read_observations_csv(fn)
        for o in obs:
            self.project.set_observation(o)
        self._mark_changed()
        msg = f"{len(obs)} observaciones importadas."
        if errors:
            msg += "\n\nErrores:\n" + "\n".join(errors[:15])
        messagebox.showinfo("Observaciones", msg, parent=self)

    # ================================================================== carpeta de salida
    def change_output_dir(self) -> None:
        d = filedialog.askdirectory(parent=self, title="Carpeta de resultados", initialdir=self.project.default_output_dir())
        if d:
            self.project.output_dir = d
            self.project.results.pop("last_output_dir", None)
            self._mark_changed()

    def open_output_dir(self) -> None:
        out = self._current_output()
        ensure_output_dirs(out)
        self._open(out)

    def _open(self, path: Path) -> None:
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                import webbrowser

                webbrowser.open(Path(path).as_uri())
        except OSError as exc:
            messagebox.showerror("Abrir", f"No se pudo abrir {path}:\n{exc}", parent=self)
