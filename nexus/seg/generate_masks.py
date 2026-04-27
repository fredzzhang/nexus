"""Utilities for generating segmentation masks based on annotation files
in VGG Image Annotator (VIA) format.

Fred Zhang <fredzz@amazon.com>
"""
import os
import cv2
import json
import argparse
import numpy as np
from collections import defaultdict


DEFAULT_CLASS_MAP = {
    "401": 2,   # Decay
    "402": 3,   # Overripe/Wet bruising
    "403": 4,   # Mould
}
DEFAULT_PRIORITY = ["402", "401", "403"]
DEFAULT_BACKGROUND = 255


def load_annotations(annotation_path, class_map=None):
    """Load polygon annotations from a VIA-format JSON file.

    Args:
        annotation_path: Path to the annotation JSON file.
        class_map: Dict mapping class ID strings to pixel values
            (e.g. {"401": 2, "403": 4}). Defaults to DEFAULT_CLASS_MAP.

    Returns:
        fid_to_fname: Maps file ID to file names
        file_annotations: Maps file IDs to lists of (class_id, points) tuples for classes in class_map
        annotated_fids: the set of file IDs that have any metadata entries (regardless of class).
    """
    if class_map is None:
        class_map = DEFAULT_CLASS_MAP

    with open(annotation_path) as f:
        data = json.load(f)

    fid_to_fname = {v["fid"]: v["fname"] for v in data["file"].values()}

    annotated_fids = set()
    file_annotations = defaultdict(list)
    for entry in data["metadata"].values():
        fid = entry["vid"]
        annotated_fids.add(fid)
        class_id = entry["av"].get("1")
        if class_id not in class_map:
            continue
        coords = entry["xy"][1:]
        pts = np.array(coords, dtype=np.int32).reshape(-1, 2)
        file_annotations[fid].append((class_id, pts))

    return fid_to_fname, file_annotations, annotated_fids


def generate_masks(annotation_path, image_dir, output_dir,
                   class_map=None, priority=None, background=DEFAULT_BACKGROUND):
    """Generate segmentation mask images from annotations. Note that when annotated polygons in an image
    do not belong to one of the pre-defined classes, the image is considered to only have background pixels.
    Images without any annotated polygons are considered un-annotated and thus skipped.

    Args:
        annotation_path: Path to the annotation JSON file.
        image_dir: Directory containing the source images.
        output_dir: Directory to save the generated mask PNGs.
        class_map: Dict mapping class ID strings to pixel values
            (e.g. {"401": 2, "403": 4}). Defaults to DEFAULT_CLASS_MAP.
        priority: List of class ID strings in draw order. Classes later
            in the list are drawn on top and override earlier ones on
            overlap. Defaults to DEFAULT_PRIORITY.
        background: Pixel value for unannotated regions. Defaults to 255.
    """
    if class_map is None:
        class_map = DEFAULT_CLASS_MAP
    if priority is None:
        priority = DEFAULT_PRIORITY

    priority_order = {cid: i for i, cid in enumerate(priority)}

    os.makedirs(output_dir, exist_ok=True)
    fid_to_fname, file_annotations, annotated_fids = load_annotations(annotation_path, class_map)

    skipped = []
    for fid, fname in fid_to_fname.items():
        if fid not in annotated_fids:
            skipped.append(fname)
            continue

        img_path = os.path.join(image_dir, fname)
        if not os.path.isfile(img_path):
            continue

        img = cv2.imread(img_path)
        if img is None:
            print(f"Warning: could not read {img_path}, skipping")
            continue
        h, w = img.shape[:2]

        mask = np.full((h, w), background, dtype=np.uint8)
        annots = sorted(file_annotations.get(fid, []),
                        key=lambda a: priority_order.get(a[0], -1))
        for class_id, pts in annots:
            cv2.fillPoly(mask, [pts], class_map[class_id])

        out_name = os.path.splitext(fname)[0] + ".png"
        cv2.imwrite(os.path.join(output_dir, out_name), mask)

    if skipped:
        print(f"Skipped {len(skipped)} un-annotated image(s): {skipped}")
    print(f"Done. Masks saved to {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate segmentation masks from annotation JSON")
    parser.add_argument("annotation", help="Path to annotation JSON file")
    parser.add_argument("image_dir", help="Directory containing source images")
    parser.add_argument("-o", "--output", default=None, help="Output directory (default: <image_dir>_masks)")
    parser.add_argument("-b", "--background", type=int, default=DEFAULT_BACKGROUND,
                        help=f"Background pixel value (default: {DEFAULT_BACKGROUND})")
    parser.add_argument("-m", "--class-map", default=None,
                        help='Class-to-pixel mapping as JSON, e.g. \'{"401": 2, "403": 4}\'')
    parser.add_argument("-p", "--priority", default=None,
                        help='Draw order as JSON list, e.g. \'["402", "401", "403"]\'. '
                             'Later entries override earlier ones on overlap.')
    args = parser.parse_args()

    output_dir = args.output or args.image_dir.rstrip("/") + "_masks"
    class_map = json.loads(args.class_map) if args.class_map else None
    priority = json.loads(args.priority) if args.priority else None
    generate_masks(args.annotation, args.image_dir, output_dir,
                   class_map=class_map, priority=priority, background=args.background)
