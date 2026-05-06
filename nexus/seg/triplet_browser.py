"""GUI tool for comparing ground truth and predicted segmentation masks.

Displays triplets: original image | GT overlay | prediction overlay,
with filtering by class presence, a vertical scrollbar, and keyboard
navigation.

Fred Zhang <fredzz@amazon.com>
"""
import os
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
DEFAULT_GT_BACKGROUND = 255
DEFAULT_GT_FOREGROUND = None  # None means foreground class doesn't exist
DEFAULT_PRED_BACKGROUND = 255
DEFAULT_PRED_FOREGROUND = None
ALPHA = 0.5
IMAGE_EXTS = {'.png', '.jpg', '.jpeg'}
DISPLAY_WIDTH = 350  # Width per panel in the triplet
# Legend sizing as percentage of image width/height
LEGEND_FONT_SCALE = 0.04   # Font size relative to image width
LEGEND_SWATCH_SCALE = 0.04  # Swatch size relative to image width
LEGEND_PADDING_SCALE = 0.02  # Padding relative to image width


def _skip_values(background, foreground):
    """Return the set of pixel values to skip (background + foreground)."""
    skip = {background}
    if foreground is not None:
        skip.add(foreground)
    return skip


def overlay_mask(img_rgb, mask, colour_map, label_map, background, foreground=None, alpha=ALPHA):
    """Overlay mask on image and return RGB numpy array with legend.

    Args:
        img_rgb: Source image as RGB numpy array.
        mask: Single-channel mask (pixel values are class IDs).
        colour_map: Dict mapping pixel values to RGB colour tuples.
        label_map: Dict mapping pixel values to label strings.
        background: Pixel value treated as background (skipped).
        foreground: Pixel value treated as generic foreground (skipped
            in overlay/legend but used as denominator for area ratio).
            If None, total image area is used as denominator.
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
    skip = _skip_values(background, foreground)
    present = set(np.unique(mask_resized)) - skip

    # Denominator: foreground area if foreground class exists, else total pixels
    if foreground is not None:
        denom = np.count_nonzero(mask_resized == foreground)
        # Include defect pixels too (they are part of the fruit)
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
            draw.text((padding * 2 + swatch, y), lbl, fill=(255, 255, 255), font=font)
            y += line_height

    return np.array(pil_img), present


def load_triplet_data(image_dir, gt_dir, pred_dir, gt_background, gt_foreground,
                      pred_background, pred_foreground):
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

    gt_skip = _skip_values(gt_background, gt_foreground)
    pred_skip = _skip_values(pred_background, pred_foreground)

    # Include stems that have at least one mask available
    mask_stems = set(gt_lookup) | set(pred_lookup)
    stems = sorted(set(image_lookup) & mask_stems)
    triplets = []
    for stem in stems:
        gt_path = gt_lookup.get(stem)
        pred_path = pred_lookup.get(stem)
        gt_classes = set()
        pred_classes = set()
        if gt_path:
            gt_mask = cv2.imread(gt_path, cv2.IMREAD_GRAYSCALE)
            if gt_mask is not None:
                gt_classes = set(np.unique(gt_mask)) - gt_skip
        if pred_path:
            pred_mask = cv2.imread(pred_path, cv2.IMREAD_GRAYSCALE)
            if pred_mask is not None:
                pred_classes = set(np.unique(pred_mask)) - pred_skip
        triplets.append({
            'stem': stem,
            'image_path': image_lookup[stem],
            'gt_path': gt_path,
            'pred_path': pred_path,
            'gt_classes': gt_classes,
            'pred_classes': pred_classes,
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
    def __init__(self, parent, colour_map, label_map):
        super().__init__(parent)
        self.title("Class Settings")
        self.colour_map = dict(colour_map)
        self.label_map = dict(label_map)
        self.result = None
        self.grab_set()

        ttk.Label(self, text="Configure classes (pixel value → label & colour):").pack(padx=10, pady=5)

        self.entries_frame = ttk.Frame(self)
        self.entries_frame.pack(padx=10, pady=5, fill=tk.X)
        self.rows = []
        for pv in sorted(self.colour_map):
            self._add_row(pv, self.label_map.get(pv, str(pv)), self.colour_map[pv])

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=5)
        ttk.Button(btn_frame, text="Add Class", command=self._add_new).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove Last", command=self._remove_last).pack(side=tk.LEFT, padx=5)

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
        self.result = (self.colour_map, self.label_map)
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
                 gt_background=DEFAULT_GT_BACKGROUND, gt_foreground=DEFAULT_GT_FOREGROUND,
                 pred_background=DEFAULT_PRED_BACKGROUND, pred_foreground=DEFAULT_PRED_FOREGROUND):
        self.root = root
        self.root.title("Mask Comparison Viewer")
        self.root.geometry("1200x800")
        self.colour_map = dict(colour_map or DEFAULT_COLOUR_MAP)
        self.label_map = dict(label_map or DEFAULT_LABEL_MAP)
        self.gt_background = gt_background
        self.gt_foreground = gt_foreground
        self.pred_background = pred_background
        self.pred_foreground = pred_foreground
        self.triplets = []
        self.filtered = []
        self.photo_refs = []  # prevent GC

        self._build_toolbar()
        self._build_canvas()
        self.root.bind("<Key>", self._on_key)

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

        ttk.Label(toolbar, text="Filter:").pack(side=tk.LEFT)
        self.filter_source = ttk.Combobox(toolbar, values=["GT", "Pred", "Either"], width=5, state="readonly")
        self.filter_source.set("GT")
        self.filter_source.pack(side=tk.LEFT, padx=2)

        ttk.Label(toolbar, text="has").pack(side=tk.LEFT)
        self.filter_class = ttk.Combobox(toolbar, values=["Any"] + list(self.label_map.values()), width=8, state="readonly")
        self.filter_class.set("Any")
        self.filter_class.pack(side=tk.LEFT, padx=2)

        ttk.Button(toolbar, text="Apply", command=self._apply_filter).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Reset", command=self._reset_filter).pack(side=tk.LEFT, padx=2)

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

    def _open_settings(self):
        dlg = SettingsDialog(self.root, self.colour_map, self.label_map)
        if dlg.result:
            self.colour_map, self.label_map = dlg.result
            self.filter_class['values'] = ["Any"] + list(self.label_map.values())
            self.filter_class.set("Any")
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
                                            self.gt_background, self.gt_foreground,
                                            self.pred_background, self.pred_foreground)
        self.filtered = self.triplets
        self.status_var.set(f"{len(self.triplets)} triplet(s) found")
        self._render()

    def _apply_filter(self):
        cls_name = self.filter_class.get()
        source = self.filter_source.get()
        if cls_name == "Any":
            self.filtered = self.triplets
        else:
            cls_val = next((k for k, v in self.label_map.items() if v == cls_name), None)
            if cls_val is None:
                self.filtered = self.triplets
            else:
                self.filtered = []
                for t in self.triplets:
                    if source == "GT" and cls_val in t['gt_classes']:
                        self.filtered.append(t)
                    elif source == "Pred" and cls_val in t['pred_classes']:
                        self.filtered.append(t)
                    elif source == "Either" and (cls_val in t['gt_classes'] or cls_val in t['pred_classes']):
                        self.filtered.append(t)
        self.status_var.set(f"Showing {len(self.filtered)}/{len(self.triplets)} triplet(s)")
        self._render()

    def _reset_filter(self):
        self.filter_class.set("Any")
        self.filtered = self.triplets
        self.status_var.set(f"Showing {len(self.filtered)}/{len(self.triplets)} triplet(s)")
        self._render()

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
                                             self.gt_background, self.gt_foreground)
                panels.append((gt_overlay, "Ground Truth"))

        if triplet['pred_path']:
            pred_mask = cv2.imread(triplet['pred_path'], cv2.IMREAD_GRAYSCALE)
            if pred_mask is not None:
                pred_overlay, _ = overlay_mask(img_rgb, pred_mask, self.colour_map, self.label_map,
                                              self.pred_background, self.pred_foreground)
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
                           gt_background=DEFAULT_GT_BACKGROUND,
                           gt_foreground=DEFAULT_GT_FOREGROUND,
                           pred_background=DEFAULT_PRED_BACKGROUND,
                           pred_foreground=DEFAULT_PRED_FOREGROUND):
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
        gt_background: Pixel value for background in GT masks.
            Defaults to 255.
        gt_foreground: Pixel value for generic foreground in GT masks.
            If not None, this class is skipped in overlay/legend and
            the foreground area (foreground + defect pixels) is used
            as the denominator for area ratio. Defaults to None.
        pred_background: Pixel value for background in prediction masks.
            Defaults to 255.
        pred_foreground: Pixel value for generic foreground in prediction
            masks. Same behaviour as gt_foreground. Defaults to None.
    """
    root = tk.Tk()
    CompareApp(root, colour_map=colour_map, label_map=label_map,
               gt_background=gt_background, gt_foreground=gt_foreground,
               pred_background=pred_background, pred_foreground=pred_foreground)
    root.mainloop()


if __name__ == "__main__":
    segmentation_diagnosis()
