import os
import re
import time
import uuid
import shutil
import threading
import subprocess
import zipfile
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, send_file, render_template, abort

app = Flask(__name__)

DOWNLOAD_ROOT = os.environ.get("DOWNLOAD_ROOT", "/data/downloads")
FILE_TTL_SECONDS = int(os.environ.get("FILE_TTL_SECONDS", 3600))  # 1 hour
COMMAND_TIMEOUT = int(os.environ.get("COMMAND_TIMEOUT", 1800))  # 30 min max per job

os.makedirs(DOWNLOAD_ROOT, exist_ok=True)

# In-memory job store: job_id -> dict
JOBS = {}
JOBS_LOCK = threading.Lock()

URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def now():
    return datetime.utcnow()


def make_job(url, username):
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "pending",  # pending -> running -> success | error
            "error": None,
            "url": url,
            "username": username,
            "created_at": now(),
            "finished_at": None,
            "file_path": None,
            "file_name": None,
            "expires_at": None,
        }
    return job_id


def update_job(job_id, **kwargs):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)


def get_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def run_job(job_id, url, username, password):
    job_dir = os.path.join(DOWNLOAD_ROOT, job_id)
    os.makedirs(job_dir, exist_ok=True)
    update_job(job_id, status="running")

    cmd = [
        "audiobook-dl",
        "--username", "'",username,"'",
        "--password", "'",password,"'",
        "--combine",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=job_dir,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        update_job(job_id, status="error", error="Command timed out.", finished_at=now())
        shutil.rmtree(job_dir, ignore_errors=True)
        return
    except FileNotFoundError:
        update_job(job_id, status="error", error="audiobook-dl is not installed in the container.", finished_at=now())
        shutil.rmtree(job_dir, ignore_errors=True)
        return

    if result.returncode != 0:
        # Keep only a short, safe error message — never log/echo the password.
        stderr_tail = (result.stderr or "").strip().splitlines()[-1:] or ["Command failed."]
        update_job(job_id, status="error", error=stderr_tail[0][:500], finished_at=now())
        shutil.rmtree(job_dir, ignore_errors=True)
        return

    produced = [
        f for f in os.listdir(job_dir)
        if os.path.isfile(os.path.join(job_dir, f)) and not f.startswith(".")
    ]

    if not produced:
        update_job(job_id, status="error", error="Command succeeded but produced no output file.", finished_at=now())
        shutil.rmtree(job_dir, ignore_errors=True)
        return

    if len(produced) == 1:
        file_path = os.path.join(job_dir, produced[0])
        file_name = produced[0]
    else:
        zip_path = os.path.join(job_dir, "download.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in produced:
                zf.write(os.path.join(job_dir, f), arcname=f)
        file_path = zip_path
        file_name = "download.zip"

    expires_at = now() + timedelta(seconds=FILE_TTL_SECONDS)
    update_job(
        job_id,
        status="success",
        file_path=file_path,
        file_name=file_name,
        finished_at=now(),
        expires_at=expires_at,
    )


def cleanup_loop():
    while True:
        cutoff = now() - timedelta(seconds=FILE_TTL_SECONDS)
        with JOBS_LOCK:
            job_ids = list(JOBS.keys())
        for job_id in job_ids:
            job = get_job(job_id)
            if not job:
                continue
            # Remove finished jobs (success or error) whose creation time is past TTL.
            if job["status"] in ("success", "error") and job["created_at"] < cutoff:
                job_dir = os.path.join(DOWNLOAD_ROOT, job_id)
                shutil.rmtree(job_dir, ignore_errors=True)
                with JOBS_LOCK:
                    JOBS.pop(job_id, None)
        time.sleep(60)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.get_json(silent=True) or request.form
    url = (data.get("url") or "").strip()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not url or not username or not password:
        return jsonify({"error": "url, username, and password are all required."}), 400

    if not URL_RE.match(url):
        return jsonify({"error": "Please enter a valid http(s) URL."}), 400

    job_id = make_job(url, username)
    thread = threading.Thread(target=run_job, args=(job_id, url, username, password), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found (it may have expired)."}), 404

    resp = {
        "id": job["id"],
        "status": job["status"],
        "error": job["error"],
    }
    if job["status"] == "success":
        resp["file_name"] = job["file_name"]
        resp["download_url"] = f"/api/download/{job_id}"
        resp["expires_at"] = job["expires_at"].isoformat() + "Z" if job["expires_at"] else None
    return jsonify(resp)


@app.route("/api/download/<job_id>")
def download(job_id):
    job = get_job(job_id)
    if not job or job["status"] != "success" or not job["file_path"]:
        abort(404)
    if not os.path.exists(job["file_path"]):
        abort(404)
    return send_file(job["file_path"], as_attachment=True, download_name=job["file_name"])


if __name__ == "__main__":
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    app.run(host="0.0.0.0", port=5000)
