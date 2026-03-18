import os
import re
import time
import threading
import tempfile
import logging
import uuid
import requests

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp

# ------------------- CONFIG -------------------
app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

TEMP_FOLDER = tempfile.gettempdir()
jobs = {}  # stockage des jobs

# ------------------- CLEANUP -------------------
def cleanup_temp():
    while True:
        now = time.time()
        for job_id in list(jobs.keys()):
            job = jobs[job_id]
            if "file" in job:
                path = job["file"]
                if os.path.exists(path) and now - os.path.getmtime(path) > 600:
                    try:
                        os.remove(path)
                        del jobs[job_id]
                        logging.info(f"Supprimé {path}")
                    except Exception as e:
                        logging.error(f"Erreur suppression: {e}")
        time.sleep(300)

threading.Thread(target=cleanup_temp, daemon=True).start()

# ------------------- VALIDATION -------------------
def validate_url(url):
    if not url:
        return "URL manquante"
    if len(url) > 500:
        return "URL trop longue"
    pattern = (
        r'https?://(www\.)?'
        r'(youtube\.com|youtu\.be|tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com|'
        r'instagram\.com|facebook\.com|fb\.watch|threads\.net)/.+'
    )
    if not re.match(pattern, url):
        return "Plateforme non supportée"
    return None

# ------------------- DOWNLOAD WORKER -------------------
def download_worker(job_id, url, resolution=None):
    try:
        jobs[job_id]["status"] = "downloading"
        filename = f"media_{uuid.uuid4().hex}"
        filepath = os.path.join(TEMP_FOLDER, filename)

        ydl_opts = {"quiet": True, "retries": 3, "ignoreerrors": False}

        # ---------------- DETECTION AUTOMATIQUE ----------------
        download_type = "video"
        if "tiktok.com" in url and "/photo/" in url:
            download_type = "photo"

        # ---------------- PHOTO TIKTOK ----------------
        if download_type == "photo":
            ydl_opts.update({
                "skip_download": False,
                "writethumbnail": True,
                "outtmpl": filepath + ".%(ext)s",
            })
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if "thumbnail" in info:
                    thumb_url = info["thumbnail"]
                    thumb_path = os.path.join(TEMP_FOLDER, f"{filename}.jpg")
                    r = requests.get(thumb_url, stream=True)
                    if r.status_code == 200:
                        with open(thumb_path, "wb") as f:
                            for chunk in r.iter_content(1024):
                                f.write(chunk)
                        filepath = thumb_path

        # ---------------- VIDEO OU AUDIO ----------------
        else:
            # VIDEO
            format_map = {
                "480p": "best[height<=480]",
                "HD": "best[height<=720]",
                "UHD": "best[height<=1080]",
                "4K": "best[height<=2160]",
            }
            fmt = format_map.get(resolution, "bestvideo+bestaudio/best")
            ydl_opts.update({
                "format": fmt,
                "merge_output_format": "mp4",
                "outtmpl": filepath + ".mp4",
                "noplaylist": True
            })
            filepath += ".mp4"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)

        jobs[job_id]["status"] = "done"
        jobs[job_id]["file"] = filepath
        jobs[job_id]["type"] = download_type

    except Exception as e:
        logging.error(f"Erreur job {job_id}: {e}")
        jobs[job_id]["status"] = "error"

# ------------------- ROUTES -------------------
@app.route("/")
def health():
    return jsonify({"status": "online"}), 200

@app.route("/download", methods=["POST"])
def start_download():
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": "URL manquante"}), 400

    url = data["url"].strip()
    resolution = data.get("resolution", "HD")

    error = validate_url(url)
    if error:
        return jsonify({"error": error}), 400

    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "pending"}

    threading.Thread(target=download_worker, args=(job_id, url, resolution), daemon=True).start()

    return jsonify({"job_id": job_id, "status": "started"})

@app.route("/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable"}), 404
    return jsonify({"status": job["status"], "type": job.get("type")})

@app.route("/file/<job_id>")
def get_file(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable"}), 404
    if job["status"] != "done":
        return jsonify({"error": "Pas prêt"}), 400
    filepath = job["file"]
    return send_file(filepath, as_attachment=True)

# ------------------- RUN -------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Serveur lancé sur port {port}")
    app.run(host="0.0.0.0", port=port)
