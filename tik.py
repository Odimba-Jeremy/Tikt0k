import os
import re
import time
import threading
import tempfile
import logging
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

# ------------------- Configuration -------------------
app = Flask(__name__)
CORS(app)

# Logs
logging.basicConfig(level=logging.INFO)

# Rate limit global (fix Limiter)
limiter = Limiter(
    app=app,                    # L'application Flask
    key_func=get_remote_address, # IP du client
    default_limits=["20 per minute"]
)

# Temp folder pour les vidéos
TEMP_FOLDER = tempfile.gettempdir()

# ------------------- Nettoyage automatique -------------------
def cleanup_temp():
    while True:
        now = time.time()
        for f in os.listdir(TEMP_FOLDER):
            if f.startswith("tiktok_") and f.endswith(".mp4"):
                path = os.path.join(TEMP_FOLDER, f)
                try:
                    if now - os.path.getmtime(path) > 600:  # 10 minutes
                        os.remove(path)
                        logging.info(f"Supprimé {path}")
                except Exception as e:
                    logging.error(f"Erreur nettoyage: {e}")
        time.sleep(300)  # toutes les 5 minutes

threading.Thread(target=cleanup_temp, daemon=True).start()

# ------------------- Fonctions -------------------
def validate_url(url):
    """Validation stricte TikTok"""
    if not url:
        return "URL manquante"
    if len(url) > 300:
        return "URL trop longue"
    
    pattern = r'https?://(www\.|vm\.)?tiktok\.com/.+'
    if not re.match(pattern, url):
        return "Lien TikTok invalide"
    return None

def download_tiktok(url):
    """Télécharge la vidéo et retourne le chemin"""
    filename = f"tiktok_{int(time.time())}.mp4"
    filepath = os.path.join(TEMP_FOLDER, filename)

    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": filepath,
        "noplaylist": True,
        "quiet": True,
        "retries": 3,
        "ignoreerrors": True
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if not os.path.exists(filepath):
        raise Exception("Téléchargement échoué")

    return filepath

# ------------------- Routes -------------------
@app.route("/")
def health():
    return jsonify({"status": "online"}), 200

@app.route("/download", methods=["POST"])
@limiter.limit("10 per minute")  # Limite spécifique sur cette route
def download():
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": "URL manquante"}), 400

    url = data["url"].strip()
    error = validate_url(url)
    if error:
        return jsonify({"error": error}), 400

    try:
        filepath = download_tiktok(url)
        return send_file(
            filepath,
            mimetype="video/mp4",
            as_attachment=True,
            download_name="tiktok_video.mp4"
        )
    except Exception as e:
        logging.error(f"Téléchargement échoué: {e}")
        return jsonify({"error": "Erreur lors du téléchargement"}), 500

# ------------------- Exécution -------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Serveur démarré sur le port {port}")
    app.run(host="0.0.0.0", port=port)
