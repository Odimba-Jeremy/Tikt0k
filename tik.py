import os
import uuid
import time
import logging
import tempfile
import threading
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

TEMP_DIR = tempfile.gettempdir()
jobs = {}  # job_id -> dict {status, path, time}

# ---------------- LIMITATION PAR IP ----------------
limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["20 per minute"]  # max 20 downloads/min par IP
)

# ---------------- CLEANUP AUTO ----------------
def cleanup():
    while True:
        now = time.time()
        for job_id in list(jobs.keys()):
            job = jobs[job_id]
            path = job.get("path")
            created = job.get("time", 0)
            if path and os.path.exists(path) and now - created > 600:  # 10 min
                try:
                    os.remove(path)
                    del jobs[job_id]
                    logging.info(f"Supprimé {path}")
                except Exception as e:
                    logging.error(e)
        time.sleep(120)

threading.Thread(target=cleanup, daemon=True).start()

# ---------------- HEALTH ----------------
@app.route("/")
def health():
    return jsonify({"status": "online"}), 200

# ---------------- INFO ----------------
@app.route("/info", methods=["POST"])
def info():
    data = request.get_json()
    url = data.get("url")
    if not url:
        return jsonify({"error": "URL manquante"}), 400

    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        seen = set()
        for f in info.get("formats", []):
            if f.get("vcodec") != "none" and f.get("height"):
                label = f"{f['height']}p"
                if label not in seen:
                    formats.append({"label": label, "format_id": f["format_id"]})
                    seen.add(label)
        # ajouter audio
        formats.append({"label": "Audio", "format_id": "audio"})

        return jsonify({
            "formats": formats,
            "thumbnail": info.get("thumbnail")
        })
    except Exception as e:
        logging.error(e)
        return jsonify({"error": "Impossible de récupérer la vidéo"}), 500

# ---------------- DOWNLOAD ----------------
@app.route("/download", methods=["POST"])
@limiter.limit("10 per minute")  # max 10 downloads/min par IP
def download():
    data = request.get_json()
    url = data.get("url")
    format_id = data.get("format")

    if not url or not format_id:
        return jsonify({"error": "Paramètres manquants"}), 400

    job_id = str(uuid.uuid4())
    filename = f"{job_id}.mp4" if format_id != "audio" else f"{job_id}.mp3"
    filepath = os.path.join(TEMP_DIR, filename)

    try:
        # AUDIO
        if format_id == "audio":
            ydl_opts = {
                "format": "bestaudio",
                "outtmpl": filepath,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
            }
        else:
            ydl_opts = {
                "format": format_id,
                "outtmpl": filepath,
                "merge_output_format": "mp4"
            }

        jobs[job_id] = {"status": "downloading", "time": time.time()}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        jobs[job_id]["status"] = "done"
        jobs[job_id]["path"] = filepath

        return send_file(filepath, as_attachment=True)

    except Exception as e:
        logging.error(e)
        jobs[job_id]["status"] = "error"
        return jsonify({"error": "Erreur téléchargement"}), 500

# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Backend Ultra Pro lancé sur port {port}")
    app.run(host="0.0.0.0", port=port)
