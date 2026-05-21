"""Claude inference via Amazon Bedrock

Fred Zhang <frezz@amazon.com>
"""

import json
import boto3
import base64
import argparse

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

_DEFAULT_MODEL = "us.anthropic.claude-opus-4-20250514-v1:0"
_DEFAULT_REGION = "us-east-1"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _build_messages(prompt: str, image_paths: list[str] | None = None) -> list[dict]:
    """Build the messages payload, optionally including images."""
    content = []
    for path in (image_paths or []):
        p = Path(path)
        media_type = _MEDIA_TYPES[p.suffix.lower()]
        data = base64.standard_b64encode(p.read_bytes()).decode()
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]


def single_inference_with_claude(
    prompt: str,
    image_paths: list[str] | None = None,
    model_id: str = _DEFAULT_MODEL,
    region: str = _DEFAULT_REGION,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> str:
    """Run a single inference call with Claude on Bedrock.

    Args:
        prompt: Text prompt to send to Claude.
        image_paths: Optional list of image file paths for vision input.
        model_id: Bedrock model identifier.
        region: AWS region for the Bedrock endpoint.
        max_tokens: Maximum number of tokens in the response.
        temperature: Sampling temperature.

    Returns:
        The text content of Claude's response.
    """
    client = boto3.client("bedrock-runtime", region_name=region)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": _build_messages(prompt, image_paths),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    response = client.invoke_model(modelId=model_id, body=json.dumps(body))
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


def generate_with_claude(
    input_dir: str,
    output_dir: str,
    prompt: str,
    model_id: str = _DEFAULT_MODEL,
    region: str = _DEFAULT_REGION,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    workers: int = 4,
):
    """Batch-process images in a directory with Claude vision.

    Sends each image along with the prompt to Claude and saves the
    text response as a .txt file in the output directory.

    Args:
        input_dir: Directory containing source images.
        output_dir: Directory to save text responses.
        prompt: Prompt to apply to each image.
        model_id: Bedrock model identifier.
        region: AWS region for the Bedrock endpoint.
        max_tokens: Maximum number of tokens per response.
        temperature: Sampling temperature.
        workers: Number of parallel requests.
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    images = [p for p in input_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS]
    if not images:
        print(f"No images found in {input_dir}")
        return

    print(f"Processing {len(images)} images with {workers} workers...")

    def _process(image_path: Path):
        text = single_inference_with_claude(
            prompt=prompt,
            image_paths=[str(image_path)],
            model_id=model_id,
            region=region,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        out_path = output_dir / f"{image_path.stem}.txt"
        out_path.write_text(text)
        print(f"✓ {image_path.name} -> {out_path.name}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process, img): img for img in images}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"✗ {futures[future].name}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Claude inference via Amazon Bedrock")
    sub = parser.add_subparsers(dest="command", required=True)

    # Single inference
    single = sub.add_parser("single", help="Run a single inference call")
    single.add_argument("--prompt", required=True, help="Text prompt")
    single.add_argument("--images", nargs="*", default=[], help="Image paths for vision input")
    single.add_argument("--model", default=_DEFAULT_MODEL, help="Bedrock model ID")
    single.add_argument("--region", default=_DEFAULT_REGION, help="AWS region")
    single.add_argument("--max-tokens", type=int, default=4096, help="Max response tokens")
    single.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature")

    # Batch inference
    batch = sub.add_parser("batch", help="Batch-process a directory of images")
    batch.add_argument("--input-dir", required=True, help="Directory containing source images")
    batch.add_argument("--output-dir", required=True, help="Directory to save responses")
    batch.add_argument("--prompt", required=True, help="Prompt to apply to each image")
    batch.add_argument("--model", default=_DEFAULT_MODEL, help="Bedrock model ID")
    batch.add_argument("--region", default=_DEFAULT_REGION, help="AWS region")
    batch.add_argument("--max-tokens", type=int, default=4096, help="Max response tokens")
    batch.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature")
    batch.add_argument("--workers", type=int, default=4, help="Number of parallel requests")

    args = parser.parse_args()

    if args.command == "single":
        response = single_inference_with_claude(
            prompt=args.prompt,
            image_paths=args.images or None,
            model_id=args.model,
            region=args.region,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
        )
        print(response)
    elif args.command == "batch":
        generate_with_claude(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            prompt=args.prompt,
            model_id=args.model,
            region=args.region,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            workers=args.workers,
        )


if __name__ == "__main__":
    main()
