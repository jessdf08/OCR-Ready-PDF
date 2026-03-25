"""Flask web interface for OCR-Ready PDF converter."""

import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, after_this_request, jsonify, render_template, request, send_file

import convert

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB upload limit

# In-memory job registry: {job_id: {status, message, output_path, tmp_dir, created_at}}
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run_job(job_id: str, mode: str, saved_files: list[Path], tmp_dir: Path) -> None:
    """Run the OCR conversion in a background thread."""
    output_path = tmp_dir / "output.pdf"
    try:
        if mode == "pdf":
            pdf_path = saved_files[0]
            img_subdir = tmp_dir / "pages"
            img_subdir.mkdir()
            files = convert.extract_pdf_to_images(pdf_path, img_subdir)
        else:
            files = convert.sort_images_by_capture_date(saved_files)
            files = files[: convert.MAX_PAGES]

        if not files:
            raise ValueError("No processable pages found in the uploaded file(s).")

        quality, max_dim = convert.find_quality_settings(files)
        convert.build_pdf(files, output_path, quality, max_dim)

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["output_path"] = output_path

    except BaseException as exc:  # catch SystemExit from convert.sys.exit() too
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = str(exc) or type(exc).__name__


# ---------------------------------------------------------------------------
# Stale-job cleanup daemon
# ---------------------------------------------------------------------------

def _cleanup_daemon() -> None:
    """Delete jobs older than 30 minutes that were never downloaded."""
    while True:
        time.sleep(600)  # run every 10 minutes
        cutoff = time.time() - 1800  # 30-minute TTL
        stale: list[str] = []
        with JOBS_LOCK:
            for jid, job in JOBS.items():
                if job["created_at"] < cutoff:
                    stale.append(jid)
            for jid in stale:
                _delete_job_files(JOBS.pop(jid))


def _delete_job_files(job: dict) -> None:
    """Delete the temp directory for a job (best-effort)."""
    tmp_dir: Path | None = job.get("tmp_dir")
    if tmp_dir and tmp_dir.exists():
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except OSError:
            pass


_daemon = threading.Thread(target=_cleanup_daemon, daemon=True)
_daemon.start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    mode = request.form.get("mode", "").strip()
    if mode not in ("images", "pdf"):
        return jsonify(error="Invalid mode. Choose 'images' or 'pdf'."), 400

    uploaded = request.files.getlist("files")
    if not uploaded or all(f.filename == "" for f in uploaded):
        return jsonify(error="No files were uploaded."), 400

    # Validate file types
    if mode == "pdf":
        if len(uploaded) != 1:
            return jsonify(error="Upload exactly one PDF file."), 400
        if not uploaded[0].filename.lower().endswith(".pdf"):
            return jsonify(error="File must be a .pdf"), 400
    else:
        allowed = convert.SUPPORTED_IMAGE_FORMATS
        bad = [f.filename for f in uploaded
               if Path(f.filename).suffix.lower() not in allowed]
        if bad:
            return jsonify(error=f"Unsupported file type(s): {', '.join(bad)}"), 400

    job_id = str(uuid.uuid4())
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"ocrpdf_{job_id}_"))

    # Save uploads to disk
    saved: list[Path] = []
    for f in uploaded:
        dest = tmp_dir / Path(f.filename).name
        f.save(dest)
        saved.append(dest)

    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "message": "",
            "output_path": None,
            "tmp_dir": tmp_dir,
            "created_at": time.time(),
        }

    t = threading.Thread(target=_run_job, args=(job_id, mode, saved, tmp_dir), daemon=True)
    t.start()

    return jsonify(job_id=job_id), 202


@app.route("/status/<job_id>")
def status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return jsonify(error="Job not found."), 404
    return jsonify(status=job["status"], message=job.get("message", ""))


@app.route("/download/<job_id>")
def download(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return jsonify(error="Job not found."), 404
    if job["status"] != "done":
        return jsonify(error="Job is not ready for download."), 409
    output_path: Path = job["output_path"]
    if not output_path or not output_path.exists():
        return jsonify(error="Output file missing."), 500

    @after_this_request
    def cleanup(response):
        with JOBS_LOCK:
            finished_job = JOBS.pop(job_id, None)
        if finished_job:
            _delete_job_files(finished_job)
        return response

    return send_file(
        output_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="ocr-output.pdf",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
