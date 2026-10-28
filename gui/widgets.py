"""Widgets reutilizables de la interfaz (Tkinter / ttk)."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

import pandas as pd

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 16, "bold")
FONT_MONO = ("Consolas", 10)
COLOR_ACCENT = "#2f5d3a"
COLOR_DEMO = "#b00000"


class ScrollableFrame(ttk.Frame):
    """Marco con barra de desplazamiento vertical. Use ``.inner`` para añadir widgets."""

    def __init__(self, master, width: int = 300, **kw) -> None:
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, width=width, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.inner.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.inner.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event) -> None:
        if self.canvas.winfo_height() < self.inner.winfo_reqheight():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ToolTip:
    """Texto de ayuda que aparece al pasar el ratón por encima de un widget."""

    def __init__(self, widget, text: str) -> None:
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _e=None) -> None:
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", background="#ffffe0", relief="solid",
                 borderwidth=1, wraplength=380, font=("Segoe UI", 9)).pack(ipadx=4, ipady=2)

    def hide(self, _e=None) -> None:
        if self.tip:
            self.tip.destroy()
            self.tip = None


def _fmt_cell(v: Any, decimals: int) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sí" if v else "no"
    if isinstance(v, float):
        if math.isnan(v):
            return "—"
        if math.isinf(v):
            return "∞"
        if v.is_integer() and abs(v) >= 1000:
            return f"{v:.0f}"
        return f"{v:.{decimals}f}"
    try:
        if pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        pass
    return str(v)


class DataFrameTable(ttk.Frame):
    """Tabla (Treeview) que muestra un DataFrame de pandas con barras de desplazamiento."""

    def __init__(self, master, height: int = 10, decimals: int = 4, selectmode: str = "browse", **kw) -> None:
        super().__init__(master, **kw)
        self.decimals = decimals
        self.df = pd.DataFrame()
        self.tree = ttk.Treeview(self, show="headings", height=height, selectmode=selectmode)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.empty_label = ttk.Label(self, text="", foreground="#777")

    def set_dataframe(self, df: pd.DataFrame | None, columns: list[str] | None = None,
                      max_rows: int = 5000, empty_text: str = "Sin datos") -> None:
        self.tree.delete(*self.tree.get_children())
        df = df if df is not None else pd.DataFrame()
        if columns:
            df = df[[c for c in columns if c in df.columns]]
        self.df = df.reset_index(drop=True)
        cols = [str(c) for c in self.df.columns]
        self.tree["columns"] = cols
        for c in cols:
            self.tree.heading(c, text=c)
            sample = [len(_fmt_cell(v, self.decimals)) for v in self.df[c].head(50)] if len(self.df) else [0]
            width = min(max(len(c), *sample) * 8 + 16, 360)
            self.tree.column(c, width=max(width, 60), minwidth=40, stretch=False, anchor="w")
        for i, row in enumerate(self.df.head(max_rows).itertuples(index=False)):
            self.tree.insert("", "end", iid=str(i), values=[_fmt_cell(v, self.decimals) for v in row])
        if self.df.empty:
            self.empty_label.configure(text=empty_text)
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.empty_label.place_forget()

    def selected_rows(self) -> list[int]:
        return [int(i) for i in self.tree.selection()]

    def selected_record(self) -> dict | None:
        rows = self.selected_rows()
        if not rows:
            return None
        return self.df.iloc[rows[0]].to_dict()


class ProgressPanel(ttk.Frame):
    """Barra de progreso con mensaje y botón de cancelar."""

    def __init__(self, master, on_cancel: Callable[[], None] | None = None, **kw) -> None:
        super().__init__(master, **kw)
        self.var = tk.DoubleVar(value=0)
        self.msg = tk.StringVar(value="Listo.")
        ttk.Label(self, textvariable=self.msg, width=70, anchor="w").pack(side="left", padx=(8, 4))
        self.bar = ttk.Progressbar(self, variable=self.var, maximum=100, length=260)
        self.bar.pack(side="left", padx=4, pady=4)
        self.pct = ttk.Label(self, text="", width=7)
        self.pct.pack(side="left")
        self.cancel_btn = ttk.Button(self, text="Cancelar", command=on_cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=4)

    def update_progress(self, fraction: float, message: str) -> None:
        self.var.set(max(0.0, min(1.0, fraction)) * 100)
        self.pct.configure(text=f"{fraction * 100:.0f} %")
        self.msg.set(message)

    def set_busy(self, busy: bool) -> None:
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        if not busy:
            self.pct.configure(text="")

    def set_message(self, message: str) -> None:
        self.msg.set(message)


class FormDialog(tk.Toplevel):
    """Diálogo modal genérico de formulario.

    ``fields`` es una lista de diccionarios con:
    ``key``, ``label``, ``type`` ("str" | "int" | "float" | "choice" | "text" | "bool"),
    ``default``, ``options`` (para "choice"), ``help`` (opcional), ``readonly``.
    ``validate`` recibe el diccionario de valores y devuelve una lista de errores.
    """

    def __init__(self, master, title: str, fields: list[dict], validate: Callable[[dict], list[str]] | None = None,
                 intro: str = "", width: int = 46) -> None:
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.result: dict | None = None
        self._fields = fields
        self._validate = validate
        self._vars: dict[str, Any] = {}
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        row = 0
        if intro:
            ttk.Label(body, text=intro, wraplength=420, justify="left", foreground="#444").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
            row += 1
        first = None
        for f in fields:
            ttk.Label(body, text=f["label"]).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=3)
            ftype = f.get("type", "str")
            default = f.get("default", "")
            if ftype == "text":
                w = tk.Text(body, width=width, height=4, font=FONT, wrap="word")
                w.insert("1.0", "" if default is None else str(default))
                self._vars[f["key"]] = w
            elif ftype == "choice":
                var = tk.StringVar(value="" if default is None else str(default))
                w = ttk.Combobox(body, textvariable=var, values=f.get("options", []), width=width - 3,
                                 state="readonly" if f.get("strict", True) else "normal")
                self._vars[f["key"]] = var
            elif ftype == "bool":
                var = tk.BooleanVar(value=bool(default))
                w = ttk.Checkbutton(body, variable=var)
                self._vars[f["key"]] = var
            else:
                var = tk.StringVar(value="" if default is None else str(default))
                w = ttk.Entry(body, textvariable=var, width=width)
                if f.get("readonly"):
                    w.configure(state="readonly")
                self._vars[f["key"]] = var
            w.grid(row=row, column=1, sticky="we", pady=3)
            if f.get("help"):
                ToolTip(w, f["help"])
            if first is None and not f.get("readonly"):
                first = w
            row += 1
        self.error_label = ttk.Label(body, text="", foreground="#b00000", wraplength=420)
        self.error_label.grid(row=row, column=0, columnspan=2, sticky="w")
        btns = ttk.Frame(body)
        btns.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Aceptar", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self._ok() if not isinstance(e.widget, tk.Text) else None)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        if first is not None:
            first.focus_set()
        _center(self, master)
        self.wait_window(self)

    def _collect(self) -> dict | None:
        values: dict[str, Any] = {}
        for f in self._fields:
            w = self._vars[f["key"]]
            raw = w.get("1.0", "end").strip() if isinstance(w, tk.Text) else w.get()
            ftype = f.get("type", "str")
            if ftype in ("int", "float"):
                txt = str(raw).strip().replace(",", ".")
                if txt == "":
                    values[f["key"]] = None
                    continue
                try:
                    values[f["key"]] = int(round(float(txt))) if ftype == "int" else float(txt)
                except ValueError:
                    self.error_label.configure(text=f"'{f['label']}' debe ser numérico.")
                    return None
            else:
                values[f["key"]] = raw.strip() if isinstance(raw, str) else raw
        return values

    def _ok(self) -> None:
        values = self._collect()
        if values is None:
            return
        errors = self._validate(values) if self._validate else []
        if errors:
            self.error_label.configure(text="\n".join(errors))
            return
        self.result = values
        self.destroy()


def _center(win: tk.Toplevel, master) -> None:
    win.update_idletasks()
    try:
        x = master.winfo_rootx() + (master.winfo_width() - win.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    except tk.TclError:
        pass


def show_text_window(master, title: str, text: str, width: int = 100, height: int = 32) -> None:
    """Ventana de sólo lectura con texto desplazable."""
    win = tk.Toplevel(master)
    win.title(title)
    win.transient(master)
    frame = ttk.Frame(win, padding=8)
    frame.pack(fill="both", expand=True)
    txt = tk.Text(frame, wrap="word", width=width, height=height, font=FONT)
    sb = ttk.Scrollbar(frame, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    txt.insert("1.0", text)
    txt.configure(state="disabled")
    ttk.Button(win, text="Cerrar", command=win.destroy).pack(pady=6)
    _center(win, master)


def ask_overwrite(master, path) -> str | None:
    """Pregunta qué hacer si ya existen resultados.

    Returns:
        ``"overwrite"``, ``"new"`` o ``None`` (cancelar).
    """
    ans = messagebox.askyesnocancel(
        "Resultados existentes",
        f"Ya existen resultados en:\n{path}\n\n¿Desea sobrescribir los resultados?\n\n"
        "• Sí: sobrescribir los archivos de resultados\n"
        "• No: guardar en una carpeta nueva (con fecha y hora)\n"
        "• Cancelar: no hacer nada",
        parent=master, icon="warning")
    if ans is None:
        return None
    return "overwrite" if ans else "new"
