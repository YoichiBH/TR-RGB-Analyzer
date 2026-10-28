"""Visor de imágenes con zoom, desplazamiento y dibujo/edición de parcelas.

Controles:
* Rueda del ratón: zoom centrado en el cursor.
* Arrastrar con botón derecho o central (o izquierdo sobre zona vacía): desplazar.
* Modo "Dibujar parcela": arrastrar con botón izquierdo para crear un rectángulo.
* Clic en una parcela: seleccionarla. Arrastrarla: moverla.
  Arrastrar una esquina de la parcela seleccionada: cambiar su tamaño.
* Doble clic en una parcela: editarla. Tecla Supr: eliminarla.

Todas las coordenadas que se muestran y guardan son píxeles de la imagen
ORIGINAL, aunque el visor trabaje con una versión reducida para ir rápido.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Callable

import numpy as np
from PIL import Image, ImageTk

from src.graphs import colorize
from src.image_loader import load_preview
from src.indices import compute_index, vegetation_mask
from src.statistics import robust_range

from .widgets import ToolTip

VIEW_MODES = ["Original", "GLI", "VARI", "ExG", "Máscara RGB aprox. de vegetación"]
HANDLE = 6  # tamaño (px de pantalla) de los tiradores de las esquinas


class ImageViewer(ttk.Frame):
    """Visor de una fotografía con superposición de ROIs."""

    def __init__(self, master, on_photo_selected: Callable[[str], None] | None = None,
                 on_roi_drawn: Callable[[tuple[int, int, int, int]], None] | None = None,
                 on_roi_selected: Callable[[str | None], None] | None = None,
                 on_roi_changed: Callable[[str, tuple[int, int, int, int]], None] | None = None,
                 on_roi_edit: Callable[[str], None] | None = None,
                 on_roi_delete: Callable[[str], None] | None = None,
                 get_settings: Callable[[], object] | None = None,
                 viewer_max_side: int = 2500) -> None:
        super().__init__(master)
        self.on_photo_selected = on_photo_selected
        self.on_roi_drawn = on_roi_drawn
        self.on_roi_selected = on_roi_selected
        self.on_roi_changed = on_roi_changed
        self.on_roi_edit = on_roi_edit
        self.on_roi_delete = on_roi_delete
        self.get_settings = get_settings
        self.viewer_max_side = viewer_max_side

        self.preview: Image.Image | None = None
        self.orig_size = (1, 1)
        self.preview_ratio = 1.0
        self.photo_name: str | None = None
        self._display_cache: dict[str, Image.Image] = {}
        self._legend_cache: dict[str, str] = {}
        self.legend_text = tk.StringVar(value="")
        self.scale = 1.0
        self.off_x = 0.0
        self.off_y = 0.0
        self.rois = []
        self.selected_uid: str | None = None
        self.draw_mode = tk.BooleanVar(value=False)
        self._drag: dict | None = None
        self._temp: dict[str, tuple[float, float, float, float]] = {}
        self._tkimg = None
        self._render_pending = False
        self._fitted = False

        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="Foto:").pack(side="left")
        self.photo_var = tk.StringVar()
        self.photo_combo = ttk.Combobox(bar, textvariable=self.photo_var, state="readonly", width=32)
        self.photo_combo.pack(side="left", padx=4)
        self.photo_combo.bind("<<ComboboxSelected>>", lambda e: self.on_photo_selected and self.on_photo_selected(self.photo_var.get()))
        self.draw_btn = ttk.Checkbutton(bar, text="✏ Dibujar parcela", variable=self.draw_mode,
                                        command=self._update_cursor, style="Toolbutton")
        self.draw_btn.pack(side="left", padx=(10, 4))
        ToolTip(self.draw_btn, "Active este modo y arrastre sobre la foto para dibujar una parcela rectangular.")
        for txt, cmd, tip in (("＋", lambda: self.zoom(1.25), "Acercar (también con la rueda del ratón)"),
                              ("－", lambda: self.zoom(0.8), "Alejar"),
                              ("Ajustar", self.fit, "Ver la foto completa"),
                              ("1:1", self.zoom_100, "Un píxel de la foto = un píxel de pantalla")):
            b = ttk.Button(bar, text=txt, width=7 if len(txt) > 2 else 3, command=cmd)
            b.pack(side="left", padx=1)
            ToolTip(b, tip)
        ttk.Label(bar, text="  Vista:").pack(side="left")
        self.mode_var = tk.StringVar(value=VIEW_MODES[0])
        mode = ttk.Combobox(bar, textvariable=self.mode_var, values=VIEW_MODES, state="readonly", width=30)
        mode.pack(side="left", padx=4)
        mode.bind("<<ComboboxSelected>>", lambda e: self.render())
        ToolTip(mode, "Original o vista previa de un índice. La vista previa usa una imagen reducida y escala "
                      "automática; los mapas exportados y las estadísticas usan la resolución completa.")

        self.canvas = tk.Canvas(self, background="#3a3a3a", highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True)
        status = ttk.Frame(self)
        status.pack(fill="x")
        self.coord_label = ttk.Label(status, text="X: —  Y: —", width=28, font=("Consolas", 9))
        self.coord_label.pack(side="left")
        self.zoom_label = ttk.Label(status, text="Zoom: —", width=14)
        self.zoom_label.pack(side="left")
        ttk.Label(status, textvariable=self.legend_text, foreground="#555").pack(side="left", padx=8)

        c = self.canvas
        c.bind("<Configure>", self._on_configure)
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", self._on_double)
        for btn in ("2", "3"):
            c.bind(f"<ButtonPress-{btn}>", self._on_pan_start)
            c.bind(f"<B{btn}-Motion>", self._on_pan_move)
            c.bind(f"<ButtonRelease-{btn}>", lambda e: setattr(self, "_drag", None))
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-4>", lambda e: self.zoom(1.25, e.x, e.y))  # Linux
        c.bind("<Button-5>", lambda e: self.zoom(0.8, e.x, e.y))
        c.bind("<Motion>", self._on_motion)
        c.bind("<Delete>", lambda e: self.selected_uid and self.on_roi_delete and self.on_roi_delete(self.selected_uid))
        c.bind("<Escape>", lambda e: (self.draw_mode.set(False), self._update_cursor()))
        self._show_message("Importe fotografías (sección 1) y selecciónelas aquí para verlas.")

    # ------------------------------------------------------------------ carga
    def set_photo_list(self, names: list[str], current: str | None = None) -> None:
        self.photo_combo["values"] = names
        if current:
            self.photo_var.set(current)
        elif not names:
            self.photo_var.set("")

    def load_image(self, path: str, name: str) -> bool:
        """Carga la vista previa de una foto. Devuelve False si no se puede leer."""
        try:
            prev, size = load_preview(path, self.viewer_max_side)
        except Exception as exc:  # noqa: BLE001
            self.clear(f"No se puede mostrar {name}:\n{exc}")
            return False
        self.preview = prev
        self.orig_size = size
        self.preview_ratio = prev.width / size[0]
        self.photo_name = name
        self.photo_var.set(name)
        self._display_cache = {}
        self._temp = {}
        self._fitted = False
        self.fit()
        return True

    def clear(self, message: str = "") -> None:
        self.preview = None
        self.photo_name = None
        self._display_cache = {}
        self.rois = []
        self.selected_uid = None
        self.canvas.delete("all")
        self.legend_text.set("")
        self._show_message(message)

    def invalidate_views(self) -> None:
        """Recalcula las vistas de índices (p. ej. si cambian las opciones)."""
        self._display_cache = {}
        self.render()

    def _show_message(self, message: str) -> None:
        self.canvas.delete("msg")
        if message:
            self.canvas.create_text(20, 20, text=message, anchor="nw", fill="#ddd", font=("Segoe UI", 12),
                                    tags="msg", width=600)

    # ------------------------------------------------------------------ ROIs
    def set_rois(self, rois, selected_uid: str | None = None) -> None:
        self.rois = list(rois)
        self.selected_uid = selected_uid if any(r.uid == selected_uid for r in self.rois) else None
        self._temp = {}
        self.draw_rois()

    def select_roi(self, uid: str | None, center: bool = False) -> None:
        self.selected_uid = uid
        if center and uid:
            roi = next((r for r in self.rois if r.uid == uid), None)
            if roi:
                cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
                self.off_x = (roi.x1 + roi.x2) / 2 - cw / 2 / self.scale
                self.off_y = (roi.y1 + roi.y2) / 2 - ch / 2 / self.scale
                self.render()
                return
        self.draw_rois()

    # ------------------------------------------------------------------ geometría
    def canvas_to_img(self, cx: float, cy: float) -> tuple[float, float]:
        return self.off_x + cx / self.scale, self.off_y + cy / self.scale

    def img_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.off_x) * self.scale, (y - self.off_y) * self.scale

    def _clamp_img(self, x: float, y: float) -> tuple[float, float]:
        W, H = self.orig_size
        return min(max(x, 0), W), min(max(y, 0), H)

    def fit(self) -> None:
        if self.preview is None:
            return
        cw, ch = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
        W, H = self.orig_size
        self.scale = min(cw / W, ch / H) * 0.97
        self.off_x = -(cw / self.scale - W) / 2
        self.off_y = -(ch / self.scale - H) / 2
        self._fitted = True
        self.render()

    def zoom_100(self) -> None:
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.zoom(1.0 / self.scale, cw / 2, ch / 2)

    def zoom(self, factor: float, cx: float | None = None, cy: float | None = None) -> None:
        if self.preview is None:
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        cx = cw / 2 if cx is None else cx
        cy = ch / 2 if cy is None else cy
        ix, iy = self.canvas_to_img(cx, cy)
        W, H = self.orig_size
        min_scale = min(cw / W, ch / H) * 0.3
        self.scale = min(max(self.scale * factor, min_scale), 40.0)
        self.off_x = ix - cx / self.scale
        self.off_y = iy - cy / self.scale
        self.render()

    # ------------------------------------------------------------------ dibujo
    def _display_image(self) -> Image.Image:
        mode = self.mode_var.get()
        if mode == "Original" or self.preview is None:
            self.legend_text.set("")
            return self.preview
        if mode in self._display_cache:
            self.legend_text.set(self._legend_cache.get(mode, ""))
            return self._display_cache[mode]
        settings = self.get_settings() if self.get_settings else None
        exg_mode = getattr(settings, "exg_mode", "normalized")
        rgb = np.asarray(self.preview, dtype=np.float32) / 255.0
        if mode.startswith("Máscara"):
            method = getattr(settings, "vegetation_method", "exg")
            thr = float(getattr(settings, "vegetation_threshold", 0.1))
            mask = vegetation_mask(rgb, method, thr, getattr(settings, "vegetation_exg_mode", "normalized"))
            gray = np.asarray(self.preview.convert("L").convert("RGB"), dtype=np.float32)
            over = gray.copy()
            over[mask] = gray[mask] * 0.35 + np.array([0, 200, 60]) * 0.65
            img = Image.fromarray(over.astype(np.uint8))
            legend = (f"Verde = vegetación según máscara RGB aproximada ({mask.mean() * 100:.1f} % de la vista previa). "
                      "Clasificación aproximada.")
        else:
            res = compute_index(mode, rgb, None, 1e-6, exg_mode, getattr(settings, "vari_clip", True))
            lo, hi = robust_range(res.values, 2, 98)
            if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
                lo, hi = -1.0, 1.0
            img = Image.fromarray(colorize(res.values, lo, hi))
            legend = f"{mode} (vista previa, escala automática P2–P98): rojo = {lo:.3f} … verde = {hi:.3f}; gris = no calculable"
        self._legend_cache[mode] = legend
        self._display_cache[mode] = img
        self.legend_text.set(legend)
        return img

    def render(self) -> None:
        c = self.canvas
        c.delete("img")
        if self.preview is None:
            return
        c.delete("msg")
        cw, ch = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
        W, H = self.orig_size
        x0, y0 = max(self.off_x, 0), max(self.off_y, 0)
        x1, y1 = min(self.off_x + cw / self.scale, W), min(self.off_y + ch / self.scale, H)
        if x1 > x0 and y1 > y0:
            img = self._display_image()
            pr = self.preview_ratio
            bx0, by0 = int(math.floor(x0 * pr)), int(math.floor(y0 * pr))
            bx1 = max(int(math.ceil(x1 * pr)), bx0 + 1)
            by1 = max(int(math.ceil(y1 * pr)), by0 + 1)
            bx1, by1 = min(bx1, img.width), min(by1, img.height)
            crop = img.crop((bx0, by0, bx1, by1))
            ox0, oy0 = bx0 / pr, by0 / pr
            dw = max(1, int(round((bx1 - bx0) / pr * self.scale)))
            dh = max(1, int(round((by1 - by0) / pr * self.scale)))
            resample = Image.Resampling.NEAREST if self.scale / pr > 1.5 else Image.Resampling.BILINEAR
            crop = crop.resize((dw, dh), resample)
            self._tkimg = ImageTk.PhotoImage(crop)
            cx, cy = self.img_to_canvas(ox0, oy0)
            c.create_image(cx, cy, anchor="nw", image=self._tkimg, tags="img")
            c.tag_lower("img")
        self.zoom_label.configure(text=f"Zoom: {self.scale * 100:.0f} %")
        self.draw_rois()

    def _schedule_render(self) -> None:
        if not self._render_pending:
            self._render_pending = True
            self.after(15, self._do_render)

    def _do_render(self) -> None:
        self._render_pending = False
        self.render()

    def _roi_coords(self, roi) -> tuple[float, float, float, float]:
        return self._temp.get(roi.uid, (roi.x1, roi.y1, roi.x2, roi.y2))

    def draw_rois(self) -> None:
        c = self.canvas
        c.delete("roi")
        if self.preview is None:
            return
        for roi in self.rois:
            x1, y1, x2, y2 = self._roi_coords(roi)
            cx1, cy1 = self.img_to_canvas(x1, y1)
            cx2, cy2 = self.img_to_canvas(x2, y2)
            sel = roi.uid == self.selected_uid
            color = "#ffeb3b" if sel else "#00e5ff"
            c.create_rectangle(cx1, cy1, cx2, cy2, outline="black", width=4 if sel else 3, tags="roi")
            c.create_rectangle(cx1, cy1, cx2, cy2, outline=color, width=2, tags="roi")
            label = roi.parcel_id + (f" · {roi.name}" if roi.name and sel else "")
            t = c.create_text(cx1 + 4, cy1 + 3, text=label, anchor="nw", fill="black", font=("Segoe UI", 10, "bold"), tags="roi")
            bb = c.bbox(t)
            if bb:
                bg = c.create_rectangle(bb[0] - 2, bb[1] - 1, bb[2] + 2, bb[3] + 1, fill=color, outline="", tags="roi")
                c.tag_lower(bg, t)
            if sel:
                for hx, hy in ((cx1, cy1), (cx2, cy1), (cx2, cy2), (cx1, cy2)):
                    c.create_rectangle(hx - HANDLE, hy - HANDLE, hx + HANDLE, hy + HANDLE, fill=color,
                                       outline="black", tags="roi")

    # ------------------------------------------------------------------ hit-test
    def _handle_at(self, cx: float, cy: float) -> int | None:
        roi = next((r for r in self.rois if r.uid == self.selected_uid), None)
        if roi is None:
            return None
        x1, y1, x2, y2 = self._roi_coords(roi)
        corners = [self.img_to_canvas(x1, y1), self.img_to_canvas(x2, y1),
                   self.img_to_canvas(x2, y2), self.img_to_canvas(x1, y2)]
        for i, (hx, hy) in enumerate(corners):
            if abs(cx - hx) <= HANDLE + 2 and abs(cy - hy) <= HANDLE + 2:
                return i
        return None

    def _roi_at(self, cx: float, cy: float):
        ix, iy = self.canvas_to_img(cx, cy)
        hits = [r for r in self.rois if r.contains(ix, iy)]
        if not hits:
            return None
        sel = [r for r in hits if r.uid == self.selected_uid]
        return sel[0] if sel else min(hits, key=lambda r: r.area)

    # ------------------------------------------------------------------ eventos
    def _on_configure(self, _e) -> None:
        if self.preview is not None and not self._fitted:
            self.fit()
        else:
            self._schedule_render()

    def _update_cursor(self) -> None:
        self.canvas.configure(cursor="crosshair" if self.draw_mode.get() else "arrow")

    def _on_motion(self, e) -> None:
        if self.preview is None:
            return
        ix, iy = self.canvas_to_img(e.x, e.y)
        W, H = self.orig_size
        inside = 0 <= ix < W and 0 <= iy < H
        self.coord_label.configure(text=f"X: {int(ix):>5}  Y: {int(iy):>5}" if inside else "X: —  Y: —")
        if not self.draw_mode.get():
            h = self._handle_at(e.x, e.y)
            cursor = "sizing" if h is not None else ("fleur" if self._roi_at(e.x, e.y) else "arrow")
            self.canvas.configure(cursor=cursor)

    def _on_press(self, e) -> None:
        self.canvas.focus_set()
        if self.preview is None:
            return
        ix, iy = self._clamp_img(*self.canvas_to_img(e.x, e.y))
        if self.draw_mode.get():
            self._drag = {"kind": "draw", "start": (ix, iy)}
            self.canvas.delete("rubber")
            return
        h = self._handle_at(e.x, e.y)
        if h is not None:
            roi = next(r for r in self.rois if r.uid == self.selected_uid)
            self._drag = {"kind": "resize", "uid": roi.uid, "corner": h, "orig": roi.coords}
            return
        roi = self._roi_at(e.x, e.y)
        if roi is not None:
            if roi.uid != self.selected_uid:
                self.selected_uid = roi.uid
                self.draw_rois()
                if self.on_roi_selected:
                    self.on_roi_selected(roi.uid)
            self._drag = {"kind": "move", "uid": roi.uid, "start": (ix, iy), "orig": roi.coords}
            return
        if self.selected_uid is not None:
            self.selected_uid = None
            self.draw_rois()
            if self.on_roi_selected:
                self.on_roi_selected(None)
        self._on_pan_start(e)

    def _on_drag(self, e) -> None:
        d = self._drag
        if d is None:
            return
        if d["kind"] == "pan":
            self._on_pan_move(e)
            return
        ix, iy = self._clamp_img(*self.canvas_to_img(e.x, e.y))
        W, H = self.orig_size
        if d["kind"] == "draw":
            sx, sy = d["start"]
            self.canvas.delete("rubber")
            c1, c2 = self.img_to_canvas(sx, sy), self.img_to_canvas(ix, iy)
            self.canvas.create_rectangle(*c1, *c2, outline="#ff4081", width=2, dash=(4, 2), tags="rubber")
            self.coord_label.configure(text=f"{int(abs(ix - sx))} × {int(abs(iy - sy))} px")
        elif d["kind"] == "move":
            x1, y1, x2, y2 = d["orig"]
            dx, dy = ix - d["start"][0], iy - d["start"][1]
            dx = min(max(dx, -x1), W - x2)
            dy = min(max(dy, -y1), H - y2)
            self._temp[d["uid"]] = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
            self.draw_rois()
        elif d["kind"] == "resize":
            x1, y1, x2, y2 = d["orig"]
            corner = d["corner"]
            if corner in (0, 3):
                x1 = ix
            else:
                x2 = ix
            if corner in (0, 1):
                y1 = iy
            else:
                y2 = iy
            self._temp[d["uid"]] = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            self.draw_rois()
            self.coord_label.configure(text=f"{int(abs(x2 - x1))} × {int(abs(y2 - y1))} px")

    def _on_release(self, e) -> None:
        d = self._drag
        self._drag = None
        if d is None:
            return
        if d["kind"] == "draw":
            self.canvas.delete("rubber")
            ix, iy = self._clamp_img(*self.canvas_to_img(e.x, e.y))
            sx, sy = d["start"]
            x1, x2 = sorted((int(round(sx)), int(round(ix))))
            y1, y2 = sorted((int(round(sy)), int(round(iy))))
            if x2 - x1 >= 3 and y2 - y1 >= 3:
                self.draw_mode.set(False)
                self._update_cursor()
                if self.on_roi_drawn:
                    self.on_roi_drawn((x1, y1, x2, y2))
        elif d["kind"] in ("move", "resize"):
            new = self._temp.pop(d["uid"], None)
            if new is None:
                return
            coords = tuple(int(round(v)) for v in new)
            if coords != tuple(d["orig"]) and coords[2] - coords[0] >= 1 and coords[3] - coords[1] >= 1:
                if self.on_roi_changed:
                    self.on_roi_changed(d["uid"], coords)  # type: ignore[arg-type]
            self.draw_rois()

    def _on_double(self, e) -> None:
        if self.draw_mode.get():
            return
        roi = self._roi_at(e.x, e.y)
        if roi is not None and self.on_roi_edit:
            self._drag = None
            self.on_roi_edit(roi.uid)

    def _on_pan_start(self, e) -> None:
        self._drag = {"kind": "pan", "start": (e.x, e.y), "off": (self.off_x, self.off_y)}

    def _on_pan_move(self, e) -> None:
        d = self._drag
        if not d or d.get("kind") != "pan":
            return
        sx, sy = d["start"]
        ox, oy = d["off"]
        self.off_x = ox - (e.x - sx) / self.scale
        self.off_y = oy - (e.y - sy) / self.scale
        self._schedule_render()

    def _on_wheel(self, e) -> None:
        self.zoom(1.2 if e.delta > 0 else 1 / 1.2, e.x, e.y)
