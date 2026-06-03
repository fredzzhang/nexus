from .polygon_annotation import polygon_annotation_with_reference
from .annotation_utils import merge_annotations, collate_annotations, remap_classes, add_suffix
from .generate_masks import load_annotations, generate_masks
from .visualise_masks import visualise_one, visualise_directory
from .triplet_browser import segmentation_diagnosis


def __getattr__(name):
    if name == "summarise":
        from .summarise_annotations import summarise
        return summarise
    raise AttributeError(f"module 'nexus.seg' has no attribute {name!r}")
