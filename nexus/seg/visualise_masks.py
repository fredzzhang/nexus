"""Visualise segmentation masks overlaid on source images.

Generates side-by-side images with the original on the left and the
mask overlay on the right.

Fred Zhang <fredzz@amazon.com>
"""
import os
import cv2
import argparse
import numpy as np

DEFAULT_COLOUR_MAP = {
    2: (0, 0, 255),     # Decay -> red
    3: (0, 165, 255),   # Overripe/Wet bruising -> orange
    4: (0, 255, 0),     # Mould -> green
}
DEFAULT_LABEL_MAP = {
    2: "Decay",
    3: "Overripe",
    4: "Mould",
}
DEFAULT_BACKGROUND = 255
DEFAULT_ALPHA = 0.5


def visualise_one(image_path, mask_path, colour_map=None, label_map=None,
                  background=DEFAULT_BACKGROUND, alpha=DEFAULT_ALPHA):
    """Create a side-by-side visualisation of an image and its mask overlay.

    Args:
        image_path: Path to the source image.
        mask_path: Path to the mask image (single-channel, pixel values
            correspond to class IDs).
        colour_map: Dict mapping pixel values to BGR colour tuples
            (e.g. {2: (0, 0, 255)}). Defaults to DEFAULT_COLOUR_MAP.
        label_map: Dict mapping pixel values to label strings for the
            legend (e.g. {2: "Decay"}). Defaults to DEFAULT_LABEL_MAP.
        background: Pixel value treated as background (not overlaid).
            Defaults to 255.
        alpha: Opacity of the mask overlay. Defaults to 0.5.

    Returns:
        A numpy array (H, W*2, 3) with the original image on the left
        and the overlaid image on the right, or None if either file
        cannot be read.
    """
    if colour_map is None:
        colour_map = DEFAULT_COLOUR_MAP
    if label_map is None:
        label_map = DEFAULT_LABEL_MAP

    img = cv2.imread(image_path)
    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if img is None or mask is None:
        return None

    mask = cv2.resize(mask, (img.shape[1], img.shape[0]),
                      interpolation=cv2.INTER_NEAREST)

    overlay = img.copy()
    for pixel_val, colour in colour_map.items():
        overlay[mask == pixel_val] = colour
    blended = cv2.addWeighted(img, 1 - alpha, overlay, alpha, 0)

    # Draw legend
    present = set(np.unique(mask)) - {background}
    entries = [(pv, colour_map[pv], label_map.get(pv, str(pv)))
               for pv in sorted(colour_map) if pv in present]
    if entries:
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        thickness = 2
        swatch_size = 16
        padding = 8
        line_height = swatch_size + padding
        legend_h = len(entries) * line_height + padding
        max_text_w = max(cv2.getTextSize(lbl, font, font_scale, thickness)[0][0]
                         for _, _, lbl in entries)
        legend_w = swatch_size + padding * 3 + max_text_w

        x0 = blended.shape[1] - legend_w - padding
        y0 = padding
        cv2.rectangle(blended, (x0, y0),
                      (x0 + legend_w, y0 + legend_h), (0, 0, 0), -1)
        cv2.rectangle(blended, (x0, y0),
                      (x0 + legend_w, y0 + legend_h), (255, 255, 255), 1)

        for i, (_, colour, label) in enumerate(entries):
            sy = y0 + padding + i * line_height
            cv2.rectangle(blended,
                          (x0 + padding, sy),
                          (x0 + padding + swatch_size, sy + swatch_size),
                          colour, -1)
            cv2.putText(blended, label,
                        (x0 + padding * 2 + swatch_size, sy + swatch_size - 2),
                        font, font_scale, (255, 255, 255), thickness)

    return np.hstack([img, blended])


def visualise_directory(image_dir, mask_dir, output_dir, colour_map=None,
                        label_map=None, background=DEFAULT_BACKGROUND,
                        alpha=DEFAULT_ALPHA):
    """Generate overlay visualisations for all masks in a directory.

    Iterates through mask images and finds corresponding source images
    by matching filenames (ignoring extension). Skips masks without a
    matching source image.

    Args:
        image_dir: Directory containing source images.
        mask_dir: Directory containing mask images.
        output_dir: Directory to save the visualisation images.
        colour_map: Dict mapping pixel values to BGR colour tuples.
            Defaults to DEFAULT_COLOUR_MAP.
        label_map: Dict mapping pixel values to label strings for the
            legend. Defaults to DEFAULT_LABEL_MAP.
        background: Pixel value treated as background. Defaults to 255.
        alpha: Opacity of the mask overlay. Defaults to 0.5.
    """
    os.makedirs(output_dir, exist_ok=True)

    image_exts = {'.png', '.jpg', '.jpeg'}
    image_lookup = {}
    for f in os.listdir(image_dir):
        if os.path.splitext(f)[1].lower() in image_exts:
            stem = os.path.splitext(f)[0]
            image_lookup[stem] = os.path.join(image_dir, f)

    count = 0
    for mask_name in sorted(os.listdir(mask_dir)):
        if os.path.splitext(mask_name)[1].lower() not in image_exts:
            continue
        stem = os.path.splitext(mask_name)[0]
        if stem not in image_lookup:
            print(f"Warning: no source image for mask {mask_name}, skipping")
            continue

        mask_path = os.path.join(mask_dir, mask_name)
        vis = visualise_one(image_lookup[stem], mask_path,
                            colour_map=colour_map, label_map=label_map,
                            background=background, alpha=alpha)
        if vis is None:
            print(f"Warning: could not read {mask_name} or its source, skipping")
            continue

        out_name = stem + ".jpg"
        cv2.imwrite(os.path.join(output_dir, out_name), vis)
        count += 1

    print(f"Done. {count} visualisation(s) saved to {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Visualise segmentation masks overlaid on source images")
    parser.add_argument("image_dir", help="Directory containing source images")
    parser.add_argument("mask_dir", help="Directory containing mask images")
    parser.add_argument("-o", "--output", default=None,
                        help="Output directory (default: <mask_dir>_vis)")
    parser.add_argument("-a", "--alpha", type=float, default=DEFAULT_ALPHA,
                        help=f"Overlay opacity (default: {DEFAULT_ALPHA})")
    args = parser.parse_args()

    output_dir = args.output or args.mask_dir.rstrip("/") + "_vis"
    visualise_directory(args.image_dir, args.mask_dir, output_dir, alpha=args.alpha)
