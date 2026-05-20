"""Batch image generation using fal.ai Nano Banana 2 edit API

Fred Zhang <frezz@amazon.com>
"""

import os
import argparse
import requests
import fal_client

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

def upload_and_edit(image_path: Path, prompt: str, output_dir: Path, resolution: str, ref_urls: list[str]):
    """Upload a local image to fal.ai and run the Nano Banana 2 edit model.

    Args:
        image_path: Path to the source image.
        prompt: Editing prompt describing the desired transformation.
        output_dir: Directory to save generated images.
        resolution: Output resolution ("0.5K", "1K", "2K", or "4K").
        ref_urls: Pre-uploaded fal.ai URLs for reference images.
    """
    url = fal_client.upload_file(image_path)
    result = fal_client.subscribe(
        "fal-ai/nano-banana-2/edit",
        arguments={"prompt": prompt, "image_urls": [url] + ref_urls, "resolution": resolution, "output_format": "jpeg"},
    )
    for i, img in enumerate(result["images"]):
        resp = requests.get(img["url"])
        resp.raise_for_status()
        suffix = f"_{i}" if i > 0 else ""
        out_path = output_dir / f"{image_path.stem}{suffix}.jpg"
        out_path.write_bytes(resp.content)
        print(f"✓ {image_path.name} -> {out_path.name}")


def generate_with_nano_banana(
    input_dir: str,
    output_dir: str,
    prompt: str,
    fal_key_path: str | None = None,
    reference_images: list[str] | None = None,
    resolution: str = "0.5K",
    workers: int = 4,
):
    """Batch-edit images using the fal.ai Nano Banana 2 model.

    Args:
        input_dir: Directory containing source images.
        output_dir: Directory to save generated images.
        prompt: Editing prompt to apply to all images.
        fal_key_path: Path to a file containing the fal.ai API key.
            If provided, sets the FAL_KEY environment variable.
        reference_images: Optional list of reference image paths included
            with every request.
        resolution: Output resolution. One of "0.5K", "1K", "2K", "4K".
        workers: Number of parallel requests.
    """
    if fal_key_path:
        os.environ.setdefault("FAL_KEY", Path(fal_key_path).read_text().strip())
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    images = [p for p in input_dir.iterdir() if p.suffix.lower() in exts]

    if not images:
        print(f"No images found in {input_dir}")
        return

    ref_urls = [fal_client.upload_file(Path(p)) for p in (reference_images or [])]
    if ref_urls:
        print(f"Uploaded {len(ref_urls)} reference image(s)")

    print(f"Processing {len(images)} images with {workers} workers...")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(upload_and_edit, img, prompt, output_dir, resolution, ref_urls): img for img in images}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"✗ {futures[future].name}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Batch image editing with Nano Banana 2")
    parser.add_argument("--input-dir", required=True, help="Directory containing source images")
    parser.add_argument("--output-dir", required=True, help="Directory to save generated images")
    parser.add_argument("--prompt", required=True, help="Editing prompt to apply to all images")
    parser.add_argument("--reference-images", nargs="*", default=[], help="Reference image paths included with every request")
    parser.add_argument("--resolution", default="0.5K", choices=["0.5K", "1K", "2K", "4K"], help="Output resolution")
    parser.add_argument("--fal-key-path", default=None, help="Path to file containing fal.ai API key")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel requests")
    args = parser.parse_args()

    generate_with_nano_banana(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        prompt=args.prompt,
        fal_key_path=args.fal_key_path,
        reference_images=args.reference_images,
        resolution=args.resolution,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
