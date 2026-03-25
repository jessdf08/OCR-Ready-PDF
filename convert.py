#!/usr/bin/env python3
"""
Image to Searchable PDF Converter
Converts up to 300 images into a single OCR-searchable PDF.
Adobe Acrobat can recognize and read aloud the embedded text.
File size is automatically kept at or below 100 MB.
"""

import os
import re
import sys
import argparse
from pathlib import Path
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    sys.exit("Missing dependency: pip install Pillow")

try:
    import pytesseract
except ImportError:
    sys.exit("Missing dependency: pip install pytesseract  (also install tesseract-ocr system package)")

try:
    from pypdf import PdfWriter, PdfReader
except ImportError:
    sys.exit("Missing dependency: pip install pypdf")


MAX_FILE_SIZE_MB = 100
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_IMAGES = 300
SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp"}

# Quality ladder: try from highest to lowest until size fits
QUALITY_LADDER = [85, 75, 65, 55, 45, 35, 25, 15, 10]

# Maximum dimension (longest side) when resizing is needed
MAX_DIM_LADDER = [2400, 1800, 1400, 1000]


def natural_sort_key(path: Path) -> list:
    """Sort filenames so page_2 comes before page_10."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", path.name)]


def gather_images(input_path: Path) -> list[Path]:
    if not input_path.exists():
        sys.exit(f"Error: path not found: {input_path}")

    if input_path.is_dir():
        files = sorted(
            [f for f in input_path.iterdir() if f.suffix.lower() in SUPPORTED_FORMATS],
            key=natural_sort_key,
        )
    elif input_path.is_file() and input_path.suffix.lower() in SUPPORTED_FORMATS:
        files = [input_path]
    else:
        sys.exit(f"Error: {input_path} is not a directory or supported image file.")

    if not files:
        sys.exit(f"No supported images found in {input_path}")

    if len(files) > MAX_IMAGES:
        print(f"Warning: found {len(files)} images — using first {MAX_IMAGES}.")
        files = files[:MAX_IMAGES]

    return files


def compress_image(img: Image.Image, quality: int, max_dim: int) -> Image.Image:
    """Resize (if needed) and JPEG-compress an image, returning a fresh PIL image."""
    # Resize down if larger than max_dim
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # Convert to RGB (JPEG doesn't support RGBA / palette modes)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    buf.seek(0)
    return Image.open(buf)


def estimate_total_bytes(files: list[Path], quality: int, max_dim: int) -> int:
    """Quick size estimate by compressing every image and summing byte counts."""
    total = 0
    for f in files:
        with Image.open(f) as img:
            compressed = compress_image(img, quality, max_dim)
            buf = BytesIO()
            compressed.save(buf, format="JPEG", quality=quality, optimize=True)
            total += buf.tell()
    return int(total * 1.12)  # ~12% overhead for PDF structure & OCR text layer


def find_quality_settings(files: list[Path]) -> tuple[int, int]:
    """
    Return (quality, max_dim) that keeps the output under MAX_FILE_SIZE_BYTES.
    Tries quality reduction first; if still too big, also shrinks dimensions.
    """
    print("Estimating optimal quality to stay under 100 MB …")
    for max_dim in MAX_DIM_LADDER:
        for quality in QUALITY_LADDER:
            est = estimate_total_bytes(files, quality, max_dim)
            tag = f"quality={quality}, max_dim={max_dim}px"
            print(f"  {tag}: ~{est / 1024 / 1024:.1f} MB")
            if est <= MAX_FILE_SIZE_BYTES:
                print(f"  → Selected {tag}")
                return quality, max_dim

    # Absolute fallback
    print("  → Using minimum quality / smallest size as fallback.")
    return 10, 800


def page_to_searchable_pdf(img: Image.Image, quality: int, max_dim: int) -> bytes:
    """
    Convert a single PIL image into a searchable PDF page.
    The page contains the (compressed) image with an invisible OCR text layer
    so Adobe Acrobat can select, copy, and read it aloud.
    """
    compressed = compress_image(img, quality, max_dim)
    # pytesseract outputs a single-page PDF with image + invisible text overlay
    pdf_bytes = pytesseract.image_to_pdf_or_hocr(compressed, extension="pdf")
    return pdf_bytes


def convert(files: list[Path], output_path: Path, quality: int, max_dim: int) -> None:
    writer = PdfWriter()
    total = len(files)

    for i, image_file in enumerate(files, 1):
        print(f"  [{i}/{total}] {image_file.name}")
        with Image.open(image_file) as img:
            img.load()  # force read before context closes
            pdf_bytes = page_to_searchable_pdf(img, quality, max_dim)

        reader = PdfReader(BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as fh:
        writer.write(fh)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert up to 300 images into a single OCR-searchable PDF (≤ 100 MB).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python convert.py ./scans/
  python convert.py ./scans/ -o book.pdf
  python convert.py ./scans/ -q 70          # force quality instead of auto
        """,
    )
    parser.add_argument("input", help="Directory of images (or a single image file)")
    parser.add_argument(
        "-o", "--output", default="output.pdf",
        help="Output PDF path (default: output.pdf)"
    )
    parser.add_argument(
        "-q", "--quality", type=int, default=None,
        help="JPEG quality 1–95. Skips auto-sizing; use with caution on large sets."
    )
    parser.add_argument(
        "--max-dim", type=int, default=None,
        help="Resize images so longest side ≤ this many pixels (default: auto)."
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    print(f"Scanning: {input_path}")
    files = gather_images(input_path)
    print(f"Found {len(files)} image(s).\n")

    if args.quality is not None:
        quality = max(1, min(95, args.quality))
        max_dim = args.max_dim if args.max_dim else 2400
        print(f"Using specified quality={quality}, max_dim={max_dim}px")
    else:
        quality, max_dim = find_quality_settings(files)

    print(f"\nConverting {len(files)} page(s) — this may take a while …\n")
    convert(files, output_path, quality, max_dim)

    file_size = output_path.stat().st_size
    size_mb = file_size / 1024 / 1024
    print(f"\nDone!  →  {output_path}  ({size_mb:.2f} MB)")

    if file_size > MAX_FILE_SIZE_BYTES:
        print(
            f"⚠  File is {size_mb:.2f} MB which exceeds the {MAX_FILE_SIZE_MB} MB target.\n"
            f"   Re-run without -q to let the tool auto-select a lower quality."
        )
    else:
        print(f"File size is within the {MAX_FILE_SIZE_MB} MB limit. ✓")


if __name__ == "__main__":
    main()
