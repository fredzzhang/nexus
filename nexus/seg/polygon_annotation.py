"""Polygon annotation tool with reference views

Fred Zhang <frezz@amazon.com>
"""
import os
import json
import copy
import random
import string
import tempfile
import tkinter as tk
from datetime import datetime
from PIL import Image, ImageTk, ImageDraw
from tkinter import filedialog, messagebox, ttk

from .generate_masks import generate_masks

AUTOSAVE_PATH = os.path.join(tempfile.gettempdir(), "polygon_annotation_autosave.json")

BASE_DATA = {
    "project": {"pname": ""},
    "attribute": {
        "1": {
            "options": {
                "401": "Strawberry - Decay",
                "402": "Strawberry - Overripe/Wet bruising",
                "403": "Strawberry - Mould",
                "404": "Strawberry - Condensation",
                "406": "Strawberry - Instance Fully Visible",
            }
        }
    },
    "file": {},
    "metadata": {}
}

# Glasbey palette: 32 perceptually distinct colors for class visualization.
_GLASBEY_PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
    "#dcbeff", "#9a6324", "#fffac8", "#800000", "#aaffc3",
    "#808000", "#ffd8b1", "#000075", "#a9a9a9", "#e6beff",
    "#ffe119", "#00ff7f", "#ff6347", "#7b68ee", "#00ced1",
    "#ff1493", "#7fff00", "#dc143c", "#00bfff", "#ff8c00",
    "#adff2f", "#da70d6",
]

class PolygonAnnotationWithReference:
    """A tkinter-based polygon annotation tool with side-by-side reference image viewing.

    Allows users to draw, edit, and label polygons on images while viewing
    corresponding reference images. Annotations are saved/loaded in a VIA-compatible
    JSON format with file, metadata, and attribute sections.

    Args:
        root: The tkinter root window.
        custom_classes: Optional dict mapping class index strings to class names
            (e.g. {"500": "Blueberry - Decay"}). Indices must not collide with
            those in BASE_DATA.
        asin: Product name used to filter classes at startup (case-insensitive).
            Classes whose names start with "<asin> - " are shown. If None, the
            user is prompted via a dialog.
        name_format: Optional list of glob-like patterns defining the naming
            convention for annotation and reference images. The first pattern
            identifies the annotation image; the rest identify reference images.
            Each pattern uses '*' as a wildcard for the shared stem between
            filenames. For example::

                ['*_cam0.jpg', '*_cam1.jpg', '*_cam2.jpg']

            means files like ``001_cam0.jpg`` (annotate), ``001_cam1.jpg`` and
            ``001_cam2.jpg`` (references) share the stem ``001``.
            If None, all images in the directory are treated as annotation
            targets with no reference images.
        autosave_interval: Interval in minutes between automatic saves
            (default 5). The auto-save is written to a temporary file and
            removed after a successful manual save.

    Controls:
        - Left-click: Add polygon vertex (click near first point to close).
        - Right-click: Close current polygon.
        - Double-click: Delete polygon under cursor.
        - Edit Mode: Select polygons to drag vertices or reassign classes.
        - Prev/Next Ref buttons: Scroll through reference images.
    """

    def __init__(self, root, custom_classes=None, asin="strawberry", name_format=None, autosave_interval=5):
        self.root = root
        self.root.title("Polygon Annotation Tool")
        self._custom_classes = custom_classes
        self._asin = asin
        self._name_format = name_format
        
        self.top_frame = tk.Frame(root)
        self.top_frame.pack(side=tk.TOP, fill=tk.X)
        
        tk.Button(self.top_frame, text="Load Directory", command=self.load_directory).pack(side=tk.LEFT)
        tk.Button(self.top_frame, text="<", command=self.prev_image).pack(side=tk.LEFT)
        tk.Button(self.top_frame, text=">", command=self.next_image).pack(side=tk.LEFT)
        
        self.file_dropdown = ttk.Combobox(self.top_frame, state="readonly", width=40)
        self.file_dropdown.pack(side=tk.LEFT, padx=10)
        self.file_dropdown.bind("<<ComboboxSelected>>", self.on_file_selected)
        
        self.filename_label = tk.Label(self.top_frame, text="No image loaded")
        self.filename_label.pack(side=tk.LEFT, padx=10)
        
        tk.Label(self.top_frame, text="Filter:").pack(side=tk.LEFT)
        self.filter_dropdown = ttk.Combobox(self.top_frame, state="readonly", width=20)
        self.filter_dropdown.pack(side=tk.LEFT, padx=5)
        self.filter_dropdown.set("All")
        self.filter_dropdown.bind("<<ComboboxSelected>>", self._apply_filter)
        
        self.canvas_frame = tk.Frame(root)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        self.canvas = tk.Canvas(self.canvas_frame, cursor="cross")
        self.canvas.pack(side=tk.LEFT, anchor=tk.N)
        
        self.ref_canvases = []
        self.ref_photos = []
        
        self.btn_frame = tk.Frame(root)
        self.btn_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        control_frame = tk.Frame(self.btn_frame)
        control_frame.pack(side=tk.TOP, fill=tk.X)
        
        tk.Button(control_frame, text="Clear Current [C]", command=self.clear_current).pack(side=tk.LEFT)
        tk.Button(control_frame, text="Clear All [Del]", command=self.clear_all).pack(side=tk.LEFT)
        self.edit_mode_btn = tk.Button(control_frame, text="Edit Mode: OFF [E]", command=self.toggle_edit_mode)
        self.edit_mode_btn.pack(side=tk.LEFT)
        tk.Button(control_frame, text="Manage Classes", command=self.manage_classes).pack(side=tk.LEFT)
        tk.Button(control_frame, text="Load Annotations", command=self.load_annotations).pack(side=tk.LEFT)
        tk.Button(control_frame, text="Save Annotations", command=self.save_annotations).pack(side=tk.LEFT)
        tk.Button(control_frame, text="Revert [R]", command=self.revert_annotations).pack(side=tk.LEFT)
        self.show_original_btn = tk.Button(control_frame, text="Show Original [T]", command=self.toggle_show_original)
        self.show_original_btn.pack(side=tk.LEFT)
        tk.Button(control_frame, text="Generate Masks", command=self.generate_masks_dialog).pack(side=tk.LEFT)
        
        self.class_buttons_canvas = tk.Canvas(self.btn_frame, height=100)
        self.class_buttons_canvas.pack(side=tk.TOP, fill=tk.X, pady=5)
        self.class_buttons_frame = tk.Frame(self.class_buttons_canvas)
        self.class_buttons_canvas.create_window(0, 0, window=self.class_buttons_frame, anchor=tk.NW)
        self.class_buttons_canvas.bind('<Configure>', self.on_canvas_configure)
        
        self.class_buttons = {}
        
        self.image = None
        self.photo = None
        self.current_polygon = []
        self.polygons = []
        self.polygon_items = []
        self.image_files = []
        self._filtered_files = None
        self.current_index = 0
        self.directory = None
        self.scale = 1.0
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._pan_start = None
        self.max_width = 1200
        self.max_height = 800
        self.all_annotations = {}
        self.classes = {}
        self.polygon_labels = {}
        self._class_colors = {}
        self._next_color_idx = 0
        self.selected_class = None
        self.loaded_data = None
        self._saved_annotations = {}
        self._saved_labels = {}
        self.edit_mode = False
        self.showing_original = False
        self.selected_polygon_idx = None
        self.selected_vertex_idx = None
        self.vertex_handles = []
        self._loaded_annotation_path = None
        self._autosave_interval = autosave_interval * 60 * 1000
        self._autosave_id = None
        
        self.canvas.bind("<Button-1>", self.add_point)
        self.canvas.bind("<Double-Button-1>", self.delete_polygon)
        self.canvas.bind("<Button-2>", self._pan_start_event)
        self.canvas.bind("<B2-Motion>", self._pan_motion)
        self.canvas.bind("<ButtonRelease-2>", self._pan_end)
        self.canvas.bind("<Button-3>", self._pan_start_event)
        self.canvas.bind("<B3-Motion>", self._pan_motion)
        self.canvas.bind("<ButtonRelease-3>", self._pan_end)
        
        self.root.bind("<Left>", lambda e: self.prev_image())
        self.root.bind("<Right>", lambda e: self.next_image())
        self.root.bind("r", lambda e: self.revert_annotations())
        self.root.bind("c", lambda e: self.clear_current())
        self.root.bind("<Delete>", lambda e: self.clear_all())
        self.root.bind("t", lambda e: self.toggle_show_original())
        self.root.bind("e", lambda e: self.toggle_edit_mode())
        self.root.bind("+", lambda e: self.zoom_in())
        self.root.bind("=", lambda e: self.zoom_in())
        self.root.bind("-", lambda e: self.zoom_out())
        self.root.bind("0", lambda e: self.zoom_reset())
        
        self.load_base_classes()
        self._check_autosave()
        self._schedule_autosave()
    
    def _color_for_class(self, class_idx):
        """Return a stable, distinct color for a class index using Glasbey palette."""
        if class_idx not in self._class_colors:
            self._class_colors[class_idx] = _GLASBEY_PALETTE[self._next_color_idx % len(_GLASBEY_PALETTE)]
            self._next_color_idx += 1
        return self._class_colors[class_idx]

    @staticmethod
    def _parse_pattern(pattern):
        parts = pattern.split('*', 1)
        return (parts[0], parts[1]) if len(parts) == 2 else ('', parts[0])

    def _match_pattern(self, filename, prefix, suffix):
        return filename.startswith(prefix) and filename.endswith(suffix)

    def _extract_stem(self, filename, prefix, suffix):
        return filename[len(prefix):len(filename) - len(suffix)]

    def load_directory(self):
        directory = filedialog.askdirectory()
        if directory:
            self.directory = directory
            all_files = sorted(f for f in os.listdir(directory)
                               if f.lower().endswith(('.png', '.jpg', '.jpeg')))
            if self._name_format:
                ann_prefix, ann_suffix = self._parse_pattern(self._name_format[0])
                self.image_files = [os.path.join(directory, f) for f in all_files
                                    if self._match_pattern(f, ann_prefix, ann_suffix)]
            else:
                self.image_files = [os.path.join(directory, f) for f in all_files]
            self.image_files.sort()
            if self.image_files:
                self._filtered_files = None
                self.filter_dropdown.set("All")
                filenames = [f"[{i+1}] {os.path.basename(f)}" for i, f in enumerate(self.image_files)]
                self.file_dropdown['values'] = filenames
                self.all_annotations = {}
                self.current_index = 0
                self.load_current_image()
            else:
                messagebox.showwarning("No Images", "No images found in directory")
    
    def load_current_image(self):
        if self.image_files:
            self.save_current_annotations()
            
            path = self.image_files[self.current_index]
            self.image = Image.open(path)
            
            self.root.update_idletasks()
            
            self.scale = min(self.max_width / self.image.width, 
                           self.max_height / self.image.height, 1.0)
            
            self.pan_x = 0.0
            self.pan_y = 0.0
            self._clamp_pan()
            viewport_w = int(self.image.width * self.scale)
            viewport_h = int(self.image.height * self.scale)
            effective_scale = self.scale * self.zoom
            zoomed_size = (int(self.image.width * effective_scale),
                          int(self.image.height * effective_scale))
            zoomed_image = self.image.resize(zoomed_size, Image.LANCZOS)
            left = int(self.pan_x)
            top = int(self.pan_y)
            cropped = zoomed_image.crop((left, top, left + viewport_w, top + viewport_h))
            
            self.photo = ImageTk.PhotoImage(cropped)
            self.canvas.delete("all")
            self.canvas.config(width=viewport_w, height=viewport_h)
            self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
            self.image_path = path
            active = self._get_active_files()
            if path in active:
                filtered_idx = active.index(path)
            else:
                filtered_idx = self.current_index
            self.filename_label.config(text=f"{os.path.basename(path)} ({filtered_idx + 1}/{len(active)})")
            self.file_dropdown.set(f"[{filtered_idx + 1}] {os.path.basename(path)}")
            self.current_polygon = []
            
            self.load_reference_image(path)
            self.restore_annotations()
    
    def _get_active_files(self):
        """Return the currently active file list (filtered or all)."""
        if hasattr(self, '_filtered_files') and self._filtered_files is not None:
            return self._filtered_files
        return self.image_files

    def prev_image(self):
        active = self._get_active_files()
        if not active:
            return
        cur_path = self.image_files[self.current_index] if self.image_files else None
        if cur_path in active:
            idx = active.index(cur_path)
            if idx > 0:
                self.current_index = self.image_files.index(active[idx - 1])
                self.load_current_image()
    
    def next_image(self):
        active = self._get_active_files()
        if not active:
            return
        cur_path = self.image_files[self.current_index] if self.image_files else None
        if cur_path in active:
            idx = active.index(cur_path)
            if idx < len(active) - 1:
                self.current_index = self.image_files.index(active[idx + 1])
                self.load_current_image()
    
    def on_file_selected(self, event):
        selected = self.file_dropdown.get()
        idx = int(selected.split("]", 1)[0].lstrip("[")) - 1
        active = self._get_active_files()
        if idx < len(active):
            self.current_index = self.image_files.index(active[idx])
        self.load_current_image()
    
    def load_reference_image(self, main_path):
        for c in self.ref_canvases:
            c.destroy()
        self.ref_canvases = []
        self.ref_photos = []
        self._ref_images = []

        ref_patterns = self._name_format[1:] if self._name_format and len(self._name_format) > 1 else []
        if not ref_patterns:
            return

        basename = os.path.basename(main_path)
        ann_prefix, ann_suffix = self._parse_pattern(self._name_format[0])
        stem = self._extract_stem(basename, ann_prefix, ann_suffix)

        for pattern in ref_patterns:
            ref_prefix, ref_suffix = self._parse_pattern(pattern)
            ref_name = ref_prefix + stem + ref_suffix
            ref_path = os.path.join(self.directory, ref_name)

            if os.path.exists(ref_path):
                self._ref_images.append(Image.open(ref_path))
            else:
                self._ref_images.append(None)

        self._refresh_reference_images()

    def _refresh_reference_images(self):
        """Redraw reference images with the same zoom/pan as the primary image."""
        for c in self.ref_canvases:
            c.destroy()
        self.ref_canvases = []
        self.ref_photos = []

        if not hasattr(self, '_ref_images') or not self._ref_images:
            return

        viewport_h = int(self.image.height * self.scale)
        viewport_w = int(self.image.width * self.scale)

        for ref_image in self._ref_images:
            if ref_image is not None:
                # Scale ref to match primary image's base scale ratio
                ref_scale = self.scale * (self.image.height / ref_image.height) * self.zoom
                zoomed_size = (int(ref_image.width * ref_scale),
                               int(ref_image.height * ref_scale))
                zoomed_ref = ref_image.resize(zoomed_size, Image.LANCZOS)
                # Apply same pan ratio
                effective_scale = self.scale * self.zoom
                max_x = max(1, int(self.image.width * effective_scale) - viewport_w)
                max_y = max(1, int(self.image.height * effective_scale) - viewport_h)
                pan_ratio_x = self.pan_x / max_x if max_x > 0 else 0
                pan_ratio_y = self.pan_y / max_y if max_y > 0 else 0
                ref_viewport_w = min(viewport_w, zoomed_size[0])
                ref_viewport_h = min(viewport_h, zoomed_size[1])
                ref_max_x = max(0, zoomed_size[0] - ref_viewport_w)
                ref_max_y = max(0, zoomed_size[1] - ref_viewport_h)
                ref_pan_x = int(pan_ratio_x * ref_max_x)
                ref_pan_y = int(pan_ratio_y * ref_max_y)
                cropped = zoomed_ref.crop((ref_pan_x, ref_pan_y,
                                           ref_pan_x + ref_viewport_w,
                                           ref_pan_y + ref_viewport_h))
                photo = ImageTk.PhotoImage(cropped)
                canvas_w, canvas_h = ref_viewport_w, ref_viewport_h
            else:
                placeholder = Image.new('RGB', (viewport_w, viewport_h), 'gray')
                draw = ImageDraw.Draw(placeholder)
                draw.text((viewport_w // 2 - 40, viewport_h // 2), "No ref", fill='white')
                photo = ImageTk.PhotoImage(placeholder)
                canvas_w, canvas_h = viewport_w, viewport_h

            self.ref_photos.append(photo)
            canvas = tk.Canvas(self.canvas_frame, width=canvas_w, height=canvas_h)
            canvas.pack(side=tk.LEFT, anchor=tk.N)
            canvas.create_image(0, 0, anchor=tk.NW, image=photo)
            self.ref_canvases.append(canvas)
    
    def toggle_edit_mode(self):
        self.edit_mode = not self.edit_mode
        if self.edit_mode:
            self.edit_mode_btn.config(text="Edit Mode: ON [E]", relief=tk.SUNKEN)
            self.clear_current()
        else:
            self.edit_mode_btn.config(text="Edit Mode: OFF [E]", relief=tk.RAISED)
            self.deselect_polygon()
            # Save and reload to ensure consistency
            self.save_current_annotations()
            self.restore_annotations()
    
    def select_polygon_for_edit(self, x, y):
        ox, oy = self._display_to_original(x, y)
        for i, polygon in enumerate(self.polygons):
            if self.point_in_polygon(ox, oy, polygon):
                self.selected_polygon_idx = i
                self.show_vertex_handles()
                return True
        return False
    
    def show_vertex_handles(self):
        self.canvas.delete("vertex_handle")
        self.vertex_handles = []
        if self.selected_polygon_idx is not None:
            polygon = self.polygons[self.selected_polygon_idx]
            for i, (ox, oy) in enumerate(polygon):
                x, y = self._original_to_display(ox, oy)
                handle = self.canvas.create_oval(x-5, y-5, x+5, y+5, fill="yellow", outline="black", tags="vertex_handle")
                self.vertex_handles.append(handle)
    
    def deselect_polygon(self):
        self.selected_polygon_idx = None
        self.selected_vertex_idx = None
        self.canvas.delete("vertex_handle")
        self.vertex_handles = []
    
    def find_vertex_at(self, x, y):
        if self.selected_polygon_idx is not None:
            polygon = self.polygons[self.selected_polygon_idx]
            for i, (vx, vy) in enumerate(polygon):
                dx, dy = self._original_to_display(vx, vy)
                if abs(x - dx) < 8 and abs(y - dy) < 8:
                    return i
        return None
    
    def add_point(self, event):
        if event.widget != self.canvas:
            return
        x, y = event.x, event.y
        
        if self.edit_mode:
            # Check if clicking on a vertex (compare in display coords)
            vertex_idx = self.find_vertex_at(x, y)
            if vertex_idx is not None:
                self.selected_vertex_idx = vertex_idx
                self.canvas.bind("<B1-Motion>", self.drag_vertex)
                self.canvas.bind("<ButtonRelease-1>", self.release_vertex)
            elif self.selected_polygon_idx is None:
                # Select polygon
                self.select_polygon_for_edit(x, y)
            return
        
        if len(self.current_polygon) > 2:
            dx0, dy0 = self._original_to_display(*self.current_polygon[0])
            distance = ((x - dx0) ** 2 + (y - dy0) ** 2) ** 0.5
            if distance < 10:
                self._close_current_polygon()
                return
        
        # Store in original coordinates
        orig_x, orig_y = self._display_to_original(x, y)
        self.current_polygon.append((orig_x, orig_y))
        self.canvas.create_oval(x-3, y-3, x+3, y+3, fill="red", tags="temp")
        
        if len(self.current_polygon) > 1:
            x1, y1 = self._original_to_display(*self.current_polygon[-2])
            self.canvas.create_line(x1, y1, x, y, fill="red", width=2, tags="temp")
    
    def drag_vertex(self, event):
        if self.selected_polygon_idx is not None and self.selected_vertex_idx is not None:
            orig_x, orig_y = self._display_to_original(event.x, event.y)
            self.polygons[self.selected_polygon_idx][self.selected_vertex_idx] = (orig_x, orig_y)
            self.redraw_polygon(self.selected_polygon_idx)
            self.show_vertex_handles()
    
    def release_vertex(self, event):
        self.selected_vertex_idx = None
        self.canvas.unbind("<B1-Motion>")
        self.canvas.unbind("<ButtonRelease-1>")
    
    def redraw_polygon(self, poly_idx):
        if poly_idx < len(self.polygon_items):
            for item_id in self.polygon_items[poly_idx]:
                self.canvas.delete(item_id)
            
            polygon = self.polygons[poly_idx]
            poly_key = (self.image_path, poly_idx)
            class_idx = self.polygon_labels.get(poly_key)
            if class_idx:
                color = self._color_for_class(class_idx)
            else:
                color = 'green'
            
            display_pts = [self._original_to_display(ox, oy) for ox, oy in polygon]
            flat_coords = [coord for point in display_pts for coord in point]
            poly_id = self.canvas.create_polygon(flat_coords, outline=color, fill="", width=2, tags="polygon")
            
            x1, y1 = display_pts[-1]
            x2, y2 = display_pts[0]
            line_id = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2, tags="polygon")
            
            self.polygon_items[poly_idx] = [line_id, poly_id]
    
    def _close_current_polygon(self):
        if len(self.current_polygon) > 2:
            poly_idx = len(self.polygons)
            self.polygons.append(self.current_polygon[:])
            
            if self.selected_class and self.selected_class in self.classes:
                class_idx = self.classes[self.selected_class]
                color = self._color_for_class(class_idx)
            else:
                class_idx = None
                color = 'green'
            
            poly_key = (self.image_path, poly_idx)
            self.polygon_labels[poly_key] = class_idx
            
            display_pts = [self._original_to_display(ox, oy) for ox, oy in self.current_polygon]
            x1, y1 = display_pts[-1]
            x2, y2 = display_pts[0]
            line_id = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2, tags="polygon")
            
            flat_coords = [coord for point in display_pts for coord in point]
            poly_id = self.canvas.create_polygon(flat_coords, outline=color, fill="", width=2, tags="polygon")
            
            self.polygon_items.append([line_id, poly_id])
            
            self.canvas.delete("temp")
            self.current_polygon = []
    
    def clear_current(self):
        self.canvas.delete("temp")
        self.current_polygon = []
    
    def clear_all(self):
        if not self.polygons:
            return
        if not messagebox.askyesno("Clear All", "Remove all polygons on this image?"):
            return
        self.deselect_polygon()
        self.polygons = []
        self.polygon_items = []
        for key in list(self.polygon_labels):
            if key[0] == self.image_path:
                del self.polygon_labels[key]
        self.all_annotations[self.image_path] = []
        self.canvas.delete("polygon")
        self.canvas.delete("temp")
        self.current_polygon = []
    
    def revert_annotations(self):
        if not hasattr(self, 'image_path'):
            return
        path = self.image_path
        if path in self._saved_annotations:
            self.all_annotations[path] = copy.deepcopy(self._saved_annotations[path])
            # Restore labels for this image
            for key in list(self.polygon_labels):
                if key[0] == path:
                    del self.polygon_labels[key]
            for key, val in self._saved_labels.items():
                if key[0] == path:
                    self.polygon_labels[key] = val
        else:
            self.all_annotations[path] = []
            for key in list(self.polygon_labels):
                if key[0] == path:
                    del self.polygon_labels[key]
        self.deselect_polygon()
        self.canvas.delete("temp")
        self.current_polygon = []
        self.restore_annotations()
    
    def toggle_show_original(self):
        self.showing_original = not self.showing_original
        if self.showing_original:
            self.save_current_annotations()
            self.show_original_btn.config(relief=tk.SUNKEN, text="Show Annotations [T]")
            self.canvas.delete("polygon")
            self.canvas.delete("vertex_handle")
            self.canvas.delete("temp")
        else:
            self.show_original_btn.config(relief=tk.RAISED, text="Show Original [T]")
            self.restore_annotations()
    
    def _refresh_display(self):
        """Redraw the primary image and annotations at the current zoom level."""
        if not self.image:
            return
        self.save_current_annotations()
        self._clamp_pan()
        viewport_w = int(self.image.width * self.scale)
        viewport_h = int(self.image.height * self.scale)
        effective_scale = self.scale * self.zoom
        # Crop the zoomed image to the viewport
        zoomed_size = (int(self.image.width * effective_scale),
                       int(self.image.height * effective_scale))
        zoomed_image = self.image.resize(zoomed_size, Image.LANCZOS)
        left = int(self.pan_x)
        top = int(self.pan_y)
        right = left + viewport_w
        bottom = top + viewport_h
        cropped = zoomed_image.crop((left, top, right, bottom))
        self.photo = ImageTk.PhotoImage(cropped)
        self.canvas.delete("all")
        self.canvas.config(width=viewport_w, height=viewport_h)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
        self.restore_annotations()
        self._refresh_reference_images()

    def _clamp_pan(self):
        """Clamp pan offsets so the viewport stays within the zoomed image."""
        effective_scale = self.scale * self.zoom
        max_x = max(0, int(self.image.width * effective_scale) - int(self.image.width * self.scale))
        max_y = max(0, int(self.image.height * effective_scale) - int(self.image.height * self.scale))
        self.pan_x = max(0, min(self.pan_x, max_x))
        self.pan_y = max(0, min(self.pan_y, max_y))

    def _display_to_original(self, x, y):
        """Convert display (canvas) coordinates to original image coordinates."""
        effective_scale = self.scale * self.zoom
        orig_x = (x + self.pan_x) / effective_scale
        orig_y = (y + self.pan_y) / effective_scale
        return orig_x, orig_y

    def _original_to_display(self, x, y):
        """Convert original image coordinates to display (canvas) coordinates."""
        effective_scale = self.scale * self.zoom
        disp_x = x * effective_scale - self.pan_x
        disp_y = y * effective_scale - self.pan_y
        return disp_x, disp_y

    def zoom_in(self):
        # Zoom towards center of viewport
        viewport_w = int(self.image.width * self.scale)
        viewport_h = int(self.image.height * self.scale)
        center_x = self.pan_x + viewport_w / 2
        center_y = self.pan_y + viewport_h / 2
        old_zoom = self.zoom
        self.zoom = min(self.zoom * 1.25, 5.0)
        ratio = self.zoom / old_zoom
        self.pan_x = center_x * ratio - viewport_w / 2
        self.pan_y = center_y * ratio - viewport_h / 2
        self._refresh_display()

    def zoom_out(self):
        viewport_w = int(self.image.width * self.scale)
        viewport_h = int(self.image.height * self.scale)
        center_x = self.pan_x + viewport_w / 2
        center_y = self.pan_y + viewport_h / 2
        old_zoom = self.zoom
        self.zoom = max(self.zoom / 1.25, 1.0)
        ratio = self.zoom / old_zoom
        self.pan_x = center_x * ratio - viewport_w / 2
        self.pan_y = center_y * ratio - viewport_h / 2
        self._refresh_display()

    def zoom_reset(self):
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._refresh_display()

    def _pan_start_event(self, event):
        self._pan_start = (event.x, event.y)

    def _pan_motion(self, event):
        if self._pan_start:
            dx = self._pan_start[0] - event.x
            dy = self._pan_start[1] - event.y
            self.pan_x += dx
            self.pan_y += dy
            self._pan_start = (event.x, event.y)
            self._refresh_display()

    def _pan_end(self, event):
        self._pan_start = None

    def delete_polygon(self, event):
        if not self.edit_mode:
            return
        if event.widget != self.canvas:
            return
        x, y = event.x, event.y
        ox, oy = self._display_to_original(x, y)
        
        delete_idx = None
        
        if self.edit_mode and self.selected_polygon_idx is not None:
            delete_idx = self.selected_polygon_idx
            self.deselect_polygon()
        else:
            for i, polygon in enumerate(self.polygons):
                if self.point_in_polygon(ox, oy, polygon):
                    delete_idx = i
                    break
        
        if delete_idx is not None:
            del self.polygons[delete_idx]
            del self.polygon_items[delete_idx]
            
            new_labels = {}
            for (path, idx), class_idx in self.polygon_labels.items():
                if path == self.image_path:
                    if idx < delete_idx:
                        new_labels[(path, idx)] = class_idx
                    elif idx > delete_idx:
                        new_labels[(path, idx - 1)] = class_idx
                else:
                    new_labels[(path, idx)] = class_idx
            self.polygon_labels = new_labels
            
            self.save_current_annotations()
            self.restore_annotations()
    
    def point_in_polygon(self, x, y, polygon):
        n = len(polygon)
        inside = False
        x1, y1 = polygon[0]
        for i in range(1, n + 1):
            x2, y2 = polygon[i % n]
            if y > min(y1, y2) and y <= max(y1, y2) and x <= max(x1, x2):
                if y1 != y2:
                    xinters = (y - y1) * (x2 - x1) / (y2 - y1) + x1
                if x1 == x2 or x <= xinters:
                    inside = not inside
            x1, y1 = x2, y2
        return inside
    
    def save_current_annotations(self):
        if hasattr(self, 'image_path') and self.polygons:
            original_polygons = []
            new_labels = {}
            new_idx = 0
            for old_idx, polygon in enumerate(self.polygons):
                if len(polygon) < 3:
                    continue
                original_polygons.append(list(polygon))
                old_key = (self.image_path, old_idx)
                if old_key in self.polygon_labels:
                    new_labels[(self.image_path, new_idx)] = self.polygon_labels[old_key]
                new_idx += 1
            self.all_annotations[self.image_path] = original_polygons
            for key in list(self.polygon_labels.keys()):
                if key[0] == self.image_path:
                    del self.polygon_labels[key]
            self.polygon_labels.update(new_labels)
        elif hasattr(self, 'image_path'):
            self.all_annotations[self.image_path] = []
    
    def restore_annotations(self):
        self.canvas.delete("polygon")
        self.canvas.delete("vertex_handle")
        self.polygons = []
        self.polygon_items = []
        
        if self.image_path in self.all_annotations:
            for poly_idx, original_polygon in enumerate(self.all_annotations[self.image_path]):
                self.polygons.append(list(original_polygon))
                
                poly_key = (self.image_path, poly_idx)
                class_idx = self.polygon_labels.get(poly_key)
                if class_idx:
                    color = self._color_for_class(class_idx)
                else:
                    color = 'green'
                
                display_pts = [self._original_to_display(x, y) for x, y in original_polygon]
                flat_coords = [coord for point in display_pts for coord in point]
                poly_id = self.canvas.create_polygon(flat_coords, outline=color, fill="", width=2, tags="polygon")
                
                x1, y1 = display_pts[-1]
                x2, y2 = display_pts[0]
                line_id = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2, tags="polygon")
                
                self.polygon_items.append([line_id, poly_id])
    
    def select_class(self, class_name):
        self.selected_class = class_name
        for name, btn in self.class_buttons.items():
            if name == class_name:
                btn.config(relief=tk.SUNKEN)
            else:
                btn.config(relief=tk.RAISED)
        
        # If in edit mode and polygon is selected, change its class
        if self.edit_mode and self.selected_polygon_idx is not None:
            if class_name in self.classes:
                class_idx = self.classes[class_name]
                poly_key = (self.image_path, self.selected_polygon_idx)
                self.polygon_labels[poly_key] = class_idx
                self.redraw_polygon(self.selected_polygon_idx)
                self.show_vertex_handles()
    
    def on_canvas_configure(self, event):
        self.reflow_class_buttons()
    
    def reflow_class_buttons(self):
        if not self.class_buttons:
            return
        
        canvas_width = self.class_buttons_canvas.winfo_width()
        if canvas_width <= 1:
            return
        
        for widget in self.class_buttons_frame.winfo_children():
            widget.grid_forget()
        
        x, row, col = 0, 0, 0
        for name, btn in self.class_buttons.items():
            btn.grid(row=row, column=col, padx=2, pady=2, sticky=tk.W)
            self.class_buttons_frame.update_idletasks()
            btn_width = btn.winfo_width()
            x += btn_width + 4
            
            if x > canvas_width - 20 and col > 0:
                row += 1
                col = 0
                x = btn_width + 4
                btn.grid(row=row, column=col, padx=2, pady=2, sticky=tk.W)
            else:
                col += 1
        
        self.class_buttons_frame.update_idletasks()
        self.class_buttons_canvas.config(height=min(self.class_buttons_frame.winfo_height() + 10, 150))
    
    def update_class_buttons(self):
        for widget in self.class_buttons_frame.winfo_children():
            widget.destroy()
        self.class_buttons.clear()
        
        # Re-assign colors sequentially based on class order
        self._class_colors = {}
        self._next_color_idx = 0
        for name, idx in self.classes.items():
            self._color_for_class(idx)
        
        for name, idx in self.classes.items():
            color = self._color_for_class(idx)
            btn = tk.Button(self.class_buttons_frame, text=name, bg=color, 
                          activebackground=color, highlightbackground=color,
                          command=lambda n=name: self.select_class(n))
            self.class_buttons[name] = btn
        
        self.root.after(100, self.reflow_class_buttons)
        self._update_filter_options()
    
    def _update_filter_options(self):
        options = ["All", "Unannotated"] + list(self.classes.keys())
        current = self.filter_dropdown.get()
        self.filter_dropdown['values'] = options
        if current not in options:
            self.filter_dropdown.set("All")

    def _apply_filter(self, event=None):
        if not self.image_files:
            return
        self.save_current_annotations()
        selected = self.filter_dropdown.get()
        if selected == "All":
            filtered = self.image_files
        elif selected == "Unannotated":
            filtered = [p for p in self.image_files
                        if not self.all_annotations.get(p)]
        else:
            # Filter by class name
            class_idx = self.classes.get(selected)
            if class_idx is None:
                filtered = self.image_files
            else:
                filtered = []
                for p in self.image_files:
                    for poly_idx in range(len(self.all_annotations.get(p, []))):
                        if self.polygon_labels.get((p, poly_idx)) == class_idx:
                            filtered.append(p)
                            break
        self._filtered_files = filtered
        filenames = [f"[{i+1}] {os.path.basename(f)}" for i, f in enumerate(filtered)]
        self.file_dropdown['values'] = filenames
        if filtered:
            self.current_index = 0
            self._set_current_from_filtered(0)
            self.load_current_image()
        else:
            self.file_dropdown.set("")
            self.filename_label.config(text="No images match filter")

    def _set_current_from_filtered(self, filtered_idx):
        """Set self.current_index to the index in self.image_files for the filtered selection."""
        if hasattr(self, '_filtered_files') and self._filtered_files:
            path = self._filtered_files[filtered_idx]
            self.current_index = self.image_files.index(path)

    def load_base_classes(self):
        data = copy.deepcopy(BASE_DATA)
        base_options = data["attribute"]["1"]["options"]

        if self._custom_classes:
            collisions = set(self._custom_classes.keys()) & set(base_options.keys())
            if collisions:
                messagebox.showerror(
                    "Index Collision",
                    f"Custom class indices collide with base data: {collisions}"
                )
                return
            for idx, name in self._custom_classes.items():
                base_options[idx] = name

        all_classes = {v: k for k, v in base_options.items()}

        if self._asin:
            produce_lower = self._asin.lower()
            self.classes = {name: idx for name, idx in all_classes.items()
                           if name.lower().startswith(produce_lower + " - ")}
        else:
            produce_name = self.prompt_produce_name()
            if produce_name:
                produce_lower = produce_name.lower()
                self.classes = {name: idx for name, idx in all_classes.items()
                               if name.lower().startswith(produce_lower + " - ")}
            else:
                self.classes = all_classes

        self.update_class_buttons()
        self.loaded_data = data
    
    def prompt_produce_name(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Filter Classes by Produce")
        dialog.geometry("600x120")
        dialog.transient(self.root)
        dialog.grab_set()
        
        tk.Label(dialog, text="Enter ASIN name (not case sensitive) to filter classes, e.g., strawberry:").pack(pady=10)
        entry = tk.Entry(dialog, width=30)
        entry.pack(pady=5)
        entry.focus()
        
        result = [None]
        
        def on_ok():
            result[0] = entry.get().strip()
            dialog.destroy()
        
        def on_cancel():
            dialog.destroy()
        
        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="OK", command=on_ok, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Cancel", command=on_cancel, width=10).pack(side=tk.LEFT, padx=5)
        
        entry.bind('<Return>', lambda e: on_ok())
        
        dialog.wait_window()
        return result[0]
    
    def manage_classes(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Manage Classes")
        dialog.geometry("400x350")
        
        tk.Label(dialog, text="Class Index:").grid(row=0, column=0, padx=5, pady=5)
        idx_entry = tk.Entry(dialog)
        idx_entry.grid(row=0, column=1, padx=5, pady=5)
        
        tk.Label(dialog, text="Class Name:").grid(row=1, column=0, padx=5, pady=5)
        name_entry = tk.Entry(dialog)
        name_entry.grid(row=1, column=1, padx=5, pady=5)
        
        selected_class = [None]
        
        def add_class():
            idx = idx_entry.get().strip()
            name = name_entry.get().strip()
            if idx and name:
                self.classes[name] = idx
                refresh_listbox()
                idx_entry.delete(0, tk.END)
                name_entry.delete(0, tk.END)
                self.update_class_buttons()
        
        def modify_class():
            if selected_class[0]:
                old_name = selected_class[0]
                new_idx = idx_entry.get().strip()
                new_name = name_entry.get().strip()
                if new_idx and new_name:
                    del self.classes[old_name]
                    self.classes[new_name] = new_idx
                    
                    # Update polygon labels
                    for key in list(self.polygon_labels.keys()):
                        if self.polygon_labels[key] == self.classes.get(old_name):
                            self.polygon_labels[key] = new_idx
                    
                    refresh_listbox()
                    idx_entry.delete(0, tk.END)
                    name_entry.delete(0, tk.END)
                    selected_class[0] = None
                    self.update_class_buttons()
        
        def on_select(event):
            selection = listbox.curselection()
            if selection:
                item = listbox.get(selection[0])
                idx, name = item.split(": ", 1)
                idx_entry.delete(0, tk.END)
                idx_entry.insert(0, idx)
                name_entry.delete(0, tk.END)
                name_entry.insert(0, name)
                selected_class[0] = name
        
        def refresh_listbox():
            listbox.delete(0, tk.END)
            for name, idx in self.classes.items():
                listbox.insert(tk.END, f"{idx}: {name}")
        
        btn_frame = tk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        tk.Button(btn_frame, text="Add Class", command=add_class).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Modify Class", command=modify_class).pack(side=tk.LEFT, padx=5)
        
        tk.Label(dialog, text="Current Classes:").grid(row=3, column=0, columnspan=2)
        listbox = tk.Listbox(dialog, height=10)
        listbox.grid(row=4, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")
        listbox.bind('<<ListboxSelect>>', on_select)
        
        refresh_listbox()
        
        dialog.grid_rowconfigure(4, weight=1)
        dialog.grid_columnconfigure(1, weight=1)
    
    def load_annotations(self):
        if not self.directory:
            messagebox.showwarning("No Directory", "Please load a directory first")
            return
        
        path = filedialog.askopenfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            self._loaded_annotation_path = path
            with open(path, "r") as f:
                data = json.load(f)
            
            file_dict = data.get("file", {})
            metadata_dict = data.get("metadata", {})
            attribute_dict = data.get("attribute", {})
            
            if "1" in attribute_dict and "options" in attribute_dict["1"]:
                all_classes = {v: k for k, v in attribute_dict["1"]["options"].items()}
                
                # Prompt for produce name to filter classes
                produce_name = self.prompt_produce_name()
                if produce_name:
                    produce_lower = produce_name.lower()
                    self.classes = {name: idx for name, idx in all_classes.items() 
                                   if name.lower().startswith(produce_lower + " - ")}
                else:
                    self.classes = all_classes
                
                self.update_class_buttons()
            
            self.root.update_idletasks()
            frame_width = self.canvas_frame.winfo_width()
            frame_height = self.canvas_frame.winfo_height()
            
            loaded_annotations = {}
            self.polygon_labels = {}
            
            for file_id, file_info in file_dict.items():
                fname = file_info["fname"]
                img_path = os.path.join(self.directory, fname)
                
                if os.path.exists(img_path):
                    polygons = []
                    poly_idx = 0
                    
                    for key, poly_data in metadata_dict.items():
                        if key.startswith(f"{file_id}_"):
                            coords = poly_data["xy"]
                            polygon = []
                            # Store in original image coordinates
                            for i in range(1, len(coords), 2):
                                x = coords[i]
                                y = coords[i+1]
                                polygon.append((x, y))
                            polygons.append(polygon)
                            
                            if "av" in poly_data and "1" in poly_data["av"]:
                                class_idx = poly_data["av"]["1"]
                                poly_key = (img_path, poly_idx)
                                self.polygon_labels[poly_key] = class_idx
                            
                            poly_idx += 1
                    
                    loaded_annotations[img_path] = polygons
            
            self.all_annotations = loaded_annotations
            self._saved_annotations = copy.deepcopy(loaded_annotations)
            self._saved_labels = copy.deepcopy(self.polygon_labels)
            self.loaded_data = data
            self.restore_annotations()
            messagebox.showinfo("Loaded", f"Annotations for {len(self.all_annotations)} image(s) loaded")
    
    def prompt_project_name(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Project Name")
        dialog.geometry("400x120")
        dialog.transient(self.root)
        dialog.grab_set()
        
        tk.Label(dialog, text="Enter project name:").pack(pady=10)
        entry = tk.Entry(dialog, width=40)
        entry.pack(pady=5)
        entry.focus()
        
        result = [None]
        
        def on_ok():
            result[0] = entry.get().strip()
            dialog.destroy()
        
        def on_cancel():
            dialog.destroy()
        
        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="OK", command=on_ok, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Cancel", command=on_cancel, width=10).pack(side=tk.LEFT, padx=5)
        
        entry.bind('<Return>', lambda e: on_ok())
        
        dialog.wait_window()
        return result[0]
    
    def _build_save_data(self, project_name=""):
        """Build the annotation JSON dict from current state."""
        attribute_dict = {}
        if self.loaded_data:
            attribute_dict = self.loaded_data.get("attribute", {})
        if "1" not in attribute_dict:
            attribute_dict["1"] = {"options": {}}
        attribute_dict["1"]["options"].update({idx: name for name, idx in self.classes.items()})

        file_dict = {}
        metadata_dict = {}
        next_fid = 1
        for img_path, polygons in self.all_annotations.items():
            if not polygons:
                continue
            fname = os.path.basename(img_path)
            file_id = str(next_fid)
            next_fid += 1
            file_dict[file_id] = {"fid": file_id, "fname": fname}
            for poly_idx, polygon in enumerate(polygons):
                while True:
                    rand = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
                    key = f"{file_id}_{rand}"
                    if key not in metadata_dict:
                        break
                coords = [2]
                for x, y in polygon:
                    coords.extend([int(x), int(y)])
                poly_key = (img_path, poly_idx)
                class_idx = self.polygon_labels.get(poly_key, "405")
                metadata_dict[key] = {
                    "vid": file_id,
                    "xy": coords,
                    "av": {"1": class_idx if class_idx else "405"}
                }
        return {
            "project": {"pname": project_name},
            "attribute": attribute_dict,
            "file": file_dict,
            "metadata": metadata_dict,
        }

    def save_annotations(self):
        self.save_current_annotations()
        
        if not self.all_annotations or all(not polys for polys in self.all_annotations.values()):
            messagebox.showwarning("No Annotations", "No polygons to save")
            return
        
        project_name = self.prompt_project_name()
        if not project_name:
            return
        
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            output = self._build_save_data(project_name)
            with open(path, "w") as f:
                json.dump(output, f, indent=4)
            # Remove autosave after successful manual save
            if os.path.exists(AUTOSAVE_PATH):
                os.remove(AUTOSAVE_PATH)
            messagebox.showinfo("Saved", f"Annotations for {len(output['file'])} image(s) saved to {path}")

    def generate_masks_dialog(self):
        if not self.directory:
            messagebox.showwarning("No Directory", "Please load a directory first")
            return
        self.save_current_annotations()
        if not self.all_annotations or all(not polys for polys in self.all_annotations.values()):
            messagebox.showwarning("No Annotations", "No polygons to generate masks from")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Generate Masks")
        dialog.geometry("550x500")
        dialog.transient(self.root)
        dialog.grab_set()

        # Output directory
        tk.Label(dialog, text="Output Directory:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        output_var = tk.StringVar(value=self.directory.rstrip("/") + "_masks")
        output_entry = tk.Entry(dialog, textvariable=output_var, width=40)
        output_entry.grid(row=0, column=1, padx=5, pady=5)
        tk.Button(dialog, text="Browse", command=lambda: output_var.set(
            filedialog.askdirectory() or output_var.get())).grid(row=0, column=2, padx=5, pady=5)

        # Default pixel value
        tk.Label(dialog, text="Default Pixel Value:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        bg_var = tk.StringVar(value="255")
        tk.Entry(dialog, textvariable=bg_var, width=10).grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)

        # Class-to-pixel mapping
        tk.Label(dialog, text="Class → Pixel Mapping (empty = use default):").grid(
            row=2, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(10, 0))

        mapping_frame = tk.Frame(dialog)
        mapping_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)

        # Headers
        tk.Label(mapping_frame, text="Class", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, padx=5)
        tk.Label(mapping_frame, text="Index", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=1, padx=5)
        tk.Label(mapping_frame, text="Pixel", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=2, padx=5)
        tk.Label(mapping_frame, text="Empty", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=3, padx=5)

        pixel_entries = {}
        empty_vars = {}
        for i, (name, idx) in enumerate(sorted(self.classes.items()), start=1):
            tk.Label(mapping_frame, text=name).grid(row=i, column=0, sticky=tk.W, padx=5)
            tk.Label(mapping_frame, text=idx).grid(row=i, column=1, padx=5)
            pix_var = tk.StringVar(value="")
            tk.Entry(mapping_frame, textvariable=pix_var, width=6).grid(row=i, column=2, padx=5)
            pixel_entries[idx] = pix_var
            empty_var = tk.BooleanVar(value=False)
            tk.Checkbutton(mapping_frame, variable=empty_var).grid(row=i, column=3, padx=5)
            empty_vars[idx] = empty_var

        # Priority
        tk.Label(dialog, text="Priority (comma-separated class indices, later = higher):").grid(
            row=4, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(10, 0))
        priority_var = tk.StringVar(value="")
        tk.Entry(dialog, textvariable=priority_var, width=40).grid(
            row=5, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)

        def on_generate():
            output_dir = output_var.get().strip()
            if not output_dir:
                messagebox.showwarning("Missing", "Please specify an output directory", parent=dialog)
                return
            try:
                background = int(bg_var.get().strip())
            except ValueError:
                messagebox.showwarning("Invalid", "Default pixel value must be an integer", parent=dialog)
                return

            # Build class_map from entries, skipping empty-checked classes
            class_map = {}
            for idx, pix_var in pixel_entries.items():
                if empty_vars[idx].get():
                    continue
                val = pix_var.get().strip()
                if val:
                    try:
                        class_map[idx] = int(val)
                    except ValueError:
                        messagebox.showwarning("Invalid", f"Pixel value for class {idx} must be an integer", parent=dialog)
                        return

            # Build priority
            priority_str = priority_var.get().strip()
            if priority_str:
                priority = [p.strip() for p in priority_str.split(",")]
            else:
                priority = list(class_map.keys())

            # Save current annotations to a temp file and generate masks
            tmp_ann = os.path.join(tempfile.gettempdir(), "polygon_annotation_tmp_masks.json")
            output_data = self._build_save_data()
            with open(tmp_ann, "w") as f:
                json.dump(output_data, f)
            try:
                generate_masks(tmp_ann, self.directory, output_dir,
                              class_map=class_map, priority=priority, background=background)
                messagebox.showinfo("Done", f"Masks saved to {output_dir}", parent=dialog)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to generate masks:\n{e}", parent=dialog)
            finally:
                if os.path.exists(tmp_ann):
                    os.remove(tmp_ann)
            dialog.destroy()

        tk.Button(dialog, text="Generate", command=on_generate).grid(
            row=6, column=0, columnspan=3, pady=15)

        dialog.grid_rowconfigure(3, weight=1)
        dialog.grid_columnconfigure(1, weight=1)

    def _schedule_autosave(self):
        self._autosave_id = self.root.after(self._autosave_interval, self._autosave)

    def _autosave(self):
        self.save_current_annotations()
        if self.all_annotations and any(polys for polys in self.all_annotations.values()):
            output = self._build_save_data()
            output["_session"] = {
                "image_dir": self.directory or "",
                "annotation_path": self._loaded_annotation_path or "",
                "asin": self._asin or "",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "current_index": self.current_index,
            }
            with open(AUTOSAVE_PATH, "w") as f:
                json.dump(output, f, indent=4)
        self._schedule_autosave()

    def _check_autosave(self):
        if not os.path.exists(AUTOSAVE_PATH):
            return
        try:
            with open(AUTOSAVE_PATH, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            os.remove(AUTOSAVE_PATH)
            return
        session = data.get("_session", {})
        msg = (
            "An auto-saved session was found:\n\n"
            f"  Image directory: {session.get('image_dir', 'N/A')}\n"
            f"  Annotation file: {session.get('annotation_path', 'N/A') or 'None'}\n"
            f"  ASIN: {session.get('asin', 'N/A')}\n"
            f"  Time: {session.get('time', 'N/A')}\n\n"
            "Would you like to restore it?"
        )
        if not messagebox.askyesno("Restore Auto-Save", msg):
            os.remove(AUTOSAVE_PATH)
            return
        # Restore session
        directory = session.get("image_dir", "")
        if directory and os.path.isdir(directory):
            self.directory = directory
            all_files = sorted(f for f in os.listdir(directory)
                               if f.lower().endswith(('.png', '.jpg', '.jpeg')))
            if self._name_format:
                ann_prefix, ann_suffix = self._parse_pattern(self._name_format[0])
                self.image_files = [os.path.join(directory, f) for f in all_files
                                    if self._match_pattern(f, ann_prefix, ann_suffix)]
            else:
                self.image_files = [os.path.join(directory, f) for f in all_files]
            self.image_files.sort()
            if self.image_files:
                filenames = [f"[{i+1}] {os.path.basename(f)}" for i, f in enumerate(self.image_files)]
                self.file_dropdown['values'] = filenames
        # Load annotations from autosave
        self._loaded_annotation_path = session.get("annotation_path") or None
        file_dict = data.get("file", {})
        metadata_dict = data.get("metadata", {})
        attribute_dict = data.get("attribute", {})
        if "1" in attribute_dict and "options" in attribute_dict["1"]:
            all_classes = {v: k for k, v in attribute_dict["1"]["options"].items()}
            if self._asin:
                asin_lower = self._asin.lower()
                self.classes = {name: idx for name, idx in all_classes.items()
                               if name.lower().startswith(asin_lower + " - ")}
            else:
                self.classes = all_classes
            self.update_class_buttons()
        loaded_annotations = {}
        self.polygon_labels = {}
        for file_id, file_info in file_dict.items():
            fname = file_info["fname"]
            img_path = os.path.join(self.directory, fname) if self.directory else fname
            if self.directory and os.path.exists(img_path):
                polygons = []
                poly_idx = 0
                for key, poly_data in metadata_dict.items():
                    if key.startswith(f"{file_id}_"):
                        coords = poly_data["xy"]
                        polygon = [(coords[i], coords[i+1]) for i in range(1, len(coords), 2)]
                        polygons.append(polygon)
                        if "av" in poly_data and "1" in poly_data["av"]:
                            self.polygon_labels[(img_path, poly_idx)] = poly_data["av"]["1"]
                        poly_idx += 1
                loaded_annotations[img_path] = polygons
        self.all_annotations = loaded_annotations
        self._saved_annotations = copy.deepcopy(loaded_annotations)
        self._saved_labels = copy.deepcopy(self.polygon_labels)
        data.pop("_session", None)
        self.loaded_data = data
        if self.image_files:
            self.current_index = min(session.get("current_index", 0), len(self.image_files) - 1)
            self.load_current_image()
        os.remove(AUTOSAVE_PATH)

def merge_annotations(annotation_files, image_dir, output_path, thresholds=None, merge_strategy="keep_both"):
    """Combine multiple annotation files, keeping only entries whose images exist.

    Args:
        annotation_files: List of paths to annotation JSON files.
        image_dir: Directory containing images. Only annotations whose
            ``fname`` is found in this directory are kept.
        output_path: Path to write the merged JSON file.
        thresholds: Optional dict passed to
            :func:`~nexus.seg.summarise_annotations.summarise` to print
            a summary of the merged annotations. If None, the default
            thresholds are used.
        merge_strategy: How to handle images annotated in multiple files.
            ``"keep_both"`` (default) retains all polygons from every file.
            ``"override"`` keeps only the polygons from the last file in
            *annotation_files* that contains annotations for a given image.
    """
    if merge_strategy not in ("keep_both", "override"):
        raise ValueError(f"Unknown merge_strategy: {merge_strategy!r}")
    from .summarise_annotations import summarise
    existing_images = set(os.listdir(image_dir))
    merged_file = {}
    merged_metadata = {}
    merged_options = {}
    project_name = ""
    next_fid = 1
    fname_to_fid = {}

    for ann_path in annotation_files:
        with open(ann_path, "r") as f:
            data = json.load(f)

        project_name = project_name or data.get("project", {}).get("pname", "")
        opts = data.get("attribute", {}).get("1", {}).get("options", {})
        merged_options.update(opts)

        old_file = data.get("file", {})
        old_meta = data.get("metadata", {})

        # Map old fid -> fname for files present in image_dir
        old_fid_to_fname = {}
        for fid, info in old_file.items():
            fname = info.get("fname", "")
            if fname not in existing_images:
                continue
            old_fid_to_fname[fid] = fname
            if fname not in fname_to_fid:
                fname_to_fid[fname] = str(next_fid)
                merged_file[str(next_fid)] = {"fid": str(next_fid), "fname": fname}
                next_fid += 1

        # Re-key metadata under new fids
        if merge_strategy == "override":
            overridden_fids = set()
            for key, meta in old_meta.items():
                old_fid = meta.get("vid", key.split("_")[0])
                if old_fid in old_fid_to_fname:
                    overridden_fids.add(fname_to_fid[old_fid_to_fname[old_fid]])
            merged_metadata = {k: v for k, v in merged_metadata.items() if v["vid"] not in overridden_fids}

        for key, meta in old_meta.items():
            old_fid = meta.get("vid", key.split("_")[0])
            if old_fid not in old_fid_to_fname:
                continue
            new_fid = fname_to_fid[old_fid_to_fname[old_fid]]
            while True:
                rand = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
                new_key = f"{new_fid}_{rand}"
                if new_key not in merged_metadata:
                    break
            merged_metadata[new_key] = {"vid": new_fid, "xy": meta["xy"], "av": meta.get("av", {})}

    # Keep only files that have polygons, re-key sequentially
    fids_with_polygons = {m["vid"] for m in merged_metadata.values()}
    fid_remap = {}
    final_file = {}
    new_idx = 1
    for old_fid, info in sorted(merged_file.items(), key=lambda x: int(x[0])):
        if old_fid not in fids_with_polygons:
            continue
        new_fid = str(new_idx)
        new_idx += 1
        fid_remap[old_fid] = new_fid
        final_file[new_fid] = {"fid": new_fid, "fname": info["fname"]}
    final_metadata = {}
    for key, meta in merged_metadata.items():
        new_fid = fid_remap[meta["vid"]]
        while True:
            rand = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
            new_key = f"{new_fid}_{rand}"
            if new_key not in final_metadata:
                break
        final_metadata[new_key] = {"vid": new_fid, "xy": meta["xy"], "av": meta.get("av", {})}

    output = {
        "project": {"pname": project_name},
        "attribute": {"1": {"options": merged_options}},
        "file": final_file,
        "metadata": final_metadata,
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=4)

    summarise(output_path, image_dir, thresholds=thresholds)

def polygon_annotation_with_reference(res="1600x1000", custom_classes=None, asin="strawberry", name_format=None, autosave_interval=5):
    """Launch the polygon annotation tool.

    Args:
        res: Window geometry string (default "1600x1000").
        custom_classes: Optional dict mapping class index strings to class names
            (e.g. {"500": "Blueberry - Decay"}). Must not collide with BASE_DATA.
        asin: Product name to filter classes at startup. If None, prompts the user.
        name_format: Optional list of glob-like patterns. The first pattern
            identifies annotation images; the rest identify reference images.
            Uses '*' as a wildcard for the shared stem. Example::

                ['*_cam0.jpg', '*_cam1.jpg', '*_cam2.jpg']

            If None, all images are annotation targets with no references.
        autosave_interval: Interval in minutes between automatic saves (default 5).
    """
    root = tk.Tk()
    root.geometry(res)
    app = PolygonAnnotationWithReference(root, custom_classes=custom_classes, asin=asin, name_format=name_format, autosave_interval=autosave_interval)
    def on_close():
        if app._autosave_id is not None:
            root.after_cancel(app._autosave_id)
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

if __name__ == "__main__":
    polygon_annotation_with_reference()