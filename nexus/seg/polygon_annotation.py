"""Polygon annotation tool with reference views

Fred Zhang <frezz@amazon.com>
"""
import os
import json
import copy
import random
import string
import tkinter as tk
from PIL import Image, ImageTk, ImageDraw
from tkinter import filedialog, messagebox, ttk

BASE_DATA = {
    "project": {"pname": ""},
    "attribute": {
        "1": {
            "options": {
                "0": "",
                "400": "Strawberry - Package",
                "401": "Strawberry - Decay",
                "402": "Strawberry - Overripe/Wet bruising",
                "403": "Strawberry - Mould",
                "404": "Strawberry - Condensation",
                "405": "Strawberry - BadInstance",
                "406": "Strawberry - Instance Fully Visible",
                "407": "Strawberry - Instance Partially Visible"
            }
        }
    },
    "file": {},
    "metadata": {}
}

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

    Controls:
        - Left-click: Add polygon vertex (click near first point to close).
        - Right-click: Close current polygon.
        - Double-click: Delete polygon under cursor.
        - Edit Mode: Select polygons to drag vertices or reassign classes.
        - Prev/Next Ref buttons: Scroll through reference images.
    """

    def __init__(self, root, custom_classes=None, asin="strawberry", name_format=None):
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
        
        tk.Button(control_frame, text="Clear Current", command=self.clear_current).pack(side=tk.LEFT)
        tk.Button(control_frame, text="Clear All", command=self.clear_all).pack(side=tk.LEFT)
        self.edit_mode_btn = tk.Button(control_frame, text="Edit Mode: OFF", command=self.toggle_edit_mode)
        self.edit_mode_btn.pack(side=tk.LEFT)
        tk.Button(control_frame, text="Manage Classes", command=self.manage_classes).pack(side=tk.LEFT)
        tk.Label(control_frame, text="Class:").pack(side=tk.LEFT)
        self.class_dropdown = ttk.Combobox(control_frame, state="readonly", width=15)
        self.class_dropdown.pack(side=tk.LEFT)
        tk.Button(control_frame, text="Load Annotations", command=self.load_annotations).pack(side=tk.LEFT)
        tk.Button(control_frame, text="Save Annotations", command=self.save_annotations).pack(side=tk.LEFT)
        tk.Button(control_frame, text="Revert", command=self.revert_annotations).pack(side=tk.LEFT)
        
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
        self.current_index = 0
        self.directory = None
        self.scale = 1.0
        self.max_width = 1200
        self.max_height = 800
        self.all_annotations = {}
        self.classes = {}
        self.polygon_labels = {}
        self.colors = ['green', 'blue', 'red', 'yellow', 'purple', 'orange', 'cyan', 'magenta']
        self.selected_class = None
        self.loaded_data = None
        self._saved_annotations = {}
        self._saved_labels = {}
        self.edit_mode = False
        self.selected_polygon_idx = None
        self.selected_vertex_idx = None
        self.vertex_handles = []
        
        self.canvas.bind("<Button-1>", self.add_point)
        self.canvas.bind("<Button-3>", self.close_polygon)
        self.canvas.bind("<Double-Button-1>", self.delete_polygon)
        
        self.load_base_classes()
    
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
            
            new_size = (int(self.image.width * self.scale), 
                       int(self.image.height * self.scale))
            display_image = self.image.resize(new_size, Image.LANCZOS)
            
            self.photo = ImageTk.PhotoImage(display_image)
            self.canvas.delete("all")
            self.canvas.config(width=new_size[0], height=new_size[1])
            self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
            self.image_path = path
            self.filename_label.config(text=f"{os.path.basename(path)} ({self.current_index + 1}/{len(self.image_files)})")
            self.file_dropdown.set(f"[{self.current_index + 1}] {os.path.basename(path)}")
            self.current_polygon = []
            
            self.load_reference_image(path)
            self.restore_annotations()
    
    def prev_image(self):
        if self.image_files and self.current_index > 0:
            self.current_index -= 1
            self.load_current_image()
    
    def next_image(self):
        if self.image_files and self.current_index < len(self.image_files) - 1:
            self.current_index += 1
            self.load_current_image()
    
    def on_file_selected(self, event):
        selected = self.file_dropdown.get()
        idx = int(selected.split("]", 1)[0].lstrip("[")) - 1
        self.current_index = idx
        self.load_current_image()
    
    def load_reference_image(self, main_path):
        for c in self.ref_canvases:
            c.destroy()
        self.ref_canvases = []
        self.ref_photos = []

        ref_patterns = self._name_format[1:] if self._name_format and len(self._name_format) > 1 else []
        if not ref_patterns:
            return

        basename = os.path.basename(main_path)
        ann_prefix, ann_suffix = self._parse_pattern(self._name_format[0])
        stem = self._extract_stem(basename, ann_prefix, ann_suffix)
        target_height = int(self.image.height * self.scale)

        for pattern in ref_patterns:
            ref_prefix, ref_suffix = self._parse_pattern(pattern)
            ref_name = ref_prefix + stem + ref_suffix
            ref_path = os.path.join(self.directory, ref_name)

            if os.path.exists(ref_path):
                ref_image = Image.open(ref_path)
                scale = target_height / ref_image.height
                new_size = (int(ref_image.width * scale), target_height)
                ref_image = ref_image.resize(new_size, Image.LANCZOS)
                photo = ImageTk.PhotoImage(ref_image)
            else:
                new_size = (int(self.image.width * self.scale), target_height)
                placeholder = Image.new('RGB', new_size, 'gray')
                draw = ImageDraw.Draw(placeholder)
                draw.text((placeholder.width // 2 - 80, placeholder.height // 2),
                          f"No ref: {ref_name}", fill='white')
                photo = ImageTk.PhotoImage(placeholder)
            self.ref_photos.append(photo)

            canvas = tk.Canvas(self.canvas_frame, width=new_size[0], height=new_size[1])
            canvas.pack(side=tk.LEFT, anchor=tk.N)
            canvas.create_image(0, 0, anchor=tk.NW, image=photo)
            self.ref_canvases.append(canvas)
    
    def toggle_edit_mode(self):
        self.edit_mode = not self.edit_mode
        if self.edit_mode:
            self.edit_mode_btn.config(text="Edit Mode: ON", relief=tk.SUNKEN)
            self.clear_current()
        else:
            self.edit_mode_btn.config(text="Edit Mode: OFF", relief=tk.RAISED)
            self.deselect_polygon()
            # Save and reload to ensure consistency
            self.save_current_annotations()
            self.restore_annotations()
    
    def select_polygon_for_edit(self, x, y):
        for i, polygon in enumerate(self.polygons):
            if self.point_in_polygon(x, y, polygon):
                self.selected_polygon_idx = i
                self.show_vertex_handles()
                return True
        return False
    
    def show_vertex_handles(self):
        self.canvas.delete("vertex_handle")
        self.vertex_handles = []
        if self.selected_polygon_idx is not None:
            polygon = self.polygons[self.selected_polygon_idx]
            for i, (x, y) in enumerate(polygon):
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
                if abs(x - vx) < 8 and abs(y - vy) < 8:
                    return i
        return None
    
    def add_point(self, event):
        if event.widget != self.canvas:
            return
        x, y = event.x, event.y
        
        if self.edit_mode:
            # Check if clicking on a vertex
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
            x0, y0 = self.current_polygon[0]
            distance = ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
            if distance < 10:
                self.close_polygon(event)
                return
        
        self.current_polygon.append((x, y))
        self.canvas.create_oval(x-3, y-3, x+3, y+3, fill="red", tags="temp")
        
        if len(self.current_polygon) > 1:
            x1, y1 = self.current_polygon[-2]
            self.canvas.create_line(x1, y1, x, y, fill="red", width=2, tags="temp")
    
    def drag_vertex(self, event):
        if self.selected_polygon_idx is not None and self.selected_vertex_idx is not None:
            x, y = event.x, event.y
            self.polygons[self.selected_polygon_idx][self.selected_vertex_idx] = (x, y)
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
                color = self.colors[int(class_idx) % len(self.colors)]
            else:
                color = 'green'
            
            flat_coords = [coord for point in polygon for coord in point]
            poly_id = self.canvas.create_polygon(flat_coords, outline=color, fill="", width=2, tags="polygon")
            
            x1, y1 = polygon[-1]
            x2, y2 = polygon[0]
            line_id = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2, tags="polygon")
            
            self.polygon_items[poly_idx] = [line_id, poly_id]
    
    def close_polygon(self, event):
        if event.widget != self.canvas:
            return
        if len(self.current_polygon) > 2:
            poly_idx = len(self.polygons)
            self.polygons.append(self.current_polygon[:])
            
            if self.selected_class and self.selected_class in self.classes:
                class_idx = self.classes[self.selected_class]
                color = self.colors[int(class_idx) % len(self.colors)]
            else:
                class_idx = None
                color = 'green'
            
            poly_key = (self.image_path, poly_idx)
            self.polygon_labels[poly_key] = class_idx
            
            x1, y1 = self.current_polygon[-1]
            x2, y2 = self.current_polygon[0]
            line_id = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2, tags="polygon")
            
            flat_coords = [coord for point in self.current_polygon for coord in point]
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
    
    def delete_polygon(self, event):
        if event.widget != self.canvas:
            return
        x, y = event.x, event.y
        
        delete_idx = None
        
        if self.edit_mode and self.selected_polygon_idx is not None:
            delete_idx = self.selected_polygon_idx
            self.deselect_polygon()
        else:
            for i, polygon in enumerate(self.polygons):
                if self.point_in_polygon(x, y, polygon):
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
                original_poly = [(x / self.scale, y / self.scale) for x, y in polygon]
                original_polygons.append(original_poly)
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
                display_polygon = [(x * self.scale, y * self.scale) for x, y in original_polygon]
                self.polygons.append(display_polygon)
                
                poly_key = (self.image_path, poly_idx)
                class_idx = self.polygon_labels.get(poly_key)
                if class_idx:
                    color = self.colors[int(class_idx) % len(self.colors)]
                else:
                    color = 'green'
                
                flat_coords = [coord for point in display_polygon for coord in point]
                poly_id = self.canvas.create_polygon(flat_coords, outline=color, fill="", width=2, tags="polygon")
                
                x1, y1 = display_polygon[-1]
                x2, y2 = display_polygon[0]
                line_id = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2, tags="polygon")
                
                self.polygon_items.append([line_id, poly_id])
    
    def select_class(self, class_name):
        self.selected_class = class_name
        self.class_dropdown.set(class_name)
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
        
        for name, idx in self.classes.items():
            color = self.colors[int(idx) % len(self.colors)]
            btn = tk.Button(self.class_buttons_frame, text=name, bg=color, 
                          activebackground=color, highlightbackground=color,
                          command=lambda n=name: self.select_class(n))
            self.class_buttons[name] = btn
        
        self.root.after(100, self.reflow_class_buttons)
    
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

        self.class_dropdown['values'] = list(self.classes.keys())
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
                self.class_dropdown['values'] = list(self.classes.keys())
                if not self.class_dropdown.get():
                    self.class_dropdown.set(name)
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
                    
                    self.class_dropdown['values'] = list(self.classes.keys())
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
                
                self.class_dropdown['values'] = list(self.classes.keys())
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
            if self.loaded_data:
                output = self.loaded_data.copy()
                file_dict = output.get("file", {})
                metadata_dict = output.get("metadata", {})
                attribute_dict = output.get("attribute", {})
            else:
                output = {}
                file_dict = {}
                metadata_dict = {}
                attribute_dict = {}
            
            # Build new file and metadata dicts with sequential keys
            new_file_dict = {}
            new_metadata_dict = {}
            next_fid = 1
            
            for img_path, polygons in self.all_annotations.items():
                if not polygons:
                    continue
                fname = os.path.basename(img_path)
                file_id = str(next_fid)
                next_fid += 1
                new_file_dict[file_id] = {"fid": file_id, "fname": fname}
                
                for poly_idx, polygon in enumerate(polygons):
                    random_str = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
                    key = f"{file_id}_{random_str}"
                    
                    coords = [2]
                    for i in range(len(polygon)):
                        x, y = polygon[i]
                        coords.extend([int(x), int(y)])
                    
                    poly_key = (img_path, poly_idx)
                    class_idx = self.polygon_labels.get(poly_key, "405")
                    
                    new_metadata_dict[key] = {
                        "vid": file_id,
                        "xy": coords,
                        "av": {"1": class_idx if class_idx else "405"}
                    }
            
            file_dict = new_file_dict
            metadata_dict = new_metadata_dict
            
            # Update attribute with all classes (loaded + new)
            if "1" not in attribute_dict:
                attribute_dict["1"] = {"options": {}}
            attribute_dict["1"]["options"].update({idx: name for name, idx in self.classes.items()})
            
            # Update project name
            if "project" not in output:
                output["project"] = {}
            output["project"]["pname"] = project_name
            
            output["file"] = file_dict
            output["attribute"] = attribute_dict
            output["metadata"] = metadata_dict
            
            with open(path, "w") as f:
                json.dump(output, f, indent=4)
            messagebox.showinfo("Saved", f"Annotations for {len(file_dict)} image(s) saved to {path}")

def merge_annotations(annotation_files, image_dir, output_path):
    """Combine multiple annotation files, keeping only entries whose images exist.

    Args:
        annotation_files: List of paths to annotation JSON files.
        image_dir: Directory containing images. Only annotations whose
            ``fname`` is found in this directory are kept.
        output_path: Path to write the merged JSON file.
    """
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
        for key, meta in old_meta.items():
            old_fid = meta.get("vid", key.split("_")[0])
            if old_fid not in old_fid_to_fname:
                continue
            new_fid = fname_to_fid[old_fid_to_fname[old_fid]]
            rand = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
            new_key = f"{new_fid}_{rand}"
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
        rand = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        new_key = f"{new_fid}_{rand}"
        final_metadata[new_key] = {"vid": new_fid, "xy": meta["xy"], "av": meta.get("av", {})}

    output = {
        "project": {"pname": project_name},
        "attribute": {"1": {"options": merged_options}},
        "file": final_file,
        "metadata": final_metadata,
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=4)

def polygon_annotation_with_reference(res="1600x1000", custom_classes=None, asin="strawberry", name_format=None):
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
    """
    root = tk.Tk()
    root.geometry(res)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    app = PolygonAnnotationWithReference(root, custom_classes=custom_classes, asin=asin, name_format=name_format)
    root.mainloop()

if __name__ == "__main__":
    polygon_annotation_with_reference()