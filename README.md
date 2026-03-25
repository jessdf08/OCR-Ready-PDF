# OCR-Ready PDF Converter

Converts up to **300 images** into a single, **OCR-searchable PDF** that Adobe Acrobat can recognize text in and read aloud.

- Automatically adjusts JPEG quality (and optionally image dimensions) so the output stays **at or below 100 MB**.
- Supports JPG, PNG, BMP, TIFF, GIF, and WebP input formats.
- Images are sorted in natural order (e.g. `page_2` before `page_10`).

---

## Requirements

### System dependency — Tesseract OCR engine

**macOS**
```bash
brew install tesseract
```

**Ubuntu / Debian**
```bash
sudo apt-get install tesseract-ocr
```

**Windows**
Download the installer from https://github.com/UB-Mannheim/tesseract/wiki and add it to your `PATH`.

Additional language packs (e.g. Spanish, French) can be installed via your package manager or the Tesseract installer.

### Python dependencies
```bash
pip install -r requirements.txt
```

---

## Usage

```
python convert.py <input> [options]
```

| Argument | Description |
|---|---|
| `input` | Directory of images **or** a single image file |
| `-o`, `--output` | Output PDF path (default: `output.pdf`) |
| `-q`, `--quality` | JPEG quality 1–95. Skips auto-sizing (use carefully on large sets) |
| `--max-dim` | Resize images so longest side ≤ N pixels (default: auto) |

### Examples

```bash
# Convert all images in ./scans/ — auto quality to stay under 100 MB
python convert.py ./scans/

# Write to a custom output path
python convert.py ./scans/ -o my_book.pdf

# Force quality 70 (no auto-detection)
python convert.py ./scans/ -q 70

# Force quality AND max dimension
python convert.py ./scans/ -q 60 --max-dim 1800
```

---

## How it works

1. **Gather & sort** — collects all supported images from the input directory in natural filename order.
2. **Size estimation** — compresses a copy of every image at each quality level and estimates the total PDF size (with ~12% overhead for the PDF structure and OCR text layer).
3. **Quality selection** — picks the highest quality that keeps the output ≤ 100 MB. If quality alone isn't enough, the images are also downscaled.
4. **OCR + PDF generation** — each image is processed by Tesseract, which produces a single-page PDF containing the image as the background and an invisible text overlay. This is the layer Adobe uses for text recognition and read-aloud.
5. **Merge** — all single-page PDFs are merged into one output file.

---

## Adobe Read Aloud

Open the output PDF in **Adobe Acrobat** or **Adobe Acrobat Reader**, then:

- **Windows/Linux**: `View → Read Out Loud → Activate Read Out Loud`, then `Read This Page` or `Read To End Of Document`
- **macOS**: same menu path, or use the built-in macOS text-to-speech on the selectable text

Because the invisible OCR text layer is embedded directly in the PDF, Adobe does not need to run a separate OCR pass — it will find and read the text immediately.

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `TesseractNotFoundError` | Install Tesseract and ensure it is on your `PATH` |
| Output exceeds 100 MB | Run without `-q` so auto-quality kicks in; or add `--max-dim 1200` |
| Poor text recognition | Use higher-resolution source images; 300 DPI scans work best |
| Wrong page order | Rename images so they sort naturally (`001.jpg`, `002.jpg`, …) |
