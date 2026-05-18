"""Utilities for manipulating polygon annotation files.

Fred Zhang <frezz@amazon.com>
"""
import os
import json
import random
import string


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


def remap_classes(annotation_path, output_path, class_mapping, drop_unmapped=None):
    """Remap class indices and names in an annotation file.

    Args:
        annotation_path: Path to the input annotation JSON file.
        output_path: Path to write the remapped JSON file.
        class_mapping: Dict mapping new class index strings to
            ``[new_name, old_index]`` pairs. For example::

                {
                    "1": ["Sedan", "401"],
                    "2": ["Ute", "402"],
                    "3": ["Van", "403"],
                    "4": ["Waggon", None]
                }

            Polygons with old index ``"401"`` become new index ``"1"``
            with name ``"Sedan"``, etc. If old_index is None, a new class
            is added without remapping any polygons.
        drop_unmapped: Controls handling of polygons whose class is not
            in the mapping. If True, they are removed. If False, they are
            kept unchanged. If None (default), the user is prompted to
            confirm dropping when unmapped classes are encountered.
    """
    with open(annotation_path, "r") as f:
        data = json.load(f)

    metadata = data.get("metadata", {})
    old_options = data.get("attribute", {}).get("1", {}).get("options", {})

    # Build old_index -> new_index lookup
    old_to_new = {}
    new_options = {}
    for new_idx, (new_name, old_idx) in class_mapping.items():
        if old_idx is not None:
            old_to_new[old_idx] = new_idx
        new_options[new_idx] = new_name

    # Check for unmapped classes
    mapped_old_indices = set(old_to_new.keys())
    used_classes = {meta.get("av", {}).get("1") for meta in metadata.values()}
    used_classes.discard(None)
    unmapped = used_classes - mapped_old_indices
    if unmapped and drop_unmapped is None:
        unmapped_names = [f"{idx} ({old_options.get(idx, 'Unknown')})" for idx in sorted(unmapped)]
        print(f"The following classes are not in the mapping and will be dropped:")
        for name in unmapped_names:
            print(f"  {name}")
        response = input("Proceed? [Y/n] ").strip().lower()
        if response and response != "y":
            print("Aborted.")
            return
        drop_unmapped = True
    elif drop_unmapped is None:
        drop_unmapped = False

    # Keep unmapped classes in options if not dropping
    if not drop_unmapped:
        for old_idx, name in old_options.items():
            if old_idx not in old_to_new:
                new_options[old_idx] = name

    # Remap metadata
    new_metadata = {}
    for key, meta in metadata.items():
        av = meta.get("av", {})
        class_idx = av.get("1")
        if class_idx is not None:
            if class_idx in old_to_new:
                av = dict(av)
                av["1"] = old_to_new[class_idx]
            elif drop_unmapped:
                continue
        new_metadata[key] = {"vid": meta["vid"], "xy": meta["xy"], "av": av}

    # Remove file entries that no longer have any metadata
    file_dict = data.get("file", {})
    fids_with_polygons = {m["vid"] for m in new_metadata.values()}
    new_file = {fid: info for fid, info in file_dict.items() if fid in fids_with_polygons}

    output = {
        "project": data.get("project", {"pname": ""}),
        "attribute": {"1": {"options": new_options}},
        "file": new_file,
        "metadata": new_metadata,
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=4)
