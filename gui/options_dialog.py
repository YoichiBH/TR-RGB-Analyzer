"""Diálogo de opciones de análisis (preprocesamiento, máscara, escala, estadístico)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from src.indices import EXG_MODES, VEGETATION_MASK_LABEL, VEGETATION_METHODS

from .widgets import ToolTip, _center


class AnalysisOptionsDialog(tk.Toplevel):
    """Edita una copia de los ajustes; ``self.result`` es un dict o ``None``."""

    def __init__(self, master, settings) -> None:
        super().__init__(master)
        self.title("Opciones de análisis")
        self.transient(master)
        self.resizable(False, False)
        self.result: dict | None = None
        pp = dict(settings.preprocessing)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        # Preprocesamiento
        f = ttk.LabelFrame(body, text="Preprocesamiento (ninguna corrección se aplica sin su permiso)", padding=8)
        f.pack(fill="x", pady=4)
        self.normalize = tk.BooleanVar(value=pp.get("normalize", True))
        self.ex_sat = tk.BooleanVar(value=pp.get("exclude_saturated", False))
        self.ex_dark = tk.BooleanVar(value=pp.get("exclude_dark", False))
        self.dark_thr = tk.StringVar(value=str(pp.get("dark_threshold", 10)))
        self.ex_transp = tk.BooleanVar(value=pp.get("exclude_transparent", True))
        ds = pp.get("downscale_max_side")
        self.downscale = tk.BooleanVar(value=bool(ds))
        self.downscale_px = tk.StringVar(value=str(ds or 3000))
        ttk.Label(f, text="Siempre: conversión a RGB, conversión a float y exclusión de valores no finitos.",
                  foreground="#555").grid(row=0, column=0, columnspan=3, sticky="w")
        cb = ttk.Checkbutton(f, text="Normalizar 0–1 (valor/255) — recomendado", variable=self.normalize)
        cb.grid(row=1, column=0, columnspan=3, sticky="w")
        ToolTip(cb, "GLI, VARI y NGRDI no cambian; ExG/ExR quedan en una escala comparable entre cámaras.")
        ttk.Checkbutton(f, text="Excluir píxeles saturados (algún canal = 255)", variable=self.ex_sat).grid(
            row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(f, text="Excluir píxeles muy oscuros; media RGB menor que", variable=self.ex_dark).grid(
            row=3, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.dark_thr, width=6).grid(row=3, column=1, sticky="w")
        ttk.Label(f, text="(0–255)").grid(row=3, column=2, sticky="w")
        ttk.Checkbutton(f, text="Excluir píxeles transparentes (PNG con canal alfa)", variable=self.ex_transp).grid(
            row=4, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(f, text="Reducir resolución para ahorrar memoria; lado mayor ≤", variable=self.downscale).grid(
            row=5, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.downscale_px, width=6).grid(row=5, column=1, sticky="w")
        ttk.Label(f, text="px (sólo si el ordenador va justo de RAM)").grid(row=5, column=2, sticky="w")

        # ExG
        f = ttk.LabelFrame(body, text="Escala RGB para ExG / ExR / ExGR", padding=8)
        f.pack(fill="x", pady=4)
        self.exg_mode = tk.StringVar(value=settings.exg_mode)
        for key, text in EXG_MODES.items():
            ttk.Radiobutton(f, text=text, value=key, variable=self.exg_mode).pack(anchor="w")

        # Vegetación
        f = ttk.LabelFrame(body, text=VEGETATION_MASK_LABEL, padding=8)
        f.pack(fill="x", pady=4)
        self.veg_method = tk.StringVar(value=settings.vegetation_method)
        self.veg_thr = tk.StringVar(value=f"{settings.vegetation_threshold:g}")
        self.only_veg = tk.BooleanVar(value=settings.only_vegetation)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Método:").pack(side="left")
        for key, text in VEGETATION_METHODS.items():
            ttk.Radiobutton(row, text=text, value=key, variable=self.veg_method).pack(side="left", padx=4)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Umbral ExG:").pack(side="left")
        ttk.Entry(row, textvariable=self.veg_thr, width=8).pack(side="left", padx=4)
        ttk.Label(row, text="(típico 0.05–0.15; revise la máscara en el visor)", foreground="#555").pack(side="left")
        ttk.Radiobutton(f, text="Analizar todos los píxeles", value=False, variable=self.only_veg).pack(anchor="w")
        ttk.Radiobutton(f, text="Analizar solo píxeles clasificados como vegetación", value=True,
                        variable=self.only_veg).pack(anchor="w")
        ttk.Label(f, text="La máscara es aproximada: no identifica perfectamente la vegetación. La cobertura se "
                          "presenta como 'cobertura vegetal estimada mediante RGB'.", foreground="#a06000",
                  wraplength=520, justify="left").pack(anchor="w")

        # Mapas y agregación
        f = ttk.LabelFrame(body, text="Mapas de índices y tabla por parcela", padding=8)
        f.pack(fill="x", pady=4)
        self.scale_mode = tk.StringVar(value=settings.map_scale_mode)
        ttk.Radiobutton(f, text="Escala común: todas las fotos usan el mismo rango (recomendado para comparar)",
                        value="common", variable=self.scale_mode).pack(anchor="w")
        ttk.Radiobutton(f, text="Escala automática: cada foto se adapta a sus propios valores",
                        value="auto", variable=self.scale_mode).pack(anchor="w")
        self.agg = tk.StringVar(value=settings.aggregate_stat)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Valor de cada ROI en la tabla por parcela:").pack(side="left")
        ttk.Radiobutton(row, text="media", value="mean", variable=self.agg).pack(side="left", padx=4)
        ttk.Radiobutton(row, text="mediana (más robusta para VARI)", value="median", variable=self.agg).pack(side="left")

        self.err = ttk.Label(body, text="", foreground="#b00000")
        self.err.pack(anchor="w")
        btns = ttk.Frame(body)
        btns.pack(anchor="e", pady=(8, 0))
        ttk.Button(btns, text="Aceptar", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="left")
        self.grab_set()
        _center(self, master)
        self.wait_window(self)

    def _ok(self) -> None:
        try:
            dark = int(float(self.dark_thr.get()))
            thr = float(self.veg_thr.get().replace(",", "."))
            ds = int(float(self.downscale_px.get())) if self.downscale.get() else None
            if ds is not None and ds < 200:
                raise ValueError("El lado mayor mínimo es 200 px.")
            if not 0 <= dark <= 255:
                raise ValueError("El umbral de oscuridad debe estar entre 0 y 255.")
        except ValueError as exc:
            self.err.configure(text=f"Valor no válido: {exc}")
            return
        self.result = {
            "preprocessing": {"normalize": self.normalize.get(), "exclude_saturated": self.ex_sat.get(),
                              "exclude_dark": self.ex_dark.get(), "dark_threshold": dark,
                              "exclude_transparent": self.ex_transp.get(), "downscale_max_side": ds},
            "exg_mode": self.exg_mode.get(),
            "vegetation_method": self.veg_method.get(),
            "vegetation_threshold": thr,
            "only_vegetation": bool(self.only_veg.get()),
            "map_scale_mode": self.scale_mode.get(),
            "aggregate_stat": self.agg.get(),
        }
        self.destroy()
