FROM python:3.12-slim

# System deps: Tesseract OCR engine + Poppler (for PDF rasterisation) + Pillow libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY convert.py app.py ./
COPY templates/ templates/

ENV PORT=8080
EXPOSE 8080

# Single worker (in-memory job registry must be shared), 8 threads for concurrent requests
CMD gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT app:app
