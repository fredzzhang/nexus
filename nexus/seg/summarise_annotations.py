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

DEFAULT_THRESHOLDS = {cid: 0.0 for cid in DEFAULT_CLASS_MAP}
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


def summarise(annotation_path, image_dir, thresholds=None):
    """Print per-class image counts and average defect area ratios.

    Args:
        annotation_path: Path to the annotation JSON file.
        image_dir: Directory containing source images.
        thresholds: Dict mapping class ID strings to minimum area ratio
            thresholds (between 0 and 1). The keys define which classes
            to report on. Annotations whose per-image area ratio falls
            below the threshold are excluded from the filtered columns.
            Defaults to DEFAULT_THRESHOLDS.
    """
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS

    class_filter = {cid: 0 for cid in thresholds}
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

    header = (f"\n{'Class':<10} {'Name':<35} {'Images':>8} {'Avg area (%)':>14}"
              f" | {'Thresh':>6} {'Filtered':>8} {'Avg area (%)':>14}")
    print(header)
    print("-" * len(header))
    filtered_defect_fids = set()
    for cid in sorted(thresholds):
        ratios = class_image_ratios[cid]
        n = len(ratios)
        avg = sum(ratios.values()) / n * 100 if n else 0
        thresh = thresholds[cid]
        filtered = {fid: r for fid, r in ratios.items() if r >= thresh}
        nf = len(filtered)
        avgf = sum(filtered.values()) / nf * 100 if nf else 0
        filtered_defect_fids.update(filtered)
        name = class_names.get(cid, "Unknown")
        thresh_str = f"{thresh * 100:.1f}%"
        print(f"{cid:<10} {name:<35} {n:>8} {avg:>13.2f}%"
              f" | {thresh_str:>6} {nf:>8} {avgf:>13.2f}%")
    filtered_bg = len(annotated_fids - filtered_defect_fids)
    print(f"{'bg':<10} {BACKGROUND_LABEL:<35} {bg_count:>8} {'N/A':>14}"
          f" | {'':>6} {filtered_bg:>8} {'N/A':>14}")
    print("-" * len(header))
    total_filtered = len(filtered_defect_fids) + filtered_bg
    print(f"{'Total':<10} {'':<35} {len(annotated_fids):>8} {'':>14}"
          f" | {'':>6} {total_filtered:>8}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="summarise annotation statistics")
    parser.add_argument("annotation", help="Path to annotation JSON file")
    parser.add_argument("image_dir", help="Directory containing source images")
    parser.add_argument("-t", "--thresholds", default=None,
                        help='Class IDs and min area ratio (0-1) as JSON, '
                             'e.g. \'{"401": 0.005, "402": 0.01, "403": 0.002}\'')
    args = parser.parse_args()

    thresholds = {k: float(v) for k, v in json.loads(args.thresholds).items()} if args.thresholds else None
    summarise(args.annotation, args.image_dir, thresholds=thresholds)
