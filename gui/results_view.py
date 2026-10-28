"""Pestaña "Resultados": tablas, correlaciones, gráficas y mapas."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageTk

from src.correlation import CAUSALITY_NOTICE

from .widgets import FONT, DataFrameTable


class PngBrowser(ttk.Frame):
    """Lista de imágenes PNG con vista previa ajustada al espacio disponible."""

    def __init__(self, master, empty_text: str = "No hay imágenes. Ejecute el análisis.") -> None:
        super().__init__(master)
        self.files: list[Path] = []
        self.folder: Path | None = None
        left = ttk.Frame(self)
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, width=42, exportselection=False, font=("Segoe UI", 9))
        sb = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="y", expand=True)
        sb.pack(side="left", fill="y")
        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        bar = ttk.Frame(right)
        bar.pack(fill="x")
        ttk.Button(bar, text="Abrir imagen", command=self._open_file).pack(side="left", padx=2)
        ttk.Button(bar, text="Abrir carpeta", command=self._open_folder).pack(side="left", padx=2)
        self.canvas = tk.Canvas(right, background="white", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.empty_text = empty_text
        self._img = None
        self._pil: Image.Image | None = None
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._show())
        self.canvas.bind("<Configure>", lambda e: self._draw())

    def set_files(self, files: list[Path], folder: Path | None = None) -> None:
        self.files = [Path(f) for f in files if Path(f).exists()]
        self.folder = folder
        self.listbox.delete(0, "end")
        for f in self.files:
            self.listbox.insert("end", f.name)
        if self.files:
            self.listbox.selection_set(0)
        self._show()

    def select_name(self, name: str) -> None:
        for i, f in enumerate(self.files):
            if f.name == name:
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(i)
                self.listbox.see(i)
                self._show()
                return

    def _current(self) -> Path | None:
        sel = self.listbox.curselection()
        return self.files[sel[0]] if sel else None

    def _show(self) -> None:
        f = self._current()
        self._pil = None
        if f is not None:
            try:
                with Image.open(f) as im:
                    self._pil = im.convert("RGB")
            except OSError:
                self._pil = None
        self._draw()

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        if self._pil is None:
            c.create_text(20, 20, anchor="nw", text=self.empty_text, fill="#777", font=FONT)
            return
        cw, ch = max(c.winfo_width(), 10), max(c.winfo_height(), 10)
        img = self._pil.copy()
        img.thumbnail((cw, ch), Image.Resampling.LANCZOS)
        self._img = ImageTk.PhotoImage(img)
        c.create_image(cw // 2, ch // 2, image=self._img, anchor="center")

    def _open_file(self) -> None:
        f = self._current()
        if f is not None and hasattr(os, "startfile"):
            os.startfile(f)  # type: ignore[attr-defined]

    def _open_folder(self) -> None:
        folder = self.folder or (self.files[0].parent if self.files else None)
        if folder is not None and folder.exists() and hasattr(os, "startfile"):
            os.startfile(folder)  # type: ignore[attr-defined]


class ResultsView(ttk.Frame):
    def __init__(self, master, app) -> None:
        super().__init__(master, padding=4)
        self.app = app
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        # Tabla experimental
        f = ttk.Frame(self.nb, padding=4)
        self.nb.add(f, text="Tabla experimental (por parcela)")
        ttk.Label(f, text="Una fila por parcela: índices RGB (relativos), cobertura RGB estimada, humedad RAW, humedad "
                          "estimada (si hay calibración) y estado general. Columnas EXP_ = índices experimentales.",
                  foreground="#555", wraplength=1000, justify="left").pack(anchor="w")
        self.parcel_table = DataFrameTable(f, height=14, decimals=4)
        self.parcel_table.pack(fill="both", expand=True)

        # Por ROI
        f = ttk.Frame(self.nb, padding=4)
        self.nb.add(f, text="Estadísticas por ROI")
        ttk.Label(f, text="Estadísticas a resolución completa de cada parcela en cada foto: mean, median, std, min, max, "
                          "percentiles y n (número de píxeles). Sin eliminación de outliers.",
                  foreground="#555").pack(anchor="w")
        self.roi_table = DataFrameTable(f, height=14, decimals=4)
        self.roi_table.pack(fill="both", expand=True)

        # Comparación entre fotos
        f = ttk.Frame(self.nb, padding=4)
        self.nb.add(f, text="Comparación entre fotografías")
        ttk.Label(f, text="Variabilidad de cada parcela entre fotos (media, sd, CV) y cambio entre la primera y la última "
                          "foto cuando hay fechas.", foreground="#555").pack(anchor="w")
        self.comp_table = DataFrameTable(f, height=14, decimals=4)
        self.comp_table.pack(fill="both", expand=True)

        # Correlaciones
        f = ttk.Frame(self.nb, padding=4)
        self.nb.add(f, text="Correlaciones")
        ttk.Label(f, text="⚠ " + CAUSALITY_NOTICE, foreground="#b06000").pack(anchor="w")
        self.corr_table = DataFrameTable(f, height=8, decimals=3)
        self.corr_table.pack(fill="both", expand=True)
        ttk.Label(f, text="Interpretación (descriptiva y prudente):", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))
        self.corr_text = tk.Text(f, height=10, wrap="word", font=FONT)
        self.corr_text.pack(fill="both", expand=True)

        # Gráficas y mapas
        self.graphs = PngBrowser(self.nb, "No hay gráficas. Pulse 'Gráficas' en la sección 5.")
        self.nb.add(self.graphs, text="Gráficas")
        self.maps = PngBrowser(self.nb, "No hay mapas. Ejecute el análisis (sección 3).")
        self.nb.add(self.maps, text="Mapas de índices")

    def show_tab(self, name: str) -> None:
        index = {"parcelas": 0, "roi": 1, "comparacion": 2, "correlaciones": 3, "graficas": 4, "mapas": 5}[name]
        self.nb.select(index)

    def show_tables(self, roi_df, combined) -> None:
        self.roi_table.set_dataframe(roi_df, empty_text="Ejecute el análisis (sección 3)")
        if combined is None:
            for t in (self.parcel_table, self.comp_table, self.corr_table):
                t.set_dataframe(None)
            self._set_corr_text("")
            return
        self.parcel_table.set_dataframe(combined.parcel_table)
        self.comp_table.set_dataframe(combined.comparison, empty_text="Hace falta la misma parcela en varias fotos")
        cols = ["variable_x", "variable_y", "n", "pearson_r", "pearson_p", "spearman_rho", "spearman_p", "p_method", "warning"]
        corr = combined.correlations_df
        if not combined.exp_correlations_df.empty:
            import pandas as pd

            exp = combined.exp_correlations_df.copy()
            exp["variable_y"] = exp["variable_y"].astype(str)
            corr = pd.concat([corr, exp], ignore_index=True)
        self.corr_table.set_dataframe(corr, columns=cols)
        lines = [f"• {r.interpretation}" + (f"\n    ⚠ {r.warning}" if r.warning else "")
                 for r in combined.correlation_results]
        if combined.exp_correlation_results:
            lines.append("\nÍndices EXPERIMENTALES (fuera del análisis principal):")
            lines += [f"• {r.interpretation}" for r in combined.exp_correlation_results]
        lines.append("\nNota: en muchos sensores capacitivos un RAW más alto indica suelo más seco; compruebe el "
                     "sentido de su sensor antes de interpretar el signo de las correlaciones con la humedad RAW.")
        self._set_corr_text("\n".join(lines))

    def _set_corr_text(self, text: str) -> None:
        self.corr_text.configure(state="normal")
        self.corr_text.delete("1.0", "end")
        self.corr_text.insert("1.0", text)
        self.corr_text.configure(state="disabled")

    def show_graph_files(self, output_dir: Path) -> None:
        gdir = Path(output_dir) / "graphs"
        files = sorted(gdir.glob("*.png")) if gdir.exists() else []
        self.graphs.set_files(files, gdir)

    def show_map_files(self, output_dir: Path) -> None:
        idir, rdir = Path(output_dir) / "indices", Path(output_dir) / "rois"
        files = (sorted(idir.glob("*.png")) if idir.exists() else []) + (sorted(rdir.glob("*.png")) if rdir.exists() else [])
        self.maps.set_files(files, idir)
