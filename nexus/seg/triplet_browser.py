"""GUI tool for comparing ground truth and predicted segmentation masks.

Displays triplets: original image | GT overlay | prediction overlay,
with filtering by class presence, a vertical scrollbar, and keyboard
navigation.

Fred Zhang <fredzz@amazon.com>
"""
import os
import base64
from io import BytesIO
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, colorchooser
from PIL import Image, ImageTk, ImageDraw, ImageFont
import cv2
import numpy as np

DEFAULT_COLOUR_MAP = {
    2: (255, 0, 0),     # Decay -> red (RGB)
    3: (255, 165, 0),   # Overripe -> orange
    4: (0, 255, 0),     # Mould -> green
}
DEFAULT_LABEL_MAP = {
    2: "Decay",
    3: "Overripe",
    4: "Mould",
}
DEFAULT_GT_FOREGROUND = None  # None means foreground class doesn't exist
DEFAULT_PRED_FOREGROUND = None
ALPHA = 0.5
IMAGE_EXTS = {'.png', '.jpg', '.jpeg'}
DISPLAY_WIDTH = 350  # Width per panel in the triplet
# Legend sizing as percentage of image width/height
LEGEND_FONT_SCALE = 0.04   # Font size relative to image width
LEGEND_SWATCH_SCALE = 0.04  # Swatch size relative to image width
LEGEND_PADDING_SCALE = 0.02  # Padding relative to image width
# Tolerance for floating-point comparison in secondary class evaluation
FLOAT_EPS = 1e-9


def _defect_classes(mask, colour_map, foreground):
    """Return the set of defect classes present in a mask.

    Defect classes are pixel values that exist in both the mask and the
    colour_map. Foreground and background (anything not in colour_map)
    are excluded.
    """
    present = set(np.unique(mask))
    return present & set(colour_map.keys())


def _compute_area_ratios(mask, colour_map, foreground):
    """Compute area ratio for each class in colour_map.

    Returns a dict mapping pixel value -> ratio (float).
    Order follows sorted colour_map keys.
    """
    if foreground is not None:
        denom = np.count_nonzero(mask == foreground)
        for pv in colour_map:
            denom += np.count_nonzero(mask == pv)
    else:
        denom = mask.shape[0] * mask.shape[1]
    denom = max(denom, 1)
    return {pv: np.count_nonzero(mask == pv) / denom for pv in sorted(colour_map)}


def _compare(value, threshold, op):
    """Compare value against threshold using the given operator string.

    Uses FLOAT_EPS tolerance for equality checks to avoid floating-point
    precision issues.

    Supported operators: '>', '<', '==', '>=', '<='.
    """
    if op == '>':
        return value > threshold + FLOAT_EPS
    elif op == '<':
        return value < threshold - FLOAT_EPS
    elif op == '==':
        return abs(value - threshold) <= FLOAT_EPS
    elif op == '>=':
        return value > threshold - FLOAT_EPS
    elif op == '<=':
        return value < threshold + FLOAT_EPS
    return False


def _evaluate_secondary_classes(mask, colour_map, foreground, secondary_classes):
    """Evaluate which secondary classes a mask belongs to.

    Args:
        mask: Grayscale mask array.
        colour_map: Dict mapping pixel values to colours.
        foreground: Foreground pixel value (or None).
        secondary_classes: Dict defining secondary classes. Format:
            {
                'Grade C': {
                    'thresholds': [0, 0.05, 0],
                    'comparisons': ['>', '>=', '>'],
                    'aggregation': 'or',
                    'complement': 'Grade A',
                },
            }

    Returns:
        Set of secondary class names that this mask satisfies.
    """
    if not secondary_classes:
        return set()

    ratios = _compute_area_ratios(mask, colour_map, foreground)
    ratio_list = [ratios[pv] for pv in sorted(colour_map)]
    result = set()

    for name, defn in secondary_classes.items():
        thresholds = defn['thresholds']
        comparisons = defn['comparisons']
        aggregation = defn['aggregation']
        complement = defn.get('complement')

        checks = [_compare(r, t, op)
                  for r, t, op in zip(ratio_list, thresholds, comparisons)]

        if aggregation == 'or':
            matches = any(checks)
        else:  # 'and'
            matches = all(checks)

        if matches:
            result.add(name)
        elif complement:
            result.add(complement)

    return result


def overlay_mask(img_rgb, mask, colour_map, label_map, foreground=None, alpha=ALPHA):
    """Overlay mask on image and return RGB numpy array with legend.

    Args:
        img_rgb: Source image as RGB numpy array.
        mask: Single-channel mask (pixel values are class IDs).
        colour_map: Dict mapping pixel values to RGB colour tuples.
        label_map: Dict mapping pixel values to label strings.
        foreground: Pixel value treated as generic foreground (skipped
            in overlay/legend). When specified, the denominator for
            area ratio is foreground + defect pixels. When None, the
            denominator is total image area.
        alpha: Overlay opacity.

    Returns:
        Tuple of (blended RGB array, set of defect classes present).
    """
    mask_resized = cv2.resize(mask, (img_rgb.shape[1], img_rgb.shape[0]),
                              interpolation=cv2.INTER_NEAREST)
    overlay = img_rgb.copy()
    for pv, colour in colour_map.items():
        overlay[mask_resized == pv] = colour
    blended = (img_rgb * (1 - alpha) + overlay * alpha).astype(np.uint8)

    # Draw legend using PIL with sizes proportional to image dimensions
    pil_img = Image.fromarray(blended)
    draw = ImageDraw.Draw(pil_img)
    img_w = img_rgb.shape[1]
    present = set(np.unique(mask_resized)) & set(colour_map.keys())

    # Denominator: when foreground is specified, use foreground + defect pixels.
    # When foreground is None, use total image area.
    if foreground is not None:
        denom = np.count_nonzero(mask_resized == foreground)
        for pv in colour_map:
            denom += np.count_nonzero(mask_resized == pv)
    else:
        denom = mask_resized.shape[0] * mask_resized.shape[1]
    denom = max(denom, 1)  # avoid division by zero

    entries = []
    for pv in sorted(colour_map):
        if pv not in present:
            continue
        ratio = np.count_nonzero(mask_resized == pv) / denom
        lbl = f"{label_map.get(pv, str(pv))} ({ratio:.1%})"
        entries.append((colour_map[pv], lbl))

    if entries:
        swatch = max(int(img_w * LEGEND_SWATCH_SCALE), 10)
        padding = max(int(img_w * LEGEND_PADDING_SCALE), 5)
        font_size = max(int(img_w * LEGEND_FONT_SCALE), 10)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
        except (OSError, IOError):
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", font_size)
            except (OSError, IOError):
                font = ImageFont.load_default()
        line_height = swatch + padding
        y = padding
        for colour, lbl in entries:
            draw.rectangle([padding, y, padding + swatch, y + swatch], fill=colour)
            tx, ty = padding * 2 + swatch, y
            # Black outline
            for dx, dy in [(-1,-1),(-1,1),(1,-1),(1,1)]:
                draw.text((tx+dx, ty+dy), lbl, fill=(0, 0, 0), font=font)
            draw.text((tx, ty), lbl, fill=(255, 255, 255), font=font)
            y += line_height

    return np.array(pil_img), present


def load_triplet_data(image_dir, gt_dir, pred_dir, colour_map,
                      gt_foreground, pred_foreground, secondary_classes=None):
    """Load metadata for all entries (matched by filename stem).

    GT and pred directories are optional (may be None or empty string).
    Entries are included if the image has at least one matching mask.
    """
    image_lookup = {}
    for f in os.listdir(image_dir):
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
            image_lookup[os.path.splitext(f)[0]] = os.path.join(image_dir, f)

    gt_lookup = {}
    if gt_dir:
        for f in os.listdir(gt_dir):
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                gt_lookup[os.path.splitext(f)[0]] = os.path.join(gt_dir, f)

    pred_lookup = {}
    if pred_dir:
        for f in os.listdir(pred_dir):
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                pred_lookup[os.path.splitext(f)[0]] = os.path.join(pred_dir, f)

    # Include stems that have at least one mask available
    mask_stems = set(gt_lookup) | set(pred_lookup)
    stems = sorted(set(image_lookup) & mask_stems)
    triplets = []
    for stem in stems:
        gt_path = gt_lookup.get(stem)
        pred_path = pred_lookup.get(stem)
        gt_classes = set()
        pred_classes = set()
        gt_secondary = set()
        pred_secondary = set()
        if gt_path:
            gt_mask = cv2.imread(gt_path, cv2.IMREAD_GRAYSCALE)
            if gt_mask is not None:
                gt_classes = _defect_classes(gt_mask, colour_map, gt_foreground)
                gt_secondary = _evaluate_secondary_classes(
                    gt_mask, colour_map, gt_foreground, secondary_classes)
        if pred_path:
            pred_mask = cv2.imread(pred_path, cv2.IMREAD_GRAYSCALE)
            if pred_mask is not None:
                pred_classes = _defect_classes(pred_mask, colour_map, pred_foreground)
                pred_secondary = _evaluate_secondary_classes(
                    pred_mask, colour_map, pred_foreground, secondary_classes)
        triplets.append({
            'stem': stem,
            'image_path': image_lookup[stem],
            'gt_path': gt_path,
            'pred_path': pred_path,
            'gt_classes': gt_classes,
            'pred_classes': pred_classes,
            'gt_secondary': gt_secondary,
            'pred_secondary': pred_secondary,
        })
    return triplets


class SettingsDialog(tk.Toplevel):
    """Modal dialog for configuring the class-to-colour and class-to-label mappings.

    Presents an editable list of classes where each row contains:
      - Pixel value (integer that appears in the mask image)
      - Human-readable label string
      - Colour swatch (click to open a colour picker)

    On confirmation, the updated mappings are stored in `self.result`
    as a tuple (colour_map, label_map). If the dialog is cancelled,
    `self.result` remains None.
    """
    def __init__(self, parent, colour_map, label_map, secondary_classes=None):
        super().__init__(parent)
        self.title("Class Settings")
        self.colour_map = dict(colour_map)
        self.label_map = dict(label_map)
        self.secondary_classes = dict(secondary_classes or {})
        self.result = None
        self.grab_set()

        # --- Primary classes ---
        ttk.Label(self, text="Primary classes (pixel value → label & colour):").pack(padx=10, pady=5)

        self.entries_frame = ttk.Frame(self)
        self.entries_frame.pack(padx=10, pady=5, fill=tk.X)
        self.rows = []
        for pv in sorted(self.colour_map):
            self._add_row(pv, self.label_map.get(pv, str(pv)), self.colour_map[pv])

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=5)
        ttk.Button(btn_frame, text="Add Class", command=self._add_new).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove Last", command=self._remove_last).pack(side=tk.LEFT, padx=5)

        # --- Secondary classes ---
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(self, text="Secondary classes (derived from area ratios):").pack(padx=10, pady=5)

        self.sec_frame = ttk.Frame(self)
        self.sec_frame.pack(padx=10, pady=5, fill=tk.X)
        self.sec_rows = []
        for name, defn in self.secondary_classes.items():
            self._add_sec_row(name, defn)

        sec_btn_frame = ttk.Frame(self)
        sec_btn_frame.pack(pady=5)
        ttk.Button(sec_btn_frame, text="Add Secondary", command=self._add_sec_new).pack(side=tk.LEFT, padx=5)
        ttk.Button(sec_btn_frame, text="Remove Last Secondary", command=self._remove_sec_last).pack(side=tk.LEFT, padx=5)

        # --- OK / Cancel ---
        action_frame = ttk.Frame(self)
        action_frame.pack(pady=10)
        ttk.Button(action_frame, text="OK", command=self._ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=5)

        self.transient(parent)
        self.wait_window()

    def _add_row(self, pv, label, colour):
        row = ttk.Frame(self.entries_frame)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Pixel val:").pack(side=tk.LEFT)
        pv_var = tk.StringVar(value=str(pv))
        ttk.Entry(row, textvariable=pv_var, width=4).pack(side=tk.LEFT, padx=2)
        ttk.Label(row, text="Label:").pack(side=tk.LEFT)
        lbl_var = tk.StringVar(value=label)
        ttk.Entry(row, textvariable=lbl_var, width=12).pack(side=tk.LEFT, padx=2)
        colour_var = tk.StringVar(value=f"#{colour[0]:02x}{colour[1]:02x}{colour[2]:02x}")
        swatch = tk.Label(row, bg=colour_var.get(), width=3, relief="raised")
        swatch.pack(side=tk.LEFT, padx=2)
        swatch.bind("<Button-1>", lambda e, cv=colour_var, sw=swatch: self._pick_colour(cv, sw))
        self.rows.append((pv_var, lbl_var, colour_var))

    def _pick_colour(self, colour_var, swatch):
        c = colorchooser.askcolor(color=colour_var.get(), parent=self)
        if c[1]:
            colour_var.set(c[1])
            swatch.configure(bg=c[1])

    def _add_new(self):
        self._add_row(0, "NewClass", (128, 128, 128))

    def _remove_last(self):
        if self.rows:
            self.rows.pop()
            children = self.entries_frame.winfo_children()
            if children:
                children[-1].destroy()

    def _add_sec_row(self, name="", defn=None):
        """Add an editable row for a secondary class definition."""
        if defn is None:
            n_classes = len(self.colour_map)
            defn = {
                'thresholds': [0.0] * n_classes,
                'comparisons': ['>'] * n_classes,
                'aggregation': 'or',
                'complement': '',
            }
        frame = ttk.LabelFrame(self.sec_frame, text="Secondary class")
        frame.pack(fill=tk.X, pady=3)

        # Name and complement
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(top, text="Name:").pack(side=tk.LEFT)
        name_var = tk.StringVar(value=name)
        ttk.Entry(top, textvariable=name_var, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Label(top, text="Complement:").pack(side=tk.LEFT, padx=(10, 0))
        comp_var = tk.StringVar(value=defn.get('complement', ''))
        ttk.Entry(top, textvariable=comp_var, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Label(top, text="Aggregation:").pack(side=tk.LEFT, padx=(10, 0))
        agg_var = tk.StringVar(value=defn['aggregation'])
        ttk.Combobox(top, textvariable=agg_var, values=['or', 'and'], width=4,
                     state='readonly').pack(side=tk.LEFT, padx=2)

        # Per-class thresholds and comparisons
        thresh_vars = []
        comp_op_vars = []
        sorted_keys = sorted(self.colour_map.keys())
        for i, pv in enumerate(sorted_keys):
            row = ttk.Frame(frame)
            row.pack(fill=tk.X, padx=15, pady=1)
            lbl = self.label_map.get(pv, str(pv))
            ttk.Label(row, text=f"{lbl}:", width=12).pack(side=tk.LEFT)
            op_var = tk.StringVar(value=defn['comparisons'][i] if i < len(defn['comparisons']) else '>')
            ttk.Combobox(row, textvariable=op_var, values=['>', '<', '==', '>=', '<='],
                         width=4, state='readonly').pack(side=tk.LEFT, padx=2)
            t_var = tk.StringVar(value=str(defn['thresholds'][i] if i < len(defn['thresholds']) else 0.0))
            ttk.Entry(row, textvariable=t_var, width=8).pack(side=tk.LEFT, padx=2)
            comp_op_vars.append(op_var)
            thresh_vars.append(t_var)

        self.sec_rows.append((frame, name_var, comp_var, agg_var, thresh_vars, comp_op_vars))

    def _add_sec_new(self):
        self._add_sec_row()

    def _remove_sec_last(self):
        if self.sec_rows:
            frame, *_ = self.sec_rows.pop()
            frame.destroy()

    def _ok(self):
        self.colour_map = {}
        self.label_map = {}
        for pv_var, lbl_var, colour_var in self.rows:
            try:
                pv = int(pv_var.get())
            except ValueError:
                continue
            hex_c = colour_var.get().lstrip('#')
            rgb = tuple(int(hex_c[i:i+2], 16) for i in (0, 2, 4))
            self.colour_map[pv] = rgb
            self.label_map[pv] = lbl_var.get()

        # Parse secondary classes
        self.secondary_classes = {}
        for _, name_var, comp_var, agg_var, thresh_vars, comp_op_vars in self.sec_rows:
            name = name_var.get().strip()
            if not name:
                continue
            thresholds = []
            for tv in thresh_vars:
                try:
                    thresholds.append(float(tv.get()))
                except ValueError:
                    thresholds.append(0.0)
            comparisons = [ov.get() for ov in comp_op_vars]
            complement = comp_var.get().strip() or None
            self.secondary_classes[name] = {
                'thresholds': thresholds,
                'comparisons': comparisons,
                'aggregation': agg_var.get(),
                'complement': complement,
            }

        self.result = (self.colour_map, self.label_map, self.secondary_classes)
        self.destroy()


class CompareApp:
    """Main application for browsing segmentation mask comparison triplets.

    Provides a scrollable view of (original, GT overlay, prediction overlay)
    triplets with:
      - Directory pickers for images, ground truth masks, and predicted masks
      - Class-based filtering (by GT, prediction, or either)
      - Configurable colour/label mappings via a settings dialog
      - Keyboard shortcuts (Home/End) and mousewheel scrolling

    Args:
        root: The tkinter root window.
        colour_map: Optional dict mapping pixel values to RGB tuples.
            Defaults to DEFAULT_COLOUR_MAP.
        label_map: Optional dict mapping pixel values to label strings.
            Defaults to DEFAULT_LABEL_MAP.
    """
    def __init__(self, root, colour_map=None, label_map=None,
                 gt_foreground=DEFAULT_GT_FOREGROUND,
                 pred_foreground=DEFAULT_PRED_FOREGROUND,
                 secondary_classes=None,
                 image_dir=None, gt_dir=None, pred_dir=None):
        self.root = root
        self.root.title("Mask Comparison Viewer")
        self.root.geometry("1200x800")
        self.colour_map = dict(colour_map or DEFAULT_COLOUR_MAP)
        self.label_map = dict(label_map or DEFAULT_LABEL_MAP)
        self.gt_foreground = gt_foreground
        self.pred_foreground = pred_foreground
        self.secondary_classes = secondary_classes or {}
        self.triplets = []
        self.filtered = []
        self.bookmarks = set()  # set of bookmarked stems
        self.photo_refs = []  # prevent GC

        self._build_toolbar()
        self._build_canvas()
        self.root.bind("<Key>", self._on_key)

        # Pre-fill directories and auto-load if provided
        if image_dir:
            self.image_dir_var.set(image_dir)
        if gt_dir:
            self.gt_dir_var.set(gt_dir)
        if pred_dir:
            self.pred_dir_var.set(pred_dir)
        if image_dir and (gt_dir or pred_dir):
            self._load()

    def _build_toolbar(self):
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(toolbar, text="Image Dir", command=self._pick_image_dir).pack(side=tk.LEFT)
        self.image_dir_var = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.image_dir_var, width=20).pack(side=tk.LEFT, padx=2)

        ttk.Button(toolbar, text="GT Dir", command=self._pick_gt_dir).pack(side=tk.LEFT)
        self.gt_dir_var = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.gt_dir_var, width=20).pack(side=tk.LEFT, padx=2)

        ttk.Button(toolbar, text="Pred Dir", command=self._pick_pred_dir).pack(side=tk.LEFT)
        self.pred_dir_var = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.pred_dir_var, width=20).pack(side=tk.LEFT, padx=2)

        ttk.Button(toolbar, text="Load", command=self._load).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="⚙ Settings", command=self._open_settings).pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        class_options = self._get_filter_options()
        ttk.Label(toolbar, text="GT has:").pack(side=tk.LEFT)
        self.filter_gt_class = ttk.Combobox(toolbar, values=class_options, width=10, state="readonly")
        self.filter_gt_class.set("Any")
        self.filter_gt_class.pack(side=tk.LEFT, padx=2)

        ttk.Label(toolbar, text="Pred has:").pack(side=tk.LEFT)
        self.filter_pred_class = ttk.Combobox(toolbar, values=class_options, width=10, state="readonly")
        self.filter_pred_class.set("Any")
        self.filter_pred_class.pack(side=tk.LEFT, padx=2)

        ttk.Button(toolbar, text="Apply", command=self._apply_filter).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Reset", command=self._reset_filter).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="★ Bookmarked", command=self._show_bookmarked).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="Export HTML", command=self._export_html).pack(side=tk.LEFT, padx=2)

        self.status_var = tk.StringVar(value="No data loaded")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side=tk.RIGHT)

    def _build_canvas(self):
        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(container)
        self.scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.inner_frame = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")

        self.inner_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_canvas_resize(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(-1 * (event.delta // 120 or (-1 if event.delta < 0 else 1)), "units")

    def _on_key(self, event):
        if event.keysym == "Home":
            self.canvas.yview_moveto(0)
        elif event.keysym == "End":
            self.canvas.yview_moveto(1)

    def _pick_image_dir(self):
        d = filedialog.askdirectory(title="Select Image Directory")
        if d:
            self.image_dir_var.set(d)

    def _pick_gt_dir(self):
        d = filedialog.askdirectory(title="Select Ground Truth Mask Directory")
        if d:
            self.gt_dir_var.set(d)

    def _pick_pred_dir(self):
        d = filedialog.askdirectory(title="Select Predicted Mask Directory")
        if d:
            self.pred_dir_var.set(d)

    def _get_filter_options(self):
        """Build the list of filter options: primary classes + secondary classes."""
        options = ["Any"] + list(self.label_map.values())
        for name, defn in self.secondary_classes.items():
            options.append(name)
            complement = defn.get('complement')
            if complement:
                options.append(complement)
        return options

    def _open_settings(self):
        dlg = SettingsDialog(self.root, self.colour_map, self.label_map, self.secondary_classes)
        if dlg.result:
            self.colour_map, self.label_map, self.secondary_classes = dlg.result
            class_options = self._get_filter_options()
            self.filter_gt_class['values'] = class_options
            self.filter_gt_class.set("Any")
            self.filter_pred_class['values'] = class_options
            self.filter_pred_class.set("Any")
            if self.triplets:
                self._load()  # reload with new maps

    def _load(self):
        image_dir = self.image_dir_var.get()
        gt_dir = self.gt_dir_var.get()
        pred_dir = self.pred_dir_var.get()
        if not image_dir:
            self.status_var.set("Please specify the image directory")
            return
        if not gt_dir and not pred_dir:
            self.status_var.set("Please specify at least one mask directory (GT or Pred)")
            return
        self.status_var.set("Loading...")
        self.root.update()
        self.triplets = load_triplet_data(image_dir, gt_dir, pred_dir,
                                            self.colour_map,
                                            self.gt_foreground, self.pred_foreground,
                                            self.secondary_classes)
        self.filtered = self.triplets
        self.status_var.set(f"{len(self.triplets)} triplet(s) found")
        self._render()

    def _apply_filter(self):
        gt_name = self.filter_gt_class.get()
        pred_name = self.filter_pred_class.get()

        # Determine if filter is a primary class, secondary class, or "Any"
        primary_labels_inv = {v: k for k, v in self.label_map.items()}
        all_secondary_names = set()
        for name, defn in self.secondary_classes.items():
            all_secondary_names.add(name)
            if defn.get('complement'):
                all_secondary_names.add(defn['complement'])

        self.filtered = []
        for t in self.triplets:
            gt_match = self._matches_filter(gt_name, t['gt_classes'], t['gt_secondary'],
                                           primary_labels_inv, all_secondary_names)
            pred_match = self._matches_filter(pred_name, t['pred_classes'], t['pred_secondary'],
                                             primary_labels_inv, all_secondary_names)
            if gt_match and pred_match:
                self.filtered.append(t)

        self.status_var.set(f"Showing {len(self.filtered)}/{len(self.triplets)} triplet(s)")
        self._render()

    def _matches_filter(self, filter_name, primary_classes, secondary_classes,
                        primary_labels_inv, all_secondary_names):
        """Check if a triplet matches a filter selection."""
        if filter_name == "Any":
            return True
        if filter_name in all_secondary_names:
            return filter_name in secondary_classes
        # Primary class lookup
        pv = primary_labels_inv.get(filter_name)
        if pv is not None:
            return pv in primary_classes
        return False

    def _reset_filter(self):
        self.filter_gt_class.set("Any")
        self.filter_pred_class.set("Any")
        self.filtered = self.triplets
        self.status_var.set(f"Showing {len(self.filtered)}/{len(self.triplets)} triplet(s)")
        self._render()

    def _show_bookmarked(self):
        self.filtered = [t for t in self.triplets if t['stem'] in self.bookmarks]
        self.status_var.set(f"Showing {len(self.filtered)} bookmarked triplet(s)")
        self._render()

    def _toggle_bookmark(self, stem):
        if stem in self.bookmarks:
            self.bookmarks.discard(stem)
        else:
            self.bookmarks.add(stem)

    def _export_html(self):
        """Export the currently displayed triplets to a self-contained HTML file."""
        if not self.filtered:
            self.status_var.set("Nothing to export")
            return
        path = filedialog.asksaveasfilename(
            title="Export HTML", defaultextension=".html",
            filetypes=[("HTML files", "*.html")])
        if not path:
            return

        self.status_var.set("Exporting...")
        self.root.update()

        rows_html = []
        for i, triplet in enumerate(self.filtered):
            img_bgr = cv2.imread(triplet['image_path'])
            if img_bgr is None:
                continue
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

            panels = [(img_rgb, "Original")]
            if triplet['gt_path']:
                gt_mask = cv2.imread(triplet['gt_path'], cv2.IMREAD_GRAYSCALE)
                if gt_mask is not None:
                    gt_overlay, _ = overlay_mask(img_rgb, gt_mask, self.colour_map,
                                                 self.label_map, self.gt_foreground)
                    panels.append((gt_overlay, "Ground Truth"))
            if triplet['pred_path']:
                pred_mask = cv2.imread(triplet['pred_path'], cv2.IMREAD_GRAYSCALE)
                if pred_mask is not None:
                    pred_overlay, _ = overlay_mask(img_rgb, pred_mask, self.colour_map,
                                                   self.label_map, self.pred_foreground)
                    panels.append((pred_overlay, "Prediction"))

            cells = []
            for arr, title in panels:
                pil = Image.fromarray(arr)
                buf = BytesIO()
                pil.save(buf, format='JPEG', quality=85)
                b64 = base64.b64encode(buf.getvalue()).decode()
                cells.append(
                    f'<td style="text-align:center;padding:4px">'
                    f'<b>{title}</b><br>'
                    f'<img src="data:image/jpeg;base64,{b64}" style="max-width:350px">'
                    f'</td>')

            rows_html.append(
                f'<tr><td colspan="{len(panels)}" style="padding:8px 4px;font-weight:bold">'
                f'[{i+1}] {triplet["stem"]}</td></tr>'
                f'<tr>{"" .join(cells)}</tr>')

        html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<title>Mask Comparison Export</title>'
            '<style>body{font-family:sans-serif;margin:20px}'
            'table{border-collapse:collapse;margin:auto}'
            'tr:nth-child(4n+1){border-top:1px solid #ccc}</style>'
            '</head><body>'
            f'<h2>Mask Comparison ({len(self.filtered)} triplets)</h2>'
            f'<table>{"" .join(rows_html)}</table>'
            '</body></html>'
        )

        with open(path, 'w') as f:
            f.write(html)

        self.status_var.set(f"Exported {len(self.filtered)} triplet(s) to {os.path.basename(path)}")

    def _render(self):
        for widget in self.inner_frame.winfo_children():
            widget.destroy()
        self.photo_refs.clear()

        for i, t in enumerate(self.filtered):
            self._render_triplet(t, i)

        self.canvas.yview_moveto(0)

    def _render_triplet(self, triplet, index):
        frame = ttk.LabelFrame(self.inner_frame, text=f"[{index+1}] {triplet['stem']}")
        frame.pack(fill=tk.X, padx=5, pady=3)

        # Bookmark checkbox
        bk_var = tk.BooleanVar(value=triplet['stem'] in self.bookmarks)
        bk_cb = ttk.Checkbutton(frame, text="Bookmark", variable=bk_var,
                                command=lambda s=triplet['stem']: self._toggle_bookmark(s))
        bk_cb.pack(anchor=tk.W, padx=5)

        img_bgr = cv2.imread(triplet['image_path'])
        if img_bgr is None:
            ttk.Label(frame, text="Could not load image").pack()
            return
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

        # Build panels: always show original, then GT/Pred if available
        panels = [(img_rgb, "Original")]

        if triplet['gt_path']:
            gt_mask = cv2.imread(triplet['gt_path'], cv2.IMREAD_GRAYSCALE)
            if gt_mask is not None:
                gt_overlay, _ = overlay_mask(img_rgb, gt_mask, self.colour_map, self.label_map,
                                             self.gt_foreground)
                panels.append((gt_overlay, "Ground Truth"))

        if triplet['pred_path']:
            pred_mask = cv2.imread(triplet['pred_path'], cv2.IMREAD_GRAYSCALE)
            if pred_mask is not None:
                pred_overlay, _ = overlay_mask(img_rgb, pred_mask, self.colour_map, self.label_map,
                                              self.pred_foreground)
                panels.append((pred_overlay, "Prediction"))

        # Resize for display
        h, w = img_rgb.shape[:2]
        scale = DISPLAY_WIDTH / w
        new_h = int(h * scale)

        row = ttk.Frame(frame)
        row.pack()
        for arr, title in panels:
            pil = Image.fromarray(arr).resize((DISPLAY_WIDTH, new_h), Image.LANCZOS)
            col = ttk.Frame(row)
            col.pack(side=tk.LEFT, padx=2)
            ttk.Label(col, text=title, font=("TkDefaultFont", 9, "bold")).pack()
            photo = ImageTk.PhotoImage(pil)
            self.photo_refs.append(photo)
            lbl = ttk.Label(col, image=photo)
            lbl.pack()


def segmentation_diagnosis(colour_map=None, label_map=None,
                           gt_foreground=DEFAULT_GT_FOREGROUND,
                           pred_foreground=DEFAULT_PRED_FOREGROUND,
                           secondary_classes=None,
                           image_dir=None, gt_dir=None, pred_dir=None):
    """Launch the mask comparison GUI.

    This is the main entry point for the triplet browser tool.
    It creates a tkinter window and starts the event loop.

    Args:
        colour_map: Optional dict mapping mask pixel values to RGB
            colour tuples (e.g. {2: (255, 0, 0)}). Defaults to
            DEFAULT_COLOUR_MAP if not provided.
        label_map: Optional dict mapping mask pixel values to
            human-readable label strings (e.g. {2: "Decay"}).
            Defaults to DEFAULT_LABEL_MAP if not provided.
        gt_foreground: Pixel value for generic foreground in GT masks.
            If not None, this class is skipped in overlay/legend and
            the foreground area (foreground + defect pixels) is used
            as the denominator for area ratio. Defaults to None
            (denominator is total image area).
        pred_foreground: Pixel value for generic foreground in prediction
            masks. Same behaviour as gt_foreground. Defaults to None.
        secondary_classes: Optional dict defining secondary classes for
            filtering. These are derived from area ratios and do not
            appear in visualisations. Format:
            {
                'Grade C': {
                    'thresholds': [0, 0.05, 0],
                    'comparisons': ['>', '>=', '>'],
                    'aggregation': 'or',
                    'complement': 'Grade A',
                },
            }
            Each entry defines a class and optionally its complement.
            Thresholds and comparisons are ordered by sorted colour_map
            keys. Comparisons: '>', '<', '==', '>=', '<='. Aggregation:
            'or' (any passes) or 'and' (all pass).
        image_dir: Optional path to the image directory. If provided
            along with at least one mask directory, images are loaded
            automatically on startup.
        gt_dir: Optional path to the ground truth mask directory.
        pred_dir: Optional path to the predicted mask directory.
    """
    root = tk.Tk()
    CompareApp(root, colour_map=colour_map, label_map=label_map,
               gt_foreground=gt_foreground, pred_foreground=pred_foreground,
               secondary_classes=secondary_classes,
               image_dir=image_dir, gt_dir=gt_dir, pred_dir=pred_dir)
    root.mainloop()


if __name__ == "__main__":
    segmentation_diagnosis()
