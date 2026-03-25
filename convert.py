#!/usr/bin/env python3
"""
Image / PDF to Searchable PDF Converter

Modes
-----
  Image mode : pass a directory (or single image).
               Images are sorted by EXIF capture date/time.
               Fallback: file modification time, then natural filename order.

  PDF mode   : pass a PDF file.
               Each page is rasterised, OCR'd, and re-assembled into a
               new searchable PDF.

In both modes the output PDF contains every page as an image with an invisible
text overlay, so Adobe Acrobat can select, search, and read the text aloud
without running a separate OCR pass.

File size is automatically kept at or below 100 MB by adjusting JPEG quality
and, if necessary, downscaling image dimensions.
"""

import re
import sys
import argparse
import tempfile
from datetime import datetime
from pathlib import Path
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    sys.exit("Missing dependency: pip install Pillow")

try:
    import pytesseract
except ImportError:
    sys.exit(
        "Missing dependency: pip install pytesseract\n"
        "Also install the Tesseract engine:  https://github.com/tesseract-ocr/tesseract"
    )

try:
    from pypdf import PdfWriter, PdfReader
except ImportError:
    sys.exit("Missing dependency: pip install pypdf")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_SIZE_MB = 100
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_PAGES = 300
SUPPORTED_IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp"}

# JPEG quality ladder — highest first; step down until size fits
QUALITY_LADDER = [85, 75, 65, 55, 45, 35, 25, 15, 10]
# Max longest-side dimension ladder — used when quality alone isn't enough
MAX_DIM_LADDER = [2400, 1800, 1400, 1000]

# EXIF tag: DateTimeOriginal  (same numeric ID across JPEG/TIFF/WebP/PNG)
_EXIF_TAG_DATETIME_ORIGINAL = 36867


# ---------------------------------------------------------------------------
# Sorting helpers
# ---------------------------------------------------------------------------

def _natural_sort_key(path: Path) -> list:
    """Natural filename sort: 'page_2' sorts before 'page_10'."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", path.name)]


def _get_exif_datetime(path: Path) -> datetime | None:
    """Return the EXIF DateTimeOriginal for an image file, or None."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()            # Pillow >= 6.0; works for JPEG/TIFF/PNG/WebP
            if exif:
                raw = exif.get(_EXIF_TAG_DATETIME_ORIGINAL)
                if raw:
                    return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def sort_images_by_capture_date(files: list[Path]) -> list[Path]:
    """
    Sort image paths by capture date:
      1. EXIF DateTimeOriginal  (preferred)
      2. File modification time (fallback for images without EXIF)
      3. Natural filename order (if *no* image has EXIF data at all)
    """
    print("Reading capture dates …")
    dated = [(f, _get_exif_datetime(f)) for f in files]
    exif_count = sum(1 for _, dt in dated if dt is not None)

    if exif_count == 0:
        print("  No EXIF capture dates found — falling back to natural filename order.")
        return sorted(files, key=_natural_sort_key)

    print(f"  EXIF dates found in {exif_count}/{len(files)} image(s).")
    if exif_count < len(files):
        print("  Images without EXIF will be ordered by file modification time.")

    def sort_key(item: tuple) -> datetime:
        path, dt = item
        return dt if dt else datetime.fromtimestamp(path.stat().st_mtime)

    sorted_files = [f for f, _ in sorted(dated, key=sort_key)]

    # Show the resolved order for transparency
    print("  Capture-date order:")
    for f, dt in sorted(dated, key=sort_key):
        tag = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "mtime"
        print(f"    {tag}  {f.name}")

    return sorted_files


# ---------------------------------------------------------------------------
# Image gathering
# ---------------------------------------------------------------------------

def gather_images(input_path: Path) -> list[Path]:
    """Collect supported image files from a directory or accept a single image."""
    if not input_path.exists():
        sys.exit(f"Error: path not found: {input_path}")

    if input_path.is_dir():
        files = [f for f in input_path.iterdir() if f.suffix.lower() in SUPPORTED_IMAGE_FORMATS]
    elif input_path.is_file() and input_path.suffix.lower() in SUPPORTED_IMAGE_FORMATS:
        files = [input_path]
    else:
        sys.exit(
            f"Error: '{input_path}' is neither a directory nor a supported image file.\n"
            f"Supported formats: {', '.join(sorted(SUPPORTED_IMAGE_FORMATS))}"
        )

    if not files:
        sys.exit(f"No supported images found in {input_path}")

    if len(files) > MAX_PAGES:
        print(f"Warning: found {len(files)} images — using first {MAX_PAGES} (by capture date).")

    return files


# ---------------------------------------------------------------------------
# PDF page extraction
# ---------------------------------------------------------------------------

def extract_pdf_to_images(pdf_path: Path, tmp_dir: Path) -> list[Path]:
    """
    Rasterise each page of a PDF to a JPEG in tmp_dir and return the file paths.
    Requires the pdf2image package and poppler system library.
    """
    try:
        from pdf2image import convert_from_path
    except ImportError:
        sys.exit(
            "Missing dependency for PDF input: pip install pdf2image\n"
            "Also install poppler:\n"
            "  macOS:          brew install poppler\n"
            "  Ubuntu/Debian:  sudo apt-get install poppler-utils\n"
            "  Windows:        https://github.com/oschwartz10612/poppler-windows/releases"
        )

    print(f"Rasterising PDF pages (200 DPI) …")
    try:
        pages = convert_from_path(str(pdf_path), dpi=200)
    except Exception as exc:
        sys.exit(f"Failed to rasterise PDF: {exc}")

    if len(pages) > MAX_PAGES:
        print(f"  PDF has {len(pages)} pages — using first {MAX_PAGES}.")
        pages = pages[:MAX_PAGES]

    print(f"  Extracted {len(pages)} page(s).")
    file_paths: list[Path] = []
    for i, page in enumerate(pages, 1):
        dest = tmp_dir / f"page_{i:04d}.jpg"
        page.convert("RGB").save(dest, format="JPEG", quality=95)
        file_paths.append(dest)

    return file_paths


# ---------------------------------------------------------------------------
# Size estimation & quality selection
# ---------------------------------------------------------------------------

def _compressed_size(img: Image.Image, quality: int, max_dim: int) -> int:
    """Return the byte count of img after compression (without writing to disk)."""
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.tell()


def estimate_total_bytes(files: list[Path], quality: int, max_dim: int) -> int:
    """Estimate final PDF size by compressing every source image (no OCR yet)."""
    total = sum(
        _compressed_size(Image.open(f).convert("RGB"), quality, max_dim)
        for f in files
    )
    return int(total * 1.12)   # ~12 % overhead for PDF structure + OCR text layer


def find_quality_settings(files: list[Path]) -> tuple[int, int]:
    """
    Return (quality, max_dim) — the highest-quality combination that keeps
    the output PDF at or below MAX_FILE_SIZE_BYTES.
    """
    print("Estimating optimal quality to stay under 100 MB …")
    for max_dim in MAX_DIM_LADDER:
        for quality in QUALITY_LADDER:
            est = estimate_total_bytes(files, quality, max_dim)
            tag = f"quality={quality}, max_dim={max_dim}px"
            print(f"  {tag}: ~{est / 1024 / 1024:.1f} MB")
            if est <= MAX_FILE_SIZE_BYTES:
                print(f"  → Selected {tag}\n")
                return quality, max_dim

    print("  → Using minimum quality / smallest dimensions as fallback.\n")
    return 10, 800


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

def compress_image(img: Image.Image, quality: int, max_dim: int) -> Image.Image:
    """Return a resized + JPEG-compressed copy of img."""
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    buf.seek(0)
    return Image.open(buf)


def page_to_searchable_pdf(img: Image.Image, quality: int, max_dim: int) -> bytes:
    """
    Run OCR on img and return a one-page PDF containing:
      • the (compressed) image as the visual layer
      • an invisible text overlay for selection / search / read-aloud
    """
    compressed = compress_image(img, quality, max_dim)
    return pytesseract.image_to_pdf_or_hocr(compressed, extension="pdf")


def build_pdf(files: list[Path], output_path: Path, quality: int, max_dim: int) -> None:
    """Convert each image file to a searchable PDF page and merge them."""
    writer = PdfWriter()
    total = len(files)

    for i, image_file in enumerate(files, 1):
        print(f"  [{i:>{len(str(total))}}/{total}] {image_file.name}")
        with Image.open(image_file) as img:
            img.load()
            pdf_bytes = page_to_searchable_pdf(img, quality, max_dim)

        for page in PdfReader(BytesIO(pdf_bytes)).pages:
            writer.add_page(page)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as fh:
        writer.write(fh)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert images or a scanned PDF into a single OCR-searchable PDF (≤ 100 MB).\n"
            "Adobe Acrobat can read text aloud from the output without a separate OCR step."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Image directory — auto quality, sorted by EXIF capture date
  python convert.py ./photos/

  # Scanned PDF — OCR each page, produce searchable output
  python convert.py scan.pdf -o searchable.pdf

  # Force quality (skip auto-detection)
  python convert.py ./photos/ -q 70

  # Force quality AND max dimension
  python convert.py ./photos/ -q 60 --max-dim 1800
        """,
    )
    parser.add_argument(
        "input",
        help="Directory of images, a single image file, or a PDF file",
    )
    parser.add_argument(
        "-o", "--output", default="output.pdf",
        help="Output PDF path (default: output.pdf)",
    )
    parser.add_argument(
        "-q", "--quality", type=int, default=None,
        help="JPEG quality 1–95. Skips auto-sizing; use with caution on large sets.",
    )
    parser.add_argument(
        "--max-dim", type=int, default=None,
        help="Resize images so longest side ≤ N pixels (default: auto).",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    # ── Determine input mode ────────────────────────────────────────────────
    tmp_dir_obj: tempfile.TemporaryDirectory | None = None

    if not input_path.exists():
        sys.exit(f"Error: path not found: {input_path}")

    if input_path.suffix.lower() == ".pdf":
        # PDF mode: rasterise pages into a temp directory, then treat as images
        print(f"Input: PDF  →  {input_path}\n")
        tmp_dir_obj = tempfile.TemporaryDirectory()
        tmp_path = Path(tmp_dir_obj.name)
        files = extract_pdf_to_images(input_path, tmp_path)
        # PDF pages are already in correct order — no date-based sort needed
    else:
        # Image mode: gather files, sort by EXIF capture date
        print(f"Input: images  →  {input_path}\n")
        raw_files = gather_images(input_path)
        files = sort_images_by_capture_date(raw_files)
        if len(files) > MAX_PAGES:
            files = files[:MAX_PAGES]

    print(f"\nUsing {len(files)} page(s) for conversion.\n")

    # ── Select quality ──────────────────────────────────────────────────────
    if args.quality is not None:
        quality = max(1, min(95, args.quality))
        max_dim = args.max_dim if args.max_dim else 2400
        print(f"Using specified quality={quality}, max_dim={max_dim}px\n")
    else:
        quality, max_dim = find_quality_settings(files)

    # ── Convert ─────────────────────────────────────────────────────────────
    print(f"Converting {len(files)} page(s) to searchable PDF — this may take a while …\n")
    build_pdf(files, output_path, quality, max_dim)

    # ── Clean up temp dir (PDF mode) ─────────────────────────────────────────
    if tmp_dir_obj:
        tmp_dir_obj.cleanup()

    # ── Report ───────────────────────────────────────────────────────────────
    file_size = output_path.stat().st_size
    size_mb = file_size / 1024 / 1024
    print(f"\nDone!  →  {output_path}  ({size_mb:.2f} MB)")

    if file_size > MAX_FILE_SIZE_BYTES:
        print(
            f"  WARNING: output is {size_mb:.2f} MB, above the {MAX_FILE_SIZE_MB} MB target.\n"
            f"  Re-run without -q to let auto-quality select a lower setting."
        )
    else:
        print(f"  File size is within the {MAX_FILE_SIZE_MB} MB limit.")


if __name__ == "__main__":
    main()
