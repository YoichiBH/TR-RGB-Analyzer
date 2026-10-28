"""Pestaña "Datos de campo": humedad (Arduino), calibración y observaciones visuales."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from src.field_data import CALIBRATION_NOTICE, summarize_moisture

from .widgets import DataFrameTable, ToolTip


class FieldView(ttk.Frame):
    def __init__(self, master, app) -> None:
        super().__init__(master, padding=6)
        self.app = app
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True)

        # -- Humedad -----------------------------------------------------------
        top = ttk.LabelFrame(paned, text="Humedad del suelo — lecturas RAW del sensor capacitivo (Arduino)", padding=6)
        paned.add(top, weight=3)
        bar = ttk.Frame(top)
        bar.pack(fill="x")
        ttk.Button(bar, text="Importar CSV Arduino…", command=app.import_field_csv).pack(side="left", padx=2)
        b = ttk.Button(bar, text="Quitar datos de humedad", command=app.clear_field_data)
        b.pack(side="left", padx=2)
        ToolTip(b, "Quita las mediciones del proyecto (el archivo CSV original no se borra).")
        self.source_label = ttk.Label(bar, text="", foreground="#555")
        self.source_label.pack(side="left", padx=8)
        ttk.Label(top, text="Formato esperado:  fecha,hora,parcela,lectura_adc,porcentaje_humedad   (separador , o ;; "
                            "también se acepta parcela,medicion,humedad_raw). El análisis usa lectura_adc (RAW); "
                            "porcentaje_humedad del Arduino se conserva aparte.", foreground="#555",
                  wraplength=1000, justify="left").pack(anchor="w", pady=2)
        tables = ttk.Frame(top)
        tables.pack(fill="both", expand=True)
        left = ttk.Frame(tables)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Mediciones", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.meas_table = DataFrameTable(left, height=7, decimals=1)
        self.meas_table.pack(fill="both", expand=True)
        right = ttk.Frame(tables)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        ttk.Label(right, text="Resumen por parcela (media, mediana, desviación estándar, mín., máx., n)",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.sum_table = DataFrameTable(right, height=7, decimals=2)
        self.sum_table.pack(fill="both", expand=True)
        self.msg_label = ttk.Label(top, text="", foreground="#a06000", wraplength=900, justify="left")
        self.msg_label.pack(anchor="w")

        # -- Calibración ------------------------------------------------------------
        cal = ttk.LabelFrame(paned, text="Calibración del sensor (opcional)", padding=6)
        paned.add(cal, weight=0)
        self.cal_enabled = tk.BooleanVar()
        self.cal_dry = tk.StringVar()
        self.cal_wet = tk.StringVar()
        row = ttk.Frame(cal)
        row.pack(fill="x")
        ttk.Checkbutton(row, text="Calcular humedad estimada (%)", variable=self.cal_enabled).pack(side="left")
        ttk.Label(row, text="   RAW suelo seco:").pack(side="left")
        ttk.Entry(row, textvariable=self.cal_dry, width=9).pack(side="left")
        ttk.Label(row, text="   RAW suelo húmedo:").pack(side="left")
        ttk.Entry(row, textvariable=self.cal_wet, width=9).pack(side="left")
        ttk.Button(row, text="Aplicar", command=self._apply_calibration).pack(side="left", padx=8)
        ttk.Label(cal, text="humedad_% = (RAW − RAW_seco) / (RAW_húmedo − RAW_seco) × 100, limitada a 0–100 %. "
                            "El valor RAW original se conserva siempre.", foreground="#555").pack(anchor="w")
        ttk.Label(cal, text="⚠ " + CALIBRATION_NOTICE, foreground="#b06000").pack(anchor="w")

        # -- Observaciones -------------------------------------------------------------
        obs = ttk.LabelFrame(paned, text="Estado general de la vegetación (valoración visual)", padding=6)
        paned.add(obs, weight=2)
        bar = ttk.Frame(obs)
        bar.pack(fill="x")
        ttk.Button(bar, text="Introducir observaciones…", command=app.add_observation).pack(side="left", padx=2)
        ttk.Button(bar, text="Editar", command=self._edit_obs).pack(side="left", padx=2)
        ttk.Button(bar, text="Eliminar", command=self._delete_obs).pack(side="left", padx=2)
        ttk.Button(bar, text="Importar CSV de observaciones…", command=app.import_observations).pack(side="left", padx=2)
        ttk.Label(bar, text="Escala: 1 = muy pobre · 2 = pobre · 3 = intermedio · 4 = bueno · 5 = muy bueno",
                  foreground="#555").pack(side="left", padx=10)
        self.obs_table = DataFrameTable(obs, height=6, decimals=0)
        self.obs_table.pack(fill="both", expand=True)
        self.obs_table.tree.bind("<Double-1>", lambda e: self._edit_obs())

    def refresh(self, project) -> None:
        df = project.field_df()
        self.meas_table.set_dataframe(df, empty_text="Importe el CSV del Arduino")
        self.sum_table.set_dataframe(summarize_moisture(df, project.calibration))
        self.source_label.configure(text=f"Origen: {project.field_source}" if project.field_source else "")
        self.msg_label.configure(text="\n".join(project.field_messages[:8]))
        c = project.calibration
        self.cal_enabled.set(c.enabled)
        self.cal_dry.set("" if c.raw_dry is None else f"{c.raw_dry:g}")
        self.cal_wet.set("" if c.raw_wet is None else f"{c.raw_wet:g}")
        self.obs_table.set_dataframe(project.observations_df(), empty_text="Sin observaciones")

    def _apply_calibration(self) -> None:
        self.app.set_calibration(self.cal_enabled.get(), self.cal_dry.get(), self.cal_wet.get())

    def _selected_obs(self) -> str | None:
        rec = self.obs_table.selected_record()
        if rec is None:
            from tkinter import messagebox

            messagebox.showinfo("Observaciones", "Seleccione una observación de la lista.", parent=self)
            return None
        return str(rec["parcela"])

    def _edit_obs(self) -> None:
        p = self._selected_obs()
        if p:
            self.app.add_observation(p)

    def _delete_obs(self) -> None:
        p = self._selected_obs()
        if p:
            self.app.delete_observation(p)
