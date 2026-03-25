# OCR-Ready PDF Converter

Converts **up to 300 images** (or a scanned PDF) into a single, **OCR-searchable PDF** that Adobe Acrobat can recognize text in and read aloud — no separate OCR step required in Acrobat.

- **Image mode**: images sorted by **EXIF capture date/time** (falls back to file modification time, then natural filename order for images without EXIF data).
- **PDF mode**: each page is rasterised, OCR'd, and reassembled into a new searchable PDF.
- File size automatically kept **at or below 100 MB** by tuning JPEG quality and, if needed, image dimensions.
- Supports JPG, PNG, BMP, TIFF, GIF, WebP input formats.

---

## Requirements

### 1. Tesseract OCR engine (required for both modes)

**macOS**
```bash
brew install tesseract
```

**Ubuntu / Debian**
```bash
sudo apt-get install tesseract-ocr
```

**Windows** — download the installer from https://github.com/UB-Mannheim/tesseract/wiki and add it to your `PATH`.

Additional language packs (Spanish, French, etc.) can be installed via your package manager or the Tesseract installer.

### 2. Poppler (required only for PDF input mode)

**macOS**
```bash
brew install poppler
```

**Ubuntu / Debian**
```bash
sudo apt-get install poppler-utils
```

**Windows** — download from https://github.com/oschwartz10612/poppler-windows/releases and add the `bin/` folder to your `PATH`.

### 3. Python dependencies
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
| `input` | Directory of images, a single image file, **or a PDF file** |
| `-o`, `--output` | Output PDF path (default: `output.pdf`) |
| `-q`, `--quality` | JPEG quality 1–95. Skips auto-sizing (use carefully on large sets) |
| `--max-dim` | Resize so longest side ≤ N pixels (default: auto) |

### Examples

```bash
# Image directory — auto quality, ordered by EXIF capture date
python convert.py ./photos/

# Scanned / image-based PDF — produce a searchable version
python convert.py scan.pdf -o searchable.pdf

# Custom output path
python convert.py ./photos/ -o my_book.pdf

# Force quality 70 (skips auto-detection)
python convert.py ./photos/ -q 70

# Force quality AND max dimension
python convert.py ./photos/ -q 60 --max-dim 1800
```

---

## How it works

### Image mode

1. **Gather** all supported images from the input directory.
2. **Sort by EXIF capture date** (`DateTimeOriginal` tag) so photos appear in the order they were taken, not the order they were named or copied.
   - Images without EXIF data fall back to file modification time.
   - If *no* image has EXIF data, natural filename order is used.
3. **Estimate size** by compressing every image at each quality level and projecting total PDF size (with ~12 % overhead for structure and the OCR text layer).
4. **Select quality** — picks the highest quality setting (and if needed the smallest dimension) that keeps output ≤ 100 MB.
5. **OCR + assemble** — each image is processed by Tesseract, which produces a PDF page with the image as the background and an invisible text overlay. All pages are merged into the output PDF.

### PDF mode

1. **Rasterise** each page of the input PDF to an image at 200 DPI using `pdf2image` + poppler.
2. Pages are already in document order — no date sorting needed.
3. **Estimate size / select quality** and **OCR + assemble** proceed identically to image mode.

---

## Adobe Read Aloud

Open the output PDF in **Adobe Acrobat** or **Adobe Acrobat Reader**, then:

- **Windows / Linux**: `View → Read Out Loud → Activate Read Out Loud`, then `Read This Page` or `Read To End Of Document`
- **macOS**: same menu path, or use macOS built-in text-to-speech on the selectable text

Because the invisible OCR text layer is embedded in the PDF, Acrobat finds the text immediately — no re-scan needed.

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `TesseractNotFoundError` | Install Tesseract and ensure it is on your `PATH` |
| `pdf2image` / `PDFPageCountError` | Install poppler (see above) and ensure it is on your `PATH` |
| Output exceeds 100 MB | Run without `-q` so auto-quality kicks in; or add `--max-dim 1200` |
| Poor text recognition | Use higher-resolution source images; 300 DPI scans work best |
| Wrong image order | Check that photo EXIF data is intact; rename files `001.jpg`, `002.jpg`, … as a fallback |
