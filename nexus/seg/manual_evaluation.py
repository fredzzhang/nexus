"""Manual evaluation tool for TwoStageGrader inference artefacts.

A tkinter GUI that loads source images and their TwoStageGrader inference
artefacts, visualises each detected instance (bounding box coloured by the
model's predicted grade, plus the semantic defect-mask overlay), and lets a
human relabel every instance as grade A or C. Once instances are labelled the
tool reports grade A / grade C precision, recall and F1 (treating the manual
labels as ground truth and the model grades as predictions).

Expected artefact directory layout (same as a TwoStageGrader run), keyed by
the image stem ``<stem>``:

    <stem>_detected_instances.json   per-instance id, bbox, score, grade
    <stem>_inference_output.json     image size + defective instances
    <stem>_grader_metrics.json       per-instance / per-defect geometry
    <stem>_seg_inst_mask.png         3-channel mask: ch0 = defect class id,
                                     ch1 = instance id, ch2 unused

Manual labels are persisted to ``manual_labels.json`` in the artefact
directory so a labelling session can be stopped and resumed.

Usage:
    python -m nexus.seg.manual_evaluation \
        --image-dir /path/to/cur_img --res-dir /path/to/cur_res

Fred Zhang <fredzz@amazon.com>
"""
import os
import math
import json
import argparse
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk, ImageDraw, ImageFont

IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
LABELS_FILENAME = "manual_labels.json"

# Grade -> RGB. Used for instance boxes and label chips.
GRADE_COLOUR = {
    "A": (0, 200, 0),
    "C": (220, 30, 30),
}
SELECTED_COLOUR = (255, 215, 0)  # gold highlight for the selected instance

# Defect class definitions per produce type. Mask channel-0 values 1..N are
# defect classes; the mapping is produce-specific. Kept in sync with
# JohariAgentWorkflow/scripts/visualise_graded_crates.py (PRODUCE_CONFIGS),
# which is the canonical mask-value -> defect-name reference for the
# TwoStageGrader workflow. Colours here are fixed RGB triples (a tab10-like
# palette) so this module has no matplotlib dependency.
_PALETTE = [
    (31, 119, 180),   # blue
    (255, 127, 14),   # orange
    (44, 160, 44),    # green
    (214, 39, 40),    # red
    (148, 103, 189),  # purple
    (140, 86, 75),    # brown
    (227, 119, 194),  # pink
    (127, 127, 127),  # grey
    (188, 189, 34),   # olive
    (23, 190, 207),   # cyan
]

# produce_type -> {mask_value: (defect_name, palette_index)}
PRODUCE_DEFECTS = {
    "strawberry": {1: ("Overripe", 0), 2: ("Decay", 1), 3: ("Mould", 2)},
    "raspberry": {
        1: ("Discolouration", 0), 2: ("Overripe", 1), 3: ("Crumbled", 2),
        4: ("Crushed", 3), 5: ("Leaking", 4), 6: ("Decay", 5), 7: ("Mould", 8),
    },
    "blackberry": {
        1: ("Discolouration", 0), 2: ("Crushed", 3), 3: ("Leaking", 1),
        4: ("Mould", 6), 5: ("Shrivels", 5),
    },
    "cucumber": {
        1: ("Surface damage", 0), 2: ("Decay/rotten", 1), 3: ("Shrivelled", 2),
        4: ("Ripeness issues", 3), 5: ("Broken", 4), 6: ("Cuts", 5),
        7: ("Mould", 8),
    },
    "lemon": {
        1: ("Surface damage", 0), 5: ("Pressure damage", 1),
        2: ("Decay/rotten", 2), 3: ("Mould", 3), 4: ("Insects", 4),
        6: ("Expose/flash", 5),
    },
    "apple": {
        1: ("Surface damage", 0), 3: ("Decay/rotten", 1), 2: ("Dehydration", 2),
        4: ("Mould", 3), 5: ("Insects", 4), 6: ("Pressure damage", 5),
        7: ("Skin broken", 8),
    },
    "orange": {
        1: ("Surface damage", 0), 2: ("Decay/rotten", 1), 3: ("Mould", 3),
        4: ("Insects", 4), 5: ("Pressure damage", 5), 6: ("Expose/flash", 8),
    },
}
DEFAULT_PRODUCE = "strawberry"


def defect_colour_map(produce_type):
    """Return {mask_value: (RGB, defect_name)} for the given produce type."""
    defects = PRODUCE_DEFECTS.get(produce_type, PRODUCE_DEFECTS[DEFAULT_PRODUCE])
    return {val: (_PALETTE[idx % len(_PALETTE)], name)
            for val, (name, idx) in defects.items()}


# Defect channel (channel 0) uses 255 for "no defect"; only values 1..N are
# defect classes. The overlay iterates the produce map keys (1..N) directly, so
# this constant is only a defensive guard.
MASK_BACKGROUND = 255
OVERLAY_ALPHA = 0.45
DISPLAY_HEIGHT = 800


def bbox_corners(bbox):
    """Return the 4 (x, y) corner points of an instance bbox, in order.

    Handles both formats seen in TwoStageGrader detected_instances.json (kept
    in sync with JohariAgentWorkflow/scripts/visualise_graded_crates.py):
    - 4-value axis-aligned  [x1, y1, x2, y2]
    - 5-value rotated (OBB)  [cx, cy, w, h, angle_deg]  (e.g. cucumber)
    """
    if len(bbox) == 4:
        x1, y1, x2, y2 = bbox
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    if len(bbox) == 5:
        cx, cy, w, h, ang = bbox
        t = math.radians(ang)
        c, s = math.cos(t), math.sin(t)
        local = [(-w / 2, -h / 2), (w / 2, -h / 2),
                 (w / 2, h / 2), (-w / 2, h / 2)]
        return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in local]
    raise ValueError(f"Unsupported bbox length {len(bbox)}: {bbox}")


def polygon_area(corners):
    """Return the (absolute) area of a polygon via the shoelace formula."""
    n = len(corners)
    acc = 0.0
    for i in range(n):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % n]
        acc += x0 * y1 - x1 * y0
    return abs(acc) / 2.0


def point_in_polygon(px, py, corners):
    """True if (px, py) is inside the polygon (ray-casting, even-odd rule)."""
    inside = False
    n = len(corners)
    j = n - 1
    for i in range(n):
        xi, yi = corners[i]
        xj, yj = corners[j]
        if ((yi > py) != (yj > py)) and \
                (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _find_font(size):
    """Return a truetype font at the requested size, falling back gracefully."""
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


class ManualEvaluation:
    """A tkinter GUI to review TwoStageGrader inference and relabel instances."""

    def __init__(self, root, image_dir=None, res_dir=None,
                 labels_path=None, display_height=DISPLAY_HEIGHT,
                 produce_type=DEFAULT_PRODUCE):
        self.root = root
        self.root.title("Manual Evaluation - TwoStageGrader")

        self.image_dir = image_dir
        self.res_dir = res_dir
        self.labels_path = labels_path
        self.display_height = display_height
        self.produce_type = produce_type if produce_type in PRODUCE_DEFECTS \
            else DEFAULT_PRODUCE

        # Per-session state.
        self.stems = []                 # ordered list of image stems
        self.image_paths = {}           # stem -> source image path
        self.current_index = -1
        self.instances = []             # list of instance dicts for current image
        self.selected_id = None         # currently selected instance id
        # labels[stem][instance_id] = "A" | "C"
        self.labels = {}

        # Rendering state for the current image.
        self._display_image = None      # ImageTk.PhotoImage kept alive
        self._scale = 1.0               # display / original scale factor
        self._panel_w = 0               # width of one (scaled) image panel
        self._panel_gap = 8             # gap in px between the two panels
        self._inst_mask = None          # numpy array of instance ids (orig res)
        self._orig_size = (0, 0)        # (w, h) of the source image
        self._font = _find_font(20)
        self._small_font = _find_font(16)

        self._build_ui()

        if self.image_dir and self.res_dir:
            self._load_directories()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        top = tk.Frame(self.root)
        top.pack(side=tk.TOP, fill=tk.X)

        tk.Button(top, text="Load Directories",
                  command=self.load_directories_dialog).pack(side=tk.LEFT)
        tk.Button(top, text="< Prev", command=self.prev_image).pack(side=tk.LEFT)
        tk.Button(top, text="Next >", command=self.next_image).pack(side=tk.LEFT)

        self.file_dropdown = ttk.Combobox(top, state="readonly", width=44)
        self.file_dropdown.pack(side=tk.LEFT, padx=10)
        self.file_dropdown.bind("<<ComboboxSelected>>", self._on_dropdown_select)

        tk.Label(top, text="Produce:").pack(side=tk.LEFT, padx=(10, 0))
        self.produce_dropdown = ttk.Combobox(
            top, state="readonly", width=12,
            values=sorted(PRODUCE_DEFECTS.keys()))
        self.produce_dropdown.set(self.produce_type)
        self.produce_dropdown.pack(side=tk.LEFT, padx=5)
        self.produce_dropdown.bind("<<ComboboxSelected>>", self._on_produce_select)

        self.progress_label = tk.Label(top, text="No images loaded")
        self.progress_label.pack(side=tk.LEFT, padx=10)

        tk.Button(top, text="Metrics popup",
                  command=self.show_metrics).pack(side=tk.RIGHT, padx=5)
        tk.Button(top, text="Save Labels",
                  command=self.save_labels).pack(side=tk.RIGHT)

        # Main body: image canvas on the left, instance panel on the right.
        body = tk.Frame(self.root)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(body, cursor="hand2", background="#222222")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        side = tk.Frame(body, width=320)
        side.pack(side=tk.RIGHT, fill=tk.Y)
        side.pack_propagate(False)

        tk.Label(side, text="Instances (click to select)",
                 font=("TkDefaultFont", 11, "bold")).pack(side=tk.TOP, pady=4)

        list_frame = tk.Frame(side)
        list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.inst_list = tk.Listbox(list_frame, yscrollcommand=scrollbar.set,
                                    activestyle="dotbox", exportselection=False)
        self.inst_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.inst_list.yview)
        self.inst_list.bind("<<ListboxSelect>>", self._on_list_select)

        # Labelling controls.
        controls = tk.Frame(side)
        controls.pack(side=tk.TOP, fill=tk.X, pady=6)
        tk.Button(controls, text="Grade A  [a]",
                  command=lambda: self.label_selected("A"),
                  bg="#cceecc").pack(side=tk.LEFT, expand=True, fill=tk.X)
        tk.Button(controls, text="Grade C  [c]",
                  command=lambda: self.label_selected("C"),
                  bg="#eecccc").pack(side=tk.LEFT, expand=True, fill=tk.X)
        tk.Button(side, text="Clear label [x]",
                  command=lambda: self.label_selected(None)).pack(
                      side=tk.TOP, fill=tk.X)
        tk.Button(side, text="Copy model grades -> labels",
                  command=self.accept_model_grades).pack(side=tk.TOP, fill=tk.X, pady=2)

        self.summary_label = tk.Label(side, text="", justify=tk.LEFT,
                                      anchor="w", font=("TkFixedFont", 10))
        self.summary_label.pack(side=tk.BOTTOM, fill=tk.X, pady=6)

        # Persistent panel showing the most recently computed metrics, so they
        # remain visible after the pop-up is dismissed.
        tk.Label(side, text="Last computed metrics",
                 font=("TkDefaultFont", 10, "bold")).pack(side=tk.BOTTOM)
        self.metrics_text = tk.Text(side, height=16, width=38, state=tk.DISABLED,
                                    font=("TkFixedFont", 9), wrap=tk.NONE)
        self.metrics_text.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 6))

        # Keyboard shortcuts.
        self.root.bind("a", lambda e: self.label_selected("A"))
        self.root.bind("c", lambda e: self.label_selected("C"))
        self.root.bind("x", lambda e: self.label_selected(None))
        self.root.bind("<Left>", lambda e: self.prev_image())
        self.root.bind("<Right>", lambda e: self.next_image())

    # ------------------------------------------------------------- loading --
    def load_directories_dialog(self):
        """Prompt for the image and artefact directories."""
        image_dir = filedialog.askdirectory(title="Select image directory")
        if not image_dir:
            return
        res_dir = filedialog.askdirectory(title="Select artefact (result) directory")
        if not res_dir:
            return
        self.image_dir = image_dir
        self.res_dir = res_dir
        self.labels_path = None
        self._load_directories()

    def _load_directories(self):
        """Index images that have a matching detected-instances artefact."""
        if self.labels_path is None:
            self.labels_path = os.path.join(self.res_dir, LABELS_FILENAME)

        image_lookup = {}
        for f in sorted(os.listdir(self.image_dir)):
            stem, ext = os.path.splitext(f)
            if ext.lower() in IMAGE_EXTS:
                image_lookup[stem] = os.path.join(self.image_dir, f)

        stems = []
        for stem, path in image_lookup.items():
            if os.path.exists(self._artefact(stem, "detected_instances.json")):
                stems.append(stem)

        if not stems:
            messagebox.showwarning(
                "No data",
                "No images with matching *_detected_instances.json artefacts "
                "were found. Check the directories.")
            return

        self.stems = stems
        self.image_paths = image_lookup
        self._load_labels()

        self.file_dropdown["values"] = self.stems
        self.current_index = 0
        self._show_current()

    def _artefact(self, stem, suffix):
        """Return the path to ``<stem>_<suffix>`` in the artefact directory."""
        return os.path.join(self.res_dir, f"{stem}_{suffix}")

    def _load_labels(self):
        """Load persisted manual labels, if any."""
        self.labels = {}
        if self.labels_path and os.path.exists(self.labels_path):
            try:
                with open(self.labels_path) as fh:
                    raw = json.load(fh)
                # Normalise instance-id keys to ints.
                for stem, per_inst in raw.items():
                    self.labels[stem] = {int(k): v for k, v in per_inst.items()}
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                messagebox.showwarning(
                    "Labels", f"Could not read existing labels: {exc}")

    def save_labels(self):
        """Persist manual labels to disk."""
        if not self.labels_path:
            return
        # Drop empty per-image dicts to keep the file tidy.
        out = {stem: {str(k): v for k, v in per_inst.items()}
               for stem, per_inst in self.labels.items() if per_inst}
        try:
            with open(self.labels_path, "w") as fh:
                json.dump(out, fh, indent=2)
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self._update_summary()

    # --------------------------------------------------------- navigation --
    def _on_dropdown_select(self, _event):
        stem = self.file_dropdown.get()
        if stem in self.stems:
            self.current_index = self.stems.index(stem)
            self._show_current()

    def _on_produce_select(self, _event):
        """Switch the produce-specific defect colour map and redraw."""
        self.produce_type = self.produce_dropdown.get()
        stem = self._current_stem()
        if stem is not None:
            img = Image.open(self.image_paths[stem]).convert("RGB")
            self._render(img)

    def prev_image(self):
        if self.stems and self.current_index > 0:
            self.save_labels()
            self.current_index -= 1
            self._show_current()

    def next_image(self):
        if self.stems and self.current_index < len(self.stems) - 1:
            self.save_labels()
            self.current_index += 1
            self._show_current()

    # ------------------------------------------------------------ display --
    def _current_stem(self):
        if 0 <= self.current_index < len(self.stems):
            return self.stems[self.current_index]
        return None

    def _show_current(self):
        stem = self._current_stem()
        if stem is None:
            return
        self.selected_id = None

        with open(self._artefact(stem, "detected_instances.json")) as fh:
            self.instances = json.load(fh)

        # Load the source image and the instance mask (channel 1 = instance id).
        img = Image.open(self.image_paths[stem]).convert("RGB")
        self._orig_size = img.size  # (w, h)
        self._inst_mask = self._load_instance_mask(stem, img.size)

        self.file_dropdown.set(stem)
        self.progress_label.config(
            text=f"{self.current_index + 1}/{len(self.stems)}   {stem}")

        self._render(img)
        self._refresh_instance_list()
        self._update_summary()

    def _load_instance_mask(self, stem, size):
        """Return an (H, W) int array of instance ids, or None if unavailable."""
        mask_path = self._artefact(stem, "seg_inst_mask.png")
        if not os.path.exists(mask_path):
            return None
        mask = np.array(Image.open(mask_path))
        if mask.ndim == 3:
            inst = mask[:, :, 1]
        else:
            inst = mask
        # Resize to the source image if needed (nearest neighbour).
        if (inst.shape[1], inst.shape[0]) != size:
            inst_img = Image.fromarray(inst).resize(size, Image.NEAREST)
            inst = np.array(inst_img)
        return inst

    def _defect_overlay(self, stem, size):
        """Return an RGBA overlay of the defect mask (channel 0), or None."""
        mask_path = self._artefact(stem, "seg_inst_mask.png")
        if not os.path.exists(mask_path):
            return None
        mask = np.array(Image.open(mask_path))
        cls = mask[:, :, 0] if mask.ndim == 3 else mask
        if (cls.shape[1], cls.shape[0]) != size:
            cls = np.array(Image.fromarray(cls).resize(size, Image.NEAREST))

        rgba = np.zeros((cls.shape[0], cls.shape[1], 4), dtype=np.uint8)
        alpha = int(OVERLAY_ALPHA * 255)
        for class_id, (colour, _label) in defect_colour_map(self.produce_type).items():
            if class_id == MASK_BACKGROUND:
                continue
            sel = cls == class_id
            if sel.any():
                rgba[sel, 0] = colour[0]
                rgba[sel, 1] = colour[1]
                rgba[sel, 2] = colour[2]
                rgba[sel, 3] = alpha
        return Image.fromarray(rgba, mode="RGBA")

    def _draw_boxes(self, img):
        """Draw instance boxes and grade tags onto a copy of ``img``."""
        canvas = img.copy()
        draw = ImageDraw.Draw(canvas)
        labels_for_stem = self.labels.get(self._current_stem(), {})
        for inst in self.instances:
            iid = inst["id"]
            corners = bbox_corners(inst["bbox"])
            grade = inst.get("grade", "?")
            colour = GRADE_COLOUR.get(grade, (150, 150, 150))
            selected = iid == self.selected_id
            width = 6 if selected else 3
            box_colour = SELECTED_COLOUR if selected else colour
            # Polygon handles both axis-aligned and rotated (OBB) boxes.
            draw.polygon(corners, outline=box_colour, width=width)

            manual = labels_for_stem.get(iid)
            tag = f"#{iid} {grade}"
            if manual:
                tag += f" ->{manual}"
            # Anchor the tag at the top-most corner of the box.
            ax, ay = min(corners, key=lambda p: (p[1], p[0]))
            tw = draw.textlength(tag, font=self._font)
            th = 24
            ty = max(0, ay - th)
            draw.rectangle([ax, ty, ax + tw + 8, ty + th], fill=box_colour)
            draw.text((ax + 4, ty + 2), tag, fill=(0, 0, 0), font=self._font)
        return canvas

    def _render(self, base_img):
        """Draw the original (left) and mask-overlaid (right) views side by side."""
        stem = self._current_stem()
        base_rgba = base_img.convert("RGBA")

        # Right panel: defect-mask overlay composited under the boxes.
        overlaid = base_rgba
        overlay = self._defect_overlay(stem, base_img.size)
        if overlay is not None:
            overlaid = Image.alpha_composite(base_rgba, overlay)

        left = self._draw_boxes(base_rgba)
        right = self._draw_boxes(overlaid)

        # Scale each panel to the display height.
        w, h = base_img.size
        self._scale = self.display_height / h
        panel_w = max(1, int(w * self._scale))
        self._panel_w = panel_w
        size = (panel_w, self.display_height)
        left_disp = left.resize(size, Image.LANCZOS).convert("RGB")
        right_disp = right.resize(size, Image.LANCZOS).convert("RGB")

        # Compose the two panels with a thin gap between them.
        gap = self._panel_gap
        combined = Image.new("RGB", (panel_w * 2 + gap, self.display_height),
                             (34, 34, 34))
        combined.paste(left_disp, (0, 0))
        combined.paste(right_disp, (panel_w + gap, 0))
        self._display_image = ImageTk.PhotoImage(combined)

        self.canvas.delete("all")
        self.canvas.config(width=combined.width, height=combined.height,
                           scrollregion=(0, 0, combined.width, combined.height))
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self._display_image)
        # Panel captions.
        self.canvas.create_text(6, 6, anchor=tk.NW, text="Original",
                                fill="#ffffff", font=("TkDefaultFont", 12, "bold"))
        self.canvas.create_text(panel_w + gap + 6, 6, anchor=tk.NW,
                                text="Detections + defect mask", fill="#ffffff",
                                font=("TkDefaultFont", 12, "bold"))

    # ------------------------------------------------------------ selection --
    def _on_canvas_click(self, event):
        """Select the instance under the cursor (mask first, then bbox).

        Clicks in either panel (original or overlaid) map to the same original
        image coordinates.
        """
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        # Map the x coordinate back into a single panel's local space.
        stride = self._panel_w + self._panel_gap
        if cx >= stride:
            cx -= stride
        ox = int(cx / self._scale)
        oy = int(cy / self._scale)
        w, h = self._orig_size
        if not (0 <= ox < w and 0 <= oy < h):
            return

        hit = None
        # Prefer the instance mask for a pixel-accurate hit.
        if self._inst_mask is not None:
            iid = int(self._inst_mask[oy, ox])
            if iid != 0 and any(i["id"] == iid for i in self.instances):
                hit = iid
        # Fall back to the smallest containing bounding box (polygon test
        # handles both axis-aligned and rotated boxes).
        if hit is None:
            best_area = None
            for inst in self.instances:
                corners = bbox_corners(inst["bbox"])
                if point_in_polygon(ox, oy, corners):
                    area = polygon_area(corners)
                    if best_area is None or area < best_area:
                        best_area = area
                        hit = inst["id"]
        if hit is not None:
            self._select_instance(hit)

    def _on_list_select(self, _event):
        sel = self.inst_list.curselection()
        if sel:
            self._select_instance(self.instances[sel[0]]["id"])

    def _select_instance(self, iid):
        self.selected_id = iid
        # Sync the listbox selection.
        for idx, inst in enumerate(self.instances):
            if inst["id"] == iid:
                self.inst_list.selection_clear(0, tk.END)
                self.inst_list.selection_set(idx)
                self.inst_list.see(idx)
                break
        # Redraw with the highlight.
        img = Image.open(self.image_paths[self._current_stem()]).convert("RGB")
        self._render(img)

    # --------------------------------------------------------- labelling --
    def label_selected(self, grade):
        """Set (or clear) the manual grade of the selected instance."""
        if self.selected_id is None:
            return
        stem = self._current_stem()
        per_inst = self.labels.setdefault(stem, {})
        if grade is None:
            per_inst.pop(self.selected_id, None)
        else:
            per_inst[self.selected_id] = grade

        # Auto-advance selection to the next unlabelled instance for speed.
        self._refresh_instance_list()
        img = Image.open(self.image_paths[stem]).convert("RGB")
        self._render(img)
        self._update_summary()
        self.save_labels()
        if grade is not None:
            self._advance_to_next_unlabelled()

    def accept_model_grades(self):
        """Copy every model grade on the current image into the manual labels."""
        stem = self._current_stem()
        if stem is None:
            return
        per_inst = self.labels.setdefault(stem, {})
        for inst in self.instances:
            if "grade" in inst and inst["grade"] in GRADE_COLOUR:
                per_inst[inst["id"]] = inst["grade"]
        self._refresh_instance_list()
        img = Image.open(self.image_paths[stem]).convert("RGB")
        self._render(img)
        self._update_summary()
        self.save_labels()

    def _advance_to_next_unlabelled(self):
        stem = self._current_stem()
        per_inst = self.labels.get(stem, {})
        ids = [i["id"] for i in self.instances]
        if self.selected_id in ids:
            start = ids.index(self.selected_id)
            for offset in range(1, len(ids) + 1):
                nxt = ids[(start + offset) % len(ids)]
                if nxt not in per_inst:
                    self._select_instance(nxt)
                    return

    def _refresh_instance_list(self):
        stem = self._current_stem()
        per_inst = self.labels.get(stem, {})
        self.inst_list.delete(0, tk.END)
        for inst in self.instances:
            iid = inst["id"]
            grade = inst.get("grade", "?")
            score = inst.get("score", 0.0)
            manual = per_inst.get(iid, "-")
            self.inst_list.insert(
                tk.END,
                f"#{iid:>2}  pred:{grade}  manual:{manual}  ({score:.2f})")

    # ------------------------------------------------------------ metrics --
    def _confusion(self):
        """Tally the model-vs-manual confusion across all labelled instances.

        Returns a dict keyed by (predicted, actual) grade with counts, plus
        totals for labelled and total instances.
        """
        counts = {("A", "A"): 0, ("A", "C"): 0, ("C", "A"): 0, ("C", "C"): 0}
        labelled = 0
        total = 0
        # Build a quick lookup of model grades per stem.
        for stem in self.stems:
            try:
                with open(self._artefact(stem, "detected_instances.json")) as fh:
                    instances = json.load(fh)
            except OSError:
                continue
            per_inst = self.labels.get(stem, {})
            for inst in instances:
                total += 1
                iid = inst["id"]
                if iid not in per_inst:
                    continue
                pred = inst.get("grade")
                actual = per_inst[iid]
                if (pred, actual) in counts:
                    counts[(pred, actual)] += 1
                    labelled += 1
        return counts, labelled, total

    def _grade_stats(self, grade, counts):
        """Return per-grade (TP, n_pred, n_gt, precision, recall, f1).

        The given grade is the positive class: a true positive is an instance
        the model predicted as ``grade`` and that the manual label also marks as
        ``grade``. n_pred is how many instances the model predicted as ``grade``
        (among labelled ones); n_gt is how many the manual labels mark as
        ``grade``. precision = TP / n_pred, recall = TP / n_gt.
        """
        other = "A" if grade == "C" else "C"
        tp = counts[(grade, grade)]
        n_pred = tp + counts[(grade, other)]     # predicted `grade`
        n_gt = tp + counts[(other, grade)]       # actually `grade`
        precision = tp / n_pred if n_pred else 0.0
        recall = tp / n_gt if n_gt else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        return tp, n_pred, n_gt, precision, recall, f1

    def _metrics_text(self):
        counts, labelled, total = self._confusion()

        lines = [
            f"Labelled {labelled} / {total} instances",
            "",
        ]
        for grade in ("A", "C"):
            tp, n_pred, n_gt, p, r, f1 = self._grade_stats(grade, counts)
            lines += [
                f"Grade {grade}:",
                f"  TP = {tp}   Predictions = {n_pred}   Ground truth = {n_gt}",
                f"  Precision = TP/Pred = {tp}/{n_pred} = {p:.3f}",
                f"  Recall    = TP/GT   = {tp}/{n_gt} = {r:.3f}",
                f"  F1 = {f1:.3f}",
                "",
            ]
        lines += [
            "Confusion (pred x actual):",
            "            act A   act C",
            f"  pred A    {counts[('A','A')]:>5}   {counts[('A','C')]:>5}",
            f"  pred C    {counts[('C','A')]:>5}   {counts[('C','C')]:>5}",
        ]
        return "\n".join(lines)

    def _refresh_metrics_panel(self):
        """Recompute metrics and write them into the persistent side panel."""
        text = self._metrics_text()
        self.metrics_text.config(state=tk.NORMAL)
        self.metrics_text.delete("1.0", tk.END)
        self.metrics_text.insert("1.0", text)
        self.metrics_text.config(state=tk.DISABLED)

    def show_metrics(self):
        """Pop up the current precision / recall / F1 report."""
        self.save_labels()
        self._refresh_metrics_panel()
        messagebox.showinfo("Evaluation metrics", self._metrics_text())

    def _update_summary(self):
        # Metrics update in real time on every label change / navigation.
        self._refresh_metrics_panel()
        stem = self._current_stem()
        if stem is None:
            self.summary_label.config(text="")
            return
        per_inst = self.labels.get(stem, {})
        n_labelled = sum(1 for i in self.instances if i["id"] in per_inst)
        _counts, labelled, total = self._confusion()
        self.summary_label.config(
            text=(f"This image: {n_labelled}/{len(self.instances)} labelled\n"
                  f"Overall: {labelled}/{total} labelled"))


def manual_evaluation(image_dir=None, res_dir=None, labels_path=None,
                      display_height=DISPLAY_HEIGHT,
                      produce_type=DEFAULT_PRODUCE, res="1400x900"):
    """Launch the manual evaluation tool.

    Reads source images and their TwoStageGrader inference artefacts,
    visualises each detected instance (bounding box coloured by the model's
    predicted grade plus the semantic defect-mask overlay), lets the user
    relabel every instance as grade A or C, and reports grade A / grade C
    precision, recall and F1 (manual labels treated as ground truth).

    Args:
        image_dir: Optional path to the source-image directory. If provided
            (with res_dir), the directories are loaded automatically at
            startup; otherwise use the "Load Directories" button.
        res_dir: Optional path to the artefact (result) directory holding the
            per-image ``*_detected_instances.json``, ``*_inference_output.json``,
            ``*_grader_metrics.json`` and ``*_seg_inst_mask.png`` files.
        labels_path: Optional path to the manual labels JSON. Defaults to
            ``<res_dir>/manual_labels.json``. Labels are saved here on every
            change and reloaded on startup so a session can be resumed.
        display_height: Fixed canvas display height in pixels (default 800).
            Images are scaled to this height.
        produce_type: Produce type selecting the mask-value -> defect-name
            colour map (one of PRODUCE_DEFECTS, default "strawberry"). Can also
            be changed live via the "Produce" dropdown.
        res: Window geometry string (default "1400x900").
    """
    root = tk.Tk()
    root.geometry(res)
    ManualEvaluation(root, image_dir=image_dir, res_dir=res_dir,
                     labels_path=labels_path, display_height=display_height,
                     produce_type=produce_type)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(
        description="Manual evaluation GUI for TwoStageGrader inference artefacts")
    parser.add_argument("--image-dir", default=None,
                        help="Directory containing source images")
    parser.add_argument("--res-dir", default=None,
                        help="Directory containing inference artefacts")
    parser.add_argument("--labels", default=None,
                        help="Path to the manual labels JSON "
                             "(default: <res-dir>/manual_labels.json)")
    parser.add_argument("--display-height", type=int, default=DISPLAY_HEIGHT,
                        help=f"Canvas display height (default: {DISPLAY_HEIGHT})")
    parser.add_argument("--produce", default=DEFAULT_PRODUCE,
                        choices=sorted(PRODUCE_DEFECTS.keys()),
                        help=f"Produce type for the defect colour map "
                             f"(default: {DEFAULT_PRODUCE})")
    args = parser.parse_args()

    manual_evaluation(image_dir=args.image_dir, res_dir=args.res_dir,
                      labels_path=args.labels,
                      display_height=args.display_height,
                      produce_type=args.produce)


if __name__ == "__main__":
    main()
