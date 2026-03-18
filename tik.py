import os
import uuid
import time
import logging
import tempfile
import threading
import glob
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
import mimetypes

from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

# Configuration
class Config:
    """Configuration centralisée"""
    # Limite de taille (60MB)
    MAX_FILE_SIZE = 60 * 1024 * 1024  # 60 MB
    
    # Rate limiting
    MAX_DOWNLOADS_PER_IP = 5  # par minute (réduit à cause de la limite)
    MAX_ANALYSIS_PER_IP = 15  # par minute
    
    # Nettoyage
    CLEANUP_DELAY = 300  # 5 minutes (fichiers temporaires)
    CLEANUP_INTERVAL = 60  # 1 minute
    
    # Dossiers
    BASE_DIR = Path(tempfile.gettempdir()) / "yt_downloader"
    DOWNLOAD_DIR = BASE_DIR / "downloads"
    LOG_DIR = BASE_DIR / "logs"
    
    # Formats supportés
    SUPPORTED_SITES = [
        'youtube.com', 'youtu.be', 'tiktok.com', 
        'facebook.com', 'fb.watch', 'instagram.com',
        'threads.net', 'twitter.com', 'x.com'
    ]
    
    # Qualités vidéo max (pour économiser de l'espace)
    MAX_QUALITY = '1080p'  # Pas de 4K pour rester sous 60MB

# Créer les dossiers
Config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
Config.LOG_DIR.mkdir(parents=True, exist_ok=True)

# Configuration logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Config.LOG_DIR / 'app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=["*"])  # En production, spécifier les origines

# Rate limiting avec stockage mémoire
limiter = Limiter(
    app=app,
    key_func=lambda: request.remote_addr,
    default_limits=["100 per day", "30 per hour"],
    storage_uri="memory://"
)

# Stockage des jobs thread-safe
class JobStore:
    def __init__(self):
        self._jobs: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stats = {
            'total_downloads': 0,
            'total_size': 0,
            'errors': 0
        }
    
    def add(self, job_id: str, data: dict):
        with self._lock:
            data['time'] = time.time()
            data['ip'] = request.remote_addr
            self._jobs[job_id] = data
    
    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            return self._jobs.get(job_id)
    
    def update(self, job_id: str, **kwargs):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kwargs)
    
    def remove(self, job_id: str):
        with self._lock:
            if job_id in self._jobs:
                path = self._jobs[job_id].get('path')
                if path and Path(path).exists():
                    try:
                        Path(path).unlink()
                        logger.info(f"Fichier supprimé: {path}")
                    except Exception as e:
                        logger.error(f"Erreur suppression {path}: {e}")
                del self._jobs[job_id]
    
    def cleanup(self):
        """Supprime les jobs expirés"""
        now = time.time()
        with self._lock:
            for job_id in list(self._jobs.keys()):
                job = self._jobs[job_id]
                created = job.get('time', 0)
                if now - created > Config.CLEANUP_DELAY:
                    path = job.get('path')
                    if path and Path(path).exists():
                        try:
                            Path(path).unlink()
                        except:
                            pass
                    del self._jobs[job_id]
                    logger.debug(f"Cleanup job {job_id}")
    
    def get_stats(self) -> dict:
        with self._lock:
            return {**self._stats, 'active_jobs': len(self._jobs)}

jobs = JobStore()

# Thread de nettoyage automatique
def cleanup_worker():
    while True:
        time.sleep(Config.CLEANUP_INTERVAL)
        try:
            jobs.cleanup()
            # Nettoyer aussi les fichiers orphelins
            for f in Config.DOWNLOAD_DIR.glob("*"):
                if f.stat().st_mtime < time.time() - Config.CLEANUP_DELAY:
                    try:
                        f.unlink()
                        logger.info(f"Nettoyage fichier orphelin: {f}")
                    except:
                        pass
        except Exception as e:
            logger.error(f"Erreur cleanup: {e}")

threading.Thread(target=cleanup_worker, daemon=True).start()

# ---------------- UTILS ----------------
def validate_url(url: str) -> Tuple[bool, str]:
    """Valide l'URL et le domaine"""
    if not url or not isinstance(url, str):
        return False, "URL invalide"
    
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Vérifier les domaines autorisés
    for domain in Config.SUPPORTED_SITES:
        if domain in url.lower():
            return True, url
    
    return False, "Domaine non supporté"

def get_file_size_mb(filepath: Path) -> float:
    """Retourne la taille du fichier en MB"""
    return filepath.stat().st_size / (1024 * 1024)

def estimate_size(format_info: dict) -> float:
    """Estime la taille du fichier à télécharger"""
    filesize = format_info.get('filesize') or format_info.get('filesize_approx')
    if filesize:
        return filesize / (1024 * 1024)
    
    # Estimation basée sur le bitrate et durée
    bitrate = format_info.get('tbr') or format_info.get('abr') or format_info.get('vbr', 1000)
    duration = format_info.get('duration', 60)
    return (bitrate * duration * 1000) / (8 * 1024 * 1024)  # bits -> MB

# ---------------- ROUTES ----------------
@app.route('/')
def health():
    return jsonify({
        'status': 'online',
        'version': '2.0',
        'max_size_mb': Config.MAX_FILE_SIZE / (1024 * 1024),
        'supported_sites': Config.SUPPORTED_SITES
    })

@app.route('/info', methods=['POST'])
@limiter.limit(f"{Config.MAX_ANALYSIS_PER_IP} per minute")
def get_info():
    """Récupère les infos d'une vidéo"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Données JSON requises'}), 400
        
        url = data.get('url')
        is_valid, url_or_error = validate_url(url)
        if not is_valid:
            return jsonify({'error': url_or_error}), 400

        logger.info(f"Analyse URL: {url}")

        # Options yt-dlp optimisées
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'format': 'best',
            'max_filesize': Config.MAX_FILE_SIZE,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                logger.error(f"Erreur extraction: {e}")
                return jsonify({'error': 'Impossible d\'analyser la vidéo'}), 400

        if not info:
            return jsonify({'error': 'Aucune information trouvée'}), 404

        # Extraire les formats disponibles
        formats = []
        seen_qualities = set()
        
        if info.get('formats'):
            for f in info['formats']:
                # Vidéo
                if f.get('vcodec') != 'none' and f.get('height'):
                    height = f.get('height')
                    # Limiter la qualité max pour économiser
                    if height <= 1080:  # Pas de 4K
                        label = f"{height}p"
                        if label not in seen_qualities:
                            # Estimer la taille
                            est_size = estimate_size(f)
                            if est_size <= (Config.MAX_FILE_SIZE / (1024 * 1024)):
                                formats.append({
                                    'label': label,
                                    'format_id': f['format_id'],
                                    'size_mb': round(est_size, 1)
                                })
                                seen_qualities.add(label)
        
        # Ajouter audio avec taille estimée
        formats.append({
            'label': 'Audio MP3',
            'format_id': 'audio',
            'size_mb': '~5-10'
        })
        
        formats.append({
            'label': 'Audio Haute Qualité',
            'format_id': 'bestaudio',
            'size_mb': '~10-20'
        })

        return jsonify({
            'title': info.get('title', 'Sans titre'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'formats': formats,
            'max_size_mb': Config.MAX_FILE_SIZE / (1024 * 1024)
        })

    except Exception as e:
        logger.error(f"Erreur info: {e}")
        return jsonify({'error': 'Erreur serveur'}), 500

@app.route('/download', methods=['POST'])
@limiter.limit(f"{Config.MAX_DOWNLOADS_PER_IP} per minute")
def download_video():
    """Télécharge une vidéo ou audio"""
    job_id = str(uuid.uuid4())
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Données JSON requises'}), 400
        
        url = data.get('url')
        format_id = data.get('format')
        
        is_valid, url_or_error = validate_url(url)
        if not is_valid:
            return jsonify({'error': url_or_error}), 400
        
        if not format_id:
            return jsonify({'error': 'Format non spécifié'}), 400

        logger.info(f"Téléchargement {job_id}: {url} format {format_id}")

        # Créer le job
        jobs.add(job_id, {
            'status': 'downloading',
            'url': url,
            'format': format_id
        })

        # Nom de fichier temporaire
        temp_filename = f"download_{job_id}.%(ext)s"
        temp_path = Config.DOWNLOAD_DIR / temp_filename

        # Configuration selon le format
        if format_id == 'audio':
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': str(temp_path),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'no_warnings': True,
                'max_filesize': Config.MAX_FILE_SIZE,
            }
        elif format_id == 'bestaudio':
            ydl_opts = {
                'format': 'bestaudio',
                'outtmpl': str(temp_path),
                'quiet': True,
                'no_warnings': True,
                'max_filesize': Config.MAX_FILE_SIZE,
            }
        else:
            ydl_opts = {
                'format': format_id,
                'outtmpl': str(temp_path),
                'merge_output_format': 'mp4',
                'quiet': True,
                'no_warnings': True,
                'max_filesize': Config.MAX_FILE_SIZE,
            }

        # Téléchargement
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                ydl.download([url])
            except yt_dlp.utils.DownloadError as e:
                if 'File is too large' in str(e):
                    return jsonify({'error': 'Fichier trop gros (>60MB)'}), 400
                raise

        # Trouver le fichier téléchargé
        downloaded_files = list(Config.DOWNLOAD_DIR.glob(f"download_{job_id}.*"))
        
        if not downloaded_files:
            raise Exception("Fichier non trouvé après téléchargement")
        
        filepath = downloaded_files[0]
        file_size_mb = get_file_size_mb(filepath)
        
        logger.info(f"Fichier créé: {filepath} ({file_size_mb:.1f}MB)")

        # Vérifier la taille
        if file_size_mb > (Config.MAX_FILE_SIZE / (1024 * 1024)):
            filepath.unlink()
            return jsonify({'error': 'Fichier trop gros (>60MB)'}), 400

        # Mettre à jour le job
        jobs.update(job_id, 
            status='done',
            path=str(filepath),
            size_mb=file_size_mb
        )

        # Déterminer le nom de téléchargement
        if format_id in ['audio', 'bestaudio']:
            download_name = f"audio_{uuid.uuid4().hex[:8]}.mp3"
            mimetype = 'audio/mpeg'
        else:
            download_name = f"video_{uuid.uuid4().hex[:8]}.mp4"
            mimetype = 'video/mp4'

        # Envoyer le fichier
        response = send_file(
            filepath,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype
        )

        # Ajouter des headers de sécurité
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Length'] = filepath.stat().st_size
        
        return response

    except Exception as e:
        logger.error(f"Erreur téléchargement {job_id}: {e}")
        jobs.update(job_id, status='error', error=str(e))
        
        # Nettoyer si fichier créé
        for f in Config.DOWNLOAD_DIR.glob(f"download_{job_id}.*"):
            try:
                f.unlink()
            except:
                pass
                
        return jsonify({'error': f'Erreur: {str(e)}'}), 500

@app.route('/status/<job_id>')
def get_status(job_id):
    """Vérifie le statut d'un job"""
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job non trouvé'}), 404
    
    return jsonify({
        'status': job.get('status'),
        'size_mb': job.get('size_mb'),
        'error': job.get('error')
    })

@app.route('/stats')
def get_stats():
    """Statistiques du serveur"""
    return jsonify({
        'active_jobs': len([j for j in jobs._jobs.values() if j.get('status') == 'downloading']),
        'total_jobs_today': 0,  # À implémenter avec une DB
        'max_size_mb': Config.MAX_FILE_SIZE / (1024 * 1024),
        'supported_sites': Config.SUPPORTED_SITES
    })

# ---------------- MAIN ----------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    
    logger.info(f"🚀 Backend démarré sur port {port}")
    logger.info(f"📁 Dossier downloads: {Config.DOWNLOAD_DIR}")
    logger.info(f"📊 Limite taille: {Config.MAX_FILE_SIZE / (1024 * 1024)}MB")
    
    app.run(host='0.0.0.0', port=port, debug=debug)
