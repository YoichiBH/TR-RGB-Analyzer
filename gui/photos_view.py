"""Pestaña "Fotografías": lista de fotos del proyecto con su información."""

from __future__ import annotations

from tkinter import ttk

from src.utils import human_size

from .widgets import ToolTip


class PhotosView(ttk.Frame):
    COLUMNS = ("nombre", "resolución", "megapíxeles", "tamaño", "formato", "fecha", "origen fecha", "parcelas",
               "estado", "ruta")

    def __init__(self, master, app) -> None:
        super().__init__(master, padding=6)
        self.app = app
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Button(bar, text="Importar fotos…", command=app.import_photos).pack(side="left", padx=2)
        ttk.Button(bar, text="Ver en el visor", command=self._view).pack(side="left", padx=2)
        b = ttk.Button(bar, text="Editar fecha…", command=self._edit_date)
        b.pack(side="left", padx=2)
        ToolTip(b, "Si la foto no tiene fecha EXIF puede indicarla manualmente (necesario para la evolución temporal).")
        b = ttk.Button(bar, text="Quitar del proyecto", command=self._remove)
        b.pack(side="left", padx=2)
        ToolTip(b, "Quita la foto del proyecto. El archivo original NO se borra.")
        self.count_label = ttk.Label(bar, text="0 imágenes cargadas", font=("Segoe UI", 10, "bold"))
        self.count_label.pack(side="right", padx=6)

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=self.COLUMNS, show="headings", selectmode="browse")
        widths = {"nombre": 180, "resolución": 110, "megapíxeles": 80, "tamaño": 80, "formato": 60, "fecha": 140,
                  "origen fecha": 90, "parcelas": 60, "estado": 160, "ruta": 400}
        for c in self.COLUMNS:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=widths.get(c, 80), anchor="w", stretch=c == "ruta")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("invalid", foreground="#b00000")
        self.tree.bind("<Double-1>", lambda e: self._view())
        ttk.Label(self, text="Las fotografías originales nunca se modifican: el programa sólo las lee.",
                  foreground="#555").pack(anchor="w", pady=(4, 0))

    def refresh(self, project) -> None:
        self.tree.delete(*self.tree.get_children())
        counts = {}
        for r in project.rois:
            counts[r.photo] = counts.get(r.photo, 0) + 1
        for p in project.photos:
            state = "válida" if p.valid else f"ERROR: {p.error}"
            mp = p.width * p.height / 1e6
            if p.valid and mp > self.app.config_data.get("max_megapixels_warning", 40):
                state = "válida (muy grande: RAM)"
            self.tree.insert("", "end", iid=p.name, tags=() if p.valid else ("invalid",), values=(
                p.name, f"{p.width} × {p.height}", f"{mp:.1f}", human_size(p.size_bytes), p.format,
                p.date or "—", p.date_source, counts.get(p.name, 0), state, p.path))
        n = len(project.photos)
        self.count_label.configure(text=f"{n} imagen cargada" if n == 1 else f"{n} imágenes cargadas")

    def _selected(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            from tkinter import messagebox

            messagebox.showinfo("Fotografías", "Seleccione una foto de la lista.", parent=self)
            return None
        return sel[0]

    def _view(self) -> None:
        name = self._selected()
        if name:
            self.app.show_photo(name, switch_tab=True)

    def _edit_date(self) -> None:
        name = self._selected()
        if name:
            self.app.edit_photo_date(name)

    def _remove(self) -> None:
        name = self._selected()
        if name:
            self.app.remove_photo(name)
