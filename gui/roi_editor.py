"""Panel de gestión de parcelas / ROIs y diálogo de edición."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from src.roi import ROI, validate_roi

from .widgets import FormDialog, ToolTip


def ask_roi(master, title: str, photos: list[str], roi: ROI | None = None,
            coords: tuple[int, int, int, int] | None = None, photo: str | None = None,
            suggested_id: str = "A", image_sizes: dict[str, tuple[int, int]] | None = None,
            min_side: int = 5, min_area: int = 100) -> dict | None:
    """Diálogo para crear/editar una parcela. Devuelve los valores o ``None``."""
    c = coords or ((roi.x1, roi.y1, roi.x2, roi.y2) if roi else (0, 0, 100, 100))
    image_sizes = image_sizes or {}
    current = roi.photo if roi else photo
    image_size = image_sizes.get(current) if current else None
    intro = ("Coordenadas en píxeles de la imagen original (origen arriba-izquierda; x2 e y2 excluidos)."
             + (f" Tamaño de la foto: {image_size[0]} × {image_size[1]} px." if image_size else ""))
    fields = [
        {"key": "parcel_id", "label": "ID parcela *", "default": roi.parcel_id if roi else suggested_id,
         "help": "Identificador corto que debe coincidir con la columna 'parcela' del CSV del Arduino (p. ej. A, B, C)."},
        {"key": "name", "label": "Nombre", "default": roi.name if roi else "", "help": "Ej.: Vegetación densa"},
        {"key": "photo", "label": "Foto asociada *", "type": "choice", "options": photos,
         "default": roi.photo if roi else (photo or (photos[0] if photos else ""))},
        {"key": "x1", "label": "x1", "type": "int", "default": c[0]},
        {"key": "y1", "label": "y1", "type": "int", "default": c[1]},
        {"key": "x2", "label": "x2", "type": "int", "default": c[2]},
        {"key": "y2", "label": "y2", "type": "int", "default": c[3]},
        {"key": "description", "label": "Descripción", "type": "text", "default": roi.description if roi else ""},
    ]

    def validate(v: dict) -> list[str]:
        errs = []
        if not v.get("parcel_id"):
            errs.append("El ID de parcela es obligatorio.")
        if not v.get("photo"):
            errs.append("Seleccione una foto.")
        if any(v.get(k) is None for k in ("x1", "y1", "x2", "y2")):
            errs.append("Las cuatro coordenadas son obligatorias.")
            return errs
        tmp = ROI(v["parcel_id"] or "?", v.get("name", ""), v.get("photo", ""), v["x1"], v["y1"], v["x2"], v["y2"])
        w, h = image_sizes.get(v.get("photo"), (None, None))
        for level, msg in validate_roi(tmp, w, h, min_side, min_area):
            if level == "error":
                errs.append(msg)
        return errs

    dlg = FormDialog(master, title, fields, validate, intro=intro)
    return dlg.result


class RoiPanel(ttk.Frame):
    """Lista de parcelas con botones de gestión.

    ``app`` debe ofrecer los métodos ``roi_start_draw``, ``roi_manual``,
    ``roi_edit``, ``roi_rename``, ``roi_duplicate``, ``roi_copy_to_photo``,
    ``roi_delete``, ``roi_select``, ``roi_import`` y ``roi_export``.
    """

    COLUMNS = ("parcela", "nombre", "foto", "x1", "y1", "x2", "y2", "ancho", "alto")

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 3))
        buttons = [
            ("✏ Crear (dibujar)", app.roi_start_draw, "Activa el modo dibujo en el visor"),
            ("Coordenadas…", app.roi_manual, "Crear una parcela introduciendo coordenadas manualmente"),
            ("Editar", lambda: self._with_sel(app.roi_edit), "Editar la parcela seleccionada (o doble clic)"),
            ("Renombrar", lambda: self._with_sel(app.roi_rename), "Cambiar ID o nombre"),
            ("Duplicar", lambda: self._with_sel(app.roi_duplicate), "Nueva parcela del mismo tamaño en la misma foto"),
            ("Copiar a otra foto", lambda: self._with_sel(app.roi_copy_to_photo),
             "Copiar la parcela con las mismas coordenadas a otra(s) foto(s) (seguimiento temporal)"),
            ("Eliminar", lambda: self._with_sel(app.roi_delete), "Eliminar la parcela seleccionada (tecla Supr)"),
        ]
        for txt, cmd, tip in buttons:
            b = ttk.Button(bar, text=txt, command=cmd)
            b.pack(side="left", padx=1)
            ToolTip(b, tip)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        b = ttk.Button(bar, text="Importar…", command=app.roi_import)
        b.pack(side="left", padx=1)
        ToolTip(b, "Importar parcelas desde JSON o CSV (id_parcela,nombre,foto,x1,y1,x2,y2,descripcion)")
        b = ttk.Button(bar, text="Exportar…", command=app.roi_export)
        b.pack(side="left", padx=1)
        self.only_current = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Sólo foto actual", variable=self.only_current,
                        command=lambda: self.app.refresh_rois()).pack(side="right")

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=self.COLUMNS, show="headings", height=5, selectmode="browse")
        widths = {"parcela": 70, "nombre": 160, "foto": 170}
        for c in self.COLUMNS:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=widths.get(c, 60), anchor="w", stretch=c == "nombre")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda e: self._with_sel(app.roi_edit))
        self.tree.bind("<Delete>", lambda e: self._with_sel(app.roi_delete))

    def _with_sel(self, func) -> None:
        uid = self.selected_uid()
        if uid is None:
            from tkinter import messagebox

            messagebox.showinfo("Parcelas", "Seleccione primero una parcela en la lista o en el visor.", parent=self)
            return
        func(uid)

    def selected_uid(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def refresh(self, rois, selected_uid: str | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for r in rois:
            self.tree.insert("", "end", iid=r.uid, values=(r.parcel_id, r.name, r.photo, r.x1, r.y1, r.x2, r.y2,
                                                            r.width, r.height))
        if selected_uid and self.tree.exists(selected_uid):
            self.tree.selection_set(selected_uid)
            self.tree.see(selected_uid)

    def select(self, uid: str | None) -> None:
        if self.selected_uid() == uid:
            return
        if uid and self.tree.exists(uid):
            self.tree.selection_set(uid)
            self.tree.see(uid)
        elif self.tree.selection():
            self.tree.selection_remove(self.tree.selection())

    def _on_select(self, _e) -> None:
        # El evento <<TreeviewSelect>> llega de forma asíncrona: sólo se notifica
        # si la selección es distinta de la que ya conoce la aplicación (evita bucles).
        uid = self.selected_uid()
        if uid != self.app.selected_roi:
            self.app.roi_select(uid)
