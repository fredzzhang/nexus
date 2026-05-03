"""Print per-class summary statistics from a VIA-format annotation file.

Fred Zhang <fredzz@amazon.com>
"""
import os
import cv2
import json
import argparse
import numpy as np
from collections import defaultdict

from .generate_masks import DEFAULT_CLASS_MAP, load_annotations

DEFAULT_CLASSES = list(DEFAULT_CLASS_MAP.keys())
BACKGROUND_LABEL = "Background"


def _load_class_names(annotation_path):
    """Return a dict mapping class ID strings to names from the annotation file."""
    with open(annotation_path) as f:
        data = json.load(f)
    return data.get("attribute", {}).get("1", {}).get("options", {})


def _get_image_size(image_dir, fname):
    """Return total pixel count for an image, or None if unreadable."""
    img = cv2.imread(os.path.join(image_dir, fname))
    if img is None:
        return None
    h, w = img.shape[:2]
    return h * w


def summarise(annotation_path, image_dir, classes=None):
    if classes is None:
        classes = DEFAULT_CLASSES

    class_filter = {cid: 0 for cid in classes}
    fid_to_fname, file_annotations, annotated_fids = load_annotations(annotation_path, class_filter)
    class_names = _load_class_names(annotation_path)

    # Cache image sizes
    fid_to_size = {}
    for fid in annotated_fids:
        fname = fid_to_fname.get(fid)
        if fname:
            size = _get_image_size(image_dir, fname)
            if size:
                fid_to_size[fid] = size

    # Per class: collect area ratio per image
    class_image_ratios = defaultdict(lambda: defaultdict(float))
    fids_with_defects = set()
    for fid, annots in file_annotations.items():
        if fid not in fid_to_size:
            continue
        for class_id, pts in annots:
            area = cv2.contourArea(pts.astype(np.float32))
            class_image_ratios[class_id][fid] += area / fid_to_size[fid]
            fids_with_defects.add(fid)

    bg_count = len(annotated_fids - fids_with_defects)

    header = f"\n{'Class':<10} {'Name':<35} {'Images':>8} {'Avg area (%)':>14}"
    print(header)
    print("-" * len(header))
    for cid in sorted(classes):
        images = class_image_ratios[cid]
        n = len(images)
        avg = sum(images.values()) / n * 100 if n else 0
        name = class_names.get(cid, "Unknown")
        print(f"{cid:<10} {name:<35} {n:>8} {avg:>13.2f}%")
    print(f"{'bg':<10} {BACKGROUND_LABEL:<35} {bg_count:>8} {'N/A':>14}")
    print("-" * len(header))
    print(f"{'Total':<10} {'':<35} {len(annotated_fids):>8}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="summarise annotation statistics")
    parser.add_argument("annotation", help="Path to annotation JSON file")
    parser.add_argument("image_dir", help="Directory containing source images")
    parser.add_argument("-c", "--classes", default=None,
                        help='Class IDs as JSON list, e.g. \'["401", "403"]\'')
    args = parser.parse_args()

    classes = json.loads(args.classes) if args.classes else None
    summarise(args.annotation, args.image_dir, classes)
